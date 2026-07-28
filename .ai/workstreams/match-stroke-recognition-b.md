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
- **Not yet run:** the plan's controller-run real-footage validation step (reconstructed
  ~25s/752-frame clip from the full Axelsen match, since the original 750-frame validation
  clip referenced by the BST/TrackNetV3 specs no longer exists on disk and its extraction
  timestamp was never recorded). This is the next action.

## Blockers

None currently.

## Next action

Run B1's controller-run validation (plan's "Validation" section): analyze the reconstructed
clip through the real pipeline with real weights, inspect `strokes.json` for contact count,
both-sided hitter attribution, and a qualitative alternation check, then record an honest
outcome note (mirroring the BST/TrackNetV3 spec convention) before this workstream moves on
to B2-B11 (segment-scoped dense tracking, the background-job redesign, rally/play-detection
fixes on both footage types, fps/resolution normalization — all separate, not-yet-planned
pieces of the broader Sub-project B effort per the completion-bar doc).

<!-- ai-continuity:milestones:start -->
<!-- ai-continuity:milestones:end -->
