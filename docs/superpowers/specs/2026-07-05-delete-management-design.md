# Kestrel — Delete Management Design

_Date: 2026-07-05_

## Context

Kestrel (the new UI at `/kestrel`) can create videos, analyses, clips, and reports but
cannot remove any of them. The old UI has a single "delete all results" button backed by
`DELETE /api/output/<video_name>/<path:subpath>` (app.py), which trusts client-supplied
paths and validates nothing — tolerable while nothing in the new UI deleted files, unsafe
as the foundation of a real delete feature.

This design adds scoped, preview-first deletion to Kestrel: granular per-artifact-group
deletes on the results screens, full video removal from the library, a confirmation modal
that lists exactly what will be removed, and server-side scope resolution that closes the
path-traversal hole for the legacy route as well.

## Locked decisions (user)

- **Scope granularity:** granular groups (match results / drill results / clips / reports)
  **plus** a full "delete everything for this video" that includes the uploaded source file.
- **UI placement:** both surfaces — library cards carry full-delete; results screens carry
  the granular deletes in context.
- **Confirmation UX:** a custom Kestrel modal listing the affected groups with file counts
  and sizes, a red danger button, cancel/Esc/backdrop dismissal. Deletes are permanent —
  no trash, no undo.

## Scopes

One server-side resolver maps `(stem, scope)` → list of filesystem paths. The five scopes:

| Scope | Removes | Explicitly keeps |
|---|---|---|
| `match` | Everything directly under `outputs/<stem>/` — detections, rally segments, annotated video + temp videos, court annotation/previews, metadata, technique summary/strokes/log, match training plan, `clips/` | `posture/` subdir, `thumb.jpg` |
| `posture` | The whole `outputs/<stem>/posture/` directory (drill data, annotated video, rep clips, coach reports, posture training plan) | everything else |
| `clips` | `outputs/<stem>/clips/` and `outputs/<stem>/posture/rep_clips/` | all analysis data |
| `reports` | `outputs/<stem>/posture/coach_report_*` (json/html/pdf, all languages) | all drill data |
| `all` | The entire `outputs/<stem>/` directory, the uploaded source video (`videos/<stem>.*`), and the court template (`templates/_auto_<stem>*`) | nothing |

Notes:
- `match` is defined by exclusion (iterate `outputs/<stem>/*`, skip `posture/` and
  `thumb.jpg`) so future match artifacts are covered automatically.
- Deleting `match` also removes the court annotation, so the card status reverts to "New"
  (re-analysis redoes court setup). Accepted for simplicity.
- Scope `all` removes the video from the library entirely (its `/api/videos` entry derives
  from the source file).

## Backend

Two new routes plus hardening, all in `app.py`:

- **`GET /api/delete-preview/<video_name>?scope=<scope>`** →
  `200 {ok: true, scope, groups: [{key, files, size_mb}], total_files, total_size_mb}`.
  Group rows are **disjoint** (no double counting) with fixed keys the frontend localizes:
  - `all` → `source` (uploaded video + court template), `match` (incl. rally clips), `posture` (incl. rep clips + reports), `thumb`
  - `match` → `match` · `posture` → `posture` · `clips` → `clips_rally`, `clips_rep` · `reports` → `reports`
  - Rows with 0 files are omitted; `total_files` may be 0 (frontend disables the danger button).
- **`POST /api/delete/<video_name>`** body `{scope}` →
  `200 {ok: true, scope, deleted_files: <int>}` after removing exactly the resolver's paths.
  Errors: `400` invalid scope or unsafe/unknown stem, `409` an analysis job for this stem is
  currently running (match jobs are keyed by stem; posture jobs carry `video_name` — any
  non-terminal job whose video matches blocks deletion), `500` filesystem failure (JSON error).
- **Stem validation** — shared helper: reject empty names or names containing `/`, `\`,
  `..`, or `os.sep`; require that `videos/<stem>.*` or `outputs/<stem>/` exists. The
  **legacy** `DELETE /api/output/...` route adopts the same stem check plus a containment
  check (the resolved target must stay inside `outputs/<stem>/`), returning 400 on
  violation. The old UI's valid calls keep working.

Preview and delete share the single resolver, so the modal's list always matches what the
delete removes.

## Frontend (static/kestrel.js + kestrel.css)

- **`openDeleteModal(stem, scope, onDone)`** — builds a fixed overlay + card appended to
  `document.body`: localized title per scope, subtitle naming the video (mono), one row per
  preview group (localized label + `N files · X MB`), a red **danger button**
  (`background: var(--bad)`), and Cancel. Esc, backdrop click, and Cancel dismiss. The
  danger button is disabled while the preview loads and when `total_files` is 0 (row area
  then shows a localized "nothing to delete"). Confirm → POST → on 200 remove modal and
  call `onDone()`; on error show the localized message inline in the modal (409 gets its
  own wording: "analysis is running — wait for it to finish").
- **Library cards** — a trash button positioned on the thumbnail (hover-revealed,
  `stopPropagation` so it doesn't open the card), scope `all`; `onDone` → `loadDashboard()`.
- **Match results** — a final "danger" row with a ghost button "删除比赛结果 / Delete match
  results" (scope `match`); "删除剪辑 / Delete clips" (scope `clips`) sits beside the
  Generate-clips button. `onDone` for `match`: if the video also has posture results,
  switch the mode toggle to Drill, else return to the library; for `clips`:
  `renderResults()` refresh.
- **Posture results** — "删除训练结果 / Delete drill results" (scope `posture`) as the final
  row; "删除报告 / Delete reports" (scope `reports`) beside the coach-report downloads.
  `onDone` mirrors match (switch to Match mode if it exists, else library; reports →
  `renderResults()` refresh, report panel shows its existing "no report yet" state).
- All new strings bilingual zh/en; modal styles use theme tokens only (overlay uses a
  translucent black, consistent in both themes).

## Error handling & edge cases

- Deleting the mode you are viewing navigates you somewhere valid (other mode or library).
- A stem with no artifacts for the chosen scope shows an empty preview and a disabled
  danger button rather than erroring.
- Concurrent analysis: server-side 409 (checked at delete time, not just preview time).
- Legacy route behavior is preserved for valid inputs; traversal attempts now 400.
- Filesystem errors surface as localized inline modal text; partial deletion is possible on
  crash mid-loop (accepted for a local tool; the preview re-run shows what remains).

## Testing

- **Backend (pytest, tmp fixture tree + monkeypatched `OUTPUTS`/`VIDEOS`/`TEMPLATES`):**
  resolver partitions are disjoint and complete for `all`; per-scope path sets (match keeps
  `posture/`+`thumb.jpg`; clips hits both clip dirs; reports globs all languages); preview
  counts/sizes; delete removes exactly the resolved set and leaves the rest; source file
  removed only by `all`; 400 on traversal stems (`../x`, `a/b`, `a\\b`, empty) and invalid
  scope; well-formed but nonexistent stem → 400 (fails the existence half of validation); 409 while a
  running job matches the stem; legacy route 400 on bad stem, still 200 on valid subpath.
  Baseline 165 stays green.
- **Frontend:** `node --check` + curl (routes' JSON shapes); controller browser dogfood —
  modal open/cancel/Esc, preview rows, disabled-empty state, each scope end-to-end on a
  scratch copy of fixtures, post-delete navigation.

## Out of scope (deferred)

- Trash / undo / soft-delete.
- Bulk multi-video deletion.
- Deleting individual clips or individual reps/strokes.
- The app-wide `esc()`/hardening sweep already queued for Phase 5 (this feature only fixes
  the delete-path traversal).
