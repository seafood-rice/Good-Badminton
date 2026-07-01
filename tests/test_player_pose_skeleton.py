import numpy as np
from badminton_analysis.visualization.player_pose import PlayerPoseVisualizer
from badminton_analysis.visualization.skeleton import SKELETON_CONNECTIONS


class _FakePose:
    inference_name = "Fake"
    def process_frame(self, frame):
        return None, None


def test_uses_shared_connections():
    v = PlayerPoseVisualizer(rtmpose_processor=_FakePose())
    assert list(v.skeleton_connections) == list(SKELETON_CONNECTIONS)


def test_draw_skeleton_on_frame_applies_offset_and_draws():
    v = PlayerPoseVisualizer(rtmpose_processor=_FakePose())
    frame = np.zeros((300, 300, 3), dtype=np.uint8)
    person = np.zeros((17, 2))
    for i in range(17):
        person[i] = (10 + i * 3, 12 + i * 3)  # ROI-local coords
    # single person as (1,17,2)
    v._draw_skeleton_on_frame(frame, person[None, ...], offset_x=40, offset_y=50)
    assert int(frame.sum()) > 0  # drew something after offset shift


def test_missing_keypoint_sentinel_not_shifted_into_view():
    v = PlayerPoseVisualizer(rtmpose_processor=_FakePose())
    frame = np.zeros((300, 300, 3), dtype=np.uint8)
    # All keypoints are (0,0) — the "missing" sentinel; none should be shifted or drawn.
    person = np.zeros((17, 2))
    v._draw_skeleton_on_frame(frame, person[None, ...], offset_x=40, offset_y=50)
    # The sentinel (0,0) must NOT be shifted to (40,50) and drawn there.
    assert frame[50, 40].sum() == 0  # pixel at (offset_y=50, offset_x=40) is still black
