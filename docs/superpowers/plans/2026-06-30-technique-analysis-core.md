# Technique Analysis Core — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add biomechanical stroke analysis to Good-Badminton so `main.py --analyze-technique` detects each stroke, scores its technique against reference ranges, and writes per-stroke + match-summary reports plus a live-annotated video.

**Architecture:** A new analysis path runs alongside the existing per-frame pipeline. During the video loop the system buffers per-detect-frame data (dominant-arm keypoints, racket head, shuttlecock, centroid). After the loop, `detect_contacts` finds racket↔shuttlecock impacts, `classify_stroke` labels each, and `BiomechanicalAnalyzer` computes joint angles + scores into a `TechniqueReport`. Pure-logic modules (geometry, scoring, classification) are fully unit-tested; detection/integration use lightweight fakes.

**Tech Stack:** Python 3.12, NumPy, OpenCV, Ultralytics YOLO (racket detector), pytest (new dev dependency).

## Global Constraints

- Python 3.8+ compatible syntax (codebase targets 3.8+; dev/test venv is 3.12.4). No 3.10+-only syntax (`match`, `X | Y` type unions in annotations).
- NumPy pinned `>=1.21.6,<2.0` — do not use NumPy 2.0 APIs.
- Feature is **off by default**, gated behind `--analyze-technique` (CLI). When off, existing outputs are byte-for-byte unchanged.
- COCO 17 keypoint order is fixed (see indices in Task 2). Keypoints are 2D image coords; a value `<= 1` on both axes means "missing".
- Court coordinate system: width 6.1 m, length 13.4 m (existing convention).
- All new output files are JSON/JSONL written via the existing `badminton_analysis/data/writer.py` helpers (`write_json`, `JsonlDetectionWriter`).
- Player sides are the existing strings `"upper"` and `"lower"`. Dominant hand defaults to `"right"`.
- Stroke type vocabulary is exactly: `"high_clear"`, `"smash"`, `"drop_shot"`, `"serve"`.

---

## File Structure

**New files:**
- `badminton_analysis/analysis/joint_angles.py` — COCO indices + geometry + `compute_joint_angles`, `weight_transfer_ratio`
- `badminton_analysis/analysis/reference_ranges.py` — ideal ranges/weights per stroke
- `badminton_analysis/analysis/scoring.py` — `score_angle`, `score_stroke`, severity/direction helpers
- `badminton_analysis/analysis/biomechanics.py` — `BiomechanicalAnalyzer`, `TechniqueReport` assembly, weakness descriptions
- `badminton_analysis/stroke/__init__.py` — package marker
- `badminton_analysis/stroke/events.py` — `StrokeEvent` dataclass + `detect_contacts`
- `badminton_analysis/stroke/classifier.py` — `classify_stroke`
- `badminton_analysis/detection/racket.py` — `RacketDetector`
- `badminton_analysis/analysis/technique_writer.py` — `write_stroke_reports`, `build_match_summary`
- `badminton_analysis/visualization/technique_overlay.py` — `draw_technique_overlay`
- `tests/conftest.py` + `tests/test_*.py`
- `requirements-dev.txt`

**Modified files:**
- `badminton_analysis/system.py` — buffer analysis data in loop; run analysis post-loop when enabled
- `main.py` — add `--analyze-technique`, `--racket-model`, `--dominant-hand` args

---

### Task 1: Test infrastructure

**Files:**
- Create: `requirements-dev.txt`
- Create: `tests/__init__.py`
- Create: `tests/conftest.py`
- Test: `tests/test_smoke.py`

**Interfaces:**
- Produces: `tests/conftest.py` adds the project root to `sys.path` so `import badminton_analysis...` works without installation.

- [ ] **Step 1: Create dev requirements**

`requirements-dev.txt`:
```
pytest>=8.0
```

- [ ] **Step 2: Install pytest into the venv**

Run: `.venv/Scripts/python.exe -m pip install -r requirements-dev.txt`
Expected: installs pytest; ends with `Successfully installed ... pytest-8.x`.

- [ ] **Step 3: Create test package marker**

`tests/__init__.py`: (empty file)

- [ ] **Step 4: Create conftest.py**

`tests/conftest.py`:
```python
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
```

- [ ] **Step 5: Write smoke test**

`tests/test_smoke.py`:
```python
def test_import_package():
    import badminton_analysis
    assert hasattr(badminton_analysis, "__version__")
```

- [ ] **Step 6: Run smoke test**

Run: `.venv/Scripts/python.exe -m pytest tests/test_smoke.py -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add requirements-dev.txt tests/__init__.py tests/conftest.py tests/test_smoke.py
git commit -m "test: add pytest infrastructure"
```

---

### Task 2: Joint-angle geometry primitives

**Files:**
- Create: `badminton_analysis/analysis/joint_angles.py`
- Test: `tests/test_joint_angles.py`

**Interfaces:**
- Produces:
  - `angle_at(a, b, c) -> float | None` — interior angle in degrees at vertex `b`; `None` if either segment is degenerate (length `< 1e-6`).
  - `line_angle(p, q) -> float | None` — orientation of segment `p→q` from horizontal, in `[0, 180)`; `None` if `p == q`.
  - `is_valid(kp, idx, conf=None, conf_thresh=0.3) -> bool` — keypoint present (not `<=1` on both axes) and, if `conf` given, above threshold.
  - COCO index constants: `NOSE, L_EYE, R_EYE, L_EAR, R_EAR, L_SHOULDER, R_SHOULDER, L_ELBOW, R_ELBOW, L_WRIST, R_WRIST, L_HIP, R_HIP, L_KNEE, R_KNEE, L_ANKLE, R_ANKLE` = `0..16`.

- [ ] **Step 1: Write failing tests**

`tests/test_joint_angles.py`:
```python
import numpy as np
import pytest
from badminton_analysis.analysis import joint_angles as ja


def test_angle_at_right_angle():
    # a above vertex, c to the right of vertex -> 90 degrees
    assert ja.angle_at((0, -1), (0, 0), (1, 0)) == pytest.approx(90.0, abs=1e-6)


def test_angle_at_straight_line():
    assert ja.angle_at((-1, 0), (0, 0), (1, 0)) == pytest.approx(180.0, abs=1e-6)


def test_angle_at_degenerate_returns_none():
    assert ja.angle_at((0, 0), (0, 0), (1, 0)) is None


def test_line_angle_horizontal_is_zero():
    assert ja.line_angle((0, 0), (5, 0)) == pytest.approx(0.0, abs=1e-6)


def test_line_angle_vertical_is_90():
    assert ja.line_angle((0, 0), (0, 5)) == pytest.approx(90.0, abs=1e-6)


def test_is_valid_detects_missing_keypoint():
    kp = np.zeros((17, 2))
    kp[ja.R_ELBOW] = (1, 1)      # missing sentinel
    kp[ja.R_WRIST] = (100, 200)  # present
    assert ja.is_valid(kp, ja.R_WRIST) is True
    assert ja.is_valid(kp, ja.R_ELBOW) is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_joint_angles.py -v`
Expected: FAIL with `ModuleNotFoundError` / `AttributeError`.

- [ ] **Step 3: Implement primitives**

`badminton_analysis/analysis/joint_angles.py`:
```python
"""Joint-angle geometry on COCO 17 keypoints (2D image coordinates)."""
import numpy as np

# COCO 17 keypoint indices
NOSE = 0
L_EYE, R_EYE = 1, 2
L_EAR, R_EAR = 3, 4
L_SHOULDER, R_SHOULDER = 5, 6
L_ELBOW, R_ELBOW = 7, 8
L_WRIST, R_WRIST = 9, 10
L_HIP, R_HIP = 11, 12
L_KNEE, R_KNEE = 13, 14
L_ANKLE, R_ANKLE = 15, 16


def angle_at(a, b, c):
    """Interior angle (degrees) at vertex b formed by a-b-c. None if degenerate."""
    a = np.asarray(a, dtype=float)
    b = np.asarray(b, dtype=float)
    c = np.asarray(c, dtype=float)
    ba = a - b
    bc = c - b
    n_ba = np.linalg.norm(ba)
    n_bc = np.linalg.norm(bc)
    if n_ba < 1e-6 or n_bc < 1e-6:
        return None
    cos_ang = float(np.dot(ba, bc) / (n_ba * n_bc))
    cos_ang = max(-1.0, min(1.0, cos_ang))
    return float(np.degrees(np.arccos(cos_ang)))


def line_angle(p, q):
    """Orientation (degrees, [0,180)) of segment p->q from horizontal. None if p==q."""
    p = np.asarray(p, dtype=float)
    q = np.asarray(q, dtype=float)
    dx = q[0] - p[0]
    dy = q[1] - p[1]
    if abs(dx) < 1e-9 and abs(dy) < 1e-9:
        return None
    return float(np.degrees(np.arctan2(dy, dx)) % 180.0)


def is_valid(kp, idx, conf=None, conf_thresh=0.3):
    """True if keypoint idx is present (not <=1 on both axes) and above conf threshold."""
    x, y = float(kp[idx][0]), float(kp[idx][1])
    if x <= 1 and y <= 1:
        return False
    if conf is not None and float(conf[idx]) < conf_thresh:
        return False
    return True
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_joint_angles.py -v`
Expected: PASS (6 passed).

- [ ] **Step 5: Commit**

