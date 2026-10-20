# Kestrel Telemetry UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the telemetry-style Kestrel shell and the insights, athlete and jobs backends it needs, with every dashboard number computed from real analysis output.

**Architecture:** Pure insight functions over loaded run files (`badminton_analysis/insights/`), cached per video in an `insights.json` sidecar and aggregated per scope; an athlete/match store following `library/tags.py`; Flask Blueprints registered by `app.py`; a native-ES-module front end under `static/kestrel/` (no build step) with DOM-free logic tested by `node --test`.

**Tech Stack:** Python 3.11, Flask, pytest; vanilla ES modules, CSS custom properties, `node --test` (Node 24 is installed); no new dependencies.

**Spec:** `docs/superpowers/specs/2026-10-19-kestrel-telemetry-ui-design.md` (read it first; this plan argues from it). Visual reference: https://claude.ai/artifact/PDB7uzMNymdM866s1Z12bh

**Plan form (deviation, owner-approved scale):** tasks give exact files, interfaces, algorithms (in the spec) and the concrete assertions each test must make, not every line of code. Implementers write the tests first from these assertions.

## Global Constraints

- Python: use the shared venv `D:/Dev/Claude/Projects/good-badminton/Good-badminton/.venv/Scripts/python.exe`; run tests as `PYTHONUTF8=1 <venv python> -B -m pytest -q -p no:cacheprovider --basetemp <unique dir> <paths>` from the worktree root.
- No new Python or JS dependencies; no build step; no CDN or network at runtime (fonts and photos are vendored under `static/`).
- Error bodies from routes are static bilingual strings `中文 / English`; never an exception text or filesystem path.
- Every new user-visible string exists in `zh` and `en`; all user-derived text reaches HTML through an escaper or `textContent`.
- `main` is protected: never merge, rebase onto, or push to `main`. Work on `claude/kestral-sports-ui-design-015bc4`. Builders **do not commit**; the lead commits at task gates.
- Touch only the files listed in your task. `app.py` is edited only in Tasks 2 and 6 (registration lines and getters).
- A malformed or absent run file never fails a request; the insight is `unavailable` with a reason (spec 5.1).
- Court convention: `x` 0-6.1 m, `y` 0-13.4 m, net `y = 6.7`, `upper` is `y < 6.7` (spec section 3).
- `prefers-reduced-motion: reduce` renders every view fully at `p = 1` with no animation.
- Existing behaviour that must not change: `/api/stats`, `/api/videos`, tags routes, `_rally_summary` results, legacy library/wizard/results screens.

## Review Focus

1. **Stem names with spaces, dots or non-ASCII** (`Dji 20260919100627 0004 D`, `YTDown.com_..._1080p`): must round-trip through `/api/insights/video/<stem>`, `/api/matches/<stem>`, the hash router and `/api/output/...` URLs. Tested in Tasks 2, 6, 8, 11.
2. **Large or ragged `detections.jsonl`** (50k lines, frames with `players: {}`, `court: null`, one side missing): never raises, finishes under 3 s, second call served from the sidecar. Tested in Tasks 4, 5.
3. **Partially analysed videos** (no `hits.json`, no `strokes.json`, only `detections.jsonl`; or only a posture run): every insight degrades to `partial`/`unavailable` with a reason code, no 500. Tested in Tasks 3-6.
4. **Empty states:** empty library, athlete with no matches in the range, squad with one athlete (no benchmark). Overview returns well-formed zeros/`unavailable`, not errors. Tested in Tasks 5, 6, 9.
5. **Concurrent and corrupt athlete store:** parallel PUTs lose nothing; a corrupt file is backed up not discarded; an unreadable file is never overwritten. Tested in Tasks 1, 2.
6. **Rapid scope changes and reduced motion:** a new reveal cancels the previous clock (no stale frame writes); reduced motion paints final values at once. Tested in Tasks 7, 9.

---

## File Structure

```
badminton_analysis/library/athletes.py            T1   store (pure)
badminton_analysis/web/__init__.py                T2
badminton_analysis/web/athletes_routes.py         T2   blueprint
badminton_analysis/insights/{__init__,envelope,loaders,dates,rally,strokes,technique}.py   T3
badminton_analysis/insights/movement.py           T4   movement, recovery, pull
badminton_analysis/insights/{sidecar,aggregate,findings}.py                                T5
badminton_analysis/web/insights_routes.py         T6   insights + jobs blueprint
tests/{test_library_athletes,test_app_athletes,insights_fixtures,test_insights_*,test_app_insights,test_app_jobs,test_kestrel_js}.py
tests/js/*.test.mjs
static/kestrel/ ... (spec 6.1)                    T7-T11
```

