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


# --- Original brief tests ---

def test_identical_frames_are_not_a_cut():
    sig = shot_boundary.frame_signature(_flat(120))
    assert shot_boundary.is_cut(sig, sig) is False


def test_a_wholesale_change_is_a_cut():
    a = shot_boundary.frame_signature(_flat(20))
    b = shot_boundary.frame_signature(_flat(230))
    assert shot_boundary.is_cut(a, b) is True


def test_gradual_drift_is_not_a_cut():
    """A +3 brightness drift must not read as a camera switch."""
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


# --- Degenerate input tests: zero-mass, zero-variance, mismatched ---

def test_frame_signature_handles_black_frame():
    """A completely black frame should return all-zero signature.

    A black frame (all pixels = 0) has all its histogram mass in the first bin.
    """
    black = np.zeros((180, 320), dtype=np.uint8)
    sig = shot_boundary.frame_signature(black)
    assert len(sig) == shot_boundary.HIST_BINS
    # All pixels at value 0 → all mass in first bin
    assert sig[0] == 1.0
    assert all(v == 0.0 for v in sig[1:])


def test_zero_mass_signature_vs_real_is_not_a_cut():
    """A zero-mass (all-zero) signature compared to a real signature → no cut.

    This prevents failed decodes or empty frames from yielding spurious cuts.
    """
    zero_sig = [0.0] * shot_boundary.HIST_BINS
    real_sig = shot_boundary.frame_signature(_flat(150))
    assert shot_boundary.is_cut(zero_sig, real_sig) is False
    assert shot_boundary.is_cut(real_sig, zero_sig) is False


def test_uniform_histogram_vs_real_is_not_a_cut():
    """A uniform-histogram signature (all bins equal) vs a real signature → no cut.

    A uniform histogram has zero variance and should not produce a cut, even
    against a frame with a distinct histogram.
    """
    # Manually create a uniform signature (all bins equal)
    uniform_sig = [1.0 / shot_boundary.HIST_BINS] * shot_boundary.HIST_BINS
    real_sig = shot_boundary.frame_signature(_flat(180))
    assert shot_boundary.is_cut(uniform_sig, real_sig) is False


def test_mismatched_signature_lengths_is_not_a_cut():
    """Signatures with different lengths → no cut (safety on data corruption)."""
    sig_a = [0.5, 0.5]
    sig_b = [0.25, 0.25, 0.25, 0.25]
    assert shot_boundary.is_cut(sig_a, sig_b) is False


def test_mismatched_frame_sizes_same_content_is_not_a_cut():
    """Frames with different dimensions but same content → no exception, no cut.

    E.g., upscaled/downscaled version of the same frame should not be a cut.
    """
    frame_360x640 = _flat(100, shape=(360, 640))
    frame_720x1280 = _flat(100, shape=(720, 1280))
    sig_a = shot_boundary.frame_signature(frame_360x640)
    sig_b = shot_boundary.frame_signature(frame_720x1280)
    # Both have the same histogram (uniform at value 100), so same signature
    assert shot_boundary.is_cut(sig_a, sig_b) is False


def test_find_cuts_with_one_black_frame_between_identical():
    """A sequence of identical frames with one black frame → no cuts reported.

    The black frame has zero mass, so is_cut returns False both before and after.
    """
    real_sig = shot_boundary.frame_signature(_flat(150))
    black_sig = [0.0] * shot_boundary.HIST_BINS
    sigs = [real_sig, black_sig, real_sig]
    assert shot_boundary.find_cuts(sigs) == []


# --- Desired behavior: texture and content changes ---

def test_textured_frame_vs_same_rolled_is_not_a_cut():
    """Two identical textured frames, one rolled spatially → not a cut (desired).

    A spatial shift without content change should not affect the histogram.
    """
    rng = np.random.default_rng(42)
    texture = rng.integers(60, 120, (360, 640), dtype=np.uint8)
    rolled = np.roll(texture, 40, axis=1)  # Roll by 40 pixels horizontally
    sig_a = shot_boundary.frame_signature(texture)
    sig_b = shot_boundary.frame_signature(rolled)
    assert shot_boundary.is_cut(sig_a, sig_b) is False


def test_two_distinct_textured_scenes_is_a_cut():
    """Two distinct textured scenes with different histograms → a cut (desired)."""
    rng = np.random.default_rng(50)
    # Scene 1: darker (mean ~70)
    scene_a = rng.integers(50, 90, (360, 640), dtype=np.uint8)
    # Scene 2: lighter (mean ~190)
    scene_b = rng.integers(170, 210, (360, 640), dtype=np.uint8)
    sig_a = shot_boundary.frame_signature(scene_a)
    sig_b = shot_boundary.frame_signature(scene_b)
    assert shot_boundary.is_cut(sig_a, sig_b) is True


# --- Known limitations: false positives and false negatives ---

def test_textured_frame_vs_bright_flash_is_a_cut():
    """A textured frame vs the same frame + 60 brightness → a cut (false positive).

    DOCUMENTED LIMITATION: global brightness steps/flashes are false positives.
    """
    rng = np.random.default_rng(60)
    texture = rng.integers(50, 100, (360, 640), dtype=np.uint8)
    brightened = np.clip(texture.astype(int) + 60, 0, 255).astype(np.uint8)
    sig_a = shot_boundary.frame_signature(texture)
    sig_b = shot_boundary.frame_signature(brightened)
    assert shot_boundary.is_cut(sig_a, sig_b) is True


def test_textured_frame_vs_permuted_copy_is_not_a_cut():
    """A textured frame vs a pixel-permuted copy → not a cut (false negative).

    DOCUMENTED LIMITATION: cuts between shots with similar histograms are missed.
    A permutation preserves the histogram, so no cut is detected even if content
    is completely different.
    """
    rng = np.random.default_rng(70)
    texture = rng.integers(60, 100, (360, 640), dtype=np.uint8)
    # Permute pixels randomly (shuffle all values)
    flat = texture.flatten()
    np.random.RandomState(70).shuffle(flat)
    permuted = flat.reshape(texture.shape)
    sig_a = shot_boundary.frame_signature(texture)
    sig_b = shot_boundary.frame_signature(permuted)
    assert shot_boundary.is_cut(sig_a, sig_b) is False
