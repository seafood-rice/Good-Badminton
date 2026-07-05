# Learned Technique-Quality Model (MultiSenseBadminton) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Task 5 is controller-executed (dataset download + GPU training), not a subagent task.

**Goal:** Train a pose-sequence regressor on MultiSenseBadminton's per-swing 1–7 coach ratings and surface a per-rep "AI form score" (0–100) for `high_clear` drills alongside the rule-based scores, with weights auto-discovery and byte-identical behavior when absent.

**Architecture:** A shared normalization module (`badminton_analysis/quality/normalize.py`) turns keypoint windows into fixed-size tensors identically at training and inference. `QualityScorer` (TorchScript, CPU-capable) hooks into `PostureRunner.run` right after `analyzer.analyze(...)`, adding `report["ai_score"]`; `build_drill_summary` derives `mean_ai_score`; the app passes `--quality-model` when `weights/quality-high_clear.pt` exists (same pattern as the racket detector). Dataset prep re-extracts 2D pose from the released front/side videos with our own pose stack — the IMU skeleton is never used.

**Tech Stack:** Python 3, PyTorch 2.5.1+cu121 (in venv), numpy, our pose processors, Flask, pytest; vanilla JS for the UI chips.

## Global Constraints

- **v1 scope locked:** `high_clear` only; regression target = mean of the three per-swing coach ratings (1–7); mapped to 0–100 for display; 3-bucket accuracy is an evaluation metric only.
- **Split discipline:** train/val split **by player id** — a player never appears in both. The manifest carries player ids for exactly this.
- **One normalization, two call sites:** training and inference must both call `normalize_window` from `badminton_analysis/quality/normalize.py`. No copies.
- **Auto-discovery:** weights at `weights/quality-high_clear.pt`; `_quality_weights()` in app.py; `--quality-model` flag on `main_posture.py`; absent file → all pipelines byte-identical to today (no `ai_score`/`mean_ai_score` fields anywhere).
- **Failure tolerance:** scorer construction or per-rep inference failure logs and omits fields; an analysis run never dies because of the scorer.
- **Datasets/weights hygiene:** MultiSenseBadminton subset under `data/multisense/` (gitignored via existing `data/`); figshare license recorded at download; do NOT commit weights until license clarity. Nothing under `weights/`/`data/` committed.
- Bilingual **zh/en** for all UI strings; theme tokens only; AI chips/tiles render only when fields exist.
- **All existing tests stay green.** Suite baseline recorded at execution start (206 at plan time; racket Task 4 may not add tests). TDD via `./.venv/Scripts/python.exe -m pytest`. No GPU/network/torch-load in unit tests — scorer tests inject a fake model.
- Windows process hygiene: kill by PID via netstat+taskkill; `PYTHONUTF8=1` for pipeline runs.
- Verified anchors: `PostureRunner.__init__` at `badminton_analysis/posture/system.py:28-34`; `runner.run` report loop at `:51-68` (`report = self.analyzer.analyze(event, window_frames)` at `:65`); runner constructed at `:195-197`; `_write_reports` called at `:213`; `build_drill_summary` at `badminton_analysis/posture/writer.py:26`; `main_posture.py` argparse gained `--racket-model` at `:29-30` (add `--quality-model` after it); app posture cmd at `api_posture_analyze` (append after the `--racket-model` block added by the racket feature).

---

### Task 1: Normalization module (TDD)

**Files:**
- Create: `badminton_analysis/quality/__init__.py` (empty), `badminton_analysis/quality/normalize.py`
- Test: `tests/test_quality_normalize.py` (create)

**Interfaces:**
- Produces (Tasks 2/4 consume):
  - `TARGET_FRAMES = 64`
  - `normalize_window(frames, mirror=False, target=TARGET_FRAMES) -> np.ndarray | None` — input: list of per-frame dicts with `"keypoints"` (17×2 array or None, COCO order); output: float32 array `(target, 34)` — hip-centered, torso-scaled, time-resampled, with sentinel joints (x<=1 or y<=1) masked to 0; frames lacking two valid hips are dropped; `None` when fewer than 8 usable frames remain.
  - Constants `L_SHO, R_SHO, L_HIP, R_HIP = 5, 6, 11, 12` (COCO indices).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_quality_normalize.py`:

```python
import numpy as np

