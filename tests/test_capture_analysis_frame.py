"""Tests for the match pipeline's per-frame capture (_capture_analysis_frame) and
the capture -> hit list -> strokes.json chain.

Moved from the retired tests/test_bst_integration.py when the BST recognizer was
replaced by the one stroke classifier; the capture tests are unchanged.

Hermetic: BadmintonAnalysisSystem instances are built via object.__new__ with
only the attributes the method under test touches set directly, avoiding the
heavy YOLO/pose-model construction __init__ performs.
"""

import json
import os

import numpy as np

from badminton_analysis import system as system_mod
from badminton_analysis.data.writer import write_json
from badminton_analysis.stroke.events import hit_events
from badminton_analysis.system import BadmintonAnalysisSystem

VALID_COURT_CORNERS = [(100, 100), (900, 100), (900, 700), (100, 700)]


def _bare_system(tmp_path, monkeypatch):
    monkeypatch.setattr(system_mod, "write_json", write_json, raising=False)
    sys_ = object.__new__(BadmintonAnalysisSystem)
    sys_.stroke_model_path = None
    sys_.save_dir = str(tmp_path)
    sys_._shuttle_trajectory = None
    sys_._shuttle_source = "yolo"
    sys_._analysis_track = []
    sys_._analysis_track_both = []
    sys_._analysis_frames = {}
    sys_.court_corners = list(VALID_COURT_CORNERS)
    sys_.court_roi_corners = [(0, 0), (100, 100)]
    sys_.frame_width = 100
    sys_.frame_height = 100
    return sys_


def test_capture_analysis_frame_enriches_record_with_shuttle(tmp_path):
    """_analysis_frames records must carry "shuttle" so build_inputs sees it
    (the window builders read rec.get("shuttle")); previously only
    _analysis_track carried it."""
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

    The gated result is the state _capture_side_poses.  docstring always
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


def test_one_person_in_gate_range_of_both_sides_is_not_shared():
    """The distance gate alone is NOT enough: assignment must be one-to-one.

    Regression for the case the gate cannot catch (Codex review on PR #2).
    When only one person is detected but BOTH tracked centroids are within
    POSE_TO_PLAYER_MAX_PX of them -- e.g. both players near the projected net,
    with one detection missed -- two independent nearest-neighbour searches
    both return that same person. The gate passes, because the person really
    is close to both.

    Consequences that made this worth fixing rather than tolerating: BST's
    person-1 (opponent) slot becomes a near-duplicate of person-0 (hitter),
    which is worse than the honest zero-fill it replaced; and the duplicated
    racket point makes racket_lower == racket_upper, so _dist() ties in
    detect_contacts_multi and its documented tie-break sends EVERY such
    contact to "lower" -- misattributing genuine upper-side hits, i.e.
    defeating the exact bug this capture path was built to fix.
    """
    sys_ = _bare_capture_system()
    # One person at y=300; both centroids within 150px of it (y=260 and y=340),
    # so the distance gate admits the pairing for both sides.
    only_person = _person_with_feet_at(100.0, 300.0)
    sys_.player_pose_visualizer = _FakePoseVisualizerTwoPeople(np.array([only_person]))
    sys_.player_tracker = _FakePlayerTrackerBoth({"lower": (100.0, 340.0), "upper": (100.0, 260.0)})

    sys_._capture_analysis_frame(12, None, [(0, 0), (10, 10)], None)

    players = sys_._analysis_frames[12]["players"]
    got = [s for s in ("lower", "upper") if players[s]["keypoints"] is not None]
    assert len(got) == 1, f"person shared across sides: {got}"
    # "upper" is nearer (|300-260| = 40 vs |300-340| = 40 ... tie broken by the
    # closest-first pass), so exactly one side wins it and the other is honest.
    other = "upper" if got[0] == "lower" else "lower"
    assert players[other]["keypoints"] is None
    assert players[other]["centroid"] is not None, "unmatched side keeps its centroid"
    # The decisive downstream property: the two racket points must not be equal,
    # or detect_contacts_multi ties and always credits "lower".
    both = sys_._analysis_track_both[-1]
    assert not (both["racket_lower"] is not None
                and both["racket_lower"] == both["racket_upper"]), \
        "duplicated racket point would tie detect_contacts_multi"


