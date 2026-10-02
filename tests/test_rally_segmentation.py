"""C2: rally segmentation from a recorded per-frame track.

Boundaries are defined in SECONDS throughout, so the same temporal pattern
at 30 and 60 fps must produce the same answer (design spec done-means 8).
"""
import inspect
import json
import math
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


# --- Frozen reference for the swing signal ------------------------------------
# This is the speed / teleport-cap / smoothing arithmetic of
# scripts/score_rally_labels.py::load_activity exactly as of commit d280bec: the
# version B11 spec §0.16's F1 0.644 was measured with. It is copied here, and
# frozen, so rallies.swing_activity cannot drift from the measured signal
# unnoticed. The script itself now calls the library, so it can no longer serve
# as the independent oracle. Do NOT "simplify" or re-derive this from rallies.*.
FROZEN_FAR_CAP_PX = 21.3
FROZEN_NEAR_CAP_PX = 120.9
FROZEN_SMOOTH_SEC = 0.5


def _frozen_script_load_activity(records, fps):
    """(times, smoothed activity) from parsed detections.jsonl records (dicts)."""
    t, up, lo = [], [], []
    for d in records:
        pl = d.get("players") or {}

        def wrist(side):
            rec = pl.get(side) or {}
            hands = rec.get("hands") or {}
            pts = [p for p in (hands.get("left"), hands.get("right")) if p]
            if not pts:
                return None
            return (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))

        t.append(d["time_sec"])
        up.append(wrist("upper"))
        lo.append(wrist("lower"))
    n = len(t)

    def speed(seq, cap):
        out = [0.0] * n
        for i in range(1, n):
            a, b = seq[i - 1], seq[i]
            if a and b:
                v = math.hypot(b[0] - a[0], b[1] - a[1])
                if v <= cap:
                    out[i] = v
        return out

    act = [max(a, b) for a, b in zip(speed(lo, FROZEN_NEAR_CAP_PX),
                                     speed(up, FROZEN_FAR_CAP_PX))]
    w = max(1, int(round(FROZEN_SMOOTH_SEC * fps)))
    sm, run = [], 0.0
    for i, v in enumerate(act):
        run += v
        if i >= w:
            run -= act[i - w]
        sm.append(run / min(i + 1, w))
    return t, sm


def _random_hand(rng, prev):
    """A hand point that mostly drifts, sometimes teleports, sometimes vanishes."""
    r = rng.random()
    if r < 0.15:
        return None
    if r < 0.20 or prev is None:
        return [rng.uniform(0, 400), rng.uniform(0, 400)]
    return [prev[0] + rng.uniform(-8, 8), prev[1] + rng.uniform(-8, 8)]


def test_swing_activity_equals_the_frozen_script_signal():
    """Task 6 must reproduce §0.16 with this code: on the same detections the
    library signal is bit-identical to the frozen script arithmetic."""
    rng = random.Random(1234)
    fps = 59.94  # the script's FPS constant
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
    times_ref, act_ref = _frozen_script_load_activity(
        [json.loads(line) for line in lines], fps)
    times, act = rallies.swing_activity(
        track, fps, (FROZEN_FAR_CAP_PX, FROZEN_NEAR_CAP_PX))

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


def test_a_stride_one_track_with_gaps_is_still_identical_to_the_frozen_script():
    """Irregular gaps keep the script's semantics (records treated as adjacent)."""
    rng = random.Random(99)
    fps = 59.94  # the script's FPS constant
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
    assert rallies.track_stride(track) == 1
    times_ref, act_ref = _frozen_script_load_activity(
        [json.loads(line) for line in lines], fps)
    times, act = rallies.swing_activity(
        track, fps, (FROZEN_FAR_CAP_PX, FROZEN_NEAR_CAP_PX))
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


# --- Task 7: shuttle signal, artifact suppression, signal selection -----------------

def _track(shuttles):
    return [{"frame": i, "shuttle": s, "wrist_lower": None, "wrist_upper": None}
            for i, s in enumerate(shuttles)]


def _kept(track):
    return sum(1 for r in track if r["shuttle"] is not None)


def _jittered_cluster(rng, cx, cy, n, radius=25.0):
    """n points uniformly inside a ``radius`` px disc around (cx, cy)."""
    pts = []
    while len(pts) < n:
        dx, dy = rng.uniform(-radius, radius), rng.uniform(-radius, radius)
        if math.hypot(dx, dy) <= radius:
            pts.append((cx + dx, cy + dy))
    return pts


def test_static_fixture_is_suppressed_and_reported():
    """86% of the ball model's output on the owner's footage is one fixture (§0.7)."""
    fixture = [(473.0, 846.0)] * 900
    real = [(100.0 + 8 * i, 200.0 + 5 * i) for i in range(10)]
    cleaned, suppressed = rallies.suppress_static(_track(fixture + real))
    assert suppressed["count"] == 900
    assert len(suppressed["cells"]) == 1
    assert suppressed["cells"][0]["count"] == 900
    assert suppressed["cells"][0]["point"] == pytest.approx([473.0, 846.0])
    assert _kept(cleaned) == 10


def test_a_straight_moving_flight_is_not_suppressed():
    moving = [(10.0 * i, 5.0 * i) for i in range(100)]
    cleaned, suppressed = rallies.suppress_static(_track(moving))
    assert suppressed == {"count": 0, "cells": []}
    assert _kept(cleaned) == 100


