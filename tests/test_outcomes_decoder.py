"""Score-sequence winner decoder (badminton_analysis/outcomes/decoder.py)."""
import copy
import json

import pytest

from badminton_analysis.outcomes import decoder as dec
from badminton_analysis.outcomes import scoring
from badminton_analysis.outcomes.decoder import Obs
from badminton_analysis.outcomes.run_inputs import Inputs

# 5-point games, win by 2, cap 7, best of 3, ends change after each game
TINY, _ = scoring.validate({"preset": "custom", "points_to_win": 5, "win_by": 2, "cap": 7, "best_of": 3,
                            "change_ends_after_game": True, "change_ends_at": 3})
TINY_NOCHANGE, _ = scoring.validate({"preset": "custom", "points_to_win": 5, "win_by": 2, "cap": 7,
                                     "best_of": 3, "change_ends_after_game": True, "change_ends_at": None})
ONE_GAME = scoring.PRESETS["one_game_30"]


def neutral():
    return dec.fit([])


def pts(n, p_point=1.0):
    return [Obs(p_point=p_point) for _ in range(n)]


def winners(result):
    return [max(dec.SIDES, key=lambda s: p[s]) if max(p["upper"], p["lower"]) > p["none"] else None
            for p in result["p"]]


# ------------------------------------------------------------------ transitions

def test_bwf_game_end_deuce_cap_and_end_change():
    f = scoring.PRESETS["bwf_21x3"]
    assert dec._advance(f, None, 1, 20, 19, 0, 0) == (2, 0, 0, 0, 1, 1)        # 21-19: game over, ends change
    assert dec._advance(f, None, 1, 20, 20, 0, 0) == (1, 21, 20, 0, 0, 0)       # deuce, play on
    assert dec._advance(f, None, 1, 29, 29, 0, 1) == (2, 0, 0, 1, 1, 0)         # cap: 29-30 ends the game
    assert dec._advance(f, None, 3, 10, 5, 0, 0) == (3, 11, 5, 0, 1, 0)         # change at 11 in game 3
    assert dec._advance(f, None, 2, 10, 5, 0, 0) == (2, 11, 5, 0, 0, 0)         # not in game 2
    assert dec._advance(f, None, 3, 20, 9, 1, 0) == dec.END                  # the deciding game ends the match


def test_one_game_and_15x3_transitions():
    assert dec._advance(ONE_GAME, None, 1, 14, 3, 0, 0) == (1, 15, 3, 0, 1, 0)  # change at 15
    assert dec._advance(ONE_GAME, None, 1, 15, 3, 1, 0) == (1, 16, 3, 0, 1, 0)  # only once
    assert dec._advance(ONE_GAME, None, 1, 29, 3, 0, 0) == dec.END           # first to 30, win by 1
    g15 = scoring.PRESETS["15x3"]
    assert dec._advance(g15, None, 3, 7, 2, 0, 0) == (3, 8, 2, 0, 1, 0)
    assert dec._advance(g15, None, 1, 14, 14, 0, 0) == (1, 15, 14, 0, 0, 0)     # win by 2
    assert dec._advance(g15, None, 1, 20, 20, 0, 0) == (2, 0, 0, 0, 1, 1)       # cap 21


def test_final_forbids_wrong_game_end():
    assert dec._advance(TINY, [(5, 3)], 1, 4, 3, 0, 0) == dec.END
    assert dec._advance(TINY, [(5, 3)], 1, 4, 3, 0, 1) is None               # b would pass 3
    assert dec._advance(TINY, [(5, 3)], 1, 4, 2, 0, 0) is None               # 5-2 ends the game early


def test_best_of_3_two_nil_has_no_third_game():
    f = scoring.PRESETS["bwf_21x3"]
    assert dec._advance(f, None, 2, 20, 5, 0, 0, 1) == dec.END               # 2-0 in games: match over
    assert dec._advance(f, None, 2, 5, 20, 0, 1, 1) == (3, 0, 0, 1, 1, 1)    # 1-1: a third game
    # 5-0, 5-0 for the same player (upper, then lower after the change): the match is over
    clamps = {i: "upper" if i < 5 else "lower" for i in range(10)}
    res = dec.decode(neutral(), TINY_NOCHANGE, pts(11), clamps=clamps, start="zero")
    assert res["start"] == "zero" and res["p"][10]["none"] == 1.0
    assert list(res["end"]) == [dec.END]
    # the same winner twice in a row but a game won by each: a third game follows
    split = {i: "upper" for i in range(10)}
    res = dec.decode(neutral(), TINY_NOCHANGE, pts(11), clamps=split, start="zero")
    assert res["p"][10]["none"] < 1.0 or dec.END not in res["end"]