from badminton_analysis.quality.normalize import TARGET_FRAMES, normalize_window


def _kp(x_off=0.0):
    kp = np.zeros((17, 2), dtype=float)
    kp[5] = (90.0 + x_off, 40.0)    # L shoulder
    kp[6] = (110.0 + x_off, 40.0)   # R shoulder
    kp[11] = (92.0 + x_off, 100.0)  # L hip
    kp[12] = (108.0 + x_off, 100.0) # R hip
    kp[10] = (130.0 + x_off, 20.0)  # R wrist
    return kp


def _frames(n, x_off=0.0):
    return [{"keypoints": _kp(x_off)} for _ in range(n)]


def test_output_shape_and_dtype():
    out = normalize_window(_frames(30))
    assert out.shape == (TARGET_FRAMES, 34)
    assert out.dtype == np.float32


def test_hip_centering_and_scale():
    out = normalize_window(_frames(30))
    seq = out.reshape(TARGET_FRAMES, 17, 2)
    hips = (seq[:, 11] + seq[:, 12]) / 2
    assert np.allclose(hips, 0.0, atol=1e-5)          # hip midpoint at origin
    torso = np.linalg.norm(seq[0, 5] / 2 + seq[0, 6] / 2 - hips[0])
    assert 0.9 < torso < 1.1                          # torso length ~1


def test_translation_invariance():
    a = normalize_window(_frames(30, x_off=0.0))
    b = normalize_window(_frames(30, x_off=500.0))
    assert np.allclose(a, b, atol=1e-5)


def test_mirroring_flips_x_and_swaps_sides():
    plain = normalize_window(_frames(30)).reshape(TARGET_FRAMES, 17, 2)
    mirrored = normalize_window(_frames(30), mirror=True).reshape(TARGET_FRAMES, 17, 2)
    # valid R wrist (idx 10) lands in the L-wrist slot (idx 9) with negated x
    assert np.allclose(mirrored[:, 9, 0], -plain[:, 10, 0], atol=1e-5)
    assert np.allclose(mirrored[:, 9, 1], plain[:, 10, 1], atol=1e-5)
    # sentinel joints stay masked at zero in both
    assert np.allclose(mirrored[:, 0], 0.0, atol=1e-5)


def test_time_resampling_short_and_long():
    assert normalize_window(_frames(10)).shape == (TARGET_FRAMES, 34)
    assert normalize_window(_frames(200)).shape == (TARGET_FRAMES, 34)


def test_none_when_too_few_posed_frames():
    frames = [{"keypoints": None}] * 30 + _frames(5)
    assert normalize_window(frames) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_quality_normalize.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'badminton_analysis.quality'`.

- [ ] **Step 3: Implement**

Create `badminton_analysis/quality/__init__.py` (empty) and `badminton_analysis/quality/normalize.py`:

```python
"""Keypoint-window normalization shared by quality-model training and inference."""
import numpy as np

TARGET_FRAMES = 64
L_SHO, R_SHO, L_HIP, R_HIP = 5, 6, 11, 12
# COCO left/right index pairs for mirroring.
_SWAP = [(1, 2), (3, 4), (5, 6), (7, 8), (9, 10), (11, 12), (13, 14), (15, 16)]
_MIN_POSED = 8


def _resample(seq, target):
    """Linear time-resampling of (N, 17, 2) to (target, 17, 2)."""
    n = seq.shape[0]
    if n == target:
        return seq
    src = np.linspace(0.0, n - 1.0, target)
    lo = np.floor(src).astype(int)
    hi = np.minimum(lo + 1, n - 1)
    t = (src - lo)[:, None, None]
    return seq[lo] * (1.0 - t) + seq[hi] * t


