"""Tests for infer_racket_head — kinematic fallback when no racket model is available."""
import numpy as np
import pytest
from badminton_analysis.analysis import joint_angles as ja


def _make_kp(overrides=None):
    """Return a (17,2) array of zeros (missing). overrides: {idx: (x, y)}."""
    kp = np.zeros((17, 2), dtype=float)
    if overrides:
        for idx, (x, y) in overrides.items():
            kp[idx] = (x, y)
    return kp


def test_right_hand_inference():
    """elbow (100,100), wrist (140,100), extend=0.5 => (160.0, 100.0)."""
    kp = _make_kp({ja.R_ELBOW: (100, 100), ja.R_WRIST: (140, 100)})
    result = ja.infer_racket_head(kp, dominant="right", extend=0.5)
    assert result is not None
    assert result == (160.0, 100.0)


def test_left_hand_inference():
    """Left hand uses L_ELBOW/L_WRIST."""
    kp = _make_kp({ja.L_ELBOW: (200, 50), ja.L_WRIST: (200, 80)})
    result = ja.infer_racket_head(kp, dominant="left", extend=0.5)
    assert result is not None
    # direction is (0, 30), extend 0.5 => wrist + (0, 15) = (200, 95)
    assert result == (200.0, 95.0)


def test_missing_wrist_returns_none():
    """Wrist at (0,0) (missing) => None."""
    kp = _make_kp({ja.R_ELBOW: (100, 100), ja.R_WRIST: (0, 0)})
    result = ja.infer_racket_head(kp, dominant="right")
    assert result is None


def test_missing_elbow_returns_none():
    """Elbow at (1,1) (missing, both axes <=1) => None."""
    kp = _make_kp({ja.R_ELBOW: (1, 1), ja.R_WRIST: (140, 100)})
    result = ja.infer_racket_head(kp, dominant="right")
    assert result is None


def test_vertical_forearm():
    """elbow (100,100), wrist (100,160), extend=0.6 => (100.0, 196.0)."""
    kp = _make_kp({ja.R_ELBOW: (100, 100), ja.R_WRIST: (100, 160)})
    result = ja.infer_racket_head(kp, dominant="right", extend=0.6)
    assert result is not None
    assert abs(result[0] - 100.0) < 1e-9
    assert abs(result[1] - 196.0) < 1e-9


def test_default_dominant_is_right():
    """Default dominant='right' uses R_ELBOW/R_WRIST."""
    kp = _make_kp({ja.R_ELBOW: (100, 100), ja.R_WRIST: (140, 100)})
    result = ja.infer_racket_head(kp, extend=0.5)
    assert result == (160.0, 100.0)
