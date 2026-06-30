import json
from badminton_analysis.posture.writer import write_rep_reports, build_drill_summary


def _rep(rep_id, score, weak=None, strong=None, per_metric=None):
    return {
        "rep_id": rep_id, "stroke_type": "high_clear", "player_side": "single",
        "overall_score": score,
        "per_metric": per_metric or {},
        "weaknesses": [{"metric": m} for m in (weak or [])],
        "strengths": strong or [],
    }


def test_write_rep_reports_one_per_line(tmp_path):
    out = tmp_path / "posture" / "drill_reps.jsonl"
    write_rep_reports(str(out), [_rep(1, 80), _rep(2, 70)])
    lines = out.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["rep_id"] == 1


def test_summary_aggregates_scores_and_consistency():
    reports = [_rep(1, 80, weak=["elbow_extension"]),
               _rep(2, 60, weak=["elbow_extension", "knee_flexion"]),
               _rep(3, 70, weak=[])]
    s = build_drill_summary(reports, "high_clear")
    assert s["stroke_type"] == "high_clear"
    assert s["rep_count"] == 3
    assert s["mean_score"] == 70.0
    assert s["best_rep"] == {"rep_id": 1, "score": 80}
    assert s["worst_rep"] == {"rep_id": 2, "score": 60}
    assert s["consistency"] is not None and s["consistency"] > 0
    assert s["recurring_weaknesses"][0] == {"metric": "elbow_extension", "count": 2}


def test_summary_skips_none_scores_and_counts_strengths():
    reports = [_rep(1, None, strong=["wrist_flexion"]),
               _rep(2, 90, strong=["wrist_flexion"])]
    s = build_drill_summary(reports, "smash")
    assert s["mean_score"] == 90.0          # None skipped
    assert s["strengths"][0] == {"metric": "wrist_flexion", "count": 2}


def test_summary_empty():
    s = build_drill_summary([], "serve")
    assert s["rep_count"] == 0
    assert s["mean_score"] is None
    assert s["best_rep"] is None and s["worst_rep"] is None
    assert s["consistency"] is None
    assert s["recurring_weaknesses"] == []
