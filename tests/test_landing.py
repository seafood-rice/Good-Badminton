"""Landing estimates (badminton_analysis/insights/landing.py) and the shared floor point (outcomes/evidence.py)."""
import json

import pytest

from badminton_analysis.insights import landing as land
from badminton_analysis.outcomes import evidence as ev

# Image = court metres x 100, shifted by (50, 20): an affine camera, so the column solve is exact.
CORNERS = [[50, 20], [660, 20], [660, 1360], [50, 1360]]


def _h():
    return ev.court_homography(CORNERS)


def test_depth_bands_from_the_net():
    assert land.depth_of(6.7 - 1.98) == "front" and land.depth_of(6.7 + 1.98) == "front"
    assert land.depth_of(6.7 - 2.0) == "mid" and land.depth_of(6.7 + 4.7) == "mid"
    assert land.depth_of(6.7 - 4.8) == "rear" and land.depth_of(13.0) == "rear"


def test_reach_moves_towards_the_net_and_never_across():
    assert land.reach("upper", 2.0) == pytest.approx(2.0 + land.REACH_M)
    assert land.reach("lower", 11.0) == pytest.approx(11.0 - land.REACH_M)
    assert land.reach("upper", 6.5) == 6.7 and land.reach("lower", 6.9) == 6.7


def test_column_x_reads_the_shuttle_column_at_the_feet_depth():
    h_inv = land.inverse(_h())
    assert land.column_x(h_inv, 3.0, (50 + 420, 999)) == pytest.approx(4.2, abs=1e-6)
    # the image y of the shuttle does not matter: it is mid-air
    assert land.column_x(h_inv, 3.0, (50 + 420, -500)) == pytest.approx(4.2, abs=1e-6)


def test_column_x_with_perspective_matches_the_ground_point():
    # a trapezoid (camera behind the near baseline): the far half is narrower in the image
    corners = [[400, 100], [700, 100], [1000, 900], [100, 900]]
    h = ev.court_homography(corners)
    h_inv = land.inverse(h)
    ground = (4.0, 2.5)
    img = land._to_image(h_inv, *ground)
    assert land.column_x(h_inv, ground[1], (img[0], img[1] - 300)) == pytest.approx(4.0, abs=0.01)


def test_column_x_rejects_far_outside_and_bad_input():
    h_inv = land.inverse(_h())
    assert land.column_x(h_inv, 3.0, (50 + 900, 0)) is None          # 9 m: more than 1 m outside
    assert land.column_x(h_inv, 3.0, (50 - 90, 0)) == pytest.approx(-0.9)
    assert land.column_x(None, 3.0, (100, 0)) is None
    assert land.column_x(h_inv, float("nan"), (100, 0)) is None
    assert land.column_x(h_inv, 3.0, (True, 0)) is None


def test_estimate_sources():
    h_inv = land.inverse(_h())
    both = land.estimate("upper", (2.0, 3.0), (50 + 420, 0), h_inv)
    assert both == {"x": 4.2, "y": round(3.0 + land.REACH_M, 2), "side": "upper", "depth": "mid",
                    "source": "shuttle_feet", "out": False}
    feet = land.estimate("lower", (2.0, 12.5), None, h_inv)
    assert (feet["x"], feet["y"], feet["source"], feet["depth"]) == (2.0, 11.9, "feet", "rear")
    # an untrusted column falls back to the feet
    assert land.estimate("upper", (2.0, 3.0), (5000, 0), h_inv)["source"] == "feet"


@pytest.mark.parametrize("args", [("left", (1, 1)), ("upper", None), ("upper", (1, float("inf"))),
                                  ("upper", (None, 1))])
def test_estimate_bad_input_is_none(args):
    assert land.estimate(*args) is None


def test_make_marks_out_of_court():
    assert land.make(-0.5, 3.0, "floor") == {"x": -0.5, "y": 3.0, "side": "upper", "depth": None,
                                             "source": "floor", "out": True}
    assert land.make(3.0, 10.0, "floor")["depth"] == "mid"


def test_inverse_of_singular_is_none():
    assert land.inverse([[0, 0, 0], [0, 0, 0], [0, 0, 0]]) is None
    assert land.inverse("junk") is None


def test_floor_point_projects_the_first_still_run():
    h = _h()
    traj = {f: [100 + 10 * f, 200] for f in range(10)}            # flying
    traj.update({f: [350, 1220] for f in range(10, 40)})          # still on the floor
    xy = ev.floor_point(traj, 0, 39, 30.0, 1080, h)
    assert xy == pytest.approx((3.0, 12.0))
    assert ev.floor_point(traj, 0, 9, 30.0, 1080, h) is None       # no still run in the window
    assert ev.floor_point(traj, 0, 39, 30.0, 1080, None) is None
    assert ev.floor_point({}, 0, 39, 30.0, 1080, h) is None


def test_load_trajectory(tmp_path):
    assert ev.load_trajectory(tmp_path) == {}
    (tmp_path / "shuttle_trajectory.json").write_text(
        json.dumps({"key": "x", "trajectory": {"0": [1, 2], "x": [3, 4], "5": None}}))
    assert ev.load_trajectory(tmp_path) == {0: [1, 2], 5: None}
    (tmp_path / "shuttle_trajectory.json").write_text("{nope")
    assert ev.load_trajectory(tmp_path) == {}


def test_upper_receiver_at_the_net_stays_on_the_upper_half():
    lnd = land.estimate("upper", (3.0, 6.3))
    assert (lnd["y"], lnd["side"], lnd["depth"], lnd["out"]) == (6.7, "upper", "front", False)
    wrong_half = land.estimate("upper", (3.0, 8.0))          # a foot point past the net
    assert (wrong_half["y"], wrong_half["side"]) == (6.7, "upper")


def test_mid_rally_landing_is_kept_inside_the_court():
    h_inv = land.inverse(_h())
    lnd = land.estimate("upper", (3.0, 3.0), (50 - 90, 0), h_inv)   # column 0.9 m outside
    assert (lnd["x"], lnd["out"], lnd["source"]) == (0.0, False, "shuttle_feet")
    assert land.estimate("lower", (7.0, 14.0))["x"] == 6.1 and land.estimate("lower", (7.0, 14.0))["y"] == 13.4


@pytest.mark.parametrize("foot", [5, (1,), "ab", (1, 2, 3)])
def test_estimate_junk_foot_is_none(foot):
    assert land.estimate("upper", foot) is None


@pytest.mark.parametrize("shuttle", [5, (1,), "ab", (None, 1)])
def test_estimate_junk_shuttle_falls_back_to_feet(shuttle):
    assert land.estimate("upper", (1.0, 2.0), shuttle, land.inverse(_h()))["source"] == "feet"


def test_floor_point_junk_frames_are_none():
    assert ev.floor_point({0: [1, 2]}, "a", 5, 30.0, 1080, _h()) is None
    assert ev.floor_point({0: [1, 2]}, 0, None, 30.0, 1080, _h()) is None


def test_floor_plausible():
    assert ev.floor_plausible((-1.4, 14.8)) and not ev.floor_plausible((3.0, 15.0))


def test_repeated_floor_points_are_one_static_object():
    pts = [(2.0, 20.0), None, (2.1, 20.05), (5.0, 3.0), (2.0, 19.9), (5.0, 3.15)]
    assert ev.repeated(pts) == {0, 2, 4}            # three rallies at one spot; two is not enough
    assert ev.repeated([]) == set() and ev.repeated([None, None]) == set()
