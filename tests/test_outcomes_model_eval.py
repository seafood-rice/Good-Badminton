"""Leave-one-match-out measurement of the rally models, keep rules, thresholds and the card status."""
import json

import pytest

from badminton_analysis.models import store
from badminton_analysis.outcomes import decoder as dec
from badminton_analysis.outcomes import labels as lab
from badminton_analysis.outcomes import model_eval as me
from badminton_analysis.outcomes import run_inputs
from badminton_analysis.outcomes import segmenter as sg
from tests.rally_model_fixtures import make_match, rally_times, winners_for, write_labels

COUNTS = {"m1": 6, "m2": 7, "m3": 8}


@pytest.fixture
def world(tmp_path):
    results, labels = tmp_path / "outputs", tmp_path / "labels"
    for i, (stem, n) in enumerate(COUNTS.items()):
        make_match(results, labels, stem, n, seed=i, offset=5.0 + i)
    athletes = tmp_path / "athletes.json"
    athletes.write_text(json.dumps({"schema": 1, "matches": {}}), encoding="utf-8")
    return results, labels, athletes


# ---- which matches are measurable ------------------------------------------------------------

def test_labelled_matches_and_the_reasons_for_the_rest(world):
    results, labels, _ = world
    make_match(results, labels, "rule_hits", 6, source="contact_rule")        # Review Focus 3
    write_labels(labels, "empty", [], [])
    make_match(results, labels, "no_labels", 5, labelled=False)
    write_labels(labels, "no_run", rally_times(3), winners_for(3, 0))
    (labels / "broken.json").write_text("{nope", encoding="utf-8")
    assert me.labelled_matches(labels, results) == ["m1", "m2", "m3"]
    skipped = {s["stem"]: s["reason"] for s in me.scan(labels, results)[1]}
    assert skipped == {"rule_hits": "no_learned_hits", "empty": "no_point_labels",
                       "no_run": "no_run", "broken": "labels_unreadable"}


# ---- thresholds and keep rules ---------------------------------------------------------------

def _p(side, conf):
    other = "lower" if side == "upper" else "upper"
    return {side: conf, other: 1 - conf, "none": 0.0}


def test_choose_threshold_is_the_lowest_one_reaching_the_target():
    held = ([(_p("upper", 0.95), "upper")] * 8 + [(_p("upper", 0.7), "lower")] * 4
            + [(_p("lower", 0.8), "lower")] * 4)
    assert me.choose_threshold(held, target=0.90) == pytest.approx(0.8)


def test_choose_threshold_unreachable_is_none():
    held = [(_p("upper", 0.99), "lower")] * 5 + [(_p("upper", 0.9), "upper")] * 5
    assert me.choose_threshold(held, target=0.90) is None
    assert me.choose_threshold([], target=0.90) is None


def totals(**kw):
    base = {"seg_f1": 0.8, "swing_f1": 0.6, "segmenter_folds": 2, "points": 40, "threshold": 0.9,
            "dec_claimed": 20, "dec_correct": 19, "dec_precision": 0.95,
            "rules_claimed": 4, "rules_correct": 3, "rules_precision": 0.75}
    base.update(kw)
    return base


def test_decide_keeps_or_rejects_each_model_separately():
    both = me.decide(totals())
    assert both["segmenter"][0] and both["decoder"][0]
    assert both["segmenter"][1]["code"] == "f1_ok" and both["decoder"][1]["code"] == "decoder_ok"
    worse_seg = me.decide(totals(seg_f1=0.45, swing_f1=0.53))
    assert worse_seg["segmenter"] == (False, {"code": "lower_f1", "candidate": 0.45, "swing": 0.53})
    assert worse_seg["decoder"][0] is True
    fewer = me.decide(totals(dec_correct=2, dec_claimed=2, dec_precision=1.0))
    assert fewer["decoder"] == (False, {"code": "fewer_correct", "correct": 2, "rules": 3})
    low = me.decide(totals(dec_precision=0.8))
    assert low["decoder"][1]["code"] == "low_precision" and low["decoder"][0] is False
    unreachable = me.decide(totals(threshold=None, dec_claimed=0, dec_correct=0, dec_precision=None))
    assert unreachable["decoder"] == (False, {"code": "precision_unreachable", "target": me.TARGET_PRECISION})
    nothing = me.decide(totals(seg_f1=None, segmenter_folds=0))
    assert nothing["segmenter"] == (False, {"code": "no_training_data"})


