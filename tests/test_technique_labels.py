from badminton_analysis.analysis import biomechanics as bio
from badminton_analysis.analysis.technique_writer import build_match_summary
from badminton_analysis.stroke.events import StrokeEvent


def _report(stroke_type):
    a = bio.BiomechanicalAnalyzer()
    ev = StrokeEvent(stroke_type=stroke_type, contact_frame=10, window_start=0, window_end=20,
                     player_side="lower", confidence=0.9)
    return a.analyze(ev, [])          # no frames: metrics are all None, shape still complete


def test_profiled_type_is_scored_shape():
    r = _report("clear")
    assert r["stroke_type"] == "clear" and r["profile"] == "high_clear" and r["group"] == "clear"
    assert r["scored"] is True and set(r["per_metric"]) and "measured" in r


def test_unprofiled_type_is_labelled_not_scored():
    for t in ("lift", "net_shot", "push", "unknown"):
        r = _report(t)
        assert r["scored"] is False and r["profile"] is None
        assert r["overall_score"] is None and r["per_metric"] == {} and r["weaknesses"] == []
        assert "measured" in r


def test_drill_profile_names_still_score():
    r = _report("high_clear")
    assert r["scored"] is True and r["profile"] == "high_clear" and r["group"] == "clear"


def test_rescore_report_switches_profile():
    r = _report("lift")
    r["measured"] = {"elbow_extension": 150.0, "trunk_rotation": 30.0, "wrist_flexion": 90.0,
                     "knee_flexion": 156.0, "hip_shoulder_separation": 25.0, "weight_transfer": 0.35}
    r["feature_space"] = "2d"
    s = bio.rescore_report(r, "clear")
    assert s["scored"] is True and s["stroke_type"] == "clear" and s["overall_score"] == 100.0
    back = bio.rescore_report(s, "drive")
    assert back["scored"] is False and back["measured"] == r["measured"]


def test_summary_counts_only_scored_in_averages():
    reports = [{"stroke_type": "clear", "scored": True, "overall_score": 80.0,
                "weaknesses": [{"metric": "trunk_rotation"}], "strengths": []},
               {"stroke_type": "lift", "scored": False, "overall_score": None,
                "weaknesses": [], "strengths": []}]
    s = build_match_summary(reports)
    assert s["stroke_count"] == 2 and s["scored_count"] == 1
    assert s["by_type"]["lift"] == {"count": 1, "avg_score": None}
    assert s["by_type"]["clear"]["avg_score"] == 80.0


def test_runner_unknown_when_label_missing():
    from badminton_analysis.system import TechniqueAnalysisRunner
    runner = TechniqueAnalysisRunner(bio.BiomechanicalAnalyzer())
    hits = [{"frame": 30, "hitter": "lower"}, {"frame": 60, "hitter": "lower"}]
    strokes = {30: {"stroke": "smash", "confidence": 0.9}}
    reports, events = runner.run([], lambda f: {"player_side": "lower"}, hits=hits, strokes=strokes)
    assert [r["stroke_type"] for r in reports] == ["smash", "unknown"]
    assert reports[0]["scored"] is True and reports[1]["scored"] is False
    assert events[0].confidence == 0.9 and events[1].confidence is None
