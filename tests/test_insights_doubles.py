"""Kestrel doubles support, task 2: the ``doubles`` insight (doubles addendum 4.1)."""
import json
import time

import pytest

from badminton_analysis.insights import sidecar
from badminton_analysis.insights.doubles import doubles_insight
from badminton_analysis.insights.loaders import load_run
from badminton_analysis.insights.movement import cell
from tests.insights_fixtures import (DOUBLES_CELLS, doubles_hit, doubles_player,
                                     doubles_tracklet, make_doubles_run, make_run)

ROW_KEYS = {"id", "team", "name", "coverage", "frames", "distance_m", "avg_speed_mps",
            "max_speed_mps", "hits", "hits_named", "samples", "grid"}


def _insight(out):
    return doubles_insight(load_run(out))


def _rows(env):
    return {p["id"]: p for p in env["value"]["players"]}


def _grid_total(grid):
    return sum(sum(r) for r in grid)


def _strict(obj):
    return json.dumps(obj, allow_nan=False)


# ---- loaders ---------------------------------------------------------------------

def test_loader_reads_the_doubles_files(tmp_path):
    out = make_doubles_run(tmp_path, hits=[doubles_hit(10, "P1")])
    run = load_run(out)
    assert run.players_doc["status"] == "ok"
    assert {t["id"] for t in run.tracklets} == {1, 2, 3, 4}
    assert len(run.tracklets[0]["points"]) == 60
    assert run.tracklets[0]["points"][0] == (0, 1.0, 8.0)
    assert run.doubles_hits == [{"hitter_id": "P1", "hitter_team": 1,
                                 "hitter_evidence": "shuttle"}]


def test_loader_leaves_doubles_fields_none_on_singles_runs(tmp_path):
    out = make_run(tmp_path, "solo", frames=10, hits=[(1.0, "upper")])
    (out / "players.json").write_text('{"status": "ok", "players": []}', encoding="utf-8")
    run = load_run(out)
    assert run.players_doc is None and run.tracklets is None and run.doubles_hits is None


def test_singles_run_is_not_doubles(tmp_path):
    env = _insight(make_run(tmp_path, "solo", frames=10))
    assert env == {"status": "unavailable", "value": None, "coverage": None,
                   "reason": {"code": "not_doubles", "params": {}}}


# ---- ok run ----------------------------------------------------------------------

def test_ok_run_copies_player_rows_and_builds_one_cell_grids(tmp_path):
    out = make_doubles_run(tmp_path, names={"P1": "Alice"})
    env = _insight(out)
    assert env["status"] == "ok" and env["reason"] is None
    value = env["value"]
    assert value["status"] == "ok" and value["validated"] is False
    assert [p["id"] for p in value["players"]] == ["P1", "P2", "P3", "P4"]
    rows = _rows(env)
    for pid, row in rows.items():
        assert set(row) == ROW_KEYS
        assert row["samples"] == 60
        r, c = DOUBLES_CELLS[pid]
        assert len(row["grid"]) == 11 and all(len(g) == 10 for g in row["grid"])
        assert row["grid"][r][c] == 1.0 and _grid_total(row["grid"]) == 1.0
    p1 = rows["P1"]
    assert p1["name"] == "Alice" and rows["P2"]["name"] is None
    assert (p1["team"], p1["frames"], p1["coverage"]) == (1, 60, 0.8)
    assert (p1["distance_m"], p1["avg_speed_mps"], p1["max_speed_mps"]) == (12.5, 1.2, 4.5)
    assert rows["P4"]["distance_m"] == 50.0 and rows["P3"]["team"] == 2
    assert value["teams"] == [{"id": 1, "players": ["P1", "P2"], "hits": 0, "hits_named": 0},
                              {"id": 2, "players": ["P3", "P4"], "hits": 0, "hits_named": 0}]
    assert value["identity_video"] == {"status": "ok", "file": "detect_doubles_players.mp4"}
    _strict(env)


def test_tracklet_ids_map_to_identities_and_unmapped_tracklets_are_ignored(tmp_path):
    players = [doubles_player("P1", tracklet_ids=[7, 8]), doubles_player("P2", tracklet_ids=[9])]
    tracklets = [doubles_tracklet(7, [(f, 1.0, 8.0) for f in range(20)]),
                 doubles_tracklet(8, [(f, 1.0, 8.0) for f in range(20, 40)]),
                 doubles_tracklet(9, [(f, 5.0, 12.0) for f in range(40)]),
                 doubles_tracklet(99, [(f, 3.0, 9.0) for f in range(100)])]
    rows = _rows(_insight(make_doubles_run(tmp_path, players=players, tracklets=tracklets)))
    assert set(rows) == {"P1", "P2"}
    assert rows["P1"]["samples"] == 40 and rows["P1"]["grid"][2][1] == 1.0
    assert rows["P2"]["samples"] == 40 and rows["P2"]["grid"][8][8] == 1.0


