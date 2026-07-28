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
- **Head commit:** matches base commit as of this record; updated at each milestone.
- **Last milestone:** 2026-07-29 - completion bar drafted (`docs/superpowers/specs/
  2026-07-29-match-stroke-recognition-b-design.md`), owner decisions recorded (background
  job with UI status tracking; both fixed-camera and broadcast footage in scope; strict
  full-rally coverage as the target); the in-flight `player.py` net-line half-classification
  fix landed as the first commit (prerequisite for hitter-by-proximity selection).

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
  (commit `fe31415`, net-line half-classification fix, landed).
- `docs/superpowers/specs/2026-07-29-match-stroke-recognition-b-design.md` (this milestone).

## Verification

- `tests/test_player_half_classification.py tests/test_bst_integration.py`: 11 passed.
- Full suite (`--ignore=tests/test_ai_handoff.py`): 411 passed. Tested on the dirty tree at
  commit `fe31415` on `claude/match-stroke-recognition-b`.

## Blockers

None currently. B1's implementation plan is the next artifact to produce.

## Next action

Write the implementation plan for B1 (both-player capture + hitter-by-proximity selection +
BST person-0/person-1 fill), scoped to validate against the known-working 750-frame Axelsen
clip segment, per the owner's explicit direction to start there rather than the full
background-job/rally-detection scope.

<!-- ai-continuity:milestones:start -->
<!-- ai-continuity:milestones:end -->
