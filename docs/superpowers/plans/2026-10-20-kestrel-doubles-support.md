# Kestrel Doubles Support Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every Kestrel telemetry-UI feature handles doubles matches with real per-player data.

**Architecture:** Extend the athlete store with a doubles record shape, add a `doubles` insight to the per-video sidecar (from `players.json`, `tracklets.jsonl`, `hits.json`), aggregate it per athlete in the overview, and add doubles views to the existing screens.

**Tech Stack:** Python 3.11 + Flask, pytest; native ES modules, `node --test 'tests/js/*.test.mjs'`; no new dependencies.

**Spec:** `docs/superpowers/specs/2026-10-20-kestrel-doubles-support-design.md` (binding; also read `docs/superpowers/specs/2026-10-19-kestrel-telemetry-ui-design.md` and `docs/superpowers/specs/2026-10-17-doubles-match-analysis-design.md` sections 6.5, 6.6, 7)

## Global Constraints

- Python tests: `PYTHONUTF8=1 D:/Dev/Claude/Projects/good-badminton/Good-badminton/.venv/Scripts/python.exe -B -m pytest -q -p no:cacheprovider --basetemp <unique dir> <files>`; JS tests: `node --test 'tests/js/*.test.mjs'` (the directory form fails on Node 24); `node --check` on every touched module.
- No new dependencies; no build step. Never write into `D:/Dev/Claude/Projects/good-badminton/Good-badminton/outputs` (use temp dirs).
- Builders do not run state-changing git commands (the controller commits). Never touch `app.py`, `main.py`, `badminton_analysis/system.py`, `badminton_analysis/tracking/`, or doubles pipeline files unless a task names a specific hunk.
- Singles behaviour and contracts stay unchanged; every existing test stays green.
- Error bodies are static bilingual strings; a malformed or absent run file never fails a request (the insight becomes `unavailable` with a reason); all text reaches the DOM through textContent/escHTML; every new string exists in zh and en; honesty rules of the telemetry spec apply (no fabricated numbers; every who-hit figure shows the unvalidated badge).
- Court convention: row 0 of every grid is at the net; `x` 0-6.1, `y` 0-13.4, net 6.7.

## Review Focus

1. **Hostile doubles files** (`players.json` with players as non-list, NaN/huge numbers, duplicate ids, unknown teams; `tracklets.jsonl` with frames/court length mismatch, 200k points; `hits.json` with `hitter_id` of wrong types): never raises, bounded time, strict JSON. Tasks 2, 3.
2. **Identity mapping integrity:** same athlete on two identities, athlete deleted while mapped, identity with no athlete, a doubles record on a stem that is a singles run (and the reverse). Tasks 1, 3.
3. **Mixed scopes:** athlete who played singles and doubles; squad scope with one doubles stem; no scope with doubles only. Task 3.
4. **Degraded identity:** `degraded` / `degraded_teams` (team null) / `error` render with their reason, never as `ok`. Tasks 2, 4.
5. **Orientation:** sets oriented to the athlete's team; winner team vs athlete team for W/L. Tasks 1, 3, 4.

---

### Task 1: Doubles match records in the athlete store and routes

**Files:**
- Modify: `badminton_analysis/library/athletes.py`, `badminton_analysis/web/athletes_routes.py`
- Test: `tests/test_library_athletes.py`, `tests/test_app_athletes.py` (append new tests; do not change existing ones)

**Interfaces:**
- Produces (spec section 3): `record_format(record) -> "singles"|"doubles"`; `player_athlete(data, record, pid) -> {"athlete_id": str|None, "name": str|None}`; `validate_match(record, data)` accepts the doubles shape (new error code `invalid_players`); `set_match`/`get_match` unchanged signatures; `delete_athlete` also rewrites doubles identities; the `matches` count in `GET /api/athletes` includes doubles records.

- [ ] **Step 1: Write failing tests** for: valid doubles record round trip (store + `PUT/GET /api/matches/<stem>`); winner_team 1/2 accepted, 3/`"upper"` rejected; sets 0-5 pairs of ints 0-99 team-1-first and winner required with sets; players keys outside P1..P4 rejected; identity with both `athlete_id` and `name` rejected; same `athlete_id` on two identities rejected; unknown `athlete_id` rejected; null/missing identities accepted; mixing `upper`/`lower` with `players` rejected; `format: "doubles"` with `result.winner` rejected; delete of a mapped athlete rewrites to `{"name": ...}` and counts `rewritten`; athlete `matches` count includes doubles once per match; corrupt/hostile `players` values (list, string, nested) on load degrade to null identities without raising; error bodies static bilingual with `invalid_players` mapped.
- [ ] **Step 2: Run to confirm failure.** `pytest tests/test_library_athletes.py tests/test_app_athletes.py -q`.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the two files plus `tests/test_app_tags.py tests/test_app_library.py tests/test_app_delete.py`: all pass.