---

### Task 1: Athlete and match store

**Files:**
- Create: `badminton_analysis/library/athletes.py`
- Test: `tests/test_library_athletes.py`

**Interfaces:**
- Produces (all pure, dict in, new dict out unless noted):
  - `SCHEMA_VERSION = 1`, `class StoreCorrupt(ValueError)`, `class StoreUnreadable(OSError)`
  - `load(path) -> dict` (never raises; `{}`-shaped default `empty()` on missing/corrupt/unreadable), `load_for_update(path) -> dict` (raises the two errors), `save(data, path) -> None` (atomic, Windows retry), `backup_corrupt(path) -> str`, `empty() -> dict`
  - `normalise_name(raw) -> str | None`
  - `add_athlete(data, name, squad_id=None) -> (data, athlete_id)` raises `ValueError("duplicate")`/`("invalid")`/`("unknown_squad")`
  - `update_athlete(data, athlete_id, *, name=_UNSET, squad_id=_UNSET) -> data` raises `KeyError`
  - `delete_athlete(data, athlete_id) -> (data, rewritten_count)` (sides referencing it become `{"name": <old name>}`)
  - `add_squad / update_squad / delete_squad` (same shapes; deleting clears `squad_id` on athletes)
  - `validate_match(record) -> (clean_record | None, error_code | None)`; `set_match(data, stem, record) -> data` (a `None` or all-null record deletes the key); `get_match(data, stem) -> dict`
  - `side_athlete(data, record, side) -> {"athlete_id": str|None, "name": str|None}`

- [ ] **Step 1: Write failing tests** covering: empty store shape; add athlete returns id matching `^a_[0-9a-f]{8}$`; duplicate name case-insensitively -> `ValueError("duplicate")`; name `"  "`, 61 chars, and `"a\x00b"` -> invalid; `delete_athlete` rewrites `upper: {"athlete_id": X}` to `{"name": "J. Tanaka"}` and returns count 1; deleting a squad clears members' `squad_id`; `validate_match` accepts `{"date": "2026-10-11", "upper": {"name": "S. Ito"}, "lower": {"athlete_id": X}, "result": {"winner": "upper", "sets": [[18,21],[21,19]]}}`, rejects `date "2026-02-30"`, winner `"left"`, 6 sets, set `[100, 0]`, a side with both `athlete_id` and `name`, unknown `athlete_id`; `set_match` with `None` deletes; `save`/`load` round-trip a unicode name; a corrupt file: `load` -> empty, `load_for_update` raises `StoreCorrupt`, `backup_corrupt` returns a path that exists and holds the original bytes; a directory path as the store: `load_for_update` raises `StoreUnreadable`; two threads each `load_for_update -> add_athlete -> save` under an external lock leave both athletes (documents the caller-lock contract).
- [ ] **Step 2: Run** `pytest tests/test_library_athletes.py -q`. Expected: FAIL (module missing).
- [ ] **Step 3: Implement** `athletes.py` mirroring `library/tags.py` helpers (`_classify`, atomic save with `os.replace` retry 5 x 50 ms, logged-once unreadable paths). ids: `"a_" + uuid4().hex[:8]`, `"s_" + ...`.
- [ ] **Step 4: Run** the tests. Expected: PASS.

---

### Task 2: Athlete routes

**Files:**
- Create: `badminton_analysis/web/__init__.py` (empty), `badminton_analysis/web/athletes_routes.py`
- Modify: `app.py` (add `ATHLETES_PATH = PROJECT_ROOT / 'data' / 'athletes.json'`, `_ATHLETES_LOCK = threading.Lock()`, and one `athletes_routes.register(app, ...)` call after `app = Flask(...)`)
- Test: `tests/test_app_athletes.py`

**Interfaces:**
- Consumes: Task 1 functions; `app._safe_stem(name) -> str | None`.
- Produces: `register(app, *, store_path, lock, safe_stem)` where the first three are zero-arg-callable getters or values resolved at request time (`store_path()` returns a `Path`, `lock()` a `threading.Lock`, `safe_stem(name)` as in app). Routes per spec section 4. The fixture pattern is `monkeypatch.setattr(webapp, "ATHLETES_PATH", tmp)` so the getter must read `webapp.ATHLETES_PATH` each request: `register(app, store_path=lambda: ATHLETES_PATH, lock=lambda: _ATHLETES_LOCK, safe_stem=lambda n: _safe_stem(n))`.

