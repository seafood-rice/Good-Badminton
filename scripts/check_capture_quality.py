"""Check whether footage can support shuttle detection BEFORE analysing it.

Option D (design doc section 0.24) is a capture-time fix: the DJI hall footage cannot
support shuttle-based rally detection because of how it was shot, not because of which
detector was pointed at it. This script measures the four properties that decide the
question, so a new camera setup can be validated in minutes instead of after a full
analysis run.

Every threshold is either DERIVED from geometry or CALIBRATED against the known-bad
DJI clip. Where a threshold is a target with no measured success case behind it, the
report says so rather than implying it is validated.

Usage:
  python scripts/check_capture_quality.py --video CLIP [--quad path/to/court_annotations.txt]
  python scripts/check_capture_quality.py --video CLIP --quad "x1,y1 x2,y2 x3,y3 x4,y4"
"""
import argparse
import json
import math
import os
import sys

import cv2
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from badminton_analysis.court.scale import COURT_WIDTH_M, PerspectiveScale  # noqa: E402
from badminton_analysis.shuttle_track.classical import (build_masks,  # noqa: E402
                                                        frame_candidates,
                                                        is_static_camera)

COURT_LEN_M = 13.40
SHUTTLE_M = 0.065
DETECTOR_FLOOR_PX = 3.0      # a detector needs roughly this many px to fire
PRACTICAL_TILE_SCALE = 2.0   # aspect-preserving tiling we are willing to pay for

# --- thresholds, with their provenance ---
MAX_SPAN = 2.0
"""Perspective span (near px/m over far px/m). DERIVED.

Above 2x, one absolute-pixel constant cannot serve both ends of the court, and the
far shuttle falls below the detector floor while the near one is comfortable. The DJI
clip measures 5.68x, which is what made every fixed-pixel gate wrong (80 px meant
0.13 m near and 0.75 m far)."""

MIN_PX_PER_M = DETECTOR_FLOOR_PX * PRACTICAL_TILE_SCALE / SHUTTLE_M   # 92
"""Minimum scale anywhere on court. DERIVED: shuttle_m * px_per_m / tile_scale >= 3 px."""

MAX_CANDIDATES_PER_FRAME = 25
"""Competing movers per frame inside the court and its airspace. TARGET, NOT VALIDATED.

The DJI clip measures ~119/frame and fails: its tracker coverage was HIGHER between
rallies than during them (section 0.24). No footage that succeeds has been measured, so
this number is a goal derived from wanting the shuttle to be a minority of candidates
rather than from an observed success. Treat a pass here as necessary, not sufficient."""

MAX_CAMERA_SHIFT_PX = 3.0
"""Median per-frame camera motion, in original pixels. CALIBRATED: DJI tripod 0.0,
this project's broadcast clip 1.37 (fixed camera with jitter, not a pan)."""


