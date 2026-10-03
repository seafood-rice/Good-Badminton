# Workstream: learned-hit-detection

- **workstream_id:** `learned-hit-detection`
- **Objective:** detect almost every hit by both players on a full match with a learned hit
  detector, which unblocks per-player 7-technique scoring. Spec:
  `docs/superpowers/specs/2026-10-04-learned-hit-detection-design.md`.
- **Scope paths (design claim):** `.ai/workstreams/learned-hit-detection.md`,
  `docs/superpowers/specs/2026-10-04-learned-hit-detection-design.md`,
  `docs/superpowers/plans/2026-10-04-learned-hit-detection.md`. Implementation paths get
  claimed when the plan is approved.
- **State:** active
- **Branch:** `claude/learned-hit-detection`, created from `origin/main` (`10de179`)
- **Worktree:** primary checkout (no separate worktree)
- **Base commit:** `10de179e7923813c3f35764f99525b0637d59804`
- **Head commit:** `fc16f97331e467a86c62aa310ca29cc6d70b71e0`
- **Claim id:** `5b4f1a3f-1e63-492a-9055-668e28ce458a`
- **Last milestone:** 2026-10-03T16:57:29Z - Implementation plan written (17 tasks incl. controller-run data/training/validation and a contingent fine-tune); awaiting owner review and choice of execution method.

## Acceptance criteria

The spec's §10 done bar, measured on the owner's labelled Axelsen stretches: recall ≥ 0.60,
precision ≥ 0.70, hitter correct ≥ 0.85, within ±0.2 s. The full Axelsen run must complete end
to end with provenance. With no hit-model weights present, outputs must be identical to today.

## Decisions and rationale

See the spec §2 (owner decisions) and §3 (pretrained options rejected). In short:
- Detection comes before scoring.
- Coverage is full ("whatever it takes").
- The detector is learned (approach C), trained on ShuttleSet, plus the owner's own labels if
  transfer is weak.
- Neither pretrained candidate was usable: one has lost weights, the other has unpublished
  preprocessing.

- The owner approved the written spec on 2026-10-04 (local time). The plan followed the same
  day.

## Changed paths

- `docs/superpowers/specs/2026-10-04-learned-hit-detection-design.md` (new)
- `.ai/workstreams/learned-hit-detection.md` (new)
- `docs/superpowers/plans/2026-10-04-learned-hit-detection.md` (new)

## Verification

Not run: this is a docs-only change. Supporting measurements (baseline runs, TrackNetV3 mode
timing) are recorded in the spec §1.

## Blockers

- The owner's hit labels (`outputs/axelsen-hit-labels/LABELS.md`) are needed for any go/no-go.
- The ShuttleSet videos are fetched by the owner (spec §6.1, §12).

## Next action

Owner reviews the plan and picks an execution method; then Task 0 (takeover after 2026-10-03T22:54:56Z to widen the claim) and Task 1.

<!-- ai-continuity:milestones:start -->
- 2026-10-03T14:54:56Z - state: active - Spec written after section-by-section design approval in chat; awaiting owner review of the written spec.
  - Changed paths: `docs/superpowers/specs/2026-10-04-learned-hit-detection-design.md`, `.ai/workstreams/learned-hit-detection.md`
  - Verification: not-run - reason: docs-only change
  - Next action: Owner reviews the written spec; on approval write the implementation plan and claim implementation paths.
- 2026-10-03T16:57:29Z - state: active - Implementation plan written (17 tasks incl. controller-run data/training/validation and a contingent fine-tune); awaiting owner review and choice of execution method.
  - Changed paths: `docs/superpowers/plans/2026-10-04-learned-hit-detection.md`, `.ai/workstreams/learned-hit-detection.md`
  - Verification: not-run - reason: docs-only change
  - Next action: Owner reviews the plan and picks an execution method; then Task 0 (takeover after 2026-10-03T22:54:56Z to widen the claim) and Task 1.
<!-- ai-continuity:milestones:end -->
