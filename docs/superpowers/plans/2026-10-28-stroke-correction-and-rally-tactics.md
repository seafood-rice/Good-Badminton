# Stroke Correction, Retraining and Rally Tactics Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The owner corrects strokes on the match details page; corrections apply at once, retrain the
stroke classifier on demand (kept only if it scores at least as well), and feed a rally-tactics part of the
strategy report (effectiveness tally, opening table, overview additions).

**Architecture:** An owner-label store (`stroke/owner_labels.py`) mirrors `outcomes/labels.py` and overlays
labels onto `strokes.json` reversibly. A retrain job (shared `JobSlots`) runs the existing trainer with a new
label loader, `stroke/model_eval.py` scores candidate vs live model on the owner test split and promotes or
rejects, then a relabel job re-applies labels everywhere. `strategy/shots.py` builds a per-shot table that
`strategy/facts.py` turns into cite-able facts; the Strategy tab renders the tables from the facts.

**Tech Stack:** Python 3 / Flask / numpy / torch (existing), vanilla ES modules (Kestrel), pytest, `node --test`.

**Spec:** `docs/superpowers/specs/2026-10-28-stroke-correction-and-rally-tactics-design.md` (read it first;
section numbers below refer to it).

## Global Constraints

- Owner labels live in `data/stroke_labels/<stem>.json` (outside `outputs/`), schema 1, revisioned, atomic writes.
- Label kinds: `label`, `not_hit`, `unsure`, `added`; strokes are the 12 ids in `stroke/stroke_types.py`.
- Attach tolerance 0.15 s, one label per detected hit (nearest wins); max 5000 labels per file.
- Split `train` (default) / `test`; locked once used (model sidecar `train_matches`/`val_matches`, or a scored
  test video in `weights/stroke-model-history.json`).
- Retrain enabled at >= 50 new train labels and >= 20 labelled test shots.
- Keep rule: candidate 7-group accuracy >= live model's on the identical test shots AND candidate unknown rate <= 0.15
  (no live model: unknown <= 0.15). History keeps the last 5 replaced models under `weights/history/<utc>/`.
- Job kind `training`, ids `train_strokes` and `relabel_strokes`; one at a time; cancellable; pause answers 409.
- Shot table constants (spec 7.1): front <= 1.98 m, rear > 4.7 m from the net (net y = 6.7, court 6.1 x 13.4);
  thirds of 6.1 m, player's own view; fast < 0.7 s, slow > 1.5 s; rushed >= 2.5 m within <= 1.2 s; broken link
  = same hitter or gap > 2.5 s; position check fails when > 40 % of in-play frames are within 0.25 m of the
  half's outline or the net; position lookup +-8 frames; < 5 attempts never a strength/weakness; < 5 complete
  openings = no opening claims; up to 8 opening rows.
