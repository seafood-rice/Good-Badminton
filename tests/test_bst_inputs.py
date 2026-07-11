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
