"""Score, compare and promote stroke models on the owner's test split (spec 2026-10-28 section 6).

``test_shots`` lists the owner-labelled hits of every test-split video, ``score_weights``
runs any weights file over exactly those shots, ``decide`` applies the keep rule, and
``promote`` / ``rollback`` swap the live model with ``os.replace`` through ``models/store.py``
(the ``.pt`` and its ``.json`` sidecar always move together). ``weights_dir`` is always a parameter. This
module takes no lock: the caller serialises retrain, promote and rollback.
"""
import json
import re
from pathlib import Path

from ..models import store
from . import owner_labels
from . import stroke_classifier as sc
from .hit_signals import load_signals
from .stroke_metrics import BAR, check_bar_codes, describe_bar_code, score
from .stroke_model import meta_path

SINDHU_VAL = "Pusarla_V._Sindhu_Pornpawee_Chochuwong_HSBC_BWF_WORLD_TOUR_FINALS_2020_QuarterFinals"
LIVE_NAME = "stroke-classifier.pt"
HISTORY_FILE = "stroke-model-history.json"
HISTORY_DIR = "history"
KEEP_REPLACED = 5
MIN_NEW_TRAIN = 50
MIN_TEST_SHOTS = 20
RETIRED_MARK = ".retired"
SWAP_MARKER = ".swap-in-progress"
_MARKER_GRACE_SEC = 60        # an unreadable marker is only called stale after this long
_STAMP_DIR = re.compile(r"^\d{8}T\d{6}\d{3}Z[a-z]*$")


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
    """``(keep, reason)`` per the keep rule (spec 6.3); ``reason`` is a code dict (see ``describe``)."""
    unknown, acc = candidate_m.get("unknown_rate"), candidate_m.get("accuracy")
    if acc is None or unknown is None:
        return False, {"code": "no_confident_shot"}
    if unknown > BAR["unknown"]:
        return False, {"code": "unknown_rate", "value": unknown, "bar": BAR["unknown"]}
    if live_m is None:
        return True, {"code": "first_model", "unknown": unknown, "bar": BAR["unknown"]}
    live_acc = live_m.get("accuracy") or 0.0
    if acc < live_acc:
        return False, {"code": "lower_accuracy", "candidate": acc, "live": live_acc}
    return True, {"code": "better_or_equal", "candidate": acc, "live": live_acc, "unknown": unknown}


def describe(code):
    """English text of a reason / missing / bar code (CLI output and job messages)."""
    if not isinstance(code, dict):
        return str(code)
    k = code.get("code")
    if k == "no_confident_shot":
        return "candidate scored no confident test shot"
    if k == "unknown_rate":
        return f"candidate unknown rate {code.get('value')} > {code.get('bar')}"
    if k == "first_model":
        return f"no live model; candidate unknown rate {code.get('unknown')} <= {code.get('bar')}"
    if k == "lower_accuracy":
        return f"candidate accuracy {code.get('candidate')} < live {code.get('live')}"
    if k == "better_or_equal":
        return (f"candidate accuracy {code.get('candidate')} >= live {code.get('live')}, "
                f"unknown rate {code.get('unknown')}")
    if k == "no_test_shots":
        return "no labelled test shots"
    if k == "training_failed":
        return f"training failed (exit {code.get('exit')})"
    if k == "retrain_failed":
        return f"retrain failed: {code.get('detail') or 'unknown error'}"
    if k == "rolled_back":
        return "rolled back"
    if k == "train_labels":
        need, have = code.get("need", 0), code.get("have", 0)
        return f"{max(0, need - have)} more train labels (need {need} new)"
    if k == "test_shots":
        need, have = code.get("need", 0), code.get("have", 0)
        return f"{max(0, need - have)} more labelled test shots (need {need})"
    return describe_bar_code(code)


# ---------------------------------------------------------------- history and swaps
# The swap machinery lives in ``models/store.py``; these are the stroke-model wrappers (a ``.pt``
# with its ``.json`` sidecar is a spec with two files).

