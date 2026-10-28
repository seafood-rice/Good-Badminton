"""Per-shot table, effectiveness tally and opening table (rally tactics spec 7.1-7.4).

Built from a result directory: ``strokes.json`` (owner overlay already applied),
``rally_segments.json``, ``rally_outcomes.json`` and ``detections.jsonl``. Nothing here raises for
a missing or malformed file; a gap shows up as ``None`` values or empty lists. Standalone: the
facts pack (Task 8) turns the numbers into cite-able facts.
"""
import json
import os
import statistics
from collections import Counter

from ..insights.loaders import SIDES, load_run, number
from ..insights.movement import NET_Y
from ..insights.rally import SHOT_PAD_SEC

COURT_W = 6.1
COURT_L = 13.4
FRONT_M = 1.98          # depth from the net: front <= FRONT_M
REAR_M = 4.7            # rear > REAR_M, mid in between
FAST_SEC = 0.7          # tempo: fast < FAST_SEC
SLOW_SEC = 1.5          # slow > SLOW_SEC
RUSH_M = 2.5            # receiver moved >= RUSH_M ...
RUSH_SEC = 1.2          # ... within <= RUSH_SEC
BROKEN_GAP_SEC = 2.5    # link broken: same hitter or a gap longer than this
EDGE_M = 0.25           # position check: "on the outline or the net" distance
EDGE_SHARE = 0.4        # position check fails when more in-play frames than this are on the edge
POS_WINDOW = 8          # frames either side of a hit when looking up a position
MIN_ATTEMPTS = 5        # fewer attempts of a stroke is never a strength / weakness
MIN_OPENINGS = 5        # fewer complete openings = no opening claims
MAX_ROWS = 8
SHORT_RALLY_SEC = 9.0

ATTACKING = ("smash", "net_rush", "push", "drive")
DEFENSIVE = ("lift", "block", "clear")   # a clear only when hit from the rear zone

OUTCOMES_FILE = "rally_outcomes.json"
OUTCOMES_SCHEMA = 1
_OUTCOME_KEYS = ("effective", "neutral", "ineffective", "unknown")


# ------------------------------------------------------------------ small readers

def _read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
    except (OSError, ValueError, RecursionError, MemoryError):
        return None
    return doc if isinstance(doc, dict) else None


def _segments(out_dir):
    """``(fps, [{id, start_sec, end_sec}])`` from ``rally_segments.json`` (``(None, [])`` if unusable)."""
    doc = _read_json(os.path.join(out_dir, "rally_segments.json")) or {}
    fps = number(doc.get("fps"))
    fps = fps if fps and fps > 0 else None
    rows = []
    for r in doc.get("rallies") if isinstance(doc.get("rallies"), list) else []:
        if not isinstance(r, dict):
            continue
        start, end = number(r.get("start_sec")), number(r.get("end_sec"))
        if start is None and fps and number(r.get("start_frame")) is not None:
            start = number(r.get("start_frame")) / fps
        if end is None and fps and number(r.get("end_frame")) is not None:
            end = number(r.get("end_frame")) / fps
        rid = r.get("id")
        if start is None or end is None or end < start or isinstance(rid, bool) \
                or not isinstance(rid, (int, str)):
            continue
        rows.append({"id": rid, "start_sec": start, "end_sec": end})
    rows.sort(key=lambda r: r["start_sec"])
    return fps, rows


def _winners(out_dir, segments):
    """``{segment id: (winner_side, owner_labelled)}`` from a schema-1 ``rally_outcomes.json``.

    An outcome rally maps to a segment by ``segment_id``, else by the largest time overlap."""
    doc = _read_json(os.path.join(out_dir, OUTCOMES_FILE))
    if not doc or doc.get("schema") != OUTCOMES_SCHEMA or not isinstance(doc.get("rallies"), list):
        return {}
    by_id = {s["id"]: s for s in segments}
    out = {}
    for r in doc["rallies"]:
        if not isinstance(r, dict) or r.get("winner_side") not in SIDES:
            continue
        seg = r.get("segment_id")
        if seg not in by_id:
            seg = None
            start, end = number(r.get("start_sec")), number(r.get("end_sec"))
            if start is not None and end is not None:
                best = 0.0
                for s in segments:
                    overlap = min(end, s["end_sec"]) - max(start, s["start_sec"])
                    if overlap > best:
                        best, seg = overlap, s["id"]
        if seg is None or seg in out:
            continue
        out[seg] = (r["winner_side"], r.get("source") == "owner")
    return out


