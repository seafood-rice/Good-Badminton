"""The owner's stroke labels, ``data/stroke_labels/<stem>.json`` (spec 2026-10-28 sections 5.1-5.2).

Like the rally labels, they are keyed by time and live outside ``outputs/``, so a
re-analysis or a ``match`` delete keeps them. ``apply`` overlays them onto a
``strokes.json`` payload reversibly: the model's own stroke, group and hitter are kept in
``model_*`` fields, so ``model_view`` can always get the plain model payload back and
applying twice equals applying once. This module takes no lock: the caller holds one.

In a doubles result ``strokes.json`` carries the court side ("upper" / "lower") as the
hitter, so a label's ``hitter`` is that side; any non-empty string up to 32 characters
is accepted.
"""
import copy
import json
import math
from collections import Counter
from pathlib import Path

from ..outcomes.labels import write_json_atomic
from .stroke_classifier import STROKES_SCHEMA
from .stroke_types import STROKE_TYPES, group_of

SCHEMA = 1
KINDS = ("label", "not_hit", "unsure", "added")
SPLITS = ("train", "test")
MAX_LABELS = 5000
ATTACH_TOLERANCE_SEC = 0.15
_MAX_HITTER_CHARS = 32
_NEEDS_STROKE = ("label", "added")


def empty():
    return {"schema": SCHEMA, "revision": 0, "split": "train", "labels": []}


def _time(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    return value if math.isfinite(value) and value >= 0 else None


def _label(raw):
    """One normalised label, or None when it is not a valid label."""
    if not isinstance(raw, dict):
        return None
    time_sec = _time(raw.get("time_sec"))
    hitter, kind, stroke = raw.get("hitter"), raw.get("kind"), raw.get("stroke")
    if time_sec is None or kind not in KINDS:
        return None
    if not isinstance(hitter, str) or not hitter or len(hitter) > _MAX_HITTER_CHARS:
        return None
    if kind in _NEEDS_STROKE:
        if stroke not in STROKE_TYPES:
            return None
    else:
        stroke = None
    return {"time_sec": time_sec, "hitter": hitter, "stroke": stroke, "kind": kind}


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
    revision, split = doc.get("revision"), doc.get("split", "train")
    labels = validate_labels(doc.get("labels"))
    if type(revision) is not int or revision < 0 or split not in SPLITS or labels is None:
        return None
    return {"schema": SCHEMA, "revision": revision, "split": split, "labels": labels}


def save(path, doc):
    """Write the label document atomically."""
    write_json_atomic(path, doc)


def _recount(payload):
    strokes = payload["strokes"]
    payload["distribution"] = dict(Counter(s.get("stroke") for s in strokes))
    payload["groups"] = dict(Counter(s.get("group") for s in strokes if s.get("group")))


def model_view(doc):
    """``doc`` (a strokes.json payload) with any previous owner overlay removed."""
    out = copy.deepcopy(doc)
    strokes = []
    for s in out.get("strokes") or []:
        if s.get("source") == "owner" and "model_stroke" not in s:
            continue                                     # an owner-added shot
        if "model_stroke" in s:
            s["stroke"], s["group"] = s.pop("model_stroke"), s.pop("model_group", None)
            s["hitter"] = s.pop("model_hitter", s.get("hitter"))
        s.pop("source", None)
        s.pop("owner_unsure", None)
        strokes.append(s)
    for s in out.pop("removed", None) or []:
        s = dict(s)
        s.pop("source", None)
        strokes.append(s)
    out.pop("owner_labels", None)
    out["strokes"] = sorted(strokes, key=lambda s: s["frame"])
    _recount(out)
    return out


def attach(labels, strokes, fps):
    """``(by_index, orphaned, added)``: each non-added label on its nearest hit.

    A hit's time is ``frame / fps``. A label takes the nearest stroke within
    ``ATTACH_TOLERANCE_SEC``; a stroke takes one label, the later one in list order wins
    and the earlier is orphaned. ``by_index`` maps a position in ``strokes`` to its label.
    """
    by_index, orphaned, added = {}, [], []
    times = [s["frame"] / fps for s in strokes]
    for label in labels:
        if label["kind"] == "added":
            added.append(label)
            continue
        best, best_gap = None, ATTACH_TOLERANCE_SEC
        for i, t in enumerate(times):
            gap = abs(t - label["time_sec"])
            if gap <= best_gap and (best is None or gap < best_gap):
                best, best_gap = i, gap
        if best is None:
            orphaned.append(label)
            continue
        if best in by_index:
            orphaned.append(by_index[best])
        by_index[best] = label
    return by_index, orphaned, added


def apply(payload, labels_doc, fps):
    """``payload`` with the owner's labels overlaid (spec 5.2); ``payload`` is not modified."""
    out = model_view(payload)
    by_index, orphaned, added = attach(labels_doc["labels"], out["strokes"], fps)
    strokes, removed, unsure = [], [], 0
    for i, s in enumerate(out["strokes"]):
        label = by_index.get(i)
        if label is None:
            s["source"] = "model"
            strokes.append(s)
        elif label["kind"] == "not_hit":
            removed.append(dict(s, source="model"))
        elif label["kind"] == "unsure":
            s["source"], s["owner_unsure"] = "model", True
            unsure += 1
            strokes.append(s)
        else:
            s["model_stroke"], s["model_group"] = s.get("stroke"), s.get("group")
            s["model_hitter"] = s.get("hitter")
            s["stroke"], s["group"] = label["stroke"], group_of(label["stroke"])
            s["hitter"], s["source"] = label["hitter"], "owner"
            strokes.append(s)
    for label in added:
        strokes.append({"frame": int(round(label["time_sec"] * fps)), "hitter": label["hitter"],
                        "stroke": label["stroke"], "group": group_of(label["stroke"]),
                        "confidence": None, "source": "owner"})
    out["strokes"] = sorted(strokes, key=lambda s: s["frame"])
    out["removed"] = removed
    out["owner_labels"] = {
        "labelled": sum(1 for lb in by_index.values() if lb["kind"] == "label"),
        "added": len(added), "removed": len(removed), "unsure": unsure,
        "orphaned": len(orphaned), "revision": labels_doc["revision"]}
    _recount(out)
    return out


def _json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError, RecursionError, MemoryError):
        return None


