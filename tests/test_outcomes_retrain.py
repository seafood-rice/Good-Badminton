"""One rally retrain run: measure leave-one-match-out, keep or reject each model, promote with history."""
import json

import pytest

from badminton_analysis.outcomes import decoder as dec
from badminton_analysis.outcomes import model_eval as me
from badminton_analysis.outcomes import retrain as rt
from badminton_analysis.outcomes import segmenter as sg
from tests.rally_model_fixtures import make_match

COUNTS = {"m1": 6, "m2": 7, "m3": 8}


@pytest.fixture
def world(tmp_path):
    results, labels = tmp_path / "outputs", tmp_path / "labels"
    for i, (stem, n) in enumerate(COUNTS.items()):
        make_match(results, labels, stem, n, seed=i, offset=5.0 + i)
    athletes = tmp_path / "athletes.json"
    athletes.write_text(json.dumps({"schema": 1, "matches": {}}), encoding="utf-8")
    return results, labels, athletes, tmp_path / "weights"


def final_line(capsys):
    out = capsys.readouterr().out
    return out, json.loads(out.strip().splitlines()[-1])


def force(monkeypatch, seg, dm):
    real = me.decide

    def decide(totals):
        out = real(totals)
        if seg is not None:
            out["segmenter"] = (seg, {"code": "f1_ok"} if seg else {"code": "lower_f1", "candidate": 0.4, "swing": 0.5})
        if dm is not None:
            out["decoder"] = (dm, {"code": "decoder_ok"} if dm else {"code": "fewer_correct", "correct": 1, "rules": 2})
        return out

    monkeypatch.setattr(me, "decide", decide)


def test_both_kept_are_promoted_with_history(world, capsys):
    results, labels, athletes, weights = world
    assert rt.run(labels, results, athletes, weights) == 0
    out, last = final_line(capsys)
    assert rt.PROMOTE_PHASE in out
    assert last["segmenter"]["kept"] and last["decoder"]["kept"]
    assert last["segmenter"]["reason"]["code"] == "f1_ok" and last["totals"]["points"] == 21
    seg, dm = sg.load(weights / me.SEGMENTER_FILE), dec.load(weights / me.DECODER_FILE)
    assert seg["heldout"]["f1"] > 0.9 and seg["heldout"]["swing_f1"] == 0.0
    assert dm["claim_threshold"] == pytest.approx(last["totals"]["threshold"])
    assert dm["heldout"]["correct"] == 21 and dm["version"]
    entry = me.history(weights)[-1]
    assert entry["kept"] is True and entry["points"] == 21 and entry["totals"]["seg_f1"] > 0.9
    assert entry["models"]["segmenter"]["kept"] and entry["models"]["decoder"]["kept"]
    assert entry["live_after"]["segmenter"]["version"] == seg["version"]
    assert entry["live_after"]["decoder"]["version"] == dm["version"]
    assert not list((weights / "candidates").glob("*"))


def test_both_rejected_leaves_the_live_behaviour_alone(world, capsys, monkeypatch):
    results, labels, athletes, weights = world
    force(monkeypatch, False, False)
    assert rt.run(labels, results, athletes, weights) == 0
    out, last = final_line(capsys)
    assert rt.PROMOTE_PHASE not in out
    assert last["segmenter"]["kept"] is False and last["decoder"]["kept"] is False
    assert last["segmenter"]["reason"]["code"] == "lower_f1"
    assert not (weights / me.SEGMENTER_FILE).exists() and not (weights / me.DECODER_FILE).exists()
    entry = me.history(weights)[-1]
    assert entry["kept"] is False and entry["points"] == 21 and entry["totals"]["swing_f1"] == 0.0
    assert entry["live_after"] == {"segmenter": None, "decoder": None}
    assert entry["models"]["decoder"]["reason"]["code"] == "fewer_correct"


def test_one_model_kept_and_the_other_not(world, capsys, monkeypatch):
    results, labels, athletes, weights = world
    force(monkeypatch, True, False)
    assert rt.run(labels, results, athletes, weights) == 0
    final_line(capsys)
    assert (weights / me.SEGMENTER_FILE).is_file() and not (weights / me.DECODER_FILE).exists()
    entry = me.history(weights)[-1]
    assert entry["kept"] is True
    assert entry["models"]["segmenter"]["kept"] is True and entry["models"]["decoder"]["kept"] is False
    assert entry["live_after"]["decoder"] is None and entry["live_after"]["segmenter"]["version"]


def test_unreachable_precision_rejects_the_decoder(world, capsys, monkeypatch):
    results, labels, athletes, weights = world
    monkeypatch.setattr(me, "choose_threshold", lambda *a, **k: None)
    assert rt.run(labels, results, athletes, weights) == 0
    _out, last = final_line(capsys)
    assert last["decoder"] == {"kept": False, "reason": {"code": "precision_unreachable", "target": 0.9}}
    assert not (weights / me.DECODER_FILE).exists()


def test_a_second_kept_run_can_be_rolled_back(world, capsys):
    results, labels, athletes, weights = world
    rt.run(labels, results, athletes, weights)
    first = sg.load(weights / me.SEGMENTER_FILE)["version"]
    import time
    time.sleep(1.1)       # model versions are second-resolution stamps
    rt.run(labels, results, athletes, weights)
    capsys.readouterr()
    assert sg.load(weights / me.SEGMENTER_FILE)["version"] != first
    assert me.can_rollback(weights) is True
    assert me.rollback(weights, points=21) is True
    assert sg.load(weights / me.SEGMENTER_FILE)["version"] == first
    entry = me.history(weights)[-1]
    assert entry["rollback"] is True and entry["points"] == 21
    assert entry["live_after"]["segmenter"]["version"] == first


def test_fewer_than_two_labelled_matches_makes_no_decision(world, capsys):
    results, labels, athletes, weights = world
    for stem in ("m2", "m3"):
        (labels / f"{stem}.json").unlink()
    assert rt.run(labels, results, athletes, weights) == 1
    _out, last = final_line(capsys)
    assert last["segmenter"]["reason"] == {"code": "not_enough_matches"} and last["segmenter"]["kept"] is False
    assert me.history(weights) == []


def test_main_prints_the_final_line_and_survives_failures(world, capsys, monkeypatch):
    results, labels, athletes, weights = world
    argv = ["--labels-dir", str(labels), "--results-dir", str(results), "--athletes", str(athletes),
            "--weights-dir", str(weights)]
    assert rt.main(argv) == 0
    _out, last = final_line(capsys)
    assert last["segmenter"]["kept"] is True

    def boom(*a, **k):
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(me, "evaluate", boom)
    assert rt.main(argv) == 1
    _out, last = final_line(capsys)
    assert last["segmenter"]["reason"]["code"] == "retrain_failed" and "disk on fire" in last["segmenter"]["reason"]["detail"]
