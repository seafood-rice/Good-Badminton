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
    # racket_head_detected is absent from the frame record here (the pre-fix
    # shape) -> _window_frames defaults it to False, matching this fixture's
    # racket being kinematically inferred (never actually detected).
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
    # Inferred (not detected) racket head -> wrist_flexion is unmeasurable (None),
    # not scored as 0 for the ~180 deg collinear fallback angle.
    assert reports[0]["per_metric"]["wrist_flexion"]["measured"] is None
    assert gate_info == {"counted": 2, "filtered_non_overhead": 0, "gated": True}


def test_runner_wrist_flexion_measured_when_racket_head_detected():
    """With racket_head_detected=True carried on the frame record, wrist_flexion
    is computed (non-None) and contributes to overall_score - the counterpart
    to test_runner_one_report_per_rep_with_rep_id's inferred (None) case.
    """
    kp = _smash_kp()
    detected_head = (kp[ja.R_WRIST][0] + 20, kp[ja.R_WRIST][1])

    def frame_lookup(idx):
        return {"frame": idx, "keypoints": kp, "conf": None,
                "racket_head": detected_head, "racket_head_detected": True,
                "centroid": (100 + (idx % 5), 300)}

    runner = PostureRunner(BiomechanicalAnalyzer(dominant="right"),
                           stroke_type="high_clear", dominant="right")
    reports, reps, gate_info = runner.run(_track_with_two_swings(), frame_lookup, fps=30)
    assert len(reports) == 2
    assert reports[0]["per_metric"]["wrist_flexion"]["measured"] is not None


# ── PostureRunner._window_frames: racket_head_detected plumbing ────────────

def test_window_frames_propagates_racket_head_detected_flag():
    def frame_lookup(idx):
        return {"frame": idx, "keypoints": None, "conf": None,
                "racket_head": (1.0, 2.0), "centroid": None,
                "racket_head_detected": idx == 5}

    runner = PostureRunner(BiomechanicalAnalyzer(dominant="right"), stroke_type="high_clear")
    frames = runner._window_frames(4, 6, frame_lookup)
    by_frame = {f["frame"]: f["racket_head_detected"] for f in frames}
    assert by_frame == {4: False, 5: True, 6: False}


def test_window_frames_defaults_racket_head_detected_to_false_when_absent():
    """Older/incomplete frame records without the key must default to False
    (unmeasurable/inferred), never silently True."""
    def frame_lookup(idx):
        return {"frame": idx, "keypoints": None, "conf": None,
                "racket_head": None, "centroid": None}

    runner = PostureRunner(BiomechanicalAnalyzer(dominant="right"), stroke_type="high_clear")
    frames = runner._window_frames(1, 1, frame_lookup)
    assert frames[0]["racket_head_detected"] is False


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

    Before the contiguous-numbering fix the sole survivor kept its raw,
    pre-gate id (2, since it was segment_reps's second rep). After the fix
    run() renumbers survivors 1..N, so the lone kept report becomes rep_id 1.
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
    assert [r.rep_id for r in reps] == [1, 2]  # RepWindow list is left as-is (not renumbered)
    assert len(reports) == 1
    assert [r["rep_id"] for r in reports] == [1]
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


# ── Contiguous rep numbering after the overhead gate drops a middle rep ─────
# The gate runs per-rep and filters AFTER segment_reps assigned rep_ids, so a
# gated-out middle rep would otherwise leave surviving reports at their
# original, gapped ids (e.g. 1, 3). run() must renumber survivors 1..N.

def _track_with_three_swings(n=280):
    track = []
    for i in range(n):
        wrist = (100, 100)
        if i in (40, 110, 220):
            wrist = (260, 100)  # big displacement -> speed spike
        track.append({"frame": i, "wrist": wrist, "shuttle": None})
    return track


def test_gate_drop_of_middle_rep_renumbers_survivors_contiguously():
    """Three genuine swings (peaks near frames 40, 110, 220); only the middle
    one (110) reads non-overhead and is gated out. Before the renumbering fix
    the surviving reports keep their original ids [1, 3] (a gap); after the
    fix they must be contiguous [1, 2].
    """
    low_kp = _gate_kp(elevated=False)
    high_kp = _gate_kp(elevated=True)

    def frame_lookup(idx):
        # Elevated wrist only near the first and third swings' apex windows;
        # the middle swing's apex window (see test_gate_drops_... above for
        # the same window-isolation reasoning) never sees an elevated wrist.
        kp = high_kp if idx in (40, 220) else low_kp
        return {"frame": idx, "keypoints": kp, "conf": None,
                "racket_head": None, "centroid": (100, 300)}

    runner = PostureRunner(BiomechanicalAnalyzer(dominant="right"),
                           stroke_type="high_clear", dominant="right")
    reports, reps, gate_info = runner.run(_track_with_three_swings(), frame_lookup, fps=30)

    assert len(reps) == 3  # segmentation itself is unaffected by the gate
    assert [r.rep_id for r in reps] == [1, 2, 3]  # RepWindow list is left as-is (not renumbered)
    assert len(reports) == 2  # middle rep gated out
    assert [r["rep_id"] for r in reports] == [1, 2]  # contiguous, no gap left at the old id 3
    assert gate_info == {"counted": 2, "filtered_non_overhead": 1, "gated": True}
