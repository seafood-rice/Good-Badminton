"""Which stroke types the overhead-swing gate applies to.

The gate exists because rep over-counting was a real bug. It was applied only to
high_clear, leaving smash and drop_shot -- equally overhead strokes with the same
failure mode -- entirely unprotected.
"""
import numpy as np
import pytest
from badminton_analysis.analysis import joint_angles as ja
from badminton_analysis.analysis.biomechanics import BiomechanicalAnalyzer
from badminton_analysis.posture.system import (
    OVERHEAD_GATED_STROKES, PostureRunner, UNDERHAND_GATED_STROKES,
)


def _kp(wrist_y):
    """Dominant-side keypoints with a settable wrist height.

    torso = |shoulder_y - hip_y| = |100 - 250| = 150, so elevation is
    (100 - wrist_y) / 150: wrist_y=40 -> 0.40 (overhead), wrist_y=120 -> -0.13 (low).
    """
    kp = np.zeros((17, 2))
    kp[ja.R_SHOULDER] = (100, 100)
    kp[ja.R_ELBOW] = (140, 110)
    kp[ja.R_WRIST] = (180, wrist_y)
    kp[ja.L_SHOULDER] = (60, 90)
    kp[ja.L_HIP] = (60, 250)
    kp[ja.R_HIP] = (100, 250)
    kp[ja.R_KNEE] = (110, 300)
    kp[ja.R_ANKLE] = (100, 350)
    return kp


def _track(n=160):
    track = []
    for i in range(n):
        wrist = (100, 100)
        if i == 40 or i == 110:
            wrist = (260, 100)  # displacement spike -> rep candidate
        track.append({"frame": i, "wrist": wrist, "shuttle": None})
    return track


def _run(stroke_type, wrist_y):
    kp = _kp(wrist_y)
    racket = ja.infer_racket_head(kp, dominant="right")

    def frame_lookup(idx):
        return {"frame": idx, "keypoints": kp, "conf": None,
                "racket_head": racket, "centroid": (100 + (idx % 5), 300)}

    runner = PostureRunner(BiomechanicalAnalyzer(dominant="right"),
                           stroke_type=stroke_type, dominant="right")
    return runner.run(_track(), frame_lookup, fps=30)


def test_smash_and_drop_shot_are_gated():
    assert OVERHEAD_GATED_STROKES == ("high_clear", "smash", "drop_shot")


@pytest.mark.parametrize("stroke_type", ["smash", "drop_shot"])
def test_low_swings_are_filtered_for_overhead_strokes(stroke_type):
    _reports, _reps, gate = _run(stroke_type, wrist_y=120)
    assert gate["gated"] is True
    assert gate["counted"] == 0
    assert gate["filtered_non_overhead"] == 2


@pytest.mark.parametrize("stroke_type", ["smash", "drop_shot", "high_clear"])
def test_genuine_overhead_swings_survive(stroke_type):
    _reports, _reps, gate = _run(stroke_type, wrist_y=40)
    assert gate["counted"] == 2
    assert gate["filtered_non_overhead"] == 0


def test_serve_is_underhand_gated_not_overhead_gated():
    assert UNDERHAND_GATED_STROKES == ("serve",)
    assert "serve" not in OVERHEAD_GATED_STROKES


def test_serve_keeps_low_swings():
    """The whole point: overhead-gating a serve would filter every rep."""
    _reports, _reps, gate = _run("serve", wrist_y=120)
    assert gate["gated"] is True
    assert gate["counted"] == 2
    assert gate["filtered_overhead"] == 0
    assert gate["filtered_non_overhead"] == 0


def test_serve_filters_overhead_swings():
    _reports, _reps, gate = _run("serve", wrist_y=40)
    assert gate["counted"] == 0
    assert gate["filtered_overhead"] == 2
    assert gate["filtered_non_overhead"] == 0, (
        "a serve rep dropped for BEING overhead must not be counted as non-overhead")


def test_unjudgeable_elevation_keeps_the_rep_in_both_directions():
    """None elevation means 'cannot judge -> keep', the existing convention."""
    kp = np.zeros((17, 2))  # all-sentinel: no valid shoulder/wrist/hip
    racket = None

    def frame_lookup(idx):
        return {"frame": idx, "keypoints": kp, "conf": None,
                "racket_head": racket, "centroid": (100, 300)}

    for stroke_type in ("serve", "smash"):
        runner = PostureRunner(BiomechanicalAnalyzer(dominant="right"),
                               stroke_type=stroke_type, dominant="right")
        _reports, _reps, gate = runner.run(_track(), frame_lookup, fps=30)
        assert gate["filtered_overhead"] == 0
        assert gate["filtered_non_overhead"] == 0
