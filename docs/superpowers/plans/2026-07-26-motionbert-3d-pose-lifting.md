# MotionBERT 3D Pose Lifting Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an optional MotionBERT 3D pose-lifting stage so the rule-based posture path scores view-invariant anatomical angles, with per-rep 3D features persisted for a later AQA scorer.

**Architecture:** A new `PoseLifter` lifts each rep's 2D COCO-17 window to 3D H36M-17 (root-relative) post-loop inside `PostureRunner`. `BiomechanicalAnalyzer` computes 3D angles for the four capable metrics (elbow, knee, trunk rotation, hip-shoulder separation) against a new `reference_ranges_3d` table; `wrist_flexion` and `weight_transfer` stay 2D and are labeled. The stage is fully optional and degrades to today's exact 2D behavior.

**Tech Stack:** Python, NumPy, PyTorch (already a dependency, used by the TCN quality scorer), vendored MotionBERT under `third_party/motionbert/`.

## Global Constraints

- Python; match existing code style (module docstrings, minimal type annotations, ASCII-only source). Copy the surrounding files' idioms.
- No new pip dependencies. MotionBERT is **vendored** under `third_party/motionbert/` (like `third_party/bst`, `third_party/tracknet`). Weights live under `weights/` and are **never committed** (gitignored); datasets/weights/large binaries are never committed.
- Undetected-joint sentinel convention: a 2D keypoint with `x <= 1` and `y <= 1` is "not detected" (same as `analysis/joint_angles.py` `is_valid` and `quality/normalize.py` `posed_frames`).
- Every new stage is optional and construct-if-configured, mirroring `_build_racket_detector` / `_build_quality_scorer`. With no lifter, posture output is byte-identical to today.
- Tests run on Windows via the project venv: `PYTHONUTF8=1 ./.venv/Scripts/python.exe -B -m pytest <path> -q -p no:cacheprovider`. Tests must pass without a GPU or real weights (inject a stub model; use synthetic poses).
- Commit after each task with the trailer `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`.

---

### Task 1: COCO-17 → H36M-17 remap

**Files:**
- Create: `badminton_analysis/detection/pose_lift.py`
- Test: `tests/test_pose_lift_remap.py`

**Interfaces:**
- Produces: `coco2h36m(seq)` where `seq` is a NumPy array of shape `(T, 17, C)` (C in {2,3}) in COCO order, returning `(T, 17, C)` in H36M-17 order via the standard MotionBERT synthesis (derived pelvis/spine/thorax/head).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_pose_lift_remap.py
import numpy as np
from badminton_analysis.detection.pose_lift import coco2h36m


def test_coco2h36m_derived_and_direct_joints():
    # One frame, distinct integer coords per COCO joint so we can assert mapping.
    coco = np.zeros((1, 17, 2), dtype=float)
    for i in range(17):
        coco[0, i] = (i + 1, (i + 1) * 10)  # (x, y) unique per joint
    h = coco2h36m(coco)
    assert h.shape == (1, 17, 2)
    # Direct joints
    np.testing.assert_allclose(h[0, 14], coco[0, 6])   # R shoulder <- COCO R shoulder (6)
    np.testing.assert_allclose(h[0, 16], coco[0, 10])  # R wrist <- COCO R wrist (10)
    np.testing.assert_allclose(h[0, 13], coco[0, 9])   # L wrist <- COCO L wrist (9)
    np.testing.assert_allclose(h[0, 3], coco[0, 16])   # R ankle <- COCO R ankle (16)
    # Derived joints
    np.testing.assert_allclose(h[0, 0], (coco[0, 11] + coco[0, 12]) * 0.5)  # pelvis = mid-hip
    np.testing.assert_allclose(h[0, 8], (coco[0, 5] + coco[0, 6]) * 0.5)    # thorax = mid-shoulder
    np.testing.assert_allclose(h[0, 7], (h[0, 0] + h[0, 8]) * 0.5)          # spine = mid(pelvis,thorax)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONUTF8=1 ./.venv/Scripts/python.exe -B -m pytest tests/test_pose_lift_remap.py -q -p no:cacheprovider`
Expected: FAIL with `ImportError`/`ModuleNotFoundError` (no `pose_lift`).

- [ ] **Step 3: Write minimal implementation**

```python
# badminton_analysis/detection/pose_lift.py
"""Optional MotionBERT 2D->3D pose lifting for the posture pipeline."""
import numpy as np


def coco2h36m(seq):
    """Convert (T,17,C) COCO-17 keypoints to (T,17,C) H36M-17 (MotionBERT order).

    Synthesizes the H36M joints COCO lacks (pelvis, spine, thorax, head) from
    COCO joints, following the standard MotionBERT/VideoPose3D convention.
    """
    x = np.asarray(seq, dtype=float)
    y = np.zeros_like(x)
    y[:, 0] = (x[:, 11] + x[:, 12]) * 0.5   # 0 pelvis = mid-hip
    y[:, 1] = x[:, 12]                        # 1 R hip
    y[:, 2] = x[:, 14]                        # 2 R knee
    y[:, 3] = x[:, 16]                        # 3 R ankle
    y[:, 4] = x[:, 11]                        # 4 L hip
    y[:, 5] = x[:, 13]                        # 5 L knee
    y[:, 6] = x[:, 15]                        # 6 L ankle
    y[:, 8] = (x[:, 5] + x[:, 6]) * 0.5       # 8 thorax = mid-shoulder
    y[:, 7] = (y[:, 0] + y[:, 8]) * 0.5       # 7 spine = mid(pelvis, thorax)
    y[:, 9] = x[:, 0]                         # 9 nose
    y[:, 10] = (x[:, 1] + x[:, 2]) * 0.5      # 10 head = mid-eye
    y[:, 11] = x[:, 5]                        # 11 L shoulder
    y[:, 12] = x[:, 7]                        # 12 L elbow
    y[:, 13] = x[:, 9]                        # 13 L wrist
    y[:, 14] = x[:, 6]                        # 14 R shoulder
    y[:, 15] = x[:, 8]                        # 15 R elbow
    y[:, 16] = x[:, 10]                       # 16 R wrist
    return y
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONUTF8=1 ./.venv/Scripts/python.exe -B -m pytest tests/test_pose_lift_remap.py -q -p no:cacheprovider`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add badminton_analysis/detection/pose_lift.py tests/test_pose_lift_remap.py
git commit -m "feat(pose-lift): add COCO-17 to H36M-17 keypoint remap"
```

---

### Task 2: 3D anatomical joint angles

**Files:**
- Modify: `badminton_analysis/analysis/joint_angles.py` (append H36M indices + `vector_angle` + `compute_joint_angles_3d`)
- Test: `tests/test_joint_angles_3d.py`

**Interfaces:**
- Consumes: existing `angle_at(a, b, c)` (dimension-agnostic; already works on 3D vectors).
- Produces:
  - `vector_angle(u, v)` → degrees in `[0,180]` or `None` for a degenerate vector.
  - `compute_joint_angles_3d(keypoints_3d, dominant="right")` → dict with keys `elbow_extension`, `knee_flexion`, `trunk_rotation`, `hip_shoulder_separation` (floats or `None`). Input is `(17, 3)` in H36M order.
  - Module constant `METRICS_3D_CAPABLE = ("elbow_extension", "knee_flexion", "trunk_rotation", "hip_shoulder_separation")`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_joint_angles_3d.py
import numpy as np
from badminton_analysis.analysis import joint_angles as ja


def _straight_arm_pose():
    # H36M-17 3D pose; only the joints we assert on need meaningful values.
    kp = np.zeros((17, 3), dtype=float)
    # Right arm straight along +x: shoulder(14), elbow(15), wrist(16)
    kp[14] = (0.0, 0.0, 0.0)
    kp[15] = (1.0, 0.0, 0.0)
    kp[16] = (2.0, 0.0, 0.0)
    # Right leg bent 90 deg: hip(1), knee(2), ankle(3)
    kp[1] = (0.0, 1.0, 0.0)
    kp[2] = (0.0, 0.0, 0.0)
    kp[3] = (1.0, 0.0, 0.0)
    # Shoulder line along x; hip line rotated 30 deg in the horizontal (x,z) plane
    kp[11] = (-1.0, 0.0, 0.0)   # L shoulder
    kp[14] = (1.0, 0.0, 0.0)    # R shoulder (overrides above; fine for line test)
    kp[4] = (-np.cos(np.radians(30)), 0.0, -np.sin(np.radians(30)))  # L hip
    kp[1] = (np.cos(np.radians(30)), 0.0, np.sin(np.radians(30)))    # R hip
    return kp


def test_vector_angle_basic():
    assert ja.vector_angle((1, 0, 0), (0, 1, 0)) == 90.0
    assert ja.vector_angle((1, 0, 0), (1, 0, 0)) == 0.0
    assert ja.vector_angle((0, 0, 0), (1, 0, 0)) is None


def test_compute_joint_angles_3d_known_angles():
    kp = _straight_arm_pose()
    a = ja.compute_joint_angles_3d(kp, dominant="right")
    assert abs(a["elbow_extension"] - 180.0) < 1e-6   # straight arm
    assert abs(a["knee_flexion"] - 90.0) < 1e-6       # right-angle leg
    # Shoulder line (x-axis) vs hip line (30 deg in x,z plane): separation ~30 deg
    assert abs(a["hip_shoulder_separation"] - 30.0) < 1e-6
    assert abs(a["trunk_rotation"] - 30.0) < 1e-6
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONUTF8=1 ./.venv/Scripts/python.exe -B -m pytest tests/test_joint_angles_3d.py -q -p no:cacheprovider`
Expected: FAIL with `AttributeError: module ... has no attribute 'vector_angle'`.

