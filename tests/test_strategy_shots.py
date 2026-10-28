"""The per-shot table, effectiveness tally and openings (badminton_analysis/strategy/shots.py)."""
import json

import pytest

from badminton_analysis.strategy import shots as S
from badminton_analysis.strategy.shots import (build_shots, effectiveness, openings, overview,
                                               positions_usable, zone_of)
from tests.insights_fixtures import make_doubles_run

FPS = 100.0
SEG_SPAN = 20.0


def default_pos(side, frame):
    return (3.0, 4.0) if side == "upper" else (3.0, 9.5)


def make(tmp_path, rallies, *, pos=default_pos, outcomes=None, name="run", positions=True,
         fps=FPS):
    """A synthetic result dir.

    ``rallies``: list of ``(shot list, winner)``; a shot is ``(time_sec, hitter, stroke)`` with
    absolute times. Segment ``i + 1`` spans ``[i * 20, i * 20 + 19]``. ``outcomes`` overrides the
    outcomes rallies (list of dicts)."""
    out = tmp_path / name
    out.mkdir(parents=True, exist_ok=True)
    segs, strokes, outs = [], [], []
    for i, (shots, winner) in enumerate(rallies):
        start, end = i * SEG_SPAN, i * SEG_SPAN + SEG_SPAN - 1
        segs.append({"id": i + 1, "start_frame": int(start * fps), "end_frame": int(end * fps),
                     "start_sec": start, "end_sec": end, "degraded": False})
        for t, hitter, stroke in shots:
            strokes.append({"frame": round(t * fps), "hitter": hitter, "stroke": stroke,
                            "group": "serve" if stroke.endswith("serve") else "x",
                            "confidence": 0.9})
        if winner:
            outs.append({"segment_id": i + 1, "start_sec": start, "end_sec": end,
                         "winner_side": winner, "source": "owner"})
    (out / "rally_segments.json").write_text(json.dumps({"fps": fps, "rallies": segs}))
    (out / "strokes.json").write_text(json.dumps({"schema": 2, "strokes": strokes}))
    if outcomes is None:
        outcomes = outs
    if outcomes:
        (out / "rally_outcomes.json").write_text(
            json.dumps({"schema": 1, "rallies": outcomes}))
    if positions:
        lines = []
        for f in range(0, int(len(rallies) * SEG_SPAN * fps), 5):
            row = {"frame": f, "players": {s: {"court": list(pos(s, f))} for s in S.SIDES}}
            lines.append(json.dumps(row))
        (out / "detections.jsonl").write_text("\n".join(lines) + "\n")
    return out


def seq(start, pairs, step=1.0):
    """Shots at ``start + k * step``; ``pairs`` is ``[(hitter, stroke), ...]``."""
    return [(start + k * step, h, s) for k, (h, s) in enumerate(pairs)]


L, U = "lower", "upper"


def eff(tmp_path, pairs, winner, side=L, pos=default_pos, name=None):
    out = make(tmp_path, [(seq(1.0, pairs), winner)], pos=pos, name=name or "e")
    return effectiveness(build_shots(out), side)


# ------------------------------------------------------------------ zones and tempo

def test_zone_mirrored_per_side():
    assert zone_of(L, 1.0, 9.5) == {"depth": "mid", "side": "left"}
    assert zone_of(L, 5.5, 9.5)["side"] == "right"
    assert zone_of(U, 1.0, 3.9) == {"depth": "mid", "side": "right"}
    assert zone_of(U, 5.5, 3.9)["side"] == "left"
    assert zone_of(U, 3.0, 3.9)["side"] == "centre"
    assert zone_of(L, 0.0, 9.5)["side"] == "left" and zone_of(L, 6.1, 9.5)["side"] == "right"


def test_zone_depth_bands_from_net():
    assert zone_of(L, 3, 6.7 + 1.98)["depth"] == "front"
    assert zone_of(L, 3, 6.7 + 2.0)["depth"] == "mid"
    assert zone_of(L, 3, 6.7 + 4.7)["depth"] == "mid"
    assert zone_of(L, 3, 6.7 + 4.8)["depth"] == "rear"
    assert zone_of(U, 3, 6.7 - 1.9)["depth"] == "front"
    assert zone_of(U, 3, 6.7 - 4.8)["depth"] == "rear"


