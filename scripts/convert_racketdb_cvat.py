"""Convert the RacketDB CVAT project backup into a YOLO detection dataset.

RacketDB's Hugging Face repo ships the full dataset as huge pre-extracted-frame
archives (109-137 GB). The 1 GB `racketdb_cvat_backup.zip` contains the same
content at the source: 112 annotated 4K rally videos + CVAT annotations
(per-frame `shapes` and keyframe-interpolated `tracks`). This script expands it
locally into the Roboflow-style layout `scripts/train_racket_detector.py`
discovers (train/valid/test x images/labels), with frames downscaled to a
longest-side cap and boxes normalized to YOLO format. Frames without any racket
box get an empty label file (negatives). Split is task-level (whole videos stay
in one split) with a fixed seed; RacketDB publishes no official split manifest.
"""
import argparse
import json
import random
import zipfile
from pathlib import Path

import cv2

LABELS = {"racket", "tennis racket"}  # both map to class 0


def _interp(a, b, t):
    return [a[i] + (b[i] - a[i]) * t for i in range(4)]


def track_boxes(track, length):
    """frame -> [x1,y1,x2,y2] for one CVAT track (linear keyframe interpolation)."""
    keys = sorted(track.get("shapes", []), key=lambda s: s["frame"])
    out = {}
    for i, k in enumerate(keys):
        if k.get("outside"):
            continue
        start = k["frame"]
        if i + 1 < len(keys):
            nxt = keys[i + 1]
            for f in range(start, nxt["frame"]):
                t = (f - start) / max(nxt["frame"] - start, 1)
                out[f] = _interp(k["points"], nxt["points"], t)
        else:
            for f in range(start, length):
                out[f] = list(k["points"])
    return out


def task_frame_boxes(ann, length):
    """frame -> list of [x1,y1,x2,y2] from a task's annotations.json content."""
    boxes = {}
    for block in ann:
        for s in block.get("shapes", []):
            if s.get("type") == "rectangle" and s.get("label", "racket") and not s.get("outside"):
                boxes.setdefault(s["frame"], []).append(list(s["points"]))
        for tr in block.get("tracks", []):
            if tr.get("label") not in LABELS:
                continue
            for f, pts in track_boxes(tr, length).items():
                boxes.setdefault(f, []).append(pts)
    return boxes


def convert(zip_path, out_root, max_side=1280, seed=0, limit=None):
    z = zipfile.ZipFile(zip_path)
    tasks = sorted({n.split("/")[0] for n in z.namelist() if n.startswith("task_")},
                   key=lambda t: int(t.split("_")[1]))
    if limit:
        tasks = tasks[:limit]
    rng = random.Random(seed)
    shuffled = tasks[:]
    rng.shuffle(shuffled)
    n = len(shuffled)
    split_of = {}
    for i, t in enumerate(shuffled):
        split_of[t] = "train" if i < n * 0.8 else ("valid" if i < n * 0.9 else "test")

    out_root = Path(out_root)
    for split in ("train", "valid", "test"):
        (out_root / split / "images").mkdir(parents=True, exist_ok=True)
        (out_root / split / "labels").mkdir(parents=True, exist_ok=True)

    totals = {"frames": 0, "boxes": 0, "negatives": 0, "tasks": len(tasks)}
    tmp = out_root / "_tmp_video"
    tmp.mkdir(parents=True, exist_ok=True)
    for task in tasks:
        meta = json.loads(z.read(task + "/task.json"))
        ann = json.loads(z.read(task + "/annotations.json"))
        vids = [name for name in z.namelist()
                if name.startswith(task + "/data/") and name.endswith(".mp4")]
        if not vids:
            print("skip (no video):", task)
            continue
        vpath = tmp / (task + ".mp4")
        vpath.write_bytes(z.read(vids[0]))
        cap = cv2.VideoCapture(str(vpath))
        length = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 0
        boxes = task_frame_boxes(ann, length)
        split = split_of[task]
        stem_base = meta.get("name", task).replace(" ", "_")
        f = -1
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            f += 1
            h, w = frame.shape[:2]
            scale = min(1.0, max_side / max(h, w))
            if scale < 1.0:
                frame = cv2.resize(frame, (int(w * scale), int(h * scale)))
            name = "%s_f%05d" % (stem_base, f)
            cv2.imwrite(str(out_root / split / "images" / (name + ".jpg")),
                        frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
            lines = []
            for x1, y1, x2, y2 in boxes.get(f, []):
                cx = (x1 + x2) / 2 / w
                cy = (y1 + y2) / 2 / h
                bw = abs(x2 - x1) / w
                bh = abs(y2 - y1) / h
                if bw > 0 and bh > 0:
                    lines.append("0 %.6f %.6f %.6f %.6f" % (cx, cy, bw, bh))
            (out_root / split / "labels" / (name + ".txt")).write_text(
                "\n".join(lines) + ("\n" if lines else ""), encoding="ascii")
            totals["frames"] += 1
            totals["boxes"] += len(lines)
            if not lines:
                totals["negatives"] += 1
        cap.release()
        vpath.unlink()
        print("%s -> %s (%d frames, video %s)" % (task, split, f + 1, stem_base))
    try:
        tmp.rmdir()
    except OSError:
        pass
    print("DONE:", totals)
    return totals


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--zip", default="data/racketdb/racketdb_cvat_backup.zip")
    ap.add_argument("--out", default="data/racketdb")
    ap.add_argument("--max-side", type=int, default=1280)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--limit", type=int, default=None,
                    help="Convert only the first N tasks (smoke)")
    args = ap.parse_args()
    convert(args.zip, args.out, max_side=args.max_side, seed=args.seed, limit=args.limit)


if __name__ == "__main__":
    main()
