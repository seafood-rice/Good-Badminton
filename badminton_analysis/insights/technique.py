"""Technique scores per side from the posture reports in ``strokes.jsonl``.

A report without a ``scored`` key predates the one stroke classifier (spec 2026-10-22
section 4.6): its label came from the old rule classifier, so it is never counted. When
every usable report is such a one, the insight is unavailable with ``stale_technique``.
"""
import statistics

from . import envelope
from ..stroke.stroke_types import PROFILE_GROUP, group_of
from .loaders import SIDES, number

TECHNIQUES = ["smash", "clear", "drop", "net", "lift", "drive", "serve"]
REFERENCE = {"lo": 70, "hi": 100, "source": "literature"}
"""A technique's mean score is "inside the reference range" at ``>= lo``."""


def _side(reports):
    """``Side`` for one side's ``[(technique, score|None)]``."""
    techniques = {}
    for tech in TECHNIQUES:
        scores = [s for t, s in reports if t == tech]
        valid = [s for s in scores if s is not None]
        techniques[tech] = {
            "count": len(scores),
            "avg_score": round(statistics.fmean(valid), 1) if valid else None}
    # the index is the mean of the avg_scores as reported (already rounded)
    means = [v["avg_score"] for v in techniques.values() if v["avg_score"] is not None]
    return {"techniques": techniques,
            "index": round(statistics.fmean(means), 1) if means else None,
            "scored": len(means)}


def technique_insight(run):
    by_side = {s: [] for s in SIDES}
    usable = stale = 0
    for r in run.tech_reports or []:
        stroke_type, side = r.get("stroke_type"), r.get("player_side")
        # JSON values may be lists/dicts (unhashable): only strings reach a lookup
        if not isinstance(stroke_type, str) or not isinstance(side, str):
            continue
        usable += 1
        if "scored" not in r:
            stale += 1
            continue
        if r.get("scored") is False:
            continue
        tech = r.get("group") if isinstance(r.get("group"), str) else (
            group_of(stroke_type) or PROFILE_GROUP.get(stroke_type))
        if tech is None or side not in by_side:
            continue
        by_side[side].append((tech, number(r.get("overall_score"))))
    if not any(by_side.values()):
        return envelope.unavailable("stale_technique" if usable and stale == usable else "no_technique")

    sides = {s: _side(reports) if reports else None for s, reports in by_side.items()}
    scored = {t for side in sides.values() if side
              for t, v in side["techniques"].items() if v["avg_score"] is not None}
    value = {"by_side": sides}
    coverage = {"n": len(scored), "of": len(TECHNIQUES)}
    unscored = [t for t in TECHNIQUES if t not in scored]
    if unscored:
        return envelope.partial(value, "unscored_technique", {"names": unscored}, coverage)
    return envelope.ok(value, coverage)
