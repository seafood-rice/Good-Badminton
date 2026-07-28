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

import numpy as np

from badminton_analysis import system as system_mod
from badminton_analysis.data.writer import write_json
from badminton_analysis.system import BadmintonAnalysisSystem
from badminton_analysis.stroke_recog import bst_model as bst_model_mod
from badminton_analysis.stroke_recog import recognizer as recognizer_mod
from badminton_analysis.stroke_recog.recognizer import StrokeRecognizer

# A real 4-point court quad (order doesn't matter for these tests, only that
# CourtMapper's cv2.getPerspectiveTransform gets exactly 4 points). Finding 1:
# the match pipeline must pass *this* -- self.court_corners -- into
# label_rally, never the 2-point self.court_roi_corners (pose-detection ROI
# rectangle) which cv2.getPerspectiveTransform rejects.
VALID_COURT_CORNERS = [(100, 100), (900, 100), (900, 700), (100, 700)]


def _bare_system(tmp_path, bst_weights, monkeypatch):
    # write_json is normally set as a module global by system.py's
    # load_runtime_dependencies() (called once at app startup); wire it
    # directly here since these tests build a BadmintonAnalysisSystem without
    # going through that heavy startup path.
    monkeypatch.setattr(system_mod, "write_json", write_json, raising=False)
    sys_ = object.__new__(BadmintonAnalysisSystem)
    sys_.bst_weights = bst_weights
    sys_.save_dir = str(tmp_path)
    sys_._shuttle_trajectory = None
    sys_._shuttle_source = "yolo"
    sys_._analysis_track = []
    sys_._analysis_frames = {}
    sys_.court_corners = list(VALID_COURT_CORNERS)
    # ROI stays 2-point on purpose: it's a pose-detection rectangle, unrelated
    # to CourtMapper's 4-point perspective transform. Kept here so a
    # regression that reads the wrong attribute is caught rather than masked
    # by its absence.
    sys_.court_roi_corners = [(0, 0), (100, 100)]
    sys_.frame_width = 100
    sys_.frame_height = 100
    return sys_


def _build_synthetic_track_and_frames(contact_frame=30, total_frames=60):
    """Synthesize a minimal ``_analysis_track`` + ``_analysis_frames`` pair
    that (a) yields exactly one detected hit via the real
    ``stroke.events.detect_contacts_multi`` / ``stroke_recog.hits.hit_events``
    both-player contact track, and (b) has >=10 posed frames (both hips
    valid) in that hit's ``build_inputs`` window, so the real (unstubbed)
    recognition path runs end to end.

    Shuttle path is a "V": diagonally descending up to ``contact_frame``,
    then diagonally ascending afterwards, so ``detect_contacts_multi``'s
    direction-change check (>=45 degrees) fires exactly at ``contact_frame``.
    ``racket_lower`` is only populated at ``contact_frame`` (``racket_upper``
    is never populated) so no other frame is even a contact candidate and the
    hit is attributed to "lower".
    """
    step = 10.0
    track = []
    frames = {}
    for f in range(1, total_frames + 1):
        if f <= contact_frame:
            x, y = step * f, step * f
        else:
            offset = f - contact_frame
            x = step * contact_frame + step * offset
            y = step * contact_frame - step * offset
        shuttle = (x, y)
        racket_lower = shuttle if f == contact_frame else None
        racket_upper = None
        track.append({
            "frame": f, "racket_lower": racket_lower, "racket_upper": racket_upper,
            "shuttle": shuttle,
        })

        keypoints = np.full((17, 2), 50.0, dtype=float)
        players = {
            "lower": {"keypoints": keypoints, "centroid": (50.0, 50.0)},
            "upper": {"keypoints": keypoints, "centroid": (50.0, 50.0)},
        }
        frames[f] = {
            "frame": f, "keypoints": keypoints, "conf": None,
            "racket_lower": racket_lower, "racket_upper": racket_upper,
            "centroid": (50.0, 50.0),
            "nose": (50.0, 50.0), "shoulder": (50.0, 50.0), "hip": (50.0, 50.0),
            "elbow_angle": 170.0, "player_side": "lower", "shuttle": shuttle,
            "players": players,
        }
    return track, frames


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
    assert seen["court_corners"] == VALID_COURT_CORNERS
    assert seen["video_wh"] == (100, 100)


