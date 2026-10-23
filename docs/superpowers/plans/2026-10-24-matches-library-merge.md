# Matches + Video library merge - Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `#/matches` the one page for every video: library tools (thumbnails, search, filters, tags, delete)
joined onto the Matches table, plus a card grid, and retire the legacy library screen.

**Architecture:** A pure model (`views/library_model.js`) joins `/api/insights/overview` match rows with the
`/api/videos` file list by stem. `views/matches.js` renders it as a table or grid and keeps its record / link
editors. `widgets/videotools.js` holds the tag editor, tag manager and delete dialog. Shell and legacy code drop the
`library` route.

**Tech Stack:** Vanilla ES modules (no build), `h()` DOM helper from `widgets/frame.js`, `node --test` with a fake
DOM, Flask backend unchanged.

**Spec:** `docs/superpowers/specs/2026-10-24-matches-library-merge-design.md`

## Global Constraints

- No backend change; no new dependency (no jsdom).
- Every new UI string exists in both `STRINGS.zh` and `STRINGS.en` (`core/i18n.js`).
- User text (stems, tags, names) only via `textContent` / attributes; thumbnails via `<img src>`.
- Pref key `kestrel_matches_view`, values `table` (default) | `grid`.
- CSS class prefix `mv-` in `static/kestrel/widgets.css`.
- `split_link.test.mjs`, `doubles_*.test.mjs`, `views_model.test.mjs` pass without edits (the video list is optional).
- JS tests: `node --test tests/js/*.test.mjs` (from the worktree root).
- Python tests: `PYTHONUTF8=1 ../../../.venv/Scripts/python.exe -B -m pytest -q -p no:cacheprovider --basetemp <unique>`.

## Review Focus

1. `/api/videos` slow or failing (cold thumbnails, cv2 durations): matches must still render, with a banner.
2. A split match's part video also present in the video list: it must never show twice.
3. Scoped view (athlete set): other athletes' analysed videos must not appear as "video rows".
4. A stem with spaces, quotes or CJK characters: thumbnail URL, selectors (`attrSel`) and menu actions still work.
5. Deleting the last video / filtering to zero: the correct empty state, focus not lost to `<body>`.

Each has a test in the task that owns it (Task 1: 2, 3; Task 3: 1, 4, 5).

---

### Task 0: Strings, prefs key, API calls

**Files:**
- Modify: `static/kestrel/core/i18n.js` (zh and en blocks), `static/kestrel/core/prefs.js`, `static/kestrel/core/api.js`
- Test: `tests/js/api.test.mjs` (add), `tests/js/i18n.test.mjs` (parity already enforced)

**Produces:**
- `PREF_KEYS.matchesView === 'kestrel_matches_view'`
- `endpoints.videoTags(stem)`, `endpoints.tagRename()`, `endpoints.tag(name)`, `endpoints.deletePreview(stem)`,
  `endpoints.deleteVideo(stem)`; `api.setTags(stem, tags)`, `api.renameTag(from, to)`, `api.deleteTag(name)`,
  `api.deletePreview(stem)` (GET `?scope=all`), `api.deleteVideo(stem)` (POST `{scope:'all'}`).
- i18n keys: `status.court_set`, `status.posture`, `lib.*` (toolbar, filters, grid, menu, tag editor/manager,
  delete dialog, banners, empty states, summary), `nav.library` kept only until Task 4 removes its users.

- [ ] Write `api.test.mjs` cases: each endpoint string (percent-encoded stem `a b`), method and body via an
  injected `fetch`.
- [ ] Run: `node --test tests/js/api.test.mjs` -> FAIL (functions missing).
- [ ] Add endpoints, calls, pref key and strings.
- [ ] Run: `node --test tests/js/api.test.mjs tests/js/i18n.test.mjs` -> PASS.
- [ ] Commit `feat(kestrel): api calls and strings for the merged Matches page`.

### Task 1: Pure library model

**Files:**
- Create: `static/kestrel/views/library_model.js`
- Test: `tests/js/library_model.test.mjs`

**Consumes:** `matchRows`, `rowTitle` from `views/matches.js`; `formatHash` from `core/router.js`.

