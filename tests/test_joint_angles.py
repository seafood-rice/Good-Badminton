import numpy as np
import pytest
from badminton_analysis.analysis import joint_angles as ja


def test_angle_at_right_angle():
    # a above vertex, c to the right of vertex -> 90 degrees
    assert ja.angle_at((0, -1), (0, 0), (1, 0)) == pytest.approx(90.0, abs=1e-6)


def test_angle_at_straight_line():
    assert ja.angle_at((-1, 0), (0, 0), (1, 0)) == pytest.approx(180.0, abs=1e-6)


def test_angle_at_degenerate_returns_none():
    assert ja.angle_at((0, 0), (0, 0), (1, 0)) is None


def test_line_angle_horizontal_is_zero():
    assert ja.line_angle((0, 0), (5, 0)) == pytest.approx(0.0, abs=1e-6)


def test_line_angle_vertical_is_90():
    assert ja.line_angle((0, 0), (0, 5)) == pytest.approx(90.0, abs=1e-6)


def test_is_valid_detects_missing_keypoint():
    kp = np.zeros((17, 2))
    kp[ja.R_ELBOW] = (1, 1)      # missing sentinel
    kp[ja.R_WRIST] = (100, 200)  # present
    assert ja.is_valid(kp, ja.R_WRIST) is True
    assert ja.is_valid(kp, ja.R_ELBOW) is False
