"""The far-half crop that makes the distant player detectable."""
import pytest

from badminton_analysis.court.far_roi import (
    DEFAULT_NET_COURT_Y, far_court_roi, net_image_row)
from badminton_analysis.court.mapper import CourtMapper

# The real annotated quad from the 0007 run (TL, TR, BR, BL).
QUAD = [(1536, 1229), (2287, 1254), (3823, 2103), (9, 2031)]
SHAPE = (2160, 3840)


def test_net_row_lies_between_the_baselines():
    row = net_image_row(QUAD, CourtMapper(QUAD))
    assert 1229 < row < 2031


def test_roi_is_far_narrower_than_the_frame():
    """Using every corner's x makes the crop full-width, which defeats it."""
    roi = far_court_roi(QUAD, SHAPE, CourtMapper(QUAD))
    assert roi is not None
    x0, _y0, x1, _y1 = roi
    assert x1 - x0 < SHAPE[1] / 2


def test_roi_contains_the_far_players_measured_feet_positions():
    """Feet positions of the real opponent, measured at imgsz=2560."""
    roi = far_court_roi(QUAD, SHAPE, CourtMapper(QUAD))
    x0, y0, x1, y1 = roi
    for fx, fy in [(2063.1, 1331.7), (2216.5, 1316.1), (1943.9, 1338.5),
                   (1959.8, 1333.4), (2003.9, 1328.0)]:
        assert x0 <= fx <= x1, f"feet x {fx} outside crop"
        assert y0 <= fy <= y1, f"feet y {fy} outside crop"


def test_roi_leaves_headroom_above_the_far_baseline():
    """Keyed on where feet can be, but it must hold whole bodies."""
    roi = far_court_roi(QUAD, SHAPE, CourtMapper(QUAD))
    assert roi[1] < 1229 - 100


def test_roi_stops_at_the_net_so_the_near_player_is_not_duplicated():
    roi = far_court_roi(QUAD, SHAPE, CourtMapper(QUAD))
    net = net_image_row(QUAD, CourtMapper(QUAD))
    assert roi[3] <= net + 61


def test_roi_stays_inside_the_frame():
    roi = far_court_roi(QUAD, SHAPE, CourtMapper(QUAD))
    x0, y0, x1, y1 = roi
    assert 0 <= x0 < x1 <= SHAPE[1]
    assert 0 <= y0 < y1 <= SHAPE[0]


def test_no_quad_yields_no_roi():
    assert far_court_roi(None, SHAPE, CourtMapper(QUAD)) is None


def test_no_mapper_yields_no_roi():
    """Without geometry the caller must skip the pass, not crop arbitrarily."""
    assert far_court_roi(QUAD, SHAPE, None) is None


def test_wrong_corner_count_yields_no_roi():
    assert far_court_roi(QUAD[:3], SHAPE, CourtMapper(QUAD)) is None


def test_net_court_y_is_half_the_court_length():
    assert DEFAULT_NET_COURT_Y == pytest.approx(13.4 / 2)
