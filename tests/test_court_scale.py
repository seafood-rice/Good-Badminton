"""Perspective scale (pixels per metre) derived from the court quad.

A fixed pixel constant is wrong across a perspective gradient: on the DJI footage
the lateral scale runs from 106.3 px/m at the far baseline to 603.8 px/m at the
near one, a 5.7x span. For a planar court under a pinhole camera the scale is
linear in image row, so two anchors from the quad determine the whole model.
"""
import pytest

from badminton_analysis.court.scale import COURT_WIDTH_M, PerspectiveScale

# A quad constructed to reproduce the two scales measured on the DJI footage
# (design doc sections 0.13/0.23): 106.3 px/m at the far baseline y=1122 and
# 603.8 px/m at the near baseline y=2095, with a 6.10 m outer court width.
FAR_Y, NEAR_Y = 1122.0, 2095.0
FAR_PX_PER_M, NEAR_PX_PER_M = 106.3, 603.8
_CX = 1920.0
_FAR_HALF = FAR_PX_PER_M * COURT_WIDTH_M / 2.0     # 324.215
_NEAR_HALF = NEAR_PX_PER_M * COURT_WIDTH_M / 2.0   # 1841.59
QUAD = [
    (_CX - _FAR_HALF, FAR_Y),    # top-left     (far baseline)
    (_CX + _FAR_HALF, FAR_Y),    # top-right    (far baseline)
    (_CX + _NEAR_HALF, NEAR_Y),  # bottom-right (near baseline)
    (_CX - _NEAR_HALF, NEAR_Y),  # bottom-left  (near baseline)
]


def test_reproduces_both_measured_anchors():
    s = PerspectiveScale.from_quad(QUAD)
    assert s.px_per_m(FAR_Y) == pytest.approx(FAR_PX_PER_M, abs=0.1)
    assert s.px_per_m(NEAR_Y) == pytest.approx(NEAR_PX_PER_M, abs=0.1)


def test_horizon_and_slope_match_hand_derivation():
    # y0 = (s_far*y_near - s_near*y_far) / (s_far - s_near)
    #    = (222698.5 - 677463.6) / (-497.5) = 914.101
    # a  = s_near / (y_near - y0) = 603.8 / 1180.899 = 0.511306
    s = PerspectiveScale.from_quad(QUAD)
    assert s.y0 == pytest.approx(914.101, abs=0.01)
    assert s.a == pytest.approx(0.511306, abs=0.00005)


def test_scale_is_linear_between_anchors():
    s = PerspectiveScale.from_quad(QUAD)
    mid_y = (FAR_Y + NEAR_Y) / 2.0
    assert s.px_per_m(mid_y) == pytest.approx(355.05, abs=0.1)


def test_scale_grows_monotonically_toward_the_camera():
    s = PerspectiveScale.from_quad(QUAD)
    vals = [s.px_per_m(y) for y in range(1100, 2100, 100)]
    assert all(b > a for a, b in zip(vals, vals[1:]))


def test_degenerate_quad_is_rejected():
    flat = [(0.0, 500.0), (100.0, 500.0), (100.0, 500.0), (0.0, 500.0)]
    with pytest.raises(ValueError):
        PerspectiveScale.from_quad(flat)


def test_requires_exactly_four_corners():
    with pytest.raises(ValueError):
        PerspectiveScale.from_quad(QUAD[:3])


def test_px_per_m_above_horizon_is_clamped_positive():
    s = PerspectiveScale.from_quad(QUAD)
    # y at or above the horizon has no physical scale; must not return <= 0
    assert s.px_per_m(s.y0) > 0.0
    assert s.px_per_m(s.y0 - 500.0) > 0.0


# --- validation against the real recorded court geometry ---
#
# QUAD above is synthetic: it was built FROM the target scales, so it can only
# confirm the algebra round-trips. This quad is the actual one recorded by the DJI
# run (outputs/.../court_annotations.txt), whose baselines are tilted rather than
# horizontal. Recovering the independently measured 106.3 and 603.8 px/m from it
# is a non-circular check, and it also confirms the COURT_WIDTH_M assumption
# matches the one those measurements were taken under.
REAL_QUAD = [(1724, 1122), (2372, 1135), (3827, 2095), (145, 2005)]


def test_real_recorded_quad_reproduces_the_measured_scales():
    s = PerspectiveScale.from_quad(REAL_QUAD)
    far_y = (1122 + 1135) / 2.0
    near_y = (2005 + 2095) / 2.0
    assert s.px_per_m(far_y) == pytest.approx(106.3, abs=0.1)
    assert s.px_per_m(near_y) == pytest.approx(603.8, abs=0.1)


def test_real_quad_shows_why_a_fixed_pixel_radius_is_wrong():
    """80 px means 0.75 m at the far baseline but 0.13 m at the near one."""
    s = PerspectiveScale.from_quad(REAL_QUAD)
    far_m = 80.0 / s.px_per_m((1122 + 1135) / 2.0)
    near_m = 80.0 / s.px_per_m((2005 + 2095) / 2.0)
    assert far_m == pytest.approx(0.75, abs=0.02)
    assert near_m == pytest.approx(0.13, abs=0.02)
    assert far_m / near_m > 5.0, "the perspective span the fixed constant ignores"


def test_tilted_baselines_are_handled_via_edge_length_not_row_difference():
    """The real quad's corners differ in y; scale must come from edge length."""
    s = PerspectiveScale.from_quad(REAL_QUAD)
    assert s.a > 0.0
    assert s.y0 == pytest.approx(931.71, abs=0.05)
