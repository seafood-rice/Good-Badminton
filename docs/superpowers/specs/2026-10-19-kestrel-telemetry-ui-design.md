# Kestrel telemetry UI: design and insights service

**Date:** 2026-10-19 · **Branch:** `claude/kestral-sports-ui-design-015bc4` · **Status:** approved by the owner
(design canvas v11 approved; scope, athlete model and architecture chosen in session).

Design canvas (private): https://claude.ai/artifact/PDB7uzMNymdM866s1Z12bh — six boards: Dashboard,
Match workspace, Rail component, Foundations, Navigation, Widgets. The canvas is the visual reference
for every front-end task; where this document and the canvas disagree, this document wins.

## 1. Goal

Replace Kestrel's three-screen shell with a performance-science console (dashboard, match workspace,
players, pipeline) in a night-first "telemetry" look with cinematic motion, **and** build the backend
insights the dashboard needs that do not exist today. Every number on screen comes from real analysis
output, carries the coverage behind it, and a missing insight says so and why. Nothing is a placeholder
number.

## 2. Scope

In scope (owner decision, pieces 1-4):

1. **Shell and design port** — tokens, fonts, rail, scope bar, command palette, peek drawer, container
   transform, ambient photo ground, Overview and Match workspace screens, hash routing, zh/en.
2. **Insights service** — `/api/insights/*`, `/api/jobs`, per-video sidecars.
3. **Athlete and match store** — athletes, squads, match records, athlete assigned to *both* sides
   (`upper`, `lower`) per video.
4. **Derived insights** — court-position grid, distance, recovery to base, pull zones, technique
   profile against the reference range, trend, findings sentences.

Out of scope (rendered as "not yet available" with a reason, never as zero):

- Automatic point outcome: **rally win rate**, won/lost timeline, **unforced errors**.
- Scores for **net, lift, drive** (no reference ranges exist; counts are shown, scores are not).
- Pro-calibrated ranges (owner's earlier decision: detection first, then calibration). The reference
  range is the project's literature-based ideal ranges, and the UI says "reference range".
- Shot-landing on the ground (shuttle track is image-space and mid-air); "pull zones" replace it.
- Compare and Reports pages; free-form drag-resize of widgets.

## 3. Inputs and conventions

Per video stem `S`, `OUTPUTS/S/` may contain (all optional, any may be absent or malformed):

| File | Used for |
|---|---|
| `metadata.json` | `video.fps`, `video.duration_sec`, `court.corners` |
| `detections.jsonl` | per frame: `frame`, `time_sec`, `players.{upper,lower}.court = [x, y]` metres |
| `hits.json` | `hits[{frame, time_sec, hitter, confidence, rally_id}]` (`rally_id` is usually null) |
| `strokes.json` | `strokes[{frame, hitter, stroke, uncertain, ...}]`, `distribution`, `hit_source`, `shuttle_source` |
| `strokes.jsonl` | technique reports `{stroke_type, contact_frame, player_side, overall_score, per_metric}` |
| `technique_summary.json` | fallback counts |
| `rally_segments.json` | `rallies[{id,start_sec,end_sec,degraded}]`, `detection.signal`, `detection.degraded` |

Court coordinates: `x` 0-6.1 m across, `y` 0-13.4 m long, net at `y = 6.7`. `upper` is `y < 6.7`,
`lower` is `y >= 6.7`. Technique vocabulary: reports use `high_clear`, `serve`, `smash`, `drop_shot`;
the seven display techniques are `smash, clear, drop, net, lift, drive, serve`. Mapping:
`high_clear -> clear`, `drop_shot -> drop`, `smash`, `serve`; `net`, `lift`, `drive` have no reports.

Reference range: a technique's mean `overall_score` is "inside the reference range" at `>= 70` (the
existing `scoreHue` good threshold). Reported as `reference: {lo: 70, hi: 100, source: "literature"}`
so a pro-calibrated source can replace it without a UI change.

