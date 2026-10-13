# Workstream: learned-hit-detection

- **workstream_id:** `learned-hit-detection`
- **Objective:** detect almost every hit by both players on a full match with a learned hit
  detector, which unblocks per-player 7-technique scoring. Spec:
  `docs/superpowers/specs/2026-10-04-learned-hit-detection-design.md`.
- **Scope paths (implementation claim):** the spec, plan and this file; `badminton_analysis/stroke/`,
  `system.py`, `stroke_recog/recognizer.py`, `shuttle_track/tracknet.py`; the vendored
  `third_party/tracknet/infer.py` and `CONTRACT.md`; `static/kestrel.js`, `main.py`, `app.py`;
  the hit-detector scripts; the hit-detector test files plus three existing tests the work
  touched. The design claim was taken over on 2026-10-04 after its lease expired. The owner
  waived waiting for it, so the takeover came after the code was written.
- **State:** active
- **Branch:** `claude/learned-hit-detection`, created from `origin/main` (`10de179`)
- **Worktree:** primary checkout (no separate worktree)
- **Base commit:** `10de179e7923813c3f35764f99525b0637d59804`
- **Head commit:** `9bbf8358adbdd00ec76c9e14b2b1686a81157f02`
- **Claim id:** `4a0e6d57-d68a-45c1-8fff-ceb5d00c1123` (takeover of `5b4f1a3f-1e63-492a-9055-668e28ce458a`)
- **Last milestone:** 2026-10-13T21:50:02Z - First model scored on owner labels: recall 0.80, precision 0.67, hitter 0.88 (precision short by 0.03, mostly dead-time FPs). Owner chose to retrain with dead time; --gaps builder mode added and the gap build started.

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
  day. It was executed subagent-driven: 12 code tasks, each reviewed, then a whole-branch review
  with one fix wave.
- Rulings from the final review:
  - The pose family is recorded in the model sidecar, and a mismatch falls back.
  - Whole-video TrackNetV3 runs only with a hit model, or when `dense_whole_video=True` (the
    dataset builder sets it).
  - The alignment offset is the centre of the matching plateau.
  - Videos below 30 fps are rejected for training.
  - The scorer checks `hits.json` `source`.
  The full ruling list is in the SDD ledger (local), and is summarised in the PR description.
- **Alignment rule v3.** A match is accepted when the pooled best offset over 6 rallies is within
  0.2 s of zero and its robust z is at least 3.5. The applied offset is 0.
  - Accepted: 5 matches.
  - Rejected: Momota v Axelsen, which is 25 fps. Sindhu v Chochuwong is the reserve and is the
    held-out validation match.
- **Dataset tracking mode is nonoverlap** (a ruling made for build time). At inference, weight mode
  scores better and is the default.
- **2026-10-14, owner choice 1: retrain with dead time.** The rally clips stay as they are.
  `build_hit_dataset.py --gaps` adds one clip of up to 10 s per gap between consecutive labelled
  rallies, in `data/hit_dataset_gaps/`. The model is then retrained on both folders.

## Changed paths

- Spec and plan (new).
- New modules: `badminton_analysis/stroke/hit_*.py`, `shuttle_turns.py`.
- Changed modules: `system.py`, `stroke_recog/recognizer.py`, `shuttle_track/tracknet.py`,
  `third_party/tracknet/infer.py` and `CONTRACT.md`, `static/kestrel.js`, `main.py`, `app.py`.
- New scripts: `scripts/{score_hits,shuttleset_labels,check_shuttleset_alignment,build_hit_dataset,train_hit_detector}.py`.
- Tests: the new hit-detector tests, plus edits to `tests/test_tracknet_integration.py`,
  `tests/test_bst_integration.py`, `tests/test_rally_segmentation.py` and
  `tests/test_app_library.py`.
- This file.

## Verification

Full suite at `33c86be`: 1357 passed, 3 skipped (the symlink tests, WinError 1314).

A synthetic end-to-end seam check by the final reviewer (60 fps: dataset format → train →
save/load → detect → `hits.json` → score) matched every label exactly.

**Dataset v1.** 396 rallies with 3,829 hits from 5 matches. Training ran for 40 epochs.

**Held-out Sindhu match:** recall 0.935, precision 0.916, hitter 0.985. This is optimistic,
because the threshold was chosen on the same match.

**Owner's Axelsen stretches** (51 hits, ±0.2 s):

