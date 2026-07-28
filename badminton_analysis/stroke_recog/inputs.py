"""Per-hit BST input-tensor assembly from match-pipeline frame data.

Pure functions (numpy only, no torch, no model access): turn ``SEQ_LEN``
frames of the match pipeline's per-frame records
(``badminton_analysis.system.BadmintonAnalysisSystem._analysis_frames``) into
the exact ``(pose, shuttle, positions)`` arrays ``bst_model.predict`` expects.
See ``third_party/bst/CONTRACT.md`` for the authoritative shapes and
``third_party/bst/model/bst.py::BST_CG_AP.forward`` for the tensor layout each
array is built to match: ``JnB: (b, t, n, in_dim)``, ``shuttle: (b, t, 2)``,
``pos: (b, t, n, 2)`` -- ``build_inputs`` returns the no-batch-dim versions
(``bst_model.predict`` adds the batch dim).

Known data-availability limit: the match pipeline currently tracks a single
player's pose/position per frame -- whichever the tracker locks onto that
frame (``system.py::_capture_analysis_frame``) -- never both players
simultaneously, and it does not (yet) carry a "shuttle" key on the same
per-frame record (that lives in the separate ``_analysis_track`` list, keyed
positionally rather than by frame). So here "player" index 0 is always the
tracked player for that frame (nominally the hitter); index 1 is always
zero-filled for both ``pose`` and ``positions``, and ``shuttle`` is
zero-filled unless the caller's ``frame_lookup`` records happen to carry a
"shuttle" key (forward-compatible: today's ``system.py`` records do not --
closing that gap is left to the Task 5 recognizer wiring / a future
``system.py`` patch, not this module).
"""

import numpy as np

from ..analysis.joint_angles import L_ANKLE, L_HIP, R_ANKLE, R_HIP
from ..court.mapper import CourtMapper
from .bst_model import N_PEOPLE, POSE_IN_DIM, SEQ_LEN

POSE_SHAPE = (SEQ_LEN, N_PEOPLE, POSE_IN_DIM)
SHUTTLE_SHAPE = (SEQ_LEN, 2)
POS_SHAPE = (SEQ_LEN, N_PEOPLE, 2)

# Too few posed frames in the window -> not enough signal for BST; caller
# should skip this hit rather than feed it a mostly-zero clip.
_MIN_POSED_FRAMES = SEQ_LEN // 3

N_JOINTS = 17

# Exact upstream get_bone_pairs() 'coco' order (BST training); order is
# load-bearing -- bone features concatenate in this sequence.
BONE_PAIRS = [
    (0, 1), (0, 2), (1, 2), (1, 3), (2, 4),      # head
    (3, 5), (4, 6),                              # ears->shoulders
    (5, 7), (7, 9), (6, 8), (8, 10),             # arms
    (5, 6), (5, 11), (6, 12), (11, 12),          # torso
    (11, 13), (13, 15), (12, 14), (14, 16),      # legs
]
assert len(BONE_PAIRS) == 19

# Standard badminton court dimensions in meters (matches
# court.mapper.CourtMapper's default court_dimensions) -- used to normalize
# court-space player positions into [0, 1] the same way shuttle is normalized
# by video_wh.
_COURT_W, _COURT_H = 6.1, 13.4


def _sentinel_invalid(x, y):
    """True when (x, y) is the codebase's "undetected joint" sentinel.

    Matches ``badminton_analysis.analysis.joint_angles.is_valid``'s rule:
    a joint is invalid only when *both* coordinates are <= 1.
    """
    return x <= 1.0 and y <= 1.0


def _valid_mask(kp):
    """(17,) bool mask, vectorized ``_sentinel_invalid`` over all joints."""
    return ~((kp[:, 0] <= 1.0) & (kp[:, 1] <= 1.0))


