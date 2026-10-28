"""Promote, roll back and recover a set of model files, parametrised by a ``ModelSpec``.

The stroke model (a ``.pt`` with its ``.json`` sidecar) and the rally models (two JSON
files) share this code. A spec names the files that live in ``weights_dir``; a swap moves
only the files it is given. The replaced files go to a stamp-named history dir, a swap
marker (pid + start time) guards the swap, and ``recover`` repairs a swap whose process
was killed. This module takes no lock: the caller serialises retrain, promote and rollback.
"""
import json
import os
import re
import shutil
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import psutil

from ..outcomes.labels import write_json_atomic

HISTORY_DIR = "history"
RETIRED_MARK = ".retired"
_MARKER_GRACE_SEC = 60        # an unreadable marker is only called stale after this long
_STAMP = r"\d{8}T\d{6}\d{3}Z[a-z]*"
_STAMP_DIR = re.compile(rf"^{_STAMP}$")
_STAGED = re.compile(rf"^\.rollback-({_STAMP})(.*)$")


@dataclass(frozen=True)
class ModelSpec:
    name: str
    files: tuple
    history_file: str
    history_subdir: str = None
    keep: int = 5
    marker: str = None

    @property
    def marker_name(self):
        return self.marker or f".swap-in-progress-{self.name}"


class SwapInProgress(RuntimeError):
    """Another process is promoting or rolling back the live model."""


def read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError, RecursionError, MemoryError):
        return None


def utc():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# ---------------------------------------------------------------- history

def history_path(spec, weights_dir):
    return Path(weights_dir) / spec.history_file


def history(spec, weights_dir):
    doc = read_json(history_path(spec, weights_dir))
    return [e for e in doc if isinstance(e, dict)] if isinstance(doc, list) else []


def append_history(spec, weights_dir, entry):
    write_json_atomic(history_path(spec, weights_dir), history(spec, weights_dir) + [entry])


def _base(spec, weights_dir):
    base = Path(weights_dir) / HISTORY_DIR
    return base / spec.history_subdir if spec.history_subdir else base


def _holds_model(spec, d):
    return any((d / f).is_file() for f in spec.files)


def history_dirs(spec, weights_dir, include_retired=True):
    """Stamp-named history dirs holding a model, oldest first; retired ones only on request."""
    d = _base(spec, weights_dir)
    if not d.is_dir():
        return []
    return sorted(p for p in d.iterdir()
                  if p.is_dir() and _STAMP_DIR.match(p.name) and _holds_model(spec, p)
                  and (include_retired or not (p / RETIRED_MARK).exists()))


def new_history_dir(spec, weights_dir):
    """A new stamp dir that sorts after every existing stamp dir, even if the clock went back."""
    base = _base(spec, weights_dir)
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


def can_rollback(spec, weights_dir):
    """True when an earlier model is kept that no rollback has retired."""
    return bool(history_dirs(spec, weights_dir, include_retired=False))


# ---------------------------------------------------------------- moving files

def move_group(pairs):
    """``os.replace`` every ``(src, dst)`` in order; on a failure the ones moved are put back."""
    moved = []
    try:
        for src, dst in pairs:
            os.replace(src, dst)
            moved.append((dst, src))
    except OSError:
        for back_from, back_to in reversed(moved):
            os.replace(back_from, back_to)
        raise


def _order(spec, names):
    """The names in the order they move: the last spec file first (the stroke sidecar before its .pt)."""
    return sorted(names, key=lambda n: -spec.files.index(n))


def _live_names(spec, weights_dir, names):
    return [n for n in _order(spec, names) if (Path(weights_dir) / n).is_file()]


def install(spec, weights_dir, files):
    """Retire the live copies of ``files`` into a new history dir and put the given files in their place.

    ``files`` maps a spec file name to the path to install (None: retire the live copy, install
    nothing). Returns the retired dir (None when
    nothing was live). A failure puts everything back.
    """
    weights_dir = Path(weights_dir)
    weights_dir.mkdir(parents=True, exist_ok=True)
    for name in files:
        if name not in spec.files:
            raise ValueError(f"{name!r} is not a {spec.name} model file")
    for src in files.values():
        if src is not None and not Path(src).is_file():
            raise FileNotFoundError(str(src))
    live = _live_names(spec, weights_dir, files)
    retired = None
    if live:
        retired = new_history_dir(spec, weights_dir)
        move_group([(weights_dir / n, retired / n) for n in live])
    try:
        move_group([(Path(files[n]), weights_dir / n) for n in _order(spec, files)
                    if files[n] is not None])
    except OSError:
        if retired is not None:
            move_group([(retired / n, weights_dir / n) for n in live])
            shutil.rmtree(retired, ignore_errors=True)
        raise
    return retired


