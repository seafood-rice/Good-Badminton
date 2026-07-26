# MotionBERT 3D Pose Lifting for Posture Analysis — Design

- **Date:** 2026-07-26
- **Workstream:** `motionbert-3d-lifting`
- **Status:** Approved design (pre-plan)
- **Author:** Claude (brainstormed with the user)

## Context

Kestrel's court-free posture pipeline (`badminton_analysis/posture/`) estimates 2D
keypoints per frame (YOLO11-pose default, RTMPose optional), segments rep windows, and
scores each rep two ways:

1. **Rule-based angles** — `analysis/joint_angles.py` computes joint angles in the 2D
   image plane at the contact frame; `analysis/biomechanics.py` scores them against
   hand-tuned ranges in `analysis/reference_ranges.py`.
2. **Learned quality (TCN)** — `quality/normalize.py` builds a 2D `(64, 34)` pose window
   fed to a TorchScript temporal model (`quality/scorer.py`), gated to `high_clear` only.

The recurring, documented weakness is that **2D image-plane angles are view-dependent**:
`posture/system.py` and `reference_ranges.py` both carry comments noting foreshortening on
front/side camera angles distorts the measured angles, forcing conservative, camera-biased
thresholds. This ceiling limits every downstream score.

## Goal

Add an **optional 3D pose-lifting stage** (MotionBERT) that turns the existing 2D keypoints
into lifted 3D joints, and use those 3D joints to compute anatomical angles with **reduced
view dependence** for the **rule-based path**. Persist the 3D features so a later Action
Quality Assessment (AQA) sub-project can consume them without re-doing the wiring.

> **Amended after implementation (Task 10 + final review).** This section originally said
> "view-invariant 3D joints" / "view-independent anatomical angles". That overstates what
> MotionBERT delivers: its 3D-pose head outputs perspective-projected **2.5D image space**
> (x, y in image space plus a commensurately-scaled root-relative depth), not
> rotation-invariant camera-space 3D. The lifted angles are measurably less view-dependent
> than 2D image-plane angles, but they are not view-invariant, and their residual bias
> varies with subject distance and position in frame. See
> `badminton_analysis/detection/pose_lift.py`'s module docstring for the confirmed contract
> and `docs/motionbert-weights.md` for the validation checklist.
>
> The same review also dropped `trunk_rotation` from the 3D-scored set (below): the 2D
> metric measures the shoulder line's tilt from the image horizontal, so no 3D quantity
> reproduces it, and the 3D candidate duplicated `hip_shoulder_separation`. The 3D-scored
> metrics are `elbow_extension`, `knee_flexion`, `hip_shoulder_separation`.

### In scope

- 2D→3D lifting of per-rep windows via MotionBERT.
- 3D-aware anatomical angles for `elbow_extension`, `knee_flexion`,
  `hip_shoulder_separation` (`trunk_rotation` was in this list as designed but was dropped
  from 3D scoring in the final review — see the amendment note under Goal).
- A parallel, literature-grounded `reference_ranges_3d` table.
- Persisting per-rep 3D features to disk for the future AQA scorer.
- Rendering 3D angles on the overlay/report for the coach-eyeball validation gate.
- Graceful degradation to today's exact 2D behavior when lifting is unavailable.

### Out of scope (deferred)

- Retraining the TCN quality scorer on 3D inputs (needs labels → the AQA sub-project).
- `wrist_flexion` and `weight_transfer` in 3D (see Future Work — each is missing the 3D
  signal it needs, not merely unlabeled).
- Any labelling program or AQA model training.

## Selected approach

**Approach A — an optional post-loop lifting stage feeding a 3D-aware angle path.** It
mirrors the existing optional-stage pattern (`_build_racket_detector`,
`_build_quality_scorer`): construct-if-configured, degrade otherwise. Lifting runs
**post-loop, per rep window** inside `PostureRunner`, never in the per-frame capture loop,
so GPU time is spent only on windows that get scored.

Rejected alternatives: lifting the whole track up front (burns compute on non-rep frames,
larger change to the capture/track structures); replacing 2D pose with a native monocular
3D estimator (rips out the 2D stack the racket-head inference and rep segmenter depend on).

## Architecture and data flow

