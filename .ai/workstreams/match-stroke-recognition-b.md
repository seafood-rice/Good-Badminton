# Workstream: match-stroke-recognition-b

- **workstream_id:** `match-stroke-recognition-b`
- **Objective:** Sub-project B - full-match stroke recognition, extending the completed
  Sub-project A per-rally analysis, per the approved completion bar.
- **Scope paths (B11 claim `3388e5e9-cd73-46ad-b066-452e89c4dcf0`):**
  `.ai/workstreams/match-stroke-recognition-b.md`, `badminton_analysis/court/mapper.py`,
  `badminton_analysis/system.py`, `badminton_analysis/stroke/rallies.py`,
  `badminton_analysis/stroke/shot_boundary.py`, `app.py`, `static/kestrel.js`,
  `scripts/score_rally_labels.py`, `scripts/check_rally_gate.py`,
  `tests/test_court_annotation_headless.py`, `tests/test_court_view_gate.py`,
  `tests/test_rally_segmentation.py`, `tests/test_shot_boundary.py`, and the B11 plan, B11
  spec and B completion-bar spec.
- **State:** active
- **Branch:** `claude/match-stroke-recognition-b`, recreated 2026-09-29 from `origin/main` for
  B11. (The B1 branch of the same name was squash-merged as #2/#3 and deleted; the 2026-09-20
  defect fixes and the B11 plan landed from `claude/b11-rally-detection` as #7.)
- **Worktree:** primary checkout (no separate worktree)
- **Base commit:** `b4ee267bfac35fb75dd8ee81812a3a506b546d31` (`origin/main` after #10)
- **Head commit:** `b4ee267bfac35fb75dd8ee81812a3a506b546d31`
- **Last milestone:** 2026-09-29 - B11 execution started: branch recreated from `main`, scope
  claimed, plan to be executed task by task from Task 1. Previous milestone, 2026-07-29 -
  **B1 (both-player capture + hitter selection) complete.**
  All 7 tasks implemented via subagent-driven-development (fresh implementer + task review
  per task), plus a final whole-branch review that found and fixed 5 Important
  cross-task issues invisible at task scope: the racket YOLO model ran twice per frame,
  `_capture_side_pose` had no distance gate (letting one player's pose populate both sides
  when only one was detected), `detect_contacts_multi`'s `min_gap` was shared across
  players (silently dropping a genuine reply from the other side), a stale module
  docstring, and a missing end-to-end capture-to-recognition test. Re-review confirmed all
  5 fixed with no new breakage. Full committed suite: 433 passed.

## Acceptance criteria

See `docs/superpowers/specs/2026-07-29-match-stroke-recognition-b-design.md` section 2
("Done means") for the full list. Summary: both-player capture + proximity-based hitter
selection; a background-job deep-analysis path with live UI status; rally/play detection
fixed on both fixed-camera and broadcast footage so full-rally coverage is achievable;
per-rally BST invocation with coverage metadata; fps/resolution-normalized constants;
non-regression with no weights present; full committed suite green.

## Decisions and rationale

- Completion bar drafted by scope-planner, corrected against real on-disk full-match run
  artifacts (not just the existing design docs) — see the spec's section 0 for the premise
  correction (rally/play detection, not the frame-budget guard, is the dominant blocker).
- Owner resolved the three open questions on 2026-07-29 (spec section 6a): background job
  with UI-tracked status; both footage types in scope; strict full-rally coverage as the
  target now that time is no longer the limiting factor. This pulled two previously-deferred
  items (async job redesign, broadcast play detection) into scope.
- Owner approved landing the in-flight `player.py` net-line fix as part of this workstream.
- First implementation step, per owner direction: B1 (both-player capture + hitter-by-
  proximity selection), validated on the known-working 750-frame Axelsen clip segment where
  contacts already fire — not the full-match background-job/rally-detection work yet.
- **2026-08-23 — owner resolved B11's open questions (B11 spec §12a).** A = **no** (true
  multi-camera TV broadcast stays in scope, so B11 gains a shot-boundary component that no
  on-disk footage can validate); C = **no**, replaced same day with **uniform coarse windows
  over gate-passed court-view frames, marked degraded, with stroke recognition withheld on
  them** — chosen over labelling them unreliable because BST labels over non-rally windows are
  close to noise and a badge is weaker than the impression that strokes were detected;
  D = **yes** (completion-bar R10 amended, new R11 added). B and E were settled by measurement in the PR #4 shuttle investigation rather
  than by decision: there is no usable shuttle signal on the fixed-camera footage, so B11's
  swing signal is primary rather than a fallback.
- **B11 is the next milestone delivery**, chosen because it is the confirmed gate on all
  further real-footage validation (B1's own validation was blocked by exactly this) and it
  needs no new footage, unlike net-shot posture support, the two unvalidated posture
  thresholds, and §0.25 option D.

## Changed paths

- `badminton_analysis/tracking/player.py`, `tests/test_player_half_classification.py`
  (commit `fe31415`, net-line half-classification fix).
- `docs/superpowers/specs/2026-07-29-match-stroke-recognition-b-design.md` (completion bar).
- `docs/superpowers/plans/2026-07-29-match-stroke-recognition-b1.md` (B1 implementation plan).
- `badminton_analysis/detection/racket.py`, `badminton_analysis/stroke/events.py`,
  `badminton_analysis/stroke_recog/{hits,inputs,recognizer}.py`, `badminton_analysis/system.py`
  (the 7 B1 tasks + final-review fix pass), plus their test files.

## Verification

- Per-task: 7 task-scoped reviews, all approved (task 7's fix-in-round for `min_gap`/pose-gate
  issues landed in the final-review pass below, not per-task).
- Final whole-branch review: 5 Important findings, 1 fix round, re-review clean, no new
  Critical/Important breakage.
- Full committed suite at `HEAD` (`1ac402b`): 433 passed (`--ignore=tests/test_ai_handoff.py`);
  also ran the full literal suite including that continuity file once during the task loop:
  713 passed, 0 failed.
- **Controller-run real-footage validation: run, result BLOCKED UPSTREAM** (2026-07-29). Full
  outcome recorded in the plan's own "Validation outcome" section
  (`docs/superpowers/plans/2026-07-29-match-stroke-recognition-b1.md`). Summary: 0 BST strokes
  on the reconstructed clip, root-caused to `is_court_view`'s rally/court-detection gate
  limiting real analysis-track capture to one 87-frame window out of 752 — the same class of
  blocker the original BST T9 validation hit, and explicitly B11's scope, not B1's. B1's own
  correctness (done-means 1-3) already independently proven by a real end-to-end test added in
  the final-review fix pass; only item 4 (real-rally alternation) is unvalidated.
- **Critical data point, CORRECTED 2026-08-01:** this 25s clip took **17,443s (≈4.85h)**
  wall-clock, which this bullet originally attributed to a CPU-only machine
  (`torch==2.5.1+cpu`, no CUDA). **That attribution was false.** This machine has an NVIDIA
  RTX 4090 and the project venv's torch is `2.5.1+cu121` with CUDA available; the
  `requirements.txt` pin does not match what is actually installed (a real, separate
  reproducibility hazard worth tracking — a fresh install from `requirements.txt` would
  genuinely be CPU-only). The 17,443s was almost entirely a blocked interactive stdin prompt
  (`"Press Enter/Y to accept auto detection; press M/R/Esc for manual annotation."`, printed
  despite `--display false`), not compute: `auto_court_preview.png` was written at 02:43:06
  and the run did not proceed until `court_annotations.txt`/`metadata.json` appeared at
  07:26:56, 4h43m50s later. Actual analysis time was ~6m51s. Full investigation in
  `docs/superpowers/plans/2026-07-29-match-stroke-recognition-b1.md`'s "Validation outcome"
  section and `docs/superpowers/specs/2026-07-30-rally-play-detection-b11-design.md` §0.10.
  The owner's Q1 decision (background job) is still correct, but for two different reasons:
  (1) the stdin-blocking defect just found can hang any unattended run indefinitely, and (2)
  TrackNetV3's dense pre-pass is genuinely slow at native 4K (2.33 s/frame measured, vs 0.385
  s/frame at 1080p) regardless of GPU, because the bottleneck is per-pixel preprocessing, not
  the network itself — not because the machine lacks a GPU.

## Blockers

- Further real-footage validation of B1 is coupled to B11 (rally/court-view detection fixes on
  both footage types) — chasing a better clip segment without fixing detection first risks
  repeated runs with the same null result. Recommend addressing B11 (or at least a
  quick recalibration of `is_court_view`'s threshold) before another validation attempt.
- **New defect found during the 2026-07-29 validation run (recorded 2026-08-01):** the
  pipeline blocks on an interactive stdin prompt (`"Press Enter/Y to accept auto detection;
  press M/R/Esc for manual annotation."`) even when invoked with `--display false`. It
  silently consumed 4h43m of the run's 17,443s wall-clock time. Any unattended/background
  invocation — directly relevant to B10, the planned background-job redesign — can hang
  indefinitely at this prompt. Not fixed as part of this correction; tracked here so B10
  planning accounts for it.
  - **Root cause narrowed 2026-08-23, and it is worse than "the flag is ignored".**
    `annotate_court` (`badminton_analysis/court/mapper.py:116`) takes **no** display or
    headless parameter at all, opens a `cv2` window, and spins in `while True:
    cv2.waitKey(1)` with no timeout; `system.py:924` calls it unconditionally. So
    `--display false` *structurally cannot* suppress it — there is no code path that would
    consult the flag. Recommend fixing this as a small prerequisite ahead of B11, since B11's
    own validation needs unattended runs and B10's background job cannot exist while any
    stage can block forever on a GUI keypress.

## Interruption: 2026-09-20 match-analysis defect fixes (branch `claude/b11-rally-detection`)

Owner reported three symptoms on the new `Dji 20260919111119 0007 D` run: no playable
analysed video, an empty upper-player heatmap, and no technique data. Diagnosed to three
distinct root causes and fixed on this branch ahead of the B11 tasks.

- **Unplayable video** (`ee2b5fa`). OpenCV could not initialise H.264 (openh264 DLL version
  mismatch) and silently fell back to MPEG-4 Part 2, which no browser decodes. The ffmpeg
  rescue transcode was then killed by a fixed 300 s timeout; measured, that video needs
  ~425 s (20 s encodes in 12.5 s). It left a truncated 848 MB file with no moov atom and
  the job still reported success. Budget now scales with duration, partial output is
  deleted, and the failure is reported in the job, the result payload and the UI.
- **Far player never detected** (`aafdc6e`, `bd25b9d`). `process_frame` passed no `imgsz`,
  so Ultralytics letterboxed 3840x2160 to its 640 default and the far player's 100-155 px
  body became 17-26 px. Fixed with a second pose pass over a far-court crop at
  `imgsz=1280`: measured 8/8 sampled frames against 0/8 for the shipped path and 5/8 for a
  whole-frame 2560 pass at twice the cost. Upper-half candidates only, plus a static
  filter for courtside bystanders. Real-footage check on a 15 s cut: upper present in
  **97.9%** of records (was **0 of 36,732**), lower unchanged at 61.9% (was 65.5%).
- **Perspective contact gate wired into the technique path** (`664df2a`). It existed since
  B11 but `TechniqueAnalysisRunner` still used the fixed 80 px, which is 0.13 m near and
  0.65 m far on this footage.

**Not fixed, and why.** The empty technique output is *not* a contact-gate problem:
measured over the run's 36,732 records the shuttle appears in 4.9% of frames, comes within
80 px of a hand in 6 frames and within the perspective radius in 3, and in neither case
does any such frame also carry the shuttle at `i-1`, `i` and `i+3` that the
direction-change test needs. Contacts are 0 under both radii. The binding constraint is
shuttle detection — B11 §0.7/§0.11/§0.17 reproduced on new footage — whose remaining path
is capture-side (§0.25 option D). Rally segmentation is also still wrong on this run (13
segments, one of them 546.8 s covering 80% of the video); that is what the B11 plan's
Tasks 4-9 address and was deliberately not attempted ad hoc.

The owner's existing run was repaired in place: its video was transcoded to H.264
(verified 40,697 frames and 678.96 s, matching the original) so it plays without
re-analysis. Its *data* still has no upper player — that needs a re-run.

## Next action

Execute the B11 plan (`docs/superpowers/plans/2026-08-23-rally-play-detection-b11.md`) task by
task from Task 1 (headless-safe `annotate_court`, still unfixed on `main`), with a task review
after each and a final whole-branch review. Tasks 6 and 12 run against the owner's footage and
`outputs/b11-labelling/LABELS.md`, both on disk. Task 11's real-footage validation stays
blocked on a genuine broadcast sample. B2-B10 remain separate, unplanned pieces of Sub-project B.

(History: the "write B11's plan" next action of 2026-08-23 is done — the plan merged with #7.)

<!-- ai-continuity:milestones:start -->
<!-- ai-continuity:milestones:end -->