def test_jittered_fixture_straddling_a_cell_edge_is_suppressed():
    """The real fixture is a ~25 px jittered cluster centred 4 px from a grid edge
    (473 -> edge at 450/500, 846 -> edge at 850), so it spans several cells and a
    single stray point must not defeat the test (R11)."""
    rng = random.Random(7)
    fixture = _jittered_cluster(rng, 473.0, 846.0, 400)
    outliers = [(1500.0, 300.0), (2900.0, 1700.0), (60.0, 1900.0),
                (2000.0, 1000.0), (3300.0, 400.0)]
    pts = fixture + outliers
    rng.shuffle(pts)
    # sanity: the cluster really does straddle cell edges
    cells = {(int(x // rallies.STATIC_GRID_PX), int(y // rallies.STATIC_GRID_PX))
             for x, y in fixture}
    assert len(cells) >= 2
    cleaned, suppressed = rallies.suppress_static(_track(pts))
    assert suppressed["count"] == 400
    assert len(suppressed["cells"]) == 1
    assert suppressed["cells"][0]["count"] == 400
    x, y = suppressed["cells"][0]["point"]
    assert math.hypot(x - 473.0, y - 846.0) < 3.0
    assert _kept(cleaned) == 5
    assert {r["shuttle"] for r in cleaned if r["shuttle"] is not None} == set(outliers)


def test_serve_hold_in_a_short_clip_is_not_a_fixture():
    """A server holding the shuttle still for ~0.7 s is a real shuttle."""
    rng = random.Random(5)
    hold = _jittered_cluster(rng, 600.0, 700.0, 40, radius=3.0)
    flight = [(700.0 + 12.0 * i, 650.0 - 4.0 * i) for i in range(90)]
    cleaned, suppressed = rallies.suppress_static(_track(hold + flight))
    assert suppressed == {"count": 0, "cells": []}
    assert _kept(cleaned) == 130


def test_apex_hover_is_not_a_fixture():
    """A shuttle hanging near the top of a lob stays inside ~20 px briefly."""
    rng = random.Random(6)
    hover = _jittered_cluster(rng, 1200.0, 300.0, 30, radius=20.0)
    flight = [(200.0 + 9.0 * i, 900.0 - 3.0 * i) for i in range(70)]
    cleaned, suppressed = rallies.suppress_static(_track(flight[:35] + hover + flight[35:]))
    assert suppressed == {"count": 0, "cells": []}
    assert _kept(cleaned) == 100


def test_tiny_track_cluster_is_not_suppressed():
    """3 of 10 detections are 30% of the track, but 3 points are not a fixture."""
    pts = [(500.0, 500.0), (501.0, 500.0), (500.0, 502.0)] + \
        [(100.0 + 90.0 * i, 40.0 + 60.0 * i) for i in range(7)]
    cleaned, suppressed = rallies.suppress_static(_track(pts))
    assert suppressed == {"count": 0, "cells": []}
    assert _kept(cleaned) == 10


def test_the_absolute_floor_is_what_protects_a_small_cluster():
    assert rallies.STATIC_MIN_POINTS == 300
    floor = rallies.STATIC_MIN_POINTS
    for n, expect in ((floor - 1, 0), (floor, floor)):
        _, suppressed = rallies.suppress_static(_track([(473.0, 846.0)] * n))
        assert suppressed["count"] == expect


def test_a_slow_shuttle_through_the_fixture_area_is_not_suppressed():
    """Enough points to pass the count test in one merged block, but they travel
    ~200 px, so the robust spread rejects them."""
    slow = [(373.0 + 1.0 * i, 846.0 + 0.2 * i) for i in range(200)]
    cleaned, suppressed = rallies.suppress_static(_track(slow))
    assert suppressed == {"count": 0, "cells": []}
    assert _kept(cleaned) == 200


def test_a_shuttle_passing_the_fixture_keeps_its_far_points():
    """Fixture suppressed; the flight that crosses it keeps every point outside
    the fixture's own radius."""
    rng = random.Random(3)
    fixture = _jittered_cluster(rng, 473.0, 846.0, 600)
    flight = [(200.0 + 10.0 * i, 700.0 + 3.0 * i) for i in range(60)]
    cleaned, suppressed = rallies.suppress_static(_track(fixture + flight))
    assert len(suppressed["cells"]) == 1
    far = [p for p in flight if math.hypot(p[0] - 473.0, p[1] - 846.0) > 40.0]
    kept = {r["shuttle"] for r in cleaned if r["shuttle"] is not None}
    assert set(far) <= kept


def test_suppression_never_mutates_the_input_and_keeps_other_fields():
    track = _track([(473.0, 846.0)] * 400 + [(10.0 * i, 3.0 * i) for i in range(10)])
    for rec in track:
        rec["pos_lower"] = (1.0, 2.0)
    before = [dict(r) for r in track]
    cleaned, _ = rallies.suppress_static(track)
    assert track == before
    assert all(r["pos_lower"] == (1.0, 2.0) for r in cleaned)
    assert [r["frame"] for r in cleaned] == [r["frame"] for r in track]


def test_suppression_of_an_empty_or_shuttle_free_track():
    assert rallies.suppress_static([]) == ([], {"count": 0, "cells": []})
    cleaned, suppressed = rallies.suppress_static(_track([None] * 5))
    assert suppressed == {"count": 0, "cells": []}
    assert _kept(cleaned) == 0


def test_suppression_summary_is_json_friendly_and_rounded():
    rng = random.Random(11)
    fixture = _jittered_cluster(rng, 473.0, 846.0, 400)
    _, suppressed = rallies.suppress_static(_track(fixture))
    assert json.loads(json.dumps(suppressed)) == suppressed
    for v in suppressed["cells"][0]["point"]:
        assert isinstance(v, float) and v == round(v, 1)


def test_dense_track_selects_the_shuttle_signal():
    dense = [(10.0 * i % 900, 7.0 * i % 700) for i in range(100)]
    name, reason = rallies.choose_signal(_track(dense))
    assert name == "shuttle"
    assert "density" in reason


def test_sparse_track_falls_back_to_swing():
    sparse = [None] * 95 + [(10.0 * i, 7.0 * i) for i in range(5)]
    track = _track(sparse)
    for i, rec in enumerate(track):          # give the swing signal something to see
        rec["wrist_lower"] = (float(i), 500.0)
    name, reason = rallies.choose_signal(track)
    assert name == "swing"
    assert "0.50" in reason


def test_neither_signal_available_selects_none():
    name, reason = rallies.choose_signal(_track([None] * 100))
    assert name == "none"
    assert reason


def test_a_pinned_signal_is_honoured():
    name, reason = rallies.choose_signal(_track([None] * 10), signal="swing")
    assert name == "swing"
    assert "pinned" in reason


def test_density_is_computed_after_suppression():
    """A track that is 90% one fixture is sparse, not dense."""
    fixture = [(473.0, 846.0)] * 900
    real = [(100.0 + 8 * i, 200.0 + 5 * i) for i in range(100)]
    cleaned, _ = rallies.suppress_static(_track(fixture + real))
    assert rallies.shuttle_density(cleaned) == pytest.approx(0.10)


def test_density_of_an_empty_track_is_zero():
    assert rallies.shuttle_density([]) == 0.0


# --- court-volume gating (design spec §5: "gate the shuttle before segmentation") ---

_QUAD = [(100.0, 200.0), (900.0, 200.0), (1000.0, 800.0), (0.0, 800.0)]  # TL, TR, BR, BL


def test_points_outside_the_court_are_dropped():
    inside = (500.0, 500.0)
    outside = (500.0, 1500.0)          # well below the near baseline
    gated, dropped = rallies.gate_to_court_volume(
        _track([inside, outside, inside]), _QUAD)
    assert dropped == 1
    assert [r["shuttle"] for r in gated] == [inside, None, inside]


def test_airborne_shuttles_above_the_far_baseline_are_kept():
    """The top edge is raised, or every clear and lob would be gated away."""
    airborne = (500.0, 40.0)           # above the quad's far baseline (y=200)
    gated, dropped = rallies.gate_to_court_volume(_track([airborne]), _QUAD)
    assert dropped == 0
    assert gated[0]["shuttle"] == airborne


def test_court_volume_lift_is_0_9_of_court_depth():
    """§0.7 / §5: the volume's top edge sits 0.9 x court depth above the far baseline."""
    assert rallies.COURT_VOLUME_RAISE_FRAC == 0.9
    depth = 800.0 - 200.0
    top = 200.0 - 0.9 * depth                      # -340
    just_in, just_out = (500.0, top + 2.0), (500.0, top - 2.0)
    gated, dropped = rallies.gate_to_court_volume(_track([just_in, just_out]), _QUAD)
    assert dropped == 1
    assert [r["shuttle"] for r in gated] == [just_in, None]


def test_an_explicit_raise_frac_overrides_the_default():
    airborne = (500.0, 40.0)
    gated, dropped = rallies.gate_to_court_volume(_track([airborne]), _QUAD, raise_frac=0.0)
    assert dropped == 1 and gated[0]["shuttle"] is None


def test_gating_keeps_other_fields_and_does_not_mutate():
    track = _track([(500.0, 1500.0)])
    track[0]["pos_upper"] = (3.0, 4.0)
    gated, _ = rallies.gate_to_court_volume(track, _QUAD)
    assert track[0]["shuttle"] == (500.0, 1500.0)
    assert gated[0]["pos_upper"] == (3.0, 4.0)


def test_no_quad_means_no_gating():
    """A run without court corners must not silently discard every detection."""
    pts = [(9999.0, 9999.0)] * 5
    gated, dropped = rallies.gate_to_court_volume(_track(pts), None)
    assert dropped == 0
    assert all(r["shuttle"] is not None for r in gated)


# --- Task 8: segment_rallies -- the public API, provenance, the degraded path -----

FPS = 60.0


def _swing_track(frames, active):
    """Records for ``frames``; the lower wrist advances 3 px per active record."""
    x, out = 0.0, []
    for f in frames:
        if active(f):
            x += 3.0
        out.append({"frame": f, "shuttle": None,
                    "wrist_lower": (x, 500.0), "wrist_upper": None})
    return out


def _two_bursts(f):
    return 300 <= f < 600 or 900 <= f < 1200


def _shuttle_track(n, present):
    """A moving shuttle (never static) inside _QUAD's play volume where ``present``."""
    return [{"frame": i,
             "shuttle": (200.0 + (3 * i) % 500, 300.0 + (2 * i) % 400) if present(i) else None,
             "wrist_lower": None, "wrist_upper": None} for i in range(n)]


def _seg_frames(segments):
    return [(s["start_frame"], s["end_frame"]) for s in segments]


def test_no_signal_emits_degraded_coarse_windows_not_zero_rallies():
    """Owner decision 12a-C: never an empty result, never one whole-video rally."""
    track = _track([None] * 1800)          # 30 s at 60 fps, nothing to see
    segments, prov = rallies.segment_rallies(track, fps=FPS)
    assert prov["signal"] == "none"
    assert prov["attempted_signal"] is None
    assert prov["degraded"] is True
    assert len(segments) == 3              # 30 s / DEGRADED_WINDOW_SEC
    assert all(s["degraded"] for s in segments)
    assert segments[0]["start_frame"] == 0
    assert segments[-1]["end_frame"] == 1799
    assert not (len(segments) == 1 and segments[0]["end_frame"] == 1799)


def test_a_real_signal_produces_non_degraded_segments():
    segments, prov = rallies.segment_rallies(
        _swing_track(range(1800), _two_bursts), fps=FPS)
    assert prov["signal"] == "swing"
    assert prov["attempted_signal"] == "swing"
    assert prov["degraded"] is False
    assert len(segments) == 2
    assert all(not s["degraded"] for s in segments)
    assert [s["id"] for s in segments] == [1, 2]
    assert prov["caps_source"] == "fallback_100px"      # no quad was given


def test_segment_dicts_have_the_documented_keys_and_frame_based_seconds():
    segments, _ = rallies.segment_rallies(
        _swing_track(range(1800), _two_bursts), fps=FPS)
    for s in segments:
        assert set(s) == {"id", "start_frame", "end_frame", "start_sec", "end_sec",
                          "degraded"}
        assert s["start_sec"] == s["start_frame"] / FPS
        assert s["end_sec"] == s["end_frame"] / FPS
        assert isinstance(s["start_frame"], int) and isinstance(s["end_frame"], int)


def test_provenance_states_signal_reason_and_params():
    segments, prov = rallies.segment_rallies(_track([None] * 600), fps=FPS)
    assert set(prov) >= {"signal", "attempted_signal", "reason", "degraded", "params",
                         "suppressed_static_shuttle", "gated_outside_court",
                         "shuttle_density", "caps_source"}
    assert set(prov["params"]) == {"gap_sec", "min_len_sec", "swing_frac",
                                   "degraded_window_sec", "stride"}
    assert prov["params"]["gap_sec"] == 1.0
    assert prov["params"]["min_len_sec"] == 2.0
    assert prov["params"]["swing_frac"] == 0.25
    assert prov["params"]["degraded_window_sec"] == rallies.DEGRADED_WINDOW_SEC
    assert prov["params"]["stride"] == 1
    assert prov["reason"]


def test_out_of_court_detections_are_reported_not_silently_dropped():
    """A dead detector must look dead in the provenance, not just in the count."""
    # Moving, so suppress_static does not eat them first -- the point of this
    # test is the court gate, not the fixture filter.
    below_court = [(50.0, 1500.0 + 2.0 * i) for i in range(200)]
    _segments, prov = rallies.segment_rallies(_track(below_court), fps=FPS, quad=_QUAD)
    assert prov["gated_outside_court"] > 0
    assert prov["signal"] == "none"


def test_suppressed_fixture_appears_in_provenance():
    fixture = [(473.0, 846.0)] * 900
    _segments, prov = rallies.segment_rallies(_track(fixture + [None] * 100), fps=FPS)
    assert prov["suppressed_static_shuttle"]["count"] == 900
    assert prov["suppressed_static_shuttle"]["cells"][0]["count"] == 900


def test_empty_track_is_not_an_error():
    segments, prov = rallies.segment_rallies([], fps=FPS)
    assert segments == []
    assert prov["signal"] == "none"
    assert prov["degraded"] is True
    assert rallies.segment_rallies(None, fps=FPS)[0] == []


def test_density_is_measured_after_suppression_and_court_gating():
    """40 in-court + 60 below-court detections: raw density 1.0, gated 0.4."""
    inside = [(200.0 + 7.0 * i, 300.0 + 3.0 * i) for i in range(40)]
    outside = [(50.0 + 3.0 * i, 1500.0 + 2.0 * i) for i in range(60)]
    _segments, prov = rallies.segment_rallies(
        _track(inside + outside), fps=FPS, quad=_QUAD)
    assert prov["gated_outside_court"] == 60
    assert prov["shuttle_density"] == pytest.approx(0.4)
    assert prov["signal"] == "none"          # 0.4 < SHUTTLE_DENSITY_MIN, no wrists


def test_pinned_swing_equals_calling_the_swing_pipeline_directly():
    """PM2/R20: a pinned swing run is exactly swing_activity + segments_from_activity."""
    track = _swing_track(range(1800), _two_bursts)
    segments, prov = rallies.segment_rallies(track, FPS, signal="swing", quad=_QUAD)
    caps = rallies.baseline_caps(_QUAD, FPS)
    times, act = rallies.swing_activity(track, FPS, caps)
    spans = rallies.segments_from_activity(times, act)
    expected = [(int(round(a * FPS)), int(round(b * FPS))) for a, b in spans]
    assert len(expected) == 2
    assert _seg_frames(segments) == expected
    assert prov["signal"] == "swing"
    assert prov["caps_source"] == "quad"
    assert "pinned" in prov["reason"]


def test_pinned_shuttle_equals_the_presence_mask_segmented_directly():
    track = _shuttle_track(1800, lambda i: 100 <= i < 700 or 900 <= i < 1500)
    segments, prov = rallies.segment_rallies(track, FPS, signal="shuttle", quad=_QUAD)
    times = [rec["frame"] / FPS for rec in track]
    act = [1.0 if rec["shuttle"] is not None else 0.0 for rec in track]
    spans = rallies.segments_from_activity(times, act)
    expected = [(int(round(a * FPS)), int(round(b * FPS))) for a, b in spans]
    assert expected == [(100, 699), (900, 1499)]
    assert _seg_frames(segments) == expected
    assert prov["signal"] == "shuttle" and prov["degraded"] is False
    # Auto selection reaches the same answer on this dense track.
    auto_segments, auto_prov = rallies.segment_rallies(track, FPS, quad=_QUAD)
    assert auto_prov["signal"] == "shuttle"
    assert auto_prov["shuttle_density"] == pytest.approx(1200 / 1800)
    assert _seg_frames(auto_segments) == expected


# --- R2: seconds are frame / fps, never list-index based ------------------------------


def test_a_gapped_track_keeps_frame_time_for_real_and_degraded_segments():
    frames = list(range(0, 600)) + list(range(3000, 3600))

    def burst(f):
        return 100 <= f < 500 or 3100 <= f < 3500

    segments, prov = rallies.segment_rallies(_swing_track(frames, burst), FPS)
    assert prov["signal"] == "swing" and prov["degraded"] is False
    assert len(segments) == 2
    for s in segments:
        assert s["start_sec"] == s["start_frame"] / FPS
        assert s["end_sec"] == s["end_frame"] / FPS
        assert s["end_frame"] < 600 or s["start_frame"] >= 3000   # never across the gap
    assert segments[1]["start_sec"] > 50.0         # an index-based time would be < 20 s

    empty = [{"frame": f, "shuttle": None, "wrist_lower": None, "wrist_upper": None}
             for f in frames]
    windows, dprov = rallies.segment_rallies(empty, FPS)
    assert dprov["signal"] == "none" and dprov["degraded"] is True
    assert _seg_frames(windows) == [(0, 599), (3000, 3599)]
    for w in windows:
        assert w["start_sec"] == w["start_frame"] / FPS
        assert w["end_sec"] == w["end_frame"] / FPS
    assert windows[1]["end_sec"] == pytest.approx(3599 / FPS)


def test_degraded_windows_are_sized_in_seconds_not_records():
    """The same 25 s of video recorded at stride 1 and stride 3 tiles identically."""
    for stride in (1, 3):
        frames = range(0, 1500, stride)
        empty = [{"frame": f, "shuttle": None, "wrist_lower": None, "wrist_upper": None}
                 for f in frames]
        windows, prov = rallies.segment_rallies(empty, FPS)
        assert prov["params"]["stride"] == stride
        assert [w["start_frame"] for w in windows] == [0, 600, 1200]


def test_a_strided_track_reports_frame_times_and_its_stride():
    frames = list(range(0, 1800, 3))
    segments, prov = rallies.segment_rallies(
        _swing_track(frames, lambda f: 300 <= f < 600 or 900 <= f < 1200), FPS)
    assert prov["params"]["stride"] == 3
    assert prov["signal"] == "swing" and len(segments) == 2
    for s in segments:
        assert s["start_frame"] % 3 == 0 and s["end_frame"] % 3 == 0
        assert s["start_sec"] == s["start_frame"] / FPS


# --- pinned signals --------------------------------------------------------------


@pytest.mark.parametrize("bad", ["courtview", "SWING", "", None, 5, ["swing"]])
def test_an_invalid_pin_is_treated_as_auto_and_says_so(bad):
    track = _swing_track(range(1800), _two_bursts)
    segments, prov = rallies.segment_rallies(track, FPS, signal=bad)
    assert prov["signal"] == "swing"                       # what auto would choose
    assert "invalid signal pin" in prov["reason"] and "ignored" in prov["reason"]
    assert len(segments) == 2


def test_a_pinned_signal_with_no_usable_input_is_degraded_and_says_so():
    swing_only = _swing_track(range(1800), _two_bursts)
    segments, prov = rallies.segment_rallies(swing_only, FPS, signal="shuttle")
    assert prov["signal"] == "none" and prov["degraded"] is True
    assert prov["attempted_signal"] == "shuttle"
    assert "no usable shuttle" in prov["reason"]
    assert segments and all(s["degraded"] for s in segments)

    shuttle_only = _shuttle_track(1800, lambda i: True)
    segments, prov = rallies.segment_rallies(shuttle_only, FPS, signal="swing")
    assert prov["signal"] == "none" and prov["degraded"] is True
    assert prov["attempted_signal"] == "swing"
    assert "no usable swing" in prov["reason"]
    assert segments and all(s["degraded"] for s in segments)


def test_a_chosen_signal_that_finds_no_rally_is_degraded_with_attempted_signal():
    still = _swing_track(range(1800), lambda f: False)     # wrists present, never moving
    segments, prov = rallies.segment_rallies(still, FPS)
    assert prov["attempted_signal"] == "swing"
    assert prov["signal"] == "none" and prov["degraded"] is True
    assert "no segments" in prov["reason"]
    assert len(segments) == 3 and all(s["degraded"] for s in segments)


# --- quad validation (never fatal, never silently drops every point) ------------------


@pytest.mark.parametrize("bad_quad", [
    "not a quad",
    [1, 2, 3, 4],
    [(0, 0), (1, 1)],
    [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)],
    [("a", "b")] * 4,
    [(float("nan"), 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)],
    [(0.0, 0.0), (float("inf"), 0.0), (1.0, 1.0), (0.0, 1.0)],
    [(5.0, 5.0)] * 4,                                         # zero area
    [(0.0, 5.0), (10.0, 5.0), (10.0, 5.0), (0.0, 5.0)],       # zero depth
    42,
])
def test_an_unusable_quad_is_ignored_not_fatal_and_not_a_silent_gate(bad_quad):
    track = _shuttle_track(600, lambda i: True)
    segments, prov = rallies.segment_rallies(track, FPS, quad=bad_quad)
    assert prov["gated_outside_court"] == 0                   # nothing silently dropped
    assert prov["signal"] == "shuttle"
    assert prov["shuttle_density"] == pytest.approx(1.0)
    assert prov["caps_source"] == "fallback_100px"
    assert "quad" in prov["reason"]
    assert segments


def test_a_missing_quad_skips_gating_and_falls_back_on_the_absolute_cap():
    track = _shuttle_track(600, lambda i: True)
    _segments, prov = rallies.segment_rallies(track, FPS, quad=None)
    assert prov["gated_outside_court"] == 0
    assert prov["caps_source"] == "fallback_100px"


def test_a_well_formed_quad_that_has_no_perspective_still_gates():
    """A top-down rectangle cannot give baseline caps but is a perfectly good gate."""
    rect = [(100.0, 200.0), (900.0, 200.0), (900.0, 800.0), (100.0, 800.0)]
    inside = [(200.0 + 7.0 * i, 300.0 + 3.0 * i) for i in range(50)]
    outside = [(50.0 + 3.0 * i, 1500.0 + 2.0 * i) for i in range(50)]
    _segments, prov = rallies.segment_rallies(_track(inside + outside), FPS, quad=rect)
    assert prov["gated_outside_court"] == 50
    assert prov["caps_source"] == "fallback_100px"
    assert "caps" in prov["reason"]


# --- never fatal ---------------------------------------------------------------------


def test_an_internal_exception_yields_degraded_windows_not_a_raise(monkeypatch):
    def boom(*_a, **_k):
        raise RuntimeError("synthetic failure")
    monkeypatch.setattr(rallies, "swing_activity", boom)
    track = _swing_track(range(1800), _two_bursts)
    segments, prov = rallies.segment_rallies(track, FPS)
    assert prov["signal"] == "none" and prov["degraded"] is True
    assert "RuntimeError" in prov["reason"]
    assert len(segments) == 3 and all(s["degraded"] for s in segments)
    assert segments[0]["start_frame"] == 0 and segments[-1]["end_frame"] == 1799
    json.dumps(prov, allow_nan=False)


def test_an_early_internal_exception_is_also_contained(monkeypatch):
    def boom(*_a, **_k):
        raise ValueError("early")
    monkeypatch.setattr(rallies, "suppress_static", boom)
    segments, prov = rallies.segment_rallies(_track([None] * 700), FPS)
    assert prov["signal"] == "none" and prov["degraded"] is True
    assert "ValueError" in prov["reason"]
    assert segments
    json.dumps(prov, allow_nan=False)


def test_records_without_a_usable_frame_never_raise():
    track = [{"shuttle": None}, {"frame": "x"}, {"frame": 5, "shuttle": None}, None]
    segments, prov = rallies.segment_rallies(track, FPS)
    assert prov["degraded"] is True and prov["signal"] == "none"
    assert [(s["start_frame"], s["end_frame"]) for s in segments] == [(5, 5)]


_BAD_FPS = [float("nan"), float("inf"), float("-inf"), 0.0, -30.0, None, "abc", "60", True]


@pytest.mark.parametrize("fps", _BAD_FPS)
def test_an_invalid_fps_yields_no_segments_and_says_so(fps):
    """No time base exists, so no segment (and no second) may be invented."""
    swing = _swing_track(range(900), _two_bursts)
    empty = _track([None] * 900)
    for track in (swing, empty, []):
        segments, prov = rallies.segment_rallies(track, fps)
        assert segments == []
        assert prov["signal"] == "none" and prov["degraded"] is True
        assert "fps" in prov["reason"]
        json.dumps([segments, prov], allow_nan=False)


@pytest.mark.parametrize("fps", [None, float("nan"), 0.0, "abc"])
def test_an_invalid_fps_yields_no_segments_on_every_path(fps, monkeypatch):
    swing = _swing_track(range(900), _two_bursts)
    for pin in ("auto", "swing", "shuttle", "courtview"):
        segments, prov = rallies.segment_rallies(swing, fps, signal=pin, quad=_QUAD)
        assert segments == [] and prov["degraded"] is True and "fps" in prov["reason"]
    monkeypatch.setattr(rallies, "suppress_static",
                        lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("x")))
    segments, prov = rallies.segment_rallies(swing, fps)
    assert segments == [] and "fps" in prov["reason"] and "RuntimeError" in prov["reason"]


