"""Re-label a finished result with the current stroke classifier (spec 2026-10-22 section 4.6).

  python -m badminton_analysis.stroke.relabel [--weights weights/stroke-classifier.pt] <result_dir>...

Uses the saved hit inputs (hit_signals.npz, hits.json), so the video is not re-processed.
Rewrites strokes.json (schema 2), re-scores strokes.jsonl / technique_summary.json from
each report's measured metrics, and removes the cached training_plan.json.
"""
import argparse
import json
import os
import sys

from ..analysis.biomechanics import rescore_report
from ..analysis.technique_writer import build_match_summary, write_stroke_reports
from ..data.writer import write_json
from . import stroke_classifier as sc
from .hit_signals import load_signals

DEFAULT_WEIGHTS = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "weights", "stroke-classifier.pt")


def _json(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def relabel(result_dir, weights):
    meta = _json(os.path.join(result_dir, "metadata.json")) or {}
    if meta.get("match_format") == "doubles":
        return {"result": result_dir, "skipped": "doubles"}
    det = (_json(os.path.join(result_dir, "rally_segments.json")) or {}).get("detection") or {}
    if det.get("stroke_labels_withheld", bool(det.get("degraded"))):
        return {"result": result_dir, "skipped": "labels withheld"}
    hits_doc = _json(os.path.join(result_dir, "hits.json"))
    if not hits_doc or not hits_doc.get("hits"):
        return {"result": result_dir, "skipped": "no hits.json"}
    hits = [{"frame": int(h["frame"]), "hitter": h["hitter"], "confidence": h.get("confidence")}
            for h in hits_doc["hits"]]
    labels, info = None, None
    if weights and os.path.isfile(weights):
        try:
            model, mmeta, info = sc.load_classifier(weights)
            labels = sc.classify_hits(load_signals(os.path.join(result_dir, "hit_signals.npz")),
                                      hits, model, mmeta)
        except Exception as exc:
            print(f"{result_dir}: classifier unavailable ({exc!r}); every hit is unknown")
            labels, info = None, None
    if labels is None:
        labels = sc.unknown_labels(hits)
    write_json(os.path.join(result_dir, "strokes.json"),
               sc.build_payload(labels, info, hits_doc.get("source"), hits_doc.get("shuttle_source")))
    by_key = {(l["frame"], l["hitter"]): l for l in labels}
    reports_path = os.path.join(result_dir, "strokes.jsonl")
    n_reports = 0
    if os.path.isfile(reports_path):
        with open(reports_path, encoding="utf-8") as fh:
            reports = [json.loads(line) for line in fh if line.strip()]
        new = []
        for r in reports:
            label = by_key.get((r.get("contact_frame"), r.get("player_side"))) or {}
            out = rescore_report(r, label.get("stroke") or "unknown")
            out["confidence"] = label.get("confidence")
            new.append(out)
        write_stroke_reports(reports_path, new)
        write_json(os.path.join(result_dir, "technique_summary.json"), build_match_summary(new))
        n_reports = len(new)
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
    a = p.parse_args(argv)
    for d in a.results:
        print(json.dumps(relabel(d, a.weights)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
