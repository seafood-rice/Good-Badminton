"""Classical static-camera shuttle detection.

Emits the same contract as ``tracknet.track_video`` -- ``{frame: (x, y) | None}``
-- so it is a drop-in alternative producer. It requires a static camera because it
models the background with a per-pixel median, and therefore complements
TrackNetV3 (which serves moving-camera broadcast footage) rather than replacing it.

Four mechanisms keep the candidate count honest. In the costing spike a single
global cap of 60 candidates/frame bound on EVERY frame, dropping 533,718
candidates, so recall was shaped by arbitrary truncation and a dim far-court
shuttle could lose to bright near-court clutter:

* ``court``  -- the court quad plus the airspace above it, since the shuttle
  spends most of its flight above the floor.
* ``static`` -- pixels differing from the median in more than ``activity_frac`` of
  sampled frames are structural flicker (adjacent courts, spectators, banner
  edges), not a passing shuttle.
* per-band budgets -- candidates are capped within each depth band, so the far
  court gets its own allowance instead of competing with the near court.
* gain correction -- auto-exposure drift is removed before differencing, since a
  frame whose overall level has departed from the background model differs
  everywhere and floods with candidates.
"""
import cv2
import numpy as np

from .shuttle_tracker import Candidate

N_BANDS = 6

DEFAULT_BAND_BUDGET = 100
"""Per-band candidate ceiling -- a runaway guard, NOT a primary filter.

Measured on the DJI footage (rally 1, 105 frames), raw load by band, far to near:

    band 0  y 502-768      0.2/frame
    band 1  y 768-1033    44.5/frame
    band 2  y 1033-1299   66.2/frame
    band 3  y 1299-1565    5.5/frame
    band 4  y 1565-1830    2.1/frame
    band 5  y 1830-2096    0.1/frame

That refutes the assumption the mechanism was designed on. Per-band budgets were
meant to stop a dim FAR-court shuttle being crowded out by bright NEAR-court
clutter, but the clutter here sits at y 768-1299 -- spectators and adjacent courts
BEYOND the court -- and the hand-verified arc flies through y 758-995, i.e. the very
same bands. Banding therefore cannot separate shuttle from clutter on this footage;
the court/static masks and the brightness floor are what do the work.

A budget of 12 dropped 73.2% of candidates (worse in proportion than the spike's
65%, which section 0.23 flagged as invalidating its own recall figures). At 100 the
cap effectively never binds, while still bounding a pathological frame -- a flash or
a camera fault. The shuttle tracker recovers the verified arc at 200 candidates per
frame, so the aggressive budget was guarding against a problem that no longer
exists.
"""

GAIN_LIMITS = (0.5, 2.0)
_ACTIVITY_THRESH = 35


def build_masks(frames, quad, band_top_px=620, activity_frac=0.35):
    """Background median plus court/structural masks and depth bands.

    ``frames`` are grayscale arrays sampled across the clip. ``band_top_px`` is how
    far above the quad's highest row to admit as shuttle airspace.
    """
    if quad is None or len(quad) != 4:
        raise ValueError("court quad must have exactly 4 corners (TL, TR, BR, BL)")
    if not frames:
        raise ValueError("need at least one frame to build a background model")

    h, w = frames[0].shape[:2]
    median = np.median(np.stack(frames), axis=0).astype(np.uint8)

    pts = np.array([[int(round(x)), int(round(y))] for x, y in quad], np.int32)
    court = np.zeros((h, w), np.uint8)
    cv2.fillConvexPoly(court, pts, 255)
    # Extend upward into the airspace, keeping the quad's horizontal extent: the
    # shuttle spends most of a rally above the court, not on it.
    quad_top = int(min(p[1] for p in pts))
    top = max(0, quad_top - int(band_top_px))
    x_lo = max(0, int(min(p[0] for p in pts)))
    x_hi = min(w, int(max(p[0] for p in pts)) + 1)
    court[top:quad_top + 1, x_lo:x_hi] = 255

    activity = np.zeros((h, w), np.float32)
    for g in frames:
        activity += (cv2.absdiff(g, median) > _ACTIVITY_THRESH).astype(np.float32)
    activity /= float(len(frames))
    static = np.where(activity <= activity_frac, 255, 0).astype(np.uint8)

    rows = np.nonzero(court.any(axis=1))[0]
    y_lo, y_hi = (int(rows[0]), int(rows[-1]) + 1) if rows.size else (0, h)
    edges = np.linspace(y_lo, y_hi, N_BANDS + 1).round().astype(int)
    bands = [(int(edges[i]), int(edges[i + 1])) for i in range(N_BANDS)]

    return {"court": court, "static": static, "median": median, "bands": bands}


