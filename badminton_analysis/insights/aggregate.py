"""Overview aggregation over many videos (spec 5.4).

Pure: it works only from ``units_source`` (sidecar dicts the caller supplies)
and the athlete/match store; it does no file I/O. A *unit* is one
``(stem, side)``; a stem's rally and stroke-distribution insights belong to the
whole video, so they are counted once per stem, not once per unit.

A *doubles* stem (sidecar ``match_format == "doubles"``; for a stem not analysed yet, a
doubles match record) never yields a singles ``(stem, side)`` unit: its legacy tracker
output is one representative per side of a 4-player match, so it adds nothing to the
technique, stroke, recovery, pull or match-win aggregates, the trend or the benchmark. Its
rally windows still feed ``avg_rally``. Instead it yields one *doubles unit*
``(stem, identity)`` for each identity (``P1``..``P4``) its match record maps to a
registered athlete that the scope selects (doubles addendum 4.2); identities that are
unmapped or hold a free-text name only appear in the ``doubles`` insight's run list.
``scope.units`` counts the singles units **plus** these doubles identity units, so an
athlete who only played doubles still has a non-zero unit count. The selected athlete's
doubles grids are merged (sample-weighted) into ``movement.position``; nothing else of a
doubles unit reaches a singles KPI.

Stems without a date (no record date and no usable sidecar date) cannot be
placed in a range: they are left out of every bounded range (``7d``, ``30d``,
``season``) and of the trend, but still count for ``range="all"``.
"""
import datetime
import math
import statistics

from . import envelope
from ..library import athletes as lib
from .findings import build_findings
from .loaders import SIDES, number
from .technique import REFERENCE, TECHNIQUES

RANGES = ("7d", "30d", "season", "all")
_RANGE_DAYS = {"7d": 7, "30d": 30}
GRID_ROWS, GRID_COLS = 11, 10
OK_COUNT, FEW_COUNT = 10, 3
NEEDS_REVIEW_BELOW = 0.5
POINT_OUTCOME_KPIS = ("rally_win_rate", "unforced_errors")
IDENTITIES = lib.PLAYER_IDS
TEAM_IDS = lib.TEAMS
IDENTITY_STATUSES = ("ok", "degraded", "degraded_teams")
"""Statuses a usable ``doubles`` insight may carry; a run without one is ``error``/``missing``."""
NAME_MAX = 120


# ---------------------------------------------------------------- small helpers

def _dict(x):
    return x if isinstance(x, dict) else {}


MAX_COUNT = 10 ** 9
"""A count above this is hostile data, not a count: it is read as 0."""
MAX_SECONDS = 10 ** 6
MAX_DISTANCE_M = 10 ** 7
MAX_SPEED_MPS = 100.0
SCORE_RANGE = (0.0, 100.0)


def _count(x):
    n = number(x)
    return int(n) if n is not None and 0 <= n <= MAX_COUNT else 0


def _bounded(x, lo, hi):
    """A finite number within ``[lo, hi]``, else None (so sums can never overflow)."""
    n = number(x)
    return n if n is not None and lo <= n <= hi else None


def _share(x):
    return _bounded(x, 0.0, 1.0)


def _fin(x, default=0.0):
    return x if isinstance(x, (int, float)) and math.isfinite(x) else default


def _value(insights, key):
    """The value of one sidecar insight when it is ``ok``/``partial``, else None."""
    env = _dict(insights).get(key)
    if isinstance(env, dict) and env.get("status") in ("ok", "partial") \
            and isinstance(env.get("value"), dict):
        return env["value"]
    return None


def _env_of(insights, key):
    env = _dict(insights).get(key)
    return env if isinstance(env, dict) else {}


def _iso(value):
    """A real ``YYYY-MM-DD`` string, else None."""
    if not isinstance(value, str) or len(value) != 10:
        return None
    try:
        datetime.date.fromisoformat(value)
    except ValueError:
        return None
    return value


def _unavailable(units, code, params=None):
    """``no_data`` when nothing is selected, else ``code``."""
    return envelope.unavailable(code if units else "no_data", params)


def _first_reason(units, key):
    """The code of the first ``unavailable`` ``key`` insight among ``units``.

    ``no_data`` when none is unavailable (the values exist but carry nothing for
    the selected sides): never a code that claims a missing file.
    """
    for u in units:
        env = _env_of(u["insights"], key)
        reason = env.get("reason")
        if env.get("status") == "unavailable" and isinstance(reason, dict) \
                and isinstance(reason.get("code"), str):
            return reason["code"]
    return "no_data"


def _graded(value, n, of=None):
    """ok at >= 10, partial ``few_hits`` at 3-9, else None (caller says unavailable)."""
    coverage = None if of is None else {"n": n, "of": of}
    if n >= OK_COUNT:
        return envelope.ok(value, coverage)
    if n >= FEW_COUNT:
        return envelope.partial(value, "few_hits", coverage=coverage)
    return None


# ------------------------------------------------------------------- selection

