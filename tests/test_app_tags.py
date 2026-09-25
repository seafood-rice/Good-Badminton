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


def test_put_stores_normalised_tags(client):
    c, videos, _outputs, store = client
    (videos / "clip.mp4").write_bytes(b"\x00")

    res = c.put("/api/videos/clip/tags", json={"tags": [" Smash ", "court-a", "smash"]})
    assert res.status_code == 200
    assert res.get_json()["tags"] == ["court-a", "smash"]
    assert libtags.load(store) == {"clip": ["court-a", "smash"]}


def test_put_replaces_rather_than_appends(client):
    c, videos, _outputs, store = client
    (videos / "clip.mp4").write_bytes(b"\x00")
    libtags.save({"clip": ["old"]}, store)

    c.put("/api/videos/clip/tags", json={"tags": ["new"]})
    assert libtags.load(store) == {"clip": ["new"]}


def test_put_with_an_empty_list_clears_the_video(client):
    c, videos, _outputs, store = client
    (videos / "clip.mp4").write_bytes(b"\x00")
    libtags.save({"clip": ["old"]}, store)

    assert c.put("/api/videos/clip/tags", json={"tags": []}).status_code == 200
    assert libtags.load(store) == {}


def test_put_rejects_a_reserved_tag_and_saves_nothing(client):
    c, videos, _outputs, store = client
    (videos / "clip.mp4").write_bytes(b"\x00")

    res = c.put("/api/videos/clip/tags", json={"tags": ["smash", "match"]})
    assert res.status_code == 400
    assert "match" in res.get_json()["rejected"]
    assert libtags.load(store) == {}, "a rejected request must not partially save"


def test_put_rejects_an_overlong_tag(client):
    c, videos, _outputs, _store = client
    (videos / "clip.mp4").write_bytes(b"\x00")
    res = c.put("/api/videos/clip/tags", json={"tags": ["x" * 41]})
    assert res.status_code == 400


def test_put_rejects_a_non_list_body(client):
    c, videos, _outputs, _store = client
    (videos / "clip.mp4").write_bytes(b"\x00")
    assert c.put("/api/videos/clip/tags", json={"tags": "smash"}).status_code == 400


def test_put_on_an_unknown_video_is_404(client):
    c, _videos, _outputs, _store = client
    assert c.put("/api/videos/ghost/tags", json={"tags": ["x"]}).status_code == 404


def test_put_on_a_traversal_attempt_is_404(client):
    c, _videos, _outputs, _store = client
    assert c.put("/api/videos/..%2Fetc/tags", json={"tags": ["x"]}).status_code in (404, 400)


def test_put_reports_a_write_failure_instead_of_claiming_success(client, monkeypatch):
    c, videos, _outputs, _store = client
    (videos / "clip.mp4").write_bytes(b"\x00")

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(webapp.libtags, "save", boom)
    res = c.put("/api/videos/clip/tags", json={"tags": ["smash"]})
    assert res.status_code == 500
    assert "error" in res.get_json()
