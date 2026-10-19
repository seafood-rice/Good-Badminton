"""The per-video insights sidecar ``outputs/<stem>/insights.json`` (spec 5.2).

``compute_video`` reads the cached document when the insight version and the
mtime of every input file still match, otherwise recomputes and rewrites it
atomically. It never raises: an insight that fails becomes ``unavailable`` with
reason ``no_data``, and a directory that cannot be written just goes uncached.
"""
import datetime
import json
import os
import sys
import tempfile

from . import envelope
from .dates import video_date
from .loaders import load_run, number
from .movement import movement_insight, pull_insight, recovery_insight
from .rally import rally_insight
from .strokes import strokes_insight
from .technique import technique_insight

INSIGHTS_VERSION = 5
SCHEMA_VERSION = 1
SIDECAR_NAME = "insights.json"
INPUT_FILES = ("metadata.json", "detections.jsonl", "hits.json", "strokes.json",
               "strokes.jsonl", "technique_summary.json", "rally_segments.json")
INSIGHT_KEYS = ("rally", "strokes", "technique", "movement", "recovery", "pull")
# Per-player insights: a doubles run only has one representative per side, not a player.
PLAYER_KEYS = ("strokes", "technique", "movement", "recovery", "pull")
QUALITY_KEYS = ("shuttle_coverage_in_play", "hit_source", "shuttle_source",
                "court_view_pass_frac")


def _input_mtimes(out_dir):
    """``{file: mtime_ns | None}`` for every input the insights read."""
    mtimes = {}
    for name in INPUT_FILES:
        try:
            mtimes[name] = os.stat(os.path.join(out_dir, name)).st_mtime_ns
        except OSError:
            mtimes[name] = None
    return mtimes


def _is_envelope(env):
    return (isinstance(env, dict) and env.get("status") in ("ok", "partial", "unavailable")
            and "value" in env and "coverage" in env and "reason" in env)


def _read_cached(path, stem, inputs):
    """The cached document when it is well formed, current and for this stem, else None."""
    try:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
    except (OSError, ValueError, RecursionError, MemoryError):
        return None
    if not isinstance(doc, dict):
        return None
    if (doc.get("schema_version") != SCHEMA_VERSION or doc.get("version") != INSIGHTS_VERSION
            or doc.get("stem") != stem or doc.get("inputs") != inputs):
        return None
    if not all(_is_envelope(doc.get(k)) for k in INSIGHT_KEYS):
        return None
    if not isinstance(doc.get("date"), dict) or not isinstance(doc.get("quality"), dict):
        return None
    return doc


def _write_atomic(path, doc):
    """Unique temp file in the same directory + ``os.replace``; ``OSError`` is ignored.

    ``mkstemp`` gives every writer (Flask threads, processes) its own temp name.
    """
    tmp = None
    try:
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path) or ".", prefix="insights.",
                                   suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(doc, f, ensure_ascii=False, allow_nan=False)
        os.replace(tmp, path)
    except (OSError, ValueError, TypeError):
        if tmp is not None:
            try:
                os.remove(tmp)
            except OSError:
                pass


def _guarded(name, fn, *args):
    """``fn(*args)``, or ``unavailable`` / ``no_data`` when it raises."""
    try:
        result = fn(*args)
        if _is_envelope(result):
            return result
        raise TypeError("insight did not return an envelope")
    except Exception as exc:        # a bug in one insight must not lose the others
        print(f"insights: {name} failed ({type(exc).__name__})", file=sys.stderr)
        return envelope.unavailable("no_data")


def _text(*candidates):
    """The first candidate that is a string, else None."""
    for c in candidates:
        if isinstance(c, str):
            return c
    return None


