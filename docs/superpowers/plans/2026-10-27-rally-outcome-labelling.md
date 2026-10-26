# Rally Outcome Labelling Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Write `outputs/<stem>/rally_outcomes.json` (who won each point, how it ended) from owner labels entered on the match details page plus a cautious rule-based automatic labeller.

**Architecture:** A new pure-Python package `badminton_analysis/outcomes/` (labels store, evidence, rules, reconciliation, build) reads run files back from disk and writes one derived JSON document. `system.py` calls the build at the end of match analysis; a Flask blueprint stores owner labels in `data/rally_labels/<stem>.json` and rebuilds on save; the Kestrel match details page gets a Point block, timeline tints, a tally and "Add point end here".

**Tech Stack:** Python 3 (stdlib + numpy + cv2, already dependencies), Flask blueprint, vanilla ES modules (Kestrel), pytest, `node --test`.

**Spec:** `docs/superpowers/specs/2026-10-27-rally-outcome-labelling-design.md`

## Global Constraints

- `rally_outcomes.json` is `schema: 1`; fields are only ever added; key names exactly as spec 5.2.
- Sides are `"upper"` / `"lower"`; UI shows near = `lower`, far = `upper`.
- Owner label verdicts: `upper` | `lower` | `not_point` | `unsure`; outcomes: `winner` | `forced_error` | `unforced_error` | null.
- Auto `outcome` is always null in v1; auto confidences are 0.8 / 0.6 placeholders; `confidence_calibrated: false`.
- Nothing in `outcomes/` raises for bad input; the system hook is never fatal.
- Error bodies are static bilingual strings `中文 / English`; no exception text in a response.
- Tests: `PYTHONUTF8=1 ./.venv/Scripts/python.exe -B -m pytest -q -p no:cacheprovider --basetemp <unique>`; JS: `node --test tests/js/*.test.mjs`.
- Never commit to `main`; branch `claude/rally-outcome-labelling-d2eccc`. Keep the `.ai/STATE.md` row current.

## Review Focus

- A re-analysis that re-segments (ids change, segments split/merge): owner labels must re-attach by time, never be lost (Task 1 test `test_attach_after_resegmentation`, `test_unattached_label_becomes_added`).
- Two tabs saving labels: the second save with a stale revision gets 409, not a silent overwrite (Task 7 `test_put_stale_revision_409`).
- A corrupt or hand-edited label file: build still writes a document (labels ignored with a note), and a PUT refuses to overwrite it (Task 1 `test_load_corrupt_is_none`, Task 7 `test_put_refuses_unreadable_store`).
- A run with no `hits.json` / no trajectory / no corners (older runs, doubles): the build still writes a file with every entry unknown (Task 5 `test_build_minimal_run`).
- Saving labels while an analysis of that video is running: labels saved, rebuild skipped, answer says so (Task 7 `test_put_during_job_skips_rebuild`).

---

### Task 1: Owner label store (`outcomes/labels.py`)

**Files:**
- Create: `badminton_analysis/outcomes/__init__.py`, `badminton_analysis/outcomes/labels.py`
- Test: `tests/test_outcome_labels.py`

**Interfaces:**
- Produces:
  - `VERDICTS = ("upper", "lower", "not_point", "unsure")`, `OUTCOMES = ("winner", "forced_error", "unforced_error")`
  - `empty() -> {"schema": 1, "revision": 0, "labels": []}`
  - `validate_labels(raw) -> list[dict] | None` (None = invalid body); each label normalised to `{"start_sec": float|None, "end_sec": float, "verdict": str, "outcome": str|None, "added": bool}`; outcome forced to None unless verdict is a side; at most 2000 labels.
  - `labels_path(labels_dir, stem) -> Path`
  - `load(path) -> dict | None` (empty() when absent; None when unreadable/corrupt)
  - `save(path, doc)` atomic (tmp + `os.replace`, parent mkdir)
  - `attach(labels, segments) -> (by_segment: {segment_id: label}, added: [label])` with `segments` = `[{"id", "start_sec", "end_sec"}]`.