def _doubles_maps(out_dir):
    """``(pid -> team side, pid -> {frame: (x, y)})`` for a doubles run, else ``({}, {})``."""
    from .facts import _player_points, _team_side   # reuse the doubles identity code
    meta = _read_json(os.path.join(out_dir, "metadata.json")) or {}
    if meta.get("match_format") != "doubles":
        return {}, {}       # singles: skip the heavy full-run load
    try:
        run = load_run(out_dir)
    except Exception:   # noqa: BLE001  tolerant reader contract: never raise for bad input
        return {}, {}
    if run.match_format != "doubles" or not isinstance(run.players_doc, dict):
        return {}, {}
    teams = {}
    for row in run.players_doc.get("players") or []:
        if isinstance(row, dict) and isinstance(row.get("id"), str) and row.get("team") in (1, 2):
            teams.setdefault(row["team"], []).append({"id": row["id"]})

    class _Ctx:
        pass

    ctx = _Ctx()
    ctx.run = run
    players = [p for group in teams.values() for p in group]
    points = _player_points(ctx, players)
    side_of = {}
    for group in teams.values():
        pids = [p["id"] for p in group]
        side = _team_side(pids, points)
        for pid in pids:
            side_of[pid] = side
    return side_of, points


def _hit_pids(out_dir):
    """``{frame: player id}`` from ``hits.json`` ``hitter_id`` (doubles; empty when unusable)."""
    doc = _read_json(os.path.join(out_dir, "hits.json")) or {}
    out = {}
    for h in doc.get("hits") if isinstance(doc.get("hits"), list) else []:
        if not isinstance(h, dict):
            continue
        frame, pid = number(h.get("frame")), h.get("hitter_id")
        if frame is not None and isinstance(pid, str) and pid:
            out[int(frame)] = pid
    return out


def _read_detections(out_dir):
    """``{"upper": {frame: (x, y)}, "lower": {...}}`` from ``detections.jsonl`` (read once)."""
    pos = {side: {} for side in SIDES}
    try:
        with open(os.path.join(out_dir, "detections.jsonl"), encoding="utf-8") as f:
            for line in f:
                try:
                    row = json.loads(line)
                except (ValueError, RecursionError):
                    continue
                players = row.get("players") if isinstance(row, dict) else None
                frame = number(row.get("frame")) if isinstance(row, dict) else None
                if frame is None or not isinstance(players, dict):
                    continue
                for side in SIDES:
                    p = players.get(side)
                    court = p.get("court") if isinstance(p, dict) else None
                    if isinstance(court, (list, tuple)) and len(court) == 2:
                        x, y = number(court[0]), number(court[1])
                        if x is not None and y is not None:
                            pos[side][int(frame)] = (x, y)
    except (OSError, UnicodeDecodeError, MemoryError):
        pass
    return pos


# ------------------------------------------------------------------ zones and position check

def zone_of(side, x, y):
    """``{"depth": front|mid|rear, "side": left|centre|right}`` of a court position.

    Depth is the distance from the net (``abs(y - 6.7)``); left/right is the player's own view
    facing the net, so the far (upper) half is mirrored."""
    d = abs(y - NET_Y)
    depth = "front" if d <= FRONT_M else ("rear" if d > REAR_M else "mid")
    third = min(2, max(0, int(x / COURT_W * 3)))
    if side == "upper":
        third = 2 - third
    return {"depth": depth, "side": ("left", "centre", "right")[third]}


