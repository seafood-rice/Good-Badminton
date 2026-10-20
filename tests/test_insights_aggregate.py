"""Task 5: overview aggregation and findings (spec 5.4)."""
import pytest

from badminton_analysis.insights import aggregate, findings
from badminton_analysis.library import athletes as lib
from tests.insights_fixtures import TECHS, unit_doc


def _store(*, squad=True):
    data = lib.empty()
    sq = None
    if squad:
        data, sq = lib.add_squad(data, "Seniors")
    data, tanaka = lib.add_athlete(data, "J. Tanaka", sq)
    data, ito = lib.add_athlete(data, "S. Ito", sq)
    data, other = lib.add_athlete(data, "K. Outsider", None)
    return data, {"squad": sq, "tanaka": tanaka, "ito": ito, "other": other}


def _match(data, stem, upper=None, lower=None, date=None, winner=None, sets=None):
    def side(s):
        if s is None:
            return None
        return {"athlete_id": s} if s.startswith("a_") else {"name": s}

    result = {"winner": winner, "sets": sets or []} if winner else None
    rec, err = lib.validate_match({"date": date, "upper": side(upper), "lower": side(lower),
                                   "result": result}, data)
    assert err is None, err
    return lib.set_match(data, stem, rec)


def _unit(stem, date="2026-10-10", **kw):
    return {"stem": stem, "insights": unit_doc(stem, date=date, **kw)}


def _axes(overview):
    return {a["id"]: a for a in overview["radar"]["axes"]}


# ---- scope, sides, errors ------------------------------------------------------

def test_athlete_scope_uses_each_stems_own_side():
    data, ids = _store()
    data = _match(data, "A", upper=ids["tanaka"], lower="X. Rival")
    data = _match(data, "B", upper="Y. Rival", lower=ids["tanaka"])
    units = [_unit("A", upper={"smash": (1, 90.0)}, lower={"smash": (1, 10.0)}),
             _unit("B", upper={"smash": (1, 20.0)}, lower={"smash": (1, 70.0)})]
    ov = aggregate.overview(units, data, athlete=ids["tanaka"], range_="all")
    assert ov["scope"]["stems"] == 2 and ov["scope"]["units"] == 2
    assert _axes(ov)["smash"]["score"] == 80.0       # (90 on upper in A + 70 on lower in B) / 2
    assert {(m["stem"], m["side"]) for m in ov["matches"]} == {("A", "upper"), ("B", "lower")}


def test_athlete_not_in_a_stem_excludes_it():
    data, ids = _store()
    data = _match(data, "A", upper=ids["tanaka"])
    data = _match(data, "B", upper=ids["ito"])
    units = [_unit("A", upper={"smash": (1, 50.0)}), _unit("B", upper={"smash": (1, 90.0)}),
             _unit("C", upper={"smash": (1, 90.0)})]
    ov = aggregate.overview(units, data, athlete=ids["tanaka"], range_="all")
    assert ov["scope"]["stems"] == 1 and _axes(ov)["smash"]["score"] == 50.0


def test_no_scope_counts_both_sides_of_every_stem():
    data, _ = _store()
    units = [_unit("A", upper={"smash": (1, 60.0)}, lower={"smash": (1, 80.0)})]
    ov = aggregate.overview(units, data, range_="all")
    assert (ov["scope"]["stems"], ov["scope"]["units"]) == (1, 2)
    assert _axes(ov)["smash"]["score"] == 70.0
    assert ov["scope"]["athlete"] is None and ov["scope"]["squad"] is None
    assert [m["result"] for m in ov["matches"]] == [None, None]


def test_squad_scope_counts_only_squad_sides():
    data, ids = _store()
    data = _match(data, "A", upper=ids["tanaka"], lower=ids["other"])
    data = _match(data, "B", upper="Free Text", lower="Free Text 2")
    units = [_unit("A", upper={"smash": (1, 60.0)}, lower={"smash": (1, 10.0)}),
             _unit("B", upper={"smash": (1, 10.0)}, lower={"smash": (1, 10.0)})]
    ov = aggregate.overview(units, data, squad=ids["squad"], range_="all")
    assert (ov["scope"]["stems"], ov["scope"]["units"]) == (1, 1)
    assert _axes(ov)["smash"]["score"] == 60.0
    assert ov["scope"]["squad"] == {"id": ids["squad"], "name": "Seniors"}


def test_scope_describes_the_athlete():
    data, ids = _store()
    ov = aggregate.overview([], data, athlete=ids["tanaka"])
    assert ov["scope"]["athlete"] == {"id": ids["tanaka"], "name": "J. Tanaka",
                                      "squad_id": ids["squad"]}


def test_unknown_ids_and_bad_range():
    data, ids = _store()
    with pytest.raises(LookupError, match="athlete"):
        aggregate.overview([], data, athlete="a_nope")
    with pytest.raises(LookupError, match="squad"):
        aggregate.overview([], data, squad="s_nope")
    with pytest.raises(ValueError, match="range"):
        aggregate.overview([], data, range_="x")
    with pytest.raises(LookupError):
        aggregate.overview([], data, athlete=["unhashable"])


# ---- ranges --------------------------------------------------------------------

def _dated(*dates):
    return [_unit(f"S{i}", date=d, upper={"smash": (1, 50.0)}) for i, d in enumerate(dates)]


def test_range_7d_anchors_on_latest_selected_date():
    data, _ = _store()
    units = _dated("2026-03-01", "2026-03-05", "2026-03-07", "2026-02-28")
    ov = aggregate.overview(units, data, range_="7d")
    assert ov["scope"]["date_to"] == "2026-03-07" and ov["scope"]["date_from"] == "2026-03-01"
    assert sorted({m["stem"] for m in ov["matches"]}) == ["S0", "S1", "S2"]


def test_range_30d_and_season_and_all():
    data, _ = _store()
    units = _dated("2026-10-31", "2026-10-02", "2026-10-01", "2026-01-01", "2025-12-31")
    stems = lambda ov: {m["stem"] for m in ov["matches"]}          # noqa: E731
    assert stems(aggregate.overview(units, data, range_="30d")) == {"S0", "S1"}
    assert aggregate.overview(units, data, range_="30d")["scope"]["date_from"] == "2026-10-02"
    season = aggregate.overview(units, data, range_="season")
    assert stems(season) == {"S0", "S1", "S2", "S3"}
    assert season["scope"]["date_from"] == "2026-01-01"
    assert stems(aggregate.overview(units, data, range_="all")) == {f"S{i}" for i in range(5)}


def test_default_range_is_30d():
    data, _ = _store()
    ov = aggregate.overview(_dated("2026-10-31", "2026-08-01"), data)
    assert ov["scope"]["range"] == "30d" and ov["scope"]["stems"] == 1


def test_record_date_overrides_sidecar_date():
    data, ids = _store()
    data = _match(data, "S0", upper=ids["tanaka"], date="2026-09-01")
    units = _dated("2026-10-10")
    ov = aggregate.overview(units, data, range_="all")
    row = ov["matches"][0]
    assert (row["date"], row["date_source"]) == ("2026-09-01", "record")


def test_undated_unit_only_counts_for_all():
    data, _ = _store()
    units = _dated("2026-10-10") + [_unit("U", date=None, upper={"smash": (1, 50.0)})]
    assert aggregate.overview(units, data, range_="30d")["scope"]["stems"] == 1
    ov = aggregate.overview(units, data, range_="all")
    assert ov["scope"]["stems"] == 2
    assert [p["stem"] for p in ov["trend"]["points"]] == ["S0"]       # no date, no point
    undated = [m for m in ov["matches"] if m["stem"] == "U"][0]
    assert undated["date"] is None and undated["date_source"] is None


# ---- radar, index, trend ---------------------------------------------------------

def test_radar_axis_is_count_weighted_mean():
    data, _ = _store()
    units = [_unit("A", upper={"smash": (1, 60.0)}), _unit("B", upper={"smash": (3, 80.0)})]
    ov = aggregate.overview(units, data, range_="all")
    smash = _axes(ov)["smash"]
    assert smash == {"id": "smash", "count": 4, "score": 75.0, "status": "ok"}
    assert [a["id"] for a in ov["radar"]["axes"]] == TECHS
    assert _axes(ov)["net"] == {"id": "net", "count": 0, "score": None,
                                "status": "unavailable"}
    assert ov["radar"]["reference"] == {"lo": 70, "hi": 100, "source": "literature"}


def test_index_is_mean_of_scored_axes():
    data, _ = _store()
    units = [_unit("A", upper={"smash": (2, 60.0), "clear": (1, 80.0), "drop": (1, 70.0)})]
    ov = aggregate.overview(units, data, range_="all")
    ti = ov["kpis"]["technique_index"]
    assert ti["status"] == "partial" and ti["reason"]["code"] == "unscored_technique"
    assert ti["reason"]["params"]["names"] == ["net", "lift", "drive", "serve"]
    assert ti["value"]["value"] == 70.0 and ti["value"]["scored"] == 3
    assert ti["coverage"] == {"n": 3, "of": 7}


