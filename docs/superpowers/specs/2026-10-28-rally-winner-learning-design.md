# Rally segmentation and rally winners learned from the owner's point labels

Status: design approved in chat 2026-10-28 (sections 1-3); spec awaiting owner review.
Builds on: rally outcome labelling (`2026-10-27-rally-outcome-labelling-design.md`, #36), B11 rally detection
(`2026-07-30-rally-play-detection-b11-design.md`), learned hit detection (#13), the stroke model's retrain and
promotion code (`2026-10-28-stroke-correction-and-rally-tactics-design.md`, #38).

## 1. Request and why

The owner asked for the automatic rally-winner detection to be trained from the manual point labels (the Point
block, `data/rally_labels/<stem>.json`), so match analysis labels rally winners more accurately. After seeing the
probe below, the owner chose to learn rally segmentation as well, and to let the learned segments replace
`rally_segments.json`. The scoring format must be a per-match setting (events differ).

## 2. Findings that shaped the design (read-only probes, 2026-10-28)

Owner labels: DJI_20261024090907_0003_D (31 labels: 14 lower, 7 upper, 10 not a point) and
DJI_20261024091506_0004_D (42 labels: 15 lower, 13 upper, 14 not a point); 49 points in all.

- 14 of the 49 points have no detected segment (owner-added point ends; 13 in 0004). 24 of the 59 swing-path
  segments are not points.
- On the 35 labelled points that attach to a segment, each signal of the current rule cascade
  (`outcomes/rules.py`) is near chance:

  | signal | right | wrong | no answer |
  |---|---|---|---|
  | landing (E2) | 10 | 11 | 14 |
  | next server (E3) | 15 | 12 | 8 |
  | "last hitter won" (E1) | 16 | 18 | 1 |
  | rule cascade | 3 | 1 | 31 |

- The first detected hit after the previous labelled point end is by the previous point's winner (who must serve
  next) in 13 of 20 (0003) and 15 of 26 (0004) rallies.
- Service-court parity (server in the right court on an even own score, left on odd), with the score counted from
  the owner labels from 0-0: consistent in 2 of 13 on 0003, i.e. consistently inverted in 11 of 13 (a mirrored
  convention or an odd start score); 6 of 15 on 0004, whose positions fail the position check of #38.
- The swing segmenter uses player swing activity; the shuttle path is never chosen on DJI footage (shuttle
  density 0.047). The learned hit detector (recall about 0.80) is a stronger rally signal than swing.

Consequence: per-rally signals need the scoring rules to tie rallies together, and segmentation must be fixed for
the winner sequence to make sense.

## 3. Goal, done-means and non-goals

Goal: label-trained segmentation and winner decoding replace today's swing segments and rule cascade when, and
only when, they measure at least as well on held-out labelled matches.

Done means:

1. A per-match scoring format (section 4) with presets, stored on the match record and editable in the match editor.
2. A learned point-end segmenter (section 5) that, once kept, produces `rally_segments.json`.
3. A score-sequence winner decoder (section 6) that, once kept, produces the auto winners in `rally_outcomes.json`.
4. Leave-one-match-out measurement, keep rules, a Retrain job, a Rally model card, history and roll back (section 7).
5. Re-segmenting existing results offline after a keep or roll back (section 7.5).
6. With no kept model, every output is byte-for-byte what today's code writes.
7. Tests as in section 10 pass; full pytest and `node --test tests/js/*.test.mjs` green.

Non-goals: learning auto `outcome` (winner / forced / unforced); a video-pass re-analysis; doubles-specific
features beyond team-level rules; changing the B11 shuttle path.

## 4. Scoring format (per match)

New optional `scoring` object on a primary match record in `data/athletes.json` (validated in
`library/athletes.py`; a link record never carries it; parts use the primary's):

| field | type | meaning |
|---|---|---|
| `preset` | `bwf_21x3` / `one_game_30` / `15x3` / `custom` | the preset it started from |
| `points_to_win` | int 5-50 | points that win a game |
| `win_by` | 1 or 2 | lead needed (1 = no deuce) |
| `cap` | int or null | score that wins regardless of lead; >= `points_to_win` |
| `best_of` | 1, 3 or 5 | games |
| `change_ends_after_game` | bool | ends change after each game |
| `change_ends_at` | int or null | mid-game change in the deciding game, or in a one-game match |

Presets:
- `bwf_21x3` (default when absent): 21, win by 2, cap 30, best of 3, change after each game, at 11 in game 3.
- `one_game_30`: 30, win by 1 (first to 30 wins), no cap, best of 1, change at 15.
- `15x3`: 15, win by 2, cap 21, best of 3, change after each game, at 8 in game 3. Win-by and cap are assumptions
  (not confirmed rules); the fields are editable.

Match editor (`static/kestrel/views/matches.js`): a format selector plus the fields, EN + ZH. The result entry keeps
accepting sets as typed; a recorded result that is impossible under the format is accepted with a warning
(`scoring_mismatch`), not refused.

`outcomes/reconcile.py` uses the format: a mid-game end change (`change_ends_at`) maps sides per rally, not only per
set; a scoring mismatch makes reconciliation `no_result` with that reason.

## 5. Learned point-end segmenter

### 5.1 Examples and labels

One example per gap between consecutive detected hits (`hits.json`, learned source). Features
(`outcomes/segment_features.py`):

- gap length (s); log of it;
- swing activity during the gap (mean and max, the B11 swing signal);
- each player's walked distance during the gap (court metres, from `detections.jsonl`); 0 when positions fail the
  #38 position check;
- whether the hitter after the gap stands inside a service court (and which half);
- same side before and after the gap;
- shuttle visibility share during the gap.

Label: positive when an owner point end (segment label `end_sec` or added point) lies in
`[gap_start - 1, gap_end + 1]`; gaps inside an owner `not_point` segment are negative; other gaps negative.
Unlabelled videos give no examples.

### 5.2 Model and segments

Logistic regression in numpy (standardised features, L2, class weighting), weights in
`weights/rally-segmenter.json` with feature names, means/scales, threshold and its measured score. A predicted point
end splits the hit sequence; a rally is a run of hits between point ends: `start = first hit - 2.0 s` (the serve
pad B11 measured), `end = last hit + 1.0 s`, clipped to the video and to neighbours. Runs of a single hit with no
swing activity are dropped. Each segment carries `p_point` (the mean point-end probability of its bounding gaps)
for the decoder.

### 5.3 Output and safety

`rally_segments.json` keeps its schema; `detection.signal` becomes `learned` and `detection.model` records the
segmenter version and held-out score. No kept segmenter, unreadable weights or no learned hits: the swing path runs
exactly as today. `degraded` keeps its meaning (the learned path is never degraded; it falls back instead).

### 5.4 Consumers rechecked

`system.py` (stroke withholding, `rally_id`), `insights/rally.py` / `loaders.py` / `sidecar.py`, `outcomes/*`,
`strategy/facts.py` and `shots.py`, `stroke/owner_labels.py` and `relabel.py`, `visualization/player_positions.py`,
`web/insights_routes.py`, `web/strategy_routes.py`, rally clips in `app.py`, `static/kestrel/views/workspace*.js`,
`static/kestrel/legacy/kestrel-legacy.js`. Each gets a test on a learned-segment fixture (signal `learned`).

## 6. Score-sequence winner decoder

### 6.1 States and rules

State: (game index, score per side, server side, ends flipped). One step per segment; each step either gives a
point to a side (score and server update, winner serves next) or is "not a point" with probability
`1 - p_point`. Game ends, ends changes (which flip upper/lower) and the match end follow the match's scoring format.

- The start state is 0-0 in game 1 when the record says the video covers the match from its start (a primary video
  with no earlier part); otherwise any reachable state is allowed (uniform). A later part starts from the previous
  part's decoded end distribution.
- When the recorded result is consistent with the format and the parts cover the whole match, the final state must
  match it; otherwise the end is free and a caveat `final_score_open` is recorded.
- Doubles: the same rules at team level.

### 6.2 Observations and learned reliabilities

Per segment: landing verdict (E2), last hitter (E1), next segment's first-hit side (E3), and the serve's
service-court side (left/right from the server's court x at the first hit, mirrored for upper). Each signal has a
table P(observation | true winner) counted from labelled rallies with add-one smoothing, stored in
`weights/rally-decoder.json`. Parity: P(observed court = court implied by state); the mirror (whether "right" is
flipped) is chosen per video by likelihood; dropped when the video fails the position check.

### 6.3 Owner labels and output

Owner point labels in a match are hard evidence (the step is clamped); `not_point` clamps the step to no point.
Forward-backward gives P(winner) per segment. A winner is claimed when its probability >= the claim threshold
stored with the model (chosen on held-out labels for about 0.90 precision); otherwise `winner_side` is null.
`rally_outcomes.json` keeps schema 1: `confidence` = the probability, `evidence` gains `decoder: {p_upper, p_lower,
p_point}`, `rule` = `decoder`, `summary.confidence_calibrated` = true. No kept decoder: the rule cascade runs as today.

## 7. Training, measurement, keep rules, job, re-segmenting

### 7.1 Measurement (leave one match out)

For every labelled match M: train on the other labelled matches, evaluate on M with M's labels hidden for decoding.

- Segmenter: point-end F1 within +-1.5 s (owner point ends vs segment ends), against the swing segments of M.
- Decoder: precision of claimed winners and number of correct claims on M's labelled points, against the rule
  cascade on the same segments (decoder evaluated on the segments the candidate segmenter would produce when kept
  together, else on the live segments).

`stroke/model_eval`-style module `outcomes/model_eval.py` with `evaluate(labels_dir, results_dir, weights) ->
{per_match, totals}`; `scripts/score_rally_model.py` prints the table.

### 7.2 Keep rules (each model separately)

- Segmenter kept when its pooled held-out F1 >= the swing segmenter's pooled F1.
- Decoder kept when its pooled held-out precision >= 0.85 and its correct claims >= the rule cascade's.
Rejected candidates are deleted and recorded with the numbers.

### 7.3 Retrain job and card

Kind `training`, id `train_rally`, in the shared job slots; one at a time, cancellable, not pausable; refused while
`train_strokes`, `relabel_strokes`, `train_rally` or `resegment_rallies` is active. Enabled with >= 2 labelled
matches having >= 20 point labels each and >= 20 new point labels since the last run. Runs in a child process
(`python -m badminton_analysis.outcomes.retrain`), final JSON line `{segmenter: {kept, reason, ...}, decoder: {...}}`.

Promotion, history (capped at 5), the swap marker (pid + start time), crash recovery and roll back reuse the stroke
model code, generalised to a model name (`badminton_analysis/models/store.py`, used by both); stroke behaviour is
unchanged (its tests stay green). Files: `weights/rally-segmenter.json`, `weights/rally-decoder.json`,
`weights/rally-model-history.json`, `weights/history/rally/...`.

Rally model card on the Pipeline view beside the Stroke model card: segmenter F1 vs swing, decoder precision and
correct claims vs the rules, label counts and what is missing, Retrain, history, Roll back; strings as codes
rendered EN + ZH.

### 7.4 New analyses

`system.py` loads the kept segmenter and decoder when present; otherwise today's code.

### 7.5 Re-segmenting existing results

After a keep or roll back, job `resegment_rallies` (kind `training`) runs in a child over every result with
`hits.json` + `detections.jsonl`, skipping results whose match job is active: rewrite `rally_segments.json`
(atomic), the hits' `rally_id`, rebuild `rally_outcomes.json` for the whole match, re-apply stroke labels
(`owner_labels.apply_to_result`), mark pre-cut rally clips stale (re-cut on request). Insights and strategy reports
go stale through their existing input hashes. Cancel and failure re-apply as the relabel job does.

## 8. Code layout

- New: `badminton_analysis/outcomes/segment_features.py`, `outcomes/segmenter.py`, `outcomes/decoder.py`,
  `outcomes/scoring.py` (formats, presets, game/end rules), `outcomes/model_eval.py`, `outcomes/retrain.py`,
  `outcomes/resegment.py`, `badminton_analysis/models/store.py`, `scripts/score_rally_model.py`,
  `static/kestrel/widgets/rallymodel.js`.
- Changed: `library/athletes.py` (scoring), `outcomes/build.py` (decoder path), `outcomes/reconcile.py`,
  `stroke/rallies.py` + `system.py` (learned path), `stroke/model_eval.py` (uses `models/store.py`), `app.py`
  (jobs, routes, clip staleness), `web/` (rally model routes), `static/kestrel/views/matches.js` (format editor),
  `views/pipeline.js`, `core/api.js`, `core/i18n.js`, `widgets.css`.

## 9. Measurement record

`.ai/STATE.md` gets the leave-one-out table from the first real run (owner labels on 0003 + 0004), stated as
single-match held-out scores.

## 10. Testing

- Scoring: presets, validation, game end with deuce / no deuce / cap, mid-game end change, mismatch warning.
- Segment features and labels on synthetic hit sequences; segmenter train/predict; fallback to swing without weights
  (byte-identical `rally_segments.json`).
- Decoder: rule transitions per format; clamped labels; not-a-point steps; parity mirror choice; final-score
  constraint on and off; split-match carry-over; claims only above threshold; byte-identical outcomes without weights.
- model_eval: leave-one-out isolation (the held-out match's labels never reach its training or decoding).
- Store refactor: stroke model promotion/rollback/marker tests unchanged and green.
- Jobs and routes: retrain gates, one training job at a time across stroke and rally, cancel, resegment skips active
  matches and re-applies stroke labels.
- Consumers on a learned-segment fixture (section 5.4).
- UI: format editor, Rally model card states, EN + ZH.
- Live: train on 0003 + 0004, record the held-out table; re-segment a copy and check the workspace timeline, outcomes
  and strategy facts.

## 11. Risks

- Two labelled matches: every held-out score is one match; keep decisions are noisy until 0009 / 0010 are labelled.
- Serve detection is about 60 % right, which bounds the decoder.
- An unexpected side swap (camera moved) breaks a sequence; reconciliation `over` shows it.
- Replacing segments changes rally counts, clips and the strategy report for every match once a segmenter is kept.