**Produces:**
- `videoStatus(video, activeStems) -> 'processing'|'analysed'|'posture'|'court_set'|'new'`
- `libraryRows({overview, videos, activeStems = new Set(), scoped = false}) -> {rows, videosOk}`;
  match rows `{...matchRow, kind:'match', video, parts:[{...part, video}]}`; video rows
  `{kind:'video', stem, date, dateApprox:true, status, parts:[], doubles:false, sets:[], result:null,
  techIndex:null, athlete:null, opponent:null, side:null, video}`. `videos` may be `null` (failed) ->
  `videosOk:false`, no video rows.
- `SYS_TAGS = ['match', 'drill']`; `rowTags(row) -> string[]`; `tagLabel(tag, lang)`
- `STATUS_FILTERS = ['all','analysed','needs_review','processing','not_analysed','posture']`
- `filterRows(rows, {q, status, tags}, lang)`, `sortRows(rows, sort)` (`'date'|'name'`),
  `tagCounts(rows, filter, lang) -> [{tag, label, count, active, sys}]` (count = rows left if the tag were
  toggled on with the other filters), `isFiltered(filter)`
- `openTarget(row) -> hash`
- `activeStemsOf(jobs) -> Set<string>` (status pending/running/reencoding; `video_name` without extension)
- `tagError(raw) -> null | i18n key` mirroring `library.tags.normalise` (empty, too long > 40, only dots,
  reserved, slash, control char); `normTag(raw) -> string` (trim + lower-case)

Tests (each an `assert`):
- [ ] join: a split match with parts `p1,p2` and videos `p1,p2,x` -> 1 match row + 1 video row `x`; parts carry
  their own `video`. (Review Focus 2)
- [ ] scoped: same input with `scoped:true` -> only the match row. (Review Focus 3)
- [ ] `videos:null` -> match rows only, `videosOk:false`.
- [ ] `videoStatus`: active job wins; has_match; has_posture; has_annotations; new.
- [ ] order: video rows interleave by date desc; undated last.
- [ ] `rowTags`: match row -> `['match', ...user]`; posture-only video -> `['drill']`; both.
- [ ] `filterRows`: search over stem / names / tag label (zh label too); status groups; tags AND.
- [ ] `tagCounts`: counts, `active`, system tags first.
- [ ] `openTarget` for each kind/status; a stem with spaces is percent-encoded.
- [ ] `tagError` per rule; `normTag(' Smash ') === 'smash'`.
- [ ] Run `node --test tests/js/library_model.test.mjs` -> FAIL, implement, -> PASS.
- [ ] Commit `feat(kestrel): pure model joining match rows with the video list`.

### Task 2: Video tools widgets

**Files:**
- Create: `static/kestrel/widgets/videotools.js`
- Test: `tests/js/videotools.test.mjs` (fake DOM copied from `split_link.test.mjs`)

**Consumes:** Task 0 api calls and strings; Task 1 `rowTags`, `tagLabel`, `tagError`, `normTag`, `SYS_TAGS`.

**Produces:**
- `openTagEditor({video, anchor, lang, knownTags, api, onChanged, onClose}) -> {close()}`: popover, saves each
  add/remove with `api.setTags`, disables controls while saving, shows `tagError` / server error.
- `openTagManager({videos, lang, api, onChanged, toast}) -> {close()}`: rename (reports merge), delete with
  confirm, undo re-applies the tag to the stems that had it.
- `openDeleteDialog({stem, lang, api, onDeleted}) -> {close()}`: preview groups / totals, confirm disabled until
  preview ok, 409 text, network text, Escape / backdrop close unless busy.
- All three: `role="dialog"`, focus moves in, Escape closes, focus returns to the opener.

Tests:
- [ ] tag editor: add "Smash" -> `setTags(stem, ['smash'])`; reserved "match" -> error text, no call; failed save
  keeps the typed text.
- [ ] delete dialog: confirm disabled until preview resolves; confirm -> `deleteVideo(stem)` -> `onDeleted`;
  409 -> running message, buttons re-enabled.
- [ ] tag manager: rename calls `renameTag(from, to)` then `onChanged`; delete -> `deleteTag` then toast with Undo;
  Undo -> `setTags` for each affected stem with the tag added back.
- [ ] Run -> FAIL, implement, -> PASS. Commit `feat(kestrel): tag editor, tag manager and delete dialog widgets`.

### Task 3: The merged Matches page

**Files:**
- Modify: `static/kestrel/views/matches.js` (`mount` only: header, load, render; editor code untouched),
  `static/kestrel/widgets.css`
- Test: `tests/js/matches_library.test.mjs`

**Consumes:** Tasks 0-2.