def test_index_ok_when_all_seven_scored():
    data, _ = _store()
    units = [_unit("A", upper={t: (1, 70.0) for t in TECHS})]
    ti = aggregate.overview(units, data, range_="all")["kpis"]["technique_index"]
    assert ti["status"] == "ok" and ti["value"]["scored"] == 7


def test_trend_sorted_by_date_and_delta():
    data, _ = _store()
    one = aggregate.overview([_unit("A", upper={"smash": (1, 60.0)})], data, range_="all")
    assert one["kpis"]["technique_index"]["value"]["delta"] is None
    units = [_unit("Z", date="2026-10-12", upper={"smash": (1, 80.0)}),
             _unit("M", date="2026-10-03", upper={"smash": (1, 60.0)}),
             _unit("Q", date="2026-10-08", upper={"smash": (1, 90.0)})]
    ov = aggregate.overview(units, data, range_="all")
    assert [p["date"] for p in ov["trend"]["points"]] == ["2026-10-03", "2026-10-08",
                                                           "2026-10-12"]
    assert ov["trend"]["points"][0] == {"date": "2026-10-03", "stem": "M", "index": 60.0}
    assert ov["kpis"]["technique_index"]["value"]["delta"] == 20.0     # 80 - 60
    two = aggregate.overview([units[0], units[2]], data, range_="all")
    assert two["kpis"]["technique_index"]["value"]["delta"] == -10.0   # 80 (10-12) - 90 (10-08)


def test_stem_without_scores_has_no_trend_point():
    data, _ = _store()
    units = [_unit("A", upper={"smash": (1, 60.0)}), _unit("B")]
    ov = aggregate.overview(units, data, range_="all")
    assert [p["stem"] for p in ov["trend"]["points"]] == ["A"]


# ---- match win rate, point-outcome kpis --------------------------------------------

def test_match_win_rate_two_of_three():
    data, ids = _store()
    t = ids["tanaka"]
    data = _match(data, "A", upper=t, lower="R", winner="upper", sets=[[21, 10], [21, 15]])
    data = _match(data, "B", upper="R", lower=t, winner="lower", sets=[[10, 21], [11, 21]])
    data = _match(data, "C", upper=t, lower="R", winner="lower", sets=[[18, 21], [21, 19], [17, 21]])
    units = [_unit(s, date=f"2026-10-0{i + 1}") for i, s in enumerate("ABC")]
    ov = aggregate.overview(units, data, athlete=t, range_="all")
    kpi = ov["kpis"]["match_win_rate"]
    assert kpi["status"] == "ok"
    assert kpi["value"] == {"wins": 2, "played": 3, "rate": 0.667}
    assert kpi["coverage"] == {"n": 3, "of": 3}
    rows = {m["stem"]: m for m in ov["matches"]}
    assert (rows["A"]["result"], rows["B"]["result"], rows["C"]["result"]) == ("W", "W", "L")
    assert rows["C"]["sets"] == [[18, 21], [21, 19], [17, 21]]     # athlete is upper: as stored
    assert rows["B"]["sets"] == [[21, 10], [21, 11]]                   # athlete is lower: flipped
    assert rows["A"]["opponent"] == "R" and rows["A"]["athlete"] == "J. Tanaka"
    assert rows["B"]["side"] == "lower"


def test_match_win_rate_no_results_entered():
    data, ids = _store()
    data = _match(data, "A", upper=ids["tanaka"], lower="R")
    ov = aggregate.overview([_unit("A")], data, athlete=ids["tanaka"], range_="all")
    kpi = ov["kpis"]["match_win_rate"]
    assert kpi["status"] == "unavailable" and kpi["reason"]["code"] == "no_results_entered"
    row = ov["matches"][0]
    assert row["result"] is None and row["sets"] is None


def test_result_is_relative_to_each_rows_own_side_in_every_scope():
    data, ids = _store()
    data = _match(data, "A", upper=ids["tanaka"], lower="R", winner="upper", sets=[[21, 5]])
    for kwargs in ({}, {"squad": ids["squad"]}, {"athlete": ids["tanaka"]}):
        ov = aggregate.overview([_unit("A")], data, range_="all", **kwargs)
        by_side = {m["side"]: m["result"] for m in ov["matches"]}
        assert by_side["upper"] == "W", kwargs
        if "lower" in by_side:                  # only without a scope that drops the opponent
            assert by_side["lower"] == "L", kwargs
    # no scope: both sides are rows, each judged against its own side
    ov = aggregate.overview([_unit("A")], data, range_="all")
    assert {m["side"]: m["result"] for m in ov["matches"]} == {"upper": "W", "lower": "L"}
    assert {m["side"]: m["sets"] for m in ov["matches"]} == {"upper": [[21, 5]],
                                                             "lower": [[5, 21]]}
    assert ov["kpis"]["match_win_rate"]["status"] == "unavailable"


def test_result_without_a_recorded_winner_is_null():
    data, ids = _store()
    data = _match(data, "A", upper=ids["tanaka"], lower="R")
    ov = aggregate.overview([_unit("A")], data, range_="all")
    assert [m["result"] for m in ov["matches"]] == [None, None]
    assert [m["sets"] for m in ov["matches"]] == [None, None]


def test_opponent_is_the_other_sides_name():
    data, ids = _store()
    data = _match(data, "A", upper=ids["tanaka"], lower=ids["ito"])
    ov = aggregate.overview([_unit("A")], data, range_="all")
    by_side = {m["side"]: m for m in ov["matches"]}
    assert by_side["upper"]["athlete"] == "J. Tanaka" and by_side["upper"]["opponent"] == "S. Ito"
    assert by_side["lower"]["athlete"] == "S. Ito" and by_side["lower"]["opponent"] == "J. Tanaka"


def test_point_outcome_kpis_always_unavailable():
    data, _ = _store()
    ov = aggregate.overview([_unit("A", upper={"smash": (1, 60.0)})], data, range_="all")
    for key in ("rally_win_rate", "unforced_errors"):
        assert ov["kpis"][key]["status"] == "unavailable"
        assert ov["kpis"][key]["reason"]["code"] == "no_point_outcome"


# ---- benchmark ---------------------------------------------------------------------

def test_benchmark_needs_a_squad():
    data, ids = _store(squad=False)
    data = _match(data, "A", upper=ids["tanaka"])
    ov = aggregate.overview([_unit("A", upper={"smash": (1, 60.0)})], data,
                            athlete=ids["tanaka"], range_="all")
    bench = ov["trend"]["benchmark"]
    assert bench["status"] == "unavailable" and bench["reason"]["code"] == "no_benchmark"


def test_benchmark_without_others_unavailable():
    data, ids = _store()
    data = _match(data, "A", upper=ids["tanaka"])
    ov = aggregate.overview([_unit("A", upper={"smash": (1, 60.0)})], data,
                            athlete=ids["tanaka"], range_="all")
    assert ov["trend"]["benchmark"]["reason"]["code"] == "no_benchmark"


def test_benchmark_is_median_of_other_squad_units():
    data, ids = _store()
    data = _match(data, "A", upper=ids["tanaka"], lower="R")
    data = _match(data, "B", upper=ids["ito"], lower="R")
    data = _match(data, "C", upper="R", lower=ids["ito"])
    data = _match(data, "D", upper=ids["other"], lower="R")          # not in the squad
    units = [_unit("A", upper={"smash": (1, 60.0)}),
             _unit("B", upper={"smash": (1, 70.0)}),
             _unit("C", lower={"smash": (1, 90.0)}),
             _unit("D", upper={"smash": (1, 10.0)})]
    ov = aggregate.overview(units, data, athlete=ids["tanaka"], range_="all")
    bench = ov["trend"]["benchmark"]
    assert bench["status"] == "ok" and bench["value"] == {"index": 80.0}
    # the selection itself only holds the athlete's stem
    assert ov["scope"]["stems"] == 1


def test_benchmark_respects_the_range():
    data, ids = _store()
    data = _match(data, "A", upper=ids["tanaka"])
    data = _match(data, "B", upper=ids["ito"])
    units = [_unit("A", date="2026-10-10", upper={"smash": (1, 60.0)}),
             _unit("B", date="2026-06-01", upper={"smash": (1, 90.0)})]
    ov = aggregate.overview(units, data, athlete=ids["tanaka"], range_="30d")
    assert ov["trend"]["benchmark"]["reason"]["code"] == "no_benchmark"


# ---- status, review, other kpis -----------------------------------------------------

def test_running_and_new_statuses():
    data, _ = _store()
    units = [_unit("A", upper={"smash": (1, 60.0)}), _unit("B", upper={"smash": (1, 60.0)}),
             {"stem": "C", "insights": None}]
    ov = aggregate.overview(units, data, range_="all", running_stems=frozenset({"B"}))
    status = {m["stem"]: m["status"] for m in ov["matches"] if m["side"] == "upper"}
    assert status == {"A": "analysed", "B": "processing", "C": "new"}


def test_needs_review_below_half_coverage():
    data, _ = _store()
    units = [_unit("A", strokes={"classified": 4, "total": 10}),
             _unit("B", strokes={"classified": 5, "total": 10}),
             _unit("C")]
    ov = aggregate.overview(units, data, range_="all")
    review = {m["stem"]: m["needs_review"] for m in ov["matches"] if m["side"] == "upper"}
    assert review == {"A": True, "B": False, "C": False}


