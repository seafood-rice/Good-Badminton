import numpy as np
from badminton_analysis.detection.pose_lift import coco2h36m


def test_coco2h36m_derived_and_direct_joints():
    # One frame, distinct integer coords per COCO joint so we can assert mapping.
    coco = np.zeros((1, 17, 2), dtype=float)
    for i in range(17):
        coco[0, i] = (i + 1, (i + 1) * 10)  # (x, y) unique per joint
    h = coco2h36m(coco)
    assert h.shape == (1, 17, 2)
    # Direct joints
    np.testing.assert_allclose(h[0, 14], coco[0, 6])   # R shoulder <- COCO R shoulder (6)
    np.testing.assert_allclose(h[0, 16], coco[0, 10])  # R wrist <- COCO R wrist (10)
    np.testing.assert_allclose(h[0, 13], coco[0, 9])   # L wrist <- COCO L wrist (9)
    np.testing.assert_allclose(h[0, 3], coco[0, 16])   # R ankle <- COCO R ankle (16)
    # Derived joints
    np.testing.assert_allclose(h[0, 0], (coco[0, 11] + coco[0, 12]) * 0.5)  # pelvis = mid-hip
    np.testing.assert_allclose(h[0, 8], (coco[0, 5] + coco[0, 6]) * 0.5)    # thorax = mid-shoulder
    np.testing.assert_allclose(h[0, 7], (h[0, 0] + h[0, 8]) * 0.5)          # spine = mid(pelvis,thorax)