Rally honesty (existing, must be preserved): `signal: "swing"` => count suppressed, label "segments"
(estimated); `degraded` or `signal: "none"` => suppressed, label "windows"; `signal: "error"` or unreadable
=> unavailable. `badminton_analysis`/`app.py::_rally_summary` is the single reading of this; the insights
package reuses its rules (extracted to a shared pure helper, `app._rally_summary` keeps its behaviour).

## 4. Athlete and match store

Module `badminton_analysis/library/athletes.py`, same pattern as `library/tags.py`: pure functions
(dict in, dict out), atomic save (temp file + `os.replace`, Windows retry), `load`/`load_for_update`,
`StoreCorrupt`/`StoreUnreadable`, backup of a corrupt store on a user edit. File `data/athletes.json`
(gitignored, never under `outputs/`). One module-level lock in `app.py`, `_ATHLETES_LOCK`, serialises every
read-modify-write.

```json
{ "schema_version": 1,
  "squads":   { "s_ab12cd34": { "name": "Senior men's singles" } },
  "athletes": { "a_9f3e21aa": { "name": "J. Tanaka", "squad_id": "s_ab12cd34" } },
  "matches":  { "<stem>": {
      "date": "2026-10-11",
      "upper": { "athlete_id": "a_..." } | { "name": "S. Ito" } | null,
      "lower": { "athlete_id": "a_..." } | { "name": "..." } | null,
      "result": { "winner": "upper" | "lower", "sets": [[18,21],[21,19],[17,21]] } | null } } }
```

Validation: names are trimmed, 1-60 chars, no control characters, unique case-insensitively among
athletes (squads likewise); `date` is `YYYY-MM-DD` and a real date; `winner` is `upper` or `lower`; `sets` is
0-5 pairs of integers 0-99; a side holds either `athlete_id` (must exist) or `name`, never both; a stem must
be a known video (`app._safe_stem`). Deleting an athlete rewrites every side that referenced them to
`{"name": <their name>}` (history is kept, the registry entry goes). Deleting a squad clears `squad_id`.
Corrupt store on read => behaves as empty; on write => backed up as `athletes.json.corrupt-<UTC>` then
replaced (same rule as tags). Unreadable store => never written, 500.

Routes (a Flask Blueprint in `badminton_analysis/web/athletes_routes.py`, registered by `app.py`):

| Route | Result |
|---|---|
| `GET /api/athletes` | `{athletes:[{id,name,squad_id,matches}], squads:[{id,name,athletes}]}`, names sorted |
| `POST /api/athletes` `{name, squad_id?}` | `201 {id,name,squad_id}`; `409` duplicate; `400` invalid |
| `PUT /api/athletes/<id>` `{name?, squad_id?}` (`squad_id: null` clears) | `200`; `404` unknown |
| `DELETE /api/athletes/<id>` | `200 {ok, rewritten}` |
| `POST /api/squads`, `PUT /api/squads/<id>`, `DELETE /api/squads/<id>` | same shape |
| `GET /api/matches/<stem>` | the record, or `{}` when none |
| `PUT /api/matches/<stem>` | replaces the record whole; `{}` or all-null deletes it |

Error bodies are static bilingual strings (`中文 / English`), never an exception text or path.

## 5. Insights package

`badminton_analysis/insights/` — pure functions over loaded files, no Flask, no cv2.

```
loaders.py     load_run(out_dir) -> Run   (tolerant: every file optional; malformed => None + note)
envelope.py    Insight dataclass + helpers ok()/partial()/unavailable()
rally.py       rally_insight(run)
strokes.py     strokes_insight(run)
technique.py   technique_insight(run)
movement.py    movement_insight(run), recovery_insight(run), pull_insight(run)
sidecar.py     compute_video(stem, out_dir, video_path=None) -> dict; cached insights.json
aggregate.py   overview(selection, athletes_store, range, ...) -> dict
findings.py    build_findings(overview) -> list
dates.py       video_date(stem, video_path) -> (iso, source)
```

### 5.1 Insight envelope

```json
{ "status": "ok" | "partial" | "unavailable",
  "value": <object> | null,
  "coverage": { "n": int, "of": int } | null,
  "reason": { "code": str, "params": { } } | null }
```

