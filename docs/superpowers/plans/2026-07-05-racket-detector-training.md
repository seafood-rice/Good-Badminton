# Racket Detector Training (RacketDB) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Task 4 is controller-executed (GPU training run), not a subagent task.

**Goal:** Fine-tune a YOLO racket detector on RacketDB, install the weights where both pipelines auto-discover them, and wire the posture pipeline to use detector-first racket heads with wrist-inference fallback.

**Architecture:** A standalone training script (`scripts/train_racket_detector.py`) prepares/validates the RacketDB download, trains `yolo11n`, and installs `weights/yolo11n-racket.pt`. `app.py` gains `_racket_weights()` which appends `--racket-model <path>` to both analysis subprocess commands when weights exist. `PostureAnalysisSystem` gains the same detect-then-fallback pattern the match pipeline already has (`system.py:477-481`), plus detected/inferred frame counts in `metadata.json`. With no weights file present, behavior is byte-identical to today.

**Tech Stack:** Python 3, ultralytics 8.4.77 (already in venv), CUDA (RTX 4090), Flask, pytest.

## Global Constraints

- **Weights naming convention (discovered):** `main.py --racket-model` already defaults to `weights/yolo11s-racket.pt`. The trained nano model installs as `weights/yolo11n-racket.pt`; `_racket_weights()` checks `yolo11n-racket.pt` then `yolo11s-racket.pt` so either drop-in works.
- **`weights/` is gitignored** (existing .pt files were force-added long ago). Do NOT commit the trained weights — RacketDB's repo states **no license**; weights stay local until the license is clarified. Task 4 surfaces this to the user.
- **Dataset stays out of git:** RacketDB downloads to `data/racketdb/` (Task 1 adds `data/` to `.gitignore`). Full images live on Hugging Face (`muhabdulhaq/racketdb`, dataset repo); the GitHub repo carries annotation-format zips (incl. YOLOv8). 22,682 images total; paper splits 16,608/3,175/2,899.
- **No UI changes.** Auto-discovery only; absent weights → both pipelines behave exactly as today (RacketDetector already no-ops on a missing path).
- **All existing tests stay green** (baseline **187**). Backend tasks are TDD via `./.venv/Scripts/python.exe -m pytest` (Windows; `PYTHONUTF8=1` when launching apps/pipelines). No GPU/network in pytest — training-script tests cover only pure helpers on tmp trees.
- Windows server/process hygiene: kill by PID via netstat+taskkill, never bare `kill`.
- Entry-point anchors (verified): match cmd built at `app.py:441-448`; posture cmd at `app.py:940-945`; posture ctor at `badminton_analysis/posture/system.py:74-79`; posture racket line at `:231` inside `_capture_frame`; posture metadata write at `:198`; `main_posture.py` argparse at `:8-27`.

---

### Task 1: Training script + dataset preparation helpers (TDD for helpers)

**Files:**
- Create: `scripts/train_racket_detector.py`
- Modify: `.gitignore` (add `data/`)
- Test: `tests/test_train_racket_script.py` (create)

**Interfaces:**
- Produces: `scripts/train_racket_detector.py` with module-level pure helpers (imported by tests):
  - `find_dataset_yaml(root: Path) -> Path | None` — an existing `data.yaml`/`dataset.yaml` directly under root.
  - `discover_splits(root: Path) -> dict | None` — maps `{'train': ..., 'val': ..., 'test': ...}` image-dir paths for Roboflow/CVAT-style layouts (`<split>/images` or `images/<split>`); `val` accepts a `valid` or `val` dir; returns None if no train split found.
  - `build_dataset_yaml(root: Path, out_path: Path) -> Path` — writes a single-class (`racket`) ultralytics yaml from a found yaml (rewriting its `path:` to `root`) or discovered splits; raises `SystemExit` with download instructions when neither works.
  - CLI: `--data data/racketdb --model yolo11n.pt --epochs 60 --imgsz 640 --device 0 --out weights/yolo11n-racket.pt --smoke` (smoke: `epochs=1, fraction=0.02, imgsz=320`).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_train_racket_script.py`:

```python
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from train_racket_detector import build_dataset_yaml, discover_splits, find_dataset_yaml


def _mk(p):
    p.mkdir(parents=True, exist_ok=True)
    return p


