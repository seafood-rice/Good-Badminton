"""Automatic rally-outcome evidence (badminton_analysis/outcomes/evidence.py)."""
import pytest

from badminton_analysis.outcomes import evidence as ev

CORNERS = [[1000, 500], [2000, 500], [2600, 2000], [400, 2000]]


def test_side_of_y():
    assert ev.side_of_y(3.0) == "upper" and ev.side_of_y(10.0) == "lower"


def test_last_hitter_uses_pad():
    hits = [(10.0, 600, "lower"), (12.0, 720, "upper"), (20.4, 1224, "lower")]
    assert ev.last_hitter(hits, 9.0, 20.0) == "lower"      # 20.4 is within the 0.5 s pad
    assert ev.last_hitter(hits, 9.0, 19.0) == "upper"
    assert ev.last_hitter([], 0, 10) is None and ev.last_hitter(None, 0, 10) is None


def test_next_server_needs_pause():
    hits = [(10.0, 0, "lower"), (14.0, 0, "upper"), (15.0, 0, "lower")]
    assert ev.next_server(hits, 10.0, 13.8, 16.0) == "upper"
    assert ev.next_server(hits, 12.0, 13.8, 16.0) is None
    assert ev.next_server(hits, None, 13.8, 16.0) is None
    assert ev.next_server([], 10.0, 13.8, 16.0) is None


def test_clean_track_drops_static_and_jumps():
    traj = {0: [100, 100], 1: [105, 102], 2: [900, 900], 3: [110, 104], 4: [500, 500], 5: None, 6: ["a", 1]}
    pts = ev.clean_track(traj, range(0, 7), 1000, static_points=[(500, 500)])
    assert [p[0] for p in pts] == [0, 1, 3]


def test_find_landing_still_run():
    moving = [(f, 100.0 + 20 * f, 200.0) for f in range(10)]
    still = [(f, 400.0 + (f % 2), 600.0) for f in range(10, 30)]
    assert ev.find_landing(moving, 60, 1000) is None
    landing = ev.find_landing(moving + still, 60, 1000)
    assert landing == pytest.approx((400.5, 600.0), abs=0.01)
    assert ev.find_landing(still[:5], 60, 1000) is None
    assert ev.find_landing([], 60, 1000) is None


def test_homography_maps_corners():
    h = ev.court_homography(CORNERS)
    assert ev.to_court(h, (1000, 500)) == pytest.approx((0, 0), abs=1e-3)
    assert ev.to_court(h, (2600, 2000)) == pytest.approx((6.1, 13.4), abs=1e-3)
    assert ev.court_homography([[0, 0], [0, 0], [0, 0], [0, 0]]) is None
    assert ev.court_homography("junk") is None


@pytest.mark.parametrize("xy,hitter,winner,ending,reason", [
    ((3.0, 3.0), "lower", "lower", "in", "inside"),
    ((3.0, 3.0), "upper", "lower", "net", "inside"),
    ((3.0, 3.0), None, "lower", None, "inside"),
    ((3.0, 10.0), "upper", "upper", "in", "inside"),
    ((3.0, -1.0), "lower", "upper", "out", "outside"),
    ((7.0, 10.0), "upper", "lower", "out", "outside"),
    ((3.0, 6.8), "lower", None, None, "near_line"),
    ((0.1, 10.0), "upper", None, None, "near_line"),
    ((3.0, 13.5), "upper", None, None, "near_line"),
])
def test_landing_verdict(xy, hitter, winner, ending, reason):
    v = ev.landing_verdict(xy, hitter)
    assert (v["winner"], v["ending"], v["reason"]) == (winner, ending, reason)