def test_strokes_and_net_share_kpis():
    data, _ = _store()
    units = [_unit("A", strokes={"classified": 10, "total": 12,
                                 "distribution": {"net": 4, "clear": 6}}),
             _unit("B", strokes={"classified": 10, "total": 10,
                                 "distribution": {"net": 1, "smash": 9}})]
    ov = aggregate.overview(units, data, range_="all")
    assert ov["kpis"]["strokes"]["value"] == {"classified": 20, "total": 22}
    # no scope: every unit's own strokes, which add up to the stem-wide 5 net / 20 classified
    assert ov["kpis"]["net_share"]["value"] == {"share": 0.25}
    assert ov["kpis"]["net_share"]["coverage"] == ov["kpis"]["strokes"]["coverage"] \
        == {"n": 20, "of": 22}


def _hitter(classified, total, **distribution):
    return {"classified": classified, "total": total, "distribution": dict(distribution)}


def test_net_share_follows_the_selected_athletes_own_strokes():
    data, ids = _store()
    data = _match(data, "A", upper=ids["tanaka"], lower="R")
    data = _match(data, "B", upper="R", lower=ids["tanaka"])
    # the opponent hits every net stroke; Tanaka (upper in A, lower in B) hits none
    strokes_a = {"classified": 12, "total": 14, "distribution": {"clear": 6, "net": 6},
                 "by_hitter": {"upper": _hitter(6, 7, clear=6), "lower": _hitter(6, 7, net=6)}}
    strokes_b = {"classified": 12, "total": 14, "distribution": {"clear": 6, "net": 6},
                 "by_hitter": {"upper": _hitter(6, 7, net=6), "lower": _hitter(6, 7, clear=6)}}
    units = [_unit("A", strokes=strokes_a), _unit("B", strokes=strokes_b)]
    ov = aggregate.overview(units, data, athlete=ids["tanaka"], range_="all")
    assert ov["kpis"]["net_share"]["value"] == {"share": 0.0}
    assert ov["kpis"]["net_share"]["coverage"] == ov["kpis"]["strokes"]["coverage"] \
        == {"n": 12, "of": 14}
    assert ov["kpis"]["strokes"]["value"] == {"classified": 12, "total": 14}
    # the opponent's view of the same videos
    ov = aggregate.overview(units, data, range_="all")
    assert ov["kpis"]["net_share"]["value"] == {"share": 0.5}


def test_net_share_under_squad_scope_excludes_an_outside_opponent():
    data, ids = _store()
    data = _match(data, "A", upper=ids["tanaka"], lower="R")
    strokes = {"classified": 10, "total": 10, "distribution": {"clear": 5, "net": 5},
               "by_hitter": {"upper": _hitter(5, 5, clear=5), "lower": _hitter(5, 5, net=5)}}
    ov = aggregate.overview([_unit("A", strokes=strokes)], data, squad=ids["squad"],
                            range_="all")
    assert ov["kpis"]["net_share"]["value"] == {"share": 0.0}
    assert ov["kpis"]["strokes"]["value"] == {"classified": 5, "total": 5}


def test_strokes_values_without_side_data_use_no_data_not_no_strokes_file():
    data, _ = _store()
    doc = unit_doc("A", strokes={"classified": 10, "total": 10})
    doc["strokes"]["value"]["by_hitter"] = {}
    ov = aggregate.overview([{"stem": "A", "insights": doc}], data, range_="all")
    for key in ("strokes", "net_share"):
        assert ov["kpis"][key]["status"] == "unavailable"
        assert ov["kpis"][key]["reason"]["code"] == "no_data", key
    # a genuinely unavailable insight still reports its own reason
    ov = aggregate.overview([_unit("A")], data, range_="all")
    assert ov["kpis"]["strokes"]["reason"]["code"] == "no_strokes_file"
    assert ov["kpis"]["net_share"]["reason"]["code"] == "no_strokes_file"


def test_first_reason_ignores_reasons_of_usable_insights():
    data, _ = _store()
    partial = unit_doc("A", pull={"upper": (0.5, 4)})
    partial["pull"]["status"] = "partial"          # carries a reason, but is not unavailable
    partial["pull"]["reason"] = {"code": "few_hits", "params": {}}
    partial["pull"]["value"]["upper"] = None
    ov = aggregate.overview([{"stem": "A", "insights": partial}], data, range_="all")
    assert ov["kpis"]["pull_rear"]["reason"]["code"] == "no_data"
    assert ov["movement"]["pull"]["reason"]["code"] == "no_data"


def test_hostile_values_never_break_strict_json():
    import json
    data, _ = _store()
    huge = 1e300
    doc = unit_doc("A", upper={t: (1, 50.0) for t in TECHS},
                   strokes={"classified": 10, "total": 10, "distribution": {"net": 5}},
                   rally={"durations": [4.0], "shots": [3]},
                   recovery={"upper": (1.0, 12, 12)}, pull={"upper": (0.5, 12)},
                   movement={"upper": (2, 3, 300, 100.0, 2.0)})
    for cell in doc["technique"]["value"]["by_side"]["upper"]["techniques"].values():
        cell["count"], cell["avg_score"] = huge, 1e308
    doc["technique"]["value"]["by_side"]["upper"]["index"] = 1e308
    doc["strokes"]["value"]["by_hitter"]["upper"].update(classified=huge, total=huge)
    doc["rally"]["value"]["durations_sec"] = [1e308, 1e308]
    doc["rally"]["value"]["shots_per_rally"] = [1e308, 1e308]
    doc["recovery"]["value"]["upper"].update(median_sec=1e308, found=huge, hits=huge)
    doc["pull"]["value"]["upper"].update(back=1e308, n=huge)
    move = doc["movement"]["value"]["upper"]
    move.update(samples=huge, distance_m=1e308, mean_speed_mps=1e-300)
    move["grid"] = [[1e308] * 10 for _ in range(11)]
    other = unit_doc("B", upper={"smash": (1, 50.0)}, rally={"durations": [4.0], "shots": [3]},
                     movement={"upper": (2, 3, 300, 100.0, 2.0)}, pull={"upper": (0.5, 12)})
    other["pull"]["value"]["upper"]["grid"] = [[1e308] * 10 for _ in range(11)]
    ov = aggregate.overview([{"stem": "A", "insights": doc}, {"stem": "B", "insights": other}],
                            data, range_="all")
    json.dumps(ov, allow_nan=False)
    assert _axes(ov)["smash"]["score"] == 50.0       # out-of-range avg_score is ignored


def test_scores_outside_zero_to_hundred_are_ignored():
    data, _ = _store()
    units = [_unit("A", upper={"smash": (1, 150.0), "clear": (1, -5.0), "drop": (1, 80.0)})]
    ov = aggregate.overview(units, data, range_="all")
    axes = _axes(ov)
    assert axes["smash"]["score"] is None and axes["clear"]["score"] is None
    assert axes["drop"]["score"] == 80.0


def test_strokes_low_coverage_is_partial():
    data, _ = _store()
    ov = aggregate.overview([_unit("A", strokes={"classified": 3, "total": 10})], data,
                            range_="all")
    assert ov["kpis"]["strokes"]["status"] == "partial"
    assert ov["kpis"]["strokes"]["reason"]["code"] == "low_stroke_coverage"


def test_avg_rally_pools_durations_and_flags_swing():
    data, _ = _store()
    units = [_unit("A", rally={"durations": [4.0, 6.0], "shots": [4, 6]}),
             _unit("B", rally={"durations": [8.0], "shots": [5]})]
    ov = aggregate.overview(units, data, range_="all")
    kpi = ov["kpis"]["avg_rally"]
    assert kpi["status"] == "ok"
    assert kpi["value"] == {"seconds": 6.0, "shots": 5.0, "signal": "shuttle"}
    # one stem without shot counts makes the shots mean unknown, never a partial mean
    mixed = aggregate.overview([units[0], _unit("B", rally={"durations": [8.0], "shots": None})],
                               data, range_="all")["kpis"]["avg_rally"]
    assert mixed["value"] == {"seconds": 6.0, "shots": None, "signal": "shuttle"}
    swing = aggregate.overview([_unit("A", rally={"durations": [4.0], "signal": "swing"})],
                               data, range_="all")["kpis"]["avg_rally"]
    assert swing["status"] == "partial" and swing["reason"]["code"] == "signal_swing"
    assert swing["value"]["signal"] == "swing"


def test_recovery_median_of_unit_medians():
    data, _ = _store()
    units = [_unit("A", recovery={"upper": (1.0, 6, 8), "lower": (3.0, 6, 8)}),
             _unit("B", recovery={"upper": (2.0, 6, 8), "lower": (None, 0, 0)})]
    ov = aggregate.overview(units, data, range_="all")
    kpi = ov["kpis"]["recovery"]
    assert kpi["status"] == "ok" and kpi["value"] == {"median_sec": 2.0}
    assert kpi["coverage"] == {"n": 18, "of": 24}


