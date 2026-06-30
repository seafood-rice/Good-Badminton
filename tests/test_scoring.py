import pytest
from badminton_analysis.analysis import scoring

SPEC = {"min": 140, "max": 160, "ideal": 150, "weight": 0.25}


def test_score_angle_in_range_is_100():
    assert scoring.score_angle(150, SPEC) == 100.0


def test_score_angle_none_is_none():
    assert scoring.score_angle(None, SPEC) is None


def test_score_angle_one_width_below_is_zero():
    # range width 20; 20 below min(140) -> 120 -> score 0
    assert scoring.score_angle(120, SPEC) == pytest.approx(0.0, abs=1e-6)


def test_score_angle_half_width_above_is_50():
    # 10 above max(160) -> 170 -> half a width -> 50
    assert scoring.score_angle(170, SPEC) == pytest.approx(50.0, abs=1e-6)


def test_deviation_direction():
    assert scoring.deviation_direction(130, SPEC) == "under"
    assert scoring.deviation_direction(170, SPEC) == "over"
    assert scoring.deviation_direction(150, SPEC) == "in_range"


def test_classify_severity_bands():
    assert scoring.classify_severity(90) == "minor"
    assert scoring.classify_severity(60) == "moderate"
    assert scoring.classify_severity(10) == "severe"
    assert scoring.classify_severity(None) is None


def test_score_stroke_perfect_is_100_no_weakness():
    metrics = {"elbow_extension": 150, "trunk_rotation": 30, "wrist_flexion": 90,
               "knee_flexion": 45, "hip_shoulder_separation": 25, "weight_transfer": 0.35}
    result = scoring.score_stroke(metrics, "high_clear")
    assert result["overall"] == pytest.approx(100.0, abs=1e-6)
    assert result["weaknesses"] == []


def test_score_stroke_flags_weakness_and_skips_none():
    metrics = {"elbow_extension": 120, "trunk_rotation": 30, "wrist_flexion": None,
               "knee_flexion": 45, "hip_shoulder_separation": 25, "weight_transfer": 0.35}
    result = scoring.score_stroke(metrics, "high_clear")
    assert "elbow_extension" in result["weaknesses"]
    assert result["per_metric"]["wrist_flexion"]["score"] is None
    assert result["overall"] is not None and result["overall"] < 100.0
