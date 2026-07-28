import numpy as np
import pytest
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


# ── Match-system wiring: RacketDetector construction must not abort startup ──

def test_match_system_tolerates_racket_detector_construction_failure(tmp_path, monkeypatch):
    """Corrupt/incompatible racket weights must not kill match analysis startup.

    Mirrors the posture path's PostureAnalysisSystem._build_racket_detector:
    detector construction failure -> _racket_detector stays None -> downstream
    falls back to wrist inference (pre-feature behavior), instead of the
    exception propagating out of BadmintonAnalysisSystem.__init__.
    """
    import badminton_analysis.system as system_mod

    class _Fake:
        """Stand-in for the heavy YOLO/pose/visualizer collaborators built in
        BadmintonAnalysisSystem.__init__; none of them are exercised here."""
        def __init__(self, *args, **kwargs):
            pass

    def _boom(self, *args, **kwargs):
        raise RuntimeError("corrupt weights")

    monkeypatch.setattr(RacketDetector, "__init__", _boom)
    monkeypatch.setattr(system_mod, "YOLO", _Fake, raising=False)
    monkeypatch.setattr(system_mod, "RTMPoseProcessor", _Fake, raising=False)
    monkeypatch.setattr(system_mod, "YOLOPoseProcessor", _Fake, raising=False)
    monkeypatch.setattr(system_mod, "ShuttlecockTracker", _Fake, raising=False)
    monkeypatch.setattr(system_mod, "PlayerPoseVisualizer", _Fake, raising=False)
    monkeypatch.setattr(system_mod, "CourtTrajectoryVisualizer", _Fake, raising=False)

    video = tmp_path / "clip.mp4"
    video.write_bytes(b"x")
    ball_model = tmp_path / "ball.pt"
    ball_model.write_bytes(b"x")

    sys_ = system_mod.BadmintonAnalysisSystem(
        str(video),
        ball_model_path=str(ball_model),
        analyze_technique=True,
        racket_model_path=str(tmp_path / "racket.pt"),
        output_dir=str(tmp_path / "out"),
    )
    assert sys_._racket_detector is None


def test_detect_racket_heads_returns_all_boxes_confidence_desc():
    boxes = _FakeBoxes(xywh=[[50, 60, 10, 10], [20, 20, 10, 10]], conf=[0.4, 0.9])
    model = _FakeModel(_FakeResult(boxes))
    det = RacketDetector(model=model)
    frame = np.zeros((200, 200, 3), dtype=np.uint8)
    heads = det.detect_racket_heads(frame)
    assert heads == [(20, 20), (50, 60)]  # confidence-descending: 0.9 then 0.4


def test_detect_racket_heads_filters_outside_roi():
    boxes = _FakeBoxes(xywh=[[50, 60, 10, 10], [500, 500, 10, 10]], conf=[0.9, 0.4])
    model = _FakeModel(_FakeResult(boxes))
    det = RacketDetector(model=model)
    frame = np.zeros((600, 600, 3), dtype=np.uint8)
    heads = det.detect_racket_heads(frame, roi_corners=[(0, 0), (100, 100)])
    assert heads == [(50, 60)]


def test_detect_racket_heads_empty_without_model():
    det = RacketDetector(model=None)
    frame = np.zeros((100, 100, 3), dtype=np.uint8)
    assert det.detect_racket_heads(frame) == []


def test_detect_racket_head_unchanged_after_refactor():
    """Non-regression: detect_racket_head's own behavior is untouched by the
    _boxes_in_roi refactor -- same fixture as test_picks_highest_confidence_center."""
    boxes = _FakeBoxes(xywh=[[50, 60, 10, 10], [20, 20, 10, 10]], conf=[0.9, 0.4])
    model = _FakeModel(_FakeResult(boxes))
    det = RacketDetector(model=model)
    frame = np.zeros((200, 200, 3), dtype=np.uint8)
    assert det.detect_racket_head(frame) == (50, 60)


@pytest.mark.parametrize("conf", [
    [0.9, 0.4],          # distinct confidences
    [0.4, 0.9],          # distinct, reversed
    [0.7, 0.7],          # exact tie -> stable sort must keep detection order
    [0.7, 0.7, 0.7],     # three-way tie
])
def test_detect_racket_heads_first_equals_detect_racket_head(conf):
    """Invariant system._capture_analysis_frame relies on to run the racket
    model ONCE per frame: heads[0] is exactly what detect_racket_head returns,
    ties included (max picks the first maximum; list.sort is stable, so
    reverse-sorting does not reorder equal-confidence boxes)."""
    xywh = [[50, 60, 10, 10], [20, 20, 10, 10], [80, 90, 10, 10]][:len(conf)]
    model = _FakeModel(_FakeResult(_FakeBoxes(xywh=xywh, conf=conf)))
    det = RacketDetector(model=model)
    frame = np.zeros((200, 200, 3), dtype=np.uint8)
    heads = det.detect_racket_heads(frame)
    assert heads[0] == det.detect_racket_head(frame)


def test_detect_racket_heads_empty_matches_detect_racket_head_none():
    """The other half of the invariant: no in-ROI boxes -> [] here and None
    there, so deriving racket_head from heads[0]-or-None cannot diverge."""
    boxes = _FakeBoxes(xywh=[[500, 500, 10, 10]], conf=[0.9])
    det = RacketDetector(model=_FakeModel(_FakeResult(boxes)))
    frame = np.zeros((600, 600, 3), dtype=np.uint8)
    roi = [(0, 0), (100, 100)]
    assert det.detect_racket_heads(frame, roi_corners=roi) == []
    assert det.detect_racket_head(frame, roi_corners=roi) is None
