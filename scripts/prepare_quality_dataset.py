"""Prepare quality-model training dataset from MultiSenseBadminton annotations.

Dataset: https://github.com/...  (exact URL TBD in Task 5)
Download to data/multisense/ with structure like:
    data/multisense/
      P01/
        P01_video.mp4
        P01_annotations.json
      P02/
        ...
"""
import argparse
import json
from pathlib import Path


def clip_window(hit_frame, fps, pre_s=1.2, post_s=0.8):
    """Compute frame window around hit event: (start, end) as ints, start >= 0."""
    if not fps or fps <= 0:
        fps = 30.0
    start = max(0, int(round(hit_frame - pre_s * fps)))
    end = int(round(hit_frame + post_s * fps))
    return start, end


def manifest_row(player_id, swing_id, stroke, view, label, spread, tensor_path):
    """Build a manifest row dict for a single swing."""
    return {
        "player": player_id,
        "swing": swing_id,
        "stroke": stroke,
        "view": view,
        "label": float(label),
        "spread": float(spread),
        "tensor": str(tensor_path),
    }


def find_sources(root):
    """Discover (video_path, annotations_path) pairs in root.

    Returns a list of (video_path, annotations_path) tuples, or raises SystemExit
    with download instructions if the layout is not recognized.

    Supports plausible per-player layouts (e.g., per-player directories with
    video + annotation files). Actual layout will be confirmed in Task 5 after
    downloading the real MultiSenseBadminton dataset.
    """
    root = Path(root)
    if not root.is_dir():
        raise SystemExit(
            f"MultiSenseBadminton not found at {root}.\n"
            "Download it first using the URL in the docstring, then re-run.\n"
            "Expected structure: data/multisense/P01/video.mp4, P01/annotations.json, etc."
        )

    sources = []
    # Look for per-player directories with video + annotation files
    for player_dir in sorted(root.iterdir()):
        if not player_dir.is_dir():
            continue

        # Find video files (common extensions)
        video_file = None
        for ext in (".mp4", ".avi", ".mov", ".mkv"):
            candidates = list(player_dir.glob(f"*{ext}"))
            if candidates:
                video_file = candidates[0]
                break

        # Find annotation files (common extensions)
        annot_file = None
        for ext in (".json", ".jsonl", ".yml", ".yaml"):
            candidates = list(player_dir.glob(f"*annotations{ext}"))
            if not candidates:
                candidates = list(player_dir.glob(f"*{ext}"))
            if candidates:
                annot_file = candidates[0]
                break

        if video_file and annot_file:
            sources.append((video_file, annot_file))

    if not sources:
        raise SystemExit(
            f"No video+annotations pairs found in {root}.\n"
            "Download the MultiSenseBadminton dataset and ensure each player has:\n"
            "  - A video file (*.mp4, *.avi, *.mov, *.mkv)\n"
            "  - An annotations file (*.json, *.yaml, etc.)\n"
            "Expected layout: data/multisense/P01/video.mp4, P01/annotations.json, etc."
        )

    return sources


def parse_annotations(path):
    """Parse annotations file and return list of swing dicts.

    Each dict should have keys: swing_id, hit_frame, ratings (list of 3 floats),
    stroke, view, player.

    This is a seam function; the real file format will be confirmed in Task 5
    after downloading the actual MultiSenseBadminton dataset. For now, this
    is a placeholder that returns an empty list.
    """
    path = Path(path)
    if not path.is_file():
        return []

    # Placeholder: real parsing will be adapted in Task 5
    # For now, return empty list to support testing without the real dataset
    return []