- Every UI string EN + ZH in `static/kestrel/core/i18n.js`. API error bodies are static bilingual strings.
- Never commit `BirdEye Prototype.html` or `data/`. Never bare `git stash`. Commits end with
  `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Tests: `PYTHONUTF8=1 ./.venv/Scripts/python.exe -B -m pytest -q -p no:cacheprovider --basetemp <unique>`
  (venv lives in the main checkout: `D:/Dev/Claude/Projects/good-badminton/Good-badminton/.venv`), and
  `node --test tests/js/*.test.mjs`.

## Review Focus

1. A label file for a video with no `strokes.json` (labels withheld on degraded segmentation): the save
   succeeds, `apply` is skipped with `apply_skipped: "no_strokes"`, nothing is created. (Task 3 test.)
2. Applying twice, or relabel then apply: the result equals a single apply on the model output (idempotent,
   reversible through `model_fields`). (Task 1 test.)
3. Two detected hits within 0.15 s of one label: only the nearest takes it; the other stays model. Two labels
   on one hit: the later one in the list wins, the earlier is reported orphaned. (Task 1 test.)
4. No `data/hit_dataset` (e.g. a fresh container): the retrain job validates on the owner train video with the
   most labels instead of the ShuttleSet match, and says so; with no train labels at all it fails with a clear
   message instead of a traceback. (Task 5 test.)
5. A match with 0 complete openings, all links broken and no rally winners: facts are `insufficient` /
   `unavailable`, the report generates, the Strategy tab shows empty-state text, nothing raises. (Task 7/8 tests.)

---

### Task 1: Owner stroke-label store and overlay

**Files:**
- Create: `badminton_analysis/stroke/owner_labels.py`
- Test: `tests/test_stroke_owner_labels.py`

**Interfaces (Produces):**
- `SCHEMA = 1`, `KINDS = ("label", "not_hit", "unsure", "added")`, `SPLITS = ("train", "test")`,
  `MAX_LABELS = 5000`, `ATTACH_TOLERANCE_SEC = 0.15`
- `empty() -> {"schema": 1, "revision": 0, "split": "train", "labels": []}`
- `validate_labels(raw) -> list | None` (normalised list; None if any item invalid). Item:
  `{"time_sec": float>=0, "hitter": str (non-empty, <= 32 chars), "stroke": str|None, "kind": str}`;
  `stroke` required (a valid id) for `label` and `added`, forced None for `not_hit`/`unsure`.
- `labels_path(labels_dir, stem) -> Path`; `load(path) -> dict | None` (`empty()` when absent, None when
  unreadable/invalid); `save(path, doc)` (reuse `outcomes.labels.write_json_atomic`).
- `model_view(doc) -> dict`: a strokes.json payload with any previous overlay removed (restore
  `model_stroke`/`model_group`/`model_hitter`, drop `source == "owner"` added rows, put `removed` rows back,
  drop `owner_labels`, `source`, `removed`; recompute `distribution`/`groups`).
- `attach(labels, strokes, fps) -> (by_index: {stroke_index: label}, orphaned: [label], added: [label])`:
  hit time = `frame / fps`; each non-`added` label goes to the nearest stroke within 0.15 s; one label per
  stroke, the later label in list order wins and the earlier one goes to `orphaned`.
- `apply(payload, labels_doc, fps) -> dict`: `model_view(payload)` then overlay (spec 5.2). Every stroke gets
  `source`; owner `label` sets `stroke`, `group` (`group_of`), `hitter`, stores `model_stroke`, `model_group`,
  `model_hitter`; `not_hit` moves the stroke to `removed`; `unsure` only sets `owner_unsure: true`;
  `added` inserts `{frame: round(time*fps), hitter, stroke, group, confidence: None, source: "owner"}`; list
  re-sorted by frame; `owner_labels = {"labelled", "added", "removed", "unsure", "orphaned", "revision"}`.
- `apply_to_result(result_dir, labels_dir, stem) -> {"applied": bool, "reason": str|None}`: reads
  `strokes.json` (reason `no_strokes` if absent/not schema 2), fps from `hits.json` `fps`, else
  `rally_segments.json` `fps` (reason `no_fps`), writes atomically. Never raises on bad files (reason `unreadable`).
- `import_sheet(sheet_path, labels_dir, results_dir) -> [stem]`: one-time import from
  `outputs/dji-stroke-labels/LABELS.md` via `stroke_labels.parse_sheet` / `labelled_hits`; only stems with no
  label file; `?` -> `unsure`, `not-a-hit` -> `not_hit`, rows without frame -> `added`; split from the sheet.

- [ ] **Step 1: Write failing tests** covering: round trip save/load; invalid items rejected (bad kind, bad
  stroke, negative time, bool time, > 5000); `not_hit` stroke forced None; attach nearest within 0.15 s and not
  beyond; two hits near one label -> nearest only; two labels on one hit -> later wins, earlier orphaned;
  `added` never attaches; apply sets source/model fields and recomputes `distribution`/`groups`; apply twice ==
  apply once (Review Focus 2); `model_view(apply(p)) == p` for a plain model payload; `apply_to_result` with no
  strokes.json -> `{"applied": False, "reason": "no_strokes"}` and no file created; unreadable label file ->
  reason `unreadable`; `import_sheet` imports once and skips stems that already have a file.

```python
def payload(*rows):
    strokes = [{"frame": f, "hitter": h, "stroke": s, "group": group_of(s), "confidence": 0.6}
               for f, h, s in rows]
    return sc.build_payload(strokes, {"version": "t"}, "model", "tracknet")

def test_apply_is_idempotent():
    p = payload((60, "lower", "clear"), (120, "upper", "lift"))
    doc = {"schema": 1, "revision": 3, "split": "train",
           "labels": [{"time_sec": 1.0, "hitter": "lower", "stroke": "smash", "kind": "label"},
                      {"time_sec": 3.0, "hitter": "upper", "stroke": "net_shot", "kind": "added"}]}
    once = ol.apply(p, doc, 60.0)
    assert ol.apply(once, doc, 60.0) == once
    assert ol.model_view(once) == p
    assert [s["source"] for s in once["strokes"]] == ["owner", "model", "owner"]
    assert once["strokes"][0]["model_stroke"] == "clear"
```

- [ ] **Step 2: Run** `pytest tests/test_stroke_owner_labels.py -q` — expect ImportError.
- [ ] **Step 3: Implement** `owner_labels.py` per the interfaces above, module docstring citing spec 5.1-5.2.
- [ ] **Step 4: Run** the test file — all pass.
- [ ] **Step 5: Commit** `feat(stroke): owner stroke-label store and reversible strokes.json overlay`.

### Task 2: Keep corrections through relabel and re-analysis

**Files:**
- Modify: `badminton_analysis/stroke/relabel.py` (after `write_json(... strokes.json ...)`), `badminton_analysis/system.py`
  (after the `strokes.json` write in the stroke-labelling method near line 1770)
- Test: `tests/test_stroke_owner_labels.py` (add), existing `tests/test_*relabel*` must stay green

**Interfaces:**
- Consumes: `owner_labels.apply_to_result(result_dir, labels_dir, stem)`.
- Produces: `relabel(result_dir, weights, allow_unknown=False, labels_dir=None)`; when `labels_dir` is given
  (CLI default `data/stroke_labels` under the project root) labels are re-applied and the posture reports
  (`strokes.jsonl`) are re-scored with the **applied** stroke type (`rescore_report` keyed by `(frame, hitter)`
  using the overlaid label; owner-added strokes have no report and are skipped).
- `system.py`: the analysis object gets `stroke_labels_dir` (default `<project>/data/stroke_labels`); after a
  successful `strokes.json` write call `apply_to_result(self.save_dir, self.stroke_labels_dir, <stem>)`, never fatal.

- [ ] **Step 1: Failing tests**: relabel on a tmp result with a label file keeps the owner type and the
  `model_stroke` is the new model output; relabel without `labels_dir` behaves exactly as before.
- [ ] **Step 2: Run** — fail. **Step 3: Implement.** **Step 4: Run** new + existing relabel tests — pass.
- [ ] **Step 5: Commit** `feat(stroke): relabel and analysis keep owner stroke corrections`.

### Task 3: Stroke-label API

**Files:**
- Create: `badminton_analysis/web/stroke_labels_routes.py`
- Modify: `app.py` (constant `STROKE_LABELS_DIR = PROJECT_ROOT / 'data' / 'stroke_labels'`, `_STROKE_LABELS_LOCK`,
  `register(...)` next to `rally_labels_routes`, delete scope `all` removes the file like rally labels near line 1670,
  one-time `owner_labels.import_sheet` at startup guarded by try/except), `tests/conftest.py` (redirect
  `STROKE_LABELS_DIR` to tmp like `RALLY_LABELS_DIR`)
- Test: `tests/test_app_stroke_labels.py`

**Interfaces:**
- `register(app, *, labels_dir, outputs, lock, safe_stem, job_active, split_locked)`; `split_locked(stem) -> bool`
  comes from Task 5 (`model_eval.split_locked`); until Task 5 lands, app passes `lambda stem: False`.
- `GET /api/stroke-labels/<stem>` -> `{schema, revision, split, split_locked, labels}`; 404 unknown; 500 unreadable.
- `PUT /api/stroke-labels/<stem>` body `{revision, split?, labels}` -> 400 invalid; 404; 409 `stale` (with current
  revision) / `unreadable` / `split_locked` (split change refused); saves; then unless `job_active(stem)`
  -> `apply_to_result`; answers `{revision, applied, apply_skipped?}` where `apply_skipped` is `job_active`,
  `no_strokes`, `no_fps` or `unreadable`.

- [ ] **Step 1: Failing tests** with the Flask test client: GET empty; PUT then GET; stale 409; bad body 400;
  unknown stem 404; split change while locked 409; job active -> `apply_skipped: "job_active"` and strokes.json
  untouched; no strokes.json -> `apply_skipped: "no_strokes"` (Review Focus 1); delete scope `all` removes the
  file, `match` keeps it.
- [ ] **Step 2-4: Run / implement / run.**
- [ ] **Step 5: Commit** `feat(web): stroke-label API with revisioned saves and immediate apply`.

### Task 4: Strokes block on the match details page

**Files:**
- Create: `static/kestrel/views/strokelabels_model.js` (pure state), `static/kestrel/widgets/strokelabels.js` (DOM)
- Modify: `static/kestrel/views/workspace.js` (mount beside the Point block for the selected rally),
  `static/kestrel/core/api.js` (`getStrokeLabels(stem)`, `putStrokeLabels(stem, body)`), `static/kestrel/core/i18n.js`
  (`strokes.*` keys EN + ZH), `static/kestrel/widgets.css`
- Test: `tests/js/strokelabels_model.test.mjs`, `tests/js/strokelabels_widget.test.mjs`

**Interfaces (strokelabels_model.js):**
- `rowsForRally(strokesDoc, labelsDoc, rally, fps) -> [{time, hitter, stroke, model_stroke, source, state}]`
  with `state` in `auto | confirmed | corrected | not_hit | unsure | added`.
- `applyAction(labels, row, action) -> labels` for actions `{type: 'set', stroke}`, `{type: 'swap'}`,
  `{type: 'not_hit'}`, `{type: 'unsure'}`, `{type: 'confirm'}`, `{type: 'add', time, hitter, stroke}`,
  `{type: 'clear'}` (remove the owner label for that row).
- `keyToAction(key) -> action | null`: digits/letters per type (shown on buttons; `S` smash, `C` clear, `L` lift,
  `D` drop, `N` net_shot, `B` block, `K` net_rush, `R` drive, `U` push, `V` cross_net, `1` short_serve,
  `2` long_serve), `P` swap, `X` not hit, `?` unsure, `Enter` confirm.
- `tally(strokesDoc, labelsDoc) -> {labelled, total, agree, agreeShare}` (agreement = owner type == model type on
  labelled shots).
- Widget: click a row -> `player.seekTo(time - 0.5)` and pause; keys act on the selected row and advance;
  "Add missed shot here" uses `player` current time; train/test switch disabled with the locked reason; a 409
  reloads labels and shows `strokes.conflict`.

- [ ] **Step 1: Failing JS tests** for `rowsForRally`, `applyAction` (each action), `keyToAction`, `tally`,
  and a widget smoke test (renders rows, Enter confirms and advances, 409 reload path) in the style of
  `tests/js/strategy_widget.test.mjs`.
- [ ] **Step 2-4: Run `node --test tests/js/strokelabels_*.test.mjs` / implement / run.**
- [ ] **Step 5: Commit** `feat(kestrel): Strokes block to correct strokes from the video`.

### Task 5: Training loader, model evaluation and promotion

**Files:**
- Create: `badminton_analysis/stroke/model_eval.py`, `scripts/score_stroke_model.py`
- Modify: `scripts/train_stroke_classifier.py` (`--stroke-labels DIR`; `--val-match` optional when `--calib-video`
  given)
- Test: `tests/test_stroke_model_eval.py`, `tests/test_train_stroke_classifier.py` (add or extend)

**Interfaces:**
- Trainer: `load_owner(labels_dir, results_dir) -> [Rally]` (train-split files only; hits = applied owner
  `label` + `added` rows with frames from `apply`'s view; `unsure`/`not_hit` excluded; `Rally(sig, hits,
  f"owner:{stem}")`; frames outside the npz are counted and skipped by `extract`'s ok mask, printed once).
  `--val-match` becomes optional; the script errors with "need training and validation data" when neither
  ShuttleSet val nor a `--calib-video` gives validation rallies (owner calib names accept `owner:` or bare stems).
- `model_eval.SINDHU_VAL = "Pusarla_V._Sindhu_Pornpawee_Chochuwong_HSBC_BWF_WORLD_TOUR_FINALS_2020_QuarterFinals"`
- `model_eval.test_shots(labels_dir, results_dir) -> [(stem, hits, truth)]` (test split; owner `label` rows
  attached to detected hits only; `added` excluded; unsure excluded).
- `model_eval.score_weights(weights, shots, results_dir) -> metrics` (runs `sc.load_classifier` +
  `sc.classify_hits` per stem on `hit_signals.npz`, then `stroke_metrics.score`; the same shot set for any weights).
- `model_eval.decide(candidate_m, live_m) -> (keep: bool, reason: str)` per the keep rule.
- `model_eval.promote(weights_dir, candidate_path, record)`; `rollback(weights_dir) -> bool`;
  `history(weights_dir) -> list` (`weights/stroke-model-history.json`, entries `{utc, kept, reason, live, candidate,
  test_videos, train_labels}`); `weights/history/<utc>/` keeps last 5 replaced models.
- `model_eval.split_locked(stem, weights_dir) -> bool`.
- `model_eval.status(labels_dir, results_dir, weights_dir) -> {live: {version, metrics, bar_met, bar_reasons},
  counts: {train, test, new_train}, can_retrain, missing: [..], history}` (`new_train` = train labels whose
  stem/time are newer than the live model's `created_utc`; simplest: count train labels minus the
  `train_labels` recorded in the last kept history entry).
- `model_eval.train_args(project_root, labels_dir, results_dir, out) -> [str]`: with `data/hit_dataset` present
  `--dataset ... --val-match SINDHU_VAL --stroke-labels ...`; otherwise `--stroke-labels ... --calib-video <train stem
  with most labels>` (Review Focus 4).
- `scripts/score_stroke_model.py [--weights] [--labels-dir] [--results-dir] [--json]` prints metrics + bar.

- [ ] **Step 1: Failing tests**: decide keep/reject/no-live; promote moves files and caps history at 5;
  rollback restores; split_locked from sidecar and from history; test_shots excludes added/unsure; train_args
  both branches; trainer `load_owner` skips test split; no labels + no dataset -> clear SystemExit message.
  Use a tiny fake classifier by monkeypatching `sc.load_classifier` / `sc.classify_hits`.
- [ ] **Step 2-4.** **Step 5: Commit** `feat(stroke): train on owner labels; score and promote stroke models`.

### Task 6: Retrain and relabel jobs, stroke model card

**Files:**
- Create: `badminton_analysis/web/stroke_model_routes.py`, `static/kestrel/widgets/strokemodel.js`
- Modify: `app.py` (`queue_train_strokes()`, `queue_relabel_strokes()` modelled on `queue_strategy_job`; pause
  route refuses `train_strokes`/`relabel_strokes` like `strategy_`; `split_locked` getter now
  `model_eval.split_locked`), `badminton_analysis/web/insights_routes.py` (`_kind` -> `training`),
  `static/kestrel/views/pipeline.js` (card + `kind 'training'` rows), `static/kestrel/core/api.js`,
  `static/kestrel/core/i18n.js`, `widgets.css`
- Test: `tests/test_app_stroke_model.py`, `tests/js/strokemodel_widget.test.mjs`

**Interfaces:**
- `GET /api/stroke-model` -> `model_eval.status(...)` plus `job` (active training job or null).
- `POST /api/stroke-model/retrain` -> 409 when not `can_retrain` (`reason: "not_enough_labels"`, `missing`) or a
  training job is active (`reason: "busy"`); else `{job_id, queued}`.
- `POST /api/stroke-model/rollback` -> 409 busy / 404 nothing to roll back; else queues relabel.
- Train job: `python scripts/train_stroke_classifier.py <train_args>` -> on exit 0 run
  `python scripts/score_stroke_model.py` twice in-process via `model_eval` (candidate, live), `decide`, `promote`
  or delete the candidate, record history, then `queue_relabel_strokes()` when kept. Log to
  `weights/candidates/train.log`.
- Relabel job: for each result dir with `strokes.json`: skip when `_match_job_active(stem)`; singles ->
  `relabel(..., labels_dir=STROKE_LABELS_DIR)`; doubles -> `apply_to_result` only. Runs in a subprocess
  (`python -m badminton_analysis.stroke.relabel --labels-dir ... <dirs>`) so it is cancellable.

- [ ] **Step 1: Failing tests** (fake Popen like `tests/test_app_strategy.py`): retrain 409 cases; queued job id
  and kind; pause 409; one at a time; kept -> relabel queued; rejected -> candidate deleted, history row; rollback;
  relabel skips an active match. JS: card states (not enough labels, ready, running, bar met/not met, history).
- [ ] **Step 2-4.** **Step 5: Commit** `feat(jobs): retrain the stroke classifier from the Pipeline view`.

### Task 7: Per-shot table, effectiveness tally, openings

**Files:**
- Create: `badminton_analysis/strategy/shots.py`
- Test: `tests/test_strategy_shots.py`

**Interfaces:**
- Constants exactly as Global Constraints.
- `build_shots(out_dir) -> {"shots": [...], "rallies": [...], "positions_ok": {"upper": bool, "lower": bool},
  "fps": float}` reading `strokes.json` (overlay already applied), `rally_segments.json`, `rally_outcomes.json`
  (schema 1; `winner_side`), `detections.jsonl` (`players.<side>.court` = [x, y] metres). Shot:
  `{rally, index, frame, time, hitter, stroke, group, source, zone: {depth, side}|None, sent_to|None,
  tempo_sec|None, tempo: fast|normal|slow|None, rushed_reply: bool|None, link_broken: bool, last: bool}`.
  Rally: `{id, winner_side|None, n_shots, complete_opening: bool}`. Doubles: the hitter side is the team side.
- `zone_of(side, x, y) -> {"depth", "side"}` (lower half y > 6.7, upper half y < 6.7; distance from net
  `abs(y - 6.7)`; left/right mirrored so "left" is the player's left facing the net).
- `positions_usable(points_by_side) -> {side: bool}` (spec 7.1 rule).
- `effectiveness(table, side) -> {stroke: {effective, neutral, ineffective, unknown, n}}` (spec 7.2;
  defensive replies: `lift`, `block`, `clear` hit from the rear zone; attacking: `smash`, `net_rush`, `push`, `drive`).
- `openings(table) -> {"complete": int, "total": int, "rows": [...<= 8], "won_by": {side: int},
  "opening_winner_won_point": {"n", "won"}, "neutral": int}` (spec 7.3; row = 3 shots with stroke/zone/sent_to,
  `advantage` side or `neutral`, `point_winner`; selection order owner-labelled, closest-fought (rally with most
  shots among owner-labelled winners), longest).
- `overview(table, out_dir) -> {"active_play_sec", "rallies_under_9s_share", "shots_per_rally", "shots_source":
  "labelled"|"detected"}`.

- [ ] **Step 1: Failing tests** with tiny synthetic result dirs (write the JSON files in tmp_path): zone mirroring
  for both sides; tempo bands at 0.69/0.7/1.5/1.51; rushed; broken link on same hitter and on a 2.6 s gap;
  position check fails on a 0004-like fixture (players on the sideline/net); effectiveness classes (winner on
  this shot, winner on hitter's next shot, defensive reply, rushed reply, lost-last-shot, opponent's next shot
  wins, neutral, unknown); opening complete/incomplete; advantage rule; row cap 8; Review Focus 5 fixture
  (no winners, all broken) returns zeros and never raises.
- [ ] **Step 2-4.** **Step 5: Commit** `feat(strategy): per-shot table, effectiveness tally and openings`.

### Task 8: Facts, prompt, caveats and Strategy tab tables

**Files:**
- Modify: `badminton_analysis/strategy/facts.py`, `strategy/prompts.py`, `strategy/report.py` (Markdown export of
  the two tables), `static/kestrel/widgets/strategy.js`, `static/kestrel/views/strategy_model.js`, `core/i18n.js`, `widgets.css`
- Test: `tests/test_strategy_facts.py`, `tests/test_strategy_validate.py`, `tests/test_strategy_report.py`,
  `tests/js/strategy_model.test.mjs`, `tests/js/strategy_widget.test.mjs`

**Interfaces:**
- New facts (ids follow `facts.py` conventions; `you`/`opp` for singles, `team.<t>` for doubles):
  `match.active_play_sec` (s), `match.rallies_under_9s_share` (share), `match.shots_per_rally`,
  `match.owner_labelled_share` (share), `match.stroke_model` (`{accuracy, bar_met}` from `model_eval.status`'s live
  metrics; unavailable when none), `match.openings.complete` (count, `defs: [3]`), `match.openings.total`,
  `you.openings.won` / `opp.openings.won` (count), `you.opening_win_point` (`{n, won}`),
  `you.eff.<stroke>` / `opp.eff.<stroke>` (`{effective, neutral, ineffective, unknown, n}`; status
  `insufficient` with reason `few_attempts` when n < 5). Opening facts are `insufficient` (`few_openings`) when
  complete < 5.
- Pack gains `"tables": {"effectiveness": {...}, "openings": {...}}` (not sent to the LLM; `user_message` keeps
  `{format, focus, header, facts}`), used by the Strategy tab and `render_markdown`.
- Caveats: `stroke_model{accuracy, bar_met}`, `owner_labelled{share}`, `positions_unusable{sides}`,
  `openings_incomplete{complete, total}` with EN + ZH text where caveats are rendered today.
- Prompt: one paragraph in both languages saying strengths/weaknesses/game plan should rest on the `eff.*` and
  `openings.*` facts when available and must not call a stroke a strength/weakness when its fact is insufficient.
- `current_inputs` includes `strokes.json` (already) so labels make the report stale.

- [ ] **Step 1: Failing tests**: facts built from a fixture result; insufficient rules; tables in the pack and
  absent from `user_message`; validator accepts a claim citing `you.eff.smash` with its numbers and rejects a wrong
  one; markdown export includes both tables; JS model shapes tables and empty states; Review Focus 5.
- [ ] **Step 2-4.** **Step 5: Commit** `feat(strategy): rally tactics facts, caveats and tables in the report`.

### Task 9: Docs, full verification, live check

**Files:** `.ai/STATE.md` (row state; decision bullet), `README.md` / `README_EN.md` (short "Correct strokes and
retrain" section).

- [ ] Full pytest and `node --test tests/js/*.test.mjs`; record counts honestly.
- [ ] Live on the worktree preview server (port 5071): label a few shots of 0003, check strokes.json overlay,
  regenerate the 0003 report, check tables. Retrain once if label counts allow; otherwise record that the button
  reports what is missing.
- [ ] Commit `docs: stroke correction and rally tactics`.
