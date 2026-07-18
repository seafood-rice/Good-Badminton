# Workstream: continuity-pilot

- **workstream_id:** `continuity-pilot`
- **Objective:** install and verify the portable Claude-Codex continuity layer described in
  `docs/superpowers/specs/2026-07-16-claude-codex-continuity-design.md` and
  `docs/superpowers/plans/2026-07-17-claude-codex-continuity-pilot.md`.
- **Scope paths:** `.ai/`, `scripts/ai-handoff.ps1`, `tests/test_ai_handoff.py`
- **State:** active
- **Branch:** `codex/good-badminton-development`
- **Worktree:** `D:/Dev/Claude/Projects/good-badminton/Good-badminton` (primary checkout;
  no linked worktree yet)
- **Base commit:** `432eb7ca02b1e0a0f64aabe61288e1f2b51e0fc3`
- **Head commit:** `432eb7ca02b1e0a0f64aabe61288e1f2b51e0fc3`
- **Last milestone:** 2026-07-18T08:06:51Z - installed the shared entry points,
  `.ai/WORKFLOW.md`, this record, and the Task 1 portability tests.

## Acceptance criteria

See the design spec's "Acceptance Criteria" section (all twelve items) and the
implementation plan's per-task checklists in
`docs/superpowers/plans/2026-07-17-claude-codex-continuity-pilot.md`.

## Decisions and rationale

- Reuse the existing `codex/good-badminton-development` branch as the documented bootstrap
  exception for this workstream only (design spec, "Branch validation"); no other
  workstream may reuse it.
- Normalize `AGENTS.md` to the exact four shared includes and fix the `.Codex` case defect
  so both tools resolve the same shared documents (Task 1, Step 4).

## Changed paths

- `.ai/WORKFLOW.md` (created)
- `.ai/PROJECT_STATUS.md` (created)
- `.ai/workstreams/continuity-pilot.md` (created)
- `.ai/workstreams/match-stroke-recognition-b.md` (created)
- `tests/test_ai_handoff.py` (created)
- `AGENTS.md` (modified)
- `CLAUDE.md` (modified)
- `RTK.md`, `.claude/active-team.md`, `.claude/agents/architect.md`,
  `.claude/agents/builder.md`, `.claude/agents/qa-reviewer.md`,
  `.claude/agents/scope-planner.md`, `.claude/agents/shipper.md`, `.codex/active-team.md`,
  `.codex/agents/architect.toml`, `.codex/agents/builder.toml`,
  `.codex/agents/qa-reviewer.toml`, `.codex/agents/scope-planner.toml`,
  `.codex/agents/shipper.toml` (tracked unchanged after portability review)

## Verification

- `PYTHONUTF8=1 ./.venv/Scripts/python.exe -B -m pytest tests/test_ai_handoff.py -q -p no:cacheprovider --basetemp <unique>`
  - RED confirmed 2026-07-18T08:06Z: 4 failed (missing `.ai` files, duplicate/wrong-case
    `AGENTS.md` references), 13 passed (portable-team-file and staging-audit tests).
  - GREEN confirmed 2026-07-18T08:12:05Z, dirty tree at `HEAD`
    `432eb7ca02b1e0a0f64aabe61288e1f2b51e0fc3` (Task 1 changed paths listed above): 17
    passed.
- `PYTHONUTF8=1 ./.venv/Scripts/python.exe -B -m pytest -q -p no:cacheprovider --basetemp <unique>`
  (existing full suite plus this file): 373 passed, same dirty tree/commit, 2026-07-18T08:12Z.

## Blockers

None.

## Next action

Implement the read-only helper core from Task 2.

<!-- ai-continuity:milestones:start -->
<!-- ai-continuity:milestones:end -->
