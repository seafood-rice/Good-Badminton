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

A match record is singles (the four keys above) or doubles::

    {"date": ..., "format": "doubles",
     "players": {"P1": {"athlete_id": ...} | {"name": ...} | None, ... "P4": ...},
     "result": {"winner_team": 1 | 2, "sets": [[team1, team2], ...]} | None}

``record_format`` tells them apart; a record never carries both shapes.

One match is sometimes recorded as several videos (the camera follows the tracked
player and the recording is cut at the side change). The later videos are *parts*
of the first one, the *primary*; a part holds only a link and the record is typed
once, on the primary::

    "<part stem>": {"part_of": "<primary stem>", "sides_swapped": false}

``sides_swapped`` is true when the players changed picture sides (upper/lower)
between the primary and the part. A link record carries no other key. The primary
is a stored singles record that is not itself a part (one level: no chains, no
cycles); doubles are never linked. ``resolve_record`` returns the effective record
of any stem (a part resolves to the primary's record, sides exchanged when
swapped) and is total: a dangling or chained link in a hand-edited store resolves
to ``{}``, it never raises. ``schema_version`` stays 1 (an older reader sees a link
as a blank singles record, which is harmless).
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
PLAYER_IDS = ("P1", "P2", "P3", "P4")
TEAMS = (1, 2)

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


def _blank_doubles():
    return {"date": None, "format": "doubles", "players": {}, "result": None}


def is_link(record):
    """True for a link record (a part of another video's match), by its ``part_of`` key.

    Total: a non-dict is not a link. The key alone decides, so a malformed link in
    a hand-edited store is still treated as a link (and so resolves to ``{}``)
    rather than as a blank singles record.
    """
    return isinstance(record, dict) and "part_of" in record


def record_format(record):
    """``"doubles"`` for a record marked ``format: "doubles"``, else ``"singles"``.

    Total: a non-dict, or an unmarked / hostile ``format``, is singles.
    """
    if isinstance(record, dict) and record.get("format") == "doubles":
        return "doubles"
    return "singles"


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


def _clean_sets(sets):
    """``(clean_sets, ok)``: 0-5 pairs of ints 0-99, as lists."""
    if sets is None:
        sets = []
    if not isinstance(sets, (list, tuple)) or len(sets) > MAX_SETS:
        return None, False
    clean_sets = []
    for pair in sets:
        if not isinstance(pair, (list, tuple)) or len(pair) != 2:
            return None, False
        if not (_is_score(pair[0]) and _is_score(pair[1])):
            return None, False
        clean_sets.append([pair[0], pair[1]])
    return clean_sets, True


def _clean_result(result):
    if result is None:
        return None, None
    if not isinstance(result, dict):
        return None, "invalid_result"
    winner = result.get("winner")
    if winner not in SIDES:
        return None, "invalid_result"
    clean_sets, ok = _clean_sets(result.get("sets", []))
    if not ok:
        return None, "invalid_result"
    return {"winner": winner, "sets": clean_sets}, None


def _clean_doubles_result(result):
    """Doubles result: ``winner_team`` 1 or 2 is required; sets are team-1-first."""
    if result is None:
        return None, None
    if not isinstance(result, dict) or "winner" in result:
        return None, "invalid_result"
    winner = result.get("winner_team")
    if isinstance(winner, bool) or not isinstance(winner, int) or winner not in TEAMS:
        return None, "invalid_result"
    clean_sets, ok = _clean_sets(result.get("sets", []))
    if not ok:
        return None, "invalid_result"
    return {"winner_team": winner, "sets": clean_sets}, None


def _clean_players(players, data):
    """``(clean_players, error)`` for a doubles ``players`` map.

    ``invalid_players`` for a non-object map, a key outside P1..P4, or one
    athlete on two identities; ``invalid_side`` / ``unknown_athlete`` for a bad
    identity entry (the singles side rule). Keys come back in P1..P4 order.
    """
    if players is None:
        return {}, None
    if not isinstance(players, dict):
        return None, "invalid_players"
    if any(key not in PLAYER_IDS for key in players):
        return None, "invalid_players"
    clean, seen = {}, set()
    for pid in PLAYER_IDS:
        if pid not in players:
            continue
        value, err = _clean_side(players[pid], data)
        if err:
            return None, err
        if value is not None and "athlete_id" in value:
            if value["athlete_id"] in seen:
                return None, "invalid_players"
            seen.add(value["athlete_id"])
        clean[pid] = value
    return clean, None


