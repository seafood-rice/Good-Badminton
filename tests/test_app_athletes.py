"""Athlete, squad and match-record routes (badminton_analysis/web/athletes_routes.py)."""
import re
import threading
import time

import pytest

import app as webapp
from badminton_analysis.library import athletes as libathletes
from badminton_analysis.web import athletes_routes

BILINGUAL = re.compile(r"^[^/]*[一-鿿][^/]* / [A-Za-z].*$")


@pytest.fixture
def client(tmp_path, monkeypatch):
    videos = tmp_path / "videos"
    videos.mkdir()
    outputs = tmp_path / "outputs"
    outputs.mkdir()
    store = tmp_path / "data" / "athletes.json"
    monkeypatch.setattr(webapp, "VIDEOS", videos)
    monkeypatch.setattr(webapp, "OUTPUTS", outputs)
    monkeypatch.setattr(webapp, "ATHLETES_PATH", store)
    monkeypatch.setattr(webapp, "_video_duration_sec", lambda p: None)
    webapp.app.config["TESTING"] = True
    return webapp.app.test_client(), videos, outputs, store


def _add(c, name, **extra):
    res = c.post("/api/athletes", json={"name": name, **extra})
    assert res.status_code == 201, res.get_json()
    return res.get_json()["id"]


def _assert_static_bilingual(res, tmp_path):
    error = res.get_json()["error"]
    assert BILINGUAL.match(error), error
    assert "tmp" not in error.lower()
    assert str(tmp_path) not in error


# ------------------------------------------------------------------ athletes

def test_post_creates_an_athlete(client):
    c, *_ = client
    res = c.post("/api/athletes", json={"name": "J. Tanaka"})
    assert res.status_code == 201
    body = res.get_json()
    assert body["id"].startswith("a_")
    assert body["name"] == "J. Tanaka"
    assert body["squad_id"] is None


def test_post_duplicate_name_is_409_case_insensitively(client, tmp_path):
    c, *_ = client
    _add(c, "J. Tanaka")
    res = c.post("/api/athletes", json={"name": "j. tanaka"})
    assert res.status_code == 409
    _assert_static_bilingual(res, tmp_path)


@pytest.mark.parametrize("body", [{"name": ""}, {"name": "   "}, {"name": "x" * 61},
                                  {"name": 5}, {}, {"name": "a\x01b"}])
def test_post_invalid_name_is_400_with_a_static_body(client, tmp_path, body):
    c, *_ = client
    res = c.post("/api/athletes", json=body)
    assert res.status_code == 400
    _assert_static_bilingual(res, tmp_path)


def test_post_with_a_non_object_or_missing_body_is_400(client):
    c, *_ = client
    assert c.post("/api/athletes", json=["x"]).status_code == 400
    assert c.post("/api/athletes", data="not json",
                  content_type="application/json").status_code == 400
    assert c.post("/api/athletes").status_code == 400


def test_post_with_an_unknown_squad_is_400(client, tmp_path):
    c, *_ = client
    res = c.post("/api/athletes", json={"name": "A", "squad_id": "s_nope"})
    assert res.status_code == 400
    _assert_static_bilingual(res, tmp_path)
    assert c.post("/api/athletes", json={"name": "A", "squad_id": ["x"]}).status_code == 400


def test_get_lists_athletes_sorted_with_match_counts(client):
    c, videos, *_ = client
    (videos / "clip.mp4").write_bytes(b"\x00")
    beta = _add(c, "beta")
    alpha = _add(c, "Alpha")

    body = c.get("/api/athletes").get_json()
    assert [a["name"] for a in body["athletes"]] == ["Alpha", "beta"]
    assert all(a["matches"] == 0 for a in body["athletes"])
    assert set(body["athletes"][0]) == {"id", "name", "squad_id", "matches"}

    res = c.put("/api/matches/clip", json={"lower": {"athlete_id": alpha}})
    assert res.status_code == 200
    counts = {a["id"]: a["matches"] for a in c.get("/api/athletes").get_json()["athletes"]}
    assert counts == {alpha: 1, beta: 0}


def test_a_match_with_the_same_athlete_on_both_sides_counts_once(client):
    c, videos, *_ = client
    (videos / "clip.mp4").write_bytes(b"\x00")
    a = _add(c, "A")
    c.put("/api/matches/clip", json={"upper": {"athlete_id": a}, "lower": {"athlete_id": a}})
    assert c.get("/api/athletes").get_json()["athletes"][0]["matches"] == 1


def test_get_on_a_missing_store_is_empty(client):
    c, *_ = client
    assert c.get("/api/athletes").get_json() == {"athletes": [], "squads": []}


def test_put_athlete_renames_and_assigns_a_squad(client):
    c, *_ = client
    sid = c.post("/api/squads", json={"name": "Seniors"}).get_json()["id"]
    aid = _add(c, "J. Tanaka")

    res = c.put(f"/api/athletes/{aid}", json={"name": "Jun Tanaka", "squad_id": sid})
    assert res.status_code == 200
    body = c.get("/api/athletes").get_json()
    assert body["athletes"][0]["name"] == "Jun Tanaka"
    assert body["athletes"][0]["squad_id"] == sid
    assert body["squads"] == [{"id": sid, "name": "Seniors", "athletes": 1}]

    assert c.put(f"/api/athletes/{aid}", json={"squad_id": None}).status_code == 200
    assert c.get("/api/athletes").get_json()["athletes"][0]["squad_id"] is None
    assert c.get("/api/athletes").get_json()["athletes"][0]["name"] == "Jun Tanaka"


