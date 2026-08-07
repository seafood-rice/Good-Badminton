# BST Stroke-Type Recognition Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Label each stroke in an elevated-back-court singles rally with a coarse type
(serve/clear/smash/drop/drive/net) by running the MIT pretrained BST model for inference on
inputs built from the existing match pipeline.

**Architecture:** A new `badminton_analysis/stroke_recog/` package runs as a post-processing
stage over a rally. It reuses the match pipeline's per-frame pose/shuttle/court data and its
contact detection to build per-hit BST inputs, runs the vendored pretrained model, maps the
25 ShuttleSet classes to coarse 6 with a confidence gate, and attaches labels to the output.
Feature is fully optional: absent weights → match analysis unchanged.

**Tech Stack:** Python 3.11, PyTorch 2.4 (lazy-imported), NumPy; vendored MIT BST source;
existing RTMPose/YOLO 17-keypoint COCO pose; Flask + vanilla JS UI.

## Global Constraints

- Never commit `weights/` or `data/`; never `git add -A/-u/.` — stage files explicitly.
- BST source is MIT: vendor with its LICENSE header retained. Pretrained weights are
  git-ignored under `weights/`, auto-discovered at runtime, with fetch instructions documented.
- Match pipeline behavior with NO BST weights present must be byte-for-byte unchanged.
- Torch and the BST model are lazy-imported (never at module top level of anything on the
  `--help`/weightless path).
- Windows/venv: run everything with `.venv/Scripts/python.exe` and `PYTHONUTF8=1`.
- Coarse labels only: `serve, clear, smash, drop, drive, net`; plus runtime `uncertain`.
- Single seam for the confidence threshold: module constant `MIN_STROKE_CONF` (start 0.5).

---

## File Structure

- `third_party/bst/` — vendored MIT BST model + inference code (Task 1). Contains
  `CONTRACT.md` pinning the exact I/O (model import path, `SEQ_LEN`, tensor shapes, ordered
  25-class list). This file is the source of truth consumed by Tasks 2–4.
- `badminton_analysis/stroke_recog/__init__.py`
- `badminton_analysis/stroke_recog/bst_model.py` — load vendored model + weights; `predict`.
- `badminton_analysis/stroke_recog/classes.py` — 25→6 mapping + coarse selection.
- `badminton_analysis/stroke_recog/inputs.py` — pure per-hit input-tensor assembly.
- `badminton_analysis/stroke_recog/hits.py` — derive `[(frame, hitter_side)]` from contacts.
- `badminton_analysis/stroke_recog/recognizer.py` — orchestration seam.
- `badminton_analysis/system.py` — call the recognizer post-loop; write labels (Task 6).
- `app.py` — `_bst_weights()` discovery + `/api/models` + subprocess flag (Task 7).
- `static/kestrel.js`, `static/kestrel.css` — output surfaces (Task 8).
- Tests under `tests/`.

---

### Task 1: Vendor BST + pin the I/O contract

**Files:**
- Create: `third_party/bst/` (vendored source), `third_party/bst/CONTRACT.md`
- Create: `badminton_analysis/stroke_recog/__init__.py`, `badminton_analysis/stroke_recog/bst_model.py`
- Test: `tests/test_bst_model.py`

**Interfaces:**
- Produces: `bst_model.load_bst(weights_path) -> model` and
  `bst_model.predict(model, pose, shuttle, positions) -> np.ndarray` of shape `(N_CLASSES,)`
  logits, where the input array shapes and `N_CLASSES=25` are exactly as recorded in
  `CONTRACT.md`. Produces `CONTRACT.md` documenting `SEQ_LEN`, `POSE_SHAPE`, `SHUTTLE_SHAPE`,
  `POS_SHAPE`, and `CLASS_NAMES` (ordered list of 25 strings) — the authoritative values for
  Tasks 2–4.