`unavailable` has `value: null` and a `reason`. `partial` has a value *and* a reason (what limits it).
Reason codes: `no_data`, `no_rallies`, `signal_swing`, `signal_degraded`, `no_strokes_file`,
`no_technique`, `few_hits`, `no_hits`, `unscored_technique`, `no_point_outcome`, `no_results_entered`,
`no_benchmark`, `no_positions`, `low_stroke_coverage`, `doubles_run`.

`doubles_run`: the run was analysed with `match_format: "doubles"` (top-level `match_format` in `metadata.json`;
a missing or malformed value means singles). The legacy tracker's per-side positions and hits are one
representative per side of a 4-player match, not a player, and stroke types and technique scores are withheld,
so the per-player insights (`technique`, `strokes`, `movement`, `recovery`, `pull`) are `unavailable` with this
code; `rally` and `quality` stay as computed (rally windows are not player-specific). The sidecar carries a
top-level `"match_format": "singles" | "doubles"` (`INSIGHTS_VERSION` 5). In the overview a doubles stem is
counted in `scope.stems` and listed in `matches`, but it is not a player unit: it adds nothing to the technique,
movement, recovery, pull, stroke or match-win aggregates, the trend or the benchmark, `scope.units` does not
count it, and its rally windows still feed `avg_rally`. With only doubles stems in scope the player KPIs read
`unavailable` + `doubles_run`.

### 5.2 Per-video sidecar

`OUTPUTS/<stem>/insights.json`, schema below. Recomputed when `INSIGHTS_VERSION` changed or any input file's
`mtime_ns` differs from `inputs`; written atomically; if the directory is not writable the result is returned
without caching (never an error). `compute_video` never raises: a failing insight becomes
`unavailable` with `reason.code = "no_data"`.

```json
{ "schema_version": 1, "version": 1, "stem": "...", "computed_at": "ISO-8601 UTC",
  "inputs": { "detections.jsonl": 123, "hits.json": null, "...": 0 },
  "date": { "value": "2026-10-10", "source": "filename" | "mtime" },
  "fps": 59.94, "duration_sec": 228.7, "match_format": "singles" | "doubles",
  "rally":     Insight<{count, label, signal, suppressed, durations_sec[], mean_sec, median_sec,
                        shots_per_rally[]|null, mean_shots|null}>,
  "strokes":   Insight<{total, classified, uncertain, distribution{}, by_hitter{upper{classified,total},lower{...}}}>,
  "technique": Insight<{by_side:{upper:Side, lower:Side}}>,   Side = {techniques:{<7 keys>:{count, avg_score|null}}, index|null, scored}
  "movement":  Insight<{upper:Grid, lower:Grid}>,             Grid = {grid[11][10], samples, distance_m, mean_speed_mps, base{x,y}}
  "recovery":  Insight<{upper:{median_sec|null,found,hits}, lower:{...}}>,
  "pull":      Insight<{upper:Pull, lower:Pull}>,             Pull = {grid[11][10], front, mid, back, left, right, n}
  "quality":   {shuttle_coverage_in_play, hit_source, shuttle_source, court_view_pass_frac} }
```

### 5.3 Algorithms

- **Date.** First `YYYYMMDD` found in the stem matching `(20\d{2})(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])`
  and a real date => `filename`; else the video file's mtime date => `mtime`; else the run directory mtime.
  A match record `date` overrides it at aggregation time (`source: "record"`).
- **Rally.** `_rally_summary` rules. Durations from `end_sec - start_sec`. `status`: `ok` when
  `signal in (shuttle, courtview)` and not degraded; `partial` + `signal_swing` for swing; `partial` +
  `signal_degraded` for degraded/none (durations are coarse windows); `unavailable` + `no_rallies` for
  error/missing/zero. `shots_per_rally[i]` = hits with `time_sec` in `[start-0.5, end+0.5]` (hits from
  `hits.json`, else `strokes.json` converted with fps); null when no hits.
