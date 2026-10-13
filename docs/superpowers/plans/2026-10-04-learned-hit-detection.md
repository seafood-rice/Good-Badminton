# Learned Hit Detection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the racket-radius contact rule with a learned hit detector. It should find
almost every hit by both players on a full match, credit each hit to a player, and feed one
shared hit list to BST stroke labelling and technique analysis.

**Architecture:**
- **Raw signals dump.** The match pipeline writes per-frame raw signals (`hit_signals.npz`):
  the dense TrackNetV3 shuttle track, both players' RTMPose keypoints, and the court corners.
- **The detector.** A small temporal CNN reads camera-independent features computed from those
  signals at 30 Hz, picks hit peaks, and writes `hits.json`.
- **Training data.** The same pipeline, run over labelled ShuttleSet rallies, produces the
  training data. Training and match-time features therefore come from identical code.
- **Fallback.** Every failure falls back to today's contact rule, and the reason is recorded.

**Tech stack:** Python 3.12, NumPy 1.26, PyTorch 2.5.1 (CUDA), OpenCV, the vendored TrackNetV3
(`third_party/tracknet`), RTMPose through the existing pipeline, pytest. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-10-04-learned-hit-detection-design.md`. Executors read
it alongside this plan.

## Global Constraints

- No new dependencies; use only what `.venv` already has (torch 2.5.1, numpy 1.26.4, cv2).
- **Never commit:**
  - `data/`, `outputs/`, `weights/`, `videos/` (all gitignored);
  - `BirdEye Prototype.html`;
  - `.superpowers/`.
- **Load the hit model safely:** `torch.load(path, map_location=..., weights_only=True)`, as a
  state dict with a JSON sidecar.
- **Never fatal:** a failure in hit detection, signals or TrackNetV3 must not stop the run. It
  falls back to the contact rule and records the reason in `hits.json`.
- **No hit-model weights → outputs identical to today**, apart from the new `hits.json` and
  `hit_signals.npz`. `strokes.json` gains a `hit_source` key only on the learned path.
- **Time base:** `TARGET_HZ = 30.0`. Every window, spacing and tolerance is in seconds.
- **Side naming:** `lower` = the near player (bottom of the image), `upper` = the far player.
  Side index 0 = `lower`, 1 = `upper`.
- **Court corner order** (the existing `court_annotations.txt` / `CourtMapper` convention):
  far-left, far-right, near-right, near-left. Court is 6.1 m × 13.4 m; the net is at court
  y = 6.7.
- **Done bar** (spec §10.2): recall ≥ 0.60, precision ≥ 0.70, hitter correct ≥ 0.85, within
  ±0.2 s, on the owner's labelled stretches. Those stretches are a test set only and are never
  trained or tuned on.
- **Test command (Git Bash):**

  ```bash
  PYTHONUTF8=1 ./.venv/Scripts/python.exe -B -m pytest -q -p no:cacheprovider <paths>
  ```

  The full suite also needs `--basetemp "$TEMP/claude/<unique>"`.
- **Commit trailer:** `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- **Agents never commit to, merge into, or push `main`.** Work stays on
  `claude/learned-hit-detection`.
- **Continuity helper calls use** `pwsh -NoProfile -Command "& ./scripts/ai-handoff.ps1 ..."`
  with `@('a','b')` arrays.

## Review Focus

The five uncovered failure modes most likely to bite a user. Each one gets a pinning test in its
owning task.

1. **Re-running a video in fast mode leaves the previous run's `hits.json` / `hit_signals.npz`
   beside the new results.** Stale files must be removed, the same way `strokes.json` already
   is. (Task 8)
2. **Sloppy or degenerate manual court corners** (collinear, or a zero-area quad) must make
   the detector fall back to the contact rule with a reason, never crash the run. (Task 8)
3. **59.94 fps footage** (the DJI clips) must map 30 Hz steps back to the correct native frames,
   monotonically and without off-by-one errors. (Task 2)
4. **Full-length matches** (64k+ frames) are inferred in chunks, and chunked inference must
   equal whole-sequence inference exactly. (Task 5)
5. **A cached `shuttle_trajectory.json` from a run in a different TrackNetV3 mode** must not be
   reused. The mode is part of the cache key. (Task 7)

---

## File structure

| File | Responsibility |
|---|---|
| `badminton_analysis/stroke/hit_signals.py` (new) | `RawSignals` dataclass; builds it from the pipeline's tracks; npz save and load |
| `badminton_analysis/stroke/hit_features.py` (new) | 30 Hz resampling, `CourtGeometry`, camera-independent per-step features |
| `badminton_analysis/stroke/hit_augment.py` (new) | Training-time augmentation of `RawSignals` (mirror, homography, dropout, fps jitter) |
| `badminton_analysis/stroke/hit_model.py` (new) | `HitTCN` network, safe save and load with a sidecar and schema checks, file hash |
| `badminton_analysis/stroke/hit_detector.py` (new) | Shuttle coverage, peak-picking, chunked inference, `detect_hits` |
| `badminton_analysis/stroke/hit_metrics.py` (new) | One-to-one event matching and recall / precision / hitter metrics |
| `badminton_analysis/stroke/shuttle_turns.py` (new) | Shuttle-path turn events (approach-A baseline and alignment signal) |
| `badminton_analysis/stroke/hit_training.py` (new) | Label-to-step mapping, soft targets, windows, peak-config selection (pure numpy) |
| `badminton_analysis/system.py` (modify) | Whole-video TrackNetV3, progress heartbeat, `_run_hit_detection`, `hits.json`, consumers, fallbacks |
| `badminton_analysis/stroke_recog/recognizer.py` (modify) | `label_rally(..., hits=None)` |
| `badminton_analysis/shuttle_track/tracknet.py` (modify) | `track_video(..., eval_mode, progress)` |
| `third_party/tracknet/infer.py`, `third_party/tracknet/CONTRACT.md` (modify) | Progress callback, recorded as a deviation |
| `static/kestrel.js` (modify) | `tracking_shuttle` stage |
| `main.py`, `app.py` (modify) | `--hit-model`, `--tracknet-mode`, weights discovery |
| `scripts/shuttleset_labels.py` (new) | ShuttleSet CSV parsing, 18→7 mapping, corners, near/far, rally spans, offset search |
| `scripts/check_shuttleset_alignment.py` (new) | Accepts or rejects a fetched match video against its labels |
| `scripts/build_hit_dataset.py` (new) | Runs the pipeline over labelled rallies → `data/hit_dataset/<match>/*.npz` |
| `scripts/train_hit_detector.py` (new) | Training loop → `weights/hit-detector.pt` + `.json` |
| `scripts/score_hits.py` (new) | Evaluation harness against a `LABELS.md` |
| `tests/hit_fixtures.py` (new) | Shared synthetic `RawSignals` builders for tests |
| `tests/test_hit_*.py`, `tests/test_shuttleset_labels.py`, `tests/test_tracknet_whole_video.py` (new) | Hermetic tests |

**Spec note** (recorded in the outcome addendum in Task 15): the pipeline also writes
`hit_signals.npz`. It is the detector's raw input, and also what the dataset builder harvests,
so training and match-time inputs cannot drift apart.

---

### Task 0: Claim the implementation scope

The design claim `5b4f1a3f-1e63-492a-9055-668e28ce458a` covers only the spec, this plan and the
status file. The helper changes a claim's scope only through `takeover` of an **expired**
claim, and the lease ends at **2026-10-03T22:54:56Z**.

**Files:** `.ai/workstreams/learned-hit-detection.md`

- [ ] **Step 1:** Confirm the lease has expired, and that the tree is clean apart from
  `BirdEye Prototype.html`.

```bash
date -u +%FT%TZ
git status --short
```

Expected: the time is after 2026-10-03T22:54:56Z, and only `?? "BirdEye Prototype.html"` is
listed.

- [ ] **Step 2:** Take over with the implementation scope.

```bash
pwsh -NoProfile -Command "& ./scripts/ai-handoff.ps1 takeover -PreviousClaimId '5b4f1a3f-1e63-492a-9055-668e28ce458a' -Reason 'Plan approved; widening the design-only claim to the implementation scope.' -Agent claude -SessionId 'fa9c4028-5d3f-437e-bc53-39e250458815' -Workstream learned-hit-detection -Scope @('.ai/workstreams/learned-hit-detection.md','docs/superpowers/specs/2026-10-04-learned-hit-detection-design.md','docs/superpowers/plans/2026-10-04-learned-hit-detection.md','badminton_analysis/stroke/hit_signals.py','badminton_analysis/stroke/hit_features.py','badminton_analysis/stroke/hit_augment.py','badminton_analysis/stroke/hit_model.py','badminton_analysis/stroke/hit_detector.py','badminton_analysis/stroke/hit_metrics.py','badminton_analysis/stroke/shuttle_turns.py','badminton_analysis/stroke/hit_training.py','badminton_analysis/system.py','badminton_analysis/stroke_recog/recognizer.py','badminton_analysis/shuttle_track/tracknet.py','third_party/tracknet/infer.py','third_party/tracknet/CONTRACT.md','static/kestrel.js','main.py','app.py','scripts/shuttleset_labels.py','scripts/check_shuttleset_alignment.py','scripts/build_hit_dataset.py','scripts/build_own_hit_dataset.py','scripts/train_hit_detector.py','scripts/score_hits.py','tests/hit_fixtures.py','tests/test_hit_signals.py','tests/test_hit_features.py','tests/test_hit_augment.py','tests/test_hit_model.py','tests/test_hit_detector.py','tests/test_hit_metrics.py','tests/test_score_hits.py','tests/test_tracknet_whole_video.py','tests/test_tracknet_integration.py','tests/test_hit_integration.py','tests/test_hit_wiring.py','tests/test_shuttleset_labels.py','tests/test_build_hit_dataset.py','tests/test_hit_training.py')"
```

Expected: `ok: True`, plus a new `claim_id`. Record that claim id in the status file in Step 3.

- [ ] **Step 3:** In `.ai/workstreams/learned-hit-detection.md`:
  - set **Claim id** to the new id;
  - update **Scope paths** to say "implementation claim (see Task 0 of the plan)".

  Then commit.

```bash
git add .ai/workstreams/learned-hit-detection.md
git commit -m "chore(continuity): widen learned-hit-detection claim to the implementation scope

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 1: Raw signals (`hit_signals.py`) and the shared test fixtures

**Files:**
- Create: `badminton_analysis/stroke/hit_signals.py`
- Create: `tests/hit_fixtures.py`
- Test: `tests/test_hit_signals.py`

**Interfaces:**
- Produces:
  - `SIDES = ("lower", "upper")`, `N_KP = 17`
  - `RawSignals(fps: float, first_frame: int, shuttle: (T,2) float32, keypoints: (T,2,17,2) float32, court_corners: (4,2) float32, image_size: (w,h))`, with `.n_frames` and `.frame_number(i)`
  - `build_signals(fps, last_frame, analysis_frames, shuttle_by_frame, court_corners, image_size) -> RawSignals`
  - `save_signals(path, sig)`, `load_signals(path) -> RawSignals`
  - Test fixtures `tests.hit_fixtures`: `CORNERS`, `pose_at(hip_xy, scale)`, `make_signals(...)`, `transform(sig, s, dx, dy)`

- [ ] **Step 1: Write the fixtures** (used by the tests of Tasks 1–6 and 12).

```python
# tests/hit_fixtures.py
"""Synthetic RawSignals for hit-detector tests (no video, no models)."""
from dataclasses import replace

import numpy as np

from badminton_analysis.stroke.hit_signals import RawSignals

# far-left, far-right, near-right, near-left (CourtMapper order), 1280x720 frame
CORNERS = np.array([[440, 260], [840, 260], [1060, 660], [220, 660]], np.float32)

# A standing COCO-17 pose relative to the hip midpoint, torso length 1.0
# (shoulder midpoint at y=-1.0). Scaled by the player's on-screen size.
_POSE = np.array([
    [0.0, -1.6], [-0.1, -1.7], [0.1, -1.7], [-0.2, -1.65], [0.2, -1.65],
    [-0.35, -1.0], [0.35, -1.0],
    [-0.45, -0.5], [0.45, -0.5],
    [-0.5, -0.05], [0.5, -0.05],
    [-0.2, 0.0], [0.2, 0.0],
    [-0.22, 0.8], [0.22, 0.8],
    [-0.24, 1.6], [0.24, 1.6],
], np.float32)


def pose_at(hip_xy, scale):
    return (_POSE * float(scale) + np.asarray(hip_xy, np.float32)).astype(np.float32)


def make_signals(n=120, fps=30.0, size=(1280, 720), players=True):
    """Shuttle on a triangle wave between the players: a sharp turn every whole second."""
    t = np.arange(n) / fps
    x = 640 + 120 * np.sin(2 * np.pi * t / 2.0)
    y = 300 + 250 * np.abs((t % 2.0) - 1.0)
    shuttle = np.stack([x, y], 1).astype(np.float32)
    kp = np.full((n, 2, 17, 2), np.nan, np.float32)
    if players:
        kp[:, 0] = pose_at((640, 560), 60.0)   # near: large
        kp[:, 1] = pose_at((640, 250), 25.0)   # far: small
    return RawSignals(float(fps), 1, shuttle, kp, CORNERS.copy(), tuple(size))


def transform(sig, s, dx, dy):
    """Similarity transform of every image coordinate (a different camera zoom/position)."""
    d = np.array([dx, dy], np.float32)
    w, h = sig.image_size
    return replace(sig, shuttle=sig.shuttle * s + d, keypoints=sig.keypoints * s + d,
                   court_corners=sig.court_corners * s + d,
                   image_size=(int(round(w * s)), int(round(h * s))))
```

- [ ] **Step 2: Write the failing tests.**

```python
# tests/test_hit_signals.py
import numpy as np
import pytest

from badminton_analysis.stroke.hit_signals import (
    N_KP, RawSignals, build_signals, load_signals, save_signals)
from tests.hit_fixtures import CORNERS, make_signals, pose_at


def _frames_with(kp_lower, kp_upper):
    return {"players": {"lower": {"keypoints": kp_lower}, "upper": {"keypoints": kp_upper}}}


def test_build_places_shuttle_and_keypoints_by_one_based_frame():
    kp = pose_at((600, 500), 50.0)
    sig = build_signals(30.0, 5, {2: _frames_with(kp, None)}, {1: (10.0, 20.0), 3: None},
                        CORNERS, (1280, 720))
    assert sig.n_frames == 5 and sig.first_frame == 1
    assert sig.shuttle[0].tolist() == [10.0, 20.0]
    assert np.isnan(sig.shuttle[2]).all() and np.isnan(sig.shuttle[4]).all()
    assert np.allclose(sig.keypoints[1, 0], kp)
    assert np.isnan(sig.keypoints[1, 1]).all()          # upper missing
    assert np.isnan(sig.keypoints[0]).all()             # frame 1 had no pose record
    assert sig.frame_number(4) == 5


def test_missing_keypoints_marked_le_1_become_nan():
    kp = pose_at((600, 500), 50.0)
    kp[9] = (0.0, 0.0)                                  # the pipeline's "missing" marker
    sig = build_signals(30.0, 1, {1: _frames_with(kp, None)}, {}, CORNERS, (1280, 720))
    assert np.isnan(sig.keypoints[0, 0, 9]).all()
    assert np.isfinite(sig.keypoints[0, 0, 10]).all()


def test_malformed_keypoints_and_out_of_range_frames_are_ignored():
    sig = build_signals(30.0, 3, {2: _frames_with(np.zeros((5, 2)), None), 9: _frames_with(None, None)},
                        {7: (1.0, 1.0)}, CORNERS, (1280, 720))
    assert np.isnan(sig.keypoints).all()
    assert np.isnan(sig.shuttle).all()


def test_keypoints_with_confidence_column_keep_xy_only():
    kp = np.concatenate([pose_at((600, 500), 50.0), np.ones((N_KP, 1), np.float32)], axis=1)
    sig = build_signals(30.0, 1, {1: _frames_with(kp, None)}, {}, CORNERS, (1280, 720))
    assert sig.keypoints.shape == (1, 2, N_KP, 2)
    assert np.allclose(sig.keypoints[0, 0], kp[:, :2])


def test_no_frames_raises():
    with pytest.raises(ValueError):
        build_signals(30.0, 0, {}, {}, CORNERS, (1280, 720))


def test_save_load_roundtrip(tmp_path):
    sig = make_signals(n=40)
    path = tmp_path / "hit_signals.npz"
    save_signals(path, sig)
    back = load_signals(path)
    assert isinstance(back, RawSignals)
    assert back.fps == sig.fps and back.first_frame == sig.first_frame
    assert back.image_size == sig.image_size
    assert np.array_equal(back.shuttle, sig.shuttle, equal_nan=True)
    assert np.array_equal(back.keypoints, sig.keypoints, equal_nan=True)
    assert np.array_equal(back.court_corners, sig.court_corners)
```

- [ ] **Step 3: Run the tests to verify they fail.**

Run: `PYTHONUTF8=1 ./.venv/Scripts/python.exe -B -m pytest -q -p no:cacheprovider tests/test_hit_signals.py`

Expected: they fail with `ModuleNotFoundError: No module named 'badminton_analysis.stroke.hit_signals'`.

- [ ] **Step 4: Implement.**

```python
# badminton_analysis/stroke/hit_signals.py
"""Raw per-frame signals the learned hit detector reads.

One row per native frame, from frame 1 (the match loop's first frame_count) to
the last analysed frame: the shuttle's image position and both players' 17
COCO keypoints, plus the court corners. Missing values are NaN. Side index 0 is
"lower" (the near player), 1 is "upper" (the far player).

The match pipeline writes these to ``hit_signals.npz``. The ShuttleSet dataset
builder harvests the same file, so the detector is trained on exactly what it
sees at match time.
"""
from dataclasses import dataclass

import numpy as np

SIDES = ("lower", "upper")
N_KP = 17


@dataclass
class RawSignals:
    fps: float
    first_frame: int
    shuttle: np.ndarray        # (T, 2) float32, image px, NaN = missing
    keypoints: np.ndarray      # (T, 2, 17, 2) float32, image px, NaN = missing
    court_corners: np.ndarray  # (4, 2) float32: far-left, far-right, near-right, near-left
    image_size: tuple          # (width, height)

    @property
    def n_frames(self):
        return int(self.shuttle.shape[0])

    def frame_number(self, index):
        return self.first_frame + int(index)


def _keypoints_or_nan(kp):
    if kp is None:
        return np.full((N_KP, 2), np.nan, np.float32)
    arr = np.asarray(kp, dtype=np.float32)
    if arr.ndim != 2 or arr.shape[0] != N_KP or arr.shape[1] < 2:
        return np.full((N_KP, 2), np.nan, np.float32)
    arr = arr[:, :2].copy()
    missing = (arr[:, 0] <= 1) & (arr[:, 1] <= 1)
    arr[missing] = np.nan
    return arr


def build_signals(fps, last_frame, analysis_frames, shuttle_by_frame, court_corners, image_size):
    """RawSignals for frames 1..last_frame.

    ``analysis_frames`` is BadmintonAnalysisSystem._analysis_frames
    ({frame: {"players": {side: {"keypoints": array|None}}}}); ``shuttle_by_frame``
    maps a 1-based frame to (x, y) or None. Frames outside 1..last_frame are ignored.
    """
    total = int(last_frame)
    if total < 1:
        raise ValueError("no frames to build hit signals from")
    shuttle = np.full((total, 2), np.nan, np.float32)
    keypoints = np.full((total, 2, N_KP, 2), np.nan, np.float32)
    for frame, point in (shuttle_by_frame or {}).items():
        if point is not None and 1 <= int(frame) <= total:
            shuttle[int(frame) - 1] = (float(point[0]), float(point[1]))
    for frame, rec in (analysis_frames or {}).items():
        if not 1 <= int(frame) <= total:
            continue
        players = (rec or {}).get("players") or {}
        for s, side in enumerate(SIDES):
            keypoints[int(frame) - 1, s] = _keypoints_or_nan((players.get(side) or {}).get("keypoints"))
    corners = np.asarray(court_corners, dtype=np.float32).reshape(4, 2)
    return RawSignals(float(fps), 1, shuttle, keypoints, corners,
                      (int(image_size[0]), int(image_size[1])))


def save_signals(path, sig):
    np.savez_compressed(path, fps=np.float64(sig.fps), first_frame=np.int64(sig.first_frame),
                        shuttle=sig.shuttle, keypoints=sig.keypoints,
                        court_corners=sig.court_corners,
                        image_size=np.asarray(sig.image_size, dtype=np.int64))


def load_signals(path):
    with np.load(path) as z:
        return RawSignals(float(z["fps"]), int(z["first_frame"]),
                          z["shuttle"].astype(np.float32), z["keypoints"].astype(np.float32),
                          z["court_corners"].astype(np.float32),
                          tuple(int(v) for v in z["image_size"]))
```

- [ ] **Step 5: Run the tests to verify they pass.**

Run: `PYTHONUTF8=1 ./.venv/Scripts/python.exe -B -m pytest -q -p no:cacheprovider tests/test_hit_signals.py`

Expected: 6 passed.

- [ ] **Step 6: Commit.**

```bash
git add badminton_analysis/stroke/hit_signals.py tests/hit_fixtures.py tests/test_hit_signals.py
git commit -m "feat(hits): raw per-frame signals for the learned hit detector

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Camera-independent features (`hit_features.py`)

**Files:**
- Create: `badminton_analysis/stroke/hit_features.py`
- Test: `tests/test_hit_features.py`

