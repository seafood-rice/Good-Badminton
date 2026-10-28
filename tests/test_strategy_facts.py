"""The strategy facts pack (badminton_analysis/strategy/facts.py)."""
import json

import pytest

from badminton_analysis.strategy.facts import (INPUT_FILES, build_facts, current_inputs,
                                               fact_map)
from tests.insights_fixtures import (doubles_hit, doubles_tracklet, make_doubles_run,
                                     make_run)

STORE = {"athletes": {"a1": {"name": "Val"}}, "matches": {}}
SINGLES_RECORD = {"upper": {"name": "Sally"}, "lower": {"athlete_id": "a1"},
                  "result": {"winner": "lower", "sets": [[4, 30]]}}
STROKES = [("upper", "clear", 10), ("lower", "smash", 20), ("upper", "lift", 200),
           ("lower", "smash", 230)]


def singles(tmp_path, **kw):
    kw.setdefault("rallies", [(0, 5), (6, 20)])
    kw.setdefault("strokes", STROKES)
    kw.setdefault("frames", 700)
    return make_run(tmp_path, **kw)


def codes(pack):
    return [c["code"] for c in pack["caveats"]]


def test_singles_focus_is_registered_side(tmp_path):
    pack = build_facts(singles(tmp_path), SINGLES_RECORD, STORE)
    assert pack["format"] == "singles"
    assert pack["focus"] == {"side": "lower", "team": None, "athlete_id": "a1",
                             "assumed": False}
    assert pack["header"]["you"] == "Val" and pack["header"]["opponent"] == "Sally"
    assert pack["header"]["winner"] == "you"
    f = fact_map(pack)
    assert f["match.result"]["value"] == {"winner": "you", "sets": [[30, 4]]}
    assert f["you.mix.attack_share"]["value"] == 1.0
    assert f["opponent.mix.defence_share"]["value"] == 1.0
    assert f["you.seq2_top"]["value"][0] == {"seq": ["lift", "smash"], "n": 1}
    assert f["you.after_lift"]["value"] == {"smash": 1}
    assert f["you.last_stroke"]["value"] == {"smash": 2}
    assert f["you.zone.rear"]["status"] == "ok"
    assert f["match.rallies"]["value"] == 2


def test_facts_are_well_formed(tmp_path):
    pack = build_facts(singles(tmp_path), SINGLES_RECORD, STORE)
    ids = [f["id"] for f in pack["facts"]]
    assert len(ids) == len(set(ids))
    for fact in pack["facts"]:
        assert set(fact) - {"defs"} == {"id", "value", "unit", "who", "status", "reason", "validated"}
        assert fact["status"] in ("ok", "partial", "insufficient", "unavailable")
        if fact["status"] == "unavailable":
            assert fact["value"] is None and fact["reason"]
    json.dumps(pack)                                  # serialisable


def test_focus_override_and_assumed(tmp_path):
    out = singles(tmp_path)
    pack = build_facts(out, SINGLES_RECORD, STORE, focus="upper")
    assert pack["focus"]["side"] == "upper" and pack["focus"]["assumed"] is False
    assert fact_map(pack)["match.result"]["value"]["winner"] == "opponent"
    bare = build_facts(out, {}, STORE)
    assert bare["focus"] == {"side": "lower", "team": None, "athlete_id": None,
                             "assumed": True}
    assert "focus_assumed" in codes(bare)
    assert "focus_assumed" not in codes(pack)


def test_both_registered_prefers_lower(tmp_path):
    store = {"athletes": {"a1": {"name": "Val"}, "a2": {"name": "Wen"}}, "matches": {}}
    rec = {"upper": {"athlete_id": "a2"}, "lower": {"athlete_id": "a1"}}
    pack = build_facts(singles(tmp_path), rec, store)
    assert pack["focus"]["side"] == "lower" and pack["header"]["both_registered"] is True