# ---- leave one match out ---------------------------------------------------------------------

def test_evaluate_leaves_the_held_out_match_out(world, monkeypatch):
    results, labels, athletes = world
    seg_calls, dec_calls, decode_calls = [], [], []
    real_seg_fit, real_dec_fit, real_decode = sg.fit, dec.fit, dec.decode

    def seg_fit(matches):
        seg_calls.append(sorted(len(ends) for _inp, _doc, ends in matches))
        return real_seg_fit(matches)

    def dec_fit(labelled, fmt=None, fmts=None):
        seqs = labelled if labelled and isinstance(labelled[0], list) else [labelled]
        dec_calls.append(sorted(sum(1 for _o, v in seq if v in dec.SIDES) for seq in seqs))
        return real_dec_fit(labelled, fmt, fmts)

    def decode(model, fmt, obs, clamps=None, **kw):
        decode_calls.append(dict(clamps or {}))
        return real_decode(model, fmt, obs, clamps, **kw)

    monkeypatch.setattr(sg, "fit", seg_fit)
    monkeypatch.setattr(dec, "fit", dec_fit)
    monkeypatch.setattr(dec, "decode", decode)
    res = me.evaluate(labels, results, athletes, None)
    expected = sorted(sorted(n for s, n in COUNTS.items() if s != held) for held in COUNTS)
    assert sorted(seg_calls) == expected          # each fold trains on the other two matches only
    assert sorted(dec_calls) == expected
    assert len(decode_calls) == 3 and all(c == {} for c in decode_calls)    # no label reaches the decoding
    assert set(res["per_match"]) == set(COUNTS)


def test_evaluate_reports_the_learned_models_against_swing_and_rules(world):
    results, labels, athletes = world
    res = me.evaluate(labels, results, athletes, None)
    t = res["totals"]
    assert res["fixed"] is False
    assert t["points"] == sum(COUNTS.values()) and t["segmenter_folds"] == 3
    assert t["seg_f1"] > 0.9 and t["swing_f1"] == 0.0                     # no swing in the fixture
    assert t["rules_correct"] == 0 and t["rules_claimed"] == 0           # no landing: the cascade is silent
    assert t["threshold"] is not None and t["dec_precision"] >= 0.9 and t["dec_correct"] > 0
    m = res["per_match"]["m1"]
    assert m["points"] == 6 and m["segmenter"]["f1"] > 0.9 and m["decoder"]["correct"] > 0
    keep = me.decide(t)
    assert keep["segmenter"][0] and keep["decoder"][0]


def test_evaluate_a_fixed_candidate_trains_nothing(world, monkeypatch):
    results, labels, athletes = world
    train = [(run_inputs.load(results / s), lab.load(labels / f"{s}.json")) for s in ("m1", "m2")]
    seg = sg.fit([(inp, doc, [l["end_sec"] for l in doc["labels"]]) for inp, doc in train])
    decoder_model = dec.fit([[(None, "upper")]])
    monkeypatch.setattr(sg, "fit", lambda *a, **k: pytest.fail("a fixed candidate is not retrained"))
    monkeypatch.setattr(dec, "fit", lambda *a, **k: pytest.fail("a fixed candidate is not retrained"))
    res = me.evaluate(labels, results, athletes, {"segmenter": seg, "decoder": decoder_model})
    assert res["fixed"] is True and res["totals"]["seg_f1"] is not None


def test_a_fold_without_both_classes_is_skipped_not_fatal(world, monkeypatch):
    results, labels, athletes = world

    def boom(matches):
        raise ValueError("one class")

    monkeypatch.setattr(sg, "fit", boom)
    res = me.evaluate(labels, results, athletes, None)
    assert res["totals"]["segmenter_folds"] == 0 and res["totals"]["seg_f1"] is None
    assert me.decide(res["totals"])["segmenter"] == (False, {"code": "no_training_data"})
    assert res["totals"]["dec_claimed"] > 0           # the decoder still measured, on the live segments


