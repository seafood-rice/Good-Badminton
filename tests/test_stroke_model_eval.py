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
    assert keep is False and "0.79" in reason
    keep, reason = me.decide(_metrics(0.95, 0.2), _metrics(0.5))
    assert keep is False and "unknown" in reason
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
    # the model it replaced is the newest history entry now, so a second rollback undoes it
    assert me.rollback(w) is True
    assert (w / "stroke-classifier.pt").read_bytes() == b"pt-v1"
    assert me.history(w)[-1]["rollback"] is True


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
    assert st["live"] == {"version": None, "metrics": None, "bar_met": False, "bar_reasons": ["no model"]}
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