- [ ] **Step 1: Vendor the source.** Clone https://github.com/Va6lue/BST-Badminton-Stroke-type-Transformer,
  copy ONLY the model-definition module(s) and the minimal inference helpers for the
  `main_on_shuttleset` BST variant into `third_party/bst/`, keeping the repo's LICENSE file.
  Strip training-only code. Record the exact model class name and import path.

- [ ] **Step 2: Fetch the ShuttleSet 25-merged pretrained weights** (repo's Google Drive link)
  into `weights/bst-shuttleset.pt` (git-ignored). Add the fetch instructions to `CONTRACT.md`.

- [ ] **Step 3: Read the vendored source and RECORD in `CONTRACT.md` the concrete facts:**
  `SEQ_LEN` (README says 30), the input tensor shapes for pose / shuttle / player-positions,
  `N_CLASSES` (25), and the ordered `CLASS_NAMES` list (from the model's class-index/label
  file). These come from the source — do not invent them.

- [ ] **Step 4: Write `bst_model.py`** with `load_bst(weights_path)` (lazy `import torch`,
  build the vendored model, `load_state_dict`, `.eval()`) and
  `predict(model, pose, shuttle, positions)` returning a `(25,)` numpy logits array. Torch
  imported inside the functions only.

- [ ] **Step 5: Write the smoke test** `tests/test_bst_model.py::test_predict_shape` — build
  zero arrays of the CONTRACT shapes, load the real weights if present else `pytest.skip`,
  and assert `predict(...).shape == (25,)`. (This is an integration smoke test gated on the
  weights file existing; it must skip cleanly when absent so CI stays hermetic.)

- [ ] **Step 6: Run** `PYTHONUTF8=1 .venv/Scripts/python.exe -m pytest tests/test_bst_model.py -v`
  Expected: PASS (or SKIP if weights absent). Also run the repo's own `bst_infer.py` on its
  sample once to confirm the vendored model + weights produce sane output; note the result in
  `CONTRACT.md`.

- [ ] **Step 7: Commit.**
```bash
git add third_party/bst badminton_analysis/stroke_recog/__init__.py badminton_analysis/stroke_recog/bst_model.py tests/test_bst_model.py .gitignore
git commit -m "feat(bst): vendor MIT BST model + pin ShuttleSet-25 I/O contract"
```

---

### Task 2: Coarse class mapping (`classes.py`)

**Files:**
- Create: `badminton_analysis/stroke_recog/classes.py`
- Test: `tests/test_bst_classes.py`

**Interfaces:**
- Consumes: `CONTRACT.md` `CLASS_NAMES` (ordered 25) from Task 1.
- Produces: `COARSE = ("serve","clear","smash","drop","drive","net")`;
  `FINE_TO_COARSE: dict[str,str]` (each of the 25 class names → one COARSE label);
  `to_coarse(logits: np.ndarray, min_conf: float) -> tuple[str, float]` returning
  `(coarse_label_or_"uncertain", confidence)`.

- [ ] **Step 1: Write the failing test** `tests/test_bst_classes.py`:
```python
import numpy as np
from badminton_analysis.stroke_recog.classes import COARSE, FINE_TO_COARSE, to_coarse, CLASS_NAMES

def test_every_fine_class_maps_to_one_coarse():
    assert set(FINE_TO_COARSE) == set(CLASS_NAMES)
    assert all(v in COARSE for v in FINE_TO_COARSE.values())

def test_to_coarse_sums_bucket_probs_and_argmaxes():
    # two fine classes in the 'smash' bucket dominate -> 'smash'
    logits = np.full(len(CLASS_NAMES), -10.0)
    smash_idx = [i for i, c in enumerate(CLASS_NAMES) if FINE_TO_COARSE[c] == "smash"]
    for i in smash_idx:
        logits[i] = 5.0
    label, conf = to_coarse(logits, min_conf=0.5)
    assert label == "smash"
    assert conf > 0.5

def test_low_confidence_is_uncertain():
    logits = np.zeros(len(CLASS_NAMES))  # uniform -> max bucket prob well under a high gate
    label, conf = to_coarse(logits, min_conf=0.99)
    assert label == "uncertain"
```

- [ ] **Step 2: Run to verify it fails** (`ModuleNotFoundError` / names undefined).

- [ ] **Step 3: Implement `classes.py`.** Define `CLASS_NAMES` copied verbatim from Task 1's
  `CONTRACT.md`; `COARSE`; `FINE_TO_COARSE` using the spec's bucket rule (service→serve;
  clear/lob/defensive-lob→clear; smash/wrist-smash→smash; drop/passive-drop→drop;
  drive/driven-flight/back-court-drive/push→drive; net-shot/return-net/rush/cross-court-net→net).
  `to_coarse`: softmax the logits, sum probabilities per COARSE bucket, take the argmax bucket
  and its summed probability as `conf`; return `("uncertain", conf)` when `conf < min_conf`.

