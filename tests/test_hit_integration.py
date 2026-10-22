# tests/test_hit_integration.py
import json
import os

import numpy as np

from badminton_analysis import system as system_mod
from badminton_analysis.data.writer import write_json
from badminton_analysis.stroke import hit_detector as hd
from badminton_analysis.stroke import hit_model as hm
from badminton_analysis.system import BadmintonAnalysisSystem, TechniqueAnalysisRunner
from tests.hit_fixtures import CORNERS, pose_at

N = 90


def _system(tmp_path, monkeypatch, **kw):
    monkeypatch.setattr(system_mod, "write_json", write_json, raising=False)
    s = object.__new__(BadmintonAnalysisSystem)
    s.analyze_technique = kw.get("analyze_technique", True)
    s.hit_model_path = kw.get("hit_model_path", "weights/hit-detector.pt")
    s.stroke_model_path = None
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
    assert s._hit_list == learned and s._hit_source == "learned"
    payload = _hits_json(tmp_path)
    assert payload["source"] == "learned" and payload["reason"] is None
    assert payload["model"] == {"sha256": "a" * 64, "schema": 1, "threshold": 0.4, "min_spacing_sec": 0.3}
    assert payload["hits"][0] == {"frame": 31, "time_sec": 1.0, "hitter": "upper",
                                  "confidence": 0.9, "rally_id": 1}
    assert payload["hits"][1]["rally_id"] is None


def _learned_setup(monkeypatch, meta_extra=None):
    meta = {"threshold": 0.4, "min_spacing_sec": 0.3, "schema": 1}
    meta.update(meta_extra or {})
    monkeypatch.setattr(hm, "load_hit_model", lambda *a, **k: (object(), meta))
    monkeypatch.setattr(hm, "file_sha256", lambda p: "a" * 64)
    ran = []
    monkeypatch.setattr(hd, "detect_hits",
                        lambda sig, model, meta: ran.append(1) or [{"frame": 31, "hitter": "upper", "confidence": 0.9}])
    return ran


def test_pose_family_mismatch_falls_back_without_detecting(tmp_path, monkeypatch):
    s = _system(tmp_path, monkeypatch)
    s.pose_family = "yolo-pose"
    ran = _learned_setup(monkeypatch, {"pose_family": "rtmpose"})
    assert s._run_hit_detection(30.0, N) is None
    payload = _hits_json(tmp_path)
    assert payload["source"] == "contact_rule" and not ran
    assert payload["reason"] == "hit model trained on rtmpose keypoints; this run used yolo-pose"
    assert payload["pose_family"] == "yolo-pose"


def test_pose_family_match_stays_learned_and_is_recorded(tmp_path, monkeypatch):
    s = _system(tmp_path, monkeypatch)
    s.pose_family = "rtmpose"
    _learned_setup(monkeypatch, {"pose_family": "rtmpose"})
    assert s._run_hit_detection(30.0, N) is not None
    payload = _hits_json(tmp_path)
    assert payload["source"] == "learned" and payload["pose_family"] == "rtmpose"


def test_unknown_pose_family_skips_the_check(tmp_path, monkeypatch):
    s = _system(tmp_path, monkeypatch)
    s.pose_family = "yolo-pose"
    _learned_setup(monkeypatch)            # an older model without pose_family
    assert s._run_hit_detection(30.0, N) is not None