- [ ] **Step 1: Write failing tests** (Flask test client, tmp videos/outputs/data as in `tests/test_app_tags.py`): `POST /api/athletes {"name":"J. Tanaka"}` -> 201 with id; again -> 409; `{"name":""}` -> 400 with body matching `中文 / English` and containing no `tmp` path; `GET /api/athletes` lists with `matches: 0` then `1` after `PUT /api/matches/clip {"lower":{"athlete_id": id}}`; squads: create, assign via `PUT /api/athletes/<id> {"squad_id": sid}`, `GET` shows `squads[].athletes == 1`; delete athlete -> `GET /api/matches/clip` shows `lower == {"name":"J. Tanaka"}`; `PUT /api/matches/nope` (unknown stem) -> 404; stem `"Dji 20260919100627 0004 D"` round-trips; `PUT` with `{}` removes the record and `GET` returns `{}`; corrupt store file: `POST` succeeds and `athletes.json.corrupt-*` exists; store path is a directory: `POST` -> 500, directory untouched; 20 threads `POST` distinct names -> `GET` returns 20.
- [ ] **Step 2: Run** `pytest tests/test_app_athletes.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement** the blueprint and the `app.py` lines.
- [ ] **Step 4: Run** the new tests plus `tests/test_app_tags.py tests/test_app_library.py`. Expected: PASS.

---

### Task 3: Insight envelope, loaders, dates, rally, strokes, technique

**Files:**
- Create: `badminton_analysis/insights/{__init__,envelope,loaders,dates,rally,strokes,technique}.py`, `tests/insights_fixtures.py`
- Modify: `app.py` only to make `_rally_summary` call the shared helper (`from badminton_analysis.insights.rally import summarise_rally_payload`) with identical results (existing `tests/test_app_rally_summary.py` is the guard).
- Test: `tests/test_insights_basic.py`

**Interfaces:**
- Produces:
  - `envelope.py`: `Insight` dataclass-like dict helpers: `ok(value, coverage=None)`, `partial(value, code, params=None, coverage=None)`, `unavailable(code, params=None)`; each returns the plain dict of spec 5.1.
  - `loaders.py`: `@dataclass Run(stem, out_dir, metadata, detections, hits, strokes, tech_reports, tech_summary, rallies_payload, notes)`, `load_run(out_dir) -> Run`. `detections` is a list of `(frame, time_sec, {"upper": (x,y)|None, "lower": (x,y)|None})` tuples; `hits` a list of `(time_sec, frame, hitter)`; unreadable/malformed files become `None` and append a note.
  - `dates.py`: `video_date(stem, video_path=None, out_dir=None) -> (iso_str, "filename"|"mtime")`.
  - `rally.py`: `summarise_rally_payload(payload) -> {count, suppressed, label, signal, degraded, failed}` (the exact existing `_rally_summary` semantics), `rally_insight(run) -> Insight`.
  - `strokes.py`: `strokes_insight(run)`; `technique.py`: `technique_insight(run)`, constants `TECHNIQUES = ["smash","clear","drop","net","lift","drive","serve"]`, `REPORT_TO_TECH = {"high_clear":"clear","drop_shot":"drop","smash":"smash","serve":"serve"}`, `REFERENCE = {"lo":70,"hi":100,"source":"literature"}`.
  - `tests/insights_fixtures.py`: `make_run(tmp_path, stem="clip", *, fps=30.0, frames=0, rallies=None, hits=None, strokes=None, reports=None, signal="shuttle", positions=None) -> Path` writing a synthetic `outputs/<stem>/`.

- [ ] **Step 1: Write failing tests:** `load_run` on an empty dir returns a Run with every field None and no exception; a `detections.jsonl` with a garbage line and a frame lacking `players` is tolerated (garbage skipped, note recorded); `video_date("DJI_20261010090046_0010_D")` -> `("2026-10-10","filename")`, `"IMG_1270"` -> mtime source, `"x_20261340_a"` (month 13) -> mtime; `rally_insight` with `signal "shuttle"` -> ok with `count` and `mean_sec`; `"swing"` -> partial + `signal_swing`, `suppressed` true; `degraded` -> partial + `signal_degraded`; `error` and missing -> unavailable + `no_rallies`; shots-per-rally join counts hits at `start-0.5` and `end+0.5` inclusive and excludes `end+0.6`; `strokes_insight`: 10 strokes with 7 uncertain -> partial + `low_stroke_coverage`, coverage `{n:3, of:10}`; 10 with 2 uncertain -> ok; no file -> unavailable + `no_strokes_file`; `technique_insight`: reports `high_clear x2 (50, 70)`, `serve x1 (80)` on `lower` -> `lower.techniques.clear == {count:2, avg_score:60.0}`, `net.count == 0`, `index == mean(60, 80) == 70.0`, `scored == 2`, status partial + `unscored_technique` with `names` containing `net, lift, drive, smash, drop`; reports on both sides are separated; null `overall_score` ignored; no reports -> unavailable + `no_technique`; `app._rally_summary` results unchanged (existing test file passes).
- [ ] **Step 2: Run** `pytest tests/test_insights_basic.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement** per spec 5.3 (rally rules copied from `app._rally_summary`).
- [ ] **Step 4: Run** `tests/test_insights_basic.py tests/test_app_rally_summary.py`. Expected: PASS.