def _clean_date(record, clean):
    """Set ``clean["date"]`` from ``record``; an error code or None."""
    date = record.get("date")
    if date is None:
        return None
    if not isinstance(date, str) or not _DATE_RE.fullmatch(date):
        return "invalid_date"
    try:
        datetime.date.fromisoformat(date)
    except ValueError:
        return "invalid_date"
    clean["date"] = date
    return None


def _validate_doubles(record, data):
    if record.get("upper") is not None or record.get("lower") is not None:
        return None, "invalid_record"
    clean = _blank_doubles()
    err = _clean_date(record, clean)
    if err:
        return None, err
    players, err = _clean_players(record.get("players"), data)
    if err:
        return None, err
    clean["players"] = players
    result, err = _clean_doubles_result(record.get("result"))
    if err:
        return None, err
    clean["result"] = result
    return clean, None


def _clean_link(record, stem=None):
    """``(clean_link, error)`` for the shape of a link record; no store needed.

    ``invalid_record`` when it carries any key besides ``part_of`` and
    ``sides_swapped``; ``invalid_link`` for a ``part_of`` that is not a non-empty
    string (or is ``stem`` itself), or a ``sides_swapped`` that is not a bool
    (absent means false). Whether the primary exists is ``_validate_link``'s job.
    """
    if any(key not in ("part_of", "sides_swapped") for key in record):
        return None, "invalid_record"
    primary, swapped = record.get("part_of"), record.get("sides_swapped", False)
    if not isinstance(primary, str) or not primary or primary == stem:
        return None, "invalid_link"
    if not isinstance(swapped, bool):
        return None, "invalid_link"
    return {"part_of": primary, "sides_swapped": swapped}, None


def _validate_link(record, data, stem):
    clean, err = _clean_link(record, stem)
    if err:
        return None, err
    primary = data["matches"].get(clean["part_of"])
    if not isinstance(primary, dict) or is_link(primary) or record_format(primary) == "doubles":
        return None, "invalid_link"          # missing, itself a part (no chains), or doubles
    if stem is not None:
        if has_parts(data, stem):
            return None, "has_parts"         # first: clearing its record would unlink the parts
        own = data["matches"].get(stem)
        if isinstance(own, dict) and not is_link(own) and not _is_blank(own):
            return None, "part_has_record"   # the user clears the video's own record first
    return clean, None


def validate_match(record, data, stem=None):
    """Validate one match record: ``(clean_record, None)`` or ``(None, code)``.

    A singles record always carries ``date``/``upper``/``lower``/``result``; a
    doubles one (``format: "doubles"``) carries ``date``/``format``/``players``/
    ``result`` and never ``upper``/``lower``; a link (``part_of``, optional
    ``sides_swapped``) carries nothing else and is returned with both keys.
    ``data`` (a store dict) is required, so the existence check can never be
    skipped by accident: a referenced ``athlete_id`` must exist in it
    (``"unknown_athlete"``), and a link's primary must be a stored singles record
    that is not itself a part (``"invalid_link"``).

    ``stem`` is the video the record is for; pass it on every write. Without it the
    checks that need the video's current state are skipped: a link to itself
    (``invalid_link``), a link over a video that holds its own record
    (``part_has_record``), and a doubles or link record over a primary that still
    has parts (``has_parts``; parts would be orphaned).

    Codes: ``invalid_record``, ``invalid_date``, ``invalid_side``,
    ``invalid_result``, ``invalid_players``, ``unknown_athlete``, ``invalid_link``,
    ``part_has_record``, ``has_parts``. Never mutates ``record``.
    """
    if not isinstance(record, dict):
        return None, "invalid_record"
    if "part_of" in record or "sides_swapped" in record:
        return _validate_link(record, data, stem)
    fmt = record.get("format")
    if fmt is not None and fmt not in ("singles", "doubles"):
        return None, "invalid_record"
    if fmt == "doubles":
        clean, err = _validate_doubles(record, data)
        if err is None and stem is not None and has_parts(data, stem):
            return None, "has_parts"
        return clean, err
    if record.get("players") is not None:
        return None, "invalid_record"
    clean = _blank_match()

    err = _clean_date(record, clean)
    if err:
        return None, err

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
    if is_link(record):
        return False                  # a link is a real entry, whatever it points at
    if record_format(record) == "doubles":
        players = record.get("players")
        return (record.get("date") is None and record.get("result") is None
                and not (isinstance(players, dict)
                         and any(value is not None for value in players.values())))
    return all(record.get(key) is None for key in _blank_match())


