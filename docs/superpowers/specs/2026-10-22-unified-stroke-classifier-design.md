# Unified stroke classifier — design

Status: design approved in conversation 2026-10-22; this spec awaits owner review.
Branch: `claude/stroke-timeline-mismatch-2bfb29`.

## 1. Problem

On `DJI_20261010092251_0014_D` the stroke timeline and the Technical Analysis panel label the
same hits differently: of 104 shared hit frames, not one label agrees. The cause is two
independent stroke classifiers:

| | Timeline / distribution | Technical Analysis |
|---|---|---|
| Classifier | BST learned model (`stroke_recog/`) | rule-based `stroke/classifier.py::classify_stroke` |
| Output file | `strokes.json` (`stroke`) | `strokes.jsonl`, `technique_summary.json` (`stroke_type`) |
| Labels | serve/clear/smash/drop/drive/net/uncertain | high_clear/smash/drop_shot/serve |
| Behaviour on DJI footage | 135 of 173 hits `uncertain`; confidences mostly 0.1–0.4 | 66 of 104 hits `serve`, because "racket not overhead" falls back to serve; missing data falls back to `high_clear` at 0.2 |

BST is broadcast-trained (risk R7 in the B spec, `2026-07-29-match-stroke-recognition-b-design.md`).
Its low confidence on fixed-camera footage is domain shift, not a threshold to tune. The rule
classifier cannot tell net, lift or drive apart at all.

## 2. Owner decisions (2026-10-22)

1. **One stroke-identification mechanism for the whole app.** The timeline, distribution,
   Technical Analysis, insights and training plan all read the same per-hit label.
2. **Taxonomy: all 12 BST stroke types plus `unknown`.** They roll up into 7 groups: smash,
   clear, drop, net, lift, drive, serve.
3. **Approach: a new stroke model built on the hit detector's inputs** (approach 2). Retraining
   BST is only a fallback, and needs owner go-ahead (§8).
4. **Ground truth: the owner labels DJI clips** (about 300–400 hits).
5. **Accuracy bar** (on the held-out DJI test videos, at the 7-group level):
   - overall accuracy ≥ 85%;
   - recall ≥ 75% for every group;
   - `unknown` ≤ 15% of hits.

   12-type accuracy is reported but does not gate.
6. **Types with no technique reference ranges are labelled but not scored** (option A). Their
   ranges belong to the scoring project and will be calibrated on pro footage.

## 3. Taxonomy

| id | BST / ShuttleSet types | group |
|---|---|---|
| `short_serve` | 發短球 | serve |
| `long_serve` | 發長球 | serve |
| `clear` | 長球 | clear |
| `lift` | 挑球, 防守回挑 | lift |
| `smash` | 殺球, 點扣 | smash |
| `drop` | 切球, 過度切球 / 過渡切球 | drop |
| `net_shot` | 放小球 | net |
| `block` | 擋小球 | net |
| `cross_net` | 勾球 | net |
| `net_rush` | 撲球 | net |
| `drive` | 平球, 小平球, 後場抽平球, 防守回抽 | drive |
| `push` | 推球 | drive |
| `unknown` | model abstains (below threshold, or too little signal) | — |

`push` sits under drive, as in ShuttleSet's mapping and BST. ShuttleSet hits labelled 未知球種 are
excluded from training; they are hits the annotators could not judge, not a class.

Training hits available in `data/hit_dataset` (5 ShuttleSet matches, 3,631 labelled, 198
unknown excluded):

| type | hits | type | hits |
|---|---|---|---|
| lift | 641 | push | 229 |
| net_shot | 596 | drive | 174 |
| smash | 498 | cross_net | 152 |
| block | 387 | short_serve | 142 |
| drop | 361 | long_serve | 89 |
| clear | 311 | net_rush | 51 |

`net_rush` and `long_serve` are thin; the DJI training half is expected to help both.

The taxonomy lives in one module, `badminton_analysis/stroke/stroke_types.py`. It holds the ids,
the groups, the ShuttleSet raw-type map and the mapping to technique profiles. Python consumers
import it; the UI gets the same lists from `i18n.js`, and a test asserts the two agree.

## 4. Architecture

### 4.1 Model and training (offline)

```
data/hit_dataset/<match>/*.npz   (signals + hit_frames + hitter + stroke_raw)
  + DJI training half (hit_signals.npz + labelled hits from LABELS.md)
      -> scripts/train_stroke_classifier.py
      -> weights/stroke-classifier.pt  + weights/stroke-classifier.json (sidecar)
```

- **Inputs:** `hit_features.compute_features` (`FEATURE_SCHEMA` 1). These are the hit detector's
  camera-independent features: shuttle position, speed and turn angle in court units; each
  player's pose relative to their torso; feet in court metres.