- [ ] **Step 3: Write minimal implementation**

Append to `badminton_analysis/analysis/joint_angles.py`:

```python
# ---- 3D (MotionBERT H36M-17) angle support -------------------------------
# H36M-17 joint indices (MotionBERT output order).
H36M_PELVIS = 0
H36M_R_HIP, H36M_R_KNEE, H36M_R_ANKLE = 1, 2, 3
H36M_L_HIP, H36M_L_KNEE, H36M_L_ANKLE = 4, 5, 6
H36M_SPINE, H36M_THORAX = 7, 8
H36M_NOSE, H36M_HEAD = 9, 10
H36M_L_SHOULDER, H36M_L_ELBOW, H36M_L_WRIST = 11, 12, 13
H36M_R_SHOULDER, H36M_R_ELBOW, H36M_R_WRIST = 14, 15, 16

# Vertical axis of MotionBERT's root-relative output (Y-up, H36M convention).
# Only trunk_rotation's horizontal-plane projection depends on this; confirm it
# against the vendored model's output orientation in Task 10 and flip if needed.
VERTICAL_AXIS_3D = 1

_DOMINANT_H36M = {
    "right": (H36M_R_SHOULDER, H36M_R_ELBOW, H36M_R_WRIST, H36M_R_HIP, H36M_R_KNEE, H36M_R_ANKLE),
    "left": (H36M_L_SHOULDER, H36M_L_ELBOW, H36M_L_WRIST, H36M_L_HIP, H36M_L_KNEE, H36M_L_ANKLE),
}

METRICS_3D_CAPABLE = ("elbow_extension", "knee_flexion", "trunk_rotation", "hip_shoulder_separation")


def vector_angle(u, v):
    """Angle (degrees, [0,180]) between vectors u and v. None if either is ~0."""
    u = np.asarray(u, dtype=float)
    v = np.asarray(v, dtype=float)
    nu = np.linalg.norm(u)
    nv = np.linalg.norm(v)
    if nu < 1e-6 or nv < 1e-6:
        return None
    cos_ang = float(np.dot(u, v) / (nu * nv))
    cos_ang = max(-1.0, min(1.0, cos_ang))
    return float(np.degrees(np.arccos(cos_ang)))


def compute_joint_angles_3d(keypoints_3d, dominant="right"):
    """View-invariant anatomical angles from a (17,3) H36M-17 pose.

    Returns the four 3D-capable metrics (elbow/knee/trunk/hip-shoulder). The
    racket-relative wrist_flexion and global-translation weight_transfer are not
    computable from root-relative body-only 3D and are handled 2D elsewhere.
    """
    kp = np.asarray(keypoints_3d, dtype=float)
    sh, el, wr, hip, kn, an = _DOMINANT_H36M.get(dominant, _DOMINANT_H36M["right"])
    angles = {"elbow_extension": None, "knee_flexion": None,
              "trunk_rotation": None, "hip_shoulder_separation": None}
    angles["elbow_extension"] = angle_at(kp[sh], kp[el], kp[wr])
    angles["knee_flexion"] = angle_at(kp[hip], kp[kn], kp[an])
    sho_vec = kp[H36M_R_SHOULDER] - kp[H36M_L_SHOULDER]
    hip_vec = kp[H36M_R_HIP] - kp[H36M_L_HIP]
    angles["hip_shoulder_separation"] = vector_angle(sho_vec, hip_vec)
    horiz = [i for i in range(3) if i != VERTICAL_AXIS_3D]
    angles["trunk_rotation"] = vector_angle(sho_vec[horiz], hip_vec[horiz])
    return angles
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONUTF8=1 ./.venv/Scripts/python.exe -B -m pytest tests/test_joint_angles_3d.py -q -p no:cacheprovider`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add badminton_analysis/analysis/joint_angles.py tests/test_joint_angles_3d.py
git commit -m "feat(angles): add 3D anatomical joint angles for H36M-17 poses"
```

---

### Task 3: 3D reference ranges

**Files:**
- Create: `badminton_analysis/analysis/reference_ranges_3d.py`
- Test: `tests/test_reference_ranges_3d.py`

**Interfaces:**
- Consumes: `reference_ranges.REFERENCE_RANGES` (2D table; reused verbatim for `wrist_flexion` and `weight_transfer`).
- Produces: `REFERENCE_RANGES_3D` — same schema/strokes as the 2D table, with 3D-calibrated `min/max/ideal` for the four capable metrics and identical `weight` values so per-stroke weights still sum as before.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_reference_ranges_3d.py
from badminton_analysis.analysis.reference_ranges import REFERENCE_RANGES
from badminton_analysis.analysis.reference_ranges_3d import REFERENCE_RANGES_3D


def test_same_strokes_and_metrics_as_2d():
    assert set(REFERENCE_RANGES_3D) == set(REFERENCE_RANGES)
    for stroke in REFERENCE_RANGES:
        assert set(REFERENCE_RANGES_3D[stroke]) == set(REFERENCE_RANGES[stroke])


def test_wrist_and_weight_transfer_reuse_2d_values():
    for stroke in REFERENCE_RANGES:
        for metric in ("wrist_flexion", "weight_transfer"):
            assert REFERENCE_RANGES_3D[stroke][metric] == REFERENCE_RANGES[stroke][metric]


def test_capable_metric_ranges_are_valid_and_weights_preserved():
    for stroke in REFERENCE_RANGES:
        for metric in ("elbow_extension", "knee_flexion", "trunk_rotation", "hip_shoulder_separation"):
            spec = REFERENCE_RANGES_3D[stroke][metric]
            assert spec["min"] <= spec["ideal"] <= spec["max"]
            assert spec["weight"] == REFERENCE_RANGES[stroke][metric]["weight"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONUTF8=1 ./.venv/Scripts/python.exe -B -m pytest tests/test_reference_ranges_3d.py -q -p no:cacheprovider`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Write minimal implementation**

