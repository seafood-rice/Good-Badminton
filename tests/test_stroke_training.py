import json
import os
import sys

import numpy as np
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import train_stroke_classifier as tsc  # noqa: E402

from badminton_analysis.stroke import stroke_labels as sl  # noqa: E402
from badminton_analysis.stroke import stroke_model as smod  # noqa: E402
from badminton_analysis.stroke.hit_signals import save_signals  # noqa: E402
from tests.test_stroke_windows import _sig  # noqa: E402


def _npz(path, match, raw):
    s = _sig()
    np.savez_compressed(path, fps=s.fps, first_frame=s.first_frame, shuttle=s.shuttle,
                        keypoints=s.keypoints, court_corners=s.court_corners,
                        image_size=np.array(s.image_size), match=match, set=1, rally=1,
                        hit_frames=np.array([60, 120, 180]), hitter=np.array([0, 1, -1]),
                        stroke7=np.array([0, 0, 0]), stroke_raw=np.array(raw))


def test_load_shuttleset_maps_raw_and_drops_unusable(tmp_path):
    d = tmp_path / "ds" / "M1"
    d.mkdir(parents=True)
    _npz(d / "s1_r1.npz", "M1", ["殺球", "未知球種", "推球"])
    (d / "s1_r2.part.npz").write_bytes(b"")
    rallies = tsc.load_shuttleset(str(tmp_path / "ds"))
    assert len(rallies) == 1 and rallies[0].match == "M1"
    # 未知球種 is dropped; hitter -1 is dropped
    assert rallies[0].hits == [{"frame": 60, "hitter": "lower", "stroke": "smash"}]


def test_load_dji_train_split_only(tmp_path):
    res = tmp_path / "res"
    for name in ("A", "B"):
        (res / name).mkdir(parents=True)
        save_signals(res / name / "hit_signals.npz", _sig())
    text = sl.write_sheet([
        {"name": "A", "split": "train", "fps": 30.0, "clip": "A.mp4",
         "hits": [{"frame": 61, "time_sec": 2.0, "hitter": "lower"}]},
        {"name": "B", "split": "test", "fps": 30.0, "clip": "B.mp4",
         "hits": [{"frame": 61, "time_sec": 2.0, "hitter": "lower"}]}]).replace("|  | 61 |", "| lift | 61 |")
    sheet = tmp_path / "LABELS.md"
    sheet.write_text(text, encoding="utf-8")
    rallies = tsc.load_dji(str(sheet), str(res))
    assert [r.match for r in rallies] == ["dji:A"]
    assert rallies[0].hits == [{"frame": 61, "hitter": "lower", "stroke": "lift"}]
    with pytest.raises(ValueError, match="test"):
        tsc.load_dji(str(sheet), str(res), split="test")


def test_train_smoke_writes_loadable_model(tmp_path):
    rallies = [tsc.Rally(_sig(), [{"frame": f, "hitter": "lower", "stroke": s}
                                  for f, s in ((60, "smash"), (120, "lift"), (180, "net_shot"))],
                         f"M{k}") for k in range(3)]
    model, meta = tsc.train(rallies[:2], rallies[2:], epochs=2, hidden=8, levels=1, seed=0,
                            device="cpu", log=lambda *_: None)
    path = tmp_path / "s.pt"
    smod.save_stroke_model(model, meta, path)
    _, loaded = smod.load_stroke_model(path)
    assert 0.0 <= loaded["unknown_threshold"] < 1.0
    assert loaded["train_counts"]["smash"] == 2 and loaded["val_matches"] == ["M2"]
    assert "val_metrics" in loaded and loaded["validated_on_owner_footage"] is False


def test_load_dji_reports_skipped_rows(tmp_path, capsys):
    res = tmp_path / "res"
    (res / "A").mkdir(parents=True)
    save_signals(res / "A" / "hit_signals.npz", _sig())
    text = sl.write_sheet([
        {"name": "A", "split": "train", "fps": 30.0, "clip": "A.mp4",
         "hits": [{"frame": 61, "time_sec": 2.0, "hitter": "lower"},
                  {"frame": 91, "time_sec": 3.0, "hitter": "lower"}]}])
    text = text.replace("|  | 61 |", "| lift | 61 |").replace("|  | 91 |", "| lift | 91 |")
    text = text.replace("| 3.0000 | near |", "| 3.0000 |  |")
    sheet = tmp_path / "LABELS.md"
    sheet.write_text(text, encoding="utf-8")
    rallies = tsc.load_dji(str(sheet), str(res))
    assert len(rallies[0].hits) == 1
    assert "A: skipped 1 labelled row(s) without player or frame" in capsys.readouterr().out
