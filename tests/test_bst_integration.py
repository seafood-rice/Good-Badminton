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
    sys_._analysis_track_both = []
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
    """Both-player synthetic track + frames: exactly one detected hit (by
    "lower") via the real detect_contacts_multi -> hit_events chain, with
    >=10 posed frames in that hit's build_inputs window, so the real
    (unstubbed) recognition path runs end to end.
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
        racket_head = shuttle if f == contact_frame else None
        track.append({
            "frame": f, "racket_lower": racket_head, "racket_upper": None, "shuttle": shuttle,
        })

        keypoints = np.full((17, 2), 50.0, dtype=float)
        frames[f] = {
            "frame": f, "keypoints": keypoints, "conf": None,
            "racket_head": racket_head, "centroid": (50.0, 50.0),
            "nose": (50.0, 50.0), "shoulder": (50.0, 50.0), "hip": (50.0, 50.0),
            "elbow_angle": 170.0, "player_side": "lower", "shuttle": shuttle,
            "players": {
                "lower": {"keypoints": keypoints, "centroid": (50.0, 50.0), "racket_head": racket_head},
                "upper": {"keypoints": None, "centroid": None, "racket_head": None},
            },
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
    sys_._analysis_track_both = []
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
    sys_._analysis_track_both = track
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
    sys_._analysis_track_both = track
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


def _person_with_feet_at(x, y):
    kp = np.full((17, 2), 50.0, dtype=float)
    kp[15] = (x, y)  # L_ANKLE
    kp[16] = (x, y)  # R_ANKLE
    return kp


class _FakePoseVisualizerTwoPeople:
    def __init__(self, people, offset_x=0, offset_y=0):
        self._people = people
        self._offset_x = offset_x
        self._offset_y = offset_y

    def get_current_pose_data(self):
        return {"keypoints": self._people, "offset_x": self._offset_x, "offset_y": self._offset_y}


class _FakePlayerTrackerBoth:
    def __init__(self, players):
        self.players = players


class _FakeRacketDetectorBoth:
    def __init__(self, heads):
        self._heads = list(heads)

    def detect_racket_heads(self, frame, roi_corners=None):
        return list(self._heads)

    def detect_racket_head(self, frame, roi_corners=None):
        return self._heads[0] if self._heads else None


def test_capture_analysis_frame_captures_both_players_additively():
    sys_ = object.__new__(BadmintonAnalysisSystem)
    sys_._shuttle_trajectory = None
    sys_._shuttle_source = "yolo"
    sys_._analysis_track = []
    sys_._analysis_track_both = []
    sys_._analysis_frames = {}
    sys_.dominant_hand = "right"
    sys_._racket_detector = _FakeRacketDetectorBoth([(105.0, 105.0), (105.0, 505.0)])

    lower_person = _person_with_feet_at(100.0, 100.0)
    upper_person = _person_with_feet_at(100.0, 500.0)
    sys_.player_pose_visualizer = _FakePoseVisualizerTwoPeople(np.array([lower_person, upper_person]))
    sys_.player_tracker = _FakePlayerTrackerBoth({"lower": (100.0, 100.0), "upper": (100.0, 500.0)})

    sys_._capture_analysis_frame(7, None, [(0, 0), (10, 10)], [42.0, 24.0])

    rec = sys_._analysis_frames[7]
    assert set(rec["players"]) == {"lower", "upper"}
    assert rec["players"]["lower"]["centroid"] == (100.0, 100.0)
    assert rec["players"]["upper"]["centroid"] == (100.0, 500.0)
    np.testing.assert_allclose(rec["players"]["lower"]["keypoints"][15], (100.0, 100.0))
    np.testing.assert_allclose(rec["players"]["upper"]["keypoints"][15], (100.0, 500.0))
    assert rec["players"]["lower"]["racket_head"] == (105.0, 105.0)
    assert rec["players"]["upper"]["racket_head"] == (105.0, 505.0)

    # Non-regression: pre-existing single-player fields unchanged in meaning
    # ("prefer lower, else upper" tracked player).
    assert rec["player_side"] == "lower"
    assert rec["centroid"] == (100.0, 100.0)
    np.testing.assert_allclose(rec["keypoints"][15], (100.0, 100.0))
    assert rec["racket_head"] == (105.0, 105.0)

    both = sys_._analysis_track_both[-1]
    assert both == {"frame": 7, "racket_lower": (105.0, 105.0), "racket_upper": (105.0, 505.0), "shuttle": (42.0, 24.0)}

    # _analysis_track (TechniqueAnalysisRunner's input) keeps its exact
    # pre-B1 shape -- no new keys leak in.
    assert set(sys_._analysis_track[-1]) == {"frame", "racket_head", "shuttle"}


def test_capture_analysis_frame_racket_assignment_falls_back_to_kinematic_inference():
    """No racket detector -> each side's racket_head falls back to
    infer_racket_head from that side's OWN pose, same fallback the
    single-player path already had, now applied per side."""
    sys_ = object.__new__(BadmintonAnalysisSystem)
    sys_._shuttle_trajectory = None
    sys_._shuttle_source = "yolo"
    sys_._analysis_track = []
    sys_._analysis_track_both = []
    sys_._analysis_frames = {}
    sys_.dominant_hand = "right"
    sys_._racket_detector = None

    from badminton_analysis.analysis import joint_angles as ja

    def _person_with_arm(foot_x, foot_y):
        kp = _person_with_feet_at(foot_x, foot_y)
        kp[ja.R_ELBOW] = (foot_x, foot_y - 100)
        kp[ja.R_WRIST] = (foot_x + 40, foot_y - 100)
        return kp

    lower_person = _person_with_arm(100.0, 100.0)
    upper_person = _person_with_arm(100.0, 500.0)
    sys_.player_pose_visualizer = _FakePoseVisualizerTwoPeople(np.array([lower_person, upper_person]))
    sys_.player_tracker = _FakePlayerTrackerBoth({"lower": (100.0, 100.0), "upper": (100.0, 500.0)})

    sys_._capture_analysis_frame(9, None, [(0, 0), (10, 10)], None)

    rec = sys_._analysis_frames[9]
    assert rec["players"]["lower"]["racket_head"] is not None
    assert rec["players"]["upper"]["racket_head"] is not None
    assert rec["players"]["lower"]["racket_head"] != rec["players"]["upper"]["racket_head"]


def _bare_capture_system(racket_detector=None):
    """Minimal system instance for exercising _capture_analysis_frame alone."""
    sys_ = object.__new__(BadmintonAnalysisSystem)
    sys_._shuttle_trajectory = None
    sys_._shuttle_source = "yolo"
    sys_._analysis_track = []
    sys_._analysis_track_both = []
    sys_._analysis_frames = {}
    sys_.dominant_hand = "right"
    sys_._racket_detector = racket_detector
    return sys_


def test_capture_side_pose_gates_a_far_nearest_person_instead_of_sharing_it():
    """One detected person must never be assigned to BOTH sides.

    The pose model can miss a small/far player while the cheaper player
    tracker still holds a centroid for that half. min() over a one-element
    list always returns that element regardless of distance, so without the
    POSE_TO_PLAYER_MAX_PX gate the single lower-court person's keypoints were
    handed to "upper" as well -- fabricating a phantom opponent that is a
    near-duplicate of the hitter in BST's person-1 slot, and (because its
    kinematically inferred racket point sits on the hitter's own body) able to
    flip hitter attribution in detect_contacts_multi's nearest-wins compare.

    The gated result is the state _capture_side_pose's docstring always
    promised: (None, that side's own centroid).
    """
    sys_ = _bare_capture_system()
    only_person = _person_with_feet_at(100.0, 100.0)  # squarely the lower player
    sys_.player_pose_visualizer = _FakePoseVisualizerTwoPeople(np.array([only_person]))
    sys_.player_tracker = _FakePlayerTrackerBoth({"lower": (100.0, 100.0), "upper": (100.0, 500.0)})

    sys_._capture_analysis_frame(11, None, [(0, 0), (10, 10)], None)

    rec = sys_._analysis_frames[11]
    # The matching side still gets the real person.
    np.testing.assert_allclose(rec["players"]["lower"]["keypoints"][15], (100.0, 100.0))
    assert rec["players"]["lower"]["centroid"] == (100.0, 100.0)
    # The non-matching side is honestly empty, not a copy of the lower player.
    assert rec["players"]["upper"]["keypoints"] is None
    assert rec["players"]["upper"]["centroid"] == (100.0, 500.0)
    # ...and therefore has no kinematically inferred racket point either, so it
    # cannot win detect_contacts_multi's proximity compare against the hitter.
    assert rec["players"]["upper"]["racket_head"] is None
    assert sys_._analysis_track_both[-1]["racket_upper"] is None


def test_capture_side_pose_still_matches_a_moderately_stale_centroid():
    """Positive control that the gate isn't over-tight: the tracked centroid
    is itself a foot midpoint (visualization/player_pose.py::detect_players),
    so a centroid held stale from a slightly earlier frame must still match
    its own player rather than being rejected."""
    sys_ = _bare_capture_system()
    lower_person = _person_with_feet_at(100.0, 100.0)
    upper_person = _person_with_feet_at(100.0, 500.0)
    sys_.player_pose_visualizer = _FakePoseVisualizerTwoPeople(np.array([lower_person, upper_person]))
    # Both centroids lag their player by 100px (< POSE_TO_PLAYER_MAX_PX).
    sys_.player_tracker = _FakePlayerTrackerBoth({"lower": (100.0, 200.0), "upper": (100.0, 400.0)})

    sys_._capture_analysis_frame(13, None, [(0, 0), (10, 10)], None)

    rec = sys_._analysis_frames[13]
    np.testing.assert_allclose(rec["players"]["lower"]["keypoints"][15], (100.0, 100.0))
    np.testing.assert_allclose(rec["players"]["upper"]["keypoints"][15], (100.0, 500.0))


# --- End-to-end: real capture -> real recognition (finding 5) ---------------

E2E_VIDEO_WH = (1000, 1000)
E2E_COURT_CORNERS = [(100, 100), (900, 100), (900, 700), (100, 700)]
E2E_TOTAL_FRAMES = 90
E2E_LOWER_CENTROID = (500.0, 600.0)
E2E_UPPER_CENTROID = (500.0, 200.0)
E2E_LOWER_CONTACT = 30
E2E_UPPER_CONTACT = 40  # only 10 frames later: inside the default min_gap of 15
# Frames where the pose model sees only the lower player (the finding-2
# mechanism), chosen to sit outside both hits' SEQ_LEN build_inputs windows
# (15..44 and 25..54) so it cannot perturb the recognition result.
E2E_LOWER_ONLY_FRAMES = range(60, 71)


def _e2e_shuttle_at(f):
    """Shuttle path with a 180-degree reversal at each contact frame."""
    if f <= E2E_LOWER_CONTACT:
        return (500.0, 600.0 - (E2E_LOWER_CONTACT - f) * 10.0)
    if f <= E2E_UPPER_CONTACT:
        return (500.0, 600.0 - (f - E2E_LOWER_CONTACT) * 40.0)
    return (500.0, 200.0 + (f - E2E_UPPER_CONTACT) * 10.0)


def _e2e_racket_candidates(f):
    """Each side's racket box for frame f, coincident with the shuttle only on
    that side's own contact frame and >= 100px away (i.e. outside contact_px)
    on every other frame."""
    lower = (500.0, 600.0) if f == E2E_LOWER_CONTACT else (400.0, 600.0)
    upper = (500.0, 200.0) if f == E2E_UPPER_CONTACT else (400.0, 200.0)
    return [lower, upper]


class _FrameKeyedRacketDetector:
    """Returns the candidates configured for ``self.frame_no``.

    Also asserts finding 1: ``detect_racket_head`` must never be called, and
    ``calls`` lets the test pin exactly ONE racket forward pass per frame
    (the pre-fix code ran the YOLO racket model twice on every frame).
    """

    def __init__(self):
        self.frame_no = None
        self.calls = 0

    def detect_racket_heads(self, frame, roi_corners=None):
        self.calls += 1
        return list(_e2e_racket_candidates(self.frame_no))

    def detect_racket_head(self, frame, roi_corners=None):
        raise AssertionError(
            "detect_racket_head must not be called: the capture path derives "
            "the single-player racket_head from detect_racket_heads' first "
            "(highest-confidence) box, so the model runs once per frame")


class _MutablePoseVisualizer:
    def __init__(self):
        self.people = None

    def get_current_pose_data(self):
        return {"keypoints": self.people, "offset_x": 0, "offset_y": 0}


def test_real_capture_to_recognition_yields_both_sides_as_hitters(tmp_path, monkeypatch):
    """Findings 2, 3 and 1 regression: the real capture-to-recognition chain.

    Drives the REAL ``_capture_analysis_frame`` (real ``_capture_side_pose``,
    real per-side racket assignment) over a 90-frame synthetic rally with one
    contact by "lower" (frame 30) and one by "upper" (frame 40, deliberately
    inside the default 15-frame min_gap), then runs the REAL
    ``_run_stroke_recognition`` -> ``label_rally`` -> ``hit_events`` ->
    ``detect_contacts_multi`` -> ``build_inputs`` chain with only the torch
    model (``load_bst``/``predict``) stubbed.

    Pre-fix this failed twice over: the globally shared ``min_gap`` dropped the
    frame-40 reply entirely (so "upper" never appeared in strokes.json), and
    the ungated ``_capture_side_pose`` assigned the single detected person to
    both sides on the lower-only frames.
    """
    canned_logits = np.zeros(25, dtype=np.float32)
    canned_logits[3] = 10.0  # "Top_殺球" -> coarse "smash" (stroke_recog/classes.py)
    monkeypatch.setattr(bst_model_mod, "load_bst", lambda weights_path: _DummyBstModel())
    monkeypatch.setattr(bst_model_mod, "predict", lambda model, pose, shuttle, positions: canned_logits)

    racket_detector = _FrameKeyedRacketDetector()
    sys_ = _bare_system(tmp_path, bst_weights="weights/bst.pt", monkeypatch=monkeypatch)
    sys_.dominant_hand = "right"
    sys_._racket_detector = racket_detector
    sys_.court_corners = list(E2E_COURT_CORNERS)
    sys_.frame_width, sys_.frame_height = E2E_VIDEO_WH

    pose_vis = _MutablePoseVisualizer()
    sys_.player_pose_visualizer = pose_vis
    # Both halves stay tracked for the whole clip, including the frames where
    # the pose model only finds the lower player.
    sys_.player_tracker = _FakePlayerTrackerBoth(
        {"lower": E2E_LOWER_CENTROID, "upper": E2E_UPPER_CENTROID})

    lower_person = _person_with_feet_at(*E2E_LOWER_CENTROID)
    upper_person = _person_with_feet_at(*E2E_UPPER_CENTROID)
    # A real BGR frame: _capture_analysis_frame draws the technique overlay on
    # it via cv2 whenever a pose was captured.
    blank_frame = np.zeros((E2E_VIDEO_WH[1], E2E_VIDEO_WH[0], 3), dtype=np.uint8)

    for f in range(1, E2E_TOTAL_FRAMES + 1):
        racket_detector.frame_no = f
        if f in E2E_LOWER_ONLY_FRAMES:
            pose_vis.people = np.array([lower_person])
        else:
            pose_vis.people = np.array([lower_person, upper_person])
        shuttle = _e2e_shuttle_at(f)
        sys_._capture_analysis_frame(f, blank_frame, [(0, 0), (999, 999)], list(shuttle))

    # Finding 1: exactly one racket forward pass per captured frame.
    assert racket_detector.calls == E2E_TOTAL_FRAMES

    # Finding 2: on lower-only frames "upper" is honestly empty rather than a
    # duplicate of the lower player.
    for f in E2E_LOWER_ONLY_FRAMES:
        players = sys_._analysis_frames[f]["players"]
        assert players["upper"]["keypoints"] is None, f"frame {f}"
        assert players["upper"]["centroid"] == E2E_UPPER_CENTROID
        assert players["lower"]["keypoints"] is not None, f"frame {f}"

    sys_._run_stroke_recognition()

    strokes_path = os.path.join(str(tmp_path), "strokes.json")
    assert os.path.exists(strokes_path)
    with open(strokes_path, encoding="utf-8") as fh:
        payload = json.load(fh)

    strokes = payload["strokes"]
    # Finding 3: BOTH sides appear as hitters even though their contacts are
    # closer together than min_gap.
    assert [(s["frame"], s["hitter"]) for s in strokes] == [
        (E2E_LOWER_CONTACT, "lower"), (E2E_UPPER_CONTACT, "upper")]
    assert {s["hitter"] for s in strokes} == {"lower", "upper"}
    # Both hits had enough pose signal for BST, so neither degraded to
    # "uncertain" -- the real build_inputs/CourtMapper path ran for both, with
    # person-0/person-1 filled from opposite sides.
    for stroke in strokes:
        assert stroke["stroke"] == "smash", stroke
        assert stroke["uncertain"] is False, stroke
    assert payload["distribution"] == {"smash": 2}
