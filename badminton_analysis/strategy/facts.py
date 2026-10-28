"""The facts pack: every measured number the strategy report may talk about.

``build_facts(out_dir, record, athletes, focus=None)`` reads one ``outputs/<stem>/`` run (through
the insights sidecar and the tolerant loaders) and returns a JSON-able dict::

    {"format", "focus", "header", "facts": [{id, value, unit, who, status, reason, validated}],
     "caveats": [{code, params}], "tables", "inputs"}

Contract:
- It is total: a missing or half-written run, a hostile record or a failing fact group never
  raises. A group that fails becomes ``unavailable`` / ``no_data``.
- A fact the data cannot support stays in the pack as ``unavailable`` with its ``reason`` (the
  envelope reason code, or ``no_data``) so the model can say "not enough data".
- Doubles runs have no stroke labels (withheld upstream): they get formation, rotation, coverage
  and who-hit facts only, and who-hit facts say ``validated: false``.
- "you" is the registered athlete's side (singles) or team (doubles); see ``_focus``.
- ``p.pulled.*`` is where ``p``'s shots drag the other player (shares of that player's position
  at his next hit).
"""
import hashlib
import json
import os
import statistics
from bisect import bisect_right
from collections import Counter
from pathlib import Path

from ..insights.loaders import SIDES, load_run, number, tracklet_id
from ..insights.movement import NET_Y
from ..insights.rally import SHOT_PAD_SEC, rally_windows
from ..insights.sidecar import compute_video
from ..library import athletes as athlete_store
from . import shots as shots_mod

INPUT_FILES = ("insights.json", "strokes.json", "rally_segments.json", "hits.json",
               "players.json", "tracklets.jsonl", "technique_summary.json",
               "rally_outcomes.json")
OUTCOMES_FILE = "rally_outcomes.json"
OUTCOMES_SCHEMA = 1
RECORD_FIELDS = ("date", "format", "upper", "lower", "players", "result", "part_of",
                 "sides_swapped")
SHORT_RALLY_SHOTS = 5
LONG_RALLY_SHOTS = 10
FORMATION_GAP_M = 1.5
UNCOVERED_SHARE = 0.02
ATTACK = ("smash", "drive", "drop")
NEUTRAL = ("net", "net_shot", "push", "cross_net", "net_rush")
DEFENCE = ("clear", "lift", "block")
_ROW_ZONES = {"front": (0, 4), "mid": (4, 8), "rear": (8, 11)}
_COL_ZONES = {"left": (0, 3), "centre": (3, 7), "right": (7, 10)}
_PULL_ZONES = ("front", "mid", "back", "left", "right")
_GRID_ROWS, _GRID_COLS = 11, 10
_ZONE_NAMES = tuple(_ROW_ZONES) + tuple(_COL_ZONES)


# ---------------------------------------------------------------- fact helpers

def _fact(fid, value, who, unit=None, status="ok", reason=None, validated=True):
    return {"id": fid, "value": value, "unit": unit, "who": who, "status": status,
            "reason": reason, "validated": validated}


def _unavailable(fid, who, reason="no_data", unit=None):
    return _fact(fid, None, who, unit=unit, status="unavailable", reason=reason or "no_data")


def fact_map(pack):
    """``{fact id: fact}`` of a pack (total)."""
    facts = pack.get("facts") if isinstance(pack, dict) else None
    return {f["id"]: f for f in facts or [] if isinstance(f, dict) and "id" in f}


def _share(x):
    return round(x, 3)


def _env(ins, key):
    env = ins.get(key) if isinstance(ins, dict) else None
    return env if isinstance(env, dict) else {"status": "unavailable", "value": None,
                                              "reason": {"code": "no_data"}}


def _reason(env):
    reason = env.get("reason")
    code = reason.get("code") if isinstance(reason, dict) else None
    return code if isinstance(code, str) and code else None


def _status(env):
    """``(status, reason)`` a fact derived from this envelope inherits."""
    if env.get("status") == "ok":
        return "ok", None
    if env.get("status") == "partial":
        return "partial", _reason(env) or "partial"
    return "unavailable", _reason(env) or "no_data"


def _group(ids, who, fn):
    """``fn()``'s facts, or every id of the group unavailable when it fails."""
    try:
        return fn()
    except Exception:
        return [_unavailable(i, who) for i in ids]


def _all_unavailable(ids, who, reason):
    return [_unavailable(i, who, reason) for i in ids]


# ---------------------------------------------------------------- zones

def _grid_ok(grid):
    return (isinstance(grid, list) and len(grid) == _GRID_ROWS
            and all(isinstance(r, list) and len(r) == _GRID_COLS
                    and all(number(c) is not None for c in r) for r in grid))


def _cell_shares(grid):
    """``{(row_zone, col_zone): share}`` of the 9 cells, or None for a bad or empty grid."""
    if not _grid_ok(grid):
        return None
    total = sum(float(c) for r in grid for c in r)
    if total <= 0:
        return None
    out = {}
    for rname, (r0, r1) in _ROW_ZONES.items():
        for cname, (c0, c1) in _COL_ZONES.items():
            out[(rname, cname)] = sum(float(grid[r][c]) for r in range(r0, r1)
                                      for c in range(c0, c1)) / total
    return out


def _zone_shares(grid):
    """``{front, mid, rear, left, centre, right}`` shares of a grid, or None."""
    cells = _cell_shares(grid)
    if cells is None:
        return None
    out = {}
    for rname in _ROW_ZONES:
        out[rname] = sum(v for (r, _c), v in cells.items() if r == rname)
    for cname in _COL_ZONES:
        out[cname] = sum(v for (_r, c), v in cells.items() if c == cname)
    return out


def _zone_facts(prefix, who, grid, status="ok", reason=None):
    ids = [f"{prefix}.zone.{z}" for z in _ZONE_NAMES]
    zones = _zone_shares(grid)
    if zones is None:
        return _all_unavailable(ids, who, "no_positions")
    return [_fact(f"{prefix}.zone.{z}", _share(zones[z]), who, "share", status, reason)
            for z in _ZONE_NAMES]


# ---------------------------------------------------------------- the run

