# Match Strategy Report Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every analysed match gets a queued, LLM-written strategy report (coach-first, plus scouting; singles and
doubles; en and zh), built from measured facts, with claims that are cited and number-checked.

**Architecture:**
- A pure facts pack (`strategy/facts.py`) feeds one OpenAI-compatible JSON-schema call per language
  (`strategy/llm.py`, stdlib `urllib`).
- A validator rejects claims that are uncited or carry numbers no cited fact holds. Failing sections fall back
  to deterministic text.
- `main_strategy.py` writes `strategy_report.json` atomically.
- `app.py` queues a `strategy_<stem>` job in the existing `JobSlots` after a match job completes. A new
  blueprint serves it, and a Strategy tab on Match details renders it.

**Tech Stack:** Python 3 stdlib (no new dependency), Flask blueprint, pytest; Kestrel vanilla ES modules, `node --test`.

**Spec:** `docs/superpowers/specs/2026-10-27-match-strategy-report-design.md` (read it first; section numbers below refer to it).

## Global Constraints

- Defaults:
  - endpoint `http://localhost:11434/v1`
  - model `qwen3.8:27b`
  - key `ollama`
  - timeout `600` s per language
  - langs `en,zh`
  - enabled `on`
- Precedence: env vars `GB_STRATEGY_REPORT`, `GB_STRATEGY_LLM_URL`, `GB_STRATEGY_LLM_MODEL`, `GB_STRATEGY_LLM_KEY`,
  `GB_STRATEGY_LLM_TIMEOUT`, `GB_STRATEGY_LANGS` win over the `strategy` object in `data/settings.json`
  (optional), which wins over the defaults.
- Request body always carries `"reasoning_effort": "none"`, `"temperature": 0.3`, and `response_format`
  `{"type": "json_schema", ...}`. A leading `<think>...</think>` in content is stripped.
- No new Python or JS dependency. No real LLM call in any automated test.
- A report problem never changes the match job's status.
- Short rally means 5 shots or fewer; long rally means 10 or more.
- Never commit `data/`, `outputs/` or `BirdEye Prototype.html`.
- Tests:
  - Python: `PYTHONUTF8=1 ./.venv/Scripts/python.exe -B -m pytest -q -p no:cacheprovider --basetemp <unique> <paths>`
  - JS: `node --test tests/js/<file>.test.mjs`
- Match the surrounding code style: module docstrings that state the contract, total functions that tolerate
  bad input, and bilingual job messages written as `'中文 / English'`.

## Review Focus

1. **A run with no `strokes.json` and no `insights.json`** (analysis crashed half-way): the facts pack must
   still build, the report says "not enough data", and nothing raises. Pinned in Task 2
   (`test_empty_run_dir`).
2. **The model returns valid JSON with the claims' `evidence` as a string, not a list, or extra keys:** the
   validator rejects it and falls back, with no crash. Pinned in Task 3 (`test_evidence_not_list`).
3. **Regenerate clicked while the match itself is being re-analysed:** returns 409 with the busy message, and
   no report is written from half-written files. Pinned in Task 5 (`test_regenerate_blocked_while_analysing`).
4. **The Ollama endpoint hangs (accepts the connection, never answers):** the per-language timeout fires, the
   job ends `error` with reason `timeout`, and the slot is freed. Pinned in Task 1 (`test_timeout`) and Task 5
   (`test_report_job_failure_keeps_match_green`).
5. **Chinese text with full-width digits or `%`** ("命中率６０％"): numbers are normalised before checking, so
   the check is neither bypassed nor falsely failed. Pinned in Task 3 (`test_fullwidth_digits`).

## File Structure

| File | Task | Responsibility |
|---|---|---|
| `badminton_analysis/strategy/__init__.py` | 1 | package marker, `SCHEMA_VERSION = 1` |
| `badminton_analysis/strategy/config.py` | 1 | `load_config(settings_path=None) -> StrategyConfig` |
| `badminton_analysis/strategy/llm.py` | 1 | `LLMClient`, `LLMError` |
| `badminton_analysis/strategy/facts.py` | 2 | `build_facts(out_dir, record, athletes, focus=None) -> dict` |
| `badminton_analysis/strategy/prompts.py` | 3 | `system_prompt(lang, fmt)`, `user_message(pack)`, `report_schema(fmt)` |
| `badminton_analysis/strategy/validate.py` | 3 | `validate_report(doc, pack, fmt) -> dict[section, list[str]]` |
| `badminton_analysis/strategy/report.py` | 4 | `generate`, `fallback_sections`, `write_atomic`, `read_report`, `is_stale`, `render_markdown` |
| `main_strategy.py` | 4 | CLI |
| `badminton_analysis/web/strategy_routes.py` | 5 | the 3 routes |
| `app.py` | 5 | queue the job after a match completes; `queue_strategy_job(stem, focus=None)` |
| `badminton_analysis/web/insights_routes.py` | 5 | `_kind`: `strategy_` prefix gives `strategy` |
| `docker-compose.yml` (and any `docker-compose*.yml` with the app service) | 5 | env and `extra_hosts` |
| `README.md`, `README_EN.md` | 5 | one paragraph each on the report and Docker Ollama |
| `static/kestrel/views/strategy_model.js` | 6 | pure state and view-model functions |
| `static/kestrel/widgets/strategy.js` | 6 | `strategyPanel({stem, lang, api})` |
| `static/kestrel/views/workspace_model.js`, `workspace.js` | 6 | add the `strategy` tab |
| `static/kestrel/views/pipeline.js` | 6 | `kind` `strategy` |
| `static/kestrel/core/i18n.js` | 6 | `strategy.*`, `wsx.tab.strategy`, `job.kind.strategy` in zh and en |
| tests | 1-6 | listed per task |