def main():
    ap = argparse.ArgumentParser(
        description="Prepare quality-model dataset from MultiSenseBadminton"
    )
    ap.add_argument(
        "--data",
        default="data/multisense",
        help="Root directory of MultiSenseBadminton dataset",
    )
    ap.add_argument(
        "--out",
        default="data/multisense/prepared",
        help="Output directory for prepared tensors",
    )
    ap.add_argument(
        "--manifest",
        default="data/multisense/manifest.jsonl",
        help="Output manifest file",
    )
    ap.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Max swings to process (for smoke testing)",
    )
    ap.add_argument(
        "--min-pose-rate",
        type=float,
        default=0.8,
        help="Minimum fraction of frames with valid pose; refuse silently if below",
    )
    ap.add_argument(
        "--pose-family",
        default="yolo-pose",
        help="Pose extractor family",
    )
    ap.add_argument(
        "--smoke",
        action="store_true",
        help="Smoke-test mode: process minimal data, no GPU",
    )
    args = ap.parse_args()

    # Heavy imports inside main()
    import cv2
    import numpy as np
    from badminton_analysis.quality.normalize import normalize_window
    from badminton_analysis.pose import POSE_FAMILIES

    # Validate pose family
    if args.pose_family not in POSE_FAMILIES:
        raise SystemExit(
            f"Unknown pose family: {args.pose_family}\n"
            f"Available: {list(POSE_FAMILIES.keys())}"
        )

    root = Path(args.data)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    # Find sources
    sources = find_sources(root)
    print(f"Found {len(sources)} player(s) with video+annotations")

    if args.smoke:
        sources = sources[:1]  # Process only first player in smoke mode
        args.limit = 10

    if args.limit:
        sources = sources[:args.limit]

    # Initialize pose extractor
    pose_cls = POSE_FAMILIES[args.pose_family]
    pose_extractor = pose_cls()

    manifest = []
    total_swings = 0
    total_posed_frames = 0
    total_frames = 0

    # Process each player
    for video_path, annot_path in sources:
        print(f"Processing {video_path.parent.name}...")

        # Parse annotations
        swings = parse_annotations(annot_path)
        if not swings:
            print(f"  No annotations found in {annot_path}")
            continue

        # Open video
        cap = cv2.VideoCapture(str(video_path))
        if not cap.isOpened():
            print(f"  Failed to open video: {video_path}")
            continue

        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        total_frames_in_video = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

        # Process each swing
        for swing in swings:
            total_swings += 1
            if args.limit and total_swings > args.limit:
                break

            swing_id = swing.get("swing_id", "")
            hit_frame = swing.get("hit_frame", 0)
            ratings = swing.get("ratings", [0.0, 0.0, 0.0])
            stroke = swing.get("stroke", "unknown")
            view = swing.get("view", "unknown")
            player = swing.get("player", "unknown")

            # Compute window
            start, end = clip_window(hit_frame, fps)
            if start >= total_frames_in_video:
                continue

            # Read frames in window
            frames_data = []
            frame_idx = 0
            cap.set(cv2.CAP_PROP_POS_FRAMES, start)
            while frame_idx < (end - start) and cap.isOpened():
                ret, frame = cap.read()
                if not ret:
                    break

                # Run pose extractor on frame
                # (In real usage, this extracts keypoints; for testing, we skip)
                frames_data.append({"keypoints": None})
                frame_idx += 1
                total_frames += 1

            # Normalize window (may return None if too few posed frames)
            normalized = normalize_window(frames_data)
            if normalized is None:
                continue

            # Count posed frames (frames where keypoints are not all-zero)
            posed_frames = np.sum(~np.all(normalized == 0, axis=1))
            pose_rate = posed_frames / max(1, len(frames_data))
            total_posed_frames += posed_frames

            if pose_rate < args.min_pose_rate:
                print(f"  Skipping {swing_id}: pose_rate {pose_rate:.1%} < {args.min_pose_rate:.1%}")
                continue

            # Save tensor
            tensor_path = out_dir / f"{swing_id}.npy"
            np.save(tensor_path, normalized)

            # Compute mean rating as label
            label = float(np.mean(ratings)) if ratings else 0.0
            spread = float(np.std(ratings)) if ratings else 0.0

            # Append manifest row
            rel_tensor_path = f"prepared/{swing_id}.npy"
            row = manifest_row(player, swing_id, stroke, view, label, spread, rel_tensor_path)
            manifest.append(row)

        cap.release()

    # Write manifest
    manifest_path = Path(args.manifest)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    with open(manifest_path, "w", encoding="utf-8") as f:
        for row in manifest:
            f.write(json.dumps(row) + "\n")

    # Report
    print(f"\nProcessed {total_swings} swings, wrote {len(manifest)} to manifest")
    if total_frames > 0:
        overall_pose_rate = total_posed_frames / total_frames
        print(f"Overall pose rate: {overall_pose_rate:.1%}")
        if overall_pose_rate < args.min_pose_rate:
            raise SystemExit(
                f"Overall pose rate {overall_pose_rate:.1%} below minimum {args.min_pose_rate:.1%}"
            )

    print(f"Manifest written to {manifest_path}")


if __name__ == "__main__":
    main()