class _Ctx:
    """Everything the fact groups read, loaded once."""

    def __init__(self, out_dir):
        self.out_dir = Path(out_dir)
        self.stem = self.out_dir.name
        self.run = load_run(self.out_dir)
        self.ins = compute_video(self.stem, self.out_dir)
        self.fps = self.run.fps
        self.rally = _env(self.ins, "rally")
        self.windows = []
        if self.rally.get("status") != "unavailable":
            self.windows = rally_windows(self.run.rallies_payload, self.fps)


def _classified(s):
    group = s.get("group")
    return s.get("stroke") != "unknown" and isinstance(group, str) and bool(group)


def _stroke_name(s):
    name = s.get("stroke")
    return name if isinstance(name, str) else s.get("group")


def _bucket(s):
    for name in (s.get("stroke"), s.get("group")):
        if name in ATTACK:
            return "attack"
        if name in NEUTRAL:
            return "neutral"
        if name in DEFENCE:
            return "defence"
    return None


def _is_serve(s):
    return s.get("group") == "serve" or s.get("stroke") in ("short_serve", "long_serve")


# ---------------------------------------------------------------- match facts

MATCH_RALLY_IDS = ("match.rallies", "match.rally_median_sec", "match.rally_p90_sec",
                   "match.rally_median_shots", "match.short_rally_share",
                   "match.long_rally_share", "match.tempo_shots_per_sec")


def _percentile(values, q):
    data = sorted(values)
    if not data:
        return None
    k = (len(data) - 1) * q
    lo = int(k)
    hi = min(lo + 1, len(data) - 1)
    return data[lo] + (data[hi] - data[lo]) * (k - lo)


def _numbers(values):
    return [n for n in (number(v) for v in values) if n is not None] \
        if isinstance(values, list) else []


def _match_rally_facts(ctx):
    env = ctx.rally
    status, reason = _status(env)
    if status == "unavailable":
        return _all_unavailable(MATCH_RALLY_IDS, "match", reason)
    value = env["value"] if isinstance(env.get("value"), dict) else {}
    durations = _numbers(value.get("durations_sec"))
    shots_raw = value.get("shots_per_rally")
    shots = _numbers(shots_raw)
    out = []
    count = number(value.get("count"))
    if value.get("suppressed") or count is None:
        out.append(_unavailable("match.rallies", "match", reason or "no_data", "count"))
    else:
        out.append(_fact("match.rallies", int(count), "match", "count"))

    def mk(fid, val, unit, ok=True, defs=None):
        if val is None or not ok:
            return _unavailable(fid, "match", "no_data", unit)
        made = _fact(fid, val, "match", unit, status, reason)
        if defs:
            made["defs"] = defs                # constants that define the fact (validator)
        return made

    out.append(mk("match.rally_median_sec",
                  round(statistics.median(durations), 2) if durations else None, "s"))
    out.append(mk("match.rally_p90_sec",
                  round(_percentile(durations, 0.9), 2) if durations else None, "s",
                  defs=[90]))
    out.append(mk("match.rally_median_shots",
                  round(statistics.median(shots), 2) if shots else None, "shots"))
    out.append(mk("match.short_rally_share",
                  _share(sum(1 for s in shots if s <= SHORT_RALLY_SHOTS) / len(shots))
                  if shots else None, "share", defs=[SHORT_RALLY_SHOTS]))
    out.append(mk("match.long_rally_share",
                  _share(sum(1 for s in shots if s >= LONG_RALLY_SHOTS) / len(shots))
                  if shots else None, "share", defs=[LONG_RALLY_SHOTS]))
    aligned = (isinstance(shots_raw, list) and len(shots) == len(durations)
               and sum(durations) > 0)
    out.append(mk("match.tempo_shots_per_sec",
                  _share(sum(shots) / sum(durations)) if aligned and shots else None,
                  "shots_per_s"))
    return out


def _degraded_share(ctx):
    payload = ctx.run.rallies_payload
    rallies = payload.get("rallies") if isinstance(payload, dict) else None
    if not isinstance(rallies, list) or not rallies:
        return None
    detection = payload.get("detection")
    if isinstance(detection, dict) and detection.get("degraded"):
        return 1.0
    entries = [r for r in rallies if isinstance(r, dict)]
    if not entries:
        return None
    return sum(1 for r in entries if r.get("degraded") is True) / len(entries)


def _match_degraded_fact(ctx):
    share = _degraded_share(ctx)
    if share is None:
        return [_unavailable("match.degraded_share", "match", _reason(ctx.rally) or "no_data",
                             "share")]
    return [_fact("match.degraded_share", _share(share), "match", "share")]


def _oriented_sets(sets, flip):
    out = []
    for pair in sets if isinstance(sets, list) else []:
        if isinstance(pair, (list, tuple)) and len(pair) == 2:
            out.append([pair[1], pair[0]] if flip else [pair[0], pair[1]])
    return out


def _result_value(record, fmt, focus):
    """``{"winner", "sets"}`` from ``focus``'s view, or None without a usable result."""
    result = record.get("result")
    if not isinstance(result, dict):
        return None
    if fmt == "doubles":
        winner = result.get("winner_team")
        team = focus.get("team")
        if winner not in (1, 2) or team not in (1, 2):
            return None
        return {"winner": "you" if winner == team else "opponent",
                "sets": _oriented_sets(result.get("sets"), team == 2)}
    winner, side = result.get("winner"), focus.get("side")
    if winner not in SIDES or side not in SIDES:
        return None
    # ``sets`` stay in the primary's frame; a swapped part reads them the other way round
    flip = (side == "lower") != (record.get("sides_swapped") is True)
    return {"winner": "you" if winner == side else "opponent",
            "sets": _oriented_sets(result.get("sets"), flip)}


def _match_result_fact(record, fmt, focus):
    value = _result_value(record, fmt, focus)
    if value is None:
        return [_unavailable("match.result", "match", "no_result")]
    return [_fact("match.result", value, "match")]


# ---------------------------------------------------------------- singles facts

def _side_strokes(ctx, side):
    return [s for s in ctx.run.strokes or [] if s.get("hitter") == side]


