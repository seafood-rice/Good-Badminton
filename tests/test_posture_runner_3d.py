import numpy as np
from badminton_analysis.posture.system import PostureRunner
from badminton_analysis.posture.report_builder import build_coach_report
from badminton_analysis.posture.writer import build_drill_summary
from badminton_analysis.analysis.biomechanics import BiomechanicalAnalyzer
from badminton_analysis.analysis import joint_angles as ja


class _UnavailableLifter:
    """A configured-but-unavailable lifter (no weights / no torch)."""

    available = False

    def lift(self, window_frames, image_size):   # pragma: no cover - never called
        raise AssertionError("an unavailable lifter must not be invoked")


class _FakeLifter:
    available = True

    def lift(self, window_frames, image_size):
        frames = [int(f["frame"]) for f in window_frames if f.get("keypoints") is not None]
        if not frames:
            return None
        kp3d = np.zeros((len(frames), 17, 3), dtype=float)
        # straight R arm (elbow ~180) so 3D angles are well-defined
        kp3d[:, 14], kp3d[:, 15], kp3d[:, 16] = (0, 0, 0), (1, 0, 0), (2, 0, 0)
        kp3d[:, 1], kp3d[:, 2], kp3d[:, 3] = (0, 1, 0), (0, 0, 0), (1, 0, 0)
        kp3d[:, 11], kp3d[:, 14] = (-1, 0, 0), (1, 0, 0)
        kp3d[:, 4], kp3d[:, 1] = (-1, 0, 0), (1, 0, 0)
        return kp3d, frames


def _track_and_frames():
    # A single clear peak in the wrist-y trajectory so segment_reps yields one rep.
    fps = 30.0
    track = []
    frames = {}
    for i in range(60):
        wy = 200.0 - (80.0 if i == 30 else 0.0) - max(0.0, 40.0 - abs(i - 30) * 4)
        wrist = (100.0, wy)
        track.append({"frame": i, "wrist": wrist, "shuttle": None})
        kp = np.full((17, 2), 50.0, dtype=float)
        kp[9] = kp[10] = wrist            # wrists
        kp[11], kp[12] = (95.0, 200.0), (105.0, 200.0)
        frames[i] = {"frame": i, "keypoints": kp, "conf": None,
                     "racket_head": None, "centroid": (100.0, 200.0),
                     "racket_head_detected": False}
    return track, frames, fps


def test_runner_attaches_3d_and_counts():
    track, frames, fps = _track_and_frames()
    runner = PostureRunner(BiomechanicalAnalyzer(dominant="right"),
                           stroke_type="serve", dominant="right",
                           pose_lifter=_FakeLifter(), image_size=(200, 400))
    reports, reps, gate = runner.run(track, frames.get, fps)
    assert len(reports) >= 1
    assert reports[0]["feature_space"] == "3d"
    assert gate["scored_3d"] >= 1
    assert len(gate["reps_3d"]) >= 1


def test_runner_without_lifter_is_2d():
    track, frames, fps = _track_and_frames()
    runner = PostureRunner(BiomechanicalAnalyzer(dominant="right"),
                           stroke_type="serve", dominant="right")
    reports, reps, gate = runner.run(track, frames.get, fps)
    assert reports[0]["feature_space"] == "2d"
    assert gate["scored_3d"] == 0
    assert gate["reps_3d"] == []


def _run(lifter):
    track, frames, fps = _track_and_frames()
    runner = PostureRunner(BiomechanicalAnalyzer(dominant="right"),
                           stroke_type="serve", dominant="right",
                           pose_lifter=lifter, image_size=(200, 400))
    return runner.run(track, frames.get, fps)


def _coach_report(reports, feature_space):
    summary = build_drill_summary(reports, "serve")
    meta = {"date": "2026-07-26", "stroke_type": "serve", "dominant_hand": "right",
            "pose_family": "yolo-pose", "feature_space": feature_space}
    return build_coach_report(reports, summary, meta)["en"], summary


