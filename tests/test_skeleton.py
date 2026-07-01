import numpy as np
from badminton_analysis.visualization.skeleton import SKELETON_CONNECTIONS, draw_skeleton


def _full_person():
    # 17 valid keypoints spread across a 400x400 frame region.
    kp = np.zeros((17, 2))
    for i in range(17):
        kp[i] = (50 + i * 5, 60 + i * 5)
    return kp


def test_connections_are_coco_pairs():
    assert (5, 6) in SKELETON_CONNECTIONS
    assert len(SKELETON_CONNECTIONS) == 12
    for a, b in SKELETON_CONNECTIONS:
        assert 0 <= a < 17 and 0 <= b < 17


def test_draw_skeleton_mutates_and_returns_same_frame():
    frame = np.zeros((400, 400, 3), dtype=np.uint8)
    out = draw_skeleton(frame, _full_person())
    assert out is frame
    assert int(frame.sum()) > 0  # something drawn


def test_draw_skeleton_none_and_empty_safe():
    frame = np.zeros((100, 100, 3), dtype=np.uint8)
    assert draw_skeleton(frame, None) is frame
    assert draw_skeleton(frame, np.zeros((0, 2))) is frame
    assert int(frame.sum()) == 0  # nothing drawn


def test_draw_skeleton_skips_missing_keypoints():
    frame = np.zeros((200, 200, 3), dtype=np.uint8)
    kp = np.ones((17, 2))  # all (1,1) -> all "missing" (x<=1 and y<=1)
    draw_skeleton(frame, kp)
    assert int(frame.sum()) == 0  # nothing drawn for missing points


def test_draw_skeleton_respects_confidence():
    frame = np.zeros((200, 200, 3), dtype=np.uint8)
    kp = _full_person()
    conf = np.ones(17) * 0.9
    conf[:] = 0.0  # all below threshold
    draw_skeleton(frame, kp, conf=conf, conf_thresh=0.3)
    assert int(frame.sum()) == 0  # all filtered out by low confidence