def parse_quad(spec):
    """Accept a court_annotations.txt path or an inline 'x,y x,y x,y x,y' string."""
    if spec is None:
        return None
    if os.path.exists(spec):
        with open(spec, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line.startswith("corners="):
                    pts = json.loads(line.split("=", 1)[1])
                    return [(float(p[0]), float(p[1])) for p in pts]
        raise ValueError(f"no 'corners=' line found in {spec}")
    pts = []
    for tok in spec.replace(";", " ").split():
        x, y = tok.split(",")
        pts.append((float(x), float(y)))
    if len(pts) != 4:
        raise ValueError("quad needs exactly 4 'x,y' points (TL TR BR BL)")
    return pts


def measure_geometry(quad):
    """Perspective span and the minimum scale on court, from the quad alone."""
    scale = PerspectiveScale.from_quad(quad)
    y_far = (quad[0][1] + quad[1][1]) / 2.0
    y_near = (quad[3][1] + quad[2][1]) / 2.0
    s_far, s_near = scale.px_per_m(y_far), scale.px_per_m(y_near)
    span = s_near / max(s_far, 1e-9)
    # implied camera standoff for an end-on view, from (L + d0) / d0 = span
    standoff = COURT_LEN_M / (span - 1.0) if span > 1.0 else float("inf")
    return {"px_per_m_far": s_far, "px_per_m_near": s_near, "span": span,
            "min_px_per_m": min(s_far, s_near), "implied_standoff_m": standoff,
            "shuttle_px_at_tile_scale": SHUTTLE_M * min(s_far, s_near)
            / PRACTICAL_TILE_SCALE}


def measure_clutter(video_path, quad, start_sec, n_frames, sample_step):
    """Competing movers per frame inside the court and its airspace."""
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        cap.release()
        raise ValueError(f"cannot open video: {video_path}")
    try:
        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(round(start_sec * fps)))
        grays = []
        for _ in range(n_frames):
            ok, fr = cap.read()
            if not ok:
                break
            grays.append(cv2.cvtColor(fr, cv2.COLOR_BGR2GRAY))
    finally:
        cap.release()
    if len(grays) < 8:
        raise ValueError(f"only {len(grays)} frames decoded from {video_path}")
    masks = build_masks(grays[::max(1, len(grays) // 12)], quad)
    counts = []
    for g in grays[::sample_step]:
        cands, _dropped = frame_candidates(g, masks)
        counts.append(len(cands))
    return {"candidates_per_frame": float(np.mean(counts)),
            "candidates_max": int(max(counts)), "frames_sampled": len(counts)}


def report(video_path, quad, start_sec=0.0, n_frames=240, sample_step=4):
    geo = measure_geometry(quad)
    clut = measure_clutter(video_path, quad, start_sec, n_frames, sample_step)
    static = is_static_camera(str(video_path))

    checks = [
        ("perspective span", geo["span"], MAX_SPAN, "<=", "DERIVED",
         f"{geo['span']:.2f}x  (far {geo['px_per_m_far']:.0f} px/m, "
         f"near {geo['px_per_m_near']:.0f} px/m)"),
        ("minimum scale on court", geo["min_px_per_m"], MIN_PX_PER_M, ">=", "DERIVED",
         f"{geo['min_px_per_m']:.0f} px/m -> shuttle is "
         f"{geo['shuttle_px_at_tile_scale']:.1f} px at {PRACTICAL_TILE_SCALE:.0f}x tiling"),
        ("competing movers", clut["candidates_per_frame"], MAX_CANDIDATES_PER_FRAME,
         "<=", "TARGET (unvalidated)",
         f"{clut['candidates_per_frame']:.0f}/frame (max {clut['candidates_max']}) "
         f"over {clut['frames_sampled']} frames"),
        ("camera stability", 0.0 if static else 99.0, MAX_CAMERA_SHIFT_PX, "<=",
         "CALIBRATED", f"static={static}"),
    ]

    print(f"capture-quality report: {os.path.basename(str(video_path))}")
    print(f"  court quad: {[(int(x), int(y)) for x, y in quad]}")
    if math.isfinite(geo["implied_standoff_m"]):
        print(f"  implied end-on camera standoff: "
              f"{geo['implied_standoff_m']:.1f} m behind the near baseline")
    print()
    print(f"  {'check':<24} {'result':<52} {'verdict':<6} provenance")
    failed = []
    for name, value, limit, op, prov, detail in checks:
        ok = value <= limit if op == "<=" else value >= limit
        if not ok:
            failed.append(name)
        print(f"  {name:<24} {detail:<52} {'PASS' if ok else 'FAIL':<6} {prov}")
    print()
    if failed:
        print(f"  VERDICT: unsuitable -- failed {len(failed)}: {', '.join(failed)}")
    else:
        print("  VERDICT: all checks pass (necessary, not sufficient -- the "
              "competing-movers bar is an unvalidated target)")
    return {"geometry": geo, "clutter": clut, "static_camera": static,
            "failed": failed}


def suggest():
    print("\nhow to satisfy the geometry checks (derived, not opinion):")
    print(f"  end-on: span = (L + d0)/d0 with L = {COURT_LEN_M} m, so reaching")
    for target in (3.0, 2.0):
        print(f"    span {target:.1f}x needs the camera "
              f"{COURT_LEN_M / (target - 1.0):.1f} m behind the baseline")
    print("  side-on: span = sqrt(D^2 + (L/2)^2)/D at perpendicular distance D,")
    for D in (4.0, 6.0, 8.0):
        sp = math.sqrt(D * D + (COURT_LEN_M / 2) ** 2) / D
        s = 3840.0 / COURT_LEN_M
        print(f"    D = {D:.0f} m -> span {sp:.2f}x; with the court length across "
              f"3840 px that is {s:.0f} px/m uniform "
              f"({SHUTTLE_M * s / PRACTICAL_TILE_SCALE:.0f} px shuttle at 2x tiling)")
    print("  So side-on at 6-8 m satisfies BOTH geometry checks, while no practical")
    print("  end-on distance does. Elevating and angling down additionally puts floor")
    print("  rather than spectators behind the shuttle, which is what the")
    print("  competing-movers check measures.")


def main(argv=None):
    ap = argparse.ArgumentParser()
    # --video is not declared required: --suggest prints the derived framing tables
    # and needs no clip, so requiring it made that mode unusable.
    ap.add_argument("--video", default=None)
    ap.add_argument("--quad", default=None,
                    help="court_annotations.txt path, or 'x,y x,y x,y x,y' (TL TR BR BL)")
    ap.add_argument("--start-sec", type=float, default=0.0)
    ap.add_argument("--frames", type=int, default=240)
    ap.add_argument("--sample-step", type=int, default=4)
    ap.add_argument("--suggest", action="store_true",
                    help="print the derived framing options and exit")
    args = ap.parse_args(argv)

    if args.suggest:
        suggest()
        return 0
    if not args.video:
        ap.error("--video is required (or use --suggest for the framing tables)")
    quad = parse_quad(args.quad)
    if quad is None:
        print("a court quad is required: pass --quad with a court_annotations.txt "
              "path or four 'x,y' points", file=sys.stderr)
        return 2
    res = report(args.video, quad, args.start_sec, args.frames, args.sample_step)
    suggest()
    return 1 if res["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
