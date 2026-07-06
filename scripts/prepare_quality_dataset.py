"""Prepare quality-model training dataset from MultiSenseBadminton annotations.

Dataset: MultiSenseBadminton (Nature Scientific Data 2024), figshare collection
doi 10.6084/m9.figshare.c.6725706.v1. Download the front/side Forehand Clear
videos plus Documentations/ (both annotation xlsx files) to data/multisense/:

    data/multisense/
      Documentations/
        Annotation Data File.xlsx
        Skill Level Annotation Detail File.xlsx
      Sub00/
        Forehand Clear Front Video.mov
        Forehand Clear Side Video.mov
      Sub01/ ... Sub24/
        ...

v1 uses Forehand Clear swings only (-> stroke "high_clear"), front + side
views. There is no official video<->annotation sync; sync_video() derives one
from motion energy (see its docstring).
"""
import argparse
import json
from pathlib import Path

FIGSHARE_DOI = "10.6084/m9.figshare.c.6725706.v1"


def _dataset_missing_message(path):
    return (
        f"MultiSenseBadminton data not found at {path}.\n"
        f"Download it from the figshare collection (doi {FIGSHARE_DOI}) and "
        f"place it under {path}, then re-run."
    )


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
    """Discover per-subject Forehand Clear videos.

    Returns list of (player, view, video_path) with view in {"front", "side"},
    sorted by (player, view). Tolerant matching: video filename contains
    'forehand' and ('front' or 'side') and ends with .mov/.mp4
    (case-insensitive), so naming variants like 'Forehand Clearing Front
    Video.mov' still match.

    Subject dirs are children of root matching Sub* (Documentations/ and any
    non-Sub* entry are skipped). Raises SystemExit with download instructions
    if root is missing or nothing is found.
    """
    root = Path(root)
    if not root.is_dir():
        raise SystemExit(_dataset_missing_message(root))

    sources = []
    for player_dir in sorted(root.iterdir()):
        if not player_dir.is_dir() or not player_dir.name.startswith("Sub"):
            continue
        player = player_dir.name

        for video_path in sorted(player_dir.iterdir()):
            if not video_path.is_file():
                continue
            name_lower = video_path.name.lower()
            if not name_lower.endswith((".mov", ".mp4")):
                continue
            if "forehand" not in name_lower:
                continue
            if "front" in name_lower:
                view = "front"
            elif "side" in name_lower:
                view = "side"
            else:
                continue
            sources.append((player, view, video_path))

    sources.sort(key=lambda s: (s[0], s[1]))

    if not sources:
        raise SystemExit(_dataset_missing_message(root))

    return sources


def parse_annotations(docs_dir):
    """Read both xlsx files. Returns (swings, ratings):
    swings: list of dicts {player, stroke_num, start_s, stop_s} — Forehand Clear
            only, sorted by (player, stroke_num)
    ratings: dict player -> list of 3 floats (expert Clear ratings)
    Raises SystemExit with download instructions if either file is missing.
    """
    import pandas as pd

    docs_dir = Path(docs_dir)
    annotation_path = docs_dir / "Annotation Data File.xlsx"
    skill_path = docs_dir / "Skill Level Annotation Detail File.xlsx"

    missing = [p for p in (annotation_path, skill_path) if not p.is_file()]
    if missing:
        names = ", ".join(p.name for p in missing)
        raise SystemExit(
            f"Missing MultiSenseBadminton annotation file(s): {names}\n"
            f"Download the dataset from the figshare collection (doi {FIGSHARE_DOI}) "
            f"and place the Documentations/ folder at {docs_dir}, then re-run."
        )

    # Annotation Data File.xlsx: one row per swing, single (Korean-named) sheet.
    df = pd.read_excel(annotation_path, sheet_name=0, engine="openpyxl")
    df.columns = [c.replace("\n", " ") for c in df.columns]
    clears = df[df["Annotation Level 1 (Stroke Type)"] == "Forehand Clear"]

    swings = [
        {
            "player": row["Subject Number"],
            "stroke_num": int(row["Stroke Num"]),
            "start_s": float(row["Annotation Start Time"]),
            "stop_s": float(row["Annotation Stop Time"]),
        }
        for _, row in clears.iterrows()
    ]
    swings.sort(key=lambda s: (s["player"], s["stroke_num"]))

    # Skill Level Annotation Detail File.xlsx: awkward shape — row 0 is a junk
    # pandas header, row 1 is the human header, rows 2.. are data (one row per
    # player). Slice past the two header rows, then skip anything left that
    # isn't a real "SubNN" row (defensive against stray/blank rows).
    skill_df = pd.read_excel(skill_path, header=None, engine="openpyxl")
    ratings = {}
    for _, row in skill_df.iloc[2:].iterrows():
        subject = row[0]
        if not isinstance(subject, str) or not subject.startswith("Sub"):
            continue
        try:
            ratings[subject] = [float(row[1]), float(row[3]), float(row[5])]
        except (TypeError, ValueError):
            raise SystemExit(
                f"Non-numeric Clear skill rating for {subject} in {skill_path}."
            )

    if len(ratings) != 25 or any(len(v) != 3 for v in ratings.values()):
        raise SystemExit(
            f"Expected 25 players with 3 Clear ratings each in {skill_path}, "
            f"found {len(ratings)} player(s). Check the sheet layout."
        )

    return swings, ratings


