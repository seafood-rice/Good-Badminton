# M1 · Per-hit attributes (forehand/backhand, around-head, above/below net)

Date: 2026-11-02. Roadmap: `docs/superpowers/research/2026-11-02-oss-landscape-roadmap.md` section M1.
Branch: `claude/m1-per-hit-attributes-7e2019`.

## 1. Goal

Every classified hit also says **forehand or backhand**, **around-the-head or not** and **hit above or below
the net**, learned from ShuttleSet columns already on disk. The owner can correct forehand/backhand in the
stroke labeller, the Retrain gate rejects a model whose FH/BH gets worse, and the strategy facts pack
gains FH/BH facts.

Done means (this PR):
- The training script reads `backhand`, `aroundhead`, `hit_height` for every ShuttleSet hit (existing
  `data/hit_dataset` files, without rebuilding them) and trains three attribute heads next to the type head.
- `strokes.json` hits carry `attributes` when the live model has heads; old models still load and
  behave exactly as before (no `attributes` key).
- Owner FH/BH labels overlay reversibly, are used for training, and feed a keep-if-not-worse FH/BH check
  in the Retrain gate.
- Labeller rows show FH/BH and can set it; strategy facts `hand.*` exist.
- A candidate model is trained on the local ShuttleSet data and its validation FH/BH accuracy (Sindhu
  match) is reported. It is **not** promoted to `weights/stroke-classifier.pt` by this PR: that is the
  owner's call (Retrain card, or copy).

Not in this PR (deferred, see section 9): the X3D-M clip fallback, owner labelling of around-head /
above-net, a FH/BH bar measurement on DJI footage (needs owner test labels first).

## 2. ShuttleSet columns (measured on all 46 local label folders, 36,484 rows)

| Column | Values | Meaning used |
|---|---|---|
| `backhand` | `1`/`1.0` (13,699), blank (22,785) | 1 = backhand, blank = forehand |
| `aroundhead` | `1`/`1.0` (3,336), blank | 1 = around-the-head, blank = no |
| `hit_height` | `1` (13,170), `2` (23,272), blank (42) | 1 = above the net, 2 = below; blank = unknown |

Backhand share by type ranges from 0.0 (smash) to 0.98 (short serve) and is 0.45-0.58 for net shots,
blocks, pushes and lifts, so the head has to read the pose, not the type. `hit_height` is mostly
implied by type (smash 99 % above, lift 99 % below) but not for blocks (21 % above) and net shots.

## 3. Attribute vocabulary

`stroke_types.ATTRIBUTES = ("backhand", "around_head", "above_net")`. Each is a boolean per hit; `None`
means unknown (no label, or the model abstained). Encoding in arrays: `1` true, `0` false, `-1` unknown.

## 4. Data: attributes for the hit dataset

- `scripts/shuttleset_labels.parse_match_hits` adds `backhand`, `around_head`, `above_net` (1/0/-1) to each
  hit dict. Blank `backhand` / `aroundhead` = 0; blank or unparsable `hit_height` = -1.
- `scripts/build_hit_dataset.encode_hits` also writes `backhand`, `around_head`, `above_net` int64 arrays,
  aligned with `hit_frames`. New builds carry them.
- **Existing files are not rewritten.** `train_stroke_classifier.load_shuttleset(dataset_dir, labels_root=None)`:
  when an `.npz` lacks the attribute arrays and `labels_root` (default `data/shuttleset`, flag
  `--shuttleset-data`) has `labels/<match>/` and `alignment/<match>.json`, it recomputes that rally's labels
  exactly as the builder did: `rally_spans` of the parsed hits, `clip_window(span.start, span.end, offset)`,
  then `encode_hits(span.hits, offset, clip_start, fps, n_frames)` with the npz's own fps and frame count.
  The attributes are used **only if** the recomputed `hit_frames` and `stroke_raw` equal the npz's exactly;
  otherwise that rally trains the type head only and is counted in a printed "attributes not joined" line.
  A broken label source for a match (no homography row, missing or unreadable alignment offset, parse
  error, unreadable file) never stops training: that match is treated as not joined. Only `rally` files
  count in that line; gap files carry no labels to join.
- `Rally.hits` entries gain an `attrs` dict `{name: 0|1}` holding only the known attributes.

