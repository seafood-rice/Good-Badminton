import json
import pytest

import app as webapp


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(webapp, "OUTPUTS", tmp_path)
    webapp.app.config["TESTING"] = True
    return webapp.app.test_client()


def _write_drill(tmp_path, video):
    out = tmp_path / video / "posture"
    out.mkdir(parents=True, exist_ok=True)
    summary = {
        "stroke_type": "high_clear", "rep_count": 2, "mean_score": 70.0,
        "best_rep": {"rep_id": 1, "score": 80}, "worst_rep": {"rep_id": 2, "score": 60},
        "consistency": 10.0, "per_metric_avg": {},
        "recurring_weaknesses": [{"metric": "elbow_extension", "count": 2}], "strengths": [],
    }
    (out / "drill_summary.json").write_text(json.dumps(summary), encoding="utf-8")
    with open(out / "drill_reps.jsonl", "w", encoding="utf-8") as f:
        f.write(json.dumps({"rep_id": 1, "stroke_type": "high_clear",
                            "overall_score": 80, "weaknesses": [], "strengths": []}) + "\n")
    return out


def test_posture_404_when_missing(client):
    assert client.get("/api/posture/nope").status_code == 404


def test_posture_returns_summary_and_reps(client, tmp_path):
    _write_drill(tmp_path, "drill1")
    r = client.get("/api/posture/drill1")
    assert r.status_code == 200
    data = r.get_json()
    assert data["summary"]["rep_count"] == 2
    assert len(data["reps"]) == 1


def test_posture_500_on_malformed_summary(client, tmp_path):
    out = tmp_path / "drillbad" / "posture"
    out.mkdir(parents=True, exist_ok=True)
    (out / "drill_summary.json").write_text("{ not json", encoding="utf-8")
    r = client.get("/api/posture/drillbad")
    assert r.status_code == 500
    assert "error" in r.get_json()


def test_posture_plan_generates_and_persists(client, tmp_path):
    out = _write_drill(tmp_path, "drill1")
    r = client.get("/api/posture-plan/drill1")
    assert r.status_code == 200
    assert "weeks" in r.get_json()
    assert (out / "training_plan.json").exists()


def test_posture_plan_404_without_summary(client):
    assert client.get("/api/posture-plan/missing").status_code == 404


def test_posture_plan_post_rejects_bad_weeks(client, tmp_path):
    _write_drill(tmp_path, "drill1")
    r = client.post("/api/posture-plan/drill1", json={"weeks": "nope"})
    assert r.status_code == 400