def _on_edge(side, x, y):
    """True when a point is within EDGE_M of the half's outline or the net, or outside the half."""
    if x <= EDGE_M or x >= COURT_W - EDGE_M:
        return True
    if side == "upper":
        return y <= EDGE_M or y >= NET_Y - EDGE_M
    return y <= NET_Y + EDGE_M or y >= COURT_L - EDGE_M


def positions_usable(points_by_side):
    """``{side: bool}``: False when > 40 % of a side's in-play points lie on the edge (or no points)."""
    out = {}
    for side in SIDES:
        pts = list((points_by_side or {}).get(side) or [])
        if not pts:
            out[side] = False
            continue
        bad = sum(1 for x, y in pts if _on_edge(side, x, y))
        out[side] = bad / len(pts) <= EDGE_SHARE
    return out


def _lookup(frames, frame):
    """The position nearest ``frame`` within POS_WINDOW frames, else None."""
    if not frames:
        return None
    for d in range(POS_WINDOW + 1):
        for f in ((frame,) if d == 0 else (frame - d, frame + d)):
            if f in frames:
                return frames[f]
    return None


def _dist(a, b):
    return ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5


def _is_serve(s):
    return s.get("group") == "serve" or s.get("stroke") in ("short_serve", "long_serve")


# ------------------------------------------------------------------ the table

def _empty(fps=None):
    return {"shots": [], "rallies": [], "positions_ok": {"upper": False, "lower": False},
            "fps": fps}


def _assign(t, segments):
    """Index of the segment a shot at time ``t`` belongs to (strict containment, else padded)."""
    for i, s in enumerate(segments):
        if s["start_sec"] <= t <= s["end_sec"]:
            return i
    best, idx = None, None
    for i, s in enumerate(segments):
        if s["start_sec"] - SHOT_PAD_SEC <= t <= s["end_sec"] + SHOT_PAD_SEC:
            gap = min(abs(t - s["start_sec"]), abs(t - s["end_sec"]))
            if best is None or gap < best:
                best, idx = gap, i
    return idx


