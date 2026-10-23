# Match details page redesign - design

Status: approved by the owner in conversation on 2026-10-24 (layout A, whole-match part cards, module split,
section 1 content mapping). The owner approved the rest of the flow up front: implement, test, merge the PR,
build the image and roll it out to the 5050 container from main.

## 1. Request

Owner, verbatim: "in the match details page, should redesign UI and combine with insights from 'open full
results'. If the analysis has more than 1 video, the match details page should allow to switch between video
parts and shows its details. The video player is missing volume button."

## 2. Goal and non-goals

Goal: one match details page (`#/matches/<stem>`) that shows everything the legacy match results screen
(`#/results/<stem>?mode=match`) offered, in a redesigned layout. A split match (one match, several videos) gets a
part switcher with a Whole match view. The workspace video player gets a volume / mute control.

Non-goals: changing the analysis outputs or the server APIs (no new endpoints); merging heatmap images across
parts; one continuous player across parts (rejected option A of the whole-match question); the posture (drill)
report screen, which stays on the legacy route; deleting the legacy results screen code (its route keeps working).

## 3. Owner decisions

- The owner uses the heatmap / scatter most: it gets top billing (top band, always visible). Clips, technique
  summary and training plan move to secondary tabs.
- Split matches: switcher `Whole match | Part 1 | Part 2 ...`; a split match lands on **Whole match**.
- Layout A: player and its companions on top, tabs below.
- Whole match: no player; one card per part (poster, length, rallies, strokes, tech index, sides swapped) that
  selects that part; pooled numbers underneath.
- Links to the old screen are removed from both the workspace header ("Open full results") and the Matches row
  menu ("Classic results (clips)"). The route itself keeps working. The "Posture report" link stays.
- Code: split into focused modules (approach 1).
- Delete analysis results sits in a header "..." menu; YouTube video guides become a plain link (no embed); no
  new clip-list endpoint (clips are listed after generating, as in the legacy screen).

## 4. Legacy inventory and where each piece goes

| Legacy match screen (`renderMatchResults`) | New home |
|---|---|
| Annotated video, native controls (volume included) | Top band player, now with mute + volume |
| Rally count with the honest label | Header line (unchanged) |
| Generate rally clips (`POST /api/clip`, highlights, padding 1.5), download list | Clips tab |
| Delete clips (preview dialog, scope `clips`) | Clips tab |
| Stroke distribution by group | Strokes tab (top) |
| TrackNet provenance note (`strokes.json` `shuttle_source`) | Strokes tab, under the distribution |
| Stroke timeline (label sequence) + stroke notice | Already covered by the stroke strip and list (+ notice) |
| Heatmap + scatter images | Movement card, top band right column |
| Doubles players table | Already in the players panel |
| Doubles team / player heatmaps | Movement card (doubles) |
| Doubles identity video link | Players panel (the player already plays it first when ok) |
| Technique summary: stroke count, per type count and average, top 3 recurring weaknesses | Technique tab |
| Per-stroke metric bars | Inspector (already) |
| Per-stroke weakness lines | Inspector, under the metrics |
| Technique stale notice / doubles withheld text | Technique tab |
| Training plan: on-court / at-home, regenerate, exercise detail | Training plan tab |
| Delete match results (preview dialog, scope `match`, 409 guard) | Header "..." menu: Delete analysis results |
| Match / Drill toggle | Header "Posture report" link when the video has a posture run |

## 5. URL

- Single video: `#/matches/<stem>` (unchanged), optional `tab=<strokes|technique|clips|plan>`.
- Split match: always the primary's stem. `#/matches/<primary>` = Whole match; `#/matches/<primary>?part=<part
  stem>` selects a part (Part 1 is the primary itself: `part=<primary>`). Stems, not numbers: numbers shift when a
  part is linked or unlinked.
- Opening a part's own stem (`#/matches/<part stem>`) rewrites the hash in place (`router.replace`, no history
  entry) to `#/matches/<primary>?part=<part stem>` (keeping `tab`).
- An unknown `part` (not in the match) falls back to Whole match and the hash is rewritten without it.
- Part and tab changes inside the page go through `router.navigate` (part: a history entry) or `router.replace`
  (tab), and the view follows the new query in place via `setQuery(query)` (main.js already calls it for the same
  view): no remount, no video reload on a tab change. A part change loads that part's data and video.
- `part` on a non-split match is ignored and removed.

## 6. Page structure

Header (all views): breadcrumb; eyebrow date; title (players); meta line (sets, result, rally / stroke summary,
chips). Split matches add the switcher (`role="tablist"`-free: a group of `aria-pressed` buttons, or links with
`aria-current`) with a "sides swapped" chip on swapped parts. Actions: Back; Posture report (only when
`/api/videos` says `has_posture` for the shown stem; on Whole match, none); "..." menu with Delete analysis
results (scope `match`, for the shown stem; hidden on Whole match).

Part / single-video view (layout A):
- Top band left: video card (player), rally timeline + stroke strip (unchanged behaviour).
- Top band right: Movement card; Inspector (or the doubles players panel); Rally summary.
- Tabs: Strokes | Technique | Clips | Training plan. Doubles: Technique shows the withheld note, the Strokes tab
  keeps the hits table.