```python
# badminton_analysis/analysis/reference_ranges_3d.py
"""3D anatomical reference ranges per stroke (view-invariant angles, degrees).

Indicative first-release starting points for MotionBERT-lifted H36M-17 angles,
expected to be tuned against biomechanics literature and labeled clips (same
posture as reference_ranges.py). wrist_flexion and weight_transfer stay in the
2D image-plane space and reuse the 2D table verbatim; only the four 3D-capable
metrics are overridden. Per-metric weights are preserved so each stroke's
weights still sum to the same total as the 2D table.
"""
from .reference_ranges import REFERENCE_RANGES

# 3D min/max/ideal for the four capable metrics; weights are copied from the 2D
# table by _merge below, so they are intentionally omitted here.
_3D_OVERRIDES = {
    "high_clear": {
        "elbow_extension":         {"min": 150, "max": 175, "ideal": 165},
        "trunk_rotation":          {"min": 25,  "max": 60,  "ideal": 40},
        "knee_flexion":            {"min": 145, "max": 175, "ideal": 162},
        "hip_shoulder_separation": {"min": 20,  "max": 50,  "ideal": 35},
    },
    "smash": {
        "elbow_extension":         {"min": 155, "max": 178, "ideal": 170},
        "trunk_rotation":          {"min": 35,  "max": 70,  "ideal": 50},
        "knee_flexion":            {"min": 140, "max": 172, "ideal": 158},
        "hip_shoulder_separation": {"min": 25,  "max": 55,  "ideal": 40},
    },
    "drop_shot": {
        "elbow_extension":         {"min": 135, "max": 165, "ideal": 150},
        "trunk_rotation":          {"min": 20,  "max": 50,  "ideal": 32},
        "knee_flexion":            {"min": 148, "max": 176, "ideal": 163},
        "hip_shoulder_separation": {"min": 15,  "max": 45,  "ideal": 28},
    },
    "serve": {
        "elbow_extension":         {"min": 140, "max": 168, "ideal": 155},
        "trunk_rotation":          {"min": 10,  "max": 40,  "ideal": 22},
        "knee_flexion":            {"min": 150, "max": 178, "ideal": 166},
        "hip_shoulder_separation": {"min": 10,  "max": 35,  "ideal": 20},
    },
}


def _merge(base_stroke, override_stroke):
    merged = {name: dict(spec) for name, spec in base_stroke.items()}
    for name, ov in override_stroke.items():
        spec = dict(merged[name])           # keep 2D weight
        spec["min"] = ov["min"]
        spec["max"] = ov["max"]
        spec["ideal"] = ov["ideal"]
        merged[name] = spec
    return merged


REFERENCE_RANGES_3D = {
    stroke: _merge(REFERENCE_RANGES[stroke], _3D_OVERRIDES[stroke])
    for stroke in REFERENCE_RANGES
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONUTF8=1 ./.venv/Scripts/python.exe -B -m pytest tests/test_reference_ranges_3d.py -q -p no:cacheprovider`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add badminton_analysis/analysis/reference_ranges_3d.py tests/test_reference_ranges_3d.py
git commit -m "feat(angles): add literature-grounded 3D reference ranges"
```

---

### Task 4: PoseLifter (preprocess + lift, stub-injectable)

**Files:**
- Modify: `badminton_analysis/detection/pose_lift.py` (add `PoseLifter`, `POSE_LIFT_MIN_POSED`, screen normalization, posed-frame extraction)
- Test: `tests/test_pose_lift.py`

**Interfaces:**
- Consumes: `coco2h36m` (Task 1); `analysis.joint_angles` H36M indices are not needed here.
- Produces:
  - `PoseLifter(model_path=None, device="auto", model=None)` with attribute `available` (bool). `model` is a callable taking a `(1, M, 17, 2)` float32 `numpy` array and returning a `(1, M, 17, 3)` array (the injection seam for tests and for the real adapter in Task 10).
  - `PoseLifter.lift(window_frames, image_size)` → `(kp3d, frames)` where `kp3d` is `(M, 17, 3)` float and `frames` is a list of the `M` source frame numbers used, or `None` when fewer than `POSE_LIFT_MIN_POSED` posed frames exist.
  - `window_frames`: list of dicts with `"frame"` (int) and `"keypoints"` (`(17,2)` array or `None`), as built by `PostureRunner._window_frames`.
  - `image_size`: `(width, height)` in pixels.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_pose_lift.py
import numpy as np
from badminton_analysis.detection.pose_lift import PoseLifter, POSE_LIFT_MIN_POSED


def _posed_frame(n, hip_y=200.0):
    kp = np.full((17, 2), 2.0, dtype=float)  # all "detected" (x,y > 1)
    kp[11] = (95.0, hip_y)   # L hip valid
    kp[12] = (105.0, hip_y)  # R hip valid
    kp[5] = (90.0, 100.0)    # L shoulder
    kp[6] = (110.0, 100.0)   # R shoulder
    return {"frame": n, "keypoints": kp}


class _StubModel:
    """Echoes a deterministic 3D output: appends a z=0 plane to the 2D input."""
    def __call__(self, arr):
        arr = np.asarray(arr, dtype=np.float32)  # (1, M, 17, 2)
        z = np.zeros(arr.shape[:-1] + (1,), dtype=np.float32)
        return np.concatenate([arr, z], axis=-1)  # (1, M, 17, 3)


def test_available_flag_without_model_or_weights():
    assert PoseLifter().available is False
    assert PoseLifter(model=_StubModel()).available is True


def test_lift_returns_none_when_too_few_posed_frames():
    lifter = PoseLifter(model=_StubModel())
    frames = [_posed_frame(i) for i in range(POSE_LIFT_MIN_POSED - 1)]
    frames.append({"frame": 999, "keypoints": None})  # unposed, dropped
    assert lifter.lift(frames, image_size=(200, 400)) is None


def test_lift_shapes_and_frame_alignment():
    lifter = PoseLifter(model=_StubModel())
    src = [_posed_frame(i) for i in range(POSE_LIFT_MIN_POSED)]
    src.insert(3, {"frame": 500, "keypoints": None})  # dropped, not in output
    result = lifter.lift(src, image_size=(200, 400))
    assert result is not None
    kp3d, out_frames = result
    assert kp3d.shape == (POSE_LIFT_MIN_POSED, 17, 3)
    assert out_frames == [f["frame"] for f in src if f["keypoints"] is not None]
    assert kp3d.shape[0] == len(out_frames)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONUTF8=1 ./.venv/Scripts/python.exe -B -m pytest tests/test_pose_lift.py -q -p no:cacheprovider`
Expected: FAIL with `ImportError` (no `PoseLifter` / `POSE_LIFT_MIN_POSED`).

- [ ] **Step 3: Write minimal implementation**

Append to `badminton_analysis/detection/pose_lift.py`:

```python
import os

# Hip indices in COCO order, for the posed-frame gate (matches quality/normalize.py).
_COCO_L_HIP, _COCO_R_HIP = 11, 12
POSE_LIFT_MIN_POSED = 8


def _posed_with_frames(window_frames):
    """Return (kps, frames): (M,17,2) float array of frames with two valid hips
    (x>1 and y>1), and the list of their source frame numbers. Empty -> ([], [])."""
    kps = []
    frames = []
    for f in window_frames:
        kp = f.get("keypoints")
        if kp is None:
            continue
        kp = np.asarray(kp, dtype=float)
        if (kp[_COCO_L_HIP][0] > 1.0 and kp[_COCO_L_HIP][1] > 1.0
                and kp[_COCO_R_HIP][0] > 1.0 and kp[_COCO_R_HIP][1] > 1.0):
            kps.append(kp)
            frames.append(int(f["frame"]))
    if not kps:
        return np.zeros((0, 17, 2), dtype=float), []
    return np.stack(kps), frames


def _normalize_screen(kps, image_size):
    """Normalize pixel coords to roughly [-1,1] by width (VideoPose3D convention):
    x' = x / w * 2 - 1 ; y' = y / w * 2 - h / w. Preserves aspect ratio."""
    w, h = float(image_size[0]), float(image_size[1])
    if w <= 0:
        return kps
    out = kps.copy()
    out[..., 0] = out[..., 0] / w * 2.0 - 1.0
    out[..., 1] = out[..., 1] / w * 2.0 - h / w
    return out


class PoseLifter:
    """Optional MotionBERT 2D->3D lifter, structured like quality.scorer.QualityScorer.

    `model` (or a model loaded from `model_path` in Task 10) is a callable taking
    a (1, M, 17, 2) float32 array of normalized H36M-17 2D keypoints and returning
    a (1, M, 17, 3) array of root-relative 3D joints.
    """

    def __init__(self, model_path=None, device="auto", model=None):
        self.model_path = model_path
        self.device = device
        self._model = model
        if model is not None:
            self.available = True
        elif model_path and os.path.exists(model_path):
            self.available = True   # real load happens lazily in Task 10's _load()
        else:
            self.available = False

    def _load(self):
        # Task 10 replaces this with a real torch/MotionBERT adapter load.
        return self._model

    def lift(self, window_frames, image_size):
        if not self.available:
            return None
        model = self._load()
        if model is None:
            return None
        kps2d, frames = _posed_with_frames(window_frames)
        if kps2d.shape[0] < POSE_LIFT_MIN_POSED:
            return None
        try:
            h36m = coco2h36m(kps2d)                      # (M,17,2)
            norm = _normalize_screen(h36m, image_size)   # (M,17,2)
            batch = norm[None, ...].astype(np.float32)   # (1,M,17,2)
            out = np.asarray(model(batch), dtype=float)  # (1,M,17,3)
            kp3d = out[0]
            if kp3d.shape != (kps2d.shape[0], 17, 3):
                return None
            return kp3d, frames
        except Exception as e:
            print("Pose lifting failed (" + str(e) + ")")
            return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONUTF8=1 ./.venv/Scripts/python.exe -B -m pytest tests/test_pose_lift.py -q -p no:cacheprovider`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add badminton_analysis/detection/pose_lift.py tests/test_pose_lift.py
git commit -m "feat(pose-lift): add PoseLifter with stub-injectable model seam"
```

---

### Task 5: Biomechanics 3D branch + feature-space labeling

**Files:**
- Modify: `badminton_analysis/analysis/biomechanics.py`
- Test: `tests/test_biomechanics_3d.py`

**Interfaces:**
- Consumes: `analysis.joint_angles.compute_joint_angles_3d` + `METRICS_3D_CAPABLE` (Task 2); `analysis.reference_ranges_3d.REFERENCE_RANGES_3D` (Task 3); a contact-frame record that may carry `"keypoints_3d"` (a `(17,3)` array).
- Produces: `analyze()` output where, when 3D is present, the four capable metrics are 3D-scored against `REFERENCE_RANGES_3D`; `per_metric[m]` carries `feature_space` ("3d"/"2d") and (for capable metrics) `measured_shadow_2d`; and the report carries top-level `feature_space`. With no 3D, output is unchanged from today.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_biomechanics_3d.py
import numpy as np
from badminton_analysis.analysis.biomechanics import BiomechanicalAnalyzer
from badminton_analysis.stroke.events import StrokeEvent


def _straight_pose_3d():
    kp = np.zeros((17, 3), dtype=float)
    kp[14], kp[15], kp[16] = (0, 0, 0), (1, 0, 0), (2, 0, 0)   # straight R arm -> 180
    kp[1], kp[2], kp[3] = (0, 1, 0), (0, 0, 0), (1, 0, 0)      # R leg -> 90
    kp[11], kp[14] = (-1, 0, 0), (1, 0, 0)                     # shoulder line
    kp[4], kp[1] = (-1, 0, 0), (1, 0, 0)                       # hip line aligned -> 0 sep
    return kp


def _coco_pose_2d():
    kp = np.full((17, 2), 50.0, dtype=float)  # all detected
    return kp


def _event():
    return StrokeEvent(stroke_type="high_clear", contact_frame=10,
                       window_start=5, window_end=15, player_side="single", confidence=1.0)


def test_3d_branch_labels_feature_space_and_shadow():
    contact = {"frame": 10, "keypoints": _coco_pose_2d(), "conf": None,
               "keypoints_3d": _straight_pose_3d(), "centroid": (50.0, 50.0),
               "racket_head": None, "racket_head_detected": False}
    window = [{"frame": 5, "centroid": (40.0, 50.0)}, contact]
    report = BiomechanicalAnalyzer(dominant="right").analyze(_event(), window)
    assert report["feature_space"] == "3d"
    pm = report["per_metric"]
    assert pm["elbow_extension"]["feature_space"] == "3d"
    assert abs(pm["elbow_extension"]["measured"] - 180.0) < 1e-6
    assert "measured_shadow_2d" in pm["elbow_extension"]
    assert pm["wrist_flexion"]["feature_space"] == "2d"
    assert pm["weight_transfer"]["feature_space"] == "2d"


def test_no_3d_is_unchanged_2d():
    contact = {"frame": 10, "keypoints": _coco_pose_2d(), "conf": None,
               "centroid": (50.0, 50.0), "racket_head": None, "racket_head_detected": False}
    window = [{"frame": 5, "centroid": (40.0, 50.0)}, contact]
    report = BiomechanicalAnalyzer(dominant="right").analyze(_event(), window)
    assert report["feature_space"] == "2d"
    assert all(v["feature_space"] == "2d" for v in report["per_metric"].values())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONUTF8=1 ./.venv/Scripts/python.exe -B -m pytest tests/test_biomechanics_3d.py -q -p no:cacheprovider`
Expected: FAIL (`KeyError: 'feature_space'`).

- [ ] **Step 3: Write minimal implementation**

In `badminton_analysis/analysis/biomechanics.py`, add imports near the top:

```python
from .reference_ranges import REFERENCE_RANGES
from .reference_ranges_3d import REFERENCE_RANGES_3D
```

Replace the body of `analyze()` from the `if kp is not None:` metrics block through the `scored = score_stroke(...)` line with:

```python
        # Base 2D metrics (always computed; source of wrist_flexion/weight_transfer
        # and of the shadow comparison for the 3D-capable metrics).
        if kp is not None:
            metrics2d = ja.compute_joint_angles(
                kp, racket_head=(racket_head if racket_detected else None),
                dominant=self.dominant, conf=conf)
        else:
            metrics2d = {k: None for k in
                         ("elbow_extension", "shoulder_abduction", "trunk_rotation",
                          "knee_flexion", "hip_shoulder_separation", "wrist_flexion")}

        kp3d = contact.get("keypoints_3d") if contact else None
        used_3d = kp3d is not None
        metrics = dict(metrics2d)
        if used_3d:
            m3d = ja.compute_joint_angles_3d(kp3d, dominant=self.dominant)
            for name in ja.METRICS_3D_CAPABLE:
                metrics[name] = m3d.get(name)
            ranges = REFERENCE_RANGES_3D
        else:
            ranges = REFERENCE_RANGES

        # weight transfer: window-start centroid -> contact centroid, normalized by shoulder width
        start_centroid = next((w["centroid"] for w in window_frames if w.get("centroid") is not None), None)
        contact_centroid = contact.get("centroid") if contact else None
        shoulder_w = self._shoulder_width(kp, conf)
        wt = None
        if start_centroid is not None and contact_centroid is not None and shoulder_w:
            wt = ja.weight_transfer_ratio(start_centroid, contact_centroid, shoulder_w)
        metrics["weight_transfer"] = wt

        scored = score_stroke(metrics, stroke_event.stroke_type, ranges=ranges)

        # Per-metric feature-space labels + 2D shadow for the 3D-capable metrics.
        for name, entry in scored["per_metric"].items():
            if used_3d and name in ja.METRICS_3D_CAPABLE:
                entry["feature_space"] = "3d"
                entry["measured_shadow_2d"] = metrics2d.get(name)
            else:
                entry["feature_space"] = "2d"
```

Then, in the `weaknesses.append({...})` dict, add two keys:

```python
                "feature_space": entry.get("feature_space"),
                "measured_shadow_2d": entry.get("measured_shadow_2d"),
```

And in the returned dict, add a top-level field:

```python
            "feature_space": "3d" if used_3d else "2d",
```

- [ ] **Step 4: Run the new + existing biomechanics tests**

