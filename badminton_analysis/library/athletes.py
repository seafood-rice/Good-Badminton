"""Athletes, squads and per-video match records for the Kestrel telemetry UI.

Like the tag store this describes the VIDEO LIBRARY, not an analysis run, so it
lives in ``data/athletes.json`` and never under ``outputs/<stem>/`` (which
``/api/delete`` with scope "all" removes whole).

Everything here is pure -- each mutation takes a dict and returns a new one --
so the rules can be tested without Flask, a request, or a temp server. The
module takes no lock: the caller (``app.py``'s ``_ATHLETES_LOCK``) must hold
one across every ``load_for_update -> mutate -> save`` sequence.

In-memory shape (``save`` adds ``schema_version``)::

    {"squads":   {"s_ab12cd34": {"name": ...}},
     "athletes": {"a_9f3e21aa": {"name": ..., "squad_id": "s_..." | None}},
     "matches":  {"<stem>": {"date": "YYYY-MM-DD" | None,
                             "upper": {"athlete_id": ...} | {"name": ...} | None,
                             "lower": likewise,
                             "result": {"winner": "upper"|"lower",
                                        "sets": [[a, b], ...]} | None}}}
"""
import copy
import datetime
import json
import os
import re
import shutil
import tempfile
import time
import uuid

SCHEMA_VERSION = 1

MAX_NAME_LEN = 60
MAX_SETS = 5
MAX_SCORE = 99
SIDES = ("upper", "lower")

_SECTIONS = ("squads", "athletes", "matches")
_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")

_REPLACE_ATTEMPTS = 5
_REPLACE_BACKOFF_SEC = 0.05

_UNSET = object()
"""Distinguishes "leave this field alone" from an explicit ``None`` (clear it)."""

_logged_unreadable_paths = set()
"""Paths already reported by ``load()`` this process, so a corrupt or
unreadable store is logged once rather than once per request."""


class StoreCorrupt(ValueError):
    """The store exists but is not valid JSON in the documented shape.

    Raised only by ``load_for_update`` -- a write path that is allowed to
    replace the file after ``backup_corrupt``. ``load()`` never raises this; it
    reports the same condition as an empty store.
    """


class StoreUnreadable(OSError):
    """The store exists but could not be read (permissions, locked, etc.).

    Subclasses OSError so an existing ``except OSError`` around a write path
    stays a safe net even for callers that do not know about this type.
    """


# --------------------------------------------------------------------- names

def normalise_name(raw):
    """Trimmed name, or None if it is not usable (1-60 chars, no control chars)."""
    if not isinstance(raw, str):
        return None
    clean = raw.strip()
    if not clean or len(clean) > MAX_NAME_LEN:
        return None
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in clean):
        return None
    return clean


def _name_taken(registry, name, ignore_id=None):
    folded = name.casefold()
    return any(entry_id != ignore_id and entry["name"].casefold() == folded
               for entry_id, entry in registry.items())


def _new_id(prefix, registry):
    while True:
        candidate = f"{prefix}_{uuid.uuid4().hex[:8]}"
        if candidate not in registry:
            return candidate


# ------------------------------------------------------------------- matches

def _blank_match():
    return {"date": None, "upper": None, "lower": None, "result": None}


def _is_score(value):
    return isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= MAX_SCORE


def _clean_side(side, data):
    """(clean_side, error); a referenced athlete must exist in ``data``."""
    if side is None:
        return None, None
    if not isinstance(side, dict):
        return None, "invalid_side"
    athlete_id, name = side.get("athlete_id"), side.get("name")
    if athlete_id is not None and name is not None:
        return None, "invalid_side"
    if athlete_id is not None:
        if not isinstance(athlete_id, str) or not athlete_id:
            return None, "invalid_side"
        if athlete_id not in data["athletes"]:
            return None, "unknown_athlete"
        return {"athlete_id": athlete_id}, None
    clean = normalise_name(name)
    if clean is None:
        return None, "invalid_side"
    return {"name": clean}, None


