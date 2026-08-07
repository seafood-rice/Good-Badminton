"""Tests for per-hit BST input-tensor assembly.

See badminton_analysis/stroke_recog/inputs.py and third_party/bst/CONTRACT.md
(SEQ_LEN, POSE_SHAPE, SHUTTLE_SHAPE, POS_SHAPE) for the shapes this exercises.
"""

import numpy as np

from badminton_analysis.stroke_recog import inputs as bi

VIDEO_WH = (1280, 720)
COURT_CORNERS = [(100, 100), (1180, 100), (1180, 620), (100, 620)]
CONTACT_FRAME = 100
WINDOW_START = CONTACT_FRAME - bi.SEQ_LEN // 2  # 85


def _keypoints_for(frame):
    """17x2 array with a distinct, valid (sentinel-free) value per joint."""
    base = np.array(
        [[600 + 2 * j, 300 + 3 * j] for j in range(17)], dtype=float
    )
    base += (frame - CONTACT_FRAME)  # small per-frame jitter, still all > 1
    return base


def _record(frame, with_shuttle=True):
    kp = _keypoints_for(frame)
    return {
        "keypoints": kp,
        "centroid": (float(kp[11][0] + kp[12][0]) / 2.0, float(kp[11][1] + kp[12][1]) / 2.0),
        "player_side": "lower",
        "shuttle": (640.0 + frame, 360.0) if with_shuttle else None,
    }


def test_shapes_and_normalization_ranges():
    records = {f: _record(f) for f in range(WINDOW_START, WINDOW_START + bi.SEQ_LEN)}
    result = bi.build_inputs(CONTACT_FRAME, records.get, COURT_CORNERS, VIDEO_WH)

    assert result is not None
    assert result["pose"].shape == bi.POSE_SHAPE
    assert result["shuttle"].shape == bi.SHUTTLE_SHAPE
    assert result["positions"].shape == bi.POS_SHAPE

    pose = result["pose"]
    assert np.all(np.isfinite(pose))
    # The 17 normalized-joint features (first 34 of the 72) land in [-1, 1];
    # the 19 bone-vector features (remaining 38) are differences of two such
    # joints, so at most [-2, 2].
    joints = pose[..., :34]
    bones = pose[..., 34:]
    assert np.all(joints >= -1.0 - 1e-6) and np.all(joints <= 1.0 + 1e-6)
    assert np.all(bones >= -2.0 - 1e-6) and np.all(bones <= 2.0 + 1e-6)

    shuttle = result["shuttle"]
    assert np.all(shuttle >= 0.0) and np.all(shuttle <= 1.0)

    # contact_frame lands at index SEQ_LEN // 2 and actually carries real data
    # (not a zero-padded slot).
    contact_index = bi.SEQ_LEN // 2
    assert np.any(pose[contact_index, 0] != 0.0)


def test_all_none_keypoints_returns_none():
    result = bi.build_inputs(CONTACT_FRAME, lambda f: None, COURT_CORNERS, VIDEO_WH)
    assert result is None


def test_too_few_posed_frames_returns_none():
    # Only 3 posed frames in the window -- below SEQ_LEN // 3 == 10.
    records = {}
    for i, f in enumerate(range(WINDOW_START, WINDOW_START + bi.SEQ_LEN)):
        records[f] = _record(f) if i < 3 else {"keypoints": None, "centroid": None, "shuttle": None}
    result = bi.build_inputs(CONTACT_FRAME, records.get, COURT_CORNERS, VIDEO_WH)
    assert result is None


def test_edge_contact_near_video_start_still_full_seq_len_zero_padded():
    contact_frame = 5
    window_start = contact_frame - bi.SEQ_LEN // 2  # -10
    # Only frames 0..19 actually exist; frames -10..-1 are unrecorded (None).
    records = {f: _record(f) for f in range(0, 20)}
    result = bi.build_inputs(contact_frame, records.get, COURT_CORNERS, VIDEO_WH)

    assert result is not None
    assert result["pose"].shape == bi.POSE_SHAPE
    assert result["shuttle"].shape == bi.SHUTTLE_SHAPE
    assert result["positions"].shape == bi.POS_SHAPE

    # Frames before 0 don't exist -> zero-padded, not truncated.
    n_missing = 0 - window_start  # 10
    assert np.all(result["pose"][:n_missing] == 0.0)
    assert np.all(result["shuttle"][:n_missing] == 0.0)
    assert np.all(result["positions"][:n_missing] == 0.0)
    # Frame 0 (first real frame) lands right after the padding and has data.
    assert np.any(result["pose"][n_missing, 0] != 0.0)


def test_positions_fall_back_to_centroid_without_ankles():
    rec = {"keypoints": None, "centroid": (640.0, 400.0), "shuttle": None}
    records = {f: _record(f) for f in range(WINDOW_START, WINDOW_START + bi.SEQ_LEN)}
    records[CONTACT_FRAME] = rec
    result = bi.build_inputs(CONTACT_FRAME, records.get, COURT_CORNERS, VIDEO_WH)
    assert result is not None
    contact_index = bi.SEQ_LEN // 2
    # Centroid-derived position should be within the normalized [0, ~1] band.
    assert 0.0 <= result["positions"][contact_index, 0, 0] <= 1.2
    assert 0.0 <= result["positions"][contact_index, 0, 1] <= 1.2


