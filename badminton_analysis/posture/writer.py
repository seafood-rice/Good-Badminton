"""Write per-rep drill reports (JSONL) and build the drill-level summary."""
import json
import os
from collections import Counter, defaultdict

from ..data.writer import clean_value


def write_rep_reports(path, reports):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for report in reports:
            f.write(json.dumps(clean_value(report), ensure_ascii=False, separators=(",", ":")))
            f.write("\n")


def _stddev(values):
    n = len(values)
    if n < 2:
        return 0.0 if n == 1 else None
    mean = sum(values) / n
    var = sum((v - mean) ** 2 for v in values) / n
    return var ** 0.5


def build_drill_summary(reports, stroke_type):
    scored = [r for r in reports if r.get("overall_score") is not None]
    scores = [r["overall_score"] for r in scored]

    mean_score = round(sum(scores) / len(scores), 1) if scores else None
    consistency = round(_stddev(scores), 2) if scores else None

    best_rep = worst_rep = None
    if scored:
        best = max(scored, key=lambda r: r["overall_score"])
        worst = min(scored, key=lambda r: r["overall_score"])
        best_rep = {"rep_id": best["rep_id"], "score": best["overall_score"]}
        worst_rep = {"rep_id": worst["rep_id"], "score": worst["overall_score"]}

    metric_scores = defaultdict(list)
    weakness_counter = Counter()
    strength_counter = Counter()
    for r in reports:
        for metric, m in (r.get("per_metric") or {}).items():
            if m.get("score") is not None:
                metric_scores[metric].append(m["score"])
        for w in r.get("weaknesses", []):
            weakness_counter[w["metric"]] += 1
        for s in r.get("strengths", []):
            strength_counter[s] += 1

    per_metric_avg = {m: round(sum(v) / len(v), 1) if v else None
                      for m, v in metric_scores.items()}

    def _ranked(counter):
        return [{"metric": m, "count": c} for m, c in counter.most_common()]

    return {
        "stroke_type": stroke_type,
        "rep_count": len(reports),
        "mean_score": mean_score,
        "best_rep": best_rep,
        "worst_rep": worst_rep,
        "consistency": consistency,
        "per_metric_avg": per_metric_avg,
        "recurring_weaknesses": _ranked(weakness_counter),
        "strengths": _ranked(strength_counter),
    }