def _store_parts(store):
    store = _dict(store)
    return {key: _dict(store.get(key)) for key in ("squads", "athletes", "matches")}


def _side_ident(parts, record, side):
    return lib.side_athlete(parts, record, side)


def _make_entries(units_source, parts, running):
    """One entry per distinct stem: record, date, per-side identity, usable insights."""
    entries, seen = [], set()
    for item in units_source or []:
        if not isinstance(item, dict) or not isinstance(item.get("stem"), str):
            continue
        stem = item["stem"]
        if stem in seen:
            continue
        seen.add(stem)
        insights = item.get("insights")
        record = _dict(parts["matches"].get(stem))
        record_date = _iso(record.get("date"))
        if record_date:
            date, source = record_date, "record"
        else:
            sidecar_date = _dict(_dict(insights).get("date"))
            date = _iso(sidecar_date.get("value"))
            raw_source = sidecar_date.get("source")
            source = raw_source if date and isinstance(raw_source, str) else None
        if stem in running:
            status = "processing"
        elif isinstance(insights, dict) and insights:
            status = "analysed"
        else:
            status = "new"
        entries.append({
            "stem": stem, "record": record, "date": date, "date_source": source,
            "status": status,
            # the sidecar decides for an analysed run (only the exact value counts; a missing
            # or malformed one is singles); without one, only the match record can tell
            "doubles": (_dict(insights).get("match_format") == "doubles"
                        if status == "analysed" else lib.record_format(record) == "doubles"),
            # processing / new stems have no (or in-flux) data: never aggregated
            "insights": insights if status == "analysed" else {},
            "ident": {side: _side_ident(parts, record, side) for side in SIDES},
        })
    return entries


def _side_selected(ident, athlete, squad, athletes):
    aid = ident["athlete_id"]
    if athlete is not None and aid != athlete:
        return False
    if squad is not None:
        member = _dict(athletes.get(aid)) if aid else {}
        if member.get("squad_id") != squad:
            return False
    return True


def _identities_selected(parts, record, athlete, squad):
    """``[(pid, ident)]``: the record's identities mapped to a registered athlete in scope.

    A free-text name is not an athlete, and a singles record maps no identity at all. An
    athlete counts once per match: the store rejects one athlete on two identities, but a
    hand-edited store may still hold it, so only the first identity (P1..P4 order) is kept.
    """
    out, seen = [], set()
    for pid in IDENTITIES:
        ident = lib.player_athlete(parts, record, pid)
        aid = ident["athlete_id"]
        if aid is None or aid in seen:
            continue
        seen.add(aid)
        if _side_selected(ident, athlete, squad, parts["athletes"]):
            out.append((pid, ident))
    return out


def _bounds(range_, anchor):
    """``(lower, upper)`` dates, or None for unbounded (``all`` / nothing dated)."""
    if range_ == "all" or anchor is None:
        return None
    if range_ == "season":
        return datetime.date(anchor.year, 1, 1), anchor
    return anchor - datetime.timedelta(days=_RANGE_DAYS[range_] - 1), anchor


def _in_range(date, range_, bounds):
    if range_ == "all":
        return True
    if date is None or bounds is None:
        return False            # undated stems cannot be placed in a bounded range
    return bounds[0] <= datetime.date.fromisoformat(date) <= bounds[1]


# ---------------------------------------------------------------------- radar

def _unit_side(unit):
    """The technique ``Side`` of a unit, or None."""
    by_side = _dict(_value(unit["insights"], "technique")).get("by_side")
    side = _dict(by_side).get(unit["side"])
    return side if isinstance(side, dict) and isinstance(side.get("techniques"), dict) else None


def _radar_scores(units):
    """``[(technique, report_count, count-weighted mean score | None)]``."""
    out = []
    for tech in TECHNIQUES:
        count = weight = 0
        total = 0.0
        for u in units:
            side = _unit_side(u)
            if side is None:
                continue
            cell = _dict(side["techniques"].get(tech))
            c = _count(cell.get("count"))
            score = _bounded(cell.get("avg_score"), *SCORE_RANGE)
            count += c
            if score is not None and c > 0:
                total += score * c
                weight += c
        out.append((tech, count, _fin(total / weight, None) if weight else None))
    return out


def _index(scores):
    scored = [s for _t, _c, s in scores if s is not None]
    return (round(statistics.fmean(scored), 1) if scored else None), len(scored)


def _radar(scores):
    return {"reference": dict(REFERENCE),
            "axes": [{"id": t, "count": c,
                      "score": None if s is None else round(s, 1),
                      "status": "unavailable" if s is None else "ok"}
                     for t, c, s in scores]}


def _trend(entries, units):
    by_stem = {}
    for u in units:
        by_stem.setdefault(u["stem"], []).append(u)
    points = []
    for e in entries:
        if e["date"] is None or e["stem"] not in by_stem:
            continue
        index, _scored = _index(_radar_scores(by_stem[e["stem"]]))
        if index is not None:
            points.append({"date": e["date"], "stem": e["stem"], "index": index})
    points.sort(key=lambda p: (p["date"], p["stem"]))
    return points


