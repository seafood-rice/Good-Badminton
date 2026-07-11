"""Assemble the structured coach report (per language) from drill analysis."""
from . import coach_kb as kb
from ..training.plan_generator import generate_plan

# _SEVERITY_RANK ranks the rep-level `severity` values from scoring (severe/moderate/minor),
# which is distinct from the KB's informational `severity_hint` (high/moderate/low).
_SEVERITY_RANK = {"severe": 3, "moderate": 2, "minor": 1, None: 0}


def _verdict_key(mean_score, consistency, rep_count):
    if not rep_count or mean_score is None:
        return "verdict_insufficient"
    if mean_score >= 80:
        base = "verdict_strong"
    elif mean_score >= 60:
        base = "verdict_developing"
    else:
        base = "verdict_needs_work"
    return base


def _find_rep_finding(reports, metric):
    """Return the highest-severity rep-level weakness dict for a metric, or None."""
    best = None
    for rep in reports:
        for w in rep.get("weaknesses", []):
            if w.get("metric") == metric:
                if best is None or _SEVERITY_RANK.get(w.get("severity")) > _SEVERITY_RANK.get(best.get("severity")):
                    best = w
    return best


def _one_language(lang, reports, summary, meta):
    stroke = summary.get("stroke_type", meta.get("stroke_type"))
    rep_count = summary.get("rep_count", 0)
    mean_score = summary.get("mean_score")
    consistency = summary.get("consistency")

    header = {
        "stroke": stroke,
        "stroke_label": kb.t(lang, "stroke_" + stroke),
        "rep_count": rep_count,
        "dominant_hand": meta.get("dominant_hand", "right"),
        "date": meta.get("date"),
        "pose_family": meta.get("pose_family"),
    }

    verdict_key = _verdict_key(mean_score, consistency, rep_count)
    summary_block = {
        "mean_score": mean_score,
        "mean_final_score": summary.get("mean_final_score", mean_score),
        "consistency": consistency,
        "verdict_text": kb.t(lang, verdict_key,
                             score=("%.0f" % mean_score) if mean_score is not None else "N/A",
                             consistency=("%.1f" % consistency) if consistency is not None else "N/A"),
    }

    weaknesses = []
    for rw in summary.get("recurring_weaknesses", []):
        metric = rw["metric"]
        finding = _find_rep_finding(reports, metric)
        direction = (finding or {}).get("direction", "under")
        entry = kb.lookup_weakness(metric, stroke, direction)
        measured = (finding or {}).get("measured")
        ideal = (finding or {}).get("ideal_range")
        weaknesses.append({
            "metric": metric,
            "metric_label": kb.t(lang, "metric_" + metric),
            "measured": measured,
            "ideal_range": ideal,
            "direction": direction,
            "severity": (finding or {}).get("severity"),
            "impact_label": kb.t(lang, "impact_" + entry["impact"]),
            "mechanism_text": kb.t(lang, entry["mechanism_key"]),
            "drill_text": kb.t(lang, entry["drill_key"]),
        })

    strengths = []
    for st in summary.get("strengths", []):
        metric = st["metric"]
        entry = kb.lookup_strength(metric, stroke)
        strengths.append({
            "metric": metric,
            "metric_label": kb.t(lang, "metric_" + metric),
            "measured": (summary.get("per_metric_avg") or {}).get(metric),
            "impact_label": kb.t(lang, "impact_" + entry["impact"]),
            "text": kb.t(lang, entry["text_key"]),
        })

    per_rep = []
    for rep in reports:
        top = None
        weak = rep.get("weaknesses", [])
        if weak:
            top = max(weak, key=lambda w: _SEVERITY_RANK.get(w.get("severity"))).get("metric")
        overall = rep.get("overall_score")
        final = rep.get("final_score")
        per_rep.append({
            "rep_id": rep.get("rep_id"),
            "overall_score": overall,
            "final_score": final if final is not None else overall,
            "top_weakness": top,
        })

    training_plan = generate_plan(summary)

    section_labels = {
        "strengths":      kb.t(lang, "section_strengths"),
        "weaknesses":     kb.t(lang, "section_weaknesses"),
        "per_rep":        kb.t(lang, "section_per_rep"),
        "training_plan":  kb.t(lang, "section_training_plan"),
        "col_rep":        kb.t(lang, "col_rep"),
        "col_score":      kb.t(lang, "col_score"),
        "col_top_weakness": kb.t(lang, "col_top_weakness"),
    }

    return {
        "lang": lang,
        "header": header,
        "summary": summary_block,
        "strengths": strengths,
        "weaknesses": weaknesses,
        "per_rep": per_rep,
        "training_plan": training_plan,
        "section_labels": section_labels,
    }


def build_coach_report(reports, summary, meta):
    return {lang: _one_language(lang, reports, summary, meta) for lang in kb.SUPPORTED_LANGS}
