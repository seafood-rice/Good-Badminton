"""Single-target shuttle tracking over per-frame candidate blobs.

Two ideas carry this module.

1. Ballistic chaining. A shuttle's next position is close to the constant-velocity
   prediction, so chains are grown by predicting ``2*last - prev`` and accepting
   only near neighbours. ``min_speed`` is enforced on EVERY step, not just the
   seeding triple -- omitting that let a stationary noise blob chain across an
   entire window in the costing spike.

2. The area~speed signature. A real shuttle's apparent size is motion-blur
   length, so blob area tracks instantaneous speed almost proportionally
   (measured 13.7 vs 13.9 on the hand-verified trajectory). Clutter -- a limb, a
   flickering edge -- has no such relation, which makes the correlation a cheap
   and discriminating score.

Selection applies a one-shuttle prior: seed on the best-scoring chain, then extend
only with chains continuous in time and space with what is already accepted. The
alternative -- greedy non-overlapping selection over all chains -- fragmented a
single shuttle into roughly 38 pieces per rally.
"""
import math
from collections import namedtuple

import numpy as np
from scipy.spatial import cKDTree

Candidate = namedtuple("Candidate", "x y area bright")


def _dist(a, b):
    return math.hypot(b[0] - a[0], b[1] - a[1])


def _trees(candidates):
    out = []
    for frame in candidates:
        if frame:
            out.append(cKDTree(np.array([[c.x, c.y] for c in frame], dtype=float)))
        else:
            out.append(None)
    return out


def build_chains(candidates, min_speed=8.0, max_speed=250.0, accel_frac=0.25,
                 accel_px=4.0, min_len=4):
    """Grow ballistic chains of ``(frame_index, Candidate)`` pairs.

    ``candidates`` is a list whose element *i* holds the ``Candidate`` objects
    found in frame *i*.

    ``max_speed`` must stay well below the search band's width: it is used as a
    KD-tree query radius, so a radius wider than the band makes every candidate a
    neighbour of every other and the search degenerates to O(n^2) per frame.
    """
    n = len(candidates)
    if n < 3:
        return []
    trees = _trees(candidates)
    chains = []
    for f in range(n - 2):
        if not (trees[f] and trees[f + 1] and trees[f + 2]):
            continue
        for c1 in candidates[f]:
            for i2 in trees[f + 1].query_ball_point((c1.x, c1.y), max_speed):
                c2 = candidates[f + 1][i2]
                s1 = _dist(c1, c2)
                if s1 < min_speed:
                    continue
                pred = (2 * c2.x - c1.x, 2 * c2.y - c1.y)
                for i3 in trees[f + 2].query_ball_point(pred, accel_frac * s1 + accel_px):
                    c3 = candidates[f + 2][i3]
                    if not (min_speed <= _dist(c2, c3) <= max_speed):
                        continue
                    chain = [(f, c1), (f + 1, c2), (f + 2, c3)]
                    prev, last, k = c2, c3, f + 3
                    while k < n and trees[k]:
                        speed = _dist(prev, last)
                        pr = (2 * last.x - prev.x, 2 * last.y - prev.y)
                        idx = trees[k].query_ball_point(pr, accel_frac * speed + accel_px)
                        nxt = [candidates[k][j] for j in idx
                               if min_speed <= _dist(last, candidates[k][j]) <= max_speed]
                        if not nxt:
                            break
                        pick = min(nxt, key=lambda c: _dist(pr, c))
                        chain.append((k, pick))
                        prev, last = last, pick
                        k += 1
                    if len(chain) >= min_len:
                        chains.append(chain)
                    break
    return chains


def score_chain(chain):
    """Higher is more shuttle-like. Zero when the area~speed relation is absent.

    Squaring the correlation makes a weak relation contribute very little, and the
    brightness term is a mild preference rather than a gate, since a far-court
    shuttle is dimmer than near-court clutter.
    """
    pts = [c for _f, c in chain]
    if len(pts) < 4:
        return 0.0
    speeds = [_dist(pts[i], pts[i + 1]) for i in range(len(pts) - 1)]
    areas = [float(p.area) for p in pts[:-1]]
    if np.std(speeds) < 1e-6 or np.std(areas) < 1e-6:
        return 0.0
    r = float(np.corrcoef(speeds, areas)[0, 1])
    if not math.isfinite(r) or r <= 0.0:
        return 0.0
    bright = float(np.mean([p.bright for p in pts])) / 255.0
    return (r ** 2) * len(chain) * (0.5 + bright)


def select_track(chains, n_frames, max_gap=6, predict_tol_px=40.0):
    """One-shuttle selection: best chain as seed, then continuous extensions.

    Returns ``{frame_index: (x, y)}`` covering only the frames the accepted track
    explains; absent frames are the caller's ``None``.

    A candidate chain joins only if it abuts the accepted track within ``max_gap``
    frames AND its nearest endpoint is within ``predict_tol_px`` per bridged frame,
    which keeps a spatially unrelated chain from being welded on merely because it
    happens to sit in unclaimed frames.
    """
    scored = [(score_chain(c), c) for c in chains]
    scored = sorted((sc for sc in scored if sc[0] > 0.0), key=lambda z: -z[0])
    if not scored:
        return {}

    track = {f: (cand.x, cand.y) for f, cand in scored[0][1]}

    for _s, chain in scored[1:]:
        frames = [f for f, _c in chain]
        if any(f in track for f in frames):
            continue
        known = sorted(track)
        before = [f for f in known if f < frames[0]]
        after = [f for f in known if f > frames[-1]]
        joined = False
        if before:
            gap = frames[0] - before[-1]
            if gap <= max_gap and _dist(track[before[-1]], chain[0][1]) <= predict_tol_px * gap:
                joined = True
        if not joined and after:
            gap = after[0] - frames[-1]
            if gap <= max_gap and _dist(track[after[0]], chain[-1][1]) <= predict_tol_px * gap:
                joined = True
        if not joined:
            continue
        for f, cand in chain:
            if 0 <= f < n_frames:
                track[f] = (cand.x, cand.y)
    return track