- [ ] **Step 4: Run** `... -m pytest tests/test_bst_classes.py -v` → PASS.

- [ ] **Step 5: Commit** (`feat(bst): 25→6 coarse stroke-class mapping + confidence gate`).

---

### Task 3: Per-hit input assembly (`inputs.py`)

**Files:**
- Create: `badminton_analysis/stroke_recog/inputs.py`
- Test: `tests/test_bst_inputs.py`

**Interfaces:**
- Consumes: `SEQ_LEN` and the tensor shapes from Task 1's `CONTRACT.md`; the match pipeline's
  `_analysis_frames[frame]` dicts (keys: `keypoints` (17×2 full-frame or None), `centroid`,
  `player_side`) and the two player positions + court corners.
- Produces: `build_inputs(contact_frame, frame_lookup, court_corners, video_wh) ->
  dict|None` returning `{"pose": np.ndarray POSE_SHAPE, "shuttle": np.ndarray SHUTTLE_SHAPE,
  "positions": np.ndarray POS_SHAPE}` or `None` if the window has too few posed frames.

- [ ] **Step 1: Write the failing test** `tests/test_bst_inputs.py` with a synthetic
  `frame_lookup` (dict.get) covering `SEQ_LEN` frames around a contact: assert the returned
  arrays have exactly the CONTRACT shapes; that pose is normalized relative to the player
  bbox (values roughly in [-1,1]); that shuttle is normalized to [0,1] by `video_wh`; that a
  window with all-None keypoints returns `None`; and that a window clamped at frame 0 (contact
  near the video start) still returns arrays of full `SEQ_LEN` (zero/edge-padded), not a short
  array.

- [ ] **Step 2: Run to verify it fails.**

- [ ] **Step 3: Implement `inputs.py`** as pure functions: gather the `SEQ_LEN`-frame window
  centered on `contact_frame` (clamp+pad at edges), extract the hitter pose per frame
  (bbox-relative, center-aligned per CONTRACT), the shuttle track (normalized by `video_wh`),
  and both players' feet mapped to court coordinates (normalized by `court_corners`). Missing
  frames → zero-filled per CONTRACT. Return `None` when posed-frame count `< SEQ_LEN//3`.

- [ ] **Step 4: Run** `... -m pytest tests/test_bst_inputs.py -v` → PASS.

- [ ] **Step 5: Commit** (`feat(bst): per-hit input-tensor assembly from match-pipeline data`).

---

### Task 4: Hit/hitter extraction (`hits.py`)

**Files:**
- Create: `badminton_analysis/stroke_recog/hits.py`
- Test: `tests/test_bst_hits.py`

**Interfaces:**
- Consumes: the match pipeline's `_analysis_track` (list of `{frame, racket_head, shuttle}`)
  and `badminton_analysis.stroke.events.detect_contacts`.
- Produces: `hit_events(track) -> list[dict]` each `{"frame": int, "hitter": str}` where
  `hitter` is `"lower"`/`"upper"` (from the tracked player nearest the shuttle at that frame),
  sorted by frame.

