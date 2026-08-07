import numpy as np
from badminton_analysis.detection.pose_lift import (
    coco2h36m, coco2h36m_valid, joint_validity,
)


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


def test_joint_validity_flags_the_sentinel():
    kp = np.full((2, 17, 2), 50.0)
    kp[0, 9] = (0.0, 0.0)     # undetected sentinel
    kp[1, 3] = (1.0, 1.0)     # sentinel boundary: x<=1 and y<=1 -> undetected
    v = joint_validity(kp)
    assert v.shape == (2, 17)
    assert not v[0, 9] and not v[1, 3]
    assert v[0].sum() == 16 and v[1].sum() == 16


def test_coco2h36m_valid_direct_and_derived_joints():
    valid = np.ones((1, 17), dtype=bool)
    valid[0, 9] = False                       # COCO L wrist undetected
    v = coco2h36m_valid(valid)
    assert v.shape == (1, 17)
    assert not v[0, 13]                       # H36M L wrist <- COCO 9 (direct)
    assert v[0, [j for j in range(17) if j != 13]].all()

    # A derived joint is invalid when ANY of its COCO sources is.
    valid = np.ones((1, 17), dtype=bool)
    valid[0, 12] = False                      # COCO R hip
    v = coco2h36m_valid(valid)
    assert not v[0, 0]                        # pelvis = mid-hip
    assert not v[0, 1]                        # R hip (direct)
    assert not v[0, 7]                        # spine = mid(pelvis, thorax)
    assert v[0, 8]                            # thorax unaffected (shoulders only)

    valid = np.ones((1, 17), dtype=bool)
    valid[0, 1] = False                       # COCO L eye
    assert not coco2h36m_valid(valid)[0, 10]  # head = mid-eye

    valid = np.ones((1, 17), dtype=bool)
    valid[0, 5] = False                       # COCO L shoulder
    v = coco2h36m_valid(valid)
    assert not v[0, 8] and not v[0, 7] and not v[0, 11]
    assert v[0, 0]                            # pelvis unaffected (hips only)
