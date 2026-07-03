# tests/test_app_rep_clip.py
import json
import pytest

import app as webapp


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(webapp, "OUTPUTS", tmp_path)
    webapp.app.config["TESTING"] = True
    return webapp.app.test_client()


def _write_reps(tmp_path, video, with_video=False):
    out = tmp_path / video / "posture"
    out.mkdir(parents=True, exist_ok=True)
    with open(out / "drill_reps.jsonl", "w", encoding="utf-8") as f:
        f.write(json.dumps({"rep_id": 1, "contact_frame": 90, "overall_score": 50}) + "\n")
        f.write(json.dumps({"rep_id": 2, "contact_frame": 300, "overall_score": 70}) + "\n")
    (out / "metadata.json").write_text(json.dumps({"video": {"fps": 30.0}}), encoding="utf-8")
    if with_video:
        (out / ("detect_" + video + ".mp4")).write_bytes(b"x")
    return out


def test_rep_clip_window_math():
    assert webapp._rep_clip_window(90, 30.0, 1.5) == (1.5, 3.0)     # center 3.0s -> start 1.5, dur 3.0
    assert webapp._rep_clip_window(0, 30.0, 1.5) == (0.0, 3.0)      # clamp start at 0
    assert webapp._rep_clip_window(90, 0, 1.5) == (1.5, 3.0)        # fps<=0 -> fallback 30


def test_rep_clip_400_without_rep_id(client, tmp_path):
    _write_reps(tmp_path, "drill1", with_video=True)
    r = client.post("/api/posture-rep-clip/drill1", json={})
    assert r.status_code == 400


def test_rep_clip_404_without_reps_file(client):
    r = client.post("/api/posture-rep-clip/nope", json={"rep_id": 1})
    assert r.status_code == 404


def test_rep_clip_404_when_rep_missing(client, tmp_path):
    _write_reps(tmp_path, "drill1", with_video=True)
    r = client.post("/api/posture-rep-clip/drill1", json={"rep_id": 999})
    assert r.status_code == 404


def test_rep_clip_404_without_annotated_video(client, tmp_path):
    _write_reps(tmp_path, "drill1", with_video=False)
    r = client.post("/api/posture-rep-clip/drill1", json={"rep_id": 1})
    assert r.status_code == 404
