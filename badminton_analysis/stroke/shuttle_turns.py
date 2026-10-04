"""Frames where the shuttle path turns sharply.

Approach A of the hit-detection design: the free baseline every learned model is
compared against, and the signal the ShuttleSet alignment check correlates with
labelled hit times. Not used by the match pipeline's hit list.
"""
import numpy as np


def shuttle_turn_events(shuttle, fps, min_turn_deg=45.0, lookahead_sec=0.05, min_gap_sec=0.25):
    pts = np.asarray(shuttle, dtype=np.float64)
    n = len(pts)
    look = max(1, int(round(lookahead_sec * fps)))
    gap = max(1, int(round(min_gap_sec * fps)))
    events, last = [], -10 ** 9
    for i in range(1, n - look):
        a, b, c = pts[i - 1], pts[i], pts[i + look]
        if not (np.isfinite(a).all() and np.isfinite(b).all() and np.isfinite(c).all()):
            continue
        v1, v2 = b - a, c - b
        n1, n2 = np.linalg.norm(v1), np.linalg.norm(v2)
        if n1 < 1e-6 or n2 < 1e-6:
            continue
        angle = np.degrees(np.arccos(np.clip(np.dot(v1, v2) / (n1 * n2), -1.0, 1.0)))
        if angle >= min_turn_deg and i - last >= gap:
            events.append(i)
            last = i
    return events
