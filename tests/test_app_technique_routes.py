import json
import pytest

import app as webapp


@pytest.fixture
def client(tmp_path, monkeypatch):
    # Redirect OUTPUTS to a temp dir so tests don't touch real outputs
    monkeypatch.setattr(webapp, "OUTPUTS", tmp_path)
    webapp.app.config["TESTING"] = True
    return webapp.app.test_client()


def _write_summary(tmp_path, video):
    out = tmp_path / video
    out.mkdir(parents=True, exist_ok=True)
    summary = {
        "stroke_count": 2,
        "by_type": {"smash": {"count": 2, "avg_score": 65.0}},
        "recurring_weaknesses": [{"metric": "elbow_extension", "count": 2}],
        "strengths": [],
    }
    (out / "technique_summary.json").write_text(json.dumps(summary), encoding="utf-8")
    with open(out / "strokes.jsonl", "w", encoding="utf-8") as f:
        f.write(json.dumps({"stroke_type": "smash", "overall_score": 65.0,
                            "weaknesses": [], "strengths": []}) + "\n")
    return out


def test_technique_404_when_missing(client):
    r = client.get("/api/technique/nope")
    assert r.status_code == 404


def test_technique_returns_summary_and_strokes(client, tmp_path):
    _write_summary(tmp_path, "demo")
    r = client.get("/api/technique/demo")
    assert r.status_code == 200
    data = r.get_json()
    assert data["summary"]["stroke_count"] == 2
    assert len(data["strokes"]) == 1


def test_training_plan_generates_and_persists(client, tmp_path):
    out = _write_summary(tmp_path, "demo")
    r = client.get("/api/training-plan/demo")
    assert r.status_code == 200
    plan = r.get_json()
    assert "weeks" in plan and len(plan["weeks"]) >= 1
    assert (out / "training_plan.json").exists()


def test_training_plan_404_without_summary(client):
    r = client.get("/api/training-plan/missing")
    assert r.status_code == 404


def test_training_plan_post_regenerates_with_weeks(client, tmp_path):
    _write_summary(tmp_path, "demo")
    r = client.post("/api/training-plan/demo", json={"weeks": 6})
    assert r.status_code == 200
    assert len(r.get_json()["weeks"]) == 6


def test_training_plan_post_rejects_bad_weeks(client, tmp_path):
    _write_summary(tmp_path, "demo")
    r = client.post("/api/training-plan/demo", json={"weeks": "not-a-number"})
    assert r.status_code == 400


def test_technique_500_on_malformed_summary(client, tmp_path):
    out_dir = tmp_path / "demo"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "technique_summary.json").write_text("{ not valid json", encoding="utf-8")
    r = client.get("/api/technique/demo")
    assert r.status_code == 500
    assert "error" in r.get_json()


class _FakeProc:
    returncode = 0

    def poll(self):
        return 0

    def wait(self):
        return 0


def test_analyze_command_enables_technique_analysis(client, tmp_path, monkeypatch):
    """The web analyze flow must request technique analysis, otherwise
    technique_summary.json is never produced and the technique panel 404s."""
    videos = tmp_path / "vids"
    videos.mkdir()
    (videos / "clip.mp4").write_bytes(b"\x00")
    monkeypatch.setattr(webapp, "VIDEOS", videos)
    monkeypatch.setattr(webapp, "TEMPLATES", tmp_path / "tpl")
    (tmp_path / "tpl").mkdir()

    captured = {}

    def fake_popen(cmd, **kwargs):
        captured["cmd"] = cmd
        return _FakeProc()

    monkeypatch.setattr(webapp.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(webapp.subprocess, "run", lambda *a, **k: _FakeProc())

    r = client.post("/api/analyze", json={"video": "clip.mp4"})
    assert r.status_code == 200
    assert "--analyze-technique" in captured["cmd"]


def test_analyze_command_omits_technique_when_opted_out(client, tmp_path, monkeypatch):
    """Unchecking the technique box must drop the flag so the heavier
    biomechanics pass is skipped."""
    videos = tmp_path / "vids"
    videos.mkdir()
    (videos / "clip.mp4").write_bytes(b"\x00")
    monkeypatch.setattr(webapp, "VIDEOS", videos)
    monkeypatch.setattr(webapp, "TEMPLATES", tmp_path / "tpl")
    (tmp_path / "tpl").mkdir()

    captured = {}

    def fake_popen(cmd, **kwargs):
        captured["cmd"] = cmd
        return _FakeProc()

    monkeypatch.setattr(webapp.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(webapp.subprocess, "run", lambda *a, **k: _FakeProc())

    r = client.post("/api/analyze", json={"video": "clip.mp4", "analyze_technique": False})
    assert r.status_code == 200
    assert "--analyze-technique" not in captured["cmd"]
