from badminton_analysis.posture.report_builder import build_coach_report
from badminton_analysis.posture.report_render import render_html


def _reports():
    return [{
        "rep_id": 1, "overall_score": 60.0, "final_score": 60.0,
        "feature_space": "3d",
        "weaknesses": [{
            "metric": "elbow_extension", "measured": 120.0, "ideal_range": [150, 175],
            "direction": "under", "severity": "moderate",
            "measured_shadow_2d": 118.0, "feature_space": "3d",
            "description": "x",
        }],
        "strengths": [],
    }]


def _summary():
    return {"stroke_type": "high_clear", "rep_count": 1, "mean_score": 60.0,
            "consistency": 100.0, "recurring_weaknesses": [{"metric": "elbow_extension"}],
            "strengths": [], "per_metric_avg": {}}


def test_builder_carries_feature_space_and_shadow():
    meta = {"date": "2026-07-26", "stroke_type": "high_clear",
            "dominant_hand": "right", "pose_family": "yolo-pose", "feature_space": "3d"}
    by_lang = build_coach_report(_reports(), _summary(), meta)
    en = by_lang["en"]
    assert en["header"]["feature_space"] == "3d"
    assert en["weaknesses"][0]["measured_shadow_2d"] == 118.0


def test_render_shows_badge_and_shadow():
    meta = {"date": "2026-07-26", "stroke_type": "high_clear",
            "dominant_hand": "right", "pose_family": "yolo-pose", "feature_space": "3d"}
    en = build_coach_report(_reports(), _summary(), meta)["en"]
    html = render_html(en)
    assert "3D" in html
    assert "2D: 118" in html
