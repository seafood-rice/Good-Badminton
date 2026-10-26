"""Rally label routes (badminton_analysis/web/rally_labels_routes.py) and app wiring."""
import json
import re

import pytest

import app as webapp
from tests.outcome_fixtures import make_run

BILINGUAL = re.compile(r"^[^/]*[一-鿿][^/]* / [A-Za-z].*$")
LABEL = {"start_sec": 1.0, "end_sec": 5.0, "verdict": "upper", "outcome": "winner"}


@pytest.fixture
def client(tmp_path, monkeypatch):
    videos = tmp_path / "videos"
    videos.mkdir()
    outputs = tmp_path / "outputs"
    make_run(outputs / "vid", [(1, 1.0, 5.0), (2, 8.0, 12.0)])
    labels_dir = tmp_path / "rally_labels"
    monkeypatch.setattr(webapp, "VIDEOS", videos)
    monkeypatch.setattr(webapp, "OUTPUTS", outputs)
    monkeypatch.setattr(webapp, "ATHLETES_PATH", tmp_path / "athletes.json")
    monkeypatch.setattr(webapp, "RALLY_LABELS_DIR", labels_dir)
    monkeypatch.setattr(webapp, "jobs", {})
    webapp.app.config["TESTING"] = True
    return webapp.app.test_client(), outputs, labels_dir


def _put(c, revision, labels, stem="vid"):
    return c.put(f"/api/rally-labels/{stem}", json={"revision": revision, "labels": labels})


def _static_bilingual(res):
    assert BILINGUAL.match(res.get_json()["error"]), res.get_json()


def test_get_empty(client):
    c, _, _ = client
    res = c.get("/api/rally-labels/vid")
    assert res.status_code == 200 and res.get_json() == {"schema": 1, "revision": 0, "labels": []}


def test_put_round_trip_and_rebuild(client):
    c, outputs, labels_dir = client
    res = _put(c, 0, [LABEL])
    assert res.status_code == 200 and res.get_json() == {"revision": 1, "outcomes_rebuilt": ["vid"]}
    got = c.get("/api/rally-labels/vid").get_json()
    assert got["revision"] == 1 and got["labels"][0]["verdict"] == "upper"
    doc = json.loads((outputs / "vid" / "rally_outcomes.json").read_text(encoding="utf-8"))
    assert doc["rallies"][0]["source"] == "owner" and doc["summary"]["labelled"] == 1
    assert (labels_dir / "vid.json").is_file()


def test_put_stale_revision_409(client):
    c, _, _ = client
    assert _put(c, 0, [LABEL]).status_code == 200
    res = _put(c, 0, [])
    assert res.status_code == 409 and res.get_json()["revision"] == 1
    _static_bilingual(res)
    assert c.get("/api/rally-labels/vid").get_json()["labels"][0]["verdict"] == "upper"


@pytest.mark.parametrize("body", [
    {"revision": 0, "labels": [{"end_sec": 1, "verdict": "both"}]},
    {"revision": "0", "labels": []},
    {"labels": []},
    [],
])
def test_put_bad_body_400(client, body):
    c, _, _ = client
    res = c.put("/api/rally-labels/vid", json=body)
    assert res.status_code == 400
    _static_bilingual(res)


def test_unknown_stem_404(client):
    c, _, _ = client
    assert c.get("/api/rally-labels/nope").status_code == 404
    res = _put(c, 0, [], stem="nope")
    assert res.status_code == 404
    _static_bilingual(res)
    assert c.get("/api/rally-labels/..%2Fx").status_code == 404


def test_put_refuses_unreadable_store(client):
    c, _, labels_dir = client
    labels_dir.mkdir()
    (labels_dir / "vid.json").write_text("{broken", encoding="utf-8")
    res = _put(c, 0, [LABEL])
    assert res.status_code == 409
    _static_bilingual(res)
    assert (labels_dir / "vid.json").read_text(encoding="utf-8") == "{broken"
    assert c.get("/api/rally-labels/vid").status_code == 500


def test_put_during_job_skips_rebuild(client, monkeypatch):
    c, outputs, _ = client
    monkeypatch.setattr(webapp, "jobs", {"vid": {"status": "running", "video_name": "vid.mp4"}})
    res = _put(c, 0, [LABEL])
    assert res.status_code == 200
    assert res.get_json() == {"revision": 1, "outcomes_rebuilt": [], "rebuild_skipped": "job_active"}
    assert not (outputs / "vid" / "rally_outcomes.json").exists()


def test_delete_all_removes_labels(client):
    c, _, labels_dir = client
    assert _put(c, 0, [LABEL]).status_code == 200
    res = c.post("/api/delete/vid", json={"scope": "all"})
    assert res.status_code == 200, res.get_json()
    assert not (labels_dir / "vid.json").exists()


def test_delete_match_keeps_labels(client):
    c, outputs, labels_dir = client
    assert _put(c, 0, [LABEL]).status_code == 200
    res = c.post("/api/delete/vid", json={"scope": "match"})
    assert res.status_code == 200, res.get_json()
    assert (labels_dir / "vid.json").is_file()
    assert not (outputs / "vid" / "rally_outcomes.json").exists()