---

### Task 4: Movement, recovery to base, pull zones

**Files:**
- Create: `badminton_analysis/insights/movement.py`
- Test: `tests/test_insights_movement.py`

**Interfaces:**
- Consumes: Task 3 `Run`, `rally_insight`, envelope helpers.
- Produces: `movement_insight(run, rally) -> Insight`, `recovery_insight(run) -> Insight`, `pull_insight(run) -> Insight`; constants `GRID_ROWS = 11`, `GRID_COLS = 10`, `BASE = {"upper": (3.05, 3.35), "lower": (3.05, 10.05)}`, `RECOVERY_RADIUS_M = 1.0`, `RECOVERY_WINDOW_S = 4.0`, `PULL_GAP_S = 3.0`; helper `cell(side, x, y) -> (row, col) | None`.

- [ ] **Step 1: Write failing tests:** `cell("upper", 0.0, 0.0) == (0, 0)`, `cell("upper", 6.09, 6.69) == (10, 9)`, `cell("lower", 3.05, 6.7) == (0, 4)` (row 0 at the net for both sides), a point 1.6 m outside returns None, 1.0 m outside is clamped to an edge cell; 100 frames all at the upper base -> grid shares sum to 1.0 +/- 1e-3 with the mass in one cell, `samples == 100`, `distance_m == 0`; a player stepping 0.1 m per frame at 30 fps for 30 frames -> `distance_m` about 2.9 and `mean_speed_mps` about 3.0; a 6 m jump between consecutive frames is excluded from distance; under 30 samples -> unavailable + `no_positions`; rally windows given and not degraded -> only in-window frames counted; no windows -> all frames and status partial + `no_rallies`; recovery: a hit at t=1.0 with the player 3 m from base returning inside 1 m at t=1.8 -> found, `median_sec == 0.8`; a player already inside -> 0.0; never returning within 4 s -> not found (excluded from the median, counted in `hits`); 12 found hits -> ok, 5 -> partial + `few_hits`, 2 -> unavailable + `no_hits`, no hits at all -> unavailable + `no_hits`; hits fall back to `strokes.json` frames when `hits.json` is absent; pull: hits A(lower) at 1.0 s then B(upper) at 2.0 s with B at (1.0, 1.0) at that frame -> lower's pull has `n == 1`, cell `(1, 1)`, `front == 1.0`, `left == 1.0`; same-hitter consecutive hits and gaps over 3.0 s are ignored; B position absent within 5 frames -> skipped; a 50,000-frame synthetic run completes in under 3 s.
- [ ] **Step 2: Run** `pytest tests/test_insights_movement.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement** per spec 5.3 (use `bisect` on frame numbers for nearest-frame lookups).
- [ ] **Step 4: Run** the tests. Expected: PASS.

---

### Task 5: Sidecar, aggregation, findings

**Files:**
- Create: `badminton_analysis/insights/{sidecar,aggregate,findings}.py`
- Test: `tests/test_insights_sidecar.py`, `tests/test_insights_aggregate.py`

**Interfaces:**
- Consumes: Tasks 1, 3, 4.
- Produces:
  - `sidecar.INSIGHTS_VERSION = 1`; `compute_video(stem, out_dir, video_path=None) -> dict` (spec 5.2 sidecar document; never raises; reads `insights.json` when `version` and every `inputs` mtime match, else recomputes and writes atomically, ignoring `OSError` on write).
  - `aggregate.overview(units_source, athletes_store, *, athlete=None, squad=None, range_="30d", running_stems=frozenset()) -> dict` where `units_source` is `list[{"stem": str, "insights": dict}]` (the caller supplies sidecar dicts). Raises `LookupError("athlete")`/`LookupError("squad")` for unknown ids and `ValueError("range")` for a bad range. Returns the spec 5.4 response.
  - `findings.build_findings(overview) -> list[{"code","severity","params"}]`.

- [ ] **Step 1: Write failing tests:** sidecar: first call writes `insights.json` and a second call does not recompute (assert via a monkeypatched `technique_insight` call counter); touching `strokes.jsonl` (new mtime) recomputes; a read-only output dir returns the result without raising; a malformed existing `insights.json` is replaced; one insight raising (monkeypatch `rally_insight` to raise) yields that key `unavailable` + `no_data` and the others intact; a directory with only `detections.jsonl` yields valid shape. Aggregate (hand-built `units_source` dicts): `athlete` scope picks the right side per stem (athlete on `upper` in stem A and `lower` in stem B uses each stem's own side); `range="7d"` anchors on the latest selected date; `season` starts Jan 1 of that year; `all` unbounded; unknown athlete -> `LookupError`; `range="x"` -> `ValueError`; radar axis score is the count-weighted mean (two units: smash (count 1, 60) and (count 3, 80) -> 75.0); `index` is the mean of scored axes; trend points sorted by date; `delta` null with one point, `last - first` with two; `match_win_rate`: 2 wins of 3 recorded -> rate 0.667, coverage `{3, 3}`, none recorded -> unavailable + `no_results_entered`; `rally_win_rate` and `unforced_errors` always unavailable + `no_point_outcome`; benchmark unavailable + `no_benchmark` without a squad, ok with another athlete's sessions; stems in `running_stems` get `status "processing"`; `needs_review` true when strokes coverage is under 0.5; an empty selection returns well-formed shape (stems 0, every insight unavailable + `no_data`); findings: an axis score 66 -> `technique_below_reference` with `lo` 70 listed first; strongest axis present; at most 6; severity order watch, good, info; `techniques_unscored` lists exactly the three unscored names when only four are scored.
- [ ] **Step 2: Run** `pytest tests/test_insights_sidecar.py tests/test_insights_aggregate.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement** per spec 5.2-5.4.
- [ ] **Step 4: Run** the tests. Expected: PASS.

