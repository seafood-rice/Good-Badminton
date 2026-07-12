"""Hit/hitter extraction from the match pipeline's contact-detection track.

Pure function: turns the match pipeline's ``_analysis_track`` (list of
``{frame, racket_head, shuttle}`` records, see
``badminton_analysis.system.BadmintonAnalysisSystem._capture_analysis_frame``)
into the ordered list of hit events the Task 5 recognizer iterates over. Hit
*frames* come straight from the existing
``badminton_analysis.stroke.events.detect_contacts`` contact detector (no new
detection logic here); this module only attaches the hitter's side.
"""

from ..stroke.events import detect_contacts


def hit_events(track, frame_lookup=None):
    """Derive ``[{"frame": int, "hitter": str}, ...]`` from a contact track.

    Parameters
    ----------
    track : list of {"frame", "racket_head", "shuttle"}
        The match pipeline's ``_analysis_track``.
    frame_lookup : callable[int] -> dict | None, optional
        Match-pipeline per-frame record lookup (e.g.
        ``_analysis_frames.get``). Its ``"player_side"`` value
        (``"lower"``/``"upper"``) becomes the hit's ``"hitter"``; when
        ``frame_lookup`` is ``None``, returns ``None``, or the record has no
        ``"player_side"``, the hitter falls back to ``"unknown"``.

    Returns
    -------
    list of {"frame": int, "hitter": str}
        Sorted by frame ascending (sorted explicitly rather than relying on
        ``detect_contacts`` / the input track already being in frame order).
    """
    contacts = detect_contacts(track)
    events = []
    for contact in contacts:
        frame = contact["contact_frame"]
        hitter = "unknown"
        if frame_lookup is not None:
            rec = frame_lookup(frame)
            if rec is not None:
                hitter = rec.get("player_side", "unknown")
        events.append({"frame": frame, "hitter": hitter})
    return sorted(events, key=lambda e: e["frame"])
