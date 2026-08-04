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

    # Precomputed per-frame constants. Profiling a 4K frame showed the combined mask
    # cost 1.37 ms every frame for a value that never changes, and that restricting
    # work to the court's bounding box removes 29% of the pixels touched.
    allow = cv2.bitwise_and(court, static)
    cols = np.nonzero(court.any(axis=0))[0]
    x_lo, x_hi = (int(cols[0]), int(cols[-1]) + 1) if cols.size else (0, w)
    roi = (x_lo, y_lo, x_hi, y_hi)

    return {"court": court, "static": static, "median": median, "bands": bands,
            "allow": allow, "roi": roi,
            "median_roi": median[y_lo:y_hi, x_lo:x_hi],
            "allow_roi": allow[y_lo:y_hi, x_lo:x_hi]}


def _gain_correct(gray, median):
    """Rescale ``gray`` to the background's mean level.

    Auto-exposure drift shifts the whole frame, and once the overall level departs
    from the background model every pixel exceeds the difference threshold and the
    frame floods with candidates. The ratio is clamped so a frame dominated by one
    large bright object cannot distort the correction -- which also means a very
    large drift is deliberately left uncorrected rather than wrongly rescaled.

    Implemented as an 8-bit lookup table. Profiling showed the equivalent float pass
    (astype float32, multiply, clip, astype uint8) cost 30.9 ms on a 4K frame -- a
    third of the whole per-frame budget -- while a LUT is a single table lookup.
    Results are identical because both truncate toward zero.
    """
    cur = float(gray.mean())
    if cur <= 1e-6:
        return gray
    ratio = float(median.mean()) / cur
    lo, hi = GAIN_LIMITS
    if not (lo < ratio < hi):
        return gray
    if abs(ratio - 1.0) < 1e-3:      # already matched; skip the work entirely
        return gray
    table = np.clip(np.arange(256, dtype=np.float32) * ratio, 0, 255).astype(np.uint8)
    return cv2.LUT(gray, table)


def frame_candidates(gray, masks, diff_thresh=35, area_lo=15, area_hi=1500,
                     aspect_max=2.5, min_bright=110.0,
                     band_budget=DEFAULT_BAND_BUDGET, gain_correct=True):
    """Compact bright movers in this frame, budgeted per depth band.

    Returns ``(candidates, n_dropped)``. A non-zero ``n_dropped`` means a band hit
    its budget, which is reported rather than silently truncating -- a silent cap
    reads as "covered everything" when it did not.
    """
    # Work inside the court's bounding box only; 29% of a 4K frame lies outside it and
    # is masked away regardless. Coordinates are offset back to full-frame at the end.
    rx0, ry0, rx1, ry1 = masks["roi"]
    roi = gray[ry0:ry1, rx0:rx1]
    if gain_correct:
        roi = _gain_correct(roi, masks["median_roi"])
    diff = cv2.absdiff(roi, masks["median_roi"])
    diff = cv2.bitwise_and(diff, diff, mask=masks["allow_roi"])
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
        # Per-component slicing, deliberately: profiling showed scipy.ndimage.maximum
        # over the label image costs 288 ms against this loop's 1.4 ms, because the
        # loop only touches each component's own bounding box.
        bright = float(roi[y0:y0 + bh_, x0:x0 + bw_].max())
        if bright < min_bright:
            continue
        cx, cy = float(cent[k][0]) + rx0, float(cent[k][1]) + ry0
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


_PC_W, _PC_H = 320, 180
_MIN_PC_RESPONSE = 0.15

WINDOW_FRAMES = 300
WINDOW_OVERLAP = 30


