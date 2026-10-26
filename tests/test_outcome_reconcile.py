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
