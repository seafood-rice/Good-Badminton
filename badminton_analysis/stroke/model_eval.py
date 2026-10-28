"""Score, compare and promote stroke models on the owner's test split (spec 2026-10-28 section 6).

``test_shots`` lists the owner-labelled hits of every test-split video, ``score_weights``
runs any weights file over exactly those shots, ``decide`` applies the keep rule, and
``promote`` / ``rollback`` swap the live model with ``os.replace`` (the ``.pt`` and its
``.json`` sidecar always move together). ``weights_dir`` is always a parameter. This
module takes no lock: the caller serialises retrain, promote and rollback.
"""
import json
import os
import shutil
import time
from pathlib import Path

from ..outcomes.labels import write_json_atomic
from . import owner_labels
from . import stroke_classifier as sc
from .hit_signals import load_signals
from .stroke_metrics import BAR, check_bar, score
from .stroke_model import meta_path

SINDHU_VAL = "Pusarla_V._Sindhu_Pornpawee_Chochuwong_HSBC_BWF_WORLD_TOUR_FINALS_2020_QuarterFinals"
LIVE_NAME = "stroke-classifier.pt"
HISTORY_FILE = "stroke-model-history.json"
HISTORY_DIR = "history"
KEEP_REPLACED = 5
MIN_NEW_TRAIN = 50
MIN_TEST_SHOTS = 20


def _json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError, RecursionError, MemoryError):
        return None


def _hits(result_dir):
    doc = _json(Path(result_dir) / "hits.json")
    if not isinstance(doc, dict) or not isinstance(doc.get("hits"), list):
        return []
    return [{"frame": int(h["frame"]), "hitter": h["hitter"], "confidence": h.get("confidence")}
            for h in doc["hits"]
            if isinstance(h, dict) and isinstance(h.get("frame"), int)
            and not isinstance(h.get("frame"), bool) and h.get("hitter")]


def _label_docs(labels_dir, split):
    """``[(stem, doc)]`` of the readable label files with this split, sorted by stem."""
    out = []
    d = Path(labels_dir)
    if not d.is_dir():
        return out
    for path in sorted(d.glob("*.json")):
        doc = owner_labels.load(path)
        if doc is not None and doc["split"] == split:
            out.append((path.stem, doc))
    return out


def _trainable(doc):
    return sum(1 for lb in doc["labels"] if lb["kind"] in ("label", "added"))


def test_shots(labels_dir, results_dir):
    """``[(stem, hits, truth)]``: detected hits with an owner ``label`` in the test split.

    ``truth[i]`` is the owner's stroke for ``hits[i]``. ``added`` rows (missed hits) and
    ``unsure`` / ``not_hit`` rows are left out; videos with no shot are skipped.
    """
    shots = []
    for stem, doc in _label_docs(labels_dir, "test"):
        result_dir = Path(results_dir) / stem
        fps = owner_labels._fps(result_dir)
        hits = _hits(result_dir)
        if fps is None or not hits:
            continue
        by_index, _, _ = owner_labels.attach(doc["labels"], hits, fps)
        picked = sorted(i for i, lb in by_index.items() if lb["kind"] == "label")
        if picked:
            shots.append((stem, [hits[i] for i in picked], [by_index[i]["stroke"] for i in picked]))
    # `test_shots` is a helper, not a pytest test
    return shots


test_shots.__test__ = False


def score_weights(weights, shots, results_dir):
    """``stroke_metrics.score`` of ``weights`` on ``shots`` (the same shots for any weights)."""
    model, meta, _ = sc.load_classifier(weights)
    pairs = []
    for stem, hits, truth in shots:
        path = Path(results_dir) / stem / "hit_signals.npz"
        if not path.is_file():
            continue
        labels = sc.classify_hits(load_signals(path), hits, model, meta)
        predicted = {l["frame"]: l["stroke"] for l in labels}
        for hit, true in zip(hits, truth):
            if hit["frame"] in predicted:
                pairs.append((true, predicted[hit["frame"]]))
    return score(pairs)


