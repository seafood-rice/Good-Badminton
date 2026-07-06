import json
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import prepare_quality_dataset as pqd
import train_quality_model as tqm
from prepare_quality_dataset import (
    clip_window,
    find_sources,
    manifest_row,
    parse_annotations,
    sync_video,
)
from train_quality_model import bucket_of, player_split


def _write_docs(docs_dir, swings_rows, ratings_by_player):
    """Write the two real-shape MultiSenseBadminton annotation xlsx files.

    swings_rows: list of (player, stroke_num, stroke_type, start_s, stop_s).
    ratings_by_player: dict player -> (r1, r2, r3) Clear skill ratings.
    """
    import pandas as pd

    docs_dir.mkdir(parents=True, exist_ok=True)

    ann_df = pd.DataFrame({
        "Subject Number": [r[0] for r in swings_rows],
        "Annotation Start Time": [r[3] for r in swings_rows],
        "Annotation Stop Time": [r[4] for r in swings_rows],
        "Stroke Num": [r[1] for r in swings_rows],
        # Real column names embed a literal newline before "(Stroke Type)" etc.
        "Annotation Level 1\n(Stroke Type)": [r[2] for r in swings_rows],
    })
    ann_df.to_excel(docs_dir / "Annotation Data File.xlsx", index=False, engine="openpyxl")

    # Real file: row 0 junk header, row 1 human header, rows 2.. data.
    rows = [
        [None, "Expert 1", None, "Expert 2", None, "Expert 3", None],
        ["Subject Number", "Clear Skill Level", "Evaluation Reason",
         "Clear Skill Level", "Evaluation Reason", "Clear Skill Level",
         "Evaluation Reason"],
    ]
    for player, (r1, r2, r3) in ratings_by_player.items():
        rows.append([player, r1, "reason", r2, "reason", r3, "reason"])
    skill_df = pd.DataFrame(rows)
    skill_df.to_excel(docs_dir / "Skill Level Annotation Detail File.xlsx",
                      index=False, header=False, engine="openpyxl")


def test_clip_window_math():
    assert clip_window(90, 30.0) == (54, 114)      # 1.2s pre, 0.8s post @30fps
    assert clip_window(10, 30.0) == (0, 34)        # clamped at 0
    assert clip_window(90, 0) == (54, 114)         # fps<=0 -> fallback 30


def test_manifest_row_shape():
    row = manifest_row("P01", "P01_fc_0007", "high_clear", "front", 4.33, 1.15,
                       "prepared/P01_fc_0007.npy")
    assert row == {"player": "P01", "swing": "P01_fc_0007", "stroke": "high_clear",
                   "view": "front", "label": 4.33, "spread": 1.15,
                   "tensor": "prepared/P01_fc_0007.npy"}


def test_bucket_of():
    assert bucket_of(2.9) == "novice"
    assert bucket_of(3.0) == "intermediate"
    assert bucket_of(6.0) == "expert"


def test_player_split_no_leakage():
    rows = [{"player": "P%02d" % (i % 10), "swing": str(i)} for i in range(100)]
    train, val = player_split(rows, val_fraction=0.2, seed=0)
    train_players = {r["player"] for r in train}
    val_players = {r["player"] for r in val}
    assert train_players.isdisjoint(val_players)
    assert len(val_players) == 2
    assert len(train) + len(val) == 100


def test_player_split_deterministic():
    rows = [{"player": "P%02d" % (i % 10), "swing": str(i)} for i in range(100)]
    a = player_split(rows, val_fraction=0.2, seed=0)
    b = player_split(rows, val_fraction=0.2, seed=0)
    assert a == b


def test_player_split_requires_two_players():
    rows = [{"player": "P01", "swing": "1"}, {"player": "P01", "swing": "2"}]
    with pytest.raises(SystemExit):
        player_split(rows)


def test_find_sources_failure_paths(tmp_path):
    missing_root = tmp_path / "does_not_exist"
    with pytest.raises(SystemExit) as missing_exc:
        find_sources(missing_root)
    msg = str(missing_exc.value).lower()
    assert "multisense" in msg or "figshare" in msg

    empty_root = tmp_path / "empty"
    empty_root.mkdir()
    with pytest.raises(SystemExit) as empty_exc:
        find_sources(empty_root)
    msg = str(empty_exc.value).lower()
    assert "multisense" in msg or "figshare" in msg


