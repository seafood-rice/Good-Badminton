"""Shuttlecock-contact stroke event detection."""
from dataclasses import dataclass, asdict

import numpy as np


@dataclass
class StrokeEvent:
    stroke_type: str
    contact_frame: int
    window_start: int
    window_end: int
    player_side: str
    confidence: float

    def to_dict(self):
        return asdict(self)


def _dist(a, b):
    return float(np.hypot(a[0] - b[0], a[1] - b[1]))


def _seg_angle(p, q):
    return float(np.degrees(np.arctan2(q[1] - p[1], q[0] - p[0])))


def _shuttle_dir_change(track, i, lookahead):
    """Direction change (deg, 0..180) of shuttle travel around index i. None if data missing."""
    if i - 1 < 0 or i + lookahead >= len(track):
        return None
    a = track[i - 1]["shuttle"]
    b = track[i]["shuttle"]
    c = track[i + lookahead]["shuttle"]
    if a is None or b is None or c is None:
        return None
    if _dist(a, b) < 1e-6 or _dist(b, c) < 1e-6:
        return None
    before = _seg_angle(a, b)
    after = _seg_angle(b, c)
    diff = abs(after - before) % 360.0
    return diff if diff <= 180.0 else 360.0 - diff


def detect_contacts(track, contact_px=80.0, lookahead=3, dir_change_deg=45.0,
                    window_pre=20, window_post=15, min_gap=15):
    """Find racket-shuttle impacts. Returns list of {contact_frame, window_start, window_end}."""
    contacts = []
    last_contact_frame = None
    for i, rec in enumerate(track):
        racket = rec.get("racket_head")
        shuttle = rec.get("shuttle")
        if racket is None or shuttle is None:
            continue
        if _dist(racket, shuttle) >= contact_px:
            continue
        change = _shuttle_dir_change(track, i, lookahead)
        if change is None or change < dir_change_deg:
            continue
        frame = rec["frame"]
        if last_contact_frame is not None and (frame - last_contact_frame) < min_gap:
            continue
        contacts.append({
            "contact_frame": frame,
            "window_start": max(0, frame - window_pre),
            "window_end": frame + window_post,
        })
        last_contact_frame = frame
    return contacts
