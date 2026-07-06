import numpy as np
import pytest

from badminton_analysis.posture.system import PostureAnalysisSystem, person_roi
from badminton_analysis.detection.racket import RacketDetector
import badminton_analysis.analysis.joint_angles as ja
import app as webapp


def _system(tmp_path, **kwargs):
    video = tmp_path / "drill.mp4"
    video.write_bytes(b"x")  # ctor only checks existence
    return PostureAnalysisSystem(str(video), "high_clear",
                                 output_dir=str(tmp_path / "out"), **kwargs)


def _kp():
    kp = np.zeros((17, 2), dtype=float)
    kp[ja.R_ELBOW] = (100.0, 100.0)
    kp[ja.R_WRIST] = (120.0, 80.0)
    return kp


# ── person_roi: bounding box around valid keypoints, padded by ROI_MARGIN ──

def test_person_roi_none_when_kp_is_none():
    assert person_roi(None) is None


def test_person_roi_none_when_fewer_than_two_valid_joints():
    kp = np.zeros((17, 2), dtype=float)
    kp[ja.R_WRIST] = (120.0, 80.0)  # only one valid joint; rest are (0, 0) sentinels
    assert person_roi(kp) is None


def test_person_roi_expands_bbox_by_margin():
    kp = _kp()  # R_ELBOW (100, 100), R_WRIST (120, 80) -> bbox 20 x 20
    # pad = ROI_MARGIN(0.75) * max(width=20, height=20) = 15
    assert person_roi(kp) == [(85.0, 65.0), (135.0, 115.0)]


class _FakeDetector:
    def __init__(self, point):
        self.point = point
        self.calls = 0

    def detect_racket_head(self, frame, roi_corners=None):
        self.calls += 1
        return self.point


def test_detector_result_wins(tmp_path):
    sys_ = _system(tmp_path)
    sys_._racket_detector = _FakeDetector((5.0, 6.0))
    head = sys_._resolve_racket_head(frame=None, kp=_kp(), ja=ja)
    assert head == (5.0, 6.0)
    assert sys_._racket_stats == {"detected": 1, "inferred": 0}


def test_fallback_when_detector_returns_none(tmp_path):
    sys_ = _system(tmp_path)
    sys_._racket_detector = _FakeDetector(None)
    head = sys_._resolve_racket_head(frame=None, kp=_kp(), ja=ja)
    assert head is not None  # wrist inference produced a point
    assert sys_._racket_stats == {"detected": 0, "inferred": 1}


def test_fallback_when_no_detector(tmp_path):
    sys_ = _system(tmp_path)
    assert sys_._racket_detector is None
    head = sys_._resolve_racket_head(frame=None, kp=_kp(), ja=ja)
    assert head is not None
    assert sys_._racket_stats == {"detected": 0, "inferred": 1}


def test_missing_weights_path_does_not_raise(tmp_path):
    sys_ = _system(tmp_path, racket_model_path=str(tmp_path / "nope.pt"))
    sys_._build_racket_detector()
    assert sys_._racket_detector is None or sys_._racket_detector.model is None
    head = sys_._resolve_racket_head(frame=None, kp=_kp(), ja=ja)
    assert head is not None


def test_detector_runs_without_pose(tmp_path):
    sys_ = _system(tmp_path)
    sys_._racket_detector = _FakeDetector((7.0, 8.0))
    head = sys_._resolve_racket_head(frame=None, kp=None, ja=ja)
    assert head == (7.0, 8.0)
    assert sys_._racket_stats == {"detected": 1, "inferred": 0}


def test_no_pose_and_no_detection_yields_none(tmp_path):
    sys_ = _system(tmp_path)
    sys_._racket_detector = _FakeDetector(None)
    head = sys_._resolve_racket_head(frame=None, kp=None, ja=ja)
    assert head is None
    assert sys_._racket_stats == {"detected": 0, "inferred": 0}


def test_detector_construction_failure_is_tolerated(tmp_path, monkeypatch):
    import badminton_analysis.detection.racket as racket_mod

    def _boom(self, *a, **k):
        raise RuntimeError("cuda exploded")

    monkeypatch.setattr(racket_mod.RacketDetector, "__init__", _boom)
    sys_ = _system(tmp_path, racket_model_path=str(tmp_path / "w.pt"))
    sys_._build_racket_detector()
    assert sys_._racket_detector is None
    head = sys_._resolve_racket_head(frame=None, kp=_kp(), ja=ja)
    assert head is not None


