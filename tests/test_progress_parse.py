# tests/test_progress_parse.py
import app as webapp


def test_parse_progress_line_valid():
    assert webapp._parse_progress_line("PROGRESS 42 analyzing") == (42, "analyzing")
    assert webapp._parse_progress_line("  PROGRESS 5 loading\n") == (5, "loading")


def test_parse_progress_line_rejects_noise():
    assert webapp._parse_progress_line("Posture analysis: 8 reps") is None
    assert webapp._parse_progress_line("PROGRESS x analyzing") is None
    assert webapp._parse_progress_line("") is None


def test_match_status_includes_stage(monkeypatch):
    monkeypatch.setitem(webapp.jobs, "j-match", {"status": "running", "progress": 40, "message": "x", "stage": "analyzing"})
    client = webapp.app.test_client()
    r = client.get("/api/status/j-match")
    assert r.status_code == 200
    assert r.get_json()["stage"] == "analyzing"


def test_posture_status_includes_stage(monkeypatch):
    monkeypatch.setitem(webapp.jobs, "j-post", {"status": "running", "progress": 60, "message": "x", "stage": "scoring"})
    client = webapp.app.test_client()
    r = client.get("/api/posture-analyze-status/j-post")
    assert r.status_code == 200
    assert r.get_json()["stage"] == "scoring"