def normalize_window(frames, mirror=False, target=TARGET_FRAMES):
    """(target, 34) float32 pose sequence: hip-centered, torso-scaled, resampled.

    frames: per-frame dicts with "keypoints" (17x2 or None). A joint with
    x<=1 and-or y<=1 is treated as an undetected sentinel (codebase convention)
    and masked to 0 in the output (the body-center origin). Frames without two
    valid hips are dropped. Returns None when fewer than _MIN_POSED usable
    frames remain.
    """
    posed = []
    for f in frames:
        kp = f.get("keypoints")
        if kp is None:
            continue
        kp = np.asarray(kp, dtype=float)
        if (kp[L_HIP][0] > 1.0 and kp[L_HIP][1] > 1.0
                and kp[R_HIP][0] > 1.0 and kp[R_HIP][1] > 1.0):
            posed.append(kp)
    if len(posed) < _MIN_POSED:
        return None
    seq = np.stack(posed)                                   # (N, 17, 2)
    valid = (seq[..., 0] > 1.0) & (seq[..., 1] > 1.0)       # (N, 17)
    hips = (seq[:, L_HIP] + seq[:, R_HIP]) / 2.0
    seq = seq - hips[:, None, :]
    shoulders = (seq[:, L_SHO] + seq[:, R_SHO]) / 2.0
    torso = np.linalg.norm(shoulders, axis=1)
    torso_ok = valid[:, L_SHO] & valid[:, R_SHO] & (torso > 1e-6)
    scale = float(np.median(torso[torso_ok])) if np.any(torso_ok) else 1.0
    seq = seq / max(scale, 1e-6)
    seq[~valid] = 0.0                                       # sentinels -> body-center
    if mirror:
        seq[:, :, 0] = -seq[:, :, 0]
        for a, b in _SWAP:
            seq[:, [a, b]] = seq[:, [b, a]]
    seq = _resample(seq, target)
    return seq.reshape(target, 34).astype(np.float32)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_quality_normalize.py -v`
Expected: PASS (6 tests).

- [ ] **Step 5: Full suite** — `./.venv/Scripts/python.exe -m pytest -q` → baseline + 6.

- [ ] **Step 6: Commit**

```bash
git add badminton_analysis/quality/__init__.py badminton_analysis/quality/normalize.py tests/test_quality_normalize.py
git commit -m "feat(quality): shared keypoint-window normalization module"
```

---

### Task 2: Scorer + posture pipeline wiring (TDD)

**Files:**
- Create: `badminton_analysis/quality/scorer.py`
- Modify: `badminton_analysis/posture/system.py` (runner param + system ctor/build/metadata), `badminton_analysis/posture/writer.py` (`mean_ai_score`), `main_posture.py` (`--quality-model`)
- Test: `tests/test_quality_scorer.py` (create)

**Interfaces:**
- Consumes: `normalize_window` (Task 1); `PostureRunner.run` loop (`posture/system.py:51-68`); `build_drill_summary` (`writer.py:26`).
- Produces (Task 3/5 consume):
  - `QualityScorer(model_path, stroke_type='high_clear', model=None)` — DI like `RacketDetector`; `.available` bool; `score(window_frames, dominant='right') -> float | None` (0–100, 1dp; None on any failure or unnormalizable window). Loads TorchScript lazily on first `score()`.
  - `PostureRunner(..., quality_scorer=None)`; when set, each report gains `"ai_score"` (may be None — key added only when scorer returned a number).
  - `build_drill_summary` adds `"mean_ai_score"` (1dp) when ≥1 report has `ai_score`, else key absent.
  - `PostureAnalysisSystem(..., quality_model_path=None)`; lazy `_build_quality_scorer()` (try/except → None); metadata gains `"quality": {"model": <path|None>, "scored_reps": <int>}`.
  - `main_posture.py --quality-model` (default None).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_quality_scorer.py`:

```python
import numpy as np

from badminton_analysis.posture.writer import build_drill_summary
from badminton_analysis.quality.scorer import QualityScorer, to_display_score


class _FakeModel:
    def __init__(self, value):
        self.value = value

    def __call__(self, x):
        import torch
        return torch.tensor([[self.value]], dtype=torch.float32)


def _frames(n=30):
    kp = np.zeros((17, 2), dtype=float)
    kp[5] = (90.0, 40.0)
    kp[6] = (110.0, 40.0)
    kp[11] = (92.0, 100.0)
    kp[12] = (108.0, 100.0)
    return [{"keypoints": kp} for _ in range(n)]


def test_display_mapping():
    assert to_display_score(1.0) == 0.0
    assert to_display_score(7.0) == 100.0
    assert to_display_score(4.0) == 50.0
    assert to_display_score(9.0) == 100.0   # clamped
    assert to_display_score(0.0) == 0.0     # clamped


def test_score_with_fake_model():
    s = QualityScorer(model_path=None, model=_FakeModel(4.0))
    assert s.available
    assert s.score(_frames()) == 50.0


def test_score_none_on_unnormalizable_window():
    s = QualityScorer(model_path=None, model=_FakeModel(4.0))
    assert s.score([{"keypoints": None}] * 30) is None


def test_score_none_on_model_failure():
    class _Boom:
        def __call__(self, x):
            raise RuntimeError("bad weights")
    s = QualityScorer(model_path=None, model=_Boom())
    assert s.score(_frames()) is None


def test_unavailable_without_model_or_path():
    s = QualityScorer(model_path=None)
    assert not s.available
    assert s.score(_frames()) is None


def test_missing_weights_file_unavailable(tmp_path):
    s = QualityScorer(model_path=str(tmp_path / "nope.pt"))
    assert not s.available


def test_summary_mean_ai_score():
    reports = [
        {"rep_id": 1, "stroke_type": "high_clear", "overall_score": 50,
         "per_metric": {}, "weaknesses": [], "strengths": [], "ai_score": 40.0},
        {"rep_id": 2, "stroke_type": "high_clear", "overall_score": 60,
         "per_metric": {}, "weaknesses": [], "strengths": [], "ai_score": 60.0},
    ]
    summary = build_drill_summary(reports, "high_clear")
    assert summary["mean_ai_score"] == 50.0


def test_summary_omits_mean_ai_score_when_absent():
    reports = [{"rep_id": 1, "stroke_type": "high_clear", "overall_score": 50,
                "per_metric": {}, "weaknesses": [], "strengths": []}]
    summary = build_drill_summary(reports, "high_clear")
    assert "mean_ai_score" not in summary
```

- [ ] **Step 2: Run to verify failure** — `ModuleNotFoundError: ...quality.scorer`.

- [ ] **Step 3: Implement**

Create `badminton_analysis/quality/scorer.py`:

```python
"""Learned per-rep form scorer (TorchScript over normalized keypoint windows)."""
import os

from .normalize import normalize_window


def to_display_score(raw):
    """Map a 1-7 coach-scale prediction to 0-100 (clamped, 1dp)."""
    pct = (float(raw) - 1.0) / 6.0 * 100.0
    return round(min(100.0, max(0.0, pct)), 1)


class QualityScorer:
    def __init__(self, model_path=None, stroke_type="high_clear", model=None):
        self.stroke_type = stroke_type
        self.model_path = model_path
        self._model = model
        if model is not None:
            self.available = True
        elif model_path and os.path.exists(model_path):
            self.available = True   # loaded lazily on first score()
        else:
            self.available = False

    def _load(self):
        if self._model is None and self.available:
            try:
                import torch
                self._model = torch.jit.load(self.model_path, map_location="cpu")
                self._model.eval()
            except Exception as e:
                print("Quality model unavailable (" + str(e) + ")")
                self.available = False
        return self._model

    def score(self, window_frames, dominant="right"):
        """0-100 AI form score for one rep window, or None."""
        if not self.available:
            return None
        model = self._load()
        if model is None:
            return None
        arr = normalize_window(window_frames, mirror=(dominant == "left"))
        if arr is None:
            return None
        try:
            import torch
            with torch.no_grad():
                out = model(torch.from_numpy(arr).unsqueeze(0))
            return to_display_score(out.reshape(-1)[0].item())
        except Exception as e:
            print("Quality scoring failed (" + str(e) + ")")
            return None
```

