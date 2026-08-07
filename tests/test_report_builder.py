from badminton_analysis.posture.report_builder import build_coach_report
from badminton_analysis.posture import coach_kb as kb


def _summary():
    return {
        "stroke_type": "smash", "rep_count": 5, "mean_score": 68.0,
        "best_rep": {"rep_id": 2, "score": 82}, "worst_rep": {"rep_id": 4, "score": 51},
        "consistency": 11.2, "per_metric_avg": {"elbow_extension": 60.0},
        "recurring_weaknesses": [{"metric": "elbow_extension", "count": 4}],
        "strengths": [{"metric": "trunk_rotation", "count": 3}],
    }


def _reports():
    return [
        {"rep_id": 1, "stroke_type": "smash", "overall_score": 65,
         "per_metric": {"elbow_extension": {"measured": 138, "score": 55,
                        "ideal_range": [150, 170], "direction": "under", "severity": "moderate"}},
         "weaknesses": [{"metric": "elbow_extension", "measured": 138, "ideal_range": [150, 170],
                         "direction": "under", "severity": "moderate", "description": "x"}],
         "strengths": ["trunk_rotation"]},
    ]


def _meta():
    return {"date": "2026-07-01", "stroke_type": "smash",
            "dominant_hand": "right", "pose_family": "yolo-pose"}


def test_builds_all_languages():
    out = build_coach_report(_reports(), _summary(), _meta())
    assert set(out.keys()) == set(kb.SUPPORTED_LANGS)


def test_report_structure_and_correlation():
    r = build_coach_report(_reports(), _summary(), _meta())["en"]
    assert r["lang"] == "en"
    assert r["header"]["stroke"] == "smash"
    assert r["header"]["date"] == "2026-07-01"
    assert r["summary"]["mean_score"] == 68.0
    assert isinstance(r["summary"]["verdict_text"], str) and r["summary"]["verdict_text"]
    # weakness carries impact + mechanism + drill (the correlation to shot quality)
    w = r["weaknesses"][0]
    assert w["metric"] == "elbow_extension"
    assert w["direction"] == "under"
    assert w["impact_label"] and w["mechanism_text"] and w["drill_text"]
    # strengths + per-rep + plan present
    assert r["strengths"] and r["strengths"][0]["metric"] == "trunk_rotation"
    assert r["per_rep"] and r["per_rep"][0]["rep_id"] == 1
    assert "weeks" in r["training_plan"]


def test_chinese_text_differs_from_english():
    out = build_coach_report(_reports(), _summary(), _meta())
    en_mech = out["en"]["weaknesses"][0]["mechanism_text"]
    hans_mech = out["zh-Hans"]["weaknesses"][0]["mechanism_text"]
    assert en_mech and hans_mech and en_mech != hans_mech


def test_empty_reps_insufficient_data_verdict():
    empty_summary = {"stroke_type": "serve", "rep_count": 0, "mean_score": None,
                     "best_rep": None, "worst_rep": None, "consistency": None,
                     "per_metric_avg": {}, "recurring_weaknesses": [], "strengths": []}
    r = build_coach_report([], empty_summary, {"date": "2026-07-01", "stroke_type": "serve",
                            "dominant_hand": "right", "pose_family": "yolo-pose"})["en"]
    assert r["header"]["rep_count"] == 0
    assert r["weaknesses"] == [] and r["strengths"] == []
    assert "verdict_text" in r["summary"] and r["summary"]["verdict_text"]


def test_section_labels_present_in_all_languages():
    out = build_coach_report(_reports(), _summary(), _meta())
    for lang in ("en", "zh-Hant", "zh-Hans"):
        labels = out[lang].get("section_labels", {})
        assert labels, f"section_labels missing in {lang}"
        for key in ("strengths", "weaknesses", "per_rep", "training_plan",
                    "col_rep", "col_score", "col_top_weakness"):
            assert key in labels, f"{lang} section_labels missing key '{key}'"
            assert labels[key], f"{lang} section_labels['{key}'] is empty"


def test_section_labels_differ_by_language():
    out = build_coach_report(_reports(), _summary(), _meta())
    en_labels = out["en"]["section_labels"]
    hans_labels = out["zh-Hans"]["section_labels"]
    # Each label key should be translated (not identical to the English text)
    assert en_labels["strengths"] != hans_labels["strengths"], \
        "zh-Hans 'strengths' label must differ from English"
    assert en_labels["weaknesses"] != hans_labels["weaknesses"], \
        "zh-Hans 'weaknesses' label must differ from English"
    assert en_labels["per_rep"] != hans_labels["per_rep"], \
        "zh-Hans 'per_rep' label must differ from English"
    assert en_labels["training_plan"] != hans_labels["training_plan"], \
        "zh-Hans 'training_plan' label must differ from English"