- [ ] **Step 1: Write failing tests** covering: validate accepts good doc, rejects bad verdict / non-finite time / non-list / outcome with `not_point` normalised to None; `load` absent -> empty, corrupt JSON -> None, wrong schema -> None; `save` round-trip and no `.tmp` left; `attach` exact match; `test_attach_after_resegmentation` (label made on segment 20-30 s; new segments 18-25 and 26-31 -> attaches to the segment containing end 30 ±1 -> 26-31); `test_unattached_label_becomes_added`; added labels always go to `added`; two labels on one segment -> the later list entry wins, the other becomes added.
- [ ] **Step 2: Run, expect FAIL** (`ModuleNotFoundError`).
- [ ] **Step 3: Implement.** Attachment: for each non-added label, candidates = segments with `start-1 <= end_sec <= end+1`; choose min `abs(seg.end - end_sec)`; iterate labels in order so a later label replaces an earlier one on the same segment (the replaced one is appended to `added` only if its verdict is a side; a replaced `not_point`/`unsure` is dropped).
- [ ] **Step 4: Run, expect PASS.**
- [ ] **Step 5: Commit** `feat(outcomes): owner rally label store with time-based re-attachment`.

### Task 2: Evidence (`outcomes/evidence.py`)

**Files:**
- Create: `badminton_analysis/outcomes/evidence.py`
- Test: `tests/test_outcome_evidence.py`