def test_a_degraded_window_never_covers_a_gap_longer_than_gap_sec():
    """Frames 0-29 and 570-599 at 60 fps: 9 s of absence must not be claimed."""
    frames = list(range(0, 30)) + list(range(570, 600))
    empty = [{"frame": f, "shuttle": None, "wrist_lower": None, "wrist_upper": None}
             for f in frames]
    windows, prov = rallies.segment_rallies(empty, FPS)
    assert prov["degraded"] is True
    assert _seg_frames(windows) == [(0, 29), (570, 599)]
    # A gap at or under gap_sec (1 s = 60 frames) does not split a window.
    near = [{"frame": f, "shuttle": None, "wrist_lower": None, "wrist_upper": None}
            for f in list(range(0, 30)) + list(range(89, 119))]
    assert _seg_frames(rallies.segment_rallies(near, FPS)[0]) == [(0, 118)]
    # gap_sec is the caller's: widening it merges what the default splits.
    wide = rallies.segment_rallies(empty, FPS, gap_sec=10.0)[0]
    assert _seg_frames(wide) == [(0, 599)]


def test_invalid_numeric_parameters_fall_back_to_defaults_and_say_so():
    track = _swing_track(range(1800), _two_bursts)
    segments, prov = rallies.segment_rallies(
        track, FPS, gap_sec=float("nan"), min_len_sec=-1.0, swing_frac="x")
    assert prov["params"]["gap_sec"] == 1.0
    assert prov["params"]["min_len_sec"] == 2.0
    assert prov["params"]["swing_frac"] == 0.25
    assert "gap_sec" in prov["reason"]
    assert len(segments) == 2
    json.dumps(prov, allow_nan=False)