def build_shots(out_dir):
    out_dir = str(out_dir)
    fps, segments = _segments(out_dir)
    doc = _read_json(os.path.join(out_dir, "strokes.json")) or {}
    raw = doc.get("strokes") if isinstance(doc.get("strokes"), list) else []
    if not fps or not segments:
        return _empty(fps)
    winners = _winners(out_dir, segments)
    side_of, player_pts = _doubles_maps(out_dir)
    doubles = bool(side_of)
    hit_pid = _hit_pids(out_dir) if doubles else {}

    # hits -> (time, side, actor, stroke dict) per segment
    per_seg = [[] for _ in segments]
    for s in raw:
        if not isinstance(s, dict):
            continue
        frame = number(s.get("frame"))
        hitter = s.get("hitter")
        if doubles:
            side = hitter if hitter in SIDES else side_of.get(hitter)
        else:
            side = hitter if hitter in SIDES else None
        if frame is None or side not in SIDES:
            continue
        actor = hitter
        if doubles:
            # the player behind the hit (hits.json, by frame) when it is on this side, else the
            # side itself (no position)
            pid = hit_pid.get(int(frame))
            actor = pid if pid in player_pts and side_of.get(pid) == side else (
                hitter if hitter in side_of and side_of[hitter] == side else side)
        t = frame / fps
        i = _assign(t, segments)
        if i is not None:
            per_seg[i].append((t, int(frame), side, actor, s))

    det = _read_detections(out_dir)
    # positions by actor: singles = side, doubles = player id. A doubles hit with no known hitter
    # has no position: the partners' mean says nothing about who hit.
    positions = dict(player_pts) if doubles else det
    in_play = {side: [] for side in SIDES}
    spans = [(round(seg["start_sec"] * fps), round(seg["end_sec"] * fps)) for seg in segments]
    if doubles:
        for pid, frames in player_pts.items():
            side = side_of.get(pid)
            if side in SIDES:
                for f, xy in frames.items():
                    if any(a <= f <= b for a, b in spans):
                        in_play[side].append(xy)
    else:
        for side in SIDES:
            for f, xy in det[side].items():
                if any(a <= f <= b for a, b in spans):
                    in_play[side].append(xy)
    ok = positions_usable(in_play)

    def where(actor, side, frame):
        if not ok.get(side):
            return None
        return _lookup(positions.get(actor), frame)

    shots, rallies = [], []
    for seg, hits in zip(segments, per_seg):
        hits.sort(key=lambda h: h[0])
        rows = []
        for k, (t, frame, side, actor, s) in enumerate(hits):
            xy = where(actor, side, frame)
            prev_t = hits[k - 1][0] if k else None
            tempo_sec = round(t - prev_t, 3) if prev_t is not None else None
            tempo = None
            if tempo_sec is not None:
                tempo = "fast" if tempo_sec < FAST_SEC else ("slow" if tempo_sec > SLOW_SEC
                                                             else "normal")
            rows.append({
                "rally": seg["id"], "index": k, "frame": frame, "time": round(t, 3),
                "hitter": side, "stroke": s.get("stroke") if isinstance(s.get("stroke"), str)
                else None,
                "group": s.get("group") if isinstance(s.get("group"), str) else None,
                "source": s.get("source") if s.get("source") in ("model", "owner") else "model",
                "zone": zone_of(side, *xy) if xy else None, "sent_to": None,
                "tempo_sec": tempo_sec, "tempo": tempo, "rushed_reply": None,
                "link_broken": False, "last": k == len(hits) - 1,
            })
        for k in range(len(rows) - 1):
            a, b = rows[k], rows[k + 1]
            ha, hb = hits[k], hits[k + 1]
            a["link_broken"] = a["hitter"] == b["hitter"] or b["time"] - a["time"] > BROKEN_GAP_SEC
            if a["link_broken"]:
                continue
            a["sent_to"] = b["zone"]
            p_then = where(hb[3], hb[2], ha[1])      # receiver when this shot was hit
            p_next = where(hb[3], hb[2], hb[1])      # receiver at the reply
            if p_then and p_next:
                a["rushed_reply"] = (_dist(p_then, p_next) >= RUSH_M
                                     and b["time"] - a["time"] <= RUSH_SEC)
        shots.extend(rows)
        win = winners.get(seg["id"])
        rallies.append({"id": seg["id"], "winner_side": win[0] if win else None,
                        "n_shots": len(rows), "complete_opening": _complete(rows, hits),
                        "owner_labelled": bool(win and win[1])})
    return {"shots": shots, "rallies": rallies, "positions_ok": ok, "fps": fps}


def _complete(rows, hits):
    """Shot 1 is a serve, shots 1-3 alternate hitters, no broken link among them."""
    if len(rows) < 3 or not _is_serve(hits[0][4]):
        return False
    h = [r["hitter"] for r in rows[:3]]
    return h[0] != h[1] and h[0] == h[2] and not rows[0]["link_broken"] \
        and not rows[1]["link_broken"]


# ------------------------------------------------------------------ effectiveness

def _by_rally(table):
    out = {}
    for s in table.get("shots") or []:
        out.setdefault(s["rally"], []).append(s)
    for rows in out.values():
        rows.sort(key=lambda s: s["index"])
    return out


def _defensive(shot):
    """A lift or block is defensive anywhere; a clear only when hit from the rear."""
    if shot["stroke"] in ("lift", "block"):
        return True
    return (shot["stroke"] == "clear" and bool(shot["zone"])
            and shot["zone"]["depth"] == "rear")


def _classify(rows, i, side, winner):
    shot = rows[i]
    last = len(rows) - 1
    other = "lower" if side == "upper" else "upper"
    if shot["last"]:
        if winner == side:
            return "effective"
        return "ineffective" if winner in SIDES else "unknown"
    # the hitter's last shot of a lost rally, whatever the link after it looks like
    if winner == other and not any(r["hitter"] == side for r in rows[i + 1:]):
        return "ineffective"
    # the rally ends within this shot's next two links, all of them intact
    if winner == side:
        end = last
        if end <= i + 2 and not any(rows[j]["link_broken"] for j in range(i, end)):
            return "effective"
    nxt = rows[i + 1]
    if shot["link_broken"]:
        return "unknown"
    if nxt["last"] and winner == other:
        return "ineffective"
    if _defensive(nxt) or shot["rushed_reply"]:
        return "effective"
    return "neutral"


