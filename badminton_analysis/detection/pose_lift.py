"""Optional MotionBERT 2D->3D pose lifting for the posture pipeline."""
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
