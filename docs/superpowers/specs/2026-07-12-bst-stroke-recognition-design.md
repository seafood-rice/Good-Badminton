# BST Stroke-Type Recognition — Design Spec

**Date:** 2026-07-12
**Status:** Design approved; awaiting spec review before writing the implementation plan.

## Goal

Automatically label each stroke in an elevated-back-court **singles** rally video with a
coarse stroke type, by running the MIT-licensed pretrained **BST** (Badminton Stroke-type
Transformer) for inference on inputs built from the app's existing match pipeline. No
model training; no dataset dependency.

## Coarse stroke set (6 classes)

`serve`, `clear`, `smash`, `drop`, `drive`, `net`.

The pretrained ShuttleSet BST predicts a fine taxonomy (25 merged / 35 full classes); we
collapse it to these 6. `uncertain` is an additional runtime label (not a model class)
emitted when confidence is low or inputs are incomplete.

## Non-goals (v1, out of scope)

- Doubles (BST is a singles model; 4-player is a different problem).
- Retraining or fine-tuning BST; using the ShuttleSet dataset at all (inference-only with
  the provided MIT weights avoids ShuttleSet's separate, more restrictive license).
- Drill-mode auto-detect (replacing the manual `--stroke-type` picker in the posture flow).
- The full 25/35-class fine taxonomy in the UI.
- Tactical analysis (shot placement, win prediction) — stroke-type labels only.

## Tech stack / licensing

- **BST**: ported from https://github.com/Va6lue/BST-Badminton-Stroke-type-Transformer
  (MIT). Pretrained ShuttleSet weights (provided via the repo's Google Drive) are
  redistributable under MIT, but — consistent with the RacketDB/quality-model convention —
  weights are NOT committed; they live under `weights/` (git-ignored) with documented
  fetch instructions, and are auto-discovered at runtime.
- **PyTorch** (already a project dependency for the other models). Lazy-imported so
  `--help` and weightless runs stay torch-free where they already are.
- Pose: the match pipeline's existing 17-keypoint COCO pose (RTMPose / YOLO-pose). BST
  expects an MMPose skeleton; the COCO-17 joint layout must be confirmed to match BST's
  expected joint order at implementation time (see Risks).

## Architecture & data flow

The recognizer runs as a post-processing stage over a rally, after the match pipeline's
per-frame processing. It consumes what that pipeline already produces (both players'
tracked poses, shuttle trajectory, court corners, contact-detection track) and emits a
coarse label per detected hit.

1. **Stroke segmentation** — derive the hit-event list `[(frame, hitter)]` from the
   existing contact-detection track (`_analysis_track` in `badminton_analysis/system.py`).
   Hitter = the player nearest the shuttle at contact. This defines which strokes to
   classify. Contact-detection tuning, if needed, is an implementation sub-step.
2. **Per-stroke input assembly** — for each hit at frame `f` by player `P`, build BST's
   input over a `seq_len` window centered on `f`:
   - **Hitter pose**: `P`'s 17 joints per frame, normalized as BST expects (coordinates
     relative to the player bounding box, center-aligned).
   - **Shuttle**: `(x, y)` per frame, normalized to `[0, 1]` by video resolution.
   - **Player court positions**: both players' feet points mapped to court coordinates and
     normalized by the court boundary (court corners come from the existing detection).
   - **Variant**: use the pretrained **ShuttleSet 25-merged-class** BST variant (fewer,
     already-consolidated classes make the coarse-6 mapping cleaner than the 35-class
     full taxonomy). `seq_len` and the exact normalization/joint order are taken from that
     variant's shipped config at implementation time (the model config and class-index
     file are authoritative).
3. **BST inference** — load the pretrained ShuttleSet weights once; run per stroke →
   fine-class logits.
4. **Coarse mapping + confidence gate** — softmax; sum probabilities within each coarse
   bucket; take the argmax bucket. If the top bucket probability is below
   `MIN_STROKE_CONF` (default set at implementation from a quick calibration on real
   footage; start ~0.5) or the input window was incomplete → label `uncertain`.
5. **Output** — attach the coarse label + confidence + `uncertain` flag to each hit.

## Components (new `badminton_analysis/stroke_recog/` package)

Each unit has one responsibility and is testable in isolation:

- `bst_model.py` — ported BST architecture + weight loading. Torch imported lazily inside.
- `inputs.py` — **pure functions** converting per-frame pose/shuttle/court data into BST's
  normalized input tensors. The highest-risk, most-tested unit (format fidelity).