def test_failed_signals_leave_no_stale_outputs(tmp_path, monkeypatch):
    from badminton_analysis.stroke import hit_signals as hs

    (tmp_path / "hits.json").write_text('{"source": "learned", "reason": null}')
    (tmp_path / "hit_signals.npz").write_bytes(b"old")
    s = _system(tmp_path, monkeypatch)
    monkeypatch.setattr(hs, "build_signals", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    assert s._run_hit_detection(30.0, N) is None
    assert not (tmp_path / "hit_signals.npz").exists()
    assert _hits_json(tmp_path)["reason"] == "hit signals unavailable"


def test_import_failure_is_not_fatal(tmp_path, monkeypatch):
    import builtins

    real_import = builtins.__import__

    def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
        if fromlist and "hit_signals" in fromlist:
            raise ImportError("no hit_signals")
        return real_import(name, globals, locals, fromlist, level)

    s = _system(tmp_path, monkeypatch)
    monkeypatch.setattr(builtins, "__import__", fake_import)
    assert s._run_hit_detection(30.0, N) is None


def test_fast_mode_removes_stale_hit_outputs(tmp_path, monkeypatch):
    (tmp_path / "hits.json").write_text("{}")
    (tmp_path / "hit_signals.npz").write_bytes(b"old")
    s = _system(tmp_path, monkeypatch, analyze_technique=False)
    assert s._run_hit_detection(30.0, N) is None
    assert not (tmp_path / "hits.json").exists()
    assert not (tmp_path / "hit_signals.npz").exists()


def test_technique_runner_uses_injected_hits(monkeypatch):
    import badminton_analysis.stroke.events as events

    monkeypatch.setattr(events, "detect_contacts", lambda *a, **k: (_ for _ in ()).throw(AssertionError("contact rule used")))
    runner = TechniqueAnalysisRunner.__new__(TechniqueAnalysisRunner)
    runner.window_pre, runner.window_post, runner.scale = 20, 15, None
    seen = []
    runner._window_frames = lambda a, b, lookup: []
    runner.analyzer = type("A", (), {"analyze": lambda self, ev, frames: seen.append(ev) or {"ok": 1}})()
    reports, evs = runner.run([], lambda f: {"player_side": "lower"},
                              hits=[{"frame": 50, "hitter": "lower"}, {"frame": 10, "hitter": "lower"}])
    assert [e.contact_frame for e in evs] == [10, 50]
    assert evs[0].window_start == 0 and evs[1].window_end == 65


def test_technique_analysis_gets_only_the_tracked_players_hits(tmp_path, monkeypatch):
    s = _system(tmp_path, monkeypatch)
    s._racket_detector, s.dominant_hand, s.fps = None, "right", 30.0
    captured = {}

    def fake_run(self, track, lookup, hits=None, strokes=None):
        captured["hits"], captured["strokes"] = hits, strokes
        return [], []

    monkeypatch.setattr(TechniqueAnalysisRunner, "run", fake_run)
    s._hit_list = [{"frame": 5, "hitter": "lower"}, {"frame": 9, "hitter": "upper"}]
    label = {"frame": 5, "hitter": "lower", "stroke": "smash", "confidence": 0.8}
    s._run_technique_analysis([label])
    assert captured["hits"] == [{"frame": 5, "hitter": "lower"}]
    assert captured["strokes"] == {5: label}


def test_hit_list_feeds_schema2_strokes_json(tmp_path, monkeypatch):
    s = _system(tmp_path, monkeypatch, hit_model_path=None)
    s.rally_detection = None
    s._analysis_track_both = []
    s._run_hit_detection(30.0, N)
    assert s._hit_source == "contact_rule" and s._hit_list == [] and s._hit_signals is not None
    assert s._run_stroke_classification() == [] and not (tmp_path / "strokes.json").exists()
    s._hit_list, s._hit_source = [{"frame": 3, "hitter": "lower", "confidence": 0.9}], "learned"
    s._run_stroke_classification()
    payload = json.loads((tmp_path / "strokes.json").read_text())
    assert payload["schema"] == 2 and payload["hit_source"] == "learned"
    assert [x["stroke"] for x in payload["strokes"]] == ["unknown"]


def test_contact_rule_listing_failure_is_not_fatal(tmp_path, monkeypatch):
    import badminton_analysis.stroke.events as hits_mod

    s = _system(tmp_path, monkeypatch, hit_model_path=None)
    monkeypatch.setattr(hits_mod, "hit_events", lambda track: (_ for _ in ()).throw(RuntimeError("bad track")))
    assert s._run_hit_detection(30.0, N) is None


def test_malformed_learned_hit_is_not_fatal(tmp_path, monkeypatch):
    s = _system(tmp_path, monkeypatch)
    monkeypatch.setattr(hm, "load_hit_model", lambda *a, **k: (object(), {"threshold": 0.4, "min_spacing_sec": 0.3, "schema": 1}))
    monkeypatch.setattr(hm, "file_sha256", lambda p: "a" * 64)
    monkeypatch.setattr(hd, "detect_hits", lambda sig, model, meta: [{"frame": 31, "confidence": 0.9}])
    assert s._run_hit_detection(30.0, N) is None
