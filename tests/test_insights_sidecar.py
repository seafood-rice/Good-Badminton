"""Task 5: the per-video sidecar (spec 5.2)."""
import json
import os

import pytest

from badminton_analysis.insights import sidecar
from tests.insights_fixtures import make_run

INSIGHT_KEYS = ("rally", "strokes", "technique", "movement", "recovery", "pull")
REPORTS = [("smash", "upper", 60.0), ("high_clear", "upper", 80.0), ("serve", "lower", 70.0)]


def _full_run(tmp_path, stem="DJI_20261010090046_0010_D"):
    return make_run(tmp_path, stem, frames=120, rallies=[(0.5, 3.0)],
                    hits=[(1.0, "upper"), (1.5, "lower")],
                    strokes=[("upper", "smash"), ("lower", "net")], reports=REPORTS)


def _count_calls(monkeypatch, name):
    calls = []
    real = getattr(sidecar, name)

    def wrapper(*a, **kw):
        calls.append(1)
        return real(*a, **kw)

    monkeypatch.setattr(sidecar, name, wrapper)
    return calls


def _assert_envelope(env):
    assert set(env) == {"status", "value", "coverage", "reason"}
    assert env["status"] in ("ok", "partial", "unavailable")
    if env["status"] == "unavailable":
        assert env["value"] is None and env["reason"]["code"]


def test_version_constant():
    assert sidecar.INSIGHTS_VERSION == 3


def test_document_shape_and_file_written(tmp_path):
    out = _full_run(tmp_path)
    doc = sidecar.compute_video(out.name, out)
    assert doc["schema_version"] == 1 and doc["version"] == sidecar.INSIGHTS_VERSION == 3
    assert doc["stem"] == out.name
    assert doc["computed_at"].endswith("Z")
    assert doc["date"] == {"value": "2026-10-10", "source": "filename"}
    assert doc["fps"] == 30.0 and doc["duration_sec"] == pytest.approx(4.0)
    for key in INSIGHT_KEYS:
        _assert_envelope(doc[key])
    assert set(doc["quality"]) == {"shuttle_coverage_in_play", "hit_source",
                                   "shuttle_source", "court_view_pass_frac"}
    assert doc["quality"]["shuttle_coverage_in_play"] == 0.9
    assert doc["quality"]["shuttle_source"] == "tracknet"
    assert doc["inputs"]["detections.jsonl"] is not None
    assert doc["inputs"]["hits.json"] is not None
    assert doc["inputs"]["technique_summary.json"] is None
    assert doc["technique"]["status"] == "partial"
    on_disk = json.loads((out / "insights.json").read_text(encoding="utf-8"))
    assert on_disk == doc


def test_second_call_does_not_recompute(tmp_path, monkeypatch):
    out = _full_run(tmp_path)
    calls = _count_calls(monkeypatch, "technique_insight")
    first = sidecar.compute_video(out.name, out)
    second = sidecar.compute_video(out.name, out)
    assert len(calls) == 1
    assert second == first


def test_touching_an_input_recomputes(tmp_path, monkeypatch):
    out = _full_run(tmp_path)
    calls = _count_calls(monkeypatch, "technique_insight")
    sidecar.compute_video(out.name, out)
    target = out / "strokes.jsonl"
    stat = target.stat()
    os.utime(target, ns=(stat.st_atime_ns, stat.st_mtime_ns + 5_000_000_000))
    sidecar.compute_video(out.name, out)
    assert len(calls) == 2


def test_new_or_removed_input_recomputes(tmp_path, monkeypatch):
    out = make_run(tmp_path, "clip", strokes=[("upper", "smash")])
    calls = _count_calls(monkeypatch, "technique_insight")
    sidecar.compute_video("clip", out)
    (out / "strokes.jsonl").write_text(
        json.dumps({"stroke_type": "smash", "player_side": "upper", "overall_score": 50.0})
        + "\n", encoding="utf-8")
    doc = sidecar.compute_video("clip", out)
    assert len(calls) == 2
    assert doc["technique"]["value"]["by_side"]["upper"]["techniques"]["smash"]["count"] == 1


def test_version_change_recomputes(tmp_path, monkeypatch):
    out = _full_run(tmp_path)
    calls = _count_calls(monkeypatch, "technique_insight")
    sidecar.compute_video(out.name, out)
    monkeypatch.setattr(sidecar, "INSIGHTS_VERSION", 4)
    doc = sidecar.compute_video(out.name, out)
    assert len(calls) == 2 and doc["version"] == 4


@pytest.mark.parametrize("content", ["{not json", "[1, 2]", "null", '{"version": 1}', ""])
def test_malformed_existing_sidecar_is_replaced(tmp_path, content):
    out = _full_run(tmp_path)
    (out / "insights.json").write_text(content, encoding="utf-8")
    doc = sidecar.compute_video(out.name, out)
    _assert_envelope(doc["technique"])
    assert json.loads((out / "insights.json").read_text(encoding="utf-8")) == doc


def test_unwritable_target_returns_result_without_raising(tmp_path):
    out = _full_run(tmp_path)
    (out / "insights.json").mkdir()          # replacing a directory fails with OSError
    doc = sidecar.compute_video(out.name, out)
    assert doc["technique"]["status"] == "partial"
    assert not [p for p in os.listdir(out) if "tmp" in p]       # temp file cleaned up


