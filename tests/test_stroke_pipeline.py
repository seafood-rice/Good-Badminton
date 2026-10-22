# tests/test_stroke_pipeline.py
import json

from badminton_analysis.stroke import stroke_model as smod
from badminton_analysis.stroke.stroke_windows import INPUT_DIM
from tests.test_stroke_windows import _sig


def _bare(tmp_path, model_path=None, detection=None):
    from badminton_analysis.system import BadmintonAnalysisSystem
    s = BadmintonAnalysisSystem.__new__(BadmintonAnalysisSystem)
    s.save_dir = str(tmp_path)
    s.stroke_model_path = model_path
    s.rally_detection = detection or {}
    s._shuttle_source = "tracknet"
    s._hit_signals = _sig()
    s._hit_source = "learned"
    s._hit_list = [{"frame": 150, "hitter": "lower", "confidence": 0.9}]
    return s


def _read(tmp_path):
    return json.loads((tmp_path / "strokes.json").read_text(encoding="utf-8"))


def test_no_model_writes_unknown_with_null_classifier(tmp_path):
    labels = _bare(tmp_path)._run_stroke_classification()
    p = _read(tmp_path)
    assert p["schema"] == 2 and p["classifier"] is None
    assert [l["stroke"] for l in labels] == ["unknown"] == [s["stroke"] for s in p["strokes"]]
    assert p["reason"] == "no stroke model"


def test_model_labels_hits(tmp_path):
    m = smod.StrokeNet(INPUT_DIM, hidden=8, levels=1)
    path = tmp_path / "w.pt"
    smod.save_stroke_model(m, smod.base_meta(8, 1, 0.0), path)
    _bare(tmp_path, str(path))._run_stroke_classification()
    p = _read(tmp_path)
    assert p["classifier"]["weights_sha256"] and p["strokes"][0]["stroke"] != "unknown"
    assert "reason" not in p


def test_incompatible_model_is_unknown_not_fatal(tmp_path):
    bad = tmp_path / "w.pt"
    bad.write_bytes(b"not a model")
    _bare(tmp_path, str(bad))._run_stroke_classification()
    p = _read(tmp_path)
    assert p["classifier"] is None and p["reason"].startswith("classifier failed: ")


def _weights(tmp_path):
    path = tmp_path / "w.pt"
    smod.save_stroke_model(smod.StrokeNet(INPUT_DIM, hidden=8, levels=1), smod.base_meta(8, 1, 0.0), path)
    return str(path)


def test_classify_failure_after_load_is_unknown_with_reason(tmp_path, monkeypatch):
    from badminton_analysis.stroke import stroke_classifier as sc

    def boom(*_a, **_k):
        raise RuntimeError("cuda gone")
    monkeypatch.setattr(sc, "classify_hits", boom)
    labels = _bare(tmp_path, _weights(tmp_path))._run_stroke_classification()
    p = _read(tmp_path)
    assert p["classifier"] is None and p["reason"] == "classifier failed: RuntimeError"
    assert [l["stroke"] for l in labels] == ["unknown"] == [s["stroke"] for s in p["strokes"]]


def test_model_without_hit_signals_says_why(tmp_path, capsys):
    s = _bare(tmp_path, _weights(tmp_path))
    s._hit_signals = None
    s._run_stroke_classification()
    p = _read(tmp_path)
    assert p["classifier"] is None and p["reason"] == "hit signals unavailable"
    assert p["strokes"][0]["stroke"] == "unknown"
    assert "signals are unavailable" in capsys.readouterr().out


def test_withheld_removes_stale_file(tmp_path):
    (tmp_path / "strokes.json").write_text("{}", encoding="utf-8")
    labels = _bare(tmp_path, detection={"stroke_labels_withheld": True})._run_stroke_classification()
    assert labels == [] and not (tmp_path / "strokes.json").exists()


def test_no_hits_writes_nothing(tmp_path):
    s = _bare(tmp_path)
    s._hit_list = None
    assert s._run_stroke_classification() == [] and not (tmp_path / "strokes.json").exists()


def test_stroke_recog_package_is_gone():
    import importlib.util
    assert importlib.util.find_spec("badminton_analysis.stroke_recog") is None
    assert importlib.util.find_spec("badminton_analysis.stroke.classifier") is None


def test_cli_and_app_pass_the_stroke_model():
    import inspect

    import app as webapp
    import main as main_mod
    assert main_mod.build_parser().parse_args(["--stroke-model", "w.pt"]).stroke_model == "w.pt"
    assert "stroke_model_path=args.stroke_model" in inspect.getsource(main_mod.main)
    assert "cmd += ['--stroke-model', stroke_weights]" in inspect.getsource(webapp)
