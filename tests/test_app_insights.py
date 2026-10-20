"""Insights routes (badminton_analysis/web/insights_routes.py)."""
import json
import re
import sys
import time
from pathlib import Path

import pytest

import app as webapp

sys.path.insert(0, str(Path(__file__).resolve().parent))
from insights_fixtures import make_run  # noqa: E402

BILINGUAL = re.compile(r"^[^/]*[一-鿿][^/]* / [A-Za-z].*$")
STEM_A = "Dji 20260919100627 0004 D"
STEM_B = "Dji 20260919104815 0005 D"
KPI_KEYS = {"technique_index", "match_win_rate", "avg_rally", "rally_win_rate",
            "recovery", "net_share", "pull_rear", "unforced_errors", "strokes"}


@pytest.fixture
def lib(tmp_path, monkeypatch):
    videos = tmp_path / "videos"
    videos.mkdir()
    outputs = tmp_path / "outputs"
    outputs.mkdir()
    store = tmp_path / "data" / "athletes.json"
    monkeypatch.setattr(webapp, "VIDEOS", videos)
    monkeypatch.setattr(webapp, "OUTPUTS", outputs)
    monkeypatch.setattr(webapp, "ATHLETES_PATH", store)
    monkeypatch.setattr(webapp, "jobs", {})
    webapp.app.config["TESTING"] = True
    return webapp.app.test_client(), tmp_path, videos, outputs, store


def _run(tmp_path, videos, stem):
    out = make_run(
        tmp_path, stem, frames=60,
        rallies=[(0.2, 1.0), (1.2, 1.9)], hits=[(0.4, "upper"), (0.7, "lower")],
        strokes=[("upper", "smash"), ("lower", "clear"), ("upper", "net")],
        reports=[("smash", "upper", 80.0), ("clear", "lower", 75.0)])
    (videos / f"{stem}.mp4").write_bytes(b"\x00")
    return out


def _assert_static_bilingual(res, tmp_path):
    error = res.get_json()["error"]
    assert BILINGUAL.match(error), error
    assert str(tmp_path) not in error
    assert "tmp" not in error.lower()


def test_overview_on_an_empty_library(lib):
    c, *_ = lib
    res = c.get("/api/insights/overview?range=all")
    assert res.status_code == 200
    body = res.get_json()
    assert body["scope"]["stems"] == 0
    assert set(body["kpis"]) == KPI_KEYS


def test_overview_shape_for_two_runs(lib):
    c, tmp_path, videos, *_ = lib
    _run(tmp_path, videos, STEM_A)
    _run(tmp_path, videos, STEM_B)
    res = c.get("/api/insights/overview?range=all")
    assert res.status_code == 200
    body = res.get_json()
    assert body["scope"]["stems"] == 2
    assert set(body["kpis"]) == KPI_KEYS
    assert body["kpis"]["rally_win_rate"]["status"] == "unavailable"
    assert body["kpis"]["unforced_errors"]["status"] == "unavailable"
    assert len(body["radar"]["axes"]) == 7
    assert {m["stem"] for m in body["matches"]} == {STEM_A, STEM_B}


def test_overview_default_range_is_accepted(lib):
    c, *_ = lib
    assert c.get("/api/insights/overview").status_code == 200


def test_overview_unknown_athlete_is_404_bilingual(lib):
    c, tmp_path, *_ = lib
    res = c.get("/api/insights/overview?athlete=nope")
    assert res.status_code == 404
    _assert_static_bilingual(res, tmp_path)


def test_overview_unknown_squad_is_404_bilingual(lib):
    c, tmp_path, *_ = lib
    res = c.get("/api/insights/overview?squad=nope")
    assert res.status_code == 404
    _assert_static_bilingual(res, tmp_path)


def test_overview_bad_range_is_400_bilingual(lib):
    c, tmp_path, *_ = lib
    res = c.get("/api/insights/overview?range=1y")
    assert res.status_code == 400
    _assert_static_bilingual(res, tmp_path)


def test_overview_athlete_filter_selects_only_that_athletes_stems(lib):
    c, tmp_path, videos, *_ = lib
    _run(tmp_path, videos, STEM_A)
    _run(tmp_path, videos, STEM_B)
    athlete = c.post("/api/athletes", json={"name": "J. Tanaka"}).get_json()["id"]
    res = c.put(f"/api/matches/{STEM_A}", json={"upper": {"athlete_id": athlete}})
    assert res.status_code == 200, res.get_json()
    body = c.get(f"/api/insights/overview?range=all&athlete={athlete}").get_json()
    assert body["scope"]["athlete"]["id"] == athlete
    assert body["scope"]["stems"] == 1
    assert [m["stem"] for m in body["matches"]] == [STEM_A]


def test_overview_skips_directories_that_are_not_runs(lib):
    c, tmp_path, videos, outputs, _ = lib
    _run(tmp_path, videos, STEM_A)
    (outputs / "empty_dir").mkdir()
    (outputs / "only_thumb").mkdir()
    (outputs / "only_thumb" / "thumb.jpg").write_bytes(b"\x00")
    (outputs / "stray.txt").write_text("x")
    body = c.get("/api/insights/overview?range=all").get_json()
    assert body["scope"]["stems"] == 1