def test_recovery_few_found_is_partial_none_unavailable():
    data, _ = _store()
    few = aggregate.overview([_unit("A", recovery={"upper": (1.0, 4, 8)})], data,
                             range_="all")["kpis"]["recovery"]
    assert few["status"] == "partial" and few["reason"]["code"] == "few_hits"
    none = aggregate.overview([_unit("A", recovery={"upper": (None, 1, 8)})], data,
                              range_="all")["kpis"]["recovery"]
    assert none["status"] == "unavailable" and none["reason"]["code"] == "no_hits"


def test_pull_rear_is_mean_of_unit_back_shares():
    data, ids = _store()
    data = _match(data, "A", upper=ids["tanaka"], lower="R")
    data = _match(data, "B", upper="R", lower=ids["tanaka"])
    units = [_unit("A", pull={"upper": (0.2, 8), "lower": (0.9, 8)}),
             _unit("B", pull={"upper": (0.9, 8), "lower": (0.4, 8)})]
    ov = aggregate.overview(units, data, athlete=ids["tanaka"], range_="all")
    assert ov["kpis"]["pull_rear"]["value"] == {"share": 0.3}        # mean(0.2, 0.4)
    assert ov["kpis"]["pull_rear"]["status"] == "ok"


def test_movement_position_merges_weighted_grids():
    data, _ = _store()
    units = [_unit("A", movement={"upper": (2, 3, 300, 100.0, 2.0)}),
             _unit("B", movement={"upper": (5, 6, 100, 50.0, 1.0)})]
    ov = aggregate.overview(units, data, range_="all")
    pos = ov["movement"]["position"]
    assert pos["status"] == "ok"
    grid = pos["value"]["grid"]
    assert (len(grid), len(grid[0])) == (11, 10)
    assert grid[2][3] == 0.75 and grid[5][6] == 0.25
    assert pos["value"]["samples"] == 400 and pos["value"]["distance_m"] == 150.0
    assert pos["value"]["mean_speed_mps"] == pytest.approx(150 / (50 + 50), abs=0.01)
    assert pos["value"]["base"] == {"x": 3.05, "y": 3.35}


def test_movement_pull_merges_by_pairs():
    data, _ = _store()
    units = [_unit("A", pull={"upper": (0.8, 12)}), _unit("B", pull={"upper": (0.2, 4)})]
    pull = aggregate.overview(units, data, range_="all")["movement"]["pull"]
    assert pull["value"]["n"] == 16 and pull["value"]["back"] == 0.65
    assert (len(pull["value"]["grid"]), len(pull["value"]["grid"][0])) == (11, 10)


# ---- empty selection, shape ---------------------------------------------------------

def test_empty_selection_is_well_formed():
    data, _ = _store()
    ov = aggregate.overview([], data, range_="7d")
    assert ov["scope"]["stems"] == 0 and ov["scope"]["units"] == 0
    assert ov["scope"]["date_from"] is None and ov["scope"]["date_to"] is None
    for key, kpi in ov["kpis"].items():
        assert kpi["status"] == "unavailable", key
        if key not in ("rally_win_rate", "unforced_errors"):
            assert kpi["reason"]["code"] == "no_data", key
    assert ov["trend"]["points"] == []
    assert ov["trend"]["benchmark"]["status"] == "unavailable"
    assert ov["movement"]["position"]["reason"]["code"] == "no_data"
    assert ov["movement"]["pull"]["reason"]["code"] == "no_data"
    assert ov["matches"] == [] and isinstance(ov["findings"], list)
    assert len(ov["radar"]["axes"]) == 7
    assert all(a["status"] == "unavailable" and a["score"] is None for a in ov["radar"]["axes"])


def test_response_keys_match_the_contract():
    data, _ = _store()
    ov = aggregate.overview([_unit("A", upper={"smash": (1, 60.0)})], data, range_="all")
    assert list(ov) == ["scope", "kpis", "radar", "trend", "movement", "doubles", "matches",
                        "findings"]
    assert set(ov["scope"]) == {"athlete", "squad", "range", "stems", "units", "date_from",
                                "date_to", "generated_at"}
    assert set(ov["kpis"]) == {"technique_index", "match_win_rate", "avg_rally",
                               "rally_win_rate", "recovery", "net_share", "pull_rear",
                               "unforced_errors", "strokes"}
    assert set(ov["radar"]) == {"reference", "axes"}
    assert set(ov["trend"]) == {"points", "benchmark"}
    assert set(ov["movement"]) == {"position", "pull"}
    assert set(ov["matches"][0]) == {"stem", "date", "date_source", "side", "athlete",
                                     "opponent", "result", "sets", "tech_index", "status",
                                     "needs_review", "doubles"}
    for kpi in ov["kpis"].values():
        assert set(kpi) == {"status", "value", "coverage", "reason"}


def test_aggregate_tolerates_malformed_unit_documents():
    data, _ = _store()
    units = [{"stem": "A", "insights": {"technique": "junk", "rally": None, "strokes": 3}},
             {"stem": "B", "insights": {"date": {"value": "not a date"}}},
             _unit("C", upper={"smash": (1, 60.0)})]
    ov = aggregate.overview(units, data, range_="all")
    assert _axes(ov)["smash"]["score"] == 60.0


# ---- findings ------------------------------------------------------------------------

def _overview(scores, **kw):
    data, _ = _store()
    units = [_unit("A", upper={t: (1, s) for t, s in scores.items()}, **kw)]
    return aggregate.overview(units, data, range_="all")


def test_findings_below_reference_lowest_first_then_strongest():
    ov = _overview({"smash": 66.0, "clear": 55.0, "drop": 90.0, "net": 80.0})
    fs = findings.build_findings(ov)
    assert fs[0] == {"code": "technique_below_reference", "severity": "watch",
                     "params": {"technique": "clear", "score": 55.0, "lo": 70}}
    assert fs[1]["params"] == {"technique": "smash", "score": 66.0, "lo": 70}
    strongest = [f for f in fs if f["code"] == "technique_strongest"]
    assert strongest == [{"code": "technique_strongest", "severity": "good",
                          "params": {"technique": "drop", "score": 90.0}}]


def test_findings_unscored_names_exact():
    ov = _overview({"smash": 66.0, "clear": 75.0, "drop": 80.0, "net": 90.0})
    fs = [f for f in findings.build_findings(ov) if f["code"] == "techniques_unscored"]
    assert fs == [{"code": "techniques_unscored", "severity": "info",
                   "params": {"names": ["lift", "drive", "serve"]}}]


def test_findings_capped_at_six_and_sorted_by_severity():
    ov = _overview({"smash": 10.0, "clear": 20.0, "drop": 30.0, "net": 40.0, "lift": 50.0})
    ov["kpis"]["recovery"] = {"status": "ok", "value": {"median_sec": 1.5},
                              "coverage": None, "reason": None}
    ov["kpis"]["avg_rally"] = {"status": "partial", "value": {"seconds": 5, "shots": None,
                                                              "signal": "swing"},
                               "coverage": None, "reason": {"code": "signal_swing",
                                                            "params": {}}}
    ov["kpis"]["strokes"] = {"status": "partial", "value": {"classified": 3, "total": 10},
                             "coverage": None, "reason": {"code": "low_stroke_coverage",
                                                          "params": {}}}
    ov["kpis"]["technique_index"]["value"]["delta"] = -5.0
    fs = findings.build_findings(ov)
    assert len(fs) <= 6
    order = {"watch": 0, "good": 1, "info": 2}
    assert [order[f["severity"]] for f in fs] == sorted(order[f["severity"]] for f in fs)
    codes = [f["code"] for f in fs]
    assert "strokes_coverage_low" in codes and "index_trend" in codes
    trend = [f for f in fs if f["code"] == "index_trend"][0]
    assert trend["severity"] == "watch" and trend["params"] == {"delta": -5.0, "range": "all"}


def test_findings_recovery_and_rally_estimated_and_good_trend():
    ov = _overview({"smash": 90.0})
    ov["kpis"]["recovery"] = {"status": "ok", "value": {"median_sec": 1.5},
                              "coverage": None, "reason": None}
    ov["kpis"]["avg_rally"] = {"status": "partial", "value": {"seconds": 5, "shots": None,
                                                              "signal": "swing"},
                               "coverage": None, "reason": {"code": "signal_swing",
                                                            "params": {}}}
    ov["kpis"]["technique_index"]["value"]["delta"] = 4.0
    by_code = {f["code"]: f for f in findings.build_findings(ov)}
    assert by_code["recovery_median"] == {"code": "recovery_median", "severity": "info",
                                          "params": {"sec": 1.5}}
    assert by_code["rally_estimated"]["params"] == {"signal": "swing"}
    assert by_code["index_trend"]["severity"] == "good"


def test_findings_empty_overview_is_empty_list():
    data, _ = _store()
    assert findings.build_findings(aggregate.overview([], data, range_="all")) == []


def test_findings_tolerate_a_bare_dict():
    assert findings.build_findings({}) == []


def test_overview_embeds_findings():
    ov = _overview({"smash": 66.0})
    assert ov["findings"] == findings.build_findings(ov)


# ---- doubles runs ---------------------------------------------------------------

