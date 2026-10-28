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


def test_relabel_without_weights_refuses_and_leaves_result(tmp_path, capsys):
    d = tmp_path / "V"
    _old_result(d)
    before = {f: (d / f).read_bytes() for f in ("strokes.json", "strokes.jsonl", "training_plan.json")}
    summary = relabel.relabel(str(d), str(tmp_path / "missing.pt"))
    assert summary["skipped"] == "weights not found"
    assert {f: (d / f).read_bytes() for f in before} == before
    assert "--allow-unknown" in capsys.readouterr().out
    assert relabel.main(["--weights", str(tmp_path / "missing.pt"), str(d)]) == 0
    assert {f: (d / f).read_bytes() for f in before} == before


def test_relabel_cli_allow_unknown_writes_unknown(tmp_path):
    d = tmp_path / "V"
    _old_result(d)
    relabel.main(["--weights", str(tmp_path / "missing.pt"), "--allow-unknown", str(d)])
    assert json.loads((d / "strokes.json").read_text(encoding="utf-8"))["strokes"][0]["stroke"] == "unknown"


def test_relabel_without_weights_marks_unknown(tmp_path):
    d = tmp_path / "V"
    _old_result(d)
    relabel.relabel(str(d), str(tmp_path / "missing.pt"), allow_unknown=True)
    s = json.loads((d / "strokes.json").read_text(encoding="utf-8"))
    assert s["classifier"] is None and s["strokes"][0]["stroke"] == "unknown"
    assert s["reason"] == "no stroke model"
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


def _weights(tmp_path):
    m = smod.StrokeNet(INPUT_DIM, hidden=8, levels=1)
    w = tmp_path / "w.pt"
    smod.save_stroke_model(m, smod.base_meta(8, 1, 0.0), w)
    return str(w)


def _reports(d):
    return [json.loads(x) for x in (d / "strokes.jsonl").read_text(encoding="utf-8").splitlines()]


def test_relabel_skips_withheld(tmp_path):
    for name, det in (("A", {"stroke_labels_withheld": True}), ("B", {"degraded": True})):
        d = tmp_path / name
        _old_result(d)
        (d / "rally_segments.json").write_text(json.dumps({"detection": det}), encoding="utf-8")
        before = [(d / f).read_bytes() for f in ("strokes.json", "strokes.jsonl")]
        assert relabel.relabel(str(d), _weights(tmp_path))["skipped"] == "labels withheld"
        assert [(d / f).read_bytes() for f in ("strokes.json", "strokes.jsonl")] == before


def test_relabel_idempotent_and_keeps_measured(tmp_path, monkeypatch):
    d = tmp_path / "V"
    _old_result(d)
    orig = _reports(d)[0]["per_metric"]
    metrics = {k: v["measured"] for k, v in orig.items()}
    relabel.relabel(str(d), str(tmp_path / "missing.pt"), allow_unknown=True)
    first = _reports(d)[0]
    assert first["scored"] is False and first["measured"] == metrics
    w = _weights(tmp_path)
    # random-init weights may pick an unprofiled type; pin a profiled one
    monkeypatch.setattr(relabel.sc, "classify_hits", lambda sig, hits, model, meta: [
        {"frame": h["frame"], "hitter": h["hitter"], "stroke": "clear", "group": "clear",
         "confidence": 0.9} for h in hits])
    relabel.relabel(str(d), w)
    second = _reports(d)[0]
    assert second["scored"] is True and second["measured"] == metrics
    second_bytes = (d / "strokes.jsonl").read_bytes()
    relabel.relabel(str(d), w)
    third = _reports(d)[0]
    assert third["measured"] == metrics
    assert (d / "strokes.jsonl").read_bytes() == second_bytes


def test_relabel_report_without_matching_hit(tmp_path):
    d = tmp_path / "V"
    _old_result(d)
    rep = _reports(d)[0]
    rep["contact_frame"] = 999
    (d / "strokes.jsonl").write_text(json.dumps(rep) + "\n", encoding="utf-8")
    relabel.relabel(str(d), _weights(tmp_path))
    out = _reports(d)[0]
    assert out["stroke_type"] == "unknown" and out["scored"] is False


