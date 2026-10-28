"""Score a stroke model on the owner's test-split stroke labels (spec 2026-10-28 section 6.3).

Usage:
  python scripts/score_stroke_model.py [--weights weights/stroke-classifier.pt] \
      [--labels-dir data/stroke_labels] [--results-dir outputs] [--json]

Exit 0 = the owner's bar is met, 1 = not met or no test shots, 2 = the weights are
unreadable, or a test video was trained or calibrated on (sidecar train_matches/val_matches).
"""
import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from badminton_analysis.stroke import model_eval  # noqa: E402
from badminton_analysis.stroke.stroke_metrics import check_bar  # noqa: E402
from badminton_analysis.stroke.stroke_model import meta_path  # noqa: E402


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--weights", default=os.path.join(ROOT, "weights", "stroke-classifier.pt"))
    p.add_argument("--labels-dir", default=os.path.join(ROOT, "data", "stroke_labels"))
    p.add_argument("--results-dir", default=os.path.join(ROOT, "outputs"))
    p.add_argument("--json", action="store_true", help="print the result as JSON")
    a = p.parse_args(argv)
    shots = model_eval.test_shots(a.labels_dir, a.results_dir)
    n = sum(len(truth) for _, _, truth in shots)
    if not n:
        print(f"no test shots: no test-split label file in {a.labels_dir} has a labelled "
              f"hit with a result in {a.results_dir}")
        return 1
    sidecar = None
    if os.path.isfile(meta_path(a.weights)):
        with open(meta_path(a.weights), encoding="utf-8") as fh:
            sidecar = json.load(fh)
    if sidecar is not None:
        seen = set(sidecar.get("train_matches") or []) | set(sidecar.get("val_matches") or [])
        leaked = sorted(s for s, _, _ in shots if {s, f"owner:{s}", f"dji:{s}"} & seen)
        if leaked:
            print(f"{', '.join(leaked)}: in the model's train_matches/val_matches; scoring them "
                  f"would not measure held-out accuracy")
            return 2
    try:
        metrics = model_eval.score_weights(a.weights, shots, a.results_dir)
    except Exception as exc:
        print(f"cannot score {a.weights}: {exc}")
        return 2
    met, reasons = check_bar(metrics)
    if a.json:
        print(json.dumps({"shots": n, "videos": [s for s, _, _ in shots], "metrics": metrics,
                          "bar_met": met, "bar_reasons": reasons}, indent=1))
    else:
        print(f"{n} test shots in {len(shots)} video(s)")
        print(f"7-group accuracy {metrics['accuracy']}, unknown rate {metrics['unknown_rate']}, "
              f"12-type accuracy {metrics['type_accuracy']}")
        for g, r in metrics["recall"].items():
            print(f"  {g}: recall {r['recall']} ({r['correct']}/{r['n']})")
        print("bar MET" if met else "bar NOT MET: " + "; ".join(reasons))
    return 0 if met else 1


if __name__ == "__main__":
    raise SystemExit(main())
