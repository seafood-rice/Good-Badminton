import json
import os
from badminton_analysis.analysis.technique_writer import (
    write_stroke_reports, build_match_summary,
)


def _report(stroke, score, weak_metrics, strong_metrics=None):
    if strong_metrics is None:
        strong_metrics = []
    return {
        "stroke_type": stroke, "contact_frame": 1, "player_side": "upper",
        "confidence": 0.9, "overall_score": score, "per_metric": {},
        "weaknesses": [{"metric": m} for m in weak_metrics], "strengths": strong_metrics,
    }


def test_write_stroke_reports_one_json_per_line(tmp_path):
    reports = [_report("smash", 80, ["elbow_extension"]), _report("serve", 70, [])]
    out = tmp_path / "strokes.jsonl"
    write_stroke_reports(str(out), reports)
    lines = out.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["stroke_type"] == "smash"


def test_build_match_summary_aggregates():
    reports = [
        _report("smash", 80, ["elbow_extension"]),
        _report("smash", 60, ["elbow_extension", "knee_flexion"]),
        _report("serve", None, []),
    ]
    summary = build_match_summary(reports)
    assert summary["stroke_count"] == 3
    assert summary["by_type"]["smash"]["count"] == 2
    assert summary["by_type"]["smash"]["avg_score"] == 70.0
    # elbow_extension recurs twice -> top recurring weakness
    assert summary["recurring_weaknesses"][0]["metric"] == "elbow_extension"
    assert summary["recurring_weaknesses"][0]["count"] == 2


def test_build_match_summary_empty():
    summary = build_match_summary([])
    assert summary["stroke_count"] == 0
    assert summary["by_type"] == {}
    assert summary["recurring_weaknesses"] == []


def test_build_match_summary_counts_strengths():
    reports = [
        _report("smash", 80, [], strong_metrics=["elbow_extension", "knee_flexion"]),
        _report("smash", 60, [], strong_metrics=["elbow_extension"]),
        _report("serve", None, [], strong_metrics=["wrist_snap"]),
    ]
    summary = build_match_summary(reports)
    assert summary["stroke_count"] == 3
    # elbow_extension recurs twice -> top recurring strength
    assert summary["strengths"][0]["metric"] == "elbow_extension"
    assert summary["strengths"][0]["count"] == 2
    # knee_flexion and wrist_snap each appear once
    assert len(summary["strengths"]) == 3
    assert {s["metric"] for s in summary["strengths"]} == {
        "elbow_extension", "knee_flexion", "wrist_snap"
    }
