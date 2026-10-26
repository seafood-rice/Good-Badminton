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

from ..stroke.stroke_classifier import is_current

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
    rally_outcomes: dict = None  # rally_outcomes.json, raw (insights/outcomes.py validates it)
    notes: list = field(default_factory=list)
    hits_meta: dict = None       # hits.json without the hit list (source, coverage, fps...)
    strokes_meta: dict = None    # strokes.json without the stroke list
    # Doubles runs only (left None on singles runs):
    players_doc: dict = None     # players.json, raw (doubles.py validates every field)
    tracklets: list = None       # [{"id": int|str, "points": [(frame, x, y)]}] from tracklets.jsonl
    doubles_hits: list = None    # [{"hitter_id": str|None, "hitter_team": 1|2|None,
                                 #   "hitter_evidence": str|None}] per hits.json entry

    @property
    def match_format(self):
        """``"doubles"`` when ``metadata.json`` says so, else ``"singles"``.

        A missing key (older runs), a missing/unreadable file or any other value
        counts as singles; it never raises.
        """
        value = (self.metadata or {}).get("match_format")
        return "doubles" if value == "doubles" else "singles"

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
    try:
        value = float(value)
    except OverflowError:       # an int too large for a float (e.g. 10 ** 400) is no number
        return None
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
    """``(hits, meta, raw)``: side-level hits, the rest of the file, the raw hit list."""
    doc = _json_object(out_dir, "hits.json", notes)
    if doc is None:
        return None, None, None
    raw = doc.get("hits")
    meta = {k: v for k, v in doc.items() if k != "hits"}
    if not isinstance(raw, list):
        notes.append("hits.json: no hits list")
        return None, meta, None
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
    return hits, meta, raw


def _load_strokes(out_dir, notes):
    doc = _json_object(out_dir, "strokes.json", notes)
    if doc is None:
        return None, None
    meta = {k: v for k, v in doc.items() if k != "strokes"}
    if not is_current(doc):
        notes.append("strokes.json: from the old classifier; re-label")
        return None, meta
    raw = doc.get("strokes")
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


def tracklet_id(value):
    """A tracklet id (a non-bool int or a string), or None (also for unhashable values)."""
    if isinstance(value, bool):
        return None
    return value if isinstance(value, (int, str)) else None


def _tracklet_points(frames, court):
    """``[(frame, x, y)]`` for the usable points; None when the lists do not line up."""
    if (not isinstance(frames, list) or not isinstance(court, list)
            or len(frames) != len(court)):
        return None
    points = []
    for frame, xy in zip(frames, court):
        frame = _integer(frame)
        if frame is None or not isinstance(xy, (list, tuple)) or len(xy) != 2:
            continue
        x, y = number(xy[0]), number(xy[1])
        if x is not None and y is not None:
            points.append((frame, x, y))
    return points


def _tracklet(rec):
    """``{"id", "points"}`` for one ``tracklets.jsonl`` record, or None when unusable."""
    tid = tracklet_id(rec.get("id")) if isinstance(rec, dict) else None
    if tid is None:
        return None
    points = _tracklet_points(rec.get("frames"), rec.get("court"))
    return None if points is None else {"id": tid, "points": points}


def _load_tracklets(out_dir, notes):
    """Usable tracklets from ``tracklets.jsonl``; None when the file is absent.

    Each line also carries per-frame keypoints and appearance samples, so the file is
    streamed line by line and only ``id`` and the ``frames`` / ``court`` points are kept.
    """
    name = "tracklets.jsonl"
    path = os.path.join(out_dir, name)
    if not os.path.isfile(path):
        return None
    tracklets, bad, skipped = [], 0, 0
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    tracklet = _tracklet(json.loads(line))
                except (ValueError, RecursionError, MemoryError):
                    bad += 1
                    continue
                if tracklet is None:
                    skipped += 1
                else:
                    tracklets.append(tracklet)
    except (OSError, MemoryError) as exc:
        notes.append(f"{name}: unreadable ({type(exc).__name__})")
        return None
    if bad:
        notes.append(f"{name}: skipped {bad} malformed line(s)")
    if skipped:
        notes.append(f"{name}: skipped {skipped} unusable tracklet(s)")
    return tracklets


def _hit_attribution(h):
    """The doubles who-hit fields of one ``hits.json`` entry, each validated or None."""
    pid, team, evidence = h.get("hitter_id"), h.get("hitter_team"), h.get("hitter_evidence")
    return {"hitter_id": pid if isinstance(pid, str) and pid else None,
            "hitter_team": team if type(team) is int and team in (1, 2) else None,
            "hitter_evidence": evidence if isinstance(evidence, str) else None}


def _doubles_hits(raw):
    """Every ``hits.json`` entry's attribution; None when there is no hit list."""
    if not isinstance(raw, list):
        return None
    return [_hit_attribution(h) for h in raw if isinstance(h, dict)]


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
    run.hits, run.hits_meta, raw_hits = _load_hits(d, notes)
    run.strokes, run.strokes_meta = _load_strokes(d, notes)
    run.tech_reports = _load_reports(d, notes)
    run.tech_summary = _json_object(d, "technique_summary.json", notes)
    run.rallies_payload = _json_object(d, "rally_segments.json", notes)
    run.rally_outcomes = _json_object(d, "rally_outcomes.json", notes)
    if run.match_format == "doubles":
        # Only doubles runs have (current) doubles files; tracklets.jsonl also carries
        # keypoints and appearance, so it is not parsed when nothing would use it.
        run.players_doc = _json_object(d, "players.json", notes)
        run.tracklets = _load_tracklets(d, notes)
        run.doubles_hits = _doubles_hits(raw_hits)
    return run
