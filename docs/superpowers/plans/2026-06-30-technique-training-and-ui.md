# Technique Training Plan + Web UI — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the technique reports produced by the core plan into a personalized hybrid training plan and an interactive Web UI viewer in the existing Flask app.

**Architecture:** A pure-logic training layer maps match-summary weaknesses to exercises from a JSON library and assembles a progressive multi-week plan. New Flask routes read the core plan's output files (`strokes.jsonl`, `technique_summary.json`) and a generated `training_plan.json`, and serve them to new front-end views (stroke timeline, stroke detail, match summary, training plan tab) added to `web_ui.html`.

**Tech Stack:** Python 3.12, Flask (existing), pytest; vanilla JS/HTML/CSS in `web_ui.html` (existing pattern — no build step).

**Depends on:** `2026-06-30-technique-analysis-core.md` (must be implemented first; this plan consumes `strokes.jsonl` and `technique_summary.json`).

## Global Constraints

- Python 3.8+ compatible syntax. NumPy `<2.0`. Flask `>=2.0,<4.0`.
- Metric vocabulary (must match core plan exactly): `elbow_extension`, `trunk_rotation`, `wrist_flexion`, `knee_flexion`, `hip_shoulder_separation`, `weight_transfer`.
- Stroke vocabulary: `high_clear`, `smash`, `drop_shot`, `serve`.
- All new output written via existing `badminton_analysis/data/writer.py` `write_json`.
- `web_ui.html` is a single self-contained file served by `app.py` `index()`; follow its existing fetch-based API pattern (`/api/...` returns JSON). No external CDNs (matches existing file).
- New Flask routes return JSON and never raise on missing files — they return a structured `{"error": ...}` with the right status code, matching existing `app.py` conventions.

---

## File Structure

**New files:**
- `badminton_analysis/training/__init__.py` — package marker
- `badminton_analysis/training/exercise_library.json` — exercise catalog
- `badminton_analysis/training/exercise_library.py` — load + query the catalog
- `badminton_analysis/training/plan_generator.py` — weakness → progressive weekly plan
- `tests/test_exercise_library.py`, `tests/test_plan_generator.py`, `tests/test_app_technique_routes.py`

**Modified files:**
- `app.py` — add `/api/technique/<video>`, `/api/training-plan/<video>` (GET + POST regenerate)
- `web_ui.html` — add technique + training-plan views

---

### Task 1: Exercise library data + loader

**Files:**
- Create: `badminton_analysis/training/__init__.py`
- Create: `badminton_analysis/training/exercise_library.json`
- Create: `badminton_analysis/training/exercise_library.py`
- Test: `tests/test_exercise_library.py`

**Interfaces:**
- Produces:
  - `load_library(path=None) -> list[dict]` — loads the bundled JSON (default: the file beside the module). Each exercise: `{id, name, category, target_weaknesses[], description, equipment, difficulty, duration_min, reps, sets, location}` where `category ∈ {on_court, mobility, strength, flexibility}` and `location ∈ {court, home}`.
  - `exercises_for(weakness, library=None) -> list[dict]` — all exercises whose `target_weaknesses` contains `weakness`, ordered by `difficulty` ascending (`beginner` < `intermediate` < `advanced`).
- Consumes: nothing.

- [ ] **Step 1: Write failing tests**

