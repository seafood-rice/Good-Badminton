import re

import pytest
from badminton_analysis.training import exercise_library as lib
from badminton_analysis.training.exercise_library import load_library

METRICS = {"elbow_extension", "trunk_rotation", "wrist_flexion",
           "knee_flexion", "hip_shoulder_separation", "weight_transfer"}
CATEGORIES = {"on_court", "mobility", "strength", "flexibility"}
LOCATIONS = {"court", "home"}


def test_library_loads_nonempty():
    exercises = lib.load_library()
    assert len(exercises) >= 12


def test_every_exercise_has_required_fields_and_valid_enums():
    for ex in lib.load_library():
        for field in ("id", "name", "category", "target_weaknesses", "description",
                      "equipment", "difficulty", "duration_min", "reps", "sets", "location"):
            assert field in ex, (ex.get("id"), field)
        assert ex["category"] in CATEGORIES
        assert ex["location"] in LOCATIONS
        assert ex["difficulty"] in {"beginner", "intermediate", "advanced"}
        assert isinstance(ex["target_weaknesses"], list) and ex["target_weaknesses"]


def test_ids_are_unique():
    ids = [ex["id"] for ex in lib.load_library()]
    assert len(ids) == len(set(ids))


def test_every_metric_has_at_least_two_exercises():
    exercises = lib.load_library()
    for metric in METRICS:
        matches = [e for e in exercises if metric in e["target_weaknesses"]]
        assert len(matches) >= 2, metric


def test_exercises_for_sorted_by_difficulty():
    result = lib.exercises_for("elbow_extension")
    order = {"beginner": 0, "intermediate": 1, "advanced": 2}
    diffs = [order[e["difficulty"]] for e in result]
    assert diffs == sorted(diffs)
    assert len(result) >= 2


def test_library_detail_schema():
    lib = load_library()
    assert len(lib) == 15
    for ex in lib:
        assert isinstance(ex.get("name_zh"), str) and ex["name_zh"].strip(), ex["id"]
        assert isinstance(ex.get("description_zh"), str) and ex["description_zh"].strip(), ex["id"]
        for field, minimum in (("instructions", 3), ("coaching_cues", 2), ("common_mistakes", 2)):
            block = ex.get(field)
            assert isinstance(block, dict), (ex["id"], field)
            for lang in ("en", "zh"):
                items = block.get(lang)
                assert isinstance(items, list) and len(items) >= minimum, (ex["id"], field, lang)
                assert all(isinstance(s, str) and s.strip() for s in items), (ex["id"], field, lang)
        assert isinstance(ex.get("video_url"), str), ex["id"]
        assert ex["video_url"].startswith("https://www.youtube.com/"), ex["id"]


def test_curated_watch_urls():
    library = load_library()
    watch = [ex for ex in library
             if re.match(r"^https://www\.youtube\.com/watch\?v=[\w-]{11}$", ex["video_url"])]
    assert len(watch) >= 4
