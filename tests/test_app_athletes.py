"""Athlete, squad and match-record routes (badminton_analysis/web/athletes_routes.py)."""
import re
import threading
import time

import pytest

import app as webapp
from badminton_analysis.library import athletes as libathletes

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
