"""User-assigned tags for the video library.

Tags describe the VIDEO, not an analysis run, so they deliberately do not live
in ``outputs/<stem>/``: ``/api/delete`` with scope "all" removes that directory
whole, and clearing analysis results must not clear a video's labels.

Everything here is pure -- each mutation takes a dict and returns a new one --
so the rules can be tested without Flask, a request, or a temp server.
"""
import datetime
import json
import os
import shutil
import tempfile
import time

RESERVED = ("match", "drill")
"""Names derived from analysis results (``has_match`` / ``has_posture``).

They are computed per request and never stored, so they cannot go stale and
cannot be deleted into a state the system still believes. Refusing them as user
tags keeps one meaning per name.
"""

MAX_TAG_LEN = 40
SCHEMA_VERSION = 1

_BAD_CHARS = ("/", "\\")

_REPLACE_ATTEMPTS = 5
_REPLACE_BACKOFF_SEC = 0.05

_logged_unreadable_paths = set()
"""Paths already reported by ``load()`` this process, so a corrupt or
unreadable store is logged once rather than once per request."""


class StoreCorrupt(ValueError):
    """The store exists but is not valid JSON in the documented shape.

    Raised only by ``load_for_update`` -- a write path that is allowed to
    replace the file (spec Sec.9: a user edit may win over a corrupt store).
    ``load()`` never raises this; it reports the same condition as ``{}``.
    """


class StoreUnreadable(OSError):
    """The store exists but could not be read (permissions, locked, etc.).

    Subclasses OSError so an existing ``except OSError`` around a write path
    stays a safe net even for callers that do not know about this type.
    """


def normalise(tag):
    """Canonical form of a tag, or None if it is not usable.

    Lowercasing is deliberate and lossy: it stops ``Smash`` and ``smash`` both
    existing, which would make a rename ambiguous.
    """
    if not isinstance(tag, str):
        return None
    clean = tag.strip().lower()
    if not clean or len(clean) > MAX_TAG_LEN:
        return None
    if clean.strip(".") == "":
        # ".." collapses in a URL path segment (DELETE /api/tags/..), so a
        # dot-only tag could be created but never deleted through the API.
        return None
    if clean in RESERVED:
        return None
    if any(ch in clean for ch in _BAD_CHARS):
        return None
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in clean):
        return None
    return clean


def normalise_all(tags):
    """Split an incoming list into (clean, rejected).

    Rejected entries are returned rather than dropped so a caller can say which
    tag was refused and why, instead of silently saving fewer tags than asked.
    """
    clean, rejected = set(), []
    for tag in tags or []:
        norm = normalise(tag)
        if norm is None:
            rejected.append(tag)
        else:
            clean.add(norm)
    return sorted(clean), rejected


def _classify(path):
    """Read what is on disk without ever raising.

    Returns one of:
    - ("missing", None) -- no file at all.
    - ("unreadable", exc) -- the file exists but could not be opened/read.
    - ("corrupt", None) -- read, but not valid JSON in the documented shape.
    - ("ok", {stem: [tag, ...]}) -- the raw (unsanitised) ``videos`` mapping.
    """
    try:
        with open(path, encoding="utf-8") as handle:
            payload = json.load(handle)
    except FileNotFoundError:
        return "missing", None
    except OSError as exc:
        return "unreadable", exc
    except ValueError:
        return "corrupt", None
    videos = payload.get("videos") if isinstance(payload, dict) else None
    if not isinstance(videos, dict):
        return "corrupt", None
    return "ok", videos


def _sanitise_videos(videos):
    """Run every stored tag through ``normalise`` and drop what fails.

    A store can be hand-edited (or predate a rule): a stored ``"match"``
    would otherwise duplicate the system chip and make that video's PUT
    400, and a stored ``"Smash"`` could not be renamed or deleted because
    every route normalises its input before comparing.
    """
    out = {}
    for stem, tags in videos.items():
        if not isinstance(stem, str) or not isinstance(tags, list):
            continue
        clean = set()
        for tag in tags:
            if not isinstance(tag, str):
                continue
            norm = normalise(tag)
            if norm is not None:
                clean.add(norm)
        if clean:
            out[stem] = sorted(clean)
    return out


