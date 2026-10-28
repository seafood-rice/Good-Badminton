import json
import os
import subprocess
import sys

from badminton_analysis.stroke import model_eval as me
from badminton_analysis.stroke import stroke_classifier as sc
from badminton_analysis.stroke.hit_signals import save_signals
from tests.test_stroke_windows import _sig

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _write(path, doc):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc), encoding="utf-8")


def _result(results, stem, frames=(60, 120, 180)):
    d = results / stem
    d.mkdir(parents=True)
    _write(d / "hits.json", {"fps": 30.0, "hits": [{"frame": f, "hitter": "lower"} for f in frames]})
    save_signals(d / "hit_signals.npz", _sig())
    return d


def _labels(labels_dir, stem, rows, split="test"):
    _write(labels_dir / f"{stem}.json", {"schema": 1, "revision": 1, "split": split, "labels": [
        {"time_sec": t, "hitter": "lower", "stroke": s, "kind": k} for t, s, k in rows]})


def _metrics(acc, unknown=0.05):
    return {"accuracy": acc, "unknown_rate": unknown}


def _model(weights, created="2026-01-01T00:00:00Z", train=()):
    weights.parent.mkdir(parents=True, exist_ok=True)
    weights.write_bytes(b"pt-" + created.encode())
    weights.with_suffix(".json").write_text(
        json.dumps({"created_utc": created, "train_matches": list(train), "val_matches": []}),
        encoding="utf-8")


def _cand(w, name="v1"):
    c = w / "candidates" / f"{name}.pt"
    _model(c, name)
    return c


def test_decide_keep_reject_and_no_live():
    assert me.decide(_metrics(0.8), _metrics(0.8))[0] is True
    keep, reason = me.decide(_metrics(0.79), _metrics(0.8))
    assert keep is False and reason["candidate"] == 0.79
    keep, reason = me.decide(_metrics(0.95, 0.2), _metrics(0.5))
    assert keep is False and reason["code"] == "unknown_rate"
    assert me.decide(_metrics(0.5, 0.15), None)[0] is True
    assert me.decide(_metrics(0.5, 0.16), None)[0] is False
    assert me.decide(_metrics(None, None), None)[0] is False


def test_promote_moves_pair_and_caps_history(tmp_path):
    w = tmp_path / "weights"
    _model(w / "stroke-classifier.pt", "v0")
    for k in range(1, 8):
        cand = _cand(w, f"v{k}")
        me.promote(w, cand, {"candidate": f"v{k}"})
        assert not cand.exists() and not cand.with_suffix(".json").exists()
    assert (w / "stroke-classifier.pt").read_bytes() == b"pt-v7"
    assert json.loads((w / "stroke-classifier.json").read_text())["created_utc"] == "v7"
    dirs = sorted((w / "history").iterdir())
    assert len(dirs) == 5
    assert (dirs[-1] / "stroke-classifier.pt").read_bytes() == b"pt-v6"
    assert (dirs[-1] / "stroke-classifier.json").is_file()
    assert (dirs[0] / "stroke-classifier.pt").read_bytes() == b"pt-v2"
    hist = me.history(w)
    assert len(hist) == 7 and all(e["kept"] and e["utc"] for e in hist)


def test_promote_without_live_model(tmp_path):
    w = tmp_path / "weights"
    me.promote(w, _cand(w), {})
    assert (w / "stroke-classifier.pt").read_bytes() == b"pt-v1"
    assert not (w / "history").exists()


def test_reject_deletes_candidate_and_records(tmp_path):
    w = tmp_path / "weights"
    cand = _cand(w)
    me.reject(w, cand, {"reason": "worse", "test_videos": ["A"]})
    assert not cand.exists() and not cand.with_suffix(".json").exists()
    assert me.history(w)[0]["kept"] is False


