import numpy as np
import pytest

from badminton_analysis.stroke import stroke_metrics as sm
from badminton_analysis.stroke.stroke_types import GROUPS, STROKE_TYPES


def test_score_counts_groups_and_unknown_as_miss():
    pairs = [("smash", "smash"), ("smash", "unknown"), ("net_shot", "block"),
             ("push", "drive"), ("clear", "lift")]
    m = sm.score(pairs)
    assert m["matched"] == 5 and m["confident"] == 4
    assert m["unknown_rate"] == pytest.approx(0.2)
    assert m["accuracy"] == pytest.approx(3 / 4)          # block->net and push->drive count
    assert m["type_accuracy"] == pytest.approx(1 / 4)
    assert m["recall"]["smash"] == {"n": 2, "correct": 1, "recall": 0.5}
    assert m["recall"]["net"]["recall"] == 1.0 and m["recall"]["clear"]["recall"] == 0.0
    assert m["recall"]["serve"] == {"n": 0, "correct": 0, "recall": None}
    assert m["confusion"]["net_shot"] == {"block": 1}


def test_score_empty():
    m = sm.score([])
    assert m["matched"] == 0 and m["accuracy"] is None and m["unknown_rate"] is None


def test_check_bar():
    good = [(t, t) for t in STROKE_TYPES for _ in range(20)]
    ok, reasons = sm.check_bar(sm.score(good))
    assert ok and reasons == []
    # 60 unknown pushes: unknown rate 60/300 = 0.2 > 0.15, drive recall 40/100 = 0.4 < 0.75
    bad = good + [("push", "unknown")] * 60
    ok, reasons = sm.check_bar(sm.score(bad))
    assert not ok and any("unknown" in r for r in reasons) and any(r.startswith("drive") for r in reasons)
    few = [("smash", "smash")] * 5
    ok, reasons = sm.check_bar(sm.score(few))
    assert not ok and any("fewer than 20" in r for r in reasons)


def test_predict_labels_threshold():
    probs = np.zeros((2, 12))
    probs[0, 4] = 0.9
    probs[1, 0] = 0.3
    assert sm.predict_labels(probs, 0.5) == ["smash", "unknown"]


def test_choose_threshold_respects_unknown_cap():
    rng = np.random.default_rng(0)
    truth = [STROKE_TYPES[i % 12] for i in range(240)]
    probs = rng.dirichlet(np.ones(12), size=240)
    for k, t in enumerate(truth):
        probs[k] *= 0.3
        probs[k, STROKE_TYPES.index(t)] += 0.7 * (k % 3 != 0)
        probs[k] /= probs[k].sum()
    thr = sm.choose_threshold(probs, truth, max_unknown=0.15)
    labels = sm.predict_labels(probs, thr)
    assert labels.count("unknown") / len(labels) <= 0.15
    assert 0.0 <= thr < 1.0