`tests/test_exercise_library.py`:
```python
import pytest
from badminton_analysis.training import exercise_library as lib

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
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/Scripts/python.exe -m pytest tests/test_exercise_library.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Create the package marker + data file**

`badminton_analysis/training/__init__.py`: (empty file)

`badminton_analysis/training/exercise_library.json` — at least 2 exercises per metric across categories/locations. Each metric appears in ≥2 entries; the set below satisfies the tests:
```json
[
  {"id": "shadow_clear", "name": "Shadow clear swings", "category": "on_court",
   "target_weaknesses": ["elbow_extension", "wrist_flexion"],
   "description": "Full overhead clear motion without a shuttle, focusing on full arm extension and wrist snap at the top.",
   "equipment": "racket", "difficulty": "beginner", "duration_min": 10, "reps": 20, "sets": 3, "location": "court"},

  {"id": "overhead_band_press", "name": "Resistance band overhead press", "category": "strength",
   "target_weaknesses": ["elbow_extension"],
   "description": "Press a resistance band overhead to full lockout to build extension strength.",
   "equipment": "resistance band", "difficulty": "beginner", "duration_min": 8, "reps": 12, "sets": 3, "location": "home"},

  {"id": "wall_throws", "name": "Overhead medicine ball wall throws", "category": "strength",
   "target_weaknesses": ["elbow_extension", "weight_transfer"],
   "description": "Explosively throw a light medicine ball overhead into a wall, extending fully and driving weight forward.",
   "equipment": "medicine ball", "difficulty": "intermediate", "duration_min": 10, "reps": 10, "sets": 3, "location": "home"},

  {"id": "trunk_rotation_drill", "name": "Cable/band trunk rotations", "category": "strength",
   "target_weaknesses": ["trunk_rotation", "hip_shoulder_separation"],
   "description": "Rotate the trunk against band resistance to build rotational power.",
   "equipment": "resistance band", "difficulty": "beginner", "duration_min": 8, "reps": 15, "sets": 3, "location": "home"},

  {"id": "thoracic_rotation_stretch", "name": "Thoracic spine rotation stretch", "category": "mobility",
   "target_weaknesses": ["trunk_rotation"],
   "description": "Open-book thoracic rotations to improve mid-back rotational range.",
   "equipment": "none", "difficulty": "beginner", "duration_min": 5, "reps": 12, "sets": 2, "location": "home"},

  {"id": "rotation_smash_drill", "name": "Multi-shuttle rotational smash", "category": "on_court",
   "target_weaknesses": ["trunk_rotation", "hip_shoulder_separation", "weight_transfer"],
   "description": "Feed multi-shuttle smashes emphasizing hip-shoulder separation and forward drive.",
   "equipment": "racket, shuttles", "difficulty": "advanced", "duration_min": 15, "reps": 30, "sets": 2, "location": "court"},

  {"id": "wrist_snap_drill", "name": "Wrist snap shadow drill", "category": "on_court",
   "target_weaknesses": ["wrist_flexion"],
   "description": "Short overhead snaps focusing on wrist pronation at contact.",
   "equipment": "racket", "difficulty": "beginner", "duration_min": 8, "reps": 25, "sets": 3, "location": "court"},

  {"id": "wrist_curls", "name": "Weighted wrist curls", "category": "strength",
   "target_weaknesses": ["wrist_flexion"],
   "description": "Wrist flexion/extension curls with a light dumbbell to build forearm control.",
   "equipment": "dumbbell", "difficulty": "intermediate", "duration_min": 8, "reps": 15, "sets": 3, "location": "home"},

  {"id": "split_step_lunge", "name": "Split-step into lunge", "category": "on_court",
   "target_weaknesses": ["knee_flexion", "weight_transfer"],
   "description": "Split-step then deep lunge to the net, loading the knees and transferring weight forward.",
   "equipment": "none", "difficulty": "intermediate", "duration_min": 10, "reps": 12, "sets": 3, "location": "court"},

  {"id": "single_leg_squat", "name": "Single-leg squats", "category": "strength",
   "target_weaknesses": ["knee_flexion"],
   "description": "Controlled single-leg squats to build stance stability and leg drive.",
   "equipment": "none", "difficulty": "intermediate", "duration_min": 10, "reps": 10, "sets": 3, "location": "home"},

  {"id": "hip_flexor_stretch", "name": "Hip flexor stretch", "category": "flexibility",
   "target_weaknesses": ["hip_shoulder_separation", "knee_flexion"],
   "description": "Half-kneeling hip flexor stretch to open the hips for rotation and lunging.",
   "equipment": "none", "difficulty": "beginner", "duration_min": 5, "reps": 4, "sets": 2, "location": "home"},

  {"id": "shoulder_dislocates", "name": "Band shoulder dislocates", "category": "mobility",
   "target_weaknesses": ["wrist_flexion", "elbow_extension"],
   "description": "Pass a band overhead and behind to improve shoulder/arm mobility for the overhead action.",
   "equipment": "resistance band", "difficulty": "beginner", "duration_min": 5, "reps": 12, "sets": 2, "location": "home"},

  {"id": "lunge_footwork", "name": "Four-corner lunge footwork", "category": "on_court",
   "target_weaknesses": ["weight_transfer", "knee_flexion"],
   "description": "Lunge to four court corners and recover, driving weight into and out of each lunge.",
   "equipment": "none", "difficulty": "intermediate", "duration_min": 12, "reps": 16, "sets": 2, "location": "court"},

  {"id": "serve_target_practice", "name": "Serve target practice", "category": "on_court",
   "target_weaknesses": ["wrist_flexion", "trunk_rotation"],
   "description": "Serve to marked targets to refine controlled wrist and trunk action on the serve.",
   "equipment": "racket, shuttles", "difficulty": "beginner", "duration_min": 12, "reps": 30, "sets": 2, "location": "court"},

  {"id": "med_ball_rotational_throw", "name": "Rotational medicine ball throw", "category": "strength",
   "target_weaknesses": ["hip_shoulder_separation", "trunk_rotation"],
   "description": "Explosive sideways medicine ball throws to develop hip-shoulder separation power.",
   "equipment": "medicine ball", "difficulty": "advanced", "duration_min": 10, "reps": 8, "sets": 3, "location": "home"}
]
```

- [ ] **Step 4: Implement the loader**

`badminton_analysis/training/exercise_library.py`:
```python
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
```

- [ ] **Step 5: Run to verify pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_exercise_library.py -v`
Expected: PASS (5 passed).

