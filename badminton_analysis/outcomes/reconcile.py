"""Reconcile claimed points with the recorded match result (spec 5.4).

The match is the primary record plus its linked parts; each part's points are
mapped into the primary's sides (exchanged when the part has ``sides_swapped``).
A one-set result is compared per side; more sets only by the point total (the
players change ends between sets, so a video's sides no longer name a player).

A record with a ``scoring`` format (outcomes/scoring.py) adds two things: a result
impossible under the format makes the status ``no_result`` with reason
``scoring_mismatch``; and in a one-game match with a mid-game end change
(``change_ends_at``) the rallies of the part holding the change are mapped to the
players per rally (the sides swap once the leader first reaches that score).
Records without ``scoring`` behave exactly as before.
"""
from ..library import athletes as libathletes
from . import scoring

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


def _count(value):
    """A non-negative point count, or None (booleans and non-ints are not counts)."""
    return value if type(value) is int and value >= 0 else None


def _no_result(parts, reason=None):
    out = {"status": "no_result", "scope": None, "expected": None, "claimed": None,
           "parts": [p for p, _ in parts]}
    if reason:
        out["reason"] = reason
    return out


def _claimed_per_rally(fmt, parts, part_docs):
    """``{upper, lower}`` points won by the primary's sides, mapping every rally of a
    part through the mid-game end change; None when a part has no rally list.

    Within a part the sides swap once the leader first reaches ``change_ends_at``
    (the swap resets at each part boundary: a part's own ``sides_swapped`` already
    says how it lines up). Rallies without a winner do not move the score.
    """
    claimed = {"upper": 0, "lower": 0}
    score = [0, 0]
    for part, swapped in parts:
        rallies = (part_docs.get(part) or {}).get("rallies")
        if not isinstance(rallies, list):
            return None
        flipped = False
        for rally in rallies:
            side = rally.get("winner_side") if isinstance(rally, dict) else None
            if side not in SIDES:
                continue
            idx = SIDES.index(side) ^ int(swapped) ^ int(flipped)
            prev = tuple(score)
            score[idx] += 1
            claimed[SIDES[idx]] += 1
            if scoring.ends_change(fmt, 1, score[0], score[1], prev[0], prev[1]):
                flipped = not flipped
    return claimed


def reconcile(data, stem, part_docs):
    """The reconciliation block for ``stem``'s match; ``part_docs`` = ``{stem: doc|None}``."""
    parts = match_parts(data, stem)
    if not parts:
        return _no_result([(stem, False)])
    primary = parts[0][0]
    record = libathletes.resolve_record(data, primary)
    expected, scope = _expected(record)
    if expected is None:
        return _no_result(parts)
    fmt = scoring.resolve(record) if record.get("scoring") is not None else None
    if fmt is not None and not scoring.consistent(fmt, record["result"]["sets"]):
        return _no_result(parts, "scoring_mismatch")
    claimed = {"upper": 0, "lower": 0}
    complete = True
    for part, swapped in parts:
        summary = (part_docs.get(part) or {}).get("summary")
        if not isinstance(summary, dict):
            complete = False
            continue
        won = summary.get("points_won")
        up, low = (_count(won.get(s)) if isinstance(won, dict) else None for s in SIDES)
        if up is None or low is None:   # a hand-edited or broken part file: unknown
            complete = False
            continue
        if swapped:
            up, low = low, up
        claimed["upper"] += up
        claimed["lower"] += low
        if summary.get("labelled") != summary.get("entries"):
            complete = False
    if (fmt is not None and scope == "sides" and fmt["best_of"] == 1
            and fmt["change_ends_at"] is not None):
        per_rally = _claimed_per_rally(fmt, parts, part_docs)
        if per_rally is not None:
            claimed = per_rally
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