---

### Task 6: Insights and jobs routes

**Files:**
- Create: `badminton_analysis/web/insights_routes.py`
- Modify: `app.py` (one `insights_routes.register(app, ...)` call)
- Test: `tests/test_app_insights.py`, `tests/test_app_jobs.py`

**Interfaces:**
- Consumes: Tasks 1, 2, 5; `app.VIDEOS`, `app.OUTPUTS`, `app.jobs`, `app.ATHLETES_PATH` via getters.
- Produces: `register(app, *, videos, outputs, athletes_path, athletes_lock, jobs)` (callables resolved per request); routes `GET /api/insights/overview`, `GET /api/insights/video/<stem>`, `GET /api/jobs`.

- [ ] **Step 1: Write failing tests:** overview on an empty library -> 200 with `scope.stems == 0`; a library of two synthetic runs (via `insights_fixtures.make_run` plus matching tiny video files) returns kpis with the documented keys and `rally_win_rate.status == "unavailable"`; `?athlete=nope` -> 404 bilingual; `?range=1y` -> 400; `?athlete=<id>` filters; the stem `"Dji 20260919100627 0004 D"` works through `/api/insights/video/Dji%2020260919100627%200004%20D`; unknown stem -> 404; a stem with outputs but corrupt `detections.jsonl` -> 200 with `movement` unavailable; the response never contains the tmp path string; jobs: an in-memory job with a `proc` object serialises without `proc`; a disk-only `progress.json` updated now appears as `detached`, one updated 11 minutes ago does not; newest first; `GET /api/jobs` with none -> `[]`.
- [ ] **Step 2: Run** `pytest tests/test_app_insights.py tests/test_app_jobs.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement** the blueprint (enumerate stems that have any of `detections.jsonl`, `strokes.json`, `strokes.jsonl`, `technique_summary.json`, `rally_segments.json`; skip stems whose sidecar compute raises).
- [ ] **Step 4: Run** new tests plus `tests/test_app_library.py tests/test_app_tags.py tests/test_app_progress.py`. Expected: PASS.

---

### Task 7: Tokens, fonts, ground assets, CSS, page skeleton

**Files:**
- Create: `static/kestrel/tokens.css`, `static/kestrel/shell.css`, `static/kestrel/widgets.css`, `static/fonts/SpaceGrotesk-Variable.woff2`, `static/fonts/InstrumentSerif-Regular.woff2`, `static/fonts/InstrumentSerif-Italic.woff2`, `static/img/ground/court-aerial-night.jpg`, `static/img/ground/court-racket-top.jpg`, `static/img/ground/CREDITS.md`
- Modify: `kestrel.html` (stylesheet order, `<div id="app">`, module script tag; keep a `<noscript>` line)
- Source files for the photos (already downloaded, copy them): `C:/Users/seafoodrice/AppData/Local/Temp/claude/D--Dev-Claude-Projects-good-badminton-Good-badminton--claude-worktrees-kestral-sports-ui-design-015bc4/68847a6b-ad19-494c-bbb5-7bc70f58e037/scratchpad/stock/{unsplash-court-aerial.jpg,pexels-court-top-view.jpg}`
- Fonts: fetch the Latin woff2 files that Google Fonts serves for Space Grotesk (variable, weights 400-700) and Instrument Serif (regular and italic), both SIL OFL, via the Google Fonts CSS API; record the source URL and license in `static/fonts/FONTS.md`.

**Interfaces:**
- Produces: CSS custom properties of spec 6.3 (night default, `[data-theme="day"]`), `@font-face` for the three families, legacy-variable aliases, the classes `.hud .scan .ground .rail .topbar .page .gap-card .pill-slider .mono .serif .chip` used by Tasks 8-11, keyframes `kUp kFade kIn kPop kLive kFlow kRing kTravel`, and the reduced-motion block.

- [ ] **Step 1: Write the failing check** `tests/js/css_tokens.test.mjs`: reads `tokens.css`, asserts every token name in spec 6.3 is defined in both themes, that each legacy alias (`--paper --card --border --fill --muted --faint --sidebar-bg`) is defined, that `@font-face` names `Space Grotesk`, `Instrument Serif`, `IBM Plex Mono` and the font files exist on disk, and that a `prefers-reduced-motion` block exists. A second test computes WCAG contrast for `--ink2` and `--ink3` on `--surface` for both themes and asserts >= 4.5.
- [ ] **Step 2: Run** `node --test tests/js/css_tokens.test.mjs`. Expected: FAIL.
- [ ] **Step 3: Implement** the CSS and assets; legacy `kestrel.css` is left untouched and loaded before `tokens.css`.
- [ ] **Step 4: Run** the node test. Expected: PASS.

---

### Task 8: Core modules (DOM-free logic)

**Files:**
- Create: `static/kestrel/core/{geometry,format,scope,router,i18n,motion,prefs,store,api}.js`
- Test: `tests/js/{geometry,format,scope,router,i18n,motion}.test.mjs`

**Interfaces (all ES module named exports):**
- `geometry.js`: `radarPoint(i, value, {cx, cy, r, n=7}) -> [x, y]`; `radarPolygon(axes, scale, opts) -> "x,y x,y"` skipping axes whose `score == null`; `ringPolygon(fraction, opts)`; `trendPath(points, {w, h, pad, yMin, yMax}) -> "M..L.."`; `gridCells(grid, {reveal}) -> [{row, col, share, alpha}]` where `alpha = share / maxShare * reveal`; `arcDash(pct, circumference) -> "a b"`; `clamp(x,a,b)`.
- `format.js`: `fmtNumber(v, decimals)`, `fmtPercent(rate)`, `fmtDuration(seconds) -> "m:ss"`, `fmtDate(iso, lang) -> string`, `ago(seconds) -> "m:ss"`, `escHTML(s)`.
- `scope.js`: `rangeBounds(range, referenceIso) -> {from, to}` (same rules as spec 5.4), `pickRange(points, range)`.
- `router.js`: `parseHash(hash) -> {name, params, query}`, `formatHash(name, params, query) -> string` with percent-encoding of stems, defaulting unknown hashes to `{name:"overview"}`.
- `i18n.js`: `STRINGS = {zh:{...}, en:{...}}`, `t(key, lang, params)`, `reasonText(reason, lang)`, `findingText(finding, lang) -> {title, detail}`, `FINDING_CODES`, `REASON_CODES`.
- `motion.js`: `ease(x)` (quart ease-out), `local(p, i)` -> `ease(clamp((p - i*0.06)/0.45, 0, 1))`, `createClock({duration, reduced, onFrame, raf, now})` returning `{start, cancel}`; `start` cancels any running clock; with `reduced` it calls `onFrame(1)` once synchronously.
- `prefs.js` (localStorage wrapper that tolerates a throwing storage), `store.js` (tiny observable `createStore(initial) -> {get, set, subscribe}`), `api.js` (`getJSON(url)` with timeout/AbortController and bilingual error mapping, endpoint helpers for every route in the spec).

- [ ] **Step 1: Write failing tests:** `radarPoint(0, 100, {cx:160, cy:150, r:105})` is `[160, 45]`; axis 0 at value 50 is `[160, 97.5]`; `radarPolygon` with one null score omits that vertex and keeps order; `trendPath` of two points starts `M` and has one `L`, y of the max point is `pad`; `gridCells` alpha of the max cell equals `reveal`; `arcDash(50, 100)` is `"50 100"`; `rangeBounds("30d", "2026-10-18")` -> from `2026-09-19`; `season` -> `2026-01-01`; `all` -> from null; `fmtDuration(125)` is `"2:05"`; `escHTML('<a href="x">&\'')` has no raw `<>"'&`; router: `parseHash("#/matches/Dji%2020260919100627%200004%20D")` -> `{name:"matches", params:{stem:"Dji 20260919100627 0004 D"}}`, `formatHash("matches", {stem:"a b/c"})` round-trips, `"#/nonsense"` -> overview, `"#/overview?athlete=a_1&range=7d&lens=technique"` parses the query; i18n: `Object.keys(STRINGS.zh)` equals `Object.keys(STRINGS.en)` (sorted), every `FINDING_CODES` and `REASON_CODES` entry has a string in both languages, `findingText({code:"technique_below_reference", params:{technique:"drop", score:66, lo:70}}, "en").title` contains `66` and `70`; motion: a fake raf/now clock reaches `p = 1` at the duration and a second `start` before completion stops the first (no frames from the first after the second begins), `reduced` yields exactly one frame with `p = 1`; prefs: with a storage whose `getItem` throws, `get` returns the default.
- [ ] **Step 2: Run** `node --test tests/js`. Expected: FAIL.
- [ ] **Step 3: Implement** the modules.
- [ ] **Step 4: Run** `node --test tests/js`. Expected: PASS.