---

### Task 1: Config and LLM client

**Files:**
- Create: `badminton_analysis/strategy/__init__.py`, `badminton_analysis/strategy/config.py`, `badminton_analysis/strategy/llm.py`
- Test: `tests/test_strategy_llm.py`

**Interfaces:**
- Produces:
  - `StrategyConfig` (a frozen dataclass): `enabled: bool`, `url: str` (no trailing slash), `model: str`,
    `key: str`, `timeout: float`, `langs: tuple[str, ...]` (each in `("en", "zh")`; unknown ones are dropped,
    and an empty result becomes `("en", "zh")`).
  - `load_config(settings_path=None, env=None) -> StrategyConfig`. `env` defaults to `os.environ`. A missing,
    unreadable or non-dict settings file is ignored. `GB_STRATEGY_REPORT` is `off` when its value is one of
    `off`, `0`, `false`, `no` (case-insensitive). A bad timeout falls back to 600.
  - `class LLMError(Exception)`: `.code` in `{"llm_unreachable", "model_missing", "timeout", "bad_response",
    "http_error"}`, plus `.params: dict`.
  - `LLMClient(config)`:
    - `.list_models() -> list[str]` (GET `{url}/models`, the `data[].id` values)
    - `.check_model()` (raises `model_missing{model}`)
    - `.complete_json(system: str, user: str, schema: dict, *, extra_messages=()) -> dict`

- [ ] **Step 1: Write failing tests.** `tests/test_strategy_llm.py` runs a stub server:
  `http.server.ThreadingHTTPServer` on `("127.0.0.1", 0)` in a daemon thread. A handler class reads a
  per-test `responses` list of `(status, body_or_callable, delay_sec)`, and records each request's path and
  JSON body. Tests:

```python
def test_complete_json_parses_content(stub):
    stub.respond(200, {"choices": [{"message": {"content": '{"a": 1}'}}]})
    out = LLMClient(cfg(stub.url)).complete_json("sys", "user", {"type": "object"})
    assert out == {"a": 1}
    body = stub.requests[-1]["body"]
    assert body["model"] == "qwen3.8:27b" and body["reasoning_effort"] == "none"
    assert body["response_format"]["type"] == "json_schema"
    assert stub.requests[-1]["headers"]["Authorization"] == "Bearer ollama"

def test_strips_think_prefix(stub):
    stub.respond(200, {"choices": [{"message": {"content": '<think>hm</think>\n{"a": 2}'}}]})
    assert LLMClient(cfg(stub.url)).complete_json("s", "u", {}) == {"a": 2}

def test_code_fence_json_is_accepted(stub):     # some models wrap JSON in ```json fences
    stub.respond(200, {"choices": [{"message": {"content": '```json\n{"a": 3}\n```'}}]})
    assert LLMClient(cfg(stub.url)).complete_json("s", "u", {}) == {"a": 3}

def test_invalid_json_is_bad_response(stub):
    stub.respond(200, {"choices": [{"message": {"content": "not json"}}]})
    with pytest.raises(LLMError) as e: LLMClient(cfg(stub.url)).complete_json("s", "u", {})
    assert e.value.code == "bad_response"

def test_unreachable():
    with pytest.raises(LLMError) as e:
        LLMClient(cfg("http://127.0.0.1:9/v1")).complete_json("s", "u", {})
    assert e.value.code == "llm_unreachable"

def test_timeout(stub):
    stub.respond(200, {"choices": []}, delay=2.0)
    with pytest.raises(LLMError) as e: LLMClient(cfg(stub.url, timeout=0.5)).complete_json("s", "u", {})
    assert e.value.code == "timeout"

def test_model_missing(stub):
    stub.respond(200, {"data": [{"id": "gemma4:31b"}]})
    with pytest.raises(LLMError) as e: LLMClient(cfg(stub.url)).check_model()
    assert e.value.code == "model_missing" and e.value.params == {"model": "qwen3.8:27b"}

def test_http_500_is_http_error(stub): ...                      # code "http_error", params {"status": 500}
def test_extra_messages_are_appended(stub): ...                 # the retry path: messages == [system, user, *extra]
def test_config_env_beats_settings_beats_default(tmp_path): ... # settings {"strategy": {"model": "x", "url": "http://a/v1/"}}, env GB_STRATEGY_LLM_MODEL=y -> model y, url "http://a/v1"
def test_config_off_values(): ...                               # "OFF", "0", "false", "no" -> enabled False
def test_config_bad_settings_file_ignored(tmp_path): ...        # "[1,2" in file -> defaults
def test_config_langs_filtered(): ...                           # "fr,zh" -> ("zh",); "fr" -> ("en", "zh")
```

- [ ] **Step 2: Run them and see them fail.** `... -m pytest -q tests/test_strategy_llm.py` fails with an
  ImportError.
- [ ] **Step 3: Implement.** Core of `llm.py`:

```python
_THINK = re.compile(r"^\s*<think>.*?</think>\s*", re.S)
_FENCE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.S)

def _parse_content(text):
    text = _THINK.sub("", text or "")
    m = _FENCE.match(text)
    if m:
        text = m.group(1)
    try:
        doc = json.loads(text)
    except ValueError:
        raise LLMError("bad_response", {"why": "json"})
    if not isinstance(doc, dict):
        raise LLMError("bad_response", {"why": "not_object"})
    return doc

