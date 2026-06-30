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


_DOMINANT = {
    "right": (R_SHOULDER, R_ELBOW, R_WRIST, R_HIP, R_KNEE, R_ANKLE),
    "left": (L_SHOULDER, L_ELBOW, L_WRIST, L_HIP, L_KNEE, L_ANKLE),
}


def compute_joint_angles(keypoints, racket_head=None, dominant="right", conf=None):
    """Compute the six biomechanical angles for one frame. Missing inputs -> None."""
    kp = np.asarray(keypoints, dtype=float)
    sh, el, wr, hip, kn, an = _DOMINANT.get(dominant, _DOMINANT["right"])

    angles = {
        "elbow_extension": None,
        "shoulder_abduction": None,
        "trunk_rotation": None,
        "knee_flexion": None,
        "hip_shoulder_separation": None,
        "wrist_flexion": None,
    }

    if is_valid(kp, sh, conf) and is_valid(kp, el, conf) and is_valid(kp, wr, conf):
        angles["elbow_extension"] = angle_at(kp[sh], kp[el], kp[wr])
    if is_valid(kp, hip, conf) and is_valid(kp, sh, conf) and is_valid(kp, el, conf):
        angles["shoulder_abduction"] = angle_at(kp[hip], kp[sh], kp[el])
    if is_valid(kp, hip, conf) and is_valid(kp, kn, conf) and is_valid(kp, an, conf):
        angles["knee_flexion"] = angle_at(kp[hip], kp[kn], kp[an])

    shoulder_line = None
    if is_valid(kp, L_SHOULDER, conf) and is_valid(kp, R_SHOULDER, conf):
        shoulder_line = line_angle(kp[L_SHOULDER], kp[R_SHOULDER])
    hip_line = None
    if is_valid(kp, L_HIP, conf) and is_valid(kp, R_HIP, conf):
        hip_line = line_angle(kp[L_HIP], kp[R_HIP])

    if shoulder_line is not None:
        angles["trunk_rotation"] = shoulder_line
    if shoulder_line is not None and hip_line is not None:
        diff = abs(shoulder_line - hip_line)
        angles["hip_shoulder_separation"] = min(diff, 180.0 - diff)

    if racket_head is not None and is_valid(kp, el, conf) and is_valid(kp, wr, conf):
        angles["wrist_flexion"] = angle_at(kp[el], kp[wr], racket_head)

    return angles


def weight_transfer_ratio(centroid_start, centroid_contact, shoulder_width_px):
    """Horizontal centroid displacement normalized by shoulder width. None if width invalid."""
    if not shoulder_width_px or abs(float(shoulder_width_px)) < 1e-6:
        return None
    dx = abs(float(centroid_contact[0]) - float(centroid_start[0]))
    return float(dx / float(shoulder_width_px))