def _clean_result(result):
    if result is None:
        return None, None
    if not isinstance(result, dict):
        return None, "invalid_result"
    winner = result.get("winner")
    if winner not in SIDES:
        return None, "invalid_result"
    sets = result.get("sets", [])
    if sets is None:
        sets = []
    if not isinstance(sets, (list, tuple)) or len(sets) > MAX_SETS:
        return None, "invalid_result"
    clean_sets = []
    for pair in sets:
        if not isinstance(pair, (list, tuple)) or len(pair) != 2:
            return None, "invalid_result"
        if not (_is_score(pair[0]) and _is_score(pair[1])):
            return None, "invalid_result"
        clean_sets.append([pair[0], pair[1]])
    return {"winner": winner, "sets": clean_sets}, None


def validate_match(record, data):
    """Validate one match record: ``(clean_record, None)`` or ``(None, code)``.

    The clean record always carries all four keys. ``data`` (a store dict) is
    required, so the existence check can never be skipped by accident: a side's
    ``athlete_id`` must exist in it (``"unknown_athlete"``). Codes:
    ``invalid_record``, ``invalid_date``, ``invalid_side``, ``invalid_result``,
    ``unknown_athlete``. Never mutates ``record``.
    """
    if not isinstance(record, dict):
        return None, "invalid_record"
    clean = _blank_match()

    date = record.get("date")
    if date is not None:
        if not isinstance(date, str) or not _DATE_RE.fullmatch(date):
            return None, "invalid_date"
        try:
            datetime.date.fromisoformat(date)
        except ValueError:
            return None, "invalid_date"
        clean["date"] = date

    for side in SIDES:
        value, err = _clean_side(record.get(side), data)
        if err:
            return None, err
        clean[side] = value

    result, err = _clean_result(record.get("result"))
    if err:
        return None, err
    clean["result"] = result
    return clean, None


def _is_blank(record):
    return all(record.get(key) is None for key in _blank_match())


def set_match(data, stem, record):
    """Replace one video's match record whole.

    ``None`` or an all-null record deletes the key -- absence, not an empty
    object on disk. ``record`` is expected to have passed ``validate_match``.
    """
    out = copy.deepcopy(data)
    if record is None or _is_blank(record):
        out["matches"].pop(stem, None)
        return out
    stored = _blank_match()
    stored.update({key: copy.deepcopy(record.get(key)) for key in stored})
    out["matches"][stem] = stored
    return out


def get_match(data, stem):
    """A copy of the stem's record, or ``{}`` when there is none."""
    return copy.deepcopy(data["matches"].get(stem, {}))


def side_athlete(data, record, side):
    """Resolve one side of a match to ``{"athlete_id", "name"}``.

    A registered athlete yields both (the CURRENT registry name, so a rename is
    followed); a plain-name side yields only ``name``; an empty side, or a
    reference to an athlete that no longer exists, yields both ``None``.
    """
    nobody = {"athlete_id": None, "name": None}
    entry = (record or {}).get(side)
    if not isinstance(entry, dict):
        return nobody
    athlete_id = entry.get("athlete_id")
    if athlete_id is not None:
        athlete = data["athletes"].get(athlete_id) if isinstance(athlete_id, str) else None
        if athlete is None:
            return nobody
        return {"athlete_id": athlete_id, "name": athlete["name"]}
    name = entry.get("name")
    if isinstance(name, str) and name:
        return {"athlete_id": None, "name": name}
    return nobody


# ------------------------------------------------------------------ athletes

def empty():
    return {"squads": {}, "athletes": {}, "matches": {}}


def _known_squad(data, squad_id):
    """True for an existing squad id. A non-string (e.g. a JSON list, which is
    unhashable) is simply unknown rather than a TypeError."""
    return isinstance(squad_id, str) and squad_id in data["squads"]


def add_athlete(data, name, squad_id=None):
    """``(new_data, athlete_id)``; ValueError ``invalid``/``duplicate``/``unknown_squad``."""
    clean = normalise_name(name)
    if clean is None:
        raise ValueError("invalid")
    if _name_taken(data["athletes"], clean):
        raise ValueError("duplicate")
    if squad_id is not None and not _known_squad(data, squad_id):
        raise ValueError("unknown_squad")
    out = copy.deepcopy(data)
    athlete_id = _new_id("a", out["athletes"])
    out["athletes"][athlete_id] = {"name": clean, "squad_id": squad_id}
    return out, athlete_id