def test_scores_identical_with_and_without_lifter():
    """The no-lifter path is *score-identical and schema-additive*, which is the
    accurate form of the plan's original "byte-identical" claim: a configured but
    unavailable lifter changes no score anywhere, from per-metric entries up
    through the drill summary and the coach report."""
    plain_reports, _, plain_gate = _run(None)
    degraded_reports, _, degraded_gate = _run(_UnavailableLifter())

    assert plain_reports == degraded_reports
    assert plain_gate["scored_3d"] == degraded_gate["scored_3d"] == 0
    assert plain_gate["reps_3d"] == degraded_gate["reps_3d"] == []

    plain_en, plain_summary = _coach_report(plain_reports, "2d")
    degraded_en, degraded_summary = _coach_report(degraded_reports, "2d")
    assert plain_summary == degraded_summary
    assert plain_en == degraded_en
    assert plain_en["header"]["feature_space"] == "2d"
    # The additive labels exist but carry no 3D content on this path.
    for report in plain_reports:
        assert report["feature_space"] == "2d"
        for entry in report["per_metric"].values():
            assert entry["feature_space"] == "2d"
            assert "measured_shadow_2d" not in entry


def test_lifter_does_not_change_the_2d_only_metric_scores():
    """Only METRICS_3D_CAPABLE may move when a lifter is configured. trunk_rotation
    is deliberately not in that set, so its measurement, range and score must be
    bit-for-bit the same with and without lifting -- otherwise a drill's score
    history stops being comparable across lifter on/off."""
    reports_2d, _, _ = _run(None)
    reports_3d, _, _ = _run(_FakeLifter())
    assert reports_2d[0]["feature_space"] == "2d"
    assert reports_3d[0]["feature_space"] == "3d"

    pm_2d = reports_2d[0]["per_metric"]
    pm_3d = reports_3d[0]["per_metric"]
    assert set(pm_2d) == set(pm_3d)
    for name in pm_2d:
        if name in ja.METRICS_3D_CAPABLE:
            assert pm_3d[name]["feature_space"] == "3d"
        else:
            assert pm_3d[name]["feature_space"] == "2d", name
            assert pm_3d[name] == pm_2d[name], name
    assert "trunk_rotation" in pm_2d and "trunk_rotation" not in ja.METRICS_3D_CAPABLE


# ── Overhead-gate + lifter combination: a gated-out rep must not inflate
# scored_3d/reps_3d, and the surviving rep's reps_3d entry must carry the same
# (renumbered) rep_id as its final report. Mirrors
# tests/test_posture_runner.py's _gate_kp/_track_with_two_swings fixtures.

def _gate_kp(elevated):
    """Right-dominant pose; elevated=True lifts the wrist above the shoulder
    (ratio 0.4 > OVERHEAD_MIN_ELEVATION=0.35), elevated=False keeps it well
    below (ratio -0.333)."""
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


def _track_with_two_swings(n=160):
    track = []
    for i in range(n):
        wrist = (100, 100)
        if i == 40 or i == 110:
            wrist = (260, 100)  # big displacement -> speed spike
        track.append({"frame": i, "wrist": wrist, "shuttle": None})
    return track


def test_gated_out_rep_does_not_inflate_scored_3d_or_reps_3d():
    """Two reps (peaks at frame 40 and 110, see test_posture_runner.py's
    test_gate_drops_non_overhead_rep_and_keeps_overhead_rep for the window-
    isolation reasoning): rep 1's apex window never sees an elevated wrist and
    is gated out; rep 2's does and survives. With an active lifter, the gated-
    out rep must be neither lifted nor scored: scored_3d/reps_3d must reflect
    only the survivor, and that survivor's reps_3d rep_id must match its
    final (renumbered) report rep_id, not its original pre-gate rep_id (2).
    """
    low_kp = _gate_kp(elevated=False)
    high_kp = _gate_kp(elevated=True)

    def frame_lookup(idx):
        kp = high_kp if idx == 100 else low_kp
        return {"frame": idx, "keypoints": kp, "conf": None,
                "racket_head": None, "centroid": (100, 300),
                "racket_head_detected": False}

    runner = PostureRunner(BiomechanicalAnalyzer(dominant="right"),
                           stroke_type="high_clear", dominant="right",
                           pose_lifter=_FakeLifter(), image_size=(200, 400))
    reports, reps, gate = runner.run(_track_with_two_swings(), frame_lookup, fps=30)

    assert len(reps) == 2
    assert gate["filtered_non_overhead"] == 1
    assert len(reports) == 1
    assert gate["counted"] == 1
    assert gate["scored_3d"] <= gate["counted"]
    assert gate["scored_3d"] == 1
    assert reports[0]["feature_space"] == "3d"
    assert len(gate["reps_3d"]) == 1
    assert reports[0]["rep_id"] == 1  # renumbered survivor id
    assert gate["reps_3d"][0]["rep_id"] == reports[0]["rep_id"]