def _request(self, method, path, body=None):
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(self.config.url + path, data=data, method=method, headers={
        "Content-Type": "application/json", "Authorization": "Bearer " + self.config.key})
    try:
        with urllib.request.urlopen(req, timeout=self.config.timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise LLMError("http_error", {"status": exc.code})
    except (socket.timeout, TimeoutError):
        raise LLMError("timeout", {"sec": self.config.timeout})
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, (socket.timeout, TimeoutError)):
            raise LLMError("timeout", {"sec": self.config.timeout})
        raise LLMError("llm_unreachable", {})
    except (ValueError, OSError):
        raise LLMError("bad_response", {"why": "body"})
```

  `complete_json` posts to `/chat/completions` with `{"model", "temperature": 0.3, "reasoning_effort": "none",
  "messages": [...], "response_format": {"type": "json_schema", "json_schema": {"name": "strategy_report",
  "strict": True, "schema": schema}}}`. It reads `choices[0].message.content`; a missing path raises
  `bad_response`. Never log the key.
- [ ] **Step 4: Run the tests; they pass.**

---

### Task 2: Facts pack

**Files:**
- Create: `badminton_analysis/strategy/facts.py`
- Test: `tests/test_strategy_facts.py` (uses `tests/insights_fixtures.py` `make_run`, `make_doubles_run`)

**Interfaces:**
- Consumes:
  - `badminton_analysis.insights.sidecar.compute_video(stem, out_dir)` (the insight envelopes: `rally`,
    `strokes`, `technique`, `movement`, `recovery`, `pull`, `doubles`, `quality`, `match_format`)
  - `badminton_analysis.insights.loaders.load_run(out_dir)` (`Run.strokes`, `Run.rallies_payload`, `Run.fps`)
  - `badminton_analysis.library.athletes.side_athlete(data, record, side)` and
    `player_athlete(data, record, pid)`
- Produces `build_facts(out_dir, record, athletes, focus=None) -> dict`:

```python
{"format": "singles"|"doubles",
 "focus": {"side": "lower"|"upper"|None, "team": 1|2|None, "athlete_id": str|None, "assumed": bool},
 "header": {"you": str|None, "opponent": str|None, "date": str|None, "winner": "you"|"opponent"|None,
            "sets": [[int, int]]|None, "part_of": str|None, "confidence": "high"|"medium"|"low",
            "both_registered": bool},
 "facts": [{"id": str, "value": number|str|list|dict|None, "unit": str|None, "who": str,
            "status": "ok"|"partial"|"unavailable", "reason": str|None, "validated": bool}],
 "caveats": [{"code": str, "params": dict}],
 "inputs": {"<file name>": mtime_float, "record": "<sha1 of relevant record fields>"}}
