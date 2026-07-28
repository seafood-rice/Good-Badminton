"""Upper/lower player split must follow the true net line under perspective.

Regression coverage for the bug where the near-court (front) player was tracked
as the "upper" player and the far player was dropped: the tracker split halves
on a fixed image row (the arithmetic average of the court-corner rows), which
under an angled camera sits deep in the near court rather than on the net.
"""

import numpy as np

from badminton_analysis.court.mapper import CourtMapper
from badminton_analysis.tracking.player import PlayerTracker

# Perspective corners from a real angled-camera clip (Dji ...0010 D): the far
# baseline is compressed near the top of the frame, so the true net projects
# far ABOVE the arithmetic average of the corner rows.
CORNERS = [[1737, 1122], [2389, 1122], [3823, 2129], [171, 2001]]
# The legacy split row: mean of the top-edge and bottom-edge midpoints (~1593).
NAIVE_MID = int(((1122 + 1122) / 2 + (2129 + 2001) / 2) / 2)


def _img(court_xy):
    """Project a court-space (x, y) point in meters to image pixels."""
    pt = CourtMapper(CORNERS).court_to_image(np.array(court_xy, dtype=float))
    return (float(pt[0]), float(pt[1]))


def _tracker():
    return PlayerTracker(corners=CORNERS, threshold=NAIVE_MID, history_size=5, fps=30)


def test_front_player_above_naive_line_is_lower_not_upper():
    # A near-court player (in front of the net) at court-Y = 9.0 m. Under this
    # perspective their feet sit ABOVE the naive split row, so the legacy pixel
    # split misfiled them as "upper". They must now be classified "lower".
    near = _img((3.05, 9.0))
    assert near[1] < NAIVE_MID, "test fixture must exercise the misclassification trap"
    t = _tracker()
    t.update(1, [near], None, {}, {}, 1)
    assert t.players["lower"] is not None
    assert t.players["upper"] is None


def test_far_and_near_players_split_at_net():
    far = _img((3.05, 3.0))
    near = _img((3.05, 10.0))
    t = _tracker()
    t.update(1, [near, far], None, {}, {}, 1)
    assert tuple(t.players["upper"]) == far
    assert tuple(t.players["lower"]) == near


def test_far_half_keeps_player_nearest_net_over_deep_spectator():
    # Two candidates behind the net: the real player near the net and a
    # spectator deep behind the far baseline (still within the on-court margin).
    player = _img((3.05, 5.5))
    spectator = _img((3.05, -0.3))
    t = _tracker()
    t.update(1, [spectator, player], None, {}, {}, 1)
    assert tuple(t.players["upper"]) == player


def test_near_half_keeps_player_nearest_net_over_behind_baseline():
    player = _img((3.05, 8.0))
    behind_baseline = _img((3.05, 13.9))
    t = _tracker()
    t.update(1, [behind_baseline, player], None, {}, {}, 1)
    assert tuple(t.players["lower"]) == player
