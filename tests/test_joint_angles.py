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


def _straight_arm_keypoints():
    kp = np.zeros((17, 2))
    # right arm in a straight vertical line -> elbow extension 180
    kp[ja.R_SHOULDER] = (100, 100)
    kp[ja.R_ELBOW] = (100, 150)
    kp[ja.R_WRIST] = (100, 200)
    # both shoulders / hips horizontal -> trunk_rotation ~0, separation ~0
    kp[ja.L_SHOULDER] = (60, 100)
    kp[ja.L_HIP] = (60, 250)
    kp[ja.R_HIP] = (100, 250)
    # right leg straight -> knee 180
    kp[ja.R_KNEE] = (100, 300)
    kp[ja.R_ANKLE] = (100, 350)
    return kp


def test_compute_joint_angles_elbow_and_knee():
    angles = ja.compute_joint_angles(_straight_arm_keypoints(), dominant="right")
    assert angles["elbow_extension"] == pytest.approx(180.0, abs=1e-6)
    assert angles["knee_flexion"] == pytest.approx(180.0, abs=1e-6)
    assert angles["trunk_rotation"] == pytest.approx(0.0, abs=1e-6)


def test_compute_joint_angles_missing_racket_gives_none_wrist():
    angles = ja.compute_joint_angles(_straight_arm_keypoints(), racket_head=None, dominant="right")
    assert angles["wrist_flexion"] is None


def test_compute_joint_angles_with_racket():
    kp = _straight_arm_keypoints()
    angles = ja.compute_joint_angles(kp, racket_head=(150, 200), dominant="right")
    # elbow(100,150)->wrist(100,200)->racket(150,200): 90 degrees
    assert angles["wrist_flexion"] == pytest.approx(90.0, abs=1e-6)


def test_weight_transfer_ratio_basic():
    assert ja.weight_transfer_ratio((100, 0), (130, 0), 60.0) == pytest.approx(0.5, abs=1e-6)


def test_weight_transfer_ratio_zero_width_none():
    assert ja.weight_transfer_ratio((100, 0), (130, 0), 0.0) is None
