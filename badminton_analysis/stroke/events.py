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


CONTACT_M = 0.30
"""Contact radius in metres, for the perspective-correct gate.

A fixed pixel radius is wrong across a perspective gradient. Measured on the DJI
footage, 80 px is 0.13 m at the near baseline (603.8 px/m) but 0.75 m at the far
one (106.3 px/m) -- so it accepts non-contacts far away and rejects real contacts
close up. At the mid-court scale of ~355 px/m, 0.30 m is ~107 px, comparable to
the legacy 80 px, while scaling to ~32 px far and ~181 px near.
"""


def _dist(a, b):
    return float(np.hypot(a[0] - b[0], a[1] - b[1]))


def _contact_radius(shuttle, contact_px, contact_m, scale):
    """Pixel radius allowed at this shuttle's depth.

    Returns ``contact_px`` unchanged unless a metre radius and a ``px_per_m``
    provider are both supplied, so existing callers are unaffected. The radius is
    taken at the SHUTTLE's row rather than the racket's: the shuttle is the object
    whose position the gate is testing, and at contact the two are within a
    racket-length anyway.
    """
    if contact_m is None:
        return contact_px
    return contact_m * scale.px_per_m(shuttle[1])


def _validate_gate(contact_m, scale):
    if contact_m is not None and scale is None:
        raise ValueError("contact_m requires a scale providing px_per_m(y)")


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


def detect_contacts(track, contact_px=80.0, contact_m=None, scale=None, lookahead=3,
                    dir_change_deg=45.0, window_pre=20, window_post=15, min_gap=15):
    """Find racket-shuttle impacts. Returns list of {contact_frame, window_start, window_end}.

    ``contact_m`` plus ``scale`` (any object exposing ``px_per_m(y)``) opt into a
    depth-scaled contact radius; both default to None, which keeps the fixed
    ``contact_px`` behaviour exactly as before.
    """
    _validate_gate(contact_m, scale)
    contacts = []
    last_contact_frame = None
    for i, rec in enumerate(track):
        racket = rec.get("racket_head")
        shuttle = rec.get("shuttle")
        if racket is None or shuttle is None:
            continue
        if _dist(racket, shuttle) >= _contact_radius(shuttle, contact_px, contact_m, scale):
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


def detect_contacts_multi(track, contact_px=80.0, contact_m=None, scale=None,
                          lookahead=3, dir_change_deg=45.0,
                          window_pre=20, window_post=15, min_gap=15):
    """Find racket-shuttle impacts by EITHER player.

    Generalizes detect_contacts (which stays single-racket and is still the
    sole detector wired to TechniqueAnalysisRunner's existing, unchanged
    path) to a per-frame record carrying BOTH players' racket points -- see
    badminton_analysis.system.BadmintonAnalysisSystem._analysis_track_both:
    {"frame", "racket_lower", "racket_upper", "shuttle"}.

    A contact fires when EITHER player's racket point is within contact_px of
    the shuttle AND the shuttle changes direction by >= dir_change_deg soon
    after (the same physical test as detect_contacts, evaluated against two
    racket points instead of one). "hitter" is the side whose racket point is
    nearer the shuttle at the contact frame (this is the fix for the known
    hitter/opponent bug: attribution comes from proximity here, never from a
    frame's cached "currently tracked" player_side). Ties are broken toward
    "lower" (arbitrary; noted, not tuned).

    ``min_gap`` is tracked PER SIDE, keeping the exact meaning it has in
    detect_contacts: a same-racket dedup guard that suppresses one player's
    racket re-triggering a contact a few frames after its own previous
    contact. It deliberately does NOT suppress across sides -- a genuine reply
    by the OTHER player is always kept, however soon it lands. (A single
    shared gap would silently discard replies inside min_gap frames, i.e. 0.5s
    at min_gap=15 / 30fps, which is well inside a fast net exchange or drive
    rally and would produce implausible runs of the same hitter.)

    ``contact_m`` plus ``scale`` (any object exposing ``px_per_m(y)``) opt into a
    depth-scaled contact radius; both default to None, which keeps the fixed
    ``contact_px`` behaviour exactly as before. Scaling changes the gate only --
    hitter attribution is still the nearer racket point, unchanged.

    Returns list of {contact_frame, window_start, window_end, hitter}.
    """
    _validate_gate(contact_m, scale)
    contacts = []
    last_contact_frame = {"lower": None, "upper": None}
    for i, rec in enumerate(track):
        shuttle = rec.get("shuttle")
        if shuttle is None:
            continue
        radius = _contact_radius(shuttle, contact_px, contact_m, scale)
        best_side, best_dist = None, None
        for side in ("lower", "upper"):
            racket = rec.get("racket_" + side)
            if racket is None:
                continue
            d = _dist(racket, shuttle)
            if d < radius and (best_dist is None or d < best_dist):
                best_dist, best_side = d, side
        if best_side is None:
            continue
        change = _shuttle_dir_change(track, i, lookahead)
        if change is None or change < dir_change_deg:
            continue
        frame = rec["frame"]
        prev = last_contact_frame[best_side]
        if prev is not None and (frame - prev) < min_gap:
            continue
        contacts.append({
            "contact_frame": frame,
            "window_start": max(0, frame - window_pre),
            "window_end": frame + window_post,
            "hitter": best_side,
        })
        last_contact_frame[best_side] = frame
    return contacts