def _strokes_status(ctx):
    """``(available, status, reason)`` for facts built from strokes.json."""
    env = _env(ctx.ins, "strokes")
    if not ctx.run.strokes:
        return False, "unavailable", _reason(env) or "no_strokes_file"
    status, reason = _status(env)
    return True, ("partial" if status == "partial" else "ok"), reason


MIX_IDS = ("mix.attack_share", "mix.neutral_share", "mix.defence_share")
SERVE_IDS = ("serve.count", "serve.short_share")
MISC_IDS = ("net_share", "unknown_share", "groups")
SEQ_IDS = ("seq2_top", "seq3_top", "after_lift")


def _mix_facts(ctx, p, side):
    ids = [f"{p}.{i}" for i in MIX_IDS]
    avail, status, reason = _strokes_status(ctx)
    if not avail:
        return _all_unavailable(ids, p, reason)
    buckets = Counter(b for s in _side_strokes(ctx, side)
                      if _classified(s) and not _is_serve(s) and (b := _bucket(s)))
    total = sum(buckets.values())
    if not total:
        return _all_unavailable(ids, p, "no_data")
    return [_fact(f"{p}.mix.{b}_share", _share(buckets[b] / total), p, "share", status, reason)
            for b in ("attack", "neutral", "defence")]


def _serve_facts(ctx, p, side):
    avail, status, reason = _strokes_status(ctx)
    if not avail:
        return _all_unavailable([f"{p}.{i}" for i in SERVE_IDS], p, reason)
    serves = [s for s in _side_strokes(ctx, side) if _classified(s) and _is_serve(s)]
    out = [_fact(f"{p}.serve.count", len(serves), p, "count", status, reason)]
    typed = [s for s in serves if s.get("stroke") in ("short_serve", "long_serve")]
    if typed:
        short = sum(1 for s in typed if s["stroke"] == "short_serve")
        out.append(_fact(f"{p}.serve.short_share", _share(short / len(typed)), p, "share",
                         status, reason))
    else:
        out.append(_unavailable(f"{p}.serve.short_share", p, "no_data", "share"))
    return out


def _misc_facts(ctx, p, side):
    avail, status, reason = _strokes_status(ctx)
    if not avail:
        return _all_unavailable([f"{p}.{i}" for i in MISC_IDS], p, reason)
    mine = _side_strokes(ctx, side)
    sure = [s for s in mine if _classified(s) and not _is_serve(s)]
    out = []
    if sure:
        net = sum(1 for s in sure if s.get("group") == "net")
        out.append(_fact(f"{p}.net_share", _share(net / len(sure)), p, "share", status, reason))
    else:
        out.append(_unavailable(f"{p}.net_share", p, "no_data", "share"))
    if mine:
        unknown = sum(1 for s in mine if not _classified(s))
        out.append(_fact(f"{p}.unknown_share", _share(unknown / len(mine)), p, "share",
                         status, reason))
    else:
        out.append(_unavailable(f"{p}.unknown_share", p, "no_data", "share"))
    by_hitter = (_env(ctx.ins, "strokes").get("value") or {}).get("by_hitter")
    dist = ((by_hitter or {}).get(side) or {}).get("distribution")
    if isinstance(dist, dict):
        out.append(_fact(f"{p}.groups", dict(dist), p, "count", status, reason))
    else:
        out.append(_unavailable(f"{p}.groups", p, "no_data", "count"))
    return out


def _rally_strokes(ctx):
    """Classified strokes per rally window, in frame order (empty list without a clock)."""
    if not ctx.run.strokes or not ctx.fps or not ctx.windows:
        return []
    timed = []
    for s in ctx.run.strokes:
        frame = number(s.get("frame"))
        if frame is not None and _classified(s) and s.get("hitter") in SIDES:
            timed.append((frame / ctx.fps, s))
    timed.sort(key=lambda x: x[0])
    rallies = []
    for start, end in sorted(ctx.windows):
        rally = [s for t, s in timed if start - SHOT_PAD_SEC <= t <= end + SHOT_PAD_SEC]
        if rally:
            rallies.append(rally)
    return rallies


def _top(counter, last_seen, n=3):
    """Top ``n`` sequences by count; ties go to the one seen most recently in the match."""
    ranked = sorted(counter, key=lambda k: (-counter[k], -last_seen[k]))
    return [{"seq": list(k), "n": counter[k]} for k in ranked[:n]]


def _seq_facts(ctx, p, side):
    ids = [f"{p}.{i}" for i in SEQ_IDS]
    avail, status, reason = _strokes_status(ctx)
    if not avail:
        return _all_unavailable(ids, p, reason)
    rallies = _rally_strokes(ctx)
    if not rallies:
        return _all_unavailable(ids, p, "no_rallies" if not ctx.windows else "no_data")
    other = "upper" if side == "lower" else "lower"
    seq2, seq3, after_lift = Counter(), Counter(), Counter()
    last2, last3 = {}, {}
    order = 0
    for rally in rallies:
        for i, cur in enumerate(rally):
            order += 1
            if cur["hitter"] != side or i == 0:
                continue
            prev = rally[i - 1]
            if prev["hitter"] == other:
                key = (_stroke_name(prev), _stroke_name(cur))
                seq2[key] += 1
                last2[key] = order
                if prev.get("group") == "lift":
                    after_lift[_stroke_name(cur)] += 1
            if i >= 2:
                key3 = (_stroke_name(rally[i - 2]), _stroke_name(prev), _stroke_name(cur))
                seq3[key3] += 1
                last3[key3] = order
    out = []
    for fid, counter, last in ((f"{p}.seq2_top", seq2, last2), (f"{p}.seq3_top", seq3, last3)):
        out.append(_fact(fid, _top(counter, last), p, "count", status, reason)
                   if counter else _unavailable(fid, p, "no_data", "count"))
    out.append(_fact(f"{p}.after_lift", dict(after_lift), p, "count", status, reason)
               if after_lift else _unavailable(f"{p}.after_lift", p, "no_data", "count"))
    return out


