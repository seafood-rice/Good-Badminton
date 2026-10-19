# Workstream: doubles-hardening

- **workstream_id:** `doubles-hardening`
- **Objective:** harden doubles match analysis with the minor items its reviews deferred
  ("can wait"), each fixed TDD with a covering test.
- **Scope paths (intended; no helper claim, see Deviation):** this file;
  `badminton_analysis/tracking/{identity,multi}.py`, `badminton_analysis/system.py`,
  `badminton_analysis/visualization/doubles_render.py`, `app.py`, `static/kestrel.js`, the
  doubles spec (6.4, 6.5 amendments), `tests/test_doubles_*.py`,
  `tests/test_progress_heartbeat.py`, `tests/test_app_progress.py`. Not touched:
  `stroke/hit_*.py`, `stroke_recog/`, `shuttle_track/`, `third_party/tracknet/`.
- **State:** handoff - all 7 items done, PR open against `main`, stacked on PR #14
- **Branch:** `claude/doubles-hardening`, created from `origin/claude/doubles-match-analysis`
  (`b80a113`, PR #14 head, not yet merged)
- **Worktree:** `.claude/worktrees/beautiful-bhabha-593349`
- **Base commit:** `b80a113`
- **Claim id:** none (see Deviation)
- **Last milestone:** 2026-10-19 - both Codex review findings on PR #15 fixed (R1, R2);
  full suite re-run.

## Deviation: no continuity claim

`start` failed for the same reason as `doubles-match-analysis`: the scope overlaps the expired
but still blocking claim `4a0e6d57-...` (`learned-hit-detection`). Per the owner's standing
rule (claims are a record, not a gate), work proceeded without one.

## Items and commits

| # | Item | Commit |
|---|---|---|
| 1 | `degraded_teams` median weighted by component frame count (spec 6.4 amended) | `4b86b27` |
| 2 | `_split` seeds the new Kalman with the real frame gaps (fast-mode stride) | `76840da` |
| 3 | Who-hit falls back to YOLO shuttle points when TrackNet did not run (spec 6.5 amended) | `77aef61` |
| 4 | `_doubles_clean_frame` / `_observe_doubles` get separate one-shot log flags | `f85a287` |
| 5 | Heatmap figures closed in `finally`; aborted `write_heatmaps` leaves no PNGs; identity-video labels drawn with PIL + `simhei.ttf` (CJK names) | `8aec250` |
| 6 | `progress.json` `substage` for the doubles stage (identity, heatmaps, video render with a 2 s heartbeat); app message and Kestrel heartbeat during re-encode | `77c6b5a` |
| 7 | Missing tests: hitter window / fallback / close contest, appearance embed and partial terms, collector malformed inputs, render frame alignment, identity coverage and stage-2 triangle | `4f91006` |
| R1 | Codex review: doubles runs also remove a singles run's cached `training_plan.json` (spec 7 amended) | `ad4f5f4` |
| R2 | Codex review: `players.json` records `identity_video` {status, file, reason}; Kestrel links the video only on `ok` (spec 6.6 amended) | `2e56fca` |

Decisions:

- Weighted median: a component weighs its frame count; ties at exactly half average the
  two neighbours, so equal weights reproduce `np.median`.
- Shuttle fallback only when TrackNet did not run; TrackNet gaps are not filled with YOLO
  points (two sources in different quality would mix silently). Evidence stays `shuttle`.
- Labels are rendered once per (text, colour, size) and alpha-blitted per frame: no
  per-frame PIL conversion of 4K frames. Without the font, cv2 is used as before.
- `substage` is written only when set, so singles `progress.json` is unchanged; the
  `visualizing` pct stays 97 (the heartbeat and message show liveness, not a percentage).

## Verification

| Command | Result | Tested commit | Date |
|---|---|---|---|
| `PYTHONUTF8=1 .venv/Scripts/python.exe -B -m pytest -q -p no:cacheprovider --basetemp <unique>` | 1511 passed, 5 skipped (621 s; was 1477 on `b80a113`, +34 new tests). Skips: 3 symlink-privilege, 2 absent weight files | `4f91006` | 2026-10-18 |
| same | 1513 passed, 5 skipped (680 s; +2 tests for the Codex review fixes) | `2e56fca` | 2026-10-19 |

Kestrel's change (heartbeat visible while `reencoding`) is covered by a source test only;
not checked in a browser, since it needs a full doubles run to reach that state.

## Next action

Owner: merge PR #14, then review and merge this PR (rebase onto `main` first if #14 was
squash-merged). The owner-run Dji doubles check in `doubles-match-analysis.md` should
now also note whether the identity video's labels and the Kestrel sub-stage messages look
right on 4K.