def test_doubles_has_no_stroke_facts(tmp_path):
    out = make_doubles_run(
        tmp_path, frames=60,
        tracklets=[doubles_tracklet(1, [(f, 1.0, 8.0) for f in range(60)]),   # P1 behind
                   doubles_tracklet(2, [(f, 1.5, 11.0) for f in range(60)]),  # P2 front... same x
                   doubles_tracklet(3, [(f, 1.0, 2.0) for f in range(60)]),
                   doubles_tracklet(4, [(f, 5.0, 2.5) for f in range(60)])],
        hits=[doubles_hit(10, "P1"), doubles_hit(20, "P1"), doubles_hit(30, "P2")])
    rec = {"format": "doubles", "players": {"P1": {"athlete_id": "a1"}, "P3": {"name": "Z"}},
           "result": {"winner_team": 1, "sets": [[21, 10]]}}
    pack = build_facts(out, rec, STORE)
    f = fact_map(pack)
    assert pack["format"] == "doubles"
    assert pack["focus"]["team"] == 1 and pack["focus"]["side"] is None
    assert not [i for i in f if i.startswith(("you.mix", "opponent.mix", "you.seq2"))]
    assert f["team.you.formation.front_back_share"]["value"] == 1.0
    assert f["team.opponent.formation.front_back_share"]["value"] == 0.0
    assert "doubles_strokes_withheld" in codes(pack) and "doubles_hits_unvalidated" in codes(pack)
    assert f["you.P1.hit_share"]["value"] == round(2 / 3, 3)
    assert f["you.P1.hit_share"]["validated"] is False
    assert f["you.P1.distance_m"]["status"] == "ok"
    assert f["you.P1.zone.front"]["status"] in ("ok", "unavailable")
    assert f["match.result"]["value"] == {"winner": "you", "sets": [[21, 10]]}
    assert pack["header"]["you"] == "Val"


def test_doubles_uncovered_and_team_two_focus(tmp_path):
    out = make_doubles_run(tmp_path, frames=60)
    rec = {"format": "doubles", "players": {"P3": {"athlete_id": "a1"}},
           "result": {"winner_team": 1, "sets": [[21, 10]]}}
    pack = build_facts(out, rec, STORE)
    f = fact_map(pack)
    assert pack["focus"]["team"] == 2
    assert f["match.result"]["value"] == {"winner": "opponent", "sets": [[10, 21]]}
    assert isinstance(f["team.you.uncovered"]["value"], list)
    assert len(f["team.you.uncovered"]["value"]) <= 3


def test_no_rally_outcomes_caveat(tmp_path):
    pack = build_facts(singles(tmp_path), SINGLES_RECORD, STORE)
    assert not [f for f in pack["facts"] if f["id"].endswith(".points_won")]
    assert "no_rally_outcomes" in codes(pack)


def _outcomes(out, known, total=4, reconciliation="consistent", schema=1):
    """A ``rally_outcomes.json`` in the rally outcome spec's schema 1 (section 5.2)."""
    rallies = [{"id": i + 1, "segment_id": i + 1, "start_sec": 10.0 * i, "end_sec": 10.0 * i + 5,
                "winner_side": "lower" if i < known else None,
                "outcome": None, "ending": "in" if i < known else None,
                "ending_stroke": {"type": "smash", "group": "smash", "hitter": "lower",
                                  "frame": 100 * i, "confidence": 0.9},
                "confidence": 0.8, "source": "auto", "reviewed": False, "evidence": None}
               for i in range(total)]
    summary = {"entries": total, "labelled": known, "coverage": known / total,
               "points_won": {"upper": 0, "lower": known},
               "reconciliation": {"status": reconciliation, "scope": "sides"}}
    (out / "rally_outcomes.json").write_text(
        json.dumps({"schema": schema, "rallies": rallies, "summary": summary}), encoding="utf-8")


def test_rally_outcomes_endings_and_unknown_outcome(tmp_path):
    out = singles(tmp_path)
    _outcomes(out, known=4)
    f = fact_map(build_facts(out, SINGLES_RECORD, STORE))
    assert f["you.won_ending"]["value"] == {"in": 4}
    assert f["opponent.lost_by"]["value"] == {"unknown": 4}