- [ ] **Step 6: Commit**

```bash
git add badminton_analysis/training/__init__.py badminton_analysis/training/exercise_library.json badminton_analysis/training/exercise_library.py tests/test_exercise_library.py
git commit -m "feat: badminton exercise library and loader"
```

---

### Task 2: Training plan generator

**Files:**
- Create: `badminton_analysis/training/plan_generator.py`
- Test: `tests/test_plan_generator.py`

**Interfaces:**
- Consumes: `exercises_for`, `load_library` (Task 1).
- Produces:
  - `generate_plan(match_summary, library=None, weeks=4) -> dict`. `match_summary` is the core plan's `technique_summary.json` shape (`recurring_weaknesses: [{metric, count}], by_type, stroke_count`). Returns:
    ```
    {
      "weeks": [
        {"week": 1, "phase": "Foundation",
         "sessions": [{"exercise_id", "name", "category", "location",
                       "frequency_per_week", "reps", "sets", "duration_min", "targets": [metric...]}]}
        ...
      ],
      "targeted_weaknesses": [metric...],          # from match_summary, capped
      "on_court": [exercise_id...],                # convenience split
      "at_home": [exercise_id...]
    }
    ```
  - Selection: take the top `max_weaknesses=3` recurring weaknesses; for each, pick up to 2 exercises (`exercises_for`), dedupe by `id`. Early weeks favor `beginner`/`mobility`/`flexibility`; later weeks add `intermediate`/`advanced` strength + on-court drills (progressive). `phase` is `"Foundation"` for the first half of `weeks`, `"Progression"` for the second half. If no weaknesses, return a general baseline (pick 4 `beginner` exercises spanning categories).

- [ ] **Step 1: Write failing tests**

`tests/test_plan_generator.py`:
```python
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
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/Scripts/python.exe -m pytest tests/test_plan_generator.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

`badminton_analysis/training/plan_generator.py`:
```python
"""Generate a progressive multi-week training plan from a technique match summary."""
from .exercise_library import exercises_for, load_library

