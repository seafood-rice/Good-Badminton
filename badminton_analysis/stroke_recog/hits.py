"""Hit/hitter extraction from the match pipeline's both-player contact track.

Pure function: turns badminton_analysis.system.BadmintonAnalysisSystem's
``_analysis_track_both`` (list of ``{frame, racket_lower, racket_upper,
shuttle}`` records) into the ordered list of hit events the recognizer
iterates over. Both the hit *frames* and the *hitter* attribution come
straight from ``stroke.events.detect_contacts_multi`` -- this module only
sorts and reshapes its output.

Pre-B1, hitter was read from a frame_lookup's cached "player_side", which is
~always "lower" in a real two-player match (the documented hitter/opponent
bug). Post-B1, detect_contacts_multi already knows which racket triggered
each contact, so hitter is correct by construction and no longer depends on
frame_lookup / pose availability at all.
"""

from ..stroke.events import detect_contacts_multi


def hit_events(track):
    """Derive ``[{"frame": int, "hitter": "lower"|"upper"}, ...]`` from a
    both-player contact track (see module docstring), sorted by frame
    ascending (sorted explicitly rather than relying on
    ``detect_contacts_multi`` / the input track already being in frame
    order).
    """
    contacts = detect_contacts_multi(track)
    events = [{"frame": c["contact_frame"], "hitter": c["hitter"]} for c in contacts]
    return sorted(events, key=lambda e: e["frame"])