**Interfaces:**
- Consumes: `RawSignals` (Task 1)
- Produces:
  - `TARGET_HZ = 30.0`, `COURT_W = 6.1`, `COURT_L = 13.4`
  - `SHUTTLE_DIM = 8`, `PLAYER_DIM = 41`, `FEATURE_DIM = 90`, `FEATURE_SCHEMA = 1`
  - `resample_index(n_frames, fps, target_hz=TARGET_HZ) -> np.ndarray[int64]`
  - `apply_homography(h, pts) -> np.ndarray`
  - `CourtGeometry(corners)`, with `.net_mid`, `.length_px`, `.court_units(pts)`,
    `.ground_metres(pts)`; it raises `ValueError` on degenerate corners
  - `compute_features(sig, idx=None) -> (feats (K, 90) float32, idx (K,) int64)`
  - The feature layout per step:
    - `[0:2]` shuttle position, `[2:4]` velocity, `[4:6]` acceleration, all in court units;
    - `[6]` turn angle / 180°;
    - `[7]` shuttle-visible flag;
    - then one 41-wide block per player, side 0 then side 1, each holding:
      - `[0:34]` keypoints relative to the hip, in torso lengths;
      - `[34]` wrist speed (torso lengths per second);
      - `[35:37]` feet position on court (x / 6.1, y / 13.4);
      - `[37:39]` shuttle offset from the hip (torso lengths);
      - `[39]` shuttle–nearest-wrist distance (torso lengths);
      - `[40]` player-valid flag.

- [ ] **Step 1: Write the failing tests.**

```python
# tests/test_hit_features.py
import numpy as np
import pytest

from badminton_analysis.stroke.hit_features import (
    FEATURE_DIM, PLAYER_DIM, SHUTTLE_DIM, CourtGeometry, compute_features, resample_index)
from tests.hit_fixtures import CORNERS, make_signals, transform


def test_resample_index_30_60_and_degenerate():
    assert resample_index(10, 30.0).tolist() == list(range(10))
    assert resample_index(10, 60.0).tolist() == [0, 2, 4, 6, 8]
    assert resample_index(0, 30.0).size == 0
    assert resample_index(10, 0.0).size == 0
    assert resample_index(10, float("nan")).size == 0


def test_resample_index_5994_is_monotonic_and_in_range():
    idx = resample_index(6000, 59.94)
    assert idx[0] == 0 and idx[-1] <= 5999
    assert np.all(np.diff(idx) >= 1)
    # step k sits at k/30 s; native frame idx[k] sits at idx[k]/59.94 s -- within half a frame
    t_step = np.arange(idx.size) / 30.0
    assert np.max(np.abs(idx / 59.94 - t_step)) <= 0.5 / 59.94 + 1e-9


def test_feature_shape_and_no_nans():
    feats, idx = compute_features(make_signals(n=90))
    assert feats.shape == (90, FEATURE_DIM) and feats.dtype == np.float32
    assert np.isfinite(feats).all()
    assert idx.tolist() == list(range(90))


def test_features_invariant_to_camera_zoom_and_shift():
    sig = make_signals(n=90)
    a, _ = compute_features(sig)
    b, _ = compute_features(transform(sig, 1.5, 100.0, 50.0))
    assert np.allclose(a, b, atol=1e-3)


def test_60fps_features_use_every_other_frame():
    sig = make_signals(n=120, fps=60.0)
    feats, idx = compute_features(sig)
    assert idx.tolist() == list(range(0, 120, 2))
    assert feats.shape == (60, FEATURE_DIM)


def test_missing_shuttle_zeroes_shuttle_block_and_clears_flag():
    sig = make_signals(n=30)
    sig.shuttle[10:15] = np.nan
    feats, _ = compute_features(sig)
    assert feats[12, 7] == 0.0 and np.all(feats[12, 0:7] == 0.0)
    assert feats[20, 7] == 1.0


def test_missing_player_block_is_zero_with_flag_cleared():
    sig = make_signals(n=30, players=False)
    feats, _ = compute_features(sig)
    for p in range(2):
        base = SHUTTLE_DIM + p * PLAYER_DIM
        assert np.all(feats[:, base:base + PLAYER_DIM] == 0.0)


def test_player_size_does_not_change_pose_features():
    sig = make_signals(n=30)
    big = make_signals(n=30)
    hip = np.nanmean(big.keypoints[:, 0, [11, 12]], axis=1, keepdims=True)   # (T,1,2)
    big.keypoints[:, 0] = (big.keypoints[:, 0] - hip) * 2.0 + hip            # near player 2x larger
    a, _ = compute_features(sig)
    b, _ = compute_features(big)
    base = SHUTTLE_DIM
    assert np.allclose(a[:, base:base + 34], b[:, base:base + 34], atol=1e-5)
    assert a[0, base + 40] == 1.0


def test_turn_feature_peaks_at_the_triangle_wave_corners():
    feats, _ = compute_features(make_signals(n=90))
    turn = feats[:, 6]
    assert turn[30] > 0.3 and turn[60] > 0.3       # the shuttle reverses at t = 1 s and 2 s
    assert turn[45] < 0.1


def test_degenerate_corners_raise():
    with pytest.raises(ValueError):
        CourtGeometry(np.array([[0, 0], [10, 0], [20, 0], [30, 0]], np.float32))


def test_court_geometry_net_midpoint_lies_between_baselines():
    geo = CourtGeometry(CORNERS)
    assert 260 < geo.net_mid[1] < 660
    assert geo.length_px > 300
```

- [ ] **Step 2: Run the tests to verify they fail.**

Run: `PYTHONUTF8=1 ./.venv/Scripts/python.exe -B -m pytest -q -p no:cacheprovider tests/test_hit_features.py`

Expected: they fail with `ModuleNotFoundError`.

- [ ] **Step 3: Implement.**

```python
# badminton_analysis/stroke/hit_features.py
"""Per-step features for the learned hit detector, built to travel across cameras.

Everything is resampled to TARGET_HZ first. Shuttle positions are expressed in
"court units" (offset from the net's image midpoint divided by the court's
on-screen baseline-to-baseline length); each player's pose in that player's own
torso lengths; feet in court metres through the court homography (feet are on
the ground, so that mapping is exact). A zoomed, shifted or higher-resolution
view of the same play therefore yields the same features. Missing inputs become
0 with an explicit flag.
"""
import cv2
import numpy as np

TARGET_HZ = 30.0
COURT_W, COURT_L = 6.1, 13.4
L_SH, R_SH, L_WR, R_WR, L_HIP, R_HIP, L_ANK, R_ANK = 5, 6, 9, 10, 11, 12, 15, 16
SHUTTLE_DIM = 8
PLAYER_DIM = 41
FEATURE_DIM = SHUTTLE_DIM + 2 * PLAYER_DIM
FEATURE_SCHEMA = 1
_CLIP = 1.0e3


def resample_index(n_frames, fps, target_hz=TARGET_HZ):
    """Native frame indices nearest to each TARGET_HZ step (empty if fps is unusable)."""
    try:
        fps = float(fps)
    except (TypeError, ValueError):
        return np.zeros(0, dtype=np.int64)
    if n_frames <= 0 or not np.isfinite(fps) or fps <= 0 or target_hz <= 0:
        return np.zeros(0, dtype=np.int64)
    k_max = int(np.floor((n_frames - 1) * target_hz / fps + 1e-9))
    idx = np.rint(np.arange(k_max + 1) * fps / target_hz).astype(np.int64)
    return np.clip(idx, 0, n_frames - 1)


def apply_homography(h, pts):
    pts = np.asarray(pts, dtype=np.float64)
    flat = pts.reshape(-1, 2)
    homog = np.hstack([flat, np.ones((flat.shape[0], 1))]) @ np.asarray(h, np.float64).T
    with np.errstate(invalid="ignore", divide="ignore"):
        out = homog[:, :2] / homog[:, 2:3]
    return out.reshape(pts.shape)


class CourtGeometry:
    """Court-relative measurements from the four image corners."""

    def __init__(self, corners):
        c = np.asarray(corners, dtype=np.float32).reshape(4, 2)
        dst = np.array([[0, 0], [COURT_W, 0], [COURT_W, COURT_L], [0, COURT_L]], np.float32)
        area = 0.5 * abs(float(np.cross(c[2] - c[0], c[3] - c[1])))
        if not np.isfinite(c).all() or area < 1.0:
            raise ValueError("degenerate court corners")
        try:
            self.to_court = cv2.getPerspectiveTransform(c, dst).astype(np.float64)
            self.to_image = cv2.getPerspectiveTransform(dst, c).astype(np.float64)
        except cv2.error as exc:
            raise ValueError(f"degenerate court corners: {exc}") from exc
        far = apply_homography(self.to_image, [COURT_W / 2, 0.0])
        near = apply_homography(self.to_image, [COURT_W / 2, COURT_L])
        self.net_mid = apply_homography(self.to_image, [COURT_W / 2, COURT_L / 2])
        self.length_px = float(np.hypot(*(near - far)))
        if not np.isfinite(self.length_px) or self.length_px < 1.0 or not np.isfinite(self.net_mid).all():
            raise ValueError("degenerate court corners")

    def court_units(self, pts):
        return (np.asarray(pts, np.float64) - self.net_mid) / self.length_px

    def ground_metres(self, pts):
        return apply_homography(self.to_court, pts)


def _diff(a):
    out = np.full(a.shape, np.nan, dtype=np.float64)
    out[1:] = a[1:] - a[:-1]
    return out


def _turn_angles(pos):
    n = len(pos)
    out = np.full(n, np.nan)
    if n < 3:
        return out
    v1 = pos[1:-1] - pos[:-2]
    v2 = pos[2:] - pos[1:-1]
    n1 = np.linalg.norm(v1, axis=1)
    n2 = np.linalg.norm(v2, axis=1)
    ok = np.isfinite(n1) & np.isfinite(n2) & (n1 > 1e-9) & (n2 > 1e-9)
    with np.errstate(invalid="ignore", divide="ignore"):
        cos = np.einsum("ij,ij->i", v1, v2) / (n1 * n2)
    ang = np.degrees(np.arccos(np.clip(cos, -1.0, 1.0))) / 180.0
    out[1:-1] = np.where(ok, ang, np.nan)
    return out


def _mid(a, b):
    return (a + b) / 2.0


def _either(a, b):
    both = np.isfinite(a).all(axis=1) & np.isfinite(b).all(axis=1)
    only_a = np.isfinite(a).all(axis=1) & ~both
    out = np.where(both[:, None], _mid(a, b), np.where(only_a[:, None], a, b))
    return out


def _player_block(kp, shuttle, geo, dt):
    """(K, 41) features for one side; kp (K,17,2) and shuttle (K,2) in image px."""
    k = kp.shape[0]
    kp = kp.astype(np.float64)
    shuttle = shuttle.astype(np.float64)
    hip = _mid(kp[:, L_HIP], kp[:, R_HIP])
    shoulder = _mid(kp[:, L_SH], kp[:, R_SH])
    torso = np.linalg.norm(shoulder - hip, axis=1)
    valid = np.isfinite(torso) & (torso > 1.0)
    t = np.where(valid, torso, np.nan)
    with np.errstate(invalid="ignore", divide="ignore"):
        rel = (kp - hip[:, None, :]) / t[:, None, None]
        wrists = kp[:, [L_WR, R_WR]]
        wrist_speed = np.linalg.norm(_diff(wrists), axis=2) / dt / t[:, None]
        speed = np.fmax(wrist_speed[:, 0], wrist_speed[:, 1])
        feet = geo.ground_metres(_either(kp[:, L_ANK], kp[:, R_ANK])) / np.array([COURT_W, COURT_L])
        shuttle_off = (shuttle - hip) / t[:, None]
        wrist_dist = np.linalg.norm(shuttle[:, None, :] - wrists, axis=2) / t[:, None]
        nearest = np.fmin(wrist_dist[:, 0], wrist_dist[:, 1])
    block = np.concatenate([rel.reshape(k, 34), speed[:, None], feet, shuttle_off,
                            nearest[:, None]], axis=1)
    block = np.clip(np.nan_to_num(block, nan=0.0, posinf=0.0, neginf=0.0), -_CLIP, _CLIP)
    block[~valid] = 0.0
    return np.concatenate([block, valid[:, None].astype(np.float64)], axis=1)


def compute_features(sig, idx=None):
    """(features (K, FEATURE_DIM) float32, native indices (K,)) at TARGET_HZ."""
    if idx is None:
        idx = resample_index(sig.n_frames, sig.fps)
    idx = np.asarray(idx, dtype=np.int64)
    feats = np.zeros((len(idx), FEATURE_DIM), np.float32)
    if len(idx) == 0:
        return feats, idx
    geo = CourtGeometry(sig.court_corners)
    dt = 1.0 / TARGET_HZ
    shuttle_px = sig.shuttle[idx]
    pos = geo.court_units(shuttle_px)
    visible = np.isfinite(pos).all(axis=1)
    vel = _diff(pos) / dt
    acc = _diff(vel) / dt
    turn = _turn_angles(pos)
    block = np.concatenate([pos, vel, acc, turn[:, None]], axis=1)
    feats[:, 0:7] = np.clip(np.nan_to_num(block, nan=0.0, posinf=0.0, neginf=0.0), -_CLIP, _CLIP)
    feats[:, 7] = visible
    for p in range(2):
        base = SHUTTLE_DIM + p * PLAYER_DIM
        feats[:, base:base + PLAYER_DIM] = _player_block(sig.keypoints[idx, p], shuttle_px, geo, dt)
    return feats, idx
```

- [ ] **Step 4: Run the tests to verify they pass.**

Run: `PYTHONUTF8=1 ./.venv/Scripts/python.exe -B -m pytest -q -p no:cacheprovider tests/test_hit_features.py`

Expected: 11 passed. If the turn test fails on the exact step index, check the triangle wave
(the turns are at t = 1 s and t = 2 s, which is steps 30 and 60 at 30 Hz) before changing the
thresholds.

- [ ] **Step 5: Commit.**

```bash
git add badminton_analysis/stroke/hit_features.py tests/test_hit_features.py
git commit -m "feat(hits): camera-independent 30 Hz features for hit detection

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Training augmentation (`hit_augment.py`)

**Files:**
- Create: `badminton_analysis/stroke/hit_augment.py`
- Test: `tests/test_hit_augment.py`

**Interfaces:**
- Consumes: `RawSignals` (Task 1); `apply_homography`, `resample_index`, `TARGET_HZ` (Task 2)
- Produces:
  - `FLIP_KP` (a list of 17 indices)
  - `mirror(sig)`
  - `random_homography(sig, rng, scale=(0.8,1.2), shift_frac=0.1, persp_frac=0.03)`
  - `shuttle_dropout(sig, rng, keep_range=(0.65,1.0), max_gap=10)`
  - `keypoint_dropout(sig, rng, joint_p=0.05, player_p=0.02)`
  - `augment(sig, rng)`
  - `jittered_index(n_frames, fps, rng, jitter=0.1) -> np.ndarray`

  Every function returns a new `RawSignals` (or an index array) and never mutates its input.

- [ ] **Step 1: Write the failing tests.**

```python
# tests/test_hit_augment.py
import numpy as np

from badminton_analysis.stroke.hit_augment import (
    augment, jittered_index, keypoint_dropout, mirror, random_homography, shuttle_dropout)
from badminton_analysis.stroke.hit_features import compute_features
from tests.hit_fixtures import make_signals


def test_mirror_twice_is_identity_and_input_untouched():
    sig = make_signals(n=40)
    before = sig.shuttle.copy()
    twice = mirror(mirror(sig))
    assert np.allclose(twice.shuttle, sig.shuttle)
    assert np.allclose(twice.keypoints, sig.keypoints, equal_nan=True)
    assert np.allclose(twice.court_corners, sig.court_corners)
    assert np.array_equal(sig.shuttle, before)


def test_mirror_swaps_left_right_joints_and_negates_court_x():
    sig = make_signals(n=40)
    m = mirror(sig)
    w = sig.image_size[0]
    assert np.isclose(m.keypoints[0, 0, 9, 0], (w - 1) - sig.keypoints[0, 0, 10, 0])
    a, _ = compute_features(sig)
    b, _ = compute_features(m)
    assert np.allclose(a[:, 0], -b[:, 0], atol=1e-3)     # shuttle court-x flips sign
    assert np.allclose(a[:, 1], b[:, 1], atol=1e-3)      # court-y unchanged


def test_random_homography_moves_points_keeps_nan_and_is_seeded():
    sig = make_signals(n=40)
    sig.shuttle[5] = np.nan
    a = random_homography(sig, np.random.default_rng(1))
    b = random_homography(sig, np.random.default_rng(1))
    assert np.isnan(a.shuttle[5]).all()
    assert not np.allclose(a.shuttle[0], sig.shuttle[0])
    assert np.allclose(a.shuttle, b.shuttle, equal_nan=True)


def test_shuttle_dropout_reaches_target_presence_and_never_invents_points():
    sig = make_signals(n=600)
    out = shuttle_dropout(sig, np.random.default_rng(0), keep_range=(0.7, 0.7))
    present = np.isfinite(out.shuttle).all(axis=1)
    assert 0.6 <= present.mean() <= 0.75
    assert np.all(np.isfinite(sig.shuttle[present]))


def test_keypoint_dropout_only_removes():
    sig = make_signals(n=200)
    out = keypoint_dropout(sig, np.random.default_rng(0), joint_p=0.2, player_p=0.1)
    assert np.isnan(out.keypoints).sum() > np.isnan(sig.keypoints).sum()
    kept = np.isfinite(out.keypoints)
    assert np.allclose(out.keypoints[kept], sig.keypoints[kept])


def test_augment_output_still_featurises():
    out = augment(make_signals(n=120), np.random.default_rng(3))
    feats, _ = compute_features(out)
    assert np.isfinite(feats).all()


def test_jittered_index_is_monotonic_and_in_range():
    rng = np.random.default_rng(0)
    for _ in range(20):
        idx = jittered_index(300, 60.0, rng)
        assert idx.size > 0 and idx.min() >= 0 and idx.max() <= 299
        assert np.all(np.diff(idx) >= 0)
```

- [ ] **Step 2: Run the tests to verify they fail.**

Run: `PYTHONUTF8=1 ./.venv/Scripts/python.exe -B -m pytest -q -p no:cacheprovider tests/test_hit_augment.py`

Expected: they fail with `ModuleNotFoundError`.

- [ ] **Step 3: Implement.**

```python
# badminton_analysis/stroke/hit_augment.py
"""Training-time augmentation of RawSignals -- the camera-gap defence (spec 7.4).

Applied to raw signals before features are computed, so every feature sees a
consistent transformed scene. Never mutates its input.
"""
from dataclasses import replace

import cv2
import numpy as np

from .hit_features import TARGET_HZ, apply_homography, resample_index

FLIP_KP = [0, 2, 1, 4, 3, 6, 5, 8, 7, 10, 9, 12, 11, 14, 13, 16, 15]


def mirror(sig):
    w = sig.image_size[0]

    def flip_x(a):
        b = np.array(a, dtype=np.float32, copy=True)
        b[..., 0] = (w - 1) - b[..., 0]
        return b

    return replace(sig, shuttle=flip_x(sig.shuttle),
                   keypoints=flip_x(sig.keypoints)[:, :, FLIP_KP],
                   court_corners=flip_x(sig.court_corners)[[1, 0, 3, 2]])


def random_homography(sig, rng, scale=(0.8, 1.2), shift_frac=0.1, persp_frac=0.03):
    w, h = sig.image_size
    src = np.float32([[0, 0], [w, 0], [w, h], [0, h]])
    centre = np.float32([w / 2.0, h / 2.0])
    size = np.float32([w, h])
    s = rng.uniform(*scale)
    dst = (src - centre) * s + centre + rng.uniform(-shift_frac, shift_frac, 2) * size
    dst = dst + rng.uniform(-persp_frac, persp_frac, (4, 2)) * size
    hmat = cv2.getPerspectiveTransform(src, dst.astype(np.float32))

    def warp(a):
        return apply_homography(hmat, a).astype(np.float32)

    return replace(sig, shuttle=warp(sig.shuttle), keypoints=warp(sig.keypoints),
                   court_corners=warp(sig.court_corners))


def shuttle_dropout(sig, rng, keep_range=(0.65, 1.0), max_gap=10):
    shuttle = sig.shuttle.copy()
    n = len(shuttle)
    present = np.isfinite(shuttle).all(axis=1)
    to_drop = int(round(present.sum() * (1.0 - rng.uniform(*keep_range))))
    dropped, guard = 0, 0
    while dropped < to_drop and guard < 10 * n + 10:
        start = int(rng.integers(0, max(n, 1)))
        seg = slice(start, min(n, start + int(rng.integers(1, max_gap + 1))))
        dropped += int(np.isfinite(shuttle[seg]).all(axis=1).sum())
        shuttle[seg] = np.nan
        guard += 1
    return replace(sig, shuttle=shuttle)


def keypoint_dropout(sig, rng, joint_p=0.05, player_p=0.02):
    kp = sig.keypoints.copy()
    kp[rng.random(kp.shape[:3]) < joint_p] = np.nan
    kp[rng.random(kp.shape[:2]) < player_p] = np.nan
    return replace(sig, keypoints=kp)


def augment(sig, rng):
    if rng.random() < 0.5:
        sig = mirror(sig)
    sig = random_homography(sig, rng)
    sig = shuttle_dropout(sig, rng)
    return keypoint_dropout(sig, rng)


def jittered_index(n_frames, fps, rng, jitter=0.1):
    """Resampling index with +-jitter frame-rate warp (the model still treats it as 30 Hz)."""
    return resample_index(n_frames, fps, TARGET_HZ * rng.uniform(1.0 - jitter, 1.0 + jitter))
```

- [ ] **Step 4: Run the tests to verify they pass.**

Run: `PYTHONUTF8=1 ./.venv/Scripts/python.exe -B -m pytest -q -p no:cacheprovider tests/test_hit_augment.py`

Expected: 7 passed.

- [ ] **Step 5: Commit.**

```bash
git add badminton_analysis/stroke/hit_augment.py tests/test_hit_augment.py
git commit -m "feat(hits): training augmentation for the camera gap

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: The model (`hit_model.py`)

**Files:**
- Create: `badminton_analysis/stroke/hit_model.py`
- Test: `tests/test_hit_model.py`