def _technique_kpi(units, scores, points):
    index, scored = _index(scores)
    if index is None:
        return _unavailable(units, "no_technique")
    delta = round(points[-1]["index"] - points[0]["index"], 1) if len(points) >= 2 else None
    value = {"value": index, "delta": delta, "scored": scored}
    coverage = {"n": scored, "of": len(TECHNIQUES)}
    unscored = [t for t, _c, s in scores if s is None]
    if unscored:
        return envelope.partial(value, "unscored_technique", {"names": unscored}, coverage)
    return envelope.ok(value, coverage)


def _benchmark(athlete, parts, all_entries, range_, bounds, has_units):
    if not has_units:
        return envelope.unavailable("no_data")
    squad_id = _dict(parts["athletes"].get(athlete)).get("squad_id") if athlete else None
    if not squad_id:
        return envelope.unavailable("no_benchmark")
    indexes = []
    for e in all_entries:
        if e["doubles"] or not _in_range(e["date"], range_, bounds):
            continue
        for side in SIDES:
            aid = e["ident"][side]["athlete_id"]
            if aid is None or aid == athlete:
                continue
            if _dict(parts["athletes"].get(aid)).get("squad_id") != squad_id:
                continue
            unit = {"stem": e["stem"], "side": side, "insights": e["insights"]}
            index = _bounded(_dict(_unit_side(unit)).get("index"), *SCORE_RANGE)
            if index is not None:
                indexes.append(index)
    if not indexes:
        return envelope.unavailable("no_benchmark")
    return envelope.ok({"index": round(statistics.median(indexes), 1)},
                       {"n": len(indexes), "of": len(indexes)})


# ----------------------------------------------------------------------- kpis

def _match_win_rate(units, scoped):
    if not units or not scoped:
        return envelope.unavailable("no_data")
    played = wins = 0
    for u in units:
        winner = _dict(_dict(u["record"]).get("result")).get("winner")
        if winner in SIDES:
            played += 1
            wins += winner == u["side"]
    if not played:
        return envelope.unavailable("no_results_entered")
    return envelope.ok({"wins": wins, "played": played, "rate": round(wins / played, 3)},
                       {"n": played, "of": len(units)})


def _stem_entries(units):
    """One representative unit per stem (insights that describe the whole video)."""
    seen, out = set(), []
    for u in units:
        if u["stem"] not in seen:
            seen.add(u["stem"])
            out.append(u)
    return out


def _avg_rally(units):
    stems = _stem_entries(units)
    durations, shots, worst, signal, with_rally = [], [], 0, None, 0
    shots_missing = False
    for u in stems:
        env = _env_of(u["insights"], "rally")
        value = _value(u["insights"], "rally")
        raw = _dict(value).get("durations_sec")
        durs = ([d for d in (_bounded(x, 0.0, MAX_SECONDS) for x in raw) if d is not None]
                if isinstance(raw, list) else [])
        if not durs:
            continue
        with_rally += 1
        durations.extend(durs)
        per = value.get("shots_per_rally")
        got = ([s for s in (_bounded(x, 0.0, MAX_COUNT) for x in per) if s is not None]
               if isinstance(per, list) else [])
        if got:
            shots.extend(got)
        else:
            shots_missing = True        # a unit without shot counts makes the mean unknown
        code = _dict(env.get("reason")).get("code")
        rank = {"signal_degraded": 2, "signal_swing": 1}.get(code, 0)
        label = {2: "degraded", 1: "swing"}.get(rank, value.get("signal"))
        if signal is None or rank > worst:
            worst, signal = rank, label
    if not durations:
        return _unavailable(units, "no_rallies")
    value = {"seconds": round(statistics.fmean(durations), 2),
             "shots": None if shots_missing or not shots else round(statistics.fmean(shots), 2),
             "signal": signal}
    coverage = {"n": with_rally, "of": len(stems)}
    if worst:
        return envelope.partial(value, "signal_degraded" if worst == 2 else "signal_swing",
                                coverage=coverage)
    return envelope.ok(value, coverage)


def _recovery(units):
    medians, found, hits = [], 0, 0
    seen = False
    for u in units:
        stats = _dict(_dict(_value(u["insights"], "recovery")).get(u["side"]))
        if not stats:
            continue
        seen = True
        found += _count(stats.get("found"))
        hits += _count(stats.get("hits"))
        median = _bounded(stats.get("median_sec"), 0.0, MAX_SECONDS)
        if median is not None and _count(stats.get("found")) > 0:
            medians.append(median)
    if not seen:
        return _unavailable(units, _first_reason(units, "recovery"))
    if not medians:
        return _unavailable(units, "no_hits")
    value = {"median_sec": round(statistics.median(medians), 3)}
    return _graded(value, found, hits) or envelope.unavailable("no_hits")


