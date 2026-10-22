"""Score strokes.json against the DJI stroke-label sheet (spec 2026-10-22 section 6.2).

Usage:
  python scripts/score_strokes.py --labels outputs/dji-stroke-labels/LABELS.md \
      [--results-dir outputs] [--split test] [--tol 0.2] [--json result.json]       [--weights weights/stroke-classifier.pt]

Exit 0 = the owner's bar is met, 1 = not met, 2 = a result's strokes.json is missing or
from the old classifier (re-label it first), or a scored video was trained or calibrated on
(the model's sidecar lists it in train_matches or val_matches).
"""
import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from badminton_analysis.stroke import stroke_labels as sl  # noqa: E402
from badminton_analysis.stroke.stroke_classifier import is_current  # noqa: E402
from badminton_analysis.stroke.stroke_metrics import check_bar, score  # noqa: E402
from badminton_analysis.stroke.stroke_model import meta_path  # noqa: E402


def leaked_videos(weights, names):
    """Scored videos the model saw in training or calibration; None when there is no sidecar."""
    sidecar = meta_path(weights)
    if not os.path.isfile(sidecar):
        return None
    with open(sidecar, encoding="utf-8") as fh:
        meta = json.load(fh)
    seen = set(meta.get("train_matches") or []) | set(meta.get("val_matches") or [])
    return sorted(n for n in names if f"dji:{n}" in seen)


def match(labelled, predicted, fps, tol=0.2):
    used, pairs, missed = set(), [], 0
    for lab in labelled:
        best = None
        for k, p in enumerate(predicted):
            if k in used:
                continue
            dt = abs((int(p["frame"]) - int(lab["frame"])) / fps)
            if dt > tol:
                continue
            key = (p.get("hitter") != lab["hitter"], dt)
            if best is None or key < best[0]:
                best = (key, k)
        if best is None:
            missed += 1
            continue
        used.add(best[1])
        pairs.append((lab["stroke"], predicted[best[1]]["stroke"]))
    return pairs, missed


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--labels", required=True)
    p.add_argument("--results-dir", default=os.path.join(ROOT, "outputs"))
    p.add_argument("--split", default="test", choices=["test", "train"])
    p.add_argument("--tol", type=float, default=0.2)
    p.add_argument("--json")
    p.add_argument("--weights", default=os.path.join(ROOT, "weights", "stroke-classifier.pt"),
                   help="the model whose strokes.json files are scored (its sidecar is checked)")
    a = p.parse_args(argv)
    with open(a.labels, encoding="utf-8") as fh:
        sheet = sl.parse_sheet(fh.read())
    names = [n for n, e in sheet.items() if e["split"] == a.split]
    leaked = leaked_videos(a.weights, names)
    if leaked is None:
        print(f"warning: no sidecar for {a.weights}; cannot check that the scored videos "
              f"were held out of training")
    elif leaked:
        print(f"{', '.join(leaked)}: in the model's train_matches/val_matches "
              f"({meta_path(a.weights)}); scoring them would not measure held-out accuracy")
        return 2
    pairs, missed, videos = [], 0, []
    for name, entry in sheet.items():
        if entry["split"] != a.split:
            continue
        path = os.path.join(a.results_dir, name, "strokes.json")
        try:
            with open(path, encoding="utf-8") as fh:
                payload = json.load(fh)
        except (OSError, ValueError):
            payload = None
        if not is_current(payload):
            print(f"{name}: strokes.json missing or from the old classifier; re-label it first")
            return 2
        try:
            with open(os.path.join(a.results_dir, name, "hits.json"), encoding="utf-8") as fh:
                fps = float(json.load(fh)["fps"])
        except (OSError, ValueError, KeyError, TypeError):
            print(f"{name}: hits.json missing or unreadable; re-run the analysis")
            return 2
        pv, mv = match(sl.labelled_hits(entry), payload["strokes"], fps, a.tol)
        pairs += pv
        missed += mv
        videos.append(name)
    metrics = score(pairs)
    passed, reasons = check_bar(metrics)
    print(f"videos ({a.split}): {', '.join(videos) or 'none'}")
    print(f"matched {metrics['matched']} labelled hits; {missed} not found by the hit detector")
    print(f"group accuracy {metrics['accuracy']}  unknown rate {metrics['unknown_rate']}  "
          f"12-type accuracy {metrics['type_accuracy']}")
    for g, r in metrics["recall"].items():
        print(f"  {g:<6} recall {r['recall']} ({r['correct']}/{r['n']})")
    print("PASS" if passed else "FAIL: " + "; ".join(reasons))
    if a.json:
        with open(a.json, "w", encoding="utf-8") as fh:
            json.dump({"split": a.split, "videos": videos, "missed_by_hit_detector": missed,
                       "metrics": metrics, "passed": passed, "reasons": reasons}, fh, indent=2)
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
