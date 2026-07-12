# TrackNetV3 Dense Shuttle Tracking Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Vendor an inference-only TrackNetV3, run it as an offline pre-pass that produces a dense per-frame shuttle trajectory, and feed that trajectory into the existing contact detector + BST so match-stroke recognition fires on real broadcast footage.

**Architecture:** A pre-pass (`_run_shuttle_pretrack`) runs vendored TrackNetV3 (TrackNet tracking + InpaintNet rectification) over the whole video once, on GPU, before the streaming analysis loop. It produces `{frame_index: (x, y) | None}` in original pixel coordinates, cached to disk. When analyzing, `_capture_analysis_frame` sources its `shuttle` field from that trajectory instead of the per-frame yolo detection. The live rendered overlay keeps using `yolo11s-ball`. Everything is additive and never-fatal: with no TrackNet weights the pipeline is byte-identical to today.

**Tech Stack:** Python, PyTorch 2.5 (CUDA), OpenCV, NumPy, pandas (already present); TrackNetV3 vendored from https://github.com/qaz812345/TrackNetV3 @ commit `77c123ad4dd449b7d275f16cc43f316ba5b54042` (MIT). One new dependency: `parse` (required by the vendored files' module-level imports).

## Global Constraints

- **Never commit** model weights or datasets. Weights live in git-ignored `weights/`. Never `git add -A`/`-u`/`.`; stage explicit paths only.
- **Windows env:** run Python as `.venv/Scripts/python.exe` with `PYTHONUTF8=1`. Kill background processes by PID.
- **Lazy `torch` import** inside wrapper functions — `--help` and weightless runs must stay torch-free where they already are.
- **Never-fatal integration:** the pre-pass must never abort `process_video`; on any exception it logs and falls back to the yolo shuttle. (A mid-pipeline crash dies before `_cleanup` and corrupts the output video — the BST lesson.)
- **Zero regression when weights absent:** with no TrackNet weights, `_analysis_track`/`_analysis_frames` and all outputs are unchanged from today.
- **Vendored code stays verbatim** except the documented mechanical edits in Task 2; record the upstream commit SHA and every edit in `third_party/tracknet/CONTRACT.md`.
- **TrackNet checkpoints are NOT pure state_dicts** — they carry a `param_dict`. Load with `torch.load(..., weights_only=False)` (unlike BST). Read `seq_len` and `bg_mode` from the checkpoint's `param_dict`; never hardcode them.
- **Frame-index convention:** TrackNet `run_prediction` returns **0-based** frame indices (first `cap.read()` = Frame 0). The match loop does `frame_count += 1` *before* processing, so the first processed frame is `frame_count == 1`. The trajectory consumed by `_capture_analysis_frame` MUST be keyed `frame_count = TrackNet_frame + 1`.
- **Model space is 288×512** (H×W); `run_prediction` already scales outputs to original pixels via `img_scaler = (w/512, h/288)`. The wrapper consumes pixel coordinates — it does not rescale.

---

## File Structure

**Vendored (`third_party/tracknet/`), mirrors `third_party/bst/`:**
- `model.py` — TrackNet + InpaintNet nets (verbatim from upstream).
- `utils/__init__.py`, `utils/general.py` — constants (`HEIGHT`, `WIDTH`, `COOR_TH`), `get_model`, `generate_frames`, `to_img`, `to_img_format` (verbatim).
- `dataset.py` — `Shuttlecock_Trajectory_Dataset`, `Video_IterableDataset` (verbatim).
- `infer.py` — **our** pycocotools-free extraction: the three helpers (`get_ensemble_weight`, `predict_location`, `generate_inpaint_mask`) + `predict()` + `run_prediction()`.
- `LICENSE` (verbatim MIT), `CONTRACT.md` (new; pins commit, params, edits, weight fetch).

**App code (`badminton_analysis/shuttle_track/`), new package:**
- `tracknet.py` — inference wrapper: `load_tracknet`, `track_video`.
- `trajectory.py` — trajectory cache: key builder + load/save.

**Modified:**
- `badminton_analysis/system.py` — ctor args, `_run_shuttle_pretrack`, `_capture_analysis_frame`, `shuttle_source` in `strokes.json`.
- `main.py` — `--tracknet-model` / `--inpaintnet-model` flags + ctor wiring.
- `app.py` — `_tracknet_weights` / `_inpaintnet_weights`, `/api/models`, subprocess flags.
- `static/kestrel.js` — provenance line in the strokes.json handler.
- `requirements.txt` — add `parse`.

**Tests:** one file per task under `tests/`.

---

### Task 1: Vendor TrackNetV3 model + utils + dataset, add `parse`, LICENSE + CONTRACT

**Files:**
- Create: `third_party/tracknet/model.py`, `third_party/tracknet/utils/__init__.py`, `third_party/tracknet/utils/general.py`, `third_party/tracknet/dataset.py`, `third_party/tracknet/LICENSE`, `third_party/tracknet/CONTRACT.md`
- Modify: `requirements.txt`
- Test: `tests/test_tracknet_vendor.py`

**Interfaces:**
- Consumes: nothing (first task).
- Produces: importable vendored modules when `third_party/tracknet` is on `sys.path`: `from model import TrackNet, InpaintNet`; `from utils.general import get_model, HEIGHT, WIDTH, COOR_TH, to_img, to_img_format, generate_frames`; `from dataset import Shuttlecock_Trajectory_Dataset, Video_IterableDataset`. `get_model('TrackNet', seq_len, bg_mode)` returns a `TrackNet`; `get_model('InpaintNet')` returns an `InpaintNet`.

- [ ] **Step 1: Copy the vendored files verbatim from the pinned upstream commit**

