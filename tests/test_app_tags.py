"""Tag routes and the tags field on the library listing."""
import threading
import time

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


def test_put_error_responses_do_not_leak_the_store_path(client, monkeypatch):
    """{exc} in a Flask error response can include an absolute filesystem
    path -- print it server-side instead and return a static message."""
    c, videos, _outputs, store = client
    (videos / "clip.mp4").write_bytes(b"\x00")

    def boom(*a, **k):
        raise OSError(f"disk full: {store}")

    monkeypatch.setattr(webapp.libtags, "save", boom)
    res = c.put("/api/videos/clip/tags", json={"tags": ["smash"]})
    assert res.status_code == 500
    assert str(store) not in res.get_json()["error"]


def test_put_against_a_corrupt_store_backs_it_up_then_saves(client):
    """Spec Sec.9 permits a user tag edit to replace a corrupt store: back
    the bad file up untouched, then save the edit onto a fresh {}."""
    c, videos, _outputs, store = client
    (videos / "clip.mp4").write_bytes(b"\x00")
    store.parent.mkdir(parents=True, exist_ok=True)
    corrupt_text = "{not json"
    store.write_text(corrupt_text, encoding="utf-8")

    res = c.put("/api/videos/clip/tags", json={"tags": ["smash"]})
    assert res.status_code == 200
    assert libtags.load(store) == {"clip": ["smash"]}

    backups = list(store.parent.glob("library_tags.json.corrupt-*"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == corrupt_text


def test_put_against_an_unreadable_store_refuses_and_does_not_write(client, monkeypatch):
    c, videos, _outputs, store = client
    (videos / "clip.mp4").write_bytes(b"\x00")

    def boom(*a, **k):
        raise libtags.StoreUnreadable("locked")

    monkeypatch.setattr(webapp.libtags, "load_for_update", boom)
    res = c.put("/api/videos/clip/tags", json={"tags": ["smash"]})
    assert res.status_code == 500
    assert not store.exists()


def test_concurrent_puts_to_different_videos_do_not_clobber_each_other(client, monkeypatch):
    """Every tag write is load -> mutate -> save with no lock, and Flask serves
    requests on threads: two overlapping writes can interleave as A loads, B
    loads (before A saves), A saves, B saves -- B's save started from a
    snapshot that predates A's write, so A's write is silently lost.

    ``libtags.load`` is monkeypatched to sleep briefly so the overlapping
    read-modify-write windows are guaranteed rather than merely likely, then N
    concurrent PUTs to N different videos (each from its own thread, each
    using its own ``test_client()`` per the fixture's existing pattern) must
    all persist.
    """
    c, videos, _outputs, store = client
    names = [f"v{i}" for i in range(8)]
    for n in names:
        (videos / f"{n}.mp4").write_bytes(b"\x00")

    real_load = libtags.load

    def slow_load(path):
        time.sleep(0.02)
        return real_load(path)

    monkeypatch.setattr(webapp.libtags, "load", slow_load)

    results = {}

    def put_one(name):
        thread_client = webapp.app.test_client()
        res = thread_client.put(f"/api/videos/{name}/tags", json={"tags": [name]})
        results[name] = res.status_code

    threads = [threading.Thread(target=put_one, args=(n,)) for n in names]
    for th in threads:
        th.start()
    for th in threads:
        th.join()

    assert all(status == 200 for status in results.values()), results
    saved = libtags.load(store)
    lost = [n for n in names if saved.get(n) != [n]]
    assert not lost, f"tag writes lost to an unlocked read-modify-write race: {lost}"


def test_concurrent_rename_and_put_do_not_clobber(client, monkeypatch):
    """A rename touches the whole file just like a PUT does: one rename plus
    N concurrent PUTs to other videos must all persist, not just the PUTs."""
    c, videos, _outputs, store = client
    names = [f"v{i}" for i in range(6)]
    for n in names:
        (videos / f"{n}.mp4").write_bytes(b"\x00")
    (videos / "renamed.mp4").write_bytes(b"\x00")
    libtags.save({"renamed": ["old"]}, store)

    real_load = libtags.load
    real_load_for_update = libtags.load_for_update

    def slow_load(path):
        time.sleep(0.02)
        return real_load(path)

    def slow_load_for_update(path):
        time.sleep(0.02)
        return real_load_for_update(path)

    monkeypatch.setattr(webapp.libtags, "load", slow_load)
    monkeypatch.setattr(webapp.libtags, "load_for_update", slow_load_for_update)

    results = {}

    def put_one(name):
        thread_client = webapp.app.test_client()
        res = thread_client.put(f"/api/videos/{name}/tags", json={"tags": [name]})
        results[name] = res.status_code

    def rename_one():
        thread_client = webapp.app.test_client()
        res = thread_client.post("/api/tags/rename", json={"from": "old", "to": "new"})
        results["rename"] = res.status_code

    threads = [threading.Thread(target=put_one, args=(n,)) for n in names]
    threads.append(threading.Thread(target=rename_one))
    for th in threads:
        th.start()
    for th in threads:
        th.join()

    assert all(status == 200 for status in results.values()), results
    saved = libtags.load(store)
    lost = [n for n in names if saved.get(n) != [n]]
    assert not lost, f"PUTs lost to an unlocked race with a concurrent rename: {lost}"
    assert saved.get("renamed") == ["new"], "the rename itself must persist too"


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


def test_delete_of_an_untagged_video_does_not_create_the_store(client):
    """forget-on-delete must not write at all when the stem has no entry: no
    store file for an untagged video, and no rewrite of an unchanged store."""
    c, videos, outputs, store = client
    (videos / "clip.mp4").write_bytes(b"\x00")
    (outputs / "clip").mkdir(parents=True)
    assert not store.exists()

    assert c.post("/api/delete/clip", json={"scope": "all"}).status_code == 200
    assert not store.exists()


def test_video_delete_leaves_a_corrupt_store_byte_identical(client):
    """A video delete's tag cleanup must skip entirely on a corrupt store --
    no write, no backup -- and the delete itself still succeeds."""
    c, videos, outputs, store = client
    (videos / "clip.mp4").write_bytes(b"\x00")
    (outputs / "clip").mkdir(parents=True)
    store.parent.mkdir(parents=True, exist_ok=True)
    corrupt_text = "{not json"
    store.write_text(corrupt_text, encoding="utf-8")

    res = c.post("/api/delete/clip", json={"scope": "all"})
    assert res.status_code == 200
    assert store.read_text(encoding="utf-8") == corrupt_text
    assert list(store.parent.glob("*.corrupt-*")) == []


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


def test_the_real_tag_store_is_never_touched_by_a_fixture_that_forgets_to_patch_it(
        tmp_path, monkeypatch):
    """Regression for the suite writing the developer's real data/library_tags.json.

    This deliberately mirrors tests/test_app_delete.py's `_tree` fixture, which
    patches VIDEOS/OUTPUTS/TEMPLATES but not TAGS_PATH: without a suite-wide
    safety net, api_delete's scope="all" branch would call libtags.save(...,
    TAGS_PATH) against the real on-disk store. The safety net is the autouse
    fixture in tests/conftest.py, not this test's own setup -- this test's
    fixture intentionally does NOT patch TAGS_PATH itself.
    """
    videos = tmp_path / "videos"
    videos.mkdir()
    outputs = tmp_path / "outputs"
    outputs.mkdir()
    monkeypatch.setattr(webapp, "VIDEOS", videos)
    monkeypatch.setattr(webapp, "OUTPUTS", outputs)
    monkeypatch.setattr(webapp, "_video_duration_sec", lambda p: None)
    webapp.app.config["TESTING"] = True
    c = webapp.app.test_client()

    (videos / "clip.mp4").write_bytes(b"\x00")
    (outputs / "clip").mkdir(parents=True)

    real_store = webapp.PROJECT_ROOT / "data" / "library_tags.json"
    before = real_store.read_bytes() if real_store.exists() else None
    before_mtime = real_store.stat().st_mtime_ns if real_store.exists() else None

    assert c.post("/api/delete/clip", json={"scope": "all"}).status_code == 200

    assert webapp.TAGS_PATH != real_store, (
        "TAGS_PATH must be redirected even when a test fixture forgets to do it")
    after = real_store.read_bytes() if real_store.exists() else None
    after_mtime = real_store.stat().st_mtime_ns if real_store.exists() else None
    assert after == before
    assert after_mtime == before_mtime
