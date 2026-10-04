"""Training-time augmentation of RawSignals -- the camera-gap defence (spec 7.4).

Applied to raw signals before features are computed, so every feature sees a
consistent transformed scene. Never mutates its input.
"""
from dataclasses import replace

import cv2
import numpy as np

from .hit_features import TARGET_HZ, apply_homography, resample_index

FLIP_KP = [0, 2, 1, 4, 3, 6, 5, 8, 7, 10, 9, 12, 11, 14, 13, 16, 15]


def mirror(sig):
    w = sig.image_size[0]

    def flip_x(a):
        b = np.array(a, dtype=np.float32, copy=True)
        b[..., 0] = (w - 1) - b[..., 0]
        return b

    return replace(sig, shuttle=flip_x(sig.shuttle),
                   keypoints=flip_x(sig.keypoints)[:, :, FLIP_KP],
                   court_corners=flip_x(sig.court_corners)[[1, 0, 3, 2]])


def random_homography(sig, rng, scale=(0.8, 1.2), shift_frac=0.1, persp_frac=0.03):
    w, h = sig.image_size
    src = np.float32([[0, 0], [w, 0], [w, h], [0, h]])
    centre = np.float32([w / 2.0, h / 2.0])
    size = np.float32([w, h])
    s = rng.uniform(*scale)
    dst = (src - centre) * s + centre + rng.uniform(-shift_frac, shift_frac, 2) * size
    dst = dst + rng.uniform(-persp_frac, persp_frac, (4, 2)) * size
    hmat = cv2.getPerspectiveTransform(src, dst.astype(np.float32))

    def warp(a):
        return apply_homography(hmat, a).astype(np.float32)

    return replace(sig, shuttle=warp(sig.shuttle), keypoints=warp(sig.keypoints),
                   court_corners=warp(sig.court_corners))


def shuttle_dropout(sig, rng, keep_range=(0.65, 1.0), max_gap=10):
    shuttle = sig.shuttle.copy()
    n = len(shuttle)
    present = np.isfinite(shuttle).all(axis=1)
    to_drop = int(round(present.sum() * (1.0 - rng.uniform(*keep_range))))
    dropped, guard = 0, 0
    while dropped < to_drop and guard < 10 * n + 10:
        start = int(rng.integers(0, max(n, 1)))
        seg = slice(start, min(n, start + int(rng.integers(1, max_gap + 1))))
        dropped += int(np.isfinite(shuttle[seg]).all(axis=1).sum())
        shuttle[seg] = np.nan
        guard += 1
    return replace(sig, shuttle=shuttle)


def keypoint_dropout(sig, rng, joint_p=0.05, player_p=0.02):
    kp = sig.keypoints.copy()
    kp[rng.random(kp.shape[:3]) < joint_p] = np.nan
    kp[rng.random(kp.shape[:2]) < player_p] = np.nan
    return replace(sig, keypoints=kp)


def augment(sig, rng):
    if rng.random() < 0.5:
        sig = mirror(sig)
    sig = random_homography(sig, rng)
    sig = shuttle_dropout(sig, rng)
    return keypoint_dropout(sig, rng)


def jittered_index(n_frames, fps, rng, jitter=0.1):
    """Resampling index with +-jitter frame-rate warp (the model still treats it as 30 Hz)."""
    return resample_index(n_frames, fps, TARGET_HZ * rng.uniform(1.0 - jitter, 1.0 + jitter))
