# Good Badminton - Project Status

- **schema_version:** 1
- **last_updated_utc:** 2026-09-29T13:25:01Z

This file is a concise protected-integration snapshot, updated only at integration
milestones. It links to durable per-workstream detail instead of embedding full history.
See `.ai/WORKFLOW.md` for the operating contract and `.superpowers/sdd/progress.md` for
the complete historical ledger.

## Integration state

- **Protected integration branch:** `main`
- **Integration commit:** `0e7f43827f77b3e66dc8d332a2aca10e22887a2c` (PR #9, 2026-09-29)
- **Remote state (`main`):** `pushed` - fork `origin`
  (`https://github.com/seafood-rice/Good-Badminton.git`, `isFork: true`, parent
  `qwpyyx/Good-Badminton`, default branch `main`). PRs are opened against the fork's `main`
  and squash-merged by the owner. `upstream/main`
  (`https://github.com/qwpyyx/Good-Badminton.git`, fetch-only, push disabled) is still at
  `c39e4af994e51b8941c1fd4d742625f70f87ff76`; nothing has been proposed upstream.
- **Preserved disconnected refs:** `archive/pre-fork-good-badminton-development-2026-07-17`
  and `archive/pre-fork-master-2026-07-17`.

## Merged since the last snapshot

| PR | Merge commit | Date | What landed |
|---|---|---|---|
| #1 | `7714e94` | 2026-08-07 | Optional MotionBERT 3D pose-lifting stage (also carried the continuity layer onto `main`) |
| #2, #3 | `7a81e7e` | 2026-08-07 | Sub-project B step 1 (B1): both-player capture + hitter selection |
| #4 | `1d9f3cd` | 2026-08-07 | B11 groundwork: shuttle-detector investigation (negative result), perspective scale, contact gate, capture-quality gate |
| #5 | `9fe1b36` | 2026-08-08 | Thumbnails for names with spaces; usable `--suggest` |
| #6 | `d4c69d4` | 2026-08-23 | Posture rep detection for smash, drop shot and serve |
| #7 | `00acb5a` | 2026-09-25 | Four analysis defects (unplayable video, VideoWriter hang, missing far-court player, perspective contact gate) + B11 decision record and implementation plan |
| #8 | `cf0eba8` | 2026-09-29 | `ai-handoff.ps1` rejects comma-joined path elements from `pwsh -File` |
| #9 | `0e7f438` | 2026-09-29 | Video library tags (multi-tag AND filter, rename/merge/delete with undo, Type filter absorbed) |

## Current objective and phase

- **Objective:** Sub-project B - full-match stroke recognition - per
  `docs/superpowers/specs/2026-07-29-match-stroke-recognition-b-design.md`.
- **Phase:** B1 is merged. The next milestone is **B11 (rally / play detection)**: a
  self-calibrating court-view gate and a post-loop rally segmenter that reports which signal
  it used and how far to trust it. Its design
  (`docs/superpowers/specs/2026-07-30-rally-play-detection-b11-design.md`) and 12-task plan
  (`docs/superpowers/plans/2026-08-23-rally-play-detection-b11.md`) are approved and merged;
  no B11 implementation task has started. B2-B10 remain unplanned.

## Workstreams

| ID | State | Branch | Head commit | Status file | Next action | Updated (UTC) |
|---|---|---|---|---|---|---|
| `match-stroke-recognition-b` | `active` - B11 next | a new `claude/` branch for B11 (to be created from `main`) | `0e7f438` (`main`) | `.ai/workstreams/match-stroke-recognition-b.md` | Execute the B11 plan from Task 1 (headless-safe `annotate_court`). The status file's own Next action predates the plan and will be corrected at B11 start | 2026-09-29 |
| `project-status-refresh` | `active` | `claude/project-status-refresh` | - | `.ai/workstreams/project-status-refresh.md` | Owner merges this snapshot refresh | 2026-09-29 |
| `library-tags` | merged (#9) | `claude/library-tags` | `bef6f6d` | `.ai/workstreams/library-tags.md` | None - done. Claim narrowed to its own status file (see Blockers) | 2026-09-29 |
| `handoff-file-scope` | merged (#8) | `claude/handoff-file-scope` | - | `.ai/workstreams/handoff-file-scope.md` | None - done | 2026-09-29 |
| `motionbert-3d-lifting` | merged (#1) | `claude/motionbert-3d-lifting` | - | `.ai/workstreams/motionbert-3d-lifting.md` | None for the lifting stage. The AQA scorer follow-up is a separate, unplanned sub-project gated on labelled data | 2026-08-07 |
| `continuity-pilot` | integrated (via #1) | `codex/good-badminton-development` | - | `.ai/workstreams/continuity-pilot.md` | None - the helper is in daily use | 2026-07-21 |

Status files for merged workstreams still show their last in-flight `State`/`Next action`;
this table is the authoritative integration record for them (see Blockers).

## Durable decisions

- The continuity layer (`scripts/ai-handoff.ps1`, `.ai/`) is the operating contract for all
  agent work here: `docs/superpowers/specs/2026-07-16-claude-codex-continuity-design.md`.
  Adapting it to the other local `Projects` repositories is still deferred.
- Sub-project B's completion bar is approved (B spec section 2, "Done means"); B1 stands on
  its synthetic/unit/end-to-end evidence plus an honestly reported upstream-blocked
  real-footage attempt, and B11 is the next delivery (owner decision, 2026-08-23).
- B11 owner decisions are recorded in the B11 spec section 12a (2026-08-23): **A** - true
  multi-camera broadcast stays in scope, so a shot-boundary component is required, validated
  hermetically only until a genuine broadcast sample exists; **B** - resolved by measurement:
  no usable shuttle signal on the owner's fixed-camera footage, so the swing signal is
  primary there; **C** - no "zero rallies": with no usable signal, emit degraded coarse
  windows and withhold stroke labels on them; **D** - completion-bar R10 amended.
- Library tags live in `data/library_tags.json` (gitignored), never in `outputs/<stem>/`;
  every store read-modify-write is serialised by `_TAGS_LOCK` in `app.py`.

## Verification records

| Command | Result | Tested commit / dirty state | UTC timestamp |
|---|---|---|---|
| `PYTHONUTF8=1 ./.venv/Scripts/python.exe -B -m pytest -q -p no:cacheprovider --basetemp <unique>` | 976 passed, 3 skipped | `d016cad` (library-tags branch head before squash; tree identical to #9's code) | 2026-09-25 |
| `PYTHONUTF8=1 ./.venv/Scripts/python.exe -B -m pytest -q -p no:cacheprovider --basetemp <unique>` | 982 passed, 3 skipped (589 s) | `0e7f438` (`main`); only uncommitted changes were this refresh's two `.ai/` docs files, plus untracked `BirdEye Prototype.html` | 2026-09-29T13:25:01Z |

The 3 skips are `tests/test_ai_handoff.py` symlink tests, which need the Windows symlink
privilege (Developer Mode); they are environment-only. Earlier records (continuity pilot,
2026-07-17 to 2026-07-21) are in `.ai/workstreams/continuity-pilot.md` and Git history.

## Blockers and deferred work

- **The continuity helper has no close operation.** `start` treats every `active` or
  `handoff-ready` claim as live regardless of lease expiry, so a merged workstream's expired
  claim keeps blocking overlapping paths. Workaround in use: `takeover` the expired claim with
  a scope of only its own status file (done for `library-tags`, claim `2423a354`). A
  follow-up to add a proper close is proposed separately.
- B11 Task 11 (shot-boundary detection) can ship with hermetic tests only; real-footage
  validation needs a genuine broadcast sample, which does not exist yet.
- B11 Tasks 6 and 12 need the owner's match footage and the human labels at
  `outputs/b11-labelling/LABELS.md` on disk.
- Deferred minors from library tags: purity tests for `rename`/`delete`/`forget`; directory
  fsync after `os.replace` (not possible on Windows).
- `BirdEye Prototype.html` at the repo root is a pre-existing untracked file; never commit it.

## Immediate integration next actions

1. Owner merges this snapshot refresh.
2. Start B11 on a new `claude/` branch from `main`: claim its scope, correct the
   `match-stroke-recognition-b` status file, then execute the plan task by task with review
   gates, beginning with Task 1.

## Links

- Sub-project B spec: `docs/superpowers/specs/2026-07-29-match-stroke-recognition-b-design.md`
- B11 spec: `docs/superpowers/specs/2026-07-30-rally-play-detection-b11-design.md`
- B11 plan: `docs/superpowers/plans/2026-08-23-rally-play-detection-b11.md`
- Historical ledger: `.superpowers/sdd/progress.md`
