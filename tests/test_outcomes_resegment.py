"""Re-segmenting existing results after the segmenter changed (spec 7.5)."""
import json

import pytest

from badminton_analysis.outcomes import build as ob
from badminton_analysis.outcomes import labels as lab
from badminton_analysis.outcomes import model_eval as me
from badminton_analysis.outcomes import resegment as rs
from badminton_analysis.outcomes import run_inputs
from badminton_analysis.outcomes import segmenter as sg
from badminton_analysis.stroke import owner_labels, relabel
from tests.rally_model_fixtures import make_match, rally_times, winners_for, write_labels


@pytest.fixture
def world(tmp_path):
    results, labels = tmp_path / "outputs", tmp_path / "labels"
    for i, (stem, n) in enumerate({"m1": 6, "m2": 7}.items()):
        make_match(results, labels, stem, n, seed=i, offset=5.0 + i)
    athletes = tmp_path / "athletes.json"
    athletes.write_text(json.dumps({"schema": 1, "matches": {}}), encoding="utf-8")
    return results, labels, athletes, tmp_path / "weights", tmp_path / "stroke_labels"


def keep_segmenter(results, labels, weights):
    ms = []
    for stem in ("m1", "m2"):
        doc = lab.load(labels / f"{stem}.json")
        ms.append((run_inputs.load(results / stem), doc, [l["end_sec"] for l in doc["labels"]]))
    model = sg.fit(ms)
    weights.mkdir(parents=True, exist_ok=True)
    (weights / me.SEGMENTER_FILE).write_text(json.dumps(model), encoding="utf-8")
    return model


def call(world, dirs=None):
    results, labels, athletes, weights, stroke = world
    dirs = dirs if dirs is not None else [results / "m1", results / "m2"]
    return rs.run(dirs, weights, labels, stroke, athletes)


def segs(results, stem):
    return json.loads((results / stem / "rally_segments.json").read_text())


def test_a_kept_segmenter_rewrites_segments_hits_and_outcomes(world):
    results, labels, athletes, weights, _ = world
    keep_segmenter(results, labels, weights)
    assert segs(results, "m1")["detection"]["signal"] == "swing"
    summary = call(world)
    assert sorted(summary["touched"]) == ["m1", "m2"] and summary["failed"] == []
    doc = segs(results, "m1")
    assert doc["detection"]["signal"] == "learned" and doc["detection"]["degraded"] is False
    assert doc["detection"]["model"]["version"]
    assert len(doc["rallies"]) == 6 and all("p_point" in r for r in doc["rallies"])
    hits = json.loads((results / "m1" / "hits.json").read_text())
    assert [h["rally_id"] for h in hits["hits"]] == [i for i in range(1, 7) for _ in range(3)]
    assert hits["source"] == "learned"
    out = json.loads((results / "m1" / "rally_outcomes.json").read_text())
    assert out["summary"]["by_source"]["owner"] == 6


def test_unchanged_results_are_not_rewritten(world):
    results, labels, athletes, weights, _ = world
    keep_segmenter(results, labels, weights)
    call(world)
    first = (results / "m1" / "rally_segments.json").read_bytes()
    summary = call(world)
    assert summary["unchanged"] == ["m1", "m2"] and summary["touched"] == []
    assert (results / "m1" / "rally_segments.json").read_bytes() == first


def test_no_kept_model_and_swing_segments_changes_nothing(world):
    results = world[0]
    files = {p.name: p.read_bytes() for p in (results / "m1").iterdir()}
    summary = call(world)
    assert summary["touched"] == [] and summary["unchanged"] == ["m1", "m2"]
    assert {p.name: p.read_bytes() for p in (results / "m1").iterdir()} == files


def test_rolling_back_to_no_segmenter_puts_the_swing_path_back(world):
    results, labels, athletes, weights, _ = world
    keep_segmenter(results, labels, weights)
    call(world)
    assert segs(results, "m1")["detection"]["signal"] == "learned"
    (weights / me.SEGMENTER_FILE).unlink()
    summary = call(world)
    assert "m1" in summary["touched"]
    det = segs(results, "m1")["detection"]
    assert det["signal"] != "learned" and "model" not in det
    assert det["stroke_labels_withheld"] == bool(det.get("degraded"))