Clone (or reuse a clone of) `https://github.com/qaz812345/TrackNetV3` at commit `77c123ad4dd449b7d275f16cc43f316ba5b54042`. Copy verbatim:
- upstream `model.py` → `third_party/tracknet/model.py`
- upstream `utils/__init__.py` → `third_party/tracknet/utils/__init__.py`
- upstream `utils/general.py` → `third_party/tracknet/utils/general.py`
- upstream `dataset.py` → `third_party/tracknet/dataset.py`
- upstream `LICENSE` → `third_party/tracknet/LICENSE`

Do NOT copy `test.py` (it imports `pycocotools` at module load), `predict.py`, `train.py`, `preprocess.py`, `error_analysis.py`, `correct_label.py`, or `utils/metric.py`/`utils/visualize.py`. The inference path does not need them.

- [ ] **Step 2: Add the `parse` dependency**

`utils/general.py` and `dataset.py` do `import parse` at module level (used only by training/data-prep helpers we never call, but the import must resolve). Append `parse` to `requirements.txt`, then install:

```bash
PYTHONUTF8=1 .venv/Scripts/python.exe -m pip install parse
```

- [ ] **Step 3: Write the failing test**

```python
# tests/test_tracknet_vendor.py
import sys
from pathlib import Path

VENDOR = Path(__file__).resolve().parents[1] / "third_party" / "tracknet"


def _ensure_on_path():
    p = str(VENDOR)
    if p not in sys.path:
        sys.path.insert(0, p)


def test_vendored_modules_import_and_build_models():
    _ensure_on_path()
    from utils.general import get_model, HEIGHT, WIDTH  # noqa: E402
    from model import TrackNet, InpaintNet  # noqa: E402

    assert (HEIGHT, WIDTH) == (288, 512)

    # bg_mode='concat' -> in_dim = (seq_len+1)*3 ; out_dim = seq_len
    tn = get_model("TrackNet", seq_len=8, bg_mode="concat")
    assert isinstance(tn, TrackNet)
    assert tn.down_block_1.conv_1.conv.in_channels == (8 + 1) * 3
    assert tn.predictor.out_channels == 8

    inp = get_model("InpaintNet")
    assert isinstance(inp, InpaintNet)


def test_dataset_module_imports_without_pycocotools():
    _ensure_on_path()
    import dataset  # noqa: F401  (must not raise; no pycocotools dependency)
    from dataset import Shuttlecock_Trajectory_Dataset, Video_IterableDataset  # noqa: F401
```

- [ ] **Step 4: Run the test to verify it fails**

Run: `PYTHONUTF8=1 .venv/Scripts/python.exe -m pytest tests/test_tracknet_vendor.py -v`
Expected: FAIL (files not yet copied / `parse` not installed → ImportError).

- [ ] **Step 5: Make it pass**

Complete Steps 1-2 (copy files, install `parse`). Re-run.

Run: `PYTHONUTF8=1 .venv/Scripts/python.exe -m pytest tests/test_tracknet_vendor.py -v`
Expected: PASS.

- [ ] **Step 6: Write CONTRACT.md**

Create `third_party/tracknet/CONTRACT.md` documenting: upstream repo + commit `77c123ad4dd449b7d275f16cc43f316ba5b54042`; the vendored file list and that `model.py`/`utils/general.py`/`dataset.py`/`utils/__init__.py`/`LICENSE` are verbatim; that `infer.py` (Task 2) is our extraction of upstream `predict.py`'s `__main__` + three `test.py` helpers, with the exact edits enumerated; pinned params (`HEIGHT=288`, `WIDTH=512`, tracking `seq_len=8`, inpaint `seq_len=16`, output `Frame,Visibility,X,Y` with Visibility 0=invisible/1=visible, coords in original pixels); `torch.load(..., weights_only=False)` requirement; and weight fetch instructions:

> Download `TrackNet_best.pt` and `InpaintNet_best.pt` from the repo's Google Drive (see upstream README), place as `weights/tracknet.pt` and `weights/inpaintnet.pt`. Not committed.

- [ ] **Step 7: Commit**

```bash
git add third_party/tracknet/model.py third_party/tracknet/utils/__init__.py third_party/tracknet/utils/general.py third_party/tracknet/dataset.py third_party/tracknet/LICENSE third_party/tracknet/CONTRACT.md requirements.txt tests/test_tracknet_vendor.py
git commit -m "feat(tracknet): vendor inference-only TrackNetV3 model/utils/dataset (MIT)"
```

---

### Task 2: `infer.py` — pycocotools-free extraction of the prediction pipeline

**Files:**
- Create: `third_party/tracknet/infer.py`
- Test: `tests/test_tracknet_infer.py`

**Interfaces:**
- Consumes: vendored `model`, `utils.general`, `dataset` (Task 1).
- Produces:
  - `predict(indices, y_pred=None, c_pred=None, img_scaler=(1, 1)) -> {'Frame':[int], 'X':[int], 'Y':[int], 'Visibility':[int]}` (verbatim from upstream `predict.py`).
  - `run_prediction(video_file, tracknet_file, inpaintnet_file='', batch_size=16, eval_mode='weight', large_video=True, max_sample_num=1800, video_range=None, device=None) -> pred_dict` — same dict shape, coords in original pixels.
  - Helpers `get_ensemble_weight`, `predict_location`, `generate_inpaint_mask`.

- [ ] **Step 1: Create `infer.py` header + the three extracted helpers**

Create `third_party/tracknet/infer.py`. Start with imports and the three helpers copied **verbatim** from upstream `test.py` (lines 25-50, 52-79, 223-258) — they use only `math`/`numpy`/`cv2`/`torch`, no pycocotools:

```python
"""Inference-only extraction of TrackNetV3's prediction pipeline.

This file is NOT verbatim upstream. It is a mechanical extraction of
`predict.py`'s `__main__` block into `run_prediction()` plus the three
`test.py` helpers it needs, so we avoid importing `test.py` (which imports
`pycocotools` at module load). Edits vs upstream are enumerated in CONTRACT.md.
Source: qaz812345/TrackNetV3 @ 77c123ad4dd449b7d275f16cc43f316ba5b54042
"""
import math

import cv2
import numpy as np
import torch
from torch.utils.data import DataLoader
from tqdm import tqdm

from dataset import Shuttlecock_Trajectory_Dataset, Video_IterableDataset
from utils.general import get_model, generate_frames, to_img, to_img_format, HEIGHT, WIDTH, COOR_TH


def get_ensemble_weight(seq_len, eval_mode):
    # verbatim from upstream test.py:25-50
    if eval_mode == 'average':
        weight = torch.ones(seq_len) / seq_len
    elif eval_mode == 'weight':
        weight = torch.ones(seq_len)
        for i in range(math.ceil(seq_len / 2)):
            weight[i] = (i + 1)
            weight[seq_len - i - 1] = (i + 1)
        weight = weight / weight.sum()
    else:
        raise ValueError('Invalid mode')
    return weight


def predict_location(heatmap):
    # verbatim from upstream test.py:52-79
    if np.amax(heatmap) == 0:
        return 0, 0, 0, 0
    (cnts, _) = cv2.findContours(heatmap.copy(), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    rects = [cv2.boundingRect(ctr) for ctr in cnts]
    max_area_idx = 0
    max_area = rects[0][2] * rects[0][3]
    for i in range(1, len(rects)):
        area = rects[i][2] * rects[i][3]
        if area > max_area:
            max_area_idx = i
            max_area = area
    x, y, w, h = rects[max_area_idx]
    return x, y, w, h


def generate_inpaint_mask(pred_dict, th_h=30):
    # verbatim from upstream test.py:223-258
    y = np.array(pred_dict['Y'])
    vis_pred = np.array(pred_dict['Visibility'])
    inpaint_mask = np.zeros_like(y)
    i = 0
    j = 0
    threshold = th_h
    while j < len(vis_pred):
        while i < len(vis_pred) - 1 and vis_pred[i] == 1:
            i += 1
        j = i
        while j < len(vis_pred) - 1 and vis_pred[j] == 0:
            j += 1
        if j == i:
            break
        elif i == 0 and y[j] > threshold:
            inpaint_mask[:j] = 1
        elif (i > 1 and y[i - 1] > threshold) and (j < len(vis_pred) and y[j] > threshold):
            inpaint_mask[i:j] = 1
        else:
            pass
        i = j
    return inpaint_mask.tolist()
```

- [ ] **Step 2: Add `predict()` verbatim from upstream `predict.py`**

Paste upstream `predict.py`'s `predict(indices, y_pred=None, c_pred=None, img_scaler=(1, 1))` function (lines 14-69) verbatim into `infer.py`. It references `WIDTH`, `HEIGHT`, `to_img`, `to_img_format`, `predict_location` — all now imported/defined in this module.

- [ ] **Step 3: Add `run_prediction()` — extracted from `predict.py`'s `__main__`**

Add `run_prediction` by transcribing upstream `predict.py` lines 86-306 into a function body, with these exact edits (record them in CONTRACT.md):
1. Signature: `def run_prediction(video_file, tracknet_file, inpaintnet_file='', batch_size=16, eval_mode='weight', large_video=True, max_sample_num=1800, video_range=None, device=None):`
2. Device resolution at top: `device = device or ('cuda' if torch.cuda.is_available() else 'cpu')`.
3. Replace every `args.X` with the parameter `X`; delete `num_workers`/argparse/`video_name`/`out_csv_file`/`out_video_file`/`os.makedirs(save_dir)` setup.
4. `torch.load(tracknet_file)` → `torch.load(tracknet_file, map_location=device, weights_only=False)`; same for `inpaintnet_file`.
5. Replace `.cuda()` → `.to(device)` on the two model constructions; replace `x.float().cuda()` → `x.float().to(device)` (two sites); replace `inpaintnet(coor_pred.cuda(), inpaint_mask.cuda())` → `inpaintnet(coor_pred.to(device), inpaint_mask.to(device))` (two sites).
6. Use `num_workers=0` in every `DataLoader(...)` (Windows-safe; avoids multiprocessing pickling of in-memory frame arrays).
7. Delete the CSV/video writing block (upstream lines 304-311); end with `return inpaint_pred_dict if inpaintnet is not None else tracknet_pred_dict`.

The `predict` symbol it calls is the one defined in Step 2 (same module).

- [ ] **Step 4: Write the failing test (coordinate math + helpers — no weights needed)**