def test_provenance_and_segments_are_json_serialisable_and_finite():
    fixture = [(473.0, 846.0)] * 900
    scenarios = [
        rallies.segment_rallies(_track(fixture + [None] * 100), FPS, quad=DJI_QUAD),
        rallies.segment_rallies(_swing_track(range(1800), _two_bursts), FPS, quad=_QUAD),
        rallies.segment_rallies(_shuttle_track(900, lambda i: True), FPS, quad=_QUAD),
        rallies.segment_rallies([], FPS),
    ]
    for segments, prov in scenarios:
        round_trip = json.loads(json.dumps([segments, prov], allow_nan=False))
        assert round_trip == json.loads(json.dumps([segments, prov]))


def test_segment_rallies_does_not_mutate_the_track():
    track = _swing_track(range(900), _two_bursts)
    for rec in track:
        rec["shuttle"] = (473.0, 846.0)
    before = [dict(r) for r in track]
    rallies.segment_rallies(track, FPS, quad=_QUAD)
    assert track == before


def test_the_rallies_module_stays_pure():
    src = inspect.getsource(rallies)
    for forbidden in ("import cv2", "badminton_analysis.system", "from ..system",
                      "import logging", "open(", "print("):
        assert forbidden not in src


# --- Task 9: the pipeline writes rally_segments.json post-loop ----------------------
#
# Hermetic and runnable alone: systems are built with __new__, and the runtime
# globals (cv2, write_json) are never needed -- the writer imports its own.