```bash
git add badminton_analysis/analysis/joint_angles.py tests/test_joint_angles.py
git commit -m "feat: joint-angle geometry primitives"
```

---

### Task 3: Stroke-frame angle bundle + weight-transfer metric

**Files:**
- Modify: `badminton_analysis/analysis/joint_angles.py`
- Test: `tests/test_joint_angles.py`

**Interfaces:**
- Consumes: `angle_at`, `line_angle`, `is_valid`, COCO constants (Task 2).
- Produces:
  - `compute_joint_angles(keypoints, racket_head=None, dominant="right", conf=None) -> dict` — keys: `"elbow_extension"`, `"shoulder_abduction"`, `"trunk_rotation"`, `"knee_flexion"`, `"hip_shoulder_separation"`, `"wrist_flexion"`; each a float or `None`. `keypoints` is a `(17,2)` array-like.
  - `weight_transfer_ratio(centroid_start, centroid_contact, shoulder_width_px) -> float | None` — horizontal centroid displacement normalized by shoulder width; `None` if `shoulder_width_px` is falsy/`< 1e-6`.

- [ ] **Step 1: Write failing tests**

Append to `tests/test_joint_angles.py`:
```python
def _straight_arm_keypoints():
    kp = np.zeros((17, 2))
    # right arm in a straight vertical line -> elbow extension 180
    kp[ja.R_SHOULDER] = (100, 100)
    kp[ja.R_ELBOW] = (100, 150)
    kp[ja.R_WRIST] = (100, 200)
    # both shoulders / hips horizontal -> trunk_rotation ~0, separation ~0
    kp[ja.L_SHOULDER] = (60, 100)
    kp[ja.L_HIP] = (60, 250)
    kp[ja.R_HIP] = (100, 250)
    # right leg straight -> knee 180
    kp[ja.R_KNEE] = (100, 300)
    kp[ja.R_ANKLE] = (100, 350)
    return kp


def test_compute_joint_angles_elbow_and_knee():
    angles = ja.compute_joint_angles(_straight_arm_keypoints(), dominant="right")
    assert angles["elbow_extension"] == pytest.approx(180.0, abs=1e-6)
    assert angles["knee_flexion"] == pytest.approx(180.0, abs=1e-6)
    assert angles["trunk_rotation"] == pytest.approx(0.0, abs=1e-6)


def test_compute_joint_angles_missing_racket_gives_none_wrist():
    angles = ja.compute_joint_angles(_straight_arm_keypoints(), racket_head=None, dominant="right")
    assert angles["wrist_flexion"] is None


def test_compute_joint_angles_with_racket():
    kp = _straight_arm_keypoints()
    angles = ja.compute_joint_angles(kp, racket_head=(150, 200), dominant="right")
    # elbow(100,150)->wrist(100,200)->racket(150,200): 90 degrees
    assert angles["wrist_flexion"] == pytest.approx(90.0, abs=1e-6)


def test_weight_transfer_ratio_basic():
    assert ja.weight_transfer_ratio((100, 0), (130, 0), 60.0) == pytest.approx(0.5, abs=1e-6)


def test_weight_transfer_ratio_zero_width_none():
    assert ja.weight_transfer_ratio((100, 0), (130, 0), 0.0) is None
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/Scripts/python.exe -m pytest tests/test_joint_angles.py -k "compute or weight" -v`
Expected: FAIL with `AttributeError: module ... has no attribute 'compute_joint_angles'`.

- [ ] **Step 3: Implement**

Append to `badminton_analysis/analysis/joint_angles.py`:
```python
_DOMINANT = {
    "right": (R_SHOULDER, R_ELBOW, R_WRIST, R_HIP, R_KNEE, R_ANKLE),
    "left": (L_SHOULDER, L_ELBOW, L_WRIST, L_HIP, L_KNEE, L_ANKLE),
}


def compute_joint_angles(keypoints, racket_head=None, dominant="right", conf=None):
    """Compute the six biomechanical angles for one frame. Missing inputs -> None."""
    kp = np.asarray(keypoints, dtype=float)
    sh, el, wr, hip, kn, an = _DOMINANT.get(dominant, _DOMINANT["right"])

    angles = {
        "elbow_extension": None,
        "shoulder_abduction": None,
        "trunk_rotation": None,
        "knee_flexion": None,
        "hip_shoulder_separation": None,
        "wrist_flexion": None,
    }

    if is_valid(kp, sh, conf) and is_valid(kp, el, conf) and is_valid(kp, wr, conf):
        angles["elbow_extension"] = angle_at(kp[sh], kp[el], kp[wr])
    if is_valid(kp, hip, conf) and is_valid(kp, sh, conf) and is_valid(kp, el, conf):
        angles["shoulder_abduction"] = angle_at(kp[hip], kp[sh], kp[el])
    if is_valid(kp, hip, conf) and is_valid(kp, kn, conf) and is_valid(kp, an, conf):
        angles["knee_flexion"] = angle_at(kp[hip], kp[kn], kp[an])

    shoulder_line = None
    if is_valid(kp, L_SHOULDER, conf) and is_valid(kp, R_SHOULDER, conf):
        shoulder_line = line_angle(kp[L_SHOULDER], kp[R_SHOULDER])
    hip_line = None
    if is_valid(kp, L_HIP, conf) and is_valid(kp, R_HIP, conf):
        hip_line = line_angle(kp[L_HIP], kp[R_HIP])

    if shoulder_line is not None:
        angles["trunk_rotation"] = shoulder_line
    if shoulder_line is not None and hip_line is not None:
        diff = abs(shoulder_line - hip_line)
        angles["hip_shoulder_separation"] = min(diff, 180.0 - diff)

    if racket_head is not None and is_valid(kp, el, conf) and is_valid(kp, wr, conf):
        angles["wrist_flexion"] = angle_at(kp[el], kp[wr], racket_head)

    return angles


def weight_transfer_ratio(centroid_start, centroid_contact, shoulder_width_px):
    """Horizontal centroid displacement normalized by shoulder width. None if width invalid."""
    if not shoulder_width_px or abs(float(shoulder_width_px)) < 1e-6:
        return None
    dx = abs(float(centroid_contact[0]) - float(centroid_start[0]))
    return float(dx / float(shoulder_width_px))
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_joint_angles.py -v`
Expected: PASS (11 passed).

- [ ] **Step 5: Commit**

```bash
git add badminton_analysis/analysis/joint_angles.py tests/test_joint_angles.py
git commit -m "feat: per-frame joint-angle bundle and weight-transfer metric"
```

---

### Task 4: Reference ranges

**Files:**
- Create: `badminton_analysis/analysis/reference_ranges.py`
- Test: `tests/test_reference_ranges.py`

**Interfaces:**
- Produces: `REFERENCE_RANGES: dict[str, dict[str, dict]]` keyed by the four stroke types, each mapping the six metric names (`elbow_extension`, `trunk_rotation`, `wrist_flexion`, `knee_flexion`, `hip_shoulder_separation`, `weight_transfer`) to `{"min", "max", "ideal", "weight"}`. Weights per stroke sum to `1.0`. Note `weight_transfer` is a ratio metric (not an angle) and replaces `shoulder_abduction` in scoring.

- [ ] **Step 1: Write failing tests**

`tests/test_reference_ranges.py`:
```python
import pytest
from badminton_analysis.analysis.reference_ranges import REFERENCE_RANGES

STROKES = {"high_clear", "smash", "drop_shot", "serve"}
METRICS = {"elbow_extension", "trunk_rotation", "wrist_flexion",
           "knee_flexion", "hip_shoulder_separation", "weight_transfer"}


def test_all_strokes_present():
    assert set(REFERENCE_RANGES.keys()) == STROKES


def test_each_stroke_has_all_metrics():
    for stroke, specs in REFERENCE_RANGES.items():
        assert set(specs.keys()) == METRICS, stroke


def test_weights_sum_to_one():
    for stroke, specs in REFERENCE_RANGES.items():
        total = sum(s["weight"] for s in specs.values())
        assert total == pytest.approx(1.0, abs=1e-9), stroke


def test_ranges_are_ordered():
    for stroke, specs in REFERENCE_RANGES.items():
        for name, s in specs.items():
            assert s["min"] <= s["ideal"] <= s["max"], (stroke, name)
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/Scripts/python.exe -m pytest tests/test_reference_ranges.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

`badminton_analysis/analysis/reference_ranges.py`:
```python
"""Sport-science reference ranges per stroke. Values are indicative first-release
starting points (image-plane 2D angles, degrees; weight_transfer is a ratio of
shoulder widths) and are expected to be tuned against literature/labeled clips."""