def sync_video(video_path, windows, cache_path):
    """Derive (t0, fps_eff, corr_z) mapping annotation epochs to video frames.

    windows: list of (start_s, stop_s) epoch pairs for this subject's swings.
    Returns dict {t0, fps_eff, z, nframes, vfps} or None if the video can't be
    read. Caches the motion-energy signal at cache_path (.npz) — recomputes
    only if absent.

    Validated method: there is no official video<->annotation sync (.mov
    creation_time is an anonymization-export date, not a capture time; the
    hdf5 sensor logs carry no camera stream). Frame-difference motion energy
    is correlated against the annotated swing windows via a joint grid search
    over (video-start epoch t0, effective fps) — clock skew means the
    effective fps can differ slightly from the nominal metadata fps.
    """
    import cv2
    import numpy as np

    if not windows:
        return None

    video_path = Path(video_path)
    cache_path = Path(cache_path)

    if cache_path.is_file():
        cached = np.load(cache_path)
        fidx = cached["fidx"]
        m = cached["m"]
        vfps = float(cached["vfps"])
        nframes = int(cached["nframes"])
    else:
        cap = cv2.VideoCapture(str(video_path))
        if not cap.isOpened():
            return None
        vfps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        nframes = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        stride = max(1, round(vfps / 10.0))  # ~10 Hz motion-energy sampling

        fidx_list = []
        m_list = []
        prev = None
        idx = 0
        while True:
            ok = cap.grab()
            if not ok:
                break
            if idx % stride == 0:
                ok2, frame = cap.retrieve()
                if not ok2:
                    break
                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                small = cv2.resize(gray, (160, 120)).astype(np.float32)
                if prev is not None:
                    m_list.append(float(np.mean(np.abs(small - prev))))
                    fidx_list.append(idx)
                prev = small
            idx += 1
        cap.release()

        fidx = np.array(fidx_list, dtype=np.int64)
        m = np.array(m_list, dtype=np.float32)
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        np.savez(cache_path, fidx=fidx, m=m, vfps=vfps, nframes=nframes)

    fidx = np.asarray(fidx, dtype=np.float64)
    m = np.asarray(m, dtype=np.float64)
    if m.size < 2:
        return None

    from scipy.signal import fftconvolve

    mz = (m - m.mean()) / (m.std() + 1e-9)

    starts = [w[0] for w in windows]
    stops = [w[1] for w in windows]
    grid = np.arange(min(starts) - 2200, max(stops) + 300, 0.1)
    ann = np.zeros_like(grid)
    for start_s, stop_s in windows:
        ann[(grid >= start_s) & (grid < stop_s)] = 1.0
    annz = (ann - ann.mean()) / (ann.std() + 1e-9)

    best = None
    for fps_eff in np.arange(27.5, 30.11, 0.01):
        sample_t = fidx / fps_eff
        vt = np.arange(0.0, sample_t[-1], 0.1)
        if vt.size < 2 or vt.size >= annz.size:
            continue
        mu = np.interp(vt, sample_t, mz)
        corr = fftconvolve(annz, mu[::-1], mode="valid") / len(mu)
        if corr.size == 0:
            continue
        k = int(np.argmax(corr))
        peak = float(corr[k])
        if best is None or peak > best["peak"]:
            best = {"peak": peak, "t0": float(grid[k]), "fps_eff": float(fps_eff),
                    "corr": corr}

    if best is None:
        return None

    corr = best["corr"]
    z = float((best["peak"] - corr.mean()) / (corr.std() + 1e-9))

    return {
        "t0": best["t0"],
        "fps_eff": best["fps_eff"],
        "z": z,
        "nframes": nframes,
        "vfps": float(vfps),
    }


