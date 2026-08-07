"""Write per-stroke reports (JSONL) and the match-level summary (JSON)."""
import json
import os
from collections import Counter, defaultdict

from ..data.writer import clean_value as _clean_value


def write_stroke_reports(path, reports):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for report in reports:
            f.write(json.dumps(_clean_value(report), ensure_ascii=False, separators=(",", ":")))
            f.write("\n")


def build_match_summary(reports):
    by_type_scores = defaultdict(list)
    by_type_count = Counter()
    weakness_counter = Counter()
    strength_counter = Counter()

    for r in reports:
        st = r["stroke_type"]
        by_type_count[st] += 1
        if r.get("overall_score") is not None:
            by_type_scores[st].append(r["overall_score"])
        for w in r.get("weaknesses", []):
            weakness_counter[w["metric"]] += 1
        for s in r.get("strengths", []):
            strength_counter[s] += 1

    by_type = {}
    for st, count in by_type_count.items():
        scores = by_type_scores.get(st, [])
        avg = round(sum(scores) / len(scores), 1) if scores else None
        by_type[st] = {"count": count, "avg_score": avg}

    def _ranked(counter):
        return [{"metric": m, "count": c} for m, c in counter.most_common()]

    return {
        "stroke_count": len(reports),
        "by_type": by_type,
        "recurring_weaknesses": _ranked(weakness_counter),
        "strengths": _ranked(strength_counter),
    }
