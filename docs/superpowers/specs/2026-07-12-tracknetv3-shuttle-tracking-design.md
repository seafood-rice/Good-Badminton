# TrackNetV3 Dense Shuttle Tracking — Design Spec

**Date:** 2026-07-12
**Status:** Design approved; awaiting spec review before writing the implementation plan.

## Goal

Give the match pipeline a **dense per-frame shuttle trajectory** from a vendored,
inference-only **TrackNetV3**, fed into the existing contact detector and BST stroke
recognizer, so match-stroke recognition actually fires on real broadcast footage. No model
training; no dataset dependency (that lands in the follow-on benchmark project).

## Background — why this project exists

BST stroke recognition shipped code-complete and verified in isolation, but is **dormant on
real footage**. Root cause (T9 validation, Axelsen vs Kodai clip): the current shuttle
detector (`yolo11s-ball`) finds the shuttle in only ~35% of frames on broadcast video. The
contact detector (`badminton_analysis/stroke/events.py::detect_contacts`) needs three
consecutive non-missing shuttle points to measure a direction change, so on a 65%-gappy
track it finds **0 contacts at every threshold** → BST has nothing to classify. TrackNetV3
is a dense, heatmap-based tracker purpose-built for the small/fast/blurry badminton
shuttle; it is precisely the tracker the ShuttleSet/BST authors used. Making the track dense
is the fundamental unblock.

## This is project 1 of 2

Dependency forces the order:

1. **TrackNetV3 integration (this spec):** vendor it, port to the project's torch, offline
   dense-trajectory pre-pass, feed contacts + BST, wire flags/UI, validate **functionally**
   on the Axelsen clip.
2. **ShuttleSet end-to-end benchmark (separate spec, later):** acquire data, parse labels,
   clip rallies, run the pipeline, report tracking accuracy (TrackNet radius metric),
   contact-detection precision/recall, and coarse stroke-type accuracy + confusion matrix.

The user's ultimate "measured accuracy" bar is met at the end of project 2. Project 1's done
bar is functional: the pipeline fires end-to-end on real footage.

## Integration approach

**Offline pre-pass, analysis-only.** TrackNetV3 runs as a batch pass over the video before
streaming analysis, producing a dense trajectory that only the **analysis track** (contacts
+ BST) consumes. The live rendered overlay keeps using `yolo11s-ball` unchanged. This is the
smallest blast radius, is purely additive (zero regression when weights absent), and fully
serves the project-2 benchmark (the overlay is irrelevant to measurement).

Rejected alternatives: full replacement (touches the render path, removes a working
component — deferred as cosmetic unification); streaming TrackNetV3 (its InpaintNet
rectification uses non-causal 16-frame windows and cannot run causally, so a sliding-window
streaming variant loses the gap-filling that is the whole point).

## Architecture & data flow

```
video → [TrackNetV3 pre-pass] → dense trajectory (cached) → analysis-track shuttle field
      → detect_contacts → hit_events → BST → strokes.json
```

1. **Pre-pass** — `track_video(video_path)` runs vendored TrackNetV3 (TrackNet tracking +
   InpaintNet rectification) over the whole video, producing
   `{frame_idx: (x, y) | None}` in **original-frame pixel coordinates**. TrackNet emits
   heatmaps in a resized model space (288×512, H×W); the wrapper scales outputs back to
   original resolution. `Visibility == 0` frames map to `None`.
2. **Cache** — the trajectory is written to `<output_dir>/shuttle_trajectory.json`, keyed by
   video path + mtime + a hash of model params, so re-runs and project-2's many rallies skip
   re-inference.
3. **Consumption** — the streaming loop is unchanged except that, when `analyze_technique`
   is on and a trajectory is present, `_capture_analysis_frame` sources its `shuttle` field
   from the precomputed trajectory (ROI-gated with the existing court-ROI test) instead of
   the per-frame yolo detection. The rendered overlay path is untouched.
4. **Downstream** — `detect_contacts` and BST wiring are unchanged; they now receive a dense
   track, so contacts fire.

## Components & interfaces

- **`third_party/tracknet/`** — vendored TrackNetV3 model code (TrackNet + InpaintNet nets +
  the minimal inference utilities they require), plus `LICENSE` (MIT, copied verbatim) and
  `CONTRACT.md` pinning the architecture params relied upon: input seq_len 8 (tracking) /
  16 (inpainting), resize 288×512 (H×W), output CSV semantics (`Frame,Visibility,X,Y`;
  Visibility 0=invisible/1=visible), weight filenames, and the upstream commit SHA vendored
  from. Mirrors the existing `third_party/bst/` pattern.
