"""The per-player / per-team ``doubles`` insight (doubles addendum 4.1).

Built from a doubles run's ``players.json`` (identities, teams, movement numbers),
``tracklets.jsonl`` (court points, for the occupancy grids) and the who-hit fields
of ``hits.json``. Every field is validated: a value that is missing, of the wrong
type or out of range becomes ``None`` rather than a made-up number. Who-hit figures
are never validated against labels, so the value always says ``validated: false``.
"""
from . import envelope
from .loaders import number, tracklet_id
from .movement import GRID_COLS, GRID_ROWS, MIN_SAMPLES, NET_Y, cell

PLAYER_IDS = ("P1", "P2", "P3", "P4")
TEAM_IDS = (1, 2)
NAMED_EVIDENCE = ("shuttle", "swing")     # tracking/doubles.py _is_named
DEGRADED = ("degraded", "degraded_teams")
NAME_MAX = 120
# Generous plausibility bounds; anything outside is a corrupt value, not a measurement.
MAX_DISTANCE_M = 100_000.0
MAX_SPEED_MPS = 50.0
MAX_FRAMES = 10 ** 9


def _measure(value, hi, digits):
    """``value`` rounded when it is a finite number in ``[0, hi]``, else None."""
    n = number(value)
    return round(n, digits) if n is not None and 0.0 <= n <= hi else None


def _count(value, hi=MAX_FRAMES):
    n = number(value)
    return int(n) if n is not None and n == int(n) and 0 <= n <= hi else None


def _team(value):
    return value if type(value) is int and value in TEAM_IDS else None


def _name(value):
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip()[:NAME_MAX]


def _players(doc):
    """Validated player rows in file order: known ids only, the first of a duplicate wins."""
    raw = doc.get("players")
    if not isinstance(raw, list):
        return []
    rows, seen = [], set()
    for p in raw:
        pid = p.get("id") if isinstance(p, dict) else None
        if not isinstance(pid, str) or pid not in PLAYER_IDS or pid in seen:
            continue
        seen.add(pid)
        rows.append((p, {
            "id": pid, "team": _team(p.get("team")), "name": _name(p.get("name")),
            "coverage": _measure(p.get("coverage"), 1.0, 4),
            "frames": _count(p.get("frames")),
            "distance_m": _measure(p.get("distance_m"), MAX_DISTANCE_M, 2),
            "avg_speed_mps": _measure(p.get("avg_speed_mps"), MAX_SPEED_MPS, 2),
            "max_speed_mps": _measure(p.get("max_speed_mps"), MAX_SPEED_MPS, 2),
        }))
    return rows


def _owners(rows):
    """``{tracklet id: player id}`` from each row's ``tracklets``; the first claim wins."""
    owners = {}
    for raw, row in rows:
        tracklets = raw.get("tracklets")
        for t in tracklets if isinstance(tracklets, list) else ():
            tid = tracklet_id(t.get("id")) if isinstance(t, dict) else None
            if tid is not None:
                owners.setdefault(tid, row["id"])
    return owners


def _grids(run, rows):
    """``{player id: (grid|None, samples)}``; a point's half is its own ``y`` side."""
    owners = _owners(rows)
    counts = {row["id"]: [[0] * GRID_COLS for _ in range(GRID_ROWS)] for _raw, row in rows}
    seen = {pid: set() for pid in counts}
    samples = dict.fromkeys(counts, 0)
    for t in run.tracklets or ():
        pid = owners.get(t["id"])
        if pid is None:
            continue                        # a tracklet no identity claims
        grid, frames = counts[pid], seen[pid]
        for frame, x, y in t["points"]:
            if frame in frames:
                continue                    # one point per identity per frame
            rc = cell("upper" if y < NET_Y else "lower", x, y)
            if rc is None:
                continue
            frames.add(frame)
            grid[rc[0]][rc[1]] += 1
            samples[pid] += 1
    out = {}
    for pid, grid in counts.items():
        n = samples[pid]
        out[pid] = ((None if n < MIN_SAMPLES
                     else [[round(c / n, 4) for c in row] for row in grid]), n)
    return out