def _is_posed(rec):
    """A frame counts as posed when both hips are detected (non-sentinel)."""
    if rec is None:
        return False
    kp = rec.get("keypoints")
    if kp is None:
        return False
    kp = np.asarray(kp, dtype=float)
    return (not _sentinel_invalid(kp[L_HIP][0], kp[L_HIP][1])
            and not _sentinel_invalid(kp[R_HIP][0], kp[R_HIP][1]))


def _normalize_pose(kp):
    """(17,2) full-frame pixel keypoints -> (72,) bbox-relative JnB_bone features.

    Joints are re-centered on the player's bounding box (min/max of the valid
    joints in this frame) and scaled by half the bbox width/height, so joints
    at the bbox edges land at exactly +-1 and interior joints fall inside
    [-1, 1]. Missing joints (sentinel, see ``_sentinel_invalid``) are
    zero-filled, i.e. placed at the bbox center. Bone features are the vector
    between each of the 19 skeleton edges' *normalized* joint coordinates
    (``BONE_PAIRS``), concatenated after the 17 normalized joints -- 72 =
    17*2 + 19*2, matching CONTRACT.md's POSE_IN_DIM derivation.
    """
    kp = np.asarray(kp, dtype=np.float64)
    valid = _valid_mask(kp)
    norm = np.zeros((N_JOINTS, 2), dtype=np.float64)
    if np.any(valid):
        xs, ys = kp[valid, 0], kp[valid, 1]
        x_min, x_max = xs.min(), xs.max()
        y_min, y_max = ys.min(), ys.max()
        cx, cy = (x_min + x_max) / 2.0, (y_min + y_max) / 2.0
        half_w = max((x_max - x_min) / 2.0, 1e-6)
        half_h = max((y_max - y_min) / 2.0, 1e-6)
        norm[valid, 0] = (kp[valid, 0] - cx) / half_w
        norm[valid, 1] = (kp[valid, 1] - cy) / half_h
    bones = np.zeros((len(BONE_PAIRS), 2), dtype=np.float64)
    for i, (a, b) in enumerate(BONE_PAIRS):
        bones[i] = norm[b] - norm[a]
    return np.concatenate([norm.reshape(-1), bones.reshape(-1)]).astype(np.float32)


def _shuttle_xy(rec, video_wh):
    """(x, y) shuttle position normalized to [0, 1] by video_wh, or (0, 0)."""
    if rec is None:
        return 0.0, 0.0
    shuttle = rec.get("shuttle")
    if shuttle is None:
        return 0.0, 0.0
    width, height = video_wh
    if not width or not height:
        return 0.0, 0.0
    x, y = shuttle
    return float(x) / float(width), float(y) / float(height)


def _foot_point(rec):
    """Best-available full-frame pixel (x, y) foot position for the tracked player.

    Prefers the ankle midpoint from ``keypoints``; falls back to the
    tracker's bbox ``centroid`` (a coarser proxy for court position) when no
    ankle is detected; ``None`` when neither is available.
    """
    if rec is None:
        return None
    kp = rec.get("keypoints")
    if kp is not None:
        kp = np.asarray(kp, dtype=float)
        ankles = [idx for idx in (L_ANKLE, R_ANKLE) if not _sentinel_invalid(kp[idx][0], kp[idx][1])]
        if ankles:
            pts = kp[ankles]
            return float(pts[:, 0].mean()), float(pts[:, 1].mean())
    centroid = rec.get("centroid")
    if centroid is not None:
        return float(centroid[0]), float(centroid[1])
    return None


def _select_hitter_and_opponent(rec, hitter):
    """(hitter_rec, opponent_rec) sub-dicts to read pose/position from.

    hitter is None/"unknown" -> (rec, None): today's pre-B1 contract
    (person-0 from the record's own top-level keys, person-1 always zero).
    hitter is "lower"/"upper" -> reads rec["players"][hitter] /
    rec["players"][opponent]; missing/absent -> None (zero-fills that slot,
    never raises -- same never-fatal convention as the rest of this module).
    """
    if hitter not in ("lower", "upper"):
        return rec, None
    players = (rec or {}).get("players") or {}
    opponent = "upper" if hitter == "lower" else "lower"
    return players.get(hitter), players.get(opponent)