def _last_stroke_facts(ctx, p, side):
    fid = f"{p}.last_stroke"
    avail, status, reason = _strokes_status(ctx)
    if not avail:
        return [_unavailable(fid, p, reason)]
    counts = Counter(_stroke_name(r[-1]) for r in _rally_strokes(ctx) if r[-1]["hitter"] == side)
    if not counts:
        return [_unavailable(fid, p, "no_rallies" if not ctx.windows else "no_data")]
    return [_fact(fid, dict(counts), p, "count", status, reason)]


def _side_value(env, side):
    value = env.get("value")
    entry = value.get(side) if isinstance(value, dict) else None
    return entry if isinstance(entry, dict) else None


MOVE_IDS = ("distance_m", "mean_speed_mps", "base") + tuple(f"zone.{z}" for z in _ZONE_NAMES)


def _movement_facts(ctx, p, side):
    env = _env(ctx.ins, "movement")
    entry = _side_value(env, side)
    ids = [f"{p}.{i}" for i in MOVE_IDS]
    if entry is None:
        return _all_unavailable(ids, p, _reason(env) or "no_positions")
    status, reason = _status(env)
    out = []
    for key, unit in (("distance_m", "m"), ("mean_speed_mps", "m_per_s")):
        val = number(entry.get(key))
        out.append(_fact(f"{p}.{key}", val, p, unit, status, reason) if val is not None
                   else _unavailable(f"{p}.{key}", p, "no_data", unit))
    base = entry.get("base")
    out.append(_fact(f"{p}.base", {"x": base.get("x"), "y": base.get("y")}, p, "m", status,
                     reason) if isinstance(base, dict) else _unavailable(f"{p}.base", p))
    out.extend(_zone_facts(p, p, entry.get("grid"), status, reason))
    return out


def _recovery_facts(ctx, p, side):
    fid = f"{p}.recovery_median_sec"
    env = _env(ctx.ins, "recovery")
    entry = _side_value(env, side)
    val = number(entry.get("median_sec")) if entry else None
    if val is None:
        return [_unavailable(fid, p, _reason(env) or "no_data", "s")]
    status, reason = _status(env)
    return [_fact(fid, round(val, 2), p, "s", status, reason)]


def _pulled_facts(ctx, p, side):
    ids = [f"{p}.pulled.{z}" for z in _PULL_ZONES] + [f"{p}.pulled.n"]
    env = _env(ctx.ins, "pull")
    entry = _side_value(env, side)
    if entry is None or number(entry.get("n")) is None:
        return _all_unavailable(ids, p, _reason(env) or "no_hits")
    status, reason = _status(env)
    out = []
    for z in _PULL_ZONES:
        val = number(entry.get(z))
        out.append(_fact(f"{p}.pulled.{z}", _share(val), p, "share", status, reason)
                   if val is not None else _unavailable(f"{p}.pulled.{z}", p, "no_data",
                                                        "share"))
    out.append(_fact(f"{p}.pulled.n", int(entry["n"]), p, "count", status, reason))
    return out


def _technique_facts(ctx, p, side):
    ids = (f"{p}.technique_index", f"{p}.technique_weak")
    env = _env(ctx.ins, "technique")
    entry = None
    value = env.get("value")
    if isinstance(value, dict) and isinstance(value.get("by_side"), dict):
        entry = value["by_side"].get(side)
    if not isinstance(entry, dict):
        return _all_unavailable(ids, p, _reason(env) or "no_technique")
    status, reason = _status(env)
    out = []
    index = number(entry.get("index"))
    out.append(_fact(ids[0], index, p, "score", status, reason) if index is not None
               else _unavailable(ids[0], p, "no_data", "score"))
    scored = []
    for name, rec in (entry.get("techniques") or {}).items():
        score = number(rec.get("avg_score")) if isinstance(rec, dict) else None
        count = number(rec.get("count")) if isinstance(rec, dict) else None
        if score is not None and count is not None and count >= 3:
            scored.append((score, name))
    weak = [name for _score, name in sorted(scored)[:2]]
    out.append(_fact(ids[1], weak, p, None, status, reason) if weak
               else _unavailable(ids[1], p, "no_data"))
    return out


# ---------------------------------------------------------------- outcomes

def _read_outcomes(out_dir):
    """``(rallies, summary)`` of a schema-1 ``rally_outcomes.json`` (rally outcome spec 5.2), or None.

    ``summary`` is ``{}`` when the file has none; any other schema, or no usable rally, is None."""
    try:
        with open(os.path.join(out_dir, OUTCOMES_FILE), encoding="utf-8") as f:
            doc = json.load(f)
    except (OSError, ValueError, RecursionError, MemoryError):
        return None
    if not isinstance(doc, dict) or doc.get("schema") != OUTCOMES_SCHEMA:
        return None
    rallies = doc.get("rallies")
    rallies = [r for r in rallies if isinstance(r, dict)] if isinstance(rallies, list) else []
    summary = doc.get("summary") if isinstance(doc.get("summary"), dict) else {}
    return (rallies, summary) if rallies else None


def _label(value):
    """A count key: a non-empty string, the ``type`` of a stroke object, else ``unknown``."""
    if isinstance(value, dict):
        value = value.get("type")
    return value if isinstance(value, str) and value else "unknown"


def _outcome_facts(outcomes, sides):
    """Facts for ``{"you": side, "opponent": side}`` from ``_read_outcomes``'s pair."""
    rallies, summary = outcomes
    known = [r for r in rallies if r.get("winner_side") in SIDES]
    coverage = number(summary.get("coverage"))
    if coverage is None or not 0 <= coverage <= 1:
        coverage = len(known) / len(rallies)
    status = "ok" if coverage >= 0.5 else "partial"
    reason = None if status == "ok" else "low_outcome_coverage"
    out = [_fact("match.outcome_coverage", _share(coverage), "match", "share")]
    for p, side in sides.items():
        if side not in SIDES:
            continue
        won = [r for r in known if r["winner_side"] == side]
        lost = [r for r in known if r["winner_side"] != side]
        for fid, rows, key in (("won_by", won, "ending_stroke"), ("won_ending", won, "ending"),
                               ("lost_by", lost, "outcome"), ("lost_ending", lost, "ending")):
            out.append(_fact(f"{p}.{fid}", dict(Counter(_label(r.get(key)) for r in rows)),
                             p, "count", status, reason))
        out.append(_fact(f"{p}.points_won", len(won), p, "count", status, reason))
    return out


