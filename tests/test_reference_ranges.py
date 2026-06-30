import pytest
from badminton_analysis.analysis.reference_ranges import REFERENCE_RANGES

STROKES = {"high_clear", "smash", "drop_shot", "serve"}
METRICS = {"elbow_extension", "trunk_rotation", "wrist_flexion",
           "knee_flexion", "hip_shoulder_separation", "weight_transfer"}


def test_all_strokes_present():
    assert set(REFERENCE_RANGES.keys()) == STROKES


def test_each_stroke_has_all_metrics():
    for stroke, specs in REFERENCE_RANGES.items():
        assert set(specs.keys()) == METRICS, stroke


def test_weights_sum_to_one():
    for stroke, specs in REFERENCE_RANGES.items():
        total = sum(s["weight"] for s in specs.values())
        assert total == pytest.approx(1.0, abs=1e-9), stroke


def test_ranges_are_ordered():
    for stroke, specs in REFERENCE_RANGES.items():
        for name, s in specs.items():
            assert s["min"] <= s["ideal"] <= s["max"], (stroke, name)