In `badminton_analysis/posture/system.py`:
- `PostureRunner.__init__` gains trailing `quality_scorer=None`; store `self.quality_scorer = quality_scorer`.
- In `run()`, after `report["rep_id"] = rep.rep_id` add:
```python
            if self.quality_scorer is not None:
                ai = self.quality_scorer.score(window_frames, dominant=self.dominant)
                if ai is not None:
                    report["ai_score"] = ai
```
- `PostureAnalysisSystem.__init__` gains trailing `quality_model_path=None`; store it and `self._quality_scorer = None`.
- New method (next to `_build_racket_detector`):
```python
    def _build_quality_scorer(self):
        """Optional learned form scorer; analysis proceeds on failure."""
        if not self.quality_model_path or self.stroke_type != "high_clear":
            return
        try:
            from ..quality.scorer import QualityScorer
            scorer = QualityScorer(model_path=self.quality_model_path,
                                   stroke_type=self.stroke_type)
            self._quality_scorer = scorer if scorer.available else None
        except Exception as e:
            print("Quality scorer unavailable (" + str(e) + "); rule-based only.")
            self._quality_scorer = None
```
- Call `self._build_quality_scorer()` right after `self._build_racket_detector()` in `process_video`.
- Pass it to the runner (`:195-197`): `PostureRunner(BiomechanicalAnalyzer(dominant=self.dominant_hand), ..., quality_scorer=self._quality_scorer)`.
- In the `metadata.json` dict (next to the `racket` block) add:
```python
            "quality": {"model": self.quality_model_path if self._quality_scorer else None,
                        "scored_reps": sum(1 for r in reports if "ai_score" in r)},
```
(`reports` is in scope at the metadata write — verify; if the write happens before `reports` exists, compute after `runner.run` and stash on `self`.)