PLAYER_KPIS = ("technique_index", "recovery", "net_share", "pull_rear", "strokes",
               "match_win_rate")


def _full_unit(stem, match_format=None, **extra):
    """A unit with data in every per-player insight (as a stale sidecar might hold)."""
    kw = dict(upper={"smash": (12, 80.0)}, lower={"smash": (12, 60.0)},
              strokes={"classified": 20, "total": 20, "distribution": {"net": 10, "smash": 10}},
              rally={"durations": [4.0, 6.0], "shots": [5, 7], "signal": "shuttle"},
              recovery={"upper": (1.2, 12, 12), "lower": (1.4, 12, 12)},
              pull={"upper": (0.4, 12), "lower": (0.6, 12)},
              movement={"upper": (3, 4, 100, 50.0, 1.0), "lower": (8, 5, 100, 50.0, 1.0)})
    kw.update(extra)
    return _unit(stem, match_format=match_format, **kw)


def test_doubles_unit_contributes_nothing_to_player_aggregates():
    data, _ = _store()
    solo = [_full_unit("S1", date="2026-10-10")]
    mixed = solo + [_full_unit("D1", match_format="doubles", date="2026-10-11",
                               upper={"smash": (50, 99.0)}, lower={"smash": (50, 1.0)})]
    only = aggregate.overview(solo, data, range_="all")
    both = aggregate.overview(mixed, data, range_="all")
    for key in PLAYER_KPIS:
        assert both["kpis"][key] == only["kpis"][key], key
    assert both["radar"] == only["radar"]
    assert both["trend"]["points"] == only["trend"]["points"]
    assert both["movement"] == only["movement"]
    assert both["findings"] == only["findings"]
    assert both["scope"]["stems"] == 2
    assert both["scope"]["units"] == only["scope"]["units"] == 2     # singles sides only


def test_doubles_unit_still_counts_for_rallies():
    data, _ = _store()
    units = [_full_unit("S1", date="2026-10-10"),
             _full_unit("D1", match_format="doubles", date="2026-10-11",
                        rally={"durations": [10.0, 20.0], "shots": [9, 11], "signal": "shuttle"})]
    ov = aggregate.overview(units, data, range_="all")
    rally = ov["kpis"]["avg_rally"]
    assert rally["status"] == "ok"
    assert rally["value"]["seconds"] == 10.0                  # mean of 4, 6, 10, 20
    assert rally["coverage"] == {"n": 2, "of": 2}


def test_matches_rows_flag_doubles_and_withhold_the_index():
    data, _ = _store()
    units = [_full_unit("S1", date="2026-10-10", match_format="singles"),
             _full_unit("D1", match_format="doubles", date="2026-10-11")]
    rows = aggregate.overview(units, data, range_="all")["matches"]
    by_stem = {}
    for r in rows:
        by_stem.setdefault(r["stem"], []).append(r)
    assert {r["stem"] for r in rows} == {"S1", "D1"}
    assert len(by_stem["D1"]) == 1               # no scope: one row per doubles stem
    for r in by_stem["D1"]:
        assert r["doubles"] is True and r["tech_index"] is None
        # the sidecar has no doubles insight, so the identities are unknown: review it
        assert r["needs_review"] is True and r["status"] == "analysed"
    for r in by_stem["S1"]:
        assert r["doubles"] is False and r["tech_index"] is not None
    assert all(type(r["doubles"]) is bool for r in rows)


def test_only_doubles_selection_says_so_and_has_no_findings():
    data, _ = _store()
    ov = aggregate.overview([_full_unit("D1", match_format="doubles")], data, range_="all")
    assert ov["scope"]["stems"] == 1 and ov["scope"]["units"] == 0
    for key in PLAYER_KPIS:
        kpi = ov["kpis"][key]
        assert kpi["status"] == "unavailable" and kpi["reason"]["code"] == "doubles_run", key
    assert ov["movement"]["position"]["reason"]["code"] == "doubles_run"
    assert ov["movement"]["pull"]["reason"]["code"] == "doubles_run"
    assert ov["trend"]["points"] == []
    assert all(a["status"] == "unavailable" for a in ov["radar"]["axes"])
    assert ov["findings"] == []
    assert len(ov["matches"]) == 1 and all(m["doubles"] for m in ov["matches"])


def test_doubles_unit_is_not_a_benchmark_source():
    data, ids = _store()
    data = _match(data, "A", upper=ids["tanaka"])
    data = _match(data, "B", upper=ids["ito"])
    units = [_unit("A", upper={"smash": (12, 50.0)}),
             _unit("B", upper={"smash": (12, 99.0)}, match_format="doubles")]
    ov = aggregate.overview(units, data, athlete=ids["tanaka"], range_="all")
    assert ov["trend"]["benchmark"]["status"] == "unavailable"
    assert ov["trend"]["benchmark"]["reason"]["code"] == "no_benchmark"


@pytest.mark.parametrize("value", [None, "singles", "triples", 2, ["doubles"], "Doubles"])
def test_other_match_format_values_are_singles(value):
    data, _ = _store()
    ov = aggregate.overview([_full_unit("S1", match_format=value)], data, range_="all")
    assert ov["scope"]["units"] == 2
    assert all(m["doubles"] is False for m in ov["matches"])
    assert ov["kpis"]["technique_index"]["status"] != "unavailable"


# ---- doubles identities (doubles addendum 4.2) ------------------------------------------

import json  # noqa: E402

from badminton_analysis.insights import envelope  # noqa: E402
from tests.insights_fixtures import DOUBLES_TEAMS, grid_at  # noqa: E402

DOUBLES_ROW_KEYS = {"stem", "date", "date_source", "side", "athlete", "opponent", "result", "sets",
                    "tech_index", "status", "needs_review", "doubles", "format", "athlete_player",
                    "team", "partner", "teams"}
_CELL = {"P1": (2, 1), "P2": (8, 8), "P3": (7, 1), "P4": (1, 8)}


def _dplayer(pid, *, team="default", name=None, grid="default", **kw):
    row = {"id": pid, "team": DOUBLES_TEAMS[pid] if team == "default" else team, "name": name,
           "coverage": 0.8, "frames": 100, "distance_m": 40.0, "avg_speed_mps": 1.0,
           "max_speed_mps": 4.0, "hits": 10, "hits_named": 4, "samples": 100,
           "grid": grid_at(*_CELL[pid]) if grid == "default" else grid}
    row.update(kw)
    return row


def _dvalue(status="ok", players=None, teams="default"):
    players = players if players is not None else [_dplayer(p) for p in ("P1", "P2", "P3", "P4")]
    if teams == "default":
        teams = [{"id": k, "players": [p["id"] for p in players if p.get("team") == k],
                  "hits": 0, "hits_named": 0} for k in (1, 2)
                 if any(p.get("team") == k for p in players)] or None
    return {"status": status, "validated": False, "players": players, "teams": teams,
            "hits": {"total": 40, "named": 16, "named_share": 0.4, "single_candidate": 0},
            "identity_video": None}


def _dunit(stem, date="2026-10-11", status="ok", players=None, unavailable=None, teams="default",
           **kw):
    doc = unit_doc(stem, date=date, match_format="doubles", **kw)
    if unavailable:
        doc["doubles"] = envelope.unavailable(unavailable)
    elif status == "ok":
        doc["doubles"] = envelope.ok(_dvalue(status, players, teams))
    else:
        doc["doubles"] = envelope.partial(_dvalue(status, players, teams), "doubles_degraded",
                                          {"status": status})
    return {"stem": stem, "insights": doc}


def _dmatch(data, stem, players, date=None, winner_team=None, sets=None):
    def entry(v):
        if v is None:
            return None
        return {"athlete_id": v} if v.startswith("a_") else {"name": v}

    result = {"winner_team": winner_team, "sets": sets or []} if winner_team else None
    rec, err = lib.validate_match({"date": date, "format": "doubles",
                                   "players": {k: entry(v) for k, v in players.items()},
                                   "result": result}, data)
    assert err is None, err
    return lib.set_match(data, stem, rec)


def _rows_by(ov):
    return {(m["stem"], m.get("athlete_player")): m for m in ov["matches"]}


def _mixed():
    """Tanaka: singles A (upper) and doubles D1 (P3); Ito: doubles D2 (P1); D3 unmapped."""
    data, ids = _store()
    data = _match(data, "A", upper=ids["tanaka"], lower="R", date="2026-10-10")
    data = _dmatch(data, "D1", {"P1": "Alice", "P2": "Bob", "P3": ids["tanaka"], "P4": "Dee"},
                   date="2026-10-11", winner_team=1, sets=[[21, 18], [19, 21], [21, 15]])
    data = _dmatch(data, "D2", {"P1": ids["ito"]}, date="2026-10-12")
    units = [_unit("A", date="2026-10-10", upper={"smash": (12, 80.0)}, lower={"smash": (12, 40.0)},
                   movement={"upper": (2, 3, 300, 100.0, 2.0)}),
             _dunit("D1", date="2026-10-11"), _dunit("D2", date="2026-10-12"),
             _dunit("D3", date="2026-10-13")]
    return data, ids, units