def test_under_30_samples_has_no_grid(tmp_path):
    out = make_doubles_run(tmp_path, frames=29)
    for row in _rows(_insight(out)).values():
        assert row["samples"] == 29 and row["grid"] is None


def test_each_point_uses_its_own_side(tmp_path):
    # one tracklet: 30 points on the upper half, then 30 on the lower half
    points = [(f, 2.0, 3.0) for f in range(30)] + [(f, 2.0, 10.0) for f in range(30, 60)]
    players = [doubles_player("P1", tracklet_ids=[1])]
    out = make_doubles_run(tmp_path, players=players,
                           tracklets=[doubles_tracklet(1, points, side="upper")])
    row = _rows(_insight(out))["P1"]
    up, low = cell("upper", 2.0, 3.0), cell("lower", 2.0, 10.0)
    assert up != low
    assert row["samples"] == 60
    assert row["grid"][up[0]][up[1]] == 0.5 and row["grid"][low[0]][low[1]] == 0.5


def test_points_far_outside_are_dropped_and_near_ones_clamped(tmp_path):
    points = ([(f, -0.5, 8.0) for f in range(30)]          # 0.5 m outside: clamped to col 0
              + [(f, 9.0, 8.0) for f in range(30, 60)])    # 2.9 m outside: dropped
    players = [doubles_player("P1", tracklet_ids=[1])]
    row = _rows(_insight(make_doubles_run(tmp_path, players=players,
                                          tracklets=[doubles_tracklet(1, points)])))["P1"]
    assert row["samples"] == 30 and row["grid"][2][0] == 1.0


def test_a_frame_seen_twice_counts_once(tmp_path):
    players = [doubles_player("P1", tracklet_ids=[1, 2])]
    tracklets = [doubles_tracklet(1, [(f, 1.0, 8.0) for f in range(40)]),
                 doubles_tracklet(2, [(f, 5.0, 12.0) for f in range(40)])]
    row = _rows(_insight(make_doubles_run(tmp_path, players=players, tracklets=tracklets)))["P1"]
    assert row["samples"] == 40 and row["grid"][2][1] == 1.0


def test_missing_tracklets_file_gives_null_grids(tmp_path):
    env = _insight(make_doubles_run(tmp_path, tracklets=None))
    assert env["status"] == "ok"
    assert all(r["grid"] is None and r["samples"] == 0 for r in _rows(env).values())


# ---- hits ------------------------------------------------------------------------

def test_named_rule_and_per_player_and_team_counts(tmp_path):
    hits = [doubles_hit(1, "P1", "shuttle"), doubles_hit(2, "P1", "swing"),
            doubles_hit(3, "P1", "single_candidate"), doubles_hit(4, "P2", "shuttle"),
            doubles_hit(5, "P3", "swing"), doubles_hit(6, "P4", "single_candidate"),
            doubles_hit(7, None), doubles_hit(8, None)]
    env = _insight(make_doubles_run(tmp_path, hits=hits))
    rows = _rows(env)
    assert (rows["P1"]["hits"], rows["P1"]["hits_named"]) == (3, 2)
    assert (rows["P2"]["hits"], rows["P2"]["hits_named"]) == (1, 1)
    assert (rows["P3"]["hits"], rows["P3"]["hits_named"]) == (1, 1)
    assert (rows["P4"]["hits"], rows["P4"]["hits_named"]) == (1, 0)
    teams = {t["id"]: t for t in env["value"]["teams"]}
    assert (teams[1]["hits"], teams[1]["hits_named"]) == (4, 3)
    assert (teams[2]["hits"], teams[2]["hits_named"]) == (2, 1)
    assert env["value"]["hits"] == {"total": 8, "named": 4, "named_share": 0.5,
                                    "single_candidate": 2}


def test_zero_hits_has_null_named_share(tmp_path):
    env = _insight(make_doubles_run(tmp_path, hits=[]))
    assert env["value"]["hits"] == {"total": 0, "named": 0, "named_share": None,
                                    "single_candidate": 0}


def test_evidence_none_with_an_id_is_counted_but_not_named(tmp_path):
    hit = dict(doubles_hit(1, "P1"), hitter_evidence="none")
    env = _insight(make_doubles_run(tmp_path, hits=[hit]))
    assert (_rows(env)["P1"]["hits"], _rows(env)["P1"]["hits_named"]) == (1, 0)
    assert env["value"]["hits"]["named"] == 0 and env["value"]["hits"]["total"] == 1


