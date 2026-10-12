"""ShuttleSet label parsing for the hit-detector dataset (pure; no video, no models).

Labels come from wywyWang/CoachAI-Projects (MIT), ShuttleSet/set/. frame_num is
at 30 fps in the annotators' copy of each match; pixel coordinates are at
1280x720. Corners are returned in this project's CourtMapper order:
far-left, far-right, near-right, near-left.
"""
import ast
import bisect
import csv
import math
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from badminton_analysis.stroke.hit_features import CourtGeometry  # noqa: E402

BASE_SIZE = (1280, 720)
LABEL_FPS = 30.0
STROKE7 = ("clear", "serve", "drop", "smash", "net", "lift", "drive")
TYPE_TO_STROKE7 = {
    "發短球": "serve", "發長球": "serve",
    "長球": "clear",
    "挑球": "lift", "防守回挑": "lift",
    "殺球": "smash", "點扣": "smash",
    # The README spells this as 過渡切球; the CSVs use 過度切球 (both mean "passive drop")
    "切球": "drop", "過渡切球": "drop", "過度切球": "drop",
    "放小球": "net", "擋小球": "net", "勾球": "net", "撲球": "net",
    "平球": "drive", "小平球": "drive", "後場抽平球": "drive", "推球": "drive", "防守回抽": "drive",
}


def stroke7_of(raw):
    return TYPE_TO_STROKE7.get((raw or "").strip())


def load_match_table(match_csv):
    with open(match_csv, encoding="utf-8") as fh:
        return {row["video"]: row for row in csv.DictReader(fh)}


