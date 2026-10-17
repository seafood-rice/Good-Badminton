# Workstream: doubles-match-analysis

- **workstream_id:** `doubles-match-analysis`
- **Objective:** doubles support for match analysis: 4 match-level player identities,
  per-player movement stats and heatmaps, and who hit each shot with a confidence, per
  `docs/superpowers/specs/2026-10-17-doubles-match-analysis-design.md`.
- **Scope paths (intended; no helper claim, see Deviation):** the spec, plan and this file;
  `badminton_analysis/tracking/`, `badminton_analysis/stroke/hitter.py`,
  `badminton_analysis/system.py`, `badminton_analysis/visualization/`, `main.py`, `app.py`,
  `static/kestrel.js`, `tests/test_doubles_*`. Explicitly NOT touched: the
  `learned-hit-detection` paths still in use (`badminton_analysis/stroke/hit_*`,
  `stroke_recog/`, `shuttle_track/`, `third_party/tracknet/`, the hit-detector scripts).
- **State:** active
- **Branch:** `claude/doubles-match-analysis`, created from `origin/main` (`71cee7b`)
- **Worktree:** `D:/Dev/Claude/Projects/good-badminton/Good-badminton-doubles` (linked worktree)
- **Base commit:** `71cee7b`
- **Claim id:** none (see Deviation)
- **Last milestone:** 2026-10-17 - Design approved by the owner; spec written.

## Deviation: no continuity claim

`scripts/ai-handoff.ps1 start` failed: the requested scope overlaps the expired but still
blocking claim `4a0e6d57-d68a-45c1-8fff-ceb5d00c1123` (`learned-hit-detection`, lease expired
2026-10-14), whose scope includes `system.py`, `app.py`, `main.py` and `static/kestrel.js`.
The documented workaround (`takeover` narrowing the expired claim) could not run:

- from the primary checkout: `.ai` there carries cloud-sync placeholder attributes (a reparse
  point), and the helper refuses scopes that cross one;
- from this worktree or a temporary checkout: a takeover must run in the claim's recorded
  worktree and branch.

Owner decision (2026-10-17): proceed in this worktree without a helper claim and log the
deviation here. The `learned-hit-detection` paths still in active use are left untouched.
Follow-ups: the helper's missing close operation (already in PROJECT_STATUS blockers) and
the sync attributes on the primary checkout's `.ai`.

## Acceptance criteria

The spec's section 11 "Done means".

## Decisions and rationale

See the spec section 2 (owner decisions D1-D6).

## Verification

Not run yet.

## Next action

Write the implementation plan, then implement task by task.