```python
# tests/test_tracknet_infer.py
import sys
from pathlib import Path

import numpy as np
import torch

VENDOR = Path(__file__).resolve().parents[1] / "third_party" / "tracknet"
if str(VENDOR) not in sys.path:
    sys.path.insert(0, str(VENDOR))


def test_predict_scales_heatmap_center_to_original_pixels():
    from infer import predict

    # One sample, seq_len=2. Frame 0 heatmap has a hot 4x4 block centered at
    # model-space (100, 50); frame 1 is empty -> invisible.
    H, W = 288, 512
    y_pred = torch.zeros((1, 2, H, W), dtype=torch.float32)
    y_pred[0, 0, 48:52, 98:102] = 1.0
    indices = torch.tensor([[[0, 0], [0, 1]]], dtype=torch.float32)  # (N, L, 2), col 1 = frame id

    out = predict(indices, y_pred=y_pred, img_scaler=(2.0, 2.0))  # 2x upscale to original

    assert out["Frame"] == [0, 1]
    # Frame 0: center ~ (100, 50) in model space -> ~ (200, 100) original.
    assert abs(out["X"][0] - 200) <= 4 and abs(out["Y"][0] - 100) <= 4
    assert out["Visibility"][0] == 1
    # Frame 1: no response -> (0, 0), invisible.
    assert out["X"][1] == 0 and out["Y"][1] == 0 and out["Visibility"][1] == 0


def test_get_ensemble_weight_is_symmetric_and_normalized():
    from infer import get_ensemble_weight

    w = get_ensemble_weight(8, "weight")
    assert abs(float(w.sum()) - 1.0) < 1e-6
    assert torch.allclose(w, w.flip(0))


def test_generate_inpaint_mask_marks_interior_gap():
    from infer import generate_inpaint_mask

    # visible, gap, visible -> the gap (indices 2..3) gets masked (y above th_h).
    pred = {"Frame": list(range(6)),
            "X": [10, 10, 0, 0, 10, 10],
            "Y": [100, 100, 0, 0, 100, 100],
            "Visibility": [1, 1, 0, 0, 1, 1]}
    mask = generate_inpaint_mask(pred, th_h=30)
    assert mask[2] == 1 and mask[3] == 1
    assert mask[0] == 0 and mask[5] == 0
```

- [ ] **Step 5: Run to verify it fails**

Run: `PYTHONUTF8=1 .venv/Scripts/python.exe -m pytest tests/test_tracknet_infer.py -v`
Expected: FAIL (`infer` has no `predict` yet / ImportError).

- [ ] **Step 6: Run to verify it passes**

After Steps 1-3, run: `PYTHONUTF8=1 .venv/Scripts/python.exe -m pytest tests/test_tracknet_infer.py -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add third_party/tracknet/infer.py third_party/tracknet/CONTRACT.md tests/test_tracknet_infer.py
git commit -m "feat(tracknet): infer.py extraction (run_prediction, coord math, no pycocotools)"
```

---

### Task 3: Inference wrapper `badminton_analysis/shuttle_track/tracknet.py`

**Files:**
- Create: `badminton_analysis/shuttle_track/__init__.py` (empty), `badminton_analysis/shuttle_track/tracknet.py`
- Test: `tests/test_tracknet_wrapper.py`

**Interfaces:**
- Consumes: `third_party/tracknet/infer.py::run_prediction` (Task 2).
- Produces:
  - `load_tracknet(tracknet_path, inpaintnet_path=None, device=None) -> dict` — validated handle `{"tracknet_file", "inpaintnet_file", "device"}`; raises `FileNotFoundError` if `tracknet_path` missing.
  - `track_video(video_path, models, court_roi=None) -> dict[int, tuple[float, float] | None]` — 0-based TrackNet frame → pixel `(x, y)` or `None`. `court_roi` is a 2-point rect `[(x1, y1), (x2, y2)]`; points outside → `None`. Consumed by later tasks.

- [ ] **Step 1: Write the failing test (stub only the neural call)**

```python
# tests/test_tracknet_wrapper.py
import badminton_analysis.shuttle_track.tracknet as tn


def test_track_video_converts_pred_dict_and_gates_roi(monkeypatch):
    fake = {"Frame": [0, 1, 2, 3],
            "X": [100, 0, 900, 150],
            "Y": [200, 0, 200, 210],
            "Visibility": [1, 0, 1, 1]}
    monkeypatch.setattr(tn, "_run_prediction", lambda **kw: fake)

    models = {"tracknet_file": "x.pt", "inpaintnet_file": None, "device": "cpu"}
    # ROI rect keeps x in [0,800], y in [0,600]; frame 2 (x=900) is off-court.
    traj = tn.track_video("video.mp4", models, court_roi=[(0, 0), (800, 600)])

    assert traj[0] == (100.0, 200.0)
    assert traj[1] is None          # Visibility 0
    assert traj[2] is None          # gated out by ROI
    assert traj[3] == (150.0, 210.0)


def test_track_video_without_roi_keeps_all_visible(monkeypatch):
    fake = {"Frame": [0, 1], "X": [900, 10], "Y": [900, 10], "Visibility": [1, 1]}
    monkeypatch.setattr(tn, "_run_prediction", lambda **kw: fake)
    traj = tn.track_video("v.mp4", {"tracknet_file": "x.pt", "inpaintnet_file": None, "device": "cpu"})
    assert traj[0] == (900.0, 900.0) and traj[1] == (10.0, 10.0)


def test_load_tracknet_missing_file_raises(tmp_path):
    import pytest
    with pytest.raises(FileNotFoundError):
        tn.load_tracknet(str(tmp_path / "nope.pt"))
```

- [ ] **Step 2: Run to verify it fails**

Run: `PYTHONUTF8=1 .venv/Scripts/python.exe -m pytest tests/test_tracknet_wrapper.py -v`
Expected: FAIL (module does not exist).

- [ ] **Step 3: Implement the wrapper**