def test_put_athlete_unknown_is_404_and_errors_map_cleanly(client, tmp_path):
    c, *_ = client
    res = c.put("/api/athletes/a_nope", json={"name": "X"})
    assert res.status_code == 404
    _assert_static_bilingual(res, tmp_path)

    aid = _add(c, "A")
    _add(c, "B")
    dup = c.put(f"/api/athletes/{aid}", json={"name": "b"})
    assert dup.status_code == 409
    _assert_static_bilingual(dup, tmp_path)
    assert c.put(f"/api/athletes/{aid}", json={"name": ""}).status_code == 400
    assert c.put(f"/api/athletes/{aid}", json={"squad_id": "s_nope"}).status_code == 400
    assert c.put(f"/api/athletes/{aid}", json=["x"]).status_code == 400


def test_delete_athlete_keeps_history_as_a_plain_name(client):
    c, videos, *_ = client
    (videos / "clip.mp4").write_bytes(b"\x00")
    aid = _add(c, "J. Tanaka")
    c.put("/api/matches/clip", json={"lower": {"athlete_id": aid}})

    res = c.delete(f"/api/athletes/{aid}")
    assert res.status_code == 200
    assert res.get_json() == {"ok": True, "rewritten": 1}
    assert c.get("/api/matches/clip").get_json()["lower"] == {"name": "J. Tanaka"}
    assert c.get("/api/athletes").get_json()["athletes"] == []


def test_delete_unknown_athlete_is_200_with_zero_rewritten(client):
    c, *_ = client
    res = c.delete("/api/athletes/a_nope")
    assert res.status_code == 200
    assert res.get_json() == {"ok": True, "rewritten": 0}


# -------------------------------------------------------------------- squads

def test_squad_create_rename_delete(client, tmp_path):
    c, *_ = client
    res = c.post("/api/squads", json={"name": "Seniors"})
    assert res.status_code == 201
    sid = res.get_json()["id"]
    assert sid.startswith("s_")

    dup = c.post("/api/squads", json={"name": "SENIORS"})
    assert dup.status_code == 409
    _assert_static_bilingual(dup, tmp_path)
    assert c.post("/api/squads", json={"name": " "}).status_code == 400

    assert c.put(f"/api/squads/{sid}", json={"name": "Juniors"}).status_code == 200
    assert c.get("/api/athletes").get_json()["squads"][0]["name"] == "Juniors"
    assert c.put("/api/squads/s_nope", json={"name": "X"}).status_code == 404
    assert c.put(f"/api/squads/{sid}", json={"name": ""}).status_code == 400

    aid = _add(c, "A", squad_id=sid)
    res = c.delete(f"/api/squads/{sid}")
    assert res.status_code == 200
    assert res.get_json() == {"ok": True, "cleared": 1}
    body = c.get("/api/athletes").get_json()
    assert body["squads"] == []
    assert [(a["id"], a["squad_id"]) for a in body["athletes"]] == [(aid, None)]
    assert c.delete("/api/squads/s_nope").get_json() == {"ok": True, "cleared": 0}


# ------------------------------------------------------------------- matches

def test_match_for_an_unknown_stem_is_404(client, tmp_path):
    c, *_ = client
    res = c.put("/api/matches/nope", json={"date": "2026-10-11"})
    assert res.status_code == 404
    _assert_static_bilingual(res, tmp_path)
    assert c.get("/api/matches/nope").status_code == 404


def test_a_stem_with_spaces_round_trips(client):
    c, videos, *_ = client
    stem = "Dji 20260919100627 0004 D"
    (videos / f"{stem}.mp4").write_bytes(b"\x00")
    record = {"date": "2026-09-19", "upper": {"name": "S. Ito"}, "lower": None,
              "result": {"winner": "upper", "sets": [[21, 18], [21, 19]]}}
    res = c.put(f"/api/matches/{stem}", json=record)
    assert res.status_code == 200
    assert c.get(f"/api/matches/{stem.replace(' ', '%20')}").get_json() == record


def test_get_match_without_a_record_is_empty(client):
    c, videos, *_ = client
    (videos / "clip.mp4").write_bytes(b"\x00")
    assert c.get("/api/matches/clip").get_json() == {}


def test_put_empty_or_all_null_deletes_the_record(client):
    c, videos, _outputs, store = client
    (videos / "clip.mp4").write_bytes(b"\x00")
    c.put("/api/matches/clip", json={"date": "2026-10-11"})
    assert c.get("/api/matches/clip").get_json()["date"] == "2026-10-11"

    assert c.put("/api/matches/clip", json={}).status_code == 200
    assert c.get("/api/matches/clip").get_json() == {}
    assert libathletes.load(store)["matches"] == {}

    c.put("/api/matches/clip", json={"date": "2026-10-11"})
    assert c.put("/api/matches/clip",
                 json={"date": None, "upper": None, "lower": None, "result": None}
                 ).status_code == 200
    assert c.get("/api/matches/clip").get_json() == {}


def test_put_replaces_the_record_whole(client):
    c, videos, *_ = client
    (videos / "clip.mp4").write_bytes(b"\x00")
    c.put("/api/matches/clip", json={"date": "2026-10-11", "upper": {"name": "A"}})
    c.put("/api/matches/clip", json={"lower": {"name": "B"}})
    assert c.get("/api/matches/clip").get_json() == {
        "date": None, "upper": None, "lower": {"name": "B"}, "result": None}