def load_corners(homography_csv, video):
    with open(homography_csv, encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            if row["video"] == video:
                p = {k: float(row[k]) for k in row if k.endswith("_x") or k.endswith("_y")}
                return [[p["upleft_x"], p["upleft_y"]], [p["upright_x"], p["upright_y"]],
                        [p["downright_x"], p["downright_y"]], [p["downleft_x"], p["downleft_y"]]]
    raise KeyError(f"no homography row for {video}")


def scale_corners(corners, size):
    sx, sy = size[0] / BASE_SIZE[0], size[1] / BASE_SIZE[1]
    return [[x * sx, y * sy] for x, y in corners]


def hitter_side(loc_y, corners):
    try:
        y = float(loc_y)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(y):
        return None
    return "upper" if y < CourtGeometry(corners).net_mid[1] else "lower"


def _f(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return float("nan")


def parse_match_hits(match_dir, corners):
    hits = []
    for set_no in (1, 2, 3):
        path = os.path.join(match_dir, f"set{set_no}.csv")
        if not os.path.isfile(path):
            continue
        with open(path, encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                frame = _f(row.get("frame_num"))
                if not math.isfinite(frame):
                    continue
                raw = (row.get("type") or "").strip()
                hits.append({"set": set_no, "rally": int(_f(row["rally"])),
                             "ball_round": int(_f(row["ball_round"])),
                             "frame30": int(round(frame)), "t": frame / LABEL_FPS,
                             "player": row.get("player"), "type_raw": raw,
                             "stroke7": stroke7_of(raw),
                             "side": hitter_side(_f(row.get("player_location_y")), corners)})
    return sorted(hits, key=lambda h: h["frame30"])


def rally_spans(hits, pre_sec=1.0, post_sec=1.5):
    groups = {}
    for h in hits:
        groups.setdefault((h["set"], h["rally"]), []).append(h)
    spans = []
    for (set_no, rally), hs in groups.items():
        hs = sorted(hs, key=lambda h: h["frame30"])
        spans.append({"set": set_no, "rally": rally, "start": hs[0]["t"] - pre_sec,
                      "end": hs[-1]["t"] + post_sec, "hits": hs})
    return sorted(spans, key=lambda s: s["start"])


MIN_FPS = 29.5


def fps_ok(fps):
    """True when a video is at least ~30 fps (the pipeline's 30 Hz base needs no repeated frames)."""
    return bool(fps) and fps >= MIN_FPS


def best_offset(label_times, event_times, search_sec=10.0, step_sec=1 / 30, tol_sec=0.1,
                min_frac=0.6, peak_ratio=1.5, exclusion_sec=0.5):
    """Video-time offset o maximising labels t with an event within tol of t + o.

    ``accepted`` is the matched fraction alone (``frac >= min_frac``). The former peak-ratio
    test (best >= 1.5x the best count more than ``exclusion_sec`` away) is gone: rallies have
    a near-regular rhythm, so shifting the labels by one beat lines them up with neighbouring
    hits and the echo peak sits at about 0.7x the best even on a clean curve (spec 6.2,
    amended). ``alignment_verdict`` replaces it. ``peak_ratio`` is kept as
    an accepted-but-unused argument for compatibility; ``runner_up`` is informational.
    """
    labels = sorted(label_times)
    events = sorted(event_times)
    if not labels or not events:
        return {"offset": 0.0, "matched": 0, "runner_up": 0, "matched_frac": 0.0, "accepted": False}
    n_steps = int(round(search_sec / step_sec))
    offsets = [k * step_sec for k in range(-n_steps, n_steps + 1)]
    tol_sec += 1e-9          # a match exactly at the tolerance edge must not hinge on float noise
    counts = []
    for o in offsets:
        c = 0
        for t in labels:
            lo = bisect.bisect_left(events, t + o - tol_sec)
            c += lo < len(events) and events[lo] <= t + o + tol_sec
        counts.append(c)
    best_i = max(range(len(offsets)), key=lambda i: counts[i])
    # Noise-free labels match over a whole plateau of offsets; max() returns its first
    # index, so take the centre of the contiguous run of equal counts instead.
    lo_i = hi_i = best_i
    while lo_i > 0 and counts[lo_i - 1] == counts[best_i]:
        lo_i -= 1
    while hi_i < len(counts) - 1 and counts[hi_i + 1] == counts[best_i]:
        hi_i += 1
    best = round((offsets[lo_i] + offsets[hi_i]) / 2, 4)
    runner = max([c for o, c in zip(offsets, counts) if abs(o - best) > exclusion_sec] or [0])
    frac = counts[best_i] / len(labels)
    accepted = frac >= min_frac
    return {"offset": best, "matched": counts[best_i], "runner_up": runner,
            "matched_frac": round(frac, 4), "accepted": bool(accepted)}


def alignment_verdict(label_times, event_times, search_sec=10.0, step_sec=1 / 30, tol_sec=2 / 30,
                      max_abs_offset=0.2, min_z=3.5):
    """Return {"measured_offset", "z", "matched", "labels", "matched_frac", "accepted", "offset"}.

    Curve = for each offset o in [-search, +search] (step), the number of labels t with an event in
    [t + o - tol, t + o + tol]. Best = argmax, and ties take the offset nearest 0. z = robust z of the best
    count against the whole curve (median, 1.4826 * MAD; if the MAD is 0, z = inf when best > median, else 0).
    accepted = |best offset| <= max_abs_offset and z >= min_z. "offset" is 0.0 when accepted (the
    human labels are the timing ground truth; the ~-0.1 s seen on every upload is turn-detector bias),
    else the measured offset (never used, because rejected matches are not built).
    """
    labels = np.sort(np.asarray(list(label_times), dtype=float))
    events = np.sort(np.asarray(list(event_times), dtype=float))
    if labels.size == 0 or events.size == 0:
        return {"measured_offset": 0.0, "z": 0.0, "matched": 0, "labels": int(labels.size),
                "matched_frac": 0.0, "accepted": False, "offset": 0.0}
    n_steps = int(round(search_sec / step_sec))
    offsets = np.arange(-n_steps, n_steps + 1) * step_sec
    tol = tol_sec + 1e-9     # a match exactly at the tolerance edge must not hinge on float noise
    counts = np.empty(offsets.size, dtype=int)
    for i, o in enumerate(offsets):
        lo = np.searchsorted(events, labels + o - tol, side="left")
        hi = np.searchsorted(events, labels + o + tol, side="right")
        counts[i] = int(np.count_nonzero(hi > lo))
    best = int(counts.max())
    tied = np.flatnonzero(counts == best)
    best_i = int(tied[np.argmin(np.abs(offsets[tied]))])
    measured = float(offsets[best_i])
    med = float(np.median(counts))
    mad = float(np.median(np.abs(counts - med)))
    if mad > 0:
        z = (best - med) / (1.4826 * mad)
    else:
        z = float("inf") if best > med else 0.0
    accepted = bool(abs(measured) <= max_abs_offset + 1e-9 and z >= min_z)
    return {"measured_offset": round(measured, 4), "z": z, "matched": best, "labels": int(labels.size),
            "matched_frac": round(best / labels.size, 4), "accepted": accepted,
            "offset": 0.0 if accepted else round(measured, 4)}


def parse_matrix(text):
    """homography_matrix column -> nested list (kept for completeness; unused by the builder)."""
    return ast.literal_eval(text)
