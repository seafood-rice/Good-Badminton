"""Stroke-classification coverage for one run (spec 5.3 "Strokes")."""
from . import envelope
from .loaders import SIDES

OK_COVERAGE = 0.6
"""``classified / total`` at or above this is ``ok``; below it, ``low_stroke_coverage``."""


def _is_uncertain(stroke):
    return bool(stroke.get("uncertain")) or stroke.get("stroke") == "uncertain"


def strokes_insight(run):
    strokes = run.strokes
    if not strokes:
        return envelope.unavailable("no_strokes_file")

    total = len(strokes)
    distribution = {}
    by_hitter = {s: {"classified": 0, "total": 0} for s in SIDES}
    classified = 0
    for s in strokes:
        sure = not _is_uncertain(s)
        hitter = s.get("hitter")
        if hitter in by_hitter:
            by_hitter[hitter]["total"] += 1
            by_hitter[hitter]["classified"] += sure
        if sure:
            classified += 1
            name = s.get("stroke")
            if isinstance(name, str) and name:
                distribution[name] = distribution.get(name, 0) + 1

    value = {"total": total, "classified": classified, "uncertain": total - classified,
             "distribution": distribution, "by_hitter": by_hitter}
    coverage = {"n": classified, "of": total}
    # integer comparison: classified / total >= 0.6 without float rounding
    if classified * 10 >= total * 6:
        return envelope.ok(value, coverage)
    return envelope.partial(value, "low_stroke_coverage",
                            {"classified": classified, "total": total}, coverage)
