import json

import pytest

import app as webapp
import main as main_mod
from badminton_analysis import system as sysmod


def test_cli_default_is_singles():
    args = main_mod.build_parser().parse_args([])
    assert args.match_format == "singles"


def test_cli_accepts_doubles():
    args = main_mod.build_parser().parse_args(["--match-format", "doubles"])
    assert args.match_format == "doubles"


def test_cli_rejects_other_values():
    with pytest.raises(SystemExit):
        main_mod.build_parser().parse_args(["--match-format", "mixed"])


def test_match_formats_constant():
    assert sysmod.MATCH_FORMATS == ("singles", "doubles")


def test_normalize_match_format():
    assert sysmod.BadmintonAnalysisSystem._normalize_match_format("doubles") == "doubles"
    assert sysmod.BadmintonAnalysisSystem._normalize_match_format(None) == "singles"
    with pytest.raises(ValueError):
        sysmod.BadmintonAnalysisSystem._normalize_match_format("triples")


def test_metadata_records_match_format(tmp_path, monkeypatch):
    from badminton_analysis.data.writer import SCHEMA_VERSION, write_json

    # Runtime globals normally bound by load_runtime_dependencies(); wire them
    # directly (as test_court_view_gate does) so this passes when run alone.
    monkeypatch.setattr(sysmod, "write_json", write_json, raising=False)
    monkeypatch.setattr(sysmod, "SCHEMA_VERSION", SCHEMA_VERSION, raising=False)
    s = sysmod.BadmintonAnalysisSystem.__new__(sysmod.BadmintonAnalysisSystem)
    s.video_path, s.video_name = "v.mp4", "v"
    s.frame_width, s.frame_height = 640, 360
    s.ball_model_path = "b.pt"
    s.metadata_path = str(tmp_path / "metadata.json")
    s.output_video_path, s.detections_path = "o.mp4", "d.jsonl"
    s.court_view_calibration = None
    s.court_view_cut = sysmod.COURT_VIEW_FALLBACK_CUT
    s.court_view_threshold_override = None
    s.court_view_pinned = False
    s.match_format = "doubles"
    s._write_metadata(30.0, 10, 0.33, "t.png", [[0, 0], [1, 0], [1, 1], [0, 1]], [[0, 0], [1, 1]], 5)
    assert json.loads((tmp_path / "metadata.json").read_text(encoding="utf-8"))["match_format"] == "doubles"


class _FakeProc:
    returncode = None

    def poll(self):
        return None


@pytest.fixture
def analyze_client(tmp_path, monkeypatch):
    captured = {}

    def _fake_popen(cmd, *args, **kwargs):
        captured["cmd"] = list(cmd)
        return _FakeProc()

    # A private job table: the fake process never finishes, so a job left in the
    # real table keeps "clip" busy and later /api/delete/clip calls get a 409.
    monkeypatch.setattr(webapp, "jobs", {})
    monkeypatch.setattr(webapp, "VIDEOS", tmp_path)
    monkeypatch.setattr(webapp, "OUTPUTS", tmp_path / "out")
    (tmp_path / "out").mkdir()
    monkeypatch.setattr(webapp.subprocess, "Popen", _fake_popen)
    # api_analyze starts a progress-tracking thread after Popen; make it a no-op.
    monkeypatch.setattr(webapp.threading, "Thread", lambda *a, **k: type("T", (), {"start": lambda self: None})())
    (tmp_path / "clip.mp4").write_bytes(b"x")
    webapp.app.config["TESTING"] = True
    return webapp.app.test_client(), captured


def test_api_forwards_match_format(analyze_client):
    client, captured = analyze_client
    r = client.post("/api/analyze", json={"video": "clip.mp4", "match_format": "doubles"})
    assert r.status_code == 200
    i = captured["cmd"].index("--match-format")
    assert captured["cmd"][i + 1] == "doubles"


def test_api_default_is_singles(analyze_client):
    client, captured = analyze_client
    client.post("/api/analyze", json={"video": "clip.mp4"})
    i = captured["cmd"].index("--match-format")
    assert captured["cmd"][i + 1] == "singles"


def test_api_rejects_invalid_match_format(analyze_client):
    client, captured = analyze_client
    r = client.post("/api/analyze", json={"video": "clip.mp4", "match_format": "mixed"})
    assert r.status_code == 400
    assert r.get_json()["error"] == "invalid match_format"
    assert "cmd" not in captured
