"""Task 5: the per-video sidecar (spec 5.2)."""
import json
import os
import time

import pytest

from badminton_analysis.insights import sidecar
from tests.insights_fixtures import doubles_hit, make_doubles_run, make_run

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
    assert sidecar.INSIGHTS_VERSION == 6


def test_document_shape_and_file_written(tmp_path):
    out = _full_run(tmp_path)
    doc = sidecar.compute_video(out.name, out)
    assert doc["schema_version"] == 1 and doc["version"] == sidecar.INSIGHTS_VERSION == 6
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
    monkeypatch.setattr(sidecar, "INSIGHTS_VERSION", 7)
    doc = sidecar.compute_video(out.name, out)
    assert len(calls) == 2 and doc["version"] == 7


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
    assert doc["date"]["value"]                 # metadata.json's mtime date


def _day(y, m, d):
    return time.mktime((y, m, d, 12, 0, 0, 0, 0, -1))


def test_undated_stem_keeps_its_input_date_across_sidecar_writes(tmp_path, monkeypatch):
    # only run inputs: the oldest input's date, never the directory's own mtime,
    # which every sidecar write (mkstemp + os.replace) moves to "today"
    out = make_run(tmp_path, "IMG_1270", hits=[(1.0, "upper")],
                   strokes=[("upper", "smash")])
    (out / "metadata.json").unlink()
    os.utime(out / "hits.json", (_day(2024, 5, 1),) * 2)
    os.utime(out / "strokes.json", (_day(2024, 3, 2),) * 2)
    os.utime(out, (_day(2024, 1, 1),) * 2)
    first = sidecar.compute_video("IMG_1270", out)
    assert first["date"] == {"value": "2024-03-02", "source": "mtime"}
    os.utime(out, (_day(2026, 10, 19),) * 2)      # the write (and later ones) touch the dir
    monkeypatch.setattr(sidecar, "INSIGHTS_VERSION", 99)     # force a recompute
    again = sidecar.compute_video("IMG_1270", out)
    assert again["version"] == 99
    assert again["date"] == {"value": "2024-03-02", "source": "mtime"}


def test_empty_run_dir_has_no_date(tmp_path):
    out = tmp_path / "outputs" / "IMG_1270"
    out.mkdir(parents=True)
    os.utime(out, (_day(2024, 1, 1),) * 2)
    doc = sidecar.compute_video("IMG_1270", out)
    assert doc["date"] == {"value": None, "source": "mtime"}
    assert (out / "insights.json").exists()
    doc = sidecar.compute_video("IMG_1270", out)        # after its own sidecar write
    assert doc["date"] == {"value": None, "source": "mtime"}


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


# ---- doubles runs --------------------------------------------------------------

PLAYER_KEYS = ("technique", "strokes", "movement", "recovery", "pull")


def _doubles_run(tmp_path, match_format="doubles"):
    return make_run(tmp_path, "DJI_20261010090046_0010_D", frames=120, rallies=[(0.5, 3.0)],
                    hits=[(1.0, "upper"), (1.5, "lower")],
                    strokes=[("upper", "smash"), ("lower", "net")], reports=REPORTS,
                    match_format=match_format)


def test_doubles_run_withholds_every_per_player_insight(tmp_path):
    out = _doubles_run(tmp_path)
    doc = sidecar.compute_video(out.name, out)
    assert doc["match_format"] == "doubles"
    for key in PLAYER_KEYS:
        env = doc[key]
        _assert_envelope(env)
        assert env["status"] == "unavailable" and env["value"] is None, key
        assert env["reason"]["code"] == "doubles_run", key


def test_doubles_run_keeps_rally_and_quality(tmp_path):
    out = _doubles_run(tmp_path)
    doc = sidecar.compute_video(out.name, out)
    assert doc["rally"]["status"] in ("ok", "partial")
    assert doc["rally"]["value"]["count"] == 1
    assert doc["quality"]["shuttle_coverage_in_play"] == 0.9
    assert doc["quality"]["shuttle_source"] == "tracknet"


def test_singles_run_records_match_format_and_computes_everything(tmp_path):
    for fmt in (None, "singles"):
        out = make_run(tmp_path / str(fmt), "clip", frames=120, rallies=[(0.5, 3.0)],
                       hits=[(1.0, "upper"), (1.5, "lower")],
                       strokes=[("upper", "smash"), ("lower", "net")], reports=REPORTS,
                       match_format=fmt)
        doc = sidecar.compute_video(out.name, out)
        assert doc["match_format"] == "singles"
        assert doc["technique"]["status"] == "partial"
        assert doc["strokes"]["status"] in ("ok", "partial")
        assert doc["movement"]["status"] != "unavailable"


def test_doubles_run_does_not_run_per_player_insights(tmp_path, monkeypatch):
    out = _doubles_run(tmp_path)
    calls = {name: _count_calls(monkeypatch, name)
             for name in ("technique_insight", "strokes_insight", "movement_insight",
                          "recovery_insight", "pull_insight")}
    sidecar.compute_video(out.name, out)
    assert {name: len(c) for name, c in calls.items()} == dict.fromkeys(calls, 0)


