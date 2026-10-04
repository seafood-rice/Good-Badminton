"""Synthetic RawSignals for hit-detector tests (no video, no models)."""
from dataclasses import replace

import numpy as np

from badminton_analysis.stroke.hit_signals import RawSignals

# far-left, far-right, near-right, near-left (CourtMapper order), 1280x720 frame
CORNERS = np.array([[440, 260], [840, 260], [1060, 660], [220, 660]], np.float32)

# A standing COCO-17 pose relative to the hip midpoint, torso length 1.0
# (shoulder midpoint at y=-1.0). Scaled by the player's on-screen size.
_POSE = np.array([
    [0.0, -1.6], [-0.1, -1.7], [0.1, -1.7], [-0.2, -1.65], [0.2, -1.65],
    [-0.35, -1.0], [0.35, -1.0],
    [-0.45, -0.5], [0.45, -0.5],
    [-0.5, -0.05], [0.5, -0.05],
    [-0.2, 0.0], [0.2, 0.0],
    [-0.22, 0.8], [0.22, 0.8],
    [-0.24, 1.6], [0.24, 1.6],
], np.float32)


def pose_at(hip_xy, scale):
    return (_POSE * float(scale) + np.asarray(hip_xy, np.float32)).astype(np.float32)


def make_signals(n=120, fps=30.0, size=(1280, 720), players=True):
    """Shuttle on a triangle wave between the players: a sharp turn every whole second."""
    t = np.arange(n) / fps
    x = 640 + 120 * np.sin(2 * np.pi * t / 2.0)
    y = 300 + 250 * np.abs((t % 2.0) - 1.0)
    shuttle = np.stack([x, y], 1).astype(np.float32)
    kp = np.full((n, 2, 17, 2), np.nan, np.float32)
    if players:
        kp[:, 0] = pose_at((640, 560), 60.0)   # near: large
        kp[:, 1] = pose_at((640, 250), 25.0)   # far: small
    return RawSignals(float(fps), 1, shuttle, kp, CORNERS.copy(), tuple(size))


def transform(sig, s, dx, dy):
    """Similarity transform of every image coordinate (a different camera zoom/position)."""
    d = np.array([dx, dy], np.float32)
    w, h = sig.image_size
    return replace(sig, shuttle=sig.shuttle * s + d, keypoints=sig.keypoints * s + d,
                   court_corners=sig.court_corners * s + d,
                   image_size=(int(round(w * s)), int(round(h * s))))
