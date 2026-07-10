import numpy as np
import pytest
from badminton_analysis.analysis.biomechanics import BiomechanicalAnalyzer
from badminton_analysis.analysis import joint_angles as ja
from badminton_analysis.posture.system import (
    PostureRunner, apex_overhead_elevation, OVERHEAD_MIN_ELEVATION,
)


def _smash_kp():
    # Wrist raised above the shoulder so this fixture reads as a full overhead
    # swing and survives the overhead-swing gate (see apex_overhead_elevation
    # below): shoulder_y - wrist_y = 100-40 = 60, torso (|shoulder_y-hip_y|)
    # = 150, ratio 0.4 > OVERHEAD_MIN_ELEVATION (0.35).
    kp = np.zeros((17, 2))
    kp[ja.R_SHOULDER] = (100, 100)
    kp[ja.R_ELBOW] = (140, 110)
    kp[ja.R_WRIST] = (180, 40)
    kp[ja.L_SHOULDER] = (60, 90)
    kp[ja.L_HIP] = (60, 250)
    kp[ja.R_HIP] = (100, 250)
    kp[ja.R_KNEE] = (110, 300)
    kp[ja.R_ANKLE] = (100, 350)
    return kp


def _track_with_two_swings(n=160):
    track = []
    for i in range(n):
        wrist = (100, 100)
        if i == 40 or i == 110:
            wrist = (260, 100)  # big displacement -> speed spike
        track.append({"frame": i, "wrist": wrist, "shuttle": None})
    return track


def test_runner_one_report_per_rep_with_rep_id():
    kp = _smash_kp()
    racket = ja.infer_racket_head(kp, dominant="right")

    def frame_lookup(idx):
        return {"frame": idx, "keypoints": kp, "conf": None,
                "racket_head": racket, "centroid": (100 + (idx % 5), 300)}

    runner = PostureRunner(BiomechanicalAnalyzer(dominant="right"),
                           stroke_type="high_clear", dominant="right")
    reports, reps, gate_info = runner.run(_track_with_two_swings(), frame_lookup, fps=30)
    assert len(reps) == 2
    assert len(reports) == 2
    assert reports[0]["rep_id"] == 1 and reports[1]["rep_id"] == 2
    assert reports[0]["stroke_type"] == "high_clear"
    assert reports[0]["player_side"] == "single"
    assert "overall_score" in reports[0]
    assert gate_info == {"counted": 2, "filtered_non_overhead": 0, "gated": True}


def test_runner_empty_track_no_reports():
    runner = PostureRunner(BiomechanicalAnalyzer(), stroke_type="smash")
    reports, reps, gate_info = runner.run([], lambda i: None, fps=30)
    assert reports == [] and reps == []
    assert gate_info == {"counted": 0, "filtered_non_overhead": 0, "gated": False}


# ── apex_overhead_elevation: max (shoulder_y - wrist_y)/torso for the dominant side ──

def _kp_with(shoulder_y, wrist_y, hip_y, dominant="right"):
    kp = np.zeros((17, 2))
    shoulder_idx, wrist_idx, hip_idx = (
        (ja.R_SHOULDER, ja.R_WRIST, ja.R_HIP) if dominant == "right"
        else (ja.L_SHOULDER, ja.L_WRIST, ja.L_HIP)
    )
    kp[shoulder_idx] = (100, shoulder_y)
    kp[wrist_idx] = (100, wrist_y)
    kp[hip_idx] = (100, hip_y)
    return kp


def test_apex_elevation_positive_when_wrist_well_above_shoulder():
    # shoulder_y=100, wrist_y=40 -> shoulder_y - wrist_y = 60; torso = |100-250| = 150
    kp = _kp_with(shoulder_y=100, wrist_y=40, hip_y=250)
    window_frames = [{"keypoints": kp, "conf": None}]
    elev = apex_overhead_elevation(window_frames, dominant="right")
    assert elev == pytest.approx(60 / 150)


def test_apex_elevation_negative_or_zero_when_wrist_stays_below_shoulder():
    # Two frames, both wrist below shoulder; the max (least negative) is returned.
    kp_a = _kp_with(shoulder_y=100, wrist_y=130, hip_y=250)   # ratio = -30/150 = -0.2
    kp_b = _kp_with(shoulder_y=100, wrist_y=190, hip_y=250)   # ratio = -90/150 = -0.6
    window_frames = [{"keypoints": kp_a, "conf": None}, {"keypoints": kp_b, "conf": None}]
    elev = apex_overhead_elevation(window_frames, dominant="right")
    assert elev == pytest.approx(-0.2)
    assert elev <= 0


def test_apex_elevation_none_when_no_valid_frames():
    window_frames = [
        {"keypoints": None, "conf": None},
        {"keypoints": np.zeros((17, 2)), "conf": None},  # all sentinel (0,0)
    ]
    assert apex_overhead_elevation(window_frames, dominant="right") is None


