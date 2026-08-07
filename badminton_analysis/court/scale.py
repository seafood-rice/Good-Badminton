"""Perspective scale (pixels per metre) as a function of image row.

For a planar court viewed by a pinhole camera, lateral scale is linear in image
y: ``px_per_m(y) = a * (y - y0)``, where ``y0`` is the horizon row. Two anchors
-- the far and near baselines of the court quad, whose real-world separation is
the court width -- determine ``a`` and ``y0``.

This replaces absolute-pixel constants, which are wrong across a perspective
gradient: on the DJI footage the scale runs from 106.3 px/m at the far baseline
to 603.8 px/m at the near one, a 5.7x span. A fixed 80 px contact radius is
0.13 m at the near baseline but 0.75 m at the far one.
"""
import math

COURT_WIDTH_M = 6.10
"""Outer (doubles) sideline separation, in metres.

Named so the assumption that the quad traces the OUTER court boundary can be
corrected in one place if a caller's quad follows the singles sidelines instead.
"""

_MIN_SCALE = 1e-3           # px/m floor, so callers never divide by zero


class PerspectiveScale:
    """Linear-in-y pixels-per-metre model for one calibrated court view."""

    def __init__(self, a, y0):
        self.a = float(a)
        self.y0 = float(y0)

    @classmethod
    def from_quad(cls, quad, court_width_m=COURT_WIDTH_M):
        """Build from a 4-corner court quad ordered TL, TR, BR, BL.

        Corners 0-1 span the far baseline, corners 3-2 the near baseline -- the
        ordering ``badminton_analysis.court.mapper`` already uses.
        """
        if quad is None or len(quad) != 4:
            raise ValueError("court quad must have exactly 4 corners (TL, TR, BR, BL)")
        if court_width_m <= 0:
            raise ValueError("court_width_m must be positive")
        (tlx, tly), (trx, try_), (brx, bry), (blx, bly) = (
            (float(p[0]), float(p[1])) for p in quad)

        far_px = math.hypot(trx - tlx, try_ - tly)
        near_px = math.hypot(brx - blx, bry - bly)
        s_far = far_px / court_width_m
        s_near = near_px / court_width_m
        y_far = (tly + try_) / 2.0
        y_near = (bly + bry) / 2.0

        if s_far <= 0 or s_near <= 0:
            raise ValueError("degenerate quad: a baseline has zero length")
        if abs(s_near - s_far) < 1e-9 or abs(y_near - y_far) < 1e-9:
            raise ValueError("degenerate quad: baselines have no depth separation")

        y0 = (s_far * y_near - s_near * y_far) / (s_far - s_near)
        a = s_near / (y_near - y0)
        if a <= 0:
            raise ValueError("degenerate quad: non-physical scale gradient")
        return cls(a, y0)

    def px_per_m(self, y):
        """Pixels per metre at image row ``y``, floored to stay positive.

        Rows at or above the horizon have no physical scale; they are clamped
        rather than returning zero or a negative value, so a stray coordinate
        cannot silently invert a distance comparison.
        """
        return max(self.a * (float(y) - self.y0), _MIN_SCALE)