## 5. Model

- `StrokeNet(in_ch, n_classes=12, hidden, levels, dropout, n_attrs=0)`: adds `attr_head = Linear(2*hidden,
  n_attrs)` when `n_attrs > 0`. `forward(x)` still returns type logits only (every current caller keeps
  working); `forward_all(x)` returns `(type_logits, attr_logits or None)`.
- `MODEL_SCHEMA = 2`. Sidecar adds `attributes` (the trained attribute names, in head order, may be empty)
  and `attribute_min_confidence` (0.6). `load_stroke_model` accepts schema 1 (no heads, `attributes = []`)
  and schema 2; old code reading a schema-2 file refuses it with a clear "incompatible" error. For schema 2
  the loader raises `StrokeModelError` unless `attributes` is a list of unique names, each in
  `stroke_types.ATTRIBUTES`, and `attribute_min_confidence` is a real number (not a bool) in [0.5, 1].
- `predict_attrs(model, X)` -> `(n, n_attrs)` sigmoid probabilities, or `None` when the model has no heads.

## 6. Training

- Loss = type cross-entropy (as today) + mean over attributes of a **masked** BCE (unknown labels ignored),
  each attribute with `pos_weight = clamp(neg/pos, 1, 10)` from the training counts.
- An attribute is trained (and listed in the sidecar) only when the training set has at least
  `MIN_ATTR_LABELS = 200` known labels for it, both classes present. An owner-only run therefore trains no
  heads rather than untrained ones.
- Epoch selection is unchanged (worst group recall, then accuracy); FH/BH never picks the epoch.
- `val_metrics.attributes = {name: {"n", "accuracy", "abstained"}}` on the validation hits; accuracy counts
  an abstention as wrong (abstaining can never raise it).
- Owner train labels carry `backhand` when the owner set it (section 7), via `owner_backhand`.

## 7. Inference output and owner overlay

`stroke_classifier.classify_hits`, for a hit whose window has signal and a model with heads, adds:

```json
"attributes": {"backhand": true, "around_head": false, "above_net": null},
"attribute_confidence": {"backhand": 0.91, "around_head": 0.97, "above_net": 0.55}
```

`attribute_confidence` is `max(p, 1-p)`, rounded to 4; the value is `null` below
`attribute_min_confidence`. Hits with no signal, and every hit of a model without heads, get no
`attributes` key. `load_classifier`'s info adds `"attributes": [...]`. `strokes.json` stays schema 2
(additive keys).

Owner labels (`data/stroke_labels/<stem>.json`, schema stays 1): a `label` or `added` row may carry
`"backhand": true|false`. Absent = not set. Any other value makes the label invalid (as for other fields).
`not_hit` / `unsure` rows drop it. `owner_labels.apply`, for a label with `backhand`:
- keeps the model's dict in `model_attributes` (`null` when the hit had none) and sets
  `attributes.backhand` to the owner's value and `owner_backhand` to it;
- an added shot gets `attributes: {"backhand": v}` and `owner_backhand: v`.
`model_view` restores `attributes` from `model_attributes` (removing it when that was `null`) and drops
`owner_backhand`, so apply stays idempotent and reversible.

## 8. Retrain gate, labeller, facts

**Gate** (`model_eval`): `test_hands(labels_dir, results_dir)` -> `[(stem, hits, backhand_truth)]` for test
split `label` rows that carry `backhand` (attached like `test_shots`). `score_hands(weights, hand_shots,
results_dir)` -> `{"n", "correct", "accuracy", "abstained"}` (accuracy: abstain or no heads = wrong;
`accuracy` None when n = 0). `retrain.run` scores candidate and live on the same leak-filtered hand shots
and stores them as `metrics["backhand"]` in the record. `decide(candidate_m, live_m)` gains one rule after
the existing ones: when both carry `backhand` with `n >= MIN_HAND_SHOTS (20)` and the candidate's accuracy
is below the live one's, reject `{"code": "lower_backhand_accuracy", "candidate", "live"}`. With fewer
hand shots the rule does not apply (the type rule alone decides, as today). A second rule follows it:
`retrain.run` also puts each model's sidecar `attributes` list in its metrics dict (`metrics["attributes"]`;
a schema-1 sidecar gives `[]`, an unreadable or absent sidecar leaves the key out), and when the live
model lists attributes the candidate lacks, `decide` rejects `{"code": "lost_attributes", "missing": [names]}`
regardless of hand-shot counts. An absent key on either side means unknown and the rule does not fire (old
history records keep working); a schema-1 live model has nothing to lose. `describe` and the UI
`sm.code.*` strings know both codes (`missing` is shown joined with commas).