```python
# badminton_analysis/shuttle_track/tracknet.py
"""Inference wrapper around the vendored TrackNetV3 (third_party/tracknet).

Produces a dense per-frame shuttle trajectory from a video. Torch is imported
lazily (inside _run_prediction) so weightless / --help paths stay torch-free.
Frame indices returned here are 0-based (TrackNet's own convention); the
+1 alignment to the match loop's 1-based frame_count is applied by the
pipeline integration (see system.py::_run_shuttle_pretrack), not here.
"""
import os
import sys
from pathlib import Path

_VENDOR_DIR = Path(__file__).resolve().parents[2] / "third_party" / "tracknet"


def _add_vendored_to_path():
    p = str(_VENDOR_DIR)
    if p not in sys.path:
        sys.path.insert(0, p)


def _run_prediction(**kwargs):
    """Indirection point so tests can stub the neural inference."""
    _add_vendored_to_path()
    from infer import run_prediction
    return run_prediction(**kwargs)


def load_tracknet(tracknet_path, inpaintnet_path=None, device=None):
    if not os.path.isfile(str(tracknet_path)):
        raise FileNotFoundError(f"TrackNet weights not found: {tracknet_path}")
    if inpaintnet_path is not None and not os.path.isfile(str(inpaintnet_path)):
        # InpaintNet is optional; treat a bad path as "no rectification".
        inpaintnet_path = None
    return {"tracknet_file": str(tracknet_path),
            "inpaintnet_file": str(inpaintnet_path) if inpaintnet_path else None,
            "device": device}


def _in_roi(x, y, court_roi):
    if court_roi is None:
        return True
    (x1, y1), (x2, y2) = court_roi
    lo_x, hi_x = min(x1, x2), max(x1, x2)
    lo_y, hi_y = min(y1, y2), max(y1, y2)
    return lo_x <= x <= hi_x and lo_y <= y <= hi_y


def track_video(video_path, models, court_roi=None):
    pred = _run_prediction(
        video_file=str(video_path),
        tracknet_file=models["tracknet_file"],
        inpaintnet_file=models["inpaintnet_file"] or "",
        device=models.get("device"),
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

- [ ] **Step 4: Run to verify it passes**

Run: `PYTHONUTF8=1 .venv/Scripts/python.exe -m pytest tests/test_tracknet_wrapper.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add badminton_analysis/shuttle_track/__init__.py badminton_analysis/shuttle_track/tracknet.py tests/test_tracknet_wrapper.py
git commit -m "feat(tracknet): inference wrapper (load_tracknet, track_video + ROI gate)"
```

---

### Task 4: Trajectory cache `badminton_analysis/shuttle_track/trajectory.py`

**Files:**
- Create: `badminton_analysis/shuttle_track/trajectory.py`
- Test: `tests/test_trajectory_cache.py`

**Interfaces:**
- Consumes: nothing new.
- Produces:
  - `cache_key(video_path, params) -> str` — stable hash of video path + video mtime + a sorted repr of `params` (dict).
  - `save_cache(cache_path, key, trajectory) -> None` — writes JSON `{"key": key, "trajectory": {str(frame): [x, y] | null}}`.
  - `load_cache(cache_path, key) -> dict[int, tuple | None] | None` — returns the trajectory (int keys, tuple/None values) iff the file exists and its stored key matches; else `None`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_trajectory_cache.py
from badminton_analysis.shuttle_track import trajectory as tj


def _video(tmp_path):
    v = tmp_path / "clip.mp4"
    v.write_bytes(b"fake")
    return str(v)


def test_save_then_load_round_trips(tmp_path):
    v = _video(tmp_path)
    cache = str(tmp_path / "traj.json")
    key = tj.cache_key(v, {"eval_mode": "weight", "inpaint": True})
    traj = {0: (1.5, 2.5), 1: None, 2: (3.0, 4.0)}
    tj.save_cache(cache, key, traj)
    loaded = tj.load_cache(cache, key)
    assert loaded == traj
    assert isinstance(next(iter(loaded)), int)


def test_load_returns_none_on_key_mismatch(tmp_path):
    v = _video(tmp_path)
    cache = str(tmp_path / "traj.json")
    tj.save_cache(cache, tj.cache_key(v, {"a": 1}), {0: (1.0, 1.0)})
    assert tj.load_cache(cache, tj.cache_key(v, {"a": 2})) is None


def test_load_returns_none_when_missing(tmp_path):
    assert tj.load_cache(str(tmp_path / "nope.json"), "k") is None


def test_key_changes_with_params(tmp_path):
    v = _video(tmp_path)
    assert tj.cache_key(v, {"inpaint": True}) != tj.cache_key(v, {"inpaint": False})
```

- [ ] **Step 2: Run to verify it fails**

Run: `PYTHONUTF8=1 .venv/Scripts/python.exe -m pytest tests/test_trajectory_cache.py -v`
Expected: FAIL (module does not exist).

- [ ] **Step 3: Implement**

```python
# badminton_analysis/shuttle_track/trajectory.py
"""On-disk cache for the dense shuttle trajectory produced by the TrackNetV3
pre-pass. Keyed by video path + mtime + inference params so a stale or
differently-configured run misses and recomputes.
"""
import hashlib
import json
import os


def cache_key(video_path, params):
    try:
        mtime = os.path.getmtime(video_path)
    except OSError:
        mtime = 0.0
    payload = json.dumps(
        {"video": os.path.abspath(str(video_path)), "mtime": mtime,
         "params": {k: params[k] for k in sorted(params)}},
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def save_cache(cache_path, key, trajectory):
    serial = {str(f): (list(pt) if pt is not None else None)
              for f, pt in trajectory.items()}
    with open(cache_path, "w", encoding="utf-8") as fh:
        json.dump({"key": key, "trajectory": serial}, fh)


def load_cache(cache_path, key):
    if not os.path.isfile(cache_path):
        return None
    try:
        with open(cache_path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    if data.get("key") != key:
        return None
    out = {}
    for f, pt in data.get("trajectory", {}).items():
        out[int(f)] = (float(pt[0]), float(pt[1])) if pt is not None else None
    return out
```

- [ ] **Step 4: Run to verify it passes**

Run: `PYTHONUTF8=1 .venv/Scripts/python.exe -m pytest tests/test_trajectory_cache.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add badminton_analysis/shuttle_track/trajectory.py tests/test_trajectory_cache.py
git commit -m "feat(tracknet): on-disk shuttle-trajectory cache"
```

