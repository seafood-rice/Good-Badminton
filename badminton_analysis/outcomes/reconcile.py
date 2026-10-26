"""Reconcile claimed points with the recorded match result (spec 5.4).

The match is the primary record plus its linked parts; each part's points are
mapped into the primary's sides (exchanged when the part has ``sides_swapped``).
A one-set result is compared per side; more sets only by the point total (the
players change ends between sets, so a video's sides no longer name a player).
"""
from ..library import athletes as libathletes

SIDES = ("upper", "lower")


def match_parts(data, stem):
    """``[(stem, sides_swapped)]`` of the whole match ``stem`` belongs to, primary first.

    An unlinked video with a record is its own match; no record gives ``[]``.
    """
    record = libathletes.resolve_record(data, stem)
    if not record:
        return []
    primary = record.get("part_of") or stem
    parts = libathletes.parts_of(data, primary)
    return [(primary, False)] + [(p["stem"], p["sides_swapped"]) for p in parts]


def _expected(record):
    if libathletes.record_format(record) != "singles":
        return None, None
    result = record.get("result")
    sets = result.get("sets") if isinstance(result, dict) else None
    if not isinstance(sets, list) or not sets:
        return None, None
    try:
        upper = sum(int(s[0]) for s in sets)
        lower = sum(int(s[1]) for s in sets)
    except (TypeError, ValueError, IndexError):
        return None, None
    return {"upper": upper, "lower": lower}, ("sides" if len(sets) == 1 else "total")


def _no_result(parts):
    return {"status": "no_result", "scope": None, "expected": None, "claimed": None,
            "parts": [p for p, _ in parts]}


def reconcile(data, stem, part_docs):
    """The reconciliation block for ``stem``'s match; ``part_docs`` = ``{stem: doc|None}``."""
    parts = match_parts(data, stem)
    if not parts:
        return _no_result([(stem, False)])
    primary = parts[0][0]
    expected, scope = _expected(libathletes.resolve_record(data, primary))
    if expected is None:
        return _no_result(parts)
    claimed = {"upper": 0, "lower": 0}
    complete = True
    for part, swapped in parts:
        summary = (part_docs.get(part) or {}).get("summary")
        if not isinstance(summary, dict):
            complete = False
            continue
        won = summary.get("points_won") or {}
        up, low = int(won.get("upper") or 0), int(won.get("lower") or 0)
        if swapped:
            up, low = low, up
        claimed["upper"] += up
        claimed["lower"] += low
        if summary.get("labelled") != summary.get("entries"):
            complete = False
    if scope == "sides":
        over = any(claimed[s] > expected[s] for s in SIDES)
        equal = claimed == expected
        short = all(claimed[s] <= expected[s] for s in SIDES) and not equal
    else:
        total_c, total_e = sum(claimed.values()), sum(expected.values())
        over, equal, short = total_c > total_e, total_c == total_e, total_c < total_e
    if over:
        status = "over"
    elif not complete:
        status = "incomplete"
    elif equal:
        status = "consistent"
    else:
        status = "short" if short else "over"
    return {"status": status, "scope": scope, "expected": expected, "claimed": claimed,
            "parts": [p for p, _ in parts]}