def test_tempo_bands(tmp_path):
    times = [1.0, 1.69, 2.39, 3.89, 5.40]
    shots = [(t, L if i % 2 == 0 else U, "clear") for i, t in enumerate(times)]
    table = build_shots(make(tmp_path, [(shots, None)]))
    assert [s["tempo"] for s in table["shots"]] == [None, "fast", "normal", "normal", "slow"]
    assert [s["tempo_sec"] for s in table["shots"]][1:] == [0.69, 0.7, 1.5, 1.51]
    assert table["fps"] == FPS and table["rallies"][0]["n_shots"] == 5


def test_shot_fields_and_zones(tmp_path):
    table = build_shots(make(tmp_path, [(seq(1.0, [(L, "long_serve"), (U, "lift")]), L)]))
    a, b = table["shots"]
    assert (a["rally"], a["index"], a["frame"], a["time"]) == (1, 0, 100, 1.0)
    assert a["hitter"] == L and a["stroke"] == "long_serve" and a["source"] == "model"
    assert a["zone"] == {"depth": "mid", "side": "centre"} and a["sent_to"] == b["zone"]
    assert b["zone"] == {"depth": "mid", "side": "centre"} and b["sent_to"] is None
    assert a["last"] is False and b["last"] is True and b["tempo_sec"] == 1.0
    assert table["positions_ok"] == {"upper": True, "lower": True}
    assert table["rallies"] == [{"id": 1, "winner_side": L, "n_shots": 2,
                                 "complete_opening": False, "owner_labelled": True}]


def test_owner_source_kept(tmp_path):
    out = make(tmp_path, [(seq(1.0, [(L, "smash")]), None)])
    doc = json.loads((out / "strokes.json").read_text())
    doc["strokes"][0]["source"] = "owner"
    (out / "strokes.json").write_text(json.dumps(doc))
    assert build_shots(out)["shots"][0]["source"] == "owner"


# ------------------------------------------------------------------ rushed and broken links

def moving_pos(side, frame):
    if side == "upper":
        return (1.0, 4.0) if frame < 150 else (4.0, 4.0)
    return (3.0, 9.5)


def test_rushed_reply(tmp_path):
    fast = build_shots(make(tmp_path, [([(1.0, L, "drive"), (1.9, U, "block")], None)],
                            pos=moving_pos, name="a"))
    assert fast["shots"][0]["rushed_reply"] is True
    slow = build_shots(make(tmp_path, [([(1.0, L, "drive"), (2.3, U, "block")], None)],
                            pos=moving_pos, name="b"))
    assert slow["shots"][0]["rushed_reply"] is False       # 1.3 s is not within 1.2 s
    still = build_shots(make(tmp_path, [([(1.0, L, "drive"), (1.9, U, "block")], None)],
                             name="c"))
    assert still["shots"][0]["rushed_reply"] is False      # moved 0 m
    assert still["shots"][1]["rushed_reply"] is None       # no next shot


def test_broken_link_same_hitter_and_long_gap(tmp_path):
    table = build_shots(make(tmp_path, [([(1.0, L, "clear"), (2.0, L, "clear"),
                                          (3.0, U, "clear"), (5.6, L, "clear"),
                                          (6.5, U, "clear")], None)]))
    assert [s["link_broken"] for s in table["shots"]] == [True, False, True, False, False]
    assert table["shots"][0]["sent_to"] is None and table["shots"][0]["rushed_reply"] is None
    assert table["shots"][1]["sent_to"] is not None


def test_gap_of_exactly_limit_is_not_broken(tmp_path):
    table = build_shots(make(tmp_path, [([(1.0, L, "clear"), (3.5, U, "clear")], None)]))
    assert table["shots"][0]["link_broken"] is False


# ------------------------------------------------------------------ position check

def test_positions_usable_rule():
    good = [(3.0, 4.0)] * 10
    edge = [(0.1, 4.0)] * 4 + [(3.0, 4.0)] * 6
    assert positions_usable({"upper": good, "lower": []}) == {"upper": True, "lower": False}
    assert positions_usable({"upper": edge, "lower": [(0.1, 10.0)] * 3 + [(3.0, 10.0)] * 7}) == \
        {"upper": True, "lower": True}                    # exactly / below 40 %
    assert positions_usable({"upper": [(0.1, 4.0)] * 5 + good[:4], "lower": [(3.0, 10.0)] * 10}) == \
        {"upper": False, "lower": True}
    assert positions_usable({"upper": [(3.0, 6.6)] * 10, "lower": [(3.0, 6.8)] * 10}) == \
        {"upper": False, "lower": False}                  # on the net
    assert positions_usable({}) == {"upper": False, "lower": False}


