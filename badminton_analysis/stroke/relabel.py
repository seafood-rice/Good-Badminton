"""Re-label a finished result with the current stroke classifier (spec 2026-10-22 section 4.6).

  python -m badminton_analysis.stroke.relabel [--weights weights/stroke-classifier.pt]       [--allow-unknown] <result_dir>...

Uses the saved hit inputs (hit_signals.npz, hits.json), so the video is not re-processed.
Rewrites strokes.json (schema 2), re-scores strokes.jsonl / technique_summary.json from
each report's measured metrics, and removes the cached training_plan.json.

With ``labels_dir`` (the CLI defaults to data/stroke_labels) the owner's stroke corrections
are re-applied over the new model output and the reports are re-scored with the applied type.

Without the weights file a result is left untouched ("skipped": "weights not found"), so
good labels are never replaced by all-unknown ones; --allow-unknown writes them anyway.
"""
import argparse
import json
import os
import sys
import time

from ..analysis.biomechanics import rescore_report
from ..analysis.technique_writer import build_match_summary, write_stroke_reports
from ..data.writer import clean_value
from ..outcomes.labels import write_json_atomic
from . import owner_labels
from . import stroke_classifier as sc
from .hit_signals import load_signals

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEFAULT_WEIGHTS = os.path.join(_ROOT, "weights", "stroke-classifier.pt")
DEFAULT_LABELS_DIR = os.path.join(_ROOT, "data", "stroke_labels")


def write_json(path, payload):
    """Atomic JSON write (temp file + os.replace): the relabel child can be killed at any moment."""
    write_json_atomic(path, clean_value(payload))


STALE_TEMP_SEC = 300


def clean_stale_temps(result_dir, max_age=STALE_TEMP_SEC):
    """Delete the atomic writers' leftover ``.<name>-*.tmp`` files (a killed child) older than ``max_age``."""
    now = time.time()
    try:
        entries = [e for e in os.scandir(result_dir) if e.is_file() and e.name.startswith(".")
                   and e.name.endswith(".tmp") and "-" in e.name]
    except OSError:
        return
    for entry in entries:
        try:
            if now - entry.stat().st_mtime > max_age:
                os.remove(entry.path)
        except OSError:
            pass