- [ ] **Step 1: Write the failing test** `tests/test_bst_hits.py`: a synthetic track with two
  shuttle direction-changes near a racket → assert two hit events at the expected frames, each
  carrying a `hitter` in `{"lower","upper"}`.

- [ ] **Step 2: Run to verify it fails.**

- [ ] **Step 3: Implement `hits.py`**: call `detect_contacts(track)` for hit frames; for each,
  set `hitter` from the `player_side` recorded in `_analysis_frames` at that frame (fall back
  to nearest-player if absent).

- [ ] **Step 4: Run** `... -m pytest tests/test_bst_hits.py -v` → PASS.

- [ ] **Step 5: Commit** (`feat(bst): hit/hitter extraction from contact detection`).

---

### Task 5: Recognizer orchestration (`recognizer.py`)

**Files:**
- Create: `badminton_analysis/stroke_recog/recognizer.py`
- Test: `tests/test_bst_recognizer.py`

**Interfaces:**
- Consumes: Tasks 1–4.
- Produces: `class StrokeRecognizer(weights_path)` with
  `label_rally(track, frame_lookup, court_corners, video_wh) -> list[dict]` each
  `{"frame","hitter","stroke","confidence","uncertain"}`; `MIN_STROKE_CONF = 0.5` module
  constant. Model + torch loaded lazily on first use; any load failure → returns `[]` (feature
  off) without raising.

- [ ] **Step 1: Write the failing test** with a **stub** model (monkeypatch `bst_model.predict`
  to return canned logits) and `build_inputs` fed synthetic frames: assert one hit yields a
  `stroke` in COARSE, another with incomplete window yields `uncertain`, and that a
  `load_bst` that raises makes `label_rally` return `[]` (no exception).

- [ ] **Step 2: Run to verify it fails.**

- [ ] **Step 3: Implement `recognizer.py`**: for each hit from `hits.hit_events`, build inputs;
  `None` inputs → `uncertain`; else `predict` → `to_coarse(logits, MIN_STROKE_CONF)`. Wrap
  model load in try/except → `[]` on failure.

- [ ] **Step 4: Run** `... -m pytest tests/test_bst_recognizer.py -v` → PASS.

- [ ] **Step 5: Commit** (`feat(bst): rally stroke-labeling orchestration + graceful fallback`).

---

### Task 6: Match-pipeline integration (`system.py`)

**Files:**
- Modify: `badminton_analysis/system.py` (post-loop, near `_run_technique_analysis`, ~line 501)
- Test: `tests/test_bst_integration.py`

**Interfaces:**
- Consumes: `StrokeRecognizer` (Task 5).
- Produces: a `strokes.json` output (`[{frame,hitter,stroke,confidence,uncertain}]`) and a
  `strokes` block in `metadata.json` with counts per coarse label; only when a BST weights path
  was provided.

- [ ] **Step 1: Write the failing test**: construct the match system with a monkeypatched
  `StrokeRecognizer` returning canned labels; assert `strokes.json` is written and
  `metadata.json` gains a `strokes` distribution; and that with `bst_weights=None` NO
  `strokes.json` is written and metadata is unchanged.

- [ ] **Step 2: Run to verify it fails.**

- [ ] **Step 3: Implement**: add a `bst_weights=None` constructor arg; after the frame loop,
  if set, run `StrokeRecognizer(bst_weights).label_rally(self._analysis_track,
  self._analysis_frames.get, self.court_roi_corners, (width,height))`, write `strokes.json`,
  and add the distribution to metadata. Guard entirely behind `bst_weights`.

- [ ] **Step 4: Run** `... -m pytest tests/test_bst_integration.py -v` → PASS. Then full suite.

- [ ] **Step 5: Commit** (`feat(bst): wire stroke recognizer into match pipeline output`).

---

### Task 7: App auto-discovery + wiring (`app.py`)

