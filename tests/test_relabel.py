import json

from badminton_analysis.stroke import relabel
from badminton_analysis.stroke import stroke_model as smod
from badminton_analysis.stroke.hit_signals import save_signals
from badminton_analysis.stroke.stroke_windows import INPUT_DIM
from tests.test_stroke_windows import _sig


def _old_result(d):
    d.mkdir(parents=True, exist_ok=True)
    save_signals(d / "hit_signals.npz", _sig())
    (d / "hits.json").write_text(json.dumps({"source": "learned", "fps": 30.0, "shuttle_source": "tracknet",
        "hits": [{"frame": 150, "time_sec": 4.97, "hitter": "lower", "confidence": 0.8}]}), encoding="utf-8")
    (d / "strokes.json").write_text(json.dumps({"strokes": [{"frame": 150, "stroke": "net",
                                                             "uncertain": False}]}), encoding="utf-8")
    measured = {"elbow_extension": 150.0, "trunk_rotation": 30.0, "wrist_flexion": 90.0,
                "knee_flexion": 156.0, "hip_shoulder_separation": 25.0, "weight_transfer": 0.35}
    report = {"stroke_type": "high_clear", "contact_frame": 150, "player_side": "lower",
              "confidence": 0.2, "overall_score": 100.0, "feature_space": "2d",
              "per_metric": {k: {"measured": v} for k, v in measured.items()},
              "weaknesses": [], "strengths": []}
    (d / "strokes.jsonl").write_text(json.dumps(report) + "\n", encoding="utf-8")
    (d / "training_plan.json").write_text("{}", encoding="utf-8")


def test_relabel_converts_old_result(tmp_path):
    d = tmp_path / "V"
    _old_result(d)
    m = smod.StrokeNet(INPUT_DIM, hidden=8, levels=1)
    w = tmp_path / "w.pt"
    smod.save_stroke_model(m, smod.base_meta(8, 1, 0.0), w)
    summary = relabel.relabel(str(d), str(w))
    strokes = json.loads((d / "strokes.json").read_text(encoding="utf-8"))
    assert strokes["schema"] == 2 and strokes["strokes"][0]["stroke"] != "unknown"
    rep = json.loads((d / "strokes.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert rep["stroke_type"] == strokes["strokes"][0]["stroke"]
    assert json.loads((d / "technique_summary.json").read_text(encoding="utf-8"))["stroke_count"] == 1
    assert not (d / "training_plan.json").exists()
    assert summary["labelled"] == 1 and summary["reports"] == 1


def test_relabel_without_weights_marks_unknown(tmp_path):
    d = tmp_path / "V"
    _old_result(d)
    relabel.relabel(str(d), str(tmp_path / "missing.pt"))
    s = json.loads((d / "strokes.json").read_text(encoding="utf-8"))
    assert s["classifier"] is None and s["strokes"][0]["stroke"] == "unknown"
    rep = json.loads((d / "strokes.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert rep["scored"] is False


def test_relabel_skips_doubles(tmp_path):
    d = tmp_path / "V"
    _old_result(d)
    (d / "metadata.json").write_text(json.dumps({"match_format": "doubles"}), encoding="utf-8")
    assert relabel.relabel(str(d), None)["skipped"] == "doubles"


def test_insights_ignore_old_strokes_and_count_groups(tmp_path):
    from badminton_analysis.insights import loaders
    from badminton_analysis.insights.strokes import strokes_insight
    d = tmp_path / "V"
    _old_result(d)
    notes = []
    strokes, _ = loaders._load_strokes(str(d), notes)
    assert strokes is None and any("re-label" in n for n in notes)

    class Run:
        strokes = [{"stroke": "push", "group": "drive", "hitter": "lower"},
                   {"stroke": "drive", "group": "drive", "hitter": "lower"},
                   {"stroke": "unknown", "group": None, "hitter": "upper"}]
    v = strokes_insight(Run())["value"]
    assert v["distribution"] == {"drive": 2} and v["uncertain"] == 1
