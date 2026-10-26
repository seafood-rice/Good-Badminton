"""The rule cascade of the automatic labeller (spec 5.3).

A winner is claimed only when the landing (E2) names one and another signal
agrees with no signal disagreeing. The confidences are fixed placeholders until
they are measured against owner labels (``summary.confidence_calibrated``).
"""
from .evidence import other

CONF_LANDING_AND_SERVER = 0.8
CONF_OUT_AND_HITTER = 0.6


def _result(winner, confidence, rule):
    return {"winner_side": winner, "confidence": confidence, "rule": rule}


def decide(landing, last_hitter, next_server):
    """``{"winner_side", "confidence", "rule"}`` from E2 (a ``landing_verdict`` or None), E1, E3."""
    winner = (landing or {}).get("winner")
    if winner is None:
        return _result(None, None, "no_landing")
    out = landing.get("ending") == "out"
    if out and last_hitter == winner:
        return _result(None, None, "out_hitter_conflict")
    if next_server is not None and next_server != winner:
        return _result(None, None, "server_conflict")
    if next_server == winner:
        return _result(winner, CONF_LANDING_AND_SERVER, "landing_and_server")
    if out and last_hitter == other(winner):
        return _result(winner, CONF_OUT_AND_HITTER, "out_and_hitter")
    return _result(None, None, "single_signal")
