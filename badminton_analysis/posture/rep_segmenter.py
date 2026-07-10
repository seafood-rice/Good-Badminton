"""Segment a single-player drill clip into individual stroke reps from wrist swing motion."""
from dataclasses import dataclass, asdict

import numpy as np


@dataclass
class RepWindow:
    rep_id: int
    peak_frame: int
    window_start: int
    window_end: int
    prominence: float

    def to_dict(self):
        return asdict(self)


def _dist(a, b):
    return float(np.hypot(a[0] - b[0], a[1] - b[1]))


# Genuine wrist speed tops out ~30-80 px/frame at 1080p/30fps; a person-flip
# (multi-person scenes picking a different person) or a hard cut jumps the
# tracked wrist 300-800 px in a single frame. Deltas far above the clip's
# own typical motion are track breaks, not motion.
TELEPORT_MIN_PX = 100.0
TELEPORT_MEDIAN_MULT = 10.0


def _wrist_speed(track):
    """Per-position wrist speed (px/frame); 0 where either endpoint is
    missing, or where the delta is an implausible teleport (identity switch
    or hard cut) rather than real motion.
    """
    speed = [0.0] * len(track)
    for i in range(1, len(track)):
        a = track[i - 1].get("wrist")
        b = track[i].get("wrist")
        if a is not None and b is not None:
            speed[i] = _dist(a, b)

    positive = [s for s in speed if s > 0.0]
    if positive:
        teleport_floor = max(TELEPORT_MIN_PX, TELEPORT_MEDIAN_MULT * float(np.median(positive)))
        for i in range(len(speed)):
            if speed[i] > teleport_floor:
                speed[i] = 0.0
    return speed


def _smooth(values, window):
    if window <= 1 or len(values) == 0:
        return list(values)
    out = []
    half = window // 2
    for i in range(len(values)):
        lo = max(0, i - half)
        hi = min(len(values), i + half + 1)
        out.append(sum(values[lo:hi]) / (hi - lo))
    return out


# A fixed time gap cannot separate a swing's own follow-through from a
# genuinely separate fast stroke: on IMG_1270 intra-swing peaks sit ~0.8s
# apart, but on IMG_9691 distinct fed strokes are ~0.7s apart too - the two
# cases overlap in timing. The real discriminator is the wrist-speed VALLEY
# between the two peaks: within one swing the wrist never rests (valley stays
# 25-100% of the smaller peak); between distinct strokes the wrist resets
# (valley drops to 1-5%). VALLEY_RATIO sits cleanly in that gap. MIN_SEP_SEC
# is a noise floor: peaks closer than this are always the same stroke
# regardless of valley depth.
VALLEY_RATIO = 0.15
MIN_SEP_SEC = 0.3


def segment_reps(track, fps, pre=20, post=15, k=1.0,
                 smooth=3, min_speed_px=5.0, max_reps=50):
    if not track:
        return []

    speed = _smooth(_wrist_speed(track), smooth)
    arr = np.asarray(speed, dtype=float)
    if arr.size == 0 or float(np.max(arr)) <= 0.0:
        return []

    threshold = float(np.mean(arr) + k * np.std(arr))
    floor = max(threshold, min_speed_px)

    # Candidate local maxima above the floor.
    candidates = []
    for i in range(len(arr)):
        v = arr[i]
        if v < floor:
            continue
        left_ok = i == 0 or arr[i - 1] <= v
        right_ok = i == len(arr) - 1 or arr[i + 1] <= v
        if left_ok and right_ok:
            candidates.append(i)

    if not candidates:
        return []

    # Valley merge: candidates are already in time order (local maxima scan
    # above runs low-to-high index). Two peaks are the SAME stroke when the
    # wrist never rests between them (min speed between > VALLEY_RATIO of the
    # smaller peak) or when they're closer than the MIN_SEP_SEC noise floor.
    # A deeper valley = a reset = a genuinely new stroke.
    min_sep_frames = max(1, int(MIN_SEP_SEC * fps))
    kept = []
    for cand in candidates:
        if not kept:
            kept.append(cand)
            continue
        last = kept[-1]
        frames_apart = track[cand]["frame"] - track[last]["frame"]
        valley = float(np.min(arr[last:cand + 1]))
        same_stroke = frames_apart < min_sep_frames or valley > VALLEY_RATIO * min(arr[last], arr[cand])
        if same_stroke:
            # Merge: keep the higher-speed peak as this stroke's representative.
            if arr[cand] > arr[last]:
                kept[-1] = cand
        else:
            kept.append(cand)  # genuine reset -> new stroke

    scale = fps / 30.0
    pre_f = int(round(pre * scale))
    post_f = int(round(post * scale))
    max_speed = float(np.max(arr)) or 1.0

    if len(kept) > max_reps:
        print("segment_reps: detected " + str(len(kept)) + " reps, capping at max_reps=" + str(max_reps))

    reps = []
    for rep_id, idx in enumerate(kept[:max_reps], start=1):
        peak_frame = track[idx]["frame"]
        window_start = max(0, peak_frame - pre_f)
        window_end = peak_frame + post_f

        # Shuttle refinement: snap to the in-window frame where shuttle is closest to wrist.
        best_pos, best_d = None, None
        for p in range(len(track)):
            f = track[p]["frame"]
            if f < window_start or f > window_end:
                continue
            sh = track[p].get("shuttle")
            wr = track[p].get("wrist")
            if sh is not None and wr is not None:
                d = _dist(sh, wr)
                if best_d is None or d < best_d:
                    best_d = d
                    best_pos = p
        if best_pos is not None:
            peak_frame = track[best_pos]["frame"]
        else:
            # No shuttle to anchor contact: fall back to the wrist apex (highest
            # point = min image-y) in the window. For overhead strokes this sits at
            # or near contact and avoids centering the rep on the faster
            # follow-through/backswing speed peak. Overhead-oriented heuristic: for
            # an underhand serve the apex is not the contact, but serves are
            # typically shuttle-anchored so this fallback rarely applies to them.
            apex_pos, apex_y = None, None
            for p in range(len(track)):
                f = track[p]["frame"]
                if f < window_start or f > window_end:
                    continue
                wr = track[p].get("wrist")
                if wr is not None and (apex_y is None or wr[1] < apex_y):
                    apex_y = wr[1]
                    apex_pos = p
            if apex_pos is not None:
                peak_frame = track[apex_pos]["frame"]

        reps.append(RepWindow(
            rep_id=rep_id,
            peak_frame=peak_frame,
            window_start=max(0, peak_frame - pre_f),
            window_end=peak_frame + post_f,
            prominence=round(float(arr[idx]) / max_speed, 3),
        ))

    return reps