def test_one_racket_box_in_gate_range_of_both_sides_is_not_shared():
    """Same one-to-one requirement for the racket assignment, which has its own
    (larger) gate and so is easier to trip: both players can sit within
    RACKET_TO_PLAYER_MAX_PX of a single detected racket box."""
    sys_ = _bare_capture_system()
    lower_person = _person_with_feet_at(100.0, 340.0)
    upper_person = _person_with_feet_at(100.0, 260.0)
    sys_.player_pose_visualizer = _FakePoseVisualizerTwoPeople(
        np.array([lower_person, upper_person]))
    sys_.player_tracker = _FakePlayerTrackerBoth({"lower": (100.0, 340.0), "upper": (100.0, 260.0)})
    # A single racket box roughly between them, inside the 300px gate for both.
    sys_._racket_detector = _FakeRacketDetectorBoth([(100.0, 300.0)])

    sys_._capture_analysis_frame(13, None, [(0, 0), (10, 10)], None)

    both = sys_._analysis_track_both[-1]
    # Both sides matched a pose here, so each still gets *a* racket point (the
    # loser falls back to kinematic inference from its own pose) -- but they
    # must not be the same detected box.
    assert both["racket_lower"] != both["racket_upper"]


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


def test_real_capture_to_stroke_list_yields_both_sides_as_hitters(tmp_path, monkeypatch):
    """Findings 2 and 3 regression: the real capture -> hit list -> strokes.json chain.

    Drives the REAL ``_capture_analysis_frame`` (real ``_capture_side_poses``,
    real per-side racket assignment) over a 90-frame synthetic rally with one
    contact by "lower" (frame 30) and one by "upper" (frame 40, deliberately
    inside the default 15-frame min_gap), then the contact-rule hit list
    (``hit_events`` -> ``detect_contacts_multi``) and ``_run_stroke_classification``
    without a model, so every hit is "unknown".
    """
    racket_detector = _FrameKeyedRacketDetector()
    sys_ = _bare_system(tmp_path, monkeypatch)
    sys_.dominant_hand = "right"
    sys_._racket_detector = racket_detector
    sys_.court_corners = list(E2E_COURT_CORNERS)
    sys_.frame_width, sys_.frame_height = E2E_VIDEO_WH

    pose_vis = _MutablePoseVisualizer()
    sys_.player_pose_visualizer = pose_vis
    sys_.player_tracker = _FakePlayerTrackerBoth(
        {"lower": E2E_LOWER_CENTROID, "upper": E2E_UPPER_CENTROID})

    lower_person = _person_with_feet_at(*E2E_LOWER_CENTROID)
    upper_person = _person_with_feet_at(*E2E_UPPER_CENTROID)
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

    sys_._hit_list = [dict(e, confidence=None) for e in hit_events(sys_._analysis_track_both)]
    sys_._hit_signals, sys_._hit_source = None, "contact_rule"
    sys_._run_stroke_classification()

    strokes_path = os.path.join(str(tmp_path), "strokes.json")
    with open(strokes_path, encoding="utf-8") as fh:
        payload = json.load(fh)
    strokes = payload["strokes"]
    # Finding 3: BOTH sides appear as hitters even though their contacts are
    # closer together than min_gap.
    assert [(s["frame"], s["hitter"]) for s in strokes] == [
        (E2E_LOWER_CONTACT, "lower"), (E2E_UPPER_CONTACT, "upper")]
    assert payload["distribution"] == {"unknown": 2}
    assert payload["hit_source"] == "contact_rule"
