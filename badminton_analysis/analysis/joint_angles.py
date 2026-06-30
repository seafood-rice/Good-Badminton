"""Joint-angle geometry on COCO 17 keypoints (2D image coordinates)."""
import numpy as np

# COCO 17 keypoint indices
NOSE = 0
L_EYE, R_EYE = 1, 2
L_EAR, R_EAR = 3, 4
L_SHOULDER, R_SHOULDER = 5, 6
L_ELBOW, R_ELBOW = 7, 8
L_WRIST, R_WRIST = 9, 10
L_HIP, R_HIP = 11, 12
L_KNEE, R_KNEE = 13, 14
L_ANKLE, R_ANKLE = 15, 16


def angle_at(a, b, c):
    """Interior angle (degrees) at vertex b formed by a-b-c. None if degenerate."""
    a = np.asarray(a, dtype=float)
    b = np.asarray(b, dtype=float)
    c = np.asarray(c, dtype=float)
    ba = a - b
    bc = c - b
    n_ba = np.linalg.norm(ba)
    n_bc = np.linalg.norm(bc)
    if n_ba < 1e-6 or n_bc < 1e-6:
        return None
    cos_ang = float(np.dot(ba, bc) / (n_ba * n_bc))
    cos_ang = max(-1.0, min(1.0, cos_ang))
    return float(np.degrees(np.arccos(cos_ang)))


def line_angle(p, q):
    """Orientation (degrees, [0,180)) of segment p->q from horizontal. None if p==q."""
    p = np.asarray(p, dtype=float)
    q = np.asarray(q, dtype=float)
    dx = q[0] - p[0]
    dy = q[1] - p[1]
    if abs(dx) < 1e-9 and abs(dy) < 1e-9:
        return None
    return float(np.degrees(np.arctan2(dy, dx)) % 180.0)


def is_valid(kp, idx, conf=None, conf_thresh=0.3):
    """True if keypoint idx is present (not <=1 on both axes) and above conf threshold."""
    x, y = float(kp[idx][0]), float(kp[idx][1])
    if x <= 1 and y <= 1:
        return False
    if conf is not None and float(conf[idx]) < conf_thresh:
        return False
    return True
