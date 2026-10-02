"""Hard-cut detection for the true-broadcast footage class.

NOT VALIDATED ON REAL FOOTAGE. Owner decision B11 §12a-A kept true
multi-camera broadcast in scope, but the only "broadcast" file on disk is a
single-angle continuous recording with no cuts in it (design spec §0.5), so
there is nothing to validate against. These tests pin the mechanism, not its
accuracy on real television.
"""
import numpy as np

from badminton_analysis.stroke import shot_boundary


def _flat(value, shape=(180, 320)):
    return np.full(shape, value, dtype=np.uint8)


def test_identical_frames_are_not_a_cut():
    sig = shot_boundary.frame_signature(_flat(120))
    assert shot_boundary.is_cut(sig, sig) is False


def test_a_wholesale_change_is_a_cut():
    a = shot_boundary.frame_signature(_flat(20))
    b = shot_boundary.frame_signature(_flat(230))
    assert shot_boundary.is_cut(a, b) is True


def test_gradual_drift_is_not_a_cut():
    """A pan or a light change must not read as a camera switch."""
    rng = np.random.default_rng(7)
    base = rng.integers(60, 90, (180, 320), dtype=np.uint8)
    a = shot_boundary.frame_signature(base)
    b = shot_boundary.frame_signature(np.clip(base.astype(int) + 3, 0, 255).astype(np.uint8))
    assert shot_boundary.is_cut(a, b) is False


def test_find_cuts_reports_each_boundary_once():
    sigs = ([shot_boundary.frame_signature(_flat(30))] * 10
            + [shot_boundary.frame_signature(_flat(220))] * 10
            + [shot_boundary.frame_signature(_flat(30))] * 10)
    assert shot_boundary.find_cuts(sigs) == [10, 20]


def test_no_cuts_in_continuous_footage():
    sigs = [shot_boundary.frame_signature(_flat(100))] * 30
    assert shot_boundary.find_cuts(sigs) == []


def test_signature_is_normalised():
    sig = shot_boundary.frame_signature(_flat(77))
    assert len(sig) == shot_boundary.HIST_BINS
    assert abs(sum(sig) - 1.0) < 1e-9


# Degenerate input tests (amendments R3 and amendment handling)

def test_frame_signature_handles_empty_frame():
    """A completely black or empty frame should not crash."""
    black = np.zeros((180, 320), dtype=np.uint8)
    sig = shot_boundary.frame_signature(black)
    assert len(sig) == shot_boundary.HIST_BINS
    assert abs(sum(sig) - 1.0) < 1e-9


def test_frame_signature_handles_constant_frame_all_same_value():
    """A frame with constant pixel value (zero variance) should return normalised histogram."""
    const = np.full((180, 320), 128, dtype=np.uint8)
    sig = shot_boundary.frame_signature(const)
    assert len(sig) == shot_boundary.HIST_BINS
    # All pixels same value -> histogram is sparse (only one bin filled)
    assert abs(sum(sig) - 1.0) < 1e-9


def test_find_cuts_handles_empty_signature_list():
    """Empty signature list should return empty cut list."""
    result = shot_boundary.find_cuts([])
    assert result == []


def test_find_cuts_handles_single_signature():
    """Single signature (no comparisons possible) should return empty cut list."""
    sig = shot_boundary.frame_signature(_flat(100))
    result = shot_boundary.find_cuts([sig])
    assert result == []


def test_is_cut_handles_constant_frames():
    """Two identical constant-valued frames should not be a cut."""
    const1 = shot_boundary.frame_signature(_flat(100))
    const2 = shot_boundary.frame_signature(_flat(100))
    assert shot_boundary.is_cut(const1, const2) is False