def test_discover_splits_roboflow_layout(tmp_path):
    for split in ("train", "valid", "test"):
        _mk(tmp_path / split / "images")
        _mk(tmp_path / split / "labels")
    splits = discover_splits(tmp_path)
    assert splits["train"] == tmp_path / "train" / "images"
    assert splits["val"] == tmp_path / "valid" / "images"
    assert splits["test"] == tmp_path / "test" / "images"


def test_discover_splits_cvat_layout(tmp_path):
    for split in ("train", "val"):
        _mk(tmp_path / "images" / split)
        _mk(tmp_path / "labels" / split)
    splits = discover_splits(tmp_path)
    assert splits["train"] == tmp_path / "images" / "train"
    assert splits["val"] == tmp_path / "images" / "val"
    assert splits["test"] is None


def test_build_yaml_prefers_existing_yaml(tmp_path):
    (tmp_path / "data.yaml").write_text(
        "path: /somewhere/else\ntrain: train/images\nval: valid/images\nnc: 1\nnames: ['racket']\n",
        encoding="utf-8")
    out = build_dataset_yaml(tmp_path, tmp_path / "racketdb.yaml")
    text = out.read_text(encoding="utf-8")
    assert str(tmp_path) in text
    assert "/somewhere/else" not in text


def test_build_yaml_from_discovered_splits(tmp_path):
    for split in ("train", "valid"):
        _mk(tmp_path / split / "images")
    out = build_dataset_yaml(tmp_path, tmp_path / "racketdb.yaml")
    text = out.read_text(encoding="utf-8")
    assert "names:" in text and "racket" in text
    assert "train" in text and "valid" in text


def test_build_yaml_fails_clearly_when_empty(tmp_path):
    with pytest.raises(SystemExit) as e:
        build_dataset_yaml(tmp_path, tmp_path / "racketdb.yaml")
    assert "racketdb" in str(e.value).lower() or "huggingface" in str(e.value).lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_train_racket_script.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'train_racket_detector'`.

- [ ] **Step 3: Write the script**

Create `scripts/train_racket_detector.py`:

```python
"""Fine-tune a YOLO racket detector on RacketDB and install the weights.

Dataset: https://github.com/muhabdulhaq/racketdb (full images on Hugging Face:
https://huggingface.co/datasets/muhabdulhaq/racketdb). Download it to data/racketdb
first, e.g.:

    ./.venv/Scripts/pip.exe install -U huggingface_hub
    ./.venv/Scripts/python.exe -c "from huggingface_hub import snapshot_download; \
snapshot_download('muhabdulhaq/racketdb', repo_type='dataset', local_dir='data/racketdb')"

License note: the RacketDB repository states no license. Weights trained on it are for
local/personal use; do not commit or redistribute them until the license is clarified.
"""
import argparse
import shutil
import sys
from pathlib import Path

_DOWNLOAD_HELP = (
    "RacketDB not found or empty at %s.\n"
    "Download it first (see the docstring at the top of this script), then re-run.\n"
    "Expected either a data.yaml at the root, or split dirs like train/images,\n"
    "valid/images (Roboflow/YOLOv8 export) or images/train, images/val (CVAT export)."
)


def find_dataset_yaml(root):
    for name in ("data.yaml", "dataset.yaml"):
        p = Path(root) / name
        if p.is_file():
            return p
    return None


def discover_splits(root):
    root = Path(root)
    out = {"train": None, "val": None, "test": None}
    aliases = {"train": ("train",), "val": ("valid", "val"), "test": ("test",)}
    for key, names in aliases.items():
        for name in names:
            a = root / name / "images"          # Roboflow-style
            b = root / "images" / name          # CVAT-style
            if a.is_dir():
                out[key] = a
                break
            if b.is_dir():
                out[key] = b
                break
    if out["train"] is None:
        return None
    return out


def build_dataset_yaml(root, out_path):
    root = Path(root)
    out_path = Path(out_path)
    existing = find_dataset_yaml(root)
    if existing is not None:
        lines = []
        wrote_path = False
        for line in existing.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("path:"):
                lines.append("path: " + str(root.resolve()))
                wrote_path = True
            else:
                lines.append(line)
        if not wrote_path:
            lines.insert(0, "path: " + str(root.resolve()))
        out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return out_path
    splits = discover_splits(root)
    if splits is None:
        raise SystemExit(_DOWNLOAD_HELP % root)
    lines = ["path: " + str(root.resolve()),
             "train: " + str(splits["train"].relative_to(root))]
    lines.append("val: " + str((splits["val"] or splits["train"]).relative_to(root)))
    if splits["test"] is not None:
        lines.append("test: " + str(splits["test"].relative_to(root)))
    lines += ["nc: 1", "names: ['racket']"]
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out_path