def update_athlete(data, athlete_id, *, name=_UNSET, squad_id=_UNSET):
    """New data with the given fields changed; omitted fields are untouched.

    ``squad_id=None`` clears the squad. KeyError for an unknown athlete,
    ValueError ``invalid``/``duplicate``/``unknown_squad`` as for ``add_athlete``.
    """
    if athlete_id not in data["athletes"]:
        raise KeyError(athlete_id)
    clean = None
    if name is not _UNSET:
        clean = normalise_name(name)
        if clean is None:
            raise ValueError("invalid")
        if _name_taken(data["athletes"], clean, ignore_id=athlete_id):
            raise ValueError("duplicate")
    if squad_id is not _UNSET and squad_id is not None and not _known_squad(data, squad_id):
        raise ValueError("unknown_squad")
    out = copy.deepcopy(data)
    if clean is not None:
        out["athletes"][athlete_id]["name"] = clean
    if squad_id is not _UNSET:
        out["athletes"][athlete_id]["squad_id"] = squad_id
    return out


def delete_athlete(data, athlete_id):
    """``(new_data, rewritten_count)``.

    Every match side that referenced the athlete becomes ``{"name": <their
    name>}``: the history is kept, only the registry entry goes. Deleting an
    unknown athlete is a no-op returning ``(data, 0)`` (the route answers 200).
    """
    out = copy.deepcopy(data)
    athlete = out["athletes"].pop(athlete_id, None)
    if athlete is None:
        return out, 0
    rewritten = 0
    for record in out["matches"].values():
        for side in SIDES:
            entry = record.get(side)
            if isinstance(entry, dict) and entry.get("athlete_id") == athlete_id:
                record[side] = {"name": athlete["name"]}
                rewritten += 1
    return out, rewritten


# -------------------------------------------------------------------- squads

def add_squad(data, name):
    """``(new_data, squad_id)``; ValueError ``invalid``/``duplicate``."""
    clean = normalise_name(name)
    if clean is None:
        raise ValueError("invalid")
    if _name_taken(data["squads"], clean):
        raise ValueError("duplicate")
    out = copy.deepcopy(data)
    squad_id = _new_id("s", out["squads"])
    out["squads"][squad_id] = {"name": clean}
    return out, squad_id


def update_squad(data, squad_id, *, name=_UNSET):
    """New data with the squad renamed. KeyError if unknown; ValueError as for add."""
    if squad_id not in data["squads"]:
        raise KeyError(squad_id)
    out = copy.deepcopy(data)
    if name is not _UNSET:
        clean = normalise_name(name)
        if clean is None:
            raise ValueError("invalid")
        if _name_taken(data["squads"], clean, ignore_id=squad_id):
            raise ValueError("duplicate")
        out["squads"][squad_id]["name"] = clean
    return out


def delete_squad(data, squad_id):
    """``(new_data, cleared_count)``: members keep existing, with ``squad_id`` None.

    Deleting an unknown squad is a no-op returning ``(data, 0)``.
    """
    out = copy.deepcopy(data)
    if out["squads"].pop(squad_id, None) is None:
        return out, 0
    cleared = 0
    for athlete in out["athletes"].values():
        if athlete.get("squad_id") == squad_id:
            athlete["squad_id"] = None
            cleared += 1
    return out, cleared


# ---------------------------------------------------------------- load / save

def _classify(path):
    """Read what is on disk without ever raising.

    Returns one of:
    - ("missing", None) -- no file at all.
    - ("unreadable", exc) -- the file exists but could not be opened/read.
    - ("corrupt", None) -- read, but not valid JSON in the documented shape.
    - ("ok", payload) -- the raw (unsanitised) top-level dict.
    """
    try:
        with open(path, encoding="utf-8") as handle:
            payload = json.load(handle)
    except FileNotFoundError:
        return "missing", None
    except OSError as exc:
        return "unreadable", exc
    except (ValueError, RecursionError, MemoryError):
        # json.load raises RecursionError on pathologically nested input and
        # MemoryError on absurd sizes; both mean "not a usable store".
        return "corrupt", None
    if not isinstance(payload, dict) or not any(key in payload for key in _SECTIONS):
        return "corrupt", None
    if any(key in payload and not isinstance(payload[key], dict) for key in _SECTIONS):
        return "corrupt", None
    return "ok", payload