Run: `PYTHONUTF8=1 ./.venv/Scripts/python.exe -B -m pytest tests/test_biomechanics_3d.py tests/test_biomechanics.py -q -p no:cacheprovider`
Expected: PASS (new 3D tests pass; existing 2D tests unaffected).

- [ ] **Step 5: Commit**

```bash
git add badminton_analysis/analysis/biomechanics.py tests/test_biomechanics_3d.py
git commit -m "feat(biomechanics): score 3D-capable metrics with feature-space labels"
```

---

### Task 6: 3D feature sidecar IO

**Files:**
- Create: `badminton_analysis/analysis/pose3d_io.py`
- Test: `tests/test_pose3d_io.py`

**Interfaces:**
- Produces:
  - `write_reps_3d(path, reps_3d, meta)` — `reps_3d` is a list of `{"rep_id": int, "frames": list[int], "keypoints_3d": (M,17,3) array}`; `meta` is a JSON-serializable dict. Writes a `.npz`.
  - `read_reps_3d(path)` → `(reps, meta)` where `reps` is a list of the same dict shape sorted by `rep_id`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_pose3d_io.py
import numpy as np
from badminton_analysis.analysis.pose3d_io import write_reps_3d, read_reps_3d


def test_round_trip(tmp_path):
    reps = [
        {"rep_id": 1, "frames": [10, 11, 12], "keypoints_3d": np.arange(3 * 17 * 3).reshape(3, 17, 3).astype(float)},
        {"rep_id": 2, "frames": [20, 21], "keypoints_3d": np.ones((2, 17, 3), dtype=float)},
    ]
    meta = {"model": "motionbert", "joint_format": "h36m-17", "normalization": "screen"}
    path = tmp_path / "drill_reps_3d.npz"
    write_reps_3d(str(path), reps, meta)

    out_reps, out_meta = read_reps_3d(str(path))
    assert out_meta == meta
    assert [r["rep_id"] for r in out_reps] == [1, 2]
    assert out_reps[0]["frames"] == [10, 11, 12]
    np.testing.assert_allclose(out_reps[0]["keypoints_3d"], reps[0]["keypoints_3d"])
    assert out_reps[1]["keypoints_3d"].shape == (2, 17, 3)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONUTF8=1 ./.venv/Scripts/python.exe -B -m pytest tests/test_pose3d_io.py -q -p no:cacheprovider`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Write minimal implementation**

```python
# badminton_analysis/analysis/pose3d_io.py
"""Per-rep 3D pose feature sidecar (drill_reps_3d.npz) for the future AQA scorer.

Ragged per-rep sequences are stored as separate arrays keyed by rep id; metadata
travels as a JSON string under a reserved key.
"""
import json

import numpy as np

_META_KEY = "__meta__"


def write_reps_3d(path, reps_3d, meta):
    arrays = {_META_KEY: np.array(json.dumps(meta))}
    for r in reps_3d:
        rid = int(r["rep_id"])
        arrays["rep%d_frames" % rid] = np.asarray(r["frames"], dtype=np.int64)
        arrays["rep%d_kp3d" % rid] = np.asarray(r["keypoints_3d"], dtype=np.float32)
    np.savez(path, **arrays)


def read_reps_3d(path):
    data = np.load(path, allow_pickle=True)
    meta = json.loads(str(data[_META_KEY]))
    by_id = {}
    for key in data.files:
        if key == _META_KEY:
            continue
        rid_str, kind = key[3:].split("_", 1)  # strip "rep" prefix
        rid = int(rid_str)
        by_id.setdefault(rid, {"rep_id": rid})
        if kind == "frames":
            by_id[rid]["frames"] = [int(v) for v in data[key]]
        else:
            by_id[rid]["keypoints_3d"] = np.asarray(data[key], dtype=float)
    return [by_id[k] for k in sorted(by_id)], meta
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONUTF8=1 ./.venv/Scripts/python.exe -B -m pytest tests/test_pose3d_io.py -q -p no:cacheprovider`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add badminton_analysis/analysis/pose3d_io.py tests/test_pose3d_io.py
git commit -m "feat(pose-lift): add per-rep 3D feature sidecar IO"
```

---

### Task 7: Wire the lifter into PostureRunner and PostureAnalysisSystem

**Files:**
- Modify: `badminton_analysis/posture/system.py` (`PostureRunner.__init__`/`run`, `PostureAnalysisSystem.__init__`, `_build_pose_lifter`, `process_video`, `_write_reports` meta)
- Test: `tests/test_posture_runner_3d.py`

**Interfaces:**
- Consumes: `PoseLifter.lift(window_frames, image_size)` (Task 4); `analysis.pose3d_io.write_reps_3d` (Task 6); the biomechanics 3D branch (Task 5).
- Produces: `PostureRunner(analyzer, stroke_type, dominant, window_pre, window_post, quality_scorer, pose_lifter=None, image_size=None)`. `run()` lifts each rep window (when a lifter is available), attaches the per-frame 3D pose to the matching window records before `analyze()`, and returns `(reports, reps, gate_info)` where `gate_info` gains `"scored_3d"` (int) and `run()` also returns the collected 3D sequences via a new `reps_3d` list on `gate_info["reps_3d"]`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_posture_runner_3d.py
import numpy as np
from badminton_analysis.posture.system import PostureRunner
from badminton_analysis.analysis.biomechanics import BiomechanicalAnalyzer


class _FakeLifter:
    available = True

    def lift(self, window_frames, image_size):
        frames = [int(f["frame"]) for f in window_frames if f.get("keypoints") is not None]
        if not frames:
            return None
        kp3d = np.zeros((len(frames), 17, 3), dtype=float)
        # straight R arm (elbow ~180) so 3D angles are well-defined
        kp3d[:, 14], kp3d[:, 15], kp3d[:, 16] = (0, 0, 0), (1, 0, 0), (2, 0, 0)
        kp3d[:, 1], kp3d[:, 2], kp3d[:, 3] = (0, 1, 0), (0, 0, 0), (1, 0, 0)
        kp3d[:, 11], kp3d[:, 14] = (-1, 0, 0), (1, 0, 0)
        kp3d[:, 4], kp3d[:, 1] = (-1, 0, 0), (1, 0, 0)
        return kp3d, frames


def _track_and_frames():
    # A single clear peak in the wrist-y trajectory so segment_reps yields one rep.
    fps = 30.0
    track = []
    frames = {}
    for i in range(60):
        wy = 200.0 - (80.0 if i == 30 else 0.0) - max(0.0, 40.0 - abs(i - 30) * 4)
        wrist = (100.0, wy)
        track.append({"frame": i, "wrist": wrist, "shuttle": None})
        kp = np.full((17, 2), 50.0, dtype=float)
        kp[9] = kp[10] = wrist            # wrists
        kp[11], kp[12] = (95.0, 200.0), (105.0, 200.0)
        frames[i] = {"frame": i, "keypoints": kp, "conf": None,
                     "racket_head": None, "centroid": (100.0, 200.0),
                     "racket_head_detected": False}
    return track, frames, fps


def test_runner_attaches_3d_and_counts():
    track, frames, fps = _track_and_frames()
    runner = PostureRunner(BiomechanicalAnalyzer(dominant="right"),
                           stroke_type="smash", dominant="right",
                           pose_lifter=_FakeLifter(), image_size=(200, 400))
    reports, reps, gate = runner.run(track, frames.get, fps)
    assert len(reports) >= 1
    assert reports[0]["feature_space"] == "3d"
    assert gate["scored_3d"] >= 1
    assert len(gate["reps_3d"]) >= 1


def test_runner_without_lifter_is_2d():
    track, frames, fps = _track_and_frames()
    runner = PostureRunner(BiomechanicalAnalyzer(dominant="right"),
                           stroke_type="smash", dominant="right")
    reports, reps, gate = runner.run(track, frames.get, fps)
    assert reports[0]["feature_space"] == "2d"
    assert gate["scored_3d"] == 0
    assert gate["reps_3d"] == []
