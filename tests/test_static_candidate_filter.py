"""People who never move are not the player in a rally.

The far-court ROI pose pass recovers the opponent, but it also finds people
sitting or crouching courtside who project inside the court polygon. Measured on
the 0007 clip, one of them -- a crouching person at court (2.31, 3.92) -- won the
tracker's "closest to the net" selection in 13.6% of frames, which would plant a
false hot spot in the far-court heatmap.

Same idea as the shuttle static-artifact suppression in the B11 design: a
detection that occupies one small box for seconds is furniture, not play.
"""
import pytest

from badminton_analysis.tracking.static_filter import StaticCandidateFilter


def _feed(filt, positions, frames):
    """Run positions through the filter for the given frame indices."""
    out = None
    for f in frames:
        out = filt.filter(f, positions)
    return out


def test_a_motionless_candidate_is_suppressed():
    filt = StaticCandidateFilter(tol_px=30.0, window_frames=180, min_hits=60)
    sitting = (1800.0, 1300.0)
    kept = _feed(filt, [sitting], range(0, 180))
    assert kept == []


def test_a_moving_candidate_is_never_suppressed():
    filt = StaticCandidateFilter(tol_px=30.0, window_frames=180, min_hits=60)
    kept = None
    for f in range(0, 180):
        kept = filt.filter(f, [(1800.0 + 4.0 * f, 1300.0)])
    assert len(kept) == 1


def test_a_moving_candidate_survives_beside_a_static_one():
    filt = StaticCandidateFilter(tol_px=30.0, window_frames=180, min_hits=60)
    kept = None
    for f in range(0, 180):
        kept = filt.filter(f, [(1800.0, 1300.0), (1950.0 + 3.0 * f, 1325.0)])
    assert len(kept) == 1
    assert kept[0][0] != 1800.0


def test_nothing_is_suppressed_before_enough_evidence():
    """Early frames must not suppress: a rally can start with a still player."""
    filt = StaticCandidateFilter(tol_px=30.0, window_frames=180, min_hits=60)
    kept = _feed(filt, [(1800.0, 1300.0)], range(0, 10))
    assert len(kept) == 1


def test_evidence_outside_the_window_is_forgotten():
    """A candidate static long ago but moving now must come back."""
    filt = StaticCandidateFilter(tol_px=30.0, window_frames=60, min_hits=30)
    for f in range(0, 60):
        filt.filter(f, [(1800.0, 1300.0)])
    assert filt.filter(60, [(1800.0, 1300.0)]) == []
    kept = None
    for f in range(61, 200):
        kept = filt.filter(f, [(1800.0 + 5.0 * (f - 60), 1300.0)])
    assert len(kept) == 1


def test_tolerance_is_respected():
    """Jitter under tol_px is still static; drift beyond it is not."""
    filt = StaticCandidateFilter(tol_px=30.0, window_frames=180, min_hits=60)
    kept = None
    for f in range(0, 180):
        kept = filt.filter(f, [(1800.0 + (f % 3), 1300.0)])
    assert kept == []


def test_empty_input_is_safe():
    filt = StaticCandidateFilter(tol_px=30.0, window_frames=180, min_hits=60)
    assert filt.filter(0, []) == []


def test_candidates_are_returned_unchanged_not_copied_into_new_types():
    """Callers carry their own payload alongside the point; keep identity."""
    filt = StaticCandidateFilter(tol_px=30.0, window_frames=180, min_hits=60)
    c = (1234.5, 678.9)
    kept = filt.filter(0, [c])
    assert kept[0] is c


def test_from_fps_builds_second_based_settings():
    filt = StaticCandidateFilter.from_fps(60.0, tol_px=30.0,
                                          window_sec=3.0, min_hit_frac=0.9)
    assert filt.window_frames == 180
    assert filt.min_hits == pytest.approx(162, abs=1)


def test_from_fps_handles_a_missing_fps():
    filt = StaticCandidateFilter.from_fps(None, tol_px=30.0,
                                          window_sec=3.0, min_hit_frac=0.9)
    assert filt.window_frames > 0
    assert filt.min_hits > 0