def _is_named(h):
    return h["hitter_id"] is not None and h["hitter_evidence"] in NAMED_EVIDENCE


def _hit_counts(run, rows):
    """``(per player {id: (hits, named)}, per team {id: (hits, named)}, totals)``."""
    players = {row["id"]: [0, 0] for _raw, row in rows}
    teams = {k: [0, 0] for k in TEAM_IDS}
    if run.doubles_hits is None:
        # No usable hits.json list: fall back to players.json's own per-player counts.
        single = 0
        for raw, row in rows:
            hits = _count(raw.get("hits")) or 0
            named = min(_count(raw.get("hits_named")) or 0, hits)
            single += min(_count(raw.get("hits_single_candidate")) or 0, hits - named)
            players[row["id"]] = [hits, named]
            if row["team"] is not None:
                teams[row["team"]][0] += hits
                teams[row["team"]][1] += named
        total = sum(p[0] for p in players.values())
        named = sum(p[1] for p in players.values())
        return players, teams, (total, named, single)
    total = named = single = 0
    for h in run.doubles_hits:
        total += 1
        pid, is_named = h["hitter_id"], _is_named(h)
        if pid is None:
            continue                        # unattributed: only in the total
        named += is_named
        single += h["hitter_evidence"] == "single_candidate"
        if pid in players:
            players[pid][0] += 1
            players[pid][1] += is_named
        if h["hitter_team"] is not None:
            teams[h["hitter_team"]][0] += 1
            teams[h["hitter_team"]][1] += is_named
    return players, teams, (total, named, single)


def _identity_video(doc):
    """``{status: ok, file}`` when the identity video was rendered, else None.

    ``file`` must be a bare ``detect_<stem>_players.mp4`` name (no directory part),
    because the front end builds a URL from it.
    """
    video = doc.get("identity_video")
    if not isinstance(video, dict) or video.get("status") != "ok":
        return None
    name = video.get("file")
    if (not isinstance(name, str) or not name.startswith("detect_")
            or not name.endswith("_players.mp4") or "/" in name or "\\" in name
            or ".." in name or ":" in name):
        return None
    return {"status": "ok", "file": name}


def doubles_insight(run):
    if run.match_format != "doubles":
        return envelope.unavailable("not_doubles")
    doc = run.players_doc
    if not isinstance(doc, dict):
        return envelope.unavailable("no_players")
    status = doc.get("status")
    if status == "error":
        # players.json's `reason` is exception text (paths, internals): never echoed.
        return envelope.unavailable("doubles_error")
    rows = _players(doc)
    if not rows:
        return envelope.unavailable("no_players")
    if status != "ok" and status not in DEGRADED:
        status = "degraded"                 # an unknown status is never reported as ok

    grids = _grids(run, rows)
    per_player, per_team, (total, named, single) = _hit_counts(run, rows)
    players = []
    for _raw, row in rows:
        grid, samples = grids[row["id"]]
        hits, hits_named = per_player[row["id"]]
        players.append(dict(row, hits=hits, hits_named=hits_named, samples=samples,
                            grid=grid))
    teams = [{"id": k, "players": [p["id"] for p in players if p["team"] == k],
              "hits": per_team[k][0], "hits_named": per_team[k][1]}
             for k in TEAM_IDS if any(p["team"] == k for p in players)]
    value = {
        "status": status, "validated": False, "players": players,
        "teams": teams or None,
        "hits": {"total": total, "named": named,
                 "named_share": round(named / total, 4) if total else None,
                 "single_candidate": single},
        "identity_video": _identity_video(doc),
    }
    if status == "ok":
        return envelope.ok(value)
    return envelope.partial(value, "doubles_degraded", {"status": status})
