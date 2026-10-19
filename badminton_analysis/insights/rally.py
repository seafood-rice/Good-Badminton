"""Rally insight, built on the one reading of ``rally_segments.json``.

``summarise_rally_payload`` holds the honesty rules (what the file may claim);
``app._rally_summary`` and ``rally_insight`` both go through it.
"""
import statistics

from . import envelope
from .loaders import number

SHOT_PAD_SEC = 0.5
"""A hit belongs to a rally when it falls in ``[start - 0.5, end + 0.5]``."""


def summarise_rally_payload(payload):
    """What a parsed ``rally_segments.json`` may honestly claim.

    ``payload`` is the parsed JSON, or ``None`` when the file does not exist.
    Anything that is not a dict (a list, a string, a ``null`` document) or has
    a ``rallies`` that is not a list is a malformed file. Returns
    ``{"count", "suppressed", "label", "signal", "degraded", "failed"}``:

    * ``shuttle`` / ``courtview`` -> the count is shown, label "rallies";
    * ``swing`` -> suppressed, label "segments" (it inflates rally COUNT by
      +36% and total rally time by +41%, design spec section 0.17);
    * degraded or ``none`` -> suppressed, label "windows" (coarse windows are
      not rallies, section 12a-C);
    * ``error``, a missing or malformed file -> count 0, not suppressed,
      ``signal`` None (unknown); ``failed`` marks the ``error`` and malformed
      cases;
    * an older file with no ``detection`` block -> count shown, "rallies".

    "suppressed" means the count must not be presented (or summed) as a rally
    count; ``count`` itself is still the number of segments in the file.
    """
    unknown = {"count": 0, "suppressed": False, "label": "rallies",
               "signal": None, "degraded": False, "failed": False}
    if payload is None:
        return unknown
    if not isinstance(payload, dict):
        return dict(unknown, failed=True)
    rallies = payload.get("rallies", [])
    if not isinstance(rallies, list):
        return dict(unknown, failed=True)
    detection = payload.get("detection")
    if not isinstance(detection, dict):
        detection = {}

    signal = detection.get("signal")
    if signal == "error":
        return dict(unknown, failed=True)
    degraded = bool(detection.get("degraded"))
    summary = {"count": len(rallies), "suppressed": False, "label": "rallies",
               "signal": signal, "degraded": degraded, "failed": False}
    if degraded or signal == "none":
        summary.update(suppressed=True, label="windows")
    elif signal == "swing":
        summary.update(suppressed=True, label="segments")
    return summary


def rally_windows(payload, fps=None):
    """``[(start_sec, end_sec)]`` for the usable entries of a rallies payload."""
    windows = []
    rallies = payload.get("rallies") if isinstance(payload, dict) else None
    for r in rallies if isinstance(rallies, list) else []:
        if not isinstance(r, dict):
            continue
        start, end = number(r.get("start_sec")), number(r.get("end_sec"))
        if (start is None or end is None) and fps:
            sf, ef = number(r.get("start_frame")), number(r.get("end_frame"))
            if sf is not None and ef is not None:
                start, end = sf / fps, ef / fps
        if start is not None and end is not None and end >= start:
            windows.append((start, end))
    return windows


def _hit_times(run):
    """Hit times in seconds from hits.json only (the learned detector's hits).

    strokes.json is deliberately not a fallback: its entries are sparse stroke
    contacts, not a count of shots, and would understate shots per rally.
    """
    return [t for t, _frame, _hitter in run.hits] if run.hits else []


def _round(x):
    return round(x, 2)


def rally_insight(run):
    summary = summarise_rally_payload(run.rallies_payload)
    if run.rallies_payload is None or summary["failed"] or summary["count"] == 0:
        return envelope.unavailable("no_rallies")

    windows = rally_windows(run.rallies_payload, run.fps)
    durations = [_round(end - start) for start, end in windows]
    times = _hit_times(run)
    shots = None
    if times:
        shots = [sum(1 for t in times if start - SHOT_PAD_SEC <= t <= end + SHOT_PAD_SEC)
                 for start, end in windows]
    value = {
        "count": summary["count"],
        "label": summary["label"],
        "signal": summary["signal"],
        "suppressed": summary["suppressed"],
        "durations_sec": durations,
        "duration_missing": summary["count"] - len(windows),
        "mean_sec": _round(statistics.fmean(durations)) if durations else None,
        "median_sec": _round(statistics.median(durations)) if durations else None,
        "shots_per_rally": shots,
        "mean_shots": _round(statistics.fmean(shots)) if shots else None,
    }
    if summary["degraded"] or summary["signal"] == "none":
        return envelope.partial(value, "signal_degraded")
    if summary["signal"] == "swing":
        return envelope.partial(value, "signal_swing")
    return envelope.ok(value)
