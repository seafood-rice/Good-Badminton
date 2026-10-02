# Good Badminton - Project Status

- **schema_version:** 1
- **last_updated_utc:** 2026-10-02T14:49:14Z

This file is a concise protected-integration snapshot, updated only at integration
milestones. It links to durable per-workstream detail instead of embedding full history.
See `.ai/WORKFLOW.md` for the operating contract and `.superpowers/sdd/progress.md` for
the complete historical ledger.

## Integration state

- **Protected integration branch:** `main`
- **Integration commit:** `753ce252ce19449e85d327ea8047c60f00b8d078` (PR #11, 2026-10-02)
- **Remote state (`main`):** `pushed` - fork `origin`
  (`https://github.com/seafood-rice/Good-Badminton.git`, `isFork: true`, parent
  `qwpyyx/Good-Badminton`, default branch `main`). PRs are opened against the fork's `main`
  and squash-merged by the owner. `upstream/main`
  (`https://github.com/qwpyyx/Good-Badminton.git`, fetch-only, push disabled) is still at
  `c39e4af994e51b8941c1fd4d742625f70f87ff76`; nothing has been proposed upstream.
- **Preserved disconnected refs:** `archive/pre-fork-good-badminton-development-2026-07-17`
  and `archive/pre-fork-master-2026-07-17`.

## Merged since the last snapshot

The previous snapshot (#10) covered PRs #1-#9; see Git history for those.

| PR | Merge commit | Date | What landed |
|---|---|---|---|
| #10 | `b4ee267` | 2026-09-29 | Integration snapshot refresh after PRs #1-#9 |
| #11 | `753ce25` | 2026-10-02 | B11: self-calibrating court-view gate, post-loop rally segmenter (shuttle / swing / degraded), signal-aware rally reporting in the Kestrel UI and API, headless-safe `annotate_court`, standalone shot-boundary detector |

## Current objective and phase

- **Objective:** Sub-project B - full-match stroke recognition - per
  `docs/superpowers/specs/2026-07-29-match-stroke-recognition-b-design.md`.
- **Phase:** B1 and **B11 (rally / play detection) are merged.** All 12 B11 plan tasks plus a
  court-view gate fix shipped; spec section 15 "As built" records every deviation from the
  spec and plan. Measured: the gate admits 99.6% of the fixed-camera Dji `0010` and 97.8% of
  the Axelsen broadcast (was 0.66%); the swing signal scores F1 0.653 against the owner's
  labels on a real end-to-end run (10/11 rallies, start lag +2.22 s).
- **Next:** the owner-run Axelsen re-run that validates the shuttle path (see Blockers), then
  plan B2-B10, which are not yet planned.

## Workstreams

| ID | State | Branch | Head commit | Status file | Next action | Updated (UTC) |
|---|---|---|---|---|---|---|
| `match-stroke-recognition-b` | merged (#11) - B11 done; B2-B10 unplanned | `claude/match-stroke-recognition-b` | `1628b56` | `.ai/workstreams/match-stroke-recognition-b.md` | Owner-run Axelsen re-run; then plan B2-B10. Its claim is narrowed to its own status file once the lease expires (see Blockers) | 2026-10-02 |
| `project-status-refresh` | `active` | `claude/project-status-refresh` | - | `.ai/workstreams/project-status-refresh.md` | Owner merges this snapshot refresh | 2026-10-02 |
| `library-tags` | merged (#9) | `claude/library-tags` | `bef6f6d` | `.ai/workstreams/library-tags.md` | None - done. Claim narrowed to its own status file | 2026-09-29 |
| `handoff-file-scope` | merged (#8) | `claude/handoff-file-scope` | - | `.ai/workstreams/handoff-file-scope.md` | None - done | 2026-09-29 |
| `motionbert-3d-lifting` | merged (#1) | `claude/motionbert-3d-lifting` | - | `.ai/workstreams/motionbert-3d-lifting.md` | None for the lifting stage. The AQA scorer follow-up is a separate, unplanned sub-project gated on labelled data | 2026-08-07 |
| `continuity-pilot` | integrated (via #1) | `codex/good-badminton-development` | - | `.ai/workstreams/continuity-pilot.md` | None - the helper is in daily use | 2026-07-21 |

Status files for merged workstreams still show their last in-flight `State`/`Next action`;
this table is the authoritative integration record for them (see Blockers). The local
branches of merged workstreams have been deleted; their remote branches may remain.

## Durable decisions

- The continuity layer (`scripts/ai-handoff.ps1`, `.ai/`) is the operating contract for all
  agent work here: `docs/superpowers/specs/2026-07-16-claude-codex-continuity-design.md`.
  Adapting it to the other local `Projects` repositories is still deferred.
- Sub-project B's completion bar is approved (B spec section 2, "Done means"); B1 stands on
  its synthetic/unit/end-to-end evidence plus an honestly reported upstream-blocked
  real-footage attempt (owner decision, 2026-08-23).
- B11 owner decisions are recorded in the B11 spec section 12a (2026-08-23): **A** - true
  multi-camera broadcast stays in scope, so a shot-boundary component is required, validated
  hermetically only until a genuine broadcast sample exists; **B** - no usable shuttle signal
  on the owner's fixed-camera footage, so the swing signal is primary there; **C** - no
  "zero rallies": with no usable signal, emit degraded coarse windows and withhold stroke
  labels on them; **D** - completion-bar R10 amended.
- B11 as built: the court-view gate threshold is calibrated per video (median - 4 MAD of a
  shift-tolerant 480 px score, never stricter than 0.75); rally counts are only claimed
  when the signal supports them, and a segmenter failure is reported as `signal: "error"`,
  not as a degraded result. Constructor pins (`court_view_threshold`,
  `rally_signal="courtview"`) reproduce the pre-B11 behaviour exactly.
- Library tags live in `data/library_tags.json` (gitignored), never in `outputs/<stem>/`;
  every store read-modify-write is serialised by `_TAGS_LOCK` in `app.py`.

## Verification records

| Command | Result | Tested commit / dirty state | UTC timestamp |
|---|---|---|---|
| `PYTHONUTF8=1 ./.venv/Scripts/python.exe -B -m pytest -q -p no:cacheprovider --basetemp <unique>` | 1246 passed, 3 skipped (588 s) | `1628b56` (B11 branch; `753ce25`'s tree differs only by docs) | 2026-10-02 |
| `PYTHONUTF8=1 ./.venv/Scripts/python.exe -B -m pytest -q -p no:cacheprovider --basetemp <unique>` | 1246 passed, 3 skipped (600 s) | `753ce25` tree (`main`); only uncommitted changes were this refresh's two `.ai/` docs files, plus untracked `BirdEye Prototype.html` | 2026-10-02T14:49:14Z |

The 3 skips are `tests/test_ai_handoff.py` symlink tests, which need the Windows symlink
privilege (Developer Mode); they are environment-only. Earlier records are in the
workstream files and Git history.

## Blockers and deferred work

- **The continuity helper has no close operation.** `start` treats every `active` or
  `handoff-ready` claim as live regardless of lease expiry, so a merged workstream's expired
  claim keeps blocking overlapping paths. Workaround in use: `takeover` the expired claim with
  a scope of only its own status file (done for `library-tags`). The B11 claim
  `fc55462f-ff74-4b60-a1ed-0a4dc06856be` still covers `app.py`, `system.py`,
  `static/kestrel.js` and the B11 modules; its lease expires 2026-10-02T22:07:17Z, after
  which it is narrowed the same way. A proper close operation is a proposed follow-up.
- **B11's shuttle segmentation path is unvalidated on footage** (the UI says so). Validating
  it needs a full Axelsen re-run with the new gate, which is owner-run: long and
  memory-heavy (the gate admits about 98% of 64k frames; the Dji run peaked at about 2.4 GB).
- `stroke/shot_boundary.py` is standalone and not wired into the pipeline; real-footage
  validation needs a genuine broadcast sample with hard cuts, which does not exist yet.
- B11 follow-ups: CLI flags in `main.py` for the gate and signal pins (constructor-only
  today); the legacy `web_ui.html` disables its clip button for swing runs; clip padding is
  1.5 s against the recommended 2 s or more.
- Deferred minors from library tags: purity tests for `rename`/`delete`/`forget`; directory
  fsync after `os.replace` (not possible on Windows).
- `BirdEye Prototype.html` at the repo root is a pre-existing untracked file; never commit it.

## Immediate integration next actions

1. Owner merges this snapshot refresh.
2. After the B11 claim's lease expires, narrow it to
   `.ai/workstreams/match-stroke-recognition-b.md` with `takeover`.
3. Owner runs the full Axelsen re-run to validate the shuttle path.
4. Plan B2-B10 on a new `claude/` branch from `main`.

## Links

- Sub-project B spec: `docs/superpowers/specs/2026-07-29-match-stroke-recognition-b-design.md`
- B11 spec (section 15 "As built"): `docs/superpowers/specs/2026-07-30-rally-play-detection-b11-design.md`
- B11 plan: `docs/superpowers/plans/2026-08-23-rally-play-detection-b11.md`
- Historical ledger: `.superpowers/sdd/progress.md`