**Labeller** (`strokelabels_model.js`, `widgets/strokelabels.js`): rows get `backhand` (effective: owner's
if set, else the model's `attributes.backhand`, else null) and `model_backhand`. New action
`{type: 'hand', backhand: bool}` writes a `label` (or keeps `added`) for the row with the shown stroke and
hitter plus `backhand`; it needs a known stroke, like confirm, and does nothing on a `not_hit` or `unsure` row. Setting a type (`set`, `swap`) keeps the
row's existing owner `backhand`. Key `H` toggles the effective value. The row shows a FH / BH chip; the
action bar shows Forehand / Backhand buttons. A labelled hand that differs from the model counts the row
as `corrected`.

**Facts** (`strategy/shots.py`, `strategy/facts.py`, `strategy/report.py`): shot rows carry `backhand`
and `around_head` (bool or None). Per player (`you` / `opponent`; `team.*` in doubles, normally
unavailable there because doubles has no stroke labels):
- `p.hand.n`: non-serve shots with a known FH/BH (`insufficient` / `few_attempts` when 0 < n < `MIN_HAND`).
- `p.hand.backhand_share`: backhand share of those.
- `p.hand.backhand_share_pressed`: the same over shots under pressure: the previous linked shot was the
  opponent's attacking shot (`ATTACKING`) or that shot rushed this reply (`rushed_reply`).
- `p.hand.around_head_rear_share`: around-head share of rear-zone shots with known around-head.
Each is `insufficient` / `few_attempts` below `MIN_HAND = 20` shots, `unavailable` / `no_data` with none,
and `validated: false` until an FH/BH bar is met on owner footage. Report templates (en + zh) mark them
"(model estimate, not validated)"; `shot_selection` lists `you.hand.backhand_share`, `scouting` lists
`opponent.hand.backhand_share_pressed`.

## 9. Deferred

- X3D-M hitter-crop clip classifier (NTNU recipe, CC0): only if the pose head misses the owner bar on
  DJI. Needs owner FH/BH test labels first.
- Owner toggles for around-head / above-net (the heads still predict them).
- FH/BH bar on DJI (suggested 0.85): owner labels FH/BH on a test-split video, then Retrain.
- Rebuilding `data/hit_dataset` to carry the arrays natively (not needed: the join is exact).

## 10. Risks

- R1 Broadcast domain: ShuttleSet is broadcast footage; FH/BH from far-court pose may not transfer to DJI.
  The Retrain gate and the `validated: false` facts keep this visible until owner labels exist.
- R2 Join drift: a builder change would break the exact-equality join; then attributes silently thin out.
  The loader prints how many rallies were joined.

## 11. Results (2026-11-02)

- Join: `load_shuttleset(data/hit_dataset, data/shuttleset)` loads 392 rallies / 3,630 hits from the 5 local
  matches; all 392 joined exactly, every hit has all three attributes (backhand 1,292 / 3,630).
- Candidate trained with `scripts/train_stroke_classifier.py --dataset data/hit_dataset --val-match <Sindhu>`
  (60 epochs, seed 0; 3,031 train / 599 val hits). Validation on the held-out Sindhu match (499 matched hits;
  abstention counted wrong):

  | Head | Accuracy | Abstained |
  |---|---|---|
  | backhand | 0.890 | 21 |
  | around_head | 0.942 | 5 |
  | above_net | 0.930 | 8 |

  Baselines on the same match (all 644 labelled hits, true types): always-forehand 0.734, per-type
  majority from the other matches 0.719. So the head reads the pose, not the type.
- The candidate's type head: 7-group accuracy 0.850, unknown 0.01 (the live schema-1 model's sidecar says
  0.867 / 0.004 on the same match). It was not promoted; the owner decides (Retrain card, or copy it to
  `weights/stroke-classifier.pt`). Broadcast numbers, not DJI.
