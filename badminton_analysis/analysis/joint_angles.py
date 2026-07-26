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


def infer_racket_head(keypoints, dominant="right", extend=0.6):
    """Infer racket head from elbow+wrist keypoints when no racket model is available.

    Extends the forearm vector past the wrist by ``extend`` times the forearm length.
    Returns ``(float x, float y)`` or ``None`` if either keypoint is missing/invalid.
    """
    kp = np.asarray(keypoints, dtype=float)
    if dominant == "left":
        elbow_idx, wrist_idx = L_ELBOW, L_WRIST
    else:
        elbow_idx, wrist_idx = R_ELBOW, R_WRIST

    if not is_valid(kp, elbow_idx) or not is_valid(kp, wrist_idx):
        return None

    elbow = kp[elbow_idx]
    wrist = kp[wrist_idx]
    racket = wrist + extend * (wrist - elbow)
    return (float(racket[0]), float(racket[1]))


def weight_transfer_ratio(centroid_start, centroid_contact, shoulder_width_px):
    """Horizontal centroid displacement normalized by shoulder width. None if width invalid."""
    if not shoulder_width_px or abs(float(shoulder_width_px)) < 1e-6:
        return None
    dx = abs(float(centroid_contact[0]) - float(centroid_start[0]))
    return float(dx / float(shoulder_width_px))


# ---- 3D (MotionBERT H36M-17) angle support -------------------------------
# H36M-17 joint indices (MotionBERT output order).
H36M_PELVIS = 0
H36M_R_HIP, H36M_R_KNEE, H36M_R_ANKLE = 1, 2, 3
H36M_L_HIP, H36M_L_KNEE, H36M_L_ANKLE = 4, 5, 6
H36M_SPINE, H36M_THORAX = 7, 8
H36M_NOSE, H36M_HEAD = 9, 10
H36M_L_SHOULDER, H36M_L_ELBOW, H36M_L_WRIST = 11, 12, 13
H36M_R_SHOULDER, H36M_R_ELBOW, H36M_R_WRIST = 14, 15, 16

# Vertical axis of MotionBERT's root-relative output: index 1, pointing DOWN
# (image-space y, so head y < pelvis y -- not Y-up). Confirmed in Task 10 against
# the vendored model; see badminton_analysis/detection/pose_lift.py's module
# docstring for the evidence. No flip is needed: the sole consumer is
# trunk_rotation's horizontal-plane projection, which only *excludes* this axis
# and is therefore sign-independent.
VERTICAL_AXIS_3D = 1

_DOMINANT_H36M = {
    "right": (H36M_R_SHOULDER, H36M_R_ELBOW, H36M_R_WRIST, H36M_R_HIP, H36M_R_KNEE, H36M_R_ANKLE),
    "left": (H36M_L_SHOULDER, H36M_L_ELBOW, H36M_L_WRIST, H36M_L_HIP, H36M_L_KNEE, H36M_L_ANKLE),
}

METRICS_3D_CAPABLE = ("elbow_extension", "knee_flexion", "trunk_rotation", "hip_shoulder_separation")


def vector_angle(u, v):
    """Angle (degrees, [0,180]) between vectors u and v. None if either is ~0."""
    u = np.asarray(u, dtype=float)
    v = np.asarray(v, dtype=float)
    nu = np.linalg.norm(u)
    nv = np.linalg.norm(v)
    if nu < 1e-6 or nv < 1e-6:
        return None
    cos_ang = float(np.dot(u, v) / (nu * nv))
    cos_ang = max(-1.0, min(1.0, cos_ang))
    return float(np.degrees(np.arccos(cos_ang)))


def compute_joint_angles_3d(keypoints_3d, dominant="right"):
    """View-invariant anatomical angles from a (17,3) H36M-17 pose.

    Returns the four 3D-capable metrics (elbow/knee/trunk/hip-shoulder). The
    racket-relative wrist_flexion and global-translation weight_transfer are not
    computable from root-relative body-only 3D and are handled 2D elsewhere.
    """
    kp = np.asarray(keypoints_3d, dtype=float)
    sh, el, wr, hip, kn, an = _DOMINANT_H36M.get(dominant, _DOMINANT_H36M["right"])
    angles = {"elbow_extension": None, "knee_flexion": None,
              "trunk_rotation": None, "hip_shoulder_separation": None}
    angles["elbow_extension"] = angle_at(kp[sh], kp[el], kp[wr])
    angles["knee_flexion"] = angle_at(kp[hip], kp[kn], kp[an])
    sho_vec = kp[H36M_R_SHOULDER] - kp[H36M_L_SHOULDER]
    hip_vec = kp[H36M_R_HIP] - kp[H36M_L_HIP]
    angles["hip_shoulder_separation"] = vector_angle(sho_vec, hip_vec)
    horiz = [i for i in range(3) if i != VERTICAL_AXIS_3D]
    angles["trunk_rotation"] = vector_angle(sho_vec[horiz], hip_vec[horiz])
    return angles