def prune(spec, weights_dir):
    dirs = history_dirs(spec, weights_dir)
    for old in dirs[:-spec.keep]:
        shutil.rmtree(old, ignore_errors=True)


# ---------------------------------------------------------------- swap marker

def read_marker(path):
    """``(pid, started)`` of the marker's writer; pid None when unreadable, started None when absent."""
    doc = read_json(path)
    pid = doc.get("pid") if isinstance(doc, dict) else None
    if type(pid) is not int or pid <= 0:
        return None, None
    started = doc.get("started")
    return pid, started if isinstance(started, (int, float)) and not isinstance(started, bool) else None


def started(pid):
    """The process's start time (seconds since the epoch), or None when it is not running."""
    try:
        return psutil.Process(pid).create_time()
    except psutil.Error:
        return None


def marker_alive(path):
    """True while the process that wrote the swap marker may still be running.

    The writer's start time is compared too: container PIDs are small and reused after a restart,
    so a live process with the same pid is a different process when it started at another time.
    """
    pid, began = read_marker(path)
    if pid is None:       # unreadable: a writer may be mid-create, so only an old file counts as stale
        try:
            return time.time() - os.stat(path).st_mtime < _MARKER_GRACE_SEC
        except OSError:
            return False
    try:
        now = started(pid)
    except Exception:
        return True
    if now is None:
        return False
    return began is None or abs(now - began) < 1.0


def _clear_marker(spec, weights_dir):
    try:
        os.remove(Path(weights_dir) / spec.marker_name)
    except OSError:
        pass


@contextmanager
def swap_marker(spec, weights_dir, what):
    """Hold the swap marker (pid + start time + what) for the length of a swap.

    Raises ``SwapInProgress`` when another live process holds it. A marker left by a dead process
    is repaired by ``recover`` first.
    """
    weights_dir = Path(weights_dir)
    weights_dir.mkdir(parents=True, exist_ok=True)
    marker = weights_dir / spec.marker_name
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    for attempt in range(2):
        try:
            fd = os.open(marker, flags)
            break
        except FileExistsError:
            if attempt or marker_alive(marker):
                raise SwapInProgress(f"a model swap is already running ({marker})")
            recover(spec, weights_dir)
            _clear_marker(spec, weights_dir)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump({"pid": os.getpid(), "started": started(os.getpid()), "what": what,
                       "utc": utc()}, fh)
        yield
    finally:
        _clear_marker(spec, weights_dir)


# ---------------------------------------------------------------- rollback staging and recovery

def _staged_name(spec, stamp, name):
    """``.rollback-<stamp><suffix>`` (``.pt`` / ``.json``), or with the file name when suffixes collide."""
    suffixes = [Path(f).suffix for f in spec.files]
    if len(set(suffixes)) == len(suffixes):
        return f".rollback-{stamp}{Path(name).suffix}"
    return f".rollback-{stamp}-{name}"


def _staged_files(spec, weights_dir):
    """``{name: path}`` of the newest staged rollback set (the files that exist), else empty."""
    stamps = sorted({m.group(1) for p in Path(weights_dir).glob(".rollback-*")
                     if (m := _STAGED.match(p.name))})
    for stamp in reversed(stamps):
        found = {n: Path(weights_dir) / _staged_name(spec, stamp, n) for n in spec.files}
        found = {n: p for n, p in found.items() if p.is_file()}
        if found:
            return found
    return {}