**Interfaces:**
- Consumes: `FEATURE_DIM`, `FEATURE_SCHEMA`, `TARGET_HZ` (Task 2)
- Produces:
  - `MODEL_SCHEMA = 1`
  - `HitTCN(in_ch, hidden=64, levels=4, dropout=0.1)`
    - `forward(x: (B,T,C)) -> (hit_logits (B,T), far_logits (B,T))`
    - buffers `feat_mean`, `feat_std` (input normalisation)
    - half-width receptive field `2 * (2**levels - 1)` steps (30 at levels=4, about ±1 s)
  - `class HitModelError(Exception)`
  - `meta_path(weights_path) -> str` (the `.json` sidecar)
  - `save_hit_model(model, meta, weights_path)`
  - `load_hit_model(weights_path, device="cpu") -> (model, meta)`
  - `file_sha256(path) -> str`
  - Required meta keys: `schema`, `feature_schema`, `feature_dim`, `target_hz`, `hidden`,
    `levels`, `threshold`, `min_spacing_sec`.

- [ ] **Step 1: Write the failing tests.**

```python
# tests/test_hit_model.py
import json

import pytest
import torch

from badminton_analysis.stroke import hit_model as hm
from badminton_analysis.stroke.hit_features import FEATURE_DIM, FEATURE_SCHEMA, TARGET_HZ


def _meta(**over):
    meta = {"schema": hm.MODEL_SCHEMA, "feature_schema": FEATURE_SCHEMA, "feature_dim": FEATURE_DIM,
            "target_hz": TARGET_HZ, "hidden": 16, "levels": 4, "threshold": 0.5,
            "min_spacing_sec": 0.27}
    meta.update(over)
    return meta


def test_forward_shapes_and_parameter_budget():
    model = hm.HitTCN(FEATURE_DIM)
    hit, far = model(torch.randn(2, 100, FEATURE_DIM))
    assert hit.shape == (2, 100) and far.shape == (2, 100)
    n_params = sum(p.numel() for p in model.parameters())
    assert 50_000 <= n_params <= 300_000


def test_receptive_field_is_about_one_second():
    torch.manual_seed(0)
    model = hm.HitTCN(FEATURE_DIM, hidden=16).eval()
    x = torch.randn(1, 200, FEATURE_DIM)
    x2 = x.clone()
    x2[0, 100] += 5.0
    with torch.no_grad():
        d = (model(x)[0] - model(x2)[0]).abs()[0]
    changed = torch.nonzero(d > 1e-6).flatten()
    assert changed.min() >= 70 and changed.max() <= 130


def test_save_load_roundtrip_identical_outputs(tmp_path):
    torch.manual_seed(1)
    model = hm.HitTCN(FEATURE_DIM, hidden=16).eval()
    model.feat_mean.fill_(0.5)
    path = tmp_path / "hit-detector.pt"
    hm.save_hit_model(model, _meta(), str(path))
    loaded, meta = hm.load_hit_model(str(path))
    x = torch.randn(1, 50, FEATURE_DIM)
    with torch.no_grad():
        assert torch.allclose(model(x)[0], loaded(x)[0])
    assert meta["threshold"] == 0.5
    assert json.loads((tmp_path / "hit-detector.json").read_text())["hidden"] == 16


def test_load_uses_weights_only(tmp_path, monkeypatch):
    model = hm.HitTCN(FEATURE_DIM, hidden=16)
    path = tmp_path / "m.pt"
    hm.save_hit_model(model, _meta(), str(path))
    seen = {}
    real = torch.load

    def spy(*a, **k):
        seen.update(k)
        return real(*a, **k)

    monkeypatch.setattr(torch, "load", spy)
    hm.load_hit_model(str(path))
    assert seen.get("weights_only") is True


@pytest.mark.parametrize("bad", [{"schema": 99}, {"feature_schema": 99},
                                 {"feature_dim": 3}, {"target_hz": 25.0}])
def test_schema_mismatch_raises(tmp_path, bad):
    model = hm.HitTCN(FEATURE_DIM, hidden=16)
    path = tmp_path / "m.pt"
    hm.save_hit_model(model, _meta(**bad), str(path))
    with pytest.raises(hm.HitModelError):
        hm.load_hit_model(str(path))


def test_missing_sidecar_raises(tmp_path):
    path = tmp_path / "m.pt"
    torch.save(hm.HitTCN(FEATURE_DIM, hidden=16).state_dict(), path)
    with pytest.raises(hm.HitModelError):
        hm.load_hit_model(str(path))


def test_file_sha256_is_stable(tmp_path):
    p = tmp_path / "f.bin"
    p.write_bytes(b"abc")
    assert hm.file_sha256(str(p)) == hm.file_sha256(str(p))
    assert len(hm.file_sha256(str(p))) == 64
```

- [ ] **Step 2: Run the tests to verify they fail.**

Run: `PYTHONUTF8=1 ./.venv/Scripts/python.exe -B -m pytest -q -p no:cacheprovider tests/test_hit_model.py`

Expected: they fail with `ModuleNotFoundError`.

- [ ] **Step 3: Implement.** BatchNorm (local per step in eval mode) is used, not GroupNorm,
  because GroupNorm normalises across time and would break the receptive-field locality.

```python
# badminton_analysis/stroke/hit_model.py
"""Small temporal CNN that scores every 30 Hz step for "a hit happens here".

Dilated residual blocks (kernel 3, dilations 1..2**(levels-1)) see about +-1 s
at levels=4. Two heads: hit score and P(far player hit). Inputs are normalised
inside the model (feat_mean / feat_std buffers), so the weights file is
self-contained. Saved as a state dict plus a JSON sidecar; loaded with
torch.load(weights_only=True).
"""
import hashlib
import json
import os

import torch
from torch import nn

from .hit_features import FEATURE_DIM, FEATURE_SCHEMA, TARGET_HZ

MODEL_SCHEMA = 1
_REQUIRED_META = ("schema", "feature_schema", "feature_dim", "target_hz", "hidden", "levels",
                  "threshold", "min_spacing_sec")


class HitModelError(Exception):
    """The hit-model weights or sidecar are missing or incompatible."""


class _Block(nn.Module):
    def __init__(self, ch, dilation, dropout):
        super().__init__()
        self.conv1 = nn.Conv1d(ch, ch, 3, padding=dilation, dilation=dilation)
        self.conv2 = nn.Conv1d(ch, ch, 3, padding=dilation, dilation=dilation)
        self.norm1 = nn.BatchNorm1d(ch)
        self.norm2 = nn.BatchNorm1d(ch)
        self.drop = nn.Dropout(dropout)

    def forward(self, x):
        y = self.drop(torch.relu(self.norm1(self.conv1(x))))
        y = self.norm2(self.conv2(y))
        return torch.relu(x + y)


class HitTCN(nn.Module):
    def __init__(self, in_ch, hidden=64, levels=4, dropout=0.1):
        super().__init__()
        self.register_buffer("feat_mean", torch.zeros(in_ch))
        self.register_buffer("feat_std", torch.ones(in_ch))
        self.inp = nn.Conv1d(in_ch, hidden, 1)
        self.blocks = nn.Sequential(*[_Block(hidden, 2 ** i, dropout) for i in range(levels)])
        self.head = nn.Conv1d(hidden, 2, 1)

    def forward(self, x):
        x = (x - self.feat_mean) / self.feat_std.clamp_min(1e-6)
        h = self.blocks(torch.relu(self.inp(x.transpose(1, 2))))
        out = self.head(h)
        return out[:, 0], out[:, 1]


def meta_path(weights_path):
    return os.path.splitext(str(weights_path))[0] + ".json"


def save_hit_model(model, meta, weights_path):
    torch.save(model.state_dict(), str(weights_path))
    with open(meta_path(weights_path), "w", encoding="utf-8") as fh:
        json.dump(meta, fh, indent=2)


def load_hit_model(weights_path, device="cpu"):
    sidecar = meta_path(weights_path)
    if not os.path.isfile(sidecar):
        raise HitModelError(f"hit-model sidecar not found: {sidecar}")
    with open(sidecar, encoding="utf-8") as fh:
        meta = json.load(fh)
    missing = [k for k in _REQUIRED_META if k not in meta]
    if missing:
        raise HitModelError(f"hit-model sidecar lacks {missing}")
    if (meta["schema"] != MODEL_SCHEMA or meta["feature_schema"] != FEATURE_SCHEMA
            or meta["feature_dim"] != FEATURE_DIM or float(meta["target_hz"]) != TARGET_HZ):
        raise HitModelError(
            f"incompatible hit model (schema {meta['schema']}, features {meta['feature_schema']}/"
            f"{meta['feature_dim']} at {meta['target_hz']} Hz)")
    model = HitTCN(meta["feature_dim"], int(meta["hidden"]), int(meta["levels"]), dropout=0.0)
    state = torch.load(str(weights_path), map_location=device, weights_only=True)
    model.load_state_dict(state)
    return model.to(device).eval(), meta


def file_sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()
```

- [ ] **Step 4: Run the tests to verify they pass.**

Run: `PYTHONUTF8=1 ./.venv/Scripts/python.exe -B -m pytest -q -p no:cacheprovider tests/test_hit_model.py`

Expected: 10 passed (the parametrised test counts 4).

- [ ] **Step 5: Commit.**

```bash
git add badminton_analysis/stroke/hit_model.py tests/test_hit_model.py
git commit -m "feat(hits): temporal CNN hit model with safe state-dict loading

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: The detector (`hit_detector.py`)

**Files:**
- Create: `badminton_analysis/stroke/hit_detector.py`
- Test: `tests/test_hit_detector.py`

**Interfaces:**
- Consumes:
  - `RawSignals` (Task 1)
  - `compute_features`, `TARGET_HZ` (Task 2)
  - a model that follows Task 4's forward contract: `(B,T,C) -> (B,T), (B,T)`
- Produces:
  - `MIN_SHUTTLE_COVERAGE = 0.5`
  - `shuttle_coverage(sig, in_play_frames) -> float`
  - `pick_peaks(scores, threshold, min_spacing) -> list[int]`
  - `predict_scores(model, feats, chunk=2048, context=64) -> (hit_prob (K,), far_prob (K,))`
  - `detect_hits(sig, model, meta) -> list[{"frame": int, "hitter": "lower"|"upper", "confidence": float}]`

- [ ] **Step 1: Write the failing tests.**

```python
# tests/test_hit_detector.py
import numpy as np
import torch

from badminton_analysis.stroke import hit_detector as hd
from badminton_analysis.stroke.hit_features import FEATURE_DIM
from badminton_analysis.stroke.hit_model import HitTCN
from tests.hit_fixtures import make_signals


def test_shuttle_coverage_counts_only_in_play_frames():
    sig = make_signals(n=100)
    sig.shuttle[:50] = np.nan
    assert hd.shuttle_coverage(sig, range(51, 101)) == 1.0
    assert hd.shuttle_coverage(sig, range(1, 101)) == 0.5
    assert hd.shuttle_coverage(sig, []) == 0.0
    assert hd.shuttle_coverage(sig, [500, 600]) == 0.0


def test_pick_peaks_threshold_spacing_and_plateau():
    s = np.zeros(40)
    s[5], s[7] = 0.9, 0.6          # 7 is within spacing of the stronger 5
    s[20] = 0.4                    # below threshold
    s[30] = s[31] = 0.8            # plateau -> one peak
    assert hd.pick_peaks(s, 0.5, 4) == [5, 31]
    assert hd.pick_peaks(np.array([]), 0.5, 4) == []


def test_chunked_inference_equals_whole_sequence():
    torch.manual_seed(0)
    model = HitTCN(FEATURE_DIM, hidden=16).eval()
    feats = np.random.default_rng(0).normal(size=(500, FEATURE_DIM)).astype(np.float32)
    whole = hd.predict_scores(model, feats, chunk=10**6, context=64)
    chunked = hd.predict_scores(model, feats, chunk=50, context=40)
    assert np.allclose(whole[0], chunked[0], atol=1e-5)
    assert np.allclose(whole[1], chunked[1], atol=1e-5)


def test_detect_hits_maps_steps_to_native_frames_at_60fps(monkeypatch):
    sig = make_signals(n=120, fps=60.0)
    hit = np.zeros(60)
    far = np.zeros(60)
    hit[10], far[10] = 0.9, 0.8     # step 10 -> native index 20 -> frame 21, far player
    hit[40], far[40] = 0.7, 0.1     # step 40 -> native index 80 -> frame 81, near player
    monkeypatch.setattr(hd, "predict_scores", lambda model, feats, **k: (hit, far))
    hits = hd.detect_hits(sig, object(), {"threshold": 0.5, "min_spacing_sec": 0.3})
    assert hits == [{"frame": 21, "hitter": "upper", "confidence": 0.9},
                    {"frame": 81, "hitter": "lower", "confidence": 0.7}]


def test_detect_hits_spacing_is_in_seconds(monkeypatch):
    sig = make_signals(n=120, fps=30.0)
    hit = np.zeros(120)
    hit[10], hit[16] = 0.9, 0.8     # 0.2 s apart
    monkeypatch.setattr(hd, "predict_scores", lambda model, feats, **k: (hit, np.zeros(120)))
    assert len(hd.detect_hits(sig, object(), {"threshold": 0.5, "min_spacing_sec": 0.3})) == 1
    assert len(hd.detect_hits(sig, object(), {"threshold": 0.5, "min_spacing_sec": 0.1})) == 2
```

- [ ] **Step 2: Run the tests to verify they fail.**

Run: `PYTHONUTF8=1 ./.venv/Scripts/python.exe -B -m pytest -q -p no:cacheprovider tests/test_hit_detector.py`

Expected: they fail with `ModuleNotFoundError`.

- [ ] **Step 3: Implement.**

```python
# badminton_analysis/stroke/hit_detector.py
"""Learned hit detection: features -> per-step scores -> peaks -> native-frame hits."""
import numpy as np
import torch

from .hit_features import TARGET_HZ, compute_features

MIN_SHUTTLE_COVERAGE = 0.5


def shuttle_coverage(sig, in_play_frames):
    """Fraction of in-play frames (1-based numbers) with a shuttle position."""
    lo, hi = sig.first_frame, sig.first_frame + sig.n_frames
    idx = np.asarray([int(f) - lo for f in in_play_frames if lo <= int(f) < hi], dtype=np.int64)
    if idx.size == 0:
        return 0.0
    return float(np.isfinite(sig.shuttle[idx]).all(axis=1).mean())


def pick_peaks(scores, threshold, min_spacing):
    """Local maxima >= threshold, strongest first, at least min_spacing steps apart."""
    s = np.asarray(scores, dtype=np.float64)
    if s.size == 0:
        return []
    left = np.r_[-np.inf, s[:-1]]
    right = np.r_[s[1:], -np.inf]
    cand = np.where((s >= threshold) & (s >= left) & (s > right))[0]
    kept = []
    for i in cand[np.argsort(-s[cand], kind="stable")]:
        if all(abs(int(i) - j) >= min_spacing for j in kept):
            kept.append(int(i))
    return sorted(kept)


def predict_scores(model, feats, chunk=2048, context=64):
    """Sigmoid hit and far-player probabilities per step, inferred in overlapping chunks."""
    k = len(feats)
    hit = np.zeros(k, dtype=np.float64)
    far = np.zeros(k, dtype=np.float64)
    device = next(model.parameters()).device
    model.eval()
    with torch.no_grad():
        for start in range(0, k, chunk):
            stop = min(k, start + chunk)
            lo, hi = max(0, start - context), min(k, stop + context)
            x = torch.from_numpy(np.ascontiguousarray(feats[lo:hi])).float().unsqueeze(0).to(device)
            h, f = model(x)
            a, b = start - lo, start - lo + (stop - start)
            hit[start:stop] = torch.sigmoid(h)[0, a:b].cpu().numpy()
            far[start:stop] = torch.sigmoid(f)[0, a:b].cpu().numpy()
    return hit, far


def detect_hits(sig, model, meta):
    feats, idx = compute_features(sig)
    hit, far = predict_scores(model, feats)
    spacing = max(1, int(round(float(meta["min_spacing_sec"]) * TARGET_HZ)))
    return [{"frame": sig.frame_number(idx[k]),
             "hitter": "upper" if far[k] >= 0.5 else "lower",
             "confidence": round(float(hit[k]), 4)}
            for k in pick_peaks(hit, float(meta["threshold"]), spacing)]
```

- [ ] **Step 4: Run the tests to verify they pass.**

Run: `PYTHONUTF8=1 ./.venv/Scripts/python.exe -B -m pytest -q -p no:cacheprovider tests/test_hit_detector.py`

Expected: 5 passed.

- [ ] **Step 5: Commit.**

```bash
git add badminton_analysis/stroke/hit_detector.py tests/test_hit_detector.py
git commit -m "feat(hits): peak-picking detector with chunked inference

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Metrics, the shuttle-turn baseline, and the evaluation harness

**Files:**
- Create: `badminton_analysis/stroke/hit_metrics.py`
- Create: `badminton_analysis/stroke/shuttle_turns.py`
- Create: `scripts/score_hits.py`
- Test: `tests/test_hit_metrics.py`, `tests/test_score_hits.py`

**Interfaces:**
- Consumes: `load_signals` (Task 1)
- Produces:
  - `match_events(truth, detected, tol) -> list[(i, j)]`: one-to-one, closest first
  - `summarize(truth, detected, tol, truth_sides=None, det_sides=None) -> dict`, with keys
    `truth`, `detected`, `matched`, `recall`, `precision`, `f1`, `hitter_compared`,
    `hitter_correct`
  - `shuttle_turn_events(shuttle (T,2), fps, min_turn_deg=45.0, lookahead_sec=0.05, min_gap_sec=0.25) -> list[int]`
  - From `scripts/score_hits.py`:
    - `parse_labels(text) -> (spans {n: (start, end)}, hits {n: [{"t","player","stroke"}]}, unusable [(a, b)])`
    - `run_hits(run_dir, span_start) -> list[{"t","side"}]`
    - `baseline_hits(run_dir, span_start) -> list[{"t","side": None}]`
    - `score(labels_text, runs_dir, suffix, tol=0.2, baseline=False) -> dict`
    - `DONE_BAR = {"recall": 0.60, "precision": 0.70, "hitter": 0.85}`
    - `meets_bar(summary) -> bool`
  - `hits.json` times: `time_sec = (frame - 1) / fps`, relative to the clip. The source time is
    `span_start + time_sec`.

- [ ] **Step 1: Write the failing tests.**

```python
# tests/test_hit_metrics.py
import numpy as np

from badminton_analysis.stroke.hit_metrics import match_events, summarize
from badminton_analysis.stroke.shuttle_turns import shuttle_turn_events
from tests.hit_fixtures import make_signals


def test_match_is_one_to_one_and_closest_first():
    pairs = match_events([1.0, 1.1], [1.05], tol=0.2)
    assert pairs == [(0, 0)] or pairs == [(1, 0)]
    assert len(pairs) == 1
    assert match_events([1.0], [1.5], tol=0.2) == []


def test_summarize_rates_and_hitter_accuracy():
    s = summarize([1.0, 2.0, 3.0, 4.0], [1.05, 2.1, 5.0], tol=0.2,
                  truth_sides=["near", "far", "near", "far"], det_sides=["near", "near", "far"])
    assert s["matched"] == 2 and s["truth"] == 4 and s["detected"] == 3
    assert s["recall"] == 0.5 and abs(s["precision"] - 2 / 3) < 1e-9
    assert s["hitter_compared"] == 2 and s["hitter_correct"] == 0.5


def test_summarize_empty_inputs_are_safe():
    s = summarize([], [], tol=0.2)
    assert s["recall"] == 0.0 and s["precision"] == 0.0 and s["hitter_correct"] is None


def test_shuttle_turns_find_triangle_wave_corners():
    sig = make_signals(n=150, fps=30.0)
    events = shuttle_turn_events(sig.shuttle, 30.0)
    assert any(abs(e - 30) <= 2 for e in events) and any(abs(e - 90) <= 2 for e in events)


def test_shuttle_turns_skip_missing_points():
    shuttle = np.full((50, 2), np.nan)
    assert shuttle_turn_events(shuttle, 30.0) == []
```

```python
# tests/test_score_hits.py
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from score_hits import meets_bar, parse_labels, score  # noqa: E402

LABELS = """# sheet
### rally1.mp4 (280.5 s – 301 s)

| hit_sec | player | stroke | notes |
|---|---|---|---|
| 281.00 | near | serve | |
| 282.00 | far | lift | |
| 283.00 | near |  | |

### rally2.mp4 (527.5 s – 546.5 s)

| hit_sec | player | stroke | notes |
|---|---|---|---|
|  |  |  |  |

## Unusable spans

| from_sec | to_sec | why |
|---|---|---|
| 282.9 | 283.2 | blocked |
"""


def test_parse_labels_spans_hits_and_unusable():
    spans, hits, unusable = parse_labels(LABELS)
    assert spans == {1: (280.5, 301.0), 2: (527.5, 546.5)}
    assert [h["t"] for h in hits[1]] == [281.0, 282.0, 283.0]
    assert hits[1][1]["player"] == "far" and hits[1][1]["stroke"] == "lift"
    assert hits[2] == []
    assert unusable == [(282.9, 283.2)]


def _run(tmp_path, suffix, hits, fps=60.0):
    d = tmp_path / f"rally1_{suffix}"
    d.mkdir()
    (d / "hits.json").write_text(json.dumps({
        "source": "learned", "fps": fps,
        "hits": [{"frame": f, "time_sec": (f - 1) / fps, "hitter": h} for f, h in hits]}))


def test_score_excludes_unusable_and_checks_hitter(tmp_path):
    # 281.0 -> clip frame 31 at 60 fps (0.5 s); 282.0 -> frame 91; 283.0 is unusable
    _run(tmp_path, "learned", [(31, "lower"), (91, "lower"), (151, "lower")])
    result = score(LABELS, str(tmp_path), "learned", tol=0.2)
    s = result["summary"]
    assert s["truth"] == 2 and s["matched"] == 2 and s["detected"] == 2
    assert s["hitter_correct"] == 0.5
    assert meets_bar(s) is False


def test_meets_bar():
    assert meets_bar({"recall": 0.6, "precision": 0.7, "hitter_correct": 0.85})
    assert not meets_bar({"recall": 0.59, "precision": 0.9, "hitter_correct": 0.9})
    assert not meets_bar({"recall": 0.9, "precision": 0.9, "hitter_correct": None})
```

- [ ] **Step 2: Run the tests to verify they fail.**

Run: `PYTHONUTF8=1 ./.venv/Scripts/python.exe -B -m pytest -q -p no:cacheprovider tests/test_hit_metrics.py tests/test_score_hits.py`

