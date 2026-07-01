import json
import os
from badminton_analysis.posture.system import PostureAnalysisSystem


def _summary():
    return {"stroke_type": "smash", "rep_count": 1, "mean_score": 65.0,
            "best_rep": {"rep_id": 1, "score": 65}, "worst_rep": {"rep_id": 1, "score": 65},
            "consistency": 0.0, "per_metric_avg": {"elbow_extension": 55.0},
            "recurring_weaknesses": [{"metric": "elbow_extension", "count": 1}],
            "strengths": []}


def _reports():
    return [{"rep_id": 1, "stroke_type": "smash", "overall_score": 65,
             "per_metric": {"elbow_extension": {"measured": 138, "score": 55,
                            "ideal_range": [150, 170], "direction": "under", "severity": "moderate"}},
             "weaknesses": [{"metric": "elbow_extension", "measured": 138, "ideal_range": [150, 170],
                             "direction": "under", "severity": "moderate", "description": "x"}],
             "strengths": []}]


def test_write_reports_produces_three_languages(tmp_path):
    vid = tmp_path / "clip.mp4"; vid.write_bytes(b"x")
    sys = PostureAnalysisSystem(str(vid), stroke_type="smash", output_dir=str(tmp_path / "out"))
    paths = sys._write_reports(_reports(), _summary(), date="2026-07-01")
    for lang in ("en", "zh-Hant", "zh-Hans"):
        j = os.path.join(sys.save_dir, "coach_report_" + lang + ".json")
        h = os.path.join(sys.save_dir, "coach_report_" + lang + ".html")
        assert os.path.exists(j), j
        assert os.path.exists(h), h
        data = json.load(open(j, encoding="utf-8"))
        assert data["lang"] == lang
        assert data["header"]["date"] == "2026-07-01"
    # returned mapping covers all three langs
    assert set(paths.keys()) == {"en", "zh-Hant", "zh-Hans"}