def test_changing_the_match_format_recomputes_the_cached_sidecar(tmp_path):
    out = _doubles_run(tmp_path, match_format="singles")
    first = sidecar.compute_video(out.name, out)
    assert first["match_format"] == "singles" and first["technique"]["status"] == "partial"
    make_run(tmp_path, out.name, frames=120, rallies=[(0.5, 3.0)],
             hits=[(1.0, "upper"), (1.5, "lower")],
             strokes=[("upper", "smash"), ("lower", "net")], reports=REPORTS,
             match_format="doubles")
    meta = out / "metadata.json"
    os.utime(meta, ns=(time.time_ns() + 5_000_000_000,) * 2)       # mtime must differ
    second = sidecar.compute_video(out.name, out)
    assert second["match_format"] == "doubles"
    assert second["technique"]["reason"]["code"] == "doubles_run"


def test_fallback_documents_carry_match_format(tmp_path):
    doc = sidecar.compute_video("nothing", tmp_path / "nope")
    assert doc["match_format"] == "singles"


# ---- the doubles insight (doubles addendum 4.1) -----------------------------------

def test_singles_sidecar_has_doubles_not_doubles(tmp_path):
    out = _full_run(tmp_path)
    doc = sidecar.compute_video(out.name, out)
    assert doc["doubles"] == {"status": "unavailable", "value": None, "coverage": None,
                              "reason": {"code": "not_doubles", "params": {}}}


def test_doubles_sidecar_carries_the_doubles_insight(tmp_path):
    out = make_doubles_run(tmp_path, hits=[doubles_hit(5, "P1"), doubles_hit(9, None)])
    doc = sidecar.compute_video(out.name, out)
    _assert_envelope(doc["doubles"])
    assert doc["doubles"]["status"] == "ok"
    assert doc["doubles"]["value"]["hits"]["total"] == 2
    assert doc["technique"]["reason"]["code"] == "doubles_run"
    assert doc["inputs"]["players.json"] is not None
    assert doc["inputs"]["tracklets.jsonl"] is not None
    json.dumps(doc, allow_nan=False)
    assert json.loads((out / "insights.json").read_text(encoding="utf-8")) == doc


@pytest.mark.parametrize("name", ["players.json", "tracklets.jsonl", "hits.json"])
def test_touching_a_doubles_input_recomputes(tmp_path, monkeypatch, name):
    out = make_doubles_run(tmp_path, hits=[doubles_hit(5, "P1")])
    calls = _count_calls(monkeypatch, "doubles_insight")
    sidecar.compute_video(out.name, out)
    sidecar.compute_video(out.name, out)
    assert len(calls) == 1
    target = out / name
    stat = target.stat()
    os.utime(target, ns=(stat.st_atime_ns, stat.st_mtime_ns + 5_000_000_000))
    sidecar.compute_video(out.name, out)
    assert len(calls) == 2


def test_players_json_appearing_later_recomputes(tmp_path):
    out = make_doubles_run(tmp_path, status=None)
    assert sidecar.compute_video(out.name, out)["doubles"]["reason"]["code"] == "no_players"
    make_doubles_run(tmp_path, out.name)
    assert sidecar.compute_video(out.name, out)["doubles"]["status"] == "ok"


def test_version_5_cache_without_doubles_is_rebuilt(tmp_path, monkeypatch):
    out = make_doubles_run(tmp_path)
    doc = sidecar.compute_video(out.name, out)
    old = dict(doc, version=5)
    old.pop("doubles")
    (out / "insights.json").write_text(json.dumps(old), encoding="utf-8")
    calls = _count_calls(monkeypatch, "doubles_insight")
    again = sidecar.compute_video(out.name, out)
    assert len(calls) == 1
    assert again["version"] == 6 and again["doubles"]["status"] == "ok"


def test_current_version_cache_without_a_doubles_envelope_is_rebuilt(tmp_path):
    out = make_doubles_run(tmp_path)
    doc = sidecar.compute_video(out.name, out)
    (out / "insights.json").write_text(json.dumps(dict(doc, doubles=[1])), encoding="utf-8")
    assert sidecar.compute_video(out.name, out)["doubles"]["status"] == "ok"


def test_failing_doubles_insight_is_no_data_and_others_survive(tmp_path, monkeypatch):
    out = make_doubles_run(tmp_path)

    def boom(run):
        raise RuntimeError("bug")

    monkeypatch.setattr(sidecar, "doubles_insight", boom)
    doc = sidecar.compute_video(out.name, out)
    assert doc["doubles"]["reason"]["code"] == "no_data"
    assert doc["technique"]["reason"]["code"] == "doubles_run"


def test_loader_failure_makes_doubles_no_data(tmp_path, monkeypatch):
    out = make_doubles_run(tmp_path)
    monkeypatch.setattr(sidecar, "load_run", lambda path: 1 / 0)
    doc = sidecar.compute_video(out.name, out)
    assert doc["doubles"]["reason"]["code"] == "no_data"
    assert sidecar._fallback("x")["doubles"]["reason"]["code"] == "no_data"