- **Strokes.** `total` = len(strokes), `uncertain` = count where `uncertain` or stroke `== "uncertain"`,
  `classified = total - uncertain`. `ok` when `classified/total >= 0.6`, `partial` +
  `low_stroke_coverage` below that, `unavailable` + `no_strokes_file` if absent or `total == 0`.
- **Technique.** Group `strokes.jsonl` by `player_side` and mapped technique; `avg_score` is the mean of
  non-null `overall_score`. `index` = mean of the `avg_score` of techniques with `count >= 1` and a score.
  `scored` = how many of the seven have one. `partial` + `unscored_technique` (params `names`) while
  `scored < 7`, `ok` at 7, `unavailable` + `no_technique` with no reports. Missing side => that side null.
- **Movement.** Frames with a `court` for the side; keep frames inside a rally window when the rally
  insight has windows and is not degraded, else all frames (status `partial`, reason `no_rallies`).
  Drop a point more than 1.5 m outside the court, clamp the rest. Grid: 11 rows (0 at the net, 10 at the
  baseline, each `6.7/11` m) by 10 columns (`6.1/10` m, column 0 at `x = 0`), normalised to shares summing
  to 1 (4 decimals), `samples` = points used. `distance_m` sums consecutive steps where the frame gap is
  `<= 3` and the implied speed `<= 9 m/s`; `mean_speed_mps = distance / time spanned by those steps`.
  `base` = `(3.05, 3.35)` upper, `(3.05, 10.05)` lower. `unavailable` + `no_positions` under 30 samples.
- **Recovery to base.** For each hit by side H: find the first detection frame at or after the hit frame, within
  4.0 s, where H is within 1.0 m of `base`; the time is `frame_time - hit_time` (0 if already inside).
  `median_sec` over the found ones, coverage `{n: found, of: hits}`. `ok` at `found >= 10`, `partial` +
  `few_hits` at 3-9, `unavailable` + `no_hits` below 3 or without hits.
