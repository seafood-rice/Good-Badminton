# tests/test_hit_integration.py
import json
import os

import numpy as np

from badminton_analysis import system as system_mod
from badminton_analysis.data.writer import write_json
from badminton_analysis.stroke import hit_detector as hd
from badminton_analysis.stroke import hit_model as hm
from badminton_analysis.stroke_recog import recognizer as recognizer_mod
from badminton_analysis.stroke_recog.recognizer import StrokeRecognizer
from badminton_analysis.system import BadmintonAnalysisSystem, TechniqueAnalysisRunner
from tests.hit_fixtures import CORNERS, pose_at

N = 90


def _system(tmp_path, monkeypatch, **kw):
    monkeypatch.setattr(system_mod, "write_json", write_json, raising=False)
    s = object.__new__(BadmintonAnalysisSystem)
    s.analyze_technique = kw.get("analyze_technique", True)
    s.hit_model_path = kw.get("hit_model_path", "weights/hit-detector.pt")
    s.bst_weights = None
    s.save_dir = str(tmp_path)
    s.court_corners = CORNERS.tolist()
    s.frame_width, s.frame_height = 1280, 720
    s._shuttle_source = kw.get("shuttle_source", "tracknet")
    traj = {f: (600.0 + f, 300.0) for f in range(1, N + 1)}
    if kw.get("sparse"):
        traj = {f: (traj[f] if f % 4 == 0 else None) for f in traj}
    s._shuttle_trajectory = traj if s._shuttle_source == "tracknet" else None
    s._analysis_track_both = [{"frame": f, "racket_lower": None, "racket_upper": None,
                               "shuttle": (600.0 + f, 300.0)} for f in range(1, N + 1)]
    s._analysis_track = []
    s._analysis_frames = {
        f: {"player_side": "lower",
            "players": {"lower": {"keypoints": pose_at((640, 560), 60.0)},
                        "upper": {"keypoints": pose_at((640, 250), 25.0)}}}
        for f in range(1, N + 1)}
    s.rally_list = [{"id": 1, "start_frame": 1, "end_frame": 60}]
    if "corners" in kw:
        s.court_corners = kw["corners"]
    return s


def _hits_json(tmp_path):
    return json.loads((tmp_path / "hits.json").read_text())


def test_no_model_falls_back_and_still_writes_signals(tmp_path, monkeypatch):
    s = _system(tmp_path, monkeypatch, hit_model_path=None)
    assert s._run_hit_detection(30.0, N) is None
    payload = _hits_json(tmp_path)
    assert payload["source"] == "contact_rule" and payload["reason"] == "no hit model"
    assert (tmp_path / "hit_signals.npz").is_file()


def test_yolo_shuttle_falls_back(tmp_path, monkeypatch):
    s = _system(tmp_path, monkeypatch, shuttle_source="yolo")
    assert s._run_hit_detection(30.0, N) is None
    assert _hits_json(tmp_path)["reason"] == "no dense shuttle track"


def test_sparse_dense_track_falls_back_with_coverage(tmp_path, monkeypatch):
    s = _system(tmp_path, monkeypatch, sparse=True)
    assert s._run_hit_detection(30.0, N) is None
    payload = _hits_json(tmp_path)
    assert payload["source"] == "contact_rule" and "covers 25%" in payload["reason"]
    assert payload["shuttle_coverage_in_play"] == 0.25


def test_model_failure_falls_back(tmp_path, monkeypatch):
    s = _system(tmp_path, monkeypatch)
    monkeypatch.setattr(hm, "load_hit_model", lambda *a, **k: (_ for _ in ()).throw(hm.HitModelError("bad")))
    assert s._run_hit_detection(30.0, N) is None
    assert _hits_json(tmp_path)["reason"] == "hit detector failed: HitModelError"


def test_degenerate_corners_fall_back_not_crash(tmp_path, monkeypatch):
    s = _system(tmp_path, monkeypatch, corners=[[0, 0], [10, 0], [20, 0], [30, 0]])
    monkeypatch.setattr(hm, "load_hit_model", lambda *a, **k: (object(), {"threshold": 0.5, "min_spacing_sec": 0.3, "schema": 1}))
    monkeypatch.setattr(hm, "file_sha256", lambda p: "0" * 64)
    assert s._run_hit_detection(30.0, N) is None
    assert _hits_json(tmp_path)["reason"].startswith("hit detector failed: ValueError")


