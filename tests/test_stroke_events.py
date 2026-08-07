import pytest
from badminton_analysis.stroke.events import StrokeEvent, detect_contacts, detect_contacts_multi


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


def _shuttle_pos_multi(f, first, second):
    if f <= first:
        return (200 - f * 2, 200 - f * 2)
    p1 = 200 - first * 2
    if f <= second:
        d = f - first
        return (p1 + d * 2, p1 + d * 2)
    p2 = p1 + (second - first) * 2
    d2 = f - second
    return (p2 - d2 * 2, p2 - d2 * 2)


def _track_both(first=30, second=90, n=120, first_side="lower", second_side="upper"):
    """Two contacts, one per side, far enough apart that min_gap doesn't merge them."""
    track = []
    for f in range(n):
        shuttle = _shuttle_pos_multi(f, first, second)
        racket_lower = shuttle if (f == first and first_side == "lower") or (f == second and second_side == "lower") else (5000, 5000)
        racket_upper = shuttle if (f == first and first_side == "upper") or (f == second and second_side == "upper") else (5000, 5000)
        track.append({"frame": f, "racket_lower": racket_lower, "racket_upper": racket_upper, "shuttle": shuttle})
    return track


def test_detect_contacts_multi_attributes_hitter_per_side():
    track = _track_both(30, 90, first_side="lower", second_side="upper")
    contacts = detect_contacts_multi(track)
    assert len(contacts) == 2
    assert contacts[0]["contact_frame"] == 30
    assert contacts[0]["hitter"] == "lower"
    assert contacts[1]["contact_frame"] == 90
    assert contacts[1]["hitter"] == "upper"


def test_detect_contacts_multi_upper_only_still_fires():
    """Regression target: the pre-B1 bug meant a hit by the far/upper player
    could never register at all (racket_head belonged to the tracked/lower
    player). This must now fire."""
    track = _track_both(30, 90, first_side="upper", second_side="upper")
    contacts = detect_contacts_multi(track)
    assert len(contacts) == 2
    assert all(c["hitter"] == "upper" for c in contacts)


def test_detect_contacts_multi_ignores_far_rackets_both_sides():
    track = _track_both(30, 90)
    for fr in track:
        fr["racket_lower"] = (5000, 5000)
        fr["racket_upper"] = (5000, 5000)
    assert detect_contacts_multi(track) == []


def test_detect_contacts_multi_tie_breaks_toward_lower():
    track = _track_both(30, 90, first_side="lower", second_side="upper")
    # Make both rackets coincide with the shuttle at frame 30 (a tie).
    track[30]["racket_upper"] = track[30]["shuttle"]
    contacts = detect_contacts_multi(track)
    assert contacts[0]["contact_frame"] == 30
    assert contacts[0]["hitter"] == "lower"


def test_detect_contacts_multi_min_gap_is_per_side_not_global():
    """min_gap must not suppress the OPPONENT's reply.

    A single shared last_contact_frame silently changed min_gap's meaning from
    "same racket re-triggered" (detect_contacts' original, correct reading) to
    "any contact within min_gap frames", discarding genuine replies by the
    other player. At min_gap=15 / 30fps that is 0.5s -- well inside a fast net
    exchange, so it could keep "upper" from ever appearing as a hitter.
    """
    # 10 frames apart -- closer together than the default min_gap of 15.
    track = _track_both(30, 40, first_side="lower", second_side="upper")
    contacts = detect_contacts_multi(track)
    assert [(c["contact_frame"], c["hitter"]) for c in contacts] == [(30, "lower"), (40, "upper")]


def test_detect_contacts_multi_min_gap_still_dedups_the_same_side():
    """The other half of the contract: min_gap keeps its ORIGINAL job of
    suppressing a repeat contact by the SAME side shortly after its own
    previous one (same fixture as the per-side test, both hits by "lower")."""
    track = _track_both(30, 40, first_side="lower", second_side="lower")
    contacts = detect_contacts_multi(track)
    assert [(c["contact_frame"], c["hitter"]) for c in contacts] == [(30, "lower")]
