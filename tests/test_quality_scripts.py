import json
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import prepare_quality_dataset as pqd
import train_quality_model as tqm
from prepare_quality_dataset import clip_window, find_sources, manifest_row
from train_quality_model import bucket_of, player_split


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


def test_prep_end_to_end_smoke(tmp_path, monkeypatch):
    """CPU-only e2e smoke: synthetic video + fake pose extractor -> manifest+tensor.

    Would have caught C1 (fictional POSE_FAMILIES import) and C2 (pose extractor
    constructed but never called).
    """
    import cv2

    class _FakePoseExtractor:
        def process_frame(self, frame):
            kp = np.zeros((1, 17, 2), dtype=np.float32)
            for i in range(17):
                kp[0, i] = (10.0 + i * 5.0, 20.0 + i * 3.0)
            return kp, None

    monkeypatch.setattr(pqd, "build_pose_extractor", lambda **kwargs: _FakePoseExtractor())

    swing = {"swing_id": "P01_fc_0001", "hit_frame": 30, "ratings": [4.0, 4.5, 5.0],
             "stroke": "high_clear", "view": "front", "player": "P01"}
    monkeypatch.setattr(pqd, "parse_annotations", lambda path: [dict(swing)])

    video_path = tmp_path / "P01_video.mp4"
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(str(video_path), fourcc, 30.0, (64, 64))
    frame = np.zeros((64, 64, 3), dtype=np.uint8)
    for _ in range(60):
        writer.write(frame)
    writer.release()

    monkeypatch.setattr(pqd, "find_sources", lambda root: [(str(video_path), "unused.json")])

    out_dir = tmp_path / "prepared"
    manifest_path = tmp_path / "manifest.jsonl"
    argv = ["prepare_quality_dataset.py",
           "--data", str(tmp_path),
           "--out", str(out_dir),
           "--manifest", str(manifest_path),
           "--min-pose-rate", "0.0"]
    monkeypatch.setattr(sys, "argv", argv)

    pqd.main()

    assert manifest_path.is_file()
    lines = [l for l in manifest_path.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(lines) == 1
    row = json.loads(lines[0])
    tensor_full_path = tmp_path / row["tensor"]
    arr = np.load(tensor_full_path)
    assert arr.shape == (64, 34)
