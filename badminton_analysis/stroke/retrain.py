"""One retrain run, as a child process of the app (stroke correction spec 2026-10-28 section 6.2-6.3).

  python -m badminton_analysis.stroke.retrain --labels-dir data/stroke_labels \
      --results-dir outputs --weights-dir weights --project-root .

Runs ``scripts/train_stroke_classifier.py`` as a subprocess, scores the candidate and the live
model on the same test shots, applies the keep rule, promotes or rejects, and prints a final
JSON line ``{"kept": bool, "reason": code dict, "candidate": metrics|null, "live": metrics|null}``.
Exit 0 when a decision was made (kept or rejected), 1 when no decision could be reached.
"""
import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

from . import model_eval
from .stroke_model import meta_path

PROMOTE_PHASE = "PHASE promote"


def _final(kept, reason, candidate=None, live=None, code=0):
    print(json.dumps({"kept": kept, "reason": reason, "candidate": candidate, "live": live}))
    return code


def _leaked(candidate):
    """Stems the candidate's sidecar says it trained or calibrated on (``s``, ``owner:s``, ``dji:s``)."""
    sidecar = model_eval._json(meta_path(candidate))
    if not isinstance(sidecar, dict):
        return set()
    return set(sidecar.get("train_matches") or []) | set(sidecar.get("val_matches") or [])


def run(labels_dir, results_dir, weights_dir, project_root, python=None):
    weights_dir, project_root = Path(weights_dir), Path(project_root)
    cand_dir = weights_dir / "candidates"
    cand_dir.mkdir(parents=True, exist_ok=True)
    candidate = cand_dir / f"stroke-classifier-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}.pt"
    cmd = [python or sys.executable, str(project_root / "scripts" / "train_stroke_classifier.py"),
           *model_eval.train_args(project_root, labels_dir, results_dir, candidate)]
    # what the owner had labelled when this run started: the split may change while it trains
    snapshot = model_eval.test_shots(labels_dir, results_dir)
    train_labels = model_eval.train_count(labels_dir)
    print("training:", " ".join(cmd), flush=True)
    proc = subprocess.run(cmd, cwd=str(project_root))
    if proc.returncode != 0 or not candidate.is_file():
        return _final(False, {"code": "training_failed", "exit": proc.returncode}, code=1)
    seen = _leaked(candidate)
    shots = [sh for sh in snapshot if not ({sh[0], f"owner:{sh[0]}", f"dji:{sh[0]}"} & seen)]
    if not shots:
        model_eval.reject(weights_dir, candidate, {
            "reason": {"code": "no_test_shots"}, "live": None, "candidate": None,
            "test_videos": [], "train_labels": train_labels})
        return _final(False, {"code": "no_test_shots"}, code=1)
    videos = [s for s, _, _ in shots]
    cand_m = model_eval.score_weights(candidate, shots, results_dir)
    live = weights_dir / model_eval.LIVE_NAME
    live_m = model_eval.score_weights(live, shots, results_dir) if live.is_file() else None
    keep, reason = model_eval.decide(cand_m, live_m)
    record = {"reason": reason, "live": live_m, "candidate": cand_m, "test_videos": videos,
              "train_labels": train_labels, "bar_met": model_eval._bar_met(cand_m)}
    if keep:
        # from here a kill could land between the two os.replace calls: the app refuses to cancel
        print(PROMOTE_PHASE, flush=True)
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
        return _final(False, {"code": "retrain_failed", "detail": f"{type(exc).__name__}: {exc}"[:200]}, code=1)


if __name__ == "__main__":
    sys.exit(main())