def _log_unreadable_once(path):
    key = str(path)
    if key in _logged_unreadable_paths:
        return
    _logged_unreadable_paths.add(key)
    print(f"Tag store is corrupt or unreadable and was not overwritten: {key}")


def load(path):
    """Tags from disk as {stem: [tag, ...]}; {} when missing or unusable.

    Never raises and never writes. A corrupt or unreadable file is reported
    as empty and left exactly as it is: losing every tag to a bad parse would
    be worse than showing none, and the file may still be recoverable by
    hand. Logged once per path so every request does not repeat the line.
    """
    status, value = _classify(path)
    if status == "missing":
        return {}
    if status != "ok":
        _log_unreadable_once(path)
        return {}
    return _sanitise_videos(value)


def load_for_update(path):
    """Strict read for a write path: never silently treats a bad file as {}.

    - Missing file -> {} (a first edit is allowed to create the store).
    - Unreadable (any other OSError) -> raises StoreUnreadable; the caller
      must not write, since the file may hold tags it never saw.
    - Corrupt (unparseable / wrong shape) -> raises StoreCorrupt; the caller
      may back the file up and then treat it as {} (the user's edit wins).
    """
    status, value = _classify(path)
    if status == "missing":
        return {}
    if status == "unreadable":
        raise StoreUnreadable(f"tag store unreadable: {path}") from value
    if status == "corrupt":
        raise StoreCorrupt(f"tag store is not well-formed: {path}")
    return _sanitise_videos(value)


def backup_corrupt(path):
    """Copy a corrupt store aside, byte-for-byte, before it is replaced.

    Called only when a tag edit is about to treat a corrupt store as {} and
    save over it (StoreCorrupt from load_for_update): the edit wins, but
    what was on disk is preserved rather than silently lost.
    """
    path = str(path)
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_path = f"{path}.corrupt-{stamp}"
    shutil.copyfile(path, backup_path)
    return backup_path


def _replace_with_retry(tmp, path):
    """os.replace, retrying briefly on PermissionError.

    Windows antivirus/indexer tools transiently hold a handle open on a
    just-written file; a single collision would otherwise surface as a save
    failure for something that succeeds if retried a moment later.
    """
    for attempt in range(_REPLACE_ATTEMPTS):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if attempt == _REPLACE_ATTEMPTS - 1:
                raise
            time.sleep(_REPLACE_BACKOFF_SEC)


def save(data, path):
    """Write the store atomically.

    A temp file in the SAME directory plus os.replace, because os.replace is
    only atomic within a filesystem. This is why the store does not reuse
    badminton_analysis.data.writer.write_json, which opens the target in "w"
    and writes in place -- with every tag in one file, a crash mid-write there
    would lose all of them.
    """
    path = str(path)
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    payload = {"schema_version": SCHEMA_VERSION, "videos": data}
    handle_fd, tmp = tempfile.mkstemp(dir=directory, prefix=".library_tags-", suffix=".tmp")
    try:
        with os.fdopen(handle_fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        _replace_with_retry(tmp, path)
    except Exception:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def tags_for(data, stem):
    return list(data.get(stem, []))


def set_tags(data, stem, tags):
    """Replace one video's tags. An empty result drops the key entirely."""
    out = dict(data)
    clean = sorted(set(tags))
    if clean:
        out[stem] = clean
    else:
        out.pop(stem, None)
    return out


def rename(data, old, new):
    """Rename across every video, merging when ``new`` is already present."""
    out = {}
    for stem, tags in data.items():
        if old in tags:
            kept = [t for t in tags if t != old]
            if new not in kept:
                kept.append(new)
            out[stem] = sorted(kept)
        else:
            out[stem] = list(tags)
    return out


def delete(data, tag):
    """Remove a tag from every video, dropping any video left with none."""
    out = {}
    for stem, tags in data.items():
        kept = [t for t in tags if t != tag]
        if kept:
            out[stem] = kept
    return out


def forget(data, stem):
    """Drop a deleted video's entry."""
    out = dict(data)
    out.pop(stem, None)
    return out