- **Window:** −0.5 s to +1.0 s around the hit at 30 Hz (46 steps). The player blocks are
  reordered hitter-first, so one model serves both near and far hits. The hitter comes from
  `hits.json`.
- **Model:** `badminton_analysis/stroke/stroke_model.py`, a small dilated temporal CNN in the
  same style as `hit_model.py`:
  - input normalisation is stored inside the model;
  - temporal pooling feeds a 12-logit head;
  - it is saved as a state dict with a JSON sidecar and loaded with `weights_only=True`.
- **Sidecar fields:** schema, `feature_schema`, `feature_dim`, `target_hz`, window, the 12 ids in
  order, `unknown_threshold`, the training-data hash, and the scores at training time.
- **Augmentation:** left-right court mirror (swaps x, and the left/right keypoints) and ±2-step
  jitter of the hit time.
- **Validation:** ShuttleSet Sindhu v Chochuwong stays held out, the same split as the hit
  detector.

### 4.2 Match pipeline

```
post-loop:
  rally segmentation (unchanged)
  hit detection -> hits.json
  stroke classifier (hit_signals + hits) -> strokes.json   <- the only stroke file
  technique analysis reads strokes.json labels (tracked player's hits)
       -> strokes.jsonl, technique_summary.json  (stroke_type = the same id)
```

- New module: `badminton_analysis/stroke/stroke_classifier.py`, which provides
  `classify_hits(sig, hits, model, meta) -> [label]`.
- `strokes.json` (schema 2):
  - `{"schema": 2, "classifier": {"version", "weights_sha256"} | null, "strokes": [...],
    "distribution": {id: n}, "groups": {group: n}, "hit_source", "shuttle_source"}`;
  - each stroke is `{"frame", "hitter", "stroke": id|"unknown", "group": group|null,
    "confidence", "hit_confidence"}`.
- `TechniqueAnalysisRunner.run` takes the labels in place of `classify_stroke`:
  - the hit's id maps to a technique profile (§4.3);
  - `StrokeEvent.stroke_type` carries the id;
  - the report carries `scored: false` when there is no profile.

### 4.3 Technique profiles

| id | profile used |
|---|---|
| clear | `high_clear` |
| smash | `smash` |
| drop | `drop_shot` |
| short_serve, long_serve | `serve` |
| everything else, and `unknown` | none: labelled, not scored |

- Unscored hits appear in Technical Analysis with their label and "no reference ranges yet".
- They are left out of `overall_score`, `by_type` averages, recurring weaknesses, the training
  plan and the insights technique radar.
- `scoring.py` must not raise a KeyError on them; the guard sits in the runner, before scoring.
- The quality model (`quality-high_clear.pt`) keeps running only when the profile is
  `high_clear`.

### 4.4 Removed

- `badminton_analysis/stroke_recog/` (BST), and its tests.
- `--bst-model` (`main.py`), `_bst_weights` and the `bst` flag in `/api/models` (`app.py`).
- `third_party/bst/` stays vendored but unused; its removal is a separate decision.
- `stroke/classifier.py` (`classify_stroke`) and `test_classifier`.
- Legacy UI (`static/kestrel/legacy/kestrel-legacy.js`, `web_ui.html`): its BST and rule label
  tables are replaced with the new ids. The drill-mode stroke select is not removed (see below).

Drill mode (`/api/posture/analyze`, `main_posture.py --stroke-type`) is unchanged: the user
chooses the stroke there, so it is not stroke identification. It keeps its 4 profile names.

### 4.5 UI (Kestrel)

- Timeline chips, Technical Analysis cards and the stroke list show the 12 type names, in
  zh-Hant, zh-Hans and en, with `unknown` shown as 不確定 / uncertain.
- The distribution, filter chips, overview radar, net share and findings use the 7 groups.
  - `i18n.js` `TECHNIQUE_IDS` stays the 7 groups.
  - A new `STROKE_TYPE_IDS` lists the 12 ids.
- `workspace.js` drops `STROKE_MAP` (the old rule-label map). Both panels read `stroke` / `group`
  straight from the shared ids.
- Until §6 passes, the results page shows "stroke accuracy not yet verified on your footage".
  The notice is driven by a sidecar flag (`validated_on_owner_footage`), so it disappears with the
  validated weights and needs no code change.

### 4.6 Existing results

- New command: `python -m badminton_analysis.stroke.relabel <result_dir>...`.
  - It loads `hit_signals.npz` and `hits.json`, runs the classifier, and rewrites `strokes.json`.
  - It then re-runs technique scoring from the saved per-frame data, if present. Otherwise it
    marks Technical Analysis stale.
  - It takes seconds per result.
- A result whose `strokes.json` has no `schema: 2` shows "stroke labels out of date — re-label"
  instead of the old labels. No old label is ever shown beside a new one.

