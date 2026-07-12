"""Tests for hit/hitter extraction from contact detection.

See badminton_analysis/stroke_recog/hits.py and
badminton_analysis/stroke/events.py::detect_contacts (mirrors the track
fixture shape used by tests/test_stroke_events.py).
"""

from badminton_analysis.stroke_recog import hits


def _shuttle_pos(f, first, second):
    """Piecewise-linear shuttle path: up-left to `first`, reverses to
    down-right until `second`, reverses again -- two direction-change
    "bounces", one at each contact frame."""
    if f <= first:
        return (200 - f * 2, 200 - f * 2)
    p1 = 200 - first * 2
    if f <= second:
        d = f - first
        return (p1 + d * 2, p1 + d * 2)
    p2 = p1 + (second - first) * 2
    d2 = f - second
    return (p2 - d2 * 2, p2 - d2 * 2)


def _track_with_two_contacts(first=30, second=90, n=120):
    """Two shuttle direction-changes ("bounces") near the racket, far enough
    apart that detect_contacts's min_gap doesn't merge them."""
    track = []
    for f in range(n):
        shuttle = _shuttle_pos(f, first, second)
        racket = shuttle if f in (first, second) else (5000, 5000)
        track.append({"frame": f, "racket_head": racket, "shuttle": shuttle})
    return track


def test_hit_events_finds_two_hits_with_hitter_from_frame_lookup():
    track = _track_with_two_contacts(30, 90)
    frame_lookup = {
        30: {"player_side": "lower"},
        90: {"player_side": "upper"},
    }.get

    events = hits.hit_events(track, frame_lookup)

    assert len(events) == 2
    assert [e["frame"] for e in events] == sorted(e["frame"] for e in events)
    assert events[0]["frame"] == 30
    assert events[0]["hitter"] == "lower"
    assert events[1]["frame"] == 90
    assert events[1]["hitter"] == "upper"
    for e in events:
        assert e["hitter"] in {"lower", "upper", "unknown"}


def test_hit_events_hitter_unknown_without_frame_lookup():
    track = _track_with_two_contacts(30, 90)
    events = hits.hit_events(track)
    assert len(events) == 2
    assert all(e["hitter"] == "unknown" for e in events)


def test_hit_events_hitter_unknown_when_lookup_lacks_player_side():
    track = _track_with_two_contacts(30, 90)
    frame_lookup = {30: {}, 90: {"other_key": 1}}.get
    events = hits.hit_events(track, frame_lookup)
    assert len(events) == 2
    assert all(e["hitter"] == "unknown" for e in events)


def test_hit_events_hitter_unknown_when_lookup_returns_none():
    track = _track_with_two_contacts(30, 90)
    events = hits.hit_events(track, lambda frame: None)
    assert len(events) == 2
    assert all(e["hitter"] == "unknown" for e in events)


def test_hit_events_empty_track_returns_empty_list():
    assert hits.hit_events([]) == []


def test_hit_events_sorted_by_frame_even_if_contacts_unordered(monkeypatch):
    # hit_events must sort explicitly, not rely on detect_contacts / track order.
    monkeypatch.setattr(
        hits, "detect_contacts",
        lambda track: [{"contact_frame": 90}, {"contact_frame": 30}, {"contact_frame": 60}],
    )
    events = hits.hit_events([])
    assert [e["frame"] for e in events] == [30, 60, 90]
