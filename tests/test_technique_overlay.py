import numpy as np
from badminton_analysis.visualization.technique_overlay import draw_technique_overlay


def test_returns_same_shape_and_mutates():
    frame = np.zeros((300, 400, 3), dtype=np.uint8)
    angles = {"elbow_extension": 150.0, "knee_flexion": 40.0, "wrist_flexion": None}
    out = draw_technique_overlay(frame, angles, label="smash", score=82)
    assert out.shape == (300, 400, 3)
    assert out is frame
    assert int(frame.sum()) > 0  # something was drawn


def test_empty_angles_is_safe():
    frame = np.zeros((100, 100, 3), dtype=np.uint8)
    out = draw_technique_overlay(frame, {})
    assert out.shape == (100, 100, 3)
