"""Tests for Task 6: match-pipeline integration of the BST stroke recognizer.

See badminton_analysis/system.py (BadmintonAnalysisSystem._run_stroke_recognition
and the "shuttle" enrichment in _capture_analysis_frame) and
badminton_analysis/stroke_recog/recognizer.py (StrokeRecognizer, Task 5).

Hermetic: BadmintonAnalysisSystem instances are built via object.__new__ with
only the attributes the method under test touches set directly, avoiding the
heavy YOLO/pose-model construction __init__ performs (same "just enough
state" idea as tests/test_racket.py's fakes, minus needing to run __init__ at
all). StrokeRecognizer.label_rally is monkeypatched directly on the class, so
it doesn't matter that system.py imports it with a lazy, function-local
`from .stroke_recog.recognizer import StrokeRecognizer` -- that import binds
to the same (patched) class object.
"""

import json
import os

from badminton_analysis import system as system_mod
from badminton_analysis.data.writer import write_json
from badminton_analysis.system import BadmintonAnalysisSystem
from badminton_analysis.stroke_recog.recognizer import StrokeRecognizer


def _bare_system(tmp_path, bst_weights, monkeypatch):
    # write_json is normally set as a module global by system.py's
    # load_runtime_dependencies() (called once at app startup); wire it
    # directly here since these tests build a BadmintonAnalysisSystem without
    # going through that heavy startup path.
    monkeypatch.setattr(system_mod, "write_json", write_json, raising=False)
    sys_ = object.__new__(BadmintonAnalysisSystem)
    sys_.bst_weights = bst_weights
    sys_.save_dir = str(tmp_path)
    sys_._analysis_track = []
    sys_._analysis_frames = {}
    sys_.court_roi_corners = [(0, 0), (100, 100)]
    sys_.frame_width = 100
    sys_.frame_height = 100
    return sys_


def test_run_stroke_recognition_writes_strokes_json_and_distribution(tmp_path, monkeypatch):
    canned = [
        {"frame": 10, "hitter": "lower", "stroke": "smash", "confidence": 0.9, "uncertain": False},
        {"frame": 40, "hitter": "upper", "stroke": "smash", "confidence": 0.8, "uncertain": False},
        {"frame": 70, "hitter": "lower", "stroke": "clear", "confidence": 0.6, "uncertain": False},
    ]
    seen = {}

    def _fake_label_rally(self, track, frame_lookup, court_corners, video_wh):
        seen["weights_path"] = self.weights_path
        seen["court_corners"] = court_corners
        seen["video_wh"] = video_wh
        return canned

    monkeypatch.setattr(StrokeRecognizer, "label_rally", _fake_label_rally)

    sys_ = _bare_system(tmp_path, bst_weights="weights/bst.pt", monkeypatch=monkeypatch)
    sys_._run_stroke_recognition()

    strokes_path = os.path.join(str(tmp_path), "strokes.json")
    assert os.path.exists(strokes_path)
    with open(strokes_path, encoding="utf-8") as f:
        payload = json.load(f)

    assert payload["strokes"] == canned
    assert payload["distribution"] == {"smash": 2, "clear": 1}
    assert seen["weights_path"] == "weights/bst.pt"
    assert seen["court_corners"] == [(0, 0), (100, 100)]
    assert seen["video_wh"] == (100, 100)


def test_run_stroke_recognition_no_op_without_bst_weights(tmp_path, monkeypatch):
    called = []
    monkeypatch.setattr(
        StrokeRecognizer, "label_rally",
        lambda self, *a, **kw: called.append(1) or [],
    )

    sys_ = _bare_system(tmp_path, bst_weights=None, monkeypatch=monkeypatch)
    sys_._run_stroke_recognition()

    assert called == []
    assert not os.path.exists(os.path.join(str(tmp_path), "strokes.json"))


def test_capture_analysis_frame_enriches_record_with_shuttle(tmp_path):
    """_analysis_frames records must carry "shuttle" so build_inputs sees it
    (badminton_analysis/stroke_recog/inputs.py's _shuttle_xy reads
    rec.get("shuttle")); previously only _analysis_track carried it."""
    sys_ = object.__new__(BadmintonAnalysisSystem)
    sys_._analysis_track = []
    sys_._analysis_frames = {}
    sys_.dominant_hand = "right"
    sys_._racket_detector = None

    class _FakePoseVisualizer:
        def get_current_pose_data(self):
            return None

    class _FakePlayerTracker:
        players = {}

    sys_.player_pose_visualizer = _FakePoseVisualizer()
    sys_.player_tracker = _FakePlayerTracker()

    sys_._capture_analysis_frame(5, None, [(0, 0), (10, 10)], [42.0, 24.0])

    record = sys_._analysis_frames[5]
    assert "shuttle" in record
    assert record["shuttle"] == (42.0, 24.0)