def test_without_hits_json_player_counts_come_from_players_json(tmp_path):
    players = [doubles_player("P1", hits=5, hits_named=3, hits_single_candidate=1),
               doubles_player("P3", hits=2, hits_named=2)]
    env = _insight(make_doubles_run(tmp_path, players=players, hits=None))
    rows = _rows(env)
    assert (rows["P1"]["hits"], rows["P1"]["hits_named"]) == (5, 3)
    assert {t["id"]: (t["hits"], t["hits_named"]) for t in env["value"]["teams"]} == {
        1: (5, 3), 2: (2, 2)}
    assert env["value"]["hits"] == {"total": 7, "named": 5, "named_share": round(5 / 7, 4),
                                    "single_candidate": 1}


@pytest.mark.parametrize("bad", [5, 1.0, True, {"P": 1}, ["P1"], "", None])
def test_non_string_hitter_id_counts_only_in_total(tmp_path, bad):
    hits = [dict(doubles_hit(1, "P1"), hitter_id=bad, hitter_team=1), doubles_hit(2, "P1")]
    env = _insight(make_doubles_run(tmp_path, hits=hits))
    assert _rows(env)["P1"]["hits"] == 1
    assert env["value"]["hits"]["total"] == 2 and env["value"]["hits"]["named"] == 1
    _strict(env)


@pytest.mark.parametrize("bad", ["1", 3, True, 1.5, [1]])
def test_bad_hitter_team_is_not_counted_for_a_team(tmp_path, bad):
    hits = [dict(doubles_hit(1, "P1"), hitter_team=bad)]
    env = _insight(make_doubles_run(tmp_path, hits=hits))
    assert all(t["hits"] == 0 for t in env["value"]["teams"])
    assert _rows(env)["P1"]["hits"] == 1


# ---- identity status ----------------------------------------------------------------

@pytest.mark.parametrize("status", ["degraded", "degraded_teams"])
def test_degraded_is_partial_with_the_status(tmp_path, status):
    players = [doubles_player(p, team=None) for p in ("P1", "P2", "P3", "P4")]
    env = _insight(make_doubles_run(tmp_path, status=status, players=players,
                                    reason="only 3 identities"))
    assert env["status"] == "partial"
    assert env["reason"] == {"code": "doubles_degraded", "params": {"status": status}}
    assert env["value"]["status"] == status
    assert env["value"]["teams"] is None
    assert all(r["team"] is None for r in _rows(env).values())


def test_error_is_unavailable_and_never_echoes_the_reason(tmp_path):
    out = make_doubles_run(tmp_path, status="error", players=[],
                           reason="ValueError: secret C:/Users/x/path")
    env = _insight(out)
    assert env == {"status": "unavailable", "value": None, "coverage": None,
                   "reason": {"code": "doubles_error", "params": {}}}
    assert "secret" not in _strict(env)


def test_error_with_players_is_still_unavailable(tmp_path):
    env = _insight(make_doubles_run(tmp_path, status="error"))
    assert env["status"] == "unavailable" and env["reason"]["code"] == "doubles_error"


@pytest.mark.parametrize("status", ["weird", None, 3, ["ok"], "OK"])
def test_unknown_status_is_never_ok(tmp_path, status):
    out = make_doubles_run(tmp_path)
    doc = json.loads((out / "players.json").read_text(encoding="utf-8"))
    doc["status"] = status
    (out / "players.json").write_text(json.dumps(doc), encoding="utf-8")
    env = _insight(out)
    assert env["status"] == "partial"
    assert env["reason"] == {"code": "doubles_degraded", "params": {"status": "degraded"}}
    assert env["value"]["status"] == "degraded"


def test_missing_players_json_is_no_players(tmp_path):
    env = _insight(make_doubles_run(tmp_path, status=None))
    assert env["status"] == "unavailable" and env["reason"]["code"] == "no_players"


@pytest.mark.parametrize("content", [
    "{not json", "[1, 2]", "null", "", '{"status": "ok"}', '{"status": "ok", "players": []}',
    '{"status": "ok", "players": {"P1": {}}}', '{"status": "ok", "players": "P1"}',
    '{"status": "ok", "players": [1, "P1", null, {"id": 5}, {"id": "P9"}, {"id": ["P1"]}]}',
    pytest.param("[" * 100000 + "]" * 100000, id="deeply-nested")])
def test_malformed_players_json_is_no_players(tmp_path, content):
    out = make_doubles_run(tmp_path)
    (out / "players.json").write_text(content, encoding="utf-8")
    env = _insight(out)
    assert env["status"] == "unavailable" and env["reason"]["code"] == "no_players"


