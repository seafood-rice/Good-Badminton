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


# One full overhead clear spans >1.5s (backswing -> contact -> recovery); two
# wrist-speed peaks closer together than that are the forward swing and its
# follow-through/recovery of the SAME stroke, not two separate reps. (Raised
# from 0.8s after IMG_1270 calibration: every double-counted rep pair there
# was 24-26 frames / 0.80-0.87s apart, i.e. right on the old boundary.)
def segment_reps(track, fps, min_gap_sec=1.5, pre=20, post=15, k=1.0,
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

    # Enforce a minimum gap: keep the higher-speed peak within each gap window.
    min_gap = max(1, int(min_gap_sec * fps))
    candidates.sort(key=lambda idx: arr[idx], reverse=True)
    kept = []
    for idx in candidates:
        frame_idx = track[idx]["frame"]
        if all(abs(frame_idx - track[j]["frame"]) >= min_gap for j in kept):
            kept.append(idx)
    kept.sort(key=lambda idx: track[idx]["frame"])

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

        reps.append(RepWindow(
            rep_id=rep_id,
            peak_frame=peak_frame,
            window_start=max(0, peak_frame - pre_f),
            window_end=peak_frame + post_f,
            prominence=round(float(arr[idx]) / max_speed, 3),
        ))

    return reps