def test_evaluate_fold_specific_segments(world, monkeypatch):
    """Each fold's segmenter cuts the video differently: the evidence cache must follow the segments."""
    results, labels, athletes = world
    real_fit, real_segments = sg.fit, sg.segments
    counter = iter(range(1, 100))
    monkeypatch.setattr(sg, "fit", lambda ms: {"real": real_fit(ms), "k": next(counter)})

    def fold_segments(inp, model):
        if "real" not in model:                # the segmenter's own tuning calls
            return real_segments(inp, model)
        return [dict(x, id=x["id"] + 1000 * model["k"]) for x in real_segments(inp, model["real"])]

    monkeypatch.setattr(sg, "segments", fold_segments)
    res = me.evaluate(labels, results, athletes, None)
    assert res["totals"]["segmenter_folds"] == 3 and res["totals"]["dec_claimed"] > 0


def _two_match_world(tmp_path):
    results, labels = tmp_path / "o2", tmp_path / "l2"
    truths = {}
    for stem, n, seed in (("a1", 10, 0), ("b1", 15, 1)):
        _t, truths[stem] = make_match(results, labels, stem, n, seed=seed)
    athletes = tmp_path / "ath2.json"
    athletes.write_text(json.dumps({"schema": 1, "matches": {}}), encoding="utf-8")
    return results, labels, athletes, truths


def test_threshold_nested_precision(tmp_path, monkeypatch):
    """A threshold chosen on the very rows it is scored on cannot fail the bar; the nested one can."""
    results, labels, athletes, truths = _two_match_world(tmp_path)

    def fake_decode(out_dir, run, segments, auto, labels_doc, athletes_data, model, labels_dir, **kw):
        win = truths[run.stem]
        probs = {}
        for i, seg in enumerate(segments):
            right = win[i]
            wrong = "lower" if right == "upper" else "upper"
            if run.stem == "a1":                       # confident and always right
                side, conf = right, 0.95
            else:                                       # unsure, and wrong on the last five
                side, conf = (right if i < 10 else wrong), 0.7
            probs[seg["id"]] = {side: conf, ("lower" if side == "upper" else "upper"): 1 - conf, "none": 0.0}
        return probs, True

    monkeypatch.setattr(me.ob, "_decode_match", fake_decode)
    t = me.evaluate(labels, results, athletes, None)["totals"]
    assert t["threshold"] == pytest.approx(0.95)       # the shipped one, chosen on every fold
    # b1 is scored at a1's 0.95 (it never reaches it); a1 would need b1's threshold, which does not exist
    assert t["dec_claimed"] == 0 and t["dec_precision"] is None
    assert me.decide(t)["decoder"][1]["code"] == "no_claims"
    assert me.fold_thresholds({"x": [(_p("upper", 0.9), "upper")] * 5}) == {"x": None}   # no other fold


def test_lomo_groups_split_match_parts(world, monkeypatch):
    results, labels, athletes = world
    athletes.write_text(json.dumps({"schema_version": 1, "squads": {}, "athletes": {}, "matches": {
        "m1": {"upper": {"name": "A"}, "lower": {"name": "B"}},
        "m2": {"part_of": "m1", "sides_swapped": False}}}), encoding="utf-8")
    seg_calls, unclamped = [], {}
    real_seg_fit, real_decode = sg.fit, me.ob._decode_match

    def seg_fit(matches):
        seg_calls.append(sorted(len(ends) for _inp, _doc, ends in matches))
        return real_seg_fit(matches)

    def decode_match(out_dir, run, *a, **kw):
        unclamped[run.stem] = set(kw.get("unclamped") or ())
        return real_decode(out_dir, run, *a, **kw)

    monkeypatch.setattr(sg, "fit", seg_fit)
    monkeypatch.setattr(me.ob, "_decode_match", decode_match)
    me.evaluate(labels, results, athletes, None)
    assert sorted(seg_calls) == [[6, 7], [8]]          # m1 + m2 are held out together
    assert unclamped["m1"] == unclamped["m2"] == {"m1", "m2"} and unclamped["m3"] == {"m3"}


# ---- card status -----------------------------------------------------------------------------