def test_doubles_athlete_scope_keeps_singles_kpis_singles_only():
    data, ids, units = _mixed()
    ov = aggregate.overview(units, data, athlete=ids["tanaka"], range_="all")
    json.dumps(ov, allow_nan=False)
    assert ov["scope"]["stems"] == 2
    assert ov["scope"]["units"] == 2                 # singles A.upper + doubles D1.P3
    assert _axes(ov)["smash"]["score"] == 80.0       # from the singles unit only
    assert ov["kpis"]["match_win_rate"]["status"] == "unavailable"
    dbl = ov["doubles"]
    assert dbl["status"] == "ok"
    assert dbl["value"]["matches"] == 1
    assert [r["stem"] for r in dbl["value"]["runs"]] == ["D1"]
    athlete = dbl["value"]["athlete"]
    assert set(athlete) == {"distance_m", "avg_speed_mps", "max_speed_mps", "frames", "hits",
                            "hits_named", "hits_named_of", "named_share", "matches", "grid",
                            "samples"}
    assert (athlete["distance_m"], athlete["hits"], athlete["hits_named"]) == (40.0, 10, 4)
    assert athlete["named_share"] == 0.4 and athlete["matches"] == 1
    assert athlete["samples"] == 100 and athlete["grid"][7][1] == 1.0


def test_doubles_athlete_grid_merges_into_movement_position():
    data, ids, units = _mixed()
    pos = aggregate.overview(units, data, athlete=ids["tanaka"], range_="all")["movement"]["position"]
    assert pos["status"] == "ok"
    grid = pos["value"]["grid"]
    assert grid[2][3] == 0.75 and grid[7][1] == 0.25          # 300 singles + 100 doubles samples
    assert sum(map(sum, grid)) == pytest.approx(1.0, abs=1e-3)
    assert pos["value"]["samples"] == 400
    assert pos["coverage"] == {"n": 2, "of": 2}
    # no athlete scope: singles grids only, as before
    pos = aggregate.overview(units, data, range_="all")["movement"]["position"]
    assert pos["value"]["samples"] == 300 and pos["value"]["grid"][7][1] == 0.0
    pos = aggregate.overview(units, data, squad=ids["squad"], range_="all")["movement"]["position"]
    assert pos["value"]["samples"] == 300


def test_doubles_row_for_the_athletes_identity():
    data, ids, units = _mixed()
    ov = aggregate.overview(units, data, athlete=ids["tanaka"], range_="all")
    row = _rows_by(ov)[("D1", "P3")]
    assert set(row) == DOUBLES_ROW_KEYS
    assert row["doubles"] is True and row["format"] == "doubles"
    assert (row["athlete_player"], row["team"]) == ("P3", 2)
    assert row["athlete"] == "J. Tanaka" and row["partner"] == "Dee"
    assert row["opponent"] == "Alice / Bob"
    assert row["result"] == "L"                                   # team 1 won
    assert row["sets"] == [[18, 21], [21, 19], [15, 21]]          # oriented [own, opp]
    assert row["tech_index"] is None and row["needs_review"] is False
    assert row["side"] is None and row["status"] == "analysed"
    singles = _rows_by(ov)[("A", None)]
    assert "format" not in singles and singles["side"] == "upper"


def test_doubles_team_one_athlete_wins_and_keeps_set_order():
    data, ids = _store()
    data = _dmatch(data, "D1", {"P2": ids["tanaka"], "P1": "Alice"}, winner_team=1,
                   sets=[[21, 18]])
    ov = aggregate.overview([_dunit("D1")], data, athlete=ids["tanaka"], range_="all")
    row = ov["matches"][0]
    assert (row["athlete_player"], row["team"], row["result"]) == ("P2", 1, "W")
    assert row["sets"] == [[21, 18]] and row["partner"] == "Alice"
    assert row["opponent"] is None                    # nobody on team 2 has a name


def test_doubles_result_null_without_winner_or_team():
    data, ids = _store()
    data = _dmatch(data, "D1", {"P3": ids["tanaka"]})
    row = aggregate.overview([_dunit("D1")], data, athlete=ids["tanaka"], range_="all")["matches"][0]
    assert row["result"] is None and row["sets"] is None
    data = _dmatch(data, "D2", {"P3": ids["tanaka"]}, winner_team=2, sets=[[10, 21]])
    unit = _dunit("D2", status="degraded_teams",
                  players=[_dplayer(p, team=None) for p in ("P1", "P2", "P3", "P4")], teams=None)
    ov = aggregate.overview([unit], data, athlete=ids["tanaka"], range_="all")
    row = ov["matches"][0]
    assert row["team"] is None and row["result"] is None
    assert row["sets"] == [[10, 21]]                   # team unknown: team-1-first
    assert row["partner"] is None and row["opponent"] is None
    assert row["needs_review"] is True


def test_doubles_degraded_teams_ignores_stale_team_numbers():
    data, ids = _store()
    data = _dmatch(data, "D1", {"P3": ids["tanaka"]}, winner_team=2)
    ov = aggregate.overview([_dunit("D1", status="degraded_teams")], data, athlete=ids["tanaka"],
                            range_="all")
    assert ov["matches"][0]["team"] is None and ov["matches"][0]["result"] is None
    assert all(p["team"] is None for p in ov["doubles"]["value"]["runs"][0]["players"])


def test_squad_scope_selects_doubles_stems_with_any_member():
    data, ids, units = _mixed()
    ov = aggregate.overview(units, data, squad=ids["squad"], range_="all")
    assert {r["stem"] for r in ov["doubles"]["value"]["runs"]} == {"D1", "D2"}
    assert ov["doubles"]["value"]["matches"] == 2
    assert ov["doubles"]["value"]["athlete"] is None
    assert ov["scope"]["units"] == 3                  # A.upper + D1.P3 + D2.P1
    assert {k for k in _rows_by(ov) if k[0].startswith("D")} == {("D1", "P3"), ("D2", "P1")}
    assert _rows_by(ov)[("D2", "P1")]["athlete"] == "S. Ito"


def test_no_scope_lists_every_doubles_stem():
    data, ids, units = _mixed()
    ov = aggregate.overview(units, data, range_="all")
    dbl = ov["doubles"]["value"]
    assert [r["stem"] for r in dbl["runs"]] == ["D3", "D2", "D1"]         # newest first
    assert dbl["matches"] == 3 and dbl["athlete"] is None
    assert ov["scope"]["stems"] == 4
    assert ov["scope"]["units"] == 2 + 2              # A both sides + D1.P3 + D2.P1
    doubles_rows = [m for m in ov["matches"] if m["doubles"]]
    assert sorted(m["stem"] for m in doubles_rows) == ["D1", "D2", "D3"]  # one row per stem
    d1 = [m for m in doubles_rows if m["stem"] == "D1"][0]
    assert d1["athlete_player"] is None and d1["team"] is None and d1["result"] is None
    assert d1["sets"] == [[21, 18], [19, 21], [21, 15]]                    # team-1-first
    assert set(d1) == DOUBLES_ROW_KEYS
    run = [r for r in dbl["runs"] if r["stem"] == "D1"][0]
    assert set(run) == {"stem", "date", "date_source", "status", "validated", "teams", "players"}
    assert run["validated"] is False and run["status"] == "ok"
    names = {p["id"]: p["athlete"] for p in run["players"]}
    assert names == {"P1": "Alice", "P2": "Bob", "P3": "J. Tanaka", "P4": "Dee"}
    assert run["teams"][0]["players"] == ["P1", "P2"]
    assert "grid" not in run["players"][0]


def test_doubles_run_player_name_falls_back_to_identity_name_then_null():
    data, ids = _store()
    data = _dmatch(data, "D1", {"P1": ids["ito"]})
    data = lib.update_athlete(data, ids["ito"], name="S. Ito-Renamed")
    players = [_dplayer("P1", name="Pipeline One"), _dplayer("P2", name="Pipeline Two"),
               _dplayer("P3"), _dplayer("P4")]
    ov = aggregate.overview([_dunit("D1", players=players)], data, range_="all")
    names = {p["id"]: (p["athlete"], p["name"])
             for p in ov["doubles"]["value"]["runs"][0]["players"]}
    assert names["P1"] == ("S. Ito-Renamed", "Pipeline One")      # the registry name wins
    assert names["P2"] == ("Pipeline Two", "Pipeline Two")
    assert names["P3"] == (None, None)


def test_doubles_only_scope_says_doubles_run_for_singles_kpis():
    data, ids = _store()
    data = _dmatch(data, "D1", {"P3": ids["tanaka"]})
    ov = aggregate.overview([_dunit("D1")], data, athlete=ids["tanaka"], range_="all")
    for key in PLAYER_KPIS:
        assert ov["kpis"][key]["reason"]["code"] == "doubles_run", key
    assert ov["movement"]["pull"]["reason"]["code"] == "doubles_run"
    pos = ov["movement"]["position"]
    assert pos["status"] == "ok" and pos["value"]["grid"][7][1] == 1.0   # the doubles grid
    assert pos["value"]["samples"] == 100
    assert ov["doubles"]["status"] == "ok" and ov["scope"]["units"] == 1