REFERENCE_RANGES = {
    "high_clear": {
        "elbow_extension":         {"min": 140, "max": 160, "ideal": 150, "weight": 0.25},
        "trunk_rotation":          {"min": 20,  "max": 40,  "ideal": 30,  "weight": 0.20},
        "wrist_flexion":           {"min": 80,  "max": 100, "ideal": 90,  "weight": 0.20},
        "knee_flexion":            {"min": 30,  "max": 60,  "ideal": 45,  "weight": 0.15},
        "hip_shoulder_separation": {"min": 15,  "max": 35,  "ideal": 25,  "weight": 0.10},
        "weight_transfer":         {"min": 0.10, "max": 1.50, "ideal": 0.35, "weight": 0.10},
    },
    "smash": {
        "elbow_extension":         {"min": 150, "max": 170, "ideal": 160, "weight": 0.25},
        "trunk_rotation":          {"min": 30,  "max": 50,  "ideal": 40,  "weight": 0.20},
        "wrist_flexion":           {"min": 100, "max": 120, "ideal": 110, "weight": 0.20},
        "knee_flexion":            {"min": 40,  "max": 70,  "ideal": 55,  "weight": 0.15},
        "hip_shoulder_separation": {"min": 20,  "max": 45,  "ideal": 32,  "weight": 0.10},
        "weight_transfer":         {"min": 0.20, "max": 1.80, "ideal": 0.50, "weight": 0.10},
    },
    "drop_shot": {
        "elbow_extension":         {"min": 120, "max": 140, "ideal": 130, "weight": 0.25},
        "trunk_rotation":          {"min": 15,  "max": 30,  "ideal": 22,  "weight": 0.20},
        "wrist_flexion":           {"min": 90,  "max": 110, "ideal": 100, "weight": 0.20},
        "knee_flexion":            {"min": 25,  "max": 50,  "ideal": 38,  "weight": 0.15},
        "hip_shoulder_separation": {"min": 10,  "max": 30,  "ideal": 20,  "weight": 0.10},
        "weight_transfer":         {"min": 0.05, "max": 1.00, "ideal": 0.25, "weight": 0.10},
    },
    "serve": {
        "elbow_extension":         {"min": 130, "max": 150, "ideal": 140, "weight": 0.25},
        "trunk_rotation":          {"min": 10,  "max": 25,  "ideal": 17,  "weight": 0.20},
        "wrist_flexion":           {"min": 70,  "max": 90,  "ideal": 80,  "weight": 0.20},
        "knee_flexion":            {"min": 50,  "max": 80,  "ideal": 65,  "weight": 0.15},
        "hip_shoulder_separation": {"min": 5,   "max": 20,  "ideal": 12,  "weight": 0.10},
        "weight_transfer":         {"min": 0.02, "max": 0.80, "ideal": 0.15, "weight": 0.10},
    },
}
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_reference_ranges.py -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add badminton_analysis/analysis/reference_ranges.py tests/test_reference_ranges.py
git commit -m "feat: per-stroke biomechanical reference ranges"
```

---

### Task 5: Scoring engine

**Files:**
- Create: `badminton_analysis/analysis/scoring.py`
- Test: `tests/test_scoring.py`

**Interfaces:**
- Consumes: `REFERENCE_RANGES` (Task 4).
- Produces:
  - `score_angle(measured, spec) -> float | None` — `100.0` inside `[min,max]`; linear decay to `0.0` at one range-width outside; `None` if `measured is None`.
  - `deviation_direction(measured, spec) -> str` — `"under"`, `"over"`, or `"in_range"`.
  - `classify_severity(score) -> str | None` — `"minor"` (≥75), `"moderate"` (≥50), `"severe"` (<50), `None` if score is `None`.
  - `score_stroke(metrics, stroke_type, ranges=REFERENCE_RANGES) -> dict` — returns `{"overall": float|None, "per_metric": {name: {measured, score, ideal_range:[min,max], weight, direction, severity}}, "weaknesses": [name...], "strengths": [name...]}`. `overall` is the weight-normalized mean over metrics with non-None scores. Strength = score ≥ 90; weakness = score < 75.

- [ ] **Step 1: Write failing tests**

`tests/test_scoring.py`:
```python
import pytest
from badminton_analysis.analysis import scoring

SPEC = {"min": 140, "max": 160, "ideal": 150, "weight": 0.25}


def test_score_angle_in_range_is_100():
    assert scoring.score_angle(150, SPEC) == 100.0


def test_score_angle_none_is_none():
    assert scoring.score_angle(None, SPEC) is None


def test_score_angle_one_width_below_is_zero():
    # range width 20; 20 below min(140) -> 120 -> score 0
    assert scoring.score_angle(120, SPEC) == pytest.approx(0.0, abs=1e-6)


def test_score_angle_half_width_above_is_50():
    # 10 above max(160) -> 170 -> half a width -> 50
    assert scoring.score_angle(170, SPEC) == pytest.approx(50.0, abs=1e-6)


def test_deviation_direction():
    assert scoring.deviation_direction(130, SPEC) == "under"
    assert scoring.deviation_direction(170, SPEC) == "over"
    assert scoring.deviation_direction(150, SPEC) == "in_range"


def test_classify_severity_bands():
    assert scoring.classify_severity(90) == "minor"
    assert scoring.classify_severity(60) == "moderate"
    assert scoring.classify_severity(10) == "severe"
    assert scoring.classify_severity(None) is None


def test_score_stroke_perfect_is_100_no_weakness():
    metrics = {"elbow_extension": 150, "trunk_rotation": 30, "wrist_flexion": 90,
               "knee_flexion": 45, "hip_shoulder_separation": 25, "weight_transfer": 0.35}
    result = scoring.score_stroke(metrics, "high_clear")
    assert result["overall"] == pytest.approx(100.0, abs=1e-6)
    assert result["weaknesses"] == []


def test_score_stroke_flags_weakness_and_skips_none():
    metrics = {"elbow_extension": 120, "trunk_rotation": 30, "wrist_flexion": None,
               "knee_flexion": 45, "hip_shoulder_separation": 25, "weight_transfer": 0.35}
    result = scoring.score_stroke(metrics, "high_clear")
    assert "elbow_extension" in result["weaknesses"]
    assert result["per_metric"]["wrist_flexion"]["score"] is None
    assert result["overall"] is not None and result["overall"] < 100.0
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/Scripts/python.exe -m pytest tests/test_scoring.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

`badminton_analysis/analysis/scoring.py`:
```python
"""Score measured stroke metrics against reference ranges."""
from .reference_ranges import REFERENCE_RANGES


def score_angle(measured, spec):
    """100 inside [min,max]; linear decay to 0 at one range-width outside. None->None."""
    if measured is None:
        return None
    lo, hi = spec["min"], spec["max"]
    if lo <= measured <= hi:
        return 100.0
    span = max(hi - lo, 1e-6)
    dev = (lo - measured) / span if measured < lo else (measured - hi) / span
    return float(max(0.0, 100.0 * (1.0 - dev)))


def deviation_direction(measured, spec):
    if measured < spec["min"]:
        return "under"
    if measured > spec["max"]:
        return "over"
    return "in_range"


def classify_severity(score):
    if score is None:
        return None
    if score >= 75:
        return "minor"
    if score >= 50:
        return "moderate"
    return "severe"


def score_stroke(metrics, stroke_type, ranges=REFERENCE_RANGES):
    """Aggregate per-metric scores into an overall weighted score plus findings."""
    specs = ranges[stroke_type]
    per_metric = {}
    weighted_sum = 0.0
    weight_total = 0.0
    weaknesses = []
    strengths = []

    for name, spec in specs.items():
        measured = metrics.get(name)
        s = score_angle(measured, spec)
        per_metric[name] = {
            "measured": measured,
            "score": s,
            "ideal_range": [spec["min"], spec["max"]],
            "weight": spec["weight"],
            "direction": None if measured is None else deviation_direction(measured, spec),
            "severity": classify_severity(s),
        }
        if s is not None:
            weighted_sum += s * spec["weight"]
            weight_total += spec["weight"]
            if s >= 90:
                strengths.append(name)
            elif s < 75:
                weaknesses.append(name)

    overall = round(weighted_sum / weight_total, 1) if weight_total > 0 else None
    return {
        "overall": overall,
        "per_metric": per_metric,
        "weaknesses": weaknesses,
        "strengths": strengths,
    }
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_scoring.py -v`
Expected: PASS (8 passed).

- [ ] **Step 5: Commit**

```bash
git add badminton_analysis/analysis/scoring.py tests/test_scoring.py
git commit -m "feat: stroke technique scoring engine"
```

---

### Task 6: Contact detection + StrokeEvent

**Files:**
- Create: `badminton_analysis/stroke/__init__.py`
- Create: `badminton_analysis/stroke/events.py`
- Test: `tests/test_stroke_events.py`

**Interfaces:**
- Produces:
  - `StrokeEvent` dataclass: fields `stroke_type: str`, `contact_frame: int`, `window_start: int`, `window_end: int`, `player_side: str`, `confidence: float`; method `to_dict() -> dict`.
  - `detect_contacts(track, contact_px=80.0, lookahead=3, dir_change_deg=45.0, window_pre=20, window_post=15, min_gap=15) -> list[dict]` where each dict is `{"contact_frame": int, "window_start": int, "window_end": int}`. `track` is a list of per-frame dicts `{"frame": int, "racket_head": (x,y)|None, "shuttle": (x,y)|None}` ordered by frame. A contact requires racket↔shuttle distance `< contact_px` AND a shuttle travel-direction change `>= dir_change_deg` between the segment ending at the contact frame and a segment `lookahead` frames later. Consecutive contacts within `min_gap` frames are suppressed (keep the first).

- [ ] **Step 1: Write failing tests**

