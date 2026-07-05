# Learned Technique-Quality Model (MultiSenseBadminton) — Design

_Date: 2026-07-06_

## Context

Posture-drill scoring is rule-based today: joint angles measured per rep, compared to
ideal ranges from a knowledge base. That yields interpretable per-metric feedback but no
holistic judgment of form. **MultiSenseBadminton** (Nature Scientific Data 2024; data on
figshare DOI `10.6084/m9.figshare.c.6725706.v1`; companion repo
`github.com/dailyminiii/MultiSenseBadminton`) supplies what a learned judgment needs:
**7,763 swings from 25 players (12 novice / 8 intermediate / 5 expert), each swing rated
1–7 by three expert coaches**, with anonymized multi-view video in the public release
(front 640×480@30, side 640×480@30, whole-court 1080p@30), hitting/landing annotations,
plus IMU body tracking (Perception Neuron, 21 joints @96Hz), EMG, and foot pressure.
Strokes covered: **forehand clear** and **backhand drive**.

This project trains a per-rep "AI form score" for the app's `high_clear` drills and
surfaces it alongside the rule-based scores. It is the first of two sequenced ML projects
("B then A"); Project A (BST-style stroke-type recognition) gets its own spec later.

## Locked decisions (user-approved design)

- **Representation:** re-extract 2D pose from the released videos with OUR pose stack
  (YOLO-Pose default) and train on that — NOT the dataset's IMU skeleton. Train and
  inference then share one representation; the 21-joint-IMU ↔ COCO-17 mismatch never
  exists. EMG/foot-pressure/eye streams are excluded (unavailable at app inference).
- **Label:** regress the **mean of the three coach ratings (1–7) per swing**; the 3-class
  skill buckets serve only as an evaluation metric.
- **v1 stroke scope:** `high_clear` only (the dataset's forehand clear). Backhand drive is
  not an app stroke; smash/drop/serve stay rule-based until data exists.
- **Split discipline:** train/val split **by player** — no player appears in both.
- **Integration pattern:** weights auto-discovery (`weights/quality-high_clear.pt`), same
  as the racket detector: file present + `stroke_type == 'high_clear'` → each rep gains an
  AI form score (1–7 mapped to 0–100); file absent → behavior byte-identical to today.
- **UI:** a second, clearly model-labeled score per rep + a drill-summary mean tile;
  localized zh/en; hidden entirely when no AI scores exist.

## Components

### 1. Dataset preparation — `scripts/prepare_quality_dataset.py`

- Downloads/expects the needed **subset** of the figshare collection under
  `data/multisense/` (gitignored): front + side videos and the annotation files; EMG,
  foot-pressure, and eye streams are skipped to cut tens of GB. The exact figshare file
  layout is verified at implementation time; the script prints clear download
  instructions when the subset is absent.
- **License gate:** the figshare item's license is recorded at download time (Scientific
  Data collections are typically CC BY 4.0 — verified, not assumed) and echoed by the
  script; if it is anything restrictive, that is surfaced before training.
- Uses the hitting annotations to clip each swing's frame window; runs our pose extractor
  over each window; **normalizes** keypoint sequences (hip-midpoint centered, torso-length
  scaled, left-handed players mirrored to right); writes per-swing tensors + labels (mean
  coach score) + a manifest carrying player id, stroke type, view, and rating spread.
- **Pose-success gate:** reports the fraction of frames with a detected pose per view
  (anonymization blur may degrade head keypoints; body keypoints expected fine). If the
  success rate is poor (threshold recorded in the script), that is a stop-and-investigate
  signal before any training run.

### 2. Training — `scripts/train_quality_model.py`

- Small temporal network over normalized keypoint sequences (exact architecture chosen in
  the implementation plan; constraint: PyTorch-only, no new heavy dependencies, small
  enough for CPU inference in the posture pipeline).
- Regression (MSE) on the mean coach score, **player-level split**, fixed seed, `--smoke`
  mode (tiny subset, 1 epoch) proving the script end-to-end.
- Reported metrics: val MAE vs the predict-the-global-mean baseline, 3-bucket
  (novice/intermediate/expert) accuracy vs 33% chance, and per-bucket breakdown. Numbers
  are recorded, not promised in advance.
- Exports weights to `weights/quality-high_clear.pt` (gitignored like all weights; commit
  decision deferred until the license is confirmed).

### 3. Inference integration — posture pipeline

- `PostureAnalysisSystem` gains an optional quality scorer: built lazily when the weights
  file exists AND `stroke_type == 'high_clear'`; construction/inference failures are
  logged and degrade to "no AI fields" — an analysis run never dies because of the scorer.
- Per rep: the already-extracted rep keypoint window is normalized identically to
  training and scored; `drill_reps.jsonl` gains `ai_score` (0–100, mapped from 1–7);
  `drill_summary.json` gains `mean_ai_score`; `metadata.json` records the model path, so
  e2e checks and the UI can attribute provenance.
- Absent weights or non-`high_clear` strokes: no new fields anywhere — downstream readers
  (report, UI) treat missing fields as "feature off".

### 4. UI — `static/kestrel.js` / `.css`

- Rep rows/detail show an "AI 评分 / AI form score" chip next to the rule-based score when
  `ai_score` exists; the drill summary gains a matching tile for `mean_ai_score`; both
  labeled as model-based, localized, and absent (not zeroed) when the field is missing.

## Error handling

- No dataset → prep script exits with download instructions. No weights → pipelines
  byte-identical to today. Scorer failure at load or per-rep → log + omit fields.
- Old cached drill results (no `ai_score`) render exactly as today — the UI keys off field
  presence.

## Testing

- **Unit (pytest):** normalization functions (pure, synthetic keypoints incl. mirroring);
  the inference wrapper with an injected fake model (fields present when scorer set,
  absent when not, failure degrades cleanly); writer field presence/absence; UI-side
  verified via `node --check` + curl as established.
- **Training verification:** `--smoke` run + the recorded metrics report (not unit tests).
- **E2E:** IMG_1270 (a high_clear drill) re-run into a scratch output dir with weights
  present — `ai_score` populated per rep, `mean_ai_score` in summary, provenance in
  metadata; weights removed → outputs byte-identical to the pre-feature pipeline.
- Existing suite stays green throughout (baseline recorded at plan time).

## Success criteria

1. Pose-extraction success rate on the dataset clears the recorded gate.
2. Training completes on the 4090; val MAE beats the predict-the-mean baseline and
   3-bucket accuracy beats chance — both numbers recorded in the training report.
3. E2E high_clear re-run shows populated AI scores with provenance; absent-weights run is
   byte-identical; suite green both ways.
4. UI shows the AI chip/tile only when scores exist, in both languages.

## Risks (recorded, mitigated)

- **License:** verified at download; restrictive terms surface before training.
- **Anonymization blur:** measured by the pose-success gate before training.
- **Source resolution/views:** 640×480 front+side; user videos should roughly match those
  viewpoints for best results — noted for the coach report/docs, not enforced.
- **Rating subjectivity:** 3-rater mean as label; rating spread kept in the manifest so
  high-disagreement swings can be down-weighted or excluded in a later iteration.

## Out of scope (deferred)

- Backhand drive in-app; smash/drop/serve quality models (no data).
- EMG/foot-pressure privileged-modality distillation.
- Project A (BST stroke-type recognition) — separate spec, queued next.
- In-app retraining/feedback loops; committing weights before license clarity.