def test_parse_annotations_real_layout(tmp_path):
    docs_dir = tmp_path / "Documentations"
    swings_rows = [
        ("Sub00", 1, "Forehand Clear", 1000.0, 1004.0),
        ("Sub00", 2, "Forehand Clear", 1010.0, 1014.5),
        ("Sub00", 3, "Backhand Driving", 1020.0, 1023.0),
        ("Sub01", 1, "Forehand Clear", 2000.0, 2004.2),
        ("Sub01", 2, "Forehand Clear", 2010.0, 2013.8),
        ("Sub01", 3, "Backhand Driving", 2020.0, 2022.5),
    ]
    # All 25 canonical players get skill ratings (real-file invariant), even
    # though only Sub00/Sub01 have swings in this fixture's annotation file.
    ratings_by_player = {"Sub%02d" % i: (i % 7 + 1, (i + 1) % 7 + 1, (i + 2) % 7 + 1)
                         for i in range(25)}
    _write_docs(docs_dir, swings_rows, ratings_by_player)

    swings, ratings = parse_annotations(docs_dir)

    # Only Forehand Clear rows survive, sorted by (player, stroke_num).
    assert len(swings) == 4
    assert swings == sorted(swings, key=lambda s: (s["player"], s["stroke_num"]))
    for s in swings:
        assert set(s) == {"player", "stroke_num", "start_s", "stop_s"}
    assert [s["player"] for s in swings] == ["Sub00", "Sub00", "Sub01", "Sub01"]
    assert swings[0] == {"player": "Sub00", "stroke_num": 1, "start_s": 1000.0,
                         "stop_s": 1004.0}

    assert len(ratings) == 25
    assert ratings["Sub00"] == [1.0, 2.0, 3.0]
    assert ratings["Sub01"] == [2.0, 3.0, 4.0]
    assert all(len(v) == 3 and all(isinstance(x, float) for x in v)
              for v in ratings.values())


def test_parse_annotations_missing_files_exit(tmp_path):
    empty_docs = tmp_path / "Documentations"
    empty_docs.mkdir()
    with pytest.raises(SystemExit) as exc:
        parse_annotations(empty_docs)
    assert "figshare" in str(exc.value).lower()


def test_find_sources_views_and_variants(tmp_path):
    root = tmp_path

    docs_dir = root / "Documentations"
    docs_dir.mkdir()
    (docs_dir / "Annotation Data File.xlsx").write_bytes(b"stub")

    sub00 = root / "Sub00"
    sub00.mkdir()
    (sub00 / "Forehand Clear Front Video.mov").write_bytes(b"stub")
    (sub00 / "Forehand Clearing Side Video.mov").write_bytes(b"stub")  # naming variant

    sub01 = root / "Sub01"
    sub01.mkdir()
    (sub01 / "Backhand Drive Front Video.mov").write_bytes(b"stub")  # excluded

    sources = find_sources(root)

    assert sources == [
        ("Sub00", "front", sub00 / "Forehand Clear Front Video.mov"),
        ("Sub00", "side", sub00 / "Forehand Clearing Side Video.mov"),
    ]


def test_sync_video_recovers_known_offset(tmp_path):
    """Motion-energy fit recovers a known (t0, fps) from a short synthetic video."""
    import cv2

    fps = 30.0
    duration_s = 40.0
    n_frames = int(duration_s * fps)
    width, height = 160, 120
    burst_windows = [(3.0, 4.0), (10.0, 11.2), (18.0, 19.5), (26.0, 27.0), (34.0, 35.3)]

    video_path = tmp_path / "sync_test.mp4"
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(str(video_path), fourcc, fps, (width, height))
    rng = np.random.default_rng(0)
    for i in range(n_frames):
        t = i / fps
        if any(s <= t < e for s, e in burst_windows):
            frame = rng.integers(0, 256, (height, width, 3), dtype=np.uint8)
        else:
            frame = np.zeros((height, width, 3), dtype=np.uint8)
        writer.write(frame)
    writer.release()

    t0_true = 1000.0
    windows = [(t0_true + s, t0_true + e) for s, e in burst_windows]

    cache_path = tmp_path / "sync_cache.npz"
    result = sync_video(video_path, windows, cache_path)

    assert result is not None
    assert abs(result["t0"] - t0_true) < 0.5
    assert result["z"] > 5
    assert abs(result["fps_eff"] - 30.0) < 0.5
    assert cache_path.is_file()


