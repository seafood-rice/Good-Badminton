# Racket Detector Training (RacketDB) — Design

_Date: 2026-07-05_

## Context

Both analysis pipelines compute racket-dependent biomechanics (wrist/racket angle). The
match pipeline (`badminton_analysis/system.py`) is fully wired for a trained racket
detector — `racket_model_path` → `RacketDetector` → per-frame `detect_racket_head()` with
an `infer_racket_head()` (wrist-inference) fallback — but no trained weights exist, so the
slot has sat empty since the racket kinematic work landed. The posture pipeline
(`badminton_analysis/posture/system.py`) never constructs a detector at all: it always
uses wrist inference. Suspicious readings like `wrist_flexion 180 (ideal 80–100)` in the
coach reports are consistent with that missing signal.

**RacketDB** ("racquet db") is a public badminton racket **detection** dataset —
16,608 train / 3,175 test / 2,899 val images, 16,045 labeled racket instances
(vs 3,561 in COCO) — published at VISIGRAPP 2025 with YOLOv5/YOLOv8/DETR/Faster-R-CNN
baselines, hosted at `github.com/muhabdulhaq/racketdb`. It carries **no human-pose
keypoints and no form-quality labels**; it trains a racket detector, not a posture model.

This project: fine-tune a YOLO detector on RacketDB and plug the weights into the
existing slot, wiring the posture pipeline to consume it the same way the match pipeline
already does.

## Locked decisions (user)

- **Goal:** racket detector (option A) — not form-quality learning, not pose fine-tuning.
- **Model:** `yolo11n` fine-tune, locally on the RTX 4090 (ultralytics 8.4.77 already in
  the venv). Escalate to `s` size only if validation mAP disappoints.
- **Integration:** zero new UI. Weights auto-discovered at `weights/racket.pt` — when the
  file exists both pipelines use the detector; when absent, behavior is byte-identical to
  today (wrist inference only).

## Components

### 1. Training script — `scripts/train_racket_detector.py`

- Expects RacketDB under `data/racketdb/` (gitignored; user clones/downloads it — the
  script prints the clone command and fails with a clear message when absent).
- **Format verification at implementation time:** the repo's annotation format is
  confirmed on first read; if it is not already YOLO-format (images + txt labels + yaml),
  the script includes the converter. The dataset yaml (single class `racket`) is generated
  by the script either way, pointing at the train/val/test splits.
- Trains `yolo11n` with fixed hyperparameters (seed, epochs, imgsz recorded in the
  script), evaluates on the val split, prints mAP50/mAP50-95, and copies `best.pt` →
  `weights/racket.pt` (same home as the existing `weights/yolo11s-ball.pt`).
- `--smoke` flag: 1 epoch on a small subset — proves the script end-to-end in minutes
  without a full run.
- **License gate:** the script/README record RacketDB's license as stated in its repo;
  if it turns out research-only/non-commercial, that is flagged to the user before a full
  training run (personal use is expected to be fine; it gets recorded, not assumed).

### 2. Posture pipeline wiring — `badminton_analysis/posture/system.py`

- Constructor gains `racket_model_path=None`. When set and the file exists, builds a
  `RacketDetector` (lazily, like the match pipeline does).
- Frame loop: try `detect_racket_head(frame)` first (no ROI in v1 — posture videos are
  single-player), fall back to `infer_racket_head(keypoints, dominant=...)` when the
  detector returns None. Identical pattern to `system.py:477-481`.
- Tracks how many frames got real detections vs fallback; the count lands in
  `posture/metadata.json` (`racket: {detected_frames, inferred_frames, model}`) and one
  summary line is printed at run end — so the model's contribution is visible.
- CLI entry (`run_posture_analysis.py`) gains a `--racket-model` passthrough arg.

### 3. App auto-discovery — `app.py`

- One helper: `_racket_weights()` returns `str(PROJECT_ROOT / 'weights' / 'racket.pt')`
  when that file exists, else `None`.
- `api_analyze` and `api_posture_analyze` pass it through each pipeline's entry point
  (the match entry already accepts a racket-model argument from the earlier kinematic
  work; the posture entry gains one). No UI change; no behavior change when weights are
  absent.

## Error handling

- No dataset → training script exits with the clone instruction.
- No weights file → both pipelines behave exactly as today (fallback only).
- Corrupt/incompatible weights → `RacketDetector` already guards detection calls; the
  posture wiring must not let a failed model load kill an analysis run (build the detector
  in a try/except, log, and continue with fallback).

## Testing

- **Unit (pytest, baseline 187 stays green):** `RacketDetector` supports model injection —
  posture-side tests inject a fake model and assert: detector output used when present,
  fallback used when detector returns None, metadata counts correct, failed model load
  degrades to fallback. App-side: `_racket_weights()` path logic (tmp weights file) and
  that the flag/param is passed when present, omitted when absent.
- **Training verification (not unit-tested):** `--smoke` run completes; full run reports
  val mAP50, compared against the RacketDB paper's YOLOv8n baseline as the acceptance bar.
- **End-to-end:** posture re-run on `IMG_1270` with weights present — expect
  detector-sourced racket heads on a solid majority of frames, a plausible shift in
  `wrist_flexion` measured values (off the suspicious 180 reading), and unchanged behavior
  with the weights file removed.

## Success criteria

1. Full training run completes on the 4090; val mAP50 in the ballpark of the paper's
   YOLOv8n baseline (exact number recorded in the training report).
2. `weights/racket.pt` present → both pipelines log detector use; absent → byte-identical
   current behavior (suite green both ways).
3. IMG_1270 posture re-run shows majority detector-sourced frames + changed wrist metrics.
4. All existing tests pass (187 baseline + new wiring tests).

## Out of scope (deferred)

- Form-quality / stroke-quality learning (needs labeled form data that doesn't exist here).
- Pose-estimator fine-tuning (needs keypoint annotations RacketDB lacks).
- Committing the dataset to git (gitignored `data/`); whether to commit the ~6MB weights
  file is decided after license verification — default is gitignored with documented
  training instructions.
- UI toggles for the detector; ROI-scoped detection in the posture path.
- ORDB / ShuttleSet / other datasets.

## Outcome addendum (2026-07-07)

- Installed weights filename is `weights/yolo11n-racket.pt` (spec text says
  `weights/racket.pt`); val mAP50 0.711 / mAP50-95 0.410 at imgsz 1280 (paper YOLOv8 ref
  0.78/0.47).
- Posture-path inference runs imgsz 640, conf 0.15 (probe: imgsz 1280 inference produced
  static false positives on racket-lookalike objects; 640 showed none). Detector domains:
  user close-up phone footage 24.7% frame recall, RacketDB far-view in-domain, 640x480 lab
  far-view 0% (kinematic fallback covers).