Whole match view:
- Part cards row: poster (`/api/videos` `thumb`, blank tile without), "Part N", duration, rallies (honest
  label), strokes, tech index (the part's insights sidecar `technique.value.by_side[<side>].index` for the
  selected athlete's side in that part; with no athlete in scope, the only side that has one; else "—"), "sides
  swapped" chip,
  status when not analysed. Activating a card selects that part.
- Movement card: per-part heatmap + scatter side by side, captioned "Part N".
- Tabs: Strokes (pooled distribution, plus a per-part table: part, strokes, uncertain), Technique (pooled
  summary: counts summed, averages weighted by count, recurring weaknesses merged by metric and summed), Clips
  and Training plan (per-part: "Clips / plans are per video" with one button per part that opens that part on
  the same tab).
- Rally and stroke totals in the header line are the sum over analysed parts with the weakest honest label
  (any part `windows` -> windows; else any `segments` -> segments; else rallies; a failed part is excluded and
  noted).

## 7. Modules

- `views/workspace_model.js` (pure, DOM-free): everything pure now in `workspace.js` (buildWorkspaceModel,
  summariseRallyPayload, matchDate, matchDateApprox, listScore, rallyInput, rallyCountText, capItems, readReduced,
  constants), plus new pure helpers:
  - `matchParts(record, stem)` -> `{split, primary, parts:[{stem, n, swapped}]}` from `GET /api/matches/<stem>`
    of the primary (Part 1 = primary, then `parts` in the server's order). For a part's record (`part_of`), the
    caller fetches the primary's record first.
  - `resolvePartQuery(info, stem, query)` -> `{redirect: hash|null, view: 'whole'|'part'|'single', partStem}`.
  - `poolDistribution(strokeDocs)`, `poolTechnique(summaries)`, `poolRallyCounts(summaries)`.
  - `strokeDistribution(strokesDoc)` (group counts, mirrors legacy `distributionText` but returns data) and
    `trackNetNote(strokesDoc)`.
  - `techniqueSummary(techPayload)` -> `{state:'ok'|'empty'|'stale'|'doubles', count, byType:[{id,count,avg}],
    weaknesses:[{metric,count}]}`.
  - `weaknessLines(report)` -> per-stroke weakness items `{metric, measured, ideal, text}`.
  - `planModel(plan, loc)` -> weeks/sessions for one location.
  `workspace.js` re-exports the old names so existing imports and tests keep working.
- `widgets/player.js`: `createPlayer({files, lang, prefs, onTime, ...})` - the video card: candidates fallback,
  error messages, play / seek / prev / next hooks, theater and full screen (moved from `workspace.js` as is), and
  the new mute button + volume slider. Volume and mute are stored in prefs (`kestrel_volume`, `kestrel_muted`);
  `M` toggles mute when focus is inside the video card (not in a text field). Returns
  `{el, seekTo, setSource, destroy, ...}`.
- `widgets/movement.js`: heatmap + scatter images (one video, or several captioned by part; doubles team and
  player heatmaps). Missing image -> "Not available" cell. Click / Enter opens a larger view in a dialog
  (reusing the shell dialog look) with Escape to close.
- `widgets/technique.js`: the Technique tab body from `techniqueSummary` (one video or pooled).
- `widgets/clips.js`: Generate / list / Delete clips for one stem (POST `/api/clip`; delete via a scoped preview
  dialog).
- `widgets/plan.js`: training plan tab for one stem (GET / POST `/api/training-plan/<stem>`, location toggle,
  regenerate, expandable details; all strings from the file set as text).
- `widgets/videotools.js`: `openDeleteDialog` gains an optional `scope` (`all` default; `match`, `clips`) with
  per-scope title and warning strings.
- `views/workspace.js`: the page: load, header, switcher, tabs, URL handling, `setQuery`, whole-match view, and the
  existing timeline / strip / list / inspector / summary DOM code.
- `views/matches.js`: drop the "Classic results (clips)" menu item.

## 8. Data loading

Single or part view: as today (rally_segments, strokes, metadata, technique, insights, match record) plus
`/api/videos` (thumb, has_posture, duration; soft, a failure only hides the posture link and posters). The
training plan and clips load when their tab is first opened. Heatmap images load as `<img>`.

Split match: the primary's record gives the parts. Whole match loads, per part, rally_segments, strokes, metadata,
technique and insights (soft; a failed part shows as unavailable in its card and is left out of the pools).
Switching to a part loads that part like a single video (the header record is the primary's, already loaded;
names per part come from the resolved record, so swapped parts show the right names per side).

## 9. Error handling

- A part's outputs missing (status new / processing): its card says so; selecting it shows the not-analysed state
  inside the page (header and switcher stay).
- Video unplayable / missing: the existing messages; the volume controls are disabled with the other controls.
- Clip generation error: inline error text in the Clips tab (no `alert`).
- Plan load / regenerate error: inline text.
- Delete analysis results: on success navigate to `#/matches` (the match's analysis is gone).
- prefs storage unavailable: volume works for the session, nothing is remembered.

## 10. i18n

All new strings in zh and en in `core/i18n.js` (`ws.*`, `wsx.*` keys as needed); `tests/js/i18n.test.mjs`
enforces parity. Strings that come from files (stems, plan text, clip names) are set as text, never HTML.

## 11. Testing

- Node tests (`node --test tests/js/*.test.mjs`): the pure helpers in `workspace_model.js`; `player.js` volume
  behaviour with the fake DOM (mute toggle, slider, prefs restore, `M` key, theater / full screen still toggle);
  the URL resolution cases (single, whole, part, part's own stem redirect, unknown part, part on non-split);
  pooling; clips / plan / movement / technique widgets render from fixtures; Matches menu no longer has the
  classic results item.
- Python JS-contract tests (`tests/test_kestrel_js.py`, `tests/test_doubles_kestrel.py`) still pass (update any
  that assert the removed link).
- Full pytest at the end.
- Live check on the worktree server (port 5071, `::1`) with `videos`, `outputs`, `data` junctioned to the main
  checkout: a single match, a split match (Whole match, Part 1, Part 2, part's own stem link), a doubles match,
  volume + theater + full screen, tabs, clip generation on a short video only if safe; never confirm a delete.
