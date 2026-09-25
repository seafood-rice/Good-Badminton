"""User-assigned tags for the video library.

Tags describe the VIDEO, not an analysis run, so they deliberately do not live
in ``outputs/<stem>/``: ``/api/delete`` with scope "all" removes that directory
whole, and clearing analysis results must not clear a video's labels.

Everything here is pure -- each mutation takes a dict and returns a new one --
so the rules can be tested without Flask, a request, or a temp server.
"""
import json
import os
import tempfile

RESERVED = ("match", "drill")
"""Names derived from analysis results (``has_match`` / ``has_posture``).

They are computed per request and never stored, so they cannot go stale and
cannot be deleted into a state the system still believes. Refusing them as user
tags keeps one meaning per name.
"""

MAX_TAG_LEN = 40
SCHEMA_VERSION = 1

_BAD_CHARS = ("/", "\\")


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


def load(path):
    """Tags from disk as {stem: [tag, ...]}; {} when missing or unusable.

    Never raises and never writes. A corrupt file is reported as empty and left
    exactly as it is: losing every tag to a bad parse would be worse than
    showing none, and the file may still be recoverable by hand.
    """
    try:
        with open(path, encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError):
        return {}
    if not isinstance(payload, dict):
        return {}
    videos = payload.get("videos")
    if not isinstance(videos, dict):
        return {}
    out = {}
    for stem, tags in videos.items():
        if not isinstance(stem, str) or not isinstance(tags, list):
            continue
        clean = sorted({t for t in tags if isinstance(t, str)})
        if clean:
            out[stem] = clean
    return out


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
        os.replace(tmp, path)
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