def test_rollback_restores_and_is_false_without_history(tmp_path):
    w = tmp_path / "weights"
    assert me.rollback(w) is False
    _model(w / "stroke-classifier.pt", "v0")
    me.promote(w, _cand(w), {})
    assert me.rollback(w) is True
    assert (w / "stroke-classifier.pt").read_bytes() == b"pt-v0"
    assert json.loads((w / "stroke-classifier.json").read_text())["created_utc"] == "v0"
    assert me.history(w)[-1]["rollback"] is True
    # the model rolled away is marked retired: a second rollback has nothing older to go to
    assert me.rollback(w) is False
    assert (w / "stroke-classifier.pt").read_bytes() == b"pt-v0"


def test_two_consecutive_rollbacks_go_one_model_further_back(tmp_path):
    w = tmp_path / "weights"
    _model(w / "stroke-classifier.pt", "v0")
    me.promote(w, _cand(w, "v1"), {})
    me.promote(w, _cand(w, "v2"), {})
    assert me.rollback(w) is True
    assert (w / "stroke-classifier.pt").read_bytes() == b"pt-v1"
    assert me.rollback(w) is True
    assert (w / "stroke-classifier.pt").read_bytes() == b"pt-v0"
    assert me.rollback(w) is False
    # a fresh promote makes the model it replaced available again
    me.promote(w, _cand(w, "v3"), {})
    assert me.rollback(w) is True
    assert (w / "stroke-classifier.pt").read_bytes() == b"pt-v0"


def test_history_dir_survives_a_stray_entry_and_a_backwards_clock(tmp_path):
    w = tmp_path / "weights"
    (w / "history").mkdir(parents=True)
    (w / "history" / "zzz-notes").mkdir()
    (w / "history" / "readme.txt").write_text("x")
    assert me._new_history_dir(w).name != "zzz-notes"
    future = w / "history" / "29990101T000000000Z"
    future.mkdir()
    for _ in range(3):
        d = me._new_history_dir(w)
        assert d.name > future.name
        future = d


def test_history_entries_carry_live_after_and_train_labels(tmp_path):
    w = tmp_path / "weights"
    _model(w / "stroke-classifier.pt", "v0")
    cand_m = {**_metrics(0.9, 0.05), "recall": {}}
    kept = me.promote(w, _cand(w, "v1"), {"candidate": cand_m, "live": None, "train_labels": 70})
    assert kept["live_after"] == {"version": "v1", "accuracy": 0.9, "unknown_rate": 0.05, "bar_met": True}
    assert kept["bar_met"] is True
    live_m = {**_metrics(0.92, 0.04), "recall": {}}
    rej = me.reject(w, _cand(w, "v2"), {"candidate": _metrics(0.5), "live": live_m, "train_labels": 80})
    assert rej["live_after"] == {"version": "v1", "accuracy": 0.92, "unknown_rate": 0.04, "bar_met": True}
    me.promote(w, _cand(w, "v3"), {"candidate": _metrics(0.8, 0.1), "live": live_m, "train_labels": 90})
    assert me.rollback(w, train_labels=95) is True
    last = me.history(w)[-1]
    # restored v1: its scores come from the entry that promoted it
    assert last["live_after"] == {"version": "v1", "accuracy": 0.9, "unknown_rate": 0.05, "bar_met": True}
    assert last["train_labels"] == 95
    # a restored model nothing recorded scores for gets empty values
    w2 = tmp_path / "w2"
    _model(w2 / "stroke-classifier.pt", "old")
    me.promote(w2, _cand(w2, "n1"), {})
    me.rollback(w2)
    assert me.history(w2)[-1]["live_after"] == {"version": "old", "accuracy": None,
                                                "unknown_rate": None, "bar_met": None}


def test_new_train_uses_the_newest_entry_with_train_labels(tmp_path):
    res, lab, w = tmp_path / "res", tmp_path / "lab", tmp_path / "weights"
    for stem in ("A", "B"):
        _result(res, stem)
    _labels(lab, "A", [(2.0, "lift", "label"), (4.0, "lift", "added")], split="train")
    _labels(lab, "B", [(2.0, "lift", "label")], split="train")       # 3 train labels
    me.promote(w, _cand(w), {"train_labels": 1})
    assert me.status(lab, res, w)["counts"]["new_train"] == 2
    me.reject(w, _cand(w, "v2"), {"train_labels": 3})                  # a rejected run counts
    assert me.status(lab, res, w)["counts"]["new_train"] == 0
    me.promote(w, _cand(w, "v3"), {"train_labels": 3})
    me.rollback(w, train_labels=2)                                     # so does a rollback
    assert me.status(lab, res, w)["counts"]["new_train"] == 1