Expected: they fail with `ModuleNotFoundError`.

- [ ] **Step 3: Implement the metrics and turn events.**

```python
# badminton_analysis/stroke/hit_metrics.py
"""One-to-one hit matching within a time tolerance, plus recall/precision/hitter rates."""
import bisect


def match_events(truth, detected, tol):
    det_sorted = sorted(range(len(detected)), key=lambda j: detected[j])
    det_times = [detected[j] for j in det_sorted]
    cands = []
    for i, t in enumerate(truth):
        lo = bisect.bisect_left(det_times, t - tol)
        hi = bisect.bisect_right(det_times, t + tol)
        for pos in range(lo, hi):
            j = det_sorted[pos]
            cands.append((abs(detected[j] - t), i, j))
    cands.sort()
    used_t, used_d, pairs = set(), set(), []
    for _, i, j in cands:
        if i not in used_t and j not in used_d:
            used_t.add(i)
            used_d.add(j)
            pairs.append((i, j))
    return sorted(pairs)


def summarize(truth, detected, tol, truth_sides=None, det_sides=None):
    pairs = match_events(list(truth), list(detected), tol)
    n_t, n_d, n_m = len(truth), len(detected), len(pairs)
    recall = n_m / n_t if n_t else 0.0
    precision = n_m / n_d if n_d else 0.0
    f1 = 2 * recall * precision / (recall + precision) if recall + precision else 0.0
    compared = correct = 0
    if truth_sides is not None and det_sides is not None:
        for i, j in pairs:
            if truth_sides[i] and det_sides[j]:
                compared += 1
                correct += truth_sides[i] == det_sides[j]
    return {"truth": n_t, "detected": n_d, "matched": n_m, "recall": recall,
            "precision": precision, "f1": f1, "hitter_compared": compared,
            "hitter_correct": (correct / compared) if compared else None}
```

```python
# badminton_analysis/stroke/shuttle_turns.py
"""Frames where the shuttle path turns sharply.

Approach A of the hit-detection design: the free baseline every learned model is
compared against, and the signal the ShuttleSet alignment check correlates with
labelled hit times. Not used by the match pipeline's hit list.
"""
import numpy as np


def shuttle_turn_events(shuttle, fps, min_turn_deg=45.0, lookahead_sec=0.05, min_gap_sec=0.25):
    pts = np.asarray(shuttle, dtype=np.float64)
    n = len(pts)
    look = max(1, int(round(lookahead_sec * fps)))
    gap = max(1, int(round(min_gap_sec * fps)))
    events, last = [], -10 ** 9
    for i in range(1, n - look):
        a, b, c = pts[i - 1], pts[i], pts[i + look]
        if not (np.isfinite(a).all() and np.isfinite(b).all() and np.isfinite(c).all()):
            continue
        v1, v2 = b - a, c - b
        n1, n2 = np.linalg.norm(v1), np.linalg.norm(v2)
        if n1 < 1e-6 or n2 < 1e-6:
            continue
        angle = np.degrees(np.arccos(np.clip(np.dot(v1, v2) / (n1 * n2), -1.0, 1.0)))
        if angle >= min_turn_deg and i - last >= gap:
            events.append(i)
            last = i
    return events
```

- [ ] **Step 4: Implement the harness.**

```python
# scripts/score_hits.py
"""Score hit detection against a LABELS.md (outputs/axelsen-hit-labels format).

Usage:
  python scripts/score_hits.py --labels outputs/axelsen-hit-labels/LABELS.md \
      --runs-dir outputs/axelsen-hit-labels/runs --suffix learned [--tol 0.2] \
      [--baseline] [--json result.json]

Clip N's run lives at <runs-dir>/rally<N>_<suffix>/ and must contain hits.json
(and hit_signals.npz for --baseline, which scores shuttle-path turns instead).
Clip spans come from the "### rallyN.mp4 (START s - END s)" headings; a clip's
time t maps to source time START + t. Labels inside "Unusable spans" are
excluded, as are detections there.
"""
import argparse
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from badminton_analysis.stroke.hit_metrics import summarize  # noqa: E402

DONE_BAR = {"recall": 0.60, "precision": 0.70, "hitter": 0.85}
_HEADING = re.compile(r"^###\s+rally(\d+)\.mp4\s+\(([\d.]+)\s*s\s*[–-]\s*([\d.]+)\s*s\)")
_SIDE = {"lower": "near", "upper": "far"}


def parse_labels(text):
    spans, hits, unusable = {}, {}, []
    section = None
    for line in text.splitlines():
        m = _HEADING.match(line.strip())
        if m:
            section = int(m.group(1))
            spans[section] = (float(m.group(2)), float(m.group(3)))
            hits[section] = []
            continue
        if line.startswith("## "):
            section = "unusable" if line.lower().startswith("## unusable") else None
            continue
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if not cells or not re.match(r"^\d", cells[0]):
            continue
        if section == "unusable":
            if len(cells) > 1 and re.match(r"^\d", cells[1]):
                unusable.append((float(cells[0]), float(cells[1])))
        elif isinstance(section, int):
            hits[section].append({"t": float(cells[0]),
                                  "player": (cells[1] if len(cells) > 1 else "").lower() or None,
                                  "stroke": (cells[2] if len(cells) > 2 else "").lower() or None})
    return spans, hits, unusable


def run_hits(run_dir, span_start):
    with open(os.path.join(run_dir, "hits.json"), encoding="utf-8") as fh:
        payload = json.load(fh)
    fps = float(payload["fps"])
    return [{"t": span_start + (h["time_sec"] if h.get("time_sec") is not None
                                else (h["frame"] - 1) / fps),
             "side": _SIDE.get(h.get("hitter"))} for h in payload["hits"]]


def baseline_hits(run_dir, span_start):
    from badminton_analysis.stroke.hit_signals import load_signals
    from badminton_analysis.stroke.shuttle_turns import shuttle_turn_events

    sig = load_signals(os.path.join(run_dir, "hit_signals.npz"))
    return [{"t": span_start + i / sig.fps, "side": None}
            for i in shuttle_turn_events(sig.shuttle, sig.fps)]


def score(labels_text, runs_dir, suffix, tol=0.2, baseline=False):
    spans, hits, unusable = parse_labels(labels_text)

    def usable(t):
        return not any(a <= t <= b for a, b in unusable)

    truth, det, per_clip = [], [], {}
    for n, (start, end) in sorted(spans.items()):
        clip_truth = [h for h in hits.get(n, []) if usable(h["t"])]
        if not clip_truth:
            continue
        run_dir = os.path.join(runs_dir, f"rally{n}_{suffix}")
        found = baseline_hits(run_dir, start) if baseline else run_hits(run_dir, start)
        clip_det = [d for d in found if start <= d["t"] <= end and usable(d["t"])]
        per_clip[n] = summarize([h["t"] for h in clip_truth], [d["t"] for d in clip_det], tol,
                                [h["player"] for h in clip_truth], [d["side"] for d in clip_det])
        truth += clip_truth
        det += clip_det
    summary = summarize([h["t"] for h in truth], [d["t"] for d in det], tol,
                        [h["player"] for h in truth], [d["side"] for d in det])
    return {"suffix": suffix, "baseline": baseline, "tol": tol, "summary": summary,
            "per_clip": per_clip}


def meets_bar(summary):
    hitter = summary.get("hitter_correct")
    return (summary["recall"] >= DONE_BAR["recall"] and summary["precision"] >= DONE_BAR["precision"]
            and hitter is not None and hitter >= DONE_BAR["hitter"])


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--labels", required=True)
    p.add_argument("--runs-dir", required=True)
    p.add_argument("--suffix", required=True)
    p.add_argument("--tol", type=float, default=0.2)
    p.add_argument("--baseline", action="store_true")
    p.add_argument("--json")
    a = p.parse_args(argv)
    with open(a.labels, encoding="utf-8") as fh:
        result = score(fh.read(), a.runs_dir, a.suffix, a.tol, a.baseline)
    s = result["summary"]
    hitter = "n/a" if s["hitter_correct"] is None else f"{s['hitter_correct']:.2f}"
    print(f"{a.suffix}{' (baseline)' if a.baseline else ''}: truth {s['truth']} detected "
          f"{s['detected']} matched {s['matched']} recall {s['recall']:.2f} precision "
          f"{s['precision']:.2f} hitter {hitter} -> done bar {'MET' if meets_bar(s) else 'not met'}")
    for n, c in result["per_clip"].items():
        print(f"  clip {n}: truth {c['truth']} det {c['detected']} matched {c['matched']}")
    if a.json:
        with open(a.json, "w", encoding="utf-8") as fh:
            json.dump(result, fh, indent=2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 5: Run the tests to verify they pass.**

Run: `PYTHONUTF8=1 ./.venv/Scripts/python.exe -B -m pytest -q -p no:cacheprovider tests/test_hit_metrics.py tests/test_score_hits.py`

Expected: 8 passed.

- [ ] **Step 6: Commit.**

```bash
git add badminton_analysis/stroke/hit_metrics.py badminton_analysis/stroke/shuttle_turns.py scripts/score_hits.py tests/test_hit_metrics.py tests/test_score_hits.py
git commit -m "feat(hits): hit metrics, shuttle-turn baseline and LABELS.md scoring harness

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Whole-video TrackNetV3, its mode, and the progress heartbeat

**Files:**
- Modify: `third_party/tracknet/infer.py` (`run_prediction`)
- Modify: `third_party/tracknet/CONTRACT.md`
- Modify: `badminton_analysis/shuttle_track/tracknet.py:83-97` (`track_video`)
- Modify: `badminton_analysis/system.py`:
  - line 13 constant;
  - `__init__` signature (239–250) and body (~283);
  - `_PROGRESS_STAGE_PCT` (~1477);
  - `_run_shuttle_pretrack` (1250–1293);
  - a new module-level `_ProgressHeartbeat`.
- Modify: `static/kestrel.js:952-963`
- Modify: `tests/test_tracknet_integration.py`. Replace
  `test_pretrack_skips_video_over_frame_budget`.
- Test: `tests/test_tracknet_whole_video.py`

**Interfaces:**
- Produces:
  - `run_prediction(..., progress=None)`, where `progress(done_frames: int, total_frames: int)`
    is called after each TrackNet batch;
  - `track_video(video_path, models, court_roi=None, eval_mode="weight", progress=None)`;
  - `TRACKNET_MODES = ("weight", "nonoverlap")` in `system.py`;
  - `BadmintonAnalysisSystem(..., tracknet_mode="weight")` and
    `BadmintonAnalysisSystem._normalize_tracknet_mode(mode) -> str` (a staticmethod);
  - `_ProgressHeartbeat(write, stage, interval=2.0)`, a context manager whose `.update(done, total=None)` records progress;
  - progress stage `"tracking_shuttle"`, at a fixed 3%.

- [ ] **Step 1: Write the failing tests.**

```python
# tests/test_tracknet_whole_video.py
import inspect
import json
import re
import time
from pathlib import Path

import badminton_analysis.system as sysmod
from badminton_analysis.shuttle_track import tracknet as tnmod
from badminton_analysis.shuttle_track import trajectory as tjmod

ROOT = Path(__file__).resolve().parents[1]


def _system(tmp_path, **kw):
    s = sysmod.BadmintonAnalysisSystem.__new__(sysmod.BadmintonAnalysisSystem)
    s.analyze_technique = True
    s.tracknet_weights = "tn.pt"
    s.inpaintnet_weights = None
    s.tracknet_mode = kw.get("tracknet_mode", "weight")
    s._shuttle_trajectory = None
    s._shuttle_source = "yolo"
    s.save_dir = str(tmp_path)
    s.video_path = str(tmp_path / "v.mp4")
    s.court_roi_corners = [(0, 0), (1000, 1000)]
    s._total_frames = 64085
    (tmp_path / "v.mp4").write_bytes(b"x")     # cache_key may stat the video
    return s


def test_long_video_is_tracked_not_skipped(tmp_path, monkeypatch):
    s = _system(tmp_path)
    calls = {}
    monkeypatch.setattr(tnmod, "load_tracknet", lambda *a, **k: {"tracknet_file": "tn.pt"})

    def fake_track(path, models, court_roi=None, eval_mode="weight", progress=None):
        calls["mode"] = eval_mode
        progress(500, 64085)
        return {0: (1.0, 2.0)}

    monkeypatch.setattr(tnmod, "track_video", fake_track)
    s._run_shuttle_pretrack()
    assert s._shuttle_source == "tracknet" and calls["mode"] == "weight"
    assert not hasattr(sysmod, "SHUTTLE_PRETRACK_MAX_FRAMES")


def test_mode_reaches_tracker_and_cache_key(tmp_path, monkeypatch):
    s = _system(tmp_path, tracknet_mode="nonoverlap")
    seen = {}
    real_key = tjmod.cache_key

    def spy_key(video, params):
        seen.update(params)
        return real_key(video, params)

    monkeypatch.setattr(tjmod, "cache_key", spy_key)
    monkeypatch.setattr(tnmod, "load_tracknet", lambda *a, **k: {"tracknet_file": "tn.pt"})
    monkeypatch.setattr(tnmod, "track_video",
                        lambda *a, eval_mode="weight", progress=None, **k: (seen.setdefault("tracked", eval_mode), {})[1])
    s._run_shuttle_pretrack()
    assert seen["eval_mode"] == "nonoverlap" and seen["tracked"] == "nonoverlap"


def test_progress_reaches_progress_json(tmp_path, monkeypatch):
    s = _system(tmp_path)
    monkeypatch.setattr(tnmod, "load_tracknet", lambda *a, **k: {"tracknet_file": "tn.pt"})

    def fake_track(path, models, court_roi=None, eval_mode="weight", progress=None):
        progress(1234, 64085)
        return {}

    monkeypatch.setattr(tnmod, "track_video", fake_track)
    s._run_shuttle_pretrack()
    prog = json.loads((tmp_path / "progress.json").read_text())
    assert prog["stage"] == "tracking_shuttle" and prog["current_frame"] == 1234
    assert prog["pct"] == 3


def test_heartbeat_rewrites_while_running():
    writes = []
    with sysmod._ProgressHeartbeat(lambda stage, current_frame=None: writes.append((stage, current_frame)),
                                   "tracking_shuttle", interval=0.01) as hb:
        hb.update(10)
        time.sleep(0.08)
        hb.update(20)
        time.sleep(0.05)
    assert len(writes) >= 4
    assert writes[0] == ("tracking_shuttle", 0) and writes[-1] == ("tracking_shuttle", 20)


def test_normalize_tracknet_mode():
    norm = sysmod.BadmintonAnalysisSystem._normalize_tracknet_mode
    assert norm("nonoverlap") == "nonoverlap" and norm("weight") == "weight"
    assert norm("bogus") == "weight" and norm(None) == "weight"


def test_infer_has_progress_parameter_documented_in_contract():
    import sys
    sys.path.insert(0, str(ROOT / "third_party" / "tracknet"))
    with tnmod.vendored_imports():
        from infer import run_prediction
    assert "progress" in inspect.signature(run_prediction).parameters
    assert "progress" in (ROOT / "third_party" / "tracknet" / "CONTRACT.md").read_text(encoding="utf-8")


def test_kestrel_lists_tracking_stage_between_calibration_and_analysis():
    js = (ROOT / "static" / "kestrel.js").read_text(encoding="utf-8")
    assert re.search(r"tracking_shuttle:\s*\['[^']+',\s*'Tracking shuttle'\]", js)
    assert "'calibrating_court_view','tracking_shuttle','analyzing'" in js
```

> Note: `test_infer_has_progress_parameter_documented_in_contract` follows the vendoring
> pattern in `tests/test_tracknet_vendor.py`. If `vendored_imports()` already puts the
> vendored directory on `sys.path`, drop the `sys.path.insert` line.

- [ ] **Step 2: Replace the obsolete budget test** in `tests/test_tracknet_integration.py`.
  Delete `test_pretrack_skips_video_over_frame_budget` (lines ~83–114) entirely. Its replacement
  is `test_long_video_is_tracked_not_skipped` above.

- [ ] **Step 3: Run the tests to verify they fail.**

Run: `PYTHONUTF8=1 ./.venv/Scripts/python.exe -B -m pytest -q -p no:cacheprovider tests/test_tracknet_whole_video.py tests/test_tracknet_integration.py`

Expected: the new tests fail (no `_ProgressHeartbeat`, no `progress` parameter, the
`SHUTTLE_PRETRACK_MAX_FRAMES` constant still present). The existing integration tests pass.

- [ ] **Step 4: Add the progress callback to the vendored inference.**
  - In `third_party/tracknet/infer.py`, change the signature to

    ```python
    def run_prediction(video_file, tracknet_file, inpaintnet_file='', batch_size=16,
                       eval_mode='weight', large_video=True, max_sample_num=1800,
                       video_range=None, device=None, progress=None):
    ```

  - Add to the docstring Args:

    ```
    progress (callable, optional): called as progress(done_frames, total_frames)
        after each TrackNet batch. Project addition, see CONTRACT.md.
    ```

  - In the `nonoverlap` branch, right after the `data_loader` is created in both the
    `large_video` and non-`large_video` sub-branches, set
    `total_frames = dataset.video_len if large_video else len(frame_list)`.
  - In the overlap (`else`) branch, set `total_frames = video_len` after `video_len` is known.
  - Then, at the end of the body of **each** TrackNet `for step, (i, x) in enumerate(tqdm(data_loader)):`
    loop (two places: after `tracknet_pred_dict[key].extend(...)` in nonoverlap, and after
    `y_pred_buffer = y_pred_buffer[-buffer_size:]` in the overlap branch), add:

    ```python
                if progress is not None:
                    progress(len(tracknet_pred_dict['Frame']), total_frames)
    ```

- [ ] **Step 5: Record the deviation.** Append a bullet to the "infer.py edits vs upstream"
  list in `third_party/tracknet/CONTRACT.md`:

```markdown
- `run_prediction(..., progress=None)`: optional callback `progress(done_frames, total_frames)`
  called after each TrackNet batch (both eval modes), so the match pipeline can show a live
  "Tracking shuttle" stage during a whole-match pass (learned-hit-detection spec 8.1). No
  effect on predictions.
```

- [ ] **Step 6: Pass the mode and the callback through.** In
  `badminton_analysis/shuttle_track/tracknet.py`, replace `track_video` with:

```python
def track_video(video_path, models, court_roi=None, eval_mode="weight", progress=None):
    pred = _run_prediction(
        video_file=str(video_path),
        tracknet_file=models["tracknet_file"],
        inpaintnet_file=models.get("inpaintnet_file") or "",
        device=models.get("device"),
        eval_mode=eval_mode,
        progress=progress,
    )
    traj = {}
    for f, x, y, vis in zip(pred["Frame"], pred["X"], pred["Y"], pred["Visibility"]):
        if not vis:
            traj[int(f)] = None
            continue
        fx, fy = float(x), float(y)
        traj[int(f)] = (fx, fy) if _in_roi(fx, fy, court_roi) else None
    return traj
```

Then check that the existing wrapper tests still pass:
`PYTHONUTF8=1 ./.venv/Scripts/python.exe -B -m pytest -q -p no:cacheprovider tests/test_tracknet_wrapper.py`.
If a test stubs `_run_prediction` with a fixed keyword list, widen that stub to `**kwargs`.

- [ ] **Step 7: Make the system changes** in `badminton_analysis/system.py`:

  1. Replace the comment block and `SHUTTLE_PRETRACK_MAX_FRAMES = 2000` (lines 8–13) with:

     ```python
     # TrackNetV3 dense pre-pass modes. 'weight' (upstream default, temporal
     # ensemble) costs ~0.48 s/frame at 1080p on an RTX 4090; 'nonoverlap' ~0.18 s/frame.
     # The whole video is tracked whenever TrackNetV3 weights are present
     # (learned-hit-detection spec 8.1); the cache makes re-runs free.
     TRACKNET_MODES = ("weight", "nonoverlap")
     ```

  2. Add `import threading` to the module imports.

  3. Add the module-level heartbeat class next to the other module-level helpers, before the
     `BadmintonAnalysisSystem` class:

     ```python
     class _ProgressHeartbeat:
         """Rewrites progress.json every ``interval`` s from a background thread.

         A whole-match TrackNetV3 pass runs for hours with batches that can take longer
         than the UI's staleness window; this keeps "updated Ns ago" fresh and carries the
         latest frame count, independent of how often the tracker reports.
         """

         def __init__(self, write, stage, interval=2.0):
             self._write = write
             self._stage = stage
             self._interval = interval
             self.current = 0
             self._stop = threading.Event()
             self._thread = None

         def update(self, done, total=None):
             self.current = int(done)

         def __enter__(self):
             self._write(self._stage, current_frame=0)
             self._thread = threading.Thread(target=self._run, daemon=True)
             self._thread.start()
             return self

         def _run(self):
             while not self._stop.wait(self._interval):
                 self._write(self._stage, current_frame=self.current)

         def __exit__(self, *exc):
             self._stop.set()
             if self._thread is not None:
                 self._thread.join(timeout=5)
             self._write(self._stage, current_frame=self.current)
             return False
     ```

  4. In `__init__`:
     - add the keyword `tracknet_mode="weight"` after `inpaintnet_weights=None`;
     - after `self.inpaintnet_weights = inpaintnet_weights`, add
       `self.tracknet_mode = self._normalize_tracknet_mode(tracknet_mode)`;
     - add the staticmethod:

     ```python
         @staticmethod
         def _normalize_tracknet_mode(mode):
             if mode in TRACKNET_MODES:
                 return mode
             if mode is not None:
                 print(f"Unknown TrackNetV3 mode {mode!r}; using 'weight'.")
             return "weight"
     ```

  5. Add `"tracking_shuttle": 3,` to `_PROGRESS_STAGE_PCT`.

  6. Replace the body of `_run_shuttle_pretrack`, keeping the docstring but editing its last
     sentence to "the whole video is tracked; progress stage `tracking_shuttle`":

     ```python
             if not (self.tracknet_weights and self.analyze_technique):
                 return
             mode = getattr(self, "tracknet_mode", "weight")
             try:
                 from .shuttle_track import tracknet as tnmod
                 from .shuttle_track import trajectory as tjmod

                 params = {"eval_mode": mode,
                           "inpaint": bool(self.inpaintnet_weights),
                           "roi": self.court_roi_corners}
                 cache_path = os.path.join(self.save_dir, "shuttle_trajectory.json")
                 key = tjmod.cache_key(self.video_path, params)
                 traj0 = tjmod.load_cache(cache_path, key)
                 if traj0 is None:
                     models = tnmod.load_tracknet(self.tracknet_weights, self.inpaintnet_weights)
                     with _ProgressHeartbeat(self._write_progress, "tracking_shuttle") as beat:
                         traj0 = tnmod.track_video(self.video_path, models,
                                                   court_roi=self.court_roi_corners,
                                                   eval_mode=mode, progress=beat.update)
                     tjmod.save_cache(cache_path, key, traj0)
                 self._shuttle_trajectory = {f + 1: pt for f, pt in traj0.items()}
                 self._shuttle_source = "tracknet"
                 detected = sum(1 for pt in traj0.values() if pt is not None)
                 print(f"Dense shuttle tracking ({mode}): {detected}/{len(traj0)} frames via TrackNetV3")
             except Exception as e:  # never fatal
                 print(f"TrackNetV3 pre-pass unavailable ({e}); using yolo shuttle.")
                 self._shuttle_trajectory = None
                 self._shuttle_source = "yolo"
     ```