def _outcome_caveats(outcomes):
    """``outcomes_unreconciled{status}`` when the points do not match the recorded result."""
    rec = outcomes[1].get("reconciliation")
    status = rec.get("status") if isinstance(rec, dict) else None
    if status == "consistent":
        return []
    return [{"code": "outcomes_unreconciled",
             "params": {"status": status if isinstance(status, str) else "unknown"}}]


def _singles_side_facts(ctx, p, side):
    """All per-player singles facts of ``p`` (``you`` / ``opponent``) standing on ``side``."""
    groups = (
        (MIX_IDS, _mix_facts), (SERVE_IDS, _serve_facts), (MISC_IDS, _misc_facts),
        (SEQ_IDS, _seq_facts), (("last_stroke",), _last_stroke_facts),
        (MOVE_IDS, _movement_facts), (("recovery_median_sec",), _recovery_facts),
        (tuple(f"pulled.{z}" for z in _PULL_ZONES) + ("pulled.n",), _pulled_facts),
        (("technique_index", "technique_weak"), _technique_facts))
    out = []
    for ids, fn in groups:
        out.extend(_group([f"{p}.{i}" for i in ids], p, lambda fn=fn: fn(ctx, p, side)))
    return out


# ---------------------------------------------------------------- doubles facts

def _doubles_env(ctx):
    return _env(ctx.ins, "doubles")


def _doubles_players(env):
    value = env.get("value")
    players = value.get("players") if isinstance(value, dict) else None
    return [p for p in players or [] if isinstance(p, dict) and isinstance(p.get("id"), str)]


def _player_points(ctx, players):
    """``{player id: {frame: (x, y)}}`` from the tracklets each identity owns."""
    doc = ctx.run.players_doc if isinstance(ctx.run.players_doc, dict) else {}
    owners = {}
    for row in doc.get("players") or []:
        if not isinstance(row, dict) or not isinstance(row.get("id"), str):
            continue
        for t in row.get("tracklets") if isinstance(row.get("tracklets"), list) else ():
            tid = tracklet_id(t.get("id")) if isinstance(t, dict) else None
            if tid is not None:
                owners.setdefault(tid, row["id"])
    wanted = {p["id"] for p in players}
    points = {pid: {} for pid in wanted}
    for t in ctx.run.tracklets or ():
        pid = owners.get(t["id"])
        if pid in wanted:
            for frame, x, y in t["points"]:
                points[pid].setdefault(frame, (x, y))
    return points


def _team_side(pids, points):
    ys = [xy[1] for pid in pids for xy in points.get(pid, {}).values()]
    if not ys:
        return None
    return "upper" if statistics.median(ys) < NET_Y else "lower"


def _frame_windows(ctx):
    """Rally windows in frames (``[]`` when there is no usable rally clock)."""
    if not ctx.windows or not ctx.fps:
        return []
    return [(start * ctx.fps, end * ctx.fps) for start, end in sorted(ctx.windows)]


def _formation_facts(ctx, t, pids, points):
    """``team.<t>.formation.front_back_share`` and ``team.<t>.rotations_per_rally``."""
    fid_form = f"team.{t}.formation.front_back_share"
    fid_rot = f"team.{t}.rotations_per_rally"
    who = f"team.{t}"
    if len(pids) != 2 or any(pid not in points for pid in pids):
        return [_unavailable(fid_form, who, "no_positions", "share"),
                _unavailable(fid_rot, who, "no_positions")]
    a, b = points[pids[0]], points[pids[1]]
    windows = _frame_windows(ctx)

    def in_play(frame):
        return not windows or any(s <= frame <= e for s, e in windows)

    front_back = side_by_side = 0
    swaps_by_window = {}
    last_front = {}
    for frame in sorted(set(a) & set(b)):
        if not in_play(frame):
            continue
        (ax, ay), (bx, by) = a[frame], b[frame]
        dx, dy = abs(ax - bx), abs(ay - by)
        if dy >= FORMATION_GAP_M and dx < FORMATION_GAP_M:
            front_back += 1
        elif dx >= FORMATION_GAP_M and dy < FORMATION_GAP_M:
            side_by_side += 1
        if dy >= FORMATION_GAP_M:
            win = next((i for i, (s, e) in enumerate(windows) if s <= frame <= e), 0)
            front = pids[0] if abs(ay - NET_Y) < abs(by - NET_Y) else pids[1]
            if win in last_front and last_front[win] != front:
                swaps_by_window[win] = swaps_by_window.get(win, 0) + 1
            last_front[win] = front
    out = []
    if front_back + side_by_side:
        out.append(_fact(fid_form, _share(front_back / (front_back + side_by_side)), who,
                         "share"))
    else:
        out.append(_unavailable(fid_form, who, "no_data", "share"))
    if windows:
        out.append(_fact(fid_rot, round(sum(swaps_by_window.values()) / len(windows), 2), who,
                         "per_rally"))
    else:
        out.append(_unavailable(fid_rot, who, "no_rallies"))
    return out


def _uncovered_fact(t, team_players):
    fid = f"team.{t}.uncovered"
    cells = {}
    total = 0.0
    for pl in team_players:
        samples = number(pl.get("samples")) or 0
        shares = _cell_shares(pl.get("grid"))
        if shares is None or samples <= 0:
            continue
        total += samples
        for key, v in shares.items():
            cells[key] = cells.get(key, 0.0) + v * samples
    if total <= 0:
        return [_unavailable(fid, f"team.{t}", "no_positions")]
    merged = sorted(((v / total, f"{r}_{c}") for (r, c), v in cells.items()))
    return [_fact(fid, [name for v, name in merged if v < UNCOVERED_SHARE][:3], f"team.{t}")]