def test_degraded_run_is_partial_with_a_finding():
    data, ids = _store()
    data = _dmatch(data, "D1", {"P3": ids["tanaka"]})
    data = _dmatch(data, "D2", {"P3": ids["tanaka"]})
    units = [_dunit("D1", date="2026-10-11"), _dunit("D2", date="2026-10-12", status="degraded")]
    ov = aggregate.overview(units, data, athlete=ids["tanaka"], range_="all")
    dbl = ov["doubles"]
    assert dbl["status"] == "partial"
    assert dbl["reason"] == {"code": "doubles_degraded", "params": {"status": "degraded"}}
    assert {r["stem"]: r["status"] for r in dbl["value"]["runs"]} == {"D1": "ok", "D2": "degraded"}
    assert {m["stem"]: m["needs_review"] for m in ov["matches"]} == {"D1": False, "D2": True}
    found = [f for f in ov["findings"] if f["code"] == "doubles_identity_degraded"]
    assert found == [{"code": "doubles_identity_degraded", "severity": "watch",
                      "params": {"status": "degraded"}}]


def test_error_and_missing_runs_are_listed_and_partial():
    data, _ = _store()
    units = [_dunit("D1", date="2026-10-12", unavailable="doubles_error"),
             _dunit("D2", date="2026-10-11", unavailable="no_players")]
    ov = aggregate.overview(units, data, range_="all")
    dbl = ov["doubles"]
    assert dbl["status"] == "partial" and dbl["reason"]["code"] == "doubles_error"
    runs = {r["stem"]: r for r in dbl["value"]["runs"]}
    assert (runs["D1"]["status"], runs["D2"]["status"]) == ("error", "missing")
    assert runs["D1"]["players"] == [] and runs["D1"]["teams"] == []
    assert not [f for f in ov["findings"] if f["code"].startswith("doubles_")]
    assert all(m["needs_review"] for m in ov["matches"])


def test_unmapped_identities_only_feed_runs():
    data, ids = _store()
    data = _dmatch(data, "D1", {"P1": "Alice"})
    ov = aggregate.overview([_dunit("D1")], data, range_="all")
    assert ov["scope"]["units"] == 0 and ov["doubles"]["value"]["matches"] == 1
    assert aggregate.overview([_dunit("D1")], data, athlete=ids["tanaka"],
                              range_="all")["doubles"]["reason"]["code"] == "no_data"


def test_deleted_athlete_leaves_scope_but_the_run_stays():
    data, ids = _store()
    data = _dmatch(data, "D1", {"P3": ids["tanaka"], "P1": ids["ito"]})
    data, rewritten = lib.delete_athlete(data, ids["tanaka"])
    assert rewritten == 1
    with pytest.raises(LookupError):
        aggregate.overview([_dunit("D1")], data, athlete=ids["tanaka"], range_="all")
    ov = aggregate.overview([_dunit("D1")], data, range_="all")
    assert ov["scope"]["units"] == 1                                    # only Ito is still mapped
    p3 = [p for p in ov["doubles"]["value"]["runs"][0]["players"] if p["id"] == "P3"][0]
    assert p3["athlete"] == "J. Tanaka"                                  # now a free-text name
    ito = aggregate.overview([_dunit("D1")], data, athlete=ids["ito"], range_="all")
    assert ito["matches"][0]["opponent"] == "J. Tanaka"


def test_athlete_summary_across_matches():
    data, ids = _store()
    data = _dmatch(data, "D1", {"P3": ids["tanaka"]})
    data = _dmatch(data, "D2", {"P1": ids["tanaka"]})
    data = _dmatch(data, "D3", {"P2": ids["tanaka"]})
    one = [_dplayer("P1"), _dplayer("P2"),
           _dplayer("P3", frames=100, distance_m=30.0, avg_speed_mps=1.0, max_speed_mps=4.0,
                    hits=10, hits_named=2, samples=100), _dplayer("P4")]
    two = [_dplayer("P1", frames=300, distance_m=None, avg_speed_mps=2.0, max_speed_mps=6.0,
                    hits=30, hits_named=18, samples=300), _dplayer("P2")]
    three = [_dplayer("P2", frames=None, distance_m=float("nan"), avg_speed_mps=None,
                      max_speed_mps=None, hits=None, hits_named=None, samples=10, grid=None)]
    units = [_dunit("D1", date="2026-10-01", players=one),
             _dunit("D2", date="2026-10-02", players=two),
             _dunit("D3", date="2026-10-03", players=three)]
    ov = aggregate.overview(units, data, athlete=ids["tanaka"], range_="all")
    json.dumps(ov, allow_nan=False)
    s = ov["doubles"]["value"]["athlete"]
    assert s["matches"] == 3
    assert s["distance_m"] == 30.0                    # the null distance is skipped, not 0
    assert s["frames"] == 400
    assert s["avg_speed_mps"] == 1.75                 # (1*100 + 2*300) / 400
    assert s["max_speed_mps"] == 6.0
    assert (s["hits"], s["hits_named"], s["named_share"]) == (40, 20, 0.5)
    assert s["samples"] == 400
    assert s["grid"][7][1] == 0.25 and s["grid"][2][1] == 0.75
    assert sum(map(sum, s["grid"])) == pytest.approx(1.0, abs=1e-3)


def test_athlete_summary_of_nothing_is_null_not_zero():
    data, ids = _store()
    data = _dmatch(data, "D1", {"P3": ids["tanaka"]})
    players = [_dplayer("P3", frames=None, distance_m=None, avg_speed_mps=None,
                        max_speed_mps=None, hits=None, hits_named=None, samples=None, grid=None)]
    s = aggregate.overview([_dunit("D1", players=players)], data, athlete=ids["tanaka"],
                           range_="all")["doubles"]["value"]["athlete"]
    assert s["matches"] == 1
    for key in ("distance_m", "avg_speed_mps", "max_speed_mps", "frames", "hits", "hits_named",
                "hits_named_of", "named_share", "grid", "samples"):
        assert s[key] is None, key


def test_doubles_hits_unvalidated_finding_needs_an_athlete_summary():
    data, ids, units = _mixed()
    ov = aggregate.overview(units, data, athlete=ids["tanaka"], range_="all")
    found = [f for f in ov["findings"] if f["code"] == "doubles_hits_unvalidated"]
    assert found == [{"code": "doubles_hits_unvalidated", "severity": "info",
                      "params": {"named_share": 0.4}}]
    ov = aggregate.overview(units, data, range_="all")
    assert not [f for f in ov["findings"] if f["code"] == "doubles_hits_unvalidated"]


def test_doubles_range_and_empty_scopes_are_well_formed():
    data, ids, units = _mixed()
    ov = aggregate.overview([], data, range_="7d")
    assert ov["doubles"] == envelope.unavailable("no_data")
    ov = aggregate.overview(units[:1], data, athlete=ids["tanaka"], range_="all")
    assert ov["doubles"]["status"] == "unavailable" and ov["doubles"]["reason"]["code"] == "no_data"
    old = [_dunit("D9", date="2026-01-01")] + units
    ov = aggregate.overview(old, data, range_="30d")
    assert "D9" not in {r["stem"] for r in ov["doubles"]["value"]["runs"]}
    json.dumps(ov, allow_nan=False)


def test_doubles_record_on_a_new_stem_is_a_doubles_row():
    data, ids = _store()
    data = _dmatch(data, "N1", {"P3": ids["tanaka"]}, winner_team=2, sets=[[10, 21]])
    ov = aggregate.overview([{"stem": "N1", "insights": None}], data, athlete=ids["tanaka"],
                            range_="all")
    row = ov["matches"][0]
    assert (row["doubles"], row["status"], row["athlete_player"]) == (True, "new", "P3")
    assert row["needs_review"] is False and row["team"] is None
    assert ov["doubles"]["reason"]["code"] == "no_data"               # nothing analysed yet


def test_doubles_run_with_a_singles_record_maps_nobody():
    data, ids = _store()
    data = _match(data, "D1", upper=ids["tanaka"])
    ov = aggregate.overview([_dunit("D1")], data, athlete=ids["tanaka"], range_="all")
    assert ov["scope"]["stems"] == 0
    ov = aggregate.overview([_dunit("D1")], data, range_="all")
    assert ov["scope"]["units"] == 0 and ov["doubles"]["value"]["matches"] == 1
    assert all(p["athlete"] is None for p in ov["doubles"]["value"]["runs"][0]["players"])


def test_singles_run_with_a_doubles_record_maps_nobody():
    data, ids = _store()
    data = _dmatch(data, "S1", {"P1": ids["tanaka"]})
    units = [_unit("S1", upper={"smash": (12, 80.0)})]
    assert aggregate.overview(units, data, athlete=ids["tanaka"], range_="all")["scope"]["stems"] == 0
    ov = aggregate.overview(units, data, range_="all")
    assert ov["doubles"]["reason"]["code"] == "no_data"
    assert all(m["doubles"] is False for m in ov["matches"])