Behaviour (spec sections 4-5):
- `load()` runs `api.overview(...)` and `api.videos(LONG)` together; overview failure -> existing error card;
  videos failure -> `state.videosOk = false` + warning banner with Retry; rows from `libraryRows`.
- State adds `filter {q, status, tags}`, `sort`, `view` (from `prefs.get(PREF_KEYS.matchesView, 'table')`),
  `menu` (open stem), `videos`.
- Toolbar: search input (keeps focus and caret across re-render of the list only: the toolbar is built once
  per render of the page shell, the list is re-rendered into its own container), status `<select>`, sort
  `<select>`, view `seg2`. Tag bar + result line. Scope note with "Show all videos" (navigates to
  `#/matches` without athlete / squad via `ctx.router.navigate` or `location.hash`).
- Table: thumbnail + tags in the Match cell; video rows dash cells; Analyse link; `...` menu.
- Grid: `section.mv-grid` of `article.mv-cardv` cards; editor / parts panel `div.mv-grid-panel` after the card.
- Menu: Edit tags, Classic results (`#/results/<stem>?mode=match`, only `has_match`), Posture report
  (`?mode=posture`, only `has_posture`), Delete video.
- Empty states per spec.

Tests (fake DOM, `api` patched like `split_link.test.mjs`):
- [ ] video rows render with their status chip; part stems not duplicated.
- [ ] `api.videos` rejects -> banner text present, match rows present. (Review Focus 1)
- [ ] scoped ctx -> no video rows, scope note + link present.
- [ ] grid button -> cards rendered, `prefs` key set to `grid`; remount reads it back.
- [ ] search narrows rows; status filter `posture`; tag chip toggles; filter to zero -> "No videos match" +
  Clear filters restores. (Review Focus 5)
- [ ] menu on a stem with a space and a quote opens and lists the right items; Delete opens the dialog.
  (Review Focus 4)
- [ ] Run -> FAIL, implement, -> PASS; also `split_link`, `doubles_*`, `views_model` unchanged -> PASS.
- [ ] Commit `feat(kestrel): Matches lists every video with library tools and a grid view`.

### Task 4: Shell and legacy cleanup

**Files:**
- Modify: `static/kestrel/shell/rail.js`, `static/kestrel/core/router.js`, `static/kestrel/main.js`,
  `static/kestrel/shell/palette.js`, `static/kestrel/views/legacy.js`, `static/kestrel/legacy/kestrel-legacy.js`,
  `static/kestrel/core/i18n.js` (remove `nav.library` users' texts), docs comments.
- Test: `tests/js/shell_import.test.mjs`, `tests/js/palette_index.test.mjs`, `tests/js/router.test.mjs`,
  `tests/js/legacy_view.test.mjs`, `tests/js/i18n.test.mjs`, `tests/test_kestrel_js.py`

Steps:
- [ ] Update tests first: `activeNav(library|new|results) === 'matches'`; `NAV_GROUPS` manage = `['pipeline']`;
  `parseHash('#/library')` -> `{name:'matches', params:{}, query:{}}` and keeps a query; `formatHash('library')`
  -> `'#/overview'` (unknown); palette pages = Overview, Matches, Players, Pipeline; legacy not-found link is
  `#/matches`; `routeFor` dashboard nav -> `#/matches`; `test_kestrel_js.py` no longer expects `mountLibrary`.
- [ ] Run -> FAIL. Implement: rail, router alias, `main.js` (`VIEW_LOADERS` without `library`, `VIEW_NAV`
  new/results -> `nav.matches`, alias replace), palette, legacy wrapper, legacy module (delete dashboard screen
  code, keep what wizard / results use; `routeFor` default -> matches).
- [ ] Run all JS tests -> PASS. Commit `refactor(kestrel): retire the legacy library screen; one Matches entry`.

### Task 5: Verify, record, ship

- [ ] `node --test tests/js/*.test.mjs` -> all pass; full pytest -> all pass (record counts).
- [ ] Live check in the browser pane (worktree server on port 5071 with `outputs` / `data` junctions): table and
  grid, zh and en, search / filter / tags, Edit tags (add then remove a throwaway tag), Delete dialog preview then
  Cancel, scope note, `#/library` redirect, wizard Cancel lands on Matches.
- [ ] qa-reviewer pass on the branch; fix findings.
- [ ] `.ai/STATE.md`: add then (after merge) remove the row; commit.
- [ ] Push, PR, merge (owner pre-approved), rebuild the 5050 container, verify.
