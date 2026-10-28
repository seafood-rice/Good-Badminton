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
LABEL_PAD_SEC = 1.0      # an unsure label this close to a gap makes it unusable
NEAREST_GAP_SEC = 1.0    # an end outside every gap labels the gap whose midpoint is this close
SWING_DIST_CAP_SEC = 10.0

FEATURES = ("gap_sec", "log_gap_sec", "swing_mean", "swing_max", "walk_upper", "walk_lower",
            "svc_left", "svc_right", "same_side", "shuttle_share",
            "swing_end_dist", "swing_outside", "swing_end_in_gap")


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
    swing_segs = [(float(a), float(b)) for a, b in (getattr(inputs, "swing_segments", None) or ())]
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
        ends = [e for _s, e in swing_segs]
        inside = [e for e in ends if start <= e <= end]
        dist = 0.0 if inside else min([min(abs(e - start), abs(e - end)) for e in ends] + [SWING_DIST_CAP_SEC])
        overlaps = any(a <= end and b >= start for a, b in swing_segs)
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
            "swing_end_dist": min(dist, SWING_DIST_CAP_SEC),
            "swing_outside": 0.0 if overlaps else 1.0,
            "swing_end_in_gap": 1.0 if inside else 0.0,
        }, i))
    return out


def labels_for(gap_list, owner_labels_doc, segments=()):   # segments: kept for the interface
    """``[1 | 0 | None]`` per gap (None = not usable as an example).

    Exactly one positive gap per owner point end (a labelled winner, segment label or
    added point): the gap containing the end time, else the gap whose midpoint is nearest
    and within ``NEAREST_GAP_SEC``; an end with no such gap labels nothing. Unusable: no
    label document or an empty label list, or an ``unsure`` label ends within
    ``LABEL_PAD_SEC`` of the gap. Negative: every other gap, including those inside an
    owner ``not_point`` segment.
    """
    if not isinstance(owner_labels_doc, dict) or not owner_labels_doc.get("labels"):
        return [None] * len(gap_list)
    labels = owner_labels_doc["labels"]
    positive = set()
    for label in labels:
        if label["verdict"] not in lab.SIDES:
            continue
        e = label["end_sec"]
        containing = [i for i, g in enumerate(gap_list) if g.start <= e <= g.end]
        if containing:
            positive.add(containing[0])
            continue
        near = [(abs((g.start + g.end) / 2 - e), i) for i, g in enumerate(gap_list)]
        near = [n for n in near if n[0] <= NEAREST_GAP_SEC]
        if near:
            positive.add(min(near)[1])
    unsure = sorted(l["end_sec"] for l in labels if l["verdict"] == "unsure")
    out = []
    for i, g in enumerate(gap_list):
        if i in positive:
            out.append(1)
            continue
        j = bisect.bisect_left(unsure, g.start - LABEL_PAD_SEC)
        out.append(None if j < len(unsure) and unsure[j] <= g.end + LABEL_PAD_SEC else 0)
    return out


# --- swing-anchored veto mode: one example per swing segment -----------------------------

VETO_FEATURES = ("seg_dur", "hits_n", "alt_share", "max_hit_gap", "swing_mean", "swing_max",
                 "shuttle_share", "gap_prev", "gap_next", "walk_upper_mps", "walk_lower_mps",
                 "svc_first")
NEIGHBOUR_CAP_SEC = 30.0


@dataclass
class Seg:
    start: float
    end: float
    features: dict = field(default_factory=dict)
    index: int = -1   # index into the sorted ``inputs.swing_segments``


def segment_rows(inputs):
    """``[Seg]``: one per swing segment (``inputs.swing_segments``) with the veto features."""
    segs = sorted((float(a), float(b)) for a, b in (getattr(inputs, "swing_segments", None) or ()))
    if not segs:
        return []
    hits = list(inputs.hits or [])
    fps = float(inputs.fps)
    swing_t = np.array([t for t, _ in inputs.swing], dtype=float)
    swing_a = np.array([a for _, a in inputs.swing], dtype=float)
    shuttle = np.array(sorted(inputs.shuttle_frames or ()), dtype=np.int64)
    ordered = {s: sorted((inputs.positions or {}).get(s) or {}) for s in SIDES}
    ok = inputs.positions_ok or {}
    out = []
    for k, (start, end) in enumerate(segs):
        dur = max(end - start, 1e-3)
        inside = [h for h in hits if start <= h[0] <= end]
        times = [h[0] for h in inside]
        pairs = list(zip(inside, inside[1:]))
        alt = sum(1 for a, b in pairs if a[2] != b[2]) / len(pairs) if pairs else 0.0
        lo = int(np.searchsorted(swing_t, start, side="left"))
        hi = int(np.searchsorted(swing_t, end, side="right"))
        act = swing_a[lo:hi]
        f0, f1 = int(round(start * fps)), int(round(end * fps))
        seen = int(np.searchsorted(shuttle, f1, side="right") - np.searchsorted(shuttle, f0, side="left"))
        walk = {s: (_walked(inputs.positions[s], ordered[s], fps, f0, f1) / dur if ok.get(s) else 0.0)
                for s in SIDES}
        svc = 0.0
        if inside and ok.get(inside[0][2]):
            xy = _lookup(inputs.positions.get(inside[0][2]) or {}, int(inside[0][1]))
            svc = 1.0 if any(_service_flags(inside[0][2], xy)) else 0.0
        prev_gap = start - segs[k - 1][1] if k > 0 else NEIGHBOUR_CAP_SEC
        next_gap = segs[k + 1][0] - end if k + 1 < len(segs) else NEIGHBOUR_CAP_SEC
        out.append(Seg(start, end, {
            "seg_dur": dur, "hits_n": float(len(inside)), "alt_share": alt,
            "max_hit_gap": max((b - a for a, b in zip(times, times[1:])), default=0.0),
            "swing_mean": float(act.mean()) if act.size else 0.0,
            "swing_max": float(act.max()) if act.size else 0.0,
            "shuttle_share": min(1.0, seen / max(1, f1 - f0 + 1)),
            "gap_prev": min(NEIGHBOUR_CAP_SEC, max(0.0, prev_gap)),
            "gap_next": min(NEIGHBOUR_CAP_SEC, max(0.0, next_gap)),
            "walk_upper_mps": walk["upper"], "walk_lower_mps": walk["lower"], "svc_first": svc,
        }, k))
    return out


def veto_labels_for(seg_list, owner_labels_doc):
    """``[1 | 0 | None]`` per swing segment: 0 when an owner ``not_point`` label attaches to it,
    1 when an owner point verdict (upper / lower) does, None otherwise (unlabelled, unsure,
    or no label document)."""
    if not isinstance(owner_labels_doc, dict) or not owner_labels_doc.get("labels"):
        return [None] * len(seg_list)
    rows = [{"id": i, "start_sec": s.start, "end_sec": s.end} for i, s in enumerate(seg_list)]
    by_segment, _added = lab.attach(owner_labels_doc["labels"], rows)
    out = []
    for i in range(len(seg_list)):
        label = by_segment.get(i)
        verdict = label["verdict"] if label else None
        out.append(0 if verdict == "not_point" else 1 if verdict in lab.SIDES else None)
    return out
