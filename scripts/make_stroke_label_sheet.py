"""Write the DJI stroke-label sheet and one burnt-in-time clip per analysed video.

Usage:
  python scripts/make_stroke_label_sheet.py [--results-dir outputs] [--videos-dir videos] \
      [--out outputs/dji-stroke-labels] --test <video> [--test <video> ...] <video> [<video> ...]
  python scripts/make_stroke_label_sheet.py --progress [--out outputs/dji-stroke-labels]

Each <video> is a result folder name under --results-dir that has hits.json. Videos named
with --test go to the test split, the rest to train. An existing LABELS.md is never
overwritten unless --force is given (it holds the owner's labels).
"""
import argparse
import glob
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from badminton_analysis.stroke import stroke_labels as sl  # noqa: E402

CLIP_WIDTH = 1280


def find_video(videos_dir, name):
    for path in sorted(glob.glob(os.path.join(videos_dir, glob.escape(name) + ".*"))):
        if os.path.isfile(path):
            return path
    return None


def burn_in_clip(src, dst, width=CLIP_WIDTH):
    """Copy of src at `width` px wide with the source time (s) drawn top-left."""
    import cv2
    cap = cv2.VideoCapture(src)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    writer, k = None, 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        h, w = frame.shape[:2]
        if w > width:
            frame = cv2.resize(frame, (width, int(round(h * width / w))), interpolation=cv2.INTER_AREA)
        if writer is None:
            writer = cv2.VideoWriter(dst, cv2.VideoWriter_fourcc(*"mp4v"), fps,
                                     (frame.shape[1], frame.shape[0]))
        text = f"{k / fps:.2f}s"
        cv2.putText(frame, text, (12, 44), cv2.FONT_HERSHEY_SIMPLEX, 1.3, (0, 0, 0), 6, cv2.LINE_AA)
        cv2.putText(frame, text, (12, 44), cv2.FONT_HERSHEY_SIMPLEX, 1.3, (255, 255, 255), 2, cv2.LINE_AA)
        writer.write(frame)
        k += 1
    cap.release()
    if writer is not None:
        writer.release()
    return k


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("videos", nargs="*")
    p.add_argument("--results-dir", default=os.path.join(ROOT, "outputs"))
    p.add_argument("--videos-dir", default=os.path.join(ROOT, "videos"))
    p.add_argument("--out", default=os.path.join(ROOT, "outputs", "dji-stroke-labels"))
    p.add_argument("--test", action="append", default=[])
    p.add_argument("--force", action="store_true")
    p.add_argument("--no-clips", action="store_true")
    p.add_argument("--progress", action="store_true")
    a = p.parse_args(argv)
    sheet_path = os.path.join(a.out, "LABELS.md")

    if a.progress:
        with open(sheet_path, encoding="utf-8") as fh:
            prog = sl.progress(sl.parse_sheet(fh.read()))
        print("group   " + "  ".join(f"{s:>5}" for s in sl.SPLITS))
        for g in sl.GROUPS:
            print(f"{g:<7} " + "  ".join(f"{prog[s][g]:>5}" for s in sl.SPLITS))
        return 0
    if os.path.exists(sheet_path) and not a.force:
        print(f"{sheet_path} exists and may hold labels; pass --force to overwrite", file=sys.stderr)
        return 2
    if not a.videos:
        p.error("name at least one analysed video")
    unknown_test = set(a.test) - set(a.videos)
    if unknown_test:
        p.error(f"--test names videos not listed: {sorted(unknown_test)}")

    os.makedirs(a.out, exist_ok=True)
    entries = []
    for name in a.videos:
        with open(os.path.join(a.results_dir, name, "hits.json"), encoding="utf-8") as fh:
            payload = json.load(fh)
        fps = float(payload["fps"])
        hits = [{"frame": int(h["frame"]), "hitter": h.get("hitter"),
                 "time_sec": h["time_sec"] if h.get("time_sec") is not None else (int(h["frame"]) - 1) / fps}
                for h in payload.get("hits", [])]
        clip = f"{name}.mp4"
        if not a.no_clips:
            src = find_video(a.videos_dir, name)
            if src is None:
                raise SystemExit(f"source video for {name!r} not found in {a.videos_dir}")
            n = burn_in_clip(src, os.path.join(a.out, clip))
            print(f"{name}: {n} frames -> {clip}")
        entries.append({"name": name, "split": "test" if name in a.test else "train",
                        "fps": fps, "clip": clip, "hits": hits})
    with open(sheet_path, "w", encoding="utf-8") as fh:
        fh.write(sl.write_sheet(entries))
    print(f"wrote {sheet_path}: {sum(len(e['hits']) for e in entries)} rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