def _track_windowed(per_frame, build_chains, select_track,
                    window=WINDOW_FRAMES, overlap=WINDOW_OVERLAP):
    """Track in overlapping windows, then merge. Fixes two whole-video defects.

    1. The one-shuttle prior in ``select_track`` seeds on a single chain and extends
       only with abutting ones. That is right WITHIN a flight, but run over a whole
       video it can return only one trajectory plus its neighbours -- and a match has
       hundreds of flights, each separated by a contact that breaks ballistic
       continuity. Windowing lets every flight be selected in its own window.
    2. Chain search does not scale. Measured: a 420-frame window built chains in 8 s,
       but 4,376 frames had not finished after ~400 s of chain time, because chain
       count grows far faster than frame count. A bounded window makes cost linear in
       video length.

    ``window`` is 300 frames (5 s at 60 fps) -- comfortably longer than a single
    flight (the hand-verified arc is 30 frames) while keeping the search bounded.
    ``overlap`` ensures a flight straddling a boundary is wholly inside one window.
    On overlapping frames the window that holds the frame more CENTRALLY wins, since
    it saw more of that flight's context on both sides.
    """
    n = len(per_frame)
    if n == 0:
        return {}
    step = max(1, window - overlap)
    merged, centrality = {}, {}
    start = 0
    while start < n:
        end = min(start + window, n)
        chunk = per_frame[start:end]
        local = select_track(build_chains(chunk), len(chunk))
        mid = (end - start) / 2.0
        for lf, pt in local.items():
            gf = start + lf
            score = -abs(lf - mid)          # nearer the window centre is better
            if gf not in merged or score > centrality[gf]:
                merged[gf] = pt
                centrality[gf] = score
        if end >= n:
            break
        start += step
    return merged


