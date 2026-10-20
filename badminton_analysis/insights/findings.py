"""Deterministic findings from an overview (spec 5.4).

``build_findings`` reads only the overview dict (tolerating missing parts) and
returns at most six ``{"code", "severity", "params"}``, ordered ``watch``,
``good``, ``info``; within a severity, in the order the checks run.

Doubles (addendum 4.2): ``doubles_identity_degraded{status}`` (watch) names the first
``degraded``/``degraded_teams`` run of the ``doubles`` insight (a failed or missing run is
already the insight's own reason); ``doubles_hits_unvalidated{named_share}`` (info) only
appears with a selected athlete's doubles summary that has a named share.
"""
from .loaders import number
from .technique import REFERENCE

MAX_FINDINGS = 6
MAX_BELOW_REFERENCE = 3
"""Keeps one weak profile from crowding every other finding out of the six."""
_SEVERITY_ORDER = {"watch": 0, "good": 1, "info": 2}
DEGRADED_IDENTITY = ("degraded", "degraded_teams")
"""Doubles run statuses whose identities exist but are only partly reliable."""


def _dict(x):
    return x if isinstance(x, dict) else {}


def _finding(code, severity, **params):
    return {"code": code, "severity": severity, "params": params}


def _kpi_value(kpis, key):
    """``(status, value)`` of a KPI whose value is usable, else ``(status, None)``."""
    kpi = _dict(kpis.get(key))
    value = kpi.get("value")
    if kpi.get("status") in ("ok", "partial") and isinstance(value, dict):
        return kpi["status"], value
    return kpi.get("status"), None


def build_findings(overview):
    ov = _dict(overview)
    kpis = _dict(ov.get("kpis"))
    radar = _dict(ov.get("radar"))
    lo = _dict(radar.get("reference")).get("lo")
    if isinstance(lo, bool) or not isinstance(lo, (int, float)):
        lo = REFERENCE["lo"]

    axes = [a for a in radar.get("axes") or [] if isinstance(a, dict)]
    scored = [(a.get("id"), number(a.get("score"))) for a in axes
              if number(a.get("score")) is not None]
    unscored = [a.get("id") for a in axes if number(a.get("score")) is None]

    found = []
    below = sorted((pair for pair in scored if pair[1] < lo), key=lambda p: p[1])
    for technique, score in below[:MAX_BELOW_REFERENCE]:
        found.append(_finding("technique_below_reference", "watch",
                              technique=technique, score=score, lo=lo))

    if scored:
        best = max(scored, key=lambda p: p[1])         # first axis wins a tie
        found.append(_finding("technique_strongest", "good" if best[1] >= lo else "info",
                              technique=best[0], score=best[1]))

    _status, index = _kpi_value(kpis, "technique_index")
    delta = number(_dict(index).get("delta"))
    if delta is not None:
        severity = "watch" if delta < 0 else "good" if delta > 0 else "info"
        found.append(_finding("index_trend", severity, delta=delta,
                              range=_dict(ov.get("scope")).get("range")))

    _status, recovery = _kpi_value(kpis, "recovery")
    sec = number(_dict(recovery).get("median_sec"))
    if sec is not None:
        found.append(_finding("recovery_median", "info", sec=sec))

    if scored and unscored:
        found.append(_finding("techniques_unscored", "info", names=unscored))

    status, strokes = _kpi_value(kpis, "strokes")
    if status == "partial" and strokes is not None:
        found.append(_finding("strokes_coverage_low", "watch",
                              classified=strokes.get("classified"),
                              total=strokes.get("total")))

    status, rally = _kpi_value(kpis, "avg_rally")
    if status == "partial" and rally is not None:
        found.append(_finding("rally_estimated", "info", signal=rally.get("signal")))

    doubles = _kpi_value(ov, "doubles")[1]
    if doubles is not None:
        runs = doubles.get("runs")
        degraded = next((r.get("status") for r in runs if isinstance(r, dict)
                         and r.get("status") in DEGRADED_IDENTITY), None) \
            if isinstance(runs, list) else None
        if degraded is not None:
            found.append(_finding("doubles_identity_degraded", "watch", status=degraded))
        summary = doubles.get("athlete")
        share = number(summary.get("named_share")) if isinstance(summary, dict) else None
        if share is not None:
            found.append(_finding("doubles_hits_unvalidated", "info", named_share=share))

    found.sort(key=lambda f: _SEVERITY_ORDER[f["severity"]])      # stable
    return found[:MAX_FINDINGS]
