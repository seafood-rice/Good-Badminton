"""C2: rally segmentation from a recorded per-frame track.

Boundaries are defined in SECONDS throughout, so the same temporal pattern
at 30 and 60 fps must produce the same answer (design spec done-means 8).
"""
import importlib.util
import inspect
import json
import pathlib
import random

import pytest

import badminton_analysis.system as sysmod
from badminton_analysis.stroke import rallies

# The DJI 0010 court quad (TL, TR, BR, BL) whose measured baseline scale
# (106.3 / 603.8 px/m) produced the script's 21.3 / 120.9 px caps (§0.15/§0.16).
DJI_QUAD = [[1724, 1122], [2372, 1135], [3827, 2095], [145, 2005]]
DJI_FPS = 59.94005994005994


def _burst_series(fps, pattern):
    """(times, activity) from a list of (seconds, level) spans."""
    times, act, t = [], [], 0.0
    for dur, level in pattern:
        for _ in range(int(round(dur * fps))):
            times.append(t)
            act.append(level)
            t += 1.0 / fps
    return times, act


def test_two_bursts_separated_by_a_long_gap_are_two_segments():
    times, act = _burst_series(60, [(3.0, 1.0), (3.0, 0.0), (3.0, 1.0)])
    out = rallies.segments_from_activity(times, act)
    assert len(out) == 2


def test_a_gap_shorter_than_gap_sec_is_closed():
    times, act = _burst_series(60, [(3.0, 1.0), (0.5, 0.0), (3.0, 1.0)])
    out = rallies.segments_from_activity(times, act, gap_sec=1.0)
    assert len(out) == 1


def test_a_burst_shorter_than_min_len_sec_is_dropped():
    times, act = _burst_series(60, [(1.0, 1.0), (5.0, 0.0)])
    out = rallies.segments_from_activity(times, act, min_len_sec=2.0)
    assert out == []


@pytest.mark.parametrize("fps", [30, 60])
def test_boundaries_are_fps_invariant_in_seconds(fps):
    times, act = _burst_series(fps, [(1.0, 0.0), (4.0, 1.0), (3.0, 0.0), (4.0, 1.0)])
    out = rallies.segments_from_activity(times, act)
    assert len(out) == 2
    assert out[0][0] == pytest.approx(1.0, abs=1.5 / fps)
    assert out[0][1] == pytest.approx(5.0, abs=1.5 / fps)


def test_flat_activity_yields_no_segments():
    times, act = _burst_series(60, [(10.0, 0.0)])
    assert rallies.segments_from_activity(times, act) == []


def test_smooth_is_a_trailing_mean():
    assert rallies.smooth([0.0, 0.0, 3.0, 3.0], 2) == pytest.approx([0.0, 0.0, 1.5, 3.0])


def test_gap_in_times_sequence_creates_separate_segments():
    """Amendment R2: times with a gap (e.g., 0.0-2.0s then 10.0-12.0s) should split
    into separate segments using actual time differences, not indices."""
    times = [0.0, 1.0, 2.0, 10.0, 11.0, 12.0]
    activity = [1.0, 1.0, 1.0, 1.0, 1.0, 1.0]
    # gap_sec=1.0 (default), so the 8-second gap between times[2] and times[3]
    # should split them into two segments
    out = rallies.segments_from_activity(times, activity, gap_sec=1.0)
    assert len(out) == 2
    # First segment: from times[0] to times[2]
    assert out[0][0] == 0.0
    assert out[0][1] == 2.0
    # Second segment: from times[3] to times[5]
    assert out[1][0] == 10.0
    assert out[1][1] == 12.0


# --- Task 5: the swing signal -------------------------------------------------


def test_baseline_caps_reproduce_the_measured_dji_constants():
    """§0.16's FAR_CAP_PX=21.3 / NEAR_CAP_PX=120.9 are 12 m/s at the far/near
    baseline scale (106.3 / 603.8 px/m) divided by fps."""
    far, near = rallies.baseline_caps(DJI_QUAD, DJI_FPS)
    assert far == pytest.approx(21.3, abs=0.5)
    assert near == pytest.approx(120.9, abs=0.5)
    assert far < near


def test_baseline_caps_scale_inversely_with_fps():
    far60, near60 = rallies.baseline_caps(DJI_QUAD, 60.0)
    far30, near30 = rallies.baseline_caps(DJI_QUAD, 30.0)
    assert far30 == pytest.approx(2 * far60)
    assert near30 == pytest.approx(2 * near60)


def test_baseline_caps_reject_a_degenerate_quad_and_bad_fps():
    with pytest.raises(ValueError):
        rallies.baseline_caps([[0, 0], [1, 0], [1, 0], [0, 0]], 60.0)
    with pytest.raises(ValueError):
        rallies.baseline_caps(DJI_QUAD, 0.0)


