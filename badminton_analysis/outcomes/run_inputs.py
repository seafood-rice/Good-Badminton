"""Offline inputs of one run for the point-end segmenter (rally-winner-learning spec 5).

Everything is rebuilt from the run directory (``hits.json``, ``detections.jsonl``,
``metadata.json``, ``shuttle_trajectory.json``), the same way
``scripts/score_rally_labels.py`` rebuilds the swing signal, so training and the
pipeline see identical numbers. Nothing here writes; ``load`` returns None for an
unusable run directory and never raises for bad input.
"""
import json
import math
from pathlib import Path
from typing import NamedTuple, Optional

from ..stroke import rallies
from ..strategy.shots import positions_usable

SIDES = ("upper", "lower")
INPLAY_PAD_SEC = 1.0   # positions this close to a hit count as "in play" for the position check


class Inputs(NamedTuple):
    fps: float
    hits: list            # [(time_sec, frame, side, player_id|None)] learned hits, time order
    positions: dict       # {side: {frame: (x, y)}} court metres
    swing: list           # [(time_sec, activity)]
    shuttle_frames: set   # frames with a shuttle detection
    positions_ok: dict    # {side: bool}
    quad: Optional[list]  # court corners TL, TR, BR, BL (image px), or None
    end_sec: Optional[float] = None   # time of the last recorded frame
    swing_segments: tuple = ()        # [(start_sec, end_sec)] of the production swing segmenter


def _read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError, RecursionError, MemoryError):
        return None


def _num(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    return value if math.isfinite(value) else None


def _pair(value):
    if isinstance(value, (list, tuple)) and len(value) == 2:
        a, b = _num(value[0]), _num(value[1])
        if a is not None and b is not None:
            return (a, b)
    return None


def _learned_hits(out_dir):
    doc = _read_json(Path(out_dir) / "hits.json")
    if not isinstance(doc, dict) or doc.get("source") != "learned" or not isinstance(doc.get("hits"), list):
        return []
    hits = []
    for h in doc["hits"]:
        if not isinstance(h, dict) or h.get("hitter") not in SIDES:
            continue
        t, frame = _num(h.get("time_sec")), _num(h.get("frame"))
        if t is None or frame is None:
            continue
        pid = h.get("hitter_id")
        hits.append((t, int(frame), h["hitter"], pid if isinstance(pid, str) and pid else None))
    return sorted(hits, key=lambda h: h[0])


def _trajectory_frames(out_dir):
    doc = _read_json(Path(out_dir) / "shuttle_trajectory.json")
    traj = doc.get("trajectory") if isinstance(doc, dict) else None
    if not isinstance(traj, dict):
        return None
    frames = set()
    for key in traj:
        try:
            frames.add(int(key))
        except (TypeError, ValueError):
            continue
    return frames


def load(out_dir):
    """``Inputs`` for a run directory, or None when metadata or detections are unusable.

    A run without learned hits (``hits.json`` missing or rule-based) loads with an empty
    ``hits`` list: the segmenter then has no gaps and the caller falls back to swing.
    """
    out_dir = Path(out_dir)
    meta = _read_json(out_dir / "metadata.json")
    if not isinstance(meta, dict):
        return None
    video = meta.get("video") if isinstance(meta.get("video"), dict) else {}
    fps = _num(video.get("fps"))
    if not fps or fps <= 0:
        return None
    court = meta.get("court") if isinstance(meta.get("court"), dict) else {}
    quad = court.get("corners")
    try:
        caps = rallies.baseline_caps(quad, fps)
        quad = [list(map(float, p)) for p in quad]
    except (ValueError, TypeError):
        caps, quad = None, None

    track, positions, det_shuttle = [], {s: {} for s in SIDES}, set()
    seen = 0
    try:
        with open(out_dir / "detections.jsonl", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                frame = d.get("frame") if isinstance(d, dict) else None
                if type(frame) is not int:
                    continue
                seen += 1
                players = d.get("players") if isinstance(d.get("players"), dict) else {}
                rec = {"frame": frame, "shuttle": _pair((d.get("shuttlecock") or {}).get("image"))
                       if isinstance(d.get("shuttlecock"), dict) else None}
                for side in SIDES:
                    p = players.get(side) if isinstance(players.get(side), dict) else {}
                    hands = p.get("hands") if isinstance(p.get("hands"), dict) else {}
                    rec["wrist_" + side] = rallies.wrists_from_hands(hands.get("left"), hands.get("right"))
                    xy = _pair(p.get("court"))
                    if xy is not None:
                        positions[side][frame] = xy
                shuttle = d.get("shuttlecock") if isinstance(d.get("shuttlecock"), dict) else {}
                if _pair(shuttle.get("image")) is not None:
                    det_shuttle.add(frame)
                track.append(rec)
    except (OSError, UnicodeDecodeError, MemoryError):
        return None
    if not seen:
        return None
    try:
        times, activity = rallies.swing_activity(track, fps, caps)
    except (ValueError, TypeError, KeyError):
        times, activity = [], []
    swing = list(zip(times, activity))
    try:   # the production swing path (static suppression, court gating), pinned to swing
        segs, prov = rallies.segment_rallies(track, fps, signal="swing", quad=quad)
        swing_segments = [(float(g["start_sec"]), float(g["end_sec"])) for g in segs
                          if not g.get("degraded")] if prov.get("signal") == "swing" else []
    except Exception:
        swing_segments = []
    traj = _trajectory_frames(out_dir)
    hits = _learned_hits(out_dir)
    pts = {s: [] for s in SIDES}
    if hits:
        hit_times = [h[0] for h in hits]
        span = [(t - INPLAY_PAD_SEC, t + INPLAY_PAD_SEC) for t in hit_times]
        for side in SIDES:
            for frame, xy in positions[side].items():
                ft = frame / fps
                lo, hi = 0, len(span)
                while lo < hi:   # first window ending at or after ft
                    mid = (lo + hi) // 2
                    if span[mid][1] < ft:
                        lo = mid + 1
                    else:
                        hi = mid
                if lo < len(span) and span[lo][0] <= ft:
                    pts[side].append(xy)
    ok = positions_usable(pts)
    end_sec = max((r["frame"] for r in track), default=0) / fps
    return Inputs(fps=fps, hits=hits, positions=positions, swing=swing,
                  shuttle_frames=traj if traj is not None else det_shuttle,
                  positions_ok=ok, quad=quad, end_sec=end_sec,
                  swing_segments=swing_segments)