_MAX_WEAKNESSES = 3
_EX_PER_WEAKNESS = 2
_EARLY_DIFFICULTY = {"beginner"}
_BASELINE_CATEGORIES = ("on_court", "mobility", "strength", "flexibility")


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
    for ex in sorted(library, key=lambda e: e["difficulty"]):
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
        # ensure Foundation weeks are never empty: fall back to all easy-eligible exercises
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
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_plan_generator.py -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add badminton_analysis/training/plan_generator.py tests/test_plan_generator.py
git commit -m "feat: progressive training plan generator"
```

---

### Task 3: Flask technique + training-plan routes

**Files:**
- Modify: `app.py`
- Test: `tests/test_app_technique_routes.py`

**Interfaces:**
- Consumes: `generate_plan` (Task 2); reads `strokes.jsonl` and `technique_summary.json` produced by the core plan; writes `training_plan.json` via existing `write_json`.
- Produces (new Flask routes):
  - `GET /api/technique/<video_name>` → `{"summary": <technique_summary.json>, "strokes": [<report>...]}`; `404` `{"error": ...}` if no summary file.
  - `GET /api/training-plan/<video_name>` → existing `training_plan.json` if present, else generates from `technique_summary.json`, writes it, returns it. `404` if no summary to base it on.
  - `POST /api/training-plan/<video_name>` with optional JSON body `{"weeks": int}` → regenerates and overwrites `training_plan.json`, returns it.

- [ ] **Step 1: Write failing tests**

`tests/test_app_technique_routes.py`:
```python
import json
import os
import pytest

import app as webapp


@pytest.fixture
def client(tmp_path, monkeypatch):
    # Redirect OUTPUTS to a temp dir so tests don't touch real outputs
    monkeypatch.setattr(webapp, "OUTPUTS", tmp_path)
    webapp.app.config["TESTING"] = True
    return webapp.app.test_client()


def _write_summary(tmp_path, video):
    out = tmp_path / video
    out.mkdir(parents=True, exist_ok=True)
    summary = {
        "stroke_count": 2,
        "by_type": {"smash": {"count": 2, "avg_score": 65.0}},
        "recurring_weaknesses": [{"metric": "elbow_extension", "count": 2}],
        "strengths": [],
    }
    (out / "technique_summary.json").write_text(json.dumps(summary), encoding="utf-8")
    with open(out / "strokes.jsonl", "w", encoding="utf-8") as f:
        f.write(json.dumps({"stroke_type": "smash", "overall_score": 65.0,
                            "weaknesses": [], "strengths": []}) + "\n")
    return out


def test_technique_404_when_missing(client):
    r = client.get("/api/technique/nope")
    assert r.status_code == 404


def test_technique_returns_summary_and_strokes(client, tmp_path):
    _write_summary(tmp_path, "demo")
    r = client.get("/api/technique/demo")
    assert r.status_code == 200
    data = r.get_json()
    assert data["summary"]["stroke_count"] == 2
    assert len(data["strokes"]) == 1


def test_training_plan_generates_and_persists(client, tmp_path):
    out = _write_summary(tmp_path, "demo")
    r = client.get("/api/training-plan/demo")
    assert r.status_code == 200
    plan = r.get_json()
    assert "weeks" in plan and len(plan["weeks"]) >= 1
    assert (out / "training_plan.json").exists()


def test_training_plan_404_without_summary(client):
    r = client.get("/api/training-plan/missing")
    assert r.status_code == 404