@pytest.mark.parametrize("record,status", [
    ({"date": "2026-02-30"}, 400),
    ({"date": "11/10/2026"}, 400),
    ({"upper": {"athlete_id": "a_ghost"}}, 400),
    ({"upper": {"athlete_id": "a_x", "name": "A"}}, 400),
    ({"result": {"winner": "left", "sets": []}}, 400),
    ({"result": {"winner": "upper", "sets": [[1, 100]]}}, 400),
    (["not", "a", "dict"], 400),
])
def test_put_invalid_records_are_400_and_save_nothing(client, tmp_path, record, status):
    c, videos, _outputs, store = client
    (videos / "clip.mp4").write_bytes(b"\x00")
    res = c.put("/api/matches/clip", json=record)
    assert res.status_code == status
    _assert_static_bilingual(res, tmp_path)
    assert not store.exists()


def test_put_with_no_body_is_400_and_does_not_delete(client):
    c, videos, *_ = client
    (videos / "clip.mp4").write_bytes(b"\x00")
    c.put("/api/matches/clip", json={"date": "2026-10-11"})
    assert c.put("/api/matches/clip").status_code == 400
    assert c.get("/api/matches/clip").get_json()["date"] == "2026-10-11"


# ------------------------------------------------------------- store failures

def test_a_corrupt_store_is_backed_up_then_replaced_by_a_post(client):
    c, _videos, _outputs, store = client
    store.parent.mkdir(parents=True, exist_ok=True)
    corrupt_text = "{not json"
    store.write_text(corrupt_text, encoding="utf-8")

    assert c.get("/api/athletes").get_json() == {"athletes": [], "squads": []}
    assert store.read_text(encoding="utf-8") == corrupt_text, "a read must not touch it"

    assert c.post("/api/athletes", json={"name": "J. Tanaka"}).status_code == 201
    backups = list(store.parent.glob("athletes.json.corrupt-*"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == corrupt_text
    assert [a["name"] for a in c.get("/api/athletes").get_json()["athletes"]] == ["J. Tanaka"]


def test_a_directory_as_the_store_is_500_and_left_untouched(client, tmp_path):
    c, _videos, _outputs, store = client
    store.mkdir(parents=True)
    (store / "keep.txt").write_text("x", encoding="utf-8")

    res = c.post("/api/athletes", json={"name": "A"})
    assert res.status_code == 500
    _assert_static_bilingual(res, tmp_path)
    assert store.is_dir()
    assert [p.name for p in store.iterdir()] == ["keep.txt"]
    assert c.get("/api/athletes").status_code == 200


def test_an_unreadable_store_refuses_every_write_and_does_not_write(client, monkeypatch, tmp_path):
    c, videos, _outputs, store = client
    (videos / "clip.mp4").write_bytes(b"\x00")

    def boom(*a, **k):
        raise libathletes.StoreUnreadable(f"locked {store}")

    monkeypatch.setattr(libathletes, "load_for_update", boom)
    saved = []
    monkeypatch.setattr(libathletes, "save", lambda *a, **k: saved.append(a))

    calls = [
        c.post("/api/athletes", json={"name": "A"}),
        c.put("/api/athletes/a_x", json={"name": "A"}),
        c.delete("/api/athletes/a_x"),
        c.post("/api/squads", json={"name": "S"}),
        c.put("/api/squads/s_x", json={"name": "S"}),
        c.delete("/api/squads/s_x"),
        c.put("/api/matches/clip", json={"date": "2026-10-11"}),
    ]
    for res in calls:
        assert res.status_code == 500
        _assert_static_bilingual(res, tmp_path)
    assert saved == []
    assert not store.exists()


def test_a_save_failure_is_500_with_a_static_body(client, monkeypatch, tmp_path):
    c, *_ = client

    def boom(*a, **k):
        raise OSError(f"disk full: {tmp_path}")

    monkeypatch.setattr(libathletes, "save", boom)
    res = c.post("/api/athletes", json={"name": "A"})
    assert res.status_code == 500
    _assert_static_bilingual(res, tmp_path)


# --------------------------------------------------------------- concurrency

def test_concurrent_posts_do_not_clobber_each_other(client, monkeypatch):
    c, *_ = client
    real = libathletes.load_for_update

    def slow(path):
        time.sleep(0.01)
        return real(path)

    monkeypatch.setattr(libathletes, "load_for_update", slow)
    statuses = {}

    def post_one(i):
        statuses[i] = webapp.app.test_client().post(
            "/api/athletes", json={"name": f"athlete {i}"}).status_code

    threads = [threading.Thread(target=post_one, args=(i,)) for i in range(20)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()

    assert all(s == 201 for s in statuses.values()), statuses
    assert len(c.get("/api/athletes").get_json()["athletes"]) == 20


# ------------------------------------------- no-op edits never touch the store

def _corrupt_store(store):
    store.parent.mkdir(parents=True, exist_ok=True)
    store.write_bytes(b"{not json")
    return store.read_bytes()


@pytest.mark.parametrize("url", ["/api/athletes/a_nope", "/api/squads/s_nope"])
def test_unknown_id_delete_on_a_corrupt_store_leaves_it_byte_identical(client, url):
    c, _videos, _outputs, store = client
    before = _corrupt_store(store)
    res = c.delete(url)
    assert res.status_code == 200
    assert store.read_bytes() == before
    assert list(store.parent.glob("*.corrupt-*")) == []


@pytest.mark.parametrize("url", ["/api/athletes/a_nope", "/api/squads/s_nope"])
def test_unknown_id_delete_on_a_missing_store_creates_no_file(client, url):
    c, _videos, _outputs, store = client
    res = c.delete(url)
    assert res.status_code == 200
    assert not store.exists()
    assert not store.parent.exists() or list(store.parent.iterdir()) == []


def test_unknown_id_delete_does_not_rewrite_an_existing_store(client, monkeypatch):
    c, *_ = client
    _add(c, "A")
    saved = []
    monkeypatch.setattr(libathletes, "save", lambda *a, **k: saved.append(a))
    assert c.delete("/api/athletes/a_nope").get_json() == {"ok": True, "rewritten": 0}
    assert c.delete("/api/squads/s_nope").get_json() == {"ok": True, "cleared": 0}
    assert saved == []


def test_deleting_an_athlete_with_no_matches_still_saves(client):
    # existence decides, not the rewrite count: zero rewrites, but the athlete goes
    c, *_ = client
    athlete_id = _add(c, "A")
    res = c.delete(f"/api/athletes/{athlete_id}")
    assert res.get_json() == {"ok": True, "rewritten": 0}
    assert c.get("/api/athletes").get_json()["athletes"] == []


def test_deleting_an_empty_squad_still_saves(client):
    c, *_ = client
    squad_id = c.post("/api/squads", json={"name": "S"}).get_json()["id"]
    assert c.delete(f"/api/squads/{squad_id}").get_json() == {"ok": True, "cleared": 0}
    assert c.get("/api/athletes").get_json()["squads"] == []


def test_an_empty_put_saves_nothing(client, monkeypatch):
    c, *_ = client
    athlete_id = _add(c, "A")
    squad_id = c.post("/api/squads", json={"name": "S"}).get_json()["id"]
    saved = []
    monkeypatch.setattr(libathletes, "save", lambda *a, **k: saved.append(a))
    res = c.put(f"/api/athletes/{athlete_id}", json={})
    assert res.status_code == 200 and res.get_json()["name"] == "A"
    res = c.put(f"/api/squads/{squad_id}", json={})
    assert res.status_code == 200 and res.get_json()["name"] == "S"
    res = c.put(f"/api/athletes/{athlete_id}", json={"name": "A"})     # unchanged too
    assert res.status_code == 200
    assert saved == []
    assert c.put("/api/athletes/a_nope", json={}).status_code == 404
    assert c.put("/api/squads/s_nope", json={}).status_code == 404


def test_an_empty_put_on_a_corrupt_store_leaves_it_byte_identical(client):
    c, _videos, _outputs, store = client
    before = _corrupt_store(store)
    assert c.put("/api/athletes/a_x", json={}).status_code == 404
    assert c.put("/api/squads/s_x", json={}).status_code == 404
    assert store.read_bytes() == before
    assert list(store.parent.glob("*.corrupt-*")) == []


# ---------------------------------------- a video delete forgets its match record

def _video(videos, outputs, stem="clip"):
    (videos / f"{stem}.mp4").write_bytes(b"\x00")
    (outputs / stem).mkdir(parents=True, exist_ok=True)


def test_delete_all_removes_the_videos_match_record(client):
    c, videos, outputs, store = client
    _video(videos, outputs, "clip")
    _video(videos, outputs, "other")
    athlete_id = _add(c, "A")
    for stem in ("clip", "other"):
        assert c.put(f"/api/matches/{stem}", json={
            "date": "2026-10-11", "upper": {"athlete_id": athlete_id}}).status_code == 200

    assert c.post("/api/delete/clip", json={"scope": "all"}).status_code == 200
    data = libathletes.load(store)
    assert "clip" not in data["matches"]
    assert data["matches"]["other"]["date"] == "2026-10-11"
    assert list(data["athletes"]) == [athlete_id]


def test_delete_of_results_only_keeps_the_match_record(client):
    c, videos, outputs, store = client
    _video(videos, outputs)
    (outputs / "clip" / "detections.jsonl").write_text("{}\n", encoding="utf-8")
    assert c.put("/api/matches/clip", json={"date": "2026-10-11"}).status_code == 200
    assert c.post("/api/delete/clip", json={"scope": "match"}).status_code == 200
    assert libathletes.load(store)["matches"]["clip"]["date"] == "2026-10-11"


def test_delete_all_without_a_record_does_not_write_the_store(client, monkeypatch):
    c, videos, outputs, store = client
    _video(videos, outputs)
    assert c.post("/api/delete/clip", json={"scope": "all"}).status_code == 200
    assert not store.exists()                      # a missing store is not created

    _video(videos, outputs)
    _add(c, "A")
    saved = []
    monkeypatch.setattr(libathletes, "save", lambda *a, **k: saved.append(a))
    assert c.post("/api/delete/clip", json={"scope": "all"}).status_code == 200
    assert saved == []                             # an unchanged store is not rewritten


def test_delete_all_leaves_a_corrupt_store_byte_identical(client):
    c, videos, outputs, store = client
    _video(videos, outputs)
    before = _corrupt_store(store)
    assert c.post("/api/delete/clip", json={"scope": "all"}).status_code == 200
    assert store.read_bytes() == before
    assert list(store.parent.glob("*.corrupt-*")) == []
    assert not (videos / "clip.mp4").exists()


def test_delete_all_with_an_unreadable_store_still_succeeds(client, monkeypatch):
    c, videos, outputs, store = client
    _video(videos, outputs)

    def boom(*a, **k):
        raise libathletes.StoreUnreadable("locked")

    monkeypatch.setattr(libathletes, "load_for_update", boom)
    saved = []
    monkeypatch.setattr(libathletes, "save", lambda *a, **k: saved.append(a))
    assert c.post("/api/delete/clip", json={"scope": "all"}).status_code == 200
    assert saved == [] and not store.exists()
    assert not (videos / "clip.mp4").exists()


def test_a_failed_match_forget_does_not_fail_the_delete(client, monkeypatch):
    c, videos, outputs, _store = client
    _video(videos, outputs)
    assert c.put("/api/matches/clip", json={"date": "2026-10-11"}).status_code == 200

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(libathletes, "save", boom)
    assert c.post("/api/delete/clip", json={"scope": "all"}).status_code == 200
    assert not (videos / "clip.mp4").exists()


# ----------------------------------------------------------- doubles records

def _doubles_record(players, **extra):
    record = {"date": "2026-10-11", "format": "doubles", "players": players,
              "result": {"winner_team": 1, "sets": [[21, 18], [19, 21], [21, 15]]}}
    record.update(extra)
    return record


def test_doubles_record_round_trips_through_put_and_get(client):
    c, videos, _outputs, store = client
    (videos / "clip.mp4").write_bytes(b"\x00")
    a, b = _add(c, "Alice"), _add(c, "Bob")
    record = _doubles_record({"P1": {"athlete_id": a}, "P2": {"name": "Guest"},
                              "P3": {"athlete_id": b}, "P4": None})
    res = c.put("/api/matches/clip", json=record)
    assert res.status_code == 200
    assert res.get_json() == {"ok": True, "match": record}
    assert c.get("/api/matches/clip").get_json() == record
    assert libathletes.load(store)["matches"]["clip"] == record


def test_doubles_record_with_a_stem_with_spaces_round_trips(client):
    c, videos, *_ = client
    stem = "Dji 20260919100627 0004 D"
    (videos / f"{stem}.mp4").write_bytes(b"\x00")
    record = _doubles_record({"P1": {"name": "A"}})
    assert c.put(f"/api/matches/{stem}", json=record).status_code == 200
    assert c.get(f"/api/matches/{stem.replace(' ', '%20')}").get_json() == record


def test_a_singles_put_still_returns_exactly_the_four_singles_keys(client):
    c, videos, *_ = client
    (videos / "clip.mp4").write_bytes(b"\x00")
    c.put("/api/matches/clip", json={"upper": {"name": "A"}})
    assert set(c.get("/api/matches/clip").get_json()) == {"date", "upper", "lower", "result"}


@pytest.mark.parametrize("winner,status", [(1, 200), (2, 200), (3, 400), ("upper", 400),
                                           (True, 400), (None, 400)])
def test_doubles_winner_team_over_http(client, tmp_path, winner, status):
    c, videos, *_ = client
    (videos / "clip.mp4").write_bytes(b"\x00")
    res = c.put("/api/matches/clip", json={
        "format": "doubles", "players": {}, "result": {"winner_team": winner, "sets": []}})
    assert res.status_code == status
    if status != 200:
        _assert_static_bilingual(res, tmp_path)


@pytest.mark.parametrize("record", [
    {"format": "doubles", "players": {"P5": None}},                             # key outside P1..P4
    {"format": "doubles", "players": ["P1"]},                                   # not an object
    {"format": "doubles", "players": {"P1": {"athlete_id": "a_x", "name": "A"}}},   # both
    {"format": "doubles", "players": {"P1": {"athlete_id": "a_ghost"}}},        # unknown athlete
    {"format": "doubles", "players": {"P1": "A"}},                              # not an object
    {"format": "doubles", "players": {"P1": {"name": "A"}}, "upper": {"name": "B"}},   # mixed
    {"upper": {"name": "A"}, "players": {"P1": {"name": "B"}}},                 # mixed
    {"format": "doubles", "result": {"winner": "upper", "sets": []}},           # singles winner
    {"format": "doubles", "result": {"winner_team": 1, "sets": [[1, 100]]}},    # score range
    {"format": "doubles", "result": {"winner_team": 1, "sets": [[1, 2]] * 6}},  # six sets
    {"format": "doubles", "result": {"sets": [[21, 1]]}},                       # sets, no winner
    {"format": "triples", "date": "2026-10-11"},                                # unknown format
])
def test_invalid_doubles_records_are_400_with_static_bodies_and_save_nothing(
        client, tmp_path, record):
    c, videos, _outputs, store = client
    (videos / "clip.mp4").write_bytes(b"\x00")
    res = c.put("/api/matches/clip", json=record)
    assert res.status_code == 400
    _assert_static_bilingual(res, tmp_path)
    assert not store.exists()


def test_the_same_athlete_on_two_identities_is_400_and_saves_nothing(client, tmp_path):
    c, videos, _outputs, store = client
    (videos / "clip.mp4").write_bytes(b"\x00")
    a = _add(c, "Alice")
    before = store.read_bytes()
    res = c.put("/api/matches/clip", json=_doubles_record(
        {"P1": {"athlete_id": a}, "P3": {"athlete_id": a}}))
    assert res.status_code == 400
    _assert_static_bilingual(res, tmp_path)
    assert store.read_bytes() == before


def test_invalid_players_is_a_distinct_static_bilingual_error(client, tmp_path):
    c, videos, *_ = client
    (videos / "clip.mp4").write_bytes(b"\x00")
    bad_map = c.put("/api/matches/clip", json={"format": "doubles", "players": {"P7": None}})
    bad_record = c.put("/api/matches/clip", json={"format": "doubles", "date": 5})
    assert bad_map.status_code == 400
    _assert_static_bilingual(bad_map, tmp_path)
    assert bad_map.get_json() != bad_record.get_json()
    assert "players" in bad_map.get_json()["error"]
    assert "P7" not in bad_map.get_json()["error"]          # nothing from the request is echoed


def test_doubles_null_and_missing_identities_are_accepted(client):
    c, videos, *_ = client
    (videos / "clip.mp4").write_bytes(b"\x00")
    res = c.put("/api/matches/clip", json={"format": "doubles",
                                           "players": {"P2": {"name": "G"}, "P4": None}})
    assert res.status_code == 200
    assert c.get("/api/matches/clip").get_json() == {
        "date": None, "format": "doubles", "players": {"P2": {"name": "G"}, "P4": None},
        "result": None}


def test_an_all_empty_doubles_record_deletes(client):
    c, videos, *_ = client
    (videos / "clip.mp4").write_bytes(b"\x00")
    c.put("/api/matches/clip", json=_doubles_record({"P1": {"name": "A"}}))
    assert c.put("/api/matches/clip", json={
        "format": "doubles", "date": None, "players": {"P1": None}, "result": None}
    ).status_code == 200
    assert c.get("/api/matches/clip").get_json() == {}


def test_a_doubles_put_replaces_a_singles_record_whole(client):
    c, videos, *_ = client
    (videos / "clip.mp4").write_bytes(b"\x00")
    c.put("/api/matches/clip", json={"upper": {"name": "A"}})
    c.put("/api/matches/clip", json={"format": "doubles", "players": {"P1": {"name": "B"}}})
    body = c.get("/api/matches/clip").get_json()
    assert "upper" not in body and body["players"] == {"P1": {"name": "B"}}


def test_deleting_a_mapped_athlete_rewrites_doubles_identities(client):
    c, videos, *_ = client
    (videos / "clip.mp4").write_bytes(b"\x00")
    (videos / "single.mp4").write_bytes(b"\x00")
    a, b = _add(c, "Alice"), _add(c, "Bob")
    c.put("/api/matches/clip", json=_doubles_record(
        {"P1": {"athlete_id": a}, "P2": {"athlete_id": b}}))
    c.put("/api/matches/single", json={"upper": {"athlete_id": a}})

    res = c.delete(f"/api/athletes/{a}")
    assert res.status_code == 200
    assert res.get_json() == {"ok": True, "rewritten": 2}
    assert c.get("/api/matches/clip").get_json()["players"] == {
        "P1": {"name": "Alice"}, "P2": {"athlete_id": b}}
    assert c.get("/api/matches/single").get_json()["upper"] == {"name": "Alice"}


def test_athlete_match_counts_include_doubles_once_per_match(client):
    c, videos, *_ = client
    for stem in ("d1", "d2", "s1"):
        (videos / f"{stem}.mp4").write_bytes(b"\x00")
    a, b, d = _add(c, "Alice"), _add(c, "Bob"), _add(c, "Dave")
    c.put("/api/matches/d1", json=_doubles_record(
        {"P1": {"athlete_id": a}, "P2": {"athlete_id": b}}))
    c.put("/api/matches/d2", json=_doubles_record({"P4": {"athlete_id": a}}))
    c.put("/api/matches/s1", json={"upper": {"athlete_id": a}, "lower": {"athlete_id": b}})
    counts = {x["id"]: x["matches"] for x in c.get("/api/athletes").get_json()["athletes"]}
    assert counts == {a: 3, b: 2, d: 0}


def test_hostile_doubles_players_on_disk_never_fail_a_request(client):
    c, videos, _outputs, store = client
    (videos / "clip.mp4").write_bytes(b"\x00")
    store.parent.mkdir(parents=True, exist_ok=True)
    store.write_text(__import__("json").dumps({
        "athletes": {"a_1": {"name": "Alice"}},
        "matches": {
            "clip": {"format": "doubles", "date": "2026-10-11", "players": ["x", {"y": 1}]},
            "other": {"format": "doubles", "date": "2026-10-12",
                      "players": {"P1": {"athlete_id": []}, "P2": "z", "P3": {"athlete_id": "a_1"}}},
        }}), encoding="utf-8")
    assert c.get("/api/matches/clip").get_json() == {
        "date": "2026-10-11", "format": "doubles", "players": {}, "result": None}
    body = c.get("/api/athletes").get_json()
    assert body["athletes"][0]["matches"] == 1
    assert c.delete("/api/athletes/a_1").get_json() == {"ok": True, "rewritten": 1}


def test_doubles_put_on_an_unreadable_store_is_refused_without_writing(client, monkeypatch, tmp_path):
    c, videos, _outputs, store = client
    (videos / "clip.mp4").write_bytes(b"\x00")

    def boom(*a, **k):
        raise libathletes.StoreUnreadable(f"locked {store}")

    monkeypatch.setattr(libathletes, "load_for_update", boom)
    res = c.put("/api/matches/clip", json=_doubles_record({"P1": {"name": "A"}}))
    assert res.status_code == 500
    _assert_static_bilingual(res, tmp_path)
    assert not store.exists()


# ------------------------------------------------- split matches (linked parts)

def _split(client, *, parts=("p2",), swapped=False):
    """A singles primary ``p1`` (athlete A upper, winner upper) with linked ``parts``."""
    c, videos, outputs, store = client
    for stem in ("p1", *parts):
        _video(videos, outputs, stem)
    a = _add(c, "A")
    record = {"date": "2026-10-11", "upper": {"athlete_id": a}, "lower": {"name": "R"},
              "result": {"winner": "upper", "sets": [[21, 18], [21, 19]]}}
    assert c.put("/api/matches/p1", json=record).status_code == 200
    for stem in parts:
        res = c.put(f"/api/matches/{stem}", json={"part_of": "p1", "sides_swapped": swapped})
        assert res.status_code == 200, res.get_json()
    return c, videos, outputs, store, a, record


def test_put_link_creates_it_and_returns_the_resolved_view(client):
    c, _videos, _outputs, store, a, record = _split(client, swapped=True)
    res = c.put("/api/matches/p2", json={"part_of": "p1", "sides_swapped": True})
    assert res.status_code == 200
    match = res.get_json()["match"]
    assert match["part_of"] == "p1" and match["sides_swapped"] is True
    assert match["upper"] == {"name": "R"} and match["lower"] == {"athlete_id": a}
    assert match["result"] == {"winner": "lower", "sets": [[21, 18], [21, 19]]}
    assert libathletes.load(store)["matches"]["p2"] == {"part_of": "p1", "sides_swapped": True}


def test_put_link_without_the_swap_flag_defaults_to_false(client):
    c, videos, outputs, store, *_ = _split(client)
    _video(videos, outputs, "p3")
    assert c.put("/api/matches/p3", json={"part_of": "p1"}).status_code == 200
    assert libathletes.load(store)["matches"]["p3"] == {"part_of": "p1", "sides_swapped": False}


def test_get_a_part_returns_the_primarys_record_plus_the_link(client):
    c, *_rest, a, record = _split(client)
    got = c.get("/api/matches/p2").get_json()
    assert got == {**record, "part_of": "p1", "sides_swapped": False}


def test_get_a_primary_with_parts_lists_them_in_stem_order(client):
    c, videos, outputs, store, a, record = _split(client, parts=("p3", "p2"), swapped=True)
    got = c.get("/api/matches/p1").get_json()
    assert got == {**record, "parts": [{"stem": "p2", "sides_swapped": True},
                                       {"stem": "p3", "sides_swapped": True}]}
    assert "part_of" not in got


def test_get_an_ordinary_record_has_neither_link_key(client):
    c, videos, *_ = client
    (videos / "clip.mp4").write_bytes(b"\x00")
    c.put("/api/matches/clip", json={"date": "2026-10-11"})
    assert set(c.get("/api/matches/clip").get_json()) == {"date", "upper", "lower", "result"}


@pytest.mark.parametrize("body,code", [
    ({"part_of": "ghost"}, "invalid_link"),
    ({"part_of": "p3"}, "invalid_link"),                         # itself
    ({"part_of": "p2"}, "invalid_link"),                         # chain: p2 is a part
    ({"part_of": ""}, "invalid_link"),
    ({"part_of": ["p1"]}, "invalid_link"),
    ({"part_of": "p1", "sides_swapped": "yes"}, "invalid_link"),
    ({"part_of": "p1", "date": "2026-10-11"}, "invalid_record"),
])
def test_put_an_invalid_link_is_400_and_changes_nothing(client, tmp_path, body, code):
    c, videos, outputs, store, *_ = _split(client)
    _video(videos, outputs, "p3")
    before = store.read_bytes()
    res = c.put("/api/matches/p3", json=body)
    assert res.status_code == 400
    _assert_static_bilingual(res, tmp_path)
    assert res.get_json() == athletes_routes._MATCH_ERRORS[code]
    assert store.read_bytes() == before


def test_the_link_error_codes_have_distinct_bilingual_bodies(client, tmp_path):
    c, videos, outputs, store, *_ = _split(client)
    _video(videos, outputs, "p3")
    invalid = c.put("/api/matches/p3", json={"part_of": "ghost"})
    c.put("/api/matches/p3", json={"date": "2026-10-12"})
    own = c.put("/api/matches/p3", json={"part_of": "p1"})
    primary = c.put("/api/matches/p1", json=_doubles_record({"P1": {"name": "A"}}))
    for res in (invalid, own, primary):
        assert res.status_code == 400
        _assert_static_bilingual(res, tmp_path)
    assert len({r.get_json()["error"] for r in (invalid, own, primary)}) == 3


def test_linking_over_a_video_with_its_own_record_is_refused(client):
    c, videos, outputs, store, *_ = _split(client)
    _video(videos, outputs, "p3")
    assert c.put("/api/matches/p3", json={"date": "2026-10-12"}).status_code == 200
    res = c.put("/api/matches/p3", json={"part_of": "p1"})
    assert res.status_code == 400
    assert c.get("/api/matches/p3").get_json()["date"] == "2026-10-12"
    # clearing the record first makes it linkable
    assert c.put("/api/matches/p3", json={}).status_code == 200
    assert c.put("/api/matches/p3", json={"part_of": "p1"}).status_code == 200


def test_a_doubles_record_over_a_primary_with_parts_is_refused(client, tmp_path):
    c, _v, _o, store, a, record = _split(client)
    before = store.read_bytes()
    res = c.put("/api/matches/p1", json=_doubles_record({"P1": {"athlete_id": a}}))
    assert res.status_code == 400
    _assert_static_bilingual(res, tmp_path)
    assert store.read_bytes() == before
    assert c.get("/api/matches/p2").get_json()["part_of"] == "p1"


def test_put_empty_unlinks_a_part_and_leaves_the_primary(client):
    c, _v, _o, store, a, record = _split(client)
    assert c.put("/api/matches/p2", json={}).status_code == 200
    assert c.get("/api/matches/p2").get_json() == {}
    assert "parts" not in c.get("/api/matches/p1").get_json()
    assert list(libathletes.load(store)["matches"]) == ["p1"]


def test_a_normal_record_replaces_a_link(client):
    c, *_ = _split(client)
    assert c.put("/api/matches/p2", json={"date": "2026-10-15"}).status_code == 200
    assert "part_of" not in c.get("/api/matches/p2").get_json()
    assert "parts" not in c.get("/api/matches/p1").get_json()


def test_re_putting_a_link_replaces_it(client):
    c, videos, outputs, store, *_ = _split(client)
    assert c.put("/api/matches/p2", json={"part_of": "p1", "sides_swapped": True}
                 ).status_code == 200
    assert libathletes.load(store)["matches"]["p2"]["sides_swapped"] is True
    assert c.get("/api/matches/p1").get_json()["parts"] == [
        {"stem": "p2", "sides_swapped": True}]


def test_clearing_a_primary_unlinks_its_parts(client):
    c, _v, _o, store, *_ = _split(client, parts=("p2", "p3"))
    assert c.put("/api/matches/p1", json={}).status_code == 200
    assert libathletes.load(store)["matches"] == {}


def test_a_link_to_an_unknown_video_stem_in_the_url_is_404(client):
    c, *_ = _split(client)
    assert c.put("/api/matches/nope", json={"part_of": "p1"}).status_code == 404


def test_link_records_do_not_add_to_athlete_match_counts(client):
    c, *_rest, a, _record = _split(client, parts=("p2", "p3"))
    counts = {x["id"]: x["matches"] for x in c.get("/api/athletes").get_json()["athletes"]}
    assert counts[a] == 1


def test_delete_all_of_a_primary_unlinks_its_parts(client):
    c, videos, outputs, store, *_ = _split(client, parts=("p2", "p3"))
    assert c.post("/api/delete/p1", json={"scope": "all"}).status_code == 200
    assert libathletes.load(store)["matches"] == {}
    assert (videos / "p2.mp4").exists()                  # the parts' own videos stay
    assert c.get("/api/matches/p2").get_json() == {}


def test_delete_all_of_a_part_only_removes_its_own_link(client):
    c, videos, outputs, store, *_ = _split(client, parts=("p2", "p3"))
    assert c.post("/api/delete/p2", json={"scope": "all"}).status_code == 200
    assert sorted(libathletes.load(store)["matches"]) == ["p1", "p3"]
    assert c.get("/api/matches/p1").get_json()["parts"] == [
        {"stem": "p3", "sides_swapped": False}]


def test_delete_of_a_primary_removes_dangling_links_without_a_record(client):
    c, videos, outputs, store, *_ = _split(client)
    data = libathletes.load(store)
    del data["matches"]["p1"]                           # a hand-edited store: link only
    libathletes.save(data, store)
    assert c.post("/api/delete/p1", json={"scope": "all"}).status_code == 200
    assert libathletes.load(store)["matches"] == {}


def test_delete_of_results_only_keeps_the_links(client):
    c, videos, outputs, store, *_ = _split(client)
    assert c.post("/api/delete/p1", json={"scope": "match"}).status_code == 200
    assert sorted(libathletes.load(store)["matches"]) == ["p1", "p2"]


def test_delete_all_of_an_unrelated_video_does_not_write_the_store(client, monkeypatch):
    c, videos, outputs, store, *_ = _split(client)
    _video(videos, outputs, "solo")
    saved = []
    monkeypatch.setattr(libathletes, "save", lambda *a, **k: saved.append(a))
    assert c.post("/api/delete/solo", json={"scope": "all"}).status_code == 200
    assert saved == []


def test_delete_of_a_primary_on_a_corrupt_store_is_skipped(client):
    c, videos, outputs, store = client
    for stem in ("p1", "p2"):
        _video(videos, outputs, stem)
    before = _corrupt_store(store)
    assert c.post("/api/delete/p1", json={"scope": "all"}).status_code == 200
    assert store.read_bytes() == before


def test_put_match_scoring_round_trips_and_a_bad_one_is_400(client, tmp_path):
    c, videos, *_ = client
    (videos / "clip.mp4").write_bytes(b"\x00")
    rec = {"date": "2026-10-11", "upper": {"name": "S. Ito"}, "lower": None, "result": None,
           "scoring": {"preset": "one_game_30"}}
    res = c.put("/api/matches/clip", json=rec)
    assert res.status_code == 200
    assert c.get("/api/matches/clip").get_json()["scoring"]["points_to_win"] == 30
    bad = c.put("/api/matches/clip", json={**rec, "scoring": {"preset": "bwf_21x3", "best_of": 2}})
    assert bad.status_code == 400
    _assert_static_bilingual(bad, tmp_path)
    # A result impossible under the format is accepted (the page warns, the store does not refuse).
    odd = {**rec, "result": {"winner": "upper", "sets": [[30, 28]]}}
    assert c.put("/api/matches/clip", json=odd).status_code == 200
