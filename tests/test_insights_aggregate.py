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
             _unit("B", rally={"durations": [8.0], "shots": None})]
    ov = aggregate.overview(units, data, range_="all")
    kpi = ov["kpis"]["avg_rally"]
    assert kpi["status"] == "ok"
    assert kpi["value"] == {"seconds": 6.0, "shots": 5.0, "signal": "shuttle"}
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
    assert list(ov) == ["scope", "kpis", "radar", "trend", "movement", "matches", "findings"]
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
                                     "needs_review"}
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