def test_training_plan_post_regenerates_with_weeks(client, tmp_path):
    _write_summary(tmp_path, "demo")
    r = client.post("/api/training-plan/demo", json={"weeks": 6})
    assert r.status_code == 200
    assert len(r.get_json()["weeks"]) == 6
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/Scripts/python.exe -m pytest tests/test_app_technique_routes.py -v`
Expected: FAIL (routes return 404/HTML, assertions fail) or import errors for new helpers.

- [ ] **Step 3: Implement routes in app.py**

Add imports near the top of `app.py` (after existing imports):
```python
from badminton_analysis.data.writer import write_json
from badminton_analysis.training.plan_generator import generate_plan
```

Add these routes before the `# Static page` section in `app.py`:
```python
@app.route('/api/technique/<video_name>')
def api_technique(video_name):
    """Return technique summary + per-stroke reports for a video."""
    out_dir = OUTPUTS / video_name
    summary_path = out_dir / 'technique_summary.json'
    if not summary_path.exists():
        return jsonify({'error': '还没有技术分析数据，请先用 --analyze-technique 运行分析'}), 404

    with open(summary_path, encoding='utf-8') as f:
        summary = json.load(f)

    strokes = []
    strokes_path = out_dir / 'strokes.jsonl'
    if strokes_path.exists():
        with open(strokes_path, encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line:
                    strokes.append(json.loads(line))

    return jsonify({'summary': summary, 'strokes': strokes})


def _load_or_make_training_plan(video_name, weeks=4, force=False):
    out_dir = OUTPUTS / video_name
    plan_path = out_dir / 'training_plan.json'
    summary_path = out_dir / 'technique_summary.json'

    if plan_path.exists() and not force:
        with open(plan_path, encoding='utf-8') as f:
            return json.load(f), 200

    if not summary_path.exists():
        return {'error': '没有技术分析数据，无法生成训练计划'}, 404

    with open(summary_path, encoding='utf-8') as f:
        summary = json.load(f)

    plan = generate_plan(summary, weeks=weeks)
    write_json(str(plan_path), plan)
    return plan, 200


@app.route('/api/training-plan/<video_name>', methods=['GET'])
def api_training_plan(video_name):
    plan, status = _load_or_make_training_plan(video_name)
    return jsonify(plan), status


@app.route('/api/training-plan/<video_name>', methods=['POST'])
def api_training_plan_regenerate(video_name):
    data = request.json or {}
    weeks = int(data.get('weeks', 4))
    plan, status = _load_or_make_training_plan(video_name, weeks=weeks, force=True)
    return jsonify(plan), status
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_app_technique_routes.py -v`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
git add app.py tests/test_app_technique_routes.py
git commit -m "feat: technique and training-plan API routes"
```

---

### Task 4: Web UI — technique viewer + training plan tab

**Files:**
- Modify: `web_ui.html`
- Test: manual (front-end, no automated test harness in this project)

**Interfaces:**
- Consumes: `/api/technique/<video>`, `/api/training-plan/<video>`, existing `/api/output/<video>/detect_<video>.mp4`.
- Produces: a "技术分析 / Technique" panel rendered after analysis completes, with: a stroke list (type + score badge), a click-to-detail per-stroke breakdown (per-metric bars + weakness descriptions), a match summary block, and a training-plan section with an on-court/at-home toggle and a "重新生成 / Regenerate" control.

This is one task: the views share state (selected video, fetched technique data) and a single review gate makes sense.

- [ ] **Step 1: Locate the results panel in web_ui.html**

Run: `.venv/Scripts/python.exe -c "import re,io; s=open('web_ui.html',encoding='utf-8').read(); print(len(s)); print('RESULT PANEL idx:', s.find('result'))"`
Expected: prints file length and the index of the first `result` occurrence (used to find where to insert markup). Read the surrounding markup to match existing style/class names before editing.

- [ ] **Step 2: Add the technique panel markup**

Insert a new section in the results area of `web_ui.html` (match existing class naming; the existing file uses plain `<div>` panels and a `fetch`-based JS layer). Add a container:
```html
<div id="techniquePanel" style="display:none; margin-top:24px;">
  <h2>🏸 技术分析 / Technique Analysis</h2>
  <div id="techniqueSummary"></div>
  <div id="strokeList"></div>
  <div id="strokeDetail"></div>
  <div id="trainingPlan" style="margin-top:24px;">
    <h3>🏋 训练计划 / Training Plan</h3>
    <div>
      <button id="planCourtBtn" onclick="showPlanLocation('court')">🏟 球场 On-Court</button>
      <button id="planHomeBtn" onclick="showPlanLocation('home')">🏠 居家 At-Home</button>
      <button id="planRegenBtn" onclick="regeneratePlan()">🔄 重新生成 Regenerate</button>
    </div>
    <div id="planWeeks"></div>
  </div>
</div>
```

- [ ] **Step 3: Add the technique JS**

Add this script block before the closing `</body>` (or in the existing script section) in `web_ui.html`:
```html
<script>
let _techniqueData = null;
let _planData = null;
let _currentVideo = null;
const METRIC_LABEL = {
  elbow_extension: "肘部伸展 Elbow", trunk_rotation: "躯干旋转 Trunk",
  wrist_flexion: "手腕角度 Wrist", knee_flexion: "屈膝 Knee",
  hip_shoulder_separation: "髋肩分离 Hip-Shoulder", weight_transfer: "重心转移 Weight"
};

