"""Scoring format presets and rules (badminton_analysis/outcomes/scoring.py)."""
import pytest

from badminton_analysis.outcomes import scoring as sc

BWF, ONE, FIFT = (sc.PRESETS[n] for n in ("bwf_21x3", "one_game_30", "15x3"))


def test_presets_are_exactly_the_spec():
    assert sc.DEFAULT == "bwf_21x3"
    assert BWF == {"preset": "bwf_21x3", "points_to_win": 21, "win_by": 2, "cap": 30, "best_of": 3,
                   "change_ends_after_game": True, "change_ends_at": 11}
    assert (ONE["points_to_win"], ONE["win_by"], ONE["cap"], ONE["best_of"], ONE["change_ends_after_game"],
            ONE["change_ends_at"]) == (30, 1, None, 1, False, 15)
    assert (FIFT["points_to_win"], FIFT["win_by"], FIFT["cap"], FIFT["best_of"], FIFT["change_ends_at"]) \
        == (15, 2, 21, 3, 8)


@pytest.mark.parametrize("fmt,a,b,over", [
    (BWF, 21, 19, True), (BWF, 20, 20, False), (BWF, 21, 20, False), (BWF, 30, 29, True), (BWF, 29, 29, False),
    (BWF, 19, 21, True), (ONE, 30, 29, True), (ONE, 29, 29, False), (ONE, 30, 0, True), (ONE, 29, 5, False),
    (FIFT, 21, 20, True), (FIFT, 15, 13, True), (FIFT, 15, 14, False),
])
def test_game_over(fmt, a, b, over):
    assert sc.game_over(fmt, a, b) is over


def test_match_over():
    assert sc.match_over(BWF, 2, 0) and sc.match_over(BWF, 1, 2) and not sc.match_over(BWF, 1, 1)
    assert sc.match_over(ONE, 1, 0) and not sc.match_over(ONE, 0, 0)


def test_ends_change_after_game_and_in_deciding_game():
    assert sc.ends_change(BWF, 1, 21, 15, 20, 15) is True            # game finished
    assert sc.ends_change(BWF, 1, 11, 5, 10, 5) is False             # not the deciding game
    assert sc.ends_change(BWF, 3, 11, 5, 10, 5) is True              # leader reaches 11 in game 3
    assert sc.ends_change(BWF, 3, 11, 6, 11, 5) is False             # only the first time
    assert sc.ends_change(ONE, 1, 15, 9, 14, 9) is True
    assert sc.ends_change(ONE, 1, 14, 15, 14, 14) is True            # either side leads
    assert sc.ends_change(ONE, 1, 30, 20, 29, 20) is False           # no change after the game
    assert sc.ends_change(FIFT, 3, 3, 8, 3, 7) is True


def test_scoring_15x3_end_change_at_8_only_in_game_3():
    assert sc.ends_change(FIFT, 3, 8, 3, 7, 3) is True
    assert sc.ends_change(FIFT, 3, 9, 3, 8, 3) is False              # only on first reaching 8
    for game in (1, 2):
        assert sc.ends_change(FIFT, game, 8, 3, 7, 3) is False
    assert sc.ends_change(FIFT, 2, 15, 3, 14, 3) is True             # the game itself ended


def test_consistent():
    assert sc.consistent(BWF, [[21, 15], [18, 21], [21, 19]])
    assert sc.consistent(BWF, [[30, 29], [21, 0]])
    assert not sc.consistent(BWF, [[30, 13]])            # one game cannot decide best of 3
    assert not sc.consistent(BWF, [[22, 15], [21, 10]])  # 22-15: the game ended at 21
    assert not sc.consistent(BWF, [[31, 29], [21, 5]])
    assert not sc.consistent(BWF, [[21, 10], [21, 10], [21, 10]])   # a set after the match
    assert sc.consistent(ONE, [[30, 13]]) and sc.consistent(ONE, [[30, 29]])
    assert not sc.consistent(ONE, [[30, 28], [30, 1]])
    assert not sc.consistent(ONE, [[31, 29]]) and not sc.consistent(ONE, [[29, 29]])
    assert sc.consistent(BWF, [])


def test_validate_ranges_and_fill():
    clean, err = sc.validate({"preset": "one_game_30"})
    assert err is None and clean == ONE
    clean, err = sc.validate({"preset": "custom", "points_to_win": 11, "win_by": 1, "cap": None, "best_of": 1,
                              "change_ends_after_game": False, "change_ends_at": 6})
    assert err is None and clean["points_to_win"] == 11
    assert sc.validate(None) == (None, None)
    bad = [{"points_to_win": 4}, {"points_to_win": 51}, {"points_to_win": True}, {"win_by": 3},
           {"cap": 20}, {"best_of": 2}, {"best_of": 7}, {"change_ends_at": 0}, {"change_ends_at": 21},
           {"change_ends_after_game": 1}, {"preset": "nope"}, {"extra": 1}]
    for patch in bad:
        assert sc.validate({"preset": "bwf_21x3", **patch}) == (None, "invalid_scoring"), patch
    assert sc.validate("x") == (None, "invalid_scoring")
    assert sc.validate({"preset": "bwf_21x3", "cap": None})[0]["cap"] is None


def test_resolve_defaults_and_fills():
    assert sc.resolve({}) == BWF and sc.resolve(None) == BWF
    assert sc.resolve({"scoring": {"preset": "one_game_30"}}) == ONE
    assert sc.resolve({"scoring": "garbage"}) == BWF
    sc.resolve({})["cap"] = 1
    assert sc.PRESETS["bwf_21x3"]["cap"] == 30