---

### Task 2: The `doubles` insight in the sidecar

**Files:**
- Create: `badminton_analysis/insights/doubles.py`
- Modify: `badminton_analysis/insights/loaders.py` (read `players.json`, `tracklets.jsonl`, and the doubles fields of `hits.json` tolerantly; add `INPUT_FILES` entries so sidecar invalidation sees them), `badminton_analysis/insights/envelope.py` (new reason codes if centralised), `badminton_analysis/insights/sidecar.py` (compute the `doubles` insight; `INSIGHTS_VERSION = 6`)
- Modify: `tests/insights_fixtures.py` (additive `make_doubles_run(tmp_path, stem, *, status="ok", players=..., tracklets=..., hits=..., names=None, identity_video=...)`)
- Test: `tests/test_insights_doubles.py`, additions to `tests/test_insights_sidecar.py`

**Interfaces:**
- Produces: `doubles_insight(run) -> Insight` per spec 4.1 (value shape exactly as in the spec); sidecar document gains top-level `"doubles": Insight`; for singles runs `doubles` is `unavailable` / `not_doubles`; reason codes `not_doubles`, `no_players`, `doubles_degraded`, `doubles_error` (add them to spec 5.1 of the telemetry spec and to `static/kestrel/core/i18n.js` REASON_CODES with zh+en strings: en "Not a doubles match." / "No player data for this doubles run." / "Player identities are only partly reliable ({status})." / "Player identification failed for this run."; zh "不是双打比赛。" / "此双打比赛没有球员数据。" / "球员身份仅部分可靠（{status}）。" / "此比赛的球员识别失败。").

