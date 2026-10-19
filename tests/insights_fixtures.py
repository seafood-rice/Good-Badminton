"""Synthetic ``outputs/<stem>/`` directories for the insights tests.

``make_run`` writes only what it is asked to; an argument left at ``None`` (or
``frames=0``) leaves that file absent, which is how the tests build partially
analysed runs.
"""
import json
from pathlib import Path

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
             strokes=None, reports=None, signal="shuttle", positions=None):
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
    """
    out = Path(tmp_path) / "outputs" / stem
    out.mkdir(parents=True, exist_ok=True)
    _write_json(out / "metadata.json", {
        "schema_version": "1.0",
        "video": {"name": stem, "fps": fps, "total_frames": frames,
                  "duration_sec": frames / fps if fps else 0.0},
    })

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
                entry = {"frame": item[2] if len(item) > 2 else i * 30,
                         "hitter": hitter, "stroke": stroke,
                         "uncertain": stroke == "uncertain"}
            entries.append(entry)
        distribution = {}
        for e in entries:
            distribution[e.get("stroke")] = distribution.get(e.get("stroke"), 0) + 1
        _write_json(out / "strokes.json", {
            "strokes": entries, "distribution": distribution,
            "hit_source": "learned", "shuttle_source": "tracknet"})

    if reports is not None:
        lines = [json.dumps({"stroke_type": t, "contact_frame": i * 30,
                             "player_side": side, "confidence": 0.5,
                             "overall_score": score, "per_metric": {}})
                 for i, (t, side, score) in enumerate(reports)]
        (out / "strokes.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")

    return out