def recover(spec, weights_dir):
    """Undo a swap whose process was killed half way: a live file is gone, its replacement not in.

    Acts only when the marker exists and its pid is not alive; without a marker a missing live
    model is left alone (the owner may have deleted it), and a live process's swap is never
    touched. A staged rollback set (``.rollback-*``) completes the rollback; otherwise the newest
    history model (what promote retired) goes back to live. Returns True when something moved.
    Idempotent.
    """
    weights_dir = Path(weights_dir)
    marker = weights_dir / spec.marker_name
    if not marker.is_file() or marker_alive(marker):
        return False
    moved = False
    try:
        staged = _staged_files(spec, weights_dir)
        if staged:
            if any(not (weights_dir / n).is_file() for n in staged):
                move_group([(staged[n], weights_dir / n) for n in _order(spec, staged)])
                for d in history_dirs(spec, weights_dir, include_retired=False)[-1:]:
                    (d / RETIRED_MARK).write_text("", encoding="utf-8")   # the model rolled away
                moved = True
        else:
            dirs = history_dirs(spec, weights_dir)
            if dirs:
                names = [n for n in spec.files if (dirs[-1] / n).is_file()]
                if any(not (weights_dir / n).is_file() for n in names):
                    move_group([(dirs[-1] / n, weights_dir / n) for n in _order(spec, names)])
                    shutil.rmtree(dirs[-1], ignore_errors=True)
                    moved = True
    except OSError:
        return False          # keep the marker: the next call tries again
    _clear_marker(spec, weights_dir)
    return moved


# ---------------------------------------------------------------- promote, reject, rollback

def _install_hook(spec, install_fn):
    return install_fn if install_fn is not None else (lambda wd, files: install(spec, wd, files))


def promote(spec, weights_dir, candidate_files, record, annotate=None, install_fn=None):
    """Make the candidate files live; the replaced ones go to a new history dir.

    ``candidate_files`` maps a spec file name to the candidate's path; files not given stay.
    ``annotate(entry)`` (optional) returns extra fields for the history entry, computed after
    the swap. ``install_fn(weights_dir, files)`` replaces ``install`` (tests spy on it).
    """
    do_install = _install_hook(spec, install_fn)
    recover(spec, weights_dir)
    with swap_marker(spec, weights_dir, "promote"):
        retired = do_install(weights_dir, dict(candidate_files))
    prune(spec, weights_dir)
    entry = dict(record)
    entry.update({"utc": entry.get("utc") or utc(), "kept": True,
                  "replaced": retired.name if retired is not None else None})
    if annotate is not None:
        entry.update(annotate(entry))
    append_history(spec, weights_dir, entry)
    return entry


def reject(spec, weights_dir, candidate_files, record, annotate=None):
    """Delete a candidate that lost and record the numbers."""
    for p in candidate_files.values():
        try:
            Path(p).unlink()
        except FileNotFoundError:
            pass
    entry = dict(record)
    entry.update({"utc": entry.get("utc") or utc(), "kept": False, "replaced": None})
    if annotate is not None:
        entry.update(annotate(entry))
    append_history(spec, weights_dir, entry)
    return entry


def rollback(spec, weights_dir, live_after=None, install_fn=None, **record):
    """Swap in the newest replaced model that no rollback retired; False when there is none.

    The model rolled away is kept in history marked retired, so a second rollback goes one
    model further back instead of undoing the first. ``record`` fields (e.g. ``train_labels``)
    go on the history entry; ``live_after(newest_dir)`` (optional) returns the entry's
    ``live_after`` and runs before the swap.
    """
    do_install = _install_hook(spec, install_fn)
    weights_dir = Path(weights_dir)
    recover(spec, weights_dir)
    dirs = history_dirs(spec, weights_dir, include_retired=False)
    if not dirs:
        return False
    newest = dirs[-1]
    after = live_after(newest) if live_after is not None else None
    names = [n for n in spec.files if (newest / n).is_file()]
    with swap_marker(spec, weights_dir, "rollback"):
        # stage the old model outside history so the swap cannot see it as the current one
        staged = {n: weights_dir / _staged_name(spec, newest.name, n) for n in names}
        move_group([(newest / n, staged[n]) for n in _order(spec, names)])
        try:
            retired = do_install(weights_dir, staged)
        except OSError:
            move_group([(staged[n], newest / n) for n in _order(spec, names)])
            raise
        if retired is not None:
            try:
                (retired / RETIRED_MARK).write_text("", encoding="utf-8")
            except OSError:
                pass
    shutil.rmtree(newest, ignore_errors=True)
    prune(spec, weights_dir)
    entry = {"utc": utc(), "kept": True, "rollback": True, "reason": {"code": "rolled_back"},
             "replaced": retired.name if retired is not None else None, "restored": newest.name}
    if after is not None:
        entry["live_after"] = after
    for key, value in record.items():
        if value is not None:
            entry[key] = value
    append_history(spec, weights_dir, entry)
    return True
