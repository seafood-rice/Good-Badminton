"""Stroke label routes (badminton_analysis/web/stroke_labels_routes.py) and app wiring."""
import json
import re
import threading

import pytest
from flask import Flask

import app as webapp
from badminton_analysis.stroke import stroke_classifier as sc
from badminton_analysis.stroke.stroke_types import group_of
from badminton_analysis.web import stroke_labels_routes

BILINGUAL = re.compile(r"^[^/]*[一-鿿][^/]* / [A-Za-z].*$")
LABEL = {"time_sec": 1.0, "hitter": "lower", "stroke": "smash", "kind": "label"}


def _write_run(run, strokes=True):
    run.mkdir(parents=True)
    (run / "hits.json").write_text(json.dumps({"fps": 60.0, "hits": []}), encoding="utf-8")
    if strokes:
        rows = [{"frame": 60, "hitter": "lower", "stroke": "clear", "group": group_of("clear"),
                 "confidence": 0.6}]
        (run / "strokes.json").write_text(
            json.dumps(sc.build_payload(rows, {"version": "t"}, "model", "tracknet")), encoding="utf-8")


@pytest.fixture
def client(tmp_path, monkeypatch):
    videos = tmp_path / "videos"
    videos.mkdir()
    outputs = tmp_path / "outputs"
    _write_run(outputs / "vid")
    (outputs / "bare").mkdir()          # analysed, labels withheld: no strokes.json
    labels_dir = tmp_path / "stroke_labels"
    monkeypatch.setattr(webapp, "VIDEOS", videos)
    monkeypatch.setattr(webapp, "OUTPUTS", outputs)
    monkeypatch.setattr(webapp, "ATHLETES_PATH", tmp_path / "athletes.json")
    monkeypatch.setattr(webapp, "STROKE_LABELS_DIR", labels_dir)
    monkeypatch.setattr(webapp, "jobs", {})
    webapp.app.config["TESTING"] = True
    return webapp.app.test_client(), outputs, labels_dir


def _put(c, revision, labels, stem="vid", **extra):
    return c.put(f"/api/stroke-labels/{stem}", json=dict(revision=revision, labels=labels, **extra))


def _static_bilingual(res):
    assert BILINGUAL.match(res.get_json()["error"]), res.get_json()


def _stroke(outputs, stem="vid"):
    doc = json.loads((outputs / stem / "strokes.json").read_text(encoding="utf-8"))
    return doc["strokes"][0]["stroke"]


def test_get_empty(client):
    c, _, _ = client
    res = c.get("/api/stroke-labels/vid")
    assert res.status_code == 200
    assert res.get_json() == {"schema": 1, "revision": 0, "split": "train", "split_locked": False,
                              "labels": []}


def test_put_round_trip_and_apply(client):
    c, outputs, labels_dir = client
    res = _put(c, 0, [LABEL])
    assert res.status_code == 200 and res.get_json() == {"revision": 1, "applied": True}
    got = c.get("/api/stroke-labels/vid").get_json()
    assert got["revision"] == 1 and got["labels"][0]["stroke"] == "smash"
    assert _stroke(outputs) == "smash"
    assert (labels_dir / "vid.json").is_file()
    assert _put(c, 1, []).get_json() == {"revision": 2, "applied": True}
    assert _stroke(outputs) == "clear"      # clearing the labels restores the model's type


def test_put_stale_revision_409(client):
    c, _, _ = client
    assert _put(c, 0, [LABEL]).status_code == 200
    res = _put(c, 0, [])
    assert res.status_code == 409 and res.get_json()["revision"] == 1
    assert res.get_json()["reason"] == "stale"
    _static_bilingual(res)
    assert c.get("/api/stroke-labels/vid").get_json()["labels"][0]["stroke"] == "smash"


@pytest.mark.parametrize("body", [
    {"revision": 0, "labels": [{"time_sec": 1, "hitter": "lower", "stroke": "wallop", "kind": "label"}]},
    {"revision": "0", "labels": []},
    {"revision": 0, "labels": [], "split": "dev"},
    {"labels": []},
    [],
])
def test_put_bad_body_400(client, body):
    c, _, _ = client
    res = c.put("/api/stroke-labels/vid", json=body)
    assert res.status_code == 400
    _static_bilingual(res)


def test_unknown_stem_404(client):
    c, _, _ = client
    assert c.get("/api/stroke-labels/nope").status_code == 404
    res = _put(c, 0, [], stem="nope")
    assert res.status_code == 404
    _static_bilingual(res)
    assert c.get("/api/stroke-labels/..%2Fx").status_code == 404


def test_put_refuses_unreadable_store(client):
    c, _, labels_dir = client
    labels_dir.mkdir()
    (labels_dir / "vid.json").write_text("{broken", encoding="utf-8")
    res = _put(c, 0, [LABEL])
    assert res.status_code == 409 and res.get_json()["reason"] == "unreadable"
    _static_bilingual(res)
    assert (labels_dir / "vid.json").read_text(encoding="utf-8") == "{broken"
    assert c.get("/api/stroke-labels/vid").status_code == 500


def test_split_change_saved_when_unlocked(client):
    c, _, _ = client
    assert _put(c, 0, [], split="test").status_code == 200
    assert c.get("/api/stroke-labels/vid").get_json()["split"] == "test"


def test_split_change_refused_when_locked(tmp_path):
    outputs = tmp_path / "outputs"
    _write_run(outputs / "vid")
    flask_app = Flask(__name__)
    stroke_labels_routes.register(
        flask_app, labels_dir=lambda: tmp_path / "labels", outputs=lambda: outputs,
        lock=lambda: threading.Lock(), safe_stem=lambda s: s, job_active=lambda s: False,
        split_locked=lambda s: True)
    c = flask_app.test_client()
    assert c.get("/api/stroke-labels/vid").get_json()["split_locked"] is True
    res = _put(c, 0, [], split="test")
    assert res.status_code == 409 and res.get_json()["reason"] == "split_locked"
    _static_bilingual(res)
    assert _put(c, 0, [LABEL], split="train").status_code == 200    # same split: allowed


def test_put_during_job_skips_apply(client, monkeypatch):
    c, outputs, labels_dir = client
    monkeypatch.setattr(webapp, "jobs", {"vid": {"status": "running", "video_name": "vid.mp4"}})
    res = _put(c, 0, [LABEL])
    assert res.status_code == 200
    assert res.get_json() == {"revision": 1, "applied": False, "apply_skipped": "job_active"}
    assert _stroke(outputs) == "clear" and (labels_dir / "vid.json").is_file()


def test_put_without_strokes_json_saves_and_skips(client):
    c, outputs, labels_dir = client
    res = _put(c, 0, [LABEL], stem="bare")
    assert res.status_code == 200
    assert res.get_json() == {"revision": 1, "applied": False, "apply_skipped": "no_strokes"}
    assert (labels_dir / "bare.json").is_file()
    assert not (outputs / "bare" / "strokes.json").exists()


def test_delete_all_removes_labels(client):
    c, _, labels_dir = client
    assert _put(c, 0, [LABEL]).status_code == 200
    res = c.post("/api/delete/vid", json={"scope": "all"})
    assert res.status_code == 200, res.get_json()
    assert not (labels_dir / "vid.json").exists()


def test_delete_match_keeps_labels(client):
    c, _, labels_dir = client
    assert _put(c, 0, [LABEL]).status_code == 200
    res = c.post("/api/delete/vid", json={"scope": "match"})
    assert res.status_code == 200, res.get_json()
    assert (labels_dir / "vid.json").is_file()