def test_rally_outcomes_unreconciled_caveat(tmp_path):
    out = singles(tmp_path)
    _outcomes(out, known=4, reconciliation="incomplete")
    pack = build_facts(out, SINGLES_RECORD, STORE)
    assert {"code": "outcomes_unreconciled", "params": {"status": "incomplete"}} in pack["caveats"]


def test_rally_outcomes_other_schema_is_absent(tmp_path):
    out = singles(tmp_path)
    _outcomes(out, known=4, schema=2)
    assert "no_rally_outcomes" in codes(build_facts(out, SINGLES_RECORD, STORE))


def test_rally_outcomes_partial(tmp_path):
    out = singles(tmp_path)
    _outcomes(out, known=1)
    pack = build_facts(out, SINGLES_RECORD, STORE)
    f = fact_map(pack)
    assert f["you.points_won"]["status"] == "partial" and f["you.points_won"]["value"] == 1
    assert f["match.outcome_coverage"]["value"] == 0.25
    assert "no_rally_outcomes" not in codes(pack)


def test_rally_outcomes_full(tmp_path):
    out = singles(tmp_path)
    _outcomes(out, known=4)
    f = fact_map(build_facts(out, SINGLES_RECORD, STORE))
    assert f["you.points_won"]["status"] == "ok" and f["you.points_won"]["value"] == 4
    assert f["you.won_by"]["value"] == {"smash": 4}
    assert f["opponent.points_won"]["value"] == 0


def test_malformed_rally_outcomes_is_absent(tmp_path):
    out = singles(tmp_path)
    (out / "rally_outcomes.json").write_text("{nope", encoding="utf-8")
    pack = build_facts(out, SINGLES_RECORD, STORE)
    assert "no_rally_outcomes" in codes(pack)


def test_empty_run_dir(tmp_path):
    out = tmp_path / "outputs" / "empty"
    out.mkdir(parents=True)
    pack = build_facts(out, {}, STORE)
    f = fact_map(pack)
    assert f["match.rallies"]["status"] == "unavailable" and f["match.rallies"]["reason"]
    assert pack["header"]["confidence"] == "low"
    assert "missing" in codes(pack)
    assert all(fct["status"] == "unavailable" for fct in pack["facts"]
               if fct["id"].startswith(("match.rally", "you.mix")))


def test_missing_dir_and_bad_arguments_do_not_raise(tmp_path):
    pack = build_facts(tmp_path / "nope", None, None)
    assert pack["format"] == "singles" and pack["header"]["confidence"] == "low"
    build_facts(str(tmp_path / "nope"), "junk", {"athletes": 5}, focus="sideways")


def test_confidence_levels(tmp_path):
    degraded = singles(tmp_path, stem="d", rallies=[
        {"start_sec": 0, "end_sec": 5, "degraded": True}, (6, 20)])
    pack = build_facts(degraded, SINGLES_RECORD, STORE)
    assert fact_map(pack)["match.degraded_share"]["value"] == 0.5
    assert pack["header"]["confidence"] == "low"
    assert any(c["code"] == "degraded_rallies" and c["params"] == {"share": 0.5}
               for c in pack["caveats"])
    assert build_facts(singles(tmp_path, stem="h"), SINGLES_RECORD,
                       STORE)["header"]["confidence"] == "high"
    mixed = [("upper", "clear", 10), ("lower", "smash", 20), ("upper", "uncertain", 30),
             ("lower", "uncertain", 40), ("lower", "smash", 50)]
    assert build_facts(singles(tmp_path, stem="m", strokes=mixed), SINGLES_RECORD,
                       STORE)["header"]["confidence"] == "medium"
    few = [("upper", "uncertain", 10), ("lower", "smash", 20), ("upper", "uncertain", 30)]
    assert build_facts(singles(tmp_path, stem="l", strokes=few), SINGLES_RECORD,
                       STORE)["header"]["confidence"] == "low"