def _quality(run):
    hits_meta = run.hits_meta if isinstance(run.hits_meta, dict) else {}
    strokes_meta = run.strokes_meta if isinstance(run.strokes_meta, dict) else {}
    payload = run.rallies_payload if isinstance(run.rallies_payload, dict) else {}
    detection = payload.get("detection")
    court_view = detection.get("court_view") if isinstance(detection, dict) else None
    pass_frac = court_view.get("pass_frac") if isinstance(court_view, dict) else None
    return {
        "shuttle_coverage_in_play": number(hits_meta.get("shuttle_coverage_in_play")),
        "hit_source": _text(hits_meta.get("source"), strokes_meta.get("hit_source")),
        "shuttle_source": _text(hits_meta.get("shuttle_source"),
                                strokes_meta.get("shuttle_source")),
        "court_view_pass_frac": number(pass_frac),
    }


def _duration(run):
    video = (run.metadata or {}).get("video")
    return number(video.get("duration_sec")) if isinstance(video, dict) else None


def _build(stem, out_dir, video_path, inputs):
    try:
        run = load_run(out_dir)
    except Exception as exc:
        print(f"insights: load_run failed ({type(exc).__name__})", file=sys.stderr)
        run = None
    doc = {"schema_version": SCHEMA_VERSION, "version": INSIGHTS_VERSION, "stem": stem,
           "computed_at": datetime.datetime.now(datetime.timezone.utc)
           .strftime("%Y-%m-%dT%H:%M:%SZ"),
           "inputs": inputs, "match_format": "singles"}
    try:
        value, source = video_date(stem, video_path, out_dir, INPUT_FILES)
    except Exception:
        value, source = None, "mtime"
    doc["date"] = {"value": value, "source": source}
    if run is None:
        doc.update(fps=None, duration_sec=None)
        for key in INSIGHT_KEYS:
            doc[key] = envelope.unavailable("no_data")
        doc["quality"] = dict.fromkeys(QUALITY_KEYS)
        return doc

    doc["fps"] = run.fps
    doc["duration_sec"] = _duration(run)
    doc["match_format"] = run.match_format
    rally = _guarded("rally", rally_insight, run)
    doc["rally"] = rally
    if run.match_format == "doubles":
        # The legacy tracker keeps one representative per side of a 4-player match: its
        # movement, recovery, pull and hits are not a player's, and stroke types and
        # technique scores are withheld on doubles runs. Rally windows are not
        # player-specific, so `rally` (and `quality`) stay as computed.
        for key in PLAYER_KEYS:
            doc[key] = envelope.unavailable("doubles_run")
    else:
        doc["strokes"] = _guarded("strokes", strokes_insight, run)
        doc["technique"] = _guarded("technique", technique_insight, run)
        doc["movement"] = _guarded("movement", movement_insight, run, rally)
        doc["recovery"] = _guarded("recovery", recovery_insight, run)
        doc["pull"] = _guarded("pull", pull_insight, run)
    try:
        doc["quality"] = _quality(run)
    except Exception:
        doc["quality"] = dict.fromkeys(QUALITY_KEYS)
    return doc


def _fallback(stem):
    doc = {"schema_version": SCHEMA_VERSION, "version": INSIGHTS_VERSION, "stem": stem,
           "computed_at": datetime.datetime.now(datetime.timezone.utc)
           .strftime("%Y-%m-%dT%H:%M:%SZ"),
           "inputs": {}, "date": {"value": None, "source": "mtime"},
           "fps": None, "duration_sec": None, "match_format": "singles",
           "quality": dict.fromkeys(QUALITY_KEYS)}
    for key in INSIGHT_KEYS:
        doc[key] = envelope.unavailable("no_data")
    return doc


def compute_video(stem, out_dir, video_path=None):
    """The sidecar document for ``out_dir``; cached in ``insights.json`` when possible.

    Never raises: anything unexpected yields an all-``unavailable`` document.
    """
    try:
        out_dir = str(out_dir)
        inputs = _input_mtimes(out_dir)
        path = os.path.join(out_dir, SIDECAR_NAME)
        cached = _read_cached(path, stem, inputs)
        if cached is not None:
            return cached
        doc = _build(stem, out_dir, video_path, inputs)
        if os.path.isdir(out_dir):
            _write_atomic(path, doc)
        return doc
    except Exception as exc:
        print(f"insights: compute_video failed ({type(exc).__name__})", file=sys.stderr)
        return _fallback(stem)
