import numpy as np
from badminton_analysis.detection.racket import RacketDetector
from badminton_analysis.analysis import joint_angles as ja


class _FakeBoxes:
    def __init__(self, xywh, conf):
        self.xywh = _FakeTensor(np.array(xywh, dtype=float))
        self.conf = _FakeTensor(np.array(conf, dtype=float))


class _FakeTensor:
    def __init__(self, arr):
        self._arr = arr
        self.shape = arr.shape

    def detach(self):
        return self

    def cpu(self):
        return self

    def numpy(self):
        return self._arr


class _FakeResult:
    def __init__(self, boxes):
        self.boxes = boxes


class _FakeModel:
    def __init__(self, result):
        self._result = result

    def __call__(self, frame, **kwargs):
        return [self._result]


def test_returns_none_without_model():
    det = RacketDetector(model=None)
    frame = np.zeros((100, 100, 3), dtype=np.uint8)
    assert det.detect_racket_head(frame) is None


def test_picks_highest_confidence_center():
    boxes = _FakeBoxes(xywh=[[50, 60, 10, 10], [20, 20, 10, 10]], conf=[0.9, 0.4])
    model = _FakeModel(_FakeResult(boxes))
    det = RacketDetector(model=model)
    frame = np.zeros((200, 200, 3), dtype=np.uint8)
    assert det.detect_racket_head(frame) == (50, 60)


def test_filters_outside_roi():
    boxes = _FakeBoxes(xywh=[[500, 500, 10, 10]], conf=[0.9])
    model = _FakeModel(_FakeResult(boxes))
    det = RacketDetector(model=model)
    frame = np.zeros((600, 600, 3), dtype=np.uint8)
    # ROI is a small top-left box; detection at (500,500) is far outside
    assert det.detect_racket_head(frame, roi_corners=[(0, 0), (100, 100)]) is None


def test_infer_racket_head_valid_arm():
    """RacketDetector(model=None) can still infer racket head from keypoints."""
    det = RacketDetector(model=None)
    kp = np.zeros((17, 2), dtype=float)
    kp[ja.R_ELBOW] = (100, 100)
    kp[ja.R_WRIST] = (140, 100)
    result = det.infer_racket_head(kp, dominant="right")
    assert result is not None
    # default extend=0.6: wrist + 0.6*(40,0) = (164.0, 100.0)
    assert abs(result[0] - 164.0) < 1e-9
    assert abs(result[1] - 100.0) < 1e-9


def test_infer_racket_head_missing_wrist():
    """infer_racket_head returns None when wrist is missing."""
    det = RacketDetector(model=None)
    kp = np.zeros((17, 2), dtype=float)
    kp[ja.R_ELBOW] = (100, 100)
    # wrist stays at (0,0) — missing
    result = det.infer_racket_head(kp, dominant="right")
    assert result is None