STROKE = store.ModelSpec("stroke", (LIVE_NAME, Path(meta_path(LIVE_NAME)).name), HISTORY_FILE, None,
                         KEEP_REPLACED, marker=SWAP_MARKER, primary=LIVE_NAME, rollback_to_absent=False)
SIDECAR_NAME = STROKE.files[1]
SwapInProgress = store.SwapInProgress
_read_marker = store.read_marker
_started = store.started
_marker_alive = store.marker_alive


def history(weights_dir):
    return store.history(STROKE, weights_dir)


def _append_history(weights_dir, entry):
    store.append_history(STROKE, weights_dir, entry)


def _utc():
    return store.utc()


def _pair(pt):
    pt = Path(pt)
    return pt, Path(meta_path(pt))


def _files(pt):
    """``{file name: path}`` for a ``.pt`` and its sidecar (None when the sidecar is absent)."""
    pt, sidecar = _pair(pt)
    return {LIVE_NAME: pt, SIDECAR_NAME: sidecar if sidecar.is_file() else None}


def _history_dirs(weights_dir, include_retired=True):
    return store.history_dirs(STROKE, weights_dir, include_retired)


def _new_history_dir(weights_dir):
    return store.new_history_dir(STROKE, weights_dir)


def _move_pair(src_pt, dst_pt):
    """Move a model ``.pt`` and its sidecar; the sidecar goes first so a failure undoes cleanly."""
    (src, src_meta), (dst, dst_meta) = _pair(src_pt), _pair(dst_pt)
    pairs = [(src_meta, dst_meta)] if src_meta.is_file() else []
    store.move_group(pairs + [(src, dst)])


def _install(weights_dir, src_pt):
    """Retire the live model into a new history dir and put ``src_pt`` in its place."""
    return store.install(STROKE, weights_dir, _files(src_pt))


def _install_staged(weights_dir, files):
    return _install(weights_dir, files[LIVE_NAME])      # looked up at call time (tests spy on it)


def _prune(weights_dir):
    store.prune(STROKE, weights_dir)


def _version(pt):
    sidecar = _json(meta_path(Path(pt))) or {}
    return sidecar.get("created_utc") or "unversioned"


def _bar_met(metrics):
    if not isinstance(metrics, dict):
        return None
    return check_bar_codes({"accuracy": metrics.get("accuracy"), "unknown_rate": metrics.get("unknown_rate"),
                            "recall": metrics.get("recall") or {}})[0]


def _live_after(version, metrics):
    """The live model's scores as recorded on a history entry."""
    metrics = metrics if isinstance(metrics, dict) else {}
    return {"version": version, "accuracy": metrics.get("accuracy"),
            "unknown_rate": metrics.get("unknown_rate"),
            "bar_met": _bar_met(metrics) if metrics else None}


def _clear_marker(weights_dir):
    store._clear_marker(STROKE, weights_dir)


def _swap_marker(weights_dir, what):
    """Hold ``weights/.swap-in-progress`` (pid + start time + what) for the length of a swap."""
    return store.swap_marker(STROKE, weights_dir, what)


def recover(weights_dir):
    """Undo a swap whose process was killed half way (see ``store.recover``). Returns True when something moved."""
    return store.recover(STROKE, weights_dir)


def promote(weights_dir, candidate_path, record):
    """Make the candidate the live model; the replaced model goes to ``history/<utc>/``."""
    def annotate(entry):
        live = Path(weights_dir) / LIVE_NAME
        return {"bar_met": _bar_met(entry.get("candidate")),
                "live_after": _live_after(_version(live), entry.get("candidate"))}

    return store.promote(STROKE, weights_dir, _files(candidate_path), record, annotate,
                         install_fn=_install_staged)


