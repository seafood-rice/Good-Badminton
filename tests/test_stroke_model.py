import json

import numpy as np
import pytest
import torch

from badminton_analysis.stroke import stroke_model as smod
from badminton_analysis.stroke.stroke_types import STROKE_TYPES
from badminton_analysis.stroke.stroke_windows import INPUT_DIM, WINDOW_STEPS


def _meta(**over):
    meta = smod.base_meta(hidden=16, levels=2, unknown_threshold=0.4)
    meta.update(over)
    return meta


def test_forward_shape():
    m = smod.StrokeNet(INPUT_DIM, hidden=16, levels=2).eval()
    out = m(torch.zeros(3, WINDOW_STEPS, INPUT_DIM))
    assert out.shape == (3, len(STROKE_TYPES))


def test_save_load_round_trip(tmp_path):
    m = smod.StrokeNet(INPUT_DIM, hidden=16, levels=2).eval()
    path = tmp_path / "stroke-classifier.pt"
    smod.save_stroke_model(m, _meta(), path)
    m2, meta = smod.load_stroke_model(path)
    x = np.random.default_rng(0).normal(size=(4, WINDOW_STEPS, INPUT_DIM)).astype(np.float32)
    np.testing.assert_allclose(smod.predict_probs(m, x), smod.predict_probs(m2, x), atol=1e-6)
    assert smod.predict_probs(m2, x).sum(axis=1) == pytest.approx(np.ones(4), abs=1e-5)
    assert meta["unknown_threshold"] == 0.4


@pytest.mark.parametrize("field,value", [("stroke_types", ["a"]), ("feature_schema", 99),
                                         ("window_pre_steps", 3), ("schema", 7)])
def test_load_rejects_incompatible_sidecar(tmp_path, field, value):
    m = smod.StrokeNet(INPUT_DIM, hidden=16, levels=2)
    path = tmp_path / "s.pt"
    smod.save_stroke_model(m, _meta(**{field: value}), path)
    with pytest.raises(smod.StrokeModelError):
        smod.load_stroke_model(path)


def test_load_rejects_missing_sidecar(tmp_path):
    path = tmp_path / "s.pt"
    torch.save(smod.StrokeNet(INPUT_DIM, hidden=16, levels=2).state_dict(), path)
    with pytest.raises(smod.StrokeModelError, match="sidecar"):
        smod.load_stroke_model(path)


def test_predict_probs_empty():
    m = smod.StrokeNet(INPUT_DIM, hidden=16, levels=2)
    assert smod.predict_probs(m, np.zeros((0, WINDOW_STEPS, INPUT_DIM), np.float32)).shape == (0, 12)
