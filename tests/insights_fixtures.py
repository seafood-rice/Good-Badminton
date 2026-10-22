"""Synthetic ``outputs/<stem>/`` directories for the insights tests.

``make_run`` writes only what it is asked to; an argument left at ``None`` (or
``frames=0``) leaves that file absent, which is how the tests build partially
analysed runs.
"""
import json
from pathlib import Path

from badminton_analysis.stroke.stroke_types import group_of

NET_Y = 6.7
BASE = {"upper": (3.05, 3.35), "lower": (3.05, 10.05)}


def _write_json(path, payload):
    path.write_text(json.dumps(payload), encoding="utf-8")


def _rally_entry(i, item, fps, degraded):
    if isinstance(item, dict):
        entry = dict(item)
    else:
        start, end = item
        entry = {"start_sec": start, "end_sec": end}
    entry.setdefault("id", i + 1)
    entry.setdefault("start_frame", round(entry["start_sec"] * fps))
    entry.setdefault("end_frame", round(entry["end_sec"] * fps))
    entry.setdefault("degraded", degraded)
    return entry


def _detection(signal):
    """The ``detection`` block for a fixture signal name (None -> an old file)."""
    if signal is None:
        return None
    if signal == "degraded":
        return {"signal": "none", "degraded": True}
    if signal == "error":
        return {"signal": "error", "degraded": False, "error": True, "reason": "ValueError"}
    return {"signal": signal, "degraded": False}


def make_run(tmp_path, stem="clip", *, fps=30.0, frames=0, rallies=None, hits=None,
             strokes=None, reports=None, signal="shuttle", positions=None,
             match_format=None):
    """Write ``<tmp_path>/outputs/<stem>/`` and return that directory.

    * ``frames`` > 0 writes ``detections.jsonl`` with that many frames;
      ``positions`` is ``None`` (both players at their base), a dict
      ``{"upper": (x, y)|None, "lower": ...}`` or a callable
      ``(frame, side) -> (x, y)|None``.
    * ``rallies``: ``[(start_sec, end_sec)]`` or dicts -> ``rally_segments.json``;
      ``signal`` is ``shuttle|courtview|swing|none|degraded|error`` or ``None``
      for an older file with no ``detection`` block.
    * ``hits``: ``[(time_sec, hitter)]`` -> ``hits.json``.
    * ``strokes``: ``[(hitter, stroke[, frame])]`` or dicts -> ``strokes.json``.
    * ``reports``: ``[(stroke_type, player_side, overall_score)]`` -> ``strokes.jsonl``.
    * ``match_format``: ``None`` omits the key (an older, singles run); anything else is
      written verbatim as the top-level ``"match_format"`` of ``metadata.json`` (so tests
      can also write a malformed value).
    """
    out = Path(tmp_path) / "outputs" / stem
    out.mkdir(parents=True, exist_ok=True)
    metadata = {
        "schema_version": "1.0",
        "video": {"name": stem, "fps": fps, "total_frames": frames,
                  "duration_sec": frames / fps if fps else 0.0},
    }
    if match_format is not None:
        metadata["match_format"] = match_format
    _write_json(out / "metadata.json", metadata)

    if frames > 0:
        lines = []
        for frame in range(frames):
            players = {}
            for side in ("upper", "lower"):
                if callable(positions):
                    pos = positions(frame, side)
                elif positions is None:
                    pos = BASE[side]
                else:
                    pos = positions.get(side)
                if pos is not None:
                    players[side] = {"court": [pos[0], pos[1]]}
            lines.append(json.dumps({"frame": frame, "time_sec": frame / fps,
                                     "players": players}))
        (out / "detections.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")

    if rallies is not None:
        detection = _detection(signal)
        degraded = bool(detection and detection.get("degraded"))
        payload = {"fps": fps,
                   "rallies": [_rally_entry(i, r, fps, degraded)
                               for i, r in enumerate(rallies)]}
        if detection is not None:
            payload["detection"] = detection
        _write_json(out / "rally_segments.json", payload)

    if hits is not None:
        _write_json(out / "hits.json", {
            "source": "learned", "fps": fps, "shuttle_source": "tracknet",
            "shuttle_coverage_in_play": 0.9,
            "hits": [{"frame": round(t * fps), "time_sec": t, "hitter": h,
                      "confidence": 0.9, "rally_id": None} for t, h in hits]})

    if strokes is not None:
        entries = []
        for i, item in enumerate(strokes):
            if isinstance(item, dict):
                entry = dict(item)
            else:
                hitter, stroke = item[0], item[1]
                unknown = stroke == "uncertain"
                entry = {"frame": item[2] if len(item) > 2 else i * 30,
                         "hitter": hitter, "stroke": "unknown" if unknown else stroke,
                         "group": None if unknown else (group_of(stroke) or stroke),
                         "confidence": 0.5}
            entries.append(entry)
        distribution = {}
        for e in entries:
            distribution[e.get("group")] = distribution.get(e.get("group"), 0) + 1
        _write_json(out / "strokes.json", {
            "schema": 2, "classifier": None, "strokes": entries, "distribution": distribution,
            "hit_source": "learned", "shuttle_source": "tracknet"})

    if reports is not None:
        lines = [json.dumps({"stroke_type": t, "contact_frame": i * 30,
                             "player_side": side, "confidence": 0.5,
                             "overall_score": score, "per_metric": {}})
                 for i, (t, side, score) in enumerate(reports)]
        (out / "strokes.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")

    return out


