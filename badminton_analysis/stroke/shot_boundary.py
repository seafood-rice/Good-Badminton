"""Hard-cut detection for true multi-camera broadcast footage.

STATUS: standalone; not yet wired into rally segmentation; real-footage
validation not run — blocked on a genuine broadcast sample with hard cuts
(B11 spec §12a-A).

Method: correlate coarse intensity histograms of consecutive frames. A hard
cut replaces the whole image at once, so the histogram decorrelates in a
single step. This method EXPECTS this behaviour to hold; it is unvalidated on
real broadcast footage. This deliberately does NOT attempt replay detection
(logo wipes, slow motion), which is a different and harder problem.

Known limitations:
  - Global brightness steps, flashes, and fades read as cuts (false positives).
  - Cuts between shots with similar intensity histograms are missed (false negatives).
  - Callers should pass a downscaled frame; a full 4K frame signature costs ~83 ms per frame.
"""
import math

HIST_BINS = 32
CUT_CORRELATION_MAX = 0.60
"""Below this Pearson correlation between consecutive histograms, call it a cut.

Unvalidated on real broadcast footage -- see the module docstring. Named and
overridable so it can be calibrated the moment a real sample exists.
"""


def frame_signature(gray, bins=HIST_BINS):
    """Normalised coarse intensity histogram of a grayscale frame.

    Args:
        gray: 8-bit grayscale frame (values 0-255), any array-like with shape
        bins: number of histogram bins (default HIST_BINS)

    Returns:
        list[float]: normalised histogram summing to 1.0. Returns all-zero
                     signature [0.0, ...] on degenerate input (empty frame,
                     zero total mass, failed decode). An all-zero signature
                     never produces a cut (see is_cut).
    """
    import numpy as np

    arr = np.asarray(gray)
    hist, _ = np.histogram(arr, bins=bins, range=(0, 256))
    total = float(hist.sum())
    if total <= 0:
        return [0.0] * bins
    return [float(v) / total for v in hist]


def _is_degenerate(signature):
    """Check if a signature is degenerate (unsafe for cut detection).

    Returns True if:
    - signature is empty
    - signature has zero total mass (all zeros)
    - signature has zero variance (all values identical)
    - signature contains non-finite values (NaN, inf)
    """
    if not signature:
        return True
    # Check total mass
    total = sum(signature)
    if total <= 0:
        return True
    # Check for non-finite values
    if any(not math.isfinite(v) for v in signature):
        return True
    # Check variance
    mean = total / len(signature)
    variance = sum((v - mean) ** 2 for v in signature)
    if variance <= 0:
        return True
    return False


def _correlation(a, b):
    """Pearson correlation between two sequences.

    Returns 1.0 if both sequences have zero variance (constant).
    Returns 0.0 if sequences have mismatched lengths or only one has variance.
    """
    n = len(a)
    if n == 0 or n != len(b):
        return 0.0
    ma, mb = sum(a) / n, sum(b) / n
    num = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    da = math.sqrt(sum((x - ma) ** 2 for x in a))
    db = math.sqrt(sum((y - mb) ** 2 for y in b))
    if da <= 0 or db <= 0:
        return 1.0 if da == db else 0.0
    return num / (da * db)


def is_cut(sig_a, sig_b, max_corr=CUT_CORRELATION_MAX):
    """Whether these consecutive signatures straddle a hard cut.

    Args:
        sig_a: signature of first frame
        sig_b: signature of second frame
        max_corr: correlation threshold; below this is a cut

    Returns:
        bool: True if a cut is detected, False otherwise. Returns False on
              degenerate input (mismatched lengths, zero total mass, zero
              variance, non-finite values, or empty signature). Degenerate
              signatures never produce cuts, preventing failed decodes or
              empty frames from yielding spurious cuts.
    """
    # Degenerate input: no cut
    if _is_degenerate(sig_a) or _is_degenerate(sig_b):
        return False
    # Mismatched lengths: no cut
    if len(sig_a) != len(sig_b):
        return False
    return _correlation(sig_a, sig_b) < max_corr


def find_cuts(signatures, max_corr=CUT_CORRELATION_MAX):
    """Indices of frames on which a hard cut lands.

    Args:
        signatures: list of frame signatures
        max_corr: correlation threshold for cut detection

    Returns:
        list[int]: indices of frames where cuts land (each frame compared to
                   previous); empty list if no cuts or fewer than 2 frames.
    """
    if len(signatures) < 2:
        return []
    return [i for i in range(1, len(signatures))
            if is_cut(signatures[i - 1], signatures[i], max_corr)]
