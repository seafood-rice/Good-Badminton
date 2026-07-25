# Workstream: motionbert-3d-lifting

- **workstream_id:** `motionbert-3d-lifting`
- **Objective:** add an optional MotionBERT 3D pose-lifting stage that gives the rule-based
  posture path view-invariant anatomical angles, and persist per-rep 3D features for a
  later AQA scorer, per
  `docs/superpowers/specs/2026-07-26-motionbert-3d-pose-lifting-design.md`.
- **Scope paths:** `docs/superpowers/specs/2026-07-26-motionbert-3d-pose-lifting-design.md`,
  `.ai/workstreams/motionbert-3d-lifting.md`
- **State:** active
- **Branch:** `claude/motionbert-3d-lifting`
- **Worktree:** the primary local checkout (no linked worktree)
- **Base commit:** `793a61e2aec257129adbbbbf0896e006aa2aee08`
- **Head commit:** `73873c737f708f3885cfdf0e9f66223a30140a23`
- **Claim id:** `1f5555cd-eb3c-4e66-8443-be517dfab644`
- **Last milestone:** 2026-07-25T21:55:57Z - Design approved and spec committed (2df7dbc); 10-task TDD implementation plan written and committed (73873c7).

## Acceptance criteria

See the design spec's "Acceptance criteria" section (six items). Implementation-level
checklists will be added by the implementation plan once written.

## Decisions and rationale

- **Scope this first sub-project to the MotionBERT lifting stage only.** The AQA scorer and
  its labelling program are a separate, higher-risk sub-project gated on labelled data;
  lifting ships value immediately and de-risks the measurement half.
- **3D powers the rule-based angles now; the existing TCN quality scorer stays on 2D.**
  Rewiring the TCN to 3D would require retraining on labels, which belongs to the deferred
  AQA sub-project. 3D features are persisted so that work does not re-do the wiring.
- **`wrist_flexion` and `weight_transfer` stay 2D and labeled.** Neither has an honest 3D
  form with MotionBERT alone: the racket/hand is not lifted, and root-relative output
  removes the global translation weight transfer measures. Both recorded as future work.
- **Separate `reference_ranges_3d.py`.** 3D anatomical angles have different distributions
  than 2D image-plane angles; a parallel, literature-grounded table keeps both scoring
  paths coherent and independently tunable.
- **Approach A (optional post-loop per-rep lifting stage)** over whole-track lifting or a
  native 3D pose model: smallest blast radius, matches the existing optional-stage pattern,
  spends GPU only on scored windows, keeps 2D as a live fallback.
- **Coach-eyeball validation via shadow-compare mode**, with 2D remaining authoritative
  until sign-off.

## Changed paths

- `docs/superpowers/specs/2026-07-26-motionbert-3d-pose-lifting-design.md` (created)
- `.ai/workstreams/motionbert-3d-lifting.md` (created)

## Verification

- Not run — design-only phase, no code changed yet. Verification gates will be defined by
  the implementation plan and run during implementation.

## Blockers

None. (The AQA follow-up sub-project is gated on a labelled-data source, but it is out of
scope here and does not block this stage.)

## Next action

User selects execution mode (subagent-driven vs inline); then implement Task 1 of the plan.

<!-- ai-continuity:milestones:start -->
- 2026-07-25T21:55:57Z - state: active - Design approved and spec committed (2df7dbc); 10-task TDD implementation plan written and committed (73873c7).
  - Changed paths: `docs/superpowers/specs/2026-07-26-motionbert-3d-pose-lifting-design.md`, `docs/superpowers/plans/2026-07-26-motionbert-3d-pose-lifting.md`, `.ai/workstreams/motionbert-3d-lifting.md`
  - Verification: not-run - reason: Design/plan phase only; no code changed yet.
  - Next action: User selects execution mode (subagent-driven vs inline); then implement Task 1 of the plan.
<!-- ai-continuity:milestones:end -->
