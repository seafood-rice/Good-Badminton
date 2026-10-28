"""Score, compare and promote stroke models on the owner's test split (spec 2026-10-28 section 6).

``test_shots`` lists the owner-labelled hits of every test-split video, ``score_weights``
runs any weights file over exactly those shots, ``decide`` applies the keep rule, and
``promote`` / ``rollback`` swap the live model with ``os.replace`` (the ``.pt`` and its
``.json`` sidecar always move together). ``weights_dir`` is always a parameter. This
module takes no lock: the caller serialises retrain, promote and rollback.
"""
import json
import os
import re
import shutil
import time
from contextlib import contextmanager
from pathlib import Path

import psutil

from ..outcomes.labels import write_json_atomic
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


def _history_dirs(weights_dir, include_retired=True):
    """Stamp-named history dirs holding a model, oldest first; retired ones only on request."""
    d = Path(weights_dir) / HISTORY_DIR
    if not d.is_dir():
        return []
    return sorted(p for p in d.iterdir()
                  if p.is_dir() and _STAMP_DIR.match(p.name) and (p / LIVE_NAME).is_file()
                  and (include_retired or not (p / RETIRED_MARK).exists()))


def _new_history_dir(weights_dir):
    """A new stamp dir that sorts after every existing stamp dir, even if the clock went back."""
    base = Path(weights_dir) / HISTORY_DIR
    base.mkdir(parents=True, exist_ok=True)
    stamps = [p.name for p in base.iterdir() if p.is_dir() and _STAMP_DIR.match(p.name)]
    latest = max(stamps, default="")
    prefix = max(time.strftime("%Y%m%dT%H%M%S", time.gmtime()), latest[:15])
    for n in range(1000):
        path = base / f"{prefix}{n:03d}Z"
        if path.name > latest and not path.exists():
            path.mkdir()
            return path
    suffix = "a"                  # 1000 dirs in one second: append a suffix instead
    while (base / (latest + suffix)).exists():
        suffix += "a"
    path = base / (latest + suffix)
    path.mkdir()
    return path


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


class SwapInProgress(RuntimeError):
    """Another process is promoting or rolling back the live model."""


def _read_marker(path):
    doc = _json(path)
    pid = doc.get("pid") if isinstance(doc, dict) else None
    return pid if type(pid) is int and pid > 0 else None


def _marker_alive(path):
    """True while the process that wrote the swap marker may still be running."""
    pid = _read_marker(path)
    if pid is None:       # unreadable: a writer may be mid-create, so only an old file counts as stale
        try:
            return time.time() - os.stat(path).st_mtime < _MARKER_GRACE_SEC
        except OSError:
            return False
    try:
        return psutil.pid_exists(pid)
    except Exception:
        return True


def _clear_marker(weights_dir):
    try:
        os.remove(Path(weights_dir) / SWAP_MARKER)
    except OSError:
        pass


@contextmanager
def _swap_marker(weights_dir, what):
    """Hold ``weights/.swap-in-progress`` (pid + what) for the length of a swap.

    Raises ``SwapInProgress`` when another live process holds it. A marker left by a dead process
    is repaired by ``recover`` first.
    """
    weights_dir = Path(weights_dir)
    weights_dir.mkdir(parents=True, exist_ok=True)
    marker = weights_dir / SWAP_MARKER
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    for attempt in range(2):
        try:
            fd = os.open(marker, flags)
            break
        except FileExistsError:
            if attempt or _marker_alive(marker):
                raise SwapInProgress(f"a model swap is already running ({marker})")
            recover(weights_dir)
            _clear_marker(weights_dir)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump({"pid": os.getpid(), "what": what, "utc": _utc()}, fh)
        yield
    finally:
        _clear_marker(weights_dir)


