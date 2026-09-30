"""C2: rally segmentation from a recorded per-frame track.

Boundaries are defined in SECONDS throughout, so the same temporal pattern
at 30 and 60 fps must produce the same answer (design spec done-means 8).
"""
import pytest

from badminton_analysis.stroke import rallies


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
