# Workstream: continuity-pilot

- **workstream_id:** `continuity-pilot`
- **Objective:** install and verify the portable Claude-Codex continuity layer described in
  `docs/superpowers/specs/2026-07-16-claude-codex-continuity-design.md` and
  `docs/superpowers/plans/2026-07-17-claude-codex-continuity-pilot.md`.
- **Scope paths:** `.ai/`, `scripts/ai-handoff.ps1`, `tests/test_ai_handoff.py`
- **State:** active
- **Branch:** `codex/good-badminton-development`
- **Worktree:** the primary local checkout (no linked worktree yet)
- **Base commit:** `432eb7ca02b1e0a0f64aabe61288e1f2b51e0fc3`
- **Head commit:** `c5547bcd2e960931331c2644642696ea4aab07a9`
- **Last milestone:** 2026-07-21T04:34:48Z - completed and reviewed Tasks 1-8 (the full
  status/start/update/handoff/accept/takeover helper and failure matrix) and resolved the
  Codex whole-branch review's Critical and Important findings.

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
  - RED confirmed 2026-07-18T08:06:51Z: 4 failed (missing `.ai` files, duplicate/wrong-case
    `AGENTS.md` references), 13 passed (portable-team-file and staging-audit tests).
  - GREEN confirmed 2026-07-18T08:12:05Z, dirty tree at `HEAD`
    `432eb7ca02b1e0a0f64aabe61288e1f2b51e0fc3` (Task 1 changed paths listed above): 17
    passed.
- `PYTHONUTF8=1 ./.venv/Scripts/python.exe -B -m pytest -q -p no:cacheprovider --basetemp <unique>`
  (existing full suite plus this file): 373 passed, same dirty tree/commit, 2026-07-18T08:12:05Z.
- Task 9 gate, tested commit `c5547bcd2e960931331c2644642696ea4aab07a9`, 2026-07-21T04:34:48Z:
  - `PYTHONUTF8=1 ./.venv/Scripts/python.exe -B -m pytest tests/test_ai_handoff.py -q -p no:cacheprovider --basetemp <unique>`: 290 passed.
  - `PYTHONUTF8=1 ./.venv/Scripts/python.exe -B -m pytest -q -p no:cacheprovider --basetemp <unique>` (whole repo): 646 passed.

## Blockers

None.

## Next action

Run the committed Codex-Claude alternating handoff/accept cycle to activate the pilot, then
prepare the reviewed branch for publication.

<!-- ai-continuity:milestones:start -->
<!-- ai-continuity:milestones:end -->