```

  Also `fact_map(pack) -> dict[id, fact]`, and `INPUT_FILES` (the file names whose mtimes feed `inputs`:
  `insights.json`, `strokes.json`, `rally_segments.json`, `hits.json`, `players.json`, `tracklets.jsonl`,
  `technique_summary.json`, `rally_outcomes.json`).
  - `record` is the resolved match record (`athletes.resolve_record`) or `{}`.
  - `athletes` is the store dict (for names).
  - `focus` (`"upper"` or `"lower"`) overrides the choice.
  - `who` values: `match`, `you`, `opponent`, and for doubles `you.P1` and `opponent.P3`.

**Fact ids.** These are exact; the validator and the prompts rely on them.
- **Match:**
  - `match.rallies` (count)
  - `match.rally_median_sec`, `match.rally_p90_sec`, `match.rally_median_shots`
  - `match.short_rally_share`, `match.long_rally_share` (0..1 over rallies with a shot count)
  - `match.tempo_shots_per_sec` (sum of shots / sum of durations)
  - `match.degraded_share`
  - `match.result` (value `{"winner": "you"|"opponent", "sets": [[you, opp]...]}`; sets are reordered so that
    "you" comes first)
- **Singles, per `p` in (`you`, `opponent`):**
  - `p.mix.attack_share`, `p.mix.neutral_share`, `p.mix.defence_share`, where attack = smash, drive, drop;
    neutral = net, push; defence = clear, lift, block; `serve` is excluded from the mix. Shares are over the
    classified non-serve strokes of that hitter.
  - `p.serve.count`, `p.serve.short_share` (when serve subtypes exist in `stroke`, else unavailable)
  - `p.net_share`, `p.unknown_share`
  - `p.groups` (value: the raw distribution dict)
  - `p.seq2_top` (value: up to 3 `{"seq": ["lift", "smash"], "n": int}`, where the pair is the opponent's
    stroke followed by `p`'s reply, inside one rally window)
  - `p.seq3_top` (same idea, with 3-stroke windows ending in `p`'s stroke)
  - `p.after_lift` (value: the distribution of `p`'s reply to a lift received)
  - `p.distance_m`, `p.mean_speed_mps`, `p.base` (`{x, y}`)
  - `p.zone.front`, `p.zone.mid`, `p.zone.rear`, `p.zone.left`, `p.zone.centre`, `p.zone.right` (shares from
    the 11x10 movement grid: rows 0-3 front, 4-7 mid, 8-10 rear from that side's net; cols 0-2 left, 3-6
    centre, 7-9 right)
  - `p.recovery_median_sec`
  - `p.pulled.front`, `p.pulled.mid`, `p.pulled.back`, `p.pulled.left`, `p.pulled.right`, `p.pulled.n`
  - `p.technique_index`, `p.technique_weak` (value: up to 2 stroke names with the lowest `avg_score` among
    those with `count >= 3`)
  - `p.last_stroke` (value: the distribution of the final stroke of rallies that `p` hit last)
- **Doubles:**
  - `team.you.formation.front_back_share` and `team.opponent.formation.front_back_share`: per in-play frame
    where both team players have a tracklet point, front-back when `|dy| >= 1.5 m` and `|dx| < 1.5 m`,
    side-by-side when `|dx| >= 1.5 m` and `|dy| < 1.5 m`; other frames are excluded.
  - `team.you.rotations_per_rally` (front/back role swaps per rally; the front player is the one closer to the
    net)
  - `team.<t>.uncovered` (value: up to 3 zone names, from the 9 front/mid/rear x left/centre/right cells,
    where the team's merged grid share is below 0.02)
  - `you.P1.hit_share` (and the same per player): `validated: false`, from the doubles insight
    `hits_named / team hits_named`
  - `you.P1.distance_m`, `you.P1.zone.front` and the rest (from the per-player grid, same zone rule)
- **Outcomes** (only when `rally_outcomes.json` exists and is readable; otherwise no outcome ids at all, plus
  the caveat `no_rally_outcomes`):
  - `p.points_won`
  - `p.won_by` (dict ending_stroke -> n)
  - `p.lost_by` (dict outcome -> n for rallies `p` lost)
  - `match.outcome_coverage`
  - Status `partial` when coverage < 0.5.

**Focus rule** (§4):
- singles: the side whose record entry has `athlete_id` (both: `lower`; none: `lower` with `assumed: true`)
- doubles: the team containing a P-id whose record player has `athlete_id`, with teams from the doubles
  insight (none: team 1 with `assumed: true`)
- `focus` overrides the side or team.

**Confidence:**
- `low` when any of these holds: `match.degraded_share > 0.3`; the rally envelope is unavailable; singles with
  classified / total < 0.5; doubles with status not `ok`.
- `high` when the rally status is ok, singles classified share >= 0.75, and no degraded rallies.
- `medium` otherwise.

**Caveats** (codes):
- `no_rally_outcomes`
- `doubles_strokes_withheld` (doubles)
- `doubles_hits_unvalidated` (doubles)
- `degraded_rallies{share}` (when > 0)
- `rally_signal{signal}` (when the rally envelope is `partial`, for example swing-derived)
- `focus_assumed`
- `missing{files}` (input files absent)

Every unavailable fact carries `reason` = the envelope reason code, or `no_data`.

- [ ] **Step 1: Write failing tests:**

```python
def test_singles_focus_is_registered_side(tmp_path):
    out = make_run(tmp_path, rallies=[(0, 5), (6, 20)], strokes=[("upper", "clear", 10), ("lower", "smash", 20), ("upper", "lift", 200), ("lower", "smash", 230)], frames=700)
    rec = {"upper": {"name": "Sally"}, "lower": {"athlete_id": "a1"}, "result": {"winner": "lower", "sets": [[4, 30]]}}
    pack = build_facts(out, rec, {"athletes": {"a1": {"name": "Val"}}, "matches": {}})
    assert pack["focus"] == {"side": "lower", "team": None, "athlete_id": "a1", "assumed": False}
    f = fact_map(pack)
    assert f["match.result"]["value"] == {"winner": "you", "sets": [[30, 4]]}
    assert f["you.mix.attack_share"]["value"] == 1.0
    assert f["you.seq2_top"]["value"][0] == {"seq": ["lift", "smash"], "n": 1}