def test_owner_verdicts_survive_a_change_of_segment_ids(world):
    """Review Focus 5: labels were attached to old segments (other ids, two rallies each); they re-attach by time."""
    results, labels, athletes, weights, _ = world
    times = rally_times(6, 5.0)
    merged = [(10 + k, times[2 * k][0] - 2.0, times[2 * k + 1][1] + 1.0) for k in range(3)]
    make_match(results, labels, "m1", 6, seed=0, offset=5.0, segments=merged)
    extra = [{"start_sec": None, "end_sec": times[2][1] + 6.0, "verdict": "upper", "outcome": "winner", "added": True}]
    write_labels(labels, "m1", times, winners_for(6, 0), extra)
    keep_segmenter(results, labels, weights)
    decoder = weights / me.DECODER_FILE

    def owner_winners():
        doc = json.loads((results / "m1" / "rally_outcomes.json").read_text())
        return [e["winner_side"] for e in sorted(doc["rallies"], key=lambda e: e["end_sec"]) if e["source"] == "owner"]

    ob.rebuild_match(results, "m1", labels, athletes, decoder_path=decoder)
    before = owner_winners()
    assert len(before) == 7                                     # six segment labels and the added point
    call(world, [results / "m1"])
    assert segs(results, "m1")["detection"]["signal"] == "learned" and len(segs(results, "m1")["rallies"]) == 6
    after = owner_winners()
    assert sorted(after) == sorted(before) and len(after) == 7        # every owner verdict survives
    doc = json.loads((results / "m1" / "rally_outcomes.json").read_text())
    assert sum(1 for e in doc["rallies"] if e["segment_id"] is None) == 1     # the added point stays added
    assert all(e["reviewed"] for e in doc["rallies"] if e["source"] == "owner")


def test_clips_are_marked_stale_only_when_there_are_clips(world):
    results, labels, athletes, weights, _ = world
    keep_segmenter(results, labels, weights)
    (results / "m1" / "clips").mkdir()
    (results / "m1" / "clips" / "rally_1.mp4").write_bytes(b"x")
    call(world)
    assert (results / "m1" / "clips" / ".stale").is_file()
    assert (results / "m1" / "clips" / "rally_1.mp4").is_file()        # not deleted: re-cut on request
    assert not (results / "m2" / "clips").exists()


def test_results_without_learned_hits_are_skipped_with_a_reason(world):
    results, labels, athletes, weights, _ = world
    keep_segmenter(results, labels, weights)
    make_match(results, labels, "rule", 5, source="contact_rule", labelled=False)
    (results / "empty").mkdir()
    summary = call(world, [results / "m1", results / "rule", results / "empty", results / "gone"])
    reasons = {s["stem"]: s["reason"] for s in summary["skipped"]}
    assert reasons == {"rule": "no_learned_hits", "empty": "no_run", "gone": "no_run"}
    assert summary["touched"] == ["m1"]


def test_one_failing_result_does_not_stop_the_others(world, monkeypatch):
    results, labels, athletes, weights, _ = world
    keep_segmenter(results, labels, weights)
    real = sg.segments
    old = (results / "m1" / "rally_segments.json").read_bytes()

    def segments(inputs, model):
        if len(inputs.hits) == 18:        # m1 has 6 rallies of 3 hits
            raise RuntimeError("boom")
        return real(inputs, model)

    monkeypatch.setattr(sg, "segments", segments)
    summary = call(world)
    assert summary["failed"] == [{"stem": "m1", "error": "RuntimeError: boom"}] and summary["touched"] == ["m2"]
    assert (results / "m1" / "rally_segments.json").read_bytes() == old


def test_a_failed_hits_write_restores_the_segments_file(world, monkeypatch):
    results, labels, athletes, weights, _ = world
    keep_segmenter(results, labels, weights)
    old = (results / "m1" / "rally_segments.json").read_bytes()
    real = rs.write_pipeline_json_atomic

    def write(path, doc):
        if str(path).endswith("hits.json"):
            raise OSError("disk full")
        return real(path, doc)

    monkeypatch.setattr(rs, "write_pipeline_json_atomic", write)
    summary = call(world, [results / "m1"])
    assert summary["failed"][0]["stem"] == "m1" and summary["touched"] == []
    assert (results / "m1" / "rally_segments.json").read_bytes() == old