def _player_facts(p, pl, team_hits_named):
    who = f"{p}.{pl['id']}"
    out = []
    named, hits = number(pl.get("hits_named")), number(pl.get("hits"))
    if named is not None and team_hits_named:
        out.append(_fact(f"{who}.hit_share", _share(named / team_hits_named), who, "share",
                         validated=False))
    else:
        out.append(_unavailable(f"{who}.hit_share", who, "no_hits", "share"))
        out[-1]["validated"] = False
    dist = number(pl.get("distance_m"))
    out.append(_fact(f"{who}.distance_m", dist, who, "m") if dist is not None
               else _unavailable(f"{who}.distance_m", who, "no_data", "m"))
    out.extend(_zone_facts(who, who, pl.get("grid")))
    return out


def _doubles_facts(ctx, focus, record):
    """``(facts, sides, teams)``: doubles facts, ``{"you": side, ...}`` and the team map."""
    env = _doubles_env(ctx)
    players = _doubles_players(env)
    if env.get("status") == "unavailable" or not players:
        reason = _reason(env) or "no_players"
        ids = ["team.you.formation.front_back_share", "team.you.rotations_per_rally",
               "team.you.uncovered", "team.opponent.formation.front_back_share",
               "team.opponent.rotations_per_rally", "team.opponent.uncovered"]
        return [_unavailable(i, "team." + i.split(".")[1], reason) for i in ids], {}
    you_team = focus.get("team")
    me = [p for p in players if p.get("team") == you_team]
    them = [p for p in players if p.get("team") in (1, 2) and p.get("team") != you_team]
    points = _player_points(ctx, players)
    out = []
    sides = {}
    teams_data = _env_teams(env)
    for t, prefix, group in (("you", "you", me), ("opponent", "opponent", them)):
        pids = [p["id"] for p in group]
        sides[t] = _team_side(pids, points)
        out.extend(_group([f"team.{t}.formation.front_back_share",
                           f"team.{t}.rotations_per_rally"], f"team.{t}",
                          lambda t=t, pids=pids: _formation_facts(ctx, t, pids, points)))
        out.extend(_group([f"team.{t}.uncovered"], f"team.{t}",
                          lambda t=t, group=group: _uncovered_fact(t, group)))
        team_id = group[0].get("team") if group else None
        hits_named = (teams_data.get(team_id) or {}).get("hits_named")
        for pl in group:
            out.extend(_group([f"{prefix}.{pl['id']}.hit_share"], prefix,
                              lambda pl=pl: _player_facts(prefix, pl, number(hits_named))))
    return out, sides


def _env_teams(env):
    value = env.get("value")
    teams = value.get("teams") if isinstance(value, dict) else None
    return {t["id"]: t for t in teams or [] if isinstance(t, dict) and "id" in t}


# ---------------------------------------------------------------- rally tactics (shots.py)

TACTICS_MATCH_IDS = ("match.active_play_sec", "match.rallies_under_9s_share",
                     "match.shots_per_rally", "match.owner_labelled_share", "match.stroke_model",
                     "match.openings.complete", "match.openings.total")
WEIGHTS_DIR = Path(__file__).resolve().parents[2] / "weights"
HISTORY_FILE = "stroke-model-history.json"


def _empty_tables():
    return {"effectiveness": {"you": {}, "opponent": {}},
            "openings": {"complete": 0, "total": 0, "rows": [],
                         "won_by": {"you": 0, "opponent": 0},
                         "opening_winner_won_point": {"n": 0, "won": 0}, "neutral": 0,
                         "insufficient": True}}


def _stroke_model_quality():
    """``{accuracy, bar_met}`` of the live stroke model, or None when nothing is recorded.

    The last kept entry of ``weights/stroke-model-history.json`` holds the model's scored
    metrics (``candidate``) and, when the evaluator wrote it, ``bar_met``. One place to point at
    ``stroke.model_eval.status`` later."""
    try:
        with open(WEIGHTS_DIR / HISTORY_FILE, encoding="utf-8") as f:
            entries = json.load(f)
    except (OSError, ValueError, RecursionError, MemoryError):
        return None
    for entry in reversed(entries if isinstance(entries, list) else []):
        metrics = entry.get("candidate") if isinstance(entry, dict) and entry.get("kept") else None
        accuracy = number(metrics.get("accuracy")) if isinstance(metrics, dict) else None
        if accuracy is not None:
            bar = entry.get("bar_met")
            return {"accuracy": round(accuracy, 3), "bar_met": bar if isinstance(bar, bool) else None}
    return None


def _tactics_overview_facts(table, ov, quality):
    out = []

    def one(fid, val, unit, defs=None):
        if val is None:
            out.append(_unavailable(fid, "match", "no_data", unit))
            return
        made = _fact(fid, val, "match", unit)
        if defs:
            made["defs"] = defs
        out.append(made)

    one("match.active_play_sec", ov.get("active_play_sec"), "s")
    one("match.rallies_under_9s_share", ov.get("rallies_under_9s_share"), "share",
        [int(shots_mod.SHORT_RALLY_SEC)])
    one("match.shots_per_rally", ov.get("shots_per_rally"), "shots")
    rows = table.get("shots") or []
    one("match.owner_labelled_share",
        _share(sum(1 for s in rows if s.get("source") == "owner") / len(rows)) if rows else None,
        "share")
    if quality is None:
        out.append(_unavailable("match.stroke_model", "match", "no_model_scores"))
    else:
        out.append(_fact("match.stroke_model", dict(quality), "match"))
    return out


def _eff_facts(table, who_of, prefix_of):
    out = []
    eff = {}
    for side, p in who_of.items():
        cells = shots_mod.effectiveness(table, side)
        eff[p] = cells
        for stroke in sorted(cells):
            cell = cells[stroke]
            few = cell["n"] < shots_mod.MIN_ATTEMPTS
            out.append(_fact(f"{prefix_of(p)}.eff.{stroke}", dict(cell), p, "count",
                             "insufficient" if few else "ok", "few_attempts" if few else None))
    return out, eff


def _opening_facts(table, who_of, prefix_of, op):
    ids = ["match.openings.complete", "match.openings.total"]
    if not table.get("rallies"):
        return [_unavailable(i, "match", "no_rallies", "count") for i in ids]
    few = op["complete"] < shots_mod.MIN_OPENINGS
    status, reason = ("insufficient", "few_openings") if few else ("ok", None)
    done = _fact(ids[0], op["complete"], "match", "count", status, reason)
    done["defs"] = [3]
    out = [done, _fact(ids[1], op["total"], "match", "count", status, reason)]
    for side, p in who_of.items():
        out.append(_fact(f"{prefix_of(p)}.openings.won", op["won_by"][side], p, "count",
                         status, reason))
        out.append(_fact(f"{prefix_of(p)}.opening_win_point", dict(op["opening_winner_won_point"]),
                         p, "count", status, reason))
    return out


