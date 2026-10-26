"""Per-rally evidence for the automatic labeller (spec 5.3).

E1 ``last_hitter``: the side of the last learned hit in the rally.
E2 landing: the cleaned shuttle track after the last hit, its first still run
(the shuttle on the floor) projected to court metres, judged in / out / net.
E3 ``next_server``: the side of the next rally's first hit after a pause.

Pure functions on already-loaded data; nothing here raises for bad input.
"""
import math

NET_Y = 6.7
COURT = (6.1, 13.4)
LINE_MARGIN_M = 0.3
RALLY_PAD_SEC = 0.5
LANDING_AFTER_END_SEC = 2.0
STATIC_RADIUS_FRAC = 0.015   # of the frame height: a point this close to a static artefact is dropped
JUMP_FRAC = 0.08             # a point this far from the previous kept one is dropped
STILL_RADIUS_FRAC = 0.006    # a landed shuttle stays within this of the run's first point
STILL_MIN_SEC = 0.25
NEXT_SERVE_PAUSE_SEC = 3.0


def other(side):
    return {"upper": "lower", "lower": "upper"}.get(side)


def side_of_y(y):
    return "upper" if y < NET_Y else "lower"


def _in_window(t, start_sec, end_sec):
    return start_sec - RALLY_PAD_SEC <= t <= end_sec + RALLY_PAD_SEC


def rally_hits(hits, start_sec, end_sec):
    """The ``(time_sec, frame, hitter)`` hits inside the padded rally window, in time order."""
    return sorted((h for h in hits or [] if _in_window(h[0], start_sec, end_sec)), key=lambda h: h[0])


def last_hitter(hits, start_sec, end_sec):
    inside = rally_hits(hits, start_sec, end_sec)
    return inside[-1][2] if inside else None


def next_server(hits, this_last_hit_sec, next_start_sec, next_end_sec, min_pause=NEXT_SERVE_PAUSE_SEC):
    """The side of the next rally's first hit when a real pause precedes it, else None."""
    inside = rally_hits(hits, next_start_sec, next_end_sec)
    if not inside or this_last_hit_sec is None:
        return None
    first = inside[0]
    return first[2] if first[0] - this_last_hit_sec >= min_pause else None


def clean_track(trajectory, frames, frame_h, static_points=()):
    """``[(frame, x, y)]`` of the usable image points over ``frames``.

    Drops missing points, points near a known static artefact, and points that
    jump implausibly far from the previous kept point.
    """
    static_r = STATIC_RADIUS_FRAC * frame_h
    jump = JUMP_FRAC * frame_h
    kept = []
    for f in frames:
        p = trajectory.get(f) if isinstance(trajectory, dict) else None
        if not isinstance(p, (list, tuple)) or len(p) != 2:
            continue
        x, y = p
        if not all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
                   for v in (x, y)):
            continue
        if any(math.hypot(x - sx, y - sy) <= static_r for sx, sy in static_points):
            continue
        if kept and math.hypot(x - kept[-1][1], y - kept[-1][2]) > jump:
            continue
        kept.append((f, float(x), float(y)))
    return kept


def find_landing(points, fps, frame_h):
    """Mean image point of the first still run of >= 0.25 s, or None."""
    if not points or not fps or fps <= 0:
        return None
    radius = STILL_RADIUS_FRAC * frame_h
    need = STILL_MIN_SEC * fps
    i = 0
    while i < len(points):
        f0, x0, y0 = points[i]
        j = i
        while j + 1 < len(points) and math.hypot(points[j + 1][1] - x0, points[j + 1][2] - y0) <= radius:
            j += 1
        if points[j][0] - f0 >= need:
            run = points[i:j + 1]
            return (sum(p[1] for p in run) / len(run), sum(p[2] for p in run) / len(run))
        i += 1
    return None


def court_homography(corners):
    """Image -> court metres from corners TL, TR, BR, BL; None when unusable."""
    try:
        import cv2
        import numpy as np
        src = np.array(corners, dtype=np.float32).reshape(4, 2)
        dst = np.array([[0, 0], [COURT[0], 0], [COURT[0], COURT[1]], [0, COURT[1]]], dtype=np.float32)
        h = cv2.getPerspectiveTransform(src, dst)
    except Exception:   # malformed corners, degenerate quad (cv2.error)
        return None
    if not np.all(np.isfinite(h)) or abs(float(np.linalg.det(h))) < 1e-12:
        return None   # a degenerate quad gives a singular matrix, not an error
    return h


def to_court(h, xy):
    x, y = xy
    w = h[2][0] * x + h[2][1] * y + h[2][2]
    if not w:
        return None
    cx = (h[0][0] * x + h[0][1] * y + h[0][2]) / w
    cy = (h[1][0] * x + h[1][1] * y + h[1][2]) / w
    return (float(cx), float(cy)) if math.isfinite(cx) and math.isfinite(cy) else None


def landing_verdict(court_xy, hitter):
    """``{"winner", "ending", "point", "reason"}`` for a landed shuttle at ``court_xy``.

    Inside the court on side X: X lost (``net`` when X also hit last, ``in`` when
    the other side did, unknown ending without E1). Outside beyond side X's half:
    the other side hit it out, so X won. Within 0.3 m of a boundary or the net
    line: no verdict.
    """
    x, y = court_xy
    point = [round(x, 2), round(y, 2)]
    near = (abs(x) < LINE_MARGIN_M or abs(x - COURT[0]) < LINE_MARGIN_M
            or abs(y) < LINE_MARGIN_M or abs(y - COURT[1]) < LINE_MARGIN_M
            or abs(y - NET_Y) < LINE_MARGIN_M)
    if near:
        return {"winner": None, "ending": None, "point": point, "reason": "near_line"}
    side = side_of_y(y)
    inside = 0 <= x <= COURT[0] and 0 <= y <= COURT[1]
    if inside:
        ending = None if hitter is None else ("net" if hitter == side else "in")
        return {"winner": other(side), "ending": ending, "point": point, "reason": "inside"}
    return {"winner": side, "ending": "out", "point": point, "reason": "outside"}
