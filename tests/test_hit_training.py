import sys
from pathlib import Path

import numpy as np

from badminton_analysis.stroke.hit_training import (
    choose_peak_config, label_steps, make_targets, random_window)
from tests.hit_fixtures import make_signals

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import train_hit_detector as thd  # noqa: E402


def test_label_steps_nearest_and_dropped_outside():
    idx = np.arange(0, 120, 2)                         # 60 fps -> 30 Hz
    steps = label_steps(np.array([1, 21, 22, 500]), 1, idx)
    assert steps.tolist() == [0, 10, 10]


def test_make_targets_peaks_and_hitter_mask():
    hit_t, far_t, mask = make_targets(20, np.array([5, 12]), np.array([1, -1]), sigma=1.0, hitter_radius=1)
    assert hit_t[5] == 1.0 and hit_t[12] == 1.0 and 0.5 < hit_t[6] < 0.7
    assert mask[4:7].all() and not mask[12] and far_t[5] == 1.0


def test_random_window_fits():
    rng = np.random.default_rng(0)
    assert random_window(100, 256, rng) == (0, 100)
    s, e = random_window(1000, 256, rng)
    assert e - s == 256 and 0 <= s and e <= 1000


def test_choose_peak_config_finds_the_obvious_setting():
    hit = np.zeros(100)
    hit[[10, 40, 70]] = 0.8
    hit[[20, 50]] = 0.3            # false peaks only a 0.5 threshold rejects
    best = choose_peak_config([(hit, np.zeros(100), np.array([10, 40, 70]), np.array([0, 0, 0]))],
                              thresholds=[0.2, 0.5], spacings_sec=[0.1, 0.3])
    assert best["recall"] == 1.0 and best["precision"] == 1.0 and best["threshold"] == 0.5


def _synthetic_sample(seed):
    """Triangle-wave shuttle; a hit at every turn. The turn nearest the far player
    (y = 300) is at odd seconds, the near player's (y = 550) at even seconds."""
    sig = make_signals(n=300, fps=30.0)
    frames = np.array([31, 61, 91, 121, 151, 181, 211, 241, 271])
    hitters = np.array([(i + 1) % 2 for i in range(len(frames))])   # t=1 s far, t=2 s near, ...
    return thd.Sample(sig, frames, hitters, f"m{seed}")


def test_train_learns_an_easy_synthetic_task():
    model, meta = thd.train([_synthetic_sample(0), _synthetic_sample(1)], [_synthetic_sample(2)],
                            epochs=150, hidden=16, levels=4, window=128, batch=4, lr=3e-3,
                            seed=0, device="cpu", log=lambda *a: None)
    assert meta["val_metrics"]["recall"] >= 0.8
    assert meta["feature_dim"] == 90 and meta["hidden"] == 16
    assert 0.0 < meta["threshold"] < 1.0


def _write_rally(path, match="m", rally=1):
    sig = make_signals(n=60, fps=30.0)
    np.savez(path, fps=sig.fps, first_frame=sig.first_frame, shuttle=sig.shuttle,
             keypoints=sig.keypoints, court_corners=sig.court_corners,
             image_size=np.array(sig.image_size), match=match, set=1, rally=rally,
             hit_frames=np.array([31]), hitter=np.array([1]),
             stroke7=np.array([0]), stroke_raw=np.array(["x"]))


def test_load_dataset_skips_partial_files(tmp_path):
    d = tmp_path / "m"
    d.mkdir()
    _write_rally(d / "s1_r1.npz")
    (d / "x.part.npz").write_bytes(b"junk, not an npz")
    samples = thd.load_dataset(str(tmp_path))
    assert len(samples) == 1 and samples[0].match == "m"
    assert samples[0].hit_frames.tolist() == [31]