def _gain_correct(gray, median):
    """Rescale ``gray`` to the background's mean level.

    Auto-exposure drift shifts the whole frame, and once the overall level departs
    from the background model every pixel exceeds the difference threshold and the
    frame floods with candidates. The ratio is clamped so a frame dominated by one
    large bright object cannot distort the correction -- which also means a very
    large drift is deliberately left uncorrected rather than wrongly rescaled.
    """
    cur = float(gray.mean())
    if cur <= 1e-6:
        return gray
    ratio = float(median.mean()) / cur
    lo, hi = GAIN_LIMITS
    if not (lo < ratio < hi):
        return gray
    return np.clip(gray.astype(np.float32) * ratio, 0, 255).astype(np.uint8)


def frame_candidates(gray, masks, diff_thresh=35, area_lo=15, area_hi=1500,
                     aspect_max=2.5, min_bright=110.0,
                     band_budget=DEFAULT_BAND_BUDGET, gain_correct=True):
    """Compact bright movers in this frame, budgeted per depth band.

    Returns ``(candidates, n_dropped)``. A non-zero ``n_dropped`` means a band hit
    its budget, which is reported rather than silently truncating -- a silent cap
    reads as "covered everything" when it did not.
    """
    if gain_correct:
        gray = _gain_correct(gray, masks["median"])
    allow = cv2.bitwise_and(masks["court"], masks["static"])
    diff = cv2.absdiff(gray, masks["median"])
    diff = cv2.bitwise_and(diff, diff, mask=allow)
    _, bw = cv2.threshold(diff, diff_thresh, 255, cv2.THRESH_BINARY)
    bw = cv2.morphologyEx(bw, cv2.MORPH_OPEN,
                          cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3)))
    n, _lab, stats, cent = cv2.connectedComponentsWithStats(bw, connectivity=8)

    bands = masks["bands"]
    per_band = {i: [] for i in range(len(bands))}
    for k in range(1, n):
        area = int(stats[k, cv2.CC_STAT_AREA])
        if not (area_lo <= area <= area_hi):
            continue
        bw_, bh_ = int(stats[k, cv2.CC_STAT_WIDTH]), int(stats[k, cv2.CC_STAT_HEIGHT])
        if max(bw_, bh_) > aspect_max * max(min(bw_, bh_), 1):
            continue
        x0, y0 = int(stats[k, cv2.CC_STAT_LEFT]), int(stats[k, cv2.CC_STAT_TOP])
        bright = float(gray[y0:y0 + bh_, x0:x0 + bw_].max())
        if bright < min_bright:
            continue
        cx, cy = float(cent[k][0]), float(cent[k][1])
        for bi, (lo, hi) in enumerate(bands):
            if lo <= cy < hi:
                per_band[bi].append(Candidate(cx, cy, area, bright))
                break

    out, dropped = [], 0
    for _bi, items in per_band.items():
        if len(items) > band_budget:
            items.sort(key=lambda c: -c.bright)
            dropped += len(items) - band_budget
            items = items[:band_budget]
        out.extend(items)
    return out, dropped
