"""Task 3: insight envelope, tolerant loaders, dates, rally, strokes, technique."""
import json
import os
import time

import pytest

from badminton_analysis.insights import envelope
from badminton_analysis.insights.dates import video_date
from badminton_analysis.insights.loaders import Run, load_run
from badminton_analysis.insights.rally import rally_insight, summarise_rally_payload
from badminton_analysis.insights.strokes import strokes_insight
from badminton_analysis.insights.technique import (
    REFERENCE, REPORT_TO_TECH, TECHNIQUES, technique_insight)
from tests.insights_fixtures import make_run


# ---- envelope ---------------------------------------------------------------

def test_envelope_shapes():
    assert envelope.ok({"a": 1}) == {
        "status": "ok", "value": {"a": 1}, "coverage": None, "reason": None}
    assert envelope.ok(1, coverage={"n": 1, "of": 2})["coverage"] == {"n": 1, "of": 2}
    assert envelope.partial({"a": 1}, "few_hits", {"k": 2}, coverage={"n": 3, "of": 9}) == {
        "status": "partial", "value": {"a": 1}, "coverage": {"n": 3, "of": 9},
        "reason": {"code": "few_hits", "params": {"k": 2}}}
    assert envelope.partial(5, "no_data")["reason"] == {"code": "no_data", "params": {}}
    assert envelope.unavailable("no_rallies") == {
        "status": "unavailable", "value": None, "coverage": None,
        "reason": {"code": "no_rallies", "params": {}}}


# ---- loaders ----------------------------------------------------------------

def test_load_run_on_empty_dir_is_all_none(tmp_path):
    run = load_run(tmp_path)
    assert isinstance(run, Run)
    assert run.stem == tmp_path.name and run.out_dir == tmp_path
    for field in ("metadata", "detections", "hits", "strokes", "tech_reports",
                  "tech_summary", "rallies_payload"):
        assert getattr(run, field) is None, field
    assert run.notes == []


def test_load_run_on_missing_dir_does_not_raise(tmp_path):
    run = load_run(tmp_path / "nope")
    assert run.detections is None and run.hits is None


def test_load_run_reads_every_file(tmp_path):
    out = make_run(tmp_path, "clip", fps=30.0, frames=4, rallies=[(1.0, 2.0)],
                   hits=[(0.5, "lower")], strokes=[("lower", "net")],
                   reports=[("smash", "upper", 80)])
    run = load_run(out)
    assert run.stem == "clip"
    assert run.metadata["video"]["fps"] == 30.0
    frame, t, pos = run.detections[1]
    assert frame == 1 and t == pytest.approx(1 / 30)
    assert pos == {"upper": (3.05, 3.35), "lower": (3.05, 10.05)}
    assert run.hits == [(0.5, 15, "lower")]
    assert run.strokes[0]["stroke"] == "net"
    assert run.tech_reports[0]["stroke_type"] == "smash"
    assert run.rallies_payload["rallies"][0]["start_sec"] == 1.0
    assert run.notes == []


def test_load_run_tolerates_garbage_and_ragged_detections(tmp_path):
    (tmp_path / "detections.jsonl").write_text("\n".join([
        json.dumps({"frame": 0, "time_sec": 0.0,
                    "players": {"upper": {"court": [1.0, 2.0]}}}),
        "{not json",
        json.dumps({"frame": 1, "time_sec": 0.1}),                       # no players
        json.dumps({"frame": 2, "time_sec": 0.2,
                    "players": {"upper": {"court": None}, "lower": {"court": [1, 9]}}}),
        json.dumps({"time_sec": 0.3, "players": {}}),                    # no frame
        json.dumps([1, 2]),
    ]), encoding="utf-8")
    run = load_run(tmp_path)
    assert [d[0] for d in run.detections] == [0, 1, 2]
    assert run.detections[0][2] == {"upper": (1.0, 2.0), "lower": None}
    assert run.detections[1][2] == {"upper": None, "lower": None}
    assert run.detections[2][2] == {"upper": None, "lower": (1.0, 9.0)}
    assert any("detections.jsonl" in n for n in run.notes)


@pytest.mark.parametrize("name", ["metadata.json", "hits.json", "strokes.json",
                                  "technique_summary.json", "rally_segments.json"])
