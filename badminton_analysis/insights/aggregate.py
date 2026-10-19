"""Overview aggregation over many videos (spec 5.4).

Pure: it works only from ``units_source`` (sidecar dicts the caller supplies)
and the athlete/match store; it does no file I/O. A *unit* is one
``(stem, side)``; a stem's rally and stroke-distribution insights belong to the
whole video, so they are counted once per stem, not once per unit.

A *doubles* stem (sidecar ``match_format == "doubles"``) has no per-player data: its
legacy tracker output is one representative per side of a 4-player match. It is a
stem in the scope and a row in ``matches`` (``doubles: true``, ``tech_index: null``) and its
rally windows still feed ``avg_rally``, but it is not a player unit: it adds nothing to the
technique, movement, recovery, pull, stroke or match-win aggregates, the trend or the
benchmark, and ``scope.units`` does not count it.

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
            # only the exact value counts; a missing or malformed one is singles
            "doubles": status == "analysed" and _dict(insights).get("match_format") == "doubles",
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


def _position(units):
    parts, distance, span, statuses, first_limit = [], 0.0, 0.0, [], None
    sides = set()
    for u in units:
        env = _env_of(u["insights"], "movement")
        grid_side = _dict(_dict(_value(u["insights"], "movement")).get(u["side"]))
        samples = _count(grid_side.get("samples"))
        if not samples or not _grid_ok(grid_side.get("grid")):
            continue
        parts.append((samples, grid_side["grid"]))
        sides.add(u["side"])
        dist = _bounded(grid_side.get("distance_m"), 0.0, MAX_DISTANCE_M) or 0.0
        speed = _bounded(grid_side.get("mean_speed_mps"), 0.0, MAX_SPEED_MPS) or 0.0
        distance += dist
        span += _fin(dist / speed) if speed > 0 else 0.0
        statuses.append(env.get("status"))
        if env.get("status") != "ok" and first_limit is None:
            first_limit = env.get("reason")
    if not parts:
        return _unavailable(units, _first_reason(units, "movement"))
    # rows count from the net on both halves, so the two bases coincide in grid terms
    base = {"x": 3.05, "y": 10.05} if sides == {"lower"} else {"x": 3.05, "y": 3.35}
    value = {"grid": _merge_grids(parts), "samples": sum(w for w, _g in parts),
             "distance_m": round(_fin(distance), 2),
             "mean_speed_mps": round(_fin(distance / span), 2) if span > 0 else 0.0,
             "base": base}
    coverage = {"n": len(parts), "of": len(units)}
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


def _rows(units):
    rows = []
    for u in units:
        other = "lower" if u["side"] == "upper" else "upper"
        result = _dict(u["record"]).get("result")
        winner = _dict(result).get("winner")
        outcome = None
        if u["side"] in SIDES and winner in SIDES:       # relative to this unit's own side
            outcome = "W" if winner == u["side"] else "L"
        index = None if u["doubles"] else _bounded(_dict(_unit_side(u)).get("index"),
                                                    *SCORE_RANGE)
        rows.append({
            "stem": u["stem"], "date": u["date"], "date_source": u["date_source"],
            "side": u["side"], "athlete": u["ident"][u["side"]]["name"],
            "opponent": u["ident"][other]["name"], "result": outcome,
            "sets": _sets_pairs(result, u["side"]),
            "tech_index": index, "status": u["status"],
            "needs_review": not u["doubles"] and _stem_needs_review(u["insights"]),
            "doubles": u["doubles"],
        })
    rows.sort(key=lambda r: (r["stem"], r["side"]))
    rows.sort(key=lambda r: r["date"] or "", reverse=True)       # newest first, undated last
    return rows


# ------------------------------------------------------------------- overview

def overview(units_source, athletes_store, *, athlete=None, squad=None, range_="30d",
             running_stems=frozenset()):
    """The spec 5.4 response for the selected athlete / squad / range.

    ``units_source`` is ``[{"stem", "insights"}]`` (sidecar dicts); raises
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
    candidates = []
    for e in entries:
        sides = [s for s in SIDES
                 if _side_selected(e["ident"][s], athlete, squad, parts["athletes"])]
        if sides:
            candidates.append((e, sides))

    dates = [e["date"] for e, _s in candidates if e["date"]]
    anchor = datetime.date.fromisoformat(max(dates)) if dates else None
    bounds = _bounds(range_, anchor)
    chosen = [(e, s) for e, s in candidates if _in_range(e["date"], range_, bounds)]

    all_units = [dict(e, side=side) for e, sides in chosen for side in sides]
    # Player units: a doubles side is one representative of a 4-player team, not a player.
    # ``units`` feeds every per-player aggregate and ``scope.units``; ``all_units`` only the
    # rally average (rally windows are not player-specific) and the match rows.
    units = [u for u in all_units if not u["doubles"]]
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
    scoped = athlete is not None or squad is not None
    athlete_row = parts["athletes"].get(athlete) if athlete is not None else None
    squad_row = parts["squads"].get(squad) if squad is not None else None
    no_outcome = envelope.unavailable("no_point_outcome")
    doubles_only = not units and bool(all_units)       # every selected unit is doubles

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
            "range": range_, "stems": len(chosen_entries), "units": len(units),
            "date_from": date_from, "date_to": date_to,
            "generated_at": datetime.datetime.now(datetime.timezone.utc)
            .strftime("%Y-%m-%dT%H:%M:%SZ"),
        },
        "kpis": {
            "technique_index": player(_technique_kpi(units, scores, points)),
            "match_win_rate": player(_match_win_rate(units, scoped)),
            "avg_rally": _avg_rally(all_units),
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
        "movement": {"position": player(_position(units)),
                     "pull": player(_pull_position(units))},
        "matches": _rows(all_units),
    }
    result["findings"] = build_findings(result)
    return result
