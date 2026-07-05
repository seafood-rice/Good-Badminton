"""Keypoint-window normalization shared by quality-model training and inference."""
import numpy as np

TARGET_FRAMES = 64
L_SHO, R_SHO, L_HIP, R_HIP = 5, 6, 11, 12
# COCO left/right index pairs for mirroring.
_SWAP = [(1, 2), (3, 4), (5, 6), (7, 8), (9, 10), (11, 12), (13, 14), (15, 16)]
_MIN_POSED = 8


def _resample(seq, target):
    """Linear time-resampling of (N, 17, 2) to (target, 17, 2)."""
    n = seq.shape[0]
    if n == target:
        return seq
    src = np.linspace(0.0, n - 1.0, target)
    lo = np.floor(src).astype(int)
    hi = np.minimum(lo + 1, n - 1)
    t = (src - lo)[:, None, None]
    return seq[lo] * (1.0 - t) + seq[hi] * t


def normalize_window(frames, mirror=False, target=TARGET_FRAMES):
    """(target, 34) float32 pose sequence: hip-centered, torso-scaled, resampled.

    frames: per-frame dicts with "keypoints" (17x2 or None). A joint with
    x<=1 and-or y<=1 is treated as an undetected sentinel (codebase convention)
    and masked to 0 in the output (the body-center origin). Frames without two
    valid hips are dropped. Returns None when fewer than _MIN_POSED usable
    frames remain.
    """
    posed = []
    for f in frames:
        kp = f.get("keypoints")
        if kp is None:
            continue
        kp = np.asarray(kp, dtype=float)
        if (kp[L_HIP][0] > 1.0 and kp[L_HIP][1] > 1.0
                and kp[R_HIP][0] > 1.0 and kp[R_HIP][1] > 1.0):
            posed.append(kp)
    if len(posed) < _MIN_POSED:
        return None
    seq = np.stack(posed)                                   # (N, 17, 2)
    valid = (seq[..., 0] > 1.0) & (seq[..., 1] > 1.0)       # (N, 17)
    hips = (seq[:, L_HIP] + seq[:, R_HIP]) / 2.0
    seq = seq - hips[:, None, :]
    shoulders = (seq[:, L_SHO] + seq[:, R_SHO]) / 2.0
    torso = np.linalg.norm(shoulders, axis=1)
    torso_ok = valid[:, L_SHO] & valid[:, R_SHO] & (torso > 1e-6)
    scale = float(np.median(torso[torso_ok])) if np.any(torso_ok) else 1.0
    seq = seq / max(scale, 1e-6)
    seq[~valid] = 0.0                                       # sentinels -> body-center
    if mirror:
        seq[:, :, 0] = -seq[:, :, 0]
        for a, b in _SWAP:
            seq[:, [a, b]] = seq[:, [b, a]]
    seq = _resample(seq, target)
    return seq.reshape(target, 34).astype(np.float32)