def _unit_hitter(unit):
    """The unit's ``by_hitter[side]`` dict from the strokes insight (``{}`` if absent)."""
    by_hitter = _dict(_dict(_value(unit["insights"], "strokes")).get("by_hitter"))
    return _dict(by_hitter.get(unit["side"]))


def _strokes_and_net(units):
    """``(strokes_kpi, net_share_kpi)``, both over the selected units' own strokes."""
    classified = total = net = 0
    for u in units:
        side = _unit_hitter(u)
        classified += _count(side.get("classified"))
        total += _count(side.get("total"))
        net += _count(_dict(side.get("distribution")).get("net"))
    if not total:
        code = _first_reason(units, "strokes")
        return _unavailable(units, code), _unavailable(units, code)
    value = {"classified": classified, "total": total}
    coverage = {"n": classified, "of": total}
    low = classified * 10 < total * 6
    strokes = (envelope.partial(value, "low_stroke_coverage", dict(value), coverage)
               if low else envelope.ok(value, coverage))
    if not classified:
        return strokes, _unavailable(units, "no_data")
    share = {"share": round(net / classified, 4)}
    if low:
        return strokes, envelope.partial(share, "low_stroke_coverage", dict(value), coverage)
    return strokes, envelope.ok(share, coverage)


def _unit_pulls(units):
    out = []
    for u in units:
        pull = _dict(_dict(_value(u["insights"], "pull")).get(u["side"]))
        if _count(pull.get("n")) > 0 and _share(pull.get("back")) is not None:
            out.append(pull)
    return out


def _pull_rear(units):
    pulls = _unit_pulls(units)
    if not pulls:
        return _unavailable(units, _first_reason(units, "pull"))
    value = {"share": round(statistics.fmean(_share(p["back"]) for p in pulls), 4)}
    return _graded(value, sum(_count(p["n"]) for p in pulls)) \
        or envelope.unavailable("no_hits")


# ------------------------------------------------------------------- movement

def _grid_ok(grid):
    return (isinstance(grid, list) and len(grid) == GRID_ROWS
            and all(isinstance(r, list) and len(r) == GRID_COLS
                    and all(_share(c) is not None for c in r) for r in grid))


def _merge_grids(parts):
    """Weighted mean of ``[(weight, grid)]``, 4 decimals."""
    total = sum(w for w, _g in parts)
    return [[round(_fin(sum(w * g[r][c] for w, g in parts) / total), 4)
             for c in range(GRID_COLS)] for r in range(GRID_ROWS)]


def _position(units, doubles=()):
    """Merged court occupancy of the singles ``units`` plus ``doubles``.

    ``doubles`` is ``[(player_row | None, doubles_envelope)]`` for the selected athlete's
    doubles identities (athlete scope only): their grids merge sample-weighted like a
    singles side's.

    ``distance_m`` sums the known distances (null when none is known). ``mean_speed_mps``
    is total distance / total time over the sources whose distance **and** speed are both
    known (null when there are none), so a source without a speed never inflates it.
    """
    parts, statuses, first_limit = [], [], None
    distances, paired_distance, span = [], 0.0, 0.0
    sides = set()
    sources = [(_dict(_dict(_value(u["insights"], "movement")).get(u["side"])),
                _env_of(u["insights"], "movement"), u["side"]) for u in units]
    sources += [(_dict(row), env, None) for row, env in doubles]
    for grid_side, env, side in sources:
        samples = _count(grid_side.get("samples"))
        if not samples or not _grid_ok(grid_side.get("grid")):
            continue
        parts.append((samples, grid_side["grid"]))
        if side is not None:
            sides.add(side)
        speed_key = "mean_speed_mps" if side is not None else "avg_speed_mps"
        dist = _bounded(grid_side.get("distance_m"), 0.0, MAX_DISTANCE_M)
        speed = _bounded(grid_side.get(speed_key), 0.0, MAX_SPEED_MPS)
        if dist is not None:
            distances.append(dist)
            if speed is not None and speed > 0:
                paired_distance += dist
                span += _fin(dist / speed)
        statuses.append(env.get("status"))
        if env.get("status") != "ok" and first_limit is None \
                and isinstance(env.get("reason"), dict):
            first_limit = env["reason"]
    if not parts:
        return _unavailable(units, _first_reason(units, "movement"))
    # rows count from the net on both halves, so the two bases coincide in grid terms
    base = {"x": 3.05, "y": 10.05} if sides == {"lower"} else {"x": 3.05, "y": 3.35}
    value = {"grid": _merge_grids(parts), "samples": sum(w for w, _g in parts),
             "distance_m": round(_fin(sum(distances)), 2) if distances else None,
             "mean_speed_mps": round(_fin(paired_distance / span), 2) if span > 0 else None,
             "base": base}
    coverage = {"n": len(parts), "of": len(units) + len(doubles)}
    if first_limit is not None and isinstance(first_limit.get("code"), str):
        return envelope.partial(value, first_limit["code"],
                                _dict(first_limit.get("params")), coverage)
    return envelope.ok(value, coverage)