| Detector | Recall | Precision | Hitter |
|---|---|---|---|
| Learned, weight mode | 0.80 | 0.67 | 0.88 |
| Learned, nonoverlap mode | 0.67 | 0.51 | 0.79 |
| Contact rule | 0.18 | 0.82 | 0.67 |

In weight mode, 9 of the 20 false positives fall in dead time between rallies. Without them,
precision would be 0.79.

**Builder tests at `9bbf835`:** 18 passed (`tests/test_build_hit_dataset.py`,
`tests/test_hit_training.py`).

## Blockers

- None. The dead-time build runs detached. Pause it with
  `pwsh -File data/hit_dataset_gaps/stop_build.ps1`, and resume it with `resume_build.ps1` in the
  same folder.

## Next action

When data/hit_dataset_gaps/build.log shows BUILD DONE: retrain on data/hit_dataset + data/hit_dataset_gaps (val Sindhu), re-score the 4 Axelsen stretches, then plan Task 15 steps 5-9.

<!-- ai-continuity:milestones:start -->
- 2026-10-03T14:54:56Z - state: active - Spec written after section-by-section design approval in chat; awaiting owner review of the written spec.
  - Changed paths: `docs/superpowers/specs/2026-10-04-learned-hit-detection-design.md`, `.ai/workstreams/learned-hit-detection.md`
  - Verification: not-run - reason: docs-only change
  - Next action: Owner reviews the written spec; on approval write the implementation plan and claim implementation paths.
- 2026-10-03T16:57:29Z - state: active - Implementation plan written (17 tasks incl. controller-run data/training/validation and a contingent fine-tune); awaiting owner review and choice of execution method.
  - Changed paths: `docs/superpowers/plans/2026-10-04-learned-hit-detection.md`, `.ai/workstreams/learned-hit-detection.md`
  - Verification: not-run - reason: docs-only change
  - Next action: Owner reviews the plan and picks an execution method; then Task 0 (takeover after 2026-10-03T22:54:56Z to widen the claim) and Task 1.
- 2026-10-04T04:16:47Z - state: active - Code complete: plan Tasks 1-12 implemented and task-reviewed, whole-branch review with one fix wave re-reviewed clean; awaiting owner labels and ShuttleSet videos for Tasks 13-15.
  - Changed paths: `badminton_analysis/stroke`, `badminton_analysis/system.py`, `badminton_analysis/stroke_recog/recognizer.py`, `badminton_analysis/shuttle_track/tracknet.py`, `third_party/tracknet/infer.py`, `third_party/tracknet/CONTRACT.md`, `static/kestrel.js`, `main.py`, `app.py`, `scripts/score_hits.py`, `scripts/shuttleset_labels.py`, `scripts/check_shuttleset_alignment.py`, `scripts/build_hit_dataset.py`, `scripts/train_hit_detector.py`, `.ai/workstreams/learned-hit-detection.md`
  - Verification: passed - command `PYTHONUTF8=1 ./.venv/Scripts/python.exe -B -m pytest -q -p no:cacheprovider --basetemp <unique> (1357 passed, 3 skipped)` - commit `33c86be4a610876c4e7a1cd03e2315490f8793d5` - dirty paths: `.ai/workstreams/learned-hit-detection.md`
  - Next action: Owner fills outputs/axelsen-hit-labels/LABELS.md and fetches the five ShuttleSet videos; controller then runs plan Tasks 13-15 (alignment, ~22 h dataset build, training, validation, outcome addendum).
- 2026-10-13T21:50:02Z - state: active - First model scored on owner labels: recall 0.80, precision 0.67, hitter 0.88 (precision short by 0.03, mostly dead-time FPs). Owner chose to retrain with dead time; --gaps builder mode added and the gap build started.
  - Changed paths: `scripts/build_hit_dataset.py`, `tests/test_build_hit_dataset.py`, `.ai/workstreams/learned-hit-detection.md`
  - Verification: passed - command `pytest tests/test_build_hit_dataset.py tests/test_hit_training.py (18 passed)` - commit `9bbf8358adbdd00ec76c9e14b2b1686a81157f02` - dirty paths: `.ai/workstreams/learned-hit-detection.md`
  - Next action: When data/hit_dataset_gaps/build.log shows BUILD DONE: retrain on data/hit_dataset + data/hit_dataset_gaps (val Sindhu), re-score the 4 Axelsen stretches, then plan Task 15 steps 5-9.
<!-- ai-continuity:milestones:end -->