def test_load_run_malformed_json_becomes_none_with_note(tmp_path, name):
    (tmp_path / name).write_text("{not json", encoding="utf-8")
    run = load_run(tmp_path)
    assert run.metadata is None and run.hits is None and run.strokes is None
    assert run.tech_summary is None and run.rallies_payload is None
    assert any(name in n for n in run.notes)


@pytest.mark.parametrize("name", ["metadata.json", "hits.json", "strokes.json",
                                  "technique_summary.json", "rally_segments.json"])
def test_load_run_wrong_top_level_type_becomes_none(tmp_path, name):
    (tmp_path / name).write_text("[]", encoding="utf-8")
    run = load_run(tmp_path)
    assert run.metadata is None and run.hits is None and run.strokes is None
    assert run.tech_summary is None and run.rallies_payload is None
    assert any(name in n for n in run.notes)


def test_load_run_skips_bad_hits_and_strokes_and_reports(tmp_path):
    (tmp_path / "hits.json").write_text(json.dumps({"hits": [
        {"frame": 5, "time_sec": 0.5, "hitter": "lower"},
        {"frame": 6, "time_sec": "x", "hitter": "lower"},
        {"frame": 7, "time_sec": 0.7, "hitter": "nobody"},
        "junk"]}), encoding="utf-8")
    (tmp_path / "strokes.json").write_text(json.dumps({"strokes": [{"frame": 1}, 3]}),
                                           encoding="utf-8")
    (tmp_path / "strokes.jsonl").write_text('{"stroke_type": "smash"}\ngarbage\n[1]\n',
                                            encoding="utf-8")
    run = load_run(tmp_path)
    assert run.hits == [(0.5, 5, "lower")]
    assert run.strokes == [{"frame": 1}]
    assert run.tech_reports == [{"stroke_type": "smash"}]
    assert run.notes


def test_load_run_ragged_50k_detections_is_fast(tmp_path):
    lines = []
    for i in range(50000):
        players = {} if i % 3 == 0 else {"upper": {"court": [1.0, 2.0]}}
        lines.append(json.dumps({"frame": i, "time_sec": i / 60, "players": players}))
    (tmp_path / "detections.jsonl").write_text("\n".join(lines), encoding="utf-8")
    t0 = time.perf_counter()
    run = load_run(tmp_path)
    assert len(run.detections) == 50000
    assert time.perf_counter() - t0 < 3.0


# ---- dates ------------------------------------------------------------------

def test_video_date_from_filename():
    assert video_date("DJI_20261010090046_0010_D") == ("2026-10-10", "filename")


def test_video_date_filename_ignores_impossible_dates(tmp_path):
    video = tmp_path / "x_20261340_a.mp4"
    video.write_bytes(b"\0")
    day = time.mktime((2025, 3, 4, 12, 0, 0, 0, 0, -1))
    os.utime(video, (day, day))
    assert video_date("x_20261340_a", video_path=video) == ("2025-03-04", "mtime")
    assert video_date("IMG_1270", video_path=video) == ("2025-03-04", "mtime")
    assert video_date("x_20260230_a", video_path=video) == ("2025-03-04", "mtime")


def test_video_date_falls_back_to_run_dir_then_none(tmp_path):
    day = time.mktime((2024, 7, 8, 12, 0, 0, 0, 0, -1))
    os.utime(tmp_path, (day, day))
    assert video_date("IMG_1270", out_dir=tmp_path) == ("2024-07-08", "mtime")
    assert video_date("IMG_1270", video_path=tmp_path / "missing.mp4",
                      out_dir=tmp_path) == ("2024-07-08", "mtime")
    assert video_date("IMG_1270") == (None, "mtime")


def test_video_date_takes_the_first_valid_date():
    assert video_date("a_20260230_b_20250102") == ("2025-01-02", "filename")


# ---- rally ------------------------------------------------------------------

def test_summarise_rally_payload_matches_documented_rules():
    unknown = {"count": 0, "suppressed": False, "label": "rallies", "signal": None,
               "degraded": False, "failed": False}
    assert summarise_rally_payload(None) == unknown
    assert summarise_rally_payload([]) == dict(unknown, failed=True)
    assert summarise_rally_payload({"rallies": "x"}) == dict(unknown, failed=True)
    assert summarise_rally_payload({"rallies": [{}], "detection": "x"}) == dict(
        unknown, count=1)
    assert summarise_rally_payload(
        {"rallies": [{}], "detection": {"signal": "swing"}})["label"] == "segments"


