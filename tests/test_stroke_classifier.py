import numpy as np
import pytest
import torch

from badminton_analysis.stroke import stroke_classifier as sc
from badminton_analysis.stroke import stroke_model as smod
from badminton_analysis.stroke.stroke_windows import INPUT_DIM
from tests.test_stroke_windows import _sig


def _model_biased_to(index, tmp_path, threshold=0.5):
    m = smod.StrokeNet(INPUT_DIM, hidden=8, levels=1).eval()
    with torch.no_grad():
        m.head.weight.zero_()
        m.head.bias.zero_()
        m.head.bias[index] = 10.0
    path = tmp_path / "stroke-classifier.pt"
    smod.save_stroke_model(m, smod.base_meta(8, 1, threshold), path)
    return path


def test_classify_hits_labels_and_order(tmp_path):
    model, meta, info = sc.load_classifier(_model_biased_to(4, tmp_path))   # smash
    hits = [{"frame": 200, "hitter": "upper", "confidence": 0.8}, {"frame": 120, "hitter": "lower"}]
    labels = sc.classify_hits(_sig(), hits, model, meta)
    assert [l["frame"] for l in labels] == [120, 200]
    assert all(l["stroke"] == "smash" and l["group"] == "smash" for l in labels)
    assert labels[1]["hit_confidence"] == 0.8 and "hit_confidence" not in labels[0]
    assert 0.99 < labels[0]["confidence"] <= 1.0
    assert info["validated_on_owner_footage"] is False and len(info["weights_sha256"]) == 64


def test_below_threshold_is_unknown(tmp_path):
    model, meta, _ = sc.load_classifier(_model_biased_to(4, tmp_path, threshold=1.01))
    labels = sc.classify_hits(_sig(), [{"frame": 150, "hitter": "lower"}], model, meta)
    assert labels[0]["stroke"] == "unknown" and labels[0]["group"] is None
    assert labels[0]["confidence"] > 0.99


def test_no_signal_is_unknown_with_zero_confidence(tmp_path):
    model, meta, _ = sc.load_classifier(_model_biased_to(4, tmp_path))
    labels = sc.classify_hits(_sig(shuttle=False), [{"frame": 150, "hitter": "lower"}], model, meta)
    assert labels[0] == {"frame": 150, "hitter": "lower", "stroke": "unknown", "group": None,
                         "confidence": 0.0}


def test_unknown_labels_and_payload():
    labels = sc.unknown_labels([{"frame": 9, "hitter": "lower", "confidence": 0.7}])
    assert labels == [{"frame": 9, "hitter": "lower", "stroke": "unknown", "group": None,
                       "confidence": None, "hit_confidence": 0.7}]
    p = sc.build_payload(labels, None, "learned", "tracknet")
    assert p["schema"] == 2 and p["classifier"] is None
    assert p["distribution"] == {"unknown": 1} and p["groups"] == {}
    assert p["hit_source"] == "learned" and p["shuttle_source"] == "tracknet"
    assert sc.is_current(p) and not sc.is_current({"strokes": []}) and not sc.is_current(None)


def test_payload_counts_types_and_groups():
    labels = [{"frame": 1, "hitter": "lower", "stroke": "push", "group": "drive", "confidence": 0.9},
              {"frame": 2, "hitter": "upper", "stroke": "drive", "group": "drive", "confidence": 0.9}]
    p = sc.build_payload(labels, {"version": "v"}, "contact_rule", "yolo")
    assert p["distribution"] == {"push": 1, "drive": 1} and p["groups"] == {"drive": 2}


def test_load_classifier_missing_raises(tmp_path):
    with pytest.raises(smod.StrokeModelError):
        sc.load_classifier(tmp_path / "nope.pt")
