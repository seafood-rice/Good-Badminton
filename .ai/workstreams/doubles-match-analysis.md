# Workstream: doubles-match-analysis

- **workstream_id:** `doubles-match-analysis`
- **Objective:** doubles support for match analysis: 4 match-level player identities,
  per-player movement stats and heatmaps, and who hit each shot with a confidence, per
  `docs/superpowers/specs/2026-10-17-doubles-match-analysis-design.md`.
- **Scope paths (intended; no helper claim, see Deviation):** the spec, plan and this file;
  `badminton_analysis/tracking/`, `badminton_analysis/stroke/hitter.py`,
  `badminton_analysis/system.py`, `badminton_analysis/visualization/`, `main.py`, `app.py`,
  `static/kestrel.js`, `tests/test_doubles_*`. Explicitly NOT touched: the
  `learned-hit-detection` code paths (`badminton_analysis/stroke/hit_*`, `stroke_recog/`,
  `shuttle_track/`, `third_party/tracknet/`, the hit-detector scripts). Two of that
  workstream's guard tests (`tests/test_hit_wiring.py`, `tests/test_tracknet_wiring.py`)
  were updated because `main.py` argument parsing moved into `build_parser()`.
- **State:** handoff - implementation complete, PR open against `main`, owner-run check pending
- **Branch:** `claude/doubles-match-analysis`, created from `origin/main` (`71cee7b`)
- **Worktree:** `D:/Dev/Claude/Projects/good-badminton/Good-badminton-doubles` (linked worktree)
- **Base commit:** `71cee7b`
- **Claim id:** none (see Deviation)
- **Last milestone:** 2026-10-17 - All 9 plan tasks implemented with per-task review, final
  whole-branch review (Opus) plus one 12-item fix wave and a clean re-review; full suite green.

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
deviation here. Follow-ups: the helper's missing close operation (already in PROJECT_STATUS
blockers) and the sync attributes on the primary checkout's `.ai`.

## Acceptance criteria

The spec's section 11 "Done means". Items 1-4 are met on this branch; item 5 (owner-run check
on one Dji doubles clip) is open.

## Decisions and rationale

Spec section 2 (owner decisions D1-D6) plus these rulings made during execution:

- Tracklets are cut whenever two people are within 0.6 m (matched or not) and when a person
  re-emerges from a merged detection; gating happens before the Hungarian assignment, with
  gate growth capped at 2x. Short tracklets are cheap; a swap poisons identity.
- Identity: the team rule (same side => same team) uses only in-rally frames
  (`constraint_frames`, from `rally_list` when rally detection is neither degraded, error,
  none nor courtview), because players cross the net at end changes. Partner cannot-links and
  the same-side triple check use full frames (presence at the same frame = different people).
- Who hit: racket terms from the original design are dropped in v1 (racket points exist per
  side, not per person); every same-side candidate is scored; `hits_named_share` counts only
  shuttle/swing evidence, single-candidate guesses are counted separately.
- The TrackNet pre-pass runs whenever frames are captured (doubles included).
- Commit trailers name the model that authored each commit.

## Changed paths

New: `badminton_analysis/tracking/{appearance,multi,identity,doubles}.py`,
`badminton_analysis/stroke/hitter.py`, `badminton_analysis/visualization/doubles_render.py`,
`tests/test_doubles_*.py`. Modified: `badminton_analysis/system.py`, `main.py`, `app.py`,
`static/kestrel.js`, `tests/test_rally_segmentation.py`, `tests/test_hit_wiring.py`,
`tests/test_tracknet_wiring.py`, the spec, the plan, this file.

## Verification

| Command | Result | Tested commit | Date |
|---|---|---|---|
| `PYTHONUTF8=1 .venv/Scripts/python.exe -B -m pytest -q -p no:cacheprovider --basetemp <unique>` | 1455 passed, 5 skipped (648 s) | `850035e` (before the final fix wave) | 2026-10-17 |
| same | 1477 passed, 5 skipped (659 s): 3 symlink-privilege skips plus 2 weight-file skips (BST, MotionBERT weights are gitignored and absent from this worktree) | `1b3ae1e` | 2026-10-17 |

No real-footage run yet: no doubles clip is in this worktree, and the owner chose visual
validation (D2).

## Next action

Owner: review and merge the PR; then run one Dji doubles clip with Match format = Doubles and
check `detect_<stem>_players.mp4` by eye. Record here: `players.json` status and reason,
`stats` (tracklets total/used/dropped, median confidence, hits total, hits_named_share,
hits_single_candidate), and the distribution of `hitter_confidence` in `hits.json`. Expect
few swing-named hits on footage without a shuttle signal: the 0.6 bar needs a large score gap
(final review, Important 7); retune only from that distribution.
