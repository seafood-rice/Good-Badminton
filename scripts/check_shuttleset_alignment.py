"""Accept or reject a fetched ShuttleSet video against its labels (spec 6.2).

Usage:
  python scripts/check_shuttleset_alignment.py --match <video> [--data data/shuttleset]
      [--samples 3] [--tracknet-mode nonoverlap]

Cuts windows around a few labelled rallies (+-12 s), runs TrackNetV3 on each,
finds shuttle turn events, and searches a +-10 s offset between label time and
video time. Writes data/shuttleset/alignment/<video>.json; exit 0 if accepted, 3 if not.
"""
import argparse
import json
import os
import subprocess
import sys

import cv2
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import shuttleset_labels as sl  # noqa: E402
from badminton_analysis.shuttle_track import tracknet as tn  # noqa: E402
from badminton_analysis.stroke.shuttle_turns import shuttle_turn_events  # noqa: E402

MARGIN_SEC = 12.0


def _cut(video, start, duration, out):
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{max(0.0, start):.3f}", "-i", video,
                    "-t", f"{duration:.3f}", "-an", "-c:v", "libx264", "-crf", "16",
                    "-preset", "veryfast", out], check=True)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--match", required=True)
    p.add_argument("--data", default=os.path.join(ROOT, "data", "shuttleset"))
    p.add_argument("--samples", type=int, default=3)
    p.add_argument("--tracknet-mode", default="nonoverlap", choices=["weight", "nonoverlap"])
    a = p.parse_args(argv)

    video = os.path.join(a.data, "videos", f"{a.match}.mp4")
    cap = cv2.VideoCapture(video)
    fps = cap.get(cv2.CAP_PROP_FPS)
    size = (int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)))
    cap.release()
    if not fps or fps <= 0:
        raise SystemExit(f"cannot read {video}")
    if not sl.fps_ok(fps):
        reason = "fps below 30 (resampling would repeat frames)"
        result = {"accepted": False, "reason": reason, "video": a.match, "fps": fps,
                  "width": size[0], "height": size[1]}
        out = os.path.join(a.data, "alignment", f"{a.match}.json")
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, "w", encoding="utf-8") as fh:
            json.dump(result, fh, indent=2)
        print(f"{a.match}: REJECTED {reason} ({fps:.2f} fps) -> {out}")
        return 3

    corners = sl.load_corners(os.path.join(a.data, "labels", "homography.csv"), a.match)
    hits = sl.parse_match_hits(os.path.join(a.data, "labels", a.match), corners)
    spans = [s for s in sl.rally_spans(hits) if len(s["hits"]) >= 6]
    picks = [spans[int(i)] for i in np.linspace(0, len(spans) - 1, min(a.samples, len(spans)))]

    tmp = os.path.join(a.data, "alignment", "_tmp")
    os.makedirs(tmp, exist_ok=True)
    models = tn.load_tracknet(os.path.join(ROOT, "weights", "tracknet.pt"),
                              os.path.join(ROOT, "weights", "inpaintnet.pt"))
    label_t, event_t, win_labels, win_events = [], [], [], []
    for k, span in enumerate(picks):
        w0 = max(0.0, span["start"] - MARGIN_SEC)
        dur = span["end"] - span["start"] + 2 * MARGIN_SEC
        clip = os.path.join(tmp, f"{a.match}_{k}.mp4")
        _cut(video, w0, dur, clip)
        traj = tn.track_video(clip, models, eval_mode=a.tracknet_mode)
        n = max(traj) + 1 if traj else 0
        arr = np.full((n, 2), np.nan)
        for f, pt in traj.items():
            if pt is not None:
                arr[f] = pt
        w_events = [w0 + i / fps for i in shuttle_turn_events(arr, fps)]
        w_labels = [h["t"] for h in span["hits"]]
        win_events.append(w_events)
        win_labels.append(w_labels)
        event_t += w_events
        label_t += w_labels
        os.remove(clip)

    result = sl.best_offset(label_t, event_t)
    per_window = [sl.best_offset(lt, et) for lt, et in zip(win_labels, win_events)]
    result["accepted"] = bool(result["accepted"] and sl.windows_agree(per_window, result["offset"]))
    result.update({"video": a.match, "fps": fps, "width": size[0], "height": size[1],
                   "rule": "pooled_frac>=0.6 and per-window offsets agree within 0.1 s",
                   "sampled_rallies": [[s["set"], s["rally"]] for s in picks],
                   "windows": [{"set": s["set"], "rally": s["rally"], "offset": w["offset"],
                                "matched": w["matched"], "labels": len(lt),
                                "matched_frac": w["matched_frac"]}
                               for s, w, lt in zip(picks, per_window, win_labels)]})
    out = os.path.join(a.data, "alignment", f"{a.match}.json")
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(result, fh, indent=2)
    offsets = ", ".join(f"{w['offset']:+.2f}" for w in per_window)
    print(f"{a.match}: {'ACCEPTED' if result['accepted'] else 'REJECTED'} offset "
          f"{result['offset']:+.2f}s matched {result['matched']}/{len(label_t)} "
          f"(runner-up {result['runner_up']}) window offsets [{offsets}] -> {out}")
    return 0 if result["accepted"] else 3


if __name__ == "__main__":
    raise SystemExit(main())
