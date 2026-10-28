"""One retrain run, as a child process of the app (stroke correction spec 2026-10-28 section 6.2-6.3).

  python -m badminton_analysis.stroke.retrain --labels-dir data/stroke_labels \
      --results-dir outputs --weights-dir weights --project-root .

Runs ``scripts/train_stroke_classifier.py`` as a subprocess, scores the candidate and the live
model on the same test shots, applies the keep rule, promotes or rejects, and prints a final
JSON line ``{"kept": bool, "reason": str, "candidate": metrics|null, "live": metrics|null}``.
Exit 0 when a decision was made (kept or rejected), 1 when no decision could be reached.
"""
import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

from . import model_eval


def _final(kept, reason, candidate=None, live=None, code=0):
    print(json.dumps({"kept": kept, "reason": reason, "candidate": candidate, "live": live}))
    return code


def run(labels_dir, results_dir, weights_dir, project_root, python=None):
    weights_dir, project_root = Path(weights_dir), Path(project_root)
    cand_dir = weights_dir / "candidates"
    cand_dir.mkdir(parents=True, exist_ok=True)
    candidate = cand_dir / f"stroke-classifier-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}.pt"
    cmd = [python or sys.executable, str(project_root / "scripts" / "train_stroke_classifier.py"),
           *model_eval.train_args(project_root, labels_dir, results_dir, candidate)]
    print("training:", " ".join(cmd), flush=True)
    proc = subprocess.run(cmd, cwd=str(project_root))
    if proc.returncode != 0 or not candidate.is_file():
        return _final(False, f"training failed (exit {proc.returncode})", code=1)
    shots = model_eval.test_shots(labels_dir, results_dir)
    if not shots:
        model_eval.reject(weights_dir, candidate, {"reason": "no test shots", "live": None,
                          "candidate": None, "test_videos": [], "train_labels": 0})
        return _final(False, "no labelled test shots", code=1)
    videos = [s for s, _, _ in shots]
    train_labels = sum(model_eval._trainable(doc) for _, doc in model_eval._label_docs(labels_dir, "train"))
    cand_m = model_eval.score_weights(candidate, shots, results_dir)
    live = weights_dir / model_eval.LIVE_NAME
    live_m = model_eval.score_weights(live, shots, results_dir) if live.is_file() else None
    keep, reason = model_eval.decide(cand_m, live_m)
    record = {"reason": reason, "live": live_m, "candidate": cand_m,
              "test_videos": videos, "train_labels": train_labels}
    (model_eval.promote if keep else model_eval.reject)(weights_dir, candidate, record)
    return _final(keep, reason, cand_m, live_m)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--labels-dir", required=True)
    p.add_argument("--results-dir", required=True)
    p.add_argument("--weights-dir", required=True)
    p.add_argument("--project-root", required=True)
    a = p.parse_args(argv)
    try:
        return run(a.labels_dir, a.results_dir, a.weights_dir, a.project_root)
    except Exception as exc:
        return _final(False, f"retrain failed: {type(exc).__name__}: {exc}", code=1)


if __name__ == "__main__":
    sys.exit(main())