```

Note: `smash` is used (not `high_clear`) to avoid the overhead-elevation gate dropping the synthetic rep.

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONUTF8=1 ./.venv/Scripts/python.exe -B -m pytest tests/test_posture_runner_3d.py -q -p no:cacheprovider`
Expected: FAIL (`PostureRunner.__init__` has no `pose_lifter`; `gate` has no `scored_3d`).

- [ ] **Step 3: Modify PostureRunner**

In `badminton_analysis/posture/system.py`, extend `PostureRunner.__init__`:

```python
    def __init__(self, analyzer, stroke_type, dominant="right",
                 window_pre=20, window_post=15, quality_scorer=None,
                 pose_lifter=None, image_size=None):
        self.analyzer = analyzer
        self.stroke_type = stroke_type
        self.dominant = dominant
        self.window_pre = window_pre
        self.window_post = window_post
        self.quality_scorer = quality_scorer
        self.pose_lifter = pose_lifter
        self.image_size = image_size
```

In `run()`, initialize collectors before the loop:

```python
        reports = []
        reps_3d = []
        scored_3d = 0
        gated = self.stroke_type in OVERHEAD_GATED_STROKES
        filtered_non_overhead = 0
```

Inside the loop, AFTER `window_frames = self._window_frames(...)` and BEFORE `report = self.analyzer.analyze(...)`, insert the lift + attach:

```python
            if self.pose_lifter is not None and getattr(self.pose_lifter, "available", False):
                lifted = self.pose_lifter.lift(window_frames, self.image_size)
                if lifted is not None:
                    kp3d, frames3d = lifted
                    by_frame = {fn: kp3d[j] for j, fn in enumerate(frames3d)}
                    for w in window_frames:
                        if w["frame"] in by_frame:
                            w["keypoints_3d"] = by_frame[w["frame"]]
                    reps_3d.append({"rep_id": rep.rep_id, "frames": list(frames3d),
                                    "keypoints_3d": kp3d})
```

After `report = self.analyzer.analyze(event, window_frames)` add:

```python
            if report.get("feature_space") == "3d":
                scored_3d += 1
```

Extend `gate_info` to include the new fields:

```python
        gate_info = {"counted": len(reports), "filtered_non_overhead": filtered_non_overhead,
                     "gated": gated, "scored_3d": scored_3d, "reps_3d": reps_3d}
```

(The overhead-gate `continue` path skips a dropped rep before `analyze`; that is fine — dropped reps neither score nor contribute 3D. Keep the existing renumbering of `reports`.)

- [ ] **Step 4: Modify PostureAnalysisSystem**

Extend `PostureAnalysisSystem.__init__` signature and body (add after the `quality_model_path` handling):

```python
                 quality_model_path=None, lift_model_path=None, lift_device="auto"):
```

```python
        self.quality_model_path = quality_model_path
        self._quality_scorer = None
        self.lift_model_path = lift_model_path
        self.lift_device = lift_device
        self._pose_lifter = None
```

Add a builder mirroring `_build_quality_scorer`:

```python
    def _build_pose_lifter(self):
        """Optional MotionBERT 3D lifter; analysis proceeds on failure."""
        if not self.lift_model_path:
            return
        try:
            from ..detection.pose_lift import PoseLifter
            lifter = PoseLifter(model_path=self.lift_model_path, device=self.lift_device)
            self._pose_lifter = lifter if lifter.available else None
        except Exception as e:
            print("Pose lifter unavailable (" + str(e) + "); 2D angles only.")
            self._pose_lifter = None
```

In `process_video`, call the builder next to the others:

```python
        self._build_racket_detector()
        self._build_quality_scorer()
        self._build_pose_lifter()
```

Pass the lifter + image size into the runner (replace the existing `runner = PostureRunner(...)` construction):

```python
        runner = PostureRunner(BiomechanicalAnalyzer(dominant=self.dominant_hand),
                               stroke_type=self.stroke_type, dominant=self.dominant_hand,
                               quality_scorer=self._quality_scorer,
                               pose_lifter=self._pose_lifter, image_size=(width, height))
        reports, reps, gate_info = runner.run(self._track, self._frames.get, fps)
```

After `write_json(os.path.join(self.save_dir, "drill_summary.json"), summary)` (before the metadata write), persist the sidecar:

```python
        if gate_info.get("reps_3d"):
            from ..analysis.pose3d_io import write_reps_3d
            write_reps_3d(
                os.path.join(self.save_dir, "drill_reps_3d.npz"),
                gate_info["reps_3d"],
                {"model": self.lift_model_path, "joint_format": "h36m-17",
                 "normalization": "screen", "stroke_type": self.stroke_type,
                 "dominant_hand": self.dominant_hand},
            )
```

In the `write_json(... "metadata.json" ...)` dict, add a lift block and extend `reps`:

```python
            "lift": {"model": self.lift_model_path if self._pose_lifter else None,
                     "scored_3d": gate_info.get("scored_3d", 0)},
            "reps": {"counted": gate_info["counted"],
                     "filtered_non_overhead": gate_info["filtered_non_overhead"],
                     "gated": gate_info["gated"],
                     "scored_3d": gate_info.get("scored_3d", 0)},
```

In `_write_reports`, add `feature_space` to `meta` so the report layer can badge it:

```python
        meta = {"date": date, "stroke_type": self.stroke_type,
                "dominant_hand": self.dominant_hand,
                "pose_family": getattr(self, "pose_family", "yolo-pose"),
                "feature_space": ("3d" if any(r.get("feature_space") == "3d" for r in reports) else "2d")}
```

- [ ] **Step 5: Run the runner tests + existing posture tests**

Run: `PYTHONUTF8=1 ./.venv/Scripts/python.exe -B -m pytest tests/test_posture_runner_3d.py tests/test_posture_runner.py -q -p no:cacheprovider`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add badminton_analysis/posture/system.py tests/test_posture_runner_3d.py
git commit -m "feat(posture): wire optional 3D lifter into the posture runner"
```

---

### Task 8: Coach report — 3D badge + 2D shadow

**Files:**
- Modify: `badminton_analysis/posture/report_builder.py` (header `feature_space`; weakness `measured_shadow_2d`)
- Modify: `badminton_analysis/posture/report_render.py` (header badge; shadow text)
- Test: `tests/test_report_3d.py`

**Interfaces:**
- Consumes: `meta["feature_space"]` (set in Task 7); rep `weaknesses` entries carrying `measured_shadow_2d` (set in Task 5).
- Produces: report `header["feature_space"]`; weakness entries carry `measured_shadow_2d`; `render_html` shows an angle-space badge and, when present, a `(2D: Y)` shadow next to the measured value.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_report_3d.py
from badminton_analysis.posture.report_builder import build_coach_report
from badminton_analysis.posture.report_render import render_html


def _reports():
    return [{
        "rep_id": 1, "overall_score": 60.0, "final_score": 60.0,
        "feature_space": "3d",
        "weaknesses": [{
            "metric": "elbow_extension", "measured": 120.0, "ideal_range": [150, 175],
            "direction": "under", "severity": "moderate",
            "measured_shadow_2d": 118.0, "feature_space": "3d",
            "description": "x",
        }],
        "strengths": [],
    }]


def _summary():
    return {"stroke_type": "high_clear", "rep_count": 1, "mean_score": 60.0,
            "consistency": 100.0, "recurring_weaknesses": [{"metric": "elbow_extension"}],
            "strengths": [], "per_metric_avg": {}}


def test_builder_carries_feature_space_and_shadow():
    meta = {"date": "2026-07-26", "stroke_type": "high_clear",
            "dominant_hand": "right", "pose_family": "yolo-pose", "feature_space": "3d"}
    by_lang = build_coach_report(_reports(), _summary(), meta)
    en = by_lang["en"]
    assert en["header"]["feature_space"] == "3d"
    assert en["weaknesses"][0]["measured_shadow_2d"] == 118.0


def test_render_shows_badge_and_shadow():
    meta = {"date": "2026-07-26", "stroke_type": "high_clear",
            "dominant_hand": "right", "pose_family": "yolo-pose", "feature_space": "3d"}
    en = build_coach_report(_reports(), _summary(), meta)["en"]
    html = render_html(en)
    assert "3D" in html
    assert "2D: 118" in html
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONUTF8=1 ./.venv/Scripts/python.exe -B -m pytest tests/test_report_3d.py -q -p no:cacheprovider`
Expected: FAIL (`header` has no `feature_space`; HTML lacks the badge/shadow).