# ---- identity video ------------------------------------------------------------------

@pytest.mark.parametrize("video", [
    None, {"status": "failed", "file": None, "reason": "render failed"},
    {"status": "skipped", "file": "detect_doubles_players.mp4"},
    {"status": "ok", "file": None}, {"status": "ok", "file": 5},
    {"status": "ok", "file": "../../secret.mp4"}, {"status": "ok", "file": "a/b_players.mp4"},
    {"status": "ok", "file": "C:\\x\\detect_doubles_players.mp4"},
    {"status": "ok", "file": "detect_other.mp4"}, "ok", ["ok"]])
def test_identity_video_only_when_ok_with_a_plain_file_name(tmp_path, video):
    env = _insight(make_doubles_run(tmp_path, identity_video=video))
    assert env["value"]["identity_video"] is None


# ---- hostile inputs ------------------------------------------------------------------

@pytest.mark.parametrize("bad", ["NaN", "Infinity", "-Infinity", "1e308", "-5", "true", '"12"',
                                 "[1]", "{}", "null"])
def test_hostile_numbers_are_ignored(tmp_path, bad):
    out = make_doubles_run(tmp_path)
    text = (out / "players.json").read_text(encoding="utf-8")
    for key in ("distance_m", "avg_speed_mps", "max_speed_mps", "coverage", "frames", "hits",
                "hits_named"):
        text = text.replace(f'"{key}": ', f'"{key}": {bad}, "_{key}": ')
    (out / "players.json").write_text(text, encoding="utf-8")
    env = _insight(out)
    assert env["status"] == "ok"
    for row in _rows(env).values():
        for key in ("distance_m", "avg_speed_mps", "max_speed_mps", "coverage", "frames"):
            assert row[key] is None, (key, row[key])
    _strict(env)


def test_coverage_above_one_and_absurd_speeds_are_ignored(tmp_path):
    players = [doubles_player("P1", coverage=1.7, max_speed_mps=900.0, avg_speed_mps=-1.0,
                              distance_m=5e6, frames=2.5)]
    row = _rows(_insight(make_doubles_run(tmp_path, players=players)))["P1"]
    assert (row["coverage"], row["max_speed_mps"], row["avg_speed_mps"],
            row["distance_m"], row["frames"]) == (None, None, None, None, None)


def test_duplicate_ids_keep_the_first_and_odd_teams_are_null(tmp_path):
    players = [doubles_player("P1", distance_m=1.0), doubles_player("P1", distance_m=2.0),
               doubles_player("P2", team=3), doubles_player("P3", team=True),
               doubles_player("P4", team="2"), {"id": "P5"}, "P2", None]
    env = _insight(make_doubles_run(tmp_path, players=players, teams=[]))
    rows = _rows(env)
    assert [p["id"] for p in env["value"]["players"]] == ["P1", "P2", "P3", "P4"]
    assert rows["P1"]["distance_m"] == 1.0
    assert rows["P2"]["team"] is None and rows["P3"]["team"] is None
    assert rows["P4"]["team"] is None
    assert env["value"]["teams"] == [{"id": 1, "players": ["P1"], "hits": 0, "hits_named": 0}]


@pytest.mark.parametrize("name", [5, ["Alice"], {"n": 1}, "", "   ", True])
def test_non_string_or_blank_names_are_null(tmp_path, name):
    row = _rows(_insight(make_doubles_run(tmp_path,
                                          players=[doubles_player("P1", name=name)])))["P1"]
    assert row["name"] is None


def test_long_names_are_truncated(tmp_path):
    row = _rows(_insight(make_doubles_run(
        tmp_path, players=[doubles_player("P1", name="A" * 5000)])))["P1"]
    assert 0 < len(row["name"]) <= 120


@pytest.mark.parametrize("teams", [
    [{"id": 1, "players": ["P1", "P9", 5, ["P2"]]}, {"id": True, "players": ["P3"]},
     {"id": 2, "players": "P3"}, "x", None], "nope", None, 5])
def test_teams_come_from_the_player_rows_not_the_teams_block(tmp_path, teams):
    # the pipeline derives `teams` from the same identity->team map as the rows' `team`
    env = _insight(make_doubles_run(tmp_path, teams=teams))
    assert env["value"]["teams"] == [{"id": 1, "players": ["P1", "P2"], "hits": 0, "hits_named": 0},
                                     {"id": 2, "players": ["P3", "P4"], "hits": 0, "hits_named": 0}]