def _tactics(ctx, fmt, sides):
    """``(facts, caveats, tables)`` of the rally-tactics part (never raises)."""
    tables = _empty_tables()
    try:
        table = shots_mod.build_shots(ctx.out_dir)
        who_of = {side: p for p, side in sides.items() if side in SIDES}
        prefix_of = (lambda p: f"team.{p}") if fmt == "doubles" else (lambda p: p)
        quality = _stroke_model_quality()
        facts = _group(TACTICS_MATCH_IDS[:5], "match", lambda: _tactics_overview_facts(
            table, shots_mod.overview(table, ctx.out_dir), quality))
        eff, tables["effectiveness"] = [], {"you": {}, "opponent": {}}
        try:
            eff, by_who = _eff_facts(table, who_of, prefix_of)
            tables["effectiveness"].update(by_who)
        except Exception:
            pass
        facts.extend(eff)
        op = shots_mod.openings(table)
        facts.extend(_group(TACTICS_MATCH_IDS[5:], "match",
                            lambda: _opening_facts(table, who_of, prefix_of, op)))
        who = lambda side: who_of.get(side, side)         # noqa: E731
        tables["openings"] = {
            "complete": op["complete"], "total": op["total"], "neutral": op["neutral"],
            "insufficient": op["complete"] < shots_mod.MIN_OPENINGS,
            "won_by": {p: op["won_by"].get(side, 0) for side, p in who_of.items()}
            | {p: 0 for p in ("you", "opponent") if p not in who_of.values()},
            "opening_winner_won_point": dict(op["opening_winner_won_point"]),
            "rows": [{"rally": r["rally"], "n_shots": r["n_shots"],
                      "advantage": who(r["advantage"]) if r["advantage"] != "neutral"
                      else "neutral",
                      "point_winner": who(r["point_winner"]) if r["point_winner"] else None,
                      "shots": [{"hitter": who(s["hitter"]), "stroke": s["stroke"],
                                 "zone": s["zone"], "sent_to": s["sent_to"]}
                                for s in r["shots"]]} for r in op["rows"]]}
        caveats = []
        if quality is not None:
            caveats.append({"code": "stroke_model", "params": dict(quality)})
        shot_rows = table.get("shots") or []
        owned = sum(1 for s in shot_rows if s.get("source") == "owner")
        if owned:
            caveats.append({"code": "owner_labelled",
                            "params": {"share": _share(owned / len(shot_rows))}})
        bad = [who(s) for s in SIDES if not table["positions_ok"].get(s)]
        if shot_rows and bad:
            names = ("you", "opponent")
            caveats.append({"code": "positions_unusable",
                            "params": {"sides": [w for w in names if w in bad]
                                       + sorted(w for w in bad if w not in names)}})
        if table.get("rallies") and op["complete"] < shots_mod.MIN_OPENINGS:
            caveats.append({"code": "openings_incomplete",
                            "params": {"complete": op["complete"], "total": op["total"]}})
        return facts, caveats, tables
    except Exception:
        return ([_unavailable(i, "match") for i in TACTICS_MATCH_IDS], [], _empty_tables())


# ---------------------------------------------------------------- focus, header

def _store(athletes):
    if isinstance(athletes, dict) and isinstance(athletes.get("athletes"), dict):
        return athletes
    return athlete_store.empty()


def _entry(store, record, side):
    try:
        return athlete_store.side_athlete(store, record, side)
    except Exception:
        return {"athlete_id": None, "name": None}


def _player_entry(store, record, pid):
    try:
        return athlete_store.player_athlete(store, record, pid)
    except Exception:
        return {"athlete_id": None, "name": None}


def _singles_focus(store, record, focus):
    ids = {s: _entry(store, record, s)["athlete_id"] for s in SIDES}
    if focus in SIDES:
        return {"side": focus, "team": None, "athlete_id": ids[focus], "assumed": False}
    for side in ("lower", "upper"):
        if ids[side]:
            return {"side": side, "team": None, "athlete_id": ids[side], "assumed": False}
    return {"side": "lower", "team": None, "athlete_id": None, "assumed": True}


def _doubles_focus(ctx, store, record, focus):
    env = _doubles_env(ctx)
    players = _doubles_players(env)
    team_of = {p["id"]: p.get("team") for p in players}
    registered = {}
    for pid in ("P1", "P2", "P3", "P4"):
        aid = _player_entry(store, record, pid)["athlete_id"]
        if aid and team_of.get(pid) in (1, 2):
            registered.setdefault(team_of[pid], aid)
    if focus in SIDES:
        points = _player_points(ctx, players)
        for team in (1, 2):
            pids = [p["id"] for p in players if p.get("team") == team]
            if _team_side(pids, points) == focus:
                return {"side": None, "team": team, "athlete_id": registered.get(team),
                        "assumed": False}
        team = 1 if focus == "lower" else 2
        return {"side": None, "team": team, "athlete_id": registered.get(team),
                "assumed": False}
    for team in (1, 2):
        if team in registered:
            return {"side": None, "team": team, "athlete_id": registered[team],
                    "assumed": False}
    return {"side": None, "team": 1, "athlete_id": None, "assumed": True}


def _names(store, record, fmt, focus, ctx):
    """``(you, opponent, both_registered)`` display names."""
    if fmt == "singles":
        side = focus["side"]
        other = "upper" if side == "lower" else "lower"
        me, them = _entry(store, record, side), _entry(store, record, other)
        return me["name"], them["name"], bool(me["athlete_id"] and them["athlete_id"])
    players = _doubles_players(_doubles_env(ctx))
    names = {"you": [], "opponent": []}
    for p in players:
        who = "you" if p.get("team") == focus["team"] else "opponent"
        entry = _player_entry(store, record, p["id"])
        name = entry["name"] or (p.get("name") if isinstance(p.get("name"), str) else None)
        if name:
            names[who].append(name)
    join = lambda v: " / ".join(v) if v else None      # noqa: E731
    # Doubles focus is a team, not a side, so the UI's "other side" toggle stays hidden.
    return join(names["you"]), join(names["opponent"]), False