def main():
    ap = argparse.ArgumentParser(description="Train the RacketDB racket detector")
    ap.add_argument("--data", default="data/racketdb")
    ap.add_argument("--model", default="yolo11n.pt")
    ap.add_argument("--epochs", type=int, default=60)
    ap.add_argument("--imgsz", type=int, default=640)
    ap.add_argument("--batch", type=int, default=-1)
    ap.add_argument("--device", default="0")
    ap.add_argument("--out", default="weights/yolo11n-racket.pt")
    ap.add_argument("--smoke", action="store_true",
                    help="1 epoch on 2%% of the data at imgsz 320 — script sanity only")
    args = ap.parse_args()

    root = Path(args.data)
    license_file = next((p for p in (root / "LICENSE", root / "LICENSE.md",
                                     root / "LICENSE.txt") if p.is_file()), None)
    if license_file is not None:
        print("RacketDB license file found: " + str(license_file))
    else:
        print("NOTE: RacketDB states no license. Trained weights are for local use only; "
              "do not commit or redistribute them.")

    yaml_path = build_dataset_yaml(root, root / "racketdb.ultralytics.yaml")
    print("Dataset yaml: " + str(yaml_path))

    from ultralytics import YOLO
    model = YOLO(args.model)
    train_kwargs = dict(data=str(yaml_path), epochs=args.epochs, imgsz=args.imgsz,
                        batch=args.batch, device=args.device, seed=0,
                        project="runs", name="racketdb")
    if args.smoke:
        train_kwargs.update(epochs=1, fraction=0.02, imgsz=320)
        print("SMOKE MODE: 1 epoch, 2% of data, imgsz 320")
    results = model.train(**train_kwargs)

    metrics = model.val(data=str(yaml_path), device=args.device)
    print("val mAP50:    %.4f" % metrics.box.map50)
    print("val mAP50-95: %.4f" % metrics.box.map)

    best = Path(results.save_dir) / "weights" / "best.pt"
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(best, out)
    print("Installed weights: " + str(out))


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Add `data/` to `.gitignore`**

Append to `.gitignore`:
```
# Training datasets (downloaded separately)
data/
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_train_racket_script.py -v`
Expected: PASS (5 tests).

- [ ] **Step 6: Run the full suite**

Run: `./.venv/Scripts/python.exe -m pytest -q`
Expected: `192 passed` (187 + 5).

- [ ] **Step 7: Commit**

```bash
git add scripts/train_racket_detector.py tests/test_train_racket_script.py .gitignore
git commit -m "feat(training): RacketDB racket-detector training script with dataset discovery"
```

---

### Task 2: Posture pipeline — detector-first racket heads (TDD)

**Files:**
- Modify: `badminton_analysis/posture/system.py` (ctor param + lazy detector + `_resolve_racket_head` + metadata + summary line)
- Modify: `main_posture.py` (`--racket-model` passthrough)
- Test: `tests/test_posture_racket.py` (create)

