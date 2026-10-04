import json

import pytest
import torch

from badminton_analysis.stroke import hit_model as hm
from badminton_analysis.stroke.hit_features import FEATURE_DIM, FEATURE_SCHEMA, TARGET_HZ


def _meta(**over):
    meta = {"schema": hm.MODEL_SCHEMA, "feature_schema": FEATURE_SCHEMA, "feature_dim": FEATURE_DIM,
            "target_hz": TARGET_HZ, "hidden": 16, "levels": 4, "threshold": 0.5,
            "min_spacing_sec": 0.27}
    meta.update(over)
    return meta


def test_forward_shapes_and_parameter_budget():
    model = hm.HitTCN(FEATURE_DIM)
    hit, far = model(torch.randn(2, 100, FEATURE_DIM))
    assert hit.shape == (2, 100) and far.shape == (2, 100)
    n_params = sum(p.numel() for p in model.parameters())
    assert 50_000 <= n_params <= 300_000


def test_receptive_field_is_about_one_second():
    torch.manual_seed(0)
    model = hm.HitTCN(FEATURE_DIM, hidden=16).eval()
    x = torch.randn(1, 200, FEATURE_DIM)
    x2 = x.clone()
    x2[0, 100] += 5.0
    with torch.no_grad():
        d = (model(x)[0] - model(x2)[0]).abs()[0]
    changed = torch.nonzero(d > 1e-6).flatten()
    assert changed.min() >= 70 and changed.max() <= 130


def test_save_load_roundtrip_identical_outputs(tmp_path):
    torch.manual_seed(1)
    model = hm.HitTCN(FEATURE_DIM, hidden=16).eval()
    model.feat_mean.fill_(0.5)
    path = tmp_path / "hit-detector.pt"
    hm.save_hit_model(model, _meta(), str(path))
    loaded, meta = hm.load_hit_model(str(path))
    x = torch.randn(1, 50, FEATURE_DIM)
    with torch.no_grad():
        assert torch.allclose(model(x)[0], loaded(x)[0])
    assert meta["threshold"] == 0.5
    assert json.loads((tmp_path / "hit-detector.json").read_text())["hidden"] == 16


def test_load_uses_weights_only(tmp_path, monkeypatch):
    model = hm.HitTCN(FEATURE_DIM, hidden=16)
    path = tmp_path / "m.pt"
    hm.save_hit_model(model, _meta(), str(path))
    seen = {}
    real = torch.load

    def spy(*a, **k):
        seen.update(k)
        return real(*a, **k)

    monkeypatch.setattr(torch, "load", spy)
    hm.load_hit_model(str(path))
    assert seen.get("weights_only") is True


@pytest.mark.parametrize("bad", [{"schema": 99}, {"feature_schema": 99},
                                 {"feature_dim": 3}, {"target_hz": 25.0}])
def test_schema_mismatch_raises(tmp_path, bad):
    model = hm.HitTCN(FEATURE_DIM, hidden=16)
    path = tmp_path / "m.pt"
    hm.save_hit_model(model, _meta(**bad), str(path))
    with pytest.raises(hm.HitModelError):
        hm.load_hit_model(str(path))


def test_missing_sidecar_raises(tmp_path):
    path = tmp_path / "m.pt"
    torch.save(hm.HitTCN(FEATURE_DIM, hidden=16).state_dict(), path)
    with pytest.raises(hm.HitModelError):
        hm.load_hit_model(str(path))


def test_file_sha256_is_stable(tmp_path):
    p = tmp_path / "f.bin"
    p.write_bytes(b"abc")
    assert hm.file_sha256(str(p)) == hm.file_sha256(str(p))
    assert len(hm.file_sha256(str(p))) == 64