# ---- sidecar-shaped documents for the aggregation tests (no file I/O) --------

TECHS = ["smash", "clear", "drop", "net", "lift", "drive", "serve"]


def _env(status, value, coverage=None, reason=None, params=None):
    return {"status": status, "value": value, "coverage": coverage,
            "reason": None if reason is None else {"code": reason, "params": params or {}}}


def _tech_side(techs):
    """``{tech: (count, avg_score|None)}`` -> a technique ``Side``."""
    techniques = {t: {"count": 0, "avg_score": None} for t in TECHS}
    for t, (count, score) in (techs or {}).items():
        techniques[t] = {"count": count, "avg_score": score}
    means = [v["avg_score"] for v in techniques.values() if v["avg_score"] is not None]
    return {"techniques": techniques,
            "index": round(sum(means) / len(means), 1) if means else None,
            "scored": len(means)}


def grid_at(row, col):
    """An 11 x 10 grid with all the mass in one cell."""
    grid = [[0.0] * 10 for _ in range(11)]
    grid[row][col] = 1.0
    return grid


def _split_by_hitter(classified, total, distribution):
    """Deal the stem's classified strokes: the first half to ``upper``, the rest to ``lower``."""
    names = [name for name, n in distribution.items() for _ in range(n)]
    cut = classified // 2

    def count(chunk):
        out = {}
        for name in chunk:
            out[name] = out.get(name, 0) + 1
        return out

    return {"upper": {"classified": cut, "total": total // 2,
                      "distribution": count(names[:cut])},
            "lower": {"classified": classified - cut, "total": total - total // 2,
                      "distribution": count(names[cut:])}}