def test_stroke_labels_are_reapplied_to_touched_results(world, monkeypatch):
    results, labels, athletes, weights, stroke = world
    keep_segmenter(results, labels, weights)
    calls = []
    monkeypatch.setattr(owner_labels, "apply_to_result",
                        lambda d, ld, stem: calls.append(("apply", stem, str(ld))) or {"applied": True})
    monkeypatch.setattr(relabel, "rescore_reports", lambda d: calls.append(("rescore", d.name)) or 0)
    call(world, [results / "m1"])
    assert ("apply", "m1", str(stroke)) in calls and ("rescore", "m1") in calls
    assert not any(c[1] == "m2" for c in calls)


def test_main_prints_the_summary(world, capsys):
    results, labels, athletes, weights, stroke = world
    keep_segmenter(results, labels, weights)
    code = rs.main(["--weights-dir", str(weights), "--labels-dir", str(labels), "--stroke-labels-dir", str(stroke),
                    "--athletes", str(athletes), str(results / "m1"), str(results / "m2")])
    last = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert code == 0 and sorted(last["touched"]) == ["m1", "m2"]


# ---- review fixes ----------------------------------------------------------------------------

def _record_rebuilds(monkeypatch):
    calls = []
    real = ob.rebuild_match
    monkeypatch.setattr(ob, "rebuild_match",
                        lambda outputs, stem, *a, **k: calls.append(stem) or real(outputs, stem, *a, **k))
    return calls


def test_decoder_only_keep_rebuilds_outcomes(world, monkeypatch):
    """Review 3: nothing re-segments when only the decoder changed, yet every outcome must be rebuilt."""
    results, labels, athletes, weights, stroke = world
    keep_segmenter(results, labels, weights)
    call(world)
    calls = _record_rebuilds(monkeypatch)
    summary = call(world)
    assert summary["unchanged"] == ["m1", "m2"] and calls == []           # segmenter-only: nothing to rebuild
    summary = rs.run([results / "m1", results / "m2"], weights, labels, stroke, athletes, rebuild_all=True)
    assert summary["touched"] == [] and sorted(calls) == ["m1", "m2"] and summary["failed"] == []
    code = rs.main(["--weights-dir", str(weights), "--labels-dir", str(labels), "--stroke-labels-dir",
                    str(stroke), "--athletes", str(athletes), "--rebuild-all", str(results / "m1")])
    assert code == 0 and calls.count("m1") == 2


def test_rebuild_all_skips_results_that_were_skipped(world, monkeypatch):
    results, labels, athletes, weights, stroke = world
    make_match(results, labels, "rule", 5, source="contact_rule", labelled=False)
    calls = _record_rebuilds(monkeypatch)
    summary = rs.run([results / "rule", results / "m1"], weights, labels, stroke, athletes, rebuild_all=True)
    assert [s["stem"] for s in summary["skipped"]] == ["rule"] and calls == ["m1"]


def test_resegment_cancel_rebuilds_touched_outcomes_and_hit_ids(world, monkeypatch):
    """Review 8: a child killed between the segments write and the hits write is repaired."""
    results, labels, athletes, weights, stroke = world
    keep_segmenter(results, labels, weights)
    call(world, [results / "m1"])
    # what a kill between the segments write and the hits write leaves: new segments, hits on the old ids,
    # the journal still there (a dead process runs no cleanup)
    hits = json.loads((results / "m1" / "hits.json").read_text())
    for h in hits["hits"]:
        h["rally_id"] = 99
    (results / "m1" / "hits.json").write_text(json.dumps(hits), encoding="utf-8")
    (results / "m1" / rs.JOURNAL).write_text("{}", encoding="utf-8")
    calls = _record_rebuilds(monkeypatch)
    out = rs.repair([results / "m1"], weights, labels, stroke, athletes)
    assert out == {"repaired": ["m1"], "failed": []} and calls == ["m1"]
    ids = [h["rally_id"] for h in json.loads((results / "m1" / "hits.json").read_text())["hits"]]
    assert 99 not in ids and ids == [r["id"] for r in segs(results, "m1")["rallies"] for _ in range(3)]
    assert not (results / "m1" / rs.JOURNAL).exists()
    assert (results / "m1" / "rally_outcomes.json").is_file()
    assert rs.repair([results / "m1"], weights, labels, stroke, athletes) == {"repaired": [], "failed": []}