def test_status_gates_and_history(world, tmp_path):
    results, labels, _ = world
    weights = tmp_path / "weights"
    st = me.status(labels, results, weights)
    assert st["counts"] == {"matches": 0, "points": 21, "new_points": 21}   # 6 + 7 + 8; none has 20 points
    assert st["can_retrain"] is False
    assert st["missing"] == [{"code": "matches", "need": 2, "have": 0}]
    assert st["live"] == {"segmenter": None, "decoder": None} and st["can_rollback"] is False

    big = tmp_path / "big"
    for i in range(2):
        make_match(big / "outputs", big / "labels", f"b{i}", 21, seed=i)
    st = me.status(big / "labels", big / "outputs", weights)
    assert st["counts"] == {"matches": 2, "points": 42, "new_points": 42} and st["can_retrain"] is True
    assert st["missing"] == []
    store.append_history(me.RALLY, weights, {"kept": False, "points": 30, "utc": "2026-10-28T00:00:00Z"})
    st = me.status(big / "labels", big / "outputs", weights)
    assert st["counts"]["new_points"] == 12 and st["can_retrain"] is False
    assert st["missing"] == [{"code": "new_points", "need": 20, "have": 12}]
    assert len(st["history"]) == 1


def test_status_names_the_live_models_and_what_beat_swing(tmp_path):
    weights = tmp_path / "weights"
    weights.mkdir()
    seg = sg.train([({"gap_sec": 1.0}, 0), ({"gap_sec": 9.0}, 1)], features=["gap_sec"])
    seg["heldout"] = {"f1": 0.7, "swing_f1": 0.5}
    (weights / me.SEGMENTER_FILE).write_text(json.dumps(seg), encoding="utf-8")
    st = me.status(tmp_path / "none", tmp_path / "none", weights)
    assert st["live"]["segmenter"]["version"] == seg["version"]
    assert st["live"]["segmenter"]["heldout"] == {"f1": 0.7, "swing_f1": 0.5}
    assert st["live"]["decoder"] is None


def test_describe_codes_in_english():
    assert "0.45" in me.describe({"code": "lower_f1", "candidate": 0.45, "swing": 0.53})
    assert "precision" in me.describe({"code": "precision_unreachable", "target": 0.9})
    assert "more" in me.describe({"code": "new_points", "need": 20, "have": 5})


# ---- the script ------------------------------------------------------------------------------

def _script():
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location(
        "score_rally_model", Path(__file__).resolve().parents[1] / "scripts" / "score_rally_model.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_script_prints_the_table_and_decisions(world, tmp_path, capsys):
    results, labels, athletes = world
    empty = tmp_path / "empty-weights"
    code = _script().main(["--labels-dir", str(labels), "--results-dir", str(results),
                           "--athletes", str(athletes), "--weights-dir", str(empty)])
    out = capsys.readouterr().out
    assert code == 0 and "POOLED" in out and "m1" in out and "m3" in out
    assert "segmenter: KEEP" in out and "decoder: KEEP" in out and "live now: segmenter none" in out
    assert not empty.exists()                                  # read-only: nothing written
    code = _script().main(["--labels-dir", str(labels), "--results-dir", str(results),
                           "--athletes", str(athletes), "--weights-dir", str(empty), "--json"])
    doc = json.loads(capsys.readouterr().out)
    assert code == 0 and doc["totals"]["points"] == 21 and doc["decision"]["decoder"]["kept"] is True


def test_script_without_labelled_matches_says_so(tmp_path, capsys):
    code = _script().main(["--labels-dir", str(tmp_path / "l"), "--results-dir", str(tmp_path / "o"),
                           "--athletes", str(tmp_path / "a.json"), "--weights-dir", str(tmp_path / "w")])
    assert code == 1 and "no labelled match" in capsys.readouterr().out


def test_status_baseline_skips_rollback_entries(world, tmp_path):
    results, labels, _ = world
    weights = tmp_path / "weights"
    big = tmp_path / "big"
    for i in range(2):
        make_match(big / "outputs", big / "labels", f"b{i}", 21, seed=i)
    store.append_history(me.RALLY, weights, {"kept": True, "points": 30, "utc": "2026-10-28T00:00:00Z"})
    store.append_history(me.RALLY, weights, {"kept": True, "rollback": True, "points": 5,
                                             "reason": {"code": "rolled_back"}, "utc": "2026-10-28T01:00:00Z"})
    st = me.status(big / "labels", big / "outputs", weights)
    assert st["counts"]["new_points"] == 12            # still measured from the 30 of the last real retrain