- [ ] **Step 8: Add the Kestrel stage.** In `static/kestrel.js`:
  - add `tracking_shuttle: ['追踪羽毛球', 'Tracking shuttle'],` to `STAGE_LABELS`, after the
    `calibrating_court_view` entry;
  - change the match-mode stage list to
    `['initializing','court_setup','calibrating_court_view','tracking_shuttle','analyzing','visualizing','encoding','done']`.

- [ ] **Step 9: Run the tests to verify they pass.**

Run: `PYTHONUTF8=1 ./.venv/Scripts/python.exe -B -m pytest -q -p no:cacheprovider tests/test_tracknet_whole_video.py tests/test_tracknet_integration.py tests/test_tracknet_wrapper.py tests/test_tracknet_infer.py tests/test_trajectory_cache.py tests/test_court_view_gate.py tests/test_progress_heartbeat.py`

Expected: all pass.

- [ ] **Step 10: Commit.**

```bash
git add third_party/tracknet/infer.py third_party/tracknet/CONTRACT.md badminton_analysis/shuttle_track/tracknet.py badminton_analysis/system.py static/kestrel.js tests/test_tracknet_whole_video.py tests/test_tracknet_integration.py
git commit -m "feat(tracking): whole-video TrackNetV3 with a mode switch and live progress

Removes the 2000-frame budget; adds a tracking_shuttle progress stage with a
background heartbeat and the TrackNetV3 eval mode in the trajectory cache key.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: Pipeline integration (shared hit list, `hits.json`, fallbacks)

**Files:**
- Modify: `badminton_analysis/system.py`:
  - `__init__` (add `hit_model_path`);
  - `process_video` (467–482);
  - `_write_rally_segments` (end, around 741);
  - `_run_technique_analysis` (1295–1310);
  - `_run_stroke_recognition` (1312–1385);
  - `TechniqueAnalysisRunner.run` (1674–1703);
  - new methods `_run_hit_detection`, `_in_play_frames`, `_write_hits_json`.
- Modify: `badminton_analysis/stroke_recog/recognizer.py:51-95` (`label_rally`)
- Test: `tests/test_hit_integration.py`

**Interfaces:**
- Consumes:
  - `build_signals`, `save_signals` (Task 1)
  - `shuttle_coverage`, `detect_hits`, `MIN_SHUTTLE_COVERAGE` (Task 5)
  - `load_hit_model`, `file_sha256` (Task 4)
  - `stroke_recog.hits.hit_events` (existing)
- Produces:
  - `BadmintonAnalysisSystem(..., hit_model_path=None)`
  - `_run_hit_detection(fps, last_frame) -> list | None`. It returns the learned hits, or None
    for the contact rule.
  - `self.rally_list`, the rallies written to `rally_segments.json`
  - `StrokeRecognizer.label_rally(track, frame_lookup, court_corners, video_wh, hits=None)`
  - `TechniqueAnalysisRunner.run(track, frame_lookup, hits=None)`
  - `_run_technique_analysis(hits=None)`, `_run_stroke_recognition(hits=None)`
  - The `hits.json` schema, as in spec §8.3.

- [ ] **Step 1: Write the failing tests.**

```python
# tests/test_hit_integration.py
import json
import os

import numpy as np

from badminton_analysis import system as system_mod
from badminton_analysis.data.writer import write_json
from badminton_analysis.stroke import hit_detector as hd
from badminton_analysis.stroke import hit_model as hm
from badminton_analysis.stroke_recog import recognizer as recognizer_mod
from badminton_analysis.stroke_recog.recognizer import StrokeRecognizer
from badminton_analysis.system import BadmintonAnalysisSystem, TechniqueAnalysisRunner
from tests.hit_fixtures import CORNERS, pose_at

N = 90


def _system(tmp_path, monkeypatch, **kw):
    monkeypatch.setattr(system_mod, "write_json", write_json, raising=False)
    s = object.__new__(BadmintonAnalysisSystem)
    s.analyze_technique = kw.get("analyze_technique", True)
    s.hit_model_path = kw.get("hit_model_path", "weights/hit-detector.pt")
    s.bst_weights = None
    s.save_dir = str(tmp_path)
    s.court_corners = CORNERS.tolist()
    s.frame_width, s.frame_height = 1280, 720
    s._shuttle_source = kw.get("shuttle_source", "tracknet")
    traj = {f: (600.0 + f, 300.0) for f in range(1, N + 1)}
    if kw.get("sparse"):
        traj = {f: (traj[f] if f % 4 == 0 else None) for f in traj}
    s._shuttle_trajectory = traj if s._shuttle_source == "tracknet" else None
    s._analysis_track_both = [{"frame": f, "racket_lower": None, "racket_upper": None,
                               "shuttle": (600.0 + f, 300.0)} for f in range(1, N + 1)]
    s._analysis_track = []
    s._analysis_frames = {
        f: {"player_side": "lower",
            "players": {"lower": {"keypoints": pose_at((640, 560), 60.0)},
                        "upper": {"keypoints": pose_at((640, 250), 25.0)}}}
        for f in range(1, N + 1)}
    s.rally_list = [{"id": 1, "start_frame": 1, "end_frame": 60}]
    if "corners" in kw:
        s.court_corners = kw["corners"]
    return s


def _hits_json(tmp_path):
    return json.loads((tmp_path / "hits.json").read_text())


def test_no_model_falls_back_and_still_writes_signals(tmp_path, monkeypatch):
    s = _system(tmp_path, monkeypatch, hit_model_path=None)
    assert s._run_hit_detection(30.0, N) is None
    payload = _hits_json(tmp_path)
    assert payload["source"] == "contact_rule" and payload["reason"] == "no hit model"
    assert (tmp_path / "hit_signals.npz").is_file()


def test_yolo_shuttle_falls_back(tmp_path, monkeypatch):
    s = _system(tmp_path, monkeypatch, shuttle_source="yolo")
    assert s._run_hit_detection(30.0, N) is None
    assert _hits_json(tmp_path)["reason"] == "no dense shuttle track"


def test_sparse_dense_track_falls_back_with_coverage(tmp_path, monkeypatch):
    s = _system(tmp_path, monkeypatch, sparse=True)
    assert s._run_hit_detection(30.0, N) is None
    payload = _hits_json(tmp_path)
    assert payload["source"] == "contact_rule" and "covers 25%" in payload["reason"]
    assert payload["shuttle_coverage_in_play"] == 0.25


def test_model_failure_falls_back(tmp_path, monkeypatch):
    s = _system(tmp_path, monkeypatch)
    monkeypatch.setattr(hm, "load_hit_model", lambda *a, **k: (_ for _ in ()).throw(hm.HitModelError("bad")))
    assert s._run_hit_detection(30.0, N) is None
    assert _hits_json(tmp_path)["reason"] == "hit detector failed: HitModelError"


def test_degenerate_corners_fall_back_not_crash(tmp_path, monkeypatch):
    s = _system(tmp_path, monkeypatch, corners=[[0, 0], [10, 0], [20, 0], [30, 0]])
    monkeypatch.setattr(hm, "load_hit_model", lambda *a, **k: (object(), {"threshold": 0.5, "min_spacing_sec": 0.3, "schema": 1}))
    monkeypatch.setattr(hm, "file_sha256", lambda p: "0" * 64)
    assert s._run_hit_detection(30.0, N) is None
    assert _hits_json(tmp_path)["reason"].startswith("hit detector failed: ValueError")


def test_learned_hits_written_with_provenance_time_and_rally(tmp_path, monkeypatch):
    s = _system(tmp_path, monkeypatch)
    monkeypatch.setattr(hm, "load_hit_model", lambda *a, **k: (object(), {"threshold": 0.4, "min_spacing_sec": 0.3, "schema": 1}))
    monkeypatch.setattr(hm, "file_sha256", lambda p: "a" * 64)
    learned = [{"frame": 31, "hitter": "upper", "confidence": 0.9},
               {"frame": 80, "hitter": "lower", "confidence": 0.7}]
    monkeypatch.setattr(hd, "detect_hits", lambda sig, model, meta: learned)
    assert s._run_hit_detection(30.0, N) == learned
    payload = _hits_json(tmp_path)
    assert payload["source"] == "learned" and payload["reason"] is None
    assert payload["model"] == {"sha256": "a" * 64, "schema": 1, "threshold": 0.4, "min_spacing_sec": 0.3}
    assert payload["hits"][0] == {"frame": 31, "time_sec": 1.0, "hitter": "upper",
                                  "confidence": 0.9, "rally_id": 1}
    assert payload["hits"][1]["rally_id"] is None


def test_fast_mode_removes_stale_hit_outputs(tmp_path, monkeypatch):
    (tmp_path / "hits.json").write_text("{}")
    (tmp_path / "hit_signals.npz").write_bytes(b"old")
    s = _system(tmp_path, monkeypatch, analyze_technique=False)
    assert s._run_hit_detection(30.0, N) is None
    assert not (tmp_path / "hits.json").exists()
    assert not (tmp_path / "hit_signals.npz").exists()


def test_recognizer_uses_injected_hits(monkeypatch):
    rec = StrokeRecognizer("w.pt")
    rec._load_attempted, rec._model = True, object()
    monkeypatch.setattr(recognizer_mod, "hit_events", lambda track: (_ for _ in ()).throw(AssertionError("contact rule used")))
    monkeypatch.setattr(recognizer_mod, "build_inputs", lambda *a, **k: None)
    out = rec.label_rally([], lambda f: None, CORNERS.tolist(), (1280, 720),
                          hits=[{"frame": 40, "hitter": "upper", "confidence": 0.8}])
    assert out == [{"frame": 40, "hitter": "upper", "stroke": "uncertain", "confidence": 0.0,
                    "uncertain": True, "hit_confidence": 0.8}]


def test_technique_runner_uses_injected_hits(monkeypatch):
    import badminton_analysis.stroke.events as events

    monkeypatch.setattr(events, "detect_contacts", lambda *a, **k: (_ for _ in ()).throw(AssertionError("contact rule used")))
    runner = TechniqueAnalysisRunner.__new__(TechniqueAnalysisRunner)
    runner.window_pre, runner.window_post, runner.scale = 20, 15, None
    seen = []
    runner._build_classifier_window = lambda c, a, b, lookup: {"contact_index": 0}
    runner._window_frames = lambda a, b, lookup: []
    monkeypatch.setattr("badminton_analysis.stroke.classifier.classify_stroke", lambda w: ("smash", 0.9))
    runner.analyzer = type("A", (), {"analyze": lambda self, ev, frames: seen.append(ev) or {"ok": 1}})()
    reports, evs = runner.run([], lambda f: {"player_side": "lower"},
                              hits=[{"frame": 50, "hitter": "lower"}, {"frame": 10, "hitter": "lower"}])
    assert [e.contact_frame for e in evs] == [10, 50]
    assert evs[0].window_start == 0 and evs[1].window_end == 65


def test_technique_analysis_gets_only_the_tracked_players_hits(tmp_path, monkeypatch):
    s = _system(tmp_path, monkeypatch)
    s._racket_detector, s.dominant_hand, s.fps = None, "right", 30.0
    captured = {}

    def fake_run(self, track, lookup, hits=None):
        captured["hits"] = hits
        return [], []

    monkeypatch.setattr(TechniqueAnalysisRunner, "run", fake_run)
    s._run_technique_analysis([{"frame": 5, "hitter": "lower"}, {"frame": 9, "hitter": "upper"}])
    assert captured["hits"] == [{"frame": 5, "hitter": "lower"}]


def test_contact_rule_strokes_json_unchanged(tmp_path, monkeypatch):
    s = _system(tmp_path, monkeypatch)
    s.bst_weights = "bst.pt"
    s.rally_detection = None
    s._shuttle_source = "yolo"
    monkeypatch.setattr(StrokeRecognizer, "label_rally",
                        lambda self, *a, hits=None, **k: [{"frame": 3, "hitter": "lower", "stroke": "net",
                                                           "confidence": 0.9, "uncertain": False}])
    s._run_stroke_recognition(None)
    payload = json.loads((tmp_path / "strokes.json").read_text())
    assert set(payload) == {"strokes", "distribution"}
    s._run_stroke_recognition([{"frame": 3, "hitter": "lower", "confidence": 0.9}])
    assert json.loads((tmp_path / "strokes.json").read_text())["hit_source"] == "learned"
```

- [ ] **Step 2: Run the tests to verify they fail.**

Run: `PYTHONUTF8=1 ./.venv/Scripts/python.exe -B -m pytest -q -p no:cacheprovider tests/test_hit_integration.py`

Expected: they fail (`_run_hit_detection` missing; `label_rally` and `run` reject `hits`).

- [ ] **Step 3: Recognizer.** In `badminton_analysis/stroke_recog/recognizer.py`:
  - change the signature to `def label_rally(self, track, frame_lookup, court_corners, video_wh, hits=None):`;
  - add a docstring paragraph: "``hits`` (optional) is the pipeline's shared hit list
    (``[{frame, hitter, confidence}]``); when given it replaces ``hit_events(track)`` and each
    label carries ``hit_confidence``."

  Then change the loop:

```python
        events = hits if hits is not None else hit_events(track)
        results = []
        for hit in events:
            frame = hit["frame"]
            hitter = hit["hitter"]
            extra = ({"hit_confidence": float(hit["confidence"])}
                     if hit.get("confidence") is not None else {})
            built = build_inputs(frame, frame_lookup, court_corners, video_wh, hitter=hitter)
            if built is None:
                results.append({
                    "frame": frame,
                    "hitter": hitter,
                    "stroke": "uncertain",
                    "confidence": 0.0,
                    "uncertain": True,
                    **extra,
                })
                continue

            logits = bst_model.predict(model, built["pose"], built["shuttle"], built["positions"])
            label, conf = to_coarse(logits, MIN_STROKE_CONF)
            results.append({
                "frame": frame,
                "hitter": hitter,
                "stroke": label,
                "confidence": float(conf),
                "uncertain": label == "uncertain",
                **extra,
            })
```

- [ ] **Step 4: TechniqueAnalysisRunner.** In `badminton_analysis/system.py`, change
  `TechniqueAnalysisRunner.run` to:

```python
    def run(self, track, frame_lookup, hits=None):
        """``hits`` (optional): the shared hit list, already filtered to the player this
        runner analyses; when given it replaces the contact rule."""
        from .stroke.events import CONTACT_M, detect_contacts, StrokeEvent
        from .stroke.classifier import classify_stroke
        if hits is None:
            contacts = detect_contacts(
                track, window_pre=self.window_pre, window_post=self.window_post,
                contact_m=CONTACT_M if self.scale is not None else None,
                scale=self.scale)
        else:
            contacts = [{"contact_frame": int(h["frame"]),
                         "window_start": max(0, int(h["frame"]) - self.window_pre),
                         "window_end": int(h["frame"]) + self.window_post}
                        for h in sorted(hits, key=lambda h: int(h["frame"]))]
```

The rest of the method (the `reports` / `events` loop) is unchanged.

- [ ] **Step 5: Make the system methods.** In `badminton_analysis/system.py`:

  1. In `__init__`:
     - add the keyword `hit_model_path=None` after `rally_signal="auto"`;
     - after `self.rally_detection = None`, add `self.hit_model_path = hit_model_path` and
       `self.rally_list = []`.

  2. At the end of `_write_rally_segments`, immediately after `self.rally_detection = detection`,
     add `self.rally_list = list(rallies)`.

  3. Replace the post-loop block in `process_video`:

     ```python
             self._write_progress("visualizing")
             hits = self._run_hit_detection(fps, frame_count)
             if self.analyze_technique:
                 self._run_technique_analysis(hits)

             self._run_stroke_recognition(hits)
     ```

  4. Change the signature to `def _run_technique_analysis(self, hits=None):`, then replace its
     `runner.run(...)` line with:

     ```python
             if hits is not None:
                 # The runner reads single-player pose fields (the frame's tracked
                 # player), so it only gets that player's hits (B spec open question F).
                 hits = [h for h in hits
                         if (self._analysis_frames.get(h["frame"]) or {}).get("player_side") == h["hitter"]]
             reports, _events = runner.run(self._analysis_track, self._analysis_frames.get, hits=hits)
     ```

  5. Change the signature to `def _run_stroke_recognition(self, hits=None):` and pass
     `hits=hits` to `label_rally(...)`. After `payload = {"strokes": labels, "distribution": distribution}`, add:

     ```python
                 if hits is not None:
                     payload["hit_source"] = "learned"
     ```

  6. Add the new methods after `_run_shuttle_pretrack`:

     ```python
         _HIT_OUTPUTS = ("hits.json", "hit_signals.npz")

         def _run_hit_detection(self, fps, last_frame):
             """Build the shared hit list. Returns learned hits, or None (contact rule).

             Writes hit_signals.npz (the detector's raw input, also harvested by the
             ShuttleSet dataset builder) and hits.json. Never fatal: every failure
             falls back to today's contact rule with the reason recorded. Without
             technique analysis there are no per-frame poses, so nothing is written and
             a previous run's hit outputs are removed (they would describe another run).
             """
             if not self.analyze_technique or not last_frame:
                 for name in self._HIT_OUTPUTS:
                     try:
                         os.remove(os.path.join(self.save_dir, name))
                     except FileNotFoundError:
                         pass
                     except OSError as exc:
                         print(f"Could not remove the previous run's {name}: {exc}")
                 return None

             from .stroke import hit_detector as hd
             from .stroke import hit_signals as hs
             from .stroke_recog.hits import hit_events

             signals = None
             try:
                 shuttle = (self._shuttle_trajectory if self._shuttle_trajectory is not None
                            else {r["frame"]: r["shuttle"] for r in self._analysis_track_both})
                 signals = hs.build_signals(fps, last_frame, self._analysis_frames, shuttle,
                                            self.court_corners, (self.frame_width, self.frame_height))
                 hs.save_signals(os.path.join(self.save_dir, "hit_signals.npz"), signals)
             except Exception as exc:
                 print(f"Hit signals unavailable: {exc!r}")

             coverage = hd.shuttle_coverage(signals, self._in_play_frames()) if signals is not None else 0.0
             reason, hits, model_info = None, None, None
             if not getattr(self, "hit_model_path", None):
                 reason = "no hit model"
             elif signals is None:
                 reason = "hit signals unavailable"
             elif self._shuttle_source != "tracknet":
                 reason = "no dense shuttle track"
             elif coverage < hd.MIN_SHUTTLE_COVERAGE:
                 reason = (f"dense shuttle track covers {coverage:.0%} of in-play frames "
                           f"(needs {hd.MIN_SHUTTLE_COVERAGE:.0%})")
             else:
                 try:
                     from .stroke import hit_model as hm
                     model, meta = hm.load_hit_model(self.hit_model_path)
                     hits = hd.detect_hits(signals, model, meta)
                     model_info = {"sha256": hm.file_sha256(self.hit_model_path),
                                   "schema": meta.get("schema"), "threshold": meta.get("threshold"),
                                   "min_spacing_sec": meta.get("min_spacing_sec")}
                 except Exception as exc:
                     print(f"Hit detector failed, using the contact rule: {exc!r}")
                     reason = f"hit detector failed: {type(exc).__name__}"
                     hits = None

             if hits is None:
                 listed = [{"frame": e["frame"], "hitter": e["hitter"], "confidence": None}
                           for e in hit_events(self._analysis_track_both)]
                 source = "contact_rule"
             else:
                 listed, source = hits, "learned"
             self._write_hits_json(fps, source, reason, model_info, coverage, listed)
             print(f"Hit detection: {len(listed)} hits ({source}"
                   f"{'; ' + reason if reason else ''})")
             return hits

         def _in_play_frames(self):
             """Frames inside B11 rally segments; all analysed frames when there are none."""
             frames = set()
             for r in getattr(self, "rally_list", None) or []:
                 frames.update(range(int(r["start_frame"]), int(r["end_frame"]) + 1))
             return sorted(frames) if frames else sorted(self._analysis_frames)

         def _write_hits_json(self, fps, source, reason, model_info, coverage, hits):
             from .data.writer import write_json as _write_json

             rallies = getattr(self, "rally_list", None) or []

             def rally_id(frame):
                 for r in rallies:
                     if int(r["start_frame"]) <= frame <= int(r["end_frame"]):
                         return r["id"]
                 return None

             payload = {
                 "source": source, "reason": reason, "model": model_info,
                 "shuttle_source": self._shuttle_source,
                 "shuttle_coverage_in_play": round(float(coverage), 4),
                 "fps": fps,
                 "hits": [{"frame": int(h["frame"]),
                           "time_sec": round((int(h["frame"]) - 1) / fps, 4) if fps else None,
                           "hitter": h["hitter"], "confidence": h.get("confidence"),
                           "rally_id": rally_id(int(h["frame"]))} for h in hits],
             }
             try:
                 _write_json(os.path.join(self.save_dir, "hits.json"), payload)
             except Exception as exc:
                 print(f"hits.json could not be written: {exc!r}")
     ```

- [ ] **Step 6: Run the tests to verify they pass, then run the neighbours.**

Run: `PYTHONUTF8=1 ./.venv/Scripts/python.exe -B -m pytest -q -p no:cacheprovider tests/test_hit_integration.py tests/test_bst_integration.py tests/test_bst_recognizer.py tests/test_integration_analysis.py tests/test_technique_contact_gate.py tests/test_rally_segmentation.py`

Expected: all pass.

If `test_degenerate_corners_fall_back_not_crash` fails because `build_signals` succeeded but
`CourtGeometry` raised outside the `try`, check that `detect_hits` (which calls
`compute_features` → `CourtGeometry`) runs inside the `try` in `_run_hit_detection`. It does
in the code above.

- [ ] **Step 7: Commit.**

```bash
git add badminton_analysis/system.py badminton_analysis/stroke_recog/recognizer.py tests/test_hit_integration.py
git commit -m "feat(hits): shared hit list with hits.json, fallbacks and both consumers

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 9: CLI and web wiring