def test_overview_survives_a_corrupt_run(lib):
    c, tmp_path, videos, outputs, _ = lib
    _run(tmp_path, videos, STEM_A)
    bad = outputs / "broken"
    bad.mkdir()
    (bad / "detections.jsonl").write_text("{not json\n\x00\x00", encoding="utf-8")
    res = c.get("/api/insights/overview?range=all")
    assert res.status_code == 200
    assert res.get_json()["scope"]["stems"] == 2


def test_overview_marks_a_stem_with_a_running_job_as_processing(lib):
    c, tmp_path, videos, *_ = lib
    _run(tmp_path, videos, STEM_A)
    webapp.jobs[STEM_A] = {"status": "running", "video_name": f"{STEM_A}.mp4",
                           "progress": 10, "proc": object()}
    body = c.get("/api/insights/overview?range=all").get_json()
    statuses = {m["status"] for m in body["matches"]}  # one row per side
    assert statuses == {"processing"}


def test_overview_response_has_no_filesystem_path(lib):
    c, tmp_path, videos, *_ = lib
    _run(tmp_path, videos, STEM_A)
    text = c.get("/api/insights/overview?range=all").get_data(as_text=True)
    assert str(tmp_path) not in text
    assert str(tmp_path).replace("\\", "/") not in text
    assert str(tmp_path).replace("\\", "\\\\") not in text


def test_unexpected_failure_is_a_generic_500(lib, monkeypatch):
    c, tmp_path, *_ = lib

    def boom(*a, **k):
        raise RuntimeError(f"secret path {tmp_path}")

    monkeypatch.setattr("badminton_analysis.web.insights_routes.aggregate.overview", boom)
    res = c.get("/api/insights/overview?range=all")
    assert res.status_code == 500
    _assert_static_bilingual(res, tmp_path)
    assert "secret" not in res.get_data(as_text=True)


# ------------------------------------------------------------- per-video route

def test_video_route_works_for_a_stem_with_spaces(lib):
    c, tmp_path, videos, *_ = lib
    _run(tmp_path, videos, STEM_A)
    res = c.get("/api/insights/video/Dji%2020260919100627%200004%20D")
    assert res.status_code == 200
    body = res.get_json()
    assert body["stem"] == STEM_A
    assert body["date"]["value"] == "2026-09-19"
    assert str(tmp_path) not in res.get_data(as_text=True)


def test_video_route_unknown_stem_is_404_bilingual(lib):
    c, tmp_path, *_ = lib
    res = c.get("/api/insights/video/nope")
    assert res.status_code == 404
    _assert_static_bilingual(res, tmp_path)


def test_video_route_rejects_traversal(lib):
    c, tmp_path, *_ = lib
    res = c.get("/api/insights/video/..%2Fsecret")
    assert res.status_code == 404


def test_video_route_caches_the_sidecar_in_the_run_directory(lib):
    c, tmp_path, videos, outputs, _ = lib
    _run(tmp_path, videos, STEM_A)
    assert c.get(f"/api/insights/video/{STEM_A}").status_code == 200
    assert (outputs / STEM_A / "insights.json").is_file()


def test_video_route_corrupt_detections_gives_unavailable_movement(lib):
    c, tmp_path, videos, outputs, _ = lib
    out = outputs / "broken run"
    out.mkdir()
    (out / "detections.jsonl").write_text("{not json\n\x00\x00", encoding="utf-8")
    res = c.get("/api/insights/video/broken%20run")
    assert res.status_code == 200
    assert res.get_json()["movement"]["status"] == "unavailable"


def test_video_route_for_a_video_without_outputs_is_all_unavailable(lib):
    c, tmp_path, videos, *_ = lib
    (videos / "fresh.mp4").write_bytes(b"\x00")
    res = c.get("/api/insights/video/fresh")
    assert res.status_code == 200
    assert res.get_json()["rally"]["status"] == "unavailable"


def test_video_route_matches_video_extension_case_insensitively(lib):
    c, tmp_path, videos, outputs, _ = lib
    out = make_run(tmp_path, "shout", frames=30)
    (videos / "shout.MOV").write_bytes(b"\x00")
    res = c.get("/api/insights/video/shout")
    assert res.status_code == 200
    assert res.get_json()["stem"] == "shout"
    assert out.is_dir()


def test_overview_cached_run_is_fast(lib):
    c, tmp_path, videos, *_ = lib
    for i in range(30):
        _run(tmp_path, videos, f"Dji 202609191000{i:02d} 00{i:02d} D")
    assert c.get("/api/insights/overview?range=all").status_code == 200  # cold
    start = time.perf_counter()
    res = c.get("/api/insights/overview?range=all")
    elapsed = time.perf_counter() - start
    assert res.status_code == 200
    assert res.get_json()["scope"]["stems"] == 30
    assert elapsed < 2.0, elapsed


