"""Per-step features for the learned hit detector, built to travel across cameras.

Everything is resampled to TARGET_HZ first. Shuttle positions are expressed in
"court units" (offset from the net's image midpoint divided by the court's
on-screen baseline-to-baseline length); each player's pose in that player's own
torso lengths; feet in court metres through the court homography (feet are on
the ground, so that mapping is exact). A zoomed, shifted or higher-resolution
view of the same play therefore yields the same features. Missing inputs become
0 with an explicit flag.
"""
import cv2
import numpy as np

TARGET_HZ = 30.0
COURT_W, COURT_L = 6.1, 13.4
L_SH, R_SH, L_WR, R_WR, L_HIP, R_HIP, L_ANK, R_ANK = 5, 6, 9, 10, 11, 12, 15, 16
SHUTTLE_DIM = 8
PLAYER_DIM = 41
FEATURE_DIM = SHUTTLE_DIM + 2 * PLAYER_DIM
FEATURE_SCHEMA = 1
_CLIP = 1.0e3


def resample_index(n_frames, fps, target_hz=TARGET_HZ):
    """Native frame indices nearest to each TARGET_HZ step (empty if fps is unusable)."""
    try:
        fps = float(fps)
    except (TypeError, ValueError):
        return np.zeros(0, dtype=np.int64)
    if n_frames <= 0 or not np.isfinite(fps) or fps <= 0 or target_hz <= 0:
        return np.zeros(0, dtype=np.int64)
    k_max = int(np.floor((n_frames - 1) * target_hz / fps + 1e-9))
    idx = np.rint(np.arange(k_max + 1) * fps / target_hz).astype(np.int64)
    return np.clip(idx, 0, n_frames - 1)


def apply_homography(h, pts):
    pts = np.asarray(pts, dtype=np.float64)
    flat = pts.reshape(-1, 2)
    homog = np.hstack([flat, np.ones((flat.shape[0], 1))]) @ np.asarray(h, np.float64).T
    with np.errstate(invalid="ignore", divide="ignore"):
        out = homog[:, :2] / homog[:, 2:3]
    return out.reshape(pts.shape)


class CourtGeometry:
    """Court-relative measurements from the four image corners."""

    def __init__(self, corners):
        c = np.asarray(corners, dtype=np.float32).reshape(4, 2)
        dst = np.array([[0, 0], [COURT_W, 0], [COURT_W, COURT_L], [0, COURT_L]], np.float32)
        if not np.isfinite(c).all():
            raise ValueError("degenerate court corners")
        # Check for strictly convex quadrilateral: all cross products must be non-zero,
        # same sign, and |cross| >= 1.0 px^2. This rejects collinear and self-intersecting quads.
        edges = np.array([c[(i+1) % 4] - c[i] for i in range(4)], dtype=np.float64)
        crosses = np.array([float(np.cross(edges[i], edges[(i+1) % 4])) for i in range(4)])
        if not np.all(np.isfinite(crosses)) or np.any(np.abs(crosses) < 1.0):
            raise ValueError("degenerate court corners")
        if not (np.all(crosses > 0) or np.all(crosses < 0)):
            raise ValueError("degenerate court corners")
        area = 0.5 * abs(float(np.cross(c[2] - c[0], c[3] - c[1])))
        if area < 1.0:
            raise ValueError("degenerate court corners")
        try:
            self.to_court = cv2.getPerspectiveTransform(c, dst).astype(np.float64)
            self.to_image = cv2.getPerspectiveTransform(dst, c).astype(np.float64)
        except cv2.error as exc:
            raise ValueError(f"degenerate court corners: {exc}") from exc
        far = apply_homography(self.to_image, [COURT_W / 2, 0.0])
        near = apply_homography(self.to_image, [COURT_W / 2, COURT_L])
        self.net_mid = apply_homography(self.to_image, [COURT_W / 2, COURT_L / 2])
        self.length_px = float(np.hypot(*(near - far)))
        if not np.isfinite(self.length_px) or self.length_px < 1.0 or not np.isfinite(self.net_mid).all():
            raise ValueError("degenerate court corners")

    def court_units(self, pts):
        return (np.asarray(pts, np.float64) - self.net_mid) / self.length_px

    def ground_metres(self, pts):
        return apply_homography(self.to_court, pts)