def test_position_check_fails_like_0004(tmp_path):
    def bad(side, frame):
        return (-0.27, 4.0) if side == "upper" else (3.0, 6.8)    # outside the line / on the net
    table = build_shots(make(tmp_path, [(seq(1.0, [(L, "smash"), (U, "clear"), (L, "lift")]),
                                         L)], pos=bad))
    assert table["positions_ok"] == {"upper": False, "lower": False}
    assert all(s["zone"] is None and s["sent_to"] is None for s in table["shots"])
    assert all(s["rushed_reply"] is None for s in table["shots"])
    assert len(table["shots"]) == 3 and table["shots"][0]["tempo"] is None


def test_no_detections_means_no_positions(tmp_path):
    table = build_shots(make(tmp_path, [(seq(1.0, [(L, "smash")]), L)], positions=False))
    assert table["positions_ok"] == {"upper": False, "lower": False}
    assert table["shots"][0]["zone"] is None


# ------------------------------------------------------------------ effectiveness

def test_effective_winner_on_this_shot(tmp_path):
    e = eff(tmp_path, [(U, "clear"), (L, "smash")], L)
    assert e["smash"] == {"effective": 1, "neutral": 0, "ineffective": 0, "unknown": 0, "n": 1}


def test_effective_winner_on_hitters_next_shot(tmp_path):
    e = eff(tmp_path, [(L, "clear"), (U, "net_shot"), (L, "smash")], L)
    assert e["clear"]["effective"] == 1 and e["smash"]["effective"] == 1
    other = effectiveness(build_shots(tmp_path / "e"), U)
    assert other["net_shot"]["ineffective"] == 1       # the opponent's next shot ended it for L


def test_effective_defensive_reply(tmp_path):
    def pos(side, frame):
        return (3.0, 1.9) if side == "upper" else (3.0, 9.5)   # the far player is at the rear
    e = eff(tmp_path, [(L, "smash"), (U, "lift"), (L, "clear"), (U, "clear")], None, pos=pos)
    assert e["smash"]["effective"] == 1                # forced a rear lift
    assert e["clear"]["effective"] == 1                # forced a rear clear from the far player


def test_only_a_clear_needs_the_rear_zone(tmp_path):
    e = eff(tmp_path, [(L, "smash"), (U, "lift"), (L, "drop"), (U, "clear"), (L, "push")], None)
    assert e["smash"]["effective"] == 1                # a lift is defensive from any zone
    assert e["drop"]["neutral"] == 1                   # a clear from the mid zone is not


def test_effective_rushed_reply(tmp_path):
    e = eff(tmp_path, [(L, "drive"), (U, "block"), (L, "smash"), (U, "clear")], None,
            pos=moving_pos)
    assert e["drive"]["effective"] == 1


def test_ineffective_lost_last_shot(tmp_path):
    e = eff(tmp_path, [(U, "clear"), (L, "clear")], U)
    assert e["clear"]["ineffective"] == 1


def test_ineffective_opponents_next_shot_wins(tmp_path):
    e = eff(tmp_path, [(L, "smash"), (U, "smash")], U)
    assert e["smash"]["ineffective"] == 1


def test_neutral_and_unknown(tmp_path):
    e = eff(tmp_path, [(L, "clear"), (U, "clear"), (L, "clear"), (U, "clear")], None, name="n")
    assert e["clear"] == {"effective": 0, "neutral": 2, "ineffective": 0, "unknown": 0, "n": 2}
    e = eff(tmp_path, [(L, "clear"), (L, "lift"), (U, "clear")], None, name="b")
    assert e["clear"]["unknown"] == 1                  # broken link, no rally-end evidence
    assert e["lift"]["neutral"] == 1


def test_broken_link_with_rally_win_still_unknown_unless_last(tmp_path):
    e = eff(tmp_path, [(L, "clear"), (L, "lift")], L, name="w")
    assert e["clear"]["unknown"] == 1 and e["lift"]["effective"] == 1


def test_effectiveness_ignores_unknown_stroke_and_other_side(tmp_path):
    e = eff(tmp_path, [(L, "unknown"), (U, "clear")], L)
    assert e == {}


# ------------------------------------------------------------------ openings

def opening(start, first=(L, "long_serve"), second=(U, "clear"), third=(L, "clear"), step=1.0):
    return seq(start, [first, second, third], step)


