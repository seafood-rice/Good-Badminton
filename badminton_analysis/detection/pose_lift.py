"""Optional MotionBERT 2D->3D pose lifting for the posture pipeline."""
import os

import numpy as np


def coco2h36m(seq):
    """Convert (T,17,C) COCO-17 keypoints to (T,17,C) H36M-17 (MotionBERT order).

    Synthesizes the H36M joints COCO lacks (pelvis, spine, thorax, head) from
    COCO joints, following the standard MotionBERT/VideoPose3D convention.
    """
    x = np.asarray(seq, dtype=float)
    y = np.zeros_like(x)
    y[:, 0] = (x[:, 11] + x[:, 12]) * 0.5   # 0 pelvis = mid-hip
    y[:, 1] = x[:, 12]                        # 1 R hip
    y[:, 2] = x[:, 14]                        # 2 R knee
    y[:, 3] = x[:, 16]                        # 3 R ankle
    y[:, 4] = x[:, 11]                        # 4 L hip
    y[:, 5] = x[:, 13]                        # 5 L knee
    y[:, 6] = x[:, 15]                        # 6 L ankle
    y[:, 8] = (x[:, 5] + x[:, 6]) * 0.5       # 8 thorax = mid-shoulder
    y[:, 7] = (y[:, 0] + y[:, 8]) * 0.5       # 7 spine = mid(pelvis, thorax)
    y[:, 9] = x[:, 0]                         # 9 nose
    y[:, 10] = (x[:, 1] + x[:, 2]) * 0.5      # 10 head = mid-eye
    y[:, 11] = x[:, 5]                        # 11 L shoulder
    y[:, 12] = x[:, 7]                        # 12 L elbow
    y[:, 13] = x[:, 9]                        # 13 L wrist
    y[:, 14] = x[:, 6]                        # 14 R shoulder
    y[:, 15] = x[:, 8]                        # 15 R elbow
    y[:, 16] = x[:, 10]                       # 16 R wrist
    return y


# Hip indices in COCO order, for the posed-frame gate (matches quality/normalize.py).
_COCO_L_HIP, _COCO_R_HIP = 11, 12
POSE_LIFT_MIN_POSED = 8


def _posed_with_frames(window_frames):
    """Return (kps, frames): (M,17,2) float array of frames with two valid hips
    (x>1 and y>1), and the list of their source frame numbers. Empty -> ([], [])."""
    kps = []
    frames = []
    for f in window_frames:
        kp = f.get("keypoints")
        if kp is None:
            continue
        kp = np.asarray(kp, dtype=float)
        if (kp[_COCO_L_HIP][0] > 1.0 and kp[_COCO_L_HIP][1] > 1.0
                and kp[_COCO_R_HIP][0] > 1.0 and kp[_COCO_R_HIP][1] > 1.0):
            kps.append(kp)
            frames.append(int(f["frame"]))
    if not kps:
        return np.zeros((0, 17, 2), dtype=float), []
    return np.stack(kps), frames


def _normalize_screen(kps, image_size):
    """Normalize pixel coords to roughly [-1,1] by width (VideoPose3D convention):
    x' = x / w * 2 - 1 ; y' = y / w * 2 - h / w. Preserves aspect ratio."""
    w, h = float(image_size[0]), float(image_size[1])
    if w <= 0:
        return kps
    out = kps.copy()
    out[..., 0] = out[..., 0] / w * 2.0 - 1.0
    out[..., 1] = out[..., 1] / w * 2.0 - h / w
    return out


class PoseLifter:
    """Optional MotionBERT 2D->3D lifter, structured like quality.scorer.QualityScorer.

    `model` (or a model loaded from `model_path` in Task 10) is a callable taking
    a (1, M, 17, 2) float32 array of normalized H36M-17 2D keypoints and returning
    a (1, M, 17, 3) array of root-relative 3D joints.
    """

    def __init__(self, model_path=None, device="auto", model=None):
        self.model_path = model_path
        self.device = device
        self._model = model
        if model is not None:
            self.available = True
        elif model_path and os.path.exists(model_path):
            self.available = True   # real load happens lazily in Task 10's _load()
        else:
            self.available = False

    def _load(self):
        # Task 10 replaces this with a real torch/MotionBERT adapter load.
        return self._model

    def lift(self, window_frames, image_size):
        if not self.available:
            return None
        model = self._load()
        if model is None:
            return None
        kps2d, frames = _posed_with_frames(window_frames)
        if kps2d.shape[0] < POSE_LIFT_MIN_POSED:
            return None
        try:
            h36m = coco2h36m(kps2d)                      # (M,17,2)
            norm = _normalize_screen(h36m, image_size)   # (M,17,2)
            batch = norm[None, ...].astype(np.float32)   # (1,M,17,2)
            out = np.asarray(model(batch), dtype=float)  # (1,M,17,3)
            kp3d = out[0]
            if kp3d.shape != (kps2d.shape[0], 17, 3):
                return None
            return kp3d, frames
        except Exception as e:
            print("Pose lifting failed (" + str(e) + ")")
            return None
