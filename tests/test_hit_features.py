import numpy as np
import pytest

from badminton_analysis.stroke.hit_features import (
    FEATURE_DIM, PLAYER_DIM, SHUTTLE_DIM, CourtGeometry, compute_features, resample_index)
from tests.hit_fixtures import CORNERS, make_signals, transform


def test_resample_index_30_60_and_degenerate():
    assert resample_index(10, 30.0).tolist() == list(range(10))
    assert resample_index(10, 60.0).tolist() == [0, 2, 4, 6, 8]
    assert resample_index(0, 30.0).size == 0
    assert resample_index(10, 0.0).size == 0
    assert resample_index(10, float("nan")).size == 0


def test_resample_index_5994_is_monotonic_and_in_range():
    idx = resample_index(6000, 59.94)
    assert idx[0] == 0 and idx[-1] <= 5999
    assert np.all(np.diff(idx) >= 1)
    # step k sits at k/30 s; native frame idx[k] sits at idx[k]/59.94 s -- within half a frame
    t_step = np.arange(idx.size) / 30.0
    assert np.max(np.abs(idx / 59.94 - t_step)) <= 0.5 / 59.94 + 1e-9


def test_feature_shape_and_no_nans():
    feats, idx = compute_features(make_signals(n=90))
    assert feats.shape == (90, FEATURE_DIM) and feats.dtype == np.float32
    assert np.isfinite(feats).all()
    assert idx.tolist() == list(range(90))


def test_features_invariant_to_camera_zoom_and_shift():
    sig = make_signals(n=90)
    a, _ = compute_features(sig)
    b, _ = compute_features(transform(sig, 1.5, 100.0, 50.0))
    assert np.allclose(a, b, atol=1e-3)


def test_60fps_features_use_every_other_frame():
    sig = make_signals(n=120, fps=60.0)
    feats, idx = compute_features(sig)
    assert idx.tolist() == list(range(0, 120, 2))
    assert feats.shape == (60, FEATURE_DIM)


def test_missing_shuttle_zeroes_shuttle_block_and_clears_flag():
    sig = make_signals(n=30)
    sig.shuttle[10:15] = np.nan
    feats, _ = compute_features(sig)
    assert feats[12, 7] == 0.0 and np.all(feats[12, 0:7] == 0.0)
    assert feats[20, 7] == 1.0


def test_missing_player_block_is_zero_with_flag_cleared():
    sig = make_signals(n=30, players=False)
    feats, _ = compute_features(sig)
    for p in range(2):
        base = SHUTTLE_DIM + p * PLAYER_DIM
        assert np.all(feats[:, base:base + PLAYER_DIM] == 0.0)


def test_player_size_does_not_change_pose_features():
    sig = make_signals(n=30)
    big = make_signals(n=30)
    hip = np.nanmean(big.keypoints[:, 0, [11, 12]], axis=1, keepdims=True)   # (T,1,2)
    big.keypoints[:, 0] = (big.keypoints[:, 0] - hip) * 2.0 + hip            # near player 2x larger
    a, _ = compute_features(sig)
    b, _ = compute_features(big)
    base = SHUTTLE_DIM
    assert np.allclose(a[:, base:base + 34], b[:, base:base + 34], atol=1e-5)
    assert a[0, base + 40] == 1.0


def test_turn_feature_peaks_at_the_triangle_wave_corners():
    feats, _ = compute_features(make_signals(n=90))
    turn = feats[:, 6]
    assert turn[30] > 0.3 and turn[60] > 0.3       # the shuttle reverses at t = 1 s and 2 s
    assert turn[45] < 0.1


def test_degenerate_corners_raise():
    with pytest.raises(ValueError):
        CourtGeometry(np.array([[0, 0], [10, 0], [20, 0], [30, 0]], np.float32))


def test_collinear_corners_raise():
    with pytest.raises(ValueError):
        CourtGeometry(np.array([[0, 0], [10, 0], [20, 0], [15, 100]], np.float32))


def test_bowtie_corners_raise():
    with pytest.raises(ValueError):
        CourtGeometry(np.array([[440, 260], [840, 260], [220, 660], [1060, 660]], np.float32))


def test_mirrored_corners_work():
    # Original corners construct fine
    geo_orig = CourtGeometry(CORNERS)
    assert geo_orig.length_px > 300
    # Mirrored and reordered corners should also work: x -> 1279 - x, then order [1,0,3,2]
    mirrored = CORNERS.copy().astype(np.float32)
    mirrored[:, 0] = 1279 - mirrored[:, 0]
    mirrored_reordered = mirrored[[1, 0, 3, 2]]
    geo_mirror = CourtGeometry(mirrored_reordered)
    assert geo_mirror.length_px > 300


def test_court_geometry_net_midpoint_lies_between_baselines():
    geo = CourtGeometry(CORNERS)
    assert 260 < geo.net_mid[1] < 660
    assert geo.length_px > 300
