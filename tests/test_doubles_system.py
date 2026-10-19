import json

import numpy as np
import pytest

from badminton_analysis import system as sysmod
from badminton_analysis.court.mapper import CourtMapper

QUAD = [[0, 0], [610, 0], [610, 1340], [0, 1340]]


def bare(tmp_path, fmt):
    s = sysmod.BadmintonAnalysisSystem.__new__(sysmod.BadmintonAnalysisSystem)
    s.match_format = fmt
    s.save_dir = str(tmp_path)
    s.video_path = str(tmp_path / "v.mp4")
    s.video_name = "v"
    s.court_corners = QUAD
    s.fps = 30.0
    s.analyze_technique = False
    s.analysis_quality = "accurate"
    return s


def test_capture_gate():
    f = sysmod.BadmintonAnalysisSystem._capture_frames_for
    assert f(False, "singles", "accurate") is False
    assert f(True, "singles", "accurate") is True
    assert f(False, "doubles", "accurate") is True
    assert f(False, "doubles", "fast") is False


def test_singles_run_removes_stale_doubles_outputs(tmp_path):
    for n in ("players.json", "tracklets.jsonl", "heatmap_P1.png", "detect_v_players.mp4"):
        (tmp_path / n).write_text("x")
    s = bare(tmp_path, "singles")
    s._run_doubles_stage(30.0, 100)
    assert not any(tmp_path.iterdir())


def test_singles_init_creates_no_collector(tmp_path):
    s = bare(tmp_path, "singles")
    s._init_doubles(QUAD, 30.0)
    assert s._doubles is None


def test_doubles_stage_writes_players_json(tmp_path, monkeypatch, capsys):
    s = bare(tmp_path, "doubles")
    s._init_doubles(QUAD, 30.0)
    assert s._doubles is not None
    frame = np.zeros((1400, 700, 3), np.uint8)
    s.player_pose_visualizer = type("V", (), {"get_current_pose_data": lambda self: None})()
    for f in range(1, 61):
        s._observe_doubles(f, frame, [(150, 1000), (450, 1100), (150, 300), (450, 200)])
    s._run_doubles_stage(30.0, 60)          # no source video on disk: the re-render is skipped
    payload = json.loads((tmp_path / "players.json").read_text(encoding="utf-8"))
    assert payload["match_format"] == "doubles"
    assert "Identity video skipped: source video missing" in capsys.readouterr().out


def test_clean_copy_precedes_pose_roi_overlay_and_observe_runs_every_frame():
    import inspect

    src = inspect.getsource(sysmod.BadmintonAnalysisSystem._process_frame)
    # The pose-ROI rectangle is drawn before the fast-mode early return, so the
    # clean (overlay-free) copy must be taken before it.
    assert src.index("self._doubles_clean_frame(") < src.index("if self.show_pose_roi:")
    # observe() drives the tracker and the sampling counter: it must run on every
    # analysed frame, whether or not a clean copy was taken.
    assert "if clean is not None" not in src
    i = src.index("self._observe_doubles(frame_count, clean, centroids)")
    assert "self._doubles_frames += 1" in src[src.rindex("if ", 0, i):i]


def test_doubles_clean_frame_only_when_wanted(tmp_path):
    class C:
        def __init__(self, wants):
            self.wants = wants

        def wants_frame(self):
            return self.wants

    s = bare(tmp_path, "doubles")
    frame = np.arange(48, dtype=np.uint8).reshape(4, 4, 3)
    s._doubles = None
    assert s._doubles_clean_frame(frame, 3) is None
    s._doubles = C(False)
    assert s._doubles_clean_frame(frame, 3) is None
    s._doubles = C(True)
    out = s._doubles_clean_frame(frame, 3)
    assert out is not frame and np.array_equal(out, frame)
    s.analysis_quality = "fast"                          # outside the fast-mode stride
    assert s._doubles_clean_frame(frame, sysmod.FAST_FRAME_STRIDE + 1) is None
    assert s._doubles_clean_frame(frame, sysmod.FAST_FRAME_STRIDE) is not None
    s._doubles = object()                                # broken collector: no copy, no raise
    assert s._doubles_clean_frame(frame, sysmod.FAST_FRAME_STRIDE) is None


def test_doubles_stage_without_collector_writes_error_payload(tmp_path):
    s = bare(tmp_path, "doubles")
    s._doubles = None                  # collector failed to start
    s._run_doubles_stage(30.0, 10)
    payload = json.loads((tmp_path / "players.json").read_text(encoding="utf-8"))
    assert payload["match_format"] == "doubles"
    assert payload["status"] == "error"
    assert payload["reason"] == "doubles collector unavailable"
    assert payload["players"] == []


def test_singles_stage_leaves_hits_json_untouched(tmp_path):
    raw = json.dumps({"source": "contact_rule",
                      "hits": [{"frame": 12, "hitter": "lower", "confidence": None}]},
                     indent=2).encode("utf-8") + b"\n"
    (tmp_path / "hits.json").write_bytes(raw)
    s = bare(tmp_path, "singles")
    s._doubles = None
    s._run_doubles_stage(30.0, 100)
    assert (tmp_path / "hits.json").read_bytes() == raw
    assert not (tmp_path / "players.json").exists()