def test_focus_override_and_assumed(tmp_path): ...       # focus="upper" -> side upper; no athlete_id -> assumed True + caveat focus_assumed
def test_doubles_has_no_stroke_facts(tmp_path): ...      # make_doubles_run: no "you.mix.*", has team.you.formation.front_back_share, caveats include doubles_strokes_withheld and doubles_hits_unvalidated, hit_share validated False
def test_no_rally_outcomes_caveat(tmp_path): ...         # no file -> no "*.points_won" ids, caveat present
def test_rally_outcomes_partial(tmp_path): ...           # file with 1 of 4 rallies known -> you.points_won status partial
def test_empty_run_dir(tmp_path): ...                    # (Review Focus 1) empty dir -> pack builds, match.rallies unavailable, confidence low, caveat missing
def test_confidence_levels(tmp_path): ...                # degraded share 0.5 -> low
def test_inputs_record_hash_changes_with_result(tmp_path): ...
def test_short_long_share(tmp_path): ...                 # shots [2,4,7,5,11] -> short 3/5, long 1/5
```

- [ ] **Step 2: Run them and see them fail.**
- [ ] **Step 3: Implement** `facts.py` per the rules above. Helpers: `_fact(id, value, who, unit=None,
  status="ok", reason=None, validated=True)` and `_unavailable(id, who, reason)`. Each group is built inside
  its own `try/except Exception`, so one failing group makes its facts `unavailable` with `no_data` and never
  raises. Round shares to 3 decimal places and seconds to 2.
- [ ] **Step 4: Run the tests; they pass.**

---

### Task 3: Prompts and validator

**Files:**
- Create: `badminton_analysis/strategy/prompts.py`, `badminton_analysis/strategy/validate.py`
- Test: `tests/test_strategy_validate.py`

**Interfaces:**
- Consumes: the `build_facts` pack, `fact_map`.
- Produces:
  - `LLM_SECTIONS = ("summary", "match_profile", "your_game", "game_plan", "scouting")`
  - `report_schema(fmt) -> dict`: a JSON Schema. A claim is `{"text": str, "evidence": [str, ...]}` with
    `minItems` 1 for evidence.
    - `summary`: `{"claims": [claim x3]}` (minItems 3, maxItems 3)
    - `match_profile`: `{"claims": 2..4}`
    - `your_game`: `{"shot_selection": {"claims": 1..3}` (singles only; the key is absent from the doubles
      schema), `"movement": {"claims": 1..3}, "patterns": {"claims": 1..3}, "strengths": {"claims": 2..3},
      "weaknesses": {"claims": 2..3}}`
    - `game_plan`: `{"items": [{"do", "why", "cue", "evidence"}] 3..5, "training_focus": claim|null}`
    - `scouting`: `{"claims": 2..4}`
    - `additionalProperties: false` everywhere.
  - `system_prompt(lang, fmt) -> str`: the rules from §6.2 and the word caps (summary 80, match_profile 80,
    your_game 300, game_plan 160, scouting 100). For `zh`: "用简体中文". For doubles: "Hit counts per player
    are estimated (not validated); say so."
  - `user_message(pack) -> str`: compact JSON of `format`, `focus`, `header` and `facts`, without `inputs`.
  - `validate_report(doc, pack, fmt) -> dict[str, list[str]]`: the errors per LLM section; an empty dict
    means valid. Section keys are those in `LLM_SECTIONS`.

**Validator rules (§6.3):**
- **Structure:** counts within the bounds above. `evidence` must be a list of strings; a string or a number
  is an error (Review Focus 2). Unknown keys are an error. Every evidence id must be in `fact_map(pack)`. A
  cited fact whose status is `unavailable` is an error unless the claim text says there is not enough data
  (the claim text then contains no digits).
- **Numbers:** normalise the text with `unicodedata.normalize("NFKC", text)` (full-width digits and `％`,
  Review Focus 5), then find `re.findall(r"\d+(?:\.\d+)?%?", text)`.
  - Exempt: integers from 1 to 3 without `%` (ordinals and list counts), and numbers that are part of a
    fact-id-looking token (skip matches inside `[a-z_.]+\d`).
  - For each other token, the claim is valid when some numeric leaf `v` of a cited fact's value (walked
    recursively through dicts and lists, ignoring bools) satisfies one of these:
    - `abs(float(tok) - v) <= tol`, where `tol` = half a unit of the token's last decimal place (`"6.3"` ->
      0.05, `"6"` -> 0.5);
    - for a `%` token: `abs(tok - 100 * v) <= tol`, or `abs(tok - v) <= tol`;
    - for a share fact (unit `share`, 0..1) without `%`: `abs(tok - 100 * v) <= tol`.
  - Otherwise: an error `"number 37 in <section>[i] not in cited facts"`.

- [ ] **Step 1: Write failing tests** with a small hand-made pack: `match.rally_median_sec` = 6.27 s,
  `you.mix.attack_share` = 0.42 (unit share), `you.pulled.n` = 20, `match.result` = `{"winner": "you",
  "sets": [[30, 4]]}`, and one unavailable fact.

```python
def test_valid_report_has_no_errors(): ...
def test_invented_number(): ...                      # "attack 55%" citing attack_share 0.42 -> error in summary
def test_percent_of_share_ok(): ...                  # "42%" and "42 percent" citing 0.42 -> ok
def test_rounding_ok(): ...                          # "6.3 s" citing 6.27 -> ok; "6.4" -> error
def test_ordinal_exempt(): ...                       # "3 things" with no number fact -> ok
def test_unknown_evidence_id(): ...
def test_evidence_not_list(): ...                    # (Review Focus 2) evidence "match.rallies" -> error, no exception
def test_claim_count_bounds(): ...                   # summary with 2 claims -> error
def test_fullwidth_digits(): ...                     # (Review Focus 5) "命中率４２％" citing 0.42 -> ok; "５５％" -> error
def test_set_scores_from_list_value(): ...           # "won 30-4" citing match.result -> ok
def test_unavailable_fact_needs_no_data_phrase(): ...
def test_doubles_schema_has_no_shot_selection(): ...
def test_extra_key_rejected(): ...
```

- [ ] **Step 2: Run them and see them fail.**
- [ ] **Step 3: Implement** `prompts.py` and `validate.py`. Prompts are plain module constants composed with
  `str.format`. The `zh` rules are written in Chinese.
- [ ] **Step 4: Run the tests; they pass.**

---

### Task 4: Report generation, storage, Markdown, CLI

**Files:**
- Create: `badminton_analysis/strategy/report.py`, `main_strategy.py`
- Test: `tests/test_strategy_report.py` (reuses the stub server pattern from Task 1: copy the small stub class
  into a shared `tests/strategy_stub.py`, and have Task 1's test import it too)

**Interfaces:**
- Consumes: Tasks 1-3.
- Produces:
  - `REPORT_FILE = "strategy_report.json"`.
  - `generate(out_dir, record, athletes, config, *, focus=None, client=None, clock=time.time) -> dict`: the
    full report document of spec §5. It never raises.
    - Model check: `client.check_model()` runs once. On `LLMError`, every language gets
      `fallback_sections` and the doc gets `status: "failed"` with `reason: {"code", "params"}`.
    - Per language: `complete_json`, then `validate_report`. On errors, one retry with
      `extra_messages=[{"role": "assistant", "content": json.dumps(first)}, {"role": "user", "content":
      "Fix these problems and return the full JSON again: " + "; ".join(errors)}]`. Sections still invalid get
      the fallback text and `llm: false`.
    - An `LLMError` on one language makes that language all fallback, and the doc status `failed` with that
      reason. Otherwise the status is `partial` when any section fell back (`reason: {"code":
      "sections_fallback", "params": {"en": [...], "zh": [...]}}`), else `ok`.
    - Code-built sections: `header` comes from `pack["header"]` with `status` and `llm: false`; `caveats`
      comes from `pack["caveats"]`.
    - Each LLM section gets `status: "ok"`, `llm: true` merged in.
  - `fallback_sections(pack, lang, fmt) -> dict`: the deterministic LLM-section replacements. One claim per
    available key fact, using fixed bilingual templates ("Median rally: {v} s." / "回合中位时长：{v} 秒。"),
    with `evidence` set to that id. It satisfies `validate_report`. Each section is marked `llm: false`,
    `status: "fallback"`.
  - `write_atomic(out_dir, doc)` (a temp file in the same dir, then `os.replace`).
  - `read_report(out_dir) -> dict|None` (None when missing or malformed).
  - `is_stale(out_dir, doc, record) -> bool`: compares `doc["inputs"]` with `facts.current_inputs(out_dir,
    record)`. Add `current_inputs` to `facts.py`; it is the same function `build_facts` uses for `inputs`.
  - `render_markdown(doc, lang) -> str`.

- [ ] **Step 1: Write failing tests:**

```python
def test_generate_ok_both_langs(tmp_path, stub): ...         # stub returns a valid doc for the fixture pack (build it from fallback_sections(pack, lang, fmt) with llm flags stripped) -> status ok, by_lang en/zh, llm True
def test_retry_then_ok(tmp_path, stub): ...                  # first reply has an invented number, second valid -> ok, 2 chat calls per lang
def test_retry_then_section_fallback(tmp_path, stub): ...    # both replies bad in scouting only -> status partial, scouting llm False
def test_unreachable_gives_failed_with_fallback(tmp_path): ...  # status failed, reason llm_unreachable, by_lang.en.summary has claims
def test_model_missing_failed(tmp_path, stub): ...
def test_write_atomic_and_read(tmp_path): ...
def test_read_report_malformed_is_none(tmp_path): ...
def test_is_stale_after_input_touch(tmp_path): ...           # os.utime insights.json -> True
def test_is_stale_after_record_change(tmp_path): ...
def test_markdown_has_sections_and_evidence(tmp_path): ...   # "## Summary", "match.rally_median_sec" listed under Evidence
def test_cli_writes_report(tmp_path, stub): ...              # subprocess: python main_strategy.py <dir> --url stub.url --athletes <file> -> exit 0, file exists
def test_cli_exit_code_on_failed(tmp_path): ...              # unreachable -> exit 2, report still written
```

- [ ] **Step 2: Run them and see them fail.**
- [ ] **Step 3: Implement.** `main_strategy.py` arguments:
  - `result_dir`
  - `--athletes PATH` (default `data/athletes.json`)
  - `--settings PATH` (default `data/settings.json`)
  - `--focus {upper,lower}`
  - `--url`, `--model`, `--langs` (override the config)

  It resolves the record with `athletes.load` and `athletes.resolve_record(data, stem)`, where `stem` is the
  dir name; a missing store means `{}`. It writes `progress.json`-free output (the job tracks the process
  only) and prints one line per stage. Exit codes: 0 for `ok` or `partial`, 2 for `failed`, 1 for unexpected
  exceptions (and it still tries to write a fallback doc).
- [ ] **Step 4: Run the tests; they pass. Then run all `tests/test_strategy_*.py`.**

---

### Task 5: Job queue, API, Docker, README

**Files:**
- Create: `badminton_analysis/web/strategy_routes.py`
- Modify:
  - `app.py`: the match `track_progress` success branch (around line 1099, after `job['result'] = {...}`);
    add `queue_strategy_job`; register the blueprint next to `insights_routes.register`
  - `badminton_analysis/web/insights_routes.py:221` (`_kind`)
  - `docker-compose.yml` and other compose files defining the app service
  - `README.md`, `README_EN.md`
- Test: `tests/test_app_strategy.py`; extend `tests/test_app_jobs.py` with one `kind` test

**Interfaces:**
- Consumes: `strategy.config.load_config`, `strategy.report.read_report`, `is_stale`, `render_markdown`,
  `REPORT_FILE`.
- Produces:
  - `app.queue_strategy_job(stem, focus=None) -> (ok: bool, job_id: str, queued: bool)`. Job id
    `'strategy_' + stem`. The job dict is `{'status': 'pending', 'progress': 0, 'message': '等待生成策略报告 /
    waiting to write the strategy report', 'video_name': stem, 'save_dir': ..., 'stage': 'strategy', 'kind':
    'strategy', 'updated': time.time()}`.
    - It returns `(False, job_id, False)` when `_register_job` refuses (already active) or the analysis job
      `stem` is active.
    - `start(release)` launches `[_venv_python, 'main_strategy.py', save_dir, '--athletes', ATHLETES_PATH,
      '--settings', <DATA>/settings.json] (+ ['--focus', focus])`, logging to `strategy.log`, through
      `_start_tracker`.
    - Its tracker waits on the process. Exit 0: `completed`, progress 100, message `'策略报告已生成 / strategy
      report ready'`. Otherwise: `error`, with the message taken from the report's reason code
      (`llm_unreachable` -> `'无法连接 LLM 服务 / LLM endpoint unreachable'`, `model_missing` -> `'模型未安装 /
      model not installed: <model>'`, `timeout` -> `'LLM 超时 / LLM timed out'`, else `'策略报告失败 /
      strategy report failed'`).
    - It never touches the match job dict.
  - Auto-queue: in the match success branch, `if load_config(settings).enabled: queue_strategy_job(vs)`,
    wrapped in `try/except Exception` that only prints.
  - Routes (blueprint `strategy`, registered with getters
    `register(app, outputs=, athletes_path=, athletes_lock=, jobs=, queue=, safe_stem=, settings_path=)`):
    - `GET /api/strategy/<stem>` returns `{"enabled", "status", "stale", "job", "report"}`:
      - `status` is `none` when there is no file and no job; otherwise the job status when a job is active;
        otherwise the report's `status`.
      - `job` is `{"id", "status", "position"}` or null.
      - The athletes store is read under `athletes_lock` to resolve the record for `is_stale`.
      - 404 when the stem is unsafe or has no output dir.
    - `POST /api/strategy/<stem>/regenerate`, body `{"focus"?}`: focus must be `upper`, `lower` or absent
      (else 400). 404 without a `rally_segments.json` or `insights.json` in the output dir. 409 `_JOB_BUSY`
      when `queue` refuses. 200 `{"ok": true, "job_id", "queued", "position"}`.
    - `GET /api/strategy/<stem>.md?lang=en|zh` returns `text/markdown; charset=utf-8` with
      `Content-Disposition: attachment; filename="<stem>-strategy-<lang>.md"`. 404 when there is no report.
      An unknown lang falls back to `en`.
  - `_kind`: `str(job_id).startswith('strategy_')` returns `'strategy'`. The detached-row loop also skips
    `'strategy_' + path.name in known`.