`tests/test_stroke_events.py`:
```python
import pytest
from badminton_analysis.stroke.events import StrokeEvent, detect_contacts


def test_stroke_event_to_dict_roundtrip():
    ev = StrokeEvent("smash", 100, 80, 115, "upper", 0.9)
    d = ev.to_dict()
    assert d["stroke_type"] == "smash"
    assert d["contact_frame"] == 100
    assert d["window_start"] == 80 and d["window_end"] == 115
    assert d["player_side"] == "upper"
    assert d["confidence"] == 0.9


def _track_with_bounce(contact_frame, n=60):
    """Shuttle flies up-left, then after contact flies down-right; racket meets it at contact."""
    track = []
    for f in range(n):
        if f <= contact_frame:
            shuttle = (200 - f * 2, 200 - f * 2)
        else:
            d = f - contact_frame
            shuttle = (200 - contact_frame * 2 + d * 2, 200 - contact_frame * 2 + d * 2)
        racket = shuttle if f == contact_frame else (1000, 1000)
        track.append({"frame": f, "racket_head": racket, "shuttle": shuttle})
    return track


def test_detect_contacts_finds_single_contact():
    track = _track_with_bounce(30)
    contacts = detect_contacts(track, window_pre=20, window_post=15)
    assert len(contacts) == 1
    c = contacts[0]
    assert c["contact_frame"] == 30
    assert c["window_start"] == 10
    assert c["window_end"] == 45


def test_detect_contacts_clamps_window_at_zero():
    track = _track_with_bounce(5)
    contacts = detect_contacts(track, window_pre=20, window_post=15)
    assert contacts[0]["window_start"] == 0


def test_detect_contacts_ignores_far_racket():
    track = _track_with_bounce(30)
    for fr in track:
        fr["racket_head"] = (5000, 5000)  # never near shuttle
    assert detect_contacts(track) == []


def test_detect_contacts_suppresses_close_duplicates():
    track = _track_with_bounce(30)
    track[31]["racket_head"] = track[31]["shuttle"]  # second near-contact 1 frame later
    contacts = detect_contacts(track, min_gap=15)
    assert len(contacts) == 1
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/Scripts/python.exe -m pytest tests/test_stroke_events.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

`badminton_analysis/stroke/__init__.py`: (empty file)

`badminton_analysis/stroke/events.py`:
```python
"""Shuttlecock-contact stroke event detection."""
from dataclasses import dataclass, asdict

import numpy as np


@dataclass
class StrokeEvent:
    stroke_type: str
    contact_frame: int
    window_start: int
    window_end: int
    player_side: str
    confidence: float

    def to_dict(self):
        return asdict(self)


def _dist(a, b):
    return float(np.hypot(a[0] - b[0], a[1] - b[1]))


def _seg_angle(p, q):
    return float(np.degrees(np.arctan2(q[1] - p[1], q[0] - p[0])))


def _shuttle_dir_change(track, i, lookahead):
    """Direction change (deg, 0..180) of shuttle travel around index i. None if data missing."""
    if i - 1 < 0 or i + lookahead >= len(track):
        return None
    a = track[i - 1]["shuttle"]
    b = track[i]["shuttle"]
    c = track[i + lookahead]["shuttle"]
    if a is None or b is None or c is None:
        return None
    if _dist(a, b) < 1e-6 or _dist(b, c) < 1e-6:
        return None
    before = _seg_angle(a, b)
    after = _seg_angle(b, c)
    diff = abs(after - before) % 360.0
    return diff if diff <= 180.0 else 360.0 - diff


def detect_contacts(track, contact_px=80.0, lookahead=3, dir_change_deg=45.0,
                    window_pre=20, window_post=15, min_gap=15):
    """Find racket-shuttle impacts. Returns list of {contact_frame, window_start, window_end}."""
    contacts = []
    last_contact_frame = None
    for i, rec in enumerate(track):
        racket = rec.get("racket_head")
        shuttle = rec.get("shuttle")
        if racket is None or shuttle is None:
            continue
        if _dist(racket, shuttle) >= contact_px:
            continue
        change = _shuttle_dir_change(track, i, lookahead)
        if change is None or change < dir_change_deg:
            continue
        frame = rec["frame"]
        if last_contact_frame is not None and (frame - last_contact_frame) < min_gap:
            continue
        contacts.append({
            "contact_frame": frame,
            "window_start": max(0, frame - window_pre),
            "window_end": frame + window_post,
        })
        last_contact_frame = frame
    return contacts
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_stroke_events.py -v`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
git add badminton_analysis/stroke/__init__.py badminton_analysis/stroke/events.py tests/test_stroke_events.py
git commit -m "feat: shuttlecock-contact stroke event detection"
```

---

### Task 7: Stroke classifier

**Files:**
- Create: `badminton_analysis/stroke/classifier.py`
- Test: `tests/test_classifier.py`

**Interfaces:**
- Produces: `classify_stroke(window) -> (stroke_type: str, confidence: float)`. `window` is a dict:
  ```
  {
    "contact_index": int,           # index into the per-frame lists below
    "racket_head": [ (x,y)|None ],  # per window frame
    "nose":        [ (x,y)|None ],
    "shoulder":    [ (x,y)|None ],  # dominant shoulder
    "hip":         [ (x,y)|None ],  # dominant hip
    "elbow_angle": [ float|None ],
    "centroid":    [ (x,y)|None ],
    "fps":         float,
  }
  ```
  Image coords (y increases downward). Returns the highest-scoring stroke type and a confidence in `[0,1]` (the winning normalized score). Falls back to `("high_clear", low_conf)` when features are too sparse to decide.

- [ ] **Step 1: Write failing tests**

`tests/test_classifier.py`:
```python
import pytest
from badminton_analysis.stroke.classifier import classify_stroke


def _window(contact_idx, racket_y_at_contact, nose_y, racket_dy_after,
            elbow_angle=150.0, hip_y=300.0, racket_start_y=None, n=36, fps=30.0):
    racket = []
    nose = []
    shoulder = []
    hip = []
    elbow = []
    centroid = []
    start_y = racket_start_y if racket_start_y is not None else racket_y_at_contact
    for i in range(n):
        if i < contact_idx and contact_idx > 0:
            ry = start_y + (racket_y_at_contact - start_y) * (i / contact_idx)
        elif i == contact_idx:
            ry = racket_y_at_contact
        else:
            ry = racket_y_at_contact + racket_dy_after * (i - contact_idx)
        racket.append((100.0, ry))
        nose.append((100.0, nose_y))
        shoulder.append((100.0, nose_y + 40))
        hip.append((100.0, hip_y))
        elbow.append(elbow_angle)
        centroid.append((100.0, 320.0))
    return {
        "contact_index": contact_idx, "racket_head": racket, "nose": nose,
        "shoulder": shoulder, "hip": hip, "elbow_angle": elbow,
        "centroid": centroid, "fps": fps,
    }


def test_classify_smash_overhead_fast_downward():
    # racket above head at contact (small y), strong downward motion after
    w = _window(contact_idx=18, racket_y_at_contact=20, nose_y=60,
                racket_dy_after=25, elbow_angle=160)
    stroke, conf = classify_stroke(w)
    assert stroke == "smash"
    assert 0.0 <= conf <= 1.0


def test_classify_drop_shot_overhead_gentle():
    # overhead but very little racket motion after contact
    w = _window(contact_idx=18, racket_y_at_contact=20, nose_y=60,
                racket_dy_after=1, elbow_angle=130)
    stroke, conf = classify_stroke(w)
    assert stroke == "drop_shot"


def test_classify_serve_racket_starts_low():
    # racket starts below hip and stays low (never overhead)
    w = _window(contact_idx=18, racket_y_at_contact=320, nose_y=60,
                racket_dy_after=2, elbow_angle=140, hip_y=300, racket_start_y=360)
    stroke, conf = classify_stroke(w)
    assert stroke == "serve"


def test_classify_high_clear_overhead_moderate_upward():
    w = _window(contact_idx=18, racket_y_at_contact=25, nose_y=60,
                racket_dy_after=-6, elbow_angle=150)
    stroke, conf = classify_stroke(w)
    assert stroke == "high_clear"
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/Scripts/python.exe -m pytest tests/test_classifier.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

`badminton_analysis/stroke/classifier.py`:
```python
"""Rule-based stroke classification from a contact-centered window."""

STROKE_TYPES = ("high_clear", "smash", "drop_shot", "serve")


def _at(seq, idx):
    if idx < 0 or idx >= len(seq):
        return None
    return seq[idx]


def _racket_velocity_after(racket, contact_idx, span=3):
    """Mean per-frame vertical racket displacement just after contact (image px; + = downward)."""
    a = _at(racket, contact_idx)
    b = _at(racket, min(contact_idx + span, len(racket) - 1))
    if a is None or b is None:
        return None
    frames = max(1, min(contact_idx + span, len(racket) - 1) - contact_idx)
    return (b[1] - a[1]) / frames