def _null_dangling_sides(record, data):
    """Copy of ``record`` with any side that only references a missing athlete
    set to None, so the rest of the match (date, result) survives a hand edit."""
    out = dict(record)
    for side in SIDES:
        entry = out.get(side)
        if not isinstance(entry, dict) or entry.get("name") is not None:
            continue
        athlete_id = entry.get("athlete_id")
        if athlete_id is not None and not (
                isinstance(athlete_id, str) and athlete_id in data["athletes"]):
            out[side] = None
    return out


def _sanitise(payload):
    """Rebuild a store dict from raw payload, dropping what is unusable.

    A store can be hand-edited (or predate a rule). Entries that fail the same
    validation a write would apply are dropped, a squad reference to a squad
    that no longer exists is cleared, and a match side naming an unknown athlete
    is set to None (the rest of the match is kept), so the rest of the module can rely on the shape it documents.
    """
    out = empty()
    for squad_id, entry in payload.get("squads", {}).items():
        if not isinstance(squad_id, str) or not isinstance(entry, dict):
            continue
        name = normalise_name(entry.get("name"))
        if name is not None:
            out["squads"][squad_id] = {"name": name}
    for athlete_id, entry in payload.get("athletes", {}).items():
        if not isinstance(athlete_id, str) or not isinstance(entry, dict):
            continue
        name = normalise_name(entry.get("name"))
        if name is None:
            continue
        squad_id = entry.get("squad_id")
        squad_id = squad_id if isinstance(squad_id, str) and squad_id in out["squads"] else None
        out["athletes"][athlete_id] = {"name": name, "squad_id": squad_id}
    for stem, record in payload.get("matches", {}).items():
        if not isinstance(stem, str):
            continue
        if isinstance(record, dict):
            record = _null_dangling_sides(record, out)
        clean, err = validate_match(record, out)
        if err is None and not _is_blank(clean):
            out["matches"][stem] = clean
    return out


def _log_unreadable_once(path):
    key = str(path)
    if key in _logged_unreadable_paths:
        return
    _logged_unreadable_paths.add(key)
    print(f"Athlete store is corrupt or unreadable and was not overwritten: {key}")


def load(path):
    """The store from disk; an empty store when missing or unusable.

    Never raises and never writes. A corrupt or unreadable file is reported as
    empty and left exactly as it is, logged once per path.
    """
    status, value = _classify(path)
    if status == "missing":
        return empty()
    if status != "ok":
        _log_unreadable_once(path)
        return empty()
    return _sanitise(value)


def load_for_update(path):
    """Strict read for a write path: never silently treats a bad file as empty.

    - Missing file -> empty store (a first edit may create it).
    - Unreadable (any other OSError) -> raises StoreUnreadable; the caller must
      not write, since the file may hold data it never saw.
    - Corrupt (unparseable / wrong shape) -> raises StoreCorrupt; the caller may
      ``backup_corrupt`` and then start from ``empty()`` (the user's edit wins).
    """
    status, value = _classify(path)
    if status == "missing":
        return empty()
    if status == "unreadable":
        raise StoreUnreadable("athlete store unreadable") from value
    if status == "corrupt":
        raise StoreCorrupt("athlete store is not well-formed")
    return _sanitise(value)


def backup_corrupt(path):
    """Copy a corrupt store aside, byte-for-byte, before it is replaced."""
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
    """Write the store atomically (temp file in the SAME directory + os.replace).

    Not ``data.writer.write_json``, which writes the target in place: with the
    whole registry in one file, a crash mid-write would lose all of it.
    """
    path = str(path)
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    payload = {"schema_version": SCHEMA_VERSION}
    payload.update({key: data.get(key, {}) for key in _SECTIONS})
    handle_fd, tmp = tempfile.mkstemp(dir=directory, prefix=".athletes-", suffix=".tmp")
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
