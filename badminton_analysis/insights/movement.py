"""Movement heat-map, recovery to base and pull zones (spec 5.3).

Court coordinates are metres: ``x`` 0-6.1 across, ``y`` 0-13.4 long, net at
``y = 6.7``; ``upper`` is ``y < 6.7`` and ``lower`` is ``y >= 6.7``. Grids are
11 rows (row 0 at the net, row 10 at the baseline) by 10 columns (column 0 at
``x = 0``), the same orientation for both halves.
"""
import math
import statistics
from bisect import bisect_left, bisect_right

from . import envelope
from .loaders import SIDES, number
from .rally import rally_windows

GRID_ROWS = 11
GRID_COLS = 10
BASE = {"upper": (3.05, 3.35), "lower": (3.05, 10.05)}
RECOVERY_RADIUS_M = 1.0
RECOVERY_WINDOW_S = 4.0
PULL_GAP_S = 3.0

COURT_W = 6.1
COURT_L = 13.4
NET_Y = 6.7
OUTSIDE_LIMIT_M = 1.5
MIN_SAMPLES = 30
MAX_STEP_FRAMES = 3
MAX_STEP_SPEED_MPS = 9.0
PULL_FRAME_TOLERANCE = 5
OK_HITS = 10
FEW_HITS = 3

_ROW_M = NET_Y / GRID_ROWS
_COL_M = COURT_W / GRID_COLS


def cell(side, x, y):
    """``(row, col)`` of a court point on ``side``'s half, or None.

    A point more than 1.5 m outside that half is dropped; a nearer one is
    clamped onto the edge cell.
    """
    lo, hi = (0.0, NET_Y) if side == "upper" else (NET_Y, COURT_L)
    dx = max(0.0 - x, 0.0, x - COURT_W)
    dy = max(lo - y, 0.0, y - hi)
    if math.hypot(dx, dy) > OUTSIDE_LIMIT_M:
        return None
    x = min(max(x, 0.0), COURT_W)
    y = min(max(y, lo), hi)
    depth = (NET_Y - y) if side == "upper" else (y - NET_Y)
    row = min(int(depth / _ROW_M), GRID_ROWS - 1)
    col = min(int(x / _COL_M), GRID_COLS - 1)
    return row, col


def _frames(run):
    """Detections sorted by frame, and the frame numbers for bisecting."""
    dets = run.detections or []
    if any(dets[i][0] > dets[i + 1][0] for i in range(len(dets) - 1)):
        dets = sorted(dets, key=lambda d: d[0])
    return dets, [d[0] for d in dets]


def _merged_windows(windows):
    merged = []
    for start, end in sorted(windows):
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return merged


def _shares(counts, total):
    return [[round(c / total, 4) for c in row] for row in counts] if total else \
        [[0.0] * GRID_COLS for _ in range(GRID_ROWS)]


def _grid_side(points, side):
    """Grid, samples and path length for ``points`` = [(frame, t, x, y, window)]."""
    counts = [[0] * GRID_COLS for _ in range(GRID_ROWS)]
    samples = 0
    distance = span = 0.0
    prev = None
    for frame, t, x, y, win in points:
        rc = cell(side, x, y)
        if rc is None:
            prev = None
            continue
        counts[rc[0]][rc[1]] += 1
        samples += 1
        if prev is not None and prev[4] == win:
            dt = t - prev[1]
            if 0 < frame - prev[0] <= MAX_STEP_FRAMES and dt > 0:
                step = math.hypot(x - prev[2], y - prev[3])
                if step / dt <= MAX_STEP_SPEED_MPS:
                    distance += step
                    span += dt
        prev = (frame, t, x, y, win)
    return counts, samples, distance, span


def movement_insight(run, rally):
    if not run.detections:
        return envelope.unavailable("no_positions")
    windows = []
    if (rally and rally.get("value") is not None
            and (rally.get("reason") or {}).get("code") != "signal_degraded"):
        windows = _merged_windows(rally_windows(run.rallies_payload, run.fps))
    starts = [w[0] for w in windows]

    per_side = {s: [] for s in SIDES}
    scope_frames = 0
    for frame, t, pos in run.detections:
        win = None
        if windows:
            i = bisect_right(starts, t) - 1
            if i < 0 or t > windows[i][1]:
                continue
            win = i
        scope_frames += 1
        for side in SIDES:
            p = pos.get(side)
            if p is not None:
                per_side[side].append((frame, t, p[0], p[1], win))

    value, used = {}, 0
    for side in SIDES:
        counts, samples, distance, span = _grid_side(per_side[side], side)
        if samples < MIN_SAMPLES:
            value[side] = None
            continue
        used += samples
        value[side] = {
            "grid": _shares(counts, samples),
            "samples": samples,
            "distance_m": round(distance, 2),
            "mean_speed_mps": round(distance / span, 2) if span > 0 else 0.0,
            "base": {"x": BASE[side][0], "y": BASE[side][1]},
        }
    if all(v is None for v in value.values()):
        return envelope.unavailable("no_positions")
    coverage = {"n": used, "of": scope_frames * len(SIDES)}
    if windows:
        return envelope.ok(value, coverage=coverage)
    return envelope.partial(value, "no_rallies", coverage=coverage)


