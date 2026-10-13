import numpy as np
import torch

from badminton_analysis.stroke import hit_detector as hd
from badminton_analysis.stroke.hit_features import FEATURE_DIM
from badminton_analysis.stroke.hit_model import HitTCN
from tests.hit_fixtures import make_signals


def test_shuttle_coverage_counts_only_in_play_frames():
    sig = make_signals(n=100)
    sig.shuttle[:50] = np.nan
    assert hd.shuttle_coverage(sig, range(51, 101)) == 1.0
    assert hd.shuttle_coverage(sig, range(1, 101)) == 0.5
    assert hd.shuttle_coverage(sig, []) == 0.0
    assert hd.shuttle_coverage(sig, [500, 600]) == 0.0


def test_pick_peaks_threshold_spacing_and_plateau():
    s = np.zeros(40)
    s[5], s[7] = 0.9, 0.6          # 7 is within spacing of the stronger 5
    s[20] = 0.4                    # below threshold
    s[30] = s[31] = 0.8            # plateau -> one peak
    assert hd.pick_peaks(s, 0.5, 4) == [5, 31]
    assert hd.pick_peaks(np.array([]), 0.5, 4) == []


def test_chunked_inference_equals_whole_sequence():
    torch.manual_seed(0)
    model = HitTCN(FEATURE_DIM, hidden=16).eval()
    feats = np.random.default_rng(0).normal(size=(500, FEATURE_DIM)).astype(np.float32)
    whole = hd.predict_scores(model, feats, chunk=10**6, context=64)
    chunked = hd.predict_scores(model, feats, chunk=50, context=40)
    assert np.allclose(whole[0], chunked[0], atol=1e-5)
    assert np.allclose(whole[1], chunked[1], atol=1e-5)


def test_detect_hits_maps_steps_to_native_frames_at_60fps(monkeypatch):
    sig = make_signals(n=120, fps=60.0)
    hit = np.zeros(60)
    far = np.zeros(60)
    hit[10], far[10] = 0.9, 0.8     # step 10 -> native index 20 -> frame 21, far player
    hit[40], far[40] = 0.7, 0.1     # step 40 -> native index 80 -> frame 81, near player
    monkeypatch.setattr(hd, "predict_scores", lambda model, feats, **k: (hit, far))
    hits = hd.detect_hits(sig, object(), {"threshold": 0.5, "min_spacing_sec": 0.3})
    assert hits == [{"frame": 21, "hitter": "upper", "confidence": 0.9},
                    {"frame": 81, "hitter": "lower", "confidence": 0.7}]


def test_detect_hits_spacing_is_in_seconds(monkeypatch):
    sig = make_signals(n=120, fps=30.0)
    hit = np.zeros(120)
    hit[10], hit[16] = 0.9, 0.8     # 0.2 s apart
    monkeypatch.setattr(hd, "predict_scores", lambda model, feats, **k: (hit, np.zeros(120)))
    assert len(hd.detect_hits(sig, object(), {"threshold": 0.5, "min_spacing_sec": 0.3})) == 1
    assert len(hd.detect_hits(sig, object(), {"threshold": 0.5, "min_spacing_sec": 0.1})) == 2


def test_detect_hits_context_covers_the_receptive_field(monkeypatch):
    sig = make_signals(n=120, fps=30.0)
    seen = {}

    def spy(model, feats, **kw):
        seen.update(kw)
        return np.zeros(len(feats)), np.zeros(len(feats))

    monkeypatch.setattr(hd, "predict_scores", spy)
    hd.detect_hits(sig, object(), {"threshold": 0.5, "min_spacing_sec": 0.3, "levels": 6})
    assert seen["context"] >= 126