# ── Person-ROI gate on the posture detection path ───────────────────────────
# RacketDetector.detect_racket_head already filters candidate boxes against
# roi_corners via RacketDetector._point_in_roi; these tests fake the
# underlying YOLO model (not detect_racket_head itself) so that filtering
# actually runs and the gate is genuinely exercised.

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


class _FakeYoloResult:
    def __init__(self, boxes):
        self.boxes = boxes


class _FakeYoloModel:
    def __init__(self, xywh, conf):
        self._result = _FakeYoloResult(_FakeBoxes(xywh, conf))

    def __call__(self, frame, **kwargs):
        return [self._result]


def test_gate_accepts_detection_near_person(tmp_path):
    sys_ = _system(tmp_path)
    kp = _kp()  # person_roi(kp) == [(85.0, 65.0), (135.0, 115.0)]
    sys_._racket_detector = RacketDetector(model=_FakeYoloModel(xywh=[[110, 90, 10, 10]], conf=[0.9]))
    head = sys_._resolve_racket_head(frame=None, kp=kp, ja=ja)
    assert head == (110, 90)
    assert sys_._racket_stats == {"detected": 1, "inferred": 0}


def test_gate_rejects_detection_far_from_person(tmp_path):
    """Wall-fan-style false positive: a detection far outside the person ROI
    is rejected inside detect_racket_head, and the method falls through to
    kinematic inference instead of corrupting wrist_flexion.

    Fails before the person-ROI gate (the far detection was accepted because
    no ROI was passed to reject it); passes after.
    """
    sys_ = _system(tmp_path)
    kp = _kp()
    sys_._racket_detector = RacketDetector(model=_FakeYoloModel(xywh=[[900, 900, 10, 10]], conf=[0.9]))
    head = sys_._resolve_racket_head(frame=None, kp=kp, ja=ja)
    assert head is not None  # kinematic fallback still produced a point
    assert head != (900, 900)
    assert sys_._racket_stats == {"detected": 0, "inferred": 1}


def test_gate_allows_detector_result_when_pose_missing(tmp_path):
    sys_ = _system(tmp_path)
    sys_._racket_detector = RacketDetector(model=_FakeYoloModel(xywh=[[900, 900, 10, 10]], conf=[0.9]))
    head = sys_._resolve_racket_head(frame=None, kp=None, ja=ja)
    assert head == (900, 900)
    assert sys_._racket_stats == {"detected": 1, "inferred": 0}


def test_posture_builds_racket_detector_with_conf_0_15(tmp_path, monkeypatch):
    """Posture path must use conf=0.15 for close-up drill footage (lower threshold)."""
    import badminton_analysis.detection.racket as racket_mod

    recorded_kwargs = {}

    def capture_init(self, *args, **kwargs):
        recorded_kwargs.update(kwargs)
        # Minimal setup to avoid errors
        self.conf = kwargs.get('conf', 0.25)
        self.model = None
        self.device = 'cpu'
        self.roi_padding_ratio = 0.08

    monkeypatch.setattr(racket_mod.RacketDetector, "__init__", capture_init)
    sys_ = _system(tmp_path, racket_model_path=str(tmp_path / "w.pt"))
    sys_._build_racket_detector()
    assert recorded_kwargs.get('conf') == 0.15


def test_racket_weights_prefers_n_then_s(tmp_path):
    assert webapp._racket_weights(base=tmp_path) is None
    (tmp_path / "yolo11s-racket.pt").write_bytes(b"s")
    assert webapp._racket_weights(base=tmp_path).endswith("yolo11s-racket.pt")
    (tmp_path / "yolo11n-racket.pt").write_bytes(b"n")
    assert webapp._racket_weights(base=tmp_path).endswith("yolo11n-racket.pt")


def test_racket_weights_default_base_is_weights_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(webapp, "PROJECT_ROOT", tmp_path)
    assert webapp._racket_weights() is None
    (tmp_path / "weights").mkdir()
    (tmp_path / "weights" / "yolo11n-racket.pt").write_bytes(b"n")
    assert webapp._racket_weights().endswith("yolo11n-racket.pt")


# ── Route-level: --racket-model reaches the subprocess cmd ─────────────────

