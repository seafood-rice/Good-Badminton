# Rally Winner Learning Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Learn rally segmentation and rally winners from the owner's point labels; replace the swing segments and
the rule cascade only when the learned models measure at least as well on held-out labelled matches.

**Architecture:** A per-match scoring format drives game/end rules. A point-end segmenter (logistic regression over
gaps between detected hits) produces `rally_segments.json` when kept. A score-sequence decoder (forward-backward
over score states, with signal reliabilities counted from labels and owner labels clamped) produces auto winners in
`rally_outcomes.json` when kept. Training, leave-one-match-out measurement, promotion and history reuse the stroke
model's store, generalised to a model name.

**Tech Stack:** Python 3 + numpy (no new dependency), Flask, vanilla ES modules (Kestrel), pytest, `node --test`.

**Spec:** `docs/superpowers/specs/2026-10-28-rally-winner-learning-design.md` (read it first; section numbers below
refer to it).

## Global Constraints

- With no kept model (or unreadable weights), every output is byte-for-byte what today's code writes.
- Scoring presets exactly as spec section 4: `bwf_21x3` (default: 21, win by 2, cap 30, best of 3, change after each
  game, at 11 in game 3), `one_game_30` (30, win by 1, no cap, best of 1, change at 15), `15x3` (15, win by 2,
  cap 21, best of 3, change after each game, at 8 in game 3), `custom`. Ranges: points 5-50, win_by 1|2,
  cap null or >= points, best_of 1|3|5, change_ends_at null or 1..points-1.
- Segmenter: example = gap between consecutive learned hits; positive when an owner point end lies in
  `[gap_start - 1, gap_end + 1]`; segment `start = first hit - 2.0 s`, `end = last hit + 1.0 s`.
- Decoder claim threshold chosen on held-out labels for ~0.90 precision; keep rules: segmenter pooled held-out
  point-end F1 (+-1.5 s) >= swing's; decoder pooled held-out precision >= 0.85 AND correct claims >= the rule
  cascade's.
- Retrain enabled with >= 2 labelled matches having >= 20 point labels each and >= 20 new point labels since the
  last run. Job ids `train_rally`, `resegment_rallies`, kind `training`; refused while any training job
  (`train_strokes`, `relabel_strokes`, `train_rally`, `resegment_rallies`) is active.
- Weights: `weights/rally-segmenter.json`, `weights/rally-decoder.json`, history `weights/rally-model-history.json`,
  history dirs under `weights/history/rally/`; last 5 kept; swap marker with pid + start time (as the stroke model).
- Every UI string EN + ZH in `static/kestrel/core/i18n.js`; API error bodies static bilingual strings; card strings
  as codes.
