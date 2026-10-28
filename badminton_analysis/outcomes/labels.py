"""The owner's rally labels, ``data/rally_labels/<stem>.json`` (spec 5.1).

Labels are keyed by time, not by segment id, so a re-analysis that re-segments
the video re-attaches each label to whichever segment now holds its end time
(``attach``). The file lives outside ``outputs/`` so a re-analysis or a
``match`` delete keeps it. This module takes no lock: the caller holds one.
"""
import json
import math
import os
import tempfile
import time
from pathlib import Path

SCHEMA = 1
SIDES = ("upper", "lower")
VERDICTS = ("upper", "lower", "not_point", "unsure")
OUTCOMES = ("winner", "forced_error", "unforced_error")
MAX_LABELS = 2000
ATTACH_TOLERANCE_SEC = 1.0

_REPLACE_ATTEMPTS = 5
_REPLACE_BACKOFF_SEC = 0.05


def empty():
    return {"schema": SCHEMA, "revision": 0, "labels": []}


def _time(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    return value if math.isfinite(value) and value >= 0 else None


def _label(raw):
    """One normalised label, or None when it is not a valid label."""
    if not isinstance(raw, dict):
        return None
    end = _time(raw.get("end_sec"))
    verdict = raw.get("verdict")
    if end is None or verdict not in VERDICTS:
        return None
    start = raw.get("start_sec")
    if start is not None:
        start = _time(start)
        if start is None or start > end:
            return None
    outcome = raw.get("outcome")
    if outcome is not None and outcome not in OUTCOMES:
        return None
    if verdict not in SIDES:
        outcome = None
    added = raw.get("added", False)
    if not isinstance(added, bool) or (added and verdict not in SIDES):
        return None   # an added point is a point end someone won
    return {"start_sec": None if added else start, "end_sec": end, "verdict": verdict,
            "outcome": outcome, "added": added}


def validate_labels(raw):
    """The normalised label list, or None when ``raw`` is not a valid list of labels."""
    if not isinstance(raw, list) or len(raw) > MAX_LABELS:
        return None
    labels = [_label(item) for item in raw]
    return None if any(label is None for label in labels) else labels


def labels_path(labels_dir, stem):
    return Path(labels_dir) / f"{stem}.json"


def load(path):
    """The label document; ``empty()`` when absent, None when unreadable or invalid."""
    path = Path(path)
    if not path.is_file():
        return empty()
    try:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
    except (OSError, ValueError, RecursionError, MemoryError):
        return None
    if not isinstance(doc, dict) or doc.get("schema") != SCHEMA:
        return None
    revision = doc.get("revision")
    labels = validate_labels(doc.get("labels"))
    if type(revision) is not int or revision < 0 or labels is None:
        return None
    return {"schema": SCHEMA, "revision": revision, "labels": labels}


def save(path, doc):
    """Write the label document atomically."""
    write_json_atomic(path, doc)


def _replace_with_retry(tmp, path):
    """``os.replace``, retrying briefly on PermissionError (Windows indexers hold new files open)."""
    for attempt in range(_REPLACE_ATTEMPTS):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if attempt == _REPLACE_ATTEMPTS - 1:
                raise
            time.sleep(_REPLACE_BACKOFF_SEC)


def _atomic(path, write, mode="w"):
    """Fill a temp file in ``path``'s directory with ``write(file)``, then ``os.replace`` it onto ``path``."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.stem}-", suffix=".tmp")
    try:
        with os.fdopen(fd, mode, **({"encoding": "utf-8"} if mode == "w" else {})) as f:
            write(f)
        _replace_with_retry(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def write_json_atomic(path, doc):
    """Write ``doc`` as JSON atomically (temp file in the same directory + os.replace)."""
    _atomic(path, lambda f: json.dump(doc, f, ensure_ascii=False, indent=1))


def write_pipeline_json_atomic(path, payload):
    """``data.writer.write_json`` (clean_value, indent 2, trailing newline), but atomically.

    For the files the analysis pipeline writes (``rally_segments.json``, ``hits.json``): a later
    rewrite leaves the same bytes the pipeline would have written.
    """
    from ..data.writer import clean_value

    def write(f):
        json.dump(clean_value(payload), f, ensure_ascii=False, indent=2)
        f.write("\n")

    _atomic(path, write)


def write_bytes_atomic(path, data):
    """Put ``data`` (bytes) at ``path`` atomically."""
    _atomic(path, lambda f: f.write(data), mode="wb")


def attach(labels, segments):
    """``(by_segment, added)``: each segment label on the segment holding its end time.

    ``segments`` is ``[{"id", "start_sec", "end_sec"}]``. A label attaches to the
    segment whose ``[start - 1, end + 1]`` contains its ``end_sec``, nearest end
    first. A later label replaces an earlier one on the same segment; the replaced
    one survives as an added point only when it names a winner. A label that fits
    no segment, and every added point, is returned in ``added``.
    """
    by_segment, added = {}, []
    for label in labels:
        if label.get("added"):
            added.append(label)
            continue
        end = label["end_sec"]
        best = None
        for seg in segments:
            if seg["start_sec"] - ATTACH_TOLERANCE_SEC <= end <= seg["end_sec"] + ATTACH_TOLERANCE_SEC:
                if best is None or abs(seg["end_sec"] - end) < abs(best["end_sec"] - end):
                    best = seg
        if best is None:
            if label["verdict"] in SIDES:
                added.append(label)
            continue
        previous = by_segment.get(best["id"])
        if previous is not None and previous["verdict"] in SIDES:
            added.append(previous)
        by_segment[best["id"]] = label
    return by_segment, added