- **Pull.** For consecutive hits `A` then `B` (different hitters, gap `<= 3.0 s`), take B's court position at
  B's hit frame (nearest detection within 5 frames) and bin it on B's half into B-grid coordinates; the
  result belongs to A (A's shot pulled B there). `front` = rows 0-3, `mid` 4-7, `back` 8-10, `left` = cols
  0-4, `right` 5-9, as shares. Same status rules as recovery.
- **Quality.** Copied from `hits.json` (`shuttle_coverage_in_play`, `source`, `shuttle_source`) and
  `rally_segments.json.detection.court_view.pass_frac`.

### 5.4 Overview aggregation

`GET /api/insights/overview?athlete=<id>&squad=<id>&range=7d|30d|season|all`

- Selection: each analysed stem (has any run output) joined with its match record. `athlete` => stems where
  that athlete is on a side (and which side). `squad` => stems where any side's athlete is in the squad.
  Neither => every stem, both sides counted. Unknown id => `404`; bad `range` => `400`.
- Date and range: date = record date, else sidecar date. The range anchors on the **latest date in the
  selection** (so old footage still shows): `7d`/`30d` = that date minus N-1 days, `season` = Jan 1 of its
  year, `all` = no bound.
- A "unit" is one (stem, side). Aggregates use units. Technique axis score = count-weighted mean of unit
  `avg_score`; `index` = mean of scored axes.

Response:

```json
{ "scope": {"athlete": {...}|null, "squad": {...}|null, "range": "30d", "stems": 5, "units": 5,
            "date_from": "...", "date_to": "...", "generated_at": "..."},
  "kpis": { "technique_index": Insight<{value, delta|null, scored}>,
            "match_win_rate": Insight<{wins, played, rate}>,
            "avg_rally": Insight<{seconds, shots|null, signal}>,
            "rally_win_rate": Insight(unavailable: no_point_outcome),
            "recovery": Insight<{median_sec}>, "net_share": Insight<{share}>,
            "pull_rear": Insight<{share}>, "unforced_errors": Insight(unavailable: no_point_outcome),
            "strokes": Insight<{classified, total}> },
  "radar": { "reference": {"lo":70,"hi":100,"source":"literature"},
             "axes": [ {"id":"smash","count":n,"score":x|null,"status":"ok|unavailable"}, ... 7 ] },
  "trend": { "points": [ {"date","stem","index"} ], "benchmark": Insight<{index}> },
  "movement": { "position": Insight<Grid>, "pull": Insight<Pull> },
  "matches": [ {"stem","date","date_source","side","athlete","opponent","result":"W|L|null","sets",
                "tech_index","status":"analysed|processing|new","needs_review":bool,"doubles":bool} ],
  "findings": [ {"code","severity":"watch|good|info","params":{}} ] }
```

`technique_index.delta` = last point minus first point of the trend (null with fewer than 2 points).
`net_share` = classified `net` strokes / classified strokes for the selection. `pull_rear` = mean of unit
`back` shares. `benchmark` = median unit index of units *outside* the selected athlete in the same squad and
range (`unavailable` + `no_benchmark` without a squad or without others). `needs_review` = `strokes`
coverage `< 0.5`. `processing` = the stem has a running job. Findings (`findings.py`, deterministic, at most
6, sorted `watch`, `good`, `info`): `technique_below_reference{technique,score,lo}` (lowest first),
`technique_strongest{technique,score}`, `index_trend{delta,range}`, `recovery_median{sec}`,
`techniques_unscored{names}`, `strokes_coverage_low{classified,total}`, `rally_estimated{signal}`.

`GET /api/insights/video/<stem>` returns the sidecar (computing it when stale). `404` for an unknown stem.

### 5.5 Jobs

`GET /api/jobs` => `[{id, video, kind: "match"|"posture", status, stage, progress, message, updated}]` from the
in-memory `jobs` dict (never the `proc` handle) plus runs found on disk whose `progress.json` was updated in
the last 10 minutes and that are not in the dict (`status: "detached"`). Newest first.

## 6. Front end

### 6.1 Structure

Native ES modules, no build step, no new dependency. `kestrel.html` loads `tokens.css`, `kestrel.css` (legacy
screens), `shell.css`, `widgets.css` in that order, then `<script type="module" src="/static/kestrel/main.js">`.

```
static/kestrel/
  main.js                 entry: prefs, shell mount, router start
  core/  store.js router.js api.js i18n.js motion.js format.js geometry.js scope.js prefs.js
  shell/ rail.js topbar.js palette.js peek.js transition.js ground.js
  widgets/ frame.js kpi.js radar.js trend.js courtmap.js findings.js matches.js pipeline.js gap.js
  views/ overview.js matches.js workspace.js players.js pipeline.js legacy.js
  legacy/kestrel-legacy.js   the former static/kestrel.js, behaviour unchanged (see 6.6)
  tokens.css shell.css widgets.css
static/fonts/  SpaceGrotesk-Variable.woff2  InstrumentSerif-Regular.woff2  InstrumentSerif-Italic.woff2  (+ existing Plex Mono)
static/img/ground/  court-aerial-night.jpg  court-racket-top.jpg  CREDITS.md
```

`core/geometry.js`, `format.js`, `scope.js`, `router.js` (parse/format only) and `i18n.js` data are DOM-free so
they run under `node --test`.

### 6.2 Routes (hash)

`#/overview?athlete=&squad=&range=&lens=` (default), `#/matches`, `#/matches/<stem>`, `#/players`,
`#/pipeline`, `#/library`, `#/new`, `#/results/<stem>?mode=`. Unknown hash => overview. Back/Forward
restore scope, lens and scroll. `library`, `new`, `results` render through `views/legacy.js`.

### 6.3 Design tokens (`tokens.css`)

Night default, day optional (`[data-theme="day"]`; legacy `light` maps to day). Values come from the canvas:

| Token | Night | Day |
|---|---|---|
| `--bg --surface --surface2` | `#05070A #0B1017 #111923` | `#EDF0F3 #FFFFFF #F5F7F9` |
| `--line --lineHi` | `#1B2733 #2D4054` | `#DCE2E8 #B9C4CF` |
| `--ink --ink2 --ink3` | `#E6EDF5 #9BA9B8 #7A8898` | `#0E141B #44505E #5E6B7A` |
| `--accent --accentInk` | `#FF5A36 #FF8A6B` | `#FF5A36 #C23A1A` |
| `--cyan --cyanInk` | `#7FC8D6 #7FC8D6` | `#1B7F92 #077486` |
| `--blue --good --mid --bad` | `#9DB0E6 #5FCF9A #E8B454 #F27B86` | `#3C58D6 #17794A #8A5A00 #C4262B` |

The legacy variables (`--paper --card --border --fill --muted --faint --good --mid --bad --sidebar-bg`,
radii) are re-pointed at these so untouched legacy screens adopt the look. Fonts: Space Grotesk (UI), IBM
Plex Mono (numbers, labels), Instrument Serif (page names, titles); the CJK fallback stack is
`"PingFang SC","Microsoft YaHei"`. Text tokens hold 4.5:1 on their surface. Focus ring: 2 px `--cyan`.

### 6.4 Motion

All durations and easing from canvas Foundations. Easing `cubic-bezier(.2,.8,.2,1)`; entrance ease-out quart.

- **Reveal clock** (`core/motion.js`): one `requestAnimationFrame` clock per view entrance, 2400 ms, progress
  `p` 0-1. Widget `i` has local progress `ease((p - i*0.06)/0.45)`. Widgets update **attributes and styles
  in place** (no per-frame `innerHTML`). Count-up numbers, ring/line draw (`pathLength = 1` dash), radar
  opens from the centre, court cells bloom row by row, findings slide in, a cyan scan line sweeps each
  card while its local progress is between 0 and 1.
- **Re-acquire**: changing scope, range, lens or court-map mode restarts the clock.
- **Container transform** (`shell/transition.js`): activating a link to another route measures the clicked
  element, grows a surface-coloured layer from its rect to full viewport in 800 ms, then navigates and the
  new view reveals from the same surface (1.3 s fade, staggered sections). Falls back to an instant
  navigation with reduced motion or when the element has no rect.
- **Rail marker** glides (550 ms); rail collapse 500 ms; slider pills for range/lens/court mode glide.
- **Peek** slides in 700 ms over a blurred scrim; **palette** fades up 500 ms, results stagger 50 ms.
- **Live numbers** are real: the sync clock counts seconds since the last successful fetch; `/api/jobs` is polled
  every 2 s while any job runs (and 15 s otherwise) and job bars/percentages follow it; when a job
  completes the overview refetches. No synthetic counters.
- `prefers-reduced-motion: reduce` disables the clock (everything renders at `p = 1`), the container
  transform, sweeps, marker glides and the ground animation.

### 6.5 Screens and widgets

- **Shell:** rail (Overview, Matches, Players | Video library, Pipeline | Settings = language + theme popover),
  collapsible 232/68 px and persisted, a tab bar under 720 px; top bar (squad and athlete pickers, palette
  button, 7D/30D/Season, theme, a notifications button that opens the pipeline); command palette (`Ctrl K`
  or the button; results from pages, athletes, matches, actions); peek drawer; ambient ground.
- **Ground:** a masked layer behind the page (`ground.js`): blurred (18 px), dimmed photograph over the full
  page height (fading to ~35% toward the bottom), a glowing court, three flight arcs and a travelling shuttle
  over the top ~1000 px. Cards are opaque, so insights never sit on the photo. Day theme drops the photo to 20%.
- **Overview:** header (serif athlete name, sync clock), lenses Overview/Technique/Tactics, four metric tiles per
  lens (ring gauge for index and win rate, sparkline otherwise, hatched "not yet available" tile with its
  reason), technique radar (unscored axes dashed, "Preview full coverage" switch removed: the radar only
  shows real data), trend with hover crosshair and the benchmark line, findings, court map (Position | Pull
  cell grid with hover readout), matches table (row opens the peek), pipeline. Customize = show/hide and
  up/down reorder per widget, saved in `localStorage` (`kestrel_layout_v1`).
- **Matches:** table of the scope's matches with result chips and an inline match-record editor (date,
  upper/lower athlete or name, winner, sets); row opens `#/matches/<stem>`.