def test_swing_signal_withholds_rally_count(tmp_path):
    out = singles(tmp_path, signal="swing")
    pack = build_facts(out, SINGLES_RECORD, STORE)
    f = fact_map(pack)
    assert f["match.rallies"]["status"] == "unavailable"
    assert f["match.rally_median_sec"]["status"] == "partial"
    assert any(c["code"] == "rally_signal" and c["params"] == {"signal": "swing"}
               for c in pack["caveats"])


def test_inputs_record_hash_changes_with_result(tmp_path):
    out = singles(tmp_path)
    a = build_facts(out, SINGLES_RECORD, STORE)["inputs"]
    other = dict(SINGLES_RECORD, result={"winner": "upper", "sets": [[30, 4]]})
    b = build_facts(out, other, STORE)["inputs"]
    assert a["record"] != b["record"]
    assert a["strokes.json"] == b["strokes.json"] > 0
    assert a == current_inputs(out, SINGLES_RECORD)
    assert set(INPUT_FILES) <= set(a)


def test_short_long_share(tmp_path):
    counts = [2, 4, 7, 5, 11]
    rallies = [(10 * i, 10 * i + 5) for i in range(5)]
    hits = [(10 * i + 0.5 + 0.1 * k, "lower" if k % 2 else "upper")
            for i, n in enumerate(counts) for k in range(n)]
    out = make_run(tmp_path, rallies=rallies, hits=hits, frames=0)
    f = fact_map(build_facts(out, SINGLES_RECORD, STORE))
    assert f["match.short_rally_share"]["value"] == 0.6
    assert f["match.long_rally_share"]["value"] == 0.2
    assert f["match.rally_median_shots"]["value"] == 5
    assert f["match.rally_median_sec"]["value"] == 5.0
    assert f["match.tempo_shots_per_sec"]["value"] == round(29 / 25, 3)


def test_technique_weak_needs_three_reports(tmp_path):
    reports = [("smash", "lower", 50)] * 3 + [("clear", "lower", 60)] * 3 + [
        ("drop", "lower", 10)]
    out = singles(tmp_path, reports=reports)
    f = fact_map(build_facts(out, SINGLES_RECORD, STORE))
    assert f["you.technique_weak"]["value"] == ["smash", "clear"]
    assert f["you.technique_index"]["value"] is not None


def test_swapped_part_sets_oriented(tmp_path):
    from badminton_analysis.library import athletes as athlete_store
    store = {"athletes": {"a1": {"name": "Val"}}, "matches": {
        "m1": {"upper": {"athlete_id": "a1"}, "lower": {"name": "Sally"},
               "result": {"winner": "upper", "sets": [[21, 15]]}},
        "m2": {"part_of": "m1", "sides_swapped": True},
        "m3": {"part_of": "m1", "sides_swapped": False}}}
    out = singles(tmp_path)
    swapped = athlete_store.resolve_record(store, "m2")
    pack = build_facts(out, swapped, store)
    assert pack["focus"]["side"] == "lower"
    assert pack["header"]["sets"] == [[21, 15]] and pack["header"]["winner"] == "you"
    assert fact_map(pack)["match.result"]["value"] == {"winner": "you", "sets": [[21, 15]]}
    plain = build_facts(out, athlete_store.resolve_record(store, "m3"), store)
    assert plain["focus"]["side"] == "upper"
    assert plain["header"]["sets"] == [[21, 15]] and plain["header"]["winner"] == "you"


def test_rally_definition_defs(tmp_path):
    rallies = [(10 * i, 10 * i + 5) for i in range(3)]
    hits = [(10 * i + 0.5 + 0.1 * k, "lower" if k % 2 else "upper")
            for i, n in enumerate([2, 7, 11]) for k in range(n)]
    out = make_run(tmp_path, rallies=rallies, hits=hits, frames=0)
    f = fact_map(build_facts(out, SINGLES_RECORD, STORE))
    assert f["match.short_rally_share"]["defs"] == [5]
    assert f["match.long_rally_share"]["defs"] == [10]
    assert f["match.rally_p90_sec"]["defs"] == [90]


