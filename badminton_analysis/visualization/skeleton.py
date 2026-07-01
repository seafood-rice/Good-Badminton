"""Shared COCO-17 skeleton + keypoint drawer, used by match and posture modes."""
import cv2
import numpy as np

# COCO-17 bone connections (shoulders, arms, torso, hips, legs).
SKELETON_CONNECTIONS = [
    (5, 6), (5, 7), (7, 9), (6, 8), (8, 10),
    (5, 11), (6, 12), (11, 12),
    (11, 13), (13, 15), (12, 14), (14, 16),
]


def _valid(kp, idx, conf, conf_thresh):
    x, y = float(kp[idx][0]), float(kp[idx][1])
    if x <= 1 or y <= 1:
        return False
    if conf is not None and float(conf[idx]) < conf_thresh:
        return False
    return True


def draw_skeleton(frame, keypoints, conf=None, conf_thresh=0.3,
                  line_color=(255, 191, 0), point_color=(255, 128, 0),
                  line_thickness=2, point_radius=3):
    """Draw one person's COCO-17 skeleton + keypoints on frame (in place)."""
    if keypoints is None:
        return frame
    kp = np.asarray(keypoints, dtype=float)
    if kp.ndim != 2 or kp.shape[0] < 17 or kp.shape[1] < 2:
        return frame

    for a, b in SKELETON_CONNECTIONS:
        if _valid(kp, a, conf, conf_thresh) and _valid(kp, b, conf, conf_thresh):
            pt1 = (int(kp[a][0]), int(kp[a][1]))
            pt2 = (int(kp[b][0]), int(kp[b][1]))
            cv2.line(frame, pt1, pt2, line_color, line_thickness, cv2.LINE_AA)

    for i in range(kp.shape[0]):
        if _valid(kp, i, conf, conf_thresh):
            cv2.circle(frame, (int(kp[i][0]), int(kp[i][1])), point_radius,
                       point_color, -1, cv2.LINE_AA)

    return frame
