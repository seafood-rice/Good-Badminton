# Learned hit detection — design

- **Status:** design approved section by section in chat on 2026-10-04; this written spec awaits
  owner review.
- **Workstream:** `learned-hit-detection`, branch `claude/learned-hit-detection`.
- **Parent:** Sub-project B (`2026-07-29-match-stroke-recognition-b-design.md`). This spec
  replaces B's contact-rule track (B2–B4, B7) with a learned hit detector, at the owner's
  direction.
- **Followed by:** per-player 7-technique scoring (a separate spec). It depends on this work and
  is out of scope here.

## 1. Why this exists

After B11 shipped, the owner re-ran the full Axelsen match (`outputs/YTDown.com_..._1080p/`,
64,085 frames at 60 fps, 1068 s). Player positions looked good, but stroke output was far
short:

- `strokes.json` (BST labels) held 31 strokes.
- `technique_summary.json` scored 3 strokes, all of them serves.

The 88 detected rallies cover 823 s of play, so at roughly one stroke per second the match
holds about 700 strokes.

### 1.1 Measured root cause (2026-10-03)

Both stroke outputs depend on a racket-shuttle *contact rule* (`stroke/events.py`). A contact
needs three things:

1. A shuttle detection at frames i-1, i and i+3.
2. A racket point within a radius of the shuttle.
3. A shuttle direction change of at least 45°.

The funnel on the full match:

| Stage | Count |
|---|---|
| Real strokes in rally time (estimate) | ~700 |
| YOLO shuttle present in rally frames | 38.1% |
| Shuttle-only turn events (≥45°, 15-frame dedup) | 254 |
| BST contacts (both players, fixed 80 px radius) | 31 |
| Technique contacts (single player, depth-scaled radius) | 3 |

**Baseline on four ~20 s stretches** (`outputs/axelsen-hit-labels/runs/`). Each stretch was run
through today's pipeline twice: with the YOLO shuttle, and with TrackNetV3. TrackNetV3 runs
automatically here because each stretch is under the 2000-frame budget.

| Stretch | TrackNetV3 shuttle presence | BST contacts, YOLO → TrackNetV3 |
|---|---|---|
| 1 (280.5–301 s) | 944/1230 (77%) | 0 → 4 |
| 2 (527.5–546.5 s) | 1037/1140 (91%) | 0 → 4 |
| 3 (804–821 s) | 966/1020 (95%) | 1 → 3 |
| 4 (1025–1043.5 s) | 760/1110 (68%) | 0 → 0 |

Each stretch should contain about 15–20 hits.

Dense tracking fixes shuttle *presence*, but the contact rule still discards most hits.
- On the dense tracks, shuttle-only turn events number 23, 17, 17 and 8–10 per stretch, which is
  roughly the right count.
- At those events the shuttle is mostly 100–300 px from the nearest wrist.
- The BST path gates on a fixed 80 px (`stroke_recog/hits.py:27` calls
  `detect_contacts_multi(track)` with its defaults).
- The technique path scales its radius by the court-plane depth at the *shuttle's* image row.
  An airborne shuttle sits high in the image, so it gets a far-court radius.

The low courtside camera makes the near player large in frame, and 60 fps halves the per-frame
motion the 30 fps constants assumed (B spec finding 8).

**Further defects in technique scoring** (out of scope here, recorded for the scoring spec):
- It analyses one player only.
- It classifies with four rule-based types (`stroke/classifier.py`).
- Its reference ranges cover only clear, smash, drop and serve.
- Two of the three scored Axelsen serves measured a trunk rotation of 173° and 177° (ideal
  10–25°). That points to a 2D angle defect, not technique.

### 1.2 TrackNetV3 cost measured (2026-10-03)

Measured on stretch 1 (1230 frames, 1080p, RTX 4090), including median-image generation:

| `eval_mode` | Time | Per frame | Shuttle present | Turn events |
|---|---|---|---|---|
| `weight` (today) | 592.7 s | 0.482 s | 944 | 23 |
| `nonoverlap` | 222.4 s | 0.181 s | 968 | 18 |

For the full Axelsen match that is about 8.6 h (`weight`) or about 3.2 h (`nonoverlap`), on top
of today's 4.5 h run.

## 2. Owner decisions (2026-10-03 / 2026-10-04)