def _fps(result_dir):
    for name in ("hits.json", "rally_segments.json"):
        doc = _json(Path(result_dir) / name)
        fps = doc.get("fps") if isinstance(doc, dict) else None
        if not isinstance(fps, bool) and isinstance(fps, (int, float)) and 0 < fps < 1000:
            return float(fps)
    return None


def apply_to_result(result_dir, labels_dir, stem):
    """Rewrite ``<result_dir>/strokes.json`` with the owner's labels; never raises on bad files."""
    try:
        path = Path(result_dir) / "strokes.json"
        payload = _json(path) if path.is_file() else None
        if not isinstance(payload, dict) or payload.get("schema") != STROKES_SCHEMA \
                or not isinstance(payload.get("strokes"), list):
            return {"applied": False, "reason": "no_strokes"}
        doc = load(labels_path(labels_dir, stem))
        if doc is None:
            return {"applied": False, "reason": "unreadable"}
        has_file = labels_path(labels_dir, stem).is_file()
        if not has_file and "owner_labels" not in payload:
            return {"applied": False, "reason": "no_labels"}
        fps = _fps(result_dir)
        if fps is None:
            return {"applied": False, "reason": "no_fps"}
        # no label file but an old overlay (the labels were deleted): back to the model's view
        write_json_atomic(path, apply(payload, doc, fps) if has_file else model_view(payload))
        return {"applied": True, "reason": None}
    except (OSError, KeyError, TypeError, ValueError):
        return {"applied": False, "reason": "unreadable"}


_SHEET_HITTER = {"near": "lower", "far": "upper"}


def import_sheet(sheet_path, labels_dir, results_dir):
    """Import the filled rows of the DJI labelling sheet once; the stems imported.

    Only a video that has a result folder and no label file yet is imported. ``?`` becomes
    ``unsure``, ``not-a-hit`` becomes ``not_hit``, a labelled row without a frame is a
    missed hit (``added``); blank rows are skipped. The split comes from the sheet.
    """
    from .stroke_labels import parse_sheet
    try:
        sheet = parse_sheet(Path(sheet_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    imported = []
    for stem, entry in sheet.items():
        path = labels_path(labels_dir, stem)
        if path.exists() or not (Path(results_dir) / stem).is_dir():
            continue
        labels = []
        for r in entry["rows"]:
            hitter = _SHEET_HITTER.get(r["player"])
            if hitter is None or r["status"] == "blank":
                continue
            if r["status"] == "labelled":
                kind = "label" if r["frame"] is not None else "added"
            else:
                kind = "unsure" if r["status"] == "unsure" else "not_hit"
            if kind in ("unsure", "not_hit") and r["frame"] is None:
                continue
            labels.append({"time_sec": r["t"], "hitter": hitter, "stroke": r["stroke"], "kind": kind})
        labels = validate_labels(labels)
        if not labels:
            continue
        try:
            save(path, {"schema": SCHEMA, "revision": 1, "split": entry["split"], "labels": labels})
        except OSError:
            continue
        imported.append(stem)
    return imported