**Files:**
- Modify: `main.py` (arguments and the constructor call)
- Modify: `app.py`:
  - a new `_hit_model_weights` after `_inpaintnet_weights` (~line 266);
  - the command builder (~line 852);
  - `/api/models` (~line 644).
- Test: `tests/test_hit_wiring.py`

**Interfaces:**
- Consumes: the `hit_model_path` and `tracknet_mode` constructor keywords (Tasks 7–8)
- Produces:
  - `main.py --hit-model PATH --tracknet-mode {weight,nonoverlap}`;
  - `app._hit_model_weights(base=None) -> str | None`, which requires both
    `hit-detector.pt` and `hit-detector.json`;
  - `/api/models` gains a `hit_detector` key.

- [ ] **Step 1: Write the failing tests.**

```python
# tests/test_hit_wiring.py
import inspect

import app as webapp


def test_hit_model_discovery_needs_weights_and_sidecar(tmp_path):
    assert webapp._hit_model_weights(base=tmp_path) is None
    (tmp_path / "hit-detector.pt").write_bytes(b"x")
    assert webapp._hit_model_weights(base=tmp_path) is None
    (tmp_path / "hit-detector.json").write_text("{}")
    assert webapp._hit_model_weights(base=tmp_path) == str(tmp_path / "hit-detector.pt")


def test_api_models_reports_hit_detector():
    body = webapp.app.test_client().get("/api/models").get_json()
    assert "hit_detector" in body


def test_main_has_hit_model_and_tracknet_mode_flags():
    import main
    src = inspect.getsource(main.main)
    assert "--hit-model" in src and "hit_model_path=args.hit_model" in src
    assert "--tracknet-mode" in src and "tracknet_mode=args.tracknet_mode" in src


def test_app_passes_hit_model_when_installed():
    src = inspect.getsource(webapp)
    assert "cmd += ['--hit-model', hit_weights]" in src
```

- [ ] **Step 2: Run the tests to verify they fail.**

Run: `PYTHONUTF8=1 ./.venv/Scripts/python.exe -B -m pytest -q -p no:cacheprovider tests/test_hit_wiring.py`

Expected: they fail (`_hit_model_weights` missing).

- [ ] **Step 3: main.py.** After the `--inpaintnet-model` argument, add:

```python
    parser.add_argument('--hit-model', default=None, type=str,
                        help='学习型击球检测模型路径（可选；需同名 .json 说明文件）')
    parser.add_argument('--tracknet-mode', default='weight', choices=['weight', 'nonoverlap'],
                        help="TrackNetV3 模式：weight（默认，较慢较稳）或 nonoverlap（约快 2.7 倍）")
```

In the `BadmintonAnalysisSystem(...)` call, after `inpaintnet_weights=args.inpaintnet_model,`,
add:

```python
        tracknet_mode=args.tracknet_mode,
        hit_model_path=args.hit_model,
```

- [ ] **Step 4: app.py.**
  - After `_inpaintnet_weights`, add:

    ```python
    def _hit_model_weights(base=None):
        """Path to the learned hit detector when installed (weights + sidecar), else None."""
        base = Path(base) if base is not None else (PROJECT_ROOT / 'weights')
        p = base / 'hit-detector.pt'
        return str(p) if p.is_file() and (base / 'hit-detector.json').is_file() else None
    ```

  - After the `inpaintnet_weights` block in the command builder, add:

    ```python
        hit_weights = _hit_model_weights()
        if hit_weights:
            cmd += ['--hit-model', hit_weights]
    ```

  - In `api_models`, add `'hit_detector': _hit_model_weights() is not None,` and extend the
    docstring list with "learned hit detector".

- [ ] **Step 5: Run the tests to verify they pass.**

Run: `PYTHONUTF8=1 ./.venv/Scripts/python.exe -B -m pytest -q -p no:cacheprovider tests/test_hit_wiring.py tests/test_tracknet_wiring.py tests/test_app_progress.py`

Expected: all pass.

- [ ] **Step 6: Commit.**

```bash
git add main.py app.py tests/test_hit_wiring.py
git commit -m "feat(hits): --hit-model / --tracknet-mode flags and web discovery

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 10: ShuttleSet label tooling and the alignment check

**Files:**
- Create: `scripts/shuttleset_labels.py`
- Create: `scripts/check_shuttleset_alignment.py`
- Test: `tests/test_shuttleset_labels.py`

**Interfaces:**
- Consumes:
  - `CourtGeometry` (Task 2)
  - `shuttle_turn_events` (Task 6)
  - `tracknet.load_tracknet`, `tracknet.track_video(..., eval_mode)` (Task 7)
- Produces, in `shuttleset_labels`:
  - `BASE_SIZE = (1280, 720)`, `LABEL_FPS = 30.0`
  - `STROKE7 = ("clear","serve","drop","smash","net","lift","drive")`
  - `TYPE_TO_STROKE7 = {<18 Chinese types>: <7-class>}`; `"未知球種"` maps to `None`
  - `stroke7_of(raw) -> str | None`
  - `load_match_table(match_csv) -> {video: row dict}`
  - `load_corners(homography_csv, video) -> list[[x,y]]×4` (in our corner order, at `BASE_SIZE`)
  - `scale_corners(corners, size) -> list`
  - `hitter_side(loc_y, corners) -> "lower"|"upper"|None`
  - `parse_match_hits(match_dir, corners) -> list[dict]`. Each dict holds `set`, `rally`,
    `ball_round`, `frame30`, `t`, `player`, `type_raw`, `stroke7`, `side`.
  - `rally_spans(hits, pre_sec=1.0, post_sec=1.5) -> list[dict]`. Each dict holds `set`,
    `rally`, `start`, `end`, `hits`.
  - `best_offset(label_times, event_times, search_sec=10.0, step_sec=1/30, tol_sec=0.1, min_frac=0.6, peak_ratio=1.5, exclusion_sec=0.5) -> dict`
    with `offset`, `matched`, `runner_up`, `matched_frac`, `accepted`
- **Data layout** (all under gitignored `data/shuttleset/`):
  - `labels/match.csv`, `labels/homography.csv`, `labels/<video>/set{1,2,3}.csv`
  - `videos/<video>.mp4`
  - `alignment/<video>.json`

- [ ] **Step 1: Write the failing tests.**

```python
# tests/test_shuttleset_labels.py
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import shuttleset_labels as sl  # noqa: E402

CORNERS = [[407.6, 308.6], [867.0, 307.4], [973.0, 669.4], [307.2, 671.2]]
SET_CSV = (
    "rally,ball_round,time,frame_num,roundscore_A,roundscore_B,player,server,type,aroundhead,"
    "backhand,hit_height,hit_area,hit_x,hit_y,landing_height,landing_area,landing_x,landing_y,"
    "lose_reason,win_reason,getpoint_player,flaw,player_location_area,player_location_x,"
    "player_location_y,opponent_location_area,opponent_location_x,opponent_location_y,db\n"
    "1,1.0,00:06:28,11657.0,0,1,B,1,發短球,,1.0,2.0,,,,2.0,7,597.0,390.0,,,,,8,650.0,532.0,8,561.0,341.0,0\n"
    "1,2.0,00:06:29,11678.0,0,1,A,2,挑球,,,2.0,7,597.0,390.0,1.0,9,800.0,633.0,,,,,8,591.0,373.0,8,626.0,532.0,0\n"
    "2,1.0,00:06:58,12557.0,0,1,B,1,未知球種,,,,,,,,,,,,,,,,,,,,,0\n"
)


def test_all_18_types_map_and_unknown_is_none():
    assert len(sl.TYPE_TO_STROKE7) == 18
    assert set(sl.TYPE_TO_STROKE7.values()) == set(sl.STROKE7)
    assert sl.stroke7_of("挑球") == "lift" and sl.stroke7_of("長球") == "clear"
    assert sl.stroke7_of("推球") == "drive" and sl.stroke7_of("撲球") == "net"
    assert sl.stroke7_of("未知球種") is None and sl.stroke7_of("") is None


def test_load_corners_reorders_to_far_left_far_right_near_right_near_left(tmp_path):
    csv = tmp_path / "homography.csv"
    csv.write_text("id,video,homography_matrix,upleft_x,upright_x,downleft_x,downright_x,"
                   "upleft_y,upright_y,downleft_y,downright_y\n"
                   '1,M,"[[1,0,0],[0,1,0],[0,0,1]]",407.6,867.0,307.2,973.0,308.6,307.4,671.2,669.4\n',
                   encoding="utf-8")
    assert sl.load_corners(str(csv), "M") == CORNERS
    assert sl.scale_corners(CORNERS, (1920, 1080))[0] == [407.6 * 1.5, 308.6 * 1.5]


def test_hitter_side_from_location_against_net_row():
    assert sl.hitter_side(532.0, CORNERS) == "lower"
    assert sl.hitter_side(373.0, CORNERS) == "upper"
    assert sl.hitter_side(float("nan"), CORNERS) is None


def test_parse_match_hits_and_rally_spans(tmp_path):
    d = tmp_path / "M"
    d.mkdir()
    (d / "set1.csv").write_text(SET_CSV, encoding="utf-8")
    hits = sl.parse_match_hits(str(d), CORNERS)
    assert [h["frame30"] for h in hits] == [11657, 11678, 12557]
    assert hits[0]["stroke7"] == "serve" and hits[0]["side"] == "lower"
    assert hits[1]["stroke7"] == "lift" and hits[1]["side"] == "upper"
    assert hits[2]["stroke7"] is None and hits[2]["side"] is None
    spans = sl.rally_spans(hits)
    assert [(s["set"], s["rally"]) for s in spans] == [(1, 1), (1, 2)]
    assert abs(spans[0]["start"] - (11657 / 30.0 - 1.0)) < 1e-9
    assert abs(spans[0]["end"] - (11678 / 30.0 + 1.5)) < 1e-9


def test_best_offset_recovers_a_shift_and_rejects_noise():
    rng = np.random.default_rng(0)
    labels = np.sort(rng.uniform(0, 300, 80))
    events = np.sort(np.concatenate([labels + 2.0 + rng.normal(0, 0.02, 80),
                                     rng.uniform(0, 300, 30)]))
    r = sl.best_offset(labels.tolist(), events.tolist())
    assert abs(r["offset"] - 2.0) < 0.05 and r["accepted"] is True
    noise = sl.best_offset(labels.tolist(), np.sort(rng.uniform(0, 300, 110)).tolist())
    assert noise["accepted"] is False
    assert sl.best_offset([], [1.0])["accepted"] is False
```

- [ ] **Step 2: Run the tests to verify they fail.**

Run: `PYTHONUTF8=1 ./.venv/Scripts/python.exe -B -m pytest -q -p no:cacheprovider tests/test_shuttleset_labels.py`

Expected: they fail with `ModuleNotFoundError: No module named 'shuttleset_labels'`.

- [ ] **Step 3: Implement the labels module.**

```python
# scripts/shuttleset_labels.py
"""ShuttleSet label parsing for the hit-detector dataset (pure; no video, no models).

Labels come from wywyWang/CoachAI-Projects (MIT), ShuttleSet/set/. frame_num is
at 30 fps in the annotators' copy of each match; pixel coordinates are at
1280x720. Corners are returned in this project's CourtMapper order:
far-left, far-right, near-right, near-left.
"""
import ast
import bisect
import csv
import math
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from badminton_analysis.stroke.hit_features import CourtGeometry  # noqa: E402

BASE_SIZE = (1280, 720)
LABEL_FPS = 30.0
STROKE7 = ("clear", "serve", "drop", "smash", "net", "lift", "drive")
TYPE_TO_STROKE7 = {
    "發短球": "serve", "發長球": "serve",
    "長球": "clear",
    "挑球": "lift", "防守回挑": "lift",
    "殺球": "smash", "點扣": "smash",
    "切球": "drop", "過渡切球": "drop",
    "放小球": "net", "擋小球": "net", "勾球": "net", "撲球": "net",
    "平球": "drive", "小平球": "drive", "後場抽平球": "drive", "推球": "drive", "防守回抽": "drive",
}


def stroke7_of(raw):
    return TYPE_TO_STROKE7.get((raw or "").strip())


def load_match_table(match_csv):
    with open(match_csv, encoding="utf-8") as fh:
        return {row["video"]: row for row in csv.DictReader(fh)}


def load_corners(homography_csv, video):
    with open(homography_csv, encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            if row["video"] == video:
                p = {k: float(row[k]) for k in row if k.endswith("_x") or k.endswith("_y")}
                return [[p["upleft_x"], p["upleft_y"]], [p["upright_x"], p["upright_y"]],
                        [p["downright_x"], p["downright_y"]], [p["downleft_x"], p["downleft_y"]]]
    raise KeyError(f"no homography row for {video}")


def scale_corners(corners, size):
    sx, sy = size[0] / BASE_SIZE[0], size[1] / BASE_SIZE[1]
    return [[x * sx, y * sy] for x, y in corners]


def hitter_side(loc_y, corners):
    try:
        y = float(loc_y)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(y):
        return None
    return "upper" if y < CourtGeometry(corners).net_mid[1] else "lower"


def _f(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return float("nan")


def parse_match_hits(match_dir, corners):
    hits = []
    for set_no in (1, 2, 3):
        path = os.path.join(match_dir, f"set{set_no}.csv")
        if not os.path.isfile(path):
            continue
        with open(path, encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                frame = _f(row.get("frame_num"))
                if not math.isfinite(frame):
                    continue
                raw = (row.get("type") or "").strip()
                hits.append({"set": set_no, "rally": int(_f(row["rally"])),
                             "ball_round": int(_f(row["ball_round"])),
                             "frame30": int(round(frame)), "t": frame / LABEL_FPS,
                             "player": row.get("player"), "type_raw": raw,
                             "stroke7": stroke7_of(raw),
                             "side": hitter_side(_f(row.get("player_location_y")), corners)})
    return sorted(hits, key=lambda h: h["frame30"])


def rally_spans(hits, pre_sec=1.0, post_sec=1.5):
    groups = {}
    for h in hits:
        groups.setdefault((h["set"], h["rally"]), []).append(h)
    spans = []
    for (set_no, rally), hs in groups.items():
        hs = sorted(hs, key=lambda h: h["frame30"])
        spans.append({"set": set_no, "rally": rally, "start": hs[0]["t"] - pre_sec,
                      "end": hs[-1]["t"] + post_sec, "hits": hs})
    return sorted(spans, key=lambda s: s["start"])


def best_offset(label_times, event_times, search_sec=10.0, step_sec=1 / 30, tol_sec=0.1,
                min_frac=0.6, peak_ratio=1.5, exclusion_sec=0.5):
    """Video-time offset o maximising labels t with an event within tol of t + o."""
    labels = sorted(label_times)
    events = sorted(event_times)
    if not labels or not events:
        return {"offset": 0.0, "matched": 0, "runner_up": 0, "matched_frac": 0.0, "accepted": False}
    n_steps = int(round(search_sec / step_sec))
    offsets = [k * step_sec for k in range(-n_steps, n_steps + 1)]
    counts = []
    for o in offsets:
        c = 0
        for t in labels:
            lo = bisect.bisect_left(events, t + o - tol_sec)
            c += lo < len(events) and events[lo] <= t + o + tol_sec
        counts.append(c)
    best_i = max(range(len(offsets)), key=lambda i: counts[i])
    best = offsets[best_i]
    runner = max([c for o, c in zip(offsets, counts) if abs(o - best) > exclusion_sec] or [0])
    frac = counts[best_i] / len(labels)
    accepted = counts[best_i] >= peak_ratio * runner and frac >= min_frac
    return {"offset": round(best, 4), "matched": counts[best_i], "runner_up": runner,
            "matched_frac": round(frac, 4), "accepted": bool(accepted)}


def parse_matrix(text):
    """homography_matrix column -> nested list (kept for completeness; unused by the builder)."""
    return ast.literal_eval(text)
```

- [ ] **Step 4: Implement the alignment script.** It is run by the controller and not unit
  tested beyond its pure helpers.

```python
# scripts/check_shuttleset_alignment.py
"""Accept or reject a fetched ShuttleSet video against its labels (spec 6.2).

Usage:
  python scripts/check_shuttleset_alignment.py --match <video> [--data data/shuttleset]
      [--samples 3] [--tracknet-mode nonoverlap]

Cuts windows around a few labelled rallies (+-12 s), runs TrackNetV3 on each,
finds shuttle turn events, and searches a +-10 s offset between label time and
video time. Writes data/shuttleset/alignment/<video>.json; exit 0 if accepted, 3 if not.
"""
import argparse
import json
import os
import subprocess
import sys

import cv2
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import shuttleset_labels as sl  # noqa: E402
from badminton_analysis.shuttle_track import tracknet as tn  # noqa: E402
from badminton_analysis.stroke.shuttle_turns import shuttle_turn_events  # noqa: E402

MARGIN_SEC = 12.0


def _cut(video, start, duration, out):
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{max(0.0, start):.3f}", "-i", video,
                    "-t", f"{duration:.3f}", "-an", "-c:v", "libx264", "-crf", "16",
                    "-preset", "veryfast", out], check=True)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--match", required=True)
    p.add_argument("--data", default=os.path.join(ROOT, "data", "shuttleset"))
    p.add_argument("--samples", type=int, default=3)
    p.add_argument("--tracknet-mode", default="nonoverlap", choices=["weight", "nonoverlap"])
    a = p.parse_args(argv)

    video = os.path.join(a.data, "videos", f"{a.match}.mp4")
    cap = cv2.VideoCapture(video)
    fps = cap.get(cv2.CAP_PROP_FPS)
    size = (int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)))
    cap.release()
    if not fps or fps <= 0:
        raise SystemExit(f"cannot read {video}")

    corners = sl.load_corners(os.path.join(a.data, "labels", "homography.csv"), a.match)
    hits = sl.parse_match_hits(os.path.join(a.data, "labels", a.match), corners)
    spans = [s for s in sl.rally_spans(hits) if len(s["hits"]) >= 6]
    picks = [spans[int(i)] for i in np.linspace(0, len(spans) - 1, min(a.samples, len(spans)))]

    tmp = os.path.join(a.data, "alignment", "_tmp")
    os.makedirs(tmp, exist_ok=True)
    models = tn.load_tracknet(os.path.join(ROOT, "weights", "tracknet.pt"),
                              os.path.join(ROOT, "weights", "inpaintnet.pt"))
    label_t, event_t = [], []
    for k, span in enumerate(picks):
        w0 = max(0.0, span["start"] - MARGIN_SEC)
        dur = span["end"] - span["start"] + 2 * MARGIN_SEC
        clip = os.path.join(tmp, f"{a.match}_{k}.mp4")
        _cut(video, w0, dur, clip)
        traj = tn.track_video(clip, models, eval_mode=a.tracknet_mode)
        n = max(traj) + 1 if traj else 0
        arr = np.full((n, 2), np.nan)
        for f, pt in traj.items():
            if pt is not None:
                arr[f] = pt
        event_t += [w0 + i / fps for i in shuttle_turn_events(arr, fps)]
        label_t += [h["t"] for h in span["hits"]]
        os.remove(clip)

    result = sl.best_offset(label_t, event_t)
    result.update({"video": a.match, "fps": fps, "width": size[0], "height": size[1],
                   "sampled_rallies": [[s["set"], s["rally"]] for s in picks]})
    out = os.path.join(a.data, "alignment", f"{a.match}.json")
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(result, fh, indent=2)
    print(f"{a.match}: {'ACCEPTED' if result['accepted'] else 'REJECTED'} offset "
          f"{result['offset']:+.2f}s matched {result['matched']}/{len(label_t)} "
          f"(runner-up {result['runner_up']}) -> {out}")
    return 0 if result["accepted"] else 3


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 5: Run the tests to verify they pass.**

Run: `PYTHONUTF8=1 ./.venv/Scripts/python.exe -B -m pytest -q -p no:cacheprovider tests/test_shuttleset_labels.py`

Expected: 5 passed.

- [ ] **Step 6: Commit.**

```bash
git add scripts/shuttleset_labels.py scripts/check_shuttleset_alignment.py tests/test_shuttleset_labels.py
git commit -m "feat(hits): ShuttleSet label parsing and the video alignment check

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 11: Dataset builder

**Files:**
- Create: `scripts/build_hit_dataset.py`
- Test: `tests/test_build_hit_dataset.py`

**Interfaces:**
- Consumes:
  - `shuttleset_labels` (Task 10)
  - `load_signals` (Task 1)
  - `CourtGeometry` (Task 2)
  - `BadmintonAnalysisSystem(..., tracknet_mode, court_view_threshold)` (Task 7)
- Produces:
  - `label_to_clip_frame(t_label, offset_sec, clip_start_sec, fps) -> int`, 1-based
  - `court_annotation_text(corners) -> str`
  - `encode_hits(hits, offset_sec, clip_start_sec, fps, n_frames) -> dict`, of numpy arrays
    `hit_frames`, `hitter` (0 lower, 1 upper, -1 unknown), `stroke7` (index into `STROKE7`,
    or -1), `stroke_raw`
  - `run_pipeline_on_clip(clip, run_dir, corners, tracknet_mode) -> RawSignals`, reused by
    Task 16
  - Output: `data/hit_dataset/<video>/s<set>_r<rally>.npz`, holding the `RawSignals` fields
    plus the label arrays plus `match`, `set`, `rally`

- [ ] **Step 1: Write the failing tests.**

```python
# tests/test_build_hit_dataset.py
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import build_hit_dataset as bd  # noqa: E402

