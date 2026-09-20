"""The far-court second pose pass.

Hermetic: a stub pose processor stands in for the model, so these pin the
filtering and coordinate handling, not detection quality.
"""
import numpy as np
import pytest

from badminton_analysis.court.mapper import CourtMapper
from badminton_analysis.tracking.static_filter import StaticCandidateFilter
from badminton_analysis.visualization.player_pose import PlayerPoseVisualizer

QUAD = [(1536, 1229), (2287, 1254), (3823, 2103), (9, 2031)]
ROI = (1172, 857, 2591, 1438)


class _StubPose:
    """Returns fixed keypoints in CROP coordinates, and records its imgsz."""

    def __init__(self, people):
        self.people = people
        self.seen_imgsz = None
        self.seen_shape = None

    def process_frame(self, frame, imgsz=None):
        self.seen_imgsz = imgsz
        self.seen_shape = frame.shape
        if not self.people:
            return None, None
        return np.asarray(self.people, dtype=float), None


def _person(feet_x, feet_y):
    """A COCO-17 person whose ankles sit at the given CROP coordinates."""
    kp = np.full((17, 2), 5.0, dtype=float)
    kp[15] = (feet_x - 6.0, feet_y)
    kp[16] = (feet_x + 6.0, feet_y)
    kp[9] = (feet_x - 20.0, feet_y - 120.0)
    kp[10] = (feet_x + 20.0, feet_y - 120.0)
    return kp


def _vis(people):
    stub = _StubPose(people)
    vis = PlayerPoseVisualizer(rtmpose_processor=stub)
    vis.court_mapper = CourtMapper(QUAD)
    return vis, stub


def _frame():
    return np.zeros((2160, 3840, 3), dtype=np.uint8)


# Feet positions measured for the real far player, in FULL-FRAME coordinates.
FAR_PLAYER_ABS = (1959.8, 1333.4)
FAR_PLAYER_CROP = (FAR_PLAYER_ABS[0] - ROI[0], FAR_PLAYER_ABS[1] - ROI[1] - 10)


def test_far_player_is_returned_in_full_frame_coordinates():
    vis, _stub = _vis([_person(*FAR_PLAYER_CROP)])
    centroids, _lh, _rh = vis.detect_far_players(_frame(), ROI, imgsz=1280)
    assert len(centroids) == 1
    assert centroids[0][0] == pytest.approx(FAR_PLAYER_ABS[0], abs=1.0)
    assert centroids[0][1] == pytest.approx(FAR_PLAYER_ABS[1], abs=1.0)


def test_the_configured_imgsz_reaches_the_model():
    """Ultralytics' 640 default is the whole reason the far player was missed."""
    vis, stub = _vis([_person(*FAR_PLAYER_CROP)])
    vis.detect_far_players(_frame(), ROI, imgsz=1280)
    assert stub.seen_imgsz == 1280


def test_only_the_crop_is_given_to_the_model():
    vis, stub = _vis([_person(*FAR_PLAYER_CROP)])
    vis.detect_far_players(_frame(), ROI, imgsz=1280)
    assert stub.seen_shape[0] == ROI[3] - ROI[1]
    assert stub.seen_shape[1] == ROI[2] - ROI[0]


def test_near_half_candidates_are_rejected():
    """The people sitting just past the net are inside this crop. Admitting
    them would let them outrank the real near player, which is 'closest to
    the net' in the LOWER half."""
    sitting_abs = (1256.0, 1440.0)      # court ~(0.45, 8.5): near half
    vis, _stub = _vis([_person(sitting_abs[0] - ROI[0],
                               sitting_abs[1] - ROI[1] - 10)])
    centroids, _lh, _rh = vis.detect_far_players(_frame(), ROI, imgsz=1280)
    assert centroids == []


def test_off_court_people_are_rejected():
    vis, _stub = _vis([_person(30.0, 60.0)])   # crop's top-left: off court
    centroids, _lh, _rh = vis.detect_far_players(_frame(), ROI, imgsz=1280)
    assert centroids == []


def test_people_without_ankles_are_rejected():
    kp = np.full((17, 2), 5.0, dtype=float)
    kp[15] = (0.0, 0.0)
    kp[16] = (0.0, 0.0)
    vis, _stub = _vis([kp])
    centroids, _lh, _rh = vis.detect_far_players(_frame(), ROI, imgsz=1280)
    assert centroids == []


def test_hands_are_reported_in_full_frame_coordinates():
    vis, _stub = _vis([_person(*FAR_PLAYER_CROP)])
    _c, lh, rh = vis.detect_far_players(_frame(), ROI, imgsz=1280)
    assert lh and rh
    (lx, ly) = list(lh.values())[0]
    assert lx == pytest.approx(FAR_PLAYER_ABS[0] - 20, abs=2)
    assert ly == pytest.approx(FAR_PLAYER_ABS[1] - 130, abs=3)


def test_a_static_candidate_is_dropped_by_the_filter():
    vis, _stub = _vis([_person(*FAR_PLAYER_CROP)])
    filt = StaticCandidateFilter(tol_px=30.0, window_frames=60, min_hits=30)
    out = None
    for f in range(0, 61):
        out = vis.detect_far_players(_frame(), ROI, imgsz=1280,
                                     static_filter=filt, frame_index=f)[0]
    assert out == []


def test_no_roi_means_no_work():
    vis, stub = _vis([_person(*FAR_PLAYER_CROP)])
    assert vis.detect_far_players(_frame(), None, imgsz=1280) == ([], {}, {})
    assert stub.seen_imgsz is None


def test_without_a_court_mapper_nothing_is_returned():
    """No mapper means no upper/lower test; returning everything would hand
    the tracker people from the next court."""
    stub = _StubPose([_person(*FAR_PLAYER_CROP)])
    vis = PlayerPoseVisualizer(rtmpose_processor=stub)
    vis.court_mapper = None
    assert vis.detect_far_players(_frame(), ROI, imgsz=1280) == ([], {}, {})


def test_far_keypoints_join_current_pose_data_for_drawing():
    vis, _stub = _vis([_person(*FAR_PLAYER_CROP)])
    vis.current_pose_data = None
    vis.detect_far_players(_frame(), ROI, imgsz=1280, main_offset=(0, 0))
    assert vis.current_pose_data is not None
    kps = vis.current_pose_data["keypoints"]
    assert kps.shape[0] == 1
    # Rebased onto the main pass's offset, i.e. full-frame here.
    assert kps[0][15][0] == pytest.approx(FAR_PLAYER_ABS[0] - 6.0, abs=1.0)


def test_merging_keeps_the_existing_people():
    vis, _stub = _vis([_person(*FAR_PLAYER_CROP)])
    vis.current_pose_data = {"keypoints": np.zeros((2, 17, 2)),
                             "offset_x": 0, "offset_y": 0}
    vis.detect_far_players(_frame(), ROI, imgsz=1280, main_offset=(0, 0))
    assert vis.current_pose_data["keypoints"].shape[0] == 3