_CAL = {"cut": 0.5, "method": "median-4mad", "calibration": "median-4mad",
        "k": 4.0, "samples": 40, "median": 0.8, "mad": 0.02}


def _writer_system(tmp_path, track, *, rally_signal="auto", quad=None, frames=100,
                   court_frames=80, calibration=_CAL):
    s = object.__new__(sysmod.BadmintonAnalysisSystem)
    s.save_dir = str(tmp_path)
    s.court_corners = quad
    s._rally_track = track
    s.rally_signal = rally_signal
    s.court_view_calibration = calibration
    s._gate_frames = frames
    s._gate_court_frames = court_frames
    s.rally_active = False
    s.rally_count = 0
    s.rally_segments = []
    s._current_rally_start = 0
    return s


def _read_segments(tmp_path):
    with open(tmp_path / "rally_segments.json", encoding="utf-8") as fh:
        return json.load(fh)


def test_pipeline_writes_provenance_without_breaking_the_rallies_schema(tmp_path):
    """player_positions.py and the UI read `rallies`; it must keep its exact shape."""
    track = _swing_track(range(1800), _two_bursts)
    s = _writer_system(tmp_path, track, quad=_QUAD)
    s._write_rally_segments(fps=FPS)
    payload = _read_segments(tmp_path)

    assert payload["fps"] == FPS
    assert len(payload["rallies"]) == 2
    for rally in payload["rallies"]:
        assert set(rally) == {"id", "start_frame", "end_frame", "start_sec", "end_sec",
                              "degraded"}
        assert rally["degraded"] is False
        assert rally["start_sec"] == pytest.approx(rally["start_frame"] / FPS)
    det = payload["detection"]
    assert det["signal"] == "swing" and det["degraded"] is False
    assert det["reason"] and det["caps_source"] == "quad"
    assert det["court_view"] == {"cut": 0.5, "method": "median-4mad",
                                 "calibration": "median-4mad", "pass_frac": 0.8,
                                 "court_frames": 80, "frames": 100}
    assert s.rally_detection == det                  # what Task 10 consumes


