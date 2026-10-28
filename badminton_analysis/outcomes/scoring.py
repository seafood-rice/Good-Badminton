"""Per-match scoring format: presets, game / match end and end-change rules.

A match record may carry an optional ``scoring`` object (spec 4); absent means
``DEFAULT``. Scores are ``(a, b)`` point counts; ``sets`` are ``[a, b]`` pairs in
the order the record stores them. ``game_index`` is 1-based.
"""
import copy

DEFAULT = "bwf_21x3"

PRESETS = {
    "bwf_21x3": {"preset": "bwf_21x3", "points_to_win": 21, "win_by": 2, "cap": 30, "best_of": 3,
                 "change_ends_after_game": True, "change_ends_at": 11},
    "one_game_30": {"preset": "one_game_30", "points_to_win": 30, "win_by": 1, "cap": None, "best_of": 1,
                    "change_ends_after_game": False, "change_ends_at": 15},
    # Win-by and cap are assumptions (not confirmed rules); the fields are editable.
    "15x3": {"preset": "15x3", "points_to_win": 15, "win_by": 2, "cap": 21, "best_of": 3,
             "change_ends_after_game": True, "change_ends_at": 8},
}
PRESET_NAMES = tuple(PRESETS) + ("custom",)
_FIELDS = tuple(PRESETS[DEFAULT])


def _int(value):
    return type(value) is int


def validate(raw):
    """``(clean, None)``, ``(None, None)`` for ``None``, or ``(None, "invalid_scoring")``.

    Fields missing from ``raw`` come from its preset (``custom`` or none: the default
    preset). Never mutates ``raw``.
    """
    if raw is None:
        return None, None
    if not isinstance(raw, dict) or any(k not in _FIELDS for k in raw):
        return None, "invalid_scoring"
    preset = raw.get("preset", "custom")
    if preset not in PRESET_NAMES:
        return None, "invalid_scoring"
    clean = copy.deepcopy(PRESETS.get(preset, PRESETS[DEFAULT]))
    clean.update({k: v for k, v in raw.items() if k != "preset"})
    clean["preset"] = preset
    points, cap, at = clean["points_to_win"], clean["cap"], clean["change_ends_at"]
    if not (_int(points) and 5 <= points <= 50):
        return None, "invalid_scoring"
    if not (_int(clean["win_by"]) and clean["win_by"] in (1, 2)):
        return None, "invalid_scoring"
    if cap is not None and not (_int(cap) and cap >= points):
        return None, "invalid_scoring"
    if not (_int(clean["best_of"]) and clean["best_of"] in (1, 3, 5)):
        return None, "invalid_scoring"
    if type(clean["change_ends_after_game"]) is not bool:
        return None, "invalid_scoring"
    if at is not None and not (_int(at) and 1 <= at <= points - 1):
        return None, "invalid_scoring"
    return clean, None


def resolve(record):
    """The format of ``record``: its ``scoring`` (all fields filled) or the default preset."""
    raw = record.get("scoring") if isinstance(record, dict) else None
    clean, _err = validate(raw)
    return clean if clean is not None else copy.deepcopy(PRESETS[DEFAULT])


def game_over(fmt, a, b):
    """True when a game at ``a``-``b`` has been won."""
    hi, lo = max(a, b), min(a, b)
    if fmt["cap"] is not None and hi >= fmt["cap"]:
        return True
    return hi >= fmt["points_to_win"] and hi - lo >= fmt["win_by"]


def match_over(fmt, games_a, games_b):
    return max(games_a, games_b) >= fmt["best_of"] // 2 + 1


def ends_change(fmt, game_index, a, b, prev_a, prev_b):
    """True when the players change ends on the rally that took the score from
    ``prev`` to ``(a, b)``: when a game has just finished (``change_ends_after_game``),
    or, in the deciding game (the only game when best_of is 1), when the leader first
    reaches ``change_ends_at``."""
    if game_over(fmt, a, b):
        return bool(fmt["change_ends_after_game"])
    at = fmt["change_ends_at"]
    deciding = fmt["best_of"] == 1 or game_index == fmt["best_of"]
    if at is None or not deciding:
        return False
    return max(a, b) >= at > max(prev_a, prev_b)


def _legal_final(fmt, a, b):
    if not (_int(a) and _int(b)) or a == b or min(a, b) < 0 or not game_over(fmt, a, b):
        return False
    hi, lo = max(a, b), min(a, b)
    return not game_over(fmt, hi - 1, lo)      # the game must not have ended a rally earlier


def consistent(fmt, sets):
    """True when every recorded set is a legal final score and the sets decide the match
    exactly (no set after the match was won; none missing). No sets: nothing to contradict."""
    if not sets:
        return True
    wins = [0, 0]
    for pair in sets:
        if match_over(fmt, *wins):
            return False
        try:
            a, b = pair[0], pair[1]
        except (TypeError, IndexError, KeyError):
            return False
        if not _legal_final(fmt, a, b):
            return False
        wins[0 if a > b else 1] += 1
    return match_over(fmt, *wins)