def build_pose_extractor(pose_family="yolo-pose", model_path="weights/yolo11n-pose.pt"):
    """Construct a pose processor, mirroring
    PostureAnalysisSystem._build_pose_processor (badminton_analysis/posture/system.py).

    Constructed lazily — heavy imports stay inside this function — so tests can
    monkeypatch this seam instead of loading a real model.
    """
    if pose_family == "yolo-pose":
        from badminton_analysis.detection.yolo_pose import YOLOPoseProcessor
        return YOLOPoseProcessor(model_path=model_path)
    from badminton_analysis.detection.rtmpose import RTMPoseProcessor
    return RTMPoseProcessor(mode="balanced", pose_family=pose_family)


def _pick_largest_person(keypoints):
    """Return the largest keypoint-spread person's (17, 2) array.

    Mirrors the single-player heuristic in badminton_analysis/posture/system.py
    (PostureAnalysisSystem._capture_frame's `_spread`/`best_i` block).
    """
    def _spread(person):
        xs = person[:, 0]
        ys = person[:, 1]
        return float((xs.max() - xs.min()) + (ys.max() - ys.min()))

    best_i = max(range(len(keypoints)), key=lambda i: _spread(keypoints[i]))
    return keypoints[best_i].astype(float)


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
        help="Max total swings to process across all sources (for smoke testing)",
    )
    ap.add_argument(
        "--min-pose-rate",
        type=float,
        default=0.8,
        help="Minimum fraction of frames with valid pose; exits loudly (before "
             "writing the manifest) if the overall rate is below this",
    )
    ap.add_argument(
        "--min-sync-z",
        type=float,
        default=5.0,
        help="Minimum video/annotation sync correlation z-score; videos scoring "
             "below this are skipped (not fatal)",
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
    from badminton_analysis.quality.normalize import normalize_window, posed_frames

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    sync_dir = out_dir / "_sync"
    sync_dir.mkdir(parents=True, exist_ok=True)

    swings, ratings = parse_annotations(Path(args.data) / "Documentations")
    swings_by_player = {}
    for swing in swings:
        swings_by_player.setdefault(swing["player"], []).append(swing)

    sources = find_sources(args.data)
    print(f"Found {len(sources)} video(s)")

    if args.smoke:
        sources = sources[:1]  # Process only first video in smoke mode
        args.limit = 10

    # Initialize pose extractor once (real construction stays mockable/lazy).
    pose_extractor = build_pose_extractor(pose_family=args.pose_family)

    manifest = []
    total_swings = 0
    total_posed_frames = 0
    total_frames = 0

    # Process each (player, view) video
    for player, view, video_path in sources:
        if args.limit and total_swings >= args.limit:
            break

        video_path = Path(video_path)
        player_swings = swings_by_player.get(player, [])
        if not player_swings:
            print(f"{player}/{view}: no annotated Forehand Clear swings, skipping")
            continue

        windows = [(s["start_s"], s["stop_s"]) for s in player_swings]
        cache_path = sync_dir / f"{player}_{view}.npz"
        sync = sync_video(video_path, windows, cache_path)

        if sync is None or sync["z"] < args.min_sync_z:
            z_str = f"{sync['z']:.2f}" if sync is not None else "n/a"
            print(f"SYNC FAIL {player}/{view} z={z_str}")
            continue

        t0 = sync["t0"]
        fps_eff = sync["fps_eff"]
        nframes = sync["nframes"]

        cap = cv2.VideoCapture(str(video_path))
        if not cap.isOpened():
            print(f"  Failed to open video: {video_path}")
            continue

        usable = 0
        skipped_tail = 0
        skipped_pose = 0

        # Process each swing using the full annotated window (no clip_window
        # pre/post padding here — that heuristic assumed a hit-frame event;
        # we have exact annotated start/stop epochs instead).
        for swing in player_swings:
            total_swings += 1
            if args.limit and total_swings > args.limit:
                break

            start_f = round((swing["start_s"] - t0) * fps_eff)
            end_f = round((swing["stop_s"] - t0) * fps_eff)
            unclamped_len = end_f - start_f
            c_start = min(max(start_f, 0), nframes)
            c_end = min(max(end_f, 0), nframes)
            clamped_len = c_end - c_start

            if (unclamped_len <= 0 or clamped_len <= 0
                    or clamped_len < 0.6 * unclamped_len):
                skipped_tail += 1
                continue

            # Read frames in window, extracting pose per frame
            frames_data = []
            cap.set(cv2.CAP_PROP_POS_FRAMES, c_start)
            frame_idx = 0
            while frame_idx < clamped_len:
                ret, frame = cap.read()
                if not ret:
                    break

                keypoints, scores = pose_extractor.process_frame(frame)
                kp = None
                if keypoints is not None and len(keypoints) > 0:
                    kp = _pick_largest_person(keypoints)
                frames_data.append({"keypoints": kp})
                frame_idx += 1
                total_frames += 1

            # Pose rate against the raw window length (not the fixed-length
            # resampled tensor normalize_window produces).
            posed = posed_frames(frames_data)
            pose_rate = len(posed) / max(1, len(frames_data))
            total_posed_frames += len(posed)

            # Normalize window (may return None if too few posed frames)
            normalized = normalize_window(frames_data)
            if normalized is None:
                skipped_pose += 1
                continue

            if pose_rate < args.min_pose_rate:
                skipped_pose += 1
                continue

            swing_id = f"{player}_{view}_{int(swing['stroke_num']):04d}"

            # Save tensor
            tensor_path = out_dir / f"{swing_id}.npy"
            np.save(tensor_path, normalized)

            # Ratings are per player per stroke (not per swing): every
            # Forehand Clear swing of a player shares that player's label/spread.
            player_ratings = ratings.get(player, [])
            label = float(np.mean(player_ratings)) if player_ratings else 0.0
            spread = float(np.std(player_ratings)) if player_ratings else 0.0

            # Append manifest row
            rel_tensor_path = f"prepared/{swing_id}.npy"
            row = manifest_row(player, swing_id, "high_clear", view, label, spread,
                               rel_tensor_path)
            row["sync_z"] = float(sync["z"])
            manifest.append(row)
            usable += 1

        cap.release()
        print(f"{player}/{view}: synced z={sync['z']:.2f} usable={usable} "
              f"skipped_tail={skipped_tail} skipped_pose={skipped_pose}")

    # Report + gate BEFORE writing the manifest: a below-threshold run must not
    # leave a manifest.jsonl on disk pointing at an under-posed dataset.
    print(f"\nProcessed {total_swings} swings, {len(manifest)} candidate manifest row(s)")
    if total_frames > 0:
        overall_pose_rate = total_posed_frames / total_frames
        print(f"Overall pose rate: {overall_pose_rate:.1%}")
        if overall_pose_rate < args.min_pose_rate:
            raise SystemExit(
                f"Overall pose rate {overall_pose_rate:.1%} below minimum {args.min_pose_rate:.1%}.\n"
                f"Exiting before writing manifest.jsonl; discard any partial .npy tensors "
                f"already written under {out_dir}."
            )

    # Write manifest only after the pose-rate gate has passed.
    manifest_path = Path(args.manifest)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    with open(manifest_path, "w", encoding="utf-8") as f:
        for row in manifest:
            f.write(json.dumps(row) + "\n")

    print(f"Wrote {len(manifest)} row(s) to manifest: {manifest_path}")


if __name__ == "__main__":
    main()