def test_doubles_focus_assumed_names_the_team(tmp_path):
    out = make_doubles_run(
        tmp_path, frames=60,
        tracklets=[doubles_tracklet(i, [(f, float(i), 2.0 * i) for f in range(60)])
                   for i in (1, 2, 3, 4)],
        hits=[doubles_hit(10, "P1")])
    pack = build_facts(out, {"format": "doubles"}, STORE)
    assert pack["focus"]["assumed"] is True and pack["focus"]["team"] == 1
    assert "focus_assumed_team" in codes(pack) and "focus_assumed" not in codes(pack)


# ---------------------------------------------------------------- rally tactics (plan task 8)

FPS = 30.0


def tactic_run(tmp_path, n=6, *, winner="lower", owner=True, same_hitter=False, positions=None):
    """``n`` rallies of serve (lower), lift (upper), smash (lower); the lower side wins them."""
    rallies = [(10 * i, 10 * i + 8) for i in range(n)]
    strokes = []
    for i in range(n):
        for k, (hitter, stroke) in enumerate((("lower", "long_serve"), ("upper", "lift"),
                                              ("lower", "smash"))):
            if same_hitter:
                hitter = "lower"
            strokes.append({"frame": round((10 * i + 1 + k) * FPS), "hitter": hitter,
                            "stroke": stroke, "group": "serve" if "serve" in stroke else stroke,
                            "confidence": 0.9, "source": "owner" if owner else "model"})
    out = make_run(tmp_path, rallies=rallies, strokes=strokes, frames=int(10 * n * FPS),
                   positions=positions)
    if winner:
        outs = [{"id": i + 1, "segment_id": i + 1, "start_sec": 10.0 * i, "end_sec": 10.0 * i + 8,
                 "winner_side": winner, "source": "owner" if owner else "auto"}
                for i in range(n)]
        (out / "rally_outcomes.json").write_text(
            json.dumps({"schema": 1, "rallies": outs, "summary": {"coverage": 1.0}}),
            encoding="utf-8")
    return out


@pytest.fixture
def no_weights(tmp_path, monkeypatch):
    from badminton_analysis.strategy import facts
    monkeypatch.setattr(facts, "WEIGHTS_DIR", tmp_path / "no_weights")


def test_tactics_facts_singles(tmp_path, no_weights):
    pack = build_facts(tactic_run(tmp_path), SINGLES_RECORD, STORE)
    f = fact_map(pack)
    assert f["match.active_play_sec"]["value"] == 48.0 and f["match.active_play_sec"]["unit"] == "s"
    assert f["match.rallies_under_9s_share"]["value"] == 1.0
    assert f["match.rallies_under_9s_share"]["defs"] == [9]
    assert f["match.shots_per_rally"]["value"] == 3.0
    assert f["match.owner_labelled_share"]["value"] == 1.0
    smash = f["you.eff.smash"]
    assert smash["status"] == "ok" and smash["value"]["n"] == 6
    assert set(smash["value"]) == {"effective", "neutral", "ineffective", "unknown", "n"}
    assert smash["value"]["effective"] == 6
    assert f["opponent.eff.lift"]["value"]["n"] == 6
    assert f["match.openings.complete"]["value"] == 6
    assert f["match.openings.complete"]["defs"] == [3]
    assert f["match.openings.complete"]["status"] == "ok"
    assert f["match.openings.total"]["value"] == 6
    assert f["you.openings.won"]["value"] + f["opponent.openings.won"]["value"] <= 6
    assert set(f["you.opening_win_point"]["value"]) == {"n", "won"}
    assert f["match.stroke_model"]["status"] == "unavailable"
    assert "owner_labelled" in codes(pack) and "openings_incomplete" not in codes(pack)
    tables = pack["tables"]
    assert tables["effectiveness"]["you"]["smash"]["n"] == 6
    assert tables["effectiveness"]["opponent"]["lift"]["n"] == 6
    op = tables["openings"]
    assert op["complete"] == 6 and op["insufficient"] is False
    assert {r["advantage"] for r in op["rows"]} <= {"you", "opponent", "neutral"}
    assert all(s["hitter"] in ("you", "opponent") for r in op["rows"] for s in r["shots"])
    json.dumps(pack)


