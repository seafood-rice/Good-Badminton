# Workstream: handoff-file-scope

- **workstream_id:** `handoff-file-scope`
- **Objective:** stop `scripts/ai-handoff.ps1` from silently accepting a comma-joined path
  list as one path when invoked via `pwsh -File` (observed 2026-09-25 on the `library-tags`
  claim, whose `scope_paths` holds a single comma-joined string).
- **Scope paths:** `.ai/workstreams/handoff-file-scope.md`, `scripts/ai-handoff.ps1`,
  `tests/test_ai_handoff.py`
- **State:** active
- **Branch:** `claude/handoff-file-scope`
- **Worktree:** linked worktree `.claude/worktrees/youthful-dirac-87c153`
- **Base commit:** `00acb5a3bb71ea80e768d496059eca0b13a25604`
- **Head commit:** `00acb5a3bb71ea80e768d496059eca0b13a25604`
- **Claim id:** `2b4ba6a3-8d2c-44b3-982d-22701e9f5acc`
- **Last milestone:** 2026-09-25T14:19:49Z - Reject comma-joined path elements (pwsh -File) with exit 2; RED/GREEN tests added; Get-Help block fixed; qa-reviewer PASS.

## Acceptance criteria

1. `start`/`takeover` reject a `-Scope` element containing a comma with exit `2`, one JSON
   object, a `validation` error suggesting `-Command` with `@('a','b')`, and no mutation.
2. `update` rejects the same for `-ChangedPath` and `-VerificationDirtyPath`.
3. A failing test that invokes the helper via `pwsh -File` with a comma list exists and
   was watched failing first.
4. The decision is documented in the script's comment-based help.

## Decisions and rationale

- **Reject, do not split.** The Helper Contract says array parameters "accept a normal
  comma-separated array" - i.e. PowerShell builds the array (`-Command` or in-process
  `&`), not the helper. The design's path rules are fail-closed ("Invalid input returns
  exit `2` with no filesystem mutation"; unsafe input is exit `2`; "the helper never
  guesses"). Splitting would silently reinterpret a string the caller may have meant
  literally and would make `-File` behave differently from `-Command` for the same text.
  Rejecting is a stable, explicit exit `2` with a concrete fix. Cost: a repository path
  that literally contains a comma can no longer be claimed or recorded, so committing
  such a file inside a claimed scope blocks `handoff` until it is renamed (QA finding,
  documented in the script help rather than special-cased). No tracked path contains a
  comma today, and the pilot already refuses other legal-but-unsafe forms (wildcards).
- **Check at intake, before `Get-RepositoryContext`.** Same placement as
  `Assert-LeaseHoursInRange`: no Git or filesystem access happens first, so there is no
  mutation. Committed state (`scope_paths`, committed `Changed paths`) is not re-checked,
  so existing claims, including the malformed `library-tags` one, still load.
- **Fixed `Get-Help`.** The comment-based help was never recognized because
  `#Requires` sat directly above `<#`. A blank line fixes it; a test guards it.

## Changed paths

- `.ai/workstreams/handoff-file-scope.md` (created)
- `scripts/ai-handoff.ps1` (`Assert-NoCommaJoinedPath`, intake calls, help, `#Requires` spacing)
- `tests/test_ai_handoff.py` (`run_helper_file`, `-File` comma-list tests, `Get-Help` test)

## Verification

- RED at `HEAD` `00acb5a` plus the new tests: 4 failed, 1 passed (`-k file_invocation`).
  `start` reproduced the bug (exit 0, `scope_paths: [".ai/workstreams/continuity-pilot.md,shared"]`).
  Then 1 failed (`-k get_help`, help block not recognized).
- GREEN on the dirty tree, with the changed paths above:
  - `tests/test_ai_handoff.py`: 293 passed, 3 skipped.
  - Whole repository: 899 passed, 5 skipped.
  - The 3 helper skips are the existing symlink-privilege skips (WinError 1314).
- qa-reviewer: PASS (36 targeted tests); its one latent finding is documented above.

## Blockers

None. The live `library-tags` claim `7071572f-44c2-4925-adb0-8c993dc4a77b` is left
untouched; its owner must redo it via a completed handoff/accept or expired-claim takeover.

## Next action

User reviews branch claude/handoff-file-scope and approves push/PR; library-tags owner re-claims with an array scope.

<!-- ai-continuity:milestones:start -->
- 2026-09-25T14:19:49Z - state: active - Reject comma-joined path elements (pwsh -File) with exit 2; RED/GREEN tests added; Get-Help block fixed; qa-reviewer PASS.
  - Changed paths: `.ai/workstreams/handoff-file-scope.md`, `scripts/ai-handoff.ps1`, `tests/test_ai_handoff.py`
  - Verification: passed - command `PYTHONUTF8=1 ./.venv/Scripts/python.exe -B -m pytest tests/test_ai_handoff.py -q -p no:cacheprovider --basetemp <unique> (293 passed, 3 skipped); whole repo 899 passed, 5 skipped` - commit `00acb5a3bb71ea80e768d496059eca0b13a25604` - dirty paths: `.ai/workstreams/handoff-file-scope.md`, `scripts/ai-handoff.ps1`, `tests/test_ai_handoff.py`
  - Next action: User reviews branch claude/handoff-file-scope and approves push/PR; library-tags owner re-claims with an array scope.
<!-- ai-continuity:milestones:end -->