@pytest.mark.parametrize("junk", ["players", "numbers", "grid", "value", "teams", "status"])
def test_hostile_doubles_values_stay_strict_json(junk):
    data, ids = _store()
    data = _dmatch(data, "D1", {"P3": ids["tanaka"], "P1": ids["ito"]}, winner_team=1)
    unit = _dunit("D1")
    value = unit["insights"]["doubles"]["value"]
    if junk == "players":
        value["players"] = [None, "P1", {"id": ["x"]}, {"id": "P9"}, _dplayer("P3"),
                            _dplayer("P3", distance_m=999.0), {"id": "P1", "team": True}]
    elif junk == "numbers":
        value["players"] = [_dplayer("P3", frames=float("inf"), distance_m=1e308,
                                     avg_speed_mps=float("nan"), max_speed_mps=-1, hits=1e300,
                                     hits_named="4", samples=True, coverage=7, name=["x"])]
    elif junk == "grid":
        value["players"] = [_dplayer("P3", grid=[[float("nan")] * 10] * 11, samples=1e300),
                            _dplayer("P1", grid=[[1e308] * 10] * 11)]
    elif junk == "value":
        unit["insights"]["doubles"] = {"status": "ok", "value": ["not", "a", "dict"]}
    elif junk == "teams":
        value["teams"] = [{"id": 3}, "x", {"id": 1, "players": "P1", "hits": float("nan")}]
    else:
        value["status"] = "fine"
    ov = aggregate.overview([unit, _unit("A", upper={"smash": (12, 80.0)})], data,
                            athlete=ids["tanaka"], range_="all")
    json.dumps(ov, allow_nan=False)
    ov = aggregate.overview([unit], data, range_="all")
    json.dumps(ov, allow_nan=False)
    if junk == "numbers":
        s = aggregate.overview([unit], data, athlete=ids["tanaka"],
                               range_="all")["doubles"]["value"]["athlete"]
        assert s["distance_m"] is None and s["frames"] is None and s["hits"] is None
    if junk == "players":
        run = ov["doubles"]["value"]["runs"][0]
        assert [p["id"] for p in run["players"]] == ["P3", "P1"]      # first duplicate wins
        assert run["players"][0]["distance_m"] == 40.0
    if junk == "status":
        assert ov["doubles"]["status"] == "partial"     # an unknown status is never ok


def test_findings_doubles_codes_from_a_bare_overview():
    ov = {"doubles": {"status": "partial", "value": {
        "matches": 2, "athlete": {"named_share": 0.25},
        "runs": [{"stem": "D1", "status": "ok"}, {"stem": "D2", "status": "degraded_teams"},
                 "junk"]}}}
    assert findings.build_findings(ov) == [
        {"code": "doubles_identity_degraded", "severity": "watch",
         "params": {"status": "degraded_teams"}},
        {"code": "doubles_hits_unvalidated", "severity": "info", "params": {"named_share": 0.25}}]
    ov["doubles"]["value"]["athlete"] = None
    ov["doubles"]["value"]["runs"] = [{"status": "error"}]
    assert findings.build_findings(ov) == []
    assert findings.build_findings({"doubles": "junk"}) == []
    assert findings.build_findings({"doubles": {"status": "unavailable", "value": {
        "runs": [{"status": "degraded"}]}}}) == []


# ---- doubles fix round 1 ----------------------------------------------------------------

def test_doubles_rows_carry_both_teams_names():
    data, ids, units = _mixed()
    for kwargs in ({}, {"athlete": ids["tanaka"]}, {"squad": ids["squad"]}):
        rows = aggregate.overview(units, data, range_="all", **kwargs)["matches"]
        d1 = [m for m in rows if m["stem"] == "D1"][0]
        assert d1["teams"] == ["Alice / Bob", "J. Tanaka / Dee"], kwargs
    rows = aggregate.overview(units, data, range_="all")["matches"]
    d3 = [m for m in rows if m["stem"] == "D3"][0]
    assert d3["teams"] == ["P1 / P2", "P3 / P4"]           # unmapped: identity ids
    d2 = [m for m in rows if m["stem"] == "D2"][0]
    assert d2["teams"] == ["S. Ito / P2", "P3 / P4"]
    assert "teams" not in [m for m in rows if m["stem"] == "A"][0]     # singles rows unchanged


def test_doubles_row_teams_from_players_json_names_and_null_without_teams():
    data, ids = _store()
    players = [_dplayer("P1", name="Pip"), _dplayer("P2"), _dplayer("P3"), _dplayer("P4", team=None)]
    row = aggregate.overview([_dunit("D1", players=players)], data, range_="all")["matches"][0]
    assert row["teams"] == ["Pip / P2", "P3"]
    only_one = [_dplayer("P1"), _dplayer("P2")]
    row = aggregate.overview([_dunit("D1", players=only_one)], data, range_="all")["matches"][0]
    assert row["teams"] == ["P1 / P2", None]
    row = aggregate.overview([_dunit("D1", status="degraded_teams")], data, range_="all")["matches"][0]
    assert row["teams"] is None
    row = aggregate.overview([_dunit("D1", unavailable="doubles_error")], data,
                             range_="all")["matches"][0]
    assert row["teams"] is None
    data = _dmatch(data, "N1", {"P3": ids["tanaka"]})
    row = aggregate.overview([{"stem": "N1", "insights": None}], data, athlete=ids["tanaka"],
                             range_="all")["matches"][0]
    assert row["teams"] is None and set(row) == DOUBLES_ROW_KEYS


def test_position_distance_and_speed_skip_unknowns():
    data, ids = _store()
    data = _match(data, "A", upper=ids["tanaka"])
    data = _dmatch(data, "D1", {"P3": ids["tanaka"]})
    players = [_dplayer("P3", distance_m=40.0, avg_speed_mps=None)]
    units = [_unit("A", movement={"upper": (2, 3, 300, 100.0, 2.0)}),
             _dunit("D1", players=players)]
    value = aggregate.overview(units, data, athlete=ids["tanaka"],
                               range_="all")["movement"]["position"]["value"]
    assert value["distance_m"] == 140.0
    assert value["mean_speed_mps"] == 2.0          # never 140 / 50 = 2.8
    players = [_dplayer("P3", distance_m=None, avg_speed_mps=None)]
    value = aggregate.overview([_dunit("D1", players=players)], data, athlete=ids["tanaka"],
                               range_="all")["movement"]["position"]["value"]
    assert value["distance_m"] is None and value["mean_speed_mps"] is None
    assert value["samples"] == 100


def test_singles_position_with_unknown_speed_is_null_not_zero():
    data, _ = _store()
    doc = unit_doc("A", movement={"upper": (2, 3, 300, 100.0, 2.0)})
    doc["movement"]["value"]["upper"]["mean_speed_mps"] = None
    value = aggregate.overview([{"stem": "A", "insights": doc}], data,
                               range_="all")["movement"]["position"]["value"]
    assert value["distance_m"] == 100.0 and value["mean_speed_mps"] is None
    doc["movement"]["value"]["upper"]["distance_m"] = "junk"
    value = aggregate.overview([{"stem": "A", "insights": doc}], data,
                               range_="all")["movement"]["position"]["value"]
    assert value["distance_m"] is None and value["mean_speed_mps"] is None


def test_raw_store_with_one_athlete_on_two_identities_counts_once():
    data, ids = _store()
    data["matches"]["D1"] = {"date": None, "format": "doubles", "result": None,
                             "players": {"P1": {"athlete_id": ids["tanaka"]},
                                         "P3": {"athlete_id": ids["tanaka"]}}}
    ov = aggregate.overview([_dunit("D1")], data, athlete=ids["tanaka"], range_="all")
    assert ov["scope"]["units"] == 1
    assert [m["athlete_player"] for m in ov["matches"]] == ["P1"]
    assert ov["doubles"]["value"]["athlete"]["matches"] == 1


def test_athlete_summary_named_share_uses_the_rows_with_both_counts():
    data, ids = _store()
    data = _dmatch(data, "D1", {"P3": ids["tanaka"]})
    data = _dmatch(data, "D2", {"P3": ids["tanaka"]})
    units = [_dunit("D1", date="2026-10-01", players=[_dplayer("P3", hits=10, hits_named=4)]),
             _dunit("D2", date="2026-10-02",
                    players=[_dplayer("P3", hits=10, hits_named=None)])]
    s = aggregate.overview(units, data, athlete=ids["tanaka"],
                           range_="all")["doubles"]["value"]["athlete"]
    assert (s["hits"], s["hits_named"], s["hits_named_of"], s["named_share"]) == (20, 4, 10, 0.4)


def test_number_never_raises_on_huge_ints():
    from badminton_analysis.insights.loaders import number
    assert number(10 ** 400) is None and number(-(10 ** 400)) is None
    assert number(10 ** 6) == 1e6
    data, ids = _store()
    data = _dmatch(data, "D1", {"P3": ids["tanaka"]})
    players = [_dplayer("P3", hits=10 ** 400, frames=10 ** 400, distance_m=10 ** 400, samples=10 ** 400)]
    ov = aggregate.overview([_dunit("D1", players=players)], data, athlete=ids["tanaka"],
                            range_="all")
    json.dumps(ov, allow_nan=False)
    assert ov["doubles"]["value"]["athlete"]["hits"] is None