def unit_doc(stem="clip", *, date="2026-10-10", date_source="filename", upper=None,
             lower=None, strokes=None, rally=None, recovery=None, pull=None,
             movement=None, match_format=None):
    """A sidecar dict as ``compute_video`` would return it, built by hand.

    * ``upper`` / ``lower``: ``{tech: (count, avg_score)}`` or ``None`` (no reports).
    * ``strokes``: ``{"classified", "total", "distribution"?, "by_hitter"?}``;
      ``by_hitter`` is ``{side: {"classified", "total", "distribution"}}``, dealt
      from ``distribution`` (first half of the strokes to upper) when omitted.
    * ``rally``: ``{"durations": [...], "shots": [...]|None, "signal": "shuttle"}``.
    * ``recovery``: ``{side: (median_sec|None, found, hits)}``.
    * ``pull``: ``{side: (back_share, n)}``.
    * ``movement``: ``{side: (row, col, samples, distance_m, mean_speed_mps)}``.
    * ``match_format``: ``None`` omits the top-level key; otherwise it is set verbatim
      (the per-player envelopes are left as built, to show the aggregator ignores them).
    """
    if upper is None and lower is None:
        technique = _env("unavailable", None, reason="no_technique")
    else:
        sides = {"upper": _tech_side(upper) if upper is not None else None,
                 "lower": _tech_side(lower) if lower is not None else None}
        scored = {t for s in sides.values() if s for t, v in s["techniques"].items()
                  if v["avg_score"] is not None}
        unscored = [t for t in TECHS if t not in scored]
        technique = _env("partial" if unscored else "ok", {"by_side": sides},
                         {"n": len(scored), "of": 7},
                         "unscored_technique" if unscored else None, {"names": unscored})

    if strokes is None:
        stroke_env = _env("unavailable", None, reason="no_strokes_file")
    else:
        classified, total = strokes["classified"], strokes["total"]
        by_hitter = strokes.get("by_hitter") or _split_by_hitter(
            classified, total, strokes.get("distribution", {}))
        value = {"total": total, "classified": classified, "uncertain": total - classified,
                 "distribution": strokes.get("distribution", {}), "by_hitter": by_hitter}
        low = classified * 10 < total * 6
        stroke_env = _env("partial" if low else "ok", value,
                          {"n": classified, "of": total},
                          "low_stroke_coverage" if low else None)

    if rally is None:
        rally_env = _env("unavailable", None, reason="no_rallies")
    else:
        signal = rally.get("signal", "shuttle")
        durations = rally["durations"]
        shots = rally.get("shots")
        value = {"count": len(durations), "label": "rallies", "signal": signal,
                 "suppressed": signal == "swing", "durations_sec": durations,
                 "mean_sec": sum(durations) / len(durations) if durations else None,
                 "shots_per_rally": shots,
                 "mean_shots": sum(shots) / len(shots) if shots else None}
        reason = {"swing": "signal_swing", "none": "signal_degraded"}.get(signal)
        rally_env = _env("ok" if reason is None else "partial", value, None, reason)

    def sided(spec, build):
        if spec is None:
            return None
        return {s: build(spec[s]) if s in spec else None for s in ("upper", "lower")}

    rec_value = sided(recovery, lambda r: {"median_sec": r[0], "found": r[1], "hits": r[2]})
    if rec_value is None:
        rec_env = _env("unavailable", None, reason="no_hits")
    else:
        found = sum(v["found"] for v in rec_value.values() if v)
        rec_env = _env("ok" if found >= 10 else "partial", rec_value,
                       {"n": found, "of": found}, None if found >= 10 else "few_hits")

    def pull_build(p):
        back = p[0]
        return {"grid": grid_at(9, 4), "front": 0.0, "mid": round(1 - back, 4),
                "back": back, "left": 0.5, "right": 0.5, "n": p[1]}

    pull_value = sided(pull, pull_build)
    if pull_value is None:
        pull_env = _env("unavailable", None, reason="no_hits")
    else:
        n = sum(v["n"] for v in pull_value.values() if v)
        pull_env = _env("ok" if n >= 10 else "partial", pull_value, {"n": n, "of": n},
                        None if n >= 10 else "few_hits")

    def move_build(m):
        row, col, samples, dist, speed = m
        return {"grid": grid_at(row, col), "samples": samples, "distance_m": dist,
                "mean_speed_mps": speed, "base": {"x": 3.05, "y": 3.35}}

    move_value = sided(movement, move_build)
    move_env = (_env("unavailable", None, reason="no_positions") if move_value is None
                else _env("ok", move_value, {"n": 1, "of": 1}))

    doc_date = {"value": date, "source": date_source} if date else {"value": None,
                                                                    "source": "mtime"}
    doc = {"schema_version": 1, "version": 2, "stem": stem,
           "computed_at": "2026-10-19T00:00:00Z", "inputs": {}, "date": doc_date,
           "fps": 30.0, "duration_sec": 60.0, "rally": rally_env, "strokes": stroke_env,
           "technique": technique, "movement": move_env, "recovery": rec_env,
           "pull": pull_env,
           "quality": {"shuttle_coverage_in_play": None, "hit_source": None,
                       "shuttle_source": None, "court_view_pass_frac": None}}
    if match_format is not None:
        doc["match_format"] = match_format
    return doc


# ---- doubles runs (players.json, tracklets.jsonl, attributed hits.json) ---------

# Where each default identity stands, and the cell ``movement.cell`` puts it in.
DOUBLES_SPOTS = {"P1": (1.0, 8.0), "P2": (5.0, 12.0), "P3": (1.0, 2.0), "P4": (5.0, 6.0)}
DOUBLES_CELLS = {"P1": (2, 1), "P2": (8, 8), "P3": (7, 1), "P4": (1, 8)}
DOUBLES_TEAMS = {"P1": 1, "P2": 1, "P3": 2, "P4": 2}
_DEFAULT = object()


def doubles_player(pid, *, team=_DEFAULT, tracklet_ids=None, **fields):
    """One ``players.json`` row as ``tracking/doubles.py _player_rows`` writes it."""
    n = int(pid[1:]) if isinstance(pid, str) and pid[1:].isdigit() else 0
    row = {"id": pid, "team": DOUBLES_TEAMS.get(pid) if team is _DEFAULT else team,
           "name": None, "frames": 60, "coverage": 0.8, "distance_m": 12.5 * n,
           "avg_speed_mps": 1.2, "max_speed_mps": 4.5, "hits": 0, "hits_named": 0,
           "hits_single_candidate": 0, "heatmap": f"heatmap_{pid}.png",
           "tracklets": [{"id": t, "start_frame": 0, "end_frame": 59, "confidence": 0.9}
                         for t in (tracklet_ids if tracklet_ids is not None else [n])]}
    row.update(fields)
    return row