def test_tactics_insufficient_rules(tmp_path, no_weights):
    pack = build_facts(tactic_run(tmp_path, n=2), SINGLES_RECORD, STORE)
    f = fact_map(pack)
    smash = f["you.eff.smash"]
    assert smash["status"] == "insufficient" and smash["reason"] == "few_attempts"
    assert smash["value"]["n"] == 2
    for fid in ("match.openings.complete", "match.openings.total", "you.openings.won",
                "opponent.openings.won", "you.opening_win_point"):
        assert f[fid]["status"] == "insufficient" and f[fid]["reason"] == "few_openings"
    assert {"code": "openings_incomplete", "params": {"complete": 2, "total": 2}} in pack["caveats"]
    assert pack["tables"]["openings"]["insufficient"] is True


EMPTY_TABLES = {"effectiveness": {"you": {}, "opponent": {}},
                "openings": {"complete": 0, "total": 0, "rows": [],
                             "won_by": {"you": 0, "opponent": 0},
                             "opening_winner_won_point": {"n": 0, "won": 0},
                             "neutral": 0, "insufficient": True}}


def test_tactics_nothing_usable_never_raises(tmp_path, no_weights):
    out = tactic_run(tmp_path, n=3, winner=None, owner=False, same_hitter=True)
    pack = build_facts(out, SINGLES_RECORD, STORE)
    f = fact_map(pack)
    assert f["match.openings.complete"]["value"] == 0
    assert f["match.openings.complete"]["status"] == "insufficient"
    assert f["you.eff.smash"]["status"] == "insufficient"
    assert f["you.eff.smash"]["value"]["unknown"] == 3
    assert pack["tables"]["openings"]["rows"] == []
    assert f["match.owner_labelled_share"]["value"] == 0.0
    assert "owner_labelled" not in codes(pack) and "openings_incomplete" in codes(pack)
    for bad in (build_facts(tmp_path / "nope", None, None),
                build_facts(make_run(tmp_path, stem="bare"), {}, STORE)):
        assert bad["tables"] == EMPTY_TABLES
        assert fact_map(bad).get("match.active_play_sec", {"status": "unavailable"})[
            "status"] == "unavailable"


def test_tactics_positions_unusable_caveat(tmp_path, no_weights):
    out = tactic_run(tmp_path, positions={"upper": (0.1, 3.0), "lower": (3.0, 10.0)})
    pack = build_facts(out, SINGLES_RECORD, STORE)
    assert {"code": "positions_unusable", "params": {"sides": ["opponent"]}} in pack["caveats"]


def _history(tmp_path, monkeypatch, history):
    from badminton_analysis.strategy import facts
    weights = tmp_path / "w"
    weights.mkdir()
    if history is not None:
        (weights / "stroke-model-history.json").write_text(json.dumps(history), encoding="utf-8")
    monkeypatch.setattr(facts, "WEIGHTS_DIR", weights)


def _live(version, accuracy, bar_met):
    return {"version": version, "accuracy": accuracy, "unknown_rate": 0.1, "bar_met": bar_met}


def test_stroke_model_fact_from_history(tmp_path, monkeypatch):
    history = [{"kept": True, "utc": "a", "live_after": _live("v1", 0.5, False)},
               {"kept": True, "utc": "b", "live_after": _live("v2", 0.81, True)}]
    _history(tmp_path, monkeypatch, history)
    pack = build_facts(tactic_run(tmp_path), SINGLES_RECORD, STORE)
    fact = fact_map(pack)["match.stroke_model"]
    assert fact["status"] == "ok" and fact["value"] == {"accuracy": 0.81, "bar_met": True}
    assert {"code": "stroke_model", "params": {"accuracy": 0.81, "bar_met": True}} in pack["caveats"]


