"""Reconciliation with the recorded result (badminton_analysis/outcomes/reconcile.py)."""
from badminton_analysis.outcomes.reconcile import match_parts, reconcile


def _data(matches):
    return {"schema_version": 1, "squads": {}, "athletes": {}, "matches": matches}


def _rec(sets, winner="upper"):
    return {"upper": {"name": "A"}, "lower": {"name": "B"}, "result": {"winner": winner, "sets": sets}}


def _doc(upper, lower, entries=None, labelled=None):
    total = upper + lower
    return {"summary": {"points_won": {"upper": upper, "lower": lower},
                        "entries": total if entries is None else entries,
                        "labelled": total if labelled is None else labelled}}


def test_single_video_statuses():
    data = _data({"v": _rec([[30, 13]])})
    assert reconcile(data, "v", {"v": _doc(30, 13)})["status"] == "consistent"
    assert reconcile(data, "v", {"v": _doc(31, 5)})["status"] == "over"
    assert reconcile(data, "v", {"v": _doc(20, 10)})["status"] == "short"
    r = reconcile(data, "v", {"v": _doc(20, 10, entries=40)})
    assert r["status"] == "incomplete" and r["scope"] == "sides"
    assert r["expected"] == {"upper": 30, "lower": 13} and r["claimed"] == {"upper": 20, "lower": 10}
    assert r["parts"] == ["v"]


def test_split_match_with_swapped_part():
    data = _data({"p": _rec([[30, 13]]), "q": {"part_of": "p", "sides_swapped": True}})
    assert match_parts(data, "q") == [("p", False), ("q", True)]
    # Part q's own upper is the primary's lower.
    docs = {"p": _doc(20, 8), "q": _doc(5, 10)}
    r = reconcile(data, "q", docs)
    assert r["claimed"] == {"upper": 30, "lower": 13} and r["status"] == "consistent"
    assert r["parts"] == ["p", "q"]


def test_missing_part_doc_is_incomplete():
    data = _data({"p": _rec([[30, 13]]), "q": {"part_of": "p", "sides_swapped": False}})
    assert reconcile(data, "p", {"p": _doc(20, 8), "q": None})["status"] == "incomplete"


def test_multi_set_compares_total():
    data = _data({"v": _rec([[21, 15], [18, 21], [21, 19]])})
    r = reconcile(data, "v", {"v": _doc(60, 55)})
    assert r["scope"] == "total" and r["status"] == "consistent"


def test_no_result_cases():
    assert reconcile(_data({}), "v", {"v": _doc(1, 1)})["status"] == "no_result"
    assert reconcile(_data({"v": {"upper": {"name": "A"}}}), "v", {})["status"] == "no_result"
    doubles = {"format": "doubles", "players": {}, "result": {"winner": 1, "sets": [[21, 10]]}}
    assert reconcile(_data({"v": doubles}), "v", {"v": _doc(1, 1)})["status"] == "no_result"


def test_malformed_part_summary_is_incomplete():
    data = _data({"p": _rec([[30, 13]]), "q": {"part_of": "p", "sides_swapped": False}})
    for won in ("abc", {"upper": "x", "lower": 1}, {"upper": [1], "lower": 1}, {"upper": True, "lower": 1}):
        bad = {"summary": {"points_won": won, "entries": 1, "labelled": 1}}
        assert reconcile(data, "p", {"p": _doc(20, 8), "q": bad})["status"] == "incomplete"


# ------------------------------------------------------------- scoring format

ONE_GAME = {"preset": "one_game_30"}


def _scored(sets, scoring=ONE_GAME, winner="upper"):
    rec = _rec(sets, winner)
    if scoring is not None:
        rec["scoring"] = scoring
    return rec


def test_a_result_impossible_under_the_format_is_no_result_scoring_mismatch():
    data = _data({"v": _scored([[31, 29]])})           # one_game_30: first to 30 wins, so 31-29 is impossible
    r = reconcile(data, "v", {"v": _doc(31, 29)})
    assert r["status"] == "no_result" and r["reason"] == "scoring_mismatch"
    assert r["expected"] is None and r["claimed"] is None and r["parts"] == ["v"]


def test_a_legal_result_under_the_format_reconciles_as_before():
    data = _data({"v": _scored([[30, 13]])})
    assert reconcile(data, "v", {"v": _doc(30, 13)})["status"] == "consistent"
    assert "reason" not in reconcile(data, "v", {"v": _doc(20, 10)})


def test_records_without_scoring_never_get_the_mismatch_check():
    data = _data({"v": _scored([[31, 29]], scoring=None)})
    r = reconcile(data, "v", {"v": _doc(31, 29)})
    assert r["status"] == "consistent" and "reason" not in r


def _rallies(*sides):
    return [{"winner_side": s} for s in sides]


def test_mid_game_end_change_maps_sides_per_rally():
    # Player "upper" leads 15-0 on video-upper, then the ends change: the video's lower side is now
    # player upper, who wins 15 more as video-lower.
    doc = {"summary": {"points_won": {"upper": 15, "lower": 15}, "entries": 30, "labelled": 30},
           "rallies": _rallies(*(["upper"] * 15 + ["lower"] * 15))}
    data = _data({"v": _scored([[30, 0]])})
    r = reconcile(data, "v", {"v": doc})
    assert r["claimed"] == {"upper": 30, "lower": 0} and r["status"] == "consistent"


def test_mid_game_change_resets_at_a_part_boundary_and_follows_the_swap_flag():
    first = {"summary": {"points_won": {"upper": 15, "lower": 0}, "entries": 15, "labelled": 15},
             "rallies": _rallies(*["upper"] * 15)}
    # The second part starts after the change; the owner marked it swapped.
    second = {"summary": {"points_won": {"upper": 0, "lower": 15}, "entries": 15, "labelled": 15},
              "rallies": _rallies(*["lower"] * 15)}
    data = _data({"p": _scored([[30, 0]]), "q": {"part_of": "p", "sides_swapped": True}})
    r = reconcile(data, "p", {"p": first, "q": second})
    assert r["claimed"] == {"upper": 30, "lower": 0}


def test_without_a_rally_list_the_summary_totals_are_used():
    data = _data({"v": _scored([[30, 13]])})
    assert reconcile(data, "v", {"v": _doc(30, 13)})["claimed"] == {"upper": 30, "lower": 13}