function scoreColor(s) {
  if (s == null) return "#888";
  if (s >= 75) return "#3fb950";
  if (s >= 50) return "#d29922";
  return "#f85149";
}

async function loadTechnique(videoName) {
  _currentVideo = videoName;
  const r = await fetch(`/api/technique/${videoName}`);
  if (!r.ok) { document.getElementById('techniquePanel').style.display = 'none'; return; }
  _techniqueData = await r.json();
  document.getElementById('techniquePanel').style.display = 'block';
  renderSummary();
  renderStrokeList();
  await loadPlan();
}

function renderSummary() {
  const s = _techniqueData.summary;
  let html = `<p>共检测 ${s.stroke_count} 次击球 / ${s.stroke_count} strokes detected.</p><ul>`;
  for (const [type, info] of Object.entries(s.by_type || {})) {
    html += `<li>${type}: ${info.count} 次, 平均分 ${info.avg_score ?? 'N/A'}</li>`;
  }
  html += `</ul>`;
  if ((s.recurring_weaknesses || []).length) {
    html += `<p>主要弱点 / Top weaknesses:</p><ul>`;
    for (const w of s.recurring_weaknesses.slice(0, 3)) {
      html += `<li>${METRIC_LABEL[w.metric] || w.metric} (${w.count})</li>`;
    }
    html += `</ul>`;
  }
  document.getElementById('techniqueSummary').innerHTML = html;
}

function renderStrokeList() {
  let html = '<h3>击球列表 / Strokes</h3>';
  _techniqueData.strokes.forEach((st, i) => {
    const c = scoreColor(st.overall_score);
    html += `<div onclick="showStrokeDetail(${i})" style="cursor:pointer; padding:6px; border-left:6px solid ${c}; margin:4px 0; background:#1c1c1c;">
      #${i + 1} ${st.stroke_type} — <b style="color:${c}">${st.overall_score ?? 'N/A'}</b></div>`;
  });
  document.getElementById('strokeList').innerHTML = html;
}

function showStrokeDetail(i) {
  const st = _techniqueData.strokes[i];
  let html = `<h3>击球 #${i + 1}: ${st.stroke_type}</h3>`;
  for (const [metric, m] of Object.entries(st.per_metric || {})) {
    const sc = m.score;
    const w = sc == null ? 0 : Math.max(0, Math.min(100, sc));
    html += `<div style="margin:4px 0;">
      <span style="display:inline-block;width:160px;">${METRIC_LABEL[metric] || metric}</span>
      <span style="display:inline-block;width:200px;background:#333;">
        <span style="display:inline-block;height:12px;width:${w * 2}px;background:${scoreColor(sc)};"></span>
      </span> ${m.measured ?? 'N/A'} (ideal ${m.ideal_range?.[0]}-${m.ideal_range?.[1]})</div>`;
  }
  if ((st.weaknesses || []).length) {
    html += `<h4>改进建议 / Suggestions</h4><ul>`;
    for (const w of st.weaknesses) html += `<li>${w.description}</li>`;
    html += `</ul>`;
  }
  document.getElementById('strokeDetail').innerHTML = html;
}

async function loadPlan() {
  const r = await fetch(`/api/training-plan/${_currentVideo}`);
  if (!r.ok) return;
  _planData = await r.json();
  showPlanLocation('court');
}

async function regeneratePlan() {
  const r = await fetch(`/api/training-plan/${_currentVideo}`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ weeks: 4 })
  });
  if (r.ok) { _planData = await r.json(); showPlanLocation('court'); }
}