- Compose: add to the app service `environment: GB_STRATEGY_LLM_URL: ${GB_STRATEGY_LLM_URL:-http://host.docker.internal:11434/v1}`
  and `GB_STRATEGY_LLM_MODEL: ${GB_STRATEGY_LLM_MODEL:-qwen3.8:27b}`, plus `extra_hosts:
  ["host.docker.internal:host-gateway"]`.
- README (both languages, in the Docker section and in the feature list): what the report is, the 3 main env
  vars, that Ollama must serve the model (`ollama pull qwen3.8:27b`), and `GB_STRATEGY_REPORT=off` to disable.

- [ ] **Step 1: Write failing tests** in `tests/test_app_strategy.py`, using the `env` fixture pattern of
  `tests/test_app_jobs.py`. Monkeypatch `webapp.subprocess.Popen` with a fake process that has
  `poll/wait/returncode`, and monkeypatch `webapp.OUTPUTS` and `ATHLETES_PATH`.

```python
def test_completed_match_queues_one_strategy_job(env, monkeypatch): ...  # call the success path helper; jobs has 'strategy_clip' with kind 'strategy'
def test_disabled_queues_nothing(env, monkeypatch): ...                 # GB_STRATEGY_REPORT=off
def test_report_job_failure_keeps_match_green(env, monkeypatch): ...    # (Review Focus 4) fake proc exit 2 + report reason timeout -> strategy job error "LLM 超时 / LLM timed out", jobs['clip'] still completed, slot released (_job_slots.snapshot running == 0)
def test_regenerate_409_when_queued(env): ...
def test_regenerate_blocked_while_analysing(env): ...                   # (Review Focus 3) jobs['clip'] running -> 409
def test_regenerate_bad_focus_400(env): ...
def test_get_none_then_report(env): ...                                 # no file -> status none; write a report -> status ok, stale False; touch insights.json -> stale True
def test_markdown_download(env): ...
def test_unsafe_stem_404(env): ...
def test_jobs_list_kind_strategy(env): ...                              # /api/jobs row kind == 'strategy'
```

  To make the success path testable, extract the post-completion queueing into
  `_after_match_completed(stem)` in `app.py`, called from `track_progress`, and test that.