def test_os_replace_failure_is_ignored(tmp_path, monkeypatch):
    out = _full_run(tmp_path)

    def boom(*a, **kw):
        raise PermissionError("read-only")

    monkeypatch.setattr(sidecar.os, "replace", boom)
    doc = sidecar.compute_video(out.name, out)
    assert doc["stem"] == out.name
    assert not (out / "insights.json").exists()
    assert not [p for p in os.listdir(out) if "tmp" in p]


def test_missing_directory_never_raises_and_is_not_created(tmp_path):
    out = tmp_path / "outputs" / "ghost"
    doc = sidecar.compute_video("ghost", out)
    assert not out.exists()
    for key in INSIGHT_KEYS:
        assert doc[key]["status"] == "unavailable"


def test_one_failing_insight_is_unavailable_no_data_others_intact(tmp_path, monkeypatch):
    out = _full_run(tmp_path)

    def boom(run):
        raise RuntimeError("bug")

    monkeypatch.setattr(sidecar, "rally_insight", boom)
    doc = sidecar.compute_video(out.name, out)
    assert doc["rally"] == {"status": "unavailable", "value": None, "coverage": None,
                            "reason": {"code": "no_data", "params": {}}}
    assert doc["technique"]["status"] == "partial"
    assert doc["strokes"]["status"] == "ok"
    assert doc["movement"]["value"] is not None      # still computed without the rally


def test_loader_failure_never_raises(tmp_path, monkeypatch):
    out = _full_run(tmp_path)

    def boom(path):
        raise RuntimeError("bug")

    monkeypatch.setattr(sidecar, "load_run", boom)
    doc = sidecar.compute_video(out.name, out)
    for key in INSIGHT_KEYS:
        assert doc[key]["reason"]["code"] == "no_data"


def test_only_detections_yields_valid_shape(tmp_path):
    out = make_run(tmp_path, "bare", frames=60)
    for name in ("hits.json", "strokes.json", "strokes.jsonl", "rally_segments.json"):
        assert not (out / name).exists()
    doc = sidecar.compute_video("bare", out)
    for key in INSIGHT_KEYS:
        _assert_envelope(doc[key])
    assert doc["rally"]["reason"]["code"] == "no_rallies"
    assert doc["strokes"]["reason"]["code"] == "no_strokes_file"
    assert doc["technique"]["reason"]["code"] == "no_technique"
    assert doc["movement"]["status"] in ("ok", "partial")
    assert doc["quality"] == {"shuttle_coverage_in_play": None, "hit_source": None,
                              "shuttle_source": None, "court_view_pass_frac": None}


def test_date_falls_back_to_mtime_when_stem_has_none(tmp_path):
    out = make_run(tmp_path, "no_date_here")
    doc = sidecar.compute_video("no_date_here", out)
    assert doc["date"]["source"] == "mtime"
    assert doc["date"]["value"]                 # the run directory's mtime date


def test_court_view_quality_is_copied(tmp_path):
    out = make_run(tmp_path, "cv", rallies=[(0.0, 2.0)])
    payload = json.loads((out / "rally_segments.json").read_text(encoding="utf-8"))
    payload["detection"]["court_view"] = {"pass_frac": 0.97}
    (out / "rally_segments.json").write_text(json.dumps(payload), encoding="utf-8")
    doc = sidecar.compute_video("cv", out)
    assert doc["quality"]["court_view_pass_frac"] == 0.97


def test_version_1_cache_is_rebuilt_with_per_hitter_distribution(tmp_path):
    out = _full_run(tmp_path)
    old = sidecar.compute_video(out.name, out)
    old["version"] = 1
    for side in old["strokes"]["value"]["by_hitter"].values():
        side.pop("distribution")
    (out / "insights.json").write_text(json.dumps(old), encoding="utf-8")
    doc = sidecar.compute_video(out.name, out)
    assert doc["version"] == sidecar.INSIGHTS_VERSION
    assert doc["strokes"]["value"]["by_hitter"]["upper"]["distribution"] == {"smash": 1}


@pytest.mark.parametrize("junk", [5, ["x"], {"k": 1}, True])
def test_non_string_sources_become_none(tmp_path, junk):
    out = _full_run(tmp_path)
    payload = json.loads((out / "hits.json").read_text(encoding="utf-8"))
    payload["source"] = junk
    payload["shuttle_source"] = junk
    (out / "hits.json").write_text(json.dumps(payload), encoding="utf-8")
    doc = sidecar.compute_video(out.name, out)
    assert doc["quality"]["hit_source"] == "learned"      # falls back to strokes.json's string
    assert doc["quality"]["shuttle_source"] == "tracknet"
    strokes = json.loads((out / "strokes.json").read_text(encoding="utf-8"))
    strokes["hit_source"] = junk
    strokes["shuttle_source"] = junk
    (out / "strokes.json").write_text(json.dumps(strokes), encoding="utf-8")
    doc = sidecar.compute_video(out.name, out)
    assert doc["quality"]["hit_source"] is None and doc["quality"]["shuttle_source"] is None
    json.dumps(doc, allow_nan=False)


def test_concurrent_writers_use_distinct_temp_names(tmp_path, monkeypatch):
    out = _full_run(tmp_path)
    names = []
    real = sidecar.os.replace

    def spy(src, dst):
        names.append(os.path.basename(src))
        return real(src, dst)

    monkeypatch.setattr(sidecar.os, "replace", spy)
    sidecar._write_atomic(str(out / "insights.json"), {"a": 1})
    sidecar._write_atomic(str(out / "insights.json"), {"a": 2})
    assert len(set(names)) == 2 and all(n.startswith("insights.") and n.endswith(".tmp")
                                        for n in names)