def test_a_degraded_segmentation_is_written_as_degraded_windows(tmp_path):
    track = [{"frame": i, "shuttle": None, "wrist_lower": None, "wrist_upper": None}
             for i in range(1200)]
    s = _writer_system(tmp_path, track, quad=_QUAD)
    s._write_rally_segments(fps=FPS)
    payload = _read_segments(tmp_path)
    assert payload["rallies"] and all(r["degraded"] is True for r in payload["rallies"])
    assert payload["detection"]["signal"] == "none"
    assert payload["detection"]["degraded"] is True
    assert "error" not in payload["detection"]


def test_the_rally_signal_pin_reaches_the_segmenter(tmp_path, monkeypatch):
    seen = {}

    def _spy(track, fps, **kw):
        seen.update(kw, fps=fps, n=len(track))
        return [], {"signal": "none", "reason": "spy", "degraded": True}

    monkeypatch.setattr(rallies, "segment_rallies", _spy)
    s = _writer_system(tmp_path, _swing_track(range(10), _two_bursts),
                       rally_signal="shuttle", quad=_QUAD)
    s._write_rally_segments(fps=FPS)
    assert seen == {"signal": "shuttle", "quad": _QUAD, "fps": FPS, "n": 10}


def test_an_unknown_rally_signal_is_auto_and_the_reason_says_so(tmp_path):
    s = _writer_system(tmp_path, _swing_track(range(1800), _two_bursts),
                       rally_signal="bogus", quad=_QUAD)
    s._write_rally_segments(fps=FPS)
    det = _read_segments(tmp_path)["detection"]
    assert det["signal"] == "swing"                  # auto still chose
    assert "bogus" in det["reason"] and "auto" in det["reason"]


