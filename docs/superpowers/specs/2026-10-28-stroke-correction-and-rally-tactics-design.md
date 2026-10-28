# Stroke correction, retraining and rally tactics in the strategy report

Status: design approved in chat 2026-10-28 (sections 1-3); spec awaiting owner review.
Builds on: strategy report (`2026-10-27-match-strategy-report-design.md`, #37), rally outcome
labelling (`2026-10-27-rally-outcome-labelling-design.md`, #36), unified stroke classifier
(`2026-10-22-unified-stroke-classifier-design.md`, #26).

## 1. Request and why

The owner wants the strategy report to explain how points are won and lost: which stroke types,
tempo and court positions lead to a won or lost point, what happens after each shot, and which
opening sequences gain the advantage. Target layout: a pasted sample match analysis with a match
overview, an opening 3-shot table ("advantage gained by"), a per-stroke effectiveness tally
(effective / neutral / ineffective), then strengths, weaknesses and a training focus.

The stroke labels behind that are not good enough yet (clears overcalled, 90 of 174 shots in
0003), so the owner also asked for a way to correct strokes from the video, like rally-win
labelling, and to use the corrections to retrain the classifier and improve the report.

Career-wide sequence patterns are a later, separate athlete-level report (owner decision); this
spec covers one match.

## 2. Findings that shaped the design (read-only probes, 2026-10-28)

On the owner-labelled matches DJI_20261024090907_0003_D and DJI_20261024091506_0004_D (49 rallies,
winner known for all):

- Per shot the analysis records frame, hitter, stroke type, group and confidence. Player court
  positions (metres; x 0-6.1 across, y 0-13.4 along, net at y = 6.7) and player speed are in
  `detections.jsonl` for 95-99 % of frames. Shuttle positions are image pixels only, so shuttle
  speed and landing spots are not measured.
- Detected shots per rally: 2-4 against about 6 on court; some rallies have 0.
- The first detected shot is a serve in 5 of 30 rallies (0003) and 0 of 29 (0004).
- Consecutive detected shots by the same hitter: 35 of 69 pairs (0003), 21 of 49 (0004), so
  about half of shot-to-shot links hide a missed hit or a wrong hitter.
- Gap between detected shots in 0003: p25 0.6 s, median 1.0 s, p75 1.67 s; > 2.5 s in 3 of 69.
- Receiver movement between shots in 0003: median 1.0 m, p75 2.56 m.
- 0004 positions are implausible: the far player's median x is -0.27 m (outside the sideline),
  the near player sits on the net line.
- `stroke/relabel.py` re-labels from `hits.json` + `hit_signals.npz` (no video pass) and skips
  doubles.

Consequence: openings and shot sequences depend on the owner's stroke labels. The report says
how complete they are instead of filling gaps.

## 3. Goal, done-means and non-goals

Goal: the owner can correct strokes on the match details page; corrections apply at once,
retrain the classifier on demand, and feed a rally-tactics part of the strategy report.

Done means:

1. Strokes block on the match details page: set type, swap player, mark not-a-hit, mark unsure,
   confirm, add a missed shot; saved to `data/stroke_labels/<stem>.json`; survives re-analysis.
2. A save rewrites `strokes.json` with owner labels applied; insights and the strategy report
   (stale badge) pick it up without re-analysis.
3. Retrain button: a queued job trains a candidate on ShuttleSet + owner train labels, scores
   candidate and current model on the same owner test labels, keeps the candidate only by the
   rule in 6.3, then relabels every singles match with owner labels re-applied. Roll back works.
4. Strategy report: overview additions, per-stroke effectiveness tally, opening table, new
   caveats; as cited facts for the LLM and as fact-driven tables on the Strategy tab.
5. Tests as in section 9 pass; full pytest and `node --test tests/js/*.test.mjs` green.

Non-goals (to-do list, recorded in `.ai/STATE.md`): forehand/backhand; landing depth; a
per-game split; feeding not-a-hit / added shots into hit-detector training; career tactical
report; classifier relabel for doubles.

## 4. Owner decisions

- Pattern claims across matches belong in a later career report; the match report shows counts.
- Auto and owner rally winners count equally (the report states how many rallies have one).
- Layout follows the pasted sample, minus forehand/backhand and landing depth.
- Retraining is a button; a new model is kept only if it does at least as well on test matches.

## 5. Part 1: stroke labeller

### 5.1 Owner labels: `data/stroke_labels/<stem>.json`

```json
{"schema": 1, "revision": 7, "split": "train",
 "labels": [
   {"time_sec": 12.512, "hitter": "lower", "stroke": "smash", "kind": "label"},
   {"time_sec": 14.020, "hitter": "upper", "stroke": null,   "kind": "not_hit"},
   {"time_sec": 15.300, "hitter": "upper", "stroke": null,   "kind": "unsure"},
   {"time_sec": 17.100, "hitter": "lower", "stroke": "lift", "kind": "added"}]}
```

- `kind`: `label` (type confirmed or corrected; `hitter` may differ from the model's), `not_hit`,
  `unsure` (excluded from training and scoring, model label kept), `added` (a missed hit).
- `stroke` is one of the 12 ids in `stroke/stroke_types.py`. Doubles `hitter` is a player id.
- `split`: `train` (default) or `test`; refused (409, `reason: "split_locked"`) once a training
  run has used this video's labels: listed in the live model sidecar's `train_matches` /
  `val_matches`, or as a scored test video in `weights/stroke-model-history.json`.
- Keyed by time: a label attaches to the nearest detected hit within 0.15 s (`kind` `added`
  never attaches); unattached `label` / `not_hit` rows are kept and listed as "orphaned".
- Module `badminton_analysis/stroke/owner_labels.py`, modelled on `outcomes/labels.py`: atomic
  write with replace retries, caller holds the lock, max 5000 labels, validation total.
- One-time import of any filled rows in `outputs/dji-stroke-labels/LABELS.md` (split taken from
  the sheet), only into a video with no label file yet.

### 5.2 Applying labels to `strokes.json`

`owner_labels.apply(result_dir, doc)` rewrites `strokes.json` (schema 2 kept, additive fields):

- every stroke gains `source: "model" | "owner"`; an owner `label` sets `stroke`, `group`,
  `hitter` and keeps the model's as `model_stroke` / `model_hitter`;
- `not_hit` drops the stroke from `strokes` (kept under `removed` for the UI);
- `added` inserts a stroke (`source: "owner"`, `confidence: null`, frame from time x fps);
- top-level `owner_labels: {"labelled", "added", "removed", "revision"}`; `distribution` and
  `groups` recomputed.

`stroke/relabel.py` calls `apply` after writing, so a relabel never loses corrections. The
match pipeline calls it after its own `strokes.json` write. `strokes.jsonl` (posture reports) is
re-scored for changed types by the same code path relabel uses. The insights sidecar already
lists `strokes.json` as an input, so insights and the strategy report go stale on their own.

### 5.3 API

Blueprint `web/stroke_labels_routes.py`, same style as `rally_labels_routes.py`:

- `GET /api/stroke-labels/<stem>` -> `{schema, revision, split, split_locked, labels}`.
- `PUT /api/stroke-labels/<stem>` `{revision, split?, labels}`: 400 invalid, 404 unknown video,
  409 `stale` / `unreadable` / `split_locked`. Saves, then applies to `strokes.json` unless that
  video's match job is queued, running or paused (`apply_skipped: "job_active"`).
- Delete scope `all` removes the label file; scope `match` keeps it.

### 5.4 UI (match details page)

- Strokes block beside the Point block, for the selected rally: one row per shot (time, player,
  type, source badge auto / confirmed / corrected, confidence).
- Click a row: the player seeks to 0.5 s before the hit and pauses.
- Keys: type buttons with keys (shown on the buttons), `P` swap player, `X` not a hit, `?` unsure,
  Enter confirm and next; "Add missed shot here" at the current time (choose player and type).
- Tally: "84 of 174 shots labelled · model right on 61 %" (agreement on labelled shots).
- Train/test switch per match, disabled with a reason when locked.
- EN + ZH strings in `core/i18n.js`; a 409 reloads and says so. Doubles: the same, by player.

## 6. Part 2: retraining and model promotion

### 6.1 Stroke model card (Pipeline view)

Current model, its test-split result against the bar of the classifier spec (7-group accuracy
>= 85 %, every group recall >= 75 %, unknown <= 15 %: met / not met yet), label counts (train,
test, new train labels since the last run), Retrain, the run history (date, old -> new, kept /
rejected and why) and Roll back.

Retrain is enabled with >= 50 new train labels and >= 20 labelled test shots; otherwise the card
says what is missing. `GET /api/stroke-model` gives the card; `POST /api/stroke-model/retrain`
and `POST /api/stroke-model/rollback`.

### 6.2 Training job

- Kind `training`, id `train_strokes`, in the shared `JobSlots`; one at a time (409); cancellable,
  not pausable (pause answers 409 like `strategy_` jobs).
- Runs `scripts/train_stroke_classifier.py` with a new `--stroke-labels data/stroke_labels`
  loader (train-split files only; test split never loaded) plus `--dataset data/hit_dataset`
  when present, `--out weights/candidates/stroke-classifier-<utc>.pt`.
- Owner `added` hits need signals at their frame; the loader reads them from that result's
  `hit_signals.npz` and skips (and counts) any frame outside it. Verify in the plan that the npz
  holds per-frame signals for the whole video.

### 6.3 Scoring and the keep rule

- New `scripts/score_stroke_model.py` (thin; logic in `stroke/model_eval.py`): runs a weights
  file over the saved hits + signals of every test-split video and scores against owner labels
  with `stroke_metrics.score` (unsure excluded, not-hit ignored, added counted as misses for
  the hit detector only, so excluded here).
- Both models are scored on the identical shot set. Keep if candidate 7-group accuracy >= the
  current model's and candidate unknown rate <= 15 %; otherwise reject, delete the candidate and
  record the numbers. No current model: keep if unknown <= 15 %.
- Keep = move the live `weights/stroke-classifier.pt` (+ `.json`) to `weights/history/<utc>/`
  (last 5 kept), move the candidate in place, append to `weights/stroke-model-history.json`.
  `weights/` is a mounted volume in Docker, so this survives an image rebuild.
- Roll back = the same swap with the newest history entry; then relabel.

### 6.4 After a keep or roll back

A follow-on job (kind `training`, id `relabel_strokes`) runs `relabel` + `apply` on every
singles result; skips results whose match job is queued, running or paused; doubles results get
`apply` only. Strategy reports turn stale; they are not regenerated (minutes each).

Known risk: with about 20 test shots per group a 1-2 point difference is noise, so the >= rule
can flip models on small changes. The history shows it; tighten when the test split grows.

## 7. Part 3: rally tactics in the strategy report

### 7.1 Per-shot table (`strategy/shots.py`)

Built from `strokes.json` (owner labels applied), rally segments, `rally_outcomes.json` and
`detections.jsonl`. Per shot: rally id, index, hitter, stroke, group, source, time, and:

- zone: the hitter's court position within +-8 frames of the hit; depth from the net front
  <= 1.98 m, mid 1.98-4.7 m, rear > 4.7 m; side left / centre / right in thirds of 6.1 m, from the
  player's own view facing the net (near and far mirrored); `sent_to` = the opponent's zone at
  the next shot;
- tempo: seconds since the previous shot; fast < 0.7, slow > 1.5, else normal;
- rushed reply: the receiver moved >= 2.5 m within <= 1.2 s to reach the next shot;
- link to the next shot broken when that shot is by the same hitter or > 2.5 s later.

Position check per video and player: unusable when > 40 % of in-play frames lie within 0.25 m
of that half's outline or the net; zones and rushed are then unavailable for that video.

All cut-offs are constants in `shots.py`, set from 0003 (0004 fails the position check);
recheck them on owner-labelled matches.

### 7.2 Effectiveness tally

Per stroke type, for the focus player and the opponent (singles; doubles per team):

- effective: the hitter's side won the rally on this shot or the hitter's next one, or the
  reply was defensive (lift, block, clear from the rear) or rushed;
- ineffective: the hitter's last shot of a lost rally, or the opponent's next shot ended the
  rally in the opponent's favour;
- neutral: the reply is known and neither of the above;
- unknown: broken link and no rally-end evidence.

Types with < 5 attempts are listed but never named a strength or weakness.

### 7.3 Opening table

- Complete opening: shot 1 is a serve, shots 1-3 alternate hitters, no broken link.
- Advantage at shots 2 and 3: attacking = smash, net_rush, push, drive; defensive = lift, block,
  clear from the rear. The player who plays an attacking shot or forces a defensive or rushed
  reply gains it; otherwise neutral.
- Up to 8 rows: owner-labelled openings first, then the closest-fought points, then the
  longest rallies.
- Totals: openings won per player, how often the opening winner won the point, "openings fully
  detected N of M".
- Under 5 complete openings: the table shows; no opening claims (facts marked insufficient).

### 7.4 Overview additions

Active play time, share of rallies under 9 s, shots per rally (detected or labelled).

### 7.5 Facts, prompt and UI

- New fact ids (examples): `match.active_play_sec`, `match.rallies_under_9s_share`,
  `match.openings.complete`, `match.openings.won_by_you`, `you.eff.<stroke>` (value
  `{effective, neutral, ineffective, unknown}`), `opp.eff.<stroke>`, `you.opening_win_point_share`,
  `match.owner_labelled_share`, `match.stroke_model` (`{accuracy, bar_met}`).
- The existing validator rules apply unchanged; new facts follow the `unavailable` /
  `insufficient` conventions of `facts.py`.
- The system prompt names the tally and openings as the basis for strengths, weaknesses and the
  game plan.
- Strategy tab: the tally and opening table render from the facts pack, not from LLM text.
- New caveats: stroke model accuracy and bar; owner-labelled share; positions unusable;
  openings incomplete.

## 8. Code layout

- New: `stroke/owner_labels.py`, `stroke/model_eval.py`, `web/stroke_labels_routes.py`,
  `web/stroke_model_routes.py`, `strategy/shots.py`, `scripts/score_stroke_model.py`,
  `static/kestrel/widgets/strokelabels.js`, `static/kestrel/views/strokelabels_model.js`,
  `static/kestrel/widgets/strokemodel.js`.
- Changed: `stroke/relabel.py`, `scripts/train_stroke_classifier.py`, `strategy/facts.py`,
  `strategy/prompts.py`, `strategy/report.py` (tables), `app.py` (blueprints, jobs, delete
  scope), match pipeline `strokes.json` write, `static/kestrel/views/workspace.js`,
  `views/pipeline.js`, `widgets/strategy.js`, `core/api.js`, `core/i18n.js`, `widgets.css`.

`python scripts/ai_overlap.py` flags many branches on `app.py` and `static/kestrel/`; most look
merged. Check again at plan time and rebase on any live one.

## 9. Testing

- `owner_labels`: round trip, attach within 0.15 s, orphaned rows, added / not-hit / unsure,
  revision conflict, split lock, LABELS.md import once, junk files never raise.
- `apply`: owner wins, model fields kept, counts recomputed, relabel keeps corrections.
- Routes: 400 / 404 / 409 cases, apply skipped while a job is active, delete scopes.
- Trainer loader: test split never loaded; added hits outside the npz counted and skipped.
- `model_eval`: identical shot set for both models; keep and reject paths; roll back; history
  capped at 5; no current model.
- Jobs: one training at a time, cancel, pause refused, relabel skips active matches.
- `shots.py`: zones mirrored per side, tempo bands, rushed, broken links, position check fails on
  a fixture like 0004, effectiveness classes, opening completeness and advantage, < 5 rule.
- Facts / validator: new ids cite-able, numbers held, insufficient openings make no claims.
- JS: labeller model (keys, states, tally), model card states, Strategy tab tables.
- Live: label part of 0003 in the UI, retrain once (record the time), regenerate the 0003 report.

## 10. Risks

- Labelling effort: about 170-200 shots per match; Enter-to-confirm keeps it fast.
- Small test split makes the keep rule noisy (6.4).
- Zones depend on court calibration; the position check catches the obvious failures only.
- Effectiveness and advantage rules are heuristics; the report states the definitions.