def test_doubles_stage_never_raises(tmp_path):
    s = bare(tmp_path, "doubles")
    s._doubles = object()          # broken collector
    s._run_doubles_stage(30.0, 10)  # must not raise


def test_withhold_removes_singles_stroke_outputs(tmp_path):
    # training_plan.json is generated from technique_summary.json and served from
    # cache: a previous singles run's plan would show beside "technique withheld".
    for n in ("strokes.json", "strokes.jsonl", "technique_summary.json",
              "training_plan.json", "hits.json"):
        (tmp_path / n).write_text("x")
    s = bare(tmp_path, "doubles")
    s._withhold_singles_outputs_for_doubles()
    assert sorted(p.name for p in tmp_path.iterdir()) == ["hits.json"]


def test_constraint_frames_from_rallies(tmp_path):
    s = bare(tmp_path, "doubles")
    s.rally_list = [{"start_frame": 1, "end_frame": 10}, {"start_frame": 20, "end_frame": 25}]
    s.rally_detection = {"signal": "swing", "degraded": False}
    assert s._doubles_constraint_frames() == set(range(1, 11)) | set(range(20, 26))


@pytest.mark.parametrize("detection", [
    {"signal": "swing", "degraded": True},
    {"signal": "error", "degraded": False},
    {"signal": "none", "degraded": False},
    # A fixed camera yields one whole-video court-view window: no in-play signal.
    {"signal": "courtview", "degraded": False},
    None,
])
def test_constraint_frames_none_when_degraded(tmp_path, detection):
    s = bare(tmp_path, "doubles")
    s.rally_list = [{"start_frame": 1, "end_frame": 10}]
    s.rally_detection = detection
    assert s._doubles_constraint_frames() is None


def test_constraint_frames_none_without_rallies(tmp_path):
    s = bare(tmp_path, "doubles")
    s.rally_list = []
    s.rally_detection = {"signal": "swing", "degraded": False}
    assert s._doubles_constraint_frames() is None


def test_shuttle_dict_skips_missing_detections(tmp_path):
    s = bare(tmp_path, "doubles")
    s._shuttle_trajectory = {1: (10.0, 20.0), 2: None, 3: [0, 0], 4: [5, 6]}
    assert s._doubles_shuttle() == {1: (10.0, 20.0), 4: (5.0, 6.0)}
    s._shuttle_trajectory = None
    assert s._doubles_shuttle() is None


def test_stage_passes_constraint_frames_and_shuttle(tmp_path):
    s = bare(tmp_path, "doubles")
    s.rally_list = [{"start_frame": 1, "end_frame": 3}]
    s.rally_detection = {"signal": "swing", "degraded": False}
    s._shuttle_trajectory = {1: (1.0, 2.0), 2: None}
    seen = {}

    class Fake:
        identity_tracks = {}

        def finalize(self, save_dir, analysed_frames, *, shuttle=None, constraint_frames=None):
            seen.update(shuttle=shuttle, constraint_frames=constraint_frames)
            return {"match_format": "doubles", "players": []}

    s._doubles = Fake()
    s._run_doubles_stage(30.0, 5)
    assert seen == {"shuttle": {1: (1.0, 2.0)}, "constraint_frames": {1, 2, 3}}


def test_identity_video_outcome_lines(tmp_path, monkeypatch, capsys):
    from badminton_analysis.visualization import doubles_render as dr

    s = bare(tmp_path, "doubles")
    (tmp_path / "v.mp4").write_text("x")

    class Fake:
        identity_tracks = {}

        def finalize(self, save_dir, analysed_frames, *, shuttle=None, constraint_frames=None):
            return {"match_format": "doubles", "players": []}

    s._doubles = Fake()
    monkeypatch.setattr(dr, "write_heatmaps", lambda *a, **k: [])
    monkeypatch.setattr(dr, "render_identity_video", lambda *a, **k: True)
    s._run_doubles_stage(30.0, 5)
    assert "Identity video: " in capsys.readouterr().out
    monkeypatch.setattr(dr, "render_identity_video", lambda *a, **k: False)
    s._run_doubles_stage(30.0, 5)
    assert "Identity video skipped: render failed" in capsys.readouterr().out


def _pretrack_probe(tmp_path, monkeypatch, capture):
    """Run _run_shuttle_pretrack on a bare instance; return whether it got past the gate."""
    s = bare(tmp_path, "doubles")
    s.tracknet_weights = "w.pt"
    s.inpaintnet_weights = None
    s._capture_frames = capture
    s._shuttle_trajectory = None
    calls = []

    class FakeCV2:
        CAP_PROP_FRAME_COUNT = 7

        @staticmethod
        def VideoCapture(path):
            calls.append(path)
            raise RuntimeError("probe")      # caught by the pre-pass's own guard

    monkeypatch.setattr(sysmod, "cv2", FakeCV2, raising=False)
    s._run_shuttle_pretrack()
    return bool(calls)


