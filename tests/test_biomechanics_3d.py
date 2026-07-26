import numpy as np
from badminton_analysis.analysis.biomechanics import BiomechanicalAnalyzer
from badminton_analysis.stroke.events import StrokeEvent


def _straight_pose_3d():
    kp = np.zeros((17, 3), dtype=float)
    kp[14], kp[15], kp[16] = (0, 0, 0), (1, 0, 0), (2, 0, 0)   # straight R arm -> 180
    kp[1], kp[2], kp[3] = (0, 1, 0), (0, 0, 0), (1, 0, 0)      # R leg -> 90
    kp[11], kp[14] = (-1, 0, 0), (1, 0, 0)                     # shoulder line
    kp[15], kp[16] = (2, 0, 0), (3, 0, 0)                      # shift arm with shoulder to stay straight
    kp[4], kp[1] = (-1, 0, 0), (1, 0, 0)                       # hip line aligned -> 0 sep
    return kp


def _coco_pose_2d():
    kp = np.full((17, 2), 50.0, dtype=float)  # all detected
    return kp


def _event():
    return StrokeEvent(stroke_type="high_clear", contact_frame=10,
                       window_start=5, window_end=15, player_side="single", confidence=1.0)


def test_3d_branch_labels_feature_space_and_shadow():
    contact = {"frame": 10, "keypoints": _coco_pose_2d(), "conf": None,
               "keypoints_3d": _straight_pose_3d(), "centroid": (50.0, 50.0),
               "racket_head": None, "racket_head_detected": False}
    window = [{"frame": 5, "centroid": (40.0, 50.0)}, contact]
    report = BiomechanicalAnalyzer(dominant="right").analyze(_event(), window)
    assert report["feature_space"] == "3d"
    pm = report["per_metric"]
    assert pm["elbow_extension"]["feature_space"] == "3d"
    assert abs(pm["elbow_extension"]["measured"] - 180.0) < 1e-6
    assert "measured_shadow_2d" in pm["elbow_extension"]
    assert pm["wrist_flexion"]["feature_space"] == "2d"
    assert pm["weight_transfer"]["feature_space"] == "2d"


def test_no_3d_is_unchanged_2d():
    contact = {"frame": 10, "keypoints": _coco_pose_2d(), "conf": None,
               "centroid": (50.0, 50.0), "racket_head": None, "racket_head_detected": False}
    window = [{"frame": 5, "centroid": (40.0, 50.0)}, contact]
    report = BiomechanicalAnalyzer(dominant="right").analyze(_event(), window)
    assert report["feature_space"] == "2d"
    assert all(v["feature_space"] == "2d" for v in report["per_metric"].values())