def test_sidecar_written_once_and_reused(lib):
    c, tmp_path, videos, outputs, _ = lib
    _run(tmp_path, videos, STEM_A)
    c.get("/api/insights/overview?range=all")
    path = outputs / STEM_A / "insights.json"
    first = json.loads(path.read_text(encoding="utf-8"))["computed_at"]
    c.get("/api/insights/overview?range=all")
    assert json.loads(path.read_text(encoding="utf-8"))["computed_at"] == first


def test_overview_and_video_route_agree_on_which_stems_exist(lib):
    c, tmp_path, videos, outputs, _ = lib
    _run(tmp_path, videos, STEM_A)
    for name in (".hidden", "clip..v2"):
        d = outputs / name
        d.mkdir()
        (d / "detections.jsonl").write_text("", encoding="utf-8")
    body = c.get("/api/insights/overview?range=all").get_json()
    assert {m["stem"] for m in body["matches"]} == {STEM_A}
    assert c.get("/api/insights/video/clip..v2").status_code == 404
    assert not (outputs / "clip..v2" / "insights.json").exists()
    assert not (outputs / ".hidden" / "insights.json").exists()


# ------------------------------------------------------------- doubles (addendum 4.2)

DOUBLES_STEM = "Dji 20260920100000 0007 D"
DOUBLES_OTHER = "Dji 20260921100000 0008 D"


def _doubles(tmp_path, videos, stem, **kw):
    from insights_fixtures import doubles_hit, make_doubles_run
    hits = [doubles_hit(5, "P3", "shuttle"), doubles_hit(15, "P3", "single_candidate"),
            doubles_hit(25, "P1", "swing"), doubles_hit(35, None)]
    out = make_doubles_run(tmp_path, stem, hits=hits, **kw)
    (out / "detections.jsonl").write_text("", encoding="utf-8")       # the pipeline writes it
    (videos / f"{stem}.mp4").write_bytes(b"\x00")
    return out


def test_overview_doubles_athlete_scope_end_to_end(lib):
    c, tmp_path, videos, *_ = lib
    _run(tmp_path, videos, STEM_A)
    _doubles(tmp_path, videos, DOUBLES_STEM, names={"P1": "Alice"})
    _doubles(tmp_path, videos, DOUBLES_OTHER, status="degraded")
    athlete = c.post("/api/athletes", json={"name": "J. Tanaka"}).get_json()["id"]
    assert c.put(f"/api/matches/{STEM_A}",
                 json={"upper": {"athlete_id": athlete}}).status_code == 200
    res = c.put(f"/api/matches/{DOUBLES_STEM}",
                json={"format": "doubles", "players": {"P3": {"athlete_id": athlete},
                                                       "P4": {"name": "Dee"}},
                      "result": {"winner_team": 2, "sets": [[18, 21], [21, 19], [15, 21]]}})
    assert res.status_code == 200, res.get_json()
    res = c.get(f"/api/insights/overview?range=all&athlete={athlete}")
    assert res.status_code == 200
    json.dumps(res.get_json(), allow_nan=False)
    body = res.get_json()
    assert body["scope"]["stems"] == 2 and body["scope"]["units"] == 2
    dbl = body["doubles"]
    assert dbl["status"] == "ok" and dbl["value"]["matches"] == 1
    summary = dbl["value"]["athlete"]
    assert (summary["hits"], summary["hits_named"], summary["named_share"]) == (2, 1, 0.5)
    assert summary["distance_m"] == 37.5 and summary["samples"] == 60
    run = dbl["value"]["runs"][0]
    assert {p["id"]: p["athlete"] for p in run["players"]} == {
        "P1": "Alice", "P2": None, "P3": "J. Tanaka", "P4": "Dee"}
    row = [m for m in body["matches"] if m["stem"] == DOUBLES_STEM][0]
    assert (row["athlete_player"], row["team"], row["result"]) == ("P3", 2, "W")
    assert row["sets"] == [[21, 18], [19, 21], [21, 15]]
    assert row["partner"] == "Dee" and row["opponent"] == "Alice"
    assert body["movement"]["position"]["status"] in ("ok", "partial")
    codes = {f["code"] for f in body["findings"]}
    assert "doubles_hits_unvalidated" in codes


def test_overview_doubles_no_scope_lists_both_runs(lib):
    c, tmp_path, videos, *_ = lib
    _doubles(tmp_path, videos, DOUBLES_STEM)
    _doubles(tmp_path, videos, DOUBLES_OTHER, status="degraded")
    body = c.get("/api/insights/overview?range=all").get_json()
    dbl = body["doubles"]
    assert dbl["status"] == "partial" and dbl["reason"]["code"] == "doubles_degraded"
    assert [r["stem"] for r in dbl["value"]["runs"]] == [DOUBLES_OTHER, DOUBLES_STEM]
    assert dbl["value"]["athlete"] is None
    assert body["scope"]["units"] == 0
    assert {f["code"] for f in body["findings"]} == {"doubles_identity_degraded"}
    text = json.dumps(body)
    assert str(tmp_path).replace("\\", "/") not in text and str(tmp_path) not in text
