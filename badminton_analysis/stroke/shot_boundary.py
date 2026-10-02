"""Hard-cut detection for true multi-camera broadcast footage.

STATUS: standalone; not yet wired into rally segmentation; real-footage
validation not run — blocked on a genuine broadcast sample with hard cuts
(B11 spec §12a-A).

Method: correlate coarse intensity histograms of consecutive frames. A hard
cut replaces the whole image at once, so the histogram decorrelates in a
single step, while pans, zooms, and lighting changes drift smoothly. This
deliberately does NOT attempt replay detection (logo wipes, slow motion),
which is a different and harder problem.
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
        gray: grayscale frame (any array-like with shape and dtype)
        bins: number of histogram bins (default HIST_BINS)

    Returns:
        list[float]: normalised histogram summing to 1.0; safe on degenerate
                     input (empty/black/constant frames).
    """
    import numpy as np

    arr = np.asarray(gray)
    hist, _ = np.histogram(arr, bins=bins, range=(0, 256))
    total = float(hist.sum())
    if total <= 0:
        return [0.0] * bins
    return [float(v) / total for v in hist]


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
        bool: True if a cut is detected, False otherwise; safe on degenerate
              input.
    """
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