- [ ] **Step 2: Run them and see them fail.**
- [ ] **Step 3: Implement.** Run `python scripts/ai_overlap.py app.py badminton_analysis/web docker-compose.yml`
  first; the overlap is advisory and already known (spec §11).
- [ ] **Step 4: Run the new tests plus `tests/test_app_jobs.py`, `tests/test_app_insights.py` and
  `tests/test_app_delete.py`; they pass.**

---

### Task 6: Strategy tab UI

**Files:**
- Create: `static/kestrel/views/strategy_model.js`, `static/kestrel/widgets/strategy.js`
- Modify:
  - `static/kestrel/views/workspace_model.js:371` (`TABS`: append `'strategy'`)
  - `static/kestrel/views/workspace.js` (`tabBody`: `id === 'strategy'` -> `strategyPanel({stem: s, lang})`;
    `wholeTabBody`: treat `strategy` like `clips`/`plan` and point at the parts; header comment)
  - `static/kestrel/views/pipeline.js:50` (`kind`: posture, strategy, or match; a completed strategy job's
    `resultsHash` is `formatHash('matches', { stem })`)
  - `static/kestrel/core/i18n.js`
- Test: `tests/js/strategy_model.test.mjs`, `tests/js/strategy_widget.test.mjs` (use `tests/js/fake_dom.mjs`
  the way `tests/js/tabs_widgets.test.mjs` does); update any test asserting `TABS` or the tab count.

