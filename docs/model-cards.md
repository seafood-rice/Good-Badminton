# Model Cards

This page documents the machine-learned weights the app auto-discovers under `weights/`:
what each one does, what it was trained on, how well it performs, and what happens when
the file is absent. Every model on this page is optional — the match pipeline
(`badminton_analysis/system.py`) and the posture-drill pipeline
(`badminton_analysis/posture/system.py`) are fully functional on heuristics/kinematics
alone. A trained weights file only adds a signal on top; nothing here is required to run
an analysis.

## `weights/yolo11n-racket.pt` — Racket Detector

**What it does**

A YOLO11n model fine-tuned to detect badminton rackets. Both the match pipeline and the
posture-drill pipeline use it detector-first for racket-head localization (which feeds the
wrist/racket-angle biomechanics); whenever the detector is unavailable or returns nothing
for a frame, both pipelines fall back to a kinematic estimate of the racket head from the
elbow and wrist keypoints (`badminton_analysis/analysis/joint_angles.py`,
`infer_racket_head`).

**Training data**

Fine-tuned on RacketDB, converted from the dataset authors' CVAT project backup by
`scripts/convert_racketdb_cvat.py`: 22,868 frames, 27,472 racket boxes, split by task
(whole source videos stay in one split — RacketDB does not publish an official split
manifest).