UNCAPPED = scoring.validate({"preset": "custom", "points_to_win": 5, "win_by": 2, "cap": None, "best_of": 3,
                             "change_ends_after_game": True, "change_ends_at": 3})[0]


def test_decode_uncapped_win_by_2_terminates():
    import time
    t0 = time.time()
    res = dec.decode(neutral(), UNCAPPED, pts(12), start="zero")
    free = dec.decode(neutral(), UNCAPPED, pts(6), start="free")
    assert time.time() - t0 < 1.0
    assert len(res["p"]) == 12 and len(free["p"]) == 6
    # deuce classes keep the parity of the score: 6-6 and 4-4 are one state, 5-5 another
    assert dec._fold(UNCAPPED, 7, 6) == (5, 4) and dec._fold(UNCAPPED, 6, 6) == (4, 4)
    assert dec._fold(UNCAPPED, 5, 5) == (5, 5) and dec._fold(UNCAPPED, 4, 3) == (4, 3)


# ---------------------------------------------------------------------- decode

def test_final_constraint_and_end_change_flip_sides():
    # sets 5-0 and 5-0 for player a, who changes ends after game 1: upper wins game 1, lower game 2
    res = dec.decode(neutral(), TINY_NOCHANGE, pts(10), start="zero", final=[(5, 0), (5, 0)])
    assert res["final_open"] is False
    assert winners(res) == ["upper"] * 5 + ["lower"] * 5
    assert all(max(p.values()) > 0.99 for p in res["p"])
    swapped = dec.decode(neutral(), TINY_NOCHANGE, pts(10), start="zero", final=[(5, 0), (5, 0)], swapped=True)
    assert winners(swapped) == ["lower"] * 5 + ["upper"] * 5


def test_mid_game_end_change_flips_sides():
    # one game, a wins 5-0 with a change at 3: a is upper for points 1-3, lower after
    one = dict(TINY, best_of=1)
    res = dec.decode(neutral(), one, pts(5), start="zero", final=[(5, 0)])
    assert winners(res) == ["upper"] * 3 + ["lower"] * 2


def test_clamps_force_steps_and_propagate():
    one = dict(TINY_NOCHANGE, best_of=1)
    clamps = {0: "upper", 1: "lower", 2: "upper", 3: "lower", 4: "upper", 5: "lower", 6: "upper"}
    res = dec.decode(neutral(), one, pts(8, p_point=0.5), clamps=clamps, start="zero", final=[(5, 3)])
    assert winners(res)[:7] == [clamps[i] for i in range(7)]
    assert res["p"][0] == {"upper": 1.0, "lower": 0.0, "none": 0.0}
    assert res["p"][7]["upper"] > 0.99          # only a point by a reaches 5-3


def test_not_point_steps_keep_state():
    res = dec.decode(neutral(), TINY, pts(3), clamps={0: "not_point", 1: "not_point", 2: "not_point"})
    assert res["end"] == {dec.START_ZERO: pytest.approx(1.0)}
    assert all(p["none"] == 1.0 for p in res["p"])
    low = dec.decode(neutral(), TINY, pts(2, p_point=0.0))      # prior 1 - p_point
    assert all(p["none"] > 0.99 for p in low["p"])


def test_free_start_does_not_force_zero():
    clamps = {0: "upper", 1: "upper"}
    zero = dec.decode(neutral(), TINY_NOCHANGE, pts(2), clamps=clamps, start="zero")
    free = dec.decode(neutral(), TINY_NOCHANGE, pts(2), clamps=clamps, start="free")
    assert {(g, a + b) for g, a, b, _s, _f, _ga in zero["end"]} == {(1, 2)}
    assert any(g > 1 or a + b > 2 for g, a, b, _s, _f, _ga in free["end"])
    assert len(free["end"]) > len(zero["end"])
    assert free["start"] == "free" and zero["start"] == "zero"


def test_final_open_on_mismatch_and_without_result():
    short = dec.decode(neutral(), TINY_NOCHANGE, pts(4), start="zero", final=[(5, 0), (5, 0)])
    assert short["final_open"] is True          # 4 rallies cannot reach 10 points: end left open, no crash
    assert len(short["p"]) == 4
    off = dec.decode(neutral(), TINY_NOCHANGE, pts(10), start="zero", final=None)
    assert off["final_open"] is True
    on = dec.decode(neutral(), TINY_NOCHANGE, pts(10), start="zero", final=[(5, 0), (5, 0)])
    assert on["final_open"] is False


