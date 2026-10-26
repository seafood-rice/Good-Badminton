"""Score the automatic rally-outcome labeller against the owner's labels (spec section 8).

    python scripts/score_rally_outcomes.py outputs/<stem> [outputs/<stem> ...]
        [--labels-dir data/rally_labels] [--out score.json]

Only the owner's ``upper`` / ``lower`` verdicts count as ground truth (``unsure``
and ``not_point`` are excluded). An owner-added point has no segment, so the
automatic labeller cannot claim it: it counts against coverage.

- precision: correct automatic claims / automatic claims on owner-labelled segments
- coverage: automatic claims / owner points
- per evidence: how often each signal alone named the owner's winner. For
  ``last_hitter`` this is the "last hitter won" rate, a description rather than a
  prediction (who hit last wins on a winner, loses on an error).
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from badminton_analysis.outcomes import build as ob  # noqa: E402
from badminton_analysis.outcomes import labels as lab  # noqa: E402


def _ratio(n, d):
    return round(n / d, 4) if d else None


def _counter():
    return {"named": 0, "correct": 0}


def score(out_dirs, labels_dir=ob.DEFAULT_LABELS_DIR):
    total = {"owner_points": 0, "auto_claims": 0, "correct": 0}
    per_evidence = {"landing": _counter(), "next_server": _counter(), "last_hitter": _counter()}
    per_video = []
    for out_dir in out_dirs:
        out_dir = Path(out_dir)
        labels_doc = lab.load(lab.labels_path(labels_dir, out_dir.name)) or lab.empty()
        segments, auto = ob.segment_evidence(out_dir)
        by_segment, added = lab.attach(labels_doc["labels"], segments)
        video = {"stem": out_dir.name, "owner_points": 0, "auto_claims": 0, "correct": 0}
        video["owner_points"] += sum(1 for a in added if a["verdict"] in lab.SIDES)
        for seg_id, label in by_segment.items():
            truth = label["verdict"]
            if truth not in lab.SIDES:
                continue
            video["owner_points"] += 1
            row = auto[seg_id]
            claim = row["decision"]["winner_side"]
            if claim is not None:
                video["auto_claims"] += 1
                video["correct"] += claim == truth
            evidence = row["evidence"]
            signals = {"landing": (evidence["landing"] or {}).get("winner"),
                       "next_server": evidence["next_server"],
                       "last_hitter": evidence["last_hitter"]}
            for name, side in signals.items():
                if side is not None:
                    per_evidence[name]["named"] += 1
                    per_evidence[name]["correct"] += side == truth
        video["precision"] = _ratio(video["correct"], video["auto_claims"])
        video["coverage"] = _ratio(video["auto_claims"], video["owner_points"])
        per_video.append(video)
        for key in total:
            total[key] += video[key]
    for counts in per_evidence.values():
        counts["accuracy"] = _ratio(counts["correct"], counts["named"])
    return dict(total, precision=_ratio(total["correct"], total["auto_claims"]),
                coverage=_ratio(total["auto_claims"], total["owner_points"]),
                per_evidence=per_evidence, per_video=per_video)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("out_dirs", nargs="+")
    parser.add_argument("--labels-dir", default=str(ob.DEFAULT_LABELS_DIR))
    parser.add_argument("--out")
    args = parser.parse_args(argv)
    result = score(args.out_dirs, args.labels_dir)
    text = json.dumps(result, indent=1)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
