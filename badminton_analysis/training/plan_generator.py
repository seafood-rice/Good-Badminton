"""Generate a progressive multi-week training plan from a technique match summary."""
from .exercise_library import exercises_for, load_library, _DIFFICULTY_ORDER

_MAX_WEAKNESSES = 3
_EX_PER_WEAKNESS = 2
_EARLY_DIFFICULTY = {"beginner"}


def _session_from_exercise(ex, frequency):
    return {
        "exercise_id": ex["id"],
        "name": ex["name"],
        "category": ex["category"],
        "location": ex["location"],
        "frequency_per_week": frequency,
        "reps": ex["reps"],
        "sets": ex["sets"],
        "duration_min": ex["duration_min"],
        "targets": list(ex["target_weaknesses"]),
        "detail": {
            "name_zh": ex.get("name_zh"),
            "description": ex.get("description"),
            "description_zh": ex.get("description_zh"),
            "equipment": ex.get("equipment"),
            "difficulty": ex.get("difficulty"),
            "instructions": ex.get("instructions"),
            "coaching_cues": ex.get("coaching_cues"),
            "common_mistakes": ex.get("common_mistakes"),
            "video_url": ex.get("video_url"),
        },
    }


def _select_exercises(match_summary, library):
    weaknesses = [w["metric"] for w in match_summary.get("recurring_weaknesses", [])][:_MAX_WEAKNESSES]
    selected = []
    seen = set()
    for metric in weaknesses:
        for ex in exercises_for(metric, library)[:_EX_PER_WEAKNESS]:
            if ex["id"] not in seen:
                seen.add(ex["id"])
                selected.append(ex)
    return weaknesses, selected


def _baseline(library):
    chosen = []
    seen_categories = set()
    for ex in sorted(library, key=lambda e: _DIFFICULTY_ORDER.get(e["difficulty"], 1)):
        if ex["difficulty"] != "beginner":
            continue
        if ex["category"] in seen_categories:
            continue
        seen_categories.add(ex["category"])
        chosen.append(ex)
        if len(chosen) >= 4:
            break
    return chosen


def generate_plan(match_summary, library=None, weeks=4):
    library = library if library is not None else load_library()
    targeted, exercises = _select_exercises(match_summary, library)
    if not exercises:
        exercises = _baseline(library)

    half = max(1, weeks // 2)
    week_list = []
    for w in range(1, weeks + 1):
        phase = "Foundation" if w <= half else "Progression"
        sessions = []
        for ex in exercises:
            is_easy = ex["difficulty"] in _EARLY_DIFFICULTY or ex["category"] in ("mobility", "flexibility")
            if phase == "Foundation" and not is_easy:
                continue  # introduce harder strength/on-court work in Progression
            frequency = 3 if ex["category"] in ("on_court", "strength") else 5
            sessions.append(_session_from_exercise(ex, frequency))
        # ensure Foundation weeks are never empty: if no easy-eligible exercises exist, fall back to all selected exercises
        if not sessions:
            sessions = [_session_from_exercise(ex, 3) for ex in exercises]
        week_list.append({"week": w, "phase": phase, "sessions": sessions})

    scheduled_ids = {s["exercise_id"] for wk in week_list for s in wk["sessions"]}
    id_to_loc = {ex["id"]: ex["location"] for ex in exercises}
    on_court = sorted(i for i in scheduled_ids if id_to_loc.get(i) == "court")
    at_home = sorted(i for i in scheduled_ids if id_to_loc.get(i) == "home")

    return {
        "weeks": week_list,
        "targeted_weaknesses": targeted,
        "on_court": on_court,
        "at_home": at_home,
    }