def decide(candidate_m, live_m):
    """``(keep, reason)`` per the keep rule (spec 6.3)."""
    unknown, acc = candidate_m.get("unknown_rate"), candidate_m.get("accuracy")
    if acc is None or unknown is None:
        return False, "candidate scored no confident test shot"
    if unknown > BAR["unknown"]:
        return False, f"candidate unknown rate {unknown} > {BAR['unknown']}"
    if live_m is None:
        return True, f"no live model; candidate unknown rate {unknown} <= {BAR['unknown']}"
    live_acc = live_m.get("accuracy") or 0.0
    if acc < live_acc:
        return False, f"candidate accuracy {acc} < live {live_acc}"
    return True, f"candidate accuracy {acc} >= live {live_acc}, unknown rate {unknown}"


# ---------------------------------------------------------------- history and swaps

def _history_path(weights_dir):
    return Path(weights_dir) / HISTORY_FILE


def history(weights_dir):
    doc = _json(_history_path(weights_dir))
    return [e for e in doc if isinstance(e, dict)] if isinstance(doc, list) else []


def _append_history(weights_dir, entry):
    write_json_atomic(_history_path(weights_dir), history(weights_dir) + [entry])


def _utc():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _pair(pt):
    pt = Path(pt)
    return pt, Path(meta_path(pt))


def _history_dirs(weights_dir):
    d = Path(weights_dir) / HISTORY_DIR
    return sorted(p for p in d.iterdir() if p.is_dir() and (p / LIVE_NAME).is_file()) if d.is_dir() else []


def _new_history_dir(weights_dir):
    base = Path(weights_dir) / HISTORY_DIR
    base.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%dT%H%M%S", time.gmtime())
    n = 0
    while True:
        path = base / f"{stamp}{n:03d}Z"
        if not path.exists() and all(p.name < path.name for p in base.iterdir()):
            path.mkdir()
            return path
        n += 1


def _move_pair(src_pt, dst_pt):
    """Move a model ``.pt`` and its sidecar; the sidecar goes first so a failure undoes cleanly."""
    (src, src_meta), (dst, dst_meta) = _pair(src_pt), _pair(dst_pt)
    moved = []
    try:
        if src_meta.is_file():
            os.replace(src_meta, dst_meta)
            moved.append((dst_meta, src_meta))
        os.replace(src, dst)
    except OSError:
        for back_from, back_to in moved:
            os.replace(back_from, back_to)
        raise


def _install(weights_dir, src_pt):
    """Retire the live model into a new history dir and put ``src_pt`` in its place."""
    weights_dir = Path(weights_dir)
    weights_dir.mkdir(parents=True, exist_ok=True)
    live = weights_dir / LIVE_NAME
    retired = None
    if live.is_file():
        retired = _new_history_dir(weights_dir)
        _move_pair(live, retired / LIVE_NAME)
    try:
        _move_pair(src_pt, live)
    except OSError:
        if retired is not None:
            _move_pair(retired / LIVE_NAME, live)
            shutil.rmtree(retired, ignore_errors=True)
        raise
    return retired


def _prune(weights_dir):
    dirs = _history_dirs(weights_dir)
    for old in dirs[:-KEEP_REPLACED]:
        shutil.rmtree(old, ignore_errors=True)


def promote(weights_dir, candidate_path, record):
    """Make the candidate the live model; the replaced model goes to ``history/<utc>/``."""
    retired = _install(weights_dir, candidate_path)
    _prune(weights_dir)
    entry = dict(record)
    entry.update({"utc": entry.get("utc") or _utc(), "kept": True,
                  "replaced": retired.name if retired is not None else None})
    _append_history(weights_dir, entry)
    return entry


def reject(weights_dir, candidate_path, record):
    """Delete a candidate that lost and record the numbers."""
    pt, sidecar = _pair(candidate_path)
    for p in (pt, sidecar):
        try:
            p.unlink()
        except FileNotFoundError:
            pass
    entry = dict(record)
    entry.update({"utc": entry.get("utc") or _utc(), "kept": False, "replaced": None})
    _append_history(weights_dir, entry)
    return entry


