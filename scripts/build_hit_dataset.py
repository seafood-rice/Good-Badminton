# scripts/build_hit_dataset.py
"""Build the hit-detector training set from labelled ShuttleSet rallies (spec 6.4).

Usage:
  python scripts/build_hit_dataset.py --match <video> [--data data/shuttleset]
      [--out data/hit_dataset] [--tracknet-mode weight] [--limit N] [--gaps]

Needs data/shuttleset/alignment/<video>.json with "accepted": true. For every
labelled rally (1 s before the first hit to 1.5 s after the last) it cuts a
clip, runs the match pipeline on it headless (TrackNetV3 + RTMPose both
players, court corners from homography.csv, court-view gate pinned open) and
stores the pipeline's own hit_signals.npz together with the labels. Resumable:
existing rally files are skipped. Per-rally run folders are deleted afterwards.

With --gaps it instead cuts the dead time between consecutive labelled rallies of a set
(gap_windows), so the detector also learns that replays, walking and shuttle changes
are not hits. Gap files go to their own --out folder and carry any labelled hit that
falls inside them (normally none).
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import time

import cv2
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import shuttleset_labels as sl  # noqa: E402
from badminton_analysis.stroke.hit_features import CourtGeometry  # noqa: E402
from badminton_analysis.stroke.hit_signals import load_signals  # noqa: E402

_SIDE_CODE = {"lower": 0, "upper": 1}


def label_to_clip_frame(t_label, offset_sec, clip_start_sec, fps):
    return int(round((t_label + offset_sec - clip_start_sec) * fps)) + 1


def court_annotation_text(corners):
    pts = [[int(round(x)), int(round(y))] for x, y in corners]
    mid = int(round(CourtGeometry(corners).net_mid[1]))
    return f"corners={pts}\nroi_corners=[(0, 0), (1, 1)]\nmid_height={mid}\n"


def encode_hits(hits, offset_sec, clip_start_sec, fps, n_frames):
    frames, hitter, stroke, raw = [], [], [], []
    for h in hits:
        f = label_to_clip_frame(h["t"], offset_sec, clip_start_sec, fps)
        if not 1 <= f <= n_frames:
            continue
        frames.append(f)
        hitter.append(_SIDE_CODE.get(h.get("side"), -1))
        stroke.append(sl.STROKE7.index(h["stroke7"]) if h.get("stroke7") in sl.STROKE7 else -1)
        raw.append(h.get("type_raw") or "")
    return {"hit_frames": np.asarray(frames, np.int64), "hitter": np.asarray(hitter, np.int64),
            "stroke7": np.asarray(stroke, np.int64), "stroke_raw": np.asarray(raw, dtype=str)}


def run_pipeline_on_clip(clip, run_dir, corners, tracknet_mode):
    """Run the match pipeline headless on one clip; return its RawSignals."""
    from badminton_analysis.system import BadmintonAnalysisSystem

    os.makedirs(run_dir, exist_ok=True)
    with open(os.path.join(run_dir, "court_annotations.txt"), "w") as fh:
        fh.write(court_annotation_text(corners))
    cap = cv2.VideoCapture(clip)
    ok, frame = cap.read()
    cap.release()
    if not ok:
        raise RuntimeError(f"cannot read {clip}")
    template = os.path.join(run_dir, "template.png")
    cv2.imwrite(template, frame)
    system = BadmintonAnalysisSystem(
        clip, show_display=False, show_skeletons=False, show_player_trajectories=False,
        show_court_trajectory=False, show_shuttlecock_trajectory=False, show_player_stats=False,
        language="en", output_dir=run_dir, template_path=template,
        ball_model_path=os.path.join(ROOT, "weights", "yolo11s-ball.pt"),
        pose_family="rtmpose", pose_mode="balanced", show_pose_roi=False,
        analyze_technique=True, racket_model_path=None,
        tracknet_weights=os.path.join(ROOT, "weights", "tracknet.pt"),
        inpaintnet_weights=os.path.join(ROOT, "weights", "inpaintnet.pt"),
        tracknet_mode=tracknet_mode, court_view_threshold=0.0, dense_whole_video=True)
    system.keep_audio = False
    system.process_video()
    if getattr(system, "_shuttle_source", None) != "tracknet":
        # Without TrackNetV3 the stored signals would train the model on yolo shuttle data.
        raise RuntimeError(f"TrackNetV3 did not run for {clip} (shuttle source "
                           f"{getattr(system, '_shuttle_source', None)}); rally rejected")
    return load_signals(os.path.join(run_dir, "hit_signals.npz"))


def clip_window(span_start, span_end, offset_sec):
    """Video-time (start, duration) of a rally clip, clamped to the video start."""
    start = max(0.0, span_start + offset_sec)
    return start, (span_end + offset_sec) - start


def gap_windows(spans, offset_sec, max_sec=10.0, min_sec=2.0):
    """Video-time windows of the dead time between consecutive labelled rallies of a set.

    A gap runs from one rally span's end to the next one's start (both already padded
    around the hits). Gaps across a set break or a missing rally number are skipped, as
    are gaps shorter than ``min_sec``. A gap longer than ``max_sec`` keeps one end of
    ``max_sec``: the post-rally end before an odd rally, the pre-serve end before an even
    one, so both kinds of dead time are covered at one pipeline run per gap.
    """
    out = []
    for prev, nxt in zip(spans, spans[1:]):
        if prev["set"] != nxt["set"] or nxt["rally"] != prev["rally"] + 1:
            continue
        lo = max(0.0, prev["end"] + offset_sec)
        hi = nxt["start"] + offset_sec
        if hi - lo < min_sec:
            continue
        if hi - lo > max_sec:
            lo = lo if nxt["rally"] % 2 else hi - max_sec
            hi = lo + max_sec
        out.append({"name": f"gap_s{nxt['set']}_r{nxt['rally']}", "set": nxt["set"],
                    "rally": nxt["rally"], "start": lo, "duration": hi - lo})
    return out


def _cut(video, start, duration, out):
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{start:.3f}", "-i", video,
                    "-t", f"{duration:.3f}", "-an", "-c:v", "libx264", "-crf", "12",
                    "-preset", "fast", out], check=True)


def main(argv=None):
    from badminton_analysis.system import load_runtime_dependencies

    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--match", required=True)
    p.add_argument("--data", default=os.path.join(ROOT, "data", "shuttleset"))
    p.add_argument("--out", default=os.path.join(ROOT, "data", "hit_dataset"))
    p.add_argument("--tracknet-mode", default="weight", choices=["weight", "nonoverlap"])
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--gaps", action="store_true",
                   help="cut the dead time between rallies instead of the rallies")
    a = p.parse_args(argv)

    with open(os.path.join(a.data, "alignment", f"{a.match}.json"), encoding="utf-8") as fh:
        align = json.load(fh)
    if not align.get("accepted"):
        raise SystemExit(f"{a.match} failed the alignment check; use a reserve match")
    load_runtime_dependencies()
    video = os.path.join(a.data, "videos", f"{a.match}.mp4")
    size = (int(align["width"]), int(align["height"]))
    base_corners = sl.load_corners(os.path.join(a.data, "labels", "homography.csv"), a.match)
    corners = sl.scale_corners(base_corners, size)          # this video's pixels, for the pipeline
    # Labels' player_location_* are at BASE_SIZE, so near/far uses the unscaled corners.
    hits = sl.parse_match_hits(os.path.join(a.data, "labels", a.match), base_corners)
    out_dir = os.path.join(a.out, a.match)
    os.makedirs(out_dir, exist_ok=True)
    spans = sl.rally_spans(hits)
    offset = float(align["offset"])
    if a.gaps:
        jobs = [dict(g, kind="gap", hits=hits) for g in gap_windows(spans, offset)]
    else:
        jobs = []
        for span in spans:
            clip_start, duration = clip_window(span["start"], span["end"], offset)
            jobs.append({"name": f"s{span['set']}_r{span['rally']}", "set": span["set"],
                         "rally": span["rally"], "start": clip_start, "duration": duration,
                         "kind": "rally", "hits": span["hits"]})
    if a.limit:
        jobs = jobs[:a.limit]
    t0 = time.time()
    for k, job in enumerate(jobs, 1):
        target = os.path.join(out_dir, f"{job['name']}.npz")
        if os.path.exists(target):
            continue
        run_dir = os.path.join(a.out, "_runs", a.match, job["name"])
        os.makedirs(run_dir, exist_ok=True)
        clip = os.path.join(run_dir, "clip.mp4")
        tmp = target[:-4] + ".part.npz"      # numpy keeps a name that already ends in .npz
        try:
            _cut(video, job["start"], job["duration"], clip)
            sig = run_pipeline_on_clip(clip, run_dir, corners, a.tracknet_mode)
            labels = encode_hits(job["hits"], offset, job["start"], sig.fps, sig.n_frames)
            np.savez_compressed(tmp, fps=sig.fps, first_frame=sig.first_frame, shuttle=sig.shuttle,
                                keypoints=sig.keypoints, court_corners=sig.court_corners,
                                image_size=np.asarray(sig.image_size), match=a.match,
                                set=job["set"], rally=job["rally"], kind=job["kind"], **labels)
            os.replace(tmp, target)          # a killed write never leaves a truncated target
        except Exception as exc:  # one bad clip must not stop a 10-hour build
            print(f"  {job['kind']} {job['name']} failed: {exc!r}")
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
            shutil.rmtree(run_dir, ignore_errors=True)
        print(f"[{k}/{len(jobs)}] {job['name']} "
              f"({(time.time() - t0) / 3600:.2f} h elapsed)", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