def _json(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def _read_reports(result_dir):
    """The parsed ``strokes.jsonl`` rows, or None when the file does not exist."""
    reports_path = os.path.join(result_dir, "strokes.jsonl")
    if not os.path.isfile(reports_path):
        return None
    reports, bad = [], 0
    with open(reports_path, encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                rec = None
            if isinstance(rec, dict):
                reports.append(rec)
            else:
                bad += 1
    if bad:
        print(f"{result_dir}: skipped {bad} unusable line(s) in strokes.jsonl")
    return reports


def _write_rescored(result_dir, reports, labels):
    by_key = {(l["frame"], l.get("model_hitter", l["hitter"])): l for l in labels}
    new = []
    for r in reports:
        label = by_key.get((r.get("contact_frame"), r.get("player_side"))) or {}
        out = rescore_report(r, label.get("stroke") or "unknown")
        out["confidence"] = label.get("confidence")
        new.append(out)
    write_stroke_reports(os.path.join(result_dir, "strokes.jsonl"), new)
    write_json(os.path.join(result_dir, "technique_summary.json"), build_match_summary(new))
    return len(new)


def rescore_reports(result_dir, labels=None):
    """Re-score ``strokes.jsonl`` / ``technique_summary.json`` with the types in ``strokes.json``.

    Used after the owner's labels were applied. Returns the number of reports rescored; 0 (and
    nothing written) when there are no reports or no readable strokes.json.
    """
    result_dir = str(result_dir)
    try:
        reports = _read_reports(result_dir)
        if reports is None:
            return 0
        if labels is None:
            labels = (_json(os.path.join(result_dir, "strokes.json")) or {}).get("strokes")
        if not isinstance(labels, list) or not labels:
            return 0
        return _write_rescored(result_dir, reports, labels)
    except (OSError, KeyError, TypeError, ValueError) as exc:
        print(f"{result_dir}: reports not re-scored ({exc!r})")
        return 0


def relabel(result_dir, weights, allow_unknown=False, labels_dir=None):
    clean_stale_temps(result_dir)
    meta = _json(os.path.join(result_dir, "metadata.json")) or {}
    if meta.get("match_format") == "doubles":
        # no model relabel for doubles, but the owner's corrections still apply
        applied = labels_dir is not None and owner_labels.apply_to_result(
            result_dir, labels_dir, os.path.basename(os.path.normpath(result_dir)))["applied"]
        return {"result": result_dir, "skipped": "doubles", "applied": bool(applied)}
    det = (_json(os.path.join(result_dir, "rally_segments.json")) or {}).get("detection") or {}
    if det.get("stroke_labels_withheld", bool(det.get("degraded"))):
        return {"result": result_dir, "skipped": "labels withheld"}
    if not (weights and os.path.isfile(weights)) and not allow_unknown:
        print(f"{result_dir}: stroke weights not found ({weights}); left untouched "
              f"(pass --allow-unknown to label every hit unknown)")
        return {"result": result_dir, "skipped": "weights not found"}
    hits_doc = _json(os.path.join(result_dir, "hits.json"))
    if not hits_doc or not hits_doc.get("hits"):
        return {"result": result_dir, "skipped": "no hits.json"}
    hits = [{"frame": int(h["frame"]), "hitter": h["hitter"], "confidence": h.get("confidence")}
            for h in hits_doc["hits"]
            if isinstance(h, dict) and h.get("frame") is not None and h.get("hitter")]
    if len(hits) != len(hits_doc["hits"]):
        print(f"{result_dir}: skipped {len(hits_doc['hits']) - len(hits)} unusable hit(s)")
    if not hits:
        return {"result": result_dir, "skipped": "no hits.json"}
    # parse the old reports before writing anything, so a bad file leaves the result untouched
    reports = _read_reports(result_dir)
    labels, info, reason = None, None, "no stroke model"
    if weights and os.path.isfile(weights):
        try:
            model, mmeta, info = sc.load_classifier(weights)
            labels = sc.classify_hits(load_signals(os.path.join(result_dir, "hit_signals.npz")),
                                      hits, model, mmeta)
        except Exception as exc:
            print(f"{result_dir}: classifier unavailable ({exc!r}); every hit is unknown")
            labels, info = None, None
            reason = f"classifier failed: {type(exc).__name__}"
        else:
            reason = None
    if labels is None:
        labels = sc.unknown_labels(hits)
    write_json(os.path.join(result_dir, "strokes.json"),
               sc.build_payload(labels, info, hits_doc.get("source"), hits_doc.get("shuttle_source"),
                                reason=reason))
    applied = labels_dir is not None and owner_labels.apply_to_result(
        result_dir, labels_dir, os.path.basename(os.path.normpath(result_dir)))["applied"]
    if applied:
        # the reports are scored with the owner's types; they stay keyed by the model's hitter
        labels = (_json(os.path.join(result_dir, "strokes.json")) or {}).get("strokes") or labels
    n_reports = _write_rescored(result_dir, reports, labels) if reports is not None else 0
    try:
        os.remove(os.path.join(result_dir, "training_plan.json"))
    except FileNotFoundError:
        pass
    return {"result": result_dir, "labelled": len(labels), "reports": n_reports,
            "classifier": info is not None}


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[1].strip())
    p.add_argument("results", nargs="+")
    p.add_argument("--weights", default=DEFAULT_WEIGHTS)
    p.add_argument("--allow-unknown", action="store_true",
                   help="without the weights file, still rewrite every hit as unknown")
    p.add_argument("--labels-dir", default=DEFAULT_LABELS_DIR,
                   help="owner stroke labels to re-apply (default data/stroke_labels)")
    a = p.parse_args(argv)
    for d in a.results:
        print(json.dumps(relabel(d, a.weights, allow_unknown=a.allow_unknown,
                                 labels_dir=a.labels_dir)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