**Interfaces:**
- Produces:
  - `NET_Y = 6.7`, `COURT = (6.1, 13.4)`, `LINE_MARGIN_M = 0.3`, `RALLY_PAD_SEC = 0.5`
  - `side_of_y(y) -> "upper" | "lower"` (`upper` when y < 6.7)
  - `last_hitter(hits, start_sec, end_sec) -> str|None` (`hits` = `[(time_sec, frame, hitter)]` as in `loaders.Run.hits`)
  - `next_server(hits, this_end_hit_sec, next_start_sec, next_end_sec, min_pause=3.0) -> str|None`
  - `clean_track(traj: {frame:int -> (x,y)}, frames: range, frame_h: float, static_points: [(x,y)]) -> [(frame, x, y)]`
  - `find_landing(points, fps, frame_h) -> (x, y) | None` (first run >= 0.25 s within 0.6 % of frame_h; mean point)
  - `court_homography(corners) -> 3x3 ndarray | None` (corners TL, TR, BR, BL -> (0,0),(6.1,0),(6.1,13.4),(0,13.4)); `to_court(H, xy) -> (x, y)`
  - `landing_verdict(court_xy, last_hitter) -> {"winner": side|None, "ending": "in"|"out"|"net"|None, "point": [x, y], "reason": str}` per spec 5.3 (inside on X -> winner other(X); outside beyond X's half -> winner X; within 0.3 m of any boundary line or the net line -> winner None, reason `near_line`).
- Consumes: nothing from earlier tasks.

- [ ] **Step 1: Write failing tests**: `side_of_y`; `last_hitter` honours the 0.5 s pad and returns None with no hits; `next_server` None when the pause < 3 s; `clean_track` drops static-cell points and a 10 %-of-height jump; `find_landing` finds a 20-frame still run at 60 fps and None for a moving track; homography maps the corners to the court corners exactly; `landing_verdict` table: (3.0, 3.0) upper in -> lower wins, ending `in` with E1 lower, `net` with E1 upper, None without E1; (3.0, -1.0) beyond upper baseline -> upper wins, `out`; (3.0, 6.8) near net -> no verdict; (0.1, 10) near side line -> no verdict; (7.0, 10) out beyond lower side line -> lower wins.
- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement** with numpy/cv2 (`cv2.getPerspectiveTransform`, `cv2.perspectiveTransform`); a degenerate homography (cv2 error / non-finite) -> None.
- [ ] **Step 4: Run, expect PASS.**
- [ ] **Step 5: Commit** `feat(outcomes): per-rally evidence (last hitter, landing, next server)`.

### Task 3: Rule cascade (`outcomes/rules.py`)

**Files:** Create `badminton_analysis/outcomes/rules.py`; Test `tests/test_outcome_rules.py`

**Interfaces:**
- Consumes: the dict from `landing_verdict` (or None), E1 side or None, E3 side or None.
- Produces: `decide(landing, last_hitter, next_server) -> {"winner_side": side|None, "confidence": float|None, "rule": str}`; rules in spec table order: `no_landing`, `out_hitter_conflict`, `server_conflict`, `landing_and_server` (0.8), `out_and_hitter` (0.6), `single_signal`.

- [ ] **Step 1: Failing tests**, one per table row, plus "out, E1 inconsistent, E3 agrees" -> conflict (row order).
- [ ] **Step 2: FAIL. Step 3: implement. Step 4: PASS.**
- [ ] **Step 5: Commit** `feat(outcomes): rule cascade that abstains on conflict`.

### Task 4: Reconciliation (`outcomes/reconcile.py`)

**Files:** Create `badminton_analysis/outcomes/reconcile.py`; Test `tests/test_outcome_reconcile.py`

**Interfaces:**
- Consumes: `badminton_analysis.library.athletes` (`load`, `resolve_record`, `parts_of`, `record_format`).
- Produces: `reconcile(athletes_data, stem, part_docs: {stem: doc|None}) -> {"status", "scope", "expected", "claimed", "parts"}` and `match_parts(athletes_data, stem) -> [(part_stem, sides_swapped)]` (primary first, `(stem, False)` alone for an unlinked video, `[]` for no record).
- Rules exactly as spec 5.4. A part's doc counts as complete when `summary.labelled == summary.entries`; a missing doc is incomplete. Swapped parts exchange `points_won` before summing. `scope` `sides` for one set, `total` for more; doubles or no `result.sets` -> `no_result` (expected/claimed null).

- [ ] **Step 1: Failing tests**: single video consistent / over / short / incomplete; split match with a swapped part summing into the primary frame; part given as stem of the part (resolves the primary); a missing part doc -> incomplete; two-set result -> scope `total`; doubles record -> `no_result`; no record -> `no_result`.
- [ ] **Step 2-4: FAIL, implement, PASS.**
- [ ] **Step 5: Commit** `feat(outcomes): reconcile claimed points with the recorded result`.

### Task 5: Build and CLI (`outcomes/build.py`)

**Files:** Create `badminton_analysis/outcomes/build.py`, `badminton_analysis/outcomes/__main__.py` is not needed (`python -m badminton_analysis.outcomes.build`); Test `tests/test_outcome_build.py`

**Interfaces:**
- Consumes: Tasks 1-4; `insights.loaders.load_run`, `insights.rally.rally_windows`.
- Produces:
  - `OUTCOMES_NAME = "rally_outcomes.json"`
  - `build_document(out_dir, labels_doc, athletes_data, other_parts=None) -> dict` (pure apart from reading `out_dir`; `other_parts` = `{stem: doc|None}` for reconciliation, read from disk when None)
  - `build(out_dir, labels_dir, athletes_path) -> dict | None` (writes atomically; returns the doc; None and a printed note on failure)
  - `rebuild_match(outputs_dir, stem, labels_dir, athletes_path) -> [stem]` (this stem first, then the other parts that have an output dir)
  - CLI `main(argv)`: `python -m badminton_analysis.outcomes.build <out_dir> [--labels-dir data/rally_labels] [--athletes data/athletes.json]`.
- Entry assembly: segments from `rally_segments.json` (`id`, `start_sec`, `end_sec`, frames via fps fallback; summary `failed` -> no segments); owner labels via `attach`; auto evidence per segment (hits only when `hits_meta.source == "learned"`; trajectory from `shuttle_trajectory.json` `trajectory` dict; corners from `metadata.court.corners`; frame height from `metadata.video.height`; static cells from `rally_segments.detection.suppressed_static_shuttle.cells[].point`); `ending_stroke` from `strokes.json` for singles only (last stroke with `frame` in the window at fps, `stroke != "unknown"`); added entries' windows per spec 5.2; owner verdict precedence (side -> owner, conf 1.0; `unsure` -> reviewed, keeps auto; `not_point` -> dropped, counted). Doc keys exactly as spec 5.2; `generated_at` UTC ISO `Z`.

- [ ] **Step 1: Failing tests** with a fixture run dir (`tests/outcome_fixtures.py`: helpers writing `rally_segments.json`, `hits.json`, `strokes.json`, `metadata.json`, `shuttle_trajectory.json` into `tmp_path`): owner labels win; `not_point` dropped and counted; added point entry with window and ending stroke; auto claim from a synthetic landing + next server; `test_build_minimal_run` (only `rally_segments.json`) -> all unknown, coverage 0.0; no segments and no labels -> entries 0, coverage null; doubles -> `ending_stroke` null; corrupt labels file -> doc written, labels ignored; written file parses and `build` leaves no tmp file; `rebuild_match` rebuilds both parts and their reconciliation agrees.
- [ ] **Step 2-4: FAIL, implement, PASS.**
- [ ] **Step 5: Commit** `feat(outcomes): build rally_outcomes.json from labels and evidence`.

### Task 6: Pipeline hook and insight reader

**Files:**
- Modify: `badminton_analysis/system.py` (end of `process_video`, after `_run_doubles_stage`), add `_write_rally_outcomes()`
- Create: `badminton_analysis/insights/outcomes.py`
- Modify: `badminton_analysis/insights/loaders.py` (`Run.rally_outcomes`, read in `load_run`)
- Test: `tests/test_outcome_insight.py`, `tests/test_outcome_system_hook.py`

**Interfaces:**
- `system._write_rally_outcomes()`: `build(self.save_dir, PROJECT_ROOT/data/rally_labels, PROJECT_ROOT/data/athletes.json)` where the root is resolved as `Path(__file__).resolve().parents[1]`; any exception printed, never raised.
- `rally_outcomes_insight(run) -> envelope` per spec 5.5 (`no_outcomes`, `outcomes_incomplete`).

- [ ] **Step 1: Failing tests**: reader unavailable for missing / schema 2 / non-dict `rallies`; partial for coverage < 1; ok for full coverage + consistent; hook calls build with the save dir and swallows an exception (monkeypatch `build` to raise; call the method on a bare object via `BadmintonAnalysisSystem._write_rally_outcomes(stub)`).
- [ ] **Step 2-4: FAIL, implement, PASS.**
- [ ] **Step 5: Commit** `feat(outcomes): write rally outcomes at the end of match analysis; insight reader`.

### Task 7: Label routes (`web/rally_labels_routes.py`) and app wiring

**Files:**
- Create: `badminton_analysis/web/rally_labels_routes.py`
- Modify: `app.py` (register; `RALLY_LABELS_DIR = PROJECT_ROOT / 'data' / 'rally_labels'`, `_RALLY_LABELS_LOCK`; `all`-scope cleanup removes the label file, never fatal)
- Test: `tests/test_app_rally_labels.py`

**Interfaces:**
- `register(app, *, labels_dir, athletes_path, outputs, lock, safe_stem, job_active)`; all getters called per request; `job_active(stem) -> bool` from `app.jobs` statuses in `_ACTIVE_JOB_STATUSES`.
- `GET /api/rally-labels/<stem>` -> 200 doc; 404 unknown stem; 500 static body when unreadable.
- `PUT /api/rally-labels/<stem>` body `{revision:int, labels:[...]}` -> 200 `{revision, outcomes_rebuilt: [stem]}`; 400 bad body; 404 unknown; 409 stale revision (`{error, revision}`) or unreadable store. Rebuild via `rebuild_match` unless `job_active(stem)` (then `outcomes_rebuilt: []`, `rebuild_skipped: "job_active"`).

- [ ] **Step 1: Failing tests** (client fixture as `tests/test_app_athletes.py`, plus `RALLY_LABELS_DIR` monkeypatched): GET empty; PUT then GET round-trip, revision 1; `test_put_stale_revision_409`; 400 for bad verdict; 404 unknown stem; `test_put_refuses_unreadable_store`; `test_put_during_job_skips_rebuild`; PUT writes `rally_outcomes.json` into the output dir; delete scope `all` removes the label file; static bilingual errors.
- [ ] **Step 2-4: FAIL, implement, PASS.**
- [ ] **Step 5: Commit** `feat(web): rally label routes; rebuild outcomes on save`.

### Task 8: Kestrel UI

**Files:**
- Modify: `static/kestrel/core/api.js` (`endpoints.rallyLabels(stem)`, `api.rallyLabels`, `api.saveRallyLabels`, `api.rallyOutcomes(stem)` via the output route)
- Create: `static/kestrel/views/outcomes_model.js` (pure)
- Modify: `static/kestrel/views/workspace.js` (Point block in `buildSummary`, timeline tint, add-point button, tally + badge, whole-match pooled tally, keyboard), `static/kestrel/core/i18n.js` (en + zh), CSS in `static/kestrel/shell.css` or `widgets.css` following existing classes
- Test: `tests/js/outcomes_model.test.mjs`, extend `tests/js/workspace_mount.test.mjs`

**Interfaces (outcomes_model.js):**
- `labelForSegment(labelsDoc, seg) -> label|null` (same attach rule as Python: `start-1 <= end_sec <= end+1`, nearest end)
- `applyVerdict(labelsDoc, seg, verdict, outcome=null) -> newDoc` (replaces the segment's label; immutable)
- `addPoint(labelsDoc, timeSec, verdict, outcome=null) -> newDoc`
- `segmentState(outcomesDoc, labelsDoc, seg) -> "near"|"far"|"unknown"|"rejected"` plus `{source, auto}`
- `tally(outcomesDoc) -> {near, far, labelled, entries}`; `pooledTally(docs, swapped[])`
- `keyToAction(key) -> {verdict}|{outcome}|null` (n/f/x/u; 1/2/3)
- Behaviour per spec section 7; a 409 reloads labels and shows a notice; save then advance to the next segment.

- [ ] **Step 1: Failing node tests** for each model function (incl. attach parity with Python test cases).
- [ ] **Step 2-4: FAIL, implement, PASS**; then the view wiring and a mount test (Point block renders for a selected rally; clicking Near won calls `saveRallyLabels` with verdict `lower`).
- [ ] **Step 5: Commit** `feat(kestrel): label rally outcomes on the match details page`.

### Task 9: Accuracy script

**Files:** Create `scripts/score_rally_outcomes.py`; Test `tests/test_score_rally_outcomes.py`

**Interfaces:** `score(out_dirs, labels_dir) -> {"owner_points", "auto_claims", "correct", "precision", "coverage", "per_evidence": {"landing": {...}, "last_hitter": ..., "next_server": ...}, "per_video": [...]}`; auto results come from a build with **no labels** (`build_document(out_dir, labels.empty(), athletes)`), compared to the owner's side verdicts attached by time. `last_hitter` accuracy = "last hitter won" rate (descriptive, not a predictor). Prints JSON; `--out` writes it.

- [ ] **Step 1-4:** failing test on a fixture with two owner points (one auto-correct, one abstained) -> precision 1.0, coverage 0.5; implement; pass.
- [ ] **Step 5: Commit** `feat(scripts): score automatic rally outcomes against owner labels`.

### Task 10: Backfill, verification, ship

- [ ] Backfill the DJI runs: `python -m badminton_analysis.outcomes.build outputs/<stem>` for each run with `rally_segments.json` (outputs are gitignored; this only writes local files).
- [ ] Full pytest and node suites; record counts.
- [ ] Live check on the worktree preview server (port 5071 per project memory): label a few segments on 0009, see tint / tally / badge, reload persists, `rally_outcomes.json` updated.
- [ ] qa-reviewer pass over the branch; fix findings.
- [ ] Update `.ai/STATE.md` row (verification, measured accuracy or "owner labels pending"); commit; push; PR; merge (owner pre-approved).