def test_learned_hits_written_with_provenance_time_and_rally(tmp_path, monkeypatch):
    s = _system(tmp_path, monkeypatch)
    monkeypatch.setattr(hm, "load_hit_model", lambda *a, **k: (object(), {"threshold": 0.4, "min_spacing_sec": 0.3, "schema": 1}))
    monkeypatch.setattr(hm, "file_sha256", lambda p: "a" * 64)
    learned = [{"frame": 31, "hitter": "upper", "confidence": 0.9},
               {"frame": 80, "hitter": "lower", "confidence": 0.7}]
    monkeypatch.setattr(hd, "detect_hits", lambda sig, model, meta: learned)
    assert s._run_hit_detection(30.0, N) == learned
    payload = _hits_json(tmp_path)
    assert payload["source"] == "learned" and payload["reason"] is None
    assert payload["model"] == {"sha256": "a" * 64, "schema": 1, "threshold": 0.4, "min_spacing_sec": 0.3}
    assert payload["hits"][0] == {"frame": 31, "time_sec": 1.0, "hitter": "upper",
                                  "confidence": 0.9, "rally_id": 1}
    assert payload["hits"][1]["rally_id"] is None


def test_fast_mode_removes_stale_hit_outputs(tmp_path, monkeypatch):
    (tmp_path / "hits.json").write_text("{}")
    (tmp_path / "hit_signals.npz").write_bytes(b"old")
    s = _system(tmp_path, monkeypatch, analyze_technique=False)
    assert s._run_hit_detection(30.0, N) is None
    assert not (tmp_path / "hits.json").exists()
    assert not (tmp_path / "hit_signals.npz").exists()


def test_recognizer_uses_injected_hits(monkeypatch):
    rec = StrokeRecognizer("w.pt")
    rec._load_attempted, rec._model = True, object()
    monkeypatch.setattr(recognizer_mod, "hit_events", lambda track: (_ for _ in ()).throw(AssertionError("contact rule used")))
    monkeypatch.setattr(recognizer_mod, "build_inputs", lambda *a, **k: None)
    out = rec.label_rally([], lambda f: None, CORNERS.tolist(), (1280, 720),
                          hits=[{"frame": 40, "hitter": "upper", "confidence": 0.8}])
    assert out == [{"frame": 40, "hitter": "upper", "stroke": "uncertain", "confidence": 0.0,
                    "uncertain": True, "hit_confidence": 0.8}]


def test_technique_runner_uses_injected_hits(monkeypatch):
    import badminton_analysis.stroke.events as events

    monkeypatch.setattr(events, "detect_contacts", lambda *a, **k: (_ for _ in ()).throw(AssertionError("contact rule used")))
    runner = TechniqueAnalysisRunner.__new__(TechniqueAnalysisRunner)
    runner.window_pre, runner.window_post, runner.scale = 20, 15, None
    seen = []
    runner._build_classifier_window = lambda c, a, b, lookup: {"contact_index": 0}
    runner._window_frames = lambda a, b, lookup: []
    monkeypatch.setattr("badminton_analysis.stroke.classifier.classify_stroke", lambda w: ("smash", 0.9))
    runner.analyzer = type("A", (), {"analyze": lambda self, ev, frames: seen.append(ev) or {"ok": 1}})()
    reports, evs = runner.run([], lambda f: {"player_side": "lower"},
                              hits=[{"frame": 50, "hitter": "lower"}, {"frame": 10, "hitter": "lower"}])
    assert [e.contact_frame for e in evs] == [10, 50]
    assert evs[0].window_start == 0 and evs[1].window_end == 65


def test_technique_analysis_gets_only_the_tracked_players_hits(tmp_path, monkeypatch):
    s = _system(tmp_path, monkeypatch)
    s._racket_detector, s.dominant_hand, s.fps = None, "right", 30.0
    captured = {}

    def fake_run(self, track, lookup, hits=None):
        captured["hits"] = hits
        return [], []

    monkeypatch.setattr(TechniqueAnalysisRunner, "run", fake_run)
    s._run_technique_analysis([{"frame": 5, "hitter": "lower"}, {"frame": 9, "hitter": "upper"}])
    assert captured["hits"] == [{"frame": 5, "hitter": "lower"}]


def test_contact_rule_strokes_json_unchanged(tmp_path, monkeypatch):
    s = _system(tmp_path, monkeypatch)
    s.bst_weights = "bst.pt"
    s.rally_detection = None
    s._shuttle_source = "yolo"
    monkeypatch.setattr(StrokeRecognizer, "label_rally",
                        lambda self, *a, hits=None, **k: [{"frame": 3, "hitter": "lower", "stroke": "net",
                                                           "confidence": 0.9, "uncertain": False}])
    s._run_stroke_recognition(None)
    payload = json.loads((tmp_path / "strokes.json").read_text())
    assert set(payload) == {"strokes", "distribution"}
    s._run_stroke_recognition([{"frame": 3, "hitter": "lower", "confidence": 0.9}])
    assert json.loads((tmp_path / "strokes.json").read_text())["hit_source"] == "learned"