def build_inputs(contact_frame, frame_lookup, court_corners, video_wh, hitter=None):
    """Assemble one hit's (pose, shuttle, positions) BST input arrays.

    Gathers the ``SEQ_LEN``-frame window centered on ``contact_frame`` (15
    frames before, the contact frame itself, 14 frames after -- so
    ``contact_frame`` lands at output index ``SEQ_LEN // 2``), reading each
    frame's record via ``frame_lookup(frame_index)``. Frames outside the
    tracked range (window runs past the start/end of the video, or before any
    frame has been recorded) come back ``None`` from ``frame_lookup`` and are
    zero-filled -- the returned arrays are always exactly ``SEQ_LEN`` long,
    never truncated, even when ``contact_frame`` is near 0.

    Returns ``None`` when fewer than ``SEQ_LEN // 3`` frames in the window
    have a valid hitter pose (both hips detected) -- too little signal for
    BST to work with.

    Parameters
    ----------
    contact_frame : int
    frame_lookup : callable[int] -> dict | None
        Match-pipeline per-frame record (see module docstring for the exact
        keys read: "keypoints", "centroid", "shuttle").
    court_corners : sequence of 4 (x, y) points
        Passed straight to ``badminton_analysis.court.mapper.CourtMapper``.
    video_wh : (width, height)
    hitter : "lower" | "upper" | None
        (new, optional) selects which side's data fills person-0 (hitter) vs
        person-1 (opponent), read from each window frame's
        ``rec["players"][side]`` sub-dict (badminton_analysis.system's
        both-player capture). Omitted/None keeps the original v1 contract:
        person-0 from the record's own top-level "keypoints"/"centroid",
        person-1 always zero-filled.

    Returns
    -------
    dict | None
        ``{"pose": np.ndarray POSE_SHAPE, "shuttle": np.ndarray SHUTTLE_SHAPE,
        "positions": np.ndarray POS_SHAPE}`` (float32), or ``None``.
    """
    half = SEQ_LEN // 2
    start = contact_frame - half
    records = [frame_lookup(idx) for idx in range(start, start + SEQ_LEN)]
    sides = [_select_hitter_and_opponent(rec, hitter) for rec in records]

    if sum(1 for hitter_rec, _opp in sides if _is_posed(hitter_rec)) < _MIN_POSED_FRAMES:
        return None

    mapper = CourtMapper(court_corners)

    pose = np.zeros(POSE_SHAPE, dtype=np.float32)
    shuttle = np.zeros(SHUTTLE_SHAPE, dtype=np.float32)
    positions = np.zeros(POS_SHAPE, dtype=np.float32)

    for t, (rec, (hitter_rec, opponent_rec)) in enumerate(zip(records, sides)):
        if hitter_rec is not None and hitter_rec.get("keypoints") is not None:
            pose[t, 0] = _normalize_pose(hitter_rec["keypoints"])
        if opponent_rec is not None and opponent_rec.get("keypoints") is not None:
            pose[t, 1] = _normalize_pose(opponent_rec["keypoints"])

        shuttle[t] = _shuttle_xy(rec, video_wh)

        foot = _foot_point(hitter_rec)
        if foot is not None:
            court_xy = mapper.image_to_court(foot)
            if len(court_xy):
                positions[t, 0, 0] = float(court_xy[0]) / _COURT_W
                positions[t, 0, 1] = float(court_xy[1]) / _COURT_H

        foot_opp = _foot_point(opponent_rec)
        if foot_opp is not None:
            court_xy_opp = mapper.image_to_court(foot_opp)
            if len(court_xy_opp):
                positions[t, 1, 0] = float(court_xy_opp[0]) / _COURT_W
                positions[t, 1, 1] = float(court_xy_opp[1]) / _COURT_H

    return {"pose": pose, "shuttle": shuttle, "positions": positions}