def _pull_position(units):
    pulls = _unit_pulls(units)
    pulls = [p for p in pulls if _grid_ok(p.get("grid"))]
    if not pulls:
        return _unavailable(units, _first_reason(units, "pull"))
    weights = [(_count(p["n"]), p) for p in pulls]
    total = sum(w for w, _p in weights)

    def mean(key):
        return round(_fin(sum(w * (_share(p.get(key)) or 0.0) for w, p in weights) / total), 4)

    value = {"grid": _merge_grids([(w, p["grid"]) for w, p in weights]),
             "front": mean("front"), "mid": mean("mid"), "back": mean("back"),
             "left": mean("left"), "right": mean("right"), "n": total}
    return _graded(value, total) or envelope.unavailable("no_hits")


# -------------------------------------------------------------------- doubles

def _opt_count(x):
    """A whole count in ``[0, MAX_COUNT]``, else None (never a made-up 0)."""
    n = number(x)
    return int(n) if n is not None and 0 <= n <= MAX_COUNT and n == int(n) else None


def _team(x):
    return x if type(x) is int and x in TEAM_IDS else None


def _name(x):
    return x.strip()[:NAME_MAX] if isinstance(x, str) and x.strip() else None


def _identity_players(value, status):
    """Validated player rows of a ``doubles`` insight value, in file order.

    Known ids only (the first of a duplicate wins); every number re-checked, a bad one
    is None. Under ``degraded_teams`` no team is trusted, whatever the row says.
    """
    raw = _dict(value).get("players")
    rows, seen = [], set()
    for p in raw if isinstance(raw, list) else ():
        pid = p.get("id") if isinstance(p, dict) else None
        if not isinstance(pid, str) or pid not in IDENTITIES or pid in seen:
            continue
        seen.add(pid)
        hits, named = _opt_count(p.get("hits")), _opt_count(p.get("hits_named"))
        samples = _opt_count(p.get("samples"))
        grid = p.get("grid") if samples and _grid_ok(p.get("grid")) else None
        rows.append({
            "id": pid,
            "team": None if status == "degraded_teams" else _team(p.get("team")),
            "name": _name(p.get("name")),
            "coverage": _share(p.get("coverage")),
            "frames": _opt_count(p.get("frames")),
            "distance_m": _bounded(p.get("distance_m"), 0.0, MAX_DISTANCE_M),
            "avg_speed_mps": _bounded(p.get("avg_speed_mps"), 0.0, MAX_SPEED_MPS),
            "max_speed_mps": _bounded(p.get("max_speed_mps"), 0.0, MAX_SPEED_MPS),
            "hits": hits,
            "hits_named": named if named is not None and hits is not None and named <= hits
            else None,
            "samples": samples,
            "grid": grid,
        })
    return rows


def _identity_teams(value, status, players):
    if status == "degraded_teams":
        return []
    known = {p["id"] for p in players}
    raw = _dict(value).get("teams")
    out, seen = [], set()
    for t in raw if isinstance(raw, list) else ():
        tid = _team(t.get("id")) if isinstance(t, dict) else None
        if tid is None or tid in seen:
            continue
        seen.add(tid)
        members = t.get("players")
        ids = []
        for m in members if isinstance(members, list) else ():
            if isinstance(m, str) and m in known and m not in ids:
                ids.append(m)
        out.append({"id": tid, "players": ids, "hits": _opt_count(t.get("hits")),
                    "hits_named": _opt_count(t.get("hits_named"))})
    return out


def _identity_info(entry):
    """``{status, env, value, players, by_id}`` of one doubles stem.

    ``status`` is the identity status: the value's ``ok|degraded|degraded_teams`` (an
    unknown one, or ``ok`` under a non-ok envelope, is ``degraded``), else ``error`` for
    a ``doubles_error`` run and ``missing`` for any other run without a usable value.
    ``None`` for a stem that is not analysed (no data yet).
    """
    if entry["status"] != "analysed":
        return {"status": None, "env": {}, "value": None, "players": [], "by_id": {}}
    env = _env_of(entry["insights"], "doubles")
    value = _value(entry["insights"], "doubles")
    if value is None:
        code = _dict(env.get("reason")).get("code")
        status = "error" if code == "doubles_error" else "missing"
    else:
        status = value.get("status")
        if status not in IDENTITY_STATUSES or (status == "ok" and env.get("status") != "ok"):
            status = "degraded"
    players = _identity_players(value, status)
    return {"status": status, "env": env, "value": value, "players": players,
            "by_id": {p["id"]: p for p in players}}


def _identity_name(parts, record, pid, info):
    """The record's athlete (current registry name) or free-text name, else the identity's
    own ``players.json`` name, else None."""
    return (lib.player_athlete(parts, record, pid)["name"]
            or _dict(info["by_id"].get(pid)).get("name"))


