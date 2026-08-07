from badminton_analysis.posture.system import PostureAnalysisSystem


def _sys(tmp_path, **kw):
    # Construct without a real video: point at a dummy path but only test __init__ + _build_pose_processor,
    # which do not open the video. Use a path that exists to pass the __init__ guard.
    import os
    vid = tmp_path / "clip.mp4"
    vid.write_bytes(b"x")  # existence only; not opened by these methods
    return PostureAnalysisSystem(str(vid), stroke_type="high_clear",
                                 output_dir=str(tmp_path / "out"), **kw)


def test_defaults_to_yolo_family(tmp_path):
    s = _sys(tmp_path)
    assert s.pose_family == "yolo-pose"


def test_pose_family_stored(tmp_path):
    s = _sys(tmp_path, pose_family="rtmpose", pose_mode="performance")
    assert s.pose_family == "rtmpose"
    assert s.pose_mode == "performance"


def test_build_pose_processor_yolo_uses_injected_builder(tmp_path, monkeypatch):
    s = _sys(tmp_path, pose_family="yolo-pose")
    made = {}
    import badminton_analysis.detection.yolo_pose as yp
    class _FakeYolo:
        def __init__(self, model_path="x", **kw): made["yolo"] = model_path
        def process_frame(self, f): return None, None
    monkeypatch.setattr(yp, "YOLOPoseProcessor", _FakeYolo)
    proc = s._build_pose_processor()
    assert isinstance(proc, _FakeYolo)
    assert "yolo" in made


def test_build_pose_processor_rtm_uses_injected_builder(tmp_path, monkeypatch):
    s = _sys(tmp_path, pose_family="rtmo", pose_mode="balanced")
    made = {}
    import badminton_analysis.detection.rtmpose as rp
    class _FakeRtm:
        def __init__(self, mode="balanced", pose_family="rtmpose", **kw):
            made["mode"] = mode; made["family"] = pose_family
        def process_frame(self, f): return None, None
    monkeypatch.setattr(rp, "RTMPoseProcessor", _FakeRtm)
    proc = s._build_pose_processor()
    assert isinstance(proc, _FakeRtm)
    assert made["family"] == "rtmo" and made["mode"] == "balanced"
