"""Building rally_outcomes.json (badminton_analysis/outcomes/build.py)."""
import json

from badminton_analysis.outcomes import build as ob
from badminton_analysis.outcomes import labels as lab
from tests.outcome_fixtures import make_run, write

NO_ATHLETES = {"schema_version": 1, "squads": {}, "athletes": {}, "matches": {}}


def _labels(*items):
    return {"schema": 1, "revision": 1, "labels": lab.validate_labels(list(items))}


def _two_rally_run(tmp_path, **kw):
    # Rally 1 (10-15 s): lower hits last at 14.5, the shuttle lands inside the upper half
    # -> lower wins; rally 2 starts with a lower serve after a pause -> E3 agrees.
    return make_run(tmp_path / "vid", [(1, 10.0, 15.0), (2, 20.0, 25.0)],
                    hits=[(10.5, "upper"), (12.0, "lower"), (14.5, "lower"), (20.2, "lower"), (22.0, "upper")],
                    strokes=[(12.0, "lower", "clear"), (14.5, "lower", "smash"), (22.0, "upper", "unknown")],
                    landings=[(15.0, 3.0, 3.0)], **kw)


def test_auto_claim_from_landing_and_next_server(tmp_path):
    doc = ob.build_document(_two_rally_run(tmp_path), lab.empty(), NO_ATHLETES)
    r1, r2 = doc["rallies"]
    assert r1["winner_side"] == "lower" and r1["source"] == "auto" and r1["confidence"] == 0.8
    assert r1["ending"] == "in" and r1["evidence"]["rule"] == "landing_and_server"
    assert r1["ending_stroke"]["type"] == "smash" and r1["ending_stroke"]["hitter"] == "lower"
    assert r1["reviewed"] is False
    assert r2["winner_side"] is None and r2["ending_stroke"] is None   # last stroke unknown
    s = doc["summary"]
    assert s["entries"] == 2 and s["labelled"] == 1 and s["coverage"] == 0.5
    assert s["by_source"] == {"owner": 0, "auto": 1} and s["confidence_calibrated"] is False
    assert s["reconciliation"]["status"] == "no_result"


def test_owner_labels_win_and_not_point_dropped(tmp_path):
    out = _two_rally_run(tmp_path)
    labels = _labels({"start_sec": 10, "end_sec": 15, "verdict": "upper", "outcome": "unforced_error"},
                     {"start_sec": 20, "end_sec": 25, "verdict": "not_point"})
    doc = ob.build_document(out, labels, NO_ATHLETES)
    assert len(doc["rallies"]) == 1
    r = doc["rallies"][0]
    assert (r["winner_side"], r["outcome"], r["source"], r["confidence"]) == ("upper", "unforced_error", "owner", 1.0)
    assert r["reviewed"] is True and r["evidence"] is None and r["ending"] == "in"
    assert doc["summary"]["segments_rejected"] == 1 and doc["summary"]["segments"] == 2


def test_unsure_keeps_auto(tmp_path):
    labels = _labels({"start_sec": 10, "end_sec": 15, "verdict": "unsure"})
    r = ob.build_document(_two_rally_run(tmp_path), labels, NO_ATHLETES)["rallies"][0]
    assert r["reviewed"] is True and r["source"] == "auto" and r["winner_side"] == "lower"


def test_added_point_window_and_stroke(tmp_path):
    labels = _labels({"end_sec": 18.0, "verdict": "lower", "added": True})
    doc = ob.build_document(_two_rally_run(tmp_path), labels, NO_ATHLETES)
    added = doc["rallies"][1]
    assert added["segment_id"] is None and added["start_sec"] == 15.0 and added["end_sec"] == 18.0
    assert added["source"] == "owner" and doc["summary"]["added"] == 1
    assert [r["id"] for r in doc["rallies"]] == [1, 2, 3]


def test_build_minimal_run(tmp_path):
    out = make_run(tmp_path / "vid", [(1, 1.0, 5.0)], corners=False)
    doc = ob.build_document(out, lab.empty(), NO_ATHLETES)
    assert doc["summary"]["entries"] == 1 and doc["summary"]["coverage"] == 0.0
    assert doc["rallies"][0]["winner_side"] is None


def test_no_segments_no_labels(tmp_path):
    out = make_run(tmp_path / "vid", [])
    doc = ob.build_document(out, lab.empty(), NO_ATHLETES)
    assert doc["rallies"] == [] and doc["summary"]["coverage"] is None


def test_doubles_has_no_ending_stroke(tmp_path):
    doc = ob.build_document(_two_rally_run(tmp_path, match_format="doubles"), lab.empty(), NO_ATHLETES)
    assert all(r["ending_stroke"] is None for r in doc["rallies"])
    assert doc["match_format"] == "doubles"