---

### Task 9: Widgets and the Overview view

**Files:**
- Create: `static/kestrel/widgets/{frame,kpi,radar,trend,courtmap,findings,matches,pipeline,gap}.js`, `static/kestrel/views/overview.js`
- Test: `tests/js/widgets_smoke.test.mjs` (pure parts only: view-model builders)

**Interfaces:**
- Consumes: Task 8 modules; Task 7 CSS classes; the spec 5.4 overview response.
- Produces: each widget module exports `build(data, ctx) -> {el, update(p)}` where `ctx = {lang, theme, onOpenMatch(stem), onFocusAxis(id), requestReveal()}`; `update(p)` mutates attributes/styles in place (never `innerHTML`); `views/overview.js` exports `mount(container, {query, store, router}) -> {destroy()}`. A pure `viewModel(overview, lens, lang) -> {tiles, radar, trend, findings, court, matches}` is exported from `views/overview.js` and tested.

- [ ] **Step 1: Write failing tests (`viewModel`):** the Overview lens yields tiles `technique_index, match_win_rate, avg_rally, rally_win_rate` in that order, the last with `kind: "gap"` and a reason string; Technique lens tiles are `strongest, weakest_scored, inside_reference, techniques_scored`; Tactics lens `recovery, net_share, pull_rear, unforced_errors` (last `gap`); radar axes with `score null` are marked `unscored: true` and carry their `count`; the trend model has `reference` 70-100 only on the radar and a `benchmark` null when unavailable; a `partial` insight carries a `tag` (e.g. `"4 OF 7"`, `"EST."`); an overview with `stems: 0` yields `empty: true`; every tile/finding string resolves in both languages.
- [ ] **Step 2: Run** `node --test tests/js/widgets_smoke.test.mjs`. Expected: FAIL.
- [ ] **Step 3: Implement** the widgets and view to match the canvas Dashboard board (HUD frame, scan line while local progress is in (0,1), count-up numbers, ring/line draw with `pathLength = 1`, radar opening from the centre, court-map cell bloom, findings slide-in, trend hover crosshair, slider pills, customize mode with show/hide and up/down reorder in `kestrel_layout_v1`). Cancel the previous clock on every scope change.
- [ ] **Step 4: Run** `node --test tests/js`. Expected: PASS.