def _doubles_run(entry, info, parts):
    players = []
    for p in info["players"]:
        row = {k: v for k, v in p.items() if k != "grid"}
        row["athlete"] = _identity_name(parts, entry["record"], p["id"], info)
        players.append(row)
    return {"stem": entry["stem"], "date": entry["date"], "date_source": entry["date_source"],
            "status": info["status"], "validated": False,
            "teams": _identity_teams(info["value"], info["status"], info["players"]),
            "players": players}


def _share_grid(parts):
    """Sample-weighted merge of ``[(samples, grid)]``, renormalised so the shares sum to 1."""
    if not parts:
        return None
    cells = [[sum(w * g[r][c] for w, g in parts) for c in range(GRID_COLS)]
             for r in range(GRID_ROWS)]
    total = sum(map(sum, cells))
    if not (math.isfinite(total) and total > 0):
        return None
    return [[round(_fin(v / total), 4) for v in row] for row in cells]


def _athlete_summary(rows, matches):
    """The selected athlete's identity across their doubles matches (addendum 4.2).

    Distance, frames and hits are summed, speeds frame-weighted (max: the highest),
    grids sample-weighted. Null numbers are skipped; a sum of nothing is None, not 0.
    ``hits_named_of`` is the hits of the rows that also report ``hits_named``, and
    ``named_share = hits_named / hits_named_of``, so "N named of M" is always consistent.
    """
    rows = [r for r in rows if r]

    def total(key):
        values = [r[key] for r in rows if r[key] is not None]
        return sum(values) if values else None

    distance, frames, hits = total("distance_m"), total("frames"), total("hits")
    speeds = [(r["frames"], r["avg_speed_mps"]) for r in rows
              if r["frames"] and r["avg_speed_mps"] is not None]
    weight = sum(f for f, _s in speeds)
    maxes = [r["max_speed_mps"] for r in rows if r["max_speed_mps"] is not None]
    pairs = [(r["hits"], r["hits_named"]) for r in rows if r["hits_named"] is not None]
    named = sum(n for _h, n in pairs) if pairs else None
    of = sum(h for h, _n in pairs)
    grids = [(r["samples"], r["grid"]) for r in rows if r["grid"] is not None and r["samples"]]
    grid = _share_grid(grids)
    return {
        "distance_m": None if distance is None else round(_fin(distance), 2),
        "avg_speed_mps": round(_fin(sum(f * s for f, s in speeds) / weight), 2) if weight else None,
        "max_speed_mps": max(maxes) if maxes else None,
        "frames": frames, "hits": hits, "hits_named": named,
        # the hits of the rows that report a named count: the honest "of" for hits_named
        "hits_named_of": of if pairs else None,
        "named_share": round(named / of, 4) if pairs and of else None,
        "matches": matches,
        "grid": grid, "samples": sum(w for w, _g in grids) if grid is not None else None,
    }


_RUN_REASONS = {"error": ("doubles_error", None), "missing": ("no_players", None)}


def _doubles_insight(entries, units, infos, parts, athlete):
    """The overview's ``doubles`` Insight over the doubles stems in scope.

    ``ok`` when every analysed run is ``ok``; ``partial`` (with the first non-ok run's
    reason, newest first) when any is degraded, failed or has no identities;
    ``unavailable`` + ``no_data`` when no analysed doubles stem is in scope.
    ``matches`` and ``runs`` count **analysed** doubles stems only: a ``new`` or
    ``processing`` doubles stem is a row in ``matches`` but has no run data yet.
    """
    analysed = [e for e in entries if infos[e["stem"]]["status"] is not None]
    if not analysed:
        return envelope.unavailable("no_data")
    runs = [_doubles_run(e, infos[e["stem"]], parts) for e in analysed]
    runs.sort(key=lambda r: r["stem"])
    runs.sort(key=lambda r: r["date"] or "", reverse=True)       # newest first, undated last
    summary = None
    mine = [u for u in units if infos[u["stem"]]["status"] is not None]
    if athlete is not None and mine:
        summary = _athlete_summary([infos[u["stem"]]["by_id"].get(u["pid"]) for u in mine],
                                   len(mine))
    value = {"matches": len(analysed), "athlete": summary, "runs": runs}
    coverage = {"n": sum(r["status"] in IDENTITY_STATUSES for r in runs), "of": len(runs)}
    bad = next((r for r in runs if r["status"] != "ok"), None)
    if bad is None:
        return envelope.ok(value, coverage)
    code, params = _RUN_REASONS.get(bad["status"], ("doubles_degraded", {"status": bad["status"]}))
    return envelope.partial(value, code, params, coverage)


def _team_sets(result, team):
    """Team-1-first stored pairs oriented ``[own, opp]`` for ``team`` (as stored when unknown)."""
    if not isinstance(result, dict):
        return None
    sets = result.get("sets")
    out = []
    for p in sets if isinstance(sets, list) else []:
        if isinstance(p, (list, tuple)) and len(p) == 2:
            out.append([p[1], p[0]] if team == 2 else [p[0], p[1]])
    return out


