# Workstream: library-tags

- **workstream_id:** `library-tags`
- **Objective:** let the owner put multiple free-form tags on a video, filter the library by
  several tags at once (AND), and rename or delete a tag library-wide, folding the existing
  Match/Drill Type control into the same mechanism, per
  `docs/superpowers/specs/2026-09-22-library-tags-design.md`.
- **Scope paths:** `.ai/workstreams/library-tags.md`, `badminton_analysis/library`,
  `tests/test_library_tags.py`, `tests/test_app_tags.py`, `app.py`, `static/kestrel.js`,
  `static/kestrel.css`, `docs/superpowers/plans/2026-09-22-library-tags.md`,
  `docs/superpowers/specs/2026-09-22-library-tags-design.md`,
  `docs/superpowers/specs/2026-09-22-library-tags-wireframe.html`
- **State:** active
- **Branch:** `claude/library-tags`
- **Worktree:** the primary local checkout (no linked worktree)
- **Base commit:** `d4c69d4b3ff6bb7d12e8ee5bd52b7888d6d34ffc` (`origin/main` when the branch was cut)
- **Head commit:** `8d9cbe4a50523044de4b3cbc03372cdabe2d1de6`
- **Claim id:** `7071572f-44c2-4925-adb0-8c993dc4a77b`
- **Last milestone:** 2026-09-25 - Task 1 (tag store) implemented TDD, task-reviewed (spec compliant, approved), committed `8d9cbe4`.

## Acceptance criteria

See the design spec's acceptance criteria and the per-task checklists in
`docs/superpowers/plans/2026-09-22-library-tags.md` (nine tasks).

## Decisions and rationale

- **Tags live in `data/library_tags.json`, not `outputs/<stem>/`.** `/api/delete` with scope
  "all" removes `outputs/<stem>` whole; clearing analysis results must not clear labels.
- **The store writes atomically (temp file + `os.replace`)** rather than reusing
  `write_json`, which writes in place; one file holds every tag.
- **`match`/`drill` are derived system tags, never stored, and not mutually exclusive.**
- **Task 1 goes ahead before PR #7 merges.** It touches only new files. Tasks 2-9 wait for
  #7 to land and this branch to be rebased onto the new `main` (squash-merge), because #7
  shifts the `app.py` regions Tasks 2-5 edit.

## Changed paths

- `.ai/workstreams/library-tags.md` (created)
- `badminton_analysis/library/__init__.py` (created, Task 1)
- `badminton_analysis/library/tags.py` (created, Task 1)
- `tests/test_library_tags.py` (created, Task 1)

## Verification

- Task 1, `PYTHONUTF8=1 ./.venv/Scripts/python.exe -B -m pytest tests/test_library_tags.py -q -p no:cacheprovider --basetemp <unique>`:
  RED `No module named 'badminton_analysis.library'`; GREEN 31 passed (28 functions, one
  parametrised x4). Tested commit `8d9cbe4`, 2026-09-25.
- Full suite, same code: 869 passed, 3 skipped (symlink tests in `tests/test_ai_handoff.py`,
  WinError 1314 - symlink privilege not held on this host). 2026-09-25.
- Task review (spec + quality): spec compliant, approved; two Minor findings deferred to the
  final whole-branch review (purity tests for rename/delete/forget; directory fsync after
  `os.replace`).

## Blockers

- Tasks 2-9 are blocked on PR #7 (`claude/b11-rally-detection`) merging to `main`.
- **Claim `7071572f-44c2-4925-adb0-8c993dc4a77b` has a malformed scope.** It was started with
  `pwsh -File scripts/ai-handoff.ps1 start -Scope a,b,...`; under `-File` the comma list
  arrives as ONE string, and the helper recorded a single bogus scope path containing commas.
  The claim therefore protects no real path, and `update` refuses to run because its scope does
  not contain this file. `accept` keeps a predecessor's scope, so the fix is `takeover` once the
  lease expires (`2026-09-25T21:34:18Z`), passing the scope as a real array via
  `pwsh -Command "& ./scripts/ai-handoff.ps1 takeover ... -Scope @('...','...')"`.
  The Task 1 milestone was therefore recorded by hand here, not through `update`.

## Next action

After 2026-09-25T21:34:18Z, `takeover` claim `7071572f-44c2-4925-adb0-8c993dc4a77b` with the correct array scope (see Blockers). Then wait for PR #7 to merge, rebase this branch onto the new `main`, re-grep the `app.py` anchors, and run Task 2.

<!-- ai-continuity:milestones:start -->
<!-- ai-continuity:milestones:end -->