- Never commit `BirdEye Prototype.html`, `data/`, `outputs/`, `videos/`, `weights/`. Never bare `git stash`.
  Commits end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`; stage with explicit pathspecs.
- Tests: `PYTHONUTF8=1 D:/Dev/Claude/Projects/good-badminton/Good-badminton/.venv/Scripts/python.exe -B -m pytest -q
  -p no:cacheprovider --basetemp <unique dir under $TEMP>`, and `node --test tests/js/*.test.mjs`.

## Review Focus

1. A video that starts mid-game (no earlier part, but the first rallies are clearly not 0-0): the decoder must not
   force 0-0 when the record does not say the video covers the match start; it allows any start state. (Task 5 test.)
2. A recorded result impossible under the chosen format (e.g. 30-28 under `one_game_30`): no crash, the end is left
   open, caveat `final_score_open`, reconciliation `no_result` with reason `scoring_mismatch`. (Tasks 2 and 5 tests.)
3. A match with labels but no learned hits (`hits.json` missing or rule-based source): the segmenter gives no
   examples and falls back to swing; training skips it and says so. (Tasks 3 and 6 tests.)
4. Leave-one-out isolation: the held-out match's labels never reach its training set nor its decoding evidence.
   (Task 6 test.)
5. Re-segmenting a match whose owner rally labels were attached to old segment ids: labels re-attach by time, the
   Point block still shows them, and `rally_outcomes.json` keeps every owner verdict. (Task 7 test.)

---

### Task 1: Generalise the model store

**Files:**
- Create: `badminton_analysis/models/__init__.py`, `badminton_analysis/models/store.py`
- Modify: `badminton_analysis/stroke/model_eval.py` (history, swap marker, recover, promote, reject, rollback,
  can_rollback, _history_dirs, _new_history_dir become thin wrappers over `store` with the stroke spec)
- Test: `tests/test_model_store.py`; existing `tests/test_stroke_model_eval.py`, `tests/test_stroke_retrain.py`,
  `tests/test_app_stroke_model.py` must stay green unchanged

**Interfaces (Produces):**
- `ModelSpec(name, files: tuple[str, ...], history_file: str, history_subdir: str|None, keep=5)`. Stroke:
  `ModelSpec("stroke", ("stroke-classifier.pt", "stroke-classifier.json"), "stroke-model-history.json", None)`
  (history dirs stay at `weights/history/<stamp>/` for stroke so existing data keeps working). Rally:
  `ModelSpec("rally", ("rally-segmenter.json", "rally-decoder.json"), "rally-model-history.json", "rally")`.
- `store.history(spec, weights_dir)`, `store.append_history(spec, weights_dir, entry)`,
  `store.promote(spec, weights_dir, candidate_files: dict[name -> path], record) -> entry` (only the files given are
  swapped; others stay), `store.reject(spec, weights_dir, candidate_files, record)`, `store.rollback(spec, weights_dir,
  **record) -> bool`, `store.can_rollback(spec, weights_dir)`, `store.recover(spec, weights_dir)`,
  `store.SwapInProgress`. Swap marker file per spec: `weights/.swap-in-progress-<name>` (stroke keeps
  `.swap-in-progress` for compatibility).

- [ ] Write `tests/test_model_store.py` for a two-JSON-file spec: promote one file only, promote both, history cap 5,
  rollback one model back per call, marker refused while alive, recovery with a dead marker, reused pid with another
  start time is not alive. Run: fails (no module).
- [ ] Implement `store.py` by moving the stroke code and parametrising it; turn `stroke/model_eval.py` functions into
  wrappers with the stroke spec (same names and signatures).
- [ ] Run new + all stroke model tests: pass.
- [ ] Commit `refactor(models): one model store for stroke and rally models`.

### Task 2: Scoring format

**Files:**
- Create: `badminton_analysis/outcomes/scoring.py`
- Modify: `badminton_analysis/library/athletes.py` (validate/clean optional `scoring` on primary records; links
  refuse it), `badminton_analysis/outcomes/reconcile.py` (format-aware), `static/kestrel/views/matches.js` (format
  selector + fields in the match editor), `static/kestrel/core/api.js` if needed, `static/kestrel/core/i18n.js`
  (`scoring.*` keys EN + ZH), `widgets.css`
- Test: `tests/test_outcomes_scoring.py`, `tests/test_athletes*.py` (extend), `tests/test_outcomes_reconcile*.py`
  (extend), `tests/js/matches*.test.mjs` (extend)

**Interfaces (Produces):**
- `PRESETS: dict[str, dict]`; `DEFAULT = "bwf_21x3"`; `resolve(record) -> dict` (the record's scoring or the default
  preset, all fields filled); `validate(raw) -> (clean|None, error_code|None)`.
- `game_over(fmt, a, b) -> bool`; `match_over(fmt, games_won_a, games_won_b) -> bool`;
  `ends_change(fmt, game_index, a, b, prev_a, prev_b) -> bool` (after a game when `change_ends_after_game`; within
  the deciding game, or the only game when best_of 1, when the leader first reaches `change_ends_at`);
  `consistent(fmt, sets) -> bool` (every recorded set is a legal final score and the set count fits best_of).
- Reconcile: uses `resolve`; `consistent` False -> `{"status": "no_result", "reason": "scoring_mismatch"}`.

- [ ] Tests: each preset's game end (21-19 over, 20-20 not, 30-29 over under cap, 30-29 over and 29-29 not under
  `one_game_30` win_by 1: 30-29 over, 29-29 not; `15x3` 21-20 over by cap), ends change at 11 in game 3 and at 15 in
  `one_game_30`, validation ranges, link refuses scoring, reconcile mismatch (Review Focus 2), editor round trip in JS.
- [ ] Implement; run; commit `feat(outcomes): per-match scoring format with presets`.

### Task 3: Segment features and the point-end segmenter

**Files:**
- Create: `badminton_analysis/outcomes/run_inputs.py` (offline loaders), `outcomes/segment_features.py`,
  `outcomes/segmenter.py`
- Test: `tests/test_outcomes_segmenter.py`

**Interfaces (Produces):**
- `run_inputs.load(out_dir) -> Inputs(fps, hits: [(time_sec, frame, side, player_id|None)], positions:
  {side: {frame: (x, y)}}, swing: [(time_sec, activity)], shuttle_frames: set[int], positions_ok: {side: bool},
  quad)`. Learned hits only (`hits.json` `source` learned); swing activity rebuilt from `detections.jsonl` hands with
  `stroke.rallies.wrists_from_hands` + `swing_activity` + `baseline_caps` (same path as
  `scripts/score_rally_labels.py`); `positions_ok` via `strategy.shots.positions_usable`.
- `segment_features.FEATURES: tuple[str, ...]`; `gaps(inputs) -> [Gap(start, end, features: dict)]`;
  `labels_for(gaps, owner_labels_doc, segments) -> [0|1|None]` (spec 5.1; None = not usable).
- `segmenter.train(examples: [(features_dict, label)]) -> model_dict` (standardise, L2 logistic regression by
  gradient descent, class weighting); `segmenter.predict(model, gaps) -> [p]`;
  `segmenter.segments(inputs, model) -> [{"id", "start_frame", "end_frame", "start_sec", "end_sec", "degraded":
  False, "p_point"}]` (spec 5.2); `segmenter.load(path) -> model|None` (None on missing/unreadable/wrong schema).
- `segmenter.point_end_f1(pred_ends, true_ends, tol=1.5) -> {precision, recall, f1, tp, fp, fn}`.

- [ ] Tests with synthetic inputs: gap features (lengths, same side, walked distance 0 when positions not ok,
  service-court flag); labels from segment labels, added points and not_point; training separates an easy synthetic
  set; segments built with the 2 s / 1 s pads and clipped; single-hit no-swing runs dropped; `load` returns None on
  junk; F1 matching is one-to-one; no learned hits -> no gaps (Review Focus 3).
- [ ] Implement; run; commit `feat(outcomes): point-end segmenter learned from owner labels`.

### Task 4: Learned segments in the pipeline and consumers

**Files:**
- Modify: `badminton_analysis/system.py` (after the hit list exists, when `weights/rally-segmenter.json` loads and
  learned hits exist: segments from `segmenter.segments`, `detection = {"signal": "learned", "attempted_signal":
  "learned", "degraded": False, "model": {"version", "f1"}, ...}`; else today's call unchanged), consumers listed in
  spec 5.4 only where they special-case `signal`
- Test: `tests/test_rally_learned_segments.py` (a learned-segment fixture run through each consumer listed in spec 5.4:
  insights rally summary, outcomes build, strategy facts/shots, stroke owner_labels apply, relabel, player positions,
  workspace model JS, legacy UI JS smoke)

- [ ] Tests first: without the weights file the written `rally_segments.json` equals today's (golden from a fixture
  run); with weights the signal is `learned` and consumers accept it (no "degraded" withholding, rally counts from
  segments).
- [ ] Implement; run; commit `feat(rallies): use the learned segmenter when a kept model exists`.

### Task 5: Score-sequence winner decoder

**Files:**
- Create: `badminton_analysis/outcomes/decoder.py`
- Modify: `badminton_analysis/outcomes/build.py` (when `weights/rally-decoder.json` loads: decode the whole match
  (all parts in order) and write decoder winners; else today's `_auto`)
- Test: `tests/test_outcomes_decoder.py`, `tests/test_outcomes_build*.py` (extend)

**Interfaces (Produces):**
- `observations(segments, inputs, auto_evidence) -> [Obs(landing, last_hitter, next_server, serve_court, p_point)]`
  (`serve_court` in `left`/`right`/None from the first hit's server x, mirrored for upper; None when positions not ok).
- `fit(labelled: [(Obs, winner|'not_point')]) -> decoder_model` (add-one-smoothed tables per signal; parity
  reliability; claim threshold set later by model_eval); `load(path)`.
- `decode(model, fmt, obs: [Obs], clamps: {index: winner|'not_point'}, start: 'zero'|'free', final: (sets)|None) ->
  {"p": [{"upper", "lower", "none"}], "mirror": bool, "final_open": bool}`. State = (game, a, b, server, flipped);
  ends flip per `scoring.ends_change`; the "none" step keeps state; mirror chosen by total likelihood of both readings.
- `claims(result, threshold) -> [winner|None]`.

- [ ] Tests: transitions for all three presets (game end, deuce, cap, end change flips sides); clamps force steps and
  propagate to neighbours; a free start does not force 0-0 (Review Focus 1); final-score constraint on/off and
  `final_open` on mismatch (Review Focus 2); not-a-point steps keep state; mirror choice on a synthetic inverted
  parity sequence; claims only above threshold; byte-identical `rally_outcomes.json` without weights.
- [ ] Implement (log-space forward-backward; prune states with zero mass); run; commit
  `feat(outcomes): score-sequence winner decoder`.

### Task 6: Measurement, keep rules, retrain child

**Files:**
- Create: `badminton_analysis/outcomes/model_eval.py`, `outcomes/retrain.py`, `scripts/score_rally_model.py`
- Test: `tests/test_outcomes_model_eval.py`, `tests/test_outcomes_retrain.py`

**Interfaces (Produces):**
- `model_eval.labelled_matches(labels_dir, results_dir) -> [stem]` (>= 1 point label and learned hits; others listed
  with a reason).
- `model_eval.evaluate(labels_dir, results_dir, athletes_path, candidate: {segmenter, decoder}|None) -> {per_match:
  {stem: {...}}, totals: {seg_f1, swing_f1, dec_precision, dec_correct, rules_correct, rules_precision}}`
  (leave one match out; Review Focus 4).
- `model_eval.choose_threshold(heldout_probs_and_truth, target=0.90) -> float`.
- `model_eval.decide(totals) -> {"segmenter": (keep, reason_code), "decoder": (keep, reason_code)}`.
- `model_eval.status(labels_dir, results_dir, weights_dir) -> {live, counts: {matches, points, new_points},
  can_retrain, missing: [codes], history, can_rollback}`.
- `retrain.run(labels_dir, results_dir, athletes_path, weights_dir) -> int`; prints `PHASE promote` before a swap and a
  final JSON line `{segmenter: {kept, reason}, decoder: {kept, reason}, totals}`; uses `models.store` with the rally
  spec; history entries carry `points`, `totals`, `live_after`.
- `scripts/score_rally_model.py [--weights-dir] [--labels-dir] [--results-dir] [--json]`.

- [ ] Tests: LOMO isolation (a spy asserts the held-out stem is absent from training examples and from clamps),
  threshold choice, keep/reject per model, one model kept and the other not, history fields, gates
  (`missing` codes), no learned hits skipped with a reason (Review Focus 3).
- [ ] Implement; run; commit `feat(outcomes): leave-one-match-out measurement and rally retrain`.

### Task 7: Jobs, routes, card, re-segmenting

**Files:**
- Create: `badminton_analysis/outcomes/resegment.py`, `badminton_analysis/web/rally_model_routes.py`,
  `static/kestrel/widgets/rallymodel.js`
- Modify: `app.py` (`queue_train_rally`, `queue_resegment_rallies`, cancel/pause handling like the stroke jobs, one
  training job at a time across all four ids, clip staleness), `badminton_analysis/web/insights_routes.py` (`_kind`),
  `static/kestrel/views/pipeline.js` (card + rows), `core/api.js`, `core/i18n.js`, `widgets.css`
- Test: `tests/test_app_rally_model.py`, `tests/test_outcomes_resegment.py`, `tests/js/rallymodel_widget.test.mjs`

**Interfaces:**
- `GET /api/rally-model` -> `status` + `job`; `POST /api/rally-model/retrain` (409 `busy` / `not_enough_labels`);
  `POST /api/rally-model/rollback` (409 busy, 404 none).
- `resegment.run(results_dirs, weights_dir, labels_dir, stroke_labels_dir, athletes_path)` per result: rebuild inputs,
  write `rally_segments.json` atomically (learned or swing per the kept model), update `hits.json` `rally_id`
  atomically, `outcomes.build.rebuild_match`, `stroke.owner_labels.apply_to_result` + `relabel.rescore_reports`,
  mark `clips/` stale (write `clips/.stale`); CLI `python -m badminton_analysis.outcomes.resegment`.
- Rally clip endpoints in app.py treat `clips/.stale` as "re-cut on request".

- [ ] Tests: retrain gates and 409s; one training job across stroke and rally ids; cancel and failure re-apply
  stroke labels; resegment skips active matches; labels re-attach by time and owner verdicts survive (Review Focus 5);
  clip staleness; card states EN + ZH.
- [ ] Implement; run; commit `feat(jobs): retrain rally models and re-segment from the Pipeline view`.

### Task 8: Docs, full verification, live

- [ ] `.ai/STATE.md` (row -> state; decision bullet; the leave-one-out table from the first real run), README /
  README_EN short sections (scoring format, Rally model card).
- [ ] Full pytest and JS; record counts.
- [ ] Live on copies: run `scripts/score_rally_model.py` on 0003 + 0004 copies and record the table; retrain on copies;
  re-segment a copy and check the workspace timeline, Point block, outcomes, strategy facts in the preview server.
- [ ] Commit `docs: rally winner learning`.
