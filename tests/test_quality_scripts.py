import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from prepare_quality_dataset import clip_window, manifest_row
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