def doubles_tracklet(tid, points, side=None):
    """A ``tracklets.jsonl`` line (``_write_tracklets`` format): ``points`` = [(frame, x, y)]."""
    frames = [p[0] for p in points]
    court = [[p[1], p[2]] for p in points]
    if side is None:
        ys = sorted(c[1] for c in court)
        side = "upper" if ys and ys[len(ys) // 2] < NET_Y else "lower"
    return {"id": tid, "side": side, "frames": frames, "court": court,
            "image": [[100.0, 200.0]] * len(frames), "keypoints": [None] * len(frames),
            "appearance": []}


def doubles_hit(frame, hitter_id=None, evidence="shuttle", *, team=_DEFAULT, fps=30.0,
                hitter="lower", confidence=0.7):
    """A ``hits.json`` entry after ``attribute_hitters`` (``_augment_hits``)."""
    return {"frame": frame, "time_sec": frame / fps, "hitter": hitter, "confidence": 0.9,
            "rally_id": None, "hitter_id": hitter_id,
            "hitter_team": (DOUBLES_TEAMS.get(hitter_id) if isinstance(hitter_id, str)
                            else None) if team is _DEFAULT else team,
            "hitter_confidence": confidence,
            "hitter_evidence": evidence if hitter_id is not None else "none"}


def make_doubles_run(tmp_path, stem="doubles", *, status="ok", players=_DEFAULT,
                     tracklets=_DEFAULT, hits=_DEFAULT, names=None, identity_video=_DEFAULT,
                     fps=30.0, frames=60, reason=None, teams=_DEFAULT):
    """Write a doubles ``<tmp_path>/outputs/<stem>/`` and return that directory.

    * ``status``: ``players.json`` status; ``None`` leaves ``players.json`` absent.
    * ``players``: the rows (default: P1..P4, one tracklet each, ids 1..4).
    * ``tracklets``: ``tracklets.jsonl`` lines (default: tracklet ``n`` holds ``P<n>``
      still at ``DOUBLES_SPOTS`` for ``frames`` frames); ``None`` leaves the file absent.
    * ``hits``: ``hits.json`` entries (default: none); ``None`` leaves the file absent.
    * ``names``: ``{"P1": "Alice"}`` -> ``player_names.json`` and the rows' ``name``.
    * ``identity_video``: the ``players.json`` block (default: ``ok`` with the
      ``detect_<stem>_players.mp4`` name); ``None`` omits it.
    """
    out = Path(tmp_path) / "outputs" / stem
    out.mkdir(parents=True, exist_ok=True)
    _write_json(out / "metadata.json", {
        "schema_version": "1.0", "match_format": "doubles",
        "video": {"name": stem, "fps": fps, "total_frames": frames,
                  "duration_sec": frames / fps if fps else 0.0}})
    if players is _DEFAULT:
        players = [doubles_player(p) for p in ("P1", "P2", "P3", "P4")]
    if names:
        _write_json(out / "player_names.json", names)
        for row in players:
            if isinstance(row, dict) and row.get("id") in names:
                row["name"] = names[row["id"]]
    if tracklets is _DEFAULT:
        tracklets = [doubles_tracklet(int(p[1:]), [(f, x, y) for f in range(frames)])
                     for p, (x, y) in DOUBLES_SPOTS.items()]
    if hits is _DEFAULT:
        hits = []
    if status is not None:
        if teams is _DEFAULT:
            teams = [{"id": k, "players": sorted(p.get("id") for p in players
                                                 if isinstance(p, dict) and p.get("team") == k),
                      "coverage_heatmap": f"heatmap_team{k}.png"}
                     for k in (1, 2) if any(isinstance(p, dict) and p.get("team") == k
                                            for p in players)]
        listed = [h for h in hits or [] if isinstance(h, dict)]
        named = sum(1 for h in listed if h.get("hitter_id")
                    and h.get("hitter_evidence") in ("shuttle", "swing"))
        payload = {"schema_version": "1.0", "match_format": "doubles", "status": status,
                   "reason": reason, "players": players, "teams": teams,
                   "stats": {"tracklets_total": len(tracklets or []),
                             "tracklets_used": len(tracklets or []), "tracklets_dropped": 0,
                             "median_confidence": 0.9, "hits_total": len(listed),
                             "hits_named_share": (round(named / len(listed), 4) if listed
                                                  else 0.0),
                             "hits_single_candidate": 0},
                   "stroke_labels_withheld": "doubles: BST is a singles model",
                   "validated": False}
        if identity_video is _DEFAULT:
            identity_video = {"status": "ok", "file": f"detect_{stem}_players.mp4",
                              "reason": None}
        if identity_video is not None:
            payload["identity_video"] = identity_video
        _write_json(out / "players.json", payload)
    if tracklets is not None:
        (out / "tracklets.jsonl").write_text(
            "".join(json.dumps(t, separators=(",", ":")) + "\n" for t in tracklets),
            encoding="utf-8")
    if hits is not None:
        _write_json(out / "hits.json", {
            "source": "learned", "fps": fps, "shuttle_source": "tracknet",
            "shuttle_coverage_in_play": 0.9, "hits": hits,
            "hitter_attribution": {"validated": False}})
    return out