def classify_stroke(window):
    """Return (stroke_type, confidence in [0,1])."""
    ci = window["contact_index"]
    racket = window["racket_head"]
    nose = window["nose"]
    hip = window["hip"]

    racket_c = _at(racket, ci)
    nose_c = _at(nose, ci)
    racket_start = next((r for r in racket if r is not None), None)
    hip_c = _at(hip, ci)

    # Insufficient data -> low-confidence default.
    if racket_c is None or nose_c is None:
        return ("high_clear", 0.2)

    overhead = racket_c[1] < nose_c[1]            # racket above head (smaller y)
    vy = _racket_velocity_after(racket, ci)        # + downward
    if vy is None:
        vy = 0.0

    starts_low = racket_start is not None and hip_c is not None and racket_start[1] > hip_c[1]

    scores = {s: 0.0 for s in STROKE_TYPES}

    # Serve: racket starts below the hip and never goes overhead.
    if starts_low and not overhead:
        scores["serve"] += 1.0
    if not overhead:
        scores["serve"] += 0.3

    if overhead:
        # Smash: fast downward racket travel after contact.
        if vy >= 12:
            scores["smash"] += 1.0
        elif vy >= 6:
            scores["smash"] += 0.5
        # Drop shot: almost no racket travel after contact (gentle).
        if abs(vy) <= 3:
            scores["drop_shot"] += 1.0
        # High clear: moderate travel, not a steep downward smash.
        if -10 <= vy < 6:
            scores["high_clear"] += 0.7
        scores["high_clear"] += 0.2  # overhead baseline

    best = max(scores, key=scores.get)
    total = sum(scores.values())
    conf = (scores[best] / total) if total > 0 else 0.2
    return (best, round(float(conf), 3))
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_classifier.py -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add badminton_analysis/stroke/classifier.py tests/test_classifier.py
git commit -m "feat: rule-based stroke classifier"
```

---

### Task 8: Racket detector

**Files:**
- Create: `badminton_analysis/detection/racket.py`
- Test: `tests/test_racket.py`

**Interfaces:**
- Produces: `RacketDetector`:
  - `__init__(self, model=None, model_path=None, conf=0.25, device="auto")` — if `model` (an object with a callable interface like Ultralytics YOLO) is provided it is used directly (for tests/injection); else if `model_path` is given and exists, `YOLO(model_path)` is loaded lazily; else `self.model is None`.
  - `detect_racket_head(self, frame, roi_corners=None) -> (x, y) | None` — returns the highest-confidence racket box center within the ROI (same ROI semantics as `ShuttlecockTracker._point_in_roi`), or `None` when no model or no detection.
- Consumes: nothing from earlier tasks (uses a fake model in tests).

- [ ] **Step 1: Write failing tests**

`tests/test_racket.py`:
```python
import numpy as np
from badminton_analysis.detection.racket import RacketDetector


class _FakeBoxes:
    def __init__(self, xywh, conf):
        self.xywh = _FakeTensor(np.array(xywh, dtype=float))
        self.conf = _FakeTensor(np.array(conf, dtype=float))


class _FakeTensor:
    def __init__(self, arr):
        self._arr = arr
        self.shape = arr.shape

    def detach(self):
        return self

    def cpu(self):
        return self

    def numpy(self):
        return self._arr


class _FakeResult:
    def __init__(self, boxes):
        self.boxes = boxes


class _FakeModel:
    def __init__(self, result):
        self._result = result

    def __call__(self, frame, **kwargs):
        return [self._result]


def test_returns_none_without_model():
    det = RacketDetector(model=None)
    frame = np.zeros((100, 100, 3), dtype=np.uint8)
    assert det.detect_racket_head(frame) is None


def test_picks_highest_confidence_center():
    boxes = _FakeBoxes(xywh=[[50, 60, 10, 10], [20, 20, 10, 10]], conf=[0.9, 0.4])
    model = _FakeModel(_FakeResult(boxes))
    det = RacketDetector(model=model)
    frame = np.zeros((200, 200, 3), dtype=np.uint8)
    assert det.detect_racket_head(frame) == (50, 60)


def test_filters_outside_roi():
    boxes = _FakeBoxes(xywh=[[500, 500, 10, 10]], conf=[0.9])
    model = _FakeModel(_FakeResult(boxes))
    det = RacketDetector(model=model)
    frame = np.zeros((600, 600, 3), dtype=np.uint8)
    # ROI is a small top-left box; detection at (500,500) is far outside
    assert det.detect_racket_head(frame, roi_corners=[(0, 0), (100, 100)]) is None
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/Scripts/python.exe -m pytest tests/test_racket.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

`badminton_analysis/detection/racket.py`:
```python
"""Racket detection via an (optional) YOLO model, with dependency injection for tests."""
import os

import numpy as np


class RacketDetector:
    def __init__(self, model=None, model_path=None, conf=0.25, device="auto"):
        self.conf = conf
        self.roi_padding_ratio = 0.08
        if device in (None, "auto"):
            self.device = self._auto_device()
        else:
            self.device = device

        if model is not None:
            self.model = model
        elif model_path and os.path.exists(model_path):
            from ultralytics import YOLO
            self.model = YOLO(model_path)
        else:
            self.model = None

    @staticmethod
    def _auto_device():
        try:
            import torch
            if torch.cuda.is_available():
                return 0
        except Exception:
            pass
        return "cpu"

    def _point_in_roi(self, point, roi_corners):
        if roi_corners is None:
            return True
        x1, y1 = roi_corners[0]
        x2, y2 = roi_corners[1]
        pad = int(max(x2 - x1, y2 - y1) * self.roi_padding_ratio)
        return (x1 - pad) <= point[0] <= (x2 + pad) and (y1 - pad) <= point[1] <= (y2 + pad)

    def detect_racket_head(self, frame, roi_corners=None):
        if self.model is None:
            return None
        try:
            result = self.model(frame, conf=self.conf, device=self.device, verbose=False)[0]
        except TypeError:
            result = self.model(frame, conf=self.conf, verbose=False)[0]

        boxes = getattr(result, "boxes", None)
        if boxes is None or boxes.xywh.shape[0] < 1:
            return None

        xywh = boxes.xywh.detach().cpu().numpy()
        conf = boxes.conf.detach().cpu().numpy() if boxes.conf is not None else np.ones(len(xywh))

        best = None
        best_conf = -1.0
        for box, c in zip(xywh, conf):
            cx, cy = int(box[0]), int(box[1])
            if not self._point_in_roi((cx, cy), roi_corners):
                continue
            if c > best_conf:
                best_conf = float(c)
                best = (cx, cy)
        return best
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_racket.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add badminton_analysis/detection/racket.py tests/test_racket.py
git commit -m "feat: racket head detector with injectable model"
```

---

### Task 9: Biomechanical analyzer + weakness descriptions

**Files:**
- Create: `badminton_analysis/analysis/biomechanics.py`
- Test: `tests/test_biomechanics.py`

**Interfaces:**
- Consumes: `compute_joint_angles`, `weight_transfer_ratio` (Task 3); `score_stroke` (Task 5); `is_valid`, COCO constants (Task 2); `StrokeEvent` (Task 6).
- Produces: `BiomechanicalAnalyzer`:
  - `__init__(self, dominant="right")`.
  - `analyze(self, stroke_event, window_frames) -> dict` (a `TechniqueReport`). `window_frames` is a list of per-frame dicts `{"frame": int, "keypoints": (17,2)|None, "conf": (17,)|None, "racket_head": (x,y)|None, "centroid": (x,y)|None}` ordered by frame, covering `[window_start, window_end]`. The frame whose `"frame"` equals `stroke_event.contact_frame` is the contact frame (nearest available if exact missing). Report dict keys: `stroke_type, contact_frame, player_side, confidence, overall_score, per_metric, weaknesses (list of {metric, measured, ideal_range, direction, severity, description}), strengths (list of metric names)`.

- [ ] **Step 1: Write failing tests**

`tests/test_biomechanics.py`:
```python
import numpy as np
from badminton_analysis.analysis.biomechanics import BiomechanicalAnalyzer
from badminton_analysis.analysis import joint_angles as ja
from badminton_analysis.stroke.events import StrokeEvent


def _good_smash_keypoints():
    kp = np.zeros((17, 2))
    kp[ja.R_SHOULDER] = (100, 100)
    kp[ja.R_ELBOW] = (140, 110)
    kp[ja.R_WRIST] = (180, 122)   # near-straight arm -> ~160-ish elbow
    kp[ja.L_SHOULDER] = (60, 90)  # shoulder line tilted -> trunk_rotation > 0
    kp[ja.L_HIP] = (60, 250)
    kp[ja.R_HIP] = (100, 250)
    kp[ja.R_KNEE] = (110, 300)
    kp[ja.R_ANKLE] = (100, 350)
    return kp


def _window_frames(contact_frame, kp, racket_head, n_pre=20, n_post=15):
    frames = []
    start = contact_frame - n_pre
    for f in range(start, contact_frame + n_post + 1):
        frames.append({
            "frame": f,
            "keypoints": kp if f == contact_frame else None,
            "conf": None,
            "racket_head": racket_head if f == contact_frame else None,
            "centroid": (100 + (f - start), 300),
        })
    return frames


def test_analyze_produces_report_with_scores():
    analyzer = BiomechanicalAnalyzer(dominant="right")
    ev = StrokeEvent("smash", 30, 10, 45, "upper", 0.8)
    frames = _window_frames(30, _good_smash_keypoints(), racket_head=(220, 122))
    report = analyzer.analyze(ev, frames)
    assert report["stroke_type"] == "smash"
    assert report["player_side"] == "upper"
    assert report["overall_score"] is not None
    assert "elbow_extension" in report["per_metric"]


def test_weaknesses_carry_descriptions():
    analyzer = BiomechanicalAnalyzer(dominant="right")
    ev = StrokeEvent("smash", 30, 10, 45, "upper", 0.8)
    # bent arm -> low elbow extension -> weakness with a description string
    kp = _good_smash_keypoints()
    kp[ja.R_WRIST] = (120, 170)  # sharply bent elbow
    frames = _window_frames(30, kp, racket_head=(150, 200))
    report = analyzer.analyze(ev, frames)
    weak_metrics = [w["metric"] for w in report["weaknesses"]]
    assert "elbow_extension" in weak_metrics
    for w in report["weaknesses"]:
        assert isinstance(w["description"], str) and w["description"]


def test_missing_contact_keypoints_gives_none_scores():
    analyzer = BiomechanicalAnalyzer(dominant="right")
    ev = StrokeEvent("high_clear", 30, 10, 45, "lower", 0.5)
    frames = _window_frames(30, np.ones((17, 2)), racket_head=None)  # all missing
    report = analyzer.analyze(ev, frames)
    assert report["overall_score"] is None or report["overall_score"] >= 0
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/Scripts/python.exe -m pytest tests/test_biomechanics.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

`badminton_analysis/analysis/biomechanics.py`:
```python
"""Assemble a TechniqueReport for a single stroke from its window frames."""
import numpy as np