def _team_names(record, info, parts):
    """``["A / B", "C / D"]`` (team 1 first) for a run's teams, or None without team info.

    Each identity reads as its resolved name (``_identity_name``), else its id ("P2"). A
    team with nobody on it is None.
    """
    players = sorted(info["players"], key=lambda p: p["id"])
    if not any(p["team"] is not None for p in players):
        return None
    out = []
    for team in TEAM_IDS:
        labels = [_identity_name(parts, record, p["id"], info) or p["id"]
                  for p in players if p["team"] == team]
        out.append(" / ".join(labels) or None)
    return out


def _doubles_row(entry, pid, info, parts):
    """A ``matches`` row for one doubles identity, or for the whole stem when ``pid`` is None."""
    record = entry["record"]
    result = record.get("result") if lib.record_format(record) == "doubles" else None
    me = info["by_id"].get(pid) if pid is not None else None
    team = me["team"] if me else None
    partner = opponent = None
    if team is not None:
        def names(on_team):
            got = [_identity_name(parts, record, p["id"], info)
                   for p in sorted(info["players"], key=lambda p: p["id"])
                   if p["team"] == on_team and p["id"] != pid]
            return [n for n in got if n]

        mates = names(team)
        partner = mates[0] if mates else None
        opponent = " / ".join(names(3 - team)) or None
    winner = _team(_dict(result).get("winner_team"))
    outcome = None
    if team is not None and winner is not None:
        outcome = "W" if winner == team else "L"
    return {
        "stem": entry["stem"], "date": entry["date"], "date_source": entry["date_source"],
        "side": None,
        "athlete": None if pid is None else _identity_name(parts, record, pid, info),
        "opponent": opponent, "result": outcome, "sets": _team_sets(result, team),
        "tech_index": None, "status": entry["status"],
        "needs_review": info["status"] is not None and info["status"] != "ok",
        "doubles": True, "format": "doubles", "athlete_player": pid, "team": team,
        "partner": partner, "teams": _team_names(record, info, parts),
    }


# ---------------------------------------------------------------------- rows

def _stem_needs_review(insights):
    v = _value(insights, "strokes")
    if v is None:
        return False
    total, classified = _count(v.get("total")), _count(v.get("classified"))
    return bool(total) and classified < total * NEEDS_REVIEW_BELOW


def _sets_pairs(result, side):
    """``[[own, opp], ...]`` for ``side``: the store holds ``[upper, lower]`` pairs.

    ``None`` without a result; a result without sets gives ``[]``. A row with no
    known side keeps the stored (upper-first) order.
    """
    if not isinstance(result, dict):
        return None
    sets = result.get("sets")
    out = []
    for p in sets if isinstance(sets, list) else []:
        if isinstance(p, (list, tuple)) and len(p) == 2:
            out.append([p[1], p[0]] if side == "lower" else [p[0], p[1]])
    return out


def _rows(units, doubles_rows=()):
    """Singles rows (one per unit) plus the prepared doubles rows, newest first."""
    rows = []
    for u in units:
        other = "lower" if u["side"] == "upper" else "upper"
        result = _dict(u["record"]).get("result")
        winner = _dict(result).get("winner")
        outcome = None
        if u["side"] in SIDES and winner in SIDES:       # relative to this unit's own side
            outcome = "W" if winner == u["side"] else "L"
        rows.append({
            "stem": u["stem"], "date": u["date"], "date_source": u["date_source"],
            "side": u["side"], "athlete": u["ident"][u["side"]]["name"],
            "opponent": u["ident"][other]["name"], "result": outcome,
            "sets": _sets_pairs(result, u["side"]),
            "tech_index": _bounded(_dict(_unit_side(u)).get("index"), *SCORE_RANGE),
            "status": u["status"], "needs_review": _stem_needs_review(u["insights"]),
            "doubles": False,
        })
    rows.extend(doubles_rows)
    rows.sort(key=lambda r: (r["stem"], r["side"] or "", r.get("athlete_player") or ""))
    rows.sort(key=lambda r: r["date"] or "", reverse=True)       # newest first, undated last
    return rows


# ------------------------------------------------------------------- overview

