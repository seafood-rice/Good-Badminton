import numpy as np

from badminton_analysis.stroke.hit_augment import (
    augment, jittered_index, keypoint_dropout, mirror, random_homography, shuttle_dropout)
from badminton_analysis.stroke.hit_features import compute_features
from tests.hit_fixtures import make_signals


def test_mirror_twice_is_identity_and_input_untouched():
    sig = make_signals(n=40)
    before = sig.shuttle.copy()
    twice = mirror(mirror(sig))
    assert np.allclose(twice.shuttle, sig.shuttle)
    assert np.allclose(twice.keypoints, sig.keypoints, equal_nan=True)
    assert np.allclose(twice.court_corners, sig.court_corners)
    assert np.array_equal(sig.shuttle, before)


def test_mirror_swaps_left_right_joints_and_negates_court_x():
    sig = make_signals(n=40)
    m = mirror(sig)
    w = sig.image_size[0]
    assert np.isclose(m.keypoints[0, 0, 9, 0], (w - 1) - sig.keypoints[0, 0, 10, 0])
    a, _ = compute_features(sig)
    b, _ = compute_features(m)
    assert np.allclose(a[:, 0], -b[:, 0], atol=1e-3)     # shuttle court-x flips sign
    assert np.allclose(a[:, 1], b[:, 1], atol=1e-3)      # court-y unchanged


def test_random_homography_moves_points_keeps_nan_and_is_seeded():
    sig = make_signals(n=40)
    sig.shuttle[5] = np.nan
    a = random_homography(sig, np.random.default_rng(1))
    b = random_homography(sig, np.random.default_rng(1))
    assert np.isnan(a.shuttle[5]).all()
    assert not np.allclose(a.shuttle[0], sig.shuttle[0])
    assert np.allclose(a.shuttle, b.shuttle, equal_nan=True)


def test_shuttle_dropout_reaches_target_presence_and_never_invents_points():
    sig = make_signals(n=600)
    out = shuttle_dropout(sig, np.random.default_rng(0), keep_range=(0.7, 0.7))
    present = np.isfinite(out.shuttle).all(axis=1)
    assert 0.6 <= present.mean() <= 0.75
    assert np.all(np.isfinite(sig.shuttle[present]))


def test_keypoint_dropout_only_removes():
    sig = make_signals(n=200)
    out = keypoint_dropout(sig, np.random.default_rng(0), joint_p=0.2, player_p=0.1)
    assert np.isnan(out.keypoints).sum() > np.isnan(sig.keypoints).sum()
    kept = np.isfinite(out.keypoints)
    assert np.allclose(out.keypoints[kept], sig.keypoints[kept])


def test_augment_output_still_featurises():
    out = augment(make_signals(n=120), np.random.default_rng(3))
    feats, _ = compute_features(out)
    assert np.isfinite(feats).all()


def test_jittered_index_is_monotonic_and_in_range():
    rng = np.random.default_rng(0)
    for _ in range(20):
        idx = jittered_index(300, 60.0, rng)
        assert idx.size > 0 and idx.min() >= 0 and idx.max() <= 299
        assert np.all(np.diff(idx) >= 0)