def test_a_missing_calibration_and_no_gate_frames_write_nulls_not_garbage(tmp_path):
    s = _writer_system(tmp_path, [], calibration=None, frames=0, court_frames=0)
    s._write_rally_segments(fps=FPS)
    payload = _read_segments(tmp_path)
    assert payload["rallies"] == []
    assert payload["detection"]["court_view"] == {
        "cut": None, "method": None, "calibration": None, "pass_frac": None,
        "court_frames": 0, "frames": 0}


def test_a_non_finite_cut_is_written_as_null(tmp_path):
    cal = dict(_CAL, cut=float("-inf"), method="template-mismatch",
               calibration="template-mismatch")
    s = _writer_system(tmp_path, [], calibration=cal)
    s._write_rally_segments(fps=FPS)
    raw = (tmp_path / "rally_segments.json").read_text(encoding="utf-8")
    assert "Infinity" not in raw and "NaN" not in raw
    assert json.loads(raw)["detection"]["court_view"]["cut"] is None


def test_segmenter_failure_is_never_fatal_and_is_not_degraded(tmp_path, monkeypatch, capsys):
    def _boom(*a, **k):
        raise RuntimeError("synthetic segmenter failure with /secret/path")

    monkeypatch.setattr(rallies, "segment_rallies", _boom)
    s = _writer_system(tmp_path, [])
    s._write_rally_segments(fps=FPS)                 # must not raise

    payload = _read_segments(tmp_path)
    assert payload["rallies"] == []
    det = payload["detection"]
    assert det["signal"] == "error"
    assert det["error"] is True and det["degraded"] is False
    assert det["reason"] == "RuntimeError"           # the type only, never the message
    assert "secret" not in json.dumps(payload)
    assert det["court_view"]["frames"] == 100
    assert s.rally_detection == det
    out = capsys.readouterr().out
    assert "synthetic segmenter failure" in out      # the full exception, on the console
    assert "RuntimeError" in out


def test_a_failing_write_is_also_contained(tmp_path, monkeypatch):
    import badminton_analysis.data.writer as writer_mod

    calls = []

    def _flaky(path, payload):
        calls.append(payload["detection"]["signal"])
        raise OSError("disk full")

    monkeypatch.setattr(writer_mod, "write_json", _flaky)
    s = _writer_system(tmp_path, _swing_track(range(1800), _two_bursts), quad=_QUAD)
    s._write_rally_segments(fps=FPS)                 # must not raise
    assert calls == ["swing", "error"]
    assert s.rally_detection["signal"] == "error"
    assert s.rally_detection["degraded"] is False


def test_the_constructor_accepts_rally_signal_and_defaults_to_auto():
    params = inspect.signature(sysmod.BadmintonAnalysisSystem.__init__).parameters
    assert params["rally_signal"].default == "auto"
    src = inspect.getsource(sysmod.BadmintonAnalysisSystem.__init__)
    assert "self.rally_signal = rally_signal" in src


def test_process_video_writes_segments_after_the_frame_loop():
    src = inspect.getsource(sysmod.BadmintonAnalysisSystem.process_video)
    assert (src.index("self._process_frame(") < src.index("self._write_rally_segments(")
            < src.index("self._run_stroke_recognition()"))


# --- R20: rally_signal="courtview" reproduces today's rally_segments.json ----------

def _drive_inloop_machine(tmp_path, monkeypatch, blocks):
    """Run today's in-loop court-view state machine through the real
    ``_process_frame`` and the real PINNED gate (check interval 3), with only the
    NCC itself stubbed: a frame's value says whether it matches the template."""
    import types

    import numpy as np

    stub_cv2 = types.SimpleNamespace(COLOR_BGR2GRAY=6,
                                     cvtColor=lambda frame, code: frame[:, :, 0])
    monkeypatch.setattr(sysmod, "cv2", stub_cv2, raising=False)

    s = _writer_system(tmp_path, [], rally_signal="courtview", frames=0, court_frames=0)
    s.show_display = False
    s.show_pose_roi = False
    s.court_view_frames_threshold = 5
    s.non_court_frames_threshold = 5
    s.is_court_view_count = 0
    s.consecutive_non_court_frames = 0
    s.court_view_pinned = True               # court_view_threshold pinned
    s.court_view_threshold_override = 0.5
    s.court_view_cut = 0.5
    s.court_view_calibration = {"cut": 0.5, "method": "manual",
                                "calibration": "manual", "samples": 0}
    s.player_tracker = types.SimpleNamespace(start_new_rally=lambda: None)
    s.shuttlecock_tracker = types.SimpleNamespace(clear_trajectory=lambda: None)
    s.is_court_view = lambda gray, tmpl, threshold=None: bool(gray[0, 0])
    s._analyze_this_frame = lambda frame_count: False      # no pose/ball work
    out = types.SimpleNamespace(write=lambda frame: None)

    frame_count = 0
    for is_court, n in blocks:
        for _ in range(n):
            frame_count += 1
            frame = np.full((4, 4, 3), 1 if is_court else 0, dtype=np.uint8)
            s._process_frame(frame, None, [(0, 0)] * 4, [(0, 0), (2, 2)],
                             frame_count, out, 0)
    return s, frame_count


