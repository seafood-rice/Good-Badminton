import numpy as np
from badminton_analysis.analysis import joint_angles as ja


def _straight_arm_pose():
    # H36M-17 3D pose; only the joints we assert on need meaningful values.
    kp = np.zeros((17, 3), dtype=float)
    # Right arm straight along +x: shoulder(14), elbow(15), wrist(16)
    kp[14] = (0.0, 0.0, 0.0)
    kp[15] = (1.0, 0.0, 0.0)
    kp[16] = (2.0, 0.0, 0.0)
    # Right leg bent 90 deg: hip(1), knee(2), ankle(3)
    kp[1] = (0.0, 1.0, 0.0)
    kp[2] = (0.0, 0.0, 0.0)
    kp[3] = (0.0, 1.0, 0.0)  # perpendicular to hip vector for 90 deg after hip override
    # Shoulder line along x; hip line rotated 30 deg in the horizontal (x,z) plane
    kp[11] = (-1.0, 0.0, 0.0)   # L shoulder
    kp[14] = (1.0, 0.0, 0.0)    # R shoulder (overrides above; fine for line test)
    kp[15] = (2.0, 0.0, 0.0)    # R elbow (shift with shoulder to keep arm straight)
    kp[16] = (3.0, 0.0, 0.0)    # R wrist (shift with shoulder to keep arm straight)
    kp[4] = (-np.cos(np.radians(30)), 0.0, -np.sin(np.radians(30)))  # L hip
    kp[1] = (np.cos(np.radians(30)), 0.0, np.sin(np.radians(30)))    # R hip
    return kp


def test_vector_angle_basic():
    assert ja.vector_angle((1, 0, 0), (0, 1, 0)) == 90.0
    assert ja.vector_angle((1, 0, 0), (1, 0, 0)) == 0.0
    assert ja.vector_angle((0, 0, 0), (1, 0, 0)) is None


def test_compute_joint_angles_3d_known_angles():
    kp = _straight_arm_pose()
    a = ja.compute_joint_angles_3d(kp, dominant="right")
    assert abs(a["elbow_extension"] - 180.0) < 1e-6   # straight arm
    assert abs(a["knee_flexion"] - 90.0) < 1e-6       # right-angle leg
    # Shoulder line (x-axis) vs hip line (30 deg in x,z plane): separation ~30 deg
    assert abs(a["hip_shoulder_separation"] - 30.0) < 1e-6


def test_metrics_3d_capable_excludes_trunk_rotation():
    """trunk_rotation is a 2D-only metric: in 2D it is the shoulder line's tilt
    from the image horizontal, which no 3D quantity reproduces, and the 3D
    candidate duplicated hip_shoulder_separation."""
    assert ja.METRICS_3D_CAPABLE == ("elbow_extension", "knee_flexion",
                                     "hip_shoulder_separation")
    assert "trunk_rotation" not in ja.METRICS_3D_CAPABLE


def test_compute_joint_angles_3d_returns_only_capable_metrics():
    a = ja.compute_joint_angles_3d(_straight_arm_pose(), dominant="right")
    assert set(a) == set(ja.METRICS_3D_CAPABLE)
    assert "trunk_rotation" not in a