In `badminton_analysis/posture/writer.py`, inside `build_drill_summary` before the return, add:
```python
    ai_scores = [r["ai_score"] for r in reports if r.get("ai_score") is not None]
    if ai_scores:
        summary["mean_ai_score"] = round(sum(ai_scores) / len(ai_scores), 1)
```
(Adapt to the function's actual construction style — it may build the dict in one literal; add the key conditionally after building.)

In `main_posture.py`: add after `--racket-model`:
```python
    parser.add_argument("--quality-model", default=None,
                        help="Trained AI form-score weights (optional)")
```
and pass `quality_model_path=args.quality_model,` in the constructor call.

- [ ] **Step 4/5: Focused then full suite** — 8 new tests pass; full suite = baseline + 14. `main_posture.py --help` exits 0.

- [ ] **Step 6: Commit**

```bash
git add badminton_analysis/quality/scorer.py badminton_analysis/posture/system.py badminton_analysis/posture/writer.py main_posture.py tests/test_quality_scorer.py
git commit -m "feat(quality): AI form scorer wired into posture pipeline (fields only when available)"
```

---

### Task 3: App auto-discovery + UI chips (TDD + curl)

**Files:**
- Modify: `app.py` (`_quality_weights()`; append `--quality-model` to the posture cmd), `static/kestrel.js` (AI chips/tile), `static/kestrel.css`
- Test: `tests/test_quality_scorer.py` (append 3)

**Interfaces:**
- Consumes: `main_posture.py --quality-model` (Task 2); posture cmd block in `api_posture_analyze` (append after the `--racket-model` block); rep rows/summary renderers in `renderPostureResults`/`selectRep` (kestrel.js).
- Produces: `_quality_weights(base=None) -> str | None` (single filename `quality-high_clear.pt`).

- [ ] **Step 1: Tests** (append to `tests/test_quality_scorer.py`):

```python
import app as webapp


def test_quality_weights_discovery(tmp_path):
    assert webapp._quality_weights(base=tmp_path) is None
    (tmp_path / "quality-high_clear.pt").write_bytes(b"q")
    assert webapp._quality_weights(base=tmp_path).endswith("quality-high_clear.pt")


def test_posture_command_includes_quality_model_when_found(tmp_path, monkeypatch):
    # mirror the existing racket-flag route test in tests/test_posture_racket.py
    pass  # replaced by the real mirrored test in Step 3


def test_posture_command_omits_quality_model_when_absent(tmp_path, monkeypatch):
    pass  # replaced by the real mirrored test in Step 3
```
The two route tests MUST mirror `tests/test_posture_racket.py`'s posture-route Popen-capture tests exactly (same `_FakePostureProc`, same fixture setup), asserting `'--quality-model'` + value present when `webapp._quality_weights` is monkeypatched to a fixed path, absent when it returns None. Write them as real tests (the `pass` placeholders above are illustrative of naming only — implement fully by copying the racket versions and swapping the flag/monkeypatch target).

- [ ] **Step 2: Implement backend** — `_quality_weights` after `_racket_weights` in app.py:

```python
def _quality_weights(base=None):
    """Path to trained AI form-score weights when installed, else None."""
    base = Path(base) if base is not None else (PROJECT_ROOT / 'weights')
    p = base / 'quality-high_clear.pt'
    return str(p) if p.is_file() else None
```
In `api_posture_analyze`, right after the `--racket-model` append block:
```python
    quality_weights = _quality_weights()
    if quality_weights:
        cmd += ['--quality-model', quality_weights]
```

- [ ] **Step 3: Implement UI** — in `static/kestrel.js`:
- Rep rows (`renderPostureResults` rep list map): after the existing `scoreChipHTML(rep.overall_score)` add
  `(rep.ai_score !== undefined && rep.ai_score !== null ? '<span class="ai-chip mono" title="AI">AI ' + Math.round(rep.ai_score) + '</span>' : '')`.
- Drill summary grid: append a tile only when present:
  `(s.mean_ai_score !== undefined && s.mean_ai_score !== null ? '<div><span>' + (zh?'AI 评分':'AI score') + '</span><b>' + Math.round(s.mean_ai_score) + '</b></div>' : '')`.
- Rep detail (`selectRep`): after the `<h2>` line add
  `(rep.ai_score !== undefined && rep.ai_score !== null ? '<p class="ai-line">' + (zh?'AI 动作评分（模型判定）：':'AI form score (model-based): ') + '<b class="mono">' + rep.ai_score + '</b>/100</p>' : '')`.
- CSS append:
```css
.ai-chip{background:var(--accent-soft);color:var(--accent);border-radius:var(--radius-pill);
  padding:2px 8px;font-size:11px;font-weight:700;margin-left:6px;}
.ai-line{font-size:13px;color:var(--muted);margin:0 0 10px;}
```

- [ ] **Step 4: Verify** — pytest focused (11 in file) + full suite (baseline + 17); `node --check static/kestrel.js`; start app on 5096, curl `/kestrel` 200 + served-JS grep `ai-chip|_quality_weights` non-zero on the JS/py respectively (JS grep only for ai-chip); kill by PID.

- [ ] **Step 5: Commit**

```bash
git add app.py static/kestrel.js static/kestrel.css tests/test_quality_scorer.py
git commit -m "feat(quality): weights auto-discovery + AI score chips in drill UI"
```

---

### Task 4: Dataset prep + training scripts (TDD for pure helpers)

**Files:**
- Create: `scripts/prepare_quality_dataset.py`, `scripts/train_quality_model.py`
- Test: `tests/test_quality_scripts.py` (create)

**Interfaces:**
- Consumes: `normalize_window` (Task 1).
- Produces:
  - Prep: `clip_window(hit_frame, fps, pre_s=1.2, post_s=0.8) -> (start, end)` (ints, start ≥ 0); `manifest_row(player_id, swing_id, stroke, view, label, spread, path) -> dict`; CLI that walks `data/multisense/`, locates annotation + video files (discovery function `find_sources(root)` returning a list of `(video_path, annotations_path)` or `SystemExit` with download instructions), extracts pose per swing window with our pose stack, saves `data/multisense/prepared/<swing_id>.npy` + `manifest.jsonl`; `--limit N` smoke; prints pose-success rate and refuses to finish silently below `--min-pose-rate` (default 0.8).
  - Train: loads manifest + npy tensors; **player-level split** (`--val-players` fraction 0.2, seeded); TCN regressor (Conv1d 34→64→128 k5, GAP, MLP 128→64→1); MSE on mean coach score; reports val MAE, predict-mean baseline MAE, 3-bucket accuracy vs 33%; `--smoke` (2 epochs, ≤200 swings); exports TorchScript to `weights/quality-high_clear.pt`.
- The exact MultiSenseBadminton file layout is unknown until download — `find_sources` supports plausible layouts (per-player dirs with video+annotation files) and exits with instructions otherwise; Task 5 adapts it to the real layout and reports the adaptation (same bounded contingency that worked for RacketDB).

- [ ] **Step 1: Tests** (`tests/test_quality_scripts.py`):

```python
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from prepare_quality_dataset import clip_window, manifest_row
from train_quality_model import bucket_of, player_split


def test_clip_window_math():
    assert clip_window(90, 30.0) == (54, 114)      # 1.2s pre, 0.8s post @30fps
    assert clip_window(10, 30.0) == (0, 34)        # clamped at 0
    assert clip_window(90, 0) == (54, 114)         # fps<=0 -> fallback 30


def test_manifest_row_shape():
    row = manifest_row("P01", "P01_fc_0007", "high_clear", "front", 4.33, 1.15,
                       "prepared/P01_fc_0007.npy")
    assert row == {"player": "P01", "swing": "P01_fc_0007", "stroke": "high_clear",
                   "view": "front", "label": 4.33, "spread": 1.15,
                   "tensor": "prepared/P01_fc_0007.npy"}


def test_bucket_of():
    assert bucket_of(2.9) == "novice"
    assert bucket_of(3.0) == "intermediate"
    assert bucket_of(6.0) == "expert"


def test_player_split_no_leakage():
    rows = [{"player": "P%02d" % (i % 10), "swing": str(i)} for i in range(100)]
    train, val = player_split(rows, val_fraction=0.2, seed=0)
    train_players = {r["player"] for r in train}
    val_players = {r["player"] for r in val}
    assert train_players.isdisjoint(val_players)
    assert len(val_players) == 2
    assert len(train) + len(val) == 100


def test_player_split_deterministic():
    rows = [{"player": "P%02d" % (i % 10), "swing": str(i)} for i in range(100)]
    a = player_split(rows, val_fraction=0.2, seed=0)
    b = player_split(rows, val_fraction=0.2, seed=0)
    assert a == b
```

- [ ] **Step 2: RED** — ModuleNotFoundError.

- [ ] **Step 3: Implement both scripts.** Key required pieces (full scripts around them; heavy imports — torch, cv2, pose stack — inside `main()`/late so the tested helpers import clean):

`scripts/prepare_quality_dataset.py` helpers:
```python
def clip_window(hit_frame, fps, pre_s=1.2, post_s=0.8):
    if not fps or fps <= 0:
        fps = 30.0
    start = max(0, int(round(hit_frame - pre_s * fps)))
    end = int(round(hit_frame + post_s * fps))
    return start, end


def manifest_row(player_id, swing_id, stroke, view, label, spread, tensor_path):
    return {"player": player_id, "swing": swing_id, "stroke": stroke, "view": view,
            "label": float(label), "spread": float(spread), "tensor": str(tensor_path)}
```
`main()` flow: `find_sources(root)` → for each (video, annotations): parse swing entries (hit frame, per-coach ratings — parsing adapted at Task 5 against the real files, structured behind `parse_annotations(path) -> list[dict]`), `clip_window`, read frames via cv2, run pose extractor (constructed once; `--pose-family yolo-pose` default), pick largest person per frame (same `_spread` heuristic as the posture pipeline), `normalize_window`, save `.npy`, append manifest row; track posed-frame rate; final report + `--min-pose-rate` gate.

`scripts/train_quality_model.py` helpers + model:
```python
def bucket_of(score):
    return "novice" if score < 3.0 else ("intermediate" if score < 6.0 else "expert")


def player_split(rows, val_fraction=0.2, seed=0):
    import random
    players = sorted({r["player"] for r in rows})
    rng = random.Random(seed)
    rng.shuffle(players)
    n_val = max(1, int(round(len(players) * val_fraction)))
    val_players = set(players[:n_val])
    train = [r for r in rows if r["player"] not in val_players]
    val = [r for r in rows if r["player"] in val_players]
    return train, val
```
Model (inside main, torch import local):
```python
class TCNRegressor(nn.Module):
    def __init__(self, c_in=34, hidden=64):
        super().__init__()
        self.net = nn.Sequential(
            nn.Conv1d(c_in, hidden, 5, padding=2), nn.ReLU(),
            nn.Conv1d(hidden, hidden * 2, 5, padding=2, stride=2), nn.ReLU(),
            nn.Conv1d(hidden * 2, hidden * 2, 5, padding=2, stride=2), nn.ReLU(),
            nn.AdaptiveAvgPool1d(1))
        self.head = nn.Sequential(nn.Flatten(), nn.Linear(hidden * 2, 64),
                                  nn.ReLU(), nn.Linear(64, 1))

    def forward(self, x):          # x: (B, T, 34)
        return self.head(self.net(x.transpose(1, 2)))
```
Training loop: Adam 1e-3, MSE, 40 epochs (`--smoke`: 2 epochs, ≤200 swings), batch 64, seed 0, best-val-MAE checkpoint; metrics printed: val MAE, baseline MAE (predict train-mean), bucket accuracy + per-bucket recall; export `torch.jit.trace` on a `(1, 64, 34)` example → `--out weights/quality-high_clear.pt`.

- [ ] **Step 4/5: GREEN + full suite** — 5 new tests; baseline + 22. Both scripts `--help` exit 0 without importing torch/cv2.

- [ ] **Step 6: Commit**

```bash
git add scripts/prepare_quality_dataset.py scripts/train_quality_model.py tests/test_quality_scripts.py
git commit -m "feat(quality): dataset preparation + TCN training scripts"
```

---

### Task 5: CONTROLLER-EXECUTED — download, license, pose extraction, train, e2e

Not a subagent task (large download + GPU + adaptation against the real figshare layout).

- [ ] **Step 1:** List the figshare collection (`10.6084/m9.figshare.c.6725706`) via its API; identify the front/side video files + annotation files for forehand clear; record total subset size BEFORE downloading (the RacketDB lesson); record the license from the figshare metadata. Download the subset to `data/multisense/`.
- [ ] **Step 2:** Inspect the real annotation format; implement `parse_annotations` against it (adapt `find_sources`); report the adaptation.
- [ ] **Step 3:** `prepare_quality_dataset.py --limit 5` smoke → inspect tensors/manifest; then full run; **pose-success gate must clear 0.8** (else stop and investigate anonymization impact).
- [ ] **Step 4:** `train_quality_model.py --smoke`, then full run on the 4090 (background). Record val MAE vs baseline MAE and bucket accuracy vs 33%.
- [ ] **Step 5:** Install weights; e2e IMG_1270 high_clear re-run into a scratch output dir — `ai_score` per rep, `mean_ai_score` in summary, `quality.model` provenance in metadata; UI dogfood (AI chips render, hidden when weights removed); absent-weights parity (fields absent everywhere); suite green both ways.
- [ ] **Step 6:** Ledger + user summary (license, metrics, before/after examples); final whole-branch review (both this feature's commits and racket T4's) on the most capable model.

---

## Self-Review

- **Spec coverage:** shared normalization (T1) ✓; scorer with DI + failure tolerance + runner/writer/system/CLI wiring + provenance (T2) ✓; auto-discovery + presence-keyed bilingual UI (T3) ✓; prep with pose-success gate + player-split TCN training + metrics-vs-baseline (T4) ✓; download-size-first discipline, license record, adaptation reporting, e2e + parity + dogfood (T5) ✓. v1 = high_clear only enforced in `_build_quality_scorer`.
- **Placeholder scan:** T3 Step 1 contains two illustrative `pass` stubs explicitly ordered to be replaced by mirrored real tests in the same task — instruction, not placeholder. T4/T5's `parse_annotations` adaptation is the same bounded contingency proven on RacketDB, with helper seams (`find_sources`/`parse_annotations`) defined. No TBD/TODO.
- **Type consistency:** `normalize_window(frames, mirror, target)` identical T1↔T2↔T4; `(T=64, 34)` tensor shape matches `TCNRegressor` input and `torch.jit.trace` example; `ai_score` (0–100 float 1dp) ↔ `to_display_score` ↔ UI `Math.round`; `mean_ai_score` writer↔UI; `quality-high_clear.pt` name identical in trainer `--out`, `_quality_weights`, spec; manifest keys (`player/swing/stroke/view/label/spread/tensor`) match `player_split` usage.
- **Count math:** baseline B → B+6 (T1) → B+14 (T2) → B+17 (T3) → B+22 (T4).

## Notes for the executor

- Base commit recorded at execution start; racket-feature commits may land in between — rebase mentally on current HEAD, anchors are code-relative not line-frozen where possible.
- The posture pipeline runs the scorer on CPU inside the analysis subprocess — TCN at (64,34) is microseconds; no GPU dependency at inference.
- Do NOT commit anything under `weights/` or `data/`.
- Windows: kill leaked servers by PID; `PYTHONUTF8=1` on pipeline invocations.