def test_stroke_model_fact_after_rollback_uses_newest_live_after(tmp_path, monkeypatch):
    history = [{"kept": True, "utc": "a", "live_after": _live("v1", 0.6, False)},
               {"kept": True, "utc": "b", "live_after": _live("v2", 0.9, True)},
               {"kept": True, "rollback": True, "utc": "c", "live_after": _live("v1", 0.6, False)}]
    _history(tmp_path, monkeypatch, history)
    fact = fact_map(build_facts(tactic_run(tmp_path), SINGLES_RECORD, STORE))["match.stroke_model"]
    assert fact["value"] == {"accuracy": 0.6, "bar_met": False}


@pytest.mark.parametrize("history", [None, [], [{"kept": True, "candidate": {"accuracy": 0.9}}]])
def test_stroke_model_fact_unavailable_without_live_after(tmp_path, monkeypatch, history):
    _history(tmp_path, monkeypatch, history)
    pack = build_facts(tactic_run(tmp_path), SINGLES_RECORD, STORE)
    assert fact_map(pack)["match.stroke_model"]["status"] == "unavailable"
    assert "stroke_model" not in codes(pack)


def test_opening_win_point_differs_per_side(tmp_path, no_weights):
    # lower opens well in 4 rallies and wins 3; upper has the advantage in 2 and wins 1
    n = 6
    rallies = [(10 * i, 10 * i + 8) for i in range(n)]
    strokes = []
    for i in range(n):
        if i < 4:
            trio = (("lower", "long_serve"), ("upper", "lift"), ("lower", "smash"))
        else:
            trio = (("lower", "long_serve"), ("upper", "smash"), ("lower", "lift"))
        for k, (hitter, stroke) in enumerate(trio):
            strokes.append({"frame": round((10 * i + 1 + k) * FPS), "hitter": hitter,
                            "stroke": stroke, "group": "serve" if "serve" in stroke else stroke,
                            "confidence": 0.9})
    out = make_run(tmp_path, rallies=rallies, strokes=strokes, frames=int(10 * n * FPS))
    winners = ["lower", "lower", "lower", "upper", "upper", "lower"]
    outs = [{"id": i + 1, "segment_id": i + 1, "start_sec": 10.0 * i, "end_sec": 10.0 * i + 8,
             "winner_side": w, "source": "owner"} for i, w in enumerate(winners)]
    (out / "rally_outcomes.json").write_text(json.dumps({"schema": 1, "rallies": outs}),
                                            encoding="utf-8")
    f = fact_map(build_facts(out, SINGLES_RECORD, STORE))
    assert f["you.opening_win_point"]["value"] == {"n": 4, "won": 3}
    assert f["opponent.opening_win_point"]["value"] == {"n": 2, "won": 1}


def test_doubles_tactics_are_unavailable_or_empty(tmp_path, no_weights):
    out = make_doubles_run(tmp_path, frames=60)
    pack = build_facts(out, {"format": "doubles"}, STORE)
    f = fact_map(pack)
    assert not [i for i in f if ".eff." in i]
    assert f["match.openings.complete"]["status"] in ("insufficient", "unavailable")
    assert pack["tables"]["effectiveness"] == {"you": {}, "opponent": {}}


def test_doubles_without_any_known_hitter_gets_the_hitter_unknown_caveat(tmp_path, no_weights):
    from tests.test_strategy_shots import SIDE_STROKES, doubles_dir
    pack = build_facts(doubles_dir(tmp_path / "a", SIDE_STROKES), {"format": "doubles"}, STORE)
    assert {"code": "hitter_unknown", "params": {}} in pack["caveats"]
    hits = [{"frame": 100, "hitter_id": "P1", "hitter_team": 1, "hitter_evidence": "shuttle"}]
    known = build_facts(doubles_dir(tmp_path / "b", SIDE_STROKES, hits), {"format": "doubles"}, STORE)
    assert "hitter_unknown" not in codes(known)                     # one zone is enough
    singles = build_facts(tactic_run(tmp_path / "c"), SINGLES_RECORD, STORE)
    assert "hitter_unknown" not in codes(singles)
