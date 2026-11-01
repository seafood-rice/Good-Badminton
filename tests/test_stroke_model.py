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


def test_schema_one_sidecar_loads_without_heads(tmp_path):
    m = smod.StrokeNet(INPUT_DIM, hidden=16, levels=2)
    path = tmp_path / "s.pt"
    smod.save_stroke_model(m, _meta(schema=1), path)
    sidecar = tmp_path / "s.json"
    meta = json.loads(sidecar.read_text(encoding="utf-8"))
    meta.pop("attributes"), meta.pop("attribute_min_confidence")
    sidecar.write_text(json.dumps(meta), encoding="utf-8")
    m2, loaded = smod.load_stroke_model(path)
    assert loaded["attributes"] == [] and m2.n_attrs == 0
    x = np.zeros((2, WINDOW_STEPS, INPUT_DIM), np.float32)
    assert smod.predict_attrs(m2, x) is None and smod.predict_probs(m2, x).shape == (2, 12)


def test_schema_two_round_trip_with_heads(tmp_path):
    m = smod.StrokeNet(INPUT_DIM, hidden=16, levels=2, n_attrs=2).eval()
    path = tmp_path / "s.pt"
    smod.save_stroke_model(m, smod.base_meta(16, 2, 0.4, attributes=["backhand", "above_net"]), path)
    m2, meta = smod.load_stroke_model(path)
    assert meta["schema"] == 2 and meta["attributes"] == ["backhand", "above_net"]
    assert meta["attribute_min_confidence"] == 0.6
    x = np.random.default_rng(0).normal(size=(3, WINDOW_STEPS, INPUT_DIM)).astype(np.float32)
    a = smod.predict_attrs(m2, x)
    assert a.shape == (3, 2) and ((a > 0) & (a < 1)).all()
    np.testing.assert_allclose(a, smod.predict_attrs(m, x), atol=1e-6)
    assert smod.predict_attrs(m2, x[:0]).shape == (0, 2)


def test_forward_keeps_type_logits_only_and_forward_all_adds_attrs():
    m = smod.StrokeNet(INPUT_DIM, hidden=16, levels=2, n_attrs=3).eval()
    x = torch.zeros(3, WINDOW_STEPS, INPUT_DIM)
    logits, attrs = m.forward_all(x)
    assert m(x).shape == (3, len(STROKE_TYPES)) and torch.equal(m(x), logits)
    assert attrs.shape == (3, 3)
    assert smod.StrokeNet(INPUT_DIM, hidden=16, levels=2).forward_all(x)[1] is None


def test_load_rejects_unknown_schema_three(tmp_path):
    path = tmp_path / "s.pt"
    smod.save_stroke_model(smod.StrokeNet(INPUT_DIM, hidden=16, levels=2), _meta(schema=3), path)
    with pytest.raises(smod.StrokeModelError, match="incompatible"):
        smod.load_stroke_model(path)


@pytest.mark.parametrize("over", [{"attributes": ["foo"]}, {"attributes": ["backhand", "backhand"]},
                                  {"attributes": "backhand"}, {"attributes": [1]},
                                  {"attributes": ["backhand"], "attribute_min_confidence": "high"},
                                  {"attributes": ["backhand"], "attribute_min_confidence": True},
                                  {"attributes": ["backhand"], "attribute_min_confidence": 0.3},
                                  {"attributes": ["backhand"], "attribute_min_confidence": 1.5}])
def test_schema_two_rejects_bad_attribute_fields(tmp_path, over):
    path = tmp_path / "s.pt"
    smod.save_stroke_model(smod.StrokeNet(INPUT_DIM, hidden=16, levels=2, n_attrs=1), _meta(**over), path)
    with pytest.raises(smod.StrokeModelError, match="attribute"):
        smod.load_stroke_model(path)
