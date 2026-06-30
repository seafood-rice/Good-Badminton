import pytest
from badminton_analysis.training.plan_generator import generate_plan


def _summary(weaknesses):
    return {
        "stroke_count": 10,
        "by_type": {"smash": {"count": 6, "avg_score": 62.0}},
        "recurring_weaknesses": [{"metric": m, "count": c} for m, c in weaknesses],
        "strengths": [],
    }


def test_plan_has_requested_weeks_and_phases():
    plan = generate_plan(_summary([("elbow_extension", 5), ("knee_flexion", 3)]), weeks=4)
    assert len(plan["weeks"]) == 4
    assert plan["weeks"][0]["phase"] == "Foundation"
    assert plan["weeks"][-1]["phase"] == "Progression"


def test_plan_targets_top_weaknesses_only():
    plan = generate_plan(_summary([
        ("elbow_extension", 5), ("knee_flexion", 4),
        ("wrist_flexion", 3), ("trunk_rotation", 2)]), weeks=4)
    assert len(plan["targeted_weaknesses"]) <= 3
    assert "elbow_extension" in plan["targeted_weaknesses"]


def test_plan_sessions_reference_real_exercises():
    plan = generate_plan(_summary([("elbow_extension", 5)]), weeks=4)
    all_ids = {s["exercise_id"] for wk in plan["weeks"] for s in wk["sessions"]}
    assert all_ids  # non-empty
    # on_court / at_home split covers the same ids
    assert set(plan["on_court"]) | set(plan["at_home"]) == all_ids


def test_empty_weaknesses_returns_baseline():
    plan = generate_plan(_summary([]), weeks=4)
    assert plan["targeted_weaknesses"] == []
    assert len(plan["weeks"]) == 4
    # baseline still schedules something
    assert any(wk["sessions"] for wk in plan["weeks"])


def test_foundation_fallback_when_no_easy_exercises():
    # A library whose only exercise for the targeted weakness is advanced/strength
    lib = [
        {"id": "adv_only", "name": "Advanced only drill", "category": "strength",
         "target_weaknesses": ["elbow_extension"], "description": "x",
         "equipment": "none", "difficulty": "advanced",
         "duration_min": 10, "reps": 8, "sets": 3, "location": "home"},
    ]
    plan = generate_plan(_summary([("elbow_extension", 5)]), library=lib, weeks=4)
    # Foundation weeks (1 and 2) must still be non-empty via the fallback
    assert all(len(wk["sessions"]) >= 1 for wk in plan["weeks"]), \
        "Foundation weeks must never be empty even with no easy-eligible exercises"