def test_run_stroke_recognition_shuttle_source_key_conditional(tmp_path, monkeypatch):
    """Fix 2: strokes.json must omit "shuttle_source" entirely when the
    dense TrackNetV3 pre-pass did not run (self._shuttle_source == "yolo"),
    preserving byte-identical output for the no-TrackNet-weights case, and
    must include "shuttle_source": "tracknet" only when it did run."""
    canned = [
        {"frame": 10, "hitter": "lower", "stroke": "smash", "confidence": 0.9, "uncertain": False},
    ]
    monkeypatch.setattr(StrokeRecognizer, "label_rally", lambda self, *a, **kw: canned)

    sys_ = _bare_system(tmp_path, bst_weights="weights/bst.pt", monkeypatch=monkeypatch)
    sys_._shuttle_source = "yolo"
    sys_._run_stroke_recognition()

    strokes_path = os.path.join(str(tmp_path), "strokes.json")
    with open(strokes_path, encoding="utf-8") as f:
        payload = json.load(f)
    assert "shuttle_source" not in payload

    os.remove(strokes_path)
    sys_._shuttle_source = "tracknet"
    sys_._run_stroke_recognition()
    with open(strokes_path, encoding="utf-8") as f:
        payload = json.load(f)
    assert payload["shuttle_source"] == "tracknet"


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
    sys_._shuttle_trajectory = None
    sys_._shuttle_source = "yolo"
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


class _DummyBstModel:
    """Stands in for the real torch model object bst_model.load_bst returns."""


def test_run_stroke_recognition_real_pipeline_uses_four_point_court_corners(tmp_path, monkeypatch):
    """Finding 1 regression (red before the fix, green after).

    Exercises the *real* label_rally -> hit_events -> build_inputs ->
    CourtMapper path end to end -- only ``bst_model.load_bst``/``predict``
    (the actual torch model) are stubbed. Before the fix, system.py passed
    ``self.court_roi_corners`` (a 2-point ROI rectangle) into label_rally;
    ``build_inputs`` -> ``CourtMapper.__init__`` -> ``cv2.getPerspectiveTransform``
    requires exactly 4 points and raises ``cv2.error`` for that 2-point
    input, so this test fails red pre-fix and passes green post-fix.
    """
    canned_logits = np.zeros(25, dtype=np.float32)
    canned_logits[3] = 10.0  # "Top_殺球" -> coarse "smash" (see stroke_recog/classes.py)

    monkeypatch.setattr(bst_model_mod, "load_bst", lambda weights_path: _DummyBstModel())
    monkeypatch.setattr(bst_model_mod, "predict", lambda model, pose, shuttle, positions: canned_logits)

    track, frames = _build_synthetic_track_and_frames()
    sys_ = _bare_system(tmp_path, bst_weights="weights/bst.pt", monkeypatch=monkeypatch)
    sys_._analysis_track = track
    sys_._analysis_frames = frames
    sys_.frame_width = 1000
    sys_.frame_height = 1000

    sys_._run_stroke_recognition()

    strokes_path = os.path.join(str(tmp_path), "strokes.json")
    assert os.path.exists(strokes_path)
    with open(strokes_path, encoding="utf-8") as f:
        payload = json.load(f)

    assert len(payload["strokes"]) == 1
    stroke = payload["strokes"][0]
    assert stroke["frame"] == 30
    assert stroke["hitter"] == "lower"
    assert stroke["stroke"] == "smash"
    assert payload["distribution"] == {"smash": 1}


def test_run_stroke_recognition_swallows_recognition_exceptions(tmp_path, monkeypatch):
    """Finding 2: a raise anywhere in the recognition path (hit_events,
    build_inputs, predict, CourtMapper) must never be fatal to the match
    run -- _run_stroke_recognition must swallow it and return quietly so
    process_video can still reach _cleanup(cap)."""
    monkeypatch.setattr(bst_model_mod, "load_bst", lambda weights_path: _DummyBstModel())

    def _boom(*args, **kwargs):
        raise RuntimeError("synthetic build_inputs failure")

    monkeypatch.setattr(recognizer_mod, "build_inputs", _boom)

    track, frames = _build_synthetic_track_and_frames()
    sys_ = _bare_system(tmp_path, bst_weights="weights/bst.pt", monkeypatch=monkeypatch)
    sys_._analysis_track = track
    sys_._analysis_frames = frames
    sys_.frame_width = 1000
    sys_.frame_height = 1000

    sys_._run_stroke_recognition()  # must not raise

    assert not os.path.exists(os.path.join(str(tmp_path), "strokes.json"))


def test_run_stroke_recognition_writes_nothing_when_no_hits(tmp_path, monkeypatch):
    """Finding 4: zero detected hits -> no strokes.json written at all
    (rather than an empty {"strokes": [], "distribution": {}} file), so the
    file's presence stays a meaningful signal that BST ran and found hits."""
    monkeypatch.setattr(bst_model_mod, "load_bst", lambda weights_path: _DummyBstModel())
    monkeypatch.setattr(
        bst_model_mod, "predict",
        lambda *a, **kw: (_ for _ in ()).throw(AssertionError("predict should not run with zero hits")),
    )

    sys_ = _bare_system(tmp_path, bst_weights="weights/bst.pt", monkeypatch=monkeypatch)
    # _analysis_track/_analysis_frames already [] / {} from _bare_system -> no contacts.

    sys_._run_stroke_recognition()

    assert not os.path.exists(os.path.join(str(tmp_path), "strokes.json"))