**Interfaces:**
- Consumes: `badminton_analysis.detection.racket.RacketDetector` (existing; `detect_racket_head(frame, roi_corners=None) -> (x, y) | None`), `ja.infer_racket_head(kp, dominant=...)`.
- Produces: `PostureAnalysisSystem(..., racket_model_path=None)`; instance attrs `self.racket_model_path`, `self._racket_detector` (None until built), `self._racket_stats = {"detected": 0, "inferred": 0}`; method `_resolve_racket_head(self, frame, kp, ja) -> (x, y) | None`; `metadata.json` gains a `racket` key (Task 4's e2e reads `racket.model` to confirm the app passed the flag).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_posture_racket.py`:

```python
import numpy as np
import pytest

from badminton_analysis.posture.system import PostureAnalysisSystem
import badminton_analysis.analysis.joint_angles as ja


def _system(tmp_path, **kwargs):
    video = tmp_path / "drill.mp4"
    video.write_bytes(b"x")  # ctor only checks existence
    return PostureAnalysisSystem(str(video), "high_clear",
                                 output_dir=str(tmp_path / "out"), **kwargs)


def _kp():
    kp = np.zeros((17, 2), dtype=float)
    kp[ja.R_ELBOW] = (100.0, 100.0)
    kp[ja.R_WRIST] = (120.0, 80.0)
    return kp


class _FakeDetector:
    def __init__(self, point):
        self.point = point
        self.calls = 0

    def detect_racket_head(self, frame, roi_corners=None):
        self.calls += 1
        return self.point


def test_detector_result_wins(tmp_path):
    sys_ = _system(tmp_path)
    sys_._racket_detector = _FakeDetector((5.0, 6.0))
    head = sys_._resolve_racket_head(frame=None, kp=_kp(), ja=ja)
    assert head == (5.0, 6.0)
    assert sys_._racket_stats == {"detected": 1, "inferred": 0}


def test_fallback_when_detector_returns_none(tmp_path):
    sys_ = _system(tmp_path)
    sys_._racket_detector = _FakeDetector(None)
    head = sys_._resolve_racket_head(frame=None, kp=_kp(), ja=ja)
    assert head is not None  # wrist inference produced a point
    assert sys_._racket_stats == {"detected": 0, "inferred": 1}


def test_fallback_when_no_detector(tmp_path):
    sys_ = _system(tmp_path)
    assert sys_._racket_detector is None
    head = sys_._resolve_racket_head(frame=None, kp=_kp(), ja=ja)
    assert head is not None
    assert sys_._racket_stats == {"detected": 0, "inferred": 1}


def test_missing_weights_path_does_not_raise(tmp_path):
    sys_ = _system(tmp_path, racket_model_path=str(tmp_path / "nope.pt"))
    sys_._build_racket_detector()
    assert sys_._racket_detector is None or sys_._racket_detector.model is None
    head = sys_._resolve_racket_head(frame=None, kp=_kp(), ja=ja)
    assert head is not None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_posture_racket.py -v`
Expected: FAIL — `TypeError: __init__() got an unexpected keyword argument 'racket_model_path'`.

- [ ] **Step 3: Implement the wiring**

In `badminton_analysis/posture/system.py`:

(a) Ctor signature (`:74-79`) — add the param at the end:
```python
    def __init__(self, video_path, stroke_type, dominant_hand="right",
                 output_dir=None, ball_model_path=None, show_display=False,
                 show_overlay=True, pose_model="weights/yolo11n-pose.pt",
                 pose_family="yolo-pose", pose_mode="balanced",
                 yolo_pose_model="weights/yolo11n-pose.pt",
                 report_llm="off", racket_model_path=None):
```
and alongside the other attribute assignments add:
```python
        self.racket_model_path = racket_model_path
        self._racket_detector = None
        self._racket_stats = {"detected": 0, "inferred": 0}
```

(b) New methods (place next to `_capture_frame`):
```python
    def _build_racket_detector(self):
        """Optional trained racket detector; analysis proceeds on failure."""
        if not self.racket_model_path:
            return
        try:
            from ..detection.racket import RacketDetector
            self._racket_detector = RacketDetector(model_path=self.racket_model_path)
        except Exception as e:
            print("Racket detector unavailable (" + str(e) + "); using wrist inference.")
            self._racket_detector = None

    def _resolve_racket_head(self, frame, kp, ja):
        if self._racket_detector is not None:
            head = self._racket_detector.detect_racket_head(frame)
            if head is not None:
                self._racket_stats["detected"] += 1
                return head
        self._racket_stats["inferred"] += 1
        return ja.infer_racket_head(kp, dominant=self.dominant_hand)
```

(c) In `process_video`, call `self._build_racket_detector()` right after `pose = self._build_pose_processor()`.

(d) In `_capture_frame` (`:231`), replace:
```python
            racket_head = ja.infer_racket_head(kp, dominant=self.dominant_hand)
```
with:
```python
            racket_head = self._resolve_racket_head(frame, kp, ja)
```

(e) In the `write_json(os.path.join(self.save_dir, "metadata.json"), {...})` dict (`:198`), add:
```python
            "racket": {"detected_frames": self._racket_stats["detected"],
                       "inferred_frames": self._racket_stats["inferred"],
                       "model": self.racket_model_path},
```

(f) After the frame loop finishes in `process_video` (next to the existing end-of-run prints), add:
```python
        print("Racket source: %d detected / %d inferred"
              % (self._racket_stats["detected"], self._racket_stats["inferred"]), flush=True)
```

In `main_posture.py`, add after the `--report-llm` argument (`:27`):
```python
    parser.add_argument("--racket-model", default=None,
                        help="Trained racket detector weights (optional)")
```
and pass `racket_model_path=args.racket_model,` in the `PostureAnalysisSystem(` call (`:32`).

- [ ] **Step 4: Run tests to verify they pass**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_posture_racket.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Run the full suite**

Run: `./.venv/Scripts/python.exe -m pytest -q`
Expected: `196 passed` (192 + 4). Existing posture tests construct the system without the new kwarg — the default keeps them green; `--help` on `main_posture.py` still works: `./.venv/Scripts/python.exe main_posture.py --help` exits 0.

- [ ] **Step 6: Commit**

```bash
git add badminton_analysis/posture/system.py main_posture.py tests/test_posture_racket.py
git commit -m "feat(posture): detector-first racket heads with wrist-inference fallback"
```

---

### Task 3: App auto-discovery (TDD)

**Files:**
- Modify: `app.py` (`_racket_weights()` helper; append `--racket-model` to both subprocess cmds)
- Test: `tests/test_posture_racket.py` (append 2 tests)

**Interfaces:**
- Consumes: `main.py --racket-model` (exists, `main.py:30`), `main_posture.py --racket-model` (Task 2).
- Produces: `_racket_weights(base=None) -> str | None` — first existing of `yolo11n-racket.pt`, `yolo11s-racket.pt` under `base` (default `PROJECT_ROOT / 'weights'`).

- [ ] **Step 1: Write the failing tests** (append to `tests/test_posture_racket.py`)

```python
import app as webapp


def test_racket_weights_prefers_n_then_s(tmp_path):
    assert webapp._racket_weights(base=tmp_path) is None
    (tmp_path / "yolo11s-racket.pt").write_bytes(b"s")
    assert webapp._racket_weights(base=tmp_path).endswith("yolo11s-racket.pt")
    (tmp_path / "yolo11n-racket.pt").write_bytes(b"n")
    assert webapp._racket_weights(base=tmp_path).endswith("yolo11n-racket.pt")


def test_racket_weights_default_base_is_weights_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(webapp, "PROJECT_ROOT", tmp_path)
    assert webapp._racket_weights() is None
    (tmp_path / "weights").mkdir()
    (tmp_path / "weights" / "yolo11n-racket.pt").write_bytes(b"n")
    assert webapp._racket_weights().endswith("yolo11n-racket.pt")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_posture_racket.py -v`
Expected: the 2 new tests FAIL — `AttributeError: module 'app' has no attribute '_racket_weights'`.

- [ ] **Step 3: Implement**

In `app.py`, after `_running_job_for`, add:
```python
def _racket_weights(base=None):
    """Path to trained racket-detector weights when installed, else None."""
    base = Path(base) if base is not None else (PROJECT_ROOT / 'weights')
    for name in ('yolo11n-racket.pt', 'yolo11s-racket.pt'):
        p = base / name
        if p.is_file():
            return str(p)
    return None
```
(`Path` is already imported in app.py; verify, add `from pathlib import Path` only if missing.)

In `api_analyze`, immediately after the `cmd = [...]` list closes (`app.py:441-448` block), add:
```python
    racket_weights = _racket_weights()
    if racket_weights:
        cmd += ['--racket-model', racket_weights]
```
In `api_posture_analyze`, immediately after its `cmd = [...]` list closes (`app.py:940-945` block), add the same three lines.

- [ ] **Step 4: Run tests to verify they pass**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_posture_racket.py -v`
Expected: PASS (6 tests).

- [ ] **Step 5: Run the full suite**

Run: `./.venv/Scripts/python.exe -m pytest -q`
Expected: `198 passed` (196 + 2).

- [ ] **Step 6: Commit**

```bash
git add app.py tests/test_posture_racket.py
git commit -m "feat(api): auto-pass racket weights to both pipelines when installed"
```

---

### Task 4: CONTROLLER-EXECUTED — download, license check, train, evaluate, e2e

Not a subagent task. The controller runs these steps directly (GPU job + long waits).

- [ ] **Step 1: Download RacketDB**

```bash
./.venv/Scripts/pip.exe install -U huggingface_hub
./.venv/Scripts/python.exe -c "from huggingface_hub import snapshot_download; snapshot_download('muhabdulhaq/racketdb', repo_type='dataset', local_dir='data/racketdb')"
```
Inspect the resulting layout (`ls data/racketdb`). If the HF dataset carries images without YOLO labels, fetch the GitHub repo's YOLOv8-format annotation zip and unpack it alongside. Adapt `discover_splits` expectations only if the real layout differs from both supported shapes (report any adaptation).

- [ ] **Step 2: License check**

Look for a LICENSE in the download + the HF dataset card. Record the finding in the ledger and the final user summary. Current knowledge: none stated → weights stay local, uncommitted.

- [ ] **Step 3: Smoke run**

```bash
PYTHONUTF8=1 ./.venv/Scripts/python.exe scripts/train_racket_detector.py --smoke
```
Expected: completes in minutes, installs `weights/yolo11n-racket.pt` (smoke-quality), prints mAP.

- [ ] **Step 4: Full training run** (background, ~1–2h on the 4090; notify on completion)

```bash
PYTHONUTF8=1 ./.venv/Scripts/python.exe scripts/train_racket_detector.py
```
Record final val mAP50 / mAP50-95. Compare against the RacketDB paper's YOLOv8n baseline (fetch the number from the paper at this step; report both numbers).

- [ ] **Step 5: E2E on IMG_1270**

Save the current `outputs/IMG_1270/posture/drill_reps.jsonl` wrist_flexion measured values (baseline). Re-run posture analysis via the API or `main_posture.py --video-path videos/IMG_1270.mov --stroke-type high_clear --racket-model weights/yolo11n-racket.pt --output-dir <scratch>` (scratch output dir — do NOT overwrite the real fixtures). Verify: `metadata.json` `racket.model` set and `detected_frames` a solid majority; wrist_flexion measured values shifted off 180. Suite green (`198 passed`), and with the weights file temporarily renamed away, `_racket_weights()` returns None (behavior identical to today).

- [ ] **Step 6: Ledger + user summary** (license flag, mAP numbers, before/after wrist metrics).

---

## Self-Review

- **Spec coverage:** training script w/ discovery + smoke + license gate (T1) ✓; posture detect-then-fallback + stats in metadata + summary line + CLI passthrough (T2) ✓; app auto-discovery, no UI (T3) ✓; download/license/smoke/full-train/mAP-vs-baseline/e2e IMG_1270/no-weights parity (T4) ✓. Spec's `weights/racket.pt` name superseded by the discovered `main.py` convention (`yolo11n-racket.pt` / `yolo11s-racket.pt`) — recorded in Global Constraints.
- **Placeholder scan:** T4 Step 1's "adapt if the real layout differs" is a bounded contingency with a reporting requirement, not a placeholder — the two supported layouts are fully coded in T1. No TBD/TODO.
- **Type consistency:** `racket_model_path` (ctor) ↔ `--racket-model` (both CLIs) ↔ `_racket_weights()` return; `_resolve_racket_head(frame, kp, ja)` defined (T2) and consumed at the `:231` replacement; `_racket_stats` keys `detected`/`inferred` match metadata fields `detected_frames`/`inferred_frames` mapping; test helper `_system`/`_kp`/`_FakeDetector` self-contained; `find_dataset_yaml`/`discover_splits`/`build_dataset_yaml` names identical between script and tests.
- **Count math:** 187 → 192 (T1 +5) → 196 (T2 +4) → 198 (T3 +2).

## Notes for the executor

- Base commit is `9d22f5c` (spec commit).
- T2's `_system` helper writes a dummy file as the "video" — the ctor only checks existence; no cv2 decoding happens until `process_video`, which these tests never call.
- `RacketDetector(model_path=<missing>)` already degrades to `model=None` (its ctor checks `os.path.exists`); T2's try/except covers import/CUDA errors, not missing files.
- Do NOT `git add -f` anything under `weights/` or `data/`.
- T4 runs on the controller: training via background Bash with completion notification; kill any stray processes by PID.