- **`badminton_analysis/shuttle_track/tracknet.py`** — inference wrapper.
  - `load_tracknet(tracknet_path, inpaintnet_path=None, device=None)` → opaque models handle.
    Lazy `torch` import.
  - `track_video(video_path, models, court_roi=None) -> dict[int, tuple[float, float] | None]`
    — dense trajectory in original pixels; applies model→pixel coordinate scaling and, when
    `court_roi` is given, the ROI gate (off-court / out-of-frame → `None`).
- **`badminton_analysis/shuttle_track/trajectory.py`** — cache I/O: `load_cache(path, key)` /
  `save_cache(path, key, trajectory)`; cache key = video path + mtime + model-params hash.
  (Small; kept separate for a clear single responsibility.)
- **`badminton_analysis/system.py`** — `_run_shuttle_pretrack()` invoked when TrackNet
  weights are provided and `analyze_technique` is on; stores `self._shuttle_trajectory`
  (`dict[int, (x,y)|None]`). `_capture_analysis_frame` prefers this trajectory over the yolo
  shuttle when present. The whole pre-pass is wrapped never-fatal (see Error handling).
- **`main.py` / `app.py`** — `--tracknet-model` and `--inpaintnet-model` CLI flags;
  `_tracknet_weights()` auto-discovery (mirrors `_bst_weights()`); `/api/models` reports
  `"tracknet": bool`; provenance UI gains a "Dense shuttle tracking: TrackNetV3" line when
  used.

## Error handling & fallback

- **Missing weights** → pre-pass skipped; pipeline runs exactly as today on the yolo shuttle.
  Purely additive, zero regression.
- **Pre-pass exception** → caught, logged, fall back to the yolo shuttle; analysis still
  runs. **Never fatal.** (BST lesson: a mid-pipeline crash dies before `_cleanup` and
  corrupts the output video.)
- **Torch port** → TrackNetV3 targets torch 1.10; the project runs torch 2.5. Verify load +
  inference. If InpaintNet specifically will not port, fall back to TrackNet-only
  (rectification is separable) rather than block the project; log that rectification is off.
- **Coordinate / ROI** → points outside the frame or (when a ROI is supplied) off-court map
  to `None`, treated as missing — same contract the analysis track already expects.

## Testing strategy

- **Committed unit tests** stub **only** the neural inference, but exercise the **real**
  coordinate scaling, ROI gate, cache round-trip, and the integration seam: a realistic
  fake dense trajectory flows through `_capture_analysis_frame` → `detect_contacts` and
  yields contacts. This deliberately avoids the BST trap, where every committed test stubbed
  the entire seam and so missed a real coordinate bug that only the whole-branch review
  caught.
- **Weights-present GPU validation** is a controller-run harness on the Axelsen clip (not in
  the committed suite, mirroring the BST T9 harness): assert contacts > 0 and the BST
  distribution is non-empty. This is the functional done-check.

## Weights & data handling

- `TrackNet_best.pt` and `InpaintNet_best.pt` are downloaded from the upstream repo's Google
  Drive into `weights/`, **NOT committed** (consistent with the RacketDB / quality-model /
  BST convention). Fetch steps documented in `third_party/tracknet/CONTRACT.md` and the
  project README.
- No dataset is needed for this project; data acquisition is entirely project 2.

## Global constraints

- **Never commit** model weights or datasets. Weights live in git-ignored `weights/`.
- **Lazy torch import** in the wrapper so `--help` and weightless runs stay torch-free where
  they already are.
- **Never-fatal integration**: the pre-pass must not be able to crash the pipeline; on any
  failure it falls back to the existing yolo shuttle.
- **Zero regression when weights absent**: with no TrackNet weights, behavior is
  byte-identical to today.
- **MIT attribution**: vendored code keeps its `LICENSE`; `CONTRACT.md` records the upstream
  source and commit.
- **Windows env**: `.venv/Scripts/python.exe` with `PYTHONUTF8=1`; kill background processes
  by PID.

## Success criteria (done-means)

1. Vendored TrackNetV3 loads and infers under the project's torch on the 4090.
2. `track_video` returns a dense trajectory (shuttle present in the large majority of
   court-view frames) in original pixel coordinates, ROI-gated.
3. With TrackNet weights present, the pipeline detects a plausible number of contacts across
   a rally and BST emits a non-empty coarse stroke distribution on the Axelsen clip
   (validated by the controller-run harness).
4. With weights absent, behavior is unchanged from today (regression check).
5. Committed suite green, including the real-seam integration test.

## Out of scope (deferred)

- Render-path unification (the live overlay stays on yolo11s-ball).
- Quantitative contact / stroke-type threshold tuning (project 2 measures first; a tuning
  follow-up is triggered only if the numbers are poor).
- The ShuttleSet end-to-end benchmark and all data acquisition (project 2).
- Doubles; any change to BST itself.