- **Match workspace:** the annotated video (`detect_<stem>.mp4`: after a finished run the H.264 transcode
  `detect_<stem>_h264.mp4` replaces it in place, so `_h264` is only a fallback), rally timeline from
  `rally_segments.json` (blocks sized by length; won/lost lanes are **not** drawn, no outcome exists - a
  single lane), stroke strip and list from `strokes.json` joined by time, stroke inspector from
  `strokes.jsonl` (score on the reference range, per-metric bars), summary card with the honest rally label.
  Selecting a stroke seeks the video to its time.
- **Players:** athletes and squads CRUD, per-athlete match count.
- **Pipeline:** `/api/jobs` list with progress.
- **Not yet available tiles** use the `gap.js` widget: dashed hatched card, the reason in words, a link to the
  action that would fix it when one exists (e.g. enter match results).

### 6.6 Legacy screens

`static/kestrel.js` moves to `static/kestrel/legacy/kestrel-legacy.js` with these edits only: it no longer
renders a sidebar or owns `window.Kestrel` navigation; it exports `mountLibrary(el)`, `mountNew(el)`,
`mountResults(el, stem, mode, modes)` and calls `onNavigate(route)` where it used `setScreen`. Behaviour of
the library (tags, filters, cards), wizard, results, plan, coach report and delete modals is unchanged.
`tests/test_app_library.py` is repointed at the moved file. `kestrel.html` keeps `/kestrel`; `web_ui.html` is
untouched.