---

### Task 5: Pipeline integration in `system.py`

**Files:**
- Modify: `badminton_analysis/system.py` (ctor ~L60-70, ~L84; `process_video` after L211; `_capture_analysis_frame` L491-497; `_run_stroke_recognition` strokes.json write L561-563)
- Test: `tests/test_tracknet_integration.py`

**Interfaces:**
- Consumes: `shuttle_track.tracknet.{load_tracknet, track_video}`, `shuttle_track.trajectory.{cache_key, load_cache, save_cache}`.
- Produces: `BadmintonAnalysisSystem(..., tracknet_weights=None, inpaintnet_weights=None)`; `self._shuttle_trajectory: dict[int, tuple|None] | None`; `self._shuttle_source: str` (`"tracknet"` or `"yolo"`); `_run_shuttle_pretrack()`. `strokes.json` gains `"shuttle_source"`.

- [ ] **Step 1: Write the failing test (real seam; stub only inference)**

```python
# tests/test_tracknet_integration.py
import badminton_analysis.system as sysmod
from badminton_analysis.stroke.events import detect_contacts


def _make_system(tmp_path, **kw):
    """Construct without running __init__'s heavy model loads."""
    s = sysmod.BadmintonAnalysisSystem.__new__(sysmod.BadmintonAnalysisSystem)
    s.analyze_technique = True
    s.tracknet_weights = kw.get("tracknet_weights")
    s.inpaintnet_weights = kw.get("inpaintnet_weights")
    s._shuttle_trajectory = None
    s._shuttle_source = "yolo"
    s._analysis_track = []
    s._analysis_frames = {}
    s.save_dir = str(tmp_path)
    s.video_path = str(tmp_path / "v.mp4")
    s.court_roi_corners = [(0, 0), (1000, 1000)]
    s.frame_width, s.frame_height = 1280, 720
    return s


def test_capture_prefers_trajectory_with_plus_one_alignment(tmp_path):
    s = _make_system(tmp_path)
    # TrackNet 0-based frame 0 -> match loop frame_count 1.
    s._shuttle_trajectory = {1: (500.0, 300.0)}
    s._shuttle_source = "tracknet"
    # Minimal _capture_analysis_frame path: no pose, yolo ball says elsewhere.
    s.player_tracker = type("PT", (), {"players": {}})()
    s.player_pose_visualizer = type("PV", (), {"get_current_pose_data": lambda self=None: None})()
    s._racket_detector = None
    s.dominant_hand = "right"

    s._capture_analysis_frame(1, frame=None, roi_corners=[(0, 0), (1000, 1000)], ball_position=[0, 0])
    rec = s._analysis_track[-1]
    assert rec["shuttle"] == (500.0, 300.0)  # from trajectory, not yolo (which was [0,0])


def test_capture_falls_back_to_yolo_when_no_trajectory(tmp_path):
    s = _make_system(tmp_path)
    s._shuttle_trajectory = None
    s.player_tracker = type("PT", (), {"players": {}})()
    s.player_pose_visualizer = type("PV", (), {"get_current_pose_data": lambda self=None: None})()
    s._racket_detector = None
    s.dominant_hand = "right"
    s._capture_analysis_frame(1, frame=None, roi_corners=[(0, 0), (1000, 1000)], ball_position=[123, 456])
    assert s._analysis_track[-1]["shuttle"] == (123.0, 456.0)


def test_pretrack_is_never_fatal(tmp_path, monkeypatch):
    s = _make_system(tmp_path, tracknet_weights="tn.pt")
    import badminton_analysis.shuttle_track.tracknet as tnmod
    monkeypatch.setattr(tnmod, "load_tracknet", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    s._run_shuttle_pretrack()  # must not raise
    assert s._shuttle_trajectory is None
    assert s._shuttle_source == "yolo"


def test_dense_trajectory_yields_contacts(tmp_path):
    # A dense synthetic trajectory + racket near a direction-change frame -> >=1 contact,
    # where the same track with 65%-missing shuttle would yield 0.
    track = []
    for f in range(1, 41):
        x = 500 + (f * 5 if f <= 20 else (40 - f) * 5)   # rises then falls: a peak ~ f=20
        track.append({"frame": f, "racket_head": (x, 300) if f == 20 else (0, 0),
                      "shuttle": (x, 300)})
    contacts = detect_contacts(track, contact_px=80.0, dir_change_deg=30.0, min_gap=5)
    assert len(contacts) >= 1
```

- [ ] **Step 2: Run to verify it fails**

Run: `PYTHONUTF8=1 .venv/Scripts/python.exe -m pytest tests/test_tracknet_integration.py -v`
Expected: FAIL (ctor has no `tracknet_weights`; no `_run_shuttle_pretrack`; capture ignores trajectory).

- [ ] **Step 3: Add ctor args + state**

In `BadmintonAnalysisSystem.__init__` signature (currently ends `bst_weights=None):`), add `tracknet_weights=None, inpaintnet_weights=None`. After `self.bst_weights = bst_weights` add:

```python
        self.tracknet_weights = tracknet_weights
        self.inpaintnet_weights = inpaintnet_weights
        self._shuttle_trajectory = None
        self._shuttle_source = "yolo"
```

- [ ] **Step 4: Implement `_run_shuttle_pretrack` and invoke it**

Add the method (near `_run_stroke_recognition`):