def test_trainer_end_to_end_smoke(tmp_path, monkeypatch):
    """CPU-only e2e smoke: synthetic manifest+tensors -> trained TorchScript export.

    Would have caught C3 (string-bucket torch.tensor crash) and I4 (aliased
    best-checkpoint) before they reached the TorchScript export step.
    """
    import torch

    prepared_dir = tmp_path / "prepared"
    prepared_dir.mkdir()

    rows = []
    labels = np.linspace(2.0, 6.0, 12)
    idx = 0
    for p in range(1, 4):
        player = "P%02d" % p
        for s in range(4):
            swing_id = "%s_hc_%04d" % (player, s)
            tensor = np.random.rand(64, 34).astype(np.float32)
            tensor_path = prepared_dir / (swing_id + ".npy")
            np.save(tensor_path, tensor)
            # Absolute tensor path so the default --data-dir (unused here) is
            # irrelevant: data_dir / <absolute path> resolves to the absolute path.
            row = manifest_row(player, swing_id, "high_clear", "front",
                               float(labels[idx]), 0.3, str(tensor_path))
            rows.append(row)
            idx += 1

    manifest_path = tmp_path / "manifest.jsonl"
    with open(manifest_path, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")

    out_path = tmp_path / "q.pt"
    argv = ["train_quality_model.py",
           "--manifest", str(manifest_path),
           "--smoke",
           "--out", str(out_path),
           "--device", "cpu"]
    monkeypatch.setattr(sys, "argv", argv)

    tqm.main()

    assert out_path.is_file()
    loaded = torch.jit.load(str(out_path))
    assert loaded is not None


def test_prep_end_to_end_smoke(tmp_path, monkeypatch, capsys):
    """CPU-only e2e smoke against the real layout: real xlsx annotations +
    real find_sources discovery + synthetic video, with sync_video and
    build_pose_extractor monkeypatched (motion-energy fitting and real pose
    models each have their own dedicated tests).

    Also covers the _sync gate: a monkeypatched low-z mapping must skip the
    video (no manifest rows) and print SYNC FAIL, not raise.
    """
    import cv2

    data_root = tmp_path / "multisense"
    docs_dir = data_root / "Documentations"

    ratings_by_player = {"Sub%02d" % i: (i % 7 + 1, (i + 1) % 7 + 1, (i + 2) % 7 + 1)
                         for i in range(25)}
    swings_rows = [("Sub00", 1, "Forehand Clear", 0.0, 1.5)]
    _write_docs(docs_dir, swings_rows, ratings_by_player)

    sub_dir = data_root / "Sub00"
    sub_dir.mkdir(parents=True)
    video_path = sub_dir / "Forehand Clear Front Video.mp4"
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(str(video_path), fourcc, 30.0, (64, 64))
    frame = np.zeros((64, 64, 3), dtype=np.uint8)
    for _ in range(90):
        writer.write(frame)
    writer.release()

    class _FakePoseExtractor:
        def process_frame(self, frame):
            kp = np.zeros((1, 17, 2), dtype=np.float32)
            for i in range(17):
                kp[0, i] = (10.0 + i * 5.0, 20.0 + i * 3.0)
            return kp, None

    monkeypatch.setattr(pqd, "build_pose_extractor", lambda **kwargs: _FakePoseExtractor())

    out_dir = data_root / "prepared"

    # -- Scenario 1: good sync -> the swing lands in the manifest.
    monkeypatch.setattr(
        pqd, "sync_video",
        lambda video_path, windows, cache_path:
            {"t0": 0.0, "fps_eff": 30.0, "z": 9.0, "nframes": 90, "vfps": 30.0})

    manifest_path = data_root / "manifest.jsonl"
    argv = ["prepare_quality_dataset.py",
           "--data", str(data_root),
           "--out", str(out_dir),
           "--manifest", str(manifest_path),
           "--min-pose-rate", "0.0"]
    monkeypatch.setattr(sys, "argv", argv)

    pqd.main()

    assert manifest_path.is_file()
    lines = [l for l in manifest_path.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(lines) == 1
    row = json.loads(lines[0])
    assert row["player"] == "Sub00"
    assert row["view"] == "front"
    assert row["stroke"] == "high_clear"
    assert row["swing"] == "Sub00_front_0001"
    assert row["tensor"] == "prepared/Sub00_front_0001.npy"
    assert row["sync_z"] == pytest.approx(9.0)
    tensor_full_path = data_root / row["tensor"]
    arr = np.load(tensor_full_path)
    assert arr.shape == (64, 34)

    # -- Scenario 2: low-z sync -> video skipped, no manifest rows, SYNC FAIL logged.
    monkeypatch.setattr(
        pqd, "sync_video",
        lambda video_path, windows, cache_path:
            {"t0": 0.0, "fps_eff": 30.0, "z": 2.0, "nframes": 90, "vfps": 30.0})

    manifest_path2 = data_root / "manifest2.jsonl"
    argv2 = ["prepare_quality_dataset.py",
            "--data", str(data_root),
            "--out", str(out_dir),
            "--manifest", str(manifest_path2),
            "--min-pose-rate", "0.0"]
    monkeypatch.setattr(sys, "argv", argv2)
    capsys.readouterr()  # discard scenario-1 output

    pqd.main()

    captured = capsys.readouterr()
    assert "SYNC FAIL" in captured.out
    lines2 = [l for l in manifest_path2.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(lines2) == 0


def test_prep_pose_rate_uses_raw_window_length(tmp_path, monkeypatch):
    """Regression: pose_rate must be raw-posed-frames / raw-window-length, not
    (resampled-tensor non-zero rows) / raw-window-length.

    normalize_window always resamples to TARGET_FRAMES (64) rows, so counting
    non-zero rows of that tensor and dividing by the raw window length makes
    the rate length-dependent: a >64-frame window with every frame perfectly
    posed used to score 64/N (e.g. 64/120 ~= 0.53) and wrongly fail the
    default 0.8 gate. Here every one of ~120 raw frames is posed, so the true
    rate is 1.0 and the swing must land in the manifest.
    """
    import cv2

    data_root = tmp_path / "multisense"
    docs_dir = data_root / "Documentations"

    ratings_by_player = {"Sub%02d" % i: (i % 7 + 1, (i + 1) % 7 + 1, (i + 2) % 7 + 1)
                         for i in range(25)}
    # 4.0s window @ 30fps -> 120 raw frames, well over TARGET_FRAMES (64).
    swings_rows = [("Sub00", 1, "Forehand Clear", 0.0, 4.0)]
    _write_docs(docs_dir, swings_rows, ratings_by_player)

    sub_dir = data_root / "Sub00"
    sub_dir.mkdir(parents=True)
    video_path = sub_dir / "Forehand Clear Front Video.mp4"
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(str(video_path), fourcc, 30.0, (64, 64))
    frame = np.zeros((64, 64, 3), dtype=np.uint8)
    for _ in range(150):
        writer.write(frame)
    writer.release()

    class _FakePoseExtractor:
        def process_frame(self, frame):
            # Every frame fully posed: valid keypoints (incl. both hips) throughout.
            kp = np.zeros((1, 17, 2), dtype=np.float32)
            for i in range(17):
                kp[0, i] = (10.0 + i * 5.0, 20.0 + i * 3.0)
            return kp, None

    monkeypatch.setattr(pqd, "build_pose_extractor", lambda **kwargs: _FakePoseExtractor())
    monkeypatch.setattr(
        pqd, "sync_video",
        lambda video_path, windows, cache_path:
            {"t0": 0.0, "fps_eff": 30.0, "z": 9.0, "nframes": 150, "vfps": 30.0})

    out_dir = data_root / "prepared"
    manifest_path = data_root / "manifest.jsonl"
    argv = ["prepare_quality_dataset.py",
           "--data", str(data_root),
           "--out", str(out_dir),
           "--manifest", str(manifest_path)]
    monkeypatch.setattr(sys, "argv", argv)

    # Default --min-pose-rate is 0.8: must not exit despite the window being
    # far longer than the 64-row resampled tensor.
    pqd.main()

    assert manifest_path.is_file()
    lines = [l for l in manifest_path.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(lines) == 1
    row = json.loads(lines[0])
    assert row["swing"] == "Sub00_front_0001"
