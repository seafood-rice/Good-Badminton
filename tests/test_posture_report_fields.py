# tests/test_posture_report_fields.py
"""Anchor provenance must reach the per-rep report.

Task 4 made the serve path depend on shuttle detection quality: with a shuttle the
contact is well anchored, without one it falls back to a coarser signal. Without
this field a clip where the shuttle was rarely detected looks identical to one where
it was always detected.
"""
import numpy as np
from badminton_analysis.analysis import joint_angles as ja
from badminton_analysis.analysis.biomechanics import BiomechanicalAnalyzer
from badminton_analysis.posture.system import PostureRunner


def _kp(wrist_y):
    """torso = |100 - 250| = 150, so elevation is (100 - wrist_y) / 150.

    wrist_y=40 -> 0.40, an overhead swing. wrist_y=120 -> -0.13, a low one. A serve
    fixture MUST use the low wrist: at 0.40 the Task 2 ceiling gate filters every rep
    and there would be no report to inspect.
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


def _run(stroke_type, with_shuttle):
    # Overhead strokes need a raised wrist to survive the floor gate; a serve needs a
    # low one to survive the ceiling gate.
    kp = _kp(120 if stroke_type == "serve" else 40)
    racket = ja.infer_racket_head(kp, dominant="right")
    track = []
    for i in range(160):
        wrist = (260.0, 100.0) if i in (40, 110) else (100.0, 100.0)
        shuttle = (100.0, 100.0) if (with_shuttle and i in (40, 110)) else None
        track.append({"frame": i, "wrist": wrist, "shuttle": shuttle})

    def frame_lookup(idx):
        return {"frame": idx, "keypoints": kp, "conf": None,
                "racket_head": racket, "centroid": (100 + (idx % 5), 300)}

    runner = PostureRunner(BiomechanicalAnalyzer(dominant="right"),
                           stroke_type=stroke_type, dominant="right")
    return runner.run(track, frame_lookup, fps=30)


def test_report_records_shuttle_anchor():
    reports, _reps, _gate = _run("smash", with_shuttle=True)
    assert reports[0]["contact_anchor"] == "shuttle"


def test_report_records_apex_anchor_for_overhead_without_shuttle():
    reports, _reps, _gate = _run("smash", with_shuttle=False)
    assert reports[0]["contact_anchor"] == "apex"


def test_report_records_speed_peak_for_serve_without_shuttle():
    """Serve gets no positional refinement, so the speed peak stands."""
    reports, _reps, _gate = _run("serve", with_shuttle=False)
    assert reports
    assert reports[0]["contact_anchor"] == "speed_peak"