def test_impossible_clamps_report_only_clamps():
    one = dict(TINY_NOCHANGE, best_of=1)
    clamps = {i: "upper" for i in range(7)}      # 5-0 ends the only game; no point can follow
    res = dec.decode(neutral(), one, pts(7), clamps=clamps)
    assert res["final_open"] is True and res["p"][6]["upper"] == 1.0 and res["start"] == "none"


def _serve_sequence(side_winners, invert):
    """Observations whose serve courts follow the score parity (optionally inverted)."""
    fmt = TINY_NOCHANGE
    g, a, b, fl, server, ga = 1, 0, 0, 0, None, 0
    obs, clamps = [], {}
    for i, w in enumerate(side_winners):
        court = None
        if server is not None:
            right = (a if server == 0 else b) % 2 == 0
            court = "right" if right != invert else "left"
        obs.append(Obs(serve_court=court, p_point=1.0))
        clamps[i] = w
        state = dec._advance(fmt, None, g, a, b, fl, dec.SIDES.index(w) ^ fl, ga)
        g, a, b, server, fl, ga = state
    return obs, clamps


@pytest.mark.parametrize("invert", [False, True])
def test_mirror_choice_on_inverted_parity(invert):
    wins = ["upper", "lower", "lower", "upper", "upper", "upper", "lower"]
    obs, clamps = _serve_sequence(wins, invert)
    model = copy.deepcopy(neutral())
    model["parity"]["reliability"] = 0.9
    res = dec.decode(model, TINY_NOCHANGE, obs, clamps=clamps, start="zero")
    assert res["mirror"] is invert


def test_claims_only_above_threshold():
    result = {"p": [{"upper": 0.95, "lower": 0.03, "none": 0.02}, {"upper": 0.2, "lower": 0.7, "none": 0.1},
                    {"upper": 0.0, "lower": 0.0, "none": 1.0}]}
    assert dec.claims(result, 0.9) == ["upper", None, None]
    assert dec.claims(result, 0.6) == ["upper", "lower", None]


def test_carried_start_distribution():
    first = dec.decode(neutral(), TINY_NOCHANGE, pts(3), clamps={0: "upper", 1: "upper", 2: "lower"})
    assert {(g, a + b) for g, a, b, _s, _f, _ga in first["end"]} == {(1, 3)}
    second = dec.decode(neutral(), TINY_NOCHANGE, pts(1), clamps={0: "upper"}, start=first["end"])
    assert second["start"] == "carried"
    assert {(g, a + b) for g, a, b, _s, _f, _ga in second["end"]} == {(1, 4)}


# ------------------------------------------------------------------- fit / load

def test_fit_tables_and_parity():
    rows = [(Obs("upper", "upper", "upper", None), "upper")] * 3 + [(Obs("lower", None, None, None), "upper")] \
        + [(Obs("upper", "lower", None, None), "not_point")] + [(None, "lower")]
    m = dec.fit(rows)
    t = m["signals"]["landing"]["point"]
    assert t["agree"] == pytest.approx(4 / 7) and t["disagree"] == pytest.approx(2 / 7)
    assert m["signals"]["landing"]["none"]["present"] == pytest.approx(2 / 3)
    assert sum(m["signals"]["next_server"]["point"].values()) == pytest.approx(1.0)
    assert m["counts"] == {"points": 5, "not_points": 1} and m["claim_threshold"] == 0.9
    obs, clamps = _serve_sequence(["upper", "lower", "lower", "upper", "upper", "upper", "lower"], True)
    fitted = dec.fit([(o, clamps[i]) for i, o in enumerate(obs)], fmt=TINY_NOCHANGE)
    assert fitted["parity"]["n"] == 6 and fitted["parity"]["reliability"] == pytest.approx(7 / 8)


def test_fit_parity_uses_each_videos_own_format():
    short = scoring.validate({"preset": "custom", "points_to_win": 5, "win_by": 1, "cap": 5, "best_of": 3,
                              "change_ends_after_game": False, "change_ends_at": None})[0]
    long_ = dict(short, points_to_win=7, cap=7)

    def serve_seq(fmt):
        g, a, b, fl, server, ga = 1, 0, 0, 0, None, 0
        out = []
        for w in ["upper"] * 5 + ["lower", "upper", "lower"]:
            court = None if server is None else ("right" if (a if server == 0 else b) % 2 == 0 else "left")
            out.append((Obs(serve_court=court), w))
            g, a, b, server, fl, ga = dec._advance(fmt, None, g, a, b, fl, dec.SIDES.index(w) ^ fl, ga)
        return out

    seq = serve_seq(short)
    own = dec.fit([seq], fmts=[short])["parity"]["reliability"]
    wrong = dec.fit([seq], fmt=long_)["parity"]["reliability"]
    assert own > wrong
    other = dec.fit([seq, serve_seq(long_)], fmts=[short, long_])["parity"]["reliability"]
    assert other > dec.fit([seq, serve_seq(long_)], fmt=short)["parity"]["reliability"]