def test_rally_insight_shuttle_is_ok_with_durations(tmp_path):
    run = load_run(make_run(tmp_path, rallies=[(0.0, 4.0), (10.0, 18.0)], signal="shuttle"))
    ins = rally_insight(run)
    assert ins["status"] == "ok" and ins["reason"] is None
    v = ins["value"]
    assert v["count"] == 2 and v["label"] == "rallies" and v["signal"] == "shuttle"
    assert v["suppressed"] is False
    assert v["durations_sec"] == [4.0, 8.0]
    assert v["mean_sec"] == 6.0 and v["median_sec"] == 6.0
    assert v["shots_per_rally"] is None and v["mean_shots"] is None


def test_rally_insight_courtview_and_old_file_are_ok(tmp_path):
    assert rally_insight(load_run(make_run(tmp_path, "a", rallies=[(0, 3)],
                                           signal="courtview")))["status"] == "ok"
    assert rally_insight(load_run(make_run(tmp_path, "b", rallies=[(0, 3)],
                                           signal=None)))["status"] == "ok"


def test_rally_insight_swing_is_partial_and_suppressed(tmp_path):
    ins = rally_insight(load_run(make_run(tmp_path, rallies=[(0, 5)], signal="swing")))
    assert ins["status"] == "partial" and ins["reason"]["code"] == "signal_swing"
    assert ins["value"]["suppressed"] is True and ins["value"]["label"] == "segments"
    assert ins["value"]["count"] == 1


@pytest.mark.parametrize("signal", ["degraded", "none"])
def test_rally_insight_degraded_is_partial(tmp_path, signal):
    ins = rally_insight(load_run(make_run(tmp_path, rallies=[(0, 5)], signal=signal)))
    assert ins["status"] == "partial" and ins["reason"]["code"] == "signal_degraded"
    assert ins["value"]["suppressed"] is True and ins["value"]["label"] == "windows"


def test_rally_insight_error_missing_zero_and_malformed_are_unavailable(tmp_path):
    runs = [load_run(make_run(tmp_path, "err", rallies=[], signal="error")),
            load_run(make_run(tmp_path, "none")),
            load_run(make_run(tmp_path, "zero", rallies=[], signal="shuttle"))]
    bad = make_run(tmp_path, "bad")
    (bad / "rally_segments.json").write_text("{nope", encoding="utf-8")
    runs.append(load_run(bad))
    for run in runs:
        ins = rally_insight(run)
        assert ins["status"] == "unavailable", run.stem
        assert ins["reason"]["code"] == "no_rallies" and ins["value"] is None


def test_rally_shots_join_is_inclusive_at_half_second(tmp_path):
    hits = [(9.4, "lower"),    # before start - 0.5
            (9.5, "lower"),    # start - 0.5: in
            (12.0, "upper"),
            (20.5, "lower"),   # end + 0.5: in
            (20.6, "upper")]   # end + 0.6: out
    run = load_run(make_run(tmp_path, rallies=[(10.0, 20.0), (40.0, 45.0)], hits=hits))
    v = rally_insight(run)["value"]
    assert v["shots_per_rally"] == [3, 0]
    assert v["mean_shots"] == 1.5


def test_rally_shots_never_come_from_strokes_json(tmp_path):
    # strokes.json holds sparse stroke contacts, not shots: without hits.json the
    # shot counts are unknown (null), not an undercount
    out = make_run(tmp_path, fps=30.0, rallies=[(1.0, 3.0)],
                   strokes=[("lower", "net", 29), ("upper", "net", 60)])
    v = rally_insight(load_run(out))["value"]
    assert v["shots_per_rally"] is None and v["mean_shots"] is None


def test_rally_shots_come_only_from_hits_json(tmp_path):
    out = make_run(tmp_path, rallies=[(1.0, 3.0)], hits=[(2.0, "lower")],
                   strokes=[("lower", "net", 30), ("upper", "net", 60)])
    assert rally_insight(load_run(out))["value"]["shots_per_rally"] == [1]


def test_rally_without_any_hits_has_null_shots(tmp_path):
    out = make_run(tmp_path, rallies=[(1.0, 3.0)], hits=[])
    assert rally_insight(load_run(out))["value"]["shots_per_rally"] is None