def effectiveness(table, side):
    """``{stroke: {effective, neutral, ineffective, unknown, n}}`` for ``side``'s shots (spec 7.2)."""
    winners = {r["id"]: r.get("winner_side") for r in table.get("rallies") or []}
    out = {}
    for rid, rows in _by_rally(table).items():
        for i, shot in enumerate(rows):
            if shot["hitter"] != side or not shot["stroke"] or shot["stroke"] == "unknown":
                continue
            cls = _classify(rows, i, side, winners.get(rid))
            cell = out.setdefault(shot["stroke"], dict.fromkeys(_OUTCOME_KEYS, 0) | {"n": 0})
            cell[cls] += 1
            cell["n"] += 1
    return out


# ------------------------------------------------------------------ openings

def _advantage(rows):
    gains = Counter()
    for k in (1, 2):
        if rows[k]["stroke"] in ATTACKING:
            gains[rows[k]["hitter"]] += 1
        if _defensive(rows[k]):
            gains[rows[k - 1]["hitter"]] += 1
    for k in (0, 1):
        if rows[k]["rushed_reply"]:
            gains[rows[k]["hitter"]] += 1
    up, low = gains["upper"], gains["lower"]
    if up == low:
        return "neutral"
    return "upper" if up > low else "lower"


def openings(table):
    rallies = {r["id"]: r for r in table.get("rallies") or []}
    by_rally = _by_rally(table)
    complete = [r for r in rallies.values() if r["complete_opening"] and r["id"] in by_rally]
    cands = []
    for r in complete:
        rows = by_rally[r["id"]][:3]
        cands.append({
            "rally": r["id"], "advantage": _advantage(rows), "point_winner": r["winner_side"],
            "n_shots": r["n_shots"],
            "shots": [{"hitter": s["hitter"], "stroke": s["stroke"], "zone": s["zone"],
                       "sent_to": s["sent_to"]} for s in rows],
            "_tier": 0 if r.get("owner_labelled") else (1 if r["winner_side"] else 2),
        })
    won_by = {side: 0 for side in SIDES}
    neutral = n = won = 0
    by_side = {side: {"n": 0, "won": 0} for side in SIDES}
    for c in cands:
        if c["advantage"] == "neutral":
            neutral += 1
            continue
        won_by[c["advantage"]] += 1
        if c["point_winner"]:
            n += 1
            won += c["advantage"] == c["point_winner"]
            by_side[c["advantage"]]["n"] += 1
            by_side[c["advantage"]]["won"] += c["advantage"] == c["point_winner"]
    cands.sort(key=lambda c: (c["_tier"], -c["n_shots"], str(c["rally"])))
    rows = []
    for c in cands[:MAX_ROWS]:
        c = dict(c)
        c.pop("_tier")
        rows.append(c)
    return {"complete": len(complete), "total": len(rallies), "rows": rows, "won_by": won_by,
            "opening_winner_won_point": {"n": n, "won": won}, "by_side": by_side,
            "neutral": neutral}


# ------------------------------------------------------------------ overview

def overview(table, out_dir):
    _, segments = _segments(str(out_dir))
    durations = [s["end_sec"] - s["start_sec"] for s in segments]
    rallies = table.get("rallies") or []
    shots = table.get("shots") or []
    return {
        "active_play_sec": round(sum(durations), 1) if durations else None,
        "rallies_under_9s_share": (round(sum(1 for d in durations if d < SHORT_RALLY_SEC)
                                         / len(durations), 3) if durations else None),
        "shots_per_rally": (round(statistics.mean(r["n_shots"] for r in rallies), 2)
                            if rallies else None),
        "shots_source": "labelled" if any(s["source"] == "owner" for s in shots) else "detected",
    }
