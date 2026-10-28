"""One rally retrain run, as a child process of the app (rally-winner-learning spec 7.3).

  python -m badminton_analysis.outcomes.retrain --labels-dir data/rally_labels \
      --results-dir outputs --athletes data/athletes.json --weights-dir weights

Measures the learned segmenter and winner decoder leave-one-match-out (``model_eval.evaluate``),
applies each keep rule, trains the models that won on every labelled match, promotes them (and
records the rest as rejected) through ``models/store.py``, and prints a final JSON line
``{"segmenter": {"kept", "reason"}, "decoder": {"kept", "reason"}, "totals": {...}}``.
Exit 0 when a decision was made (every model kept or rejected), 1 when none could be reached.
"""
import argparse
import json
import sys
from pathlib import Path

from . import model_eval as me

PROMOTE_PHASE = "PHASE promote"
MODELS = (("segmenter", me.SEGMENTER_FILE), ("decoder", me.DECODER_FILE))


def _final(models, totals=None, code=0):
    print(json.dumps({"segmenter": models["segmenter"], "decoder": models["decoder"], "totals": totals}))
    return code


def _no_decision(reason, totals=None):
    both = {"kept": False, "reason": reason}
    return _final({"segmenter": both, "decoder": dict(both)}, totals, code=1)


def run(labels_dir, results_dir, athletes_path, weights_dir):
    weights_dir = Path(weights_dir)
    me.recover(weights_dir)
    usable, _skipped = me.scan(labels_dir, results_dir)
    if len(usable) < me.MIN_MATCHES:
        return _no_decision({"code": "not_enough_matches"})
    points = sum(n for _s, _d, n in usable)       # what the owner had labelled when this run started
    result = me.evaluate(labels_dir, results_dir, athletes_path, None)
    totals = result["totals"]
    decision = me.decide(totals)
    keep = {name: decision[name][0] for name, _f in MODELS}
    candidates = me.fit_candidates(labels_dir, results_dir, athletes_path, totals, keep)
    models = {}
    for name, _f in MODELS:
        kept, reason = decision[name]
        if kept and candidates[name] is None:
            kept, reason = False, {"code": "retrain_failed", "detail": "the final model could not be trained"}
        models[name] = {"kept": kept, "reason": reason}
    files = {}
    cand_dir = weights_dir / "candidates"
    for name, file in MODELS:
        if models[name]["kept"]:
            cand_dir.mkdir(parents=True, exist_ok=True)
            path = cand_dir / f"{Path(file).stem}-{candidates[name]['version']}.json"
            path.write_text(json.dumps(candidates[name], ensure_ascii=False, indent=1), encoding="utf-8")
            files[file] = path
    record = {"models": models, "points": points, "totals": totals, "matches": [s for s, _d, _n in usable]}
    if files:
        # from here a kill could land between the file swaps: the app refuses to cancel
        print(PROMOTE_PHASE, flush=True)
        me.promote(weights_dir, files, record)
    else:
        me.reject(weights_dir, {}, record)
    return _final(models, totals)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--labels-dir", required=True)
    p.add_argument("--results-dir", required=True)
    p.add_argument("--athletes", required=True)
    p.add_argument("--weights-dir", required=True)
    a = p.parse_args(argv)
    try:
        return run(a.labels_dir, a.results_dir, a.athletes, a.weights_dir)
    except Exception as exc:
        return _no_decision({"code": "retrain_failed", "detail": f"{type(exc).__name__}: {exc}"[:200]})


if __name__ == "__main__":
    sys.exit(main())