def test_relabel_corrupt_report_line_is_skipped(tmp_path, capsys):
    d = tmp_path / "V"
    _old_result(d)
    with open(d / "strokes.jsonl", "a", encoding="utf-8") as fh:
        fh.write("{not json\n[1]\n")
    summary = relabel.relabel(str(d), _weights(tmp_path))
    assert summary["reports"] == 1 and len(_reports(d)) == 1
    assert "skipped 2 unusable line(s)" in capsys.readouterr().out
    assert json.loads((d / "strokes.json").read_text(encoding="utf-8"))["schema"] == 2


def test_relabel_skips_hit_without_frame_or_hitter(tmp_path):
    d = tmp_path / "V"
    _old_result(d)
    doc = json.loads((d / "hits.json").read_text(encoding="utf-8"))
    doc["hits"] += [{"hitter": "upper"}, {"frame": 160}]
    (d / "hits.json").write_text(json.dumps(doc), encoding="utf-8")
    assert relabel.relabel(str(d), _weights(tmp_path))["labelled"] == 1


def test_relabel_doubles_still_applies_owner_labels(tmp_path, monkeypatch):
    d = tmp_path / "V"
    _old_result(d)
    (d / "metadata.json").write_text(json.dumps({"match_format": "doubles"}), encoding="utf-8")
    calls = []
    monkeypatch.setattr(relabel.owner_labels, "apply_to_result",
                        lambda rd, ld, stem: calls.append((rd, ld, stem)) or {"applied": True})
    out = relabel.relabel(str(d), None, labels_dir=str(tmp_path / "L"))
    assert out["skipped"] == "doubles" and out["applied"] is True
    assert calls == [(str(d), str(tmp_path / "L"), "V")]


def test_default_labels_dir_is_the_project_data_dir():
    import os
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    assert os.path.normpath(relabel.DEFAULT_LABELS_DIR) == os.path.normpath(
        os.path.join(root, "data", "stroke_labels"))


def test_rescore_reports_helper_rescores_with_applied_types(tmp_path):
    d = tmp_path / "V"
    _old_result(d)
    (d / "strokes.json").write_text(json.dumps({"schema": 2, "strokes": [
        {"frame": 150, "hitter": "lower", "stroke": "smash", "confidence": 0.9}]}), encoding="utf-8")
    assert relabel.rescore_reports(str(d)) == 1
    rep = json.loads((d / "strokes.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert rep["stroke_type"] == "smash"
    assert json.loads((d / "technique_summary.json").read_text(encoding="utf-8"))["stroke_count"] == 1
    assert relabel.rescore_reports(str(tmp_path / "none")) == 0


def _files(d):
    return {p.name: p.read_bytes() for p in sorted(d.iterdir()) if p.is_file()}


def test_a_failed_write_leaves_the_result_files_unchanged(tmp_path, monkeypatch):
    import pytest
    from badminton_analysis.analysis import technique_writer
    d = tmp_path / "V"
    _old_result(d)
    (d / "technique_summary.json").write_text('{"old": 1}', encoding="utf-8")
    m = smod.StrokeNet(INPUT_DIM, hidden=8, levels=1)
    w = tmp_path / "w.pt"
    smod.save_stroke_model(m, smod.base_meta(8, 1, 0.0), w)
    before = _files(d)

    def partial_dump(doc, fh, **kw):
        fh.write('{"partial')
        raise OSError("disk full")

    monkeypatch.setattr(json, "dump", partial_dump)      # strokes.json and technique_summary.json
    with pytest.raises(OSError):
        relabel.relabel(str(d), str(w))
    assert _files(d) == before                            # no half file, no temp file left

    monkeypatch.undo()
    real = technique_writer._clean_value
    calls = []

    def second_report_fails(value):
        calls.append(1)
        if len(calls) > 1:
            raise OSError("disk full")
        return real(value)

    monkeypatch.setattr(technique_writer, "_clean_value", second_report_fails)
    reports = [{"a": 1}, {"b": 2}]
    with pytest.raises(OSError):
        technique_writer.write_stroke_reports(str(d / "strokes.jsonl"), reports)
    assert _files(d) == before