def test_load_roundtrip_and_invalid(tmp_path):
    path = tmp_path / "rally-decoder.json"
    assert dec.load(path) is None
    path.write_text(json.dumps(dec.fit([])), encoding="utf-8")
    assert dec.load(path)["kind"] == "rally-decoder"
    path.write_text("{not json", encoding="utf-8")
    assert dec.load(path) is None
    bad = dec.fit([])
    bad["parity"]["reliability"] = 3
    path.write_text(json.dumps(bad), encoding="utf-8")
    assert dec.load(path) is None


# ------------------------------------------------------------------ observations

def _inputs(positions, ok=True, hits=((10.0, 600, "lower", None),)):
    return Inputs(fps=60.0, hits=list(hits), positions=positions, swing=[], shuttle_frames=set(),
                  positions_ok={"upper": ok, "lower": ok}, quad=None)


def test_observations_signals_and_serve_court():
    segs = [{"id": 1, "start_sec": 9.0, "end_sec": 14.0, "p_point": 0.4},
            {"id": 2, "start_sec": 20.0, "end_sec": 25.0}]
    auto = {1: {"evidence": {"landing": {"winner": "lower"}, "last_hitter": "upper", "next_server": "bogus"}}}
    inputs = _inputs({"lower": {598: (1.0, 12.0)}, "upper": {}})
    o1, o2 = dec.observations(segs, inputs, auto)
    assert (o1.landing, o1.last_hitter, o1.next_server) == ("lower", "upper", None)
    assert o1.serve_court == "left" and o1.p_point == 0.4        # nearest position; lower: small x is left
    assert o2 == Obs()                                            # no evidence, no hit, default p_point
    upper = _inputs({"upper": {600: (1.0, 1.0)}}, hits=((10.0, 600, "upper", None),))
    assert dec.observations(segs[:1], upper, {})[0].serve_court == "right"   # mirrored for the upper side
    assert dec.observations(segs[:1], _inputs({"lower": {600: (1.0, 12.0)}}, ok=False), {})[0].serve_court is None
    assert dec.observations(segs[:1], _inputs({"lower": {600: (3.05, 12.0)}}), {})[0].serve_court is None
    assert dec.observations(segs[:1], None, {})[0].serve_court is None


def test_free_start_covers_every_folded_deuce_state():
    """Review 3: an uncapped win-by-2 game also starts at the folded advantage scores (p+1, p) and (p, p+1)."""
    ab = {(a, b) for g, a, b, _s, _f, _ga in dec._free_states(UNCAPPED, None) if g == 1}
    assert (6, 5) in ab and (5, 6) in ab and (4, 4) in ab and (5, 5) in ab
    assert dec._fold(UNCAPPED, 8, 7) in ab and dec._fold(UNCAPPED, 7, 8) in ab     # a decode starting at 8-7
    assert all(dec._fold(UNCAPPED, a, b) == (a, b) for a, b in ab)
    free = dec.decode(neutral(), UNCAPPED, pts(4), start="free")
    assert len(free["p"]) == 4


def test_a_deuce_end_carries_into_a_later_part_with_a_final_constraint():
    """Review 6: part 1 ends 6-6 (folded to 4-4); part 2 decodes unfolded and must still continue from it."""
    one = dict(UNCAPPED, best_of=1, change_ends_after_game=False, change_ends_at=None)
    alternate = {i: ("upper" if i % 2 == 0 else "lower") for i in range(12)}
    first = dec.decode(neutral(), one, pts(12), clamps=alternate, start="zero")
    assert {(a, b) for _g, a, b, _s, _f, _ga in first["end"]} == {(4, 4)}        # 6-6, folded
    # part 2: 7-6, 7-7, 8-7, 9-7: the game ends 9-7, which only 6-6 can reach in 4 points
    clamps = {0: "upper", 1: "lower", 2: "upper", 3: "upper"}
    second = dec.decode(neutral(), one, pts(4), clamps=clamps, start=first["end"], final=[(9, 7)])
    assert second["start"] == "carried" and second["final_open"] is False
    assert winners(second) == ["upper", "lower", "upper", "upper"]
    # a later part that cannot be reached from 6-6 / 4-4 at all still falls back as before
    nope = dec.decode(neutral(), one, pts(4), clamps=clamps, start=first["end"], final=[(5, 0)])
    assert nope["start"] != "carried" or nope["final_open"] is True