def test_opening_complete_and_incomplete(tmp_path):
    rallies = [
        (opening(1.0), L),                                              # complete
        (opening(21.0, first=(L, "clear")), L),                         # not a serve
        (opening(41.0, second=(L, "clear")), L),                        # same hitter
        (opening(61.0, third=(U, "clear")), L),                         # not alternating
        (opening(81.0, step=2.6), L),                                   # broken links
        (seq(101.0, [(L, "long_serve"), (U, "clear")]), L),             # too short
    ]
    table = build_shots(make(tmp_path, rallies))
    assert [r["complete_opening"] for r in table["rallies"]] == [True, False, False, False,
                                                                 False, False]
    o = openings(table)
    assert (o["complete"], o["total"], len(o["rows"])) == (1, 6, 1)
    row = o["rows"][0]
    assert row["rally"] == 1 and row["point_winner"] == L
    assert [s["stroke"] for s in row["shots"]] == ["long_serve", "clear", "clear"]
    assert set(row["shots"][0]) == {"hitter", "stroke", "zone", "sent_to"}


def advantage(tmp_path, second, third, pos=default_pos):
    table = build_shots(make(tmp_path, [(opening(1.0, second=second, third=third), None)],
                             pos=pos, name="adv"))
    o = openings(table)
    assert o["complete"] == 1
    return o["rows"][0]["advantage"], o


def test_advantage_rule(tmp_path):
    assert advantage(tmp_path, (U, "clear"), (L, "smash"))[0] == L      # attack by the server
    assert advantage(tmp_path, (U, "smash"), (L, "lift"))[0] == U       # attack by the receiver
    assert advantage(tmp_path, (U, "clear"), (L, "clear"))[0] == "neutral"
    adv, o = advantage(tmp_path, (U, "smash"), (L, "smash"))            # one each
    assert adv == "neutral" and o["neutral"] == 1 and o["won_by"] == {U: 0, L: 0}


def test_advantage_from_forced_defensive_reply(tmp_path):
    def pos(side, frame):
        return (3.0, 1.9) if side == "upper" else (3.0, 9.5)
    adv, o = advantage(tmp_path, (U, "lift"), (L, "clear"), pos=pos)
    assert adv == L and o["won_by"][L] == 1


def test_advantage_from_rushed_reply(tmp_path):
    adv, _ = advantage(tmp_path, (U, "block"), (L, "clear"), pos=moving_pos)
    assert adv == L        # the serve made the receiver move 3 m within 1 s


def test_opening_winner_won_point_and_won_by(tmp_path):
    rallies = [(opening(1.0, third=(L, "smash")), L),
               (opening(21.0, third=(L, "smash")), U),
               (opening(41.0, second=(U, "smash"), third=(L, "lift")), U)]
    o = openings(build_shots(make(tmp_path, rallies)))
    assert o["won_by"] == {U: 1, L: 2}
    assert o["opening_winner_won_point"] == {"n": 3, "won": 2}