def test_hostile_tracklets_are_skipped(tmp_path):
    players = [doubles_player("P1", tracklet_ids=[1, [2], {"a": 1}, None, True, 3, 4, 5, 6]),
               {"id": "P2", "team": 2, "tracklets": "nope"},
               {"id": "P3", "team": 2, "tracklets": [{"id": 1}, 7, None]}]
    good = [(f, 1.0, 8.0) for f in range(40)]
    lines = [doubles_tracklet(1, good),
             dict(doubles_tracklet(3, good), court=[[1.0, 8.0]] * 39),       # length mismatch
             dict(doubles_tracklet(4, good), frames="0-39"),
             dict(doubles_tracklet(5, good), court=[["x", 8.0], [float("nan"), 1], None, [1.0]]
                  + [[1.0, 8.0]] * 36),
             dict(doubles_tracklet(6, good), id=[6]),
             [1, 2], "x", None, 5]
    out = make_doubles_run(tmp_path, players=players, tracklets=lines)
    with open(out / "tracklets.jsonl", "a", encoding="utf-8") as f:
        f.write("{not json\n" + "[" * 50000 + "]" * 50000 + "\n")
    env = _insight(out)
    rows = _rows(env)
    assert rows["P1"]["samples"] == 40           # tracklet 5's 36 good points are frames 4..39
    assert rows["P1"]["grid"][2][1] == 1.0
    assert rows["P2"]["samples"] == 0 and rows["P3"]["samples"] == 0   # 1 belongs to P1
    _strict(env)


def test_tracklet_claimed_by_two_players_goes_to_the_first(tmp_path):
    players = [doubles_player("P1", tracklet_ids=[1]), doubles_player("P2", tracklet_ids=[1])]
    rows = _rows(_insight(make_doubles_run(tmp_path, players=players)))
    assert rows["P1"]["samples"] == 60 and rows["P2"]["samples"] == 0


def test_hostile_hits_json_never_raises(tmp_path):
    hits = [doubles_hit(1, "P1"), "x", None, 5, [1], {"hitter_id": "P1"},
            {"hitter_id": "P2", "hitter_evidence": ["shuttle"]},
            {"hitter_id": "P2", "hitter_evidence": {"a": 1}, "hitter_team": {"t": 1}}]
    env = _insight(make_doubles_run(tmp_path, hits=hits))
    rows = _rows(env)
    assert rows["P1"]["hits"] == 2 and rows["P1"]["hits_named"] == 1
    assert rows["P2"]["hits"] == 2 and rows["P2"]["hits_named"] == 0
    assert env["value"]["hits"]["total"] == 4      # the 4 objects; other entries are no hits
    _strict(env)


@pytest.mark.parametrize("hits_list", ['"x"', "5", "null", "{}"])
def test_hits_json_without_a_list_falls_back(tmp_path, hits_list):
    out = make_doubles_run(tmp_path, players=[doubles_player("P1", hits=2, hits_named=1)])
    (out / "hits.json").write_text('{"hits": %s}' % hits_list, encoding="utf-8")
    env = _insight(out)
    assert _rows(env)["P1"]["hits"] == 2 and env["value"]["hits"]["total"] == 2


def test_200k_point_tracklet_is_fast(tmp_path):
    n = 200_000
    players = [doubles_player("P1", tracklet_ids=[1])]
    tracklets = [doubles_tracklet(1, [(f, 1.0 + (f % 50) * 0.1, 7.0 + (f % 60) * 0.1)
                                      for f in range(n)])]
    out = make_doubles_run(tmp_path, players=players, tracklets=tracklets, frames=n)
    start = time.perf_counter()
    doc = sidecar.compute_video(out.name, out)
    elapsed = time.perf_counter() - start
    assert elapsed < 3.0, elapsed
    row = doc["doubles"]["value"]["players"][0]
    assert row["samples"] == n and abs(_grid_total(row["grid"]) - 1.0) < 0.01
    _strict(doc)


def test_whole_hostile_sidecar_is_strict_json(tmp_path):
    out = make_doubles_run(tmp_path, hits=[doubles_hit(1, "P1")])
    text = (out / "players.json").read_text(encoding="utf-8").replace(
        '"distance_m": 12.5', '"distance_m": NaN')
    (out / "players.json").write_text(text, encoding="utf-8")
    with open(out / "tracklets.jsonl", "a", encoding="utf-8") as f:
        f.write('{"id": 1, "frames": [1, 2], "court": [[NaN, 1], [Infinity, 2]]}\n')
    doc = sidecar.compute_video(out.name, out)
    assert doc["doubles"]["status"] == "ok"
    _strict(doc)
    assert json.loads((out / "insights.json").read_text(encoding="utf-8")) == doc
