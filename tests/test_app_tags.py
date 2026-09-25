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


def test_rename_applies_across_videos(client):
    c, videos, _outputs, store = client
    for n in ("a", "b"):
        (videos / f"{n}.mp4").write_bytes(b"\x00")
    libtags.save({"a": ["smash"], "b": ["smash", "net"]}, store)

    res = c.post("/api/tags/rename", json={"from": "smash", "to": "smashes"})
    assert res.status_code == 200
    assert res.get_json()["affected"] == 2
    assert res.get_json()["merged"] is False
    assert libtags.load(store) == {"a": ["smashes"], "b": ["net", "smashes"]}


def test_rename_onto_an_existing_tag_merges_and_says_so(client):
    c, videos, _outputs, store = client
    (videos / "a.mp4").write_bytes(b"\x00")
    libtags.save({"a": ["smash", "net"]}, store)

    res = c.post("/api/tags/rename", json={"from": "smash", "to": "net"})
    assert res.status_code == 200
    assert res.get_json()["merged"] is True
    assert libtags.load(store) == {"a": ["net"]}


def test_rename_normalises_both_ends(client):
    c, videos, _outputs, store = client
    (videos / "a.mp4").write_bytes(b"\x00")
    libtags.save({"a": ["smash"]}, store)

    c.post("/api/tags/rename", json={"from": " SMASH ", "to": " Smashes "})
    assert libtags.load(store) == {"a": ["smashes"]}


def test_rename_to_a_reserved_name_is_refused(client):
    c, videos, _outputs, store = client
    (videos / "a.mp4").write_bytes(b"\x00")
    libtags.save({"a": ["smash"]}, store)

    assert c.post("/api/tags/rename", json={"from": "smash", "to": "match"}).status_code == 400
    assert libtags.load(store) == {"a": ["smash"]}


def test_rename_from_a_reserved_name_is_refused(client):
    c, _videos, _outputs, _store = client
    assert c.post("/api/tags/rename", json={"from": "match", "to": "x"}).status_code == 400


def test_rename_with_missing_fields_is_refused(client):
    c, _videos, _outputs, _store = client
    assert c.post("/api/tags/rename", json={"from": "smash"}).status_code == 400
    assert c.post("/api/tags/rename", json={}).status_code == 400


def test_delete_removes_the_tag_everywhere(client):
    c, videos, _outputs, store = client
    for n in ("a", "b"):
        (videos / f"{n}.mp4").write_bytes(b"\x00")
    libtags.save({"a": ["smash"], "b": ["smash", "net"]}, store)

    res = c.delete("/api/tags/smash")
    assert res.status_code == 200
    assert res.get_json()["affected"] == 2
    assert libtags.load(store) == {"b": ["net"]}


def test_delete_of_a_reserved_name_is_refused(client):
    c, _videos, _outputs, _store = client
    assert c.delete("/api/tags/match").status_code == 400


def test_delete_of_an_unused_tag_is_a_no_op_not_an_error(client):
    c, videos, _outputs, store = client
    (videos / "a.mp4").write_bytes(b"\x00")
    libtags.save({"a": ["net"]}, store)

    res = c.delete("/api/tags/ghost")
    assert res.status_code == 200
    assert res.get_json()["affected"] == 0
    assert libtags.load(store) == {"a": ["net"]}


def test_rename_reports_a_write_failure(client, monkeypatch):
    c, videos, _outputs, store = client
    (videos / "a.mp4").write_bytes(b"\x00")
    libtags.save({"a": ["smash"]}, store)
    monkeypatch.setattr(webapp.libtags, "save",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")))
    assert c.post("/api/tags/rename", json={"from": "smash", "to": "x"}).status_code == 500


def test_deleting_a_video_entirely_drops_its_tags(client):
    c, videos, outputs, store = client
    (videos / "clip.mp4").write_bytes(b"\x00")
    (outputs / "clip").mkdir(parents=True)
    libtags.save({"clip": ["smash"], "other": ["net"]}, store)

    assert c.post("/api/delete/clip", json={"scope": "all"}).status_code == 200
    assert libtags.load(store) == {"other": ["net"]}


def test_deleting_only_analysis_results_keeps_the_tags(client):
    """Tags describe the video. Clearing results must not clear its labels."""
    c, videos, outputs, store = client
    (videos / "clip.mp4").write_bytes(b"\x00")
    out = outputs / "clip"
    out.mkdir(parents=True)
    (out / "detections.jsonl").write_text("{}\n", encoding="utf-8")
    libtags.save({"clip": ["smash"]}, store)

    assert c.post("/api/delete/clip", json={"scope": "match"}).status_code == 200
    assert libtags.load(store) == {"clip": ["smash"]}


def test_a_failed_tag_forget_does_not_fail_the_delete(client, monkeypatch):
    """The files are already gone by then; a tag-store hiccup must not report
    the delete as failed."""
    c, videos, outputs, store = client
    (videos / "clip.mp4").write_bytes(b"\x00")
    (outputs / "clip").mkdir(parents=True)
    libtags.save({"clip": ["smash"]}, store)
    monkeypatch.setattr(webapp.libtags, "save",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")))

    assert c.post("/api/delete/clip", json={"scope": "all"}).status_code == 200
