"""Raw per-frame signals the learned hit detector reads.

One row per native frame, from frame 1 (the match loop's first frame_count) to
the last analysed frame: the shuttle's image position and both players' 17
COCO keypoints, plus the court corners. Missing values are NaN. Side index 0 is
"lower" (the near player), 1 is "upper" (the far player).

The match pipeline writes these to ``hit_signals.npz``. The ShuttleSet dataset
builder harvests the same file, so the detector is trained on exactly what it
sees at match time.
"""
from dataclasses import dataclass

import numpy as np

SIDES = ("lower", "upper")
N_KP = 17


@dataclass
class RawSignals:
    fps: float
    first_frame: int
    shuttle: np.ndarray        # (T, 2) float32, image px, NaN = missing
    keypoints: np.ndarray      # (T, 2, 17, 2) float32, image px, NaN = missing
    court_corners: np.ndarray  # (4, 2) float32: far-left, far-right, near-right, near-left
    image_size: tuple          # (width, height)

    @property
    def n_frames(self):
        return int(self.shuttle.shape[0])

    def frame_number(self, index):
        return self.first_frame + int(index)


def _keypoints_or_nan(kp):
    if kp is None:
        return np.full((N_KP, 2), np.nan, np.float32)
    arr = np.asarray(kp, dtype=np.float32)
    if arr.ndim != 2 or arr.shape[0] != N_KP or arr.shape[1] < 2:
        return np.full((N_KP, 2), np.nan, np.float32)
    arr = arr[:, :2].copy()
    missing = (arr[:, 0] <= 1) & (arr[:, 1] <= 1)
    arr[missing] = np.nan
    return arr


def build_signals(fps, last_frame, analysis_frames, shuttle_by_frame, court_corners, image_size):
    """RawSignals for frames 1..last_frame.

    ``analysis_frames`` is BadmintonAnalysisSystem._analysis_frames
    ({frame: {"players": {side: {"keypoints": array|None}}}}); ``shuttle_by_frame``
    maps a 1-based frame to (x, y) or None. Frames outside 1..last_frame are ignored.
    """
    total = int(last_frame)
    if total < 1:
        raise ValueError("no frames to build hit signals from")
    shuttle = np.full((total, 2), np.nan, np.float32)
    keypoints = np.full((total, 2, N_KP, 2), np.nan, np.float32)
    for frame, point in (shuttle_by_frame or {}).items():
        if point is not None and 1 <= int(frame) <= total:
            shuttle[int(frame) - 1] = (float(point[0]), float(point[1]))
    for frame, rec in (analysis_frames or {}).items():
        if not 1 <= int(frame) <= total:
            continue
        players = (rec or {}).get("players") or {}
        for s, side in enumerate(SIDES):
            keypoints[int(frame) - 1, s] = _keypoints_or_nan((players.get(side) or {}).get("keypoints"))
    corners = np.asarray(court_corners, dtype=np.float32).reshape(4, 2)
    return RawSignals(float(fps), 1, shuttle, keypoints, corners,
                      (int(image_size[0]), int(image_size[1])))


def save_signals(path, sig):
    np.savez_compressed(path, fps=np.float64(sig.fps), first_frame=np.int64(sig.first_frame),
                        shuttle=sig.shuttle, keypoints=sig.keypoints,
                        court_corners=sig.court_corners,
                        image_size=np.asarray(sig.image_size, dtype=np.int64))


def load_signals(path):
    with np.load(path) as z:
        return RawSignals(float(z["fps"]), int(z["first_frame"]),
                          z["shuttle"].astype(np.float32), z["keypoints"].astype(np.float32),
                          z["court_corners"].astype(np.float32),
                          tuple(int(v) for v in z["image_size"]))