def test_tracknet_pretrack_runs_for_doubles_capture(tmp_path, monkeypatch):
    assert _pretrack_probe(tmp_path, monkeypatch, True) is True
    assert _pretrack_probe(tmp_path, monkeypatch, False) is False


def test_shuttle_falls_back_to_yolo_points_without_tracknet(tmp_path):
    s = bare(tmp_path, "doubles")
    s._shuttle_trajectory = None
    s._analysis_track_both = [{"frame": 1, "shuttle": (10.0, 20.0)},
                              {"frame": 2, "shuttle": None},
                              {"frame": 3, "shuttle": [0, 0]},
                              {"frame": 4, "shuttle": [5, 6]}]
    assert s._doubles_shuttle() == {1: (10.0, 20.0), 4: (5.0, 6.0)}
    s._analysis_track_both = [{"frame": 1, "shuttle": None}]
    assert s._doubles_shuttle() is None
    # TrackNet ran: its track wins, YOLO points are not mixed in.
    s._shuttle_trajectory = {7: (1.0, 1.0)}
    s._analysis_track_both = [{"frame": 8, "shuttle": (2.0, 2.0)}]
    assert s._doubles_shuttle() == {7: (1.0, 1.0)}


def test_clean_frame_and_observe_failures_each_log_once(tmp_path, capsys):
    class Broken:
        def wants_frame(self):
            raise RuntimeError("wants")

        def observe(self, *a):
            raise RuntimeError("observe")

    class Pose:
        def get_current_pose_data(self):
            return None

    s = bare(tmp_path, "doubles")
    s._doubles = Broken()
    s.player_pose_visualizer = Pose()
    frame = np.zeros((4, 4, 3), np.uint8)
    for f in (1, 2):
        s._doubles_clean_frame(frame, f)
        s._observe_doubles(f, None, [])
    out = capsys.readouterr().out
    # A clean-frame failure must not silence the (different) observe failure.
    assert out.count("RuntimeError('wants')") == 1
    assert out.count("RuntimeError('observe')") == 1


def test_doubles_stage_reports_progress_substages(tmp_path, monkeypatch):
    from badminton_analysis.visualization import doubles_render as dr

    s = bare(tmp_path, "doubles")
    (tmp_path / "v.mp4").write_text("x")
    seen = []
    s._write_progress = lambda stage, current_frame=None, substage=None: seen.append(
        (stage, substage, current_frame))

    class Fake:
        identity_tracks = {}

        def finalize(self, *a, **k):
            seen.append("finalize")
            return {"match_format": "doubles", "players": []}

    def render(*a, progress=None, **k):
        progress(5)
        return True

    s._doubles = Fake()
    monkeypatch.setattr(dr, "write_heatmaps", lambda *a, **k: seen.append("heatmaps") or [])
    monkeypatch.setattr(dr, "render_identity_video", render)
    s._run_doubles_stage(30.0, 5)
    assert seen.index(("visualizing", "doubles_identity", None)) < seen.index("finalize")
    assert seen.index(("visualizing", "doubles_heatmaps", None)) < seen.index("heatmaps")
    assert ("visualizing", "doubles_video", 5) in seen
    singles = bare(tmp_path, "singles")
    singles._write_progress = lambda *a, **k: seen.append("singles")
    singles._run_doubles_stage(30.0, 5)
    assert "singles" not in seen


def _identity_video_record(tmp_path, monkeypatch, *, source=True, rendered=True):
    tmp_path.mkdir()
    from badminton_analysis.data.writer import write_json
    from badminton_analysis.visualization import doubles_render as dr

    s = bare(tmp_path, "doubles")
    if source:
        (tmp_path / "v.mp4").write_text("x")

    class Fake:
        identity_tracks = {}

        def finalize(self, save_dir, *a, **k):
            payload = {"match_format": "doubles", "status": "ok", "players": []}
            write_json(str(tmp_path / "players.json"), payload)
            return payload

    s._doubles = Fake()
    monkeypatch.setattr(dr, "write_heatmaps", lambda *a, **k: [])
    monkeypatch.setattr(dr, "render_identity_video", lambda *a, **k: rendered)
    s._run_doubles_stage(30.0, 5)
    saved = json.loads((tmp_path / "players.json").read_text(encoding="utf-8"))
    assert saved["status"] == "ok"                     # the rest of the payload is kept
    return saved["identity_video"]


def test_identity_video_outcome_recorded_in_players_json(tmp_path, monkeypatch):
    assert _identity_video_record(tmp_path / "a", monkeypatch) == {
        "status": "ok", "file": "detect_v_players.mp4", "reason": None}
    assert _identity_video_record(tmp_path / "b", monkeypatch, rendered=False) == {
        "status": "failed", "file": None, "reason": "render failed"}
    assert _identity_video_record(tmp_path / "c", monkeypatch, source=False) == {
        "status": "skipped", "file": None, "reason": "source video missing"}