CORNERS = [[407.6, 308.6], [867.0, 307.4], [973.0, 669.4], [307.2, 671.2]]


def test_label_to_clip_frame_applies_offset_and_one_based_frames():
    # label 100.0 s, video = label + 2.0 s, clip starts at 101.0 s, 60 fps -> 1.0 s in -> frame 61
    assert bd.label_to_clip_frame(100.0, 2.0, 101.0, 60.0) == 61
    assert bd.label_to_clip_frame(100.0, 0.0, 100.0, 30.0) == 1


def test_court_annotation_text_has_three_lines_and_net_row():
    text = bd.court_annotation_text(CORNERS)
    lines = text.strip().splitlines()
    assert lines[0].startswith("corners=[[408, 309]") and lines[1].startswith("roi_corners=")
    mid = int(lines[2].split("=")[1])
    assert 308 < mid < 671


def test_encode_hits_drops_out_of_clip_and_maps_labels():
    hits = [{"t": 10.0, "side": "lower", "stroke7": "serve", "type_raw": "發短球"},
            {"t": 11.0, "side": "upper", "stroke7": None, "type_raw": "未知球種"},
            {"t": 99.0, "side": "lower", "stroke7": "net", "type_raw": "放小球"}]
    enc = bd.encode_hits(hits, offset_sec=0.0, clip_start_sec=9.0, fps=30.0, n_frames=90)
    assert enc["hit_frames"].tolist() == [31, 61]
    assert enc["hitter"].tolist() == [0, 1]
    assert enc["stroke7"].tolist() == [1, -1]
    assert enc["stroke_raw"].tolist() == ["發短球", "未知球種"]
```

- [ ] **Step 2: Run the tests to verify they fail.**

Run: `PYTHONUTF8=1 ./.venv/Scripts/python.exe -B -m pytest -q -p no:cacheprovider tests/test_build_hit_dataset.py`

Expected: they fail with `ModuleNotFoundError`.

- [ ] **Step 3: Implement.**

```python
# scripts/build_hit_dataset.py
"""Build the hit-detector training set from labelled ShuttleSet rallies (spec 6.4).

Usage:
  python scripts/build_hit_dataset.py --match <video> [--data data/shuttleset]
      [--out data/hit_dataset] [--tracknet-mode weight] [--limit N]

Needs data/shuttleset/alignment/<video>.json with "accepted": true. For every
labelled rally (1 s before the first hit to 1.5 s after the last) it cuts a
clip, runs the match pipeline on it headless (TrackNetV3 + RTMPose both
players, court corners from homography.csv, court-view gate pinned open) and
stores the pipeline's own hit_signals.npz together with the labels. Resumable:
existing rally files are skipped. Per-rally run folders are deleted afterwards.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import time

import cv2
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import shuttleset_labels as sl  # noqa: E402
from badminton_analysis.stroke.hit_features import CourtGeometry  # noqa: E402
from badminton_analysis.stroke.hit_signals import load_signals  # noqa: E402

_SIDE_CODE = {"lower": 0, "upper": 1}


def label_to_clip_frame(t_label, offset_sec, clip_start_sec, fps):
    return int(round((t_label + offset_sec - clip_start_sec) * fps)) + 1


def court_annotation_text(corners):
    pts = [[int(round(x)), int(round(y))] for x, y in corners]
    mid = int(round(CourtGeometry(corners).net_mid[1]))
    return f"corners={pts}\nroi_corners=[(0, 0), (1, 1)]\nmid_height={mid}\n"


def encode_hits(hits, offset_sec, clip_start_sec, fps, n_frames):
    frames, hitter, stroke, raw = [], [], [], []
    for h in hits:
        f = label_to_clip_frame(h["t"], offset_sec, clip_start_sec, fps)
        if not 1 <= f <= n_frames:
            continue
        frames.append(f)
        hitter.append(_SIDE_CODE.get(h.get("side"), -1))
        stroke.append(sl.STROKE7.index(h["stroke7"]) if h.get("stroke7") in sl.STROKE7 else -1)
        raw.append(h.get("type_raw") or "")
    return {"hit_frames": np.asarray(frames, np.int64), "hitter": np.asarray(hitter, np.int64),
            "stroke7": np.asarray(stroke, np.int64), "stroke_raw": np.asarray(raw, dtype=str)}


def run_pipeline_on_clip(clip, run_dir, corners, tracknet_mode):
    """Run the match pipeline headless on one clip; return its RawSignals."""
    from badminton_analysis.system import BadmintonAnalysisSystem

    os.makedirs(run_dir, exist_ok=True)
    with open(os.path.join(run_dir, "court_annotations.txt"), "w") as fh:
        fh.write(court_annotation_text(corners))
    cap = cv2.VideoCapture(clip)
    ok, frame = cap.read()
    cap.release()
    if not ok:
        raise RuntimeError(f"cannot read {clip}")
    template = os.path.join(run_dir, "template.png")
    cv2.imwrite(template, frame)
    system = BadmintonAnalysisSystem(
        clip, show_display=False, show_skeletons=False, show_player_trajectories=False,
        show_court_trajectory=False, show_shuttlecock_trajectory=False, show_player_stats=False,
        language="en", output_dir=run_dir, template_path=template,
        ball_model_path=os.path.join(ROOT, "weights", "yolo11s-ball.pt"),
        pose_family="rtmpose", pose_mode="balanced", show_pose_roi=False,
        analyze_technique=True, racket_model_path=None,
        tracknet_weights=os.path.join(ROOT, "weights", "tracknet.pt"),
        inpaintnet_weights=os.path.join(ROOT, "weights", "inpaintnet.pt"),
        tracknet_mode=tracknet_mode, court_view_threshold=0.0)
    system.keep_audio = False
    system.process_video()
    return load_signals(os.path.join(run_dir, "hit_signals.npz"))


def _cut(video, start, duration, out):
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{max(0.0, start):.3f}", "-i", video,
                    "-t", f"{duration:.3f}", "-an", "-c:v", "libx264", "-crf", "12",
                    "-preset", "fast", out], check=True)


def main(argv=None):
    from badminton_analysis.system import load_runtime_dependencies

    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--match", required=True)
    p.add_argument("--data", default=os.path.join(ROOT, "data", "shuttleset"))
    p.add_argument("--out", default=os.path.join(ROOT, "data", "hit_dataset"))
    p.add_argument("--tracknet-mode", default="weight", choices=["weight", "nonoverlap"])
    p.add_argument("--limit", type=int, default=0)
    a = p.parse_args(argv)

    with open(os.path.join(a.data, "alignment", f"{a.match}.json"), encoding="utf-8") as fh:
        align = json.load(fh)
    if not align.get("accepted"):
        raise SystemExit(f"{a.match} failed the alignment check; use a reserve match")
    load_runtime_dependencies()
    video = os.path.join(a.data, "videos", f"{a.match}.mp4")
    size = (int(align["width"]), int(align["height"]))
    base_corners = sl.load_corners(os.path.join(a.data, "labels", "homography.csv"), a.match)
    corners = sl.scale_corners(base_corners, size)          # this video's pixels, for the pipeline
    # Labels' player_location_* are at BASE_SIZE, so near/far uses the unscaled corners.
    hits = sl.parse_match_hits(os.path.join(a.data, "labels", a.match), base_corners)
    out_dir = os.path.join(a.out, a.match)
    os.makedirs(out_dir, exist_ok=True)
    spans = sl.rally_spans(hits)
    if a.limit:
        spans = spans[:a.limit]
    t0 = time.time()
    for k, span in enumerate(spans, 1):
        target = os.path.join(out_dir, f"s{span['set']}_r{span['rally']}.npz")
        if os.path.exists(target):
            continue
        run_dir = os.path.join(a.out, "_runs", a.match, f"s{span['set']}_r{span['rally']}")
        clip_start = span["start"] + float(align["offset"])
        os.makedirs(run_dir, exist_ok=True)
        clip = os.path.join(run_dir, "clip.mp4")
        try:
            _cut(video, clip_start, span["end"] - span["start"], clip)
            sig = run_pipeline_on_clip(clip, run_dir, corners, a.tracknet_mode)
            labels = encode_hits(span["hits"], float(align["offset"]), clip_start, sig.fps, sig.n_frames)
            np.savez_compressed(target, fps=sig.fps, first_frame=sig.first_frame, shuttle=sig.shuttle,
                                keypoints=sig.keypoints, court_corners=sig.court_corners,
                                image_size=np.asarray(sig.image_size), match=a.match,
                                set=span["set"], rally=span["rally"], **labels)
        except Exception as exc:  # one bad rally must not stop a 10-hour build
            print(f"  rally s{span['set']} r{span['rally']} failed: {exc!r}")
        finally:
            shutil.rmtree(run_dir, ignore_errors=True)
        print(f"[{k}/{len(spans)}] s{span['set']} r{span['rally']} "
              f"({(time.time() - t0) / 3600:.2f} h elapsed)", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

> Note on `court_annotation_text`: `_setup_court_annotation` recomputes `roi_corners` from
> the corners and ignores line 2, so the placeholder ROI on line 2 is never used. Check that
> by reading `badminton_analysis/system.py:1452-1474` before committing.

- [ ] **Step 4: Run the tests to verify they pass.**

Run: `PYTHONUTF8=1 ./.venv/Scripts/python.exe -B -m pytest -q -p no:cacheprovider tests/test_build_hit_dataset.py`

Expected: 3 passed.

- [ ] **Step 5: Smoke-test the pipeline call on one real clip.** This is the controller's job
  and needs the weights. Run it on one Axelsen stretch reused as a stand-in, to prove that
  `run_pipeline_on_clip` produces signals:

```bash
PYTHONUTF8=1 ./.venv/Scripts/python.exe -c "
import sys; sys.path.insert(0,'scripts')
from badminton_analysis.system import load_runtime_dependencies; load_runtime_dependencies()
import build_hit_dataset as bd
sig = bd.run_pipeline_on_clip('outputs/axelsen-hit-labels/runs/src/rally3_804.mp4','outputs/axelsen-hit-labels/runs/_smoke',[[437,625],[1374,623],[1916,998],[17,1028]],'nonoverlap')
import numpy as np; print(sig.n_frames, np.isfinite(sig.shuttle).all(1).mean(), np.isfinite(sig.keypoints[:,0,11]).all(1).mean(), np.isfinite(sig.keypoints[:,1,11]).all(1).mean())"
```

Expected: about 1020 frames, shuttle presence above 0.8, near-player hip presence above 0.8,
and some far-player presence. Delete `outputs/axelsen-hit-labels/runs/_smoke` afterwards.

- [ ] **Step 6: Commit.**

```bash
git add scripts/build_hit_dataset.py tests/test_build_hit_dataset.py
git commit -m "feat(hits): ShuttleSet dataset builder running the match pipeline per rally

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 12: Training (`hit_training.py` and `train_hit_detector.py`)

**Files:**
- Create: `badminton_analysis/stroke/hit_training.py`
- Create: `scripts/train_hit_detector.py`
- Test: `tests/test_hit_training.py`

**Interfaces:**
- Consumes:
  - Tasks 1–5 (signals, features, augmentation, model, detector)
  - `summarize` (Task 6)
- Produces:
  - `label_steps(hit_frames, first_frame, idx) -> np.ndarray[int]`: the nearest step to each
    hit, dropping hits outside the resampled span
  - `make_targets(n_steps, hit_steps, hitters, sigma=1.0, hitter_radius=2) -> (hit_t, far_t, far_mask)`
  - `random_window(n_steps, length, rng) -> (start, stop)`
  - `choose_peak_config(samples, thresholds, spacings_sec, tol_sec=0.2) -> dict`. It takes
    `samples` as `[(hit_prob, far_prob, truth_steps, truth_far)]` and returns the best F1 with
    `threshold`, `min_spacing_sec`, and the `summarize` keys.
  - `scripts/train_hit_detector.py`:
    - `Sample` (namedtuple: `sig`, `hit_frames`, `hitter`, `match`)
    - `load_dataset(dir) -> list[Sample]`
    - `train(train_samples, val_samples, epochs=40, hidden=64, levels=4, window=256, batch=32, lr=1e-3, seed=0, device=None, log=print) -> (model, meta)`
    - CLI: `--dataset`, `--val-match`, `--out`, `--epochs`, `--init`, `--device`.

- [ ] **Step 1: Write the failing tests.**

```python
# tests/test_hit_training.py
import sys
from pathlib import Path

import numpy as np

from badminton_analysis.stroke.hit_training import (
    choose_peak_config, label_steps, make_targets, random_window)
from tests.hit_fixtures import make_signals

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import train_hit_detector as thd  # noqa: E402


def test_label_steps_nearest_and_dropped_outside():
    idx = np.arange(0, 120, 2)                         # 60 fps -> 30 Hz
    steps = label_steps(np.array([1, 21, 22, 500]), 1, idx)
    assert steps.tolist() == [0, 10, 10]


def test_make_targets_peaks_and_hitter_mask():
    hit_t, far_t, mask = make_targets(20, np.array([5, 12]), np.array([1, -1]), sigma=1.0, hitter_radius=1)
    assert hit_t[5] == 1.0 and hit_t[12] == 1.0 and 0.5 < hit_t[6] < 0.7
    assert mask[4:7].all() and not mask[12] and far_t[5] == 1.0


def test_random_window_fits():
    rng = np.random.default_rng(0)
    assert random_window(100, 256, rng) == (0, 100)
    s, e = random_window(1000, 256, rng)
    assert e - s == 256 and 0 <= s and e <= 1000


def test_choose_peak_config_finds_the_obvious_setting():
    hit = np.zeros(100)
    hit[[10, 40, 70]] = 0.8
    hit[[20, 50]] = 0.3            # false peaks only a 0.5 threshold rejects
    best = choose_peak_config([(hit, np.zeros(100), np.array([10, 40, 70]), np.array([0, 0, 0]))],
                              thresholds=[0.2, 0.5], spacings_sec=[0.1, 0.3])
    assert best["recall"] == 1.0 and best["precision"] == 1.0 and best["threshold"] == 0.5


def _synthetic_sample(seed):
    """Triangle-wave shuttle; a hit at every turn. The turn nearest the far player
    (y = 300) is at odd seconds, the near player's (y = 550) at even seconds."""
    sig = make_signals(n=300, fps=30.0)
    frames = np.array([31, 61, 91, 121, 151, 181, 211, 241, 271])
    hitters = np.array([(i + 1) % 2 for i in range(len(frames))])   # t=1 s far, t=2 s near, ...
    return thd.Sample(sig, frames, hitters, f"m{seed}")


def test_train_learns_an_easy_synthetic_task():
    model, meta = thd.train([_synthetic_sample(0), _synthetic_sample(1)], [_synthetic_sample(2)],
                            epochs=150, hidden=16, levels=4, window=128, batch=4, lr=3e-3,
                            seed=0, device="cpu", log=lambda *a: None)
    assert meta["val_metrics"]["recall"] >= 0.8
    assert meta["feature_dim"] == 90 and meta["hidden"] == 16
    assert 0.0 < meta["threshold"] < 1.0
```

- [ ] **Step 2: Run the tests to verify they fail.**

Run: `PYTHONUTF8=1 ./.venv/Scripts/python.exe -B -m pytest -q -p no:cacheprovider tests/test_hit_training.py`

Expected: they fail with `ModuleNotFoundError`.

- [ ] **Step 3: Implement the pure training helpers.**

```python
# badminton_analysis/stroke/hit_training.py
"""Pure training helpers for the hit detector (no torch): targets, windows, peak config."""
import numpy as np

from .hit_detector import pick_peaks
from .hit_features import TARGET_HZ
from .hit_metrics import summarize


def label_steps(hit_frames, first_frame, idx):
    idx = np.asarray(idx)
    if idx.size == 0:
        return np.zeros(0, dtype=np.int64)
    native = np.asarray(hit_frames, dtype=np.int64) - int(first_frame)
    keep = (native >= idx[0]) & (native <= idx[-1])
    pos = np.searchsorted(idx, native[keep])
    pos = np.clip(pos, 0, idx.size - 1)
    prev = np.clip(pos - 1, 0, idx.size - 1)
    nearer_prev = np.abs(idx[prev] - native[keep]) <= np.abs(idx[pos] - native[keep])
    return np.where(nearer_prev, prev, pos).astype(np.int64)


def make_targets(n_steps, hit_steps, hitters, sigma=1.0, hitter_radius=2):
    t = np.arange(n_steps)
    hit_t = np.zeros(n_steps, np.float32)
    far_t = np.zeros(n_steps, np.float32)
    mask = np.zeros(n_steps, bool)
    for s, who in zip(np.asarray(hit_steps), np.asarray(hitters)):
        hit_t = np.maximum(hit_t, np.exp(-0.5 * ((t - s) / sigma) ** 2).astype(np.float32))
        if who in (0, 1):
            lo, hi = max(0, s - hitter_radius), min(n_steps, s + hitter_radius + 1)
            mask[lo:hi] = True
            far_t[lo:hi] = float(who)
    return hit_t, far_t, mask


def random_window(n_steps, length, rng):
    if n_steps <= length:
        return 0, n_steps
    start = int(rng.integers(0, n_steps - length + 1))
    return start, start + length


def choose_peak_config(samples, thresholds, spacings_sec, tol_sec=0.2):
    best = None
    for th in thresholds:
        for sp in spacings_sec:
            spacing = max(1, int(round(sp * TARGET_HZ)))
            truth_t, det_t, truth_side, det_side = [], [], [], []
            offset = 0.0
            for hit, far, steps, far_truth in samples:
                peaks = pick_peaks(hit, th, spacing)
                det_t += [offset + p / TARGET_HZ for p in peaks]
                det_side += ["far" if far[p] >= 0.5 else "near" for p in peaks]
                truth_t += [offset + s / TARGET_HZ for s in steps]
                truth_side += [{0: "near", 1: "far"}.get(int(w)) for w in far_truth]
                offset += len(hit) / TARGET_HZ + 10.0       # keep samples apart in time
            s = summarize(truth_t, det_t, tol_sec, truth_side, det_side)
            s.update({"threshold": float(th), "min_spacing_sec": float(sp)})
            if best is None or s["f1"] > best["f1"]:
                best = s
    return best
```

- [ ] **Step 4: Implement the training script.**

```python
# scripts/train_hit_detector.py
"""Train the learned hit detector (spec 7).

Usage:
  python scripts/train_hit_detector.py --dataset data/hit_dataset \
      --val-match Kento_MOMOTA_Viktor_AXELSEN_Malaysia_Masters_2020_Finals \
      --out weights/hit-detector.pt [--epochs 40] [--init weights/hit-detector.pt] [--device cuda]

Every data/hit_dataset/<match>/*.npz is one labelled rally (scripts/build_hit_dataset.py).
The validation match is held out entirely; the decision threshold and peak spacing are
chosen on it. Writes the state dict plus a JSON sidecar (hit_model.save_hit_model).
"""
import argparse
import glob
import os
import sys
import time
from collections import namedtuple

import numpy as np
import torch
import torch.nn.functional as F

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from badminton_analysis.stroke import hit_model as hm  # noqa: E402
from badminton_analysis.stroke.hit_augment import augment, jittered_index  # noqa: E402
from badminton_analysis.stroke.hit_detector import predict_scores  # noqa: E402
from badminton_analysis.stroke.hit_features import (  # noqa: E402
    FEATURE_DIM, FEATURE_SCHEMA, TARGET_HZ, compute_features)
from badminton_analysis.stroke.hit_signals import RawSignals  # noqa: E402
from badminton_analysis.stroke.hit_training import (  # noqa: E402
    choose_peak_config, label_steps, make_targets, random_window)

Sample = namedtuple("Sample", "sig hit_frames hitter match")
THRESHOLDS = [round(x, 2) for x in np.arange(0.2, 0.85, 0.05)]
SPACINGS_SEC = [0.2, 0.27, 0.33, 0.4]


def load_dataset(dataset_dir):
    samples = []
    for path in sorted(glob.glob(os.path.join(dataset_dir, "*", "*.npz"))):
        with np.load(path) as z:
            sig = RawSignals(float(z["fps"]), int(z["first_frame"]), z["shuttle"].astype(np.float32),
                             z["keypoints"].astype(np.float32), z["court_corners"].astype(np.float32),
                             tuple(int(v) for v in z["image_size"]))
            samples.append(Sample(sig, z["hit_frames"].astype(np.int64), z["hitter"].astype(np.int64),
                                  str(z["match"])))
    return samples


def _labels_for(sample, sig, idx):
    """Hit steps and their hitter codes, dropping hits outside the resampled span."""
    native = sample.hit_frames - sig.first_frame
    inside = ((native >= idx[0]) & (native <= idx[-1])) if len(idx) else np.zeros(len(native), bool)
    return label_steps(sample.hit_frames, sig.first_frame, idx), sample.hitter[inside]


def _example(sample, rng, augmented):
    sig = augment(sample.sig, rng) if augmented else sample.sig
    idx = jittered_index(sig.n_frames, sig.fps, rng) if augmented else None
    feats, idx = compute_features(sig, idx)
    steps, hitters = _labels_for(sample, sig, idx)
    hit_t, far_t, mask = make_targets(len(idx), steps, hitters)
    return feats, hit_t, far_t, mask


def _batch(examples, window, rng):
    xs, hs, fs, ms = [], [], [], []
    for feats, hit_t, far_t, mask in examples:
        s, e = random_window(len(feats), window, rng)
        pad = window - (e - s)
        xs.append(np.pad(feats[s:e], ((0, pad), (0, 0))))
        hs.append(np.pad(hit_t[s:e], (0, pad)))
        fs.append(np.pad(far_t[s:e], (0, pad)))
        ms.append(np.pad(mask[s:e], (0, pad)))
    return [torch.from_numpy(np.stack(a)) for a in (xs, hs, fs, ms)]