def test_repair_without_journal_still_fixes_stale_hit_ids(world, monkeypatch):
    results, labels, athletes, weights, stroke = world
    hits = json.loads((results / "m2" / "hits.json").read_text())
    hits["hits"][0]["rally_id"] = 42
    (results / "m2" / "hits.json").write_text(json.dumps(hits), encoding="utf-8")
    calls = _record_rebuilds(monkeypatch)
    assert rs.repair([results / "m2"], weights, labels, stroke, athletes)["repaired"] == ["m2"]
    assert calls == ["m2"]
    assert json.loads((results / "m2" / "hits.json").read_text())["hits"][0]["rally_id"] == 1


def test_a_clean_run_leaves_no_journal(world, monkeypatch):
    results, labels, athletes, weights, stroke = world
    keep_segmenter(results, labels, weights)
    call(world)
    assert not list(results.glob("*/" + rs.JOURNAL))


def test_detection_model_f1_is_the_heldout_score(world):
    """Review 15: ``f1`` is the held-out score stored with the model; the training score is named as such."""
    results, labels, athletes, weights, _ = world
    model = keep_segmenter(results, labels, weights)
    call(world)
    block = segs(results, "m1")["detection"]["model"]
    assert block.get("f1") is None and block["train_f1"] == model["train_f1"]     # no held-out block stored
    (weights / me.SEGMENTER_FILE).write_text(json.dumps(dict(model, heldout={"f1": 0.61, "swing_f1": 0.5})),
                                             encoding="utf-8")
    call(world)
    block = segs(results, "m1")["detection"]["model"]
    assert block["f1"] == 0.61 and "train_f1" not in block


def test_first_promote_rolls_back_to_swing_segments(world):
    """Review 9 end to end: promote the segmenter on empty weights, roll back to no model, re-segment."""
    results, labels, athletes, weights, stroke = world
    model = keep_segmenter(results, labels, weights)
    (weights / me.SEGMENTER_FILE).unlink()
    cand = weights / "candidates" / "rally-segmenter-1.json"
    cand.parent.mkdir(parents=True)
    cand.write_text(json.dumps(model), encoding="utf-8")
    me.promote(weights, {me.SEGMENTER_FILE: cand}, {})
    call(world)
    assert segs(results, "m1")["detection"]["signal"] == "learned"
    assert me.can_rollback(weights) is True and me.rollback(weights) is True
    assert not (weights / me.SEGMENTER_FILE).exists()
    summary = call(world)
    assert "m1" in summary["touched"] and segs(results, "m1")["detection"]["signal"] != "learned"


def test_rollback_to_no_model_resegment_is_byte_identical(world, tmp_path):
    """Review 5: re-segmenting writes the pipeline's own format, so back on the swing path the files match."""
    from badminton_analysis.data import writer
    results, labels, athletes, weights, _ = world
    originals = {}
    for stem in ("m1", "m2"):
        for name in ("rally_segments.json", "hits.json"):      # as the analysis pipeline wrote them
            path = results / stem / name
            writer.write_json(str(path), json.loads(path.read_text(encoding="utf-8")))
            originals[(stem, name)] = path.read_bytes()
    keep_segmenter(results, labels, weights)
    call(world)
    assert segs(results, "m1")["detection"]["signal"] == "learned"
    learned = (results / "m1" / "rally_segments.json").read_bytes()
    expected = tmp_path / "expected.json"
    writer.write_json(str(expected), json.loads(learned))
    assert learned == expected.read_bytes()                    # the learned write is the pipeline's format too
    (weights / me.SEGMENTER_FILE).unlink()
    call(world)
    for stem in ("m1", "m2"):
        seg_path, hits_path = results / stem / "rally_segments.json", results / stem / "hits.json"
        original = json.loads(originals[(stem, "rally_segments.json")])
        rallies, detection = rs._swing(results / stem, original["detection"])      # what the swing path derives
        expect = tmp_path / f"{stem}-swing.json"
        writer.write_json(str(expect), {"fps": original["fps"], "rallies": rallies, "detection": detection})
        assert seg_path.read_bytes() == expect.read_bytes()
        back = json.loads(seg_path.read_text(encoding="utf-8"))
        assert back["detection"].get("court_view") == original["detection"].get("court_view")
        assert back["detection"]["signal"] != "learned" and "model" not in back["detection"]
        hits = json.loads(hits_path.read_text(encoding="utf-8"))
        for h in hits["hits"]:
            h["rally_id"] = rs._rally_id(rallies, int(h["frame"]))
        expect_hits = tmp_path / f"{stem}-hits.json"
        writer.write_json(str(expect_hits), hits)
        assert hits_path.read_bytes() == expect_hits.read_bytes()