```python
    def _run_shuttle_pretrack(self):
        """Offline TrackNetV3 dense-shuttle pre-pass. Never fatal.

        Populates ``self._shuttle_trajectory`` (keyed by match-loop
        ``frame_count`` = TrackNet 0-based frame + 1) and sets
        ``self._shuttle_source = 'tracknet'``. On any failure or when weights
        are absent, leaves the trajectory None so ``_capture_analysis_frame``
        falls back to the yolo shuttle (byte-identical to today).
        """
        if not (self.tracknet_weights and self.analyze_technique):
            return
        try:
            from .shuttle_track import tracknet as tnmod
            from .shuttle_track import trajectory as tjmod

            params = {"eval_mode": "weight",
                      "inpaint": bool(self.inpaintnet_weights)}
            cache_path = os.path.join(self.save_dir, "shuttle_trajectory.json")
            key = tjmod.cache_key(self.video_path, params)
            traj0 = tjmod.load_cache(cache_path, key)
            if traj0 is None:
                models = tnmod.load_tracknet(self.tracknet_weights, self.inpaintnet_weights)
                traj0 = tnmod.track_video(self.video_path, models, court_roi=self.court_roi_corners)
                tjmod.save_cache(cache_path, key, traj0)
            # Align TrackNet 0-based frames to the match loop's 1-based frame_count.
            self._shuttle_trajectory = {f + 1: pt for f, pt in traj0.items()}
            self._shuttle_source = "tracknet"
            print(f"Dense shuttle tracking: {len(traj0)} frames via TrackNetV3")
        except Exception as e:  # never fatal
            print(f"TrackNetV3 pre-pass unavailable ({e}); using yolo shuttle.")
            self._shuttle_trajectory = None
            self._shuttle_source = "yolo"
```

In `process_video`, after `self._write_metadata(...)` (L211) and before the `frame_count = 0` loop setup, add:

```python
        self._run_shuttle_pretrack()
```

- [ ] **Step 5: Make `_capture_analysis_frame` prefer the trajectory**

Replace the `shuttle` block (currently L491-493):

```python
        shuttle = None
        if ball_position and ball_position != [0, 0]:
            shuttle = (float(ball_position[0]), float(ball_position[1]))
```

with:

```python
        shuttle = None
        if self._shuttle_trajectory is not None:
            pt = self._shuttle_trajectory.get(frame_count)
            if pt is not None:
                shuttle = (float(pt[0]), float(pt[1]))
        elif ball_position and ball_position != [0, 0]:
            shuttle = (float(ball_position[0]), float(ball_position[1]))
```

- [ ] **Step 6: Record provenance in strokes.json**

In `_run_stroke_recognition`, change the `write_json(strokes_path, {...})` call (L563) to include the source:

```python
            write_json(strokes_path, {"strokes": labels, "distribution": distribution,
                                      "shuttle_source": self._shuttle_source})
```

- [ ] **Step 7: Run tests**

Run: `PYTHONUTF8=1 .venv/Scripts/python.exe -m pytest tests/test_tracknet_integration.py -v`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add badminton_analysis/system.py tests/test_tracknet_integration.py
git commit -m "feat(tracknet): offline pre-pass integration + trajectory-sourced analysis shuttle"
```

---

### Task 6: CLI, API, and provenance wiring

**Files:**
- Modify: `main.py` (L30-33 flags, L67 ctor wiring), `app.py` (after `_bst_weights` L170, `/api/models` L299-301, subprocess L493-495), `static/kestrel.js` (strokes.json handler ~L629-650)
- Test: `tests/test_tracknet_wiring.py`

**Interfaces:**
- Consumes: `_run_stroke_recognition`'s `shuttle_source` in strokes.json (Task 5).
- Produces: `--tracknet-model` / `--inpaintnet-model` CLI flags; `app._tracknet_weights()` / `app._inpaintnet_weights()`; `/api/models` includes `"tracknet"`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_tracknet_wiring.py
import app as webapp


def test_tracknet_weights_discovery(tmp_path):
    assert webapp._tracknet_weights(base=tmp_path) is None
    (tmp_path / "tracknet.pt").write_bytes(b"x")
    assert webapp._tracknet_weights(base=tmp_path) == str(tmp_path / "tracknet.pt")


def test_inpaintnet_weights_discovery(tmp_path):
    assert webapp._inpaintnet_weights(base=tmp_path) is None
    (tmp_path / "inpaintnet.pt").write_bytes(b"x")
    assert webapp._inpaintnet_weights(base=tmp_path) == str(tmp_path / "inpaintnet.pt")


def test_api_models_reports_tracknet():
    client = webapp.app.test_client()
    body = client.get("/api/models").get_json()
    assert "tracknet" in body


def test_main_has_tracknet_flags():
    import main
    import argparse
    # Build the parser the same way main.main() does, then parse a sample.
    # Simpler: assert the flags exist by parsing --help-free known args.
    # (Structural: main defines --tracknet-model / --inpaintnet-model.)
    import inspect
    src = inspect.getsource(main.main)
    assert "--tracknet-model" in src and "--inpaintnet-model" in src
    assert "tracknet_weights=args.tracknet_model" in src
    assert "inpaintnet_weights=args.inpaintnet_model" in src
```

- [ ] **Step 2: Run to verify it fails**

Run: `PYTHONUTF8=1 .venv/Scripts/python.exe -m pytest tests/test_tracknet_wiring.py -v`
Expected: FAIL (`_tracknet_weights` undefined; `/api/models` lacks tracknet; flags absent).

- [ ] **Step 3: main.py — add flags + ctor wiring**

After the `--bst-model` argument (L32-33), add:

```python
    parser.add_argument('--tracknet-model', default=None, type=str,
                        help='TrackNetV3 追踪模型路径（可选，密集羽毛球轨迹）')
    parser.add_argument('--inpaintnet-model', default=None, type=str,
                        help='TrackNetV3 InpaintNet 轨迹修补模型路径（可选）')
```