def test_corrupt_labels_ignored_with_note(tmp_path):
    out = _two_rally_run(tmp_path)
    labels_dir = tmp_path / "labels"
    labels_dir.mkdir()
    (labels_dir / "vid.json").write_text("{bad", encoding="utf-8")
    doc = ob.build(out, labels_dir, tmp_path / "athletes.json")
    assert doc is not None and "rally labels unreadable; ignored" in doc["notes"]
    on_disk = json.loads((out / ob.OUTCOMES_NAME).read_text(encoding="utf-8"))
    assert on_disk["schema"] == 1
    assert sorted(p.name for p in out.iterdir() if p.name.endswith(".tmp")) == []


def test_build_missing_dir_returns_none(tmp_path):
    assert ob.build(tmp_path / "nope", tmp_path, tmp_path / "a.json") is None


def test_rebuild_match_reconciles_parts(tmp_path):
    outputs = tmp_path / "outputs"
    make_run(outputs / "p", [(1, 1.0, 5.0)])
    make_run(outputs / "q", [(1, 1.0, 5.0)])
    athletes = tmp_path / "athletes.json"
    write(tmp_path, "athletes.json", {"schema_version": 1, "squads": {}, "athletes": {}, "matches": {
        "p": {"upper": {"name": "A"}, "lower": {"name": "B"}, "result": {"winner": "upper", "sets": [[1, 1]]}},
        "q": {"part_of": "p", "sides_swapped": True}}})
    labels_dir = tmp_path / "labels"
    lab.save(lab.labels_path(labels_dir, "p"), _labels({"start_sec": 1, "end_sec": 5, "verdict": "upper"}))
    lab.save(lab.labels_path(labels_dir, "q"), _labels({"start_sec": 1, "end_sec": 5, "verdict": "upper"}))
    assert ob.rebuild_match(outputs, "q", labels_dir, athletes) == ["q", "p"]
    for stem in ("p", "q"):
        rec = json.loads((outputs / stem / ob.OUTCOMES_NAME).read_text(encoding="utf-8"))["summary"]["reconciliation"]
        # q's upper is p's lower, so the match is 1-1: consistent.
        assert rec["status"] == "consistent" and rec["claimed"] == {"upper": 1, "lower": 1}


def test_cli(tmp_path, capsys):
    out = _two_rally_run(tmp_path)
    assert ob.main([str(out), "--labels-dir", str(tmp_path / "l"), "--athletes", str(tmp_path / "a.json")]) == 0
    assert "2 entries, 1 labelled" in capsys.readouterr().out


def test_build_tolerates_non_dict_static_shuttle(tmp_path):
    out = _two_rally_run(tmp_path)
    payload = json.loads((out / "rally_segments.json").read_text(encoding="utf-8"))
    payload["detection"]["suppressed_static_shuttle"] = [{"point": [1, 2]}]
    (out / "rally_segments.json").write_text(json.dumps(payload), encoding="utf-8")
    assert ob.build(out, tmp_path / "l", tmp_path / "a.json") is not None


def test_no_landing_without_last_hit(tmp_path):
    out = make_run(tmp_path / "vid", [(1, 10.0, 15.0)], landings=[(11.0, 3.0, 3.0)])
    _segs, auto = ob.segment_evidence(out)
    assert auto[1]["evidence"]["landing"] is None


def test_rebuild_match_survives_a_broken_sibling(tmp_path):
    outputs = tmp_path / "outputs"
    make_run(outputs / "p", [(1, 1.0, 5.0)])
    make_run(outputs / "q", [(1, 1.0, 5.0)])
    (outputs / "q" / ob.OUTCOMES_NAME).write_text(json.dumps(
        {"schema": 1, "rallies": [], "summary": {"points_won": "abc"}}), encoding="utf-8")
    write(tmp_path, "athletes.json", {"schema_version": 1, "squads": {}, "athletes": {}, "matches": {
        "p": {"upper": {"name": "A"}, "lower": {"name": "B"}, "result": {"winner": "upper", "sets": [[1, 1]]}},
        "q": {"part_of": "p", "sides_swapped": False}}})
    assert "p" in ob.rebuild_match(outputs, "p", tmp_path / "labels", tmp_path / "athletes.json")


# ------------------------------------------------------------- winner decoder

def _model_file(tmp_path, threshold=0.9):
    from badminton_analysis.outcomes import decoder as dec
    agree = dec.fit([(dec.Obs("upper", "upper", "upper", None), "upper")] * 30
                    + [(dec.Obs("lower", "lower", "lower", None), "lower")] * 30)
    agree["claim_threshold"] = threshold
    path = tmp_path / "weights" / "rally-decoder.json"
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(agree), encoding="utf-8")
    return path


def _stable(doc):
    doc = json.loads(json.dumps(doc))
    doc.pop("generated_at")
    return doc


def test_without_decoder_weights_output_is_unchanged(tmp_path):
    out = _two_rally_run(tmp_path)
    missing = ob.build_document(out, lab.empty(), NO_ATHLETES, decoder_path=tmp_path / "none.json")
    bad = tmp_path / "bad.json"
    bad.write_text("{broken", encoding="utf-8")
    broken = ob.build_document(out, lab.empty(), NO_ATHLETES, decoder_path=bad)
    assert _stable(missing) == _stable(broken)
    r1 = missing["rallies"][0]
    assert r1["evidence"] == {"landing": r1["evidence"]["landing"], "last_hitter": "lower",
                              "next_server": "lower", "rule": "landing_and_server"}
    assert missing["summary"]["confidence_calibrated"] is False and missing["notes"] == []