def test_apex_elevation_left_dominant_uses_left_indices():
    kp = _kp_with(shoulder_y=100, wrist_y=40, hip_y=250, dominant="left")
    # Right-side joints are left at the (0, 0) sentinel, so right-dominant can't judge.
    window_frames = [{"keypoints": kp, "conf": None}]
    assert apex_overhead_elevation(window_frames, dominant="right") is None
    elev = apex_overhead_elevation(window_frames, dominant="left")
    assert elev == pytest.approx(60 / 150)


# ── Overhead-swing gate inside PostureRunner.run() ──────────────────────────
# Empirically established discriminator (see rep-overhead-gate-brief.md): a full
# overhead swing lifts the dominant wrist above the dominant shoulder at the
# swing apex; a soft/low return keeps it at or below. Only OVERHEAD_GATED_STROKES
# ("high_clear") are gated; elev=None (can't judge) always keeps the rep.

def _gate_kp(elevated):
    """Right-dominant pose; elevated=True lifts the wrist above the shoulder,
    elevated=False keeps it well below (ratio comfortably on either side of
    OVERHEAD_MIN_ELEVATION=0.35, torso=150): elevated -> (100-40)/150=0.4,
    non-elevated -> (100-150)/150=-0.333.
    """
    kp = np.zeros((17, 2))
    kp[ja.R_SHOULDER] = (100, 100)
    kp[ja.R_ELBOW] = (140, 110)
    kp[ja.R_WRIST] = (180, 40 if elevated else 150)
    kp[ja.L_SHOULDER] = (60, 90)
    kp[ja.L_HIP] = (60, 250)
    kp[ja.R_HIP] = (100, 250)
    kp[ja.R_KNEE] = (110, 300)
    kp[ja.R_ANKLE] = (100, 350)
    return kp


def test_gate_drops_non_overhead_rep_and_keeps_overhead_rep():
    """The money test: two reps (peaks at frame 40 and 110); rep 1's apex
    window (frames 0-85, OVERHEAD_APEX_S=1.5s @ 30fps = +/-45) never sees an
    elevated wrist -> dropped. Rep 2's apex window (65-155) includes frame
    100, which is elevated -> kept.
    """
    low_kp = _gate_kp(elevated=False)
    high_kp = _gate_kp(elevated=True)

    def frame_lookup(idx):
        kp = high_kp if idx == 100 else low_kp
        return {"frame": idx, "keypoints": kp, "conf": None,
                "racket_head": None, "centroid": (100, 300)}

    runner = PostureRunner(BiomechanicalAnalyzer(dominant="right"),
                           stroke_type="high_clear", dominant="right")
    reports, reps, gate_info = runner.run(_track_with_two_swings(), frame_lookup, fps=30)

    assert len(reps) == 2  # segmentation itself is unaffected by the gate
    assert len(reports) == 1
    assert [r["rep_id"] for r in reports] == [2]
    assert gate_info == {"counted": 1, "filtered_non_overhead": 1, "gated": True}
    assert reports[0]["overhead_elevation"] is not None
    assert reports[0]["overhead_elevation"] >= OVERHEAD_MIN_ELEVATION


def _one_swing_track(n=90, spike_at=40):
    track = []
    for i in range(n):
        wrist = (100, 100)
        if i == spike_at:
            wrist = (260, 100)
        track.append({"frame": i, "wrist": wrist, "shuttle": None})
    return track


def test_gate_does_not_apply_to_non_gated_stroke_type():
    low_kp = _gate_kp(elevated=False)

    def frame_lookup(idx):
        return {"frame": idx, "keypoints": low_kp, "conf": None,
                "racket_head": None, "centroid": (100, 300)}

    runner = PostureRunner(BiomechanicalAnalyzer(dominant="right"),
                           stroke_type="serve", dominant="right")
    reports, reps, gate_info = runner.run(_one_swing_track(), frame_lookup, fps=30)

    assert len(reps) == 1
    assert len(reports) == 1
    assert gate_info == {"counted": 1, "filtered_non_overhead": 0, "gated": False}


def test_gate_keeps_rep_when_elevation_cannot_be_judged():
    def frame_lookup(idx):
        return {"frame": idx, "keypoints": None, "conf": None,
                "racket_head": None, "centroid": (100, 300)}

    runner = PostureRunner(BiomechanicalAnalyzer(dominant="right"),
                           stroke_type="high_clear", dominant="right")
    reports, reps, gate_info = runner.run(_one_swing_track(), frame_lookup, fps=30)

    assert len(reps) == 1
    assert len(reports) == 1
    assert reports[0]["overhead_elevation"] is None
    assert gate_info == {"counted": 1, "filtered_non_overhead": 0, "gated": True}