### 6.7 i18n

Every new string has `zh` and `en`. Findings, reason codes, status labels and axis names are keyed. A node test
asserts both languages define the same key set and that every reason code and finding code has a string.

## 7. Errors and edge cases

Missing or malformed run files never fail a request: the insight is `unavailable` with a reason. A stem with
no outputs is excluded from the overview. A video with a space in its name round-trips through the API and the
hash router (percent-encoded). Empty library => Overview shows the empty state with "New analysis". Athlete
deleted while selected => the scope falls back to the squad or library with a toast. A failed fetch keeps the
last good data on screen and shows a retry in the header.

## 8. Testing

- Python: `tests/test_library_athletes.py`, `tests/test_app_athletes.py`, `tests/test_insights_*.py` (synthetic
  runs built in `tmp_path` by `tests/insights_fixtures.py`), `tests/test_app_insights.py`,
  `tests/test_app_jobs.py`; `tests/test_kestrel_js.py` runs `node --test tests/js` (skips without node).
- JS (`node --test tests/js`): geometry (radar points, trend path, grid cells, arc dash), format, scope
  filtering, router parse/format, i18n parity and code coverage.
- Browser verification (Browser pane) against the real runs in the owner's `outputs/`: overview in each lens
  and range, hover readouts, peek, palette, container transform to the workspace, workspace stroke seek, players
  CRUD, match record round trip, rail collapse, both themes, a 375 px viewport, reduced motion, zh and en,
  console clean.
- The full suite stays green (`1246 passed, 3 skipped` today) plus the new tests.

## 9. Acceptance criteria

1. `GET /api/insights/overview` on the real library returns the documented shape; every insight has a status
   and, when not `ok`, a reason; no value is fabricated.
2. Athletes, squads and match records CRUD round-trip, survive a restart, and tolerate a corrupt store.
3. The Overview, Matches, Workspace, Players and Pipeline screens render real data, in zh and en, night and day.
4. Legacy library, wizard and results behave as before.
5. Reduced motion renders instantly with identical content.
6. `pytest` and `node --test tests/js` pass; the browser pass is clean.

## 10. As built

Implemented on `claude/kestral-sports-ui-design-015bc4` in eleven reviewed tasks plus one final fix wave
(ledger: the git-ignored `.superpowers/sdd/2026-10-19-kestrel-telemetry-ui/progress.md`; workstream status:
`.ai/workstreams/kestrel-telemetry-ui.md`). Everything in sections 2-9 shipped. Deviations from the text above,
all owner-compatible rulings:

- **Court grid orientation.** Row 0 is at the net on BOTH halves and the column is `floor(x / 0.61)` (the plan's
  `cell()` examples contradicted this section and were wrong). Drop/clamp is measured from the side's own half
  rectangle (a lower-side point more than 1.5 m outside the lower half is dropped, not clamped).
- **Dates.** Order: match record, then a `YYYYMMDD` in the stem, then the source video's mtime, then the mtime of a
  stable run input (`metadata.json`, else the oldest sidecar input), else no date. The run directory's mtime is never
  used (the sidecar write bumps it). Rows carry `date_source`; the UI marks `mtime` dates "approx.".
- **Shots per rally** come only from `hits.json` written by the learned detector (`hits_meta.source == "learned"`);
  the BST/contact fallback is too sparse to count shots (a real library showed "1.4 shots per rally"). The overview
  tile shows seconds when shots are unknown. The workspace labels the `strokes.json` count "Strokes detected".
- **Match rows** (`/api/insights/overview`): one row per unit (stem, side), newest first; `sets` is an array of
  `[own, opp]` integer pairs oriented to the row's `side` (upper-first when the row has no side); `result` is W/L
  against the row's own side whenever a winner is recorded (`match_win_rate` stays unavailable without an athlete or
  squad scope). With no athlete scope the overview widget shows one row per stem.
- **Net share** is computed per hitter (`by_hitter[side].distribution`), so an athlete scope never counts the
  opponent's strokes. `INSIGHTS_VERSION` is 5.
- **Video file.** After a finished run the playable file is `detect_<stem>.mp4` (the H.264 transcode replaces it);
  the workspace tries it first and shows a message when it cannot play.
- **GET /api/matches/<unknown stem>** is 404 (the front end treats it as "no record").
- **Delete.** `/api/delete/<stem>` with scope `all` also removes that stem's match record; DELETE of an unknown
  athlete/squad id and no-change PUTs never write the store.
- **Jobs.** `/api/jobs` rows never expose the process handle or log path; error messages are reduced to
  `分析失败 (exit=N)` and other messages are first-line, path-scrubbed (UNC paths and Windows paths containing spaces
  are not scrubbed in non-error messages: parked, those messages are static text today).
- **Theme storage** stays legacy-compatible: `kestrel_theme` holds `dark` or `light` (CSS maps them to night/day).
- Overview has no range pills (the top bar owns the range) and no sparklines (the response has no per-tile series);
  the canvas's "Export report" button is replaced by "New analysis"; match rows open the peek, and the peek's
  "Open match workspace" runs the container transform.
- The shell-only strings live in `core/i18n.js`; widget classes are `.k-chip` / `.k-label` (the legacy `.chip` is untouched).

Measured (owner's 13 real runs, 26 units): `/api/insights/overview` cold 5.0-7.5 s (computes and caches 13 sidecars),
warm 0.09 s; technique index 54.7 with 4 of 7 techniques scored; 86 of 304 strokes classified; recovery to base median
about 0.0-0.4 s; all KPI statuses honest (rally win rate, unforced errors and, without a scope, match win rate unavailable).
Tests: `1710 passed, 2 skipped` pytest on the tree merged with `main` (it dropped the retired claim-helper tests and added the doubles tests; pre-merge 1824 passed, 5 skipped; baseline 1246 passed, 3 skipped) and `202` Node tests at the final commit.

Known limitations and follow-ups (not done):

- Recovery to base counts a hit taken within 1 m of the base point as 0 s, so the median mostly measures standing near
  base; a stricter definition (time to return after leaving a 2 m radius) is a worthwhile follow-up.
- No automatic point outcome, net/lift/drive scoring or pro-calibrated ranges (owner scope decision); Compare and
  Reports destinations; shot-landing on the ground.
- `/api/technique/<stem>` returns 404 without `technique_summary.json`, so a run with only `strokes.jsonl` shows no
  workspace scores although the overview scores it (real runs have both).
- `app._safe_stem` accepts `stem.` on Windows (pre-existing).
- Deferred minors from review (none blocking): see the ledger.