def test_card_strings_are_codes_with_numbers(tmp_path):
    res, lab, w = tmp_path / "res", tmp_path / "lab", tmp_path / "weights"
    _result(res, "A")
    _labels(lab, "A", [(2.0, "lift", "label")], split="train")
    st = me.status(lab, res, w)
    assert st["live"]["bar_reasons"] == [{"code": "no_model"}]
    assert {"code": "train_labels", "need": 50, "have": 1} in st["missing"]
    assert {"code": "test_shots", "need": 20, "have": 0} in st["missing"]
    keep, reason = me.decide(_metrics(0.5), _metrics(0.8))
    assert keep is False and reason == {"code": "lower_accuracy", "candidate": 0.5, "live": 0.8}
    assert me.decide(_metrics(0.9, 0.3), None)[1] == {"code": "unknown_rate", "value": 0.3, "bar": 0.15}
    assert me.decide(_metrics(None, None), None)[1] == {"code": "no_confident_shot"}
    assert me.decide(_metrics(0.9), None)[1]["code"] == "first_model"
    assert me.decide(_metrics(0.9), _metrics(0.8))[1]["code"] == "better_or_equal"
    assert me.describe({"code": "lower_accuracy", "candidate": 0.5, "live": 0.8}) == \
        "candidate accuracy 0.5 < live 0.8"
    assert "50" in me.describe({"code": "train_labels", "need": 50, "have": 1})
    assert me.describe("plain text") == "plain text"


def test_split_locked_from_sidecar_and_history(tmp_path):
    w = tmp_path / "weights"
    assert me.split_locked("A", w) is False
    _model(w / "stroke-classifier.pt", train=["owner:A"])
    assert me.split_locked("A", w) is True and me.split_locked("B", w) is False
    me.reject(w, w / "none.pt", {"test_videos": ["B"]})
    assert me.split_locked("B", w) is True and me.split_locked("C", w) is False


def test_test_shots_exclude_added_unsure_and_train_split(tmp_path):
    res, lab = tmp_path / "res", tmp_path / "lab"
    _result(res, "T")
    _result(res, "R")
    _labels(lab, "T", [(2.0, "smash", "label"), (4.0, None, "unsure"), (6.0, "lift", "added"),
                       (9.0, "lift", "label")])        # 9.0 s matches no hit
    _labels(lab, "R", [(2.0, "smash", "label")], split="train")
    shots = me.test_shots(lab, res)
    assert [(s, [h["frame"] for h in hits], truth) for s, hits, truth in shots] == [("T", [60], ["smash"])]


def test_score_weights_uses_same_shots_for_any_weights(tmp_path, monkeypatch):
    res, lab = tmp_path / "res", tmp_path / "lab"
    _result(res, "T")
    _labels(lab, "T", [(2.0, "smash", "label"), (4.0, "lift", "label")])
    shots = me.test_shots(lab, res)
    seen = []

    def fake_load(path):
        return str(path), {"unknown_threshold": 0.5}, {}

    def fake_classify(sig, hits, model, meta):
        seen.append([h["frame"] for h in hits])
        right = model.endswith("good.pt")
        return [{"frame": h["frame"], "stroke": ("smash" if h["frame"] == 60 else "lift") if right
                 else "unknown"} for h in hits]

    monkeypatch.setattr(sc, "load_classifier", fake_load)
    monkeypatch.setattr(sc, "classify_hits", fake_classify)
    good = me.score_weights("good.pt", shots, res)
    bad = me.score_weights("bad.pt", shots, res)
    assert good["accuracy"] == 1.0 and good["matched"] == 2 and bad["unknown_rate"] == 1.0
    assert seen == [[60, 120], [60, 120]]


