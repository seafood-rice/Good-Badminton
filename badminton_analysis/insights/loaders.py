"""Tolerant readers for one ``outputs/<stem>/`` run directory.

Every file is optional and may be malformed. An absent, unreadable or wrongly
shaped file becomes ``None`` on the ``Run`` (a malformed one also appends a
note); individual bad records inside a readable file are skipped and counted in
one note per file. Nothing here raises for bad input.
"""
import json
import math
import os
from dataclasses import dataclass, field
from pathlib import Path

SIDES = ("upper", "lower")


@dataclass
class Run:
    stem: str
    out_dir: Path
    metadata: dict = None
    detections: list = None      # [(frame, time_sec, {"upper": (x, y)|None, "lower": ...})]
    hits: list = None            # [(time_sec, frame, hitter)]
    strokes: list = None         # [dict] from strokes.json
    tech_reports: list = None    # [dict] from strokes.jsonl
    tech_summary: dict = None
    rallies_payload: dict = None
    notes: list = field(default_factory=list)
    hits_meta: dict = None       # hits.json without the hit list (source, coverage, fps...)
    strokes_meta: dict = None    # strokes.json without the stroke list

    @property
    def fps(self):
        """Frames per second from metadata, else rally/hits files; None if unknown."""
        video = (self.metadata or {}).get("video")
        for candidate in ((video or {}).get("fps") if isinstance(video, dict) else None,
                          (self.rallies_payload or {}).get("fps"),
                          (self.hits_meta or {}).get("fps")):
            fps = number(candidate)
            if fps and fps > 0:
                return fps
        return None


def number(value):
    """A finite float, or None (booleans and non-numbers are not numbers)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    return value if math.isfinite(value) else None


def _integer(value):
    n = number(value)
    return int(n) if n is not None and n == int(n) else None


def _read_text(path):
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read()


def _json_object(out_dir, name, notes):
    """The dict in ``name``, or None when absent / unreadable / not an object."""
    path = os.path.join(out_dir, name)
    if not os.path.isfile(path):
        return None
    try:
        data = json.loads(_read_text(path))
    except (OSError, ValueError, RecursionError, MemoryError) as exc:
        # RecursionError: deeply nested JSON; MemoryError: absurdly large file
        notes.append(f"{name}: unreadable ({type(exc).__name__})")
        return None
    if not isinstance(data, dict):
        notes.append(f"{name}: not a JSON object")
        return None
    return data


def _json_lines(out_dir, name, notes):
    """Parsed value of each non-blank line (None for a bad line); None if no file."""
    path = os.path.join(out_dir, name)
    if not os.path.isfile(path):
        return None
    try:
        text = _read_text(path)
    except (OSError, MemoryError) as exc:
        notes.append(f"{name}: unreadable ({type(exc).__name__})")
        return None
    records = []
    bad = 0
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except (ValueError, RecursionError, MemoryError):
            bad += 1
            records.append(None)
    if bad:
        notes.append(f"{name}: skipped {bad} malformed line(s)")
    return records


def _court_xy(player):
    if not isinstance(player, dict):
        return None
    court = player.get("court")
    if not isinstance(court, (list, tuple)) or len(court) != 2:
        return None
    x, y = number(court[0]), number(court[1])
    return None if x is None or y is None else (x, y)


def _load_detections(out_dir, notes):
    records = _json_lines(out_dir, "detections.jsonl", notes)
    if records is None:
        return None
    frames = []
    skipped = 0
    for rec in records:
        if rec is None:
            continue                      # already counted as malformed
        if not isinstance(rec, dict):
            skipped += 1
            continue
        frame, t = _integer(rec.get("frame")), number(rec.get("time_sec"))
        if frame is None or t is None:
            skipped += 1
            continue
        players = rec.get("players")
        if not isinstance(players, dict):
            players = {}
        frames.append((frame, t, {s: _court_xy(players.get(s)) for s in SIDES}))
    if skipped:
        notes.append(f"detections.jsonl: skipped {skipped} unusable frame(s)")
    return frames


def _load_hits(out_dir, notes):
    doc = _json_object(out_dir, "hits.json", notes)
    if doc is None:
        return None, None
    raw = doc.get("hits")
    meta = {k: v for k, v in doc.items() if k != "hits"}
    if not isinstance(raw, list):
        notes.append("hits.json: no hits list")
        return None, meta
    hits, skipped = [], 0
    for h in raw:
        t = number(h.get("time_sec")) if isinstance(h, dict) else None
        frame = _integer(h.get("frame")) if isinstance(h, dict) else None
        if t is None or frame is None or h.get("hitter") not in SIDES:
            skipped += 1
            continue
        hits.append((t, frame, h["hitter"]))
    if skipped:
        notes.append(f"hits.json: skipped {skipped} unusable hit(s)")
    return hits, meta


def _load_strokes(out_dir, notes):
    doc = _json_object(out_dir, "strokes.json", notes)
    if doc is None:
        return None, None
    raw = doc.get("strokes")
    meta = {k: v for k, v in doc.items() if k != "strokes"}
    if not isinstance(raw, list):
        notes.append("strokes.json: no strokes list")
        return None, meta
    strokes = [s for s in raw if isinstance(s, dict)]
    if len(strokes) != len(raw):
        notes.append(f"strokes.json: skipped {len(raw) - len(strokes)} unusable stroke(s)")
    return strokes, meta


def _load_reports(out_dir, notes):
    records = _json_lines(out_dir, "strokes.jsonl", notes)
    if records is None:
        return None
    reports = [r for r in records if isinstance(r, dict)]
    skipped = sum(1 for r in records if r is not None and not isinstance(r, dict))
    if skipped:
        notes.append(f"strokes.jsonl: skipped {skipped} non-object line(s)")
    return reports


def load_run(out_dir):
    """Read ``out_dir`` into a ``Run``; every missing or malformed piece is None."""
    out_dir = Path(out_dir)
    notes = []
    d = str(out_dir)
    run = Run(stem=out_dir.name, out_dir=out_dir, notes=notes)
    if not out_dir.is_dir():
        return run
    run.metadata = _json_object(d, "metadata.json", notes)
    run.detections = _load_detections(d, notes)
    run.hits, run.hits_meta = _load_hits(d, notes)
    run.strokes, run.strokes_meta = _load_strokes(d, notes)
    run.tech_reports = _load_reports(d, notes)
    run.tech_summary = _json_object(d, "technique_summary.json", notes)
    run.rallies_payload = _json_object(d, "rally_segments.json", notes)
    return run