In the `BadmintonAnalysisSystem(...)` call, after `bst_weights=args.bst_model,` (L67) add:

```python
        tracknet_weights=args.tracknet_model,
        inpaintnet_weights=args.inpaintnet_model,
```

- [ ] **Step 4: app.py — discovery helpers, /api/models, subprocess flags**

After `_bst_weights` (L170) add:

```python
def _tracknet_weights(base=None):
    """Path to the TrackNetV3 tracking model when installed, else None."""
    base = Path(base) if base is not None else (PROJECT_ROOT / 'weights')
    p = base / 'tracknet.pt'
    return str(p) if p.is_file() else None


def _inpaintnet_weights(base=None):
    """Path to the TrackNetV3 InpaintNet rectifier when installed, else None."""
    base = Path(base) if base is not None else (PROJECT_ROOT / 'weights')
    p = base / 'inpaintnet.pt'
    return str(p) if p.is_file() else None
```

In `/api/models` (L299-301), add the key:

```python
    return jsonify({'racket': _racket_weights() is not None,
                    'quality': _quality_weights() is not None,
                    'bst': _bst_weights() is not None,
                    'tracknet': _tracknet_weights() is not None})
```

After the `bst_weights` block in the analyze route (L493-495), add:

```python
    tracknet_weights = _tracknet_weights()
    if tracknet_weights:
        cmd += ['--tracknet-model', tracknet_weights]
    inpaintnet_weights = _inpaintnet_weights()
    if inpaintnet_weights:
        cmd += ['--inpaintnet-model', inpaintnet_weights]
```

- [ ] **Step 5: kestrel.js — provenance line in the strokes handler**

In the strokes.json handler (the `fetch('/api/output/' + stem + '/strokes.json')` block, ~L629), after the timeline/distribution render, surface the shuttle source. Inside the `.then(function (d) {` body, after `var strokes = ...`, add:

```javascript
      if (d && d.shuttle_source === 'tracknet') {
        var prov = document.getElementById('dist-list');
        if (prov) {
          var note = (state.lang === 'zh' ? '密集羽毛球追踪：TrackNetV3' : 'Dense shuttle tracking: TrackNetV3');
          prov.insertAdjacentHTML('afterend', '<div class="prov-note faint">' + note + '</div>');
        }
      }
```

(Use `insertAdjacentHTML`, not `innerHTML +=`, to avoid re-parsing and clobbering existing handlers — the provenance-strip lesson.)

- [ ] **Step 6: Run tests**

Run: `PYTHONUTF8=1 .venv/Scripts/python.exe -m pytest tests/test_tracknet_wiring.py -v`
Expected: PASS.

- [ ] **Step 7: Run the full suite (regression gate)**

Run: `PYTHONUTF8=1 .venv/Scripts/python.exe -m pytest -q`
Expected: all green (the ~313 existing tests + the new ones). No existing test changes behavior — TrackNet is off by default.

- [ ] **Step 8: Commit**

```bash
git add main.py app.py static/kestrel.js tests/test_tracknet_wiring.py
git commit -m "feat(tracknet): CLI/API wiring + dense-tracker provenance in results"
```

---

## Validation (controller-run, not a suite test)

After all tasks pass review, the controller runs a weights-present GPU validation (mirrors the BST T9 harness; needs `weights/tracknet.pt` + `weights/inpaintnet.pt` downloaded per CONTRACT.md, and the RTX 4090):

1. On the Axelsen clip segment, run the match pipeline with `--analyze-technique --tracknet-model weights/tracknet.pt --inpaintnet-model weights/inpaintnet.pt --bst-model weights/bst-shuttleset.pt`.
2. Assert: the shuttle trajectory is dense (present in the large majority of court frames), `detect_contacts` returns > 0 contacts, and `strokes.json` has a non-empty `distribution` with `shuttle_source == "tracknet"`.
3. Confirm a weightless run (no `--tracknet-model`) is byte-identical to today (regression).

This is the functional done-check; a poor contact/stroke count here is a finding for the project-2 benchmark to quantify and (if needed) a threshold-tuning follow-up — not a reason to change TrackNet code.

---

## Self-Review

**1. Spec coverage:**
- Vendor TrackNetV3 (MIT), weights not committed → Task 1 (+ CONTRACT fetch docs). ✓
- Offline dense pre-pass, TrackNet + InpaintNet → Tasks 2-3, invoked in Task 5. ✓
- Cache → Task 4. ✓
- Analysis-only consumption; overlay unchanged → Task 5 (`_capture_analysis_frame` only). ✓
- Never-fatal + zero-regression → Task 5 (`_run_shuttle_pretrack` try/except; fallback branch; guard on `tracknet_weights`). ✓
- Torch-2.5 / weights_only=False / device / bg_mode from checkpoint → Task 2 edits + Global Constraints. ✓
- CLI/API/provenance → Task 6. ✓
- Testing avoids the BST all-stub trap (real coord/ROI/seam, stub only inference) → Tasks 2/3/5 tests. ✓
- Functional validation on Axelsen clip → Validation section. ✓

**2. Placeholder scan:** No TBD/TODO; every code step shows complete code or an exact enumerated edit against a named upstream line range. ✓

**3. Type consistency:** `track_video -> dict[int, (x,y)|None]` (0-based) consumed by `_run_shuttle_pretrack`, which re-keys `+1` into `self._shuttle_trajectory` (1-based) read by `_capture_analysis_frame(frame_count)`. `cache_key/save_cache/load_cache` signatures match their call sites in Task 5. `load_tracknet`/`track_video` handle-dict keys (`tracknet_file`/`inpaintnet_file`/`device`) consistent across Tasks 3 and 5. `_tracknet_weights`/`_inpaintnet_weights` names consistent across app.py and tests. ✓