def _hit_list(run):
    """``[(time_sec, frame, hitter)]`` by frame: hits.json, else strokes.json."""
    if run.hits:
        hits = list(run.hits)
    else:
        fps = run.fps
        hits = []
        for s in run.strokes or []:
            frame, hitter = number(s.get("frame")), s.get("hitter")
            if frame is not None and fps and hitter in SIDES:
                hits.append((frame / fps, int(frame), hitter))
    hits.sort(key=lambda h: h[1])
    return hits


def recovery_stats(run):
    """``({side: {median_sec, found, hits}}, found_total, hits_total)``."""
    dets, frames = _frames(run)
    hits = _hit_list(run)
    times = {s: [] for s in SIDES}
    counts = {s: 0 for s in SIDES}
    for t0, frame, hitter in hits:
        counts[hitter] += 1
        bxy = BASE[hitter]
        i = bisect_left(frames, frame)
        while i < len(dets):
            _f, t, pos = dets[i]
            elapsed = t - t0
            if elapsed > RECOVERY_WINDOW_S:
                break
            p = pos.get(hitter)
            if p is not None and math.hypot(p[0] - bxy[0], p[1] - bxy[1]) <= RECOVERY_RADIUS_M:
                times[hitter].append(max(elapsed, 0.0))
                break
            i += 1
    side_stats = {s: {"median_sec": (round(statistics.median(times[s]), 3)
                                     if times[s] else None),
                      "found": len(times[s]), "hits": counts[s]} for s in SIDES}
    return side_stats, sum(len(v) for v in times.values()), len(hits)


def _graded(value, n, of):
    coverage = {"n": n, "of": of}
    if n >= OK_HITS:
        return envelope.ok(value, coverage=coverage)
    if n >= FEW_HITS:
        return envelope.partial(value, "few_hits", coverage=coverage)
    return envelope.unavailable("no_hits")


def recovery_insight(run):
    if not run.detections:
        return envelope.unavailable("no_positions")
    stats, found, total = recovery_stats(run)
    return _graded(stats, found, total)


def _position_near(dets, frames, frame, side):
    """``side``'s court position at the detection nearest ``frame`` (within 5 frames)."""
    best = None
    lo = bisect_left(frames, frame - PULL_FRAME_TOLERANCE)
    hi = bisect_right(frames, frame + PULL_FRAME_TOLERANCE)
    for j in range(lo, hi):
        p = dets[j][2].get(side)
        if p is not None:
            gap = abs(frames[j] - frame)
            if best is None or gap < best[0]:
                best = (gap, p)
    return best[1] if best else None


def pull_stats(run):
    """``({side: Pull}, used_pairs, eligible_pairs)``; a Pull belongs to A's side."""
    dets, frames = _frames(run)
    hits = _hit_list(run)
    counts = {s: [[0] * GRID_COLS for _ in range(GRID_ROWS)] for s in SIDES}
    n = {s: 0 for s in SIDES}
    eligible = 0
    for (ta, _fa, a), (tb, fb, b) in zip(hits, hits[1:]):
        if a == b or tb - ta > PULL_GAP_S:
            continue
        eligible += 1
        p = _position_near(dets, frames, fb, b)
        rc = cell(b, p[0], p[1]) if p is not None else None
        if rc is None:
            continue
        counts[a][rc[0]][rc[1]] += 1
        n[a] += 1
    out = {}
    for s in SIDES:
        c, total = counts[s], n[s]

        def share(r0, r1, c0, c1):
            hit = sum(c[r][k] for r in range(r0, r1) for k in range(c0, c1))
            return round(hit / total, 4) if total else 0.0

        out[s] = {
            "grid": _shares(c, total),
            "front": share(0, 4, 0, GRID_COLS), "mid": share(4, 8, 0, GRID_COLS),
            "back": share(8, GRID_ROWS, 0, GRID_COLS),
            "left": share(0, GRID_ROWS, 0, 5), "right": share(0, GRID_ROWS, 5, GRID_COLS),
            "n": total,
        }
    return out, sum(n.values()), eligible


def pull_insight(run):
    if not run.detections:
        return envelope.unavailable("no_positions")
    stats, used, eligible = pull_stats(run)
    return _graded(stats, used, eligible)