```
capture loop (unchanged): frame → YOLO/RTMPose → 2D COCO-17 kp, conf → self._frames

PostureRunner.run  ── per rep window ───────────────────────────────────────────┐
   segment_reps → window_frames (2D)                                             │
        │                                                                        │
        ├─▶ PoseLifter.lift(window)  ─ COCO-17→H36M-17 → MotionBERT → (N,17,3)    │
        │        (optional; None → 2D path)         │                            │
        │                                           ├─▶ persist to               │
        │                                           │   drill_reps_3d sidecar    │
        │                                           └─▶ attach 3D kp to          │
        │                                               contact-frame record     │
        ▼                                                     │                  │
   BiomechanicalAnalyzer.analyze ── has 3D? ─▶ compute_joint_angles_3d           │
                                             → score vs reference_ranges_3d      │
                                  └── else ──▶ compute_joint_angles (2D)         │
                                             → reference_ranges   [unchanged]    │
```

- **In-memory:** the contact-frame record gains an optional `keypoints_3d` (H36M-17,
  root-relative). Downstream branches only on whether it is present.
- **On disk:** `outputs/<video>/posture/drill_reps_3d.npz` — per-rep 3D sequences plus
  metadata (model id, joint format, normalization). This is the artifact the deferred AQA
  scorer trains on.
- **Report:** each rep's `per_metric` is stamped with `feature_space: "2d" | "3d"`; the
  drill summary reports how many reps scored in 3D vs 2D-fallback (mirroring the existing
  racket detected/inferred and quality scored-reps counters).

## Components

New / touched, each with a single clear purpose:

- **`detection/pose_lift.py`** *(new)* — `PoseLifter`, structured like `QualityScorer`:
  an `available` flag, lazy model load, and **constructor injection**
  (`PoseLifter(model=...)`) so tests need no GPU or weights. `lift(window_frames) →
  (N, 17, 3) | None`. Owns the COCO-17→H36M-17 remap, MotionBERT input normalization, and
  device selection (`auto`: CUDA if present, else CPU).
- **`analysis/joint_angles.py`** *(touched)* — add `compute_joint_angles_3d(kp3d,
  dominant, conf)` computing true anatomical 3D angles for the four capable metrics.
  Existing 2D functions untouched.
- **`analysis/reference_ranges_3d.py`** *(new)* — parallel 3D range table, literature-
  grounded first-pass values (3D anatomical angles map directly to sport-science
  literature). Same schema as `reference_ranges.py` (`min`/`max`/`ideal`/`weight`).
- **`analysis/biomechanics.py`** *(touched)* — branch on the presence of 3D keypoints;
  stamp each report/metric with `feature_space`. A 3D-scored rep is a **labeled blend**:
  3D for the four capable metrics, 2D for `wrist_flexion` and `weight_transfer`. Never
  silently mix incomparable numbers.
- **`posture/system.py`** *(touched)* — `_build_pose_lifter()`; thread `lift_model_path` /
  `lift_device`; pass the lifter to `PostureRunner`; write the 3D sidecar; extend the
  metadata/summary counters.
- **`visualization/technique_overlay.py`** *(touched)* — render 3D angle values (labeled)
  for the coach-eyeball gate; support the shadow-compare view (below).
- **CLI** (`app.py`, `main_posture.py`) — `--lift-model`, `--lift-device`, mirroring the
  quality/racket model plumbing. Weights live in `weights/` (gitignored); MotionBERT is
  vendored under `third_party/motionbert/` like `third_party/bst`.

## Per-metric 3D capability

| Metric | 3D-capable with MotionBERT alone? | Reason |
|---|---|---|
| `elbow_extension` | Yes | shoulder–elbow–wrist all lifted |
| `knee_flexion` | Yes | hip–knee–ankle all lifted |
| `trunk_rotation` | Yes | shoulder/hip lines lifted |
| `hip_shoulder_separation` | Yes | shoulder/hip lines lifted |
| `wrist_flexion` | No → stays 2D | racket head / hand not lifted (H36M-17 has no hand joints; the racket is not a joint) |
| `weight_transfer` | No → stays 2D | MotionBERT output is pelvis-root-relative, which removes the global translation this metric measures |

## Reference-range recalibration

3D anatomical angles have different numeric distributions than 2D image-plane angles, so
reusing `reference_ranges.py` for 3D would produce wrong scores. A separate
`reference_ranges_3d.py` is introduced; each scoring path uses its own table, and both
remain independently tunable. Upside: 3D anatomical angles are exactly what biomechanics
literature reports, so the 3D table can be literature-grounded from the start — a stronger
footing than the current file's self-described "indicative first-release" 2D values.

## Error handling and graceful degradation

Mirrors the existing optional stages, at **per-rep granularity** so one bad lift never
kills a run:

- No torch / no weights / CUDA requested but absent → `PoseLifter.available = False`, log
  once, whole run uses the 2D path (bit-for-bit today's behavior).
- `lift()` returns `None` (too few posed frames, remap failure, model exception) → that rep
  falls back to 2D; other reps still lift.
- `--lift-device auto` selects CUDA if present, else CPU. Accuracy-first keeps the full
  model config; CPU merely runs slower, never blocked.

## Validation gate

The accepted gate is a **coach eyeballing overlays** — a soft, subjective gate. To make it
a real comparison rather than a guess, lifting runs in **shadow-compare mode**: when
enabled it computes both 2D and 3D angles and renders both side-by-side on the
overlay/report, so the coach sees exactly where they diverge (the view-dependent cases).
Until a coach signs off, 2D can remain the headline score with 3D shown alongside; a single
flag flips 3D to headline once trusted. 2D remains the automatic fallback throughout.

## Testing strategy (TDD, no GPU/weights needed)

Following the existing `test_joint_angles.py` / `test_quality_*.py` style, using
constructor injection like `QualityScorer(model=...)`:

- **Remap:** COCO-17→H36M-17 index mapping on a synthetic pose.
- **3D angles:** synthetic 3D poses with known angles (straight arm → ~180°, right angle →
  90°) — closed-form assertions.
- **Lifter:** a stub model returns deterministic 3D → assert shape, remap, normalization,
  and the `None`-on-degradation paths.
- **Biomechanics branch:** report stamps `feature_space` correctly; a mixed 2D/3D rep is
  labeled, not blended.
- **Persistence:** `drill_reps_3d.npz` schema round-trips.
- **Integration:** a small end-to-end posture run with a stubbed lifter yields a 3D report
  plus the sidecar; and with the lifter absent, every score and measured value is identical
  to today's 2D result (the serialized artifacts gain additive fields — see acceptance
  criterion 2).

## Future work (explicitly deferred)

- **`wrist_flexion` in 3D** — requires a hand-aware / whole-body pose model (e.g. MediaPipe
  Holistic, SMPL-X-style) or a 3D racket-head estimate, so the racket reference vector is
  itself 3D.
- **`weight_transfer` in 3D** — requires a global root-trajectory / depth estimate that
  MotionBERT alone does not produce (its output is root-relative).
- **AQA scorer on 3D features** — the deferred sub-project: a labelling program plus
  retraining the quality scorer on the `drill_reps_3d` artifact, extended beyond
  `high_clear`.

## Acceptance criteria

1. With a lifter configured, capable metrics (`elbow_extension`, `knee_flexion`,
   `hip_shoulder_separation`) are computed in 3D and scored against
   `reference_ranges_3d`; `wrist_flexion`, `weight_transfer` and `trunk_rotation` remain 2D
   and are labeled as such.
2. With no lifter (no weights / no torch / CPU-only + not configured), posture output is
   **score-identical and schema-additive** relative to the current 2D pipeline: every score
   and measured value matches, and the only differences in serialized output are new
   additive fields (`feature_space`, `measured_shadow_2d`, the `lift` block, the HTML
   angle-space badge). Nothing existing is removed, renamed or renumbered.
3. Per-rep 3D features are written to `drill_reps_3d.npz` with documented schema/metadata.
4. Shadow-compare mode renders 2D and 3D angles side-by-side for coach review.
5. All new logic is covered by tests that run without a GPU or real weights (stubbed
   model, synthetic poses).
6. No weights, datasets, or large binaries are committed; MotionBERT code is vendored,
   weights are user-supplied under `weights/`.

## Risks and open questions

- **MotionBERT input contract** — exact expected clip length, coordinate normalization, and
  confidence handling must be confirmed against the vendored implementation during
  planning; the COCO→H36M remap is the main correctness-sensitive glue.
- **Subjective validation** — coach eyeball will not catch systematic 3D bias; 2D stays
  authoritative until sign-off, and AthletePose3D benchmarking remains an available
  follow-up if a hard number is later wanted.
- **Blend coherence** — care is needed so the drill summary never aggregates 2D and 3D
  metrics as if identically scaled; per-metric `feature_space` labeling is the guard.

## Links

- Current 2D stack: `analysis/joint_angles.py`, `analysis/biomechanics.py`,
  `analysis/reference_ranges.py`, `quality/normalize.py`, `quality/scorer.py`,
  `posture/system.py`.
- Continuity workflow: `.ai/WORKFLOW.md`; workstream status:
  `.ai/workstreams/motionbert-3d-lifting.md`.