def set_match(data, stem, record):
    """Replace one video's match record whole.

    ``None`` or an all-null record deletes the key -- absence, not an empty
    object on disk. ``record`` is expected to have passed ``validate_match``.
    A link record (``part_of``) is stored as just ``part_of`` + ``sides_swapped``.

    Deleting a primary's record also deletes its parts' links (they would dangle).
    Writing a doubles or link record over a stem that still has parts would orphan
    them the same way, so that raises ``ValueError("has_parts")`` instead.
    """
    out = copy.deepcopy(data)
    if record is None or _is_blank(record):
        out["matches"].pop(stem, None)
        for part in _part_stems(out["matches"], stem):
            del out["matches"][part]
        return out
    if is_link(record):
        if has_parts(data, stem):
            raise ValueError("has_parts")
        out["matches"][stem] = {"part_of": record.get("part_of"),
                                "sides_swapped": record.get("sides_swapped") is True}
        return out
    stored = _blank_doubles() if record_format(record) == "doubles" else _blank_match()
    if stored.get("format") == "doubles" and has_parts(data, stem):
        raise ValueError("has_parts")
    stored.update({key: copy.deepcopy(record.get(key)) for key in stored})
    if stored.get("format") == "doubles" and not isinstance(stored["players"], dict):
        stored["players"] = {}
    out["matches"][stem] = stored
    return out


def get_match(data, stem):
    """A copy of the stem's record, or ``{}`` when there is none."""
    return copy.deepcopy(data["matches"].get(stem, {}))


def _matches(data):
    """The ``matches`` dict of ``data``, or ``{}`` for a hostile store."""
    matches = data.get("matches") if isinstance(data, dict) else None
    return matches if isinstance(matches, dict) else {}


def _part_stems(matches, primary):
    """Stems (sorted) of the link records that name ``primary``, valid or not."""
    if not isinstance(primary, str):
        return []
    return sorted(stem for stem, record in matches.items()
                  if isinstance(stem, str) and is_link(record)
                  and record.get("part_of") == primary)


def has_parts(data, stem):
    """True when some link record names ``stem`` as its primary. Total."""
    return bool(_part_stems(_matches(data), stem))


def _valid_primary(matches, link):
    """The primary's stem and record for a link record, or ``(None, None)``.

    A primary is a stored, non-link, singles record: a missing one, one that is
    itself a part (chain or cycle), a doubles record or hostile junk all dangle.
    """
    primary = link.get("part_of")
    record = matches.get(primary) if isinstance(primary, str) else None
    if not isinstance(record, dict) or is_link(record) or record_format(record) == "doubles":
        return None, None
    return primary, record


def resolve_record(data, stem):
    """The effective match record of ``stem``; pure, total and a copy.

    - no link: the stored record, or ``{}``.
    - a linked part: the primary's record plus ``part_of`` and ``sides_swapped``.
      When swapped, ``upper``/``lower`` and ``result.winner`` are exchanged so the
      record reads in the part's own picture frame; ``date`` and ``result.sets``
      are as stored (set scores are not used per video).
    - a dangling link (primary missing, itself a part, doubles or junk): ``{}``.
    """
    matches = _matches(data)
    record = matches.get(stem) if isinstance(stem, str) else None
    if not is_link(record):
        return copy.deepcopy(record) if isinstance(record, dict) else {}
    primary, source = _valid_primary(matches, record)
    if primary is None:
        return {}
    out = copy.deepcopy(source)
    swapped = record.get("sides_swapped") is True
    if swapped:
        out["upper"], out["lower"] = out.get("lower"), out.get("upper")
        result = out.get("result")
        if isinstance(result, dict) and result.get("winner") in SIDES:
            result["winner"] = "lower" if result["winner"] == "upper" else "upper"
    out["part_of"], out["sides_swapped"] = primary, swapped
    return out


def parts_of(data, primary):
    """``[{"stem", "sides_swapped"}]`` of ``primary``'s parts, in stem order.

    Empty unless ``primary`` is a valid primary (a stored singles record that is
    not itself a part), so it agrees with ``resolve_record`` on what dangles.
    """
    matches = _matches(data)
    if not isinstance(primary, str) or _valid_primary(matches, {"part_of": primary})[0] is None:
        return []
    return [{"stem": stem, "sides_swapped": matches[stem].get("sides_swapped") is True}
            for stem in _part_stems(matches, primary)]