## 5. Error handling

| Situation | Behaviour |
|---|---|
| Weights or sidecar missing or incompatible | Every hit is `unknown`, `classifier: null` is recorded, the UI says "stroke model not installed"; the analysis continues |
| Window has too little signal (shuttle visible in < 50% of post-hit steps, or no hitter pose) | `unknown`, and the model is not run |
| Doubles | Labels withheld, as today (`STROKES_WITHHELD`) |
| Rally segmentation degraded | Labels withheld, as today (B11 §12a-C) |
| No hits | No `strokes.json`, as today |

No other classifier ever fills in.

## 6. Ground truth and evaluation

### 6.1 Labelling sheet

`scripts/make_stroke_label_sheet.py <result_dir>...` writes `outputs/dji-stroke-labels/`:

- one clip per rally, with the original video time burnt in;
- `LABELS.md`, with one row per detected hit: `hit_sec`, `player` (near/far) and `stroke` (blank
  for the owner);
- allowed values: the 12 ids; `?` (excluded); `not-a-hit`. Missed hits may be added as new rows;
- the split (train or test) for each video, decided before any training and written in the sheet;
- a progress table of labelled hits per group, per split.

**Target:** at least 20 labelled hits per group in the test split, and about as many in the train
split. That is about 300–400 hits over 4–6 videos. The owner picks videos with varied play.

### 6.2 Scoring

`scripts/score_strokes.py` matches labels to `strokes.json` by time (±0.2 s, same-hitter
preferred) and reports:

- **Overall accuracy** (7-group) = correct / confident predictions.
- **Recall per group** = correct / labelled hits in that group. `unknown` counts as a miss.
- **Unknown rate** = `unknown` / matched hits.
- **12-type accuracy** and the confusion matrix (informational).
- **Labelled hits with no detected hit**, reported separately; they count against the hit
  detector, not the stroke model.
- **The same figures on the held-out ShuttleSet match.**

**Done** = on the DJI test split, overall ≥ 85%, every group's recall ≥ 75% and unknown ≤ 15%.
The test split is scored once per candidate model; the threshold is tuned on the train split
only.

## 7. Delivery order

1. **Labelling sheet tool** (so the owner can label while the rest is built).
2. **Classifier and training:**
   - `stroke_types.py`, the window builder, `stroke_model.py` and `train_stroke_classifier.py`;
   - a first model on ShuttleSet only, scored on Sindhu.
3. **Integration** (its own PR):
   - schema-2 `strokes.json`;
   - the technique runner reads the labels;
   - removal of BST and the rule classifier (§4.4);
   - the UI (§4.5) and the re-labelling command (§4.6);
   - the "not yet verified" notice is shown.
4. **DJI round:**
   - train with the DJI train split and tune the threshold;
   - score the test split once and record the result honestly;
   - ship validated weights only if the bar is met.

## 8. If the bar is missed

- Use the confusion matrix to find the weakest groups. Add labels for those groups, or change the
  model (window length, features, class weights). Up to three rounds.
- If it still misses, stop and bring the numbers to the owner. Fine-tuning BST is the fallback,
  only with owner go-ahead.

## 9. Testing

- **New unit tests:**
  - the taxonomy: raw → id → group, the profile map, Python/JS lists in agreement;
  - the window builder: bounds, hitter-first reordering, mirror augmentation;
  - sidecar validation, and the `unknown` fallbacks;
  - the `strokes.json` schema-2 writer;
  - the technique runner taking labels, and unscored hits staying out of scores and plans;
  - the re-labelling command;
  - `score_strokes` metrics on a hand-built fixture;
  - label-sheet parsing.
- **Existing tests:** about 45 files reference the old labels. BST tests are deleted with the
  package; the rest are updated to the new ids. The JS tests (`views_model`, `widgets_smoke`)
  are updated for the 12 types.
- **Before each PR:** the full pytest command from `.ai/STATE.md`, `node --test tests/js/*.test.mjs`,
  and a live check on one DJI result that the timeline and Technical Analysis labels are
  identical for every shared hit.

## 10. Coordination

`claude/learned-hit-detection` is active and touches `badminton_analysis/stroke/`, `system.py`,
`stroke_recog/recognizer.py` and the legacy UI. Before editing, run
`python scripts/ai_overlap.py` on the touched paths, and rebase onto that branch's latest work
where the files overlap. `ai_overlap.py` also lists several older branches; check whether they
are stale before treating them as conflicts.

## 11. Out of scope

- Reference ranges for lift, net, block, cross_net, net_rush, drive and push (scoring project).
- Doubles stroke labels.
- Removing `third_party/bst/`.
- Stroke recognition on multi-camera broadcast footage beyond what the held-out ShuttleSet score
  shows.
