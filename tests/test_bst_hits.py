"""Tests for hit/hitter extraction from the both-player contact track.

See badminton_analysis/stroke_recog/hits.py and
badminton_analysis/stroke/events.py::detect_contacts_multi. Pre-B1, hitter
came from a frame_lookup's cached "player_side" (~always "lower" in a real
match -- the known bug). Post-B1, hitter comes directly from
detect_contacts_multi's own shuttle-proximity attribution, so it no longer
depends on frame_lookup / pose data being available at all.
"""

from badminton_analysis.stroke_recog import hits


def _shuttle_pos(f, first, second):
    if f <= first:
        return (200 - f * 2, 200 - f * 2)
    p1 = 200 - first * 2
    if f <= second:
        d = f - first
        return (p1 + d * 2, p1 + d * 2)
    p2 = p1 + (second - first) * 2
    d2 = f - second
    return (p2 - d2 * 2, p2 - d2 * 2)


def _track_with_two_contacts(first=30, second=90, n=120, first_side="lower", second_side="upper"):
    track = []
    for f in range(n):
        shuttle = _shuttle_pos(f, first, second)
        racket_lower = shuttle if (f == first and first_side == "lower") or (f == second and second_side == "lower") else (5000, 5000)
        racket_upper = shuttle if (f == first and first_side == "upper") or (f == second and second_side == "upper") else (5000, 5000)
        track.append({"frame": f, "racket_lower": racket_lower, "racket_upper": racket_upper, "shuttle": shuttle})
    return track


def test_hit_events_attributes_hitter_from_contact_track_not_lookup():
    track = _track_with_two_contacts(30, 90, first_side="lower", second_side="upper")
    events = hits.hit_events(track)
    assert len(events) == 2
    assert [e["frame"] for e in events] == sorted(e["frame"] for e in events)
    assert events[0]["frame"] == 30 and events[0]["hitter"] == "lower"
    assert events[1]["frame"] == 90 and events[1]["hitter"] == "upper"


def test_hit_events_upper_only_hits_now_register():
    """The pre-B1 regression target: an upper-court-only rally must yield
    hitter == "upper", not be silently dropped or misattributed to "lower"."""
    track = _track_with_two_contacts(30, 90, first_side="upper", second_side="upper")
    events = hits.hit_events(track)
    assert len(events) == 2
    assert all(e["hitter"] == "upper" for e in events)


def test_hit_events_empty_track_returns_empty_list():
    assert hits.hit_events([]) == []


def test_hit_events_sorted_by_frame_even_if_contacts_unordered(monkeypatch):
    monkeypatch.setattr(
        hits, "detect_contacts_multi",
        lambda track: [
            {"contact_frame": 90, "hitter": "upper"},
            {"contact_frame": 30, "hitter": "lower"},
            {"contact_frame": 60, "hitter": "lower"},
        ],
    )
    events = hits.hit_events([])
    assert [e["frame"] for e in events] == [30, 60, 90]