function showPlanLocation(loc) {
  if (!_planData) return;
  let html = '';
  for (const wk of _planData.weeks) {
    const rows = wk.sessions.filter(s => s.location === loc);
    if (!rows.length) continue;
    html += `<h4>第 ${wk.week} 周 (${wk.phase}) / Week ${wk.week}</h4><ul>`;
    for (const s of rows) {
      html += `<li>${s.name} — ${s.sets}×${s.reps}, ${s.frequency_per_week}×/周, ${s.duration_min}min</li>`;
    }
    html += `</ul>`;
  }
  document.getElementById('planWeeks').innerHTML = html || '<p>本视图暂无项目 / Nothing scheduled for this view.</p>';
}
</script>
```

- [ ] **Step 4: Hook technique loading into the existing completion flow**

Find the JS that handles analysis completion (where the existing code sets the result video/heatmap on `status === 'completed'`). After it renders the existing results, add a call:
```javascript
      // after existing completed-state rendering:
      loadTechnique(job.result.video_name);
```
(`job.result.video_name` is already provided by the existing `/api/status` response — see `app.py` `track_progress`.)

- [ ] **Step 5: Manual verification**

Run: `.venv/Scripts/python.exe app.py` then open `http://127.0.0.1:5050`.
Steps: pick a video already analyzed with `--analyze-technique` (so `technique_summary.json` exists), confirm the Technique panel appears, the stroke list renders with colored score badges, clicking a stroke shows per-metric bars + suggestions, and the training plan shows On-Court / At-Home toggling and Regenerate.

Note: if no analyzed video is available, verify the panel stays hidden (the `/api/technique` 404 path hides `#techniquePanel`) and the existing UI is unaffected.

- [ ] **Step 6: Commit**

```bash
git add web_ui.html
git commit -m "feat: technique viewer and training plan UI"
```

---

### Task 5: Documentation

**Files:**
- Modify: `README.md` (and `README_EN.md` if maintaining parity)

**Interfaces:** none (docs only). Fold into one task with a single review gate.

- [ ] **Step 1: Document the feature in README.md**

Add a section describing: enabling analysis with `python main.py --analyze-technique --racket-model weights/yolo11s-racket.pt --dominant-hand right`, the new output files (`strokes.jsonl`, `technique_summary.json`, `training_plan.json`), and the Web UI technique panel. Note the racket model weight must be present in `weights/` (same Releases-download pattern as the ball model), and that the indicative reference ranges in `reference_ranges.py` can be tuned.

- [ ] **Step 2: Commit**

```bash
git add README.md README_EN.md
git commit -m "docs: document technique analysis and training plan feature"
```

---

## Self-Review

**1. Spec coverage:**
- Hybrid training plan (generated weekly plan drawn from a categorized library, user can swap/regenerate) → Tasks 1, 2, 3 (regenerate), 4 (toggle/regenerate UI). ✅
- On-court + at-home split → exercise `location` field (Task 1), plan `on_court`/`at_home` + UI toggle (Tasks 2, 4). ✅
- Interactive Web UI viewer: stroke timeline/list, stroke detail with angle breakdown + comparison to ideal, match summary, training plan tab → Task 4. ✅
- Progressive difficulty (Foundation → Progression) → Task 2. ✅
- Reads core plan outputs without modifying them → Task 3 (read-only of `strokes.jsonl`/`technique_summary.json`). ✅
- Empty-state handling (no strokes / no analysis) → Task 2 baseline, Task 3 404s, Task 4 hides panel. ✅

**2. Placeholder scan:** No TBD/TODO. Exercise library is fully specified (15 entries, every metric ≥2). All JS/Python shown in full. The one manual task (Task 4 Step 5) is a front-end verification, justified — the project has no JS test harness and the existing front end is verified manually too.

**3. Type consistency:**
- Metric names identical to core plan across library `target_weaknesses`, `generate_plan`, and UI `METRIC_LABEL`. ✅
- `match_summary` shape (`recurring_weaknesses: [{metric, count}]`, `by_type`, `stroke_count`) matches core plan Task 10 `build_match_summary` output. ✅
- `generate_plan` return keys (`weeks[].sessions[]` with `exercise_id/name/category/location/frequency_per_week/reps/sets/duration_min/targets`) consumed identically by Task 4 `showPlanLocation`. ✅
- Stroke report shape (`overall_score`, `per_metric{measured, score, ideal_range}`, `weaknesses[].description`) consumed by Task 4 detail view matches core plan Task 9. ✅
- `OUTPUTS` patched in tests is the real module global in `app.py`. ✅

No issues found requiring rework.