def test_wrists_from_hands_is_the_mean_of_whichever_hands_are_present():
    assert rallies.wrists_from_hands(None, None) is None
    assert rallies.wrists_from_hands((2.0, 4.0), None) == (2.0, 4.0)
    assert rallies.wrists_from_hands(None, [6.0, 8.0]) == (6.0, 8.0)
    assert rallies.wrists_from_hands((2.0, 4.0), [6.0, 10.0]) == (4.0, 7.0)


@pytest.mark.parametrize("bad", [(float("nan"), 1.0), (1.0, float("inf")),
                                 (1.0,), (1.0, 2.0, 3.0), "ab", (None, 1.0), 5])
def test_wrists_from_hands_treats_a_malformed_point_as_absent(bad):
    assert rallies.wrists_from_hands(bad, (3.0, 5.0)) == (3.0, 5.0)
    assert rallies.wrists_from_hands(bad, None) is None


def test_speed_rejects_deltas_above_the_cap():
    pts = [(0.0, 0.0), (5.0, 0.0), (500.0, 0.0), (505.0, 0.0)]
    out = rallies.side_speed(pts, cap_px=50.0)
    assert out[0] == 0.0            # no predecessor
    assert out[1] == pytest.approx(5.0)
    assert out[2] == 0.0            # 495 px jump: an identity switch, not motion
    assert out[3] == pytest.approx(5.0)


def test_speed_treats_missing_points_as_zero_not_as_a_jump():
    pts = [(0.0, 0.0), None, (100.0, 0.0)]
    assert rallies.side_speed(pts, cap_px=1000.0) == [0.0, 0.0, 0.0]


def _rec(frame, lower=None, upper=None):
    return {"frame": frame, "wrist_lower": lower, "wrist_upper": upper}


def test_swing_activity_times_come_from_frame_over_fps():
    track = [_rec(10), _rec(12), _rec(14)]
    times, act = rallies.swing_activity(track, 30.0, (50.0, 50.0))
    assert times == [10 / 30.0, 12 / 30.0, 14 / 30.0]
    assert len(act) == 3


def test_swing_activity_takes_the_max_across_sides():
    """The pinned combining rule (§0.15 Defect 1): max of the two sides' speeds."""
    track = [_rec(0, (0.0, 500.0), (0.0, 10.0)),
             _rec(1, (0.0, 500.0), (3.0, 10.0)),
             _rec(2, (9.0, 500.0), (3.0, 10.0))]
    _times, act = rallies.swing_activity(track, 1.0, (50.0, 50.0))
    # SMOOTH_SEC*fps rounds to 1 sample here, so the smoothing is a no-op.
    assert act == [0.0, 3.0, 9.0]


def test_each_side_gets_its_own_cap_not_a_shared_one():
    """A 30 px step is an identity switch for the far (upper) player, whose cap
    is 21.3, but ordinary motion for the near (lower) one, whose cap is 120.9."""
    caps = (21.3, 120.9)
    far_only = [_rec(0, upper=(0.0, 0.0)), _rec(1, upper=(30.0, 0.0))]
    near_only = [_rec(0, lower=(0.0, 0.0)), _rec(1, lower=(30.0, 0.0))]
    assert rallies.swing_activity(far_only, 1.0, caps)[1] == [0.0, 0.0]
    assert rallies.swing_activity(near_only, 1.0, caps)[1] == [0.0, 30.0]


def test_swing_activity_without_caps_falls_back_to_the_absolute_cap():
    track = [_rec(0, (0.0, 0.0)), _rec(1, (50.0, 0.0)), _rec(2, (500.0, 0.0))]
    _t, act = rallies.swing_activity(track, 1.0, None)
    assert act == [0.0, 50.0, 0.0]      # 450 px > TELEPORT_MIN_PX


def test_swing_activity_of_an_empty_track_is_empty():
    assert rallies.swing_activity([], 30.0, (1.0, 1.0)) == ([], [])