# ---- strokes ----------------------------------------------------------------

def _strokes(n_uncertain, n_classified):
    return ([("lower", "uncertain")] * n_uncertain
            + [("upper", "net")] * (n_classified // 2)
            + [("lower", "clear")] * (n_classified - n_classified // 2))


def test_strokes_low_coverage_is_partial(tmp_path):
    ins = strokes_insight(load_run(make_run(tmp_path, strokes=_strokes(7, 3))))
    assert ins["status"] == "partial" and ins["reason"]["code"] == "low_stroke_coverage"
    assert ins["coverage"] == {"n": 3, "of": 10}
    v = ins["value"]
    assert (v["total"], v["classified"], v["uncertain"]) == (10, 3, 7)
    assert v["distribution"] == {"net": 1, "clear": 2}
    assert v["by_hitter"] == {
        "upper": {"classified": 1, "total": 1, "distribution": {"net": 1}},
        "lower": {"classified": 2, "total": 9, "distribution": {"clear": 2}}}


def test_strokes_good_coverage_is_ok(tmp_path):
    ins = strokes_insight(load_run(make_run(tmp_path, strokes=_strokes(2, 8))))
    assert ins["status"] == "ok" and ins["reason"] is None
    assert ins["coverage"] == {"n": 8, "of": 10}


def test_strokes_threshold_is_inclusive_at_sixty_percent(tmp_path):
    assert strokes_insight(load_run(make_run(tmp_path, strokes=_strokes(4, 6))))[
        "status"] == "ok"
    assert strokes_insight(load_run(make_run(tmp_path, "b", strokes=_strokes(5, 5))))[
        "status"] == "partial"


def test_strokes_uncertain_flag_counts_even_with_a_stroke_name(tmp_path):
    out = make_run(tmp_path, strokes=[{"hitter": "lower", "stroke": "net", "uncertain": True},
                                      {"hitter": "lower", "stroke": "net", "uncertain": False}])
    v = strokes_insight(load_run(out))["value"]
    assert (v["classified"], v["uncertain"]) == (1, 1)


def test_strokes_missing_or_empty_is_unavailable(tmp_path):
    ins = strokes_insight(load_run(make_run(tmp_path, "a")))
    assert ins["status"] == "unavailable" and ins["reason"]["code"] == "no_strokes_file"
    ins = strokes_insight(load_run(make_run(tmp_path, "b", strokes=[])))
    assert ins["reason"]["code"] == "no_strokes_file"


# ---- technique --------------------------------------------------------------

def test_technique_constants():
    assert TECHNIQUES == ["smash", "clear", "drop", "net", "lift", "drive", "serve"]
    assert REPORT_TO_TECH == {"high_clear": "clear", "drop_shot": "drop",
                              "smash": "smash", "serve": "serve"}
    assert REFERENCE == {"lo": 70, "hi": 100, "source": "literature"}


def test_technique_groups_scores_and_index(tmp_path):
    out = make_run(tmp_path, reports=[("high_clear", "lower", 50), ("high_clear", "lower", 70),
                                      ("serve", "lower", 80)])
    ins = technique_insight(load_run(out))
    assert ins["status"] == "partial"
    assert ins["reason"]["code"] == "unscored_technique"
    assert set(ins["reason"]["params"]["names"]) == {"net", "lift", "drive", "smash", "drop"}
    side = ins["value"]["by_side"]["lower"]
    assert side["techniques"]["clear"] == {"count": 2, "avg_score": 60.0}
    assert side["techniques"]["serve"] == {"count": 1, "avg_score": 80.0}
    assert side["techniques"]["net"] == {"count": 0, "avg_score": None}
    assert list(side["techniques"]) == TECHNIQUES
    assert side["index"] == 70.0 and side["scored"] == 2
    assert ins["value"]["by_side"]["upper"] is None


def test_technique_sides_are_separated(tmp_path):
    out = make_run(tmp_path, reports=[("smash", "upper", 90), ("smash", "lower", 40),
                                      ("drop_shot", "lower", 60)])
    by_side = technique_insight(load_run(out))["value"]["by_side"]
    assert by_side["upper"]["techniques"]["smash"] == {"count": 1, "avg_score": 90.0}
    assert by_side["lower"]["techniques"]["smash"] == {"count": 1, "avg_score": 40.0}
    assert by_side["lower"]["techniques"]["drop"] == {"count": 1, "avg_score": 60.0}
    assert by_side["upper"]["index"] == 90.0 and by_side["lower"]["index"] == 50.0


def test_technique_null_scores_are_ignored(tmp_path):
    out = make_run(tmp_path, reports=[("smash", "lower", None), ("smash", "lower", 60),
                                      ("serve", "lower", None)])
    side = technique_insight(load_run(out))["value"]["by_side"]["lower"]
    assert side["techniques"]["smash"] == {"count": 2, "avg_score": 60.0}
    assert side["techniques"]["serve"] == {"count": 1, "avg_score": None}
    assert side["scored"] == 1 and side["index"] == 60.0


def test_technique_never_reported_techniques_stay_unscored(tmp_path):
    reports = [(t, "lower", 75) for t in ("smash", "high_clear", "drop_shot", "serve")]
    ins = technique_insight(load_run(make_run(tmp_path, reports=reports)))
    assert ins["status"] == "partial"           # net, lift, drive never have reports
    assert set(ins["reason"]["params"]["names"]) == {"net", "lift", "drive"}
    assert ins["value"]["by_side"]["lower"]["scored"] == 4


def test_technique_ignores_unknown_types_and_sides(tmp_path):
    out = make_run(tmp_path, reports=[("flick", "lower", 90), ("smash", "left", 90)])
    ins = technique_insight(load_run(out))
    assert ins["status"] == "unavailable" and ins["reason"]["code"] == "no_technique"


def test_technique_no_reports_is_unavailable(tmp_path):
    ins = technique_insight(load_run(make_run(tmp_path, "a")))
    assert ins["status"] == "unavailable" and ins["reason"]["code"] == "no_technique"
    ins = technique_insight(load_run(make_run(tmp_path, "b", reports=[])))
    assert ins["reason"]["code"] == "no_technique"


# ---- review fixes: hostile JSON values, nesting, rounding, missing durations --

_HOSTILE = [["a"], {"k": 1}, 5, None, True]


@pytest.mark.parametrize("bad", _HOSTILE)
@pytest.mark.parametrize("field", ["stroke_type", "player_side"])
def test_technique_skips_reports_with_non_string_fields(tmp_path, bad, field):
    out = make_run(tmp_path, reports=[("smash", "lower", 80)])
    good = {"stroke_type": "smash", "player_side": "lower", "overall_score": 80}
    with open(out / "strokes.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(dict(good, **{field: bad})) + "\n")
    ins = technique_insight(load_run(out))
    assert ins["value"]["by_side"]["lower"]["techniques"]["smash"]["count"] == 1


def test_technique_only_hostile_reports_is_unavailable(tmp_path):
    out = make_run(tmp_path)
    (out / "strokes.jsonl").write_text(
        '{"stroke_type": ["a"], "player_side": "lower"}\n'
        '{"stroke_type": "smash", "player_side": {"x": 1}}\n', encoding="utf-8")
    ins = technique_insight(load_run(out))
    assert ins["status"] == "unavailable" and ins["reason"]["code"] == "no_technique"


@pytest.mark.parametrize("bad", [["x"], {"k": 1}, 5, None])
def test_strokes_survive_non_string_hitter_and_stroke(tmp_path, bad):
    out = make_run(tmp_path)
    (out / "strokes.json").write_text(json.dumps({"strokes": [
        {"hitter": "lower", "stroke": "net", "uncertain": False},
        {"hitter": bad, "stroke": "net", "uncertain": False},
        {"hitter": "upper", "stroke": bad, "uncertain": False}]}), encoding="utf-8")
    v = strokes_insight(load_run(out))["value"]
    assert v["total"] == 3
    assert v["distribution"] == {"net": 2}            # a non-string stroke is not classified
    assert v["classified"] == 2 and v["uncertain"] == 1
    assert v["by_hitter"] == {
        "upper": {"classified": 0, "total": 1, "distribution": {}},
        "lower": {"classified": 1, "total": 1, "distribution": {"net": 1}}}


@pytest.mark.parametrize("bad", [["x"], {"k": 1}])
def test_rally_ignores_non_numeric_hits_and_windows(tmp_path, bad):
    out = make_run(tmp_path, rallies=[(1.0, 3.0)], hits=[(2.0, "lower")])
    payload = json.loads((out / "rally_segments.json").read_text(encoding="utf-8"))
    payload["rallies"].append({"start_sec": bad, "end_sec": bad})
    (out / "rally_segments.json").write_text(json.dumps(payload), encoding="utf-8")
    hits = json.loads((out / "hits.json").read_text(encoding="utf-8"))
    hits["hits"].append({"frame": bad, "time_sec": bad, "hitter": bad})
    (out / "hits.json").write_text(json.dumps(hits), encoding="utf-8")
    v = rally_insight(load_run(out))["value"]
    assert v["count"] == 2 and v["shots_per_rally"] == [1]


@pytest.mark.parametrize("name", ["metadata.json", "hits.json", "strokes.json",
                                  "technique_summary.json", "rally_segments.json"])
def test_load_run_deeply_nested_json_does_not_raise(tmp_path, name):
    (tmp_path / name).write_text("[" * 100000, encoding="utf-8")
    run = load_run(tmp_path)
    assert run.metadata is None and run.hits is None and run.strokes is None
    assert run.tech_summary is None and run.rallies_payload is None
    assert any(name in n for n in run.notes)


def test_load_run_deeply_nested_jsonl_line_is_skipped(tmp_path):
    (tmp_path / "detections.jsonl").write_text(
        json.dumps({"frame": 0, "time_sec": 0.0, "players": {}}) + "\n"
        + "[" * 100000 + "\n", encoding="utf-8")
    (tmp_path / "strokes.jsonl").write_text("[" * 100000 + "\n", encoding="utf-8")
    run = load_run(tmp_path)
    assert [d[0] for d in run.detections] == [0]
    assert run.tech_reports == []
    assert run.notes


def test_technique_index_is_the_mean_of_the_reported_avg_scores(tmp_path):
    # avg_scores report as 50.0, 50.0, 50.1; the unrounded mean (50.0733) would give 50.1
    out = make_run(tmp_path, reports=[("smash", "lower", 50.04), ("serve", "lower", 50.04),
                                      ("high_clear", "lower", 50.14)])
    side = technique_insight(load_run(out))["value"]["by_side"]["lower"]
    reported = [v["avg_score"] for v in side["techniques"].values()
                if v["avg_score"] is not None]
    assert reported == [50.0, 50.1, 50.0]
    assert side["index"] == round(sum(reported) / len(reported), 1) == 50.0


def test_rally_reports_how_many_rallies_lack_usable_times(tmp_path):
    out = make_run(tmp_path, rallies=[(0.0, 4.0)])
    payload = json.loads((out / "rally_segments.json").read_text(encoding="utf-8"))
    payload["rallies"] += [{"id": 2}, {"id": 3, "start_sec": 9.0, "end_sec": 5.0}]
    (out / "rally_segments.json").write_text(json.dumps(payload), encoding="utf-8")
    v = rally_insight(load_run(out))["value"]
    assert v["count"] == 3 and v["durations_sec"] == [4.0] and v["duration_missing"] == 2


def test_rally_duration_missing_is_zero_when_all_usable(tmp_path):
    out = make_run(tmp_path, rallies=[(0.0, 4.0), (10.0, 18.0)])
    assert rally_insight(load_run(out))["value"]["duration_missing"] == 0


def test_strokes_per_hitter_distribution_counts_classified_strokes_of_that_hitter(tmp_path):
    out = make_run(tmp_path, strokes=[
        ("upper", "net"), ("upper", "net"), ("upper", "smash"), ("upper", "uncertain"),
        ("lower", "clear"), ("lower", "net"),
        {"frame": 1, "hitter": "nobody", "stroke": "net", "uncertain": False}])
    v = strokes_insight(load_run(out))["value"]
    assert v["distribution"] == {"net": 4, "smash": 1, "clear": 1}     # stem-wide, unchanged
    assert v["by_hitter"]["upper"]["distribution"] == {"net": 2, "smash": 1}
    assert v["by_hitter"]["lower"]["distribution"] == {"clear": 1, "net": 1}
    # a stroke without a valid hitter belongs to neither side
    for side in ("upper", "lower"):
        h = v["by_hitter"][side]
        assert sum(h["distribution"].values()) == h["classified"]
