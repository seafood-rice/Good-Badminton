# Good Badminton - Project Status

- **schema_version:** 1
- **last_updated_utc:** 2026-07-18T08:06:51Z

This file is a concise protected-integration snapshot, updated only at integration
milestones. It links to durable per-workstream detail instead of embedding full history.
See `.ai/WORKFLOW.md` for the operating contract and `.superpowers/sdd/progress.md` for
the complete historical ledger.

## Integration state

- **Protected integration branch:** `main`
- **Integration commit:** `c39e4af994e51b8941c1fd4d742625f70f87ff76`
- **Remote state (`main`):** `pushed` - fork `origin`
  (`https://github.com/seafood-rice/Good-Badminton.git`) reports `isFork: true`, parent
  `qwpyyx/Good-Badminton`, default branch `main` at the same commit as source
  `upstream/main` (`https://github.com/qwpyyx/Good-Badminton.git`, fetch-only, push
  disabled).
- **Development branch:** `codex/good-badminton-development`
- **Development branch remote state:** `unpushed` - local `HEAD`
  `432eb7ca02b1e0a0f64aabe61288e1f2b51e0fc3` is 1 commit ahead of
  `origin/codex/good-badminton-development` at
  `565d02bf7bde1c853f163419c6f7294d4b470f43`. Pre-existing dirty/untracked paths in the
  working tree remain local and uncommitted; this snapshot does not claim they are pushed.
- **Preserved disconnected refs:** `archive/pre-fork-good-badminton-development-2026-07-17`
  and `archive/pre-fork-master-2026-07-17`.

## Current objective and phase

- **Objective:** install a portable, tested continuity layer so Claude and Codex can
  safely alternate or work in parallel on Good Badminton, per
  `docs/superpowers/specs/2026-07-16-claude-codex-continuity-design.md`.
- **Phase:** Task 1 of 9 - install the portable shared records and entry points (this
  commit). Tasks 2-8 build the read-only, claim, milestone, handoff, accept, takeover, and
  full-contract layers of `scripts/ai-handoff.ps1`. Task 9 verifies and activates the
  pilot.
- **Acceptance criteria:** the twelve criteria in the design spec's "Acceptance Criteria"
  section, including same-context startup for both tools, committed handoffs, disjoint
  concurrent claims, crash-safe claims, concise status with detailed per-workstream
  history, preserved unrelated user changes, full verification gates, no local
  secrets/settings committed, no direct writes to `main`, enforced branch-prefix rules
  (except the documented bootstrap exception), handoffs blocking until accepted, and a
  verified fork/migration history.

## Workstreams

| ID | State | Branch | Base commit | Head commit | Status file | Next action | Updated (UTC) |
|---|---|---|---|---|---|---|---|
| `continuity-pilot` | `active` | `codex/good-badminton-development` | `432eb7ca02b1e0a0f64aabe61288e1f2b51e0fc3` | `432eb7ca02b1e0a0f64aabe61288e1f2b51e0fc3` | `.ai/workstreams/continuity-pilot.md` | Implement the read-only helper core from Task 2 | 2026-07-18T08:06:51Z |
| `match-stroke-recognition-b` | `planned` | not yet created | `432eb7ca02b1e0a0f64aabe61288e1f2b51e0fc3` | `432eb7ca02b1e0a0f64aabe61288e1f2b51e0fc3` | `.ai/workstreams/match-stroke-recognition-b.md` | Approve the full-match stroke-recognition completion bar, then create its implementation plan | 2026-07-18T08:06:51Z |

## Durable decisions

- The continuity pilot's design is approved:
  `docs/superpowers/specs/2026-07-16-claude-codex-continuity-design.md`.
- The implementation plan is approved:
  `docs/superpowers/plans/2026-07-17-claude-codex-continuity-pilot.md`.
- The pilot applies only to Good Badminton; adapting it for the other 12
  `D:/Dev/Claude/Projects` repositories that contain `CLAUDE.md` is deferred until this
  pilot is proven through Sub-project B (design spec, "Rollout Scope").
- Sub-project B (full-match stroke recognition) is next after the pilot, but its product
  completion bar is a separate, unresolved decision that does not block installing the
  pilot (design spec, "Current State").

## Verification records

| Command | Result | Tested commit / dirty state | UTC timestamp |
|---|---|---|---|
| Migration-worktree pytest run (whole repo) | 355 passed, 1 optional-model test skipped | Migrated `codex/good-badminton-development` tree matching the archived pre-fork development head | 2026-07-17 |
| Later fresh-virtual-environment pytest run (whole repo) | 356 passed | Same migrated tree | 2026-07-17 |
| `PYTHONUTF8=1 ./.venv/Scripts/python.exe -B -m pytest tests/test_ai_handoff.py -q -p no:cacheprovider --basetemp <unique>` | RED: 4 failed, 13 passed (missing `.ai` files; duplicate/wrong-case `AGENTS.md` references) | Dirty tree at `HEAD` `432eb7ca02b1e0a0f64aabe61288e1f2b51e0fc3`, Task 1 changed paths | 2026-07-18T08:06Z |
| `PYTHONUTF8=1 ./.venv/Scripts/python.exe -B -m pytest tests/test_ai_handoff.py -q -p no:cacheprovider --basetemp <unique>` | GREEN: 17 passed | Same dirty tree/commit | 2026-07-18T08:12:05Z |
| `PYTHONUTF8=1 ./.venv/Scripts/python.exe -B -m pytest -q -p no:cacheprovider --basetemp <unique>` (existing full suite plus this file) | 373 passed | Same dirty tree/commit | 2026-07-18T08:12Z |

Full Task 1 evidence, including the exact RED/GREEN command output, is recorded in
`.ai/workstreams/continuity-pilot.md` and the Task 1 implementation report.

## Blockers and deferred work

- Sub-project B's product completion bar is undecided; `match-stroke-recognition-b` stays
  `planned` until it is approved.
- Pre-existing deleted assets (`assets/demo.gif`, `assets/demo_en.gif`,
  `assets/label_court_example.png`, `assets/match_heatmap.png`,
  `assets/match_heatmap_en.png`, `assets/match_scatter.png`, `assets/match_scatter_en.png`)
  and other pre-existing untracked/local files remain intentionally uncommitted; the
  continuity pilot must never sweep them into a commit.

## Immediate integration next actions

1. Complete Tasks 2-8 of the implementation plan to build and fully test
   `scripts/ai-handoff.ps1`.
2. Run Task 9's full verification gates and two-worktree smoke test, then activate the
   pilot with a real scoped claim and a committed alternating handoff/accept cycle.
3. Prepare the reviewed branch for the user to publish to
   `origin/codex/good-badminton-development`; agents never push without explicit approval.

## Links

- Design spec: `docs/superpowers/specs/2026-07-16-claude-codex-continuity-design.md`
- Implementation plan: `docs/superpowers/plans/2026-07-17-claude-codex-continuity-pilot.md`
- Historical ledger: `.superpowers/sdd/progress.md`