def _evaluate(model, samples):
    rows = []
    for smp in samples:
        feats, idx = compute_features(smp.sig)
        hit, far = predict_scores(model, feats)
        steps, hitters = _labels_for(smp, smp.sig, idx)
        rows.append((hit, far, steps, hitters))
    return choose_peak_config(rows, THRESHOLDS, SPACINGS_SEC)


def train(train_samples, val_samples, epochs=40, hidden=64, levels=4, window=256, batch=32,
          lr=1e-3, seed=0, device=None, log=print, init=None):
    device = device or ("cuda" if torch.cuda.is_available() else "cpu")
    rng = np.random.default_rng(seed)
    torch.manual_seed(seed)
    if init:
        model, init_meta = hm.load_hit_model(init, device=device)
        hidden, levels = int(init_meta["hidden"]), int(init_meta["levels"])
        model.train()
    else:
        model = hm.HitTCN(FEATURE_DIM, hidden=hidden, levels=levels).to(device)
        clean = np.concatenate([compute_features(s.sig)[0] for s in train_samples])
        model.feat_mean.copy_(torch.from_numpy(clean.mean(0)))
        model.feat_std.copy_(torch.from_numpy(clean.std(0) + 1e-3))
    pos = sum(len(s.hit_frames) for s in train_samples)
    steps = sum(len(compute_features(s.sig)[1]) for s in train_samples)
    pos_weight = torch.tensor(max(1.0, (steps - pos) / max(pos, 1) / 4.0), device=device)
    opt = torch.optim.AdamW(model.parameters(), lr=lr, weight_decay=1e-4)
    sched = torch.optim.lr_scheduler.CosineAnnealingLR(opt, T_max=max(1, epochs))
    best, best_state = None, None
    for epoch in range(1, epochs + 1):
        model.train()
        order = rng.permutation(len(train_samples))
        losses = []
        for start in range(0, len(order), batch):
            exs = [_example(train_samples[i], rng, augmented=True) for i in order[start:start + batch]]
            x, ht, ft, m = (t.to(device) for t in _batch(exs, window, rng))
            hit_logit, far_logit = model(x.float())
            loss = F.binary_cross_entropy_with_logits(hit_logit, ht.float(), pos_weight=pos_weight)
            if m.any():
                loss = loss + F.binary_cross_entropy_with_logits(far_logit[m], ft[m].float())
            opt.zero_grad()
            loss.backward()
            opt.step()
            losses.append(float(loss))
        sched.step()
        if epoch % 5 == 0 or epoch == epochs:
            model.eval()
            metrics = _evaluate(model, val_samples)
            log(f"epoch {epoch}: loss {np.mean(losses):.4f} val recall {metrics['recall']:.3f} "
                f"precision {metrics['precision']:.3f} hitter {metrics['hitter_correct']}")
            if best is None or metrics["f1"] > best["f1"]:
                best = metrics
                best_state = {k: v.detach().clone() for k, v in model.state_dict().items()}
    model.load_state_dict(best_state)
    model.eval()
    meta = {"schema": hm.MODEL_SCHEMA, "feature_schema": FEATURE_SCHEMA, "feature_dim": FEATURE_DIM,
            "target_hz": TARGET_HZ, "hidden": hidden, "levels": levels,
            "threshold": best["threshold"], "min_spacing_sec": best["min_spacing_sec"],
            "train_matches": sorted({s.match for s in train_samples}),
            "val_matches": sorted({s.match for s in val_samples}),
            "val_metrics": {k: best[k] for k in ("truth", "detected", "matched", "recall",
                                                  "precision", "f1", "hitter_correct")},
            "epochs": epochs, "created_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    return model, meta


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--dataset", required=True, action="append")
    p.add_argument("--val-match", required=True)
    p.add_argument("--out", default=os.path.join(ROOT, "weights", "hit-detector.pt"))
    p.add_argument("--epochs", type=int, default=40)
    p.add_argument("--init")
    p.add_argument("--device")
    a = p.parse_args(argv)
    samples = [s for d in a.dataset for s in load_dataset(d)]
    train_s = [s for s in samples if s.match != a.val_match]
    val_s = [s for s in samples if s.match == a.val_match]
    if not train_s or not val_s:
        raise SystemExit(f"need training and validation rallies (val match {a.val_match!r})")
    print(f"{len(train_s)} training rallies, {len(val_s)} validation rallies")
    model, meta = train(train_s, val_s, epochs=a.epochs, device=a.device, init=a.init)
    hm.save_hit_model(model.cpu(), meta, a.out)
    print(f"saved {a.out} (+ {hm.meta_path(a.out)}): val {meta['val_metrics']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 5: Run the tests to verify they pass.**

Run: `PYTHONUTF8=1 ./.venv/Scripts/python.exe -B -m pytest -q -p no:cacheprovider tests/test_hit_training.py`

Expected: 5 passed. The synthetic training test runs on CPU in under about 60 s. If it misses
the 0.8 recall bar, first print `meta["val_metrics"]` and the threshold. The task is
deliberately easy, because the hits sit exactly on shuttle turns. A miss therefore usually
points to a target/step misalignment in `label_steps` or `make_targets`, not to too few epochs.

- [ ] **Step 6: Commit.**

```bash
git add badminton_analysis/stroke/hit_training.py scripts/train_hit_detector.py tests/test_hit_training.py
git commit -m "feat(hits): training loop with held-out match and peak-config selection

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 13: Acquire, align and build the ShuttleSet dataset (controller-run; owner fetches videos)

No production code. This produces `data/` artefacts, which are never committed, and records
the results in the status file.

**Files:** `.ai/workstreams/learned-hit-detection.md` (milestone)

- [ ] **Step 1: Fetch the labels** (MIT; small).

```bash
git clone -q --depth 1 --filter=blob:none --sparse https://github.com/wywyWang/CoachAI-Projects.git data/shuttleset/_repo
git -C data/shuttleset/_repo sparse-checkout set ShuttleSet/set
mkdir -p data/shuttleset/labels && cp -r data/shuttleset/_repo/ShuttleSet/set/. data/shuttleset/labels/
ls data/shuttleset/labels | head
```

Expected: `match.csv`, `homography.csv` and 44 match folders.

- [ ] **Step 2: Ask the owner to fetch the videos.** Print the list with URLs from `match.csv`,
  and ask the owner to save each one as `data/shuttleset/videos/<video>.mp4`, named exactly by
  the `video` column. Wait for confirmation.

```bash
PYTHONUTF8=1 ./.venv/Scripts/python.exe -c "
import csv
want = {28,44,35,21,13,4,42}
for r in csv.DictReader(open('data/shuttleset/labels/match.csv', encoding='utf-8')):
    if int(r['id']) in want: print(r['id'], r['video'], r['url'])"
```

- [ ] **Step 3: Run the alignment check on each fetched match.**

```bash
for m in $(ls data/shuttleset/videos | sed 's/\.mp4$//'); do PYTHONUTF8=1 ./.venv/Scripts/python.exe scripts/check_shuttleset_alignment.py --match "$m"; done
```

Expected: `ACCEPTED` for most. If a match is `REJECTED`, ask the owner for the next reserve
(4, then 42). Keeping 4 training matches plus the validation match (13) takes priority. If 13
is rejected, the first accepted reserve becomes the validation match, and the change is
recorded.

- [ ] **Step 4: Build the dataset in the background** (about 15–30 h; resumable). Use the
  TrackNetV3 mode the match pipeline will use. Start with `weight` (today's default); Task 15
  may switch the default to `nonoverlap`, in which case the dataset is rebuilt only if the
  Task 15 comparison shows the mode matters.

```bash
for m in $(ls data/shuttleset/alignment/*.json | xargs -n1 basename | sed 's/\.json$//'); do PYTHONUTF8=1 ./.venv/Scripts/python.exe scripts/build_hit_dataset.py --match "$m" --tracknet-mode weight >> data/hit_dataset/build.log 2>&1; done
```

- [ ] **Step 5: Sanity-check the dataset.**

```bash
PYTHONUTF8=1 ./.venv/Scripts/python.exe -c "
import glob, numpy as np, sys
sys.path.insert(0,'.')
from badminton_analysis.stroke.shuttle_turns import shuttle_turn_events
for d in sorted(glob.glob('data/hit_dataset/*/')):
    if '_runs' in d: continue
    files = glob.glob(d + '*.npz'); hits = 0; near = 0; aligned = 0
    for f in files:
        z = np.load(f); hits += len(z['hit_frames']); fps = float(z['fps'])
        ev = np.array(shuttle_turn_events(z['shuttle'], fps)) + 1
        aligned += sum(bool(len(ev)) and np.min(np.abs(ev - h)) <= round(0.1*fps) for h in z['hit_frames'])
        near += (z['hitter'] == 0).sum()
    print(d, len(files), 'rallies', hits, 'hits', f'near {near/hits:.2f}', f'turn-aligned {aligned/hits:.2f}')"
```

Expected per match:
- tens of rallies and hundreds of hits;
- a near share of about 0.5;
- a turn-aligned share of about 0.6 or more, the same bar as the alignment check.

If a match is far below that, stop and investigate before training.

- [ ] **Step 6: Record a milestone** with the helper (`update`). It should give, per match, the
  accepted offsets, the rally and hit counts, and the build hours. Commit the status file.

---

### Task 14: Train, then check against the held-out match (controller-run)

**Files:** `.ai/workstreams/learned-hit-detection.md` (milestone)

- [ ] **Step 1: Train.**

```bash
PYTHONUTF8=1 ./.venv/Scripts/python.exe scripts/train_hit_detector.py --dataset data/hit_dataset --val-match Kento_MOMOTA_Viktor_AXELSEN_Malaysia_Masters_2020_Finals --out weights/hit-detector.pt --epochs 40 --device cuda
```

Expected: a log line every 5 epochs, then `saved weights/hit-detector.pt`.

- [ ] **Step 2: Apply the in-domain stop rule (spec §10.3).** Read `val_metrics` in
  `weights/hit-detector.json`.
  - **Recall ≥ 0.85:** continue.
  - **Below that:** stop. Investigate features and model on the held-out match, never on the
    owner's labels. Check by printing per-rally recall:
    - hits mis-mapped by the alignment offset;
    - player sides swapped;
    - the near/far share of missed hits.

  Fix, re-train, and record each attempt.

- [ ] **Step 3: Record a milestone** with the validation metrics and the model's sha256
  (`certutil -hashfile weights/hit-detector.pt SHA256` in PowerShell). Commit the status file.

---

### Task 15: Validate on the owner's labels, choose the TrackNetV3 mode, run full matches, write the outcome

**Files:**
- Modify: `badminton_analysis/system.py`, `main.py` (the `tracknet_mode` default), only if
  `nonoverlap` is chosen
- Modify: `docs/superpowers/specs/2026-10-04-learned-hit-detection-design.md` (append §14,
  the outcome addendum)
- Modify: `.ai/workstreams/learned-hit-detection.md`

- [ ] **Step 1: Confirm the labels exist.** `outputs/axelsen-hit-labels/LABELS.md` must have
  hit rows in every clip section. If they're missing, ask the owner and wait. Nothing below is
  meaningful without them.

- [ ] **Step 2: Run the four stretches in three configurations.**

```bash
STEM="YTDown.com_YouTube_Nice-Angle-4K60FPS-Viktor-Axelsen-vs-Kod_Media_HhrFqbM-x4Q_001_1080p"
R=outputs/axelsen-hit-labels/runs
n=1
for s in 280.5 527.5 804 1025; do
  for cfg in "rule weight" "learned_weight weight" "learned_nonoverlap nonoverlap"; do
    set -- $cfg; out="$R/rally${n}_$1"; mkdir -p "$out"; cp "outputs/$STEM/court_annotations.txt" "$out/"
    hm=(); [ "$1" != rule ] && hm=(--hit-model weights/hit-detector.pt)
    PYTHONUTF8=1 ./.venv/Scripts/python.exe -B main.py --video-path "$R/src/rally${n}_${s}.mp4" --template-path "templates/_auto_$STEM.png" --output-dir "$out" --display false --language en --pose-family rtmpose --visualize-positions false --audio false --analyze-technique --racket-model weights/yolo11n-racket.pt --bst-model weights/bst-shuttleset.pt --tracknet-model weights/tracknet.pt --inpaintnet-model weights/inpaintnet.pt --tracknet-mode "$2" "${hm[@]}" > "$out/analyze.log" 2>&1
  done
  n=$((n+1))
done
```

- [ ] **Step 3: Score every configuration and the baseline.**

```bash
for sfx in rule learned_weight learned_nonoverlap; do PYTHONUTF8=1 ./.venv/Scripts/python.exe scripts/score_hits.py --labels outputs/axelsen-hit-labels/LABELS.md --runs-dir outputs/axelsen-hit-labels/runs --suffix $sfx --json outputs/axelsen-hit-labels/score_$sfx.json; done
PYTHONUTF8=1 ./.venv/Scripts/python.exe scripts/score_hits.py --labels outputs/axelsen-hit-labels/LABELS.md --runs-dir outputs/axelsen-hit-labels/runs --suffix learned_weight --baseline --json outputs/axelsen-hit-labels/score_baseline.json
```

- [ ] **Step 4: Decide.**
  - **`learned_nonoverlap` meets the bar:** switch the default to `nonoverlap`. Change
    `tracknet_mode="weight"` to `"nonoverlap"` in `BadmintonAnalysisSystem.__init__`, and
    change `--tracknet-mode`'s `default` in `main.py`. Add a test to `tests/test_hit_wiring.py`
    asserting `"default='nonoverlap'"` appears in `inspect.getsource(main.main)` next to
    `--tracknet-mode`. Run Task 7's and Task 9's tests, then commit.
  - **Only `learned_weight` meets the bar:** keep the default `weight`.
  - **Neither meets the bar:** go to Task 16 (the stop rule). The learned detector stays
    opt-in. The default install stays on the contact rule, because `app.py` only passes
    `--hit-model` when weights exist. Do not install the weights under `weights/` for the
    app, and say so in the outcome.

- [ ] **Step 5: Run the full Axelsen match** with the chosen mode, through the same `main.py`
  flags as the web app plus `--hit-model` (this takes hours; run it in the background). Then
  check:

```bash
PYTHONUTF8=1 ./.venv/Scripts/python.exe -c "
import json, collections
d = 'outputs/YTDown.com_YouTube_Nice-Angle-4K60FPS-Viktor-Axelsen-vs-Kod_Media_HhrFqbM-x4Q_001_1080p/'
h = json.load(open(d + 'hits.json')); r = json.load(open(d + 'rally_segments.json'))
print(h['source'], h['reason'], len(h['hits']), collections.Counter(x['hitter'] for x in h['hits']))
print('rally signal', r['detection']['signal'], len(r['rallies']), 'rallies')
print('hits outside rallies', sum(x['rally_id'] is None for x in h['hits']))"
```

Expected:
- `learned`, several hundred hits split between `lower` and `upper`;
- the rally signal is probably `shuttle`, with its count recorded;
- the labelled stretches' hits fall inside rallies. Check the four spans: every labelled hit
  time should sit inside some `[start_sec, end_sec]`. Report the fraction.

  If the shuttle signal's rallies miss labelled hits, record it. Pinning
  `rally_signal="swing"` for this footage class is a follow-up, not part of this plan.

- [ ] **Step 6: Run DJI 0010.** Expect the fallback:

```bash
PYTHONUTF8=1 ./.venv/Scripts/python.exe -c "
import json; h = json.load(open('outputs/<dji 0010 output dir>/hits.json')); print(h['source'], h['reason'], h['shuttle_coverage_in_play'])"
```

Expected: `contact_rule`, with a coverage reason. If the source is `learned`, record the
coverage and a sample of the hits, and flag it.

- [ ] **Step 7: Append the outcome addendum** to the spec, as §14 "Outcome". It should cover:
  - the dataset (matches, offsets, rallies, hits, build hours);
  - the held-out metrics;
  - a table of counts **and** ratios for the four configurations (rule, both learned modes,
    baseline);
  - the mode decision;
  - the full-match results (hit count, split, stage timings from `analyze.log`, rally signal
    and its check);
  - the DJI fallback;
  - the `hit_signals.npz` spec note;
  - any deviation.

- [ ] **Step 8: Run the full suite.**

```bash
PYTHONUTF8=1 ./.venv/Scripts/python.exe -B -m pytest -q -p no:cacheprovider --basetemp "$TEMP/claude/lhd-final"
```

Expected: everything passes except the 3 known symlink skips. Record the count and the commit
SHA in the status file through the helper (`update` with the verification command, result and
commit).

- [ ] **Step 9: Commit.**

```bash
git add docs/superpowers/specs/2026-10-04-learned-hit-detection-design.md .ai/workstreams/learned-hit-detection.md
git commit -m "docs(spec): learned hit detection outcome addendum

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 16 (contingent, spec §10.3): fine-tune on the owner's own labels

Run this **only if** Task 15 Step 4 finds no configuration meeting the bar while the held-out
metrics were strong. Otherwise, mark it "not needed" in the status file.

**Files:**
- Create: `scripts/build_own_hit_dataset.py`
- Test: extend `tests/test_build_hit_dataset.py`

**Interfaces:**
- Consumes:
  - `parse_labels` (`scripts/score_hits.py`)
  - `run_pipeline_on_clip`, `encode_hits` (Task 11)
  - `STROKE7` (Task 10)
- Produces: `own_hits(spans_hits, span_start) -> list[dict]`, which converts `LABELS.md` rows
  into the `encode_hits` input format (`t` in clip-relative seconds, `side` from near/far,
  `stroke7` from the stroke column).

- [ ] **Step 1: Ask the owner for about 300 more hits.** Use a **new** sheet,
  `outputs/own-hit-labels/LABELS.md`, in the same format, with clips I cut the same way as for
  the test labels. Choose stretches that don't overlap the four test stretches, and cover both
  Axelsen and DJI footage. Wait for the labels.

- [ ] **Step 2: Write the failing test** (append to `tests/test_build_hit_dataset.py`):

```python
def test_own_hits_converts_label_rows():
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    import build_own_hit_dataset as bo
    rows = [{"t": 281.0, "player": "near", "stroke": "serve"},
            {"t": 282.5, "player": "far", "stroke": "push"}]
    out = bo.own_hits(rows, 280.5)
    assert out == [{"t": 0.5, "side": "lower", "stroke7": "serve", "type_raw": "serve"},
                   {"t": 2.0, "side": "upper", "stroke7": None, "type_raw": "push"}]
```

- [ ] **Step 3: Implement** `scripts/build_own_hit_dataset.py`:

```python
"""Own-footage fine-tuning set from a LABELS.md sheet (spec 10.3).

Usage:
  python scripts/build_own_hit_dataset.py --labels outputs/own-hit-labels/LABELS.md \
      --clips-dir outputs/own-hit-labels/src --corners "[[x,y],...]" --out data/own_hit_dataset \
      [--tracknet-mode weight]

Clip N is <clips-dir>/rally<N>.mp4 (cut from the source starting at the heading's START s);
each becomes data/own_hit_dataset/own/rally<N>.npz in build_hit_dataset's format.
"""
import argparse
import ast
import os
import shutil
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import build_hit_dataset as bd  # noqa: E402
import shuttleset_labels as sl  # noqa: E402
from score_hits import parse_labels  # noqa: E402

_SIDE = {"near": "lower", "far": "upper"}


def own_hits(rows, span_start):
    return [{"t": round(r["t"] - span_start, 6), "side": _SIDE.get(r.get("player")),
             "stroke7": r.get("stroke") if r.get("stroke") in sl.STROKE7 else None,
             "type_raw": r.get("stroke") or ""} for r in rows]


def main(argv=None):
    from badminton_analysis.system import load_runtime_dependencies

    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--labels", required=True)
    p.add_argument("--clips-dir", required=True)
    p.add_argument("--corners", required=True)
    p.add_argument("--out", default=os.path.join(ROOT, "data", "own_hit_dataset"))
    p.add_argument("--tracknet-mode", default="weight", choices=["weight", "nonoverlap"])
    a = p.parse_args(argv)
    load_runtime_dependencies()
    corners = ast.literal_eval(a.corners)
    with open(a.labels, encoding="utf-8") as fh:
        spans, hits, _ = parse_labels(fh.read())
    out_dir = os.path.join(a.out, "own")
    os.makedirs(out_dir, exist_ok=True)
    for n, (start, _end) in sorted(spans.items()):
        if not hits.get(n):
            continue
        run_dir = os.path.join(a.out, "_runs", f"rally{n}")
        sig = bd.run_pipeline_on_clip(os.path.join(a.clips_dir, f"rally{n}.mp4"), run_dir,
                                      corners, a.tracknet_mode)
        labels = bd.encode_hits(own_hits(hits[n], start), 0.0, 0.0, sig.fps, sig.n_frames)
        np.savez_compressed(os.path.join(out_dir, f"rally{n}.npz"), fps=sig.fps,
                            first_frame=sig.first_frame, shuttle=sig.shuttle,
                            keypoints=sig.keypoints, court_corners=sig.court_corners,
                            image_size=np.asarray(sig.image_size), match="own", set=0, rally=n,
                            **labels)
        shutil.rmtree(run_dir, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run the test** (it passes), then commit.

```bash
git add scripts/build_own_hit_dataset.py tests/test_build_hit_dataset.py
git commit -m "feat(hits): own-footage fine-tuning dataset from a LABELS.md sheet

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

- [ ] **Step 5: Fine-tune.** Build the own dataset, fine-tune from the ShuttleSet model, then
  repeat Task 15 Steps 2–4 and 7.
  - Hold out one own clip as an extra validation sample by moving its npz to
    `data/own_hit_dataset/own_val/` with `match` set to `own_val`, and pass that as
    `--val-match` along with a second `--dataset`.
  - Then run `train_hit_detector.py --dataset data/hit_dataset --dataset data/own_hit_dataset --val-match own_val --init weights/hit-detector.pt --epochs 20 --out weights/hit-detector-ft.pt`.
  - Score with `--hit-model weights/hit-detector-ft.pt`.
  - If it is still below the bar, stop and report (spec §10.3). The detector stays opt-in.
