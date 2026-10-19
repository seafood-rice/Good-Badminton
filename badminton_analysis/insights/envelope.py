"""The insight envelope (spec 5.1) as plain dicts, ready to serialise.

``{"status", "value", "coverage", "reason"}``: ``ok`` has a value and no reason,
``partial`` has a value *and* the reason that limits it, ``unavailable`` has
neither value nor coverage, only a reason.
"""


def _reason(code, params):
    return {"code": code, "params": dict(params or {})}


def ok(value, coverage=None):
    return {"status": "ok", "value": value, "coverage": coverage, "reason": None}


def partial(value, code, params=None, coverage=None):
    return {"status": "partial", "value": value, "coverage": coverage,
            "reason": _reason(code, params)}


def unavailable(code, params=None):
    return {"status": "unavailable", "value": None, "coverage": None,
            "reason": _reason(code, params)}
