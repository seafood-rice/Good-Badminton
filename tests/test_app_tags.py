"""Tag routes and the tags field on the library listing."""
import json

import pytest

import app as webapp
from badminton_analysis.library import tags as libtags


@pytest.fixture
def client(tmp_path, monkeypatch):
    videos = tmp_path / "videos"
    videos.mkdir()
    outputs = tmp_path / "outputs"
    outputs.mkdir()
    monkeypatch.setattr(webapp, "VIDEOS", videos)
    monkeypatch.setattr(webapp, "OUTPUTS", outputs)
    monkeypatch.setattr(webapp, "TAGS_PATH", tmp_path / "data" / "library_tags.json")
    monkeypatch.setattr(webapp, "_video_duration_sec", lambda p: None)
    webapp.app.config["TESTING"] = True
    return webapp.app.test_client(), videos, outputs, tmp_path / "data" / "library_tags.json"


def test_rows_carry_their_tags(client):
    c, videos, _outputs, store = client
    (videos / "clip.mp4").write_bytes(b"\x00")
    libtags.save({"clip": ["court-a", "smash"]}, store)

    row = next(r for r in c.get("/api/videos").get_json() if r["name"] == "clip")
    assert row["tags"] == ["court-a", "smash"]


def test_untagged_rows_carry_an_empty_list_not_null(client):
    c, videos, _outputs, _store = client
    (videos / "clip.mp4").write_bytes(b"\x00")
    assert next(r for r in c.get("/api/videos").get_json()
                if r["name"] == "clip")["tags"] == []


def test_a_missing_store_does_not_break_the_library(client):
    c, videos, _outputs, _store = client
    (videos / "clip.mp4").write_bytes(b"\x00")
    assert c.get("/api/videos").status_code == 200


def test_a_corrupt_store_does_not_break_the_library(client):
    c, videos, _outputs, store = client
    (videos / "clip.mp4").write_bytes(b"\x00")
    store.parent.mkdir(parents=True, exist_ok=True)
    store.write_text("{not json", encoding="utf-8")

    res = c.get("/api/videos")
    assert res.status_code == 200
    assert next(r for r in res.get_json() if r["name"] == "clip")["tags"] == []


def test_the_store_is_read_once_not_per_video(client, monkeypatch):
    """The listing already globs the disk; N store reads would make it worse."""
    c, videos, _outputs, _store = client
    for name in ("a", "b", "c"):
        (videos / f"{name}.mp4").write_bytes(b"\x00")

    calls = []
    real = libtags.load
    monkeypatch.setattr(webapp.libtags, "load", lambda p: calls.append(p) or real(p))

    c.get("/api/videos")
    assert len(calls) == 1