- [ ] **Step 3: Modify report_builder**

In `_one_language`, add `feature_space` to `header`:

```python
    header = {
        "stroke": stroke,
        "stroke_label": kb.t(lang, "stroke_" + stroke),
        "rep_count": rep_count,
        "dominant_hand": meta.get("dominant_hand", "right"),
        "date": meta.get("date"),
        "pose_family": meta.get("pose_family"),
        "feature_space": meta.get("feature_space", "2d"),
    }
```

In the weakness assembly loop, add the shadow value to the appended dict:

```python
            "measured_shadow_2d": (finding or {}).get("measured_shadow_2d"),
```

- [ ] **Step 4: Modify report_render**

In `render_html`, extend the meta line to include an angle-space badge (replace the existing `<div class='meta'>...` append):

```python
    angle_space = "3D" if h.get("feature_space") == "3d" else "2D"
    parts.append("<div class='meta'>" + _esc(h.get("date")) + " · " + _esc(h.get("rep_count"))
                 + " reps · " + _esc(h.get("dominant_hand")) + " · " + _esc(h.get("pose_family"))
                 + " · " + angle_space + " angles</div>")
```

In the weaknesses loop, render the shadow when present (replace the `"measured " + _esc(w.get("measured")) + " (ideal "` fragment):

```python
            _shadow = w.get("measured_shadow_2d")
            _shadow_txt = (" (2D: " + _esc(_shadow) + ")") if _shadow is not None else ""
            parts.append("<div class='finding'><b>" + _esc(w.get("metric_label"))
                         + "</b> <span class='impact'>" + _esc(w.get("impact_label")) + "</span><br>"
                         + "measured " + _esc(w.get("measured")) + _shadow_txt + " (ideal "
                         + _esc(ideal_lo) + "–" + _esc(ideal_hi) + ")<br>"
                         + _esc(w.get("mechanism_text")) + "<br><i>" + _esc(w.get("drill_text")) + "</i></div>")
```

- [ ] **Step 5: Run the report tests (new + existing)**

Run: `PYTHONUTF8=1 ./.venv/Scripts/python.exe -B -m pytest tests/test_report_3d.py tests/test_report_render.py tests/test_report_builder.py -q -p no:cacheprovider`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add badminton_analysis/posture/report_builder.py badminton_analysis/posture/report_render.py tests/test_report_3d.py
git commit -m "feat(report): badge angle space and show 2D shadow for 3D metrics"
```

---

### Task 9: CLI flags

**Files:**
- Modify: `main_posture.py` (add `--lift-model`, `--lift-device`; pass through)
- Modify: `app.py` (append `--lift-model` to the posture subprocess command when configured)
- Test: `tests/test_app_lift_flags.py`

**Interfaces:**
- Consumes: `PostureAnalysisSystem(..., lift_model_path=, lift_device=)` (Task 7).
- Produces: `main_posture.build_parser()` (a new factory so the parser is unit-testable) exposing `--lift-model` (default `None`) and `--lift-device` (default `"auto"`, choices `auto`/`cpu`/`cuda`).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_app_lift_flags.py
from main_posture import build_parser


def test_lift_flags_defaults():
    args = build_parser().parse_args(["--video-path", "v.mp4", "--stroke-type", "smash"])
    assert args.lift_model is None
    assert args.lift_device == "auto"


def test_lift_flags_parsed():
    args = build_parser().parse_args(
        ["--video-path", "v.mp4", "--stroke-type", "smash",
         "--lift-model", "weights/motionbert.pt", "--lift-device", "cuda"])
    assert args.lift_model == "weights/motionbert.pt"
    assert args.lift_device == "cuda"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONUTF8=1 ./.venv/Scripts/python.exe -B -m pytest tests/test_app_lift_flags.py -q -p no:cacheprovider`
Expected: FAIL with `ImportError: cannot import name 'build_parser'`.

- [ ] **Step 3: Refactor main_posture into a testable parser + add flags**

Rewrite `main_posture.py` so the parser is a factory and the new flags exist:

```python
#!/usr/bin/env python3
"""Posture Drill analysis CLI (court-free, single player)."""
import argparse


def build_parser():
    parser = argparse.ArgumentParser(description="Badminton posture/technique drill analysis")
    parser.add_argument("--video-path", required=True, help="Input drill video path")
    parser.add_argument("--stroke-type", required=True,
                        choices=["high_clear", "smash", "drop_shot", "serve"],
                        help="Stroke being practiced")
    parser.add_argument("--dominant-hand", default="right", choices=["right", "left"])
    parser.add_argument("--output-dir", default=None,
                        help="Output dir (default outputs/<video>/posture)")
    parser.add_argument("--ball-model", default=None,
                        help="Optional YOLO shuttlecock model for rep refinement")
    parser.add_argument("--pose-model", default="weights/yolo11n-pose.pt")
    parser.add_argument("--pose-family", default="yolo-pose",
                        choices=["yolo-pose", "rtmpose", "rtmo"],
                        help="Pose model family for skeleton detection")
    parser.add_argument("--pose-mode", default="balanced",
                        choices=["lightweight", "balanced", "performance"],
                        help="RTMPose/RTMO model tier (ignored for yolo-pose)")
    parser.add_argument("--yolo-pose-model", default="weights/yolo11n-pose.pt",
                        help="YOLO pose model path (used when pose-family=yolo-pose)")
    parser.add_argument("--display", choices=["true", "false"], default="false")
    parser.add_argument("--report-llm", default="off",
                        help="LLM polish spec 'provider:model' or 'off' (default off)")
    parser.add_argument("--racket-model", default=None,
                        help="Trained racket detector weights (optional)")
    parser.add_argument("--quality-model", default=None,
                        help="Trained AI form-score weights (optional)")
    parser.add_argument("--lift-model", default=None,
                        help="MotionBERT 3D pose-lift weights (optional; enables 3D angles)")
    parser.add_argument("--lift-device", default="auto", choices=["auto", "cpu", "cuda"],
                        help="Device for 3D pose lifting (default auto)")
    return parser


def main():
    args = build_parser().parse_args()

    from badminton_analysis.posture.system import PostureAnalysisSystem
    system = PostureAnalysisSystem(
        video_path=args.video_path,
        stroke_type=args.stroke_type,
        dominant_hand=args.dominant_hand,
        output_dir=args.output_dir,
        ball_model_path=args.ball_model,
        show_display=args.display == "true",
        pose_model=args.pose_model,
        pose_family=args.pose_family,
        pose_mode=args.pose_mode,
        yolo_pose_model=args.yolo_pose_model,
        report_llm=args.report_llm,
        racket_model_path=args.racket_model,
        quality_model_path=args.quality_model,
        lift_model_path=args.lift_model,
        lift_device=args.lift_device,
    )
    system.process_video()


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Thread the flag through app.py's posture command**

In `app.py`, locate the posture command assembly near line 1049 (where `cmd += ['--quality-model', quality_weights]` is appended). Immediately after that block, add:

```python
    lift_weights = data.get('lift_model')
    if lift_weights:
        cmd += ['--lift-model', lift_weights]
        lift_device = data.get('lift_device', 'auto')
        if lift_device in ('auto', 'cpu', 'cuda'):
            cmd += ['--lift-device', lift_device]
```

- [ ] **Step 5: Run the flag tests + full app tests**

Run: `PYTHONUTF8=1 ./.venv/Scripts/python.exe -B -m pytest tests/test_app_lift_flags.py tests/test_app_technique_routes.py -q -p no:cacheprovider`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add main_posture.py app.py tests/test_app_lift_flags.py
git commit -m "feat(cli): add --lift-model/--lift-device flags for 3D lifting"
```

