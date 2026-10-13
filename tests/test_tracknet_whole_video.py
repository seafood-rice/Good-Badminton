import inspect
import json
import re
import time

import cv2
from pathlib import Path

import badminton_analysis.system as sysmod
from badminton_analysis.shuttle_track import tracknet as tnmod
from badminton_analysis.shuttle_track import trajectory as tjmod

ROOT = Path(__file__).resolve().parents[1]


def _system(tmp_path, **kw):
    s = sysmod.BadmintonAnalysisSystem.__new__(sysmod.BadmintonAnalysisSystem)
    s.analyze_technique = True
    s.tracknet_weights = "tn.pt"
    s.inpaintnet_weights = None
    s.tracknet_mode = kw.get("tracknet_mode", "weight")
    s._shuttle_trajectory = None
    s._shuttle_source = "yolo"
    s.save_dir = str(tmp_path)
    s.video_path = str(tmp_path / "v.mp4")
    s.court_roi_corners = [(0, 0), (1000, 1000)]
    s._total_frames = 64085
    (tmp_path / "v.mp4").write_bytes(b"x")     # cache_key may stat the video
    return s


def _fake_frame_count(monkeypatch, n):
    class _Cap:
        def get(self, prop):
            return n

        def release(self):
            pass

    # system.py's cv2 global is normally set by load_runtime_dependencies().
    monkeypatch.setattr(sysmod, "cv2", cv2, raising=False)
    monkeypatch.setattr(cv2, "VideoCapture", lambda p: _Cap())


def test_long_video_is_tracked_not_skipped(tmp_path, monkeypatch):
    s = _system(tmp_path)
    s.hit_model_path = "weights/hit-detector.pt"
    _fake_frame_count(monkeypatch, 64085)
    calls = {}
    monkeypatch.setattr(tnmod, "load_tracknet", lambda *a, **k: {"tracknet_file": "tn.pt"})

    def fake_track(path, models, court_roi=None, eval_mode="weight", progress=None):
        calls["mode"] = eval_mode
        progress(500, 64085)
        return {0: (1.0, 2.0)}

    monkeypatch.setattr(tnmod, "track_video", fake_track)
    s._run_shuttle_pretrack()
    assert s._shuttle_source == "tracknet" and calls["mode"] == "weight"


def test_long_video_without_a_detector_is_skipped(tmp_path, monkeypatch):
    s = _system(tmp_path)
    s.hit_model_path, s.dense_whole_video = None, False
    _fake_frame_count(monkeypatch, 64085)
    called = []
    monkeypatch.setattr(tnmod, "load_tracknet", lambda *a, **k: called.append("load") or {})
    monkeypatch.setattr(tnmod, "track_video", lambda *a, **k: called.append("track") or {0: (1.0, 2.0)})
    s._run_shuttle_pretrack()
    assert not called
    assert s._shuttle_trajectory is None and s._shuttle_source == "yolo"


def test_long_video_with_dense_whole_video_is_tracked(tmp_path, monkeypatch):
    s = _system(tmp_path)
    s.hit_model_path, s.dense_whole_video = None, True
    _fake_frame_count(monkeypatch, 64085)
    monkeypatch.setattr(tnmod, "load_tracknet", lambda *a, **k: {"tracknet_file": "tn.pt"})
    monkeypatch.setattr(tnmod, "track_video", lambda *a, **k: {0: (1.0, 2.0)})
    s._run_shuttle_pretrack()
    assert s._shuttle_source == "tracknet"


def test_mode_reaches_tracker_and_cache_key(tmp_path, monkeypatch):
    s = _system(tmp_path, tracknet_mode="nonoverlap")
    seen = {}
    real_key = tjmod.cache_key

    def spy_key(video, params):
        seen.update(params)
        return real_key(video, params)

    monkeypatch.setattr(tjmod, "cache_key", spy_key)
    monkeypatch.setattr(tnmod, "load_tracknet", lambda *a, **k: {"tracknet_file": "tn.pt"})
    monkeypatch.setattr(tnmod, "track_video",
                        lambda *a, eval_mode="weight", progress=None, **k: (seen.setdefault("tracked", eval_mode), {})[1])
    s._run_shuttle_pretrack()
    assert seen["eval_mode"] == "nonoverlap" and seen["tracked"] == "nonoverlap"


def test_progress_reaches_progress_json(tmp_path, monkeypatch):
    s = _system(tmp_path)
    monkeypatch.setattr(tnmod, "load_tracknet", lambda *a, **k: {"tracknet_file": "tn.pt"})

    def fake_track(path, models, court_roi=None, eval_mode="weight", progress=None):
        progress(1234, 64085)
        return {}

    monkeypatch.setattr(tnmod, "track_video", fake_track)
    s._run_shuttle_pretrack()
    prog = json.loads((tmp_path / "progress.json").read_text())
    assert prog["stage"] == "tracking_shuttle" and prog["current_frame"] == 1234
    assert prog["pct"] == 3


def test_heartbeat_rewrites_while_running():
    writes = []
    with sysmod._ProgressHeartbeat(lambda stage, current_frame=None: writes.append((stage, current_frame)),
                                   "tracking_shuttle", interval=0.01) as hb:
        hb.update(10)
        time.sleep(0.08)
        hb.update(20)
        time.sleep(0.05)
    assert len(writes) >= 4
    assert writes[0] == ("tracking_shuttle", 0) and writes[-1] == ("tracking_shuttle", 20)


def test_normalize_tracknet_mode():
    norm = sysmod.BadmintonAnalysisSystem._normalize_tracknet_mode
    assert norm("nonoverlap") == "nonoverlap" and norm("weight") == "weight"
    assert norm("bogus") == "weight" and norm(None) == "weight"


def test_infer_has_progress_parameter_documented_in_contract():
    with tnmod.vendored_imports():
        from infer import run_prediction
    assert "progress" in inspect.signature(run_prediction).parameters
    assert "progress" in (ROOT / "third_party" / "tracknet" / "CONTRACT.md").read_text(encoding="utf-8")


def test_kestrel_lists_tracking_stage_between_calibration_and_analysis():
    js = (ROOT / "static" / "kestrel.js").read_text(encoding="utf-8")
    assert re.search(r"tracking_shuttle:\s*\['[^']+',\s*'Tracking shuttle'\]", js)
    assert "'calibrating_court_view','tracking_shuttle','analyzing'" in js
