# Match strategy report (singles and doubles) - design

Date: 2026-10-27. Branch: `claude/match-strategy-analysis-report-77af27`. Status: design approved in chat
(sections 1-3); this spec awaits owner review.

## 1. Goal

Every analysed match gets a strategy report written by a local LLM from the numbers the pipeline already
measured. The report is mainly for **coaching the owner's registered athlete**: what happened, what to change,
the plan for the next match. It ends with a **short scouting section on the opponent**. It covers singles and
doubles, in Chinese and English.

### Done means

1. A finished match analysis queues a `strategy_report` job automatically. The job shows in the Pipeline
   view, takes a normal job slot and can be cancelled.
2. The job writes `outputs/<stem>/strategy_report.json` with an `en` and a `zh` report following the schema in
   section 5.
3. Every LLM-written claim cites fact ids from the facts pack. A number in the prose that no cited fact holds
   is rejected (section 6.3).
4. A report problem never fails the analysis. If Ollama is down, the job fails with a named reason, and the
   page still shows the plain-text summaries built from the facts.
5. Match details shows a Strategy panel (following the UI language) with these states: queued/running, ready,
   stale, failed, off. It has Regenerate and Export Markdown.
6. Endpoint, model and on/off are configurable. The defaults are `http://localhost:11434/v1` and `qwen3.8:27b`.
7. Tests (section 9) pass with no real LLM, and a live check runs on one singles and one doubles run with
   the times recorded.

## 2. Context and what the data supports

Inputs per run in `outputs/<stem>/`, read through the tolerant loaders in `badminton_analysis/insights/`:

| Source | Used for |
|---|---|
| `insights.json` (sidecar, `insights/sidecar.py`) | rally summary, stroke-group distribution, technique, movement grids, recovery, pull zones, `match_format` |
| `strokes.json` (schema 2) | per-hit `frame`, `hitter` (upper/lower), `stroke`, `group`, confidences: sequences, serve mix |
| `rally_segments.json` | rally windows (`start_frame`, `end_frame`, `degraded`) |
| `tracklets.jsonl`, `players.json`, `hits.json` | doubles identities, teams, positions, who-hit (unvalidated) |
| `data/athletes.json` match record | players or teams, date, `result.winner`, `result.sets`, `part_of`, `sides_swapped` |
| `training_plan.json` | link target for the training-focus line, when present |
| `rally_outcomes.json` | **optional**, produced by the separate rally-outcome feature (section 2.1) |

What the data cannot claim, and the report therefore never states:

- **Who won each rally.** It is not recorded today. Without `rally_outcomes.json` there are no win/loss,
  winner or error claims, only "last stroke of the rally and who hit it".
- **Stroke labels in doubles.** Doubles runs withhold `strokes.json` (`tracking/doubles.py`
  `STROKES_WITHHELD`), so a doubles report has no stroke mix, no shot sequences and no serve mix.
- **Doubles who-hit.** It is never validated against labels, so every fact derived from it carries
  `validated: false` and the prose must say "estimated".

### 2.1 Rally outcomes (dependency, not built here)

A separate session ("Auto rally-outcome labelling", on its own branch and spec) designs automatic
per-rally outcome labelling. This report reads its file when present and otherwise works without it.
Expected shape (to be confirmed by that spec; `facts.py` reads it tolerantly):

```json
{"rallies": [{"id": 1, "winner_side": "lower", "outcome": "winner|forced_error|unforced_error|unknown",
              "ending_stroke": "smash", "confidence": 0.8}],
 "summary": {"coverage": 0.7}}
```

Outcome facts are `partial` when fewer than half the rallies have a known winner, and are left out entirely
when the file is absent or unreadable.

### 2.2 Research basis for the content

The notational analysis literature uses a consistent set of performance indicators:
- rally length and shots per rally
- shot-type distribution
- rally-ending shot and outcome (winner, forced error, unforced error), where smash, net, drop and unforced
  errors decide most rallies
- court position by zone