def test_rows_capped_at_eight_and_ordered(tmp_path):
    rallies = []
    for i in range(12):
        start = i * 20 + 1.0
        shots = opening(start)
        if i % 2:
            shots += seq(start + 3.0, [(U, "clear"), (L, "clear")] * (i // 2 + 1))
        rallies.append((shots, L))
    out = make(tmp_path, rallies)
    # make the shortest rally (segment 1) the only one the owner labelled; others become auto
    doc = json.loads((out / "rally_outcomes.json").read_text())
    for r in doc["rallies"]:
        r["source"] = "owner" if r["segment_id"] == 1 else "auto"
    (out / "rally_outcomes.json").write_text(json.dumps(doc))
    o = openings(build_shots(out))
    assert o["complete"] == 12 and len(o["rows"]) == 8
    ids = [r["rally"] for r in o["rows"]]
    assert ids[0] == 1                                   # owner-labelled first
    lengths = [r["n_shots"] for r in o["rows"][1:]]
    assert lengths == sorted(lengths, reverse=True)      # then the longest


def test_rows_unlabelled_winner_last(tmp_path):
    rallies = [(opening(1.0), None), (opening(21.0), L)]
    o = openings(build_shots(make(tmp_path, rallies)))
    assert [r["rally"] for r in o["rows"]] == [2, 1]


# ------------------------------------------------------------------ outcomes mapping, overview

def test_outcome_mapped_by_time_overlap_without_segment_id(tmp_path):
    out = make(tmp_path, [(seq(1.0, [(L, "smash")]), None), (seq(21.0, [(L, "smash")]), None)],
               outcomes=[{"start_sec": 21.5, "end_sec": 30.0, "winner_side": U, "source": "auto"},
                         {"segment_id": 99, "start_sec": 2.0, "end_sec": 5.0,
                          "winner_side": L, "source": "owner"},
                         {"segment_id": 2, "start_sec": 21.0, "end_sec": 22.0,
                          "winner_side": None}])
    rallies = build_shots(out)["rallies"]
    assert [r["winner_side"] for r in rallies] == [L, U]
    assert [r["owner_labelled"] for r in rallies] == [True, False]


def test_overview(tmp_path):
    rallies = [(seq(t, [(L, "smash")]), None) for t in (1.0, 21.0, 41.0)]
    out = make(tmp_path, rallies)
    doc = json.loads((out / "rally_segments.json").read_text())
    for r, (a, b) in zip(doc["rallies"], [(0.0, 4.0), (20.0, 32.0), (40.0, 46.0)]):
        r["start_sec"], r["end_sec"] = a, b
    (out / "rally_segments.json").write_text(json.dumps(doc))
    table = build_shots(out)
    table["rallies"][0]["n_shots"] = 2
    ov = overview(table, out)
    assert ov["active_play_sec"] == 22.0
    assert ov["rallies_under_9s_share"] == pytest.approx(0.667, abs=1e-3)
    assert ov["shots_per_rally"] == pytest.approx(1.33, abs=1e-2)
    assert ov["shots_source"] == "detected"
    table["shots"][0]["source"] = "owner"
    assert overview(table, out)["shots_source"] == "labelled"


# ------------------------------------------------------------------ nothing usable (Review Focus 5)

def test_no_winners_all_broken_returns_zeros(tmp_path):
    shots = seq(1.0, [(L, "clear")] * 4)
    out = make(tmp_path, [(shots, None)] * 2, positions=False)
    table = build_shots(out)
    assert all(s["link_broken"] for s in table["shots"][:3])
    assert effectiveness(table, U) == {}
    assert effectiveness(table, L) == {"clear": {"effective": 0, "neutral": 0, "ineffective": 0,
                                                 "unknown": 8, "n": 8}}
    o = openings(table)
    assert (o["complete"], o["rows"], o["won_by"], o["neutral"]) == (0, [], {U: 0, L: 0}, 0)
    assert o["opening_winner_won_point"] == {"n": 0, "won": 0}
    assert overview(table, out)["shots_per_rally"] == 4.0


@pytest.mark.parametrize("junk", ["", "[]", "{", '{"schema": 9}'])
def test_junk_files_never_raise(tmp_path, junk):
    out = make(tmp_path, [(seq(1.0, [(L, "smash"), (U, "clear")]), L)])
    for name in ("rally_outcomes.json", "detections.jsonl", "strokes.json"):
        (out / name).write_text(junk)
    table = build_shots(out)
    assert table["shots"] == []
    assert openings(table)["complete"] == 0 and effectiveness(table, L) == {}
    overview(table, out)


def test_empty_directory(tmp_path):
    table = build_shots(tmp_path)
    assert table == {"shots": [], "rallies": [], "positions_ok": {"upper": False, "lower": False},
                     "fps": None}
    assert openings(table)["total"] == 0 and overview(table, tmp_path)["active_play_sec"] is None


def test_strokes_outside_any_rally_dropped(tmp_path):
    out = make(tmp_path, [(seq(1.0, [(L, "smash")]) + [(19.4, U, "clear"), (50.0, L, "lift")],
                           None)])
    assert [s["stroke"] for s in build_shots(out)["shots"]] == ["smash", "clear"]


# ------------------------------------------------------------------ doubles

def test_doubles_hitter_ids_map_to_team_sides(tmp_path):
    out = make_doubles_run(tmp_path, fps=FPS, frames=2000)
    segs = [{"id": 1, "start_frame": 0, "end_frame": 1500, "start_sec": 0.0, "end_sec": 15.0}]
    (out / "rally_segments.json").write_text(json.dumps({"fps": FPS, "rallies": segs}))
    strokes = [{"frame": 100, "hitter": "P1", "stroke": "long_serve", "group": "serve"},
               {"frame": 200, "hitter": "P3", "stroke": "clear", "group": "x"},
               {"frame": 300, "hitter": "P2", "stroke": "smash", "group": "x"}]
    (out / "strokes.json").write_text(json.dumps({"schema": 2, "strokes": strokes}))
    table = build_shots(out)
    assert [s["hitter"] for s in table["shots"]] == [L, U, L]        # team 1 lower, team 2 upper
    assert table["positions_ok"] == {"upper": True, "lower": True}
    assert table["shots"][0]["zone"] == zone_of(L, 1.0, 8.0)         # P1's own position
    assert table["rallies"][0]["complete_opening"] is True
