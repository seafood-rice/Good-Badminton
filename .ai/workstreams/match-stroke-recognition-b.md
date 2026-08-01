# Workstream: match-stroke-recognition-b

- **workstream_id:** `match-stroke-recognition-b`
- **Objective:** Sub-project B - full-match stroke recognition, extending the completed
  Sub-project A per-rally analysis, per the approved completion bar.
- **Scope paths:** `badminton_analysis/tracking/player.py`, `badminton_analysis/system.py`,
  `badminton_analysis/stroke/events.py`, `badminton_analysis/stroke_recog/`,
  `badminton_analysis/shuttle_track/`, `third_party/tracknet/`, `app.py`, `static/kestrel.js`,
  `tests/test_player_half_classification.py` and related test files, plus this workstream
  file and its design/plan docs.
- **State:** active
- **Branch:** `claude/match-stroke-recognition-b`
- **Worktree:** primary checkout (no separate worktree)
- **Base commit:** `fe31415` (first commit on this branch: the `player.py` net-line fix) -
  branched from `claude/motionbert-3d-lifting`'s tip rather than `main`, since
  `main` is far behind and the MotionBERT branch's PR (seafood-rice/Good-Badminton#1) is
  expected to land first; this branch will likely need a rebase once that merges.
- **Head commit:** `1ac402b` (final-review fix pass, all 7 B1 tasks + prerequisite fix landed).
- **Last milestone:** 2026-07-29 - **B1 (both-player capture + hitter selection) complete.**
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

## Next action

Owner decision needed: (a) accept B1 as done with synthetic/unit/end-to-end evidence plus an
honestly-reported, upstream-blocked real-footage attempt, and move planning on to B11
(rally/play detection) next since it's now confirmed as the actual gate on any further
real-footage validation; or (b) spend another validation attempt on a different clip
segment first — note the real analysis cost per attempt is on the order of minutes, not
hours, once the stdin-blocking defect above is worked around (see the corrected Verification
data point). Either way, B2-B10 (segment-scoped dense tracking, the background-job redesign,
fps/resolution normalization) remain separate, not-yet-planned pieces of the broader Sub-project
B effort per the completion-bar doc.

<!-- ai-continuity:milestones:start -->
<!-- ai-continuity:milestones:end -->