---

### Task 10: Shell, router wiring, legacy integration

**Files:**
- Create: `static/kestrel/main.js`, `static/kestrel/shell/{rail,topbar,palette,peek,transition,ground}.js`, `static/kestrel/views/legacy.js`
- Move (git mv) and edit minimally: `static/kestrel.js` -> `static/kestrel/legacy/kestrel-legacy.js` (spec 6.6)
- Modify: `tests/test_app_library.py` (read the new path)
- Test: `tests/test_kestrel_js.py`, `tests/js/palette_index.test.mjs`

**Interfaces:**
- Consumes: Tasks 7-9.
- Produces: `main.js` mounts the shell into `#app`, starts the router, routes: overview, matches, workspace, players, pipeline, library, new, results. `shell/palette.js` exports a pure `buildIndex({pages, athletes, videos, actions, lang}) -> [{kind, label, hint, route}]` and `filterIndex(index, query)`. `shell/transition.js` exports `navigateWithTransform(anchorEl, hashTarget, {reduced})`. `legacy.js` exports `mount(container, route)` calling the legacy `mountLibrary/mountNew/mountResults`.

- [ ] **Step 1: Write failing tests:** `buildIndex` returns pages, athletes, matches and actions with localized labels; `filterIndex(index, "ito")` is case-insensitive and matches label, kind and hint; empty query returns all; no match returns `[]`; `tests/test_kestrel_js.py` runs `node --test tests/js` via `subprocess` (skip if `node` missing) and asserts exit 0; `test_app_library.py::test_kestrel_hedges_the_unvalidated_shuttle_count` reads `static/kestrel/legacy/kestrel-legacy.js` and still passes.
- [ ] **Step 2: Run** the tests. Expected: FAIL.
- [ ] **Step 3: Implement** the shell to match the canvas (rail with sliding marker and persisted collapse, bottom tab bar under 720 px, top bar scope pickers and range slider, palette, peek, container transform, ground layer). Legacy edits: remove its own sidebar rendering and `window.Kestrel` init, replace `setScreen(name)` with `onNavigate(route)`, keep every other function as is.
- [ ] **Step 4: Run** `pytest tests/test_kestrel_js.py tests/test_app_library.py -q` and `node --test tests/js`. Expected: PASS. Then start the app (`.claude/launch.json`, `good-badminton`) and load `/kestrel`: the shell renders and `#/library` shows the unchanged library.