def is_static_camera(video_path, samples=12, max_shift_px=3.0):
    """True when the camera does not move, which this detector requires.

    Phase correlation between CONSECUTIVE frame pairs on downscaled frames, with a
    Hanning window and a response floor. Three details matter, each learned from a
    measurement that went wrong without it:

    * A Hanning window is required. Without it, spectral leakage from the frame
      edges produced spurious peaks at half the downscaled width (160 px of 320) and
      the verdict inverted -- a static clip read as moving more than a panning one.
    * Pairs are CONSECUTIVE, not spaced. Spaced pairs accumulate real scene motion
      (a player crossing, the shuttle) which is not camera motion.
    * A low correlation response means the estimate is unreliable (a low-texture or
      ambiguous frame); those pairs are discarded rather than voted with.

    The MEDIAN of the surviving shifts decides, so one bad pair -- a flash, a cut --
    cannot flip the verdict.

    ``max_shift_px`` is in ORIGINAL frame pixels per frame, not working-size pixels:
    the measured shift is scaled back up by the downscale factor. Otherwise the
    threshold means different physical things at different input resolutions, and a
    slow broadcast pan at 1080p would fall under the same number that catches a fast
    pan at 4K.

    The 3.0 default comes from measuring this project's own footage rather than from
    taste. Median per-frame shift, in original pixels:

        DJI hall (tripod)          0.0
        broadcast 1080p            1.37   (samples 0.00 .. 2.47, responses 0.79-1.01)

    So the broadcast clip is a FIXED elevated camera with sub-3px jitter, not a pan --
    which corrects the design doc's assumption that broadcast footage necessarily
    moves. A threshold of 1.5 put that clip at 1.37, close enough that a different
    sample set could flip the routing for the same video; 3.0 clears both real clips
    with margin while still catching a genuine pan, which runs well above 5 px/frame.

    NOTE the open question this exposes: sub-3px jitter is classified static and will
    therefore route to the classical detector, but whether a median background model
    survives that jitter is untested. See the design doc's Task 5 record.
    """
    cap = cv2.VideoCapture(str(video_path))
    try:
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        if total < 4:
            return True
        src_w = float(cap.get(cv2.CAP_PROP_FRAME_WIDTH)) or float(_PC_W)
        src_h = float(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) or float(_PC_H)
        sx, sy = src_w / _PC_W, src_h / _PC_H
        win = cv2.createHanningWindow((_PC_W, _PC_H), cv2.CV_32F)
        step = max(2, total // (samples + 1))
        shifts = []
        for i in range(samples):
            start = min(i * step, max(0, total - 2))
            cap.set(cv2.CAP_PROP_POS_FRAMES, start)
            ok_a, fa = cap.read()
            ok_b, fb = cap.read()
            if not (ok_a and ok_b):
                break
            a = cv2.resize(cv2.cvtColor(fa, cv2.COLOR_BGR2GRAY),
                           (_PC_W, _PC_H)).astype(np.float32)
            b = cv2.resize(cv2.cvtColor(fb, cv2.COLOR_BGR2GRAY),
                           (_PC_W, _PC_H)).astype(np.float32)
            (dx, dy), resp = cv2.phaseCorrelate(a, b, win)
            if resp < _MIN_PC_RESPONSE:
                continue
            shifts.append(((dx * sx) ** 2 + (dy * sy) ** 2) ** 0.5)
        if not shifts:
            return True
        return bool(float(np.median(shifts)) < max_shift_px)
    finally:
        cap.release()


def track_video(video_path, quad, max_frames=None, warmup=48, refresh_every=600,
                reservoir=48, progress=None):
    """Classical dense shuttle trajectory for a static-camera clip.

    Returns ``(trajectory, stats)`` where ``trajectory`` is keyed by 0-based frame
    index and matches the value contract of ``tracknet.track_video``, so system.py
    applies the same ``+1`` alignment it already applies to TrackNet.

    Decode is a large share of per-frame cost, so this makes a SINGLE pass: the first
    ``warmup`` frames seed the background model and are then scored from the buffer.
    A background built once decays -- over a multi-minute clip the court gains bags,
    benches shift and players linger, and each such change becomes a permanent false
    mover -- so a reservoir of recent frames rebuilds it every ``refresh_every``
    frames, still without decoding anything twice.
    """
    from collections import deque

    from .shuttle_tracker import build_chains, select_track

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        cap.release()
        raise ValueError(f"cannot open video: {video_path}")
    try:
        warm = []
        recent = deque(maxlen=max(4, int(reservoir)))
        masks, refreshes = None, 0
        per_frame, dropped_total, cand_total = [], 0, 0
        idx = 0
        sample_step = max(1, warmup // max(4, int(reservoir)))

        while True:
            if max_frames is not None and idx >= max_frames:
                break
            ok, frame = cap.read()
            if not ok:
                break
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

            if masks is None:
                warm.append(gray)
                if len(warm) >= warmup:
                    masks = build_masks(warm, quad)
                    recent.extend(warm[::sample_step])
                    for g in warm:
                        cands, drop = frame_candidates(g, masks)
                        per_frame.append(cands)
                        cand_total += len(cands)
                        dropped_total += drop
                    warm = []
            else:
                if idx % 8 == 0:
                    recent.append(gray)
                if refresh_every and idx % refresh_every == 0 and len(recent) >= 4:
                    masks = build_masks(list(recent), quad)
                    refreshes += 1
                cands, drop = frame_candidates(gray, masks)
                per_frame.append(cands)
                cand_total += len(cands)
                dropped_total += drop

            idx += 1
            if progress is not None and idx % 200 == 0:
                progress(idx)

        if masks is None:                      # clip shorter than warmup
            if not warm:
                return {}, {"frames": 0, "candidates": 0, "dropped": 0,
                            "covered": 0, "refreshes": 0}
            masks = build_masks(warm, quad)
            for g in warm:
                cands, drop = frame_candidates(g, masks)
                per_frame.append(cands)
                cand_total += len(cands)
                dropped_total += drop

        n = len(per_frame)
        track = _track_windowed(per_frame, build_chains, select_track)
        traj = {f: track.get(f) for f in range(n)}
        stats = {"frames": n, "candidates": cand_total, "dropped": dropped_total,
                 "covered": sum(1 for v in traj.values() if v is not None),
                 "refreshes": refreshes}
        return traj, stats
    finally:
        cap.release()