- [ ] **Step 1: Write failing tests:** ok run -> per-player rows (distance, speeds, coverage, frames, hits, hits_named) copied/validated from players.json, grids from tracklets (a player whose tracklet sits in one cell has a one-cell grid summing to 1; tracklet ids from `players.json` map to identities; unmapped tracklets ignored; under 30 samples -> grid null; a tracklet on the upper half and later the lower half uses each point's own side); hits: named rule (shuttle/swing named, single_candidate counted separately, null `hitter_id` only in total), per-team counts, `named_share` null for zero hits; `degraded` -> partial + `doubles_degraded` with team null allowed; `error` -> unavailable + `doubles_error` and no reason text echoed; missing/malformed players.json -> unavailable + `no_players`; singles run -> `not_doubles`; hostile inputs (players not a list, NaN/huge distances clamped or ignored, duplicate ids keep first, tracklets with mismatched lengths skipped, 200,000-point tracklets under 3 s, non-string `hitter_id`) never raise and the whole sidecar passes `json.dumps(allow_nan=False)`; sidecar invalidation: touching `players.json`, `tracklets.jsonl` or `hits.json` recomputes; `INSIGHTS_VERSION == 6` and v5 caches rebuild.
- [ ] **Step 2: Run to confirm failure.**
- [ ] **Step 3: Implement** (reuse `movement.cell` for grid binning).
- [ ] **Step 4: Run** `tests/test_insights_*.py tests/test_app_insights.py`.

---

### Task 3: Doubles in the overview aggregation and findings

**Files:**
- Modify: `badminton_analysis/insights/aggregate.py`, `badminton_analysis/insights/findings.py`, `badminton_analysis/web/insights_routes.py` (only if the athletes store reading needs the doubles record: it already passes the store)
- Test: `tests/test_insights_aggregate.py` (append), `tests/test_app_insights.py` (append)
- Modify: telemetry spec section 5.4 table and the doubles addendum only if the contract needs a clarification (note it in your report).

**Interfaces:**
- Consumes: Task 1 (`record_format`, `player_athlete`, the doubles record shape) and Task 2 (the sidecar `doubles` insight). If Task 1/2 are not yet on disk when you start, the shapes in spec sections 3 and 4.1 are the contract: build fixtures from them with `tests/insights_fixtures.py` helpers.
- Produces: the overview response additions of spec 4.2 (`doubles` insight, doubles match-row keys, merged athlete grid in `movement.position`, `scope.units` rule, findings codes `doubles_identity_degraded`, `doubles_hits_unvalidated`).

- [ ] **Step 1: Write failing tests:** athlete scope over a mixed library (singles + doubles) gives singles KPIs from singles units only, `doubles.value.athlete` from the doubles identity, merged `movement.position` grid (sample-weighted, shares sum 1); same athlete mapped on two identities cannot happen (store rejects) but a record mapping the athlete to P3 selects P3; squad scope selects stems with any member mapped; no scope gives runs for every doubles stem and `athlete: null`; doubles-only scope: singles KPIs unavailable (`doubles_run`), `doubles` ok/partial; degraded run -> partial + `doubles_degraded` and a `doubles_identity_degraded` finding; rows: `doubles`, `format`, `athlete_player`, `team`, `partner`, `opponent` ("C / D"), `result` W/L from `winner_team` vs the athlete's team, `sets` oriented to the athlete's team ([own, opp]) and team-1-first with no athlete, `tech_index` null, `needs_review` true when status != ok; an unmapped identity contributes to runs only; an athlete deleted from the store (record rewritten to a name) disappears from athlete scope but the run stays; every result passes `json.dumps(allow_nan=False)`; empty scopes well-formed.
- [ ] **Step 2: Run to confirm failure.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `tests/test_insights_*.py tests/test_app_insights.py tests/test_app_athletes.py`.

---

### Task 4: Doubles in the front end

**Files:**
- Create: `static/kestrel/widgets/doubles.js` (the Overview "Doubles players" widget), `static/kestrel/views/doubles_model.js` (pure helpers shared by the views: `doublesRows`, `teamLabel`, `playerLabel`, `whoHitModel`, `identityColor`)
- Modify: `static/kestrel/views/overview.js`, `views/matches.js`, `views/workspace.js`, `views/pipeline.js`, `widgets/matches.js`, `widgets/pipeline.js` (stage labels), `static/kestrel/core/i18n.js` (additive zh+en keys), `static/kestrel/widgets.css` (append), `main.js` only for the peek if needed
- Test: `tests/js/doubles_model.test.mjs`, additions to `tests/js/views_model.test.mjs` and `tests/js/widgets_smoke.test.mjs`

**Interfaces:**
- Consumes: the overview response of spec 4.2 (`doubles` insight, doubles row keys), `GET /api/output/<stem>/players.json`, `GET /api/output/<stem>/hits.json`, `GET /api/insights/video/<stem>` (the `doubles` insight), `PUT /api/matches/<stem>` with the doubles record shape (spec 3), and the existing helpers in `core/api.js` (add `api.players(stem)` and `api.hits(stem)` thin wrappers if missing, 404 = none).
- Produces: spec section 5 UI: Overview "Doubles players" widget + athlete tiles ("Distance covered", "Hits named") in the Tactics lens; doubles chip, team title and four-identity record editor (labelled `P1 · Team 1 · 83%`, winner team select, sets team-1-first) in Matches; peek with teams/status/named-hit share + unvalidated badge and no scores; workspace identity video (`detect_<stem>_players.mp4` only when `identity_video.status == "ok"`, else the annotated video), Players panel, who-hit strip coloured per identity (muted when `hitter_id` is null or evidence is `single_candidate`) and hits table, "stroke types withheld for doubles" note; localized doubles stage names in the pipeline view (`doubles_identity`, `doubles_heatmaps`, `doubles_video`).

- [ ] **Step 1: Write failing tests** for the pure helpers: `teamLabel` for 2+2 names, missing names fall back to identity ids, degraded teams (team null) label; `whoHitModel` colours/muting rules, confidence formatting, sorting by time, hits with unknown `hitter_id`; `doublesRows` ordering and the unvalidated flag; record form validation for the doubles editor (winner team required with sets, same athlete twice rejected, sets team-1-first, blank identities -> null); every new string present in zh and en; honesty: no score or technique index ever produced for a doubles row, even if the payload holds a number; the widget model for an all-degraded run keeps the reason.
- [ ] **Step 2: Run to confirm failure:** `node --test 'tests/js/*.test.mjs'`.
- [ ] **Step 3: Implement** the widget/views to match the design canvas conventions already used by the existing views (HUD cards, `.k-chip`, `.k-label`, in-place updates, `textContent` only, reduced-motion safe, abortable fetches with stale-response guards, listeners cleaned up in `destroy()`).
- [ ] **Step 4: Run** the node suite and `node --check` on every touched module; Python `tests/test_kestrel_js.py`.
