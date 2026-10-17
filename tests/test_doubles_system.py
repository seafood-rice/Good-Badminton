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


def test_doubles_stage_never_raises(tmp_path):
    s = bare(tmp_path, "doubles")
    s._doubles = object()          # broken collector
    s._run_doubles_stage(30.0, 10)  # must not raise


def test_withhold_removes_singles_stroke_outputs(tmp_path):
    for n in ("strokes.json", "strokes.jsonl", "technique_summary.json", "hits.json"):
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