Normative profiles settle after about 4-7 matches, which is why multi-match trends are deferred (section 10).
Coaching scouting templates follow the same order: profile, tendencies, strengths and weaknesses, then a
short game plan. Doubles is thinly covered in the literature, so the doubles sections lean on formation,
rotation and coverage. Sources:
- [Frontiers in Psychology 2021](https://pmc.ncbi.nlm.nih.gov/articles/PMC8784413)
- [Woolgar, Chichester](https://eprints.chi.ac.uk/id/eprint/4079/)
- [Patterns of play of elite players](https://lida.sport-iat.de/twm/Record/3039568?lng=en)
- [Normative templates](https://lida.sport-iat.de/twm/Record/4007910?lng=en)
- [BWF Pan American Junior study](https://development.bwfbadminton.com/wp-content/uploads/2024/11/FINAL-UFDMG_Aburachid.pdf)

## 3. Architecture

New package `badminton_analysis/strategy/`. Each module has one job:

| Module | Job | Depends on |
|---|---|---|
| `facts.py` | Pure. `build_facts(out_dir, record) -> FactsPack`: fact list, `format`, `focus`, header data, caveats. No I/O except reading the run. | `insights.loaders`, `insights.sidecar` |
| `llm.py` | OpenAI-compatible chat client (stdlib `urllib`, no new dependency): `list_models()`, `complete_json(system, user, schema, timeout)`. Raises `LLMError(code, params)`. | config only |
| `prompts.py` | System and user prompt text per language and format, the JSON schema sent to the model, section word caps. | none |
| `validate.py` | `validate(section, facts) -> list[str]` errors: schema, unknown fact ids, numbers not held by cited facts. | none |
| `report.py` | `generate(out_dir, record, config) -> dict`: facts, then one LLM call per language, validate, one retry with the errors, fallback per section, then an atomic write. `render_markdown(report, lang)`. | all of the above |
| `config.py` | Resolves the settings in section 7. | none |

Plus `main_strategy.py` (CLI: `python main_strategy.py <result_dir> [--lang en,zh] [--model M] [--url U]`). The
job runs it as a subprocess, the same way analyses run `main.py`, so pausing and cancelling work unchanged.

The existing `posture/report_llm.py` is not reused. Its local provider puts the model and the base URL in one
field, and its job is a different one (polishing existing prose). Fixing that module is out of scope.

### 3.1 Data flow

```
match job done (status ok, singles or doubles)
  -> app.py queues job kind "strategy_report" for the stem (skipped when GB_STRATEGY_REPORT=off)
  -> JobSlots slot -> subprocess main_strategy.py outputs/<stem>
       facts.build_facts -> for lang in (en, zh): llm.complete_json -> validate -> retry once -> fallback
       -> write strategy_report.json atomically (tmp + os.replace)
  -> GET /api/strategy/<stem> -> Strategy panel
```

A split match (`part_of`) gets one report per part video. Its `header` shows the part number. A whole-match
report is deferred.

## 4. Facts pack

A fact is `{"id", "value", "unit", "who", "status", "validated"}`:
- `who` is `match`, `you`, `opponent`, `you.P1`, ...
- `status` is `ok`, `partial` or `unavailable`.
- `validated` is false for doubles who-hit.

Ids are stable, dotted strings (for example `rally.median_shots`, `you.mix.attack_share`). Unavailable facts
stay in the pack with their reason, so the model can say "not enough data".

**Focus.** "You" is the side (or team) whose record entry carries an `athlete_id`. If both sides carry one,
it is the `lower` side, and the UI lets the owner flip it on Regenerate (`?focus=upper`). If neither does,
"you" is `lower` with `focus.assumed: true`, and the header says so.

**Fact groups:**

- **Match:**
  - rally count, median and 90th percentile rally length (seconds and shots), the short/long rally split
    (short is 5 shots or fewer, long is 10 or more; fixed constants), tempo (shots per second in play),
    degraded rally share
  - result and sets
- **Shot selection** (singles, per side):
  - group shares, with attack = smash, drive, drop; neutral = net, push; defence = clear, lift, block, where
    the exact group-to-bucket map lives in `facts.py`
  - serve mix from the first stroke of each rally, net-play share, unknown-stroke share
- **Sequences** (singles):
  - top 3 two-shot and three-shot sequences per side, with counts
  - the response to a received lift
- **Movement** (per side or per player):
  - zone shares from the movement grids (front, mid and rear crossed with left, centre and right)
  - base position, median recovery time to base (`recovery_insight`), top pull zones (`pull_insight`)
- **Doubles:**
  - formation share per team (front-back vs side-by-side, from the two players' depth gap during rallies;
    threshold constant)
  - front/back role swaps per rally
  - per-player coverage
  - cells neither teammate covers
  - hit share per player (`validated: false`)
- **Rally ending:**
  - last stroke and its hitter, per side
  - with `rally_outcomes.json`: points won and lost by ending stroke, the error profile
- **Technique link:** recurring weaknesses from `technique_summary.json`, and whether `training_plan.json`
  exists.

## 5. Report schema

`strategy_report.json`:

```json
{
  "schema": 1, "format": "singles", "status": "ok|partial|failed",
  "reason": null, "model": "qwen3.8:27b", "endpoint": "http://localhost:11434/v1",
  "generated_at": "2026-10-27T10:00:00Z", "duration_sec": 74.2,
  "inputs": {"insights.json": 1791737451.0, "strokes.json": 1791737400.0},
  "focus": {"side": "lower", "athlete_id": "a_5be6e795", "assumed": false},
  "facts": [{"id": "rally.median_shots", "value": 7, "unit": "shots", "who": "match", "status": "ok"}],
  "by_lang": {"en": {"...": "report"}, "zh": {"...": "report"}}
}
```

Every claim in a report is `{"text": str, "evidence": [fact_id, ...]}`. Every section is
`{"status", "llm": bool, ...}`. The sections, in order:

| Section | Written by | Content |
|---|---|---|
| `header` | code | names or teams, date, result and sets, part, confidence badge (`high`, `medium` or `low` from tracking coverage, stroke coverage and degraded-rally share; thresholds in `facts.py`), focus note |
| `summary` | LLM | exactly 3 claims, how the match was won or lost, from your side |
| `match_profile` | LLM | 2-4 claims on rally length, tempo and the short/long split |
| `your_game` | LLM | `shot_selection` (singles only), `movement`, `patterns` (singles: sequences; doubles: formation, rotation, coverage gaps); each 1-3 claims; `strengths` and `weaknesses`, 2-3 claims each |
| `game_plan` | LLM | 3-5 items `{do, why, cue, evidence}` plus an optional `training_focus` claim (links `training_plan.json` when present) |
| `scouting` | LLM | 2-3 opponent tendencies, the zone they are pulled to most, their favourite finishing shot (singles), one "how to exploit" line; doubles: the pair's formation habits and coverage gaps |
| `caveats` | code | one line per withheld or unavailable fact group, with its reason (no rally outcomes, unvalidated who-hit, degraded rallies, missing files) |

The whole LLM-written part is capped at about 600-800 words per language through per-section word caps in
`prompts.py`.

**Markdown export** renders the same sections as headings and bullets, with each claim's evidence as a
footnote-style list of `id = value unit` pairs.

## 6. LLM integration

### 6.1 Calls

- **One call per language**, with all LLM sections in one JSON object. The `response_format` uses
  `json_schema`, which Ollama's OpenAI-compatible API supports.
- **Temperature 0.3.**
- **Thinking off:** Qwen's thinking is disabled with `/no_think` in the system prompt plus
  `"think": false` in the request (Ollama ignores unknown fields on servers that lack it).
  - A `<think>...</think>` prefix in the content is stripped before parsing, in case thinking was not turned
    off.
- **Before the first call**, `GET /v1/models` checks that the model is listed (`model_missing` if not).

### 6.2 Prompts

The system prompt (per language) asks for an experienced badminton coach talking directly to the athlete.
It sets these rules:
- Use only the facts given, and cite their ids in `evidence`.
- Do not invent numbers or claims.
- Say "not enough data" for unavailable facts.
- Doubles who-hit is "estimated".
- Respect the word caps.
- `zh` is written in Simplified Chinese, using badminton terms that match the UI's existing `i18n.js`
  stroke names.

The user message is the facts pack as compact JSON (header data, facts, format, focus), with no raw
per-frame data.

### 6.3 Validation and retry

`validate.py` checks, per section:

1. **Schema:** required keys, claim counts within bounds.
2. **Evidence:** every `evidence` id exists in the pack, and every claim has at least one.
3. **Numbers:** every number token in `text` (digits, decimals, percentages) matches a value held by one of
   that claim's cited facts.
   - Rounding to the cited fact's displayed precision is accepted, and so is a percentage of a share fact.
   - Ordinals and counts of at most 3 that refer to list positions ("3 takeaways") are exempt.

If any section fails, there is **one retry** of the whole language call, with the error list appended to the
messages. Sections that still fail are replaced by the plain-text fallback: a deterministic
`facts.summary_lines(section)`, marked `llm: false`. That also gives a report with **no LLM at all** when the
endpoint is unreachable.

### 6.4 Status

- **`ok`:** both languages fully LLM-written.
- **`partial`:** some sections fell back. `reason` lists them, for example `sections_fallback{en:[scouting]}`.
- **`failed`:** the LLM was unusable (`llm_unreachable`, `model_missing{model}`, `timeout`). The file still
  holds the facts and the fallback text. The job is marked failed with the same reason.

## 7. Configuration

Resolved in `strategy/config.py`. An environment variable wins over the `strategy` object in
`data/settings.json`, which wins over the default. `data/settings.json` is a new, optional, gitignored file;
it is absent today, and a missing or malformed file means the defaults apply.

| Setting | Env | Default |
|---|---|---|
| enabled | `GB_STRATEGY_REPORT` (`on` or `off`) | `on` |
| endpoint | `GB_STRATEGY_LLM_URL` | `http://localhost:11434/v1` |
| model | `GB_STRATEGY_LLM_MODEL` | `qwen3.8:27b` |
| API key | `GB_STRATEGY_LLM_KEY` | `ollama` (sent as a bearer token; Ollama ignores it, other OpenAI-compatible servers may need it) |
| timeout per language | `GB_STRATEGY_LLM_TIMEOUT` | `300` s |
| languages | `GB_STRATEGY_LANGS` | `en,zh` |

The compose files set `GB_STRATEGY_LLM_URL=http://host.docker.internal:11434/v1` and add
`extra_hosts: ["host.docker.internal:host-gateway"]`, so the container reaches the host's Ollama. The README's
Docker sections get one paragraph on this.

**GPU:** the model lives in Ollama's own process (about 17 GB VRAM for `qwen3.8:27b` Q4_K_M), and a match job
uses about 1.5 GB. The report job still takes a normal job slot, so it never adds GPU compute beside
`max_jobs` analyses. `job_slots.py` is not changed.

## 8. App, API and UI

### 8.1 Jobs (`app.py`)

- When a match job (singles or doubles) finishes with success, it queues `strategy_report` for the same stem
  through `_job_slots.submit`. That happens only when enabled, and only when no report job for that stem is
  already queued or running.
- A report job record has `kind: "strategy_report"`, and the Pipeline view lists it with its own label.
  Pause, resume and cancel follow the existing subprocess paths.
- Posture jobs do not queue a report.
- Deleting a video (`_forget_match_record` and the output cleanup) also removes `strategy_report.json`, since
  it lives in the run directory.

### 8.2 API (new routes, a small blueprint `badminton_analysis/web/strategy_routes.py`)

| Route | Returns |
|---|---|
| `GET /api/strategy/<stem>` | `{status, stale, job: {id, status, position} or null, report or null, enabled}` |
| `POST /api/strategy/<stem>/regenerate` (body `{focus?: "upper" or "lower"}`) | queues a job, `409` if one is queued or running for the stem, `404` if the stem has no match analysis |
| `GET /api/strategy/<stem>.md?lang=en` | Markdown export, `text/markdown`, as a download |

`stale` is true when any file in `inputs` has a different mtime now, or the match record's result, players or
focus changed. The record's relevant fields are hashed into `inputs["record"]`.

### 8.3 UI

- **Where:** a Strategy panel on Match details (`static/kestrel/views/workspace.js`), with its state logic in a
  new pure `static/kestrel/views/strategy_model.js` so it can be tested with `node --test`. New strings go in
  `static/kestrel/core/i18n.js`, in both `zh` and `en`.
- **Content:**
  - the header and badge
  - each section collapsible (summary and game plan open by default)
  - claims with evidence chips (hover shows `id = value unit`)
  - a footer: "AI-written from measured data", the model and the generation time
  - a fallback section shows a small "summary only" tag
- **States:** queued/running (with a link to the Pipeline view), ready, stale (banner plus Regenerate),
  failed (reason in plain words; any fallback text still shown), off (a one-line note on how to enable it).
- **Actions:** Regenerate, Export Markdown, and "Write from the other side's view" (only when both sides are
  registered athletes).
- **Split matches:** the panel follows the current `?part=`.

## 9. Testing

- **`tests/test_strategy_facts.py`:** fixture run directories (singles, doubles, degraded rallies, missing and
  malformed files, with and without `rally_outcomes.json`). Checks fact ids, values, statuses and focus
  resolution.
- **`tests/test_strategy_validate.py`:** an invented number, an unknown id, a claim with no evidence, wrong
  claim counts, the rounding and percentage cases, the ordinal exemption.
- **`tests/test_strategy_llm.py`:** a stub OpenAI-compatible HTTP server on a free local port. Cases:
  success, unreachable, model missing, timeout, invalid JSON, a `<think>` prefix, and an invalid reply followed
  by a valid one on retry.
- **`tests/test_strategy_report.py`:** end-to-end generation with the stub (both languages, partial fallback,
  failed with fallback text), the atomic write, the Markdown render.
- **`tests/test_app_strategy.py`:** a finished match job queues one report job. A failed report job leaves the
  match job's status ok. Regenerate returns 409 while a job is queued. The `stale` computation. Disabled means
  no job.
- **`tests/js/strategy_model.test.mjs`:** panel state from API payloads.
- **The real-LLM run is manual, not in the suite:** `python main_strategy.py` on one singles run
  (`DJI_20261010090046_0010_D`) and one doubles run against local `qwen3.8:27b`. Record the duration, the
  status and the number of sections that fell back, and read both languages by hand.

The full `pytest` and `node --test tests/js/*.test.mjs` suites must pass. The results go in the STATE row and
the PR, recorded as command, result and commit.

## 10. Deferred

- Whole-match report across split parts.
- Trends and normative profiles across matches (they need about 4-7 matches per athlete).
- PDF export.
- A per-job model picker in the analysis wizard (config covers it for now).
- A settings UI for the strategy config.
- Rally-outcome labelling itself (separate session, section 2.1).
- Fixing the local provider in `posture/report_llm.py`.

## 11. Overlap and risks

- `python scripts/ai_overlap.py` (2026-10-27) reports advisory overlap on `app.py`, `i18n.js`, `workspace.js`
  and `docker-compose.yml` with several unmerged branches. Most of them are older, and several are already
  merged by other PRs.
- To limit conflicts, the edits there stay additive:
  - routes in a new blueprint
  - panel state in a new `strategy_model.js`
  - i18n keys under one `strategy.` prefix
- Rebase on `main` before opening the PR.
- **Model quality risk:** a 27B model may cite facts loosely. The number check and the per-section fallback
  bound the damage, and the live check measures how often sections fall back.
- **Latency risk:** two calls at about 600-800 words each, estimated 1-3 minutes in total on the 4090. It runs
  queued, so it never delays the analysis result.
