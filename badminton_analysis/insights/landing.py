"""Where a shot lands, in court metres (landing spec 2026-11-02 section 3).

A mid-rally shot "lands" where the receiver hits it: depth from the receiver's feet moved
``REACH_M`` towards the net (contact is in front of the body), width from the shuttle's image
column at that depth. The shuttle itself is mid-air at contact, so it is never projected straight
through the ground homography. Pure functions; nothing here raises for bad input.
"""
import math

from .movement import NET_Y

COURT_W = 6.1
COURT_L = 13.4
FRONT_M = 1.98          # depth from the net: front <= FRONT_M (the short service line)
REAR_M = 4.7            # rear > REAR_M, mid in between
REACH_M = 0.6           # measured 0.62 m (mean of the near 0.28 m and far 1.02 m feet-to-landing gap)
WIDTH_SLACK_M = 1.0     # a shuttle column further than this outside the court is not trusted


def side_of_y(y):
    return "upper" if y < NET_Y else "lower"


def depth_of(y):
    """``front`` / ``mid`` / ``rear`` of a court ``y`` by its distance from the net."""
    d = abs(y - NET_Y)
    return "front" if d <= FRONT_M else ("rear" if d > REAR_M else "mid")


def in_court(x, y):
    return 0.0 <= x <= COURT_W and 0.0 <= y <= COURT_L


def _point(p, n=2):
    """``p`` as a tuple of ``n`` finite floats, or None."""
    if not isinstance(p, (list, tuple)) or len(p) != n or not _finite(*p):
        return None
    return tuple(float(v) for v in p)


def _finite(*values):
    return all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
               for v in values)


def _to_image(h_inv, x, y):
    w = h_inv[2][0] * x + h_inv[2][1] * y + h_inv[2][2]
    if not w:
        return None
    return ((h_inv[0][0] * x + h_inv[0][1] * y + h_inv[0][2]) / w,
            (h_inv[1][0] * x + h_inv[1][1] * y + h_inv[1][2]) / w)


def inverse(h):
    """The court -> image matrix of an image -> court homography, or None."""
    try:
        import numpy as np
        inv = np.linalg.inv(np.asarray(h, dtype=float))
    except Exception:   # singular or malformed
        return None
    return inv.tolist() if np.all(np.isfinite(inv)) else None


def column_x(h_inv, court_y, shuttle_xy):
    """Court x at depth ``court_y`` whose image x equals the shuttle's, or None.

    The court line ``y = court_y`` is a straight image line; the point on it with the shuttle's image x
    is linear in court x only up to perspective, so it is solved on the line's image end points and
    refined once. None when the line is (near) vertical in the image or the answer falls more than
    ``WIDTH_SLACK_M`` outside the court."""
    shuttle = _point(shuttle_xy)
    if h_inv is None or shuttle is None or not _finite(court_y):
        return None
    sx = shuttle[0]
    lo, hi = 0.0, COURT_W
    for _ in range(2):
        a, b = _to_image(h_inv, lo, court_y), _to_image(h_inv, hi, court_y)
        if a is None or b is None or abs(b[0] - a[0]) < 1e-6:
            return None
        x = lo + (sx - a[0]) / (b[0] - a[0]) * (hi - lo)
        lo, hi = x - 0.5, x + 0.5   # perspective is near linear over a metre
    if not math.isfinite(x) or x < -WIDTH_SLACK_M or x > COURT_W + WIDTH_SLACK_M:
        return None
    return x


def reach(side, y):
    """``y`` moved ``REACH_M`` towards the net on ``side``'s half, never across it."""
    if side == "upper":
        return min(y + REACH_M, NET_Y)
    return max(y - REACH_M, NET_Y)


def estimate(receiver_side, foot, shuttle_xy=None, h_inv=None):
    """``{"x", "y", "side", "depth", "source", "out"}`` for a mid-rally shot, or None.

    ``foot`` is the receiver's court position at their hit; ``shuttle_xy`` the shuttle's image point
    then; ``h_inv`` the court -> image homography. A returned shot did not land out, so the point is
    kept inside the court on the receiver's half."""
    foot = _point(foot)
    if receiver_side not in ("upper", "lower") or foot is None:
        return None
    fx, fy = foot
    x = column_x(h_inv, fy, shuttle_xy) if shuttle_xy is not None else None
    source = "shuttle_feet" if x is not None else "feet"
    x = min(COURT_W, max(0.0, fx if x is None else x))
    y = reach(receiver_side, fy)
    y = min(y, NET_Y) if receiver_side == "upper" else max(y, NET_Y)   # a foot on the wrong half
    y = min(COURT_L, max(0.0, y))
    return make(x, y, source, side=receiver_side)


def make(x, y, source, side=None):
    """A landing dict for court point ``(x, y)``; ``side`` overrides the half read from ``y`` (a point
    on the net line belongs to the receiver)."""
    out = not in_court(x, y)
    return {"x": round(x, 2), "y": round(y, 2), "side": side or side_of_y(y),
            "depth": None if out else depth_of(y), "source": source, "out": out}

