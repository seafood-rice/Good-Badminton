"""Per-gap features and labels for the point-end segmenter (spec 5.1).

One example per gap between consecutive learned hits. A gap is positive when an
owner point end lies in ``[gap_start - 1, gap_end + 1]``.
"""
import bisect
import math
from dataclasses import dataclass, field

import numpy as np

from . import labels as lab

SIDES = ("upper", "lower")
NET_Y = 6.7
COURT_W = 6.1
SERVICE_FRONT_M = 1.98       # short service line, from the net
SIDE_LINE_X = (0.46, 5.64)   # singles side lines
CENTRE_X = 3.05
POS_WINDOW = 8               # frames either side of a hit when looking up a position
WALK_HZ = 5.0                # positions are sampled at this rate to sum the walked distance
WALK_STEP_MAX_M = 3.0        # a step longer than this is a tracking jump, not walking
LABEL_PAD_SEC = 1.0

FEATURES = ("gap_sec", "log_gap_sec", "swing_mean", "swing_max", "walk_upper", "walk_lower",
            "svc_left", "svc_right", "same_side", "shuttle_share")


@dataclass
class Gap:
    start: float
    end: float
    features: dict = field(default_factory=dict)
    index: int = -1   # the gap lies between hits[index] and hits[index + 1]


def _lookup(frames, frame):
    for d in range(POS_WINDOW + 1):
        for f in ((frame,) if d == 0 else (frame - d, frame + d)):
            if f in frames:
                return frames[f]
    return None


def _walked(frames, ordered, fps, start_frame, end_frame):
    """Metres walked between two frames, sampled at WALK_HZ; jumps are ignored."""
    if not ordered:
        return 0.0
    step = max(1, int(round(fps / WALK_HZ)))
    lo = bisect.bisect_left(ordered, start_frame)
    hi = bisect.bisect_right(ordered, end_frame)
    total, prev, next_frame = 0.0, None, None
    for f in ordered[lo:hi]:
        if next_frame is not None and f < next_frame:
            continue
        next_frame = f + step
        xy = frames[f]
        if prev is not None:
            d = math.hypot(xy[0] - prev[0], xy[1] - prev[1])
            if d <= WALK_STEP_MAX_M:
                total += d
        prev = xy
    return total


def _service_flags(side, xy):
    """``(left, right)``: the hitter stands inside a service court of its own half."""
    if xy is None:
        return 0.0, 0.0
    x, y = xy
    depth = (NET_Y - y) if side == "upper" else (y - NET_Y)
    if not (SERVICE_FRONT_M <= depth <= NET_Y) or not (SIDE_LINE_X[0] <= x <= SIDE_LINE_X[1]):
        return 0.0, 0.0
    return (1.0, 0.0) if x < CENTRE_X else (0.0, 1.0)


def gaps(inputs):
    """``[Gap]`` for every pair of consecutive learned hits; [] without two hits."""
    hits = list(inputs.hits or [])
    if len(hits) < 2:
        return []
    fps = float(inputs.fps)
    swing_t = np.array([t for t, _ in inputs.swing], dtype=float)
    swing_a = np.array([a for _, a in inputs.swing], dtype=float)
    shuttle = np.array(sorted(inputs.shuttle_frames or ()), dtype=np.int64)
    ordered = {s: sorted((inputs.positions or {}).get(s) or {}) for s in SIDES}
    ok = inputs.positions_ok or {}
    out = []
    for i in range(len(hits) - 1):
        a, b = hits[i], hits[i + 1]
        start, end = float(a[0]), float(b[0])
        length = max(end - start, 1e-3)
        lo = int(np.searchsorted(swing_t, start, side="left"))
        hi = int(np.searchsorted(swing_t, end, side="right"))
        seg = swing_a[lo:hi]
        walk = {}
        for side in SIDES:
            walk[side] = (_walked(inputs.positions[side], ordered[side], fps, int(a[1]), int(b[1]))
                          if ok.get(side) else 0.0)
        hitter_xy = _lookup(inputs.positions.get(b[2]) or {}, int(b[1])) if ok.get(b[2]) else None
        left, right = _service_flags(b[2], hitter_xy)
        n_frames = max(1, int(b[1]) - int(a[1]) + 1)
        seen = int(np.searchsorted(shuttle, int(b[1]), side="right") - np.searchsorted(shuttle, int(a[1]), side="left"))
        out.append(Gap(start, end, {
            "gap_sec": length,
            "log_gap_sec": math.log(length),
            "swing_mean": float(seg.mean()) if seg.size else 0.0,
            "swing_max": float(seg.max()) if seg.size else 0.0,
            "walk_upper": walk["upper"], "walk_lower": walk["lower"],
            "svc_left": left, "svc_right": right,
            "same_side": 1.0 if a[2] == b[2] else 0.0,
            "shuttle_share": min(1.0, seen / n_frames),
        }, i))
    return out


def labels_for(gap_list, owner_labels_doc, segments=()):   # segments: kept for the interface
    """``[1 | 0 | None]`` per gap (None = not usable as an example).

    Positive: an owner point end (a labelled winner, segment label or added point) lies
    in ``[start - 1, end + 1]``. Unusable: no label document, or an ``unsure`` label ends
    there. Negative: everything else, including gaps inside an owner ``not_point`` segment.
    """
    if not isinstance(owner_labels_doc, dict) or not owner_labels_doc.get("labels"):
        return [None] * len(gap_list)
    labels = owner_labels_doc["labels"]
    point_ends = sorted(l["end_sec"] for l in labels if l["verdict"] in lab.SIDES)
    unsure = sorted(l["end_sec"] for l in labels if l["verdict"] == "unsure")
    out = []
    for g in gap_list:
        lo, hi = g.start - LABEL_PAD_SEC, g.end + LABEL_PAD_SEC
        i = bisect.bisect_left(point_ends, lo)
        if i < len(point_ends) and point_ends[i] <= hi:
            out.append(1)
            continue
        j = bisect.bisect_left(unsure, lo)
        if j < len(unsure) and unsure[j] <= hi:
            out.append(None)
            continue
        out.append(0)
    return out
