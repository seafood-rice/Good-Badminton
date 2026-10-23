# Matches + Video library merge - Design

Date: 2026-10-24. Branch `claude/consolidate-video-pages-dee928`. Owner approved the direction, layout and
scope rules in chat (2026-10-23/24) and pre-approved spec, plan, PR merge and the 5050 rollout.

## 1. Goal

One page to find, organise and open every video. Today two rail pages cover the same videos:

- **Video library** (`#/library`, Manage): one card per video file, thumbnails, stat tiles, search, status filter,
  sort, tags (filter / edit / manage), delete. Lives in `legacy/kestrel-legacy.js` (string HTML).
- **Matches** (`#/matches`, Analyze): one row per analysed match, result / sets / technique / status, record editor,
  split-match parts and linking, athlete / squad scope. Lives in `views/matches.js` (`h()` DOM).

After this change **Matches** is the one page. It keeps everything Matches does, gains the library's management
tools, lists every video file, and offers a table or a card grid.

### Done means

1. The rail shows Overview, Matches, Players | Pipeline. `#/library` opens Matches. `#/new` and `#/results/...`
   light the Matches rail item.
2. With no scope, Matches lists every video in `VIDEOS/`: analysed matches (as today, split matches folded) plus
   posture-only, court-set, new and processing videos.
3. Search, status filter, sort, tag filter (AND, counts, Clear, Manage...) work in both views.
4. A table / grid switch is remembered per browser.
5. Per video: edit tags, delete (preview + 409 guard), open the classic results (clips) and the posture report when
   they exist. Record editor and split linking behave exactly as before.
6. With an athlete or squad scope only that scope's matches show, with a "show all videos" link.
7. The legacy library screen code is removed; the wizard and results screens still work.
8. `node --test tests/js/*.test.mjs` and the full pytest suite pass; a live check on real data passes.

### Out of scope (deferred)

Bulk actions, pagination, video rename, preselecting the clicked video in the wizard, tag/delete actions on split
sub-rows (unlink first, then the video is its own row), the library stat tiles (Overview has the KPIs), date-range
scope on Matches (still `range=all`).

## 2. Approach

Grow the Matches page; join on the client. The page fetches `/api/insights/overview?range=all&athlete&squad`
(required) and `/api/videos` (optional) in parallel and a pure model joins them by stem. No backend change.

Rejected: a server-side `/api/library/rows` join (duplicates `aggregate._rows`, touches `app.py` which many
branches edit); two tabs on one page (keeps both implementations and the legacy code).

## 3. Rows

`libraryRows({overview, videos, activeStems, scoped})` (pure, `views/library_model.js`) returns
`{rows, videosOk}`:

- **Match rows**: `matchRows(overview)` unchanged, each given `kind: 'match'` and `video` = the `/api/videos` entry of
  its stem (or `null`), and each part given its own `video`.
- **Video rows** (`kind: 'video'`), only when **not scoped** and the video list loaded: one per video whose stem is
  neither a match row's stem nor one of its parts' stems. Fields: `stem`, `date` = file date, `dateApprox: true`,
  `status`, `parts: []`, `doubles: false`, `video`. Status, first match wins:
  1. stem in `activeStems` (a pending / running / re-encoding job from the store) -> `processing`
  2. `has_match` -> `analysed` (rare: a run the overview could not read)
  3. `has_posture` -> `posture`
  4. `has_annotations` -> `court_set`
  5. else `new`
- Order: date descending, undated last, then stem (same order the overview uses), so video rows interleave by date.

Scoped: rows are the scoped overview's match rows only (videos still supply thumbnails and tags). A note under the
toolbar reads "Showing <scope>'s matches · Show all videos"; the link clears athlete and squad.

### Row tags

`rowTags(row)` = system tags (`match` when the video `has_match` or the row is a match row, `drill` when
`has_posture`) + the video's user tags. Labels: system tags localised (Match / Drill), user tags verbatim.

### Open target

`openTarget(row)`: match row -> `#/matches/<stem>` (workspace, container transform); video row `analysed` ->
workspace; `posture` -> `#/results/<stem>?mode=posture`; `processing` -> `#/pipeline`; `new` / `court_set` ->
`#/new`.

## 4. Toolbar and filters

Pure helpers in `library_model.js`, applied in this order: tags (AND) -> status -> search -> sort.

- **Search** (case-insensitive substring): stem, filename, row title / subtitle (`rowTitle`), tag labels.
- **Status filter**: `all` | `analysed` (analysed + needs_review) | `needs_review` | `processing` |
  `not_analysed` (new + court_set) | `posture`. A `<select>`.
- **Sort**: `date` (default, as above) | `name` (stem, locale compare).
- **Tag bar**: one chip per tag in the current rows (system tags first, then user tags A-Z) showing the count it
  would leave given the other filters; zero-result chips disabled unless active; "Clear tags"; "Manage tags...".
- **Result line**: "N of M videos" when any filter is active.
- **View switch**: `seg2` buttons Table / Grid, `aria-pressed`, stored under `kestrel_matches_view`
  (`table` default) via `prefs`.

Filters live in the view's state for the session (not in the URL).

## 5. Rendering

**Header**: eyebrow, title "Matches", summary (unscoped: "N videos · A analysed"; scoped: "N matches"), New analysis.

**Table** (existing columns): the Match cell gains a 64x36 thumbnail with the duration overlay before the title,
and the video's tag chips under the subtitle. A video row shows "-" for result / sets / technique; its action cell
shows **Analyse** (link to `#/new`) for `new` / `court_set`, nothing else for other statuses. Every row whose
video exists ends with a **...** menu button. Split parts sub-rows get a small thumbnail.

