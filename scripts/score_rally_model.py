"""Leave-one-match-out score of the learned rally models (rally-winner-learning spec 7.1).

    python scripts/score_rally_model.py [--labels-dir data/rally_labels] [--results-dir outputs]
        [--athletes data/athletes.json] [--weights-dir weights] [--json]

For every owner-labelled match with learned hits the segmenter and the winner decoder are
trained on the OTHER labelled matches and scored on it with its labels hidden. Prints one
row per match and the pooled totals the keep rules use, then the keep decision of each model.
Read-only: nothing is trained into ``weights/`` or written under ``outputs/``;
``--weights-dir`` is only read, to name the models that are live now.
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from badminton_analysis.outcomes import model_eval as me  # noqa: E402


def _n(v, digits=2):
    return "-" if v is None else f"{v:.{digits}f}"


def table(result):
    """The text report of an ``evaluate`` result."""
    lines = [f"{'match':<34}{'points':>7}  {'seg F1':>7}{'swing F1':>10}  {'dec claims':>11}{'right':>6}"
             f"{'prec':>6}  {'rule claims':>12}{'right':>6}"]
    for stem, m in result["per_match"].items():
        seg, swing, d, r = m["segmenter"], m["swing"], m["decoder"], m["rules"]
        lines.append(f"{stem[:33]:<34}{m['points']:>7}  {_n(seg and seg['f1']):>7}{_n(swing and swing['f1']):>10}"
                     f"  {d['claimed'] if d else '-':>11}{d['correct'] if d else '-':>6}"
                     f"{_n(d and d['precision']):>6}  {r['claimed'] if r else '-':>12}{r['correct'] if r else '-':>6}")
    t = result["totals"]
    lines.append(f"{'POOLED':<34}{t['points']:>7}  {_n(t['seg_f1']):>7}{_n(t['swing_f1']):>10}"
                 f"  {t['dec_claimed']:>11}{t['dec_correct']:>6}{_n(t['dec_precision']):>6}"
                 f"  {t['rules_claimed']:>12}{t['rules_correct']:>6}")
    lines.append(f"decoder claim threshold (target precision {me.TARGET_PRECISION}): "
                 f"{'unreachable' if t['threshold'] is None else _n(t['threshold'], 3)}")
    for name, (keep, reason) in me.decide(t).items():
        lines.append(f"{name}: {'KEEP' if keep else 'REJECT'} - {me.describe(reason)}")
    for s in result["skipped"]:
        lines.append(f"skipped {s['stem']}: {s['reason']}")
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--labels-dir", default=str(ROOT / "data" / "rally_labels"))
    ap.add_argument("--results-dir", default=str(ROOT / "outputs"))
    ap.add_argument("--athletes", default=str(ROOT / "data" / "athletes.json"))
    ap.add_argument("--weights-dir", default=str(ROOT / "weights"))
    ap.add_argument("--json", action="store_true", help="print the result as JSON")
    a = ap.parse_args(argv)
    result = me.evaluate(a.labels_dir, a.results_dir, a.athletes, None)
    live = me.live_views(a.weights_dir)
    if a.json:
        print(json.dumps({**result, "live": live,
                          "decision": {k: {"kept": v[0], "reason": v[1]} for k, v in me.decide(result["totals"]).items()}},
                         indent=1))
        return 0
    if not result["per_match"]:
        print("no labelled match with learned hits")
        for s in result["skipped"]:
            print(f"skipped {s['stem']}: {s['reason']}")
        return 1
    print(table(result))
    print("live now: " + ", ".join(f"{k} {v['version'] if v else 'none'}" for k, v in live.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
