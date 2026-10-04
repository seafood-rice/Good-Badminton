import numpy as np
import pytest

from badminton_analysis.stroke.hit_signals import (
    N_KP, RawSignals, build_signals, load_signals, save_signals)
from tests.hit_fixtures import CORNERS, make_signals, pose_at


def _frames_with(kp_lower, kp_upper):
    return {"players": {"lower": {"keypoints": kp_lower}, "upper": {"keypoints": kp_upper}}}


def test_build_places_shuttle_and_keypoints_by_one_based_frame():
    kp = pose_at((600, 500), 50.0)
    sig = build_signals(30.0, 5, {2: _frames_with(kp, None)}, {1: (10.0, 20.0), 3: None},
                        CORNERS, (1280, 720))
    assert sig.n_frames == 5 and sig.first_frame == 1
    assert sig.shuttle[0].tolist() == [10.0, 20.0]
    assert np.isnan(sig.shuttle[2]).all() and np.isnan(sig.shuttle[4]).all()
    assert np.allclose(sig.keypoints[1, 0], kp)
    assert np.isnan(sig.keypoints[1, 1]).all()          # upper missing
    assert np.isnan(sig.keypoints[0]).all()             # frame 1 had no pose record
    assert sig.frame_number(4) == 5


def test_missing_keypoints_marked_le_1_become_nan():
    kp = pose_at((600, 500), 50.0)
    kp[9] = (0.0, 0.0)                                  # the pipeline's "missing" marker
    sig = build_signals(30.0, 1, {1: _frames_with(kp, None)}, {}, CORNERS, (1280, 720))
    assert np.isnan(sig.keypoints[0, 0, 9]).all()
    assert np.isfinite(sig.keypoints[0, 0, 10]).all()


def test_malformed_keypoints_and_out_of_range_frames_are_ignored():
    sig = build_signals(30.0, 3, {2: _frames_with(np.zeros((5, 2)), None), 9: _frames_with(None, None)},
                        {7: (1.0, 1.0)}, CORNERS, (1280, 720))
    assert np.isnan(sig.keypoints).all()
    assert np.isnan(sig.shuttle).all()


def test_keypoints_with_confidence_column_keep_xy_only():
    kp = np.concatenate([pose_at((600, 500), 50.0), np.ones((N_KP, 1), np.float32)], axis=1)
    sig = build_signals(30.0, 1, {1: _frames_with(kp, None)}, {}, CORNERS, (1280, 720))
    assert sig.keypoints.shape == (1, 2, N_KP, 2)
    assert np.allclose(sig.keypoints[0, 0], kp[:, :2])


def test_no_frames_raises():
    with pytest.raises(ValueError):
        build_signals(30.0, 0, {}, {}, CORNERS, (1280, 720))


def test_save_load_roundtrip(tmp_path):
    sig = make_signals(n=40)
    path = tmp_path / "hit_signals.npz"
    save_signals(path, sig)
    back = load_signals(path)
    assert isinstance(back, RawSignals)
    assert back.fps == sig.fps and back.first_frame == sig.first_frame
    assert back.image_size == sig.image_size
    assert np.array_equal(back.shuttle, sig.shuttle, equal_nan=True)
    assert np.array_equal(back.keypoints, sig.keypoints, equal_nan=True)
    assert np.array_equal(back.court_corners, sig.court_corners)
