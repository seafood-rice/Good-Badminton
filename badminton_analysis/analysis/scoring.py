"""Score measured stroke metrics against reference ranges."""
from .reference_ranges import REFERENCE_RANGES


def score_angle(measured, spec):
    """100 inside [min,max]; linear decay to 0 at one range-width outside. None->None."""
    if measured is None:
        return None
    lo, hi = spec["min"], spec["max"]
    if lo <= measured <= hi:
        return 100.0
    span = max(hi - lo, 1e-6)
    dev = (lo - measured) / span if measured < lo else (measured - hi) / span
    return float(max(0.0, 100.0 * (1.0 - dev)))


def deviation_direction(measured, spec):
    if measured < spec["min"]:
        return "under"
    if measured > spec["max"]:
        return "over"
    return "in_range"


def classify_severity(score):
    if score is None:
        return None
    if score >= 75:
        return "minor"
    if score >= 50:
        return "moderate"
    return "severe"


def score_stroke(metrics, stroke_type, ranges=REFERENCE_RANGES):
    """Aggregate per-metric scores into an overall weighted score plus findings."""
    specs = ranges[stroke_type]
    per_metric = {}
    weighted_sum = 0.0
    weight_total = 0.0
    weaknesses = []
    strengths = []

    for name, spec in specs.items():
        measured = metrics.get(name)
        s = score_angle(measured, spec)
        per_metric[name] = {
            "measured": measured,
            "score": s,
            "ideal_range": [spec["min"], spec["max"]],
            "weight": spec["weight"],
            "direction": None if measured is None else deviation_direction(measured, spec),
            "severity": classify_severity(s),
        }
        if s is not None:
            weighted_sum += s * spec["weight"]
            weight_total += spec["weight"]
            if s >= 90:
                strengths.append(name)
            elif s < 75:
                weaknesses.append(name)

    overall = round(weighted_sum / weight_total, 1) if weight_total > 0 else None
    return {
        "overall": overall,
        "per_metric": per_metric,
        "weaknesses": weaknesses,
        "strengths": strengths,
    }