def recover(weights_dir):
    """Undo a swap whose process was killed half way: the live ``.pt`` is gone, its replacement not in.

    Acts only when ``.swap-in-progress`` exists and its pid is not alive; without a marker a
    missing live model is left alone (the owner may have deleted it), and a live process's swap is
    never touched. A staged rollback model (``.rollback-*.pt``) completes the rollback; otherwise
    the newest history model (what promote retired) goes back to live. Returns True when
    something moved. Idempotent.
    """
    weights_dir = Path(weights_dir)
    marker = weights_dir / SWAP_MARKER
    if not marker.is_file() or _marker_alive(marker):
        return False
    live = weights_dir / LIVE_NAME
    moved = False
    if not live.is_file():
        try:
            staged = sorted(weights_dir.glob(".rollback-*.pt"))
            if staged:
                _move_pair(staged[-1], live)
                for d in _history_dirs(weights_dir, include_retired=False)[-1:]:
                    (d / RETIRED_MARK).write_text("", encoding="utf-8")   # the model rolled away
                moved = True
            else:
                dirs = _history_dirs(weights_dir)
                if dirs:
                    _move_pair(dirs[-1] / LIVE_NAME, live)
                    shutil.rmtree(dirs[-1], ignore_errors=True)
                    moved = True
        except OSError:
            return False          # keep the marker: the next call tries again
    _clear_marker(weights_dir)
    return moved


def promote(weights_dir, candidate_path, record):
    """Make the candidate the live model; the replaced model goes to ``history/<utc>/``."""
    recover(weights_dir)
    with _swap_marker(weights_dir, "promote"):
        retired = _install(weights_dir, candidate_path)
    _prune(weights_dir)
    entry = dict(record)
    live = Path(weights_dir) / LIVE_NAME
    entry.update({"utc": entry.get("utc") or _utc(), "kept": True,
                  "replaced": retired.name if retired is not None else None,
                  "bar_met": _bar_met(entry.get("candidate")),
                  "live_after": _live_after(_version(live), entry.get("candidate"))})
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
    live = Path(weights_dir) / LIVE_NAME
    version = _version(live) if live.is_file() else None
    if live.is_file() and not isinstance(entry.get("live"), dict):
        after = _carried_scores(weights_dir, version)     # the live model was not scored this time
    else:
        after = _live_after(version, entry.get("live"))
    entry.update({"utc": entry.get("utc") or _utc(), "kept": False, "replaced": None,
                  "bar_met": _bar_met(entry.get("candidate")), "live_after": after})
    _append_history(weights_dir, entry)
    return entry


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
    """True when an earlier model is kept that no rollback has retired."""
    return bool(_history_dirs(weights_dir, include_retired=False))


def rollback(weights_dir, train_labels=None):
    """Swap in the newest replaced model that no rollback retired; False when there is none.

    The model rolled away is kept in history marked retired, so a second rollback goes one
    model further back instead of undoing the first.
    """
    recover(weights_dir)
    dirs = _history_dirs(weights_dir, include_retired=False)
    if not dirs:
        return False
    newest = dirs[-1]
    version = _version(newest / LIVE_NAME)
    with _swap_marker(weights_dir, "rollback"):
        # stage the old model outside history so the swap cannot see it as the current one
        staged = Path(weights_dir) / f".rollback-{newest.name}.pt"
        _move_pair(newest / LIVE_NAME, staged)
        try:
            retired = _install(weights_dir, staged)
        except OSError:
            _move_pair(staged, newest / LIVE_NAME)
            raise
        if retired is not None:
            try:
                (retired / RETIRED_MARK).write_text("", encoding="utf-8")
            except OSError:
                pass
    shutil.rmtree(newest, ignore_errors=True)
    _prune(weights_dir)
    entry = {"utc": _utc(), "kept": True, "rollback": True, "reason": {"code": "rolled_back"},
             "replaced": retired.name if retired is not None else None, "restored": newest.name,
             "live_after": _promoted_scores(weights_dir, version)}
    if train_labels is not None:
        entry["train_labels"] = int(train_labels)
    _append_history(weights_dir, entry)
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