1. **Detection first, scoring second.** Fix stroke detection and measure it, then build
   per-player 7-technique scoring as a separate project.
2. **Run time: whatever it takes.** Dense-track every frame; no budget cap on coverage.
3. **Technique reference ranges will be calibrated from pro footage.** This applies to the
   scoring project.
4. **Ground truth: the owner labels 3–4 Axelsen stretches** (the hit time, near or far player,
   and optionally the stroke type), in `outputs/axelsen-hit-labels/LABELS.md`.
5. **Approach C: a learned hit detector.** The alternatives were A (shuttle-path turns with
   court-side attribution) and B (re-scaling the racket radius).
6. **Staged:** use a pretrained model if one is usable, otherwise train our own. Neither
   pretrained candidate was usable (§3), so we train our own.
7. **Training data: ShuttleSet, plus the owner's own labels if transfer is weak.**
8. **Done bar** (§10): recall ≥ 0.60, precision ≥ 0.70, hitter correct ≥ 0.85, within ±0.2 s,
   on the owner's labelled stretches.

## 3. Pretrained options investigated and rejected (2026-10-04)

- **Pose-transformer hit detector**
  ([arthur900530/Automated-Hit-frame-Detection-for-Badminton-Match-Analysis](https://github.com/arthur900530/Automated-Hit-frame-Detection-for-Badminton-Match-Analysis),
  MIT). The author stated on 2026-06-26 (issue #3) that the trained checkpoints are lost. Only
  its training keypoint sequences (73 MB) remain.
- **SwingNet** ([wish44165/A-New-Perspective-for-Shuttlecock-Hitting-Event-Detection](https://github.com/wish44165/A-New-Perspective-for-Shuttlecock-Hitting-Event-Detection),
  repo unlicensed; weights in [Zenodo 14677727](https://zenodo.org/records/14677727), CC BY 4.0).
  Two problems, either enough to reject it:
  - Its essential preprocessing (`rt_conversion()` in `src/preprocess/rt_conversion_datasets.py`)
    is an empty stub, and the Zenodo listing holds already-processed videos but not the code.
  - The archive is a single 14.5 GB file. Zenodo served it at 0.3–1.2 MB/s with no range
    support, which works out to 4–12 h.

## 4. Goal and scope

**Goal:** on a full match, detect almost every hit by both players. Each hit carries its time,
hitter (near or far) and confidence. One shared hit list feeds both BST stroke labelling and
technique analysis, and the result is measured against human labels.

**In scope:**
- ShuttleSet acquisition support, including an alignment check.
- Feature extraction and a training dataset.
- The hit-detector model and its training script.
- Whole-match dense tracking with a progress heartbeat.
- Runtime integration through a shared `hits.json`, with fallbacks.
- An evaluation harness.
- Validation against the owner's labels.

**Out of scope** (deferred):
- Predicting stroke type. Labels are *kept* in the dataset for the scoring project.
- Scoring both players, the 7-technique scores, the reference ranges, and the trunk-rotation
  defect.
- Rendering hits into the annotated video.
- The ShuttleSet numeric benchmark of the TrackNetV3 spec ("project 2").
- Doubles.

## 5. Architecture

Two halves.

**Training pipeline** (offline scripts; never runs during a normal analysis):

```
ShuttleSet labels (CSV, from the CoachAI-Projects repo)
  + match videos the owner fetches from BWF's YouTube channel
      -> scripts/check_shuttleset_alignment.py   labels line up with this upload?
      -> scripts/build_hit_dataset.py            TrackNetV3 + RTMPose over labelled rally
                                                 spans -> per-frame features + labels
                                                 (data/hit_dataset/<match>.npz)
      -> scripts/train_hit_detector.py           -> weights/hit-detector.pt
```

**Match pipeline:**

```
TrackNetV3 dense pass over the WHOLE video (cached)  -- progress stage "tracking_shuttle"
frame loop (unchanged; render path stays on YOLO)
post-loop:
  rally segmentation (B11, unchanged)
  hit_detector.detect(...) -> hits.json  ----> BST stroke labels (strokes.json)
       | fallback: today's contact rule  ----> technique analysis (tracked player's hits)
```

## 6. Data

### 6.1 Matches

These come from the ShuttleSet `match.csv`, which lists each match's YouTube URL.

| Role | id | Match | Minutes |
|---|---|---|---|
| train | 28 | Axelsen v Ng Ka Long, Yonex Thailand Open 2021 Final | 44 |
| train | 44 | Antonsen v Axelsen, BWF World Tour Finals 2020 Final | 60 |
| train | 35 | Marin v An Se Young, Toyota Thailand Open 2021 SF | 51 |
| train | 21 | An Se Young v Intanon, Yonex Thailand Open 2021 QF | 44 |
| **validation (held out)** | 13 | Momota v Axelsen, Malaysia Masters 2020 Final | 54 |
| reserve | 4 | Chen Long v Chou Tien Chen, Denmark Open 2019 QF | 54 |
| reserve | 42 | Sindhu v Chochuwong, BWF World Tour Finals 2020 QF | 42 |

A reserve match replaces any match that fails the alignment check. The split is always by
whole match.

**Acquisition is owner-run.** The owner fetches the videos (whether and how is the owner's call
under YouTube's and BWF's terms) and places them at `data/shuttleset/videos/<video>.mp4`, named
by the `video` column. The labels come from `wywyWang/CoachAI-Projects` (MIT) and are copied to
`data/shuttleset/labels/`. Everything under `data/` is gitignored.

### 6.2 Alignment check

ShuttleSet `frame_num` assumes the annotators' copy of the video at 30 fps. A re-upload can
differ in start offset, frame rate or resolution. `check_shuttleset_alignment.py` handles this
as follows:

1. Rescale label times by the fetched video's fps. Pixel coordinates (`player_location_*`,
   `homography.csv`) are rescaled by its resolution.
2. Run TrackNetV3 on a sample of labelled rallies, then cross-correlate the labelled hit times
   with shuttle turn events over offsets of ±10 s.
3. **Accept** the match only if both hold:
   - the best offset's matched-hit count is at least 1.5× the best count at any offset more
     than 0.5 s away from it;
   - at that offset, ≥ 60% of labelled hits have a turn event within ±3 frames at 30 Hz. The
   offset found is recorded and applied in feature extraction.
4. **Reject** the match otherwise, and use a reserve.

### 6.3 Labels derived per hit

- **Time:** `frame_num`, corrected by the alignment offset.
- **Hitter, near or far:** from `player_location_y` against the net line's image row, taken
  from the `homography.csv` court corners. ShuttleSet's player A/B is identity, not side.
  `downcourt` is only a cross-check.
- **Stroke type:** the raw 18-class `type`, plus a 7-class mapping. The raw value is stored, so
  the mapping can change later without re-extracting.

| 7-class | ShuttleSet types |
|---|---|
| serve | short service, long service |
| clear | clear |
| lift | lob, defensive return lob |
| smash | smash, wrist smash |
| drop | drop, passive drop |
| net | net shot, return net, cross-court net shot, rush |
| drive | drive, driven flight, back-court drive, push, defensive return drive |

`push` is borderline between net and drive; the scoring spec decides.

### 6.4 Feature extraction

`build_hit_dataset.py` processes **labelled rally spans only**: from 1 s before the first hit
to 1.5 s after the last. It uses the match pipeline's *own* components, so training and
inference features cannot drift apart:
- TrackNetV3, in the mode chosen in §11;
- RTMPose for both players, assigned to near or far with the existing one-to-one assignment;
- court corners from `homography.csv`.

Output per match: per-frame raw signals (shuttle xy and visibility, both players' 17 keypoints
with confidences, the court corners and fps), plus the label arrays. Model features (§7) are
computed from these raw signals at training time, so changing a feature never needs another
GPU pass.

Estimated compute: 5 matches × ~40% rally time ≈ 180k frames at 30 fps, which comes to roughly
15–30 h of background GPU time. The step is resumable per match.

## 7. Model

### 7.1 Time base

All features are resampled to **30 Hz** before the model. Native-fps hit times are recovered
by mapping back through the resampling index. Every window, gap and tolerance is in seconds,
replacing today's per-frame constants.

### 7.2 Per-frame features (designed to transfer across camera angles)

| Group | Features | Normalisation |
|---|---|---|
| Shuttle | position, velocity, acceleration, turn angle, visibility flag | Court units: offset from the net's image midpoint, divided by the court's image length (baseline to baseline) |
| Each player (near / far) | 17 keypoints relative to the hip midpoint; wrist speed; feet court position | Keypoints and speed in **torso lengths** (shoulder-hip distance); feet in metres through the court homography (feet are on the ground, so it is exact) |
| Shuttle ↔ player | Shuttle offset from each player's hip; distance to each wrist | That player's torso lengths |

Missing inputs are zero-filled, with a per-group mask channel.

### 7.3 Network and outputs

- A 1-D temporal convolutional network (dilated residual blocks), with a receptive field of
  about ±1 s and roughly 100–300k parameters, written in PyTorch (already a dependency).
- **Hit score** per frame: trained against Gaussian targets (σ ≈ 1 frame at 30 Hz) at labelled
  hits, with focal or weighted BCE loss.
- **Hitter**, near or far: per frame, with the loss applied only around labelled hits.
- **Peak-picking:** local maxima above a threshold, with a minimum spacing in seconds. Both are
  tuned on the validation match.
- Stroke type is not predicted (§4).

### 7.4 Augmentation (the camera-gap defence)

Applied to raw signals before features are computed:
- left-right mirroring;
- random shift, scale and mild perspective warp of image coordinates;
- shuttle dropout down to 65% presence;
- keypoint dropout;
- ±10% frame-rate jitter.

### 7.5 Weights file

`weights/hit-detector.pt` is a **state dict** loaded with `torch.load(..., weights_only=True)`,
with a JSON sidecar holding:
- the feature schema version,
- the time base,
- the threshold,
- the peak spacing,
- the training matches,
- the validation metrics.

## 8. Runtime integration

1. **Whole-video dense tracking.**
   - `SHUTTLE_PRETRACK_MAX_FRAMES` stops being a skip when TrackNetV3 weights are present.
   - The existing cache (`shuttle_trajectory.json`, keyed on video and params) makes re-runs free.
   - The `eval_mode` is chosen per §11.
   - A small **progress callback** is added to the vendored TrackNet inference loop and recorded
     in its `CONTRACT.md` as a deviation from upstream.
   - The callback drives a new stage, `tracking_shuttle`, written to `progress.json` with a
     heartbeat at least every 5 s.
   - `static/kestrel.js` gains the stage and its label, keeping Sub-project A's "never looks
     hung" contract.
2. **`badminton_analysis/stroke/hit_detector.py`** (new):
   - pure feature functions (§7.2);
   - resampling;
   - model loading;
   - inference;
   - peak-picking.

   It returns `[{frame, hitter: "lower"|"upper", confidence}]` in native frames. `lower` is the
   near player, matching existing naming.
3. **`hits.json`**, written once after the frame loop:

   ```json
   {
     "source": "learned" | "contact_rule",
     "reason": "why contact_rule was used, else null",
     "model": {"sha256": "...", "schema": 1, "threshold": 0.0, "min_spacing_sec": 0.0},
     "shuttle_source": "tracknet" | "yolo",
     "shuttle_coverage_in_play": 0.0,
     "fps": 60.0,
     "hits": [{"frame": 0, "time_sec": 0.0, "hitter": "lower", "confidence": 0.0,
               "rally_id": 1}]
   }
   ```

   `rally_id` is null when a hit falls outside every B11 segment.
4. **Consumers:**
   - `StrokeRecognizer.label_rally` gains an optional `hits` argument, which replaces
     `hit_events(track)`.
   - `TechniqueAnalysisRunner.run` gains an optional `hits` argument, which replaces
     `detect_contacts`. It receives only the **tracked player's** hits (today's
     single-player meaning, per B spec open question F), because it reads single-player pose
     fields.
   - Both behave exactly as today when `hits` is None.
5. **Wiring:**
   - `main.py --hit-model PATH`;
   - `app.py` passes `weights/hit-detector.pt` when present, following the BST/TrackNet pattern.
6. **Rally segmentation side effect.** With a dense whole-match track, B11's `auto` will choose
   the **shuttle** signal on Axelsen. That path is unvalidated. Hits do not depend on segments
   beyond `rally_id`. Validation (§10) checks the new boundaries, and if they are wrong,
   `rally_signal="swing"` is pinned for this footage class in a follow-up.

## 9. Fallbacks and error handling (never fatal)

| Condition | Behaviour | `hits.json` |
|---|---|---|
| No `--hit-model` / weights missing | Today's contact rule, unchanged | `source: contact_rule`, reason |
| No TrackNetV3 trajectory | Contact rule (the model was trained on dense tracks) | reason |
| Dense track covers < 50% of in-play frames (e.g. DJI fixed-camera footage, B11 §0.14) | Contact rule | reason + measured coverage |
| Weights schema mismatch | Contact rule | reason |
| Any exception in feature or inference code | Contact rule; error logged | reason |
| TrackNetV3 pass fails | YOLO shuttle as today, then the rule above | reason |

With no hit-model weights present, every output stays **identical to today**, apart from the
new `hits.json` that records `contact_rule`.

## 10. Testing and validation

### 10.1 Hermetic tests (committed suite; no weights, no video)

- **Features:**
  - invariance under image translation and scale;
  - torso normalisation;
  - mask channels;
  - 60 → 30 Hz resampling and the round trip back to native frames.
- **Peak-picking:** synthetic curves, threshold, and spacing in seconds at 30 and 60 fps.
- **Detector:** with a stub model, check the output schema and the hitter mapping.
- **Integration:**
  - `hits.json` and its provenance;
  - each fallback row in §9;
  - BST and technique consuming the injected hits;
  - technique filtering to the tracked player;
  - outputs identical to today with no weights.
- **Dense pass:** no budget skip, and progress plus heartbeat reach `progress.json` (with a
  stubbed `run_prediction`).
- **ShuttleSet tooling:**
  - CSV parsing from a fixture;
  - the 18 → 7 mapping;
  - near/far derivation;
  - alignment offset search on synthetic event trains.
- **Evaluation harness:** `LABELS.md` parsing, one-to-one matching within tolerance, and the
  metrics.

### 10.2 Validation (run by me; results go in this spec's outcome addendum)

1. **Held-out ShuttleSet match:** expect recall ≥ 0.85 in-domain. Lower than that means a
   feature or model bug, which is fixed before going further.
2. **The owner's four Axelsen stretches** (test set only; never trained or tuned on):
   - **Done bar:** recall ≥ 0.60, precision ≥ 0.70, hitter correct ≥ 0.85, all within ±0.2 s.
   - Both TrackNetV3 modes are scored.
   - The shuttle-turn baseline (approach A) is scored as a reference.
3. **Full Axelsen run, end to end:**
   - a plausible hit count (several hundred across about 820 s of rally time), credited to both
     players;
   - full provenance;
   - no errors;
   - stage timings recorded;
   - the new rally boundaries checked.
4. **DJI 0010:** expect the sparse-track fallback, reported as such.

### 10.3 Stop rules (agreed up front)

- **Weak in-domain** → fix features or model; the owner's labels are not touched.
- **Strong in-domain, below the bar on the owner's stretches:**
  1. The owner labels about **300 further hits** on their own footage. These are kept separate
     from the four test stretches.
  2. Fine-tune, then re-test.
- **Still below the bar:**
  - Stop and report the numbers.
  - The code may merge with the learned detector **off by default**: the contact rule stays the
    default, and the detector is used only with `--hit-model`.
  - It is never default-on below the bar.

## 11. Decisions deferred to measurement

- **TrackNetV3 `eval_mode`:** `nonoverlap` (about 2.7× faster) if it meets the bar on the
  owner's stretches; otherwise `weight`. The two are compared at the same detector.
- **Threshold and peak spacing:** tuned on the validation match only.

## 12. Owner actions

1. Fill in `outputs/axelsen-hit-labels/LABELS.md`. This is needed before any go/no-go.
2. Fetch the five §6.1 videos, plus a reserve if the alignment check rejects one.
3. Only if §10.3 triggers it: label about 300 hits for fine-tuning.

## 13. Risks

- **Camera-angle transfer.** All public training data is elevated TV footage. Mitigations:
  camera-independent features (§7.2), augmentation (§7.4), and the fine-tune step (§10.3).
- **TrackNetV3 accuracy on the low courtside angle.** Presence is 68–95% on the four stretches.
  Stretch 4 (68%) shows the weak end, and augmentation trains for it.
- **Video availability.** Matches can be removed from YouTube; reserves are listed.
- **Small test set** (about 60–80 hits). The metrics carry wide error bars, and the outcome
  addendum reports counts, not just ratios.
- **Run time.** It adds 3.2–8.6 h to the first run of a full match. That is accepted by owner
  decision 2; the cache makes re-runs cheap.