def match_view(data, stem):
    """What ``GET /api/matches/<stem>`` shows: the resolved record, plus ``parts``
    on a primary that has some. A part carries ``part_of`` / ``sides_swapped`` (from
    ``resolve_record``); an ordinary record has neither key nor ``parts``."""
    out = resolve_record(data, stem)
    parts = parts_of(data, stem)
    if parts:
        out["parts"] = parts
    return out


def record_athlete_ids(record):
    """The set of registered athlete ids a record (singles or doubles) references.

    Total: a hostile record or players map yields an empty set, never an error.
    """
    if not isinstance(record, dict):
        return set()
    if record_format(record) == "doubles":
        players = record.get("players")
        entries = list(players.values()) if isinstance(players, dict) else []
    else:
        entries = [record.get(side) for side in SIDES]
    return {entry["athlete_id"] for entry in entries
            if isinstance(entry, dict) and isinstance(entry.get("athlete_id"), str)}


def player_athlete(data, record, pid):
    """Resolve one doubles identity (``"P1"``..``"P4"``) like ``side_athlete``.

    Total: a singles record, a missing or hostile ``players`` map, an unknown
    identity or a dangling athlete all yield both ``None``.
    """
    players = record.get("players") if isinstance(record, dict) else None
    if not isinstance(players, dict) or not isinstance(pid, str):
        return {"athlete_id": None, "name": None}
    return _resolve_entry(data, players.get(pid))


def side_athlete(data, record, side):
    """Resolve one side of a match to ``{"athlete_id", "name"}``.

    A registered athlete yields both (the CURRENT registry name, so a rename is
    followed); a plain-name side yields only ``name``; an empty side, or a
    reference to an athlete that no longer exists, yields both ``None``.
    """
    return _resolve_entry(data, (record or {}).get(side))


def _resolve_entry(data, entry):
    """Shared by ``side_athlete`` and ``player_athlete``."""
    nobody = {"athlete_id": None, "name": None}
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

    Every match side or doubles identity that referenced the athlete becomes
    ``{"name": <their name>}``: the history is kept, only the registry entry goes. Deleting an
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
        players = record.get("players")
        if isinstance(players, dict):
            for pid, entry in players.items():
                if isinstance(entry, dict) and entry.get("athlete_id") == athlete_id:
                    players[pid] = {"name": athlete["name"]}
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


def _degrade_players(record, data):
    """Copy of a doubles ``record`` whose ``players`` map is made valid.

    A hostile map (not an object) becomes empty, keys outside P1..P4 are
    dropped, and an identity that fails the side rule, names a missing athlete
    or repeats an athlete already on an earlier identity becomes None, so the
    rest of the match survives a hand edit. Never raises.
    """
    out = dict(record)
    players = out.get("players")
    if not isinstance(players, dict):
        out["players"] = {}
        return out
    clean, seen = {}, set()
    for pid in PLAYER_IDS:
        if pid not in players:
            continue
        value, err = _clean_side(players[pid], data)
        if err or value is None:
            clean[pid] = None
            continue
        if "athlete_id" in value:
            if value["athlete_id"] in seen:
                clean[pid] = None
                continue
            seen.add(value["athlete_id"])
        clean[pid] = value
    out["players"] = clean
    return out


def _sanitise(payload):
    """Rebuild a store dict from raw payload, dropping what is unusable.

    A store can be hand-edited (or predate a rule). Entries that fail the same
    validation a write would apply are dropped, a squad reference to a squad
    that no longer exists is cleared, and a match side naming an unknown athlete
    is set to None (the rest of the match is kept), so the rest of the module can rely on the shape it documents.
    A well-formed link record (only ``part_of`` and ``sides_swapped``, a non-empty
    ``part_of`` other than its own stem, a bool flag) is kept even if its primary is
    gone; a malformed one (extra key, non-bool ``sides_swapped``, bad ``part_of``) is
    dropped like any other invalid record.
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
        if is_link(record):
            # Shape only: a well-formed link to a primary that is missing or itself
            # a part is kept (it just resolves to {}); a malformed link is dropped.
            clean, err = _clean_link(record, stem)
            if err is None:
                out["matches"][stem] = clean
            continue
        if record_format(record) == "doubles":
            record = _degrade_players(record, out)
        elif isinstance(record, dict):
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
