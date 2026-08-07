import json
from badminton_analysis.posture.writer import (
    write_rep_reports, build_drill_summary, compute_final_score,
)


def _rep(rep_id, score, weak=None, strong=None, per_metric=None, ai_score=None):
    rep = {
        "rep_id": rep_id, "stroke_type": "high_clear", "player_side": "single",
        "overall_score": score,
        "per_metric": per_metric or {},
        "weaknesses": [{"metric": m} for m in (weak or [])],
        "strengths": strong or [],
    }
    if ai_score is not None:
        rep["ai_score"] = ai_score
    return rep


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
    assert s["mean_final_score"] == 70.0
    assert s["best_rep"] == {"rep_id": 1, "score": 80}
    assert s["worst_rep"] == {"rep_id": 2, "score": 60}
    assert s["consistency"] is not None and s["consistency"] > 0
    assert s["recurring_weaknesses"][0] == {"metric": "elbow_extension", "count": 2}


def test_summary_skips_none_scores_and_counts_strengths():
    reports = [_rep(1, None, strong=["wrist_flexion"]),
               _rep(2, 90, strong=["wrist_flexion"])]
    s = build_drill_summary(reports, "smash")
    assert s["mean_score"] == 90.0          # None skipped
    assert s["mean_final_score"] == 90.0    # None skipped
    assert s["strengths"][0] == {"metric": "wrist_flexion", "count": 2}


def test_summary_empty():
    s = build_drill_summary([], "serve")
    assert s["rep_count"] == 0
    assert s["mean_score"] is None
    assert s["mean_final_score"] is None
    assert s["best_rep"] is None and s["worst_rep"] is None
    assert s["consistency"] is None
    assert s["recurring_weaknesses"] == []


# --- compute_final_score seam: heuristic-only, AI score excluded -----------------

def test_compute_final_score_is_heuristic_only():
    assert compute_final_score(80, 40) == 80
    assert compute_final_score(80, None) == 80
    assert compute_final_score(0, 100) == 0       # AI disagreeing must not move it


# --- write_rep_reports: stamps final_score per rep --------------------------------

def test_write_rep_reports_sets_final_score_per_rep(tmp_path):
    out = tmp_path / "posture" / "drill_reps.jsonl"
    reps = [_rep(1, 80, ai_score=20.0), _rep(2, 70)]
    write_rep_reports(str(out), reps)
    lines = [json.loads(l) for l in out.read_text(encoding="utf-8").strip().splitlines()]
    assert lines[0]["final_score"] == 80        # heuristic only, low AI score ignored
    assert lines[0]["overall_score"] == 80
    assert lines[0]["ai_score"] == 20.0
    assert lines[1]["final_score"] == 70


def test_write_rep_reports_final_score_none_when_overall_score_none(tmp_path):
    out = tmp_path / "posture" / "drill_reps.jsonl"
    write_rep_reports(str(out), [_rep(1, None, ai_score=55.0)])
    line = json.loads(out.read_text(encoding="utf-8").strip())
    assert line["final_score"] is None
    assert line["overall_score"] is None


# --- build_drill_summary: mean_final_score + best/worst by final_score -----------

def test_summary_mean_final_score_ignores_anticorrelated_ai_score():
    # AI score is anti-correlated with the heuristic here (lowest AI on the best rep);
    # mean_final_score and best/worst must track the heuristic only.
    reports = [_rep(1, 80, ai_score=10.0),
               _rep(2, 60, ai_score=90.0),
               _rep(3, None, ai_score=50.0)]     # excluded: no overall_score
    s = build_drill_summary(reports, "high_clear")
    assert s["mean_final_score"] == 70.0
    assert s["best_rep"] == {"rep_id": 1, "score": 80}
    assert s["worst_rep"] == {"rep_id": 2, "score": 60}