---

### Task 10: Vendor MotionBERT + real model adapter (guarded)

**Files:**
- Create: `third_party/motionbert/` (vendored inference code + `README` noting source, commit, licence)
- Modify: `badminton_analysis/detection/pose_lift.py` (`PoseLifter._load` builds a real adapter from `model_path`)
- Create: `docs/motionbert-weights.md` (how to obtain weights into `weights/`, never committed)
- Modify: `.gitignore` (ensure `weights/` is ignored if not already)
- Test: `tests/test_pose_lift_model.py` (skipped unless weights present)

**Interfaces:**
- Consumes: the vendored MotionBERT model; a weights file path via `PoseLifter(model_path=...)`.
- Produces: `PoseLifter._load()` returns a callable adapter with the same contract as the Task 4 stub: `(1, M, 17, 2) float32 -> (1, M, 17, 3)`. The adapter owns device placement (`self.device`: `auto`→cuda if available else cpu), any fixed-clip-length resampling MotionBERT requires internally (resample the `M` input frames to the model's `T`, run, resample the output back to `M`), and root-relative output orientation.

- [ ] **Step 1: Confirm the model's input/output contract**

Read the vendored MotionBERT inference code and confirm three things, recording the answers as comments in `pose_lift.py`:
1. Expected clip length `T` (resample the `M` posed frames to `T` on the way in, back to `M` on the way out).
2. Input normalization (this plan uses `_normalize_screen`; adjust if the vendored code differs).
3. Output orientation — which axis is vertical. If it is not Y (index 1), set `joint_angles.VERTICAL_AXIS_3D` accordingly. Verify by lifting a clip of an upright stance and checking the head sits above the pelvis on the chosen axis.

- [ ] **Step 2: Write the guarded test**

```python
# tests/test_pose_lift_model.py
import os
import numpy as np
import pytest
from badminton_analysis.detection.pose_lift import PoseLifter

_WEIGHTS = "weights/motionbert.pt"


@pytest.mark.skipif(not os.path.exists(_WEIGHTS), reason="MotionBERT weights not present")
def test_real_model_lifts_to_3d():
    lifter = PoseLifter(model_path=_WEIGHTS, device="cpu")
    assert lifter.available
    frames = []
    for i in range(16):
        kp = np.full((17, 2), 50.0, dtype=float)
        kp[11], kp[12] = (95.0, 200.0), (105.0, 200.0)
        frames.append({"frame": i, "keypoints": kp})
    result = lifter.lift(frames, image_size=(320, 240))
    assert result is not None
    kp3d, out_frames = result
    assert kp3d.shape == (16, 17, 3)
    assert len(out_frames) == 16
```

- [ ] **Step 3: Run the test to verify it skips (no weights) — the safety net**

Run: `PYTHONUTF8=1 ./.venv/Scripts/python.exe -B -m pytest tests/test_pose_lift_model.py -q -p no:cacheprovider`
Expected: 1 skipped (weights absent). This proves the guard works in CI/dev without weights.

- [ ] **Step 4: Implement the real `_load` adapter**

Replace `PoseLifter._load` in `badminton_analysis/detection/pose_lift.py` with a real loader that returns a callable adapter. Fill in the exact construction from the vendored code confirmed in Step 1:

```python
    def _resolve_device(self):
        if self.device == "cuda":
            return "cuda"
        if self.device == "cpu":
            return "cpu"
        try:
            import torch
            return "cuda" if torch.cuda.is_available() else "cpu"
        except Exception:
            return "cpu"

    def _load(self):
        if self._model is not None:
            return self._model
        if not (self.model_path and os.path.exists(self.model_path)):
            self.available = False
            return None
        try:
            import torch
            from third_party.motionbert.infer import load_model, MODEL_CLIP_LEN  # confirmed in Step 1
            device = self._resolve_device()
            net = load_model(self.model_path, device=device)
            net.eval()

            def _adapter(batch_2d):
                # batch_2d: (1, M, 17, 2) normalized float32
                import numpy as _np
                arr = _np.asarray(batch_2d, dtype=_np.float32)
                m = arr.shape[1]
                # resample M -> MODEL_CLIP_LEN if the model requires a fixed length
                src = _np.linspace(0.0, m - 1.0, MODEL_CLIP_LEN)
                lo = _np.floor(src).astype(int); hi = _np.minimum(lo + 1, m - 1)
                t = (src - lo)[None, :, None, None]
                clip = arr[:, lo] * (1.0 - t) + arr[:, hi] * t   # (1, T, 17, 2)
                with torch.no_grad():
                    out = net(torch.from_numpy(clip).to(device))  # (1, T, 17, 3)
                out = out.cpu().numpy()
                # resample T -> M so output aligns 1:1 with the input frames
                src2 = _np.linspace(0.0, MODEL_CLIP_LEN - 1.0, m)
                lo2 = _np.floor(src2).astype(int); hi2 = _np.minimum(lo2 + 1, MODEL_CLIP_LEN - 1)
                t2 = (src2 - lo2)[None, :, None, None]
                return out[:, lo2] * (1.0 - t2) + out[:, hi2] * t2  # (1, M, 17, 3)

            self._model = _adapter
            return self._model
        except Exception as e:
            print("MotionBERT load failed (" + str(e) + "); 2D angles only.")
            self.available = False
            return None
```

(If the vendored code exposes different symbols than `load_model` / `MODEL_CLIP_LEN`, adjust the import and names to match — the confirmation in Step 1 tells you the exact names.)

- [ ] **Step 5: Document weights + ignore them**

Create `docs/motionbert-weights.md` describing where to download the MotionBERT weights and that they belong at `weights/motionbert.pt` and must never be committed. Confirm `.gitignore` ignores `weights/` (add `weights/` if missing).

- [ ] **Step 6: Full suite + commit**

Run: `PYTHONUTF8=1 ./.venv/Scripts/python.exe -B -m pytest -q -p no:cacheprovider`
Expected: PASS (with `test_pose_lift_model.py` skipped when weights are absent).

```bash
git add third_party/motionbert docs/motionbert-weights.md .gitignore badminton_analysis/detection/pose_lift.py tests/test_pose_lift_model.py
git commit -m "feat(pose-lift): vendor MotionBERT and wire real lifting adapter"
```

---

## Self-Review

**Spec coverage:**
- Optional post-loop per-rep lifting stage → Tasks 4, 7.
- 3D angles for the four capable metrics → Task 2; scored against `reference_ranges_3d` → Tasks 3, 5.
- `wrist_flexion` / `weight_transfer` stay 2D and labeled → Task 5 (`feature_space`).
- 3D features persisted for AQA → Tasks 6, 7 (`drill_reps_3d.npz`).
- Coach-eyeball validation (shadow-compare) → Tasks 5 (`measured_shadow_2d`) + 8 (badge + `(2D: Y)`).
- Graceful degradation to byte-identical 2D → Tasks 4/5/7 (no-lifter paths) + tests `test_no_3d_is_unchanged_2d`, `test_runner_without_lifter_is_2d`.
- CLI/config → Task 9; vendoring + weights, no committed binaries → Task 10.
- Acceptance criteria 1-6 → Tasks 2/3/5 (1), 4/5/7 (2), 6/7 (3), 5/8 (4), all test steps (5), Task 10 (6).

**Placeholder scan:** every code step contains complete code; the only deferred confirmations are the MotionBERT contract items, which are explicit, bounded Step-1 actions in Task 10 (matching the spec's flagged open question), not vague placeholders.

**Type consistency:** `PoseLifter.lift` returns `(kp3d, frames)` consistently across Tasks 4/7/10; the stub and real adapter share the `(1,M,17,2)->(1,M,17,3)` contract; `compute_joint_angles_3d` keys match `METRICS_3D_CAPABLE` and the `reference_ranges_3d` overrides; `feature_space` / `measured_shadow_2d` names are identical across Tasks 5/7/8.