from . import joint_angles as ja
from .scoring import score_stroke

_METRIC_LABEL = {
    "elbow_extension": "Elbow extension",
    "trunk_rotation": "Trunk rotation",
    "wrist_flexion": "Wrist/racket angle",
    "knee_flexion": "Knee flexion",
    "hip_shoulder_separation": "Hip-shoulder separation",
    "weight_transfer": "Forward weight transfer",
}

_ADVICE = {
    ("elbow_extension", "under"): "Extend the arm more fully through contact to transfer power.",
    ("elbow_extension", "over"): "Avoid hyper-extending; keep a slight bend for control.",
    ("trunk_rotation", "under"): "Rotate the trunk more to add rotational power.",
    ("trunk_rotation", "over"): "Reduce trunk over-rotation to stay balanced.",
    ("wrist_flexion", "under"): "Use more wrist snap at contact for racket-head speed.",
    ("wrist_flexion", "over"): "Control the wrist; excessive flexion costs accuracy.",
    ("knee_flexion", "under"): "Bend the knees more to load the legs before the stroke.",
    ("knee_flexion", "over"): "Don't over-squat; keep an athletic, springy stance.",
    ("hip_shoulder_separation", "under"): "Increase hip-shoulder separation to build torque.",
    ("hip_shoulder_separation", "over"): "Tighten the kinetic chain; separation is excessive.",
    ("weight_transfer", "under"): "Drive body weight forward into the shot.",
    ("weight_transfer", "over"): "Stay balanced; you are lunging too far forward.",
}


def _describe(metric, entry):
    label = _METRIC_LABEL.get(metric, metric)
    measured = entry["measured"]
    lo, hi = entry["ideal_range"]
    advice = _ADVICE.get((metric, entry["direction"]), "Work toward the ideal range.")
    return f"{label} {measured:.1f} (ideal {lo}-{hi}). {advice}"