---

### Task 11: Matches, Workspace, Players, Pipeline views

**Files:**
- Create: `static/kestrel/views/{matches,workspace,players,pipeline}.js`
- Test: `tests/js/views_model.test.mjs`

**Interfaces:**
- Consumes: Tasks 8-10; endpoints in the spec; `/api/output/<stem>/{rally_segments.json,strokes.json,metadata.json}`, `/api/technique/<stem>`, `/api/output/<stem>/detect_<stem>_h264.mp4`.
- Produces: each view exports `mount(container, ctx) -> {destroy()}` plus pure model builders: `workspace.js` `buildWorkspaceModel({rallies, strokes, techReports, fps, insights}) -> {rallies:[{id,start,end,len,strokeCount}], strokes:[{i,time,hitter,label,uncertain,score|null,rallyId|null}], summary}`; `matches.js` `matchRows(overview)`, `validateRecordForm(form) -> {record|null, errors}`; `players.js` `groupBySquad(athletes, squads)`.

- [ ] **Step 1: Write failing tests:** a stroke at `frame/fps` inside rally 2 is assigned `rallyId 2`; outside any rally -> null; techReports join by `contact_frame` within 3 frames of a stroke frame and by `player_side == hitter`; a swing-signal rally set sets `summary.label` to `"segments"` and `summary.suppressed` true; no outcome field exists anywhere in the model (assert no `won`/`lost` keys); `validateRecordForm` rejects sets like `"21-"`, accepts `"21-18, 19-21, 21-15"`, requires a winner when sets are present, and maps blank sides to null; `groupBySquad` puts unassigned athletes under a null squad and sorts names case-insensitively.
- [ ] **Step 2: Run** `node --test tests/js/views_model.test.mjs`. Expected: FAIL.
- [ ] **Step 3: Implement** the four views to match the canvas Match workspace and Navigation boards (container transform from a matches row into the workspace; stroke selection seeks the video; match-record editor saves with `PUT /api/matches/<stem>` and refetches the overview).
- [ ] **Step 4: Run** `node --test tests/js`. Expected: PASS.

---

### Task 12: Integration, browser verification, ship

**Files:**
- Create: `.ai/workstreams/kestrel-telemetry-ui.md`
- Modify: `docs/superpowers/specs/2026-10-19-kestrel-telemetry-ui-design.md` (section 10 "As built"), `.ai/PROJECT_STATUS.md` only if the owner-integration rules allow (leave to the owner otherwise)

- [ ] **Step 1: Link real data** for verification only: `outputs` and `videos` junctions in the worktree pointing at the main checkout's (both gitignored). Do not link `data/`.
- [ ] **Step 2: Full suite:** `pytest` (background, about 10 minutes) plus `node --test tests/js`. Expected: previous `1246 passed, 3 skipped` plus the new tests, no failures.
- [ ] **Step 3: Browser pass** per spec section 8 on the real runs (Dji `0010`, Axelsen, the smaller Dji clips): record observations and fix defects with regression tests.
- [ ] **Step 4: Review:** a fresh `qa-reviewer` pass over the branch diff; fix findings.
- [ ] **Step 5: Ship:** workstream file, logical commits (Co-Authored-By trailer), push `claude/kestral-sports-ui-design-015bc4`, open the PR against the fork's `main`; leave the local server running the new code on the project's configured port. The owner merges.