**Grid**: one card per row: thumbnail (duration, "N parts", Doubles chips), title, subtitle, date, status chip,
result chip + technique index when known, tag chips, then the Record / chevron / ... buttons. The record editor
and the parts list open as a full-width panel (`grid-column: 1 / -1`) right after the card, using the same editor
code as the table. Clicking the card body opens `openTarget`.

**... menu** (`aria-haspopup="menu"`, Escape and outside click close, focus returns to the button):
Edit tags; Classic results (clips) when `has_match`; Posture report when `has_posture`; Delete video (danger).

**States**: loading skeleton, load error + Retry (overview failed), refresh-error banner, video-list warning banner
("Video files could not be listed; showing analysed matches only" + Retry), empty library ("No videos yet" + New
analysis), filtered-to-nothing ("No videos match these filters" + Clear filters), scoped empty (existing text).

## 6. Video tools (`widgets/videotools.js`, `h()` DOM)

Ports of the legacy behaviour, same endpoints, added to `core/api.js` as `setTags`, `renameTag`, `deleteTag`,
`deletePreview`, `deleteVideo`:

- **Tag editor** popover anchored to the menu button: current user tags as removable chips, system tags read-only,
  an input with up to 5 suggestions (arrow keys / Enter), client validation mirroring
  `badminton_analysis.library.tags.normalise` (trim, lower-case, 1-40 chars, not only dots, not reserved
  `match` / `drill`, no `/` or `\`, no control characters) with a specific message per rule, saves each change immediately with `PUT /api/videos/<stem>/tags`; a failed save shows the
  error and keeps the typed text.
- **Tag manager** modal: every user tag with its count, Rename (merge reported), Delete with confirm and an Undo
  toast that re-applies the tag to the affected videos.
- **Delete video** modal: `GET /api/delete-preview/<stem>?scope=all` groups and totals, "Delete permanently",
  `POST /api/delete/<stem>` `{scope:'all'}`; 409 -> "Analysis is running"; network error -> retry text. Escape /
  backdrop close unless busy.

Each tool calls back `onChanged()`; the page reloads both lists and keeps focus on the row's menu button (or the
next row after a delete).

## 7. Shell and legacy changes

- `shell/rail.js`: `manage` group = `['pipeline']`; `activeNav` maps `library` / `new` / `results` to `matches`.
- `core/router.js`: `library` is no longer a route; `parseHash('#/library...')` returns the `matches` route (query
  kept). `main.js` replaces the URL with the canonical hash when the alias is used.
- `main.js`: `VIEW_LOADERS` drops `library`; `VIEW_NAV` maps `new` / `results` to `nav.matches`.
- `shell/palette.js`: `PAGE_IDS` drops `library`; the `court_set` hint becomes `status.court_set`.
- `views/legacy.js`: renders only `new` and `results`; the not-found link goes to Matches.
- `legacy/kestrel-legacy.js`: the dashboard screen and its helpers (stats, cards, filters, tag bar, tag editor,
  tag manager) are deleted; `routeFor('dashboard')` -> `{name: 'matches'}` so wizard Cancel / "Back to library" and
  results Back land on Matches. Shared helpers the wizard / results still use stay.
- `core/i18n.js`: new `lib.*` and `status.court_set` / `status.posture` strings in zh and en; `nav.library` and the
  "go to the video library" texts updated or removed.
- `core/prefs.js`: `PREF_KEYS.matchesView = 'kestrel_matches_view'`.
- CSS in `widgets.css` (`mv-` prefix): toolbar, tag bar, thumbnails, grid cards, menu, popover, modals.

## 8. Errors and edge cases

- `/api/videos` fails or times out: match rows only, warning banner, tags / delete hidden (no video data).
- Overview fails: existing error card; the page never shows video rows without the overview (they could duplicate
  parts).
- A match row with no video file (outputs only): no thumbnail, no ... menu.
- A stem that is both a part and in the video list: shown only inside its match.
- Doubles rows: unchanged (no parts); they get thumbnails, tags and the menu.
- Tag / delete calls while the list reloads: the reload uses the request-id guard already in `load()`.
- Stems are only ever set through `textContent` / attributes; thumbnails use `img src` (no CSS `url()`).

## 9. Testing

- `tests/js/library_model.test.mjs`: join (parts folded, video rows, statuses, scoped), order, filters, search,
  sort, tag counts / projected counts, `openTarget`, tag validation.
- `tests/js/matches_library.test.mjs` (fake DOM like `split_link.test.mjs`): video rows render; videos failure
  shows the banner and still renders matches; scope hides video rows and shows the note; grid switch renders cards
  and persists; search / status / tag filters; ... menu items; delete flow calls the API and reloads.
- Existing tests updated: `shell_import`, `palette_index`, `router`, `legacy_view`, `i18n`, `test_kestrel_js.py`.
  `split_link.test.mjs` and the doubles tests must pass unchanged (the video list is optional).
- Full pytest; live check in the worktree preview against real `outputs/` + `data/` (junctioned), both views, both
  languages, a tag edit and a delete-preview (cancelled).

## 10. Rollout

PR to `main` on `origin`, owner pre-approved the merge. Then from the main checkout:
`python scripts/docker_build.py` and `docker compose -p kestrel up -d --force-recreate app`, after confirming
`/api/jobs` has no active job; verify `http://localhost:5050/#/matches`.
