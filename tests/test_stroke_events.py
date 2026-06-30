import pytest
from badminton_analysis.stroke.events import StrokeEvent, detect_contacts


def test_stroke_event_to_dict_roundtrip():
    ev = StrokeEvent("smash", 100, 80, 115, "upper", 0.9)
    d = ev.to_dict()
    assert d["stroke_type"] == "smash"
    assert d["contact_frame"] == 100
    assert d["window_start"] == 80 and d["window_end"] == 115
    assert d["player_side"] == "upper"
    assert d["confidence"] == 0.9


def _track_with_bounce(contact_frame, n=60):
    """Shuttle flies up-left, then after contact flies down-right; racket meets it at contact."""
    track = []
    for f in range(n):
        if f <= contact_frame:
            shuttle = (200 - f * 2, 200 - f * 2)
        else:
            d = f - contact_frame
            shuttle = (200 - contact_frame * 2 + d * 2, 200 - contact_frame * 2 + d * 2)
        racket = shuttle if f == contact_frame else (1000, 1000)
        track.append({"frame": f, "racket_head": racket, "shuttle": shuttle})
    return track


def test_detect_contacts_finds_single_contact():
    track = _track_with_bounce(30)
    contacts = detect_contacts(track, window_pre=20, window_post=15)
    assert len(contacts) == 1
    c = contacts[0]
    assert c["contact_frame"] == 30
    assert c["window_start"] == 10
    assert c["window_end"] == 45


def test_detect_contacts_clamps_window_at_zero():
    track = _track_with_bounce(5)
    contacts = detect_contacts(track, window_pre=20, window_post=15)
    assert contacts[0]["window_start"] == 0


def test_detect_contacts_ignores_far_racket():
    track = _track_with_bounce(30)
    for fr in track:
        fr["racket_head"] = (5000, 5000)  # never near shuttle
    assert detect_contacts(track) == []


def test_detect_contacts_suppresses_close_duplicates():
    track = _track_with_bounce(30)
    track[31]["racket_head"] = track[31]["shuttle"]  # second near-contact 1 frame later
    contacts = detect_contacts(track, min_gap=15)
    assert len(contacts) == 1
