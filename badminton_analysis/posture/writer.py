"""Write per-rep drill reports (JSONL) and build the drill-level summary."""
import json
import os
from collections import Counter, defaultdict

from ..data.writer import clean_value


def compute_final_score(overall_score, ai_score):
    """The per-rep final score. Currently the biomechanical heuristic only.

    The AI quality score is intentionally EXCLUDED: on real footage it is per-rep
    unreliable (weak-supervision labels + domain shift; measured anti-correlation with
    form quality). It is surfaced separately as an experimental number. To enable a
    blend later (after the AI is retrained on in-domain per-rep labels), change ONLY
    this function, e.g. `return 0.8*overall_score + 0.2*ai_score`.
    """
    return overall_score


def write_rep_reports(path, reports):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for report in reports:
            overall = report.get("overall_score")
            report["final_score"] = (
                compute_final_score(overall, report.get("ai_score")) if overall is not None else None
            )
            f.write(json.dumps(clean_value(report), ensure_ascii=False, separators=(",", ":")))
            f.write("\n")


def _stddev(values):
    n = len(values)
    if n < 2:
        return 0.0 if n == 1 else None
    mean = sum(values) / n
    var = sum((v - mean) ** 2 for v in values) / n
    return var ** 0.5


def _final_score(report):
    """final_score for one report: computed fresh via compute_final_score (the single
    seam), regardless of whether write_rep_reports has already stamped the field.
    None when overall_score is None (mirrors compute_final_score's only caller rule).
    """
    overall = report.get("overall_score")
    return compute_final_score(overall, report.get("ai_score")) if overall is not None else None


def build_drill_summary(reports, stroke_type):
    scored = [r for r in reports if r.get("overall_score") is not None]
    scores = [r["overall_score"] for r in scored]
    final_scores = [_final_score(r) for r in scored]

    mean_score = round(sum(scores) / len(scores), 1) if scores else None
    mean_final_score = round(sum(final_scores) / len(final_scores), 1) if final_scores else None
    consistency = round(_stddev(scores), 2) if scores else None

    best_rep = worst_rep = None
    if scored:
        best = max(scored, key=_final_score)
        worst = min(scored, key=_final_score)
        best_rep = {"rep_id": best["rep_id"], "score": _final_score(best)}
        worst_rep = {"rep_id": worst["rep_id"], "score": _final_score(worst)}

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

    summary = {
        "stroke_type": stroke_type,
        "rep_count": len(reports),
        "mean_score": mean_score,
        "mean_final_score": mean_final_score,
        "best_rep": best_rep,
        "worst_rep": worst_rep,
        "consistency": consistency,
        "per_metric_avg": per_metric_avg,
        "recurring_weaknesses": _ranked(weakness_counter),
        "strengths": _ranked(strength_counter),
    }
    ai_scores = [r["ai_score"] for r in reports if r.get("ai_score") is not None]
    if ai_scores:
        summary["mean_ai_score"] = round(sum(ai_scores) / len(ai_scores), 1)
    return summary