def _record_hash(record):
    relevant = {k: record.get(k) for k in RECORD_FIELDS if k in record}
    blob = json.dumps(relevant, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()


def current_inputs(out_dir, record):
    """``{file: mtime | None, "record": sha1}``: what a report was built from."""
    inputs = {}
    for name in INPUT_FILES:
        try:
            inputs[name] = os.stat(os.path.join(out_dir, name)).st_mtime
        except OSError:
            inputs[name] = None
    inputs["record"] = _record_hash(record if isinstance(record, dict) else {})
    return inputs


def _confidence(ctx, fmt, degraded):
    rally_status = ctx.rally.get("status")
    if rally_status == "unavailable" or (degraded or 0) > 0.3:
        return "low"
    if fmt == "doubles":
        denv = _doubles_env(ctx)
        if denv.get("status") != "ok":
            return "low"
        return "high" if rally_status == "ok" and not degraded else "medium"
    senv = _env(ctx.ins, "strokes")
    cov = senv.get("coverage")
    total = number(cov.get("of")) if isinstance(cov, dict) else None
    share = (number(cov.get("n")) or 0) / total if total else None
    if senv.get("status") == "unavailable" or share is None or share < 0.5:
        return "low"
    if rally_status == "ok" and share >= 0.75 and not degraded:
        return "high"
    return "medium"


def _missing(ctx, fmt):
    wanted = (("players.json", "tracklets.jsonl") if fmt == "doubles" else ("strokes.json",)) + (
        "rally_segments.json", "hits.json")
    return [n for n in wanted if not os.path.isfile(ctx.out_dir / n)]


# ---------------------------------------------------------------- build_facts

def _empty_ctx_pack(out_dir, record):
    return {"format": "singles",
            "focus": {"side": "lower", "team": None, "athlete_id": None, "assumed": True},
            "header": {"you": None, "opponent": None, "date": None, "winner": None,
                       "sets": None, "part_of": None, "confidence": "low",
                       "both_registered": False},
            "facts": [], "caveats": [{"code": "missing", "params": {"files": []}}],
            "tables": _empty_tables(), "inputs": current_inputs(out_dir, record)}


def build_facts(out_dir, record, athletes, focus=None):
    """The facts pack of one run; never raises (see the module docstring)."""
    record = record if isinstance(record, dict) else {}
    try:
        return _build(out_dir, record, _store(athletes), focus)
    except Exception:
        return _empty_ctx_pack(out_dir, record)


def _build(out_dir, record, store, focus):
    ctx = _Ctx(out_dir)
    fmt = ctx.run.match_format
    if fmt == "doubles":
        fcs = _doubles_focus(ctx, store, record, focus)
    else:
        fcs = _singles_focus(store, record, focus)
    facts = []
    facts.extend(_group(MATCH_RALLY_IDS, "match", lambda: _match_rally_facts(ctx)))
    facts.extend(_group(["match.degraded_share"], "match", lambda: _match_degraded_fact(ctx)))
    facts.extend(_group(["match.result"], "match",
                        lambda: _match_result_fact(record, fmt, fcs)))
    sides = {}
    if fmt == "doubles":
        facts.extend(_group([], "match", lambda: _doubles_part(ctx, fcs, record, sides)))
    else:
        other = "upper" if fcs["side"] == "lower" else "lower"
        sides = {"you": fcs["side"], "opponent": other}
        for p, side in sides.items():
            facts.extend(_singles_side_facts(ctx, p, side))

    outcomes = _read_outcomes(ctx.out_dir)
    caveats = []
    if outcomes is None:
        caveats.append({"code": "no_rally_outcomes", "params": {}})
    else:
        try:
            facts.extend(_outcome_facts(outcomes, sides))
            caveats.extend(_outcome_caveats(outcomes))
        except Exception:
            caveats.append({"code": "no_rally_outcomes", "params": {}})
    tactic_facts, tactic_caveats, tables = _tactics(ctx, fmt, sides)
    facts.extend(tactic_facts)
    caveats.extend(tactic_caveats)
    if fmt == "doubles":
        caveats.append({"code": "doubles_strokes_withheld", "params": {}})
        caveats.append({"code": "doubles_hits_unvalidated", "params": {}})
    degraded = _degraded_share(ctx)
    if degraded:
        caveats.append({"code": "degraded_rallies", "params": {"share": _share(degraded)}})
    value = ctx.rally.get("value")
    if ctx.rally.get("status") == "partial" and isinstance(value, dict):
        caveats.append({"code": "rally_signal", "params": {"signal": value.get("signal")}})
    if fcs["assumed"]:
        caveats.append({"code": "focus_assumed_team" if fmt == "doubles" else "focus_assumed",
                        "params": {}})
    missing = _missing(ctx, fmt)
    if missing:
        caveats.append({"code": "missing", "params": {"files": missing}})

    you, opponent, both = _names(store, record, fmt, fcs, ctx)
    result = _result_value(record, fmt, fcs)
    date = record.get("date") if isinstance(record.get("date"), str) else None
    if date is None:
        d = ctx.ins.get("date") if isinstance(ctx.ins.get("date"), dict) else {}
        date = d.get("value") if isinstance(d.get("value"), str) else None
    header = {"you": you, "opponent": opponent, "date": date,
              "winner": result["winner"] if result else None,
              "sets": result["sets"] if result else None,
              "part_of": record.get("part_of") if isinstance(record.get("part_of"), str)
              else None,
              "confidence": _confidence(ctx, fmt, degraded), "both_registered": both}
    return {"format": fmt, "focus": fcs, "header": header, "facts": facts,
            "caveats": caveats, "tables": tables,
            "inputs": current_inputs(ctx.out_dir, record)}


def _doubles_part(ctx, focus, record, sides):
    out, found = _doubles_facts(ctx, focus, record)
    sides.update(found)
    return out