# Block lengths are multiples of the pinned gate's 3-frame check interval and the
# sequence starts on a check frame, so the held gate equals the raw sequence.
_COURTVIEW_BLOCKS = [(True, 21), (False, 9), (True, 33), (False, 3), (True, 18),
                     (False, 9), (True, 3), (False, 9), (True, 21)]
# Hand-derived from the pre-change machine: a rally starts when the 5th
# consecutive court frame arrives, ends on the 5th consecutive non-court frame,
# a 3-frame dip does not end it, a 3-frame blip does not start one, and the
# still-open rally is closed at the last frame of the video.
_COURTVIEW_EXPECTED = [(1, 5, 26), (2, 35, 89), (3, 110, 126)]


def test_courtview_pin_writes_exactly_todays_rallies(tmp_path, monkeypatch):
    fps = 30.0
    s, last = _drive_inloop_machine(tmp_path, monkeypatch, _COURTVIEW_BLOCKS)
    assert last == 126
    assert s.rally_segments == _COURTVIEW_EXPECTED[:2]      # the open one is not yet closed
    assert s.rally_active is True

    s._write_rally_segments(fps=fps, last_frame=last)
    payload = _read_segments(tmp_path)

    today = [{"id": i, "start_frame": a, "end_frame": b,
              "start_sec": a / fps, "end_sec": b / fps}
             for i, a, b in _COURTVIEW_EXPECTED]
    assert [{k: v for k, v in r.items() if k != "degraded"}
            for r in payload["rallies"]] == today
    assert all(r["degraded"] is False for r in payload["rallies"])
    assert payload["fps"] == fps and set(payload) == {"fps", "rallies", "detection"}
    det = payload["detection"]
    assert det["signal"] == "courtview" and det["degraded"] is False
    assert det["court_view"]["method"] == "manual"
    assert det["court_view"]["cut"] == 0.5
    # the gate counters are fed by _process_frame itself
    assert det["court_view"]["frames"] == 126
    assert det["court_view"]["court_frames"] == 21 + 33 + 18 + 3 + 21
    assert det["court_view"]["pass_frac"] == pytest.approx(96 / 126)
    # writing must not close the live rally in the machine itself
    assert s.rally_segments == _COURTVIEW_EXPECTED[:2]


def test_the_courtview_pin_never_calls_the_segmenter(tmp_path, monkeypatch):
    def _boom(*a, **k):
        raise AssertionError("segment_rallies must not run for courtview")

    monkeypatch.setattr(rallies, "segment_rallies", _boom)
    s, last = _drive_inloop_machine(tmp_path, monkeypatch, _COURTVIEW_BLOCKS)
    s._write_rally_segments(fps=30.0, last_frame=last)
    assert _read_segments(tmp_path)["detection"]["signal"] == "courtview"


# --- Task 10: a degraded segmentation withholds stroke labels (§12a-C) -------

def _bare_system_for_recognition(tmp_path, monkeypatch, rally_detection, missing=False):
    """A bare instance whose BST recognizer is a recording fake."""
    from badminton_analysis.stroke_recog import recognizer as recog_mod

    calls = []

    class _FakeRecognizer:
        def __init__(self, weights):
            calls.append(("init", weights))

        def label_rally(self, *args):
            calls.append(("label_rally",))
            return [{"stroke": "smash"}, {"stroke": "smash"}, {"stroke": "clear"}]

    monkeypatch.setattr(recog_mod, "StrokeRecognizer", _FakeRecognizer)
    # write_json is a lazily-loaded runtime global of system.py (set by
    # load_runtime_dependencies); supply it so this test passes when run alone.
    from badminton_analysis.data.writer import write_json as _real_write_json
    monkeypatch.setattr(sysmod, "write_json", _real_write_json, raising=False)
    s = sysmod.BadmintonAnalysisSystem.__new__(sysmod.BadmintonAnalysisSystem)
    s.save_dir = str(tmp_path)
    s.bst_weights = "weights/bst.pt"        # pretend weights exist
    s._analysis_track_both = []
    s._analysis_frames = {}
    s.court_corners = DJI_QUAD
    s.frame_width, s.frame_height = 1920, 1080
    s._shuttle_source = "yolo"
    if not missing:
        s.rally_detection = rally_detection
    return s, calls


def test_degraded_segmentation_skips_stroke_recognition(tmp_path, monkeypatch, capsys):
    """Labels over windows that are not rallies would be noise presented as strokes."""
    s, calls = _bare_system_for_recognition(
        tmp_path, monkeypatch,
        {"signal": "none", "degraded": True, "reason": "no usable signal"})

    s._run_stroke_recognition()

    assert calls == []                       # the recognizer was never built or run
    assert not (tmp_path / "strokes.json").exists()
    out = capsys.readouterr().out.lower()
    assert "stroke labels withheld" in out
    assert "segmentation unreliable" in out
    assert "no usable signal" in out         # the reason is surfaced


def test_non_degraded_segmentation_does_not_block_recognition(tmp_path, monkeypatch, capsys):
    s, calls = _bare_system_for_recognition(
        tmp_path, monkeypatch,
        {"signal": "shuttle", "degraded": False, "reason": "dense"})

    s._run_stroke_recognition()

    assert ("label_rally",) in calls
    assert (tmp_path / "strokes.json").exists()
    assert "withheld" not in capsys.readouterr().out.lower()


def test_segmenter_error_is_not_degraded_recognition_still_runs(tmp_path, monkeypatch, capsys):
    """R6: an ERROR is not a degraded segmentation; recognition runs as before."""
    s, calls = _bare_system_for_recognition(
        tmp_path, monkeypatch,
        {"signal": "error", "degraded": False, "error": True, "reason": "ValueError"})

    s._run_stroke_recognition()

    assert ("label_rally",) in calls
    assert (tmp_path / "strokes.json").exists()
    assert "withheld" not in capsys.readouterr().out.lower()


def test_missing_rally_detection_recognition_runs_as_before(tmp_path, monkeypatch, capsys):
    """Bare instances / older runs have no rally_detection attribute at all."""
    s, calls = _bare_system_for_recognition(tmp_path, monkeypatch, None, missing=True)
    assert not hasattr(s, "rally_detection")

    s._run_stroke_recognition()

    assert ("label_rally",) in calls
    assert (tmp_path / "strokes.json").exists()
    assert "withheld" not in capsys.readouterr().out.lower()