def overview(units_source, athletes_store, *, athlete=None, squad=None, range_="30d",
             running_stems=frozenset()):
    """The spec 5.4 response (plus doubles addendum 4.2) for the athlete / squad / range.

    Athlete scope selects the doubles stems where the athlete is mapped to an identity,
    squad scope those where any identity is a squad member's, no scope every doubles
    stem. ``scope.units`` = singles units + mapped doubles identity units (see the
    module docstring). ``units_source`` is ``[{"stem", "insights"}]`` (sidecar dicts); raises
    ``LookupError("athlete")`` / ``LookupError("squad")`` for unknown ids and
    ``ValueError("range")`` for a bad range.
    """
    if range_ not in RANGES:
        raise ValueError("range")
    parts = _store_parts(athletes_store)
    if athlete is not None and not (isinstance(athlete, str) and athlete in parts["athletes"]):
        raise LookupError("athlete")
    if squad is not None and not (isinstance(squad, str) and squad in parts["squads"]):
        raise LookupError("squad")

    entries = _make_entries(units_source, parts, frozenset(running_stems or ()))
    scoped = athlete is not None or squad is not None
    candidates = []
    for e in entries:
        if e["doubles"]:
            # (pid, ident) pairs; with no scope every doubles stem is in, mapped or not
            picked = _identities_selected(parts, e["record"], athlete, squad)
            if picked or not scoped:
                candidates.append((e, picked))
            continue
        sides = [s for s in SIDES
                 if _side_selected(e["ident"][s], athlete, squad, parts["athletes"])]
        if sides:
            candidates.append((e, sides))

    dates = [e["date"] for e, _s in candidates if e["date"]]
    anchor = datetime.date.fromisoformat(max(dates)) if dates else None
    bounds = _bounds(range_, anchor)
    chosen = [(e, s) for e, s in candidates if _in_range(e["date"], range_, bounds)]

    # ``units`` are singles (stem, side) units: they feed every per-player aggregate.
    # ``doubles_units`` are (stem, identity) units of mapped athletes: they feed the
    # ``doubles`` insight, the athlete's court map and the rows. ``scope.units`` counts both.
    units = [dict(e, side=side) for e, sides in chosen if not e["doubles"] for side in sides]
    doubles_entries = [e for e, _p in chosen if e["doubles"]]
    doubles_units = [dict(e, side=None, pid=pid) for e, picked in chosen if e["doubles"]
                     for pid, _ident in picked]
    infos = {e["stem"]: _identity_info(e) for e in doubles_entries}
    if scoped:
        doubles_rows = [_doubles_row(u, u["pid"], infos[u["stem"]], parts) for u in doubles_units]
    else:       # no perspective: one stem-level row, sets team-1-first
        doubles_rows = [_doubles_row(e, None, infos[e["stem"]], parts) for e in doubles_entries]
    # rally windows are not player-specific: every selected stem's count once
    rally_units = units + [{"stem": e["stem"], "insights": e["insights"]} for e in doubles_entries]
    athlete_doubles = ([(infos[u["stem"]]["by_id"].get(u["pid"]), infos[u["stem"]]["env"])
                        for u in doubles_units] if athlete is not None else [])
    chosen_entries = [e for e, _s in chosen]
    selected_dates = sorted(e["date"] for e in chosen_entries if e["date"])
    if bounds is not None:
        date_from, date_to = bounds[0].isoformat(), bounds[1].isoformat()
    else:
        date_from = selected_dates[0] if selected_dates else None
        date_to = selected_dates[-1] if selected_dates else None

    scores = _radar_scores(units)
    points = _trend(chosen_entries, units)
    strokes_kpi, net_kpi = _strokes_and_net(units)
    athlete_row = parts["athletes"].get(athlete) if athlete is not None else None
    squad_row = parts["squads"].get(squad) if squad is not None else None
    no_outcome = envelope.unavailable("no_point_outcome")
    doubles_only = not units and bool(doubles_entries)     # every selected stem is doubles

    def player(insight):
        """With only doubles in scope, 'no data' is really 'doubles' (never a false 'missing')."""
        reason = _dict(insight.get("reason")).get("code")
        if doubles_only and insight.get("status") == "unavailable" and reason == "no_data":
            return envelope.unavailable("doubles_run")
        return insight

    result = {
        "scope": {
            "athlete": None if athlete_row is None else {
                "id": athlete, "name": _dict(athlete_row).get("name"),
                "squad_id": _dict(athlete_row).get("squad_id")},
            "squad": None if squad_row is None else {
                "id": squad, "name": _dict(squad_row).get("name")},
            "range": range_, "stems": len(chosen_entries),
            "units": len(units) + len(doubles_units),
            "date_from": date_from, "date_to": date_to,
            "generated_at": datetime.datetime.now(datetime.timezone.utc)
            .strftime("%Y-%m-%dT%H:%M:%SZ"),
        },
        "kpis": {
            "technique_index": player(_technique_kpi(units, scores, points)),
            "match_win_rate": player(_match_win_rate(units, scoped)),
            "avg_rally": _avg_rally(rally_units),
            "rally_win_rate": dict(no_outcome),
            "recovery": player(_recovery(units)),
            "net_share": player(net_kpi),
            "pull_rear": player(_pull_rear(units)),
            "unforced_errors": dict(no_outcome),
            "strokes": player(strokes_kpi),
        },
        "radar": _radar(scores),
        "trend": {"points": points,
                  "benchmark": _benchmark(athlete, parts, entries, range_, bounds,
                                          bool(units))},
        "movement": {"position": player(_position(units, athlete_doubles)),
                     "pull": player(_pull_position(units))},
        "doubles": _doubles_insight(doubles_entries, doubles_units, infos, parts, athlete),
        "matches": _rows(units, doubles_rows),
    }
    result["findings"] = build_findings(result)
    return result