def test_status_and_train_args(tmp_path):
    res, lab, w = tmp_path / "res", tmp_path / "lab", tmp_path / "weights"
    for stem in ("T", "A", "B"):
        _result(res, stem)
    _labels(lab, "T", [(2.0, "smash", "label")])
    _labels(lab, "A", [(2.0, "lift", "label"), (4.0, "lift", "added")], split="train")
    _labels(lab, "B", [(2.0, "lift", "label")], split="train")
    st = me.status(lab, res, w)
    assert st["live"] == {"version": None, "metrics": None, "bar_met": False, "bar_reasons": [{"code": "no_model"}]}
    assert st["counts"] == {"train": 3, "test": 1, "new_train": 3}
    assert st["can_retrain"] is False and len(st["missing"]) == 2 and st["history"] == []
    # no data/hit_dataset: calibrate on the train video with the most labels
    root = tmp_path / "root"
    out = tmp_path / "cand.pt"
    args = me.train_args(root, lab, res, out)
    assert args[args.index("--calib-video") + 1] == "A" and "--dataset" not in args
    assert args[args.index("--stroke-labels") + 1] == str(lab) and args[args.index("--out") + 1] == str(out)
    (root / "data" / "hit_dataset").mkdir(parents=True)
    args = me.train_args(root, lab, res, out)
    assert args[:4] == ["--dataset", str(root / "data" / "hit_dataset"), "--val-match", me.SINDHU_VAL]
    assert "--calib-video" not in args and "--stroke-labels" in args
    # a kept run lowers new_train
    me.promote(w, _cand(w), {"train_labels": 2})
    assert me.status(lab, res, w)["counts"]["new_train"] == 1


def test_score_script_no_test_shots(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    r = subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "score_stroke_model.py"),
                        "--labels-dir", str(empty), "--results-dir", str(tmp_path),
                        "--weights", str(tmp_path / "none.pt")],
                       capture_output=True, text=True, env=dict(os.environ, PYTHONUTF8="1"))
    assert r.returncode == 1 and "no test shots" in r.stdout


def test_describe_retrain_failed_keeps_detail():
    text = me.describe({"code": "retrain_failed", "detail": "ZeroDivisionError: division by zero"})
    assert "retrain failed" in text and "ZeroDivisionError: division by zero" in text


def _half_promote(w):
    """What a kill between promote's two moves leaves: live retired into history, candidate still staged."""
    _model(w / "stroke-classifier.pt", "v0")
    cand = _cand(w, "v1")
    retired = me._new_history_dir(w)
    me._move_pair(w / "stroke-classifier.pt", retired / "stroke-classifier.pt")
    return cand, retired


def test_status_restores_a_half_finished_promote(tmp_path):
    w = tmp_path / "weights"
    cand, retired = _half_promote(w)
    assert not (w / "stroke-classifier.pt").exists()
    me.status(tmp_path / "lab", tmp_path / "res", w)
    assert (w / "stroke-classifier.pt").read_bytes() == b"pt-v0"
    assert (w / "stroke-classifier.json").is_file() and not retired.exists()
    assert cand.exists()                      # the candidate is left for the caller to delete


def test_promote_and_rollback_recover_first(tmp_path):
    w = tmp_path / "weights"
    cand, _ = _half_promote(w)
    me.promote(w, cand, {})
    assert (w / "stroke-classifier.pt").read_bytes() == b"pt-v1"
    assert (sorted((w / "history").iterdir())[-1] / "stroke-classifier.pt").read_bytes() == b"pt-v0"
    # half rollback: the old model is staged, the live one already retired
    newest = sorted((w / "history").iterdir())[-1]
    staged = w / f".rollback-{newest.name}.pt"
    me._move_pair(newest / "stroke-classifier.pt", staged)
    retired = me._new_history_dir(w)
    me._move_pair(w / "stroke-classifier.pt", retired / "stroke-classifier.pt")
    assert me.rollback(w) is False        # the interrupted rollback was completed
    assert (w / "stroke-classifier.pt").read_bytes() == b"pt-v0"
    assert not staged.exists()
