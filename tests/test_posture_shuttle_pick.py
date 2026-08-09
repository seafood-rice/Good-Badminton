"""Choosing WHICH shuttle detection to use as the contact anchor.

The frame loop previously took boxes.xywh[0] -- whichever box the detector returned
first. The shuttle is the primary contact anchor, so an arbitrary pick can move a
rep's contact frame off the stroke.
"""
import numpy as np
from badminton_analysis.posture.shuttle_pick import pick_shuttle


def test_picks_the_box_nearest_the_wrist():
    boxes = np.array([[500.0, 500.0, 10.0, 10.0],
                      [110.0, 105.0, 10.0, 10.0],
                      [300.0, 300.0, 10.0, 10.0]])
    assert pick_shuttle(boxes, wrist=(100.0, 100.0)) == (110.0, 105.0)


def test_single_box_is_returned_regardless_of_distance():
    boxes = np.array([[900.0, 900.0, 8.0, 8.0]])
    assert pick_shuttle(boxes, wrist=(0.0, 0.0)) == (900.0, 900.0)


def test_no_wrist_falls_back_to_the_first_box():
    """Preserves the previous behaviour when there is nothing to measure against."""
    boxes = np.array([[10.0, 20.0, 5.0, 5.0], [30.0, 40.0, 5.0, 5.0]])
    assert pick_shuttle(boxes, wrist=None) == (10.0, 20.0)


def test_empty_input_returns_none():
    assert pick_shuttle(np.zeros((0, 4)), wrist=(1.0, 2.0)) is None
    assert pick_shuttle(None, wrist=(1.0, 2.0)) is None


def test_returns_plain_floats_not_numpy_scalars():
    """The track is serialised to JSON downstream."""
    boxes = np.array([[110.0, 105.0, 10.0, 10.0]])
    x, y = pick_shuttle(boxes, wrist=(100.0, 100.0))
    assert type(x) is float and type(y) is float
