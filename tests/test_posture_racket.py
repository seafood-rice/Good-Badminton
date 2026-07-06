import numpy as np
import pytest

from badminton_analysis.posture.system import PostureAnalysisSystem
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