def _diff(a):
    out = np.full(a.shape, np.nan, dtype=np.float64)
    out[1:] = a[1:] - a[:-1]
    return out


def _turn_angles(pos):
    n = len(pos)
    out = np.full(n, np.nan)
    if n < 3:
        return out
    v1 = pos[1:-1] - pos[:-2]
    v2 = pos[2:] - pos[1:-1]
    n1 = np.linalg.norm(v1, axis=1)
    n2 = np.linalg.norm(v2, axis=1)
    ok = np.isfinite(n1) & np.isfinite(n2) & (n1 > 1e-9) & (n2 > 1e-9)
    with np.errstate(invalid="ignore", divide="ignore"):
        cos = np.einsum("ij,ij->i", v1, v2) / (n1 * n2)
    ang = np.degrees(np.arccos(np.clip(cos, -1.0, 1.0))) / 180.0
    out[1:-1] = np.where(ok, ang, np.nan)
    return out


def _mid(a, b):
    return (a + b) / 2.0


def _either(a, b):
    both = np.isfinite(a).all(axis=1) & np.isfinite(b).all(axis=1)
    only_a = np.isfinite(a).all(axis=1) & ~both
    out = np.where(both[:, None], _mid(a, b), np.where(only_a[:, None], a, b))
    return out


def _player_block(kp, shuttle, geo, dt):
    """(K, 41) features for one side; kp (K,17,2) and shuttle (K,2) in image px."""
    k = kp.shape[0]
    kp = kp.astype(np.float64)
    shuttle = shuttle.astype(np.float64)
    hip = _mid(kp[:, L_HIP], kp[:, R_HIP])
    shoulder = _mid(kp[:, L_SH], kp[:, R_SH])
    torso = np.linalg.norm(shoulder - hip, axis=1)
    valid = np.isfinite(torso) & (torso > 1.0)
    t = np.where(valid, torso, np.nan)
    with np.errstate(invalid="ignore", divide="ignore"):
        rel = (kp - hip[:, None, :]) / t[:, None, None]
        wrists = kp[:, [L_WR, R_WR]]
        wrist_speed = np.linalg.norm(_diff(wrists), axis=2) / dt / t[:, None]
        speed = np.fmax(wrist_speed[:, 0], wrist_speed[:, 1])
        feet = geo.ground_metres(_either(kp[:, L_ANK], kp[:, R_ANK])) / np.array([COURT_W, COURT_L])
        shuttle_off = (shuttle - hip) / t[:, None]
        wrist_dist = np.linalg.norm(shuttle[:, None, :] - wrists, axis=2) / t[:, None]
        nearest = np.fmin(wrist_dist[:, 0], wrist_dist[:, 1])
    block = np.concatenate([rel.reshape(k, 34), speed[:, None], feet, shuttle_off,
                            nearest[:, None]], axis=1)
    block = np.clip(np.nan_to_num(block, nan=0.0, posinf=0.0, neginf=0.0), -_CLIP, _CLIP)
    block[~valid] = 0.0
    return np.concatenate([block, valid[:, None].astype(np.float64)], axis=1)


def compute_features(sig, idx=None):
    """(features (K, FEATURE_DIM) float32, native indices (K,)) at TARGET_HZ."""
    if idx is None:
        idx = resample_index(sig.n_frames, sig.fps)
    idx = np.asarray(idx, dtype=np.int64)
    feats = np.zeros((len(idx), FEATURE_DIM), np.float32)
    if len(idx) == 0:
        return feats, idx
    geo = CourtGeometry(sig.court_corners)
    dt = 1.0 / TARGET_HZ
    shuttle_px = sig.shuttle[idx]
    pos = geo.court_units(shuttle_px)
    visible = np.isfinite(pos).all(axis=1)
    vel = _diff(pos) / dt
    acc = _diff(vel) / dt
    turn = _turn_angles(pos)
    block = np.concatenate([pos, vel, acc, turn[:, None]], axis=1)
    feats[:, 0:7] = np.clip(np.nan_to_num(block, nan=0.0, posinf=0.0, neginf=0.0), -_CLIP, _CLIP)
    feats[:, 7] = visible
    for p in range(2):
        base = SHUTTLE_DIM + p * PLAYER_DIM
        feats[:, base:base + PLAYER_DIM] = _player_block(sig.keypoints[idx, p], shuttle_px, geo, dt)
    return feats, idx