@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(webapp, "OUTPUTS", tmp_path)
    webapp.app.config["TESTING"] = True
    return webapp.app.test_client()


class _FakeProc:
    returncode = 0

    def poll(self):
        return 0

    def wait(self):
        return 0


class _FakePostureProc:
    # non-zero return skips the ffmpeg re-encode branch of the posture
    # tracking thread, so these tests stay hermetic regardless of thread
    # scheduling relative to monkeypatch teardown.
    returncode = 1
    stdout = []

    def wait(self):
        return 1


def test_analyze_command_includes_racket_model_when_weights_found(client, tmp_path, monkeypatch):
    videos = tmp_path / "vids"
    videos.mkdir()
    (videos / "clip.mp4").write_bytes(b"\x00")
    monkeypatch.setattr(webapp, "VIDEOS", videos)
    monkeypatch.setattr(webapp, "TEMPLATES", tmp_path / "tpl")
    (tmp_path / "tpl").mkdir()

    monkeypatch.setattr(webapp, "_racket_weights", lambda base=None: "weights/yolo11n-racket.pt")

    captured = {}

    def fake_popen(cmd, **kwargs):
        captured["cmd"] = cmd
        return _FakeProc()

    monkeypatch.setattr(webapp.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(webapp.subprocess, "run", lambda *a, **k: _FakeProc())

    r = client.post("/api/analyze", json={"video": "clip.mp4"})
    assert r.status_code == 200
    cmd = captured["cmd"]
    assert "--racket-model" in cmd
    assert cmd[cmd.index("--racket-model") + 1] == "weights/yolo11n-racket.pt"


def test_analyze_command_omits_racket_model_when_weights_absent(client, tmp_path, monkeypatch):
    videos = tmp_path / "vids"
    videos.mkdir()
    (videos / "clip.mp4").write_bytes(b"\x00")
    monkeypatch.setattr(webapp, "VIDEOS", videos)
    monkeypatch.setattr(webapp, "TEMPLATES", tmp_path / "tpl")
    (tmp_path / "tpl").mkdir()

    monkeypatch.setattr(webapp, "_racket_weights", lambda base=None: None)

    captured = {}

    def fake_popen(cmd, **kwargs):
        captured["cmd"] = cmd
        return _FakeProc()

    monkeypatch.setattr(webapp.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(webapp.subprocess, "run", lambda *a, **k: _FakeProc())

    r = client.post("/api/analyze", json={"video": "clip.mp4"})
    assert r.status_code == 200
    assert "--racket-model" not in captured["cmd"]


def test_posture_command_includes_racket_model_when_weights_found(client, tmp_path, monkeypatch):
    monkeypatch.setattr(webapp, "VIDEOS", tmp_path)
    (tmp_path / "clip.mp4").write_bytes(b"x")

    monkeypatch.setattr(webapp, "_racket_weights", lambda base=None: "weights/yolo11n-racket.pt")

    captured = {}

    def fake_popen(cmd, **kwargs):
        captured["cmd"] = cmd
        return _FakePostureProc()

    monkeypatch.setattr(webapp.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(webapp.subprocess, "run", lambda *a, **k: _FakePostureProc())

    r = client.post("/api/posture/analyze",
                    json={"video": "clip.mp4", "stroke_type": "high_clear"})
    assert r.status_code == 200
    cmd = captured["cmd"]
    assert "--racket-model" in cmd
    assert cmd[cmd.index("--racket-model") + 1] == "weights/yolo11n-racket.pt"


def test_posture_command_omits_racket_model_when_weights_absent(client, tmp_path, monkeypatch):
    monkeypatch.setattr(webapp, "VIDEOS", tmp_path)
    (tmp_path / "clip.mp4").write_bytes(b"x")

    monkeypatch.setattr(webapp, "_racket_weights", lambda base=None: None)

    captured = {}

    def fake_popen(cmd, **kwargs):
        captured["cmd"] = cmd
        return _FakePostureProc()

    monkeypatch.setattr(webapp.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(webapp.subprocess, "run", lambda *a, **k: _FakePostureProc())

    r = client.post("/api/posture/analyze",
                    json={"video": "clip.mp4", "stroke_type": "high_clear"})
    assert r.status_code == 200
    assert "--racket-model" not in captured["cmd"]