def reject(weights_dir, candidate_path, record):
    """Delete a candidate that lost and record the numbers."""
    def annotate(entry):
        live = Path(weights_dir) / LIVE_NAME
        version = _version(live) if live.is_file() else None
        if live.is_file() and not isinstance(entry.get("live"), dict):
            after = _carried_scores(weights_dir, version)     # the live model was not scored this time
        else:
            after = _live_after(version, entry.get("live"))
        return {"bar_met": _bar_met(entry.get("candidate")), "live_after": after}

    pt, sidecar = _pair(candidate_path)
    return store.reject(STROKE, weights_dir, {LIVE_NAME: pt, SIDECAR_NAME: sidecar}, record, annotate)


def _promoted_scores(weights_dir, version):
    """The ``live_after`` of the newest kept entry that made ``version`` live, else empty values."""
    for e in reversed(history(weights_dir)):
        after = e.get("live_after")
        if e.get("kept") and isinstance(after, dict) and after.get("version") == version:
            return {"version": version, "accuracy": after.get("accuracy"),
                    "unknown_rate": after.get("unknown_rate"), "bar_met": after.get("bar_met")}
    return _live_after(version, None)


def _carried_scores(weights_dir, version):
    """The newest recorded scores of ``version`` (any entry kind), else its promoted scores."""
    for e in reversed(history(weights_dir)):
        after = e.get("live_after")
        if isinstance(after, dict) and after.get("version") == version and after.get("accuracy") is not None:
            return {"version": version, "accuracy": after.get("accuracy"),
                    "unknown_rate": after.get("unknown_rate"), "bar_met": after.get("bar_met")}
    return _promoted_scores(weights_dir, version)


def can_rollback(weights_dir):
    """True when an earlier model is kept that no rollback retired."""
    return store.can_rollback(STROKE, weights_dir)


def rollback(weights_dir, train_labels=None):
    """Swap in the newest replaced model that no rollback retired; False when there is none.

    The model rolled away is kept in history marked retired, so a second rollback goes one
    model further back instead of undoing the first.
    """
    return store.rollback(
        STROKE, weights_dir,
        live_after=lambda newest: _promoted_scores(weights_dir, _version(newest / LIVE_NAME)),
        install_fn=_install_staged,
        train_labels=int(train_labels) if train_labels is not None else None)


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
        return {"version": None, "metrics": None, "bar_met": False, "bar_reasons": [{"code": "no_model"}]}
    sidecar = _json(meta_path(live)) or {}
    version = sidecar.get("created_utc") or "unversioned"
    try:
        metrics = score_weights(live, shots, results_dir)
    except Exception as exc:        # an unreadable model must not break the card
        return {"version": version, "metrics": None, "bar_met": False,
                "bar_reasons": [{"code": "unreadable", "detail": str(exc)[:200]}]}
    met, reasons = check_bar_codes(metrics)
    return {"version": version, "metrics": metrics, "bar_met": met, "bar_reasons": reasons}


def train_count(labels_dir):
    """Trainable owner labels (label + added rows) in the train split."""
    return sum(_trainable(doc) for _, doc in _label_docs(labels_dir, "train"))


def status(labels_dir, results_dir, weights_dir):
    recover(weights_dir)
    shots = test_shots(labels_dir, results_dir)
    train = train_count(labels_dir)
    test = sum(len(truth) for _, _, truth in shots)
    hist = history(weights_dir)
    # the train count at the newest run that recorded one (kept, rejected or rolled back)
    seen = [e["train_labels"] for e in hist
            if isinstance(e.get("train_labels"), int) and not isinstance(e.get("train_labels"), bool)]
    new_train = max(0, train - (seen[-1] if seen else 0))
    missing = []
    if new_train < MIN_NEW_TRAIN:
        missing.append({"code": "train_labels", "need": MIN_NEW_TRAIN, "have": new_train})
    if test < MIN_TEST_SHOTS:
        missing.append({"code": "test_shots", "need": MIN_TEST_SHOTS, "have": test})
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