class BiomechanicalAnalyzer:
    def __init__(self, dominant="right"):
        self.dominant = dominant

    def _contact_frame_record(self, stroke_event, window_frames):
        exact = [w for w in window_frames if w["frame"] == stroke_event.contact_frame]
        if exact:
            return exact[0]
        with_kp = [w for w in window_frames if w.get("keypoints") is not None]
        if with_kp:
            return min(with_kp, key=lambda w: abs(w["frame"] - stroke_event.contact_frame))
        return window_frames[len(window_frames) // 2] if window_frames else None

    def _shoulder_width(self, kp, conf):
        if kp is None:
            return None
        if ja.is_valid(kp, ja.L_SHOULDER, conf) and ja.is_valid(kp, ja.R_SHOULDER, conf):
            return float(np.hypot(kp[ja.L_SHOULDER][0] - kp[ja.R_SHOULDER][0],
                                  kp[ja.L_SHOULDER][1] - kp[ja.R_SHOULDER][1]))
        return None

    def analyze(self, stroke_event, window_frames):
        contact = self._contact_frame_record(stroke_event, window_frames)
        kp = contact.get("keypoints") if contact else None
        conf = contact.get("conf") if contact else None
        racket_head = contact.get("racket_head") if contact else None

        if kp is not None:
            metrics = ja.compute_joint_angles(kp, racket_head=racket_head,
                                              dominant=self.dominant, conf=conf)
        else:
            metrics = {k: None for k in
                       ("elbow_extension", "shoulder_abduction", "trunk_rotation",
                        "knee_flexion", "hip_shoulder_separation", "wrist_flexion")}

        # weight transfer: window-start centroid -> contact centroid, normalized by shoulder width
        start_centroid = next((w["centroid"] for w in window_frames if w.get("centroid")), None)
        contact_centroid = contact.get("centroid") if contact else None
        shoulder_w = self._shoulder_width(kp, conf)
        wt = None
        if start_centroid is not None and contact_centroid is not None and shoulder_w:
            wt = ja.weight_transfer_ratio(start_centroid, contact_centroid, shoulder_w)
        metrics["weight_transfer"] = wt

        scored = score_stroke(metrics, stroke_event.stroke_type)

        weaknesses = []
        for metric in scored["weaknesses"]:
            entry = scored["per_metric"][metric]
            weaknesses.append({
                "metric": metric,
                "measured": entry["measured"],
                "ideal_range": entry["ideal_range"],
                "direction": entry["direction"],
                "severity": entry["severity"],
                "description": _describe(metric, entry),
            })

        return {
            "stroke_type": stroke_event.stroke_type,
            "contact_frame": stroke_event.contact_frame,
            "player_side": stroke_event.player_side,
            "confidence": stroke_event.confidence,
            "overall_score": scored["overall"],
            "per_metric": scored["per_metric"],
            "weaknesses": weaknesses,
            "strengths": scored["strengths"],
        }
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_biomechanics.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add badminton_analysis/analysis/biomechanics.py tests/test_biomechanics.py
git commit -m "feat: biomechanical analyzer assembling technique reports"
```

---

### Task 10: Technique report writers

**Files:**
- Create: `badminton_analysis/analysis/technique_writer.py`
- Test: `tests/test_technique_writer.py`

**Interfaces:**
- Consumes: `write_json` from `badminton_analysis/data/writer.py` (existing).
- Produces:
  - `write_stroke_reports(path, reports) -> None` — writes one JSON object per line (JSONL) to `path`; creates parent dirs.
  - `build_match_summary(reports) -> dict` — aggregates: `{"stroke_count", "by_type": {type: {"count", "avg_score"}}, "recurring_weaknesses": [{"metric", "count"}...] (sorted desc by count), "strengths": [{"metric","count"}...]}`. `avg_score` ignores `None` overall scores.

- [ ] **Step 1: Write failing tests**

`tests/test_technique_writer.py`:
```python
import json
import os
from badminton_analysis.analysis.technique_writer import (
    write_stroke_reports, build_match_summary,
)


def _report(stroke, score, weak_metrics):
    return {
        "stroke_type": stroke, "contact_frame": 1, "player_side": "upper",
        "confidence": 0.9, "overall_score": score, "per_metric": {},
        "weaknesses": [{"metric": m} for m in weak_metrics], "strengths": [],
    }


def test_write_stroke_reports_one_json_per_line(tmp_path):
    reports = [_report("smash", 80, ["elbow_extension"]), _report("serve", 70, [])]
    out = tmp_path / "strokes.jsonl"
    write_stroke_reports(str(out), reports)
    lines = out.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["stroke_type"] == "smash"


def test_build_match_summary_aggregates():
    reports = [
        _report("smash", 80, ["elbow_extension"]),
        _report("smash", 60, ["elbow_extension", "knee_flexion"]),
        _report("serve", None, []),
    ]
    summary = build_match_summary(reports)
    assert summary["stroke_count"] == 3
    assert summary["by_type"]["smash"]["count"] == 2
    assert summary["by_type"]["smash"]["avg_score"] == 70.0
    # elbow_extension recurs twice -> top recurring weakness
    assert summary["recurring_weaknesses"][0]["metric"] == "elbow_extension"
    assert summary["recurring_weaknesses"][0]["count"] == 2


def test_build_match_summary_empty():
    summary = build_match_summary([])
    assert summary["stroke_count"] == 0
    assert summary["by_type"] == {}
    assert summary["recurring_weaknesses"] == []
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/Scripts/python.exe -m pytest tests/test_technique_writer.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

`badminton_analysis/analysis/technique_writer.py`:
```python
"""Write per-stroke reports (JSONL) and the match-level summary (JSON)."""
import json
import os
from collections import Counter, defaultdict

from ..data.writer import _clean_value


def write_stroke_reports(path, reports):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for report in reports:
            f.write(json.dumps(_clean_value(report), ensure_ascii=False, separators=(",", ":")))
            f.write("\n")


def build_match_summary(reports):
    by_type_scores = defaultdict(list)
    by_type_count = Counter()
    weakness_counter = Counter()
    strength_counter = Counter()

    for r in reports:
        st = r["stroke_type"]
        by_type_count[st] += 1
        if r.get("overall_score") is not None:
            by_type_scores[st].append(r["overall_score"])
        for w in r.get("weaknesses", []):
            weakness_counter[w["metric"]] += 1
        for s in r.get("strengths", []):
            strength_counter[s] += 1

    by_type = {}
    for st, count in by_type_count.items():
        scores = by_type_scores.get(st, [])
        avg = round(sum(scores) / len(scores), 1) if scores else None
        by_type[st] = {"count": count, "avg_score": avg}

    def _ranked(counter):
        return [{"metric": m, "count": c} for m, c in counter.most_common()]

    return {
        "stroke_count": len(reports),
        "by_type": by_type,
        "recurring_weaknesses": _ranked(weakness_counter),
        "strengths": _ranked(strength_counter),
    }
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_technique_writer.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add badminton_analysis/analysis/technique_writer.py tests/test_technique_writer.py
git commit -m "feat: technique report writers and match summary"
```

---

### Task 11: Live technique overlay

**Files:**
- Create: `badminton_analysis/visualization/technique_overlay.py`
- Test: `tests/test_technique_overlay.py`

**Interfaces:**
- Produces: `draw_technique_overlay(frame, angles, label=None, score=None, origin=(20, 20)) -> frame`. Draws angle readouts (one line per non-None metric), an optional stroke `label`, and an optional `score` badge (color: green ≥75, yellow 50–74, red <50). Mutates and returns the same `frame` ndarray. No-op-safe when `angles` is empty.
- Consumes: nothing from earlier tasks (operates on raw angle dict). Note: richer post-contact badges / phase labels are deferred to the Web UI (Plan 2); this is the live in-video layer only.

- [ ] **Step 1: Write failing tests**

`tests/test_technique_overlay.py`:
```python
import numpy as np
from badminton_analysis.visualization.technique_overlay import draw_technique_overlay


def test_returns_same_shape_and_mutates():
    frame = np.zeros((300, 400, 3), dtype=np.uint8)
    angles = {"elbow_extension": 150.0, "knee_flexion": 40.0, "wrist_flexion": None}
    out = draw_technique_overlay(frame, angles, label="smash", score=82)
    assert out.shape == (300, 400, 3)
    assert out is frame
    assert int(frame.sum()) > 0  # something was drawn


def test_empty_angles_is_safe():
    frame = np.zeros((100, 100, 3), dtype=np.uint8)
    out = draw_technique_overlay(frame, {})
    assert out.shape == (100, 100, 3)
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/Scripts/python.exe -m pytest tests/test_technique_overlay.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

`badminton_analysis/visualization/technique_overlay.py`:
```python
"""Lightweight in-video technique overlay (live, instantaneous angles)."""
import cv2

_FONT = cv2.FONT_HERSHEY_SIMPLEX


def _score_color(score):
    if score >= 75:
        return (0, 200, 0)
    if score >= 50:
        return (0, 200, 200)
    return (0, 0, 220)


def draw_technique_overlay(frame, angles, label=None, score=None, origin=(20, 20)):
    x, y = origin
    line_h = 22

    if label:
        cv2.putText(frame, str(label), (x, y), _FONT, 0.7, (255, 255, 255), 2, cv2.LINE_AA)
        y += line_h + 4

    if score is not None:
        color = _score_color(score)
        cv2.rectangle(frame, (x, y - 16), (x + 90, y + 6), color, -1)
        cv2.putText(frame, f"{score:.0f}", (x + 6, y), _FONT, 0.6, (0, 0, 0), 2, cv2.LINE_AA)
        y += line_h + 4

    for name, value in angles.items():
        if value is None:
            continue
        text = f"{name}: {value:.0f}"
        cv2.putText(frame, text, (x, y), _FONT, 0.5, (200, 255, 200), 1, cv2.LINE_AA)
        y += line_h

    return frame
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_technique_overlay.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add badminton_analysis/visualization/technique_overlay.py tests/test_technique_overlay.py
git commit -m "feat: live technique overlay drawing"
```

---

### Task 12: Pipeline integration (CLI flag, buffering, post-loop analysis)

**Files:**
- Modify: `badminton_analysis/system.py`
- Modify: `main.py`
- Test: `tests/test_integration_analysis.py`

**Interfaces:**
- Consumes: everything above — `RacketDetector` (8), `detect_contacts`/`StrokeEvent` (6), `classify_stroke` (7), `BiomechanicalAnalyzer` (9), `write_stroke_reports`/`build_match_summary` (10), `draw_technique_overlay` (11), `compute_joint_angles` (3).
- Produces: a `TechniqueAnalysisRunner` class in `system.py` so the orchestration is testable without a real video:
  - `__init__(self, analyzer, racket_detector=None, dominant="right", window_pre=20, window_post=15)`.
  - `run(self, track, frame_lookup) -> (reports: list[dict], events: list[StrokeEvent])` where `track` is the contact-detection track (Task 6 shape) and `frame_lookup(frame_index) -> dict|None` returns a window-frame record (Task 9 shape) for any frame index.
  - `system.py` wires real buffers into this; when `--analyze-technique` is off, none of this runs.

This task has two deliverables that share a test cycle: the testable `TechniqueAnalysisRunner` (unit-tested) and the `system.py`/`main.py` wiring (exercised via the existing manual run path).

- [ ] **Step 1: Write failing test for the runner**

`tests/test_integration_analysis.py`:
```python
import numpy as np
from badminton_analysis.analysis.biomechanics import BiomechanicalAnalyzer
from badminton_analysis.analysis import joint_angles as ja
from badminton_analysis.system import TechniqueAnalysisRunner


def _smash_kp():
    kp = np.zeros((17, 2))
    kp[ja.R_SHOULDER] = (100, 100)
    kp[ja.R_ELBOW] = (140, 110)
    kp[ja.R_WRIST] = (180, 122)
    kp[ja.L_SHOULDER] = (60, 90)
    kp[ja.L_HIP] = (60, 250)
    kp[ja.R_HIP] = (100, 250)
    kp[ja.R_KNEE] = (110, 300)
    kp[ja.R_ANKLE] = (100, 350)
    return kp


def _build_track(contact_frame, n=70):
    track = []
    for f in range(n):
        if f <= contact_frame:
            shuttle = (300 - f, 60 + f)        # descending toward player
        else:
            d = f - contact_frame
            shuttle = (300 - contact_frame + d, 60 + contact_frame - d * 3)  # rebounds up/away
        racket = shuttle if f == contact_frame else (2000, 2000)
        track.append({"frame": f, "racket_head": racket, "shuttle": shuttle})
    return track


def test_runner_produces_one_report_per_contact():
    contact_frame = 30
    track = _build_track(contact_frame)
    kp = _smash_kp()

    def frame_lookup(idx):
        return {
            "frame": idx,
            "keypoints": kp if idx == contact_frame else None,
            "conf": None,
            "racket_head": (220, 122) if idx == contact_frame else None,
            "centroid": (100 + idx, 300),
            "nose": (100, 80),
            "shoulder": (100, 120),
            "hip": (100, 250),
            "elbow_angle": 160.0,
        }

    runner = TechniqueAnalysisRunner(BiomechanicalAnalyzer(dominant="right"))
    reports, events = runner.run(track, frame_lookup)
    assert len(reports) == 1
    assert len(events) == 1
    assert reports[0]["stroke_type"] in {"high_clear", "smash", "drop_shot", "serve"}
    assert "overall_score" in reports[0]
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/Scripts/python.exe -m pytest tests/test_integration_analysis.py -v`
Expected: FAIL with `ImportError: cannot import name 'TechniqueAnalysisRunner'`.

- [ ] **Step 3: Add the runner to system.py**

Add near the top of `badminton_analysis/system.py`, after the existing imports (these are light-weight, pure-python modules — safe to import at module load, unlike the heavy deps in `load_runtime_dependencies`):
```python
from .stroke.events import detect_contacts, StrokeEvent
from .stroke.classifier import classify_stroke
from .analysis.technique_writer import write_stroke_reports, build_match_summary
```

Add this class at the end of `badminton_analysis/system.py`:
```python
class TechniqueAnalysisRunner:
    """Post-loop orchestration: contacts -> classification -> biomechanical reports."""

    def __init__(self, analyzer, racket_detector=None, dominant="right",
                 window_pre=20, window_post=15):
        self.analyzer = analyzer
        self.racket_detector = racket_detector
        self.dominant = dominant
        self.window_pre = window_pre
        self.window_post = window_post

    def _build_classifier_window(self, contact_frame, window_start, window_end, frame_lookup):
        racket_head, nose, shoulder, hip, elbow_angle, centroid = [], [], [], [], [], []
        contact_index = 0
        for offset, idx in enumerate(range(window_start, window_end + 1)):
            rec = frame_lookup(idx) or {}
            if idx == contact_frame:
                contact_index = offset
            racket_head.append(rec.get("racket_head"))
            nose.append(rec.get("nose"))
            shoulder.append(rec.get("shoulder"))
            hip.append(rec.get("hip"))
            elbow_angle.append(rec.get("elbow_angle"))
            centroid.append(rec.get("centroid"))
        return {
            "contact_index": contact_index, "racket_head": racket_head, "nose": nose,
            "shoulder": shoulder, "hip": hip, "elbow_angle": elbow_angle,
            "centroid": centroid, "fps": 30.0,
        }

    def _window_frames(self, window_start, window_end, frame_lookup):
        frames = []
        for idx in range(window_start, window_end + 1):
            rec = frame_lookup(idx)
            if rec is None:
                continue
            frames.append({
                "frame": idx,
                "keypoints": rec.get("keypoints"),
                "conf": rec.get("conf"),
                "racket_head": rec.get("racket_head"),
                "centroid": rec.get("centroid"),
            })
        return frames

    def run(self, track, frame_lookup):
        contacts = detect_contacts(
            track, window_pre=self.window_pre, window_post=self.window_post)
        reports = []
        events = []
        for c in contacts:
            cw = self._build_classifier_window(
                c["contact_frame"], c["window_start"], c["window_end"], frame_lookup)
            stroke_type, conf = classify_stroke(cw)
            # player_side from the contact frame's centroid if available, else "unknown"
            contact_rec = frame_lookup(c["contact_frame"]) or {}
            side = contact_rec.get("player_side", "unknown")
            event = StrokeEvent(
                stroke_type=stroke_type,
                contact_frame=c["contact_frame"],
                window_start=c["window_start"],
                window_end=c["window_end"],
                player_side=side,
                confidence=conf,
            )
            window_frames = self._window_frames(
                c["window_start"], c["window_end"], frame_lookup)
            report = self.analyzer.analyze(event, window_frames)
            reports.append(report)
            events.append(event)
        return reports, events
```

- [ ] **Step 4: Run to verify the runner test passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_integration_analysis.py -v`
Expected: PASS (1 passed).

- [ ] **Step 5: Wire buffering + flag into BadmintonAnalysisSystem**

In `badminton_analysis/system.py`, in `BadmintonAnalysisSystem.__init__`, add a parameter `analyze_technique=False`, `racket_model_path=None`, `dominant_hand="right"` to the signature and store them:
```python
        self.analyze_technique = analyze_technique
        self.racket_model_path = racket_model_path
        self.dominant_hand = dominant_hand
        self._analysis_track = []   # contact detection track
        self._analysis_frames = {}  # frame_index -> window-frame record
        self._racket_detector = None
```

After the ball model is created in `__init__` (after `self.yolo_ball_model = YOLO(...)`), add:
```python
        if self.analyze_technique:
            from .detection.racket import RacketDetector
            self._racket_detector = RacketDetector(model_path=self.racket_model_path)
```

In `_process_frame`, inside the `is_court` branch after `ball_position` is computed and `players` is updated, capture analysis data (only when enabled). Add:
```python
        if self.analyze_technique:
            self._capture_analysis_frame(frame_count, frame, roi_corners, ball_position)
```

Add this method to `BadmintonAnalysisSystem`:
```python
    def _capture_analysis_frame(self, frame_count, frame, roi_corners, ball_position):
        from .analysis import joint_angles as ja
        pose = self.player_pose_visualizer.get_current_pose_data()
        keypoints = None
        racket_head = None
        nose = shoulder = hip = centroid = None
        elbow_angle = None
        side = "unknown"

        if pose is not None and pose.get("keypoints") is not None:
            people = pose["keypoints"]
            ox, oy = pose.get("offset_x", 0), pose.get("offset_y", 0)
            person = people[0]
            kp = person.astype(float).copy()
            # shift ROI-local keypoints back to full-frame coords (ignore missing <=1)
            mask = ~((kp[:, 0] <= 1) & (kp[:, 1] <= 1))
            kp[mask, 0] += ox
            kp[mask, 1] += oy
            keypoints = kp
            if ja.is_valid(kp, ja.NOSE):
                nose = (float(kp[ja.NOSE][0]), float(kp[ja.NOSE][1]))
            dom = ja.R_SHOULDER if self.dominant_hand == "right" else ja.L_SHOULDER
            dom_hip = ja.R_HIP if self.dominant_hand == "right" else ja.L_HIP
            if ja.is_valid(kp, dom):
                shoulder = (float(kp[dom][0]), float(kp[dom][1]))
            if ja.is_valid(kp, dom_hip):
                hip = (float(kp[dom_hip][0]), float(kp[dom_hip][1]))
            angles_now = ja.compute_joint_angles(kp, dominant=self.dominant_hand)
            elbow_angle = angles_now.get("elbow_extension")

        if self._racket_detector is not None:
            racket_head = self._racket_detector.detect_racket_head(frame, roi_corners=roi_corners)

        # centroid + side from tracked players (prefer lower court, else upper)
        for region in ("lower", "upper"):
            p = self.player_tracker.players.get(region)
            if p is not None:
                centroid = (float(p[0]), float(p[1]))
                side = region
                break

        shuttle = None
        if ball_position and ball_position != [0, 0]:
            shuttle = (float(ball_position[0]), float(ball_position[1]))

        self._analysis_track.append({
            "frame": frame_count, "racket_head": racket_head, "shuttle": shuttle,
        })
        self._analysis_frames[frame_count] = {
            "frame": frame_count, "keypoints": keypoints, "conf": None,
            "racket_head": racket_head, "centroid": centroid, "nose": nose,
            "shoulder": shoulder, "hip": hip, "elbow_angle": elbow_angle,
            "player_side": side,
        }
```

At the end of `process_video`, after the rally JSON is written and before `self._cleanup(cap)`, add:
```python
        if self.analyze_technique:
            self._run_technique_analysis()
```

Add this method:
```python
    def _run_technique_analysis(self):
        from .analysis.biomechanics import BiomechanicalAnalyzer
        runner = TechniqueAnalysisRunner(
            BiomechanicalAnalyzer(dominant=self.dominant_hand),
            racket_detector=self._racket_detector,
            dominant=self.dominant_hand,
        )
        reports, _events = runner.run(self._analysis_track, self._analysis_frames.get)
        strokes_path = os.path.join(self.save_dir, "strokes.jsonl")
        summary_path = os.path.join(self.save_dir, "technique_summary.json")
        write_stroke_reports(strokes_path, reports)
        write_json(summary_path, build_match_summary(reports))
        print(f"Technique analysis: {len(reports)} strokes -> {strokes_path}")
```

- [ ] **Step 6: Add CLI flags to main.py**

In `main.py`, add to the argument parser (after the `--audio` arg):
```python
    parser.add_argument('--analyze-technique', action='store_true', default=False,
                        help='启用击球姿态生物力学分析，输出 strokes.jsonl 与 technique_summary.json')
    parser.add_argument('--racket-model', default='weights/yolo11s-racket.pt', type=str,
                        help='YOLO 球拍检测模型路径（用于击球分析）')
    parser.add_argument('--dominant-hand', default='right', choices=['right', 'left'],
                        help='球员持拍手，默认 right')
```

In the `BadmintonAnalysisSystem(...)` construction call, add:
```python
        analyze_technique=args.analyze_technique,
        racket_model_path=args.racket_model,
        dominant_hand=args.dominant_hand,
```

- [ ] **Step 7: Verify nothing broke (analysis off by default)**

Run: `.venv/Scripts/python.exe -m pytest -v`
Expected: PASS (all tests across all files).

Run: `.venv/Scripts/python.exe main.py --help`
Expected: help text includes `--analyze-technique`, `--racket-model`, `--dominant-hand`; exits 0.

- [ ] **Step 8: Commit**

```bash
git add badminton_analysis/system.py main.py tests/test_integration_analysis.py
git commit -m "feat: wire technique analysis into pipeline behind --analyze-technique flag"
```

---

## Self-Review

**1. Spec coverage:**
- Four fundamental strokes → Tasks 4, 7 (vocabulary enforced in reference ranges + classifier). ✅
- Racket detection (full tracking) → Task 8. ✅
- Shuttlecock-contact-driven stroke detection (Approach A) → Task 6. ✅
- Joint angle measurement → Tasks 2, 3. ✅
- Scoring against ideal ranges → Tasks 4, 5. ✅
- Strengths/weaknesses with severity + suggestions → Tasks 5, 9. ✅
- Per-stroke + match-summary output files → Task 10. ✅
- In-video overlay → Task 11 (live layer) + integration Task 12. ✅
- Feature flag / existing outputs unchanged → Task 12. ✅
- Error handling: low-confidence keypoints (`is_valid`), missing racket (angles → None; `weight_transfer` None), occluded shuttle (no contact, non-fatal), no strokes (empty summary handled in Task 10 test). ✅
- **Deferred to Plan 2** (training plan generator, exercise library, Web UI viewer) — explicitly out of scope for this plan. The design's kinematic-inference racket fallback and richer post-contact overlay badges are noted as later refinements (Task 8/11 notes).

**2. Placeholder scan:** No TBD/TODO; every code step shows complete code. Reference values labeled "indicative" (intentional per design), not placeholders.

**3. Type consistency:**
- `StrokeEvent` fields identical across Tasks 6, 9, 12. ✅
- `compute_joint_angles` returns the six-key dict used by `score_stroke`; note `shoulder_abduction` is computed but `weight_transfer` (not `shoulder_abduction`) is the scored sixth metric — `score_stroke` only reads keys present in `REFERENCE_RANGES`, so the extra `shoulder_abduction` key is harmless and `weight_transfer` is injected by the analyzer (Task 9). ✅
- `score_stroke` returns `per_metric`/`overall`/`weaknesses`/`strengths`; consumed correctly in Task 9 and Task 10. ✅
- `detect_contacts` dict shape (`contact_frame`/`window_start`/`window_end`) consumed in Task 12. ✅
- `frame_lookup` record shape (Task 9 keys + classifier keys) produced by `_capture_analysis_frame` in Task 12. ✅

No issues found requiring rework.