- `classes.py` — the fine→coarse mapping table and the coarse label list. The exact fine
  class index list is taken from the pretrained model's class-index file (authoritative)
  at implementation time; each fine class maps to exactly one of the 6 buckets. Guidance:
  long/short service → `serve`; clear/lob/defensive-lob → `clear`; smash/wrist-smash →
  `smash`; drop/passive-drop → `drop`; drive/driven-flight/back-court-drive/push →
  `drive`; net-shot/return-net/rush/cross-court-net → `net`.
- `recognizer.py` — orchestrates hits + per-frame data → per-stroke coarse labels. The
  single seam the match pipeline calls. Holds the confidence gate and degradation logic.
- Integration in `badminton_analysis/system.py` (match pipeline): after per-frame
  processing, call the recognizer over detected hits; attach labels; write them to the
  output and overlay/stats.
- Weight auto-discovery: `weights/bst-shuttleset.pt`; absent → recognition skipped, match
  analysis unchanged (mirrors `_racket_weights()` / `_quality_weights()`).

## Output surfaces (all presence-keyed; absent → nothing rendered)

- **Video overlay**: coarse label drawn near the hitter at each contact on the analyzed
  match video.
- **Rally stroke timeline**: ordered per-rally sequence (e.g. `serve → clear → drop → net
  → clear → smash`) in the output JSON and on the results screen.
- **Distribution stats**: per-type counts alongside existing match stats. Each stroke
  carries its confidence and `uncertain` flag.

## Error handling / graceful degradation

Same discipline as the racket/quality models:
- Weights absent → recognition skipped; match analysis unchanged.
- Torch / model-load failure → caught; feature off; run continues.
- A stroke whose window has a pose/shuttle gap → `uncertain`, never fatal.
- Low top-class probability → `uncertain`, not a confident wrong guess.

## Testing

- **Hermetic unit tests** (no torch/weights/network):
  - `inputs.py`: synthetic pose/shuttle/court → correct normalized tensor shape and
    values (including window clamping at rally edges and missing-frame handling).
  - `classes.py`: every fine class maps to exactly one coarse bucket; bucket-probability
    summation + argmax; confidence-gate → `uncertain`.
  - `recognizer.py`: orchestration against a **stub** BST model (no real weights);
    degradation paths (absent weights, incomplete window, model-load failure).
- **Synthetic end-to-end**: fake hit events + fake per-frame data + stub model → per-stroke
  coarse labels and the output structure.
- **Real-footage validation** (controller, post-merge, before "done"): run on an actual
  elevated-singles rally clip and eyeball labels against the visible strokes — the
  domain-shift reality check. Record agreement qualitatively; if transfer is poor, ship the
  feature labeled experimental (as was done with the AI quality score) rather than
  presenting untrustworthy labels as authoritative.

## Success criteria

- Weightless/torch-absent match runs are byte-for-byte unchanged (feature fully optional).
- With weights present on an elevated-singles rally: every detected hit receives a coarse
  label or `uncertain`; the stroke timeline, overlay, and distribution render.
- On the real-footage validation clip, the coarse labels visibly track the actual strokes
  for clear cases (serve, smash, clear); ambiguous cases fall to `uncertain` rather than
  confident-wrong. (No numeric accuracy target in v1 — no labeled in-domain test set
  exists; this is a qualitative gate, honestly reported.)
- All hermetic tests green; existing suite unaffected.

## Risks (named, mitigated)

1. **Domain shift** (phone-elevated vs broadcast) — #1 risk; same lesson as the quality
   model. Mitigated by the confidence gate and real-footage validation before trusting;
   honest experimental labeling if transfer is poor.
2. **Input-format fidelity** — BST is sensitive to exact joint order / normalization;
   subtle mismatch silently degrades. Mitigated by validating our tensor against any
   reference inputs the repo ships and sanity-checking on obvious strokes.
3. **Hit/hitter detection quality** — per-stroke labeling is only as good as the contact
   detection; may need tuning (implementation sub-step).
4. **Pose skeleton match** — confirm COCO-17 (RTMPose/YOLO) aligns with BST's MMPose joint
   layout; remap if not.

## Global constraints

- Never commit `weights/` or `data/`; never `git add -A/-u/.`.
- Match pipeline behavior with no BST weights present must be unchanged.
- Torch/heavy imports lazy; do not regress weightless/`--help` paths.
- Windows/venv: `.venv/Scripts/python.exe`, `PYTHONUTF8=1`.