def rollback(weights_dir):
    """Swap in the newest replaced model; False when there is none."""
    dirs = _history_dirs(weights_dir)
    if not dirs:
        return False
    newest = dirs[-1]
    # stage the old model outside history so the swap cannot see it as the current one
    staged = Path(weights_dir) / f".rollback-{newest.name}.pt"
    _move_pair(newest / LIVE_NAME, staged)
    try:
        retired = _install(weights_dir, staged)
    except OSError:
        _move_pair(staged, newest / LIVE_NAME)
        raise
    shutil.rmtree(newest, ignore_errors=True)
    _prune(weights_dir)
    _append_history(weights_dir, {
        "utc": _utc(), "kept": True, "rollback": True, "reason": "rolled back",
        "replaced": retired.name if retired is not None else None,
        "restored": newest.name})
    return True


def split_locked(stem, weights_dir):
    """True once a training run used this video: the live sidecar names it, or it was scored."""
    sidecar = _json(meta_path(Path(weights_dir) / LIVE_NAME))
    if isinstance(sidecar, dict):
        seen = set(sidecar.get("train_matches") or []) | set(sidecar.get("val_matches") or [])
        if seen & {stem, f"owner:{stem}", f"dji:{stem}"}:
            return True
    return any(stem in (e.get("test_videos") or []) for e in history(weights_dir))


# ---------------------------------------------------------------- card and trainer command

def _live_view(weights_dir, shots, results_dir):
    live = Path(weights_dir) / LIVE_NAME
    if not live.is_file():
        return {"version": None, "metrics": None, "bar_met": False, "bar_reasons": ["no model"]}
    sidecar = _json(meta_path(live)) or {}
    version = sidecar.get("created_utc") or "unversioned"
    try:
        metrics = score_weights(live, shots, results_dir)
    except Exception as exc:        # an unreadable model must not break the card
        return {"version": version, "metrics": None, "bar_met": False,
                "bar_reasons": [f"model unreadable: {exc}"]}
    met, reasons = check_bar(metrics)
    return {"version": version, "metrics": metrics, "bar_met": met, "bar_reasons": reasons}


def status(labels_dir, results_dir, weights_dir):
    shots = test_shots(labels_dir, results_dir)
    train = sum(_trainable(doc) for _, doc in _label_docs(labels_dir, "train"))
    test = sum(len(truth) for _, _, truth in shots)
    hist = history(weights_dir)
    kept = [e for e in hist if e.get("kept")]
    new_train = max(0, train - int((kept[-1].get("train_labels") or 0) if kept else 0))
    missing = []
    if new_train < MIN_NEW_TRAIN:
        missing.append(f"{MIN_NEW_TRAIN - new_train} more train labels (need {MIN_NEW_TRAIN} new)")
    if test < MIN_TEST_SHOTS:
        missing.append(f"{MIN_TEST_SHOTS - test} more labelled test shots (need {MIN_TEST_SHOTS})")
    return {"live": _live_view(weights_dir, shots, results_dir),
            "counts": {"train": train, "test": test, "new_train": new_train},
            "can_retrain": not missing, "missing": missing, "history": hist}


def train_args(project_root, labels_dir, results_dir, out):
    """Arguments for ``scripts/train_stroke_classifier.py`` (Review Focus 4)."""
    args = ["--stroke-labels", str(labels_dir), "--results-dir", str(results_dir), "--out", str(out)]
    dataset = Path(project_root) / "data" / "hit_dataset"
    if dataset.is_dir():
        return ["--dataset", str(dataset), "--val-match", SINDHU_VAL] + args
    docs = [(stem, _trainable(doc)) for stem, doc in _label_docs(labels_dir, "train")]
    docs = [d for d in docs if d[1] > 0]
    if docs:
        best = max(docs, key=lambda d: (d[1], d[0]))[0]
        args += ["--calib-video", best]
    return args