def test_bone_pairs_match_exact_upstream_order():
    # Guards against re-drift from the exact upstream get_bone_pairs() 'coco'
    # order (BST training) -- bone features concatenate in this sequence, so
    # a different order/membership silently miscodes the model input.
    expected = [
        (0, 1), (0, 2), (1, 2), (1, 3), (2, 4),
        (3, 5), (4, 6),
        (5, 7), (7, 9), (6, 8), (8, 10),
        (5, 6), (5, 11), (6, 12), (11, 12),
        (11, 13), (13, 15), (12, 14), (14, 16),
    ]
    assert bi.BONE_PAIRS == expected
    assert len(bi.BONE_PAIRS) == 19


def _side_dict(offset=0.0):
    j = np.arange(17, dtype=float)
    kp = np.stack([600 + 2 * j, 300 + 3 * j], axis=1)
    if offset:
        # A uniform additive offset alone would be a pure translation, and
        # _normalize_pose's bbox-centered scaling is translation- and
        # scale-invariant -- so it would normalize identically to offset=0
        # and never actually distinguish "upper" from "lower". Perturb each
        # joint non-uniformly instead so the relative geometry (and hence
        # the normalized pose) genuinely differs.
        kp[:, 0] += offset * np.sin(j)
        kp[:, 1] += offset * np.cos(j)
    return {
        "keypoints": kp,
        "centroid": (float(kp[11][0] + kp[12][0]) / 2.0, float(kp[11][1] + kp[12][1]) / 2.0),
    }


def _both_players_record(frame, shuttle=(640.0, 360.0)):
    return {
        "shuttle": shuttle,
        "players": {"lower": _side_dict(offset=0.0), "upper": _side_dict(offset=100.0)},
    }


def test_hitter_lower_fills_person0_from_lower_person1_from_upper():
    records = {f: _both_players_record(f) for f in range(WINDOW_START, WINDOW_START + bi.SEQ_LEN)}
    result = bi.build_inputs(CONTACT_FRAME, records.get, COURT_CORNERS, VIDEO_WH, hitter="lower")
    assert result is not None
    contact_index = bi.SEQ_LEN // 2
    # person-0 (hitter=lower) and person-1 (opponent=upper) both carry real,
    # DIFFERENT data -- opponent is no longer permanently zero-filled.
    assert np.any(result["pose"][contact_index, 0] != 0.0)
    assert np.any(result["pose"][contact_index, 1] != 0.0)
    assert not np.allclose(result["pose"][contact_index, 0], result["pose"][contact_index, 1])
    assert np.any(result["positions"][contact_index, 0] != result["positions"][contact_index, 1])


def test_hitter_upper_swaps_person0_and_person1():
    records = {f: _both_players_record(f) for f in range(WINDOW_START, WINDOW_START + bi.SEQ_LEN)}
    result_lower = bi.build_inputs(CONTACT_FRAME, records.get, COURT_CORNERS, VIDEO_WH, hitter="lower")
    result_upper = bi.build_inputs(CONTACT_FRAME, records.get, COURT_CORNERS, VIDEO_WH, hitter="upper")
    contact_index = bi.SEQ_LEN // 2
    # Swapping hitter swaps which side lands in person-0 vs person-1.
    np.testing.assert_allclose(result_lower["pose"][contact_index, 0], result_upper["pose"][contact_index, 1])
    np.testing.assert_allclose(result_lower["pose"][contact_index, 1], result_upper["pose"][contact_index, 0])


def test_hitter_none_default_is_byte_identical_to_pre_b1_behavior():
    """No hitter given -> exactly today's contract: person-0 from the
    record's top-level keypoints/centroid, person-1 zero-filled."""
    records = {f: _record(f) for f in range(WINDOW_START, WINDOW_START + bi.SEQ_LEN)}
    result = bi.build_inputs(CONTACT_FRAME, records.get, COURT_CORNERS, VIDEO_WH)
    assert result is not None
    assert np.all(result["pose"][:, 1] == 0.0)
    assert np.all(result["positions"][:, 1] == 0.0)


def test_hitter_given_but_players_key_missing_degrades_to_zero_fill():
    """Never-fatal: a hitter is requested but some/all frames in the window
    lack the "players" sub-dict (e.g. that frame wasn't densely captured) ->
    those frames zero-fill rather than raising."""
    records = {f: {"shuttle": (640.0, 360.0)} for f in range(WINDOW_START, WINDOW_START + bi.SEQ_LEN)}
    result = bi.build_inputs(CONTACT_FRAME, records.get, COURT_CORNERS, VIDEO_WH, hitter="lower")
    # Too few posed frames (none have "players") -> gate returns None, same
    # never-fatal contract as test_all_none_keypoints_returns_none.
    assert result is None