**Interfaces:**
- Consumes: the `GET /api/strategy/<stem>` payload (Task 5), the report doc (spec §5).
- Produces:
  - `strategy_model.js` exports:
    - `panelState(payload) -> 'loading'|'off'|'none'|'queued'|'running'|'ready'|'stale'|'failed'`. `ready` is
      a report with status ok or partial and not stale; `failed` is a report with status failed or a job with
      status error.
    - `reportFor(payload, lang)`: `by_lang[lang]`, falling back to the other language.
    - `claimsOf(section)`: a flat list of `{text, evidence}` from a section's `claims` or the `your_game`
      subsections, in order.
    - `evidenceText(ids, facts)`: `"id = value unit"` lines, joining list and dict values compactly.
    - `isFallback(section)`: `section.llm === false`.
  - `strategyPanel({stem, lang, api}) -> {el, destroy}`:
    - It loads `GET /api/strategy/<stem>` and polls every 3 s while the state is queued or running (cleared on
      destroy).
    - It renders, with all text from the doc set as text (never HTML):
      - the header (names, date, result, confidence chip, focus-assumed note)
      - the sections as `<details>`, with `summary` and `game_plan` open
      - claims with evidence chips (a `title` with `evidenceText`)
      - a `strategy.fallback` tag on fallback sections
      - the caveats list
      - the footer (`strategy.footer`, with the model and the time)
    - Buttons: Regenerate (POST, then switch to queued), Export Markdown (an `<a href="/api/strategy/<stem>.md?lang=<lang>" download>`), and "other side" (only when `report.header.both_registered === true`), which POSTs `{focus: <other side>}`.
    - States: queued or running show `strategy.state.queued` or `strategy.state.running` and a link `#/pipeline`;
      stale shows a banner `strategy.state.stale`; failed shows `strategy.reason.<code>` (fallback
      `strategy.reason.unknown`) plus any fallback content; off shows `strategy.state.off`; none shows
      `strategy.state.none` and Regenerate labelled `strategy.generate`.
- i18n keys (both `zh` and `en`):
  - `wsx.tab.strategy` (策略 / Strategy)
  - `job.kind.strategy` (策略报告 / Strategy report)
  - `strategy.sec.summary`, `.match_profile`, `.your_game`, `.shot_selection`, `.movement`, `.patterns`,
    `.strengths`, `.weaknesses`, `.game_plan`, `.scouting`, `.caveats`
  - `strategy.plan.do`, `.why`, `.cue`, `strategy.training_focus`
  - `strategy.conf.high`, `.medium`, `.low`, `strategy.focus_assumed`
  - `strategy.state.queued`, `.running`, `.stale`, `.off`, `.none`
  - `strategy.reason.llm_unreachable`, `.model_missing`, `.timeout`, `.unknown`
  - `strategy.caveat.no_rally_outcomes`, `.doubles_strokes_withheld`, `.doubles_hits_unvalidated`,
    `.degraded_rallies`, `.rally_signal`, `.focus_assumed`, `.missing`
  - `strategy.regen`, `strategy.generate`, `strategy.export`, `strategy.other_side`, `strategy.fallback`,
    `strategy.footer` (`AI 根据测量数据撰写 · {model} · {time}` / `AI-written from measured data · {model} · {time}`)
  - Run the existing `tests/js/i18n.test.mjs`; it checks zh/en key parity.

- [ ] **Step 1: Write failing tests:**

```js
test('panelState maps payloads', () => {
  assert.equal(panelState(null), 'loading');
  assert.equal(panelState({ enabled: false, status: 'none' }), 'off');
  assert.equal(panelState({ enabled: true, status: 'none' }), 'none');
  assert.equal(panelState({ enabled: true, status: 'pending', job: { status: 'pending' } }), 'queued');
  assert.equal(panelState({ enabled: true, status: 'ok', stale: true, report: {} }), 'stale');
  assert.equal(panelState({ enabled: true, status: 'failed', report: {} }), 'failed');
});
test('reportFor falls back to the other language', ...);
test('claimsOf flattens your_game subsections in order', ...);
test('evidenceText formats values', ...);
// widget (fake DOM): ready payload renders 5 LLM sections + caveats, a fallback tag on llm:false, no innerHTML use (a claim text '<b>x</b>' renders literally), Regenerate POSTs and switches to queued, destroy clears the poll timer
```

- [ ] **Step 2: Run them and see them fail.**
- [ ] **Step 3: Implement.** Reuse the existing classes (`ov-btn small`, `k-chip`, `k-label`, `mv-hint`, `mv-err`,
  `kw-muted`). Add only minimal CSS (`ws-strat-*`) in the stylesheet the plan widget's `ws-plan-*` rules live
  in (find it with grep), using the existing tokens.
- [ ] **Step 4: Run `node --test tests/js/*.test.mjs`; all pass.**

---

### Task 7: Verification, live check, STATE, PR

- [ ] Full suites: `PYTHONUTF8=1 ./.venv/Scripts/python.exe -B -m pytest -q -p no:cacheprovider --basetemp <unique>` and
  `node --test tests/js/*.test.mjs`. Record the counts.
- [ ] qa-reviewer pass over the branch diff against the spec's done-means list; fix the findings.
- [ ] Live check: copy the real singles run `outputs/DJI_20261010090046_0010_D` (main checkout) into the scratchpad,
  then run `python main_strategy.py <copy>`. Do the same for a doubles run if one exists in `outputs/`. Record the
  duration, the status and the fallback sections, and read both languages.
- [ ] UI check in the preview server (worktree preview on port 5071 per project memory): the Strategy tab renders
  the copied report.
- [ ] Update the `.ai/STATE.md` row (verification lines: command, result, commit), rebase on `origin/main`,
  push the branch, open the PR, merge after the checks.