def _load_script():
    spec = importlib.util.spec_from_file_location(
        "score_rally_labels",
        pathlib.Path(__file__).resolve().parents[1] / "scripts" / "score_rally_labels.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _random_hand(rng, prev):
    """A hand point that mostly drifts, sometimes teleports, sometimes vanishes."""
    r = rng.random()
    if r < 0.15:
        return None
    if r < 0.20 or prev is None:
        return [rng.uniform(0, 400), rng.uniform(0, 400)]
    return [prev[0] + rng.uniform(-8, 8), prev[1] + rng.uniform(-8, 8)]


def test_swing_activity_equals_the_scripts_load_activity(tmp_path, monkeypatch):
    """Task 6 must reproduce §0.16 with this code: on the same detections the
    library signal is bit-identical to scripts/score_rally_labels.py."""
    script = _load_script()
    rng = random.Random(1234)
    fps = script.FPS
    lines, track = [], []
    prev = {}
    for frame in range(1, 401):
        players, wrists = {}, {}
        for side in ("upper", "lower"):
            hands = {}
            for hand in ("left", "right"):
                pt = _random_hand(rng, prev.get((side, hand)))
                prev[(side, hand)] = pt
                hands[hand] = pt
            players[side] = {"hands": hands}
            wrists[side] = rallies.wrists_from_hands(hands["left"], hands["right"])
        lines.append(json.dumps({"time_sec": frame / fps, "players": players}))
        track.append({"frame": frame, "wrist_lower": wrists["lower"],
                      "wrist_upper": wrists["upper"]})
    det = tmp_path / "detections.jsonl"
    det.write_text("\n".join(lines) + "\n", encoding="utf-8")
    monkeypatch.setattr(script, "DET", det)

    times_ref, act_ref = script.load_activity()
    times, act = rallies.swing_activity(
        track, fps, (script.FAR_CAP_PX, script.NEAR_CAP_PX))

    assert any(v > 0 for v in act_ref)
    assert times == times_ref
    assert act == act_ref


def test_segments_ignore_non_finite_activity():
    """A NaN/inf sample is inactive and must not poison the p99.5 threshold."""
    times, act = _burst_series(60, [(3.0, 0.0), (4.0, 1.0), (3.0, 0.0)])
    clean = rallies.segments_from_activity(times, act)
    act[10] = float("nan")
    act[30] = float("-inf")
    for i in range(20, 25):         # enough +inf to sit at the p99.5 rank
        act[i] = float("inf")
    assert rallies.segments_from_activity(times, act) == clean
    assert rallies.segments_from_activity(times, [float("nan")] * len(times)) == []


# --- Task 5: the per-run rally track ------------------------------------------

UPPER_C, LOWER_C = (300.0, 400.0), (900.0, 1500.0)


def _bare_tracker_system(analyze_technique=False):
    s = object.__new__(sysmod.BadmintonAnalysisSystem)
    s.analyze_technique = analyze_technique
    s._rally_track = []
    return s


def test_rally_track_record_rebuilds_hands_the_way_detections_jsonl_has_them():
    s = _bare_tracker_system()
    left = {UPPER_C[1]: (10, 20), LOWER_C[1]: (100, 200)}
    right = {LOWER_C[1]: (300, 600)}
    rec = s._rally_track_record(
        7, [UPPER_C, LOWER_C], {"upper": UPPER_C, "lower": LOWER_C}, left, right,
        [55.0, 66.0])
    assert rec == {
        "frame": 7, "shuttle": (55.0, 66.0),
        "wrist_upper": (10.0, 20.0), "pos_upper": UPPER_C,     # one hand -> that hand
        "wrist_lower": (200.0, 400.0), "pos_lower": LOWER_C,   # two hands -> their mean
    }


def test_rally_track_record_gives_an_unselected_stale_side_no_wrist_or_pos():
    s = _bare_tracker_system()
    # The tracker still reports the upper side's last-known centroid, but it
    # was not among this frame's candidates, so it was not selected.
    rec = s._rally_track_record(
        3, [LOWER_C], {"upper": UPPER_C, "lower": LOWER_C},
        {UPPER_C[1]: (10, 20)}, {}, None)
    assert rec["wrist_upper"] is None and rec["pos_upper"] is None
    assert rec["pos_lower"] == LOWER_C
    assert rec["wrist_lower"] is None       # selected, but no hands detected
    assert rec["shuttle"] is None


def test_rally_track_record_handles_missing_sides_and_a_zero_ball():
    s = _bare_tracker_system()
    rec = s._rally_track_record(1, [], {"upper": None, "lower": None}, {}, {}, [0, 0])
    assert rec == {"frame": 1, "shuttle": None, "wrist_lower": None,
                   "wrist_upper": None, "pos_lower": None, "pos_upper": None}


@pytest.mark.parametrize("analyze_technique", [False, True])
def test_record_rally_frame_appends_regardless_of_analyze_technique(analyze_technique):
    s = _bare_tracker_system(analyze_technique)
    s._record_rally_frame(4, [LOWER_C], {"lower": LOWER_C}, {}, {LOWER_C[1]: (1, 2)}, None)
    assert len(s._rally_track) == 1
    assert s._rally_track[0]["wrist_lower"] == (1.0, 2.0)


def test_process_frame_records_the_rally_track_outside_the_technique_branch():
    """Wiring guard: _process_frame reaches _record_rally_frame after the tracker
    update and before, not inside, the ``if self.analyze_technique`` block."""
    src = inspect.getsource(sysmod.BadmintonAnalysisSystem._process_frame)
    call = src.index("self._record_rally_frame(")
    gate = src.index("if self.analyze_technique:")
    assert src.index("self.player_tracker.update(") < call < gate


def test_init_starts_an_empty_rally_track():
    src = inspect.getsource(sysmod.BadmintonAnalysisSystem.__init__)
    assert "self._rally_track = []" in src


# --- Task 5 fix round 1 -------------------------------------------------------


def test_a_strided_track_is_scaled_to_per_frame_speed_and_window():
    """Fast mode records every 3rd frame: a 30 px step per record is 10 px per
    frame, under the 21.3 far cap, and the 0.5 s window is round(0.5*fps/3)."""
    fps = 60.0
    track = [_rec(3 * (i + 1), upper=(30.0 * i, 0.0)) for i in range(40)]
    times, act = rallies.swing_activity(track, fps, (21.3, 120.9))
    assert times == [3 * (i + 1) / fps for i in range(40)]
    raw = [0.0] + [10.0] * 39
    assert act == rallies.smooth(raw, round(0.5 * fps / 3))     # window 10
    assert act[1] == pytest.approx(5.0)     # not zeroed as a 30 px "teleport"
    assert act[-1] == pytest.approx(10.0)


def test_track_stride_is_the_median_frame_delta():
    assert rallies.track_stride([]) == 1
    assert rallies.track_stride([_rec(5)]) == 1
    assert rallies.track_stride([_rec(f) for f in (1, 2, 3, 4)]) == 1
    assert rallies.track_stride([_rec(f) for f in (3, 6, 9, 12)]) == 3
    # One dropped stretch does not change the regular stride.
    assert rallies.track_stride([_rec(f) for f in (3, 6, 9, 30, 33, 36, 39)]) == 3


def test_a_stride_one_track_with_gaps_is_still_identical_to_the_script(tmp_path, monkeypatch):
    """Irregular gaps keep the script's semantics (records treated as adjacent)."""
    script = _load_script()
    rng = random.Random(99)
    fps = script.FPS
    dropped = set(range(100, 106)) | {250, 251} | set(range(300, 320))
    lines, track, prev = [], [], {}
    for frame in range(1, 401):
        if frame in dropped:
            continue
        players, wrists = {}, {}
        for side in ("upper", "lower"):
            hands = {}
            for hand in ("left", "right"):
                pt = _random_hand(rng, prev.get((side, hand)))
                prev[(side, hand)] = pt
                hands[hand] = pt
            players[side] = {"hands": hands}
            wrists[side] = rallies.wrists_from_hands(hands["left"], hands["right"])
        lines.append(json.dumps({"time_sec": frame / fps, "players": players}))
        track.append({"frame": frame, "wrist_lower": wrists["lower"],
                      "wrist_upper": wrists["upper"]})
    det = tmp_path / "detections.jsonl"
    det.write_text("\n".join(lines) + "\n", encoding="utf-8")
    monkeypatch.setattr(script, "DET", det)

    assert rallies.track_stride(track) == 1
    times_ref, act_ref = script.load_activity()
    times, act = rallies.swing_activity(
        track, fps, (script.FAR_CAP_PX, script.NEAR_CAP_PX))
    assert any(v > 0 for v in act_ref)
    assert times == times_ref
    assert act == act_ref


def test_a_raising_recorder_never_propagates_and_logs_once(capsys):
    s = _bare_tracker_system()

    def boom(*_a, **_k):
        raise RuntimeError("bad record")
    s._rally_track_record = boom
    for frame in range(1, 6):
        s._record_rally_frame(frame, [], {}, {}, {}, None)     # must not raise
    assert s._rally_track == []
    out = capsys.readouterr().out
    assert out.count("bad record") == 1


@pytest.mark.parametrize("fps", [float("nan"), float("inf"), float("-inf"), 0.0, -30.0, None])
def test_baseline_caps_reject_a_non_finite_or_non_positive_fps(fps):
    with pytest.raises(ValueError):
        rallies.baseline_caps(DJI_QUAD, fps)


def test_baseline_caps_reject_a_quad_with_a_non_finite_coordinate():
    for bad in (float("nan"), float("inf")):
        quad = [list(p) for p in DJI_QUAD]
        quad[2][1] = bad
        with pytest.raises(ValueError):
            rallies.baseline_caps(quad, DJI_FPS)