**Files:**
- Modify: `app.py` (`_bst_weights`, `/api/models`, the match-analyze route's subprocess cmd)
- Test: `tests/test_app_library.py` (extend)

**Interfaces:**
- Produces: `_bst_weights(base=None)` returning `weights/bst-shuttleset.pt` if present else
  `None`; `/api/models` gains `"bst": bool`; the match route passes `--bst-model <path>` to the
  analysis subprocess when present (and the match entry script forwards it to `bst_weights`).

- [ ] **Step 1: Write the failing test**: extend the `/api/models` test — with a tmp weights
  dir containing `bst-shuttleset.pt`, `/api/models` returns `"bst": true`; absent → `false`.

- [ ] **Step 2: Run to verify it fails.**

- [ ] **Step 3: Implement** mirroring `_racket_weights`/`_quality_weights` exactly; add the
  `--bst-model` flag plumb-through in the match analyze route and its CLI entry.

- [ ] **Step 4: Run** the focused test then full suite → PASS.

- [ ] **Step 5: Commit** (`feat(bst): weight auto-discovery + /api/models + match-route flag`).

---

### Task 8: UI output surfaces (`kestrel.js` / `kestrel.css`)

**Files:**
- Modify: `static/kestrel.js`, `static/kestrel.css`
- (No JS test framework; JS is review-verified and must be null-safe.)

**Interfaces:**
- Consumes: `strokes.json` + the metadata `strokes` distribution (Task 6).

- [ ] **Step 1: Implement**, bilingual via `state.lang`, all presence-keyed (absent → nothing):
  (a) a rally stroke **timeline** on the match results screen (ordered `serve → clear → …`,
  `uncertain` shown muted); (b) a per-type **distribution** (counts) beside existing match
  stats; (c) if the match video overlay is rendered, draw each hit's coarse label near the
  hitter at its contact frame. Every field access null-safe for old outputs lacking
  `strokes.json`.

- [ ] **Step 2: Verify** `node --check static/kestrel.js` and run the full Python suite
  (unchanged count) → PASS.

- [ ] **Step 3: Commit** (`feat(ui): match rally stroke-type timeline + distribution`).

---

### Task 9: Real-footage validation (controller, post-merge)

Not a code task — the controller runs after merge: analyze a real elevated-singles rally clip
with the weights present, dump the labeled hits, and eyeball the coarse labels against the
visible strokes (as segmentation/scores were validated). If transfer is poor, surface the
feature as experimental (mirroring the AI-quality-score decision) rather than presenting
untrustworthy labels as authoritative. Record findings in the ledger and the spec's outcome
addendum.

---

## Self-Review

**Spec coverage:** scope/footage/taxonomy → Tasks 2 (coarse 6) + 9 (singles/elevated validation);
inference-only pretrained port → Task 1; input assembly (pose/shuttle/court) → Task 3; hit/hitter
→ Task 4; class map + confidence gate → Task 2/5; output surfaces (overlay/timeline/distribution)
→ Tasks 6/8; graceful degradation + auto-discovery → Tasks 5/6/7; testing → per-task hermetic +
Task 9; risks (domain shift, format fidelity, hit detection, pose skeleton) → Tasks 1/3/4/9. All
spec sections covered.

**Placeholder scan:** The only deferred-to-implementation values (`SEQ_LEN`, tensor shapes, the
25 `CLASS_NAMES`) are pinned by Task 1 from the vendored MIT source (the authoritative
definition) and consumed by name in later tasks — this is verification-against-source, not a
placeholder. All our-side logic has concrete code/tests.

**Type consistency:** `to_coarse(logits, min_conf) -> (label, conf)`, `build_inputs(...) ->
dict|None`, `hit_events(track) -> [{frame,hitter}]`, `label_rally(...) -> [{frame,hitter,stroke,
confidence,uncertain}]`, `COARSE`/`CLASS_NAMES`/`FINE_TO_COARSE` names are consistent across
Tasks 2–8.