> **License:** RacketDB is licensed **CC-BY-4.0**, per the license field of its Hugging
> Face dataset card (the paper and the GitHub repo's own text are otherwise silent on
> licensing). Weights derived from it may be shared **with attribution** to the RacketDB
> authors/dataset. This repository still doesn't commit or ship
> `weights/yolo11n-racket.pt` — that's a size/hygiene choice, not a license restriction —
> so you train your own copy with `scripts/train_racket_detector.py` (see "Regenerating
> the trained weights" below).

**Metrics**

Val mAP50 0.711 / mAP50-95 0.410 at imgsz 1280. For reference, the RacketDB paper's own
YOLOv8 baseline reports 0.78 / 0.47 — this fine-tune trails the published baseline (a
smaller model trained with different hyperparameters) but is a solidly functional
detector.

**Inference configuration**

- **Posture pipeline:** imgsz 640 (the Ultralytics default — `detect_racket_head()` never
  passes an explicit `imgsz`, so inference runs at the framework default rather than the
  training resolution), conf 0.15 (`RACKET_CONF`, `badminton_analysis/posture/system.py:11`),
  plus a person-ROI gate that rejects detections far from the player's keypoint bounding
  box (`ROI_MARGIN = 0.75`, `badminton_analysis/posture/system.py:13`, `person_roi()`).
  Both the lower confidence and the lower inference resolution were chosen empirically: a
  probe showed that running inference at imgsz 1280 produced static false positives on
  racket-lookalike objects (e.g. a wall fan), while imgsz 640 showed none.
- **Match pipeline:** the detector's class default, conf 0.25
  (`badminton_analysis/detection/racket.py:8`), scoped to the match's court ROI.

**Domain reality (measured)**

Detector recall is highly viewpoint-dependent:

| Footage | Detection rate |
|---|---|
| Close-up phone drill footage | ~24.7% of frames |
| RacketDB-like far-view rally footage | in-domain (high recall) |
| 640×480 lab far-view footage | ~0% |

The kinematic wrist-inference fallback covers all of these gaps gracefully — a rep or
stroke gets a racket-head estimate the same way whether the detector fires on most frames,
a quarter of them, or none at all.

**If the file is absent**

Both pipelines behave exactly as they did before this feature existed: pure kinematic
wrist inference, no detector is constructed, no error is raised.

## `weights/quality-high_clear.pt` — AI Form-Quality Scorer (High Clear)

**What it does**

A small TCN (temporal convolutional network) regressor, exported as TorchScript, that
predicts an expert-rated 1–7 skill score for one high-clear repetition from a 64-frame,
hip-centered, torso-scaled 2D pose window (`badminton_analysis/quality/normalize.py`). It
only ever activates for `stroke_type == "high_clear"`. The 1–7 prediction is linearly
rescaled to a 0–100 display score (`badminton_analysis/quality/scorer.py`,
`to_display_score`):

```python
def to_display_score(raw):
    pct = (float(raw) - 1.0) / 6.0 * 100.0
    return round(min(100.0, max(0.0, pct)), 1)
```

i.e. `display = clamp((raw − 1) / 6 × 100, 0, 100)`, rounded to one decimal place. It is
surfaced in the UI as `ai_score` on each rep and `mean_ai_score` on the drill summary.

**Training data**

MultiSenseBadminton (figshare collection DOI `10.6084/m9.figshare.c.6725706.v1`, **CC0 —
these weights are redistributable**). 5,066 Forehand Clear swings from 25 players. The
released videos carry no official video↔annotation sync; `sync_video()` in
`scripts/prepare_quality_dataset.py` recovers one per video by correlating frame-difference
motion energy against the annotated swing windows, jointly grid-searching the video-start
epoch and the effective fps — i.e. per-video clock-skew fitting, since real capture fps can
drift slightly from the nominal metadata value.

**Label semantics — please read**

Expert ratings in MultiSenseBadminton exist per **player** (the mean of 3 expert coaches'
ratings), not per swing — every Forehand Clear swing by a given player carries that same
player-level rating. This is weak supervision: the per-rep `ai_score` is consequently
noisy, since many different reps share one label. **The session-level `mean_ai_score` is
the number to trust; treat individual `ai_score` values as indicative, not
authoritative.**

**Metrics** (player-disjoint validation split, 3 seeds, 1–7 scale)

| Seed | Per-swing MAE | Per-swing baseline (predict mean) | Per-player (session) MAE | Per-player baseline |
|---|---|---|---|---|
| 0 (installed) | 2.135 | 2.308 | 1.994 | 2.272 |
| 1 | 1.908 | 2.320 | 1.732 | 2.123 |
| 2 | 1.623 | 2.061 | 1.500 | 2.062 |

The installed weights are the canonical seed-0 training run.

**Inference window**

±2.0 s around the detected contact frame (`QUALITY_WINDOW_PRE_S` / `QUALITY_WINDOW_POST_S`,
`badminton_analysis/posture/system.py:19-20`), matching the ~4.1 s mean window the model
was trained on much more closely than the rep segmenter's own ~1.2 s peak window. A
measured probe against that narrower ~1.2 s window showed it forfeited most of the model's
accuracy advantage, which is why the scorer gets its own wider window while the
heuristic per-metric scoring keeps the narrow one.

**If the file is absent**

No `ai_score` or `mean_ai_score` field appears anywhere in the output; heuristic
(rule-based) scoring is unaffected. Deleting the weights file is the complete rollback.

## Stock and pretrained weights

The remaining files under `weights/` are not trained by this project — they're either
pretrained upstream checkpoints or assets carried over from the base project.

| File | Role | Provenance |
|---|---|---|
| `weights/yolo11n-pose.pt` | Default human pose model for the `yolo-pose` family (COCO 17 keypoints) | Ultralytics pretrained checkpoint, auto-downloaded by the `ultralytics` package on first use |
| `weights/yolo11s-ball.pt` | Shuttlecock detector used by both pipelines | Downloaded from the upstream [yo-WASSUP/Good-Badminton Releases](https://github.com/yo-WASSUP/Good-Badminton/releases/latest); Apache License 2.0 |
| `weights/yolox_nano_8xb8-300e_humanart-40f6f0d0.onnx` | First-stage person detector for the two-stage `rtmpose` / `rtmo` pose families | OpenMMLab / RTMPose ecosystem (HumanArt-trained YOLOX-nano), distributed via the project's Releases; Apache License 2.0 |

None of these three are covered by the training workflow below — replace them by
re-downloading, not retraining.

## Regenerating the trained weights

**Racket detector:**

```bash
python scripts/convert_racketdb_cvat.py   # RacketDB CVAT backup -> YOLO dataset layout
python scripts/train_racket_detector.py   # trains + installs weights/yolo11n-racket.pt
```

**AI quality scorer:**

```bash
# MultiSenseBadminton is not bundled with this repo — fetch it yourself from the
# figshare collection (doi 10.6084/m9.figshare.c.6725706.v1) first.
python scripts/prepare_quality_dataset.py  # builds normalized training tensors + manifest
python scripts/train_quality_model.py      # trains + installs weights/quality-high_clear.pt
```