def test_decoder_winners_replace_the_rule_cascade(tmp_path):
    out = _two_rally_run(tmp_path)
    doc = ob.build_document(out, lab.empty(), NO_ATHLETES, decoder_path=_model_file(tmp_path),
                            labels_dir=tmp_path / "labels")
    r1 = doc["rallies"][0]
    assert r1["winner_side"] == "lower" and r1["source"] == "auto" and r1["confidence"] > 0.9
    assert r1["evidence"]["rule"] == "decoder"
    assert set(r1["evidence"]["decoder"]) == {"p_upper", "p_lower", "p_none"}
    assert doc["summary"]["confidence_calibrated"] is True
    assert "final_score_open" in doc["notes"]          # no recorded result: the end is open
    assert doc["summary"]["reconciliation"]["status"] == "no_result"


def test_owner_labels_clamp_the_decoder(tmp_path):
    out = _two_rally_run(tmp_path)
    labels = _labels({"start_sec": 10, "end_sec": 15, "verdict": "upper"},
                     {"start_sec": 20, "end_sec": 25, "verdict": "not_point"})
    doc = ob.build_document(out, labels, NO_ATHLETES, decoder_path=_model_file(tmp_path),
                            labels_dir=tmp_path / "labels")
    assert len(doc["rallies"]) == 1 and doc["rallies"][0]["source"] == "owner"
    assert doc["rallies"][0]["winner_side"] == "upper"
    unsure = _labels({"start_sec": 10, "end_sec": 15, "verdict": "unsure"})
    r = ob.build_document(out, unsure, NO_ATHLETES, decoder_path=_model_file(tmp_path),
                          labels_dir=tmp_path / "labels")["rallies"][0]
    assert r["source"] == "auto" and r["winner_side"] == "lower" and r["reviewed"] is True


def test_added_point_is_a_step_without_a_segment(tmp_path):
    labels = _labels({"end_sec": 18.0, "verdict": "lower", "added": True})
    doc = ob.build_document(_two_rally_run(tmp_path), labels, NO_ATHLETES,
                            decoder_path=_model_file(tmp_path), labels_dir=tmp_path / "labels")
    assert [r["segment_id"] for r in doc["rallies"]] == [1, None, 2]
    assert doc["rallies"][1]["winner_side"] == "lower" and doc["rallies"][1]["source"] == "owner"


def _record(sets, **extra):
    return dict({"upper": {"name": "A"}, "lower": {"name": "B"},
                 "result": {"winner": "upper", "sets": sets}}, **extra)


def test_start_final_and_carry_across_parts(tmp_path, monkeypatch):
    from badminton_analysis.outcomes import decoder as dec
    for name in ("p", "q"):
        _two_rally_run(tmp_path / name)   # make_run(tmp/name/vid): rename below
    for name in ("p", "q"):
        (tmp_path / name / "vid").rename(tmp_path / (name + "_run"))
    calls = []
    real = dec.decode

    def spy(model, fmt, obs, clamps=None, start="zero", final=None, swapped=False):
        calls.append({"start": start, "final": final, "swapped": swapped, "n": len(obs)})
        return real(model, fmt, obs, clamps=clamps, start=start, final=final, swapped=swapped)

    monkeypatch.setattr(dec, "decode", spy)
    data = {"schema_version": 1, "squads": {}, "athletes": {},
            "matches": {"p_run": _record([[21, 10], [21, 15]]), "q_run": {"part_of": "p_run", "sides_swapped": True}}}
    model = _model_file(tmp_path)
    ob.build_document(tmp_path / "q_run", lab.empty(), data, decoder_path=model, labels_dir=tmp_path / "labels")
    assert [c["swapped"] for c in calls] == [False, True]
    assert calls[0]["start"] == "zero" and calls[0]["final"] is None
    assert isinstance(calls[1]["start"], dict) and calls[1]["final"] == [(21, 10), (21, 15)]
    calls.clear()
    data["matches"]["p_run"] = {"upper": {"name": "A"}, "lower": {"name": "B"}}     # no result
    ob.build_document(tmp_path / "p_run", lab.empty(), data, decoder_path=model, labels_dir=tmp_path / "labels")
    assert calls[0]["start"] == "free" and calls[0]["final"] is None


def test_impossible_result_leaves_the_end_open(tmp_path):
    data = {"schema_version": 1, "squads": {}, "athletes": {},
            "matches": {"vid": _record([[31, 28]], scoring={"preset": "one_game_30"})}}
    out = _two_rally_run(tmp_path)
    doc = ob.build_document(out, lab.empty(), data, decoder_path=_model_file(tmp_path),
                            labels_dir=tmp_path / "labels")
    assert "final_score_open" in doc["notes"]
    assert doc["summary"]["reconciliation"]["status"] == "no_result"
    assert doc["summary"]["reconciliation"]["reason"] == "scoring_mismatch"
    assert len(doc["rallies"]) == 2
