"""Load and query the badminton exercise library."""
import json
import os

_DIFFICULTY_ORDER = {"beginner": 0, "intermediate": 1, "advanced": 2}
_DEFAULT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "exercise_library.json")
_CACHE = None


def load_library(path=None):
    global _CACHE
    if path is not None:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    if _CACHE is None:
        with open(_DEFAULT_PATH, "r", encoding="utf-8") as f:
            _CACHE = json.load(f)
    return _CACHE


def exercises_for(weakness, library=None):
    exercises = library if library is not None else load_library()
    matches = [e for e in exercises if weakness in e.get("target_weaknesses", [])]
    return sorted(matches, key=lambda e: _DIFFICULTY_ORDER.get(e["difficulty"], 1))
