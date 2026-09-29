# Workstream: library-tags

- **workstream_id:** `library-tags`
- **Objective:** let the owner put multiple free-form tags on a video, filter the library by
  several tags at once (AND), and rename or delete a tag library-wide, folding the existing
  Match/Drill Type control into the same mechanism, per
  `docs/superpowers/specs/2026-09-22-library-tags-design.md`.
- **Scope paths:** `.ai/workstreams/library-tags.md`, `badminton_analysis/library`,
  `tests/test_library_tags.py`, `tests/test_app_tags.py`, `tests/conftest.py`, `app.py`,
  `static/kestrel.js`, `static/kestrel.css`, `docs/superpowers/plans/2026-09-22-library-tags.md`,
  `docs/superpowers/specs/2026-09-22-library-tags-design.md`,
  `docs/superpowers/specs/2026-09-22-library-tags-wireframe.html`
- **State:** active
- **Branch:** `claude/library-tags`
- **Worktree:** the primary local checkout (no linked worktree)
- **Base commit:** `00acb5a3bb71ea80e768d496059eca0b13a25604` (`origin/main` after PR #7; the branch was rebased onto it)
- **Head commit:** `7103f3a1d8eca6b133fe1d733f9834cec2fee9a7`
- **Claim id:** `346547d5-0b6a-4d37-a93e-b99fae9f7ac3` (took over expired `7071572f-44c2-4925-adb0-8c993dc4a77b`)
- **Pull request:** https://github.com/seafood-rice/Good-Badminton/pull/9 (open, against `main`)
- **Last milestone:** 2026-09-28T14:38:53Z - Branch pushed and PR #9 opened against main; malformed claim replaced by takeover with the correct array scope.

## Acceptance criteria

See the design spec's acceptance criteria and the per-task checklists in
`docs/superpowers/plans/2026-09-22-library-tags.md` (nine tasks). All nine are done.

## Decisions and rationale

- **Tags live in `data/library_tags.json`, not `outputs/<stem>/`.** `/api/delete` with scope
  "all" removes `outputs/<stem>` whole; clearing analysis results must not clear labels.
- **The store writes atomically (temp file + `os.replace`, retried on Windows `PermissionError`)**
  rather than reusing `write_json`, which writes in place; one file holds every tag.
- **`match`/`drill` are derived system tags, never stored, and not mutually exclusive.**
- **One module-level `_TAGS_LOCK` serialises every store read-modify-write.** Browser
  verification found a lost-update race (concurrent PUTs clobbered each other, breaking Undo);
  the spec's original "accepted, not locked" position was amended (spec R2, plan non-deliverable #6).
- **A corrupt store is never silently overwritten.** A user tag edit backs it up to
  `library_tags.json.corrupt-<UTC ts>` then saves; an unreadable store is never written (500);
  a video delete never writes a corrupt/unreadable store, nor writes at all for an untagged stem.
- **All user-derived text into HTML goes through `escHTML`** (the plan concatenated it raw).
- **The test suite is kept off the real store** by an autouse `tests/conftest.py` fixture that
  redirects `app.TAGS_PATH`.
- Execution and every ruling are recorded in the SDD ledger
  `.superpowers/sdd/2026-09-22-library-tags/progress.md` (local, gitignored).

## Changed paths

- `.ai/workstreams/library-tags.md` (created)
- `badminton_analysis/library/__init__.py`, `badminton_analysis/library/tags.py` (created)
- `app.py` (tags field on `/api/videos`; `PUT /api/videos/<name>/tags`, `POST /api/tags/rename`,
  `DELETE /api/tags/<tag>`; tag cleanup on scope-`all` delete; `_TAGS_LOCK`)
- `static/kestrel.js`, `static/kestrel.css` (Type control removed; tag chips, filter row,
  editor popover, Manage modal with Undo, empty state, dialog focus management)
- `tests/test_library_tags.py`, `tests/test_app_tags.py` (created), `tests/conftest.py` (modified)
- `docs/superpowers/specs/2026-09-22-library-tags-design.md`,
  `docs/superpowers/plans/2026-09-22-library-tags.md` (amended to match what shipped)

## Verification

- Full suite, tested commit `d016cad`, 2026-09-25:
  `PYTHONUTF8=1 ./.venv/Scripts/python.exe -B -m pytest -q -p no:cacheprovider --basetemp <unique>`
  -> 976 passed, 3 skipped (symlink tests in `tests/test_ai_handoff.py`, WinError 1314 - symlink
  privilege not held on this host). `data/library_tags.json` mtime unchanged across the run.
- Focused: `tests/test_library_tags.py tests/test_app_tags.py tests/test_app_delete.py` -> 103 passed.
- `node --check static/kestrel.js` clean.
- UI: no JS test runner exists (by design); every plan browser step for Tasks 6-9 and every
  client fix was verified against a scratch library on a second server instance, including XSS
  attempts via tag names, a server-down save, concurrent writes, Undo, and keyboard-only use.
- Reviews: per-task spec+quality reviews (Tasks 2-5 and 6-9 each needed one fix round), a final
  whole-branch review ("with fixes"), one fix wave, and a scoped re-review (all addressed).

## Blockers

None. (Resolved: the original claim `7071572f-44c2-4925-adb0-8c993dc4a77b` recorded its
`-Scope` as one comma-joined string because it was started via `pwsh -File`; it was replaced by
`takeover` after its lease expired, with the scope passed as a real array via `pwsh -Command`,
producing claim `346547d5-0b6a-4d37-a93e-b99fae9f7ac3`. The helper fix is its own workstream,
`handoff-file-scope`.)

## Next action

Owner reviews and merges PR #9 (https://github.com/seafood-rice/Good-Badminton/pull/9); address any review comments on claude/library-tags; after merge, handoff/close this workstream.

<!-- ai-continuity:milestones:start -->
- 2026-09-28T14:38:53Z - state: active - Branch pushed and PR #9 opened against main; malformed claim replaced by takeover with the correct array scope.
  - Changed paths: `.ai/workstreams/library-tags.md`
  - Verification: not-run - reason: HEAD 7103f3a differs from the last fully tested commit d016cad (976 passed, 3 skipped) only by a docs-only workstream-file commit; no code changed.
  - Next action: Owner reviews and merges PR #9 (https://github.com/seafood-rice/Good-Badminton/pull/9); address any review comments on claude/library-tags; after merge, handoff/close this workstream.
<!-- ai-continuity:milestones:end -->
