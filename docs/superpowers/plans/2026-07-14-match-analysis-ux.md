# Match Analysis UX (Sub-project A) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make match analysis whole and responsive — full-duration output video, real always-advancing progress, a faster run (lossless win + Fast/Accurate toggle), and localized visualizations.

**Architecture:** Small surgical changes to the existing streaming pipeline (`badminton_analysis/system.py`), the Flask job runner (`app.py`), and the wizard UI (`static/kestrel.js`), plus a mechanical unify of the two duplicate visualization modules. No new heavy dependencies.

**Tech Stack:** Python, OpenCV, Flask, matplotlib; vanilla JS frontend.

## Global Constraints

- **Windows env:** run Python as `.venv/Scripts/python.exe` with `PYTHONUTF8=1`; run pytest as `PYTHONUTF8=1 .venv/Scripts/python.exe -m pytest ...`; kill background processes by PID.
- **Never commit** model weights or datasets; never `git add -A`/`-u`/`.` — stage explicit paths only.
- **Never-fatal instrumentation:** progress-heartbeat writes must never abort analysis (wrap in try/except).
- **Accurate is the default and behavior-preserving** for analytics (every frame analyzed); the full-duration video (A1) is the one intended change to the output video.
- **Stable output filenames:** `match_heatmap.png`, `match_scatter.png`, and the output video path stay unchanged so existing frontend URLs work.
- **No deadlock:** the web subprocess output must be redirected to a file, never left on an undrained pipe.

---

## File Structure

- `badminton_analysis/system.py` — A1 (write non-court frames), A2 (progress.json heartbeat), A3b (court-view cadence), A3c (Fast/Accurate stride + skip dense analytics).
- `app.py` — A2 (subprocess log redirect, poll progress.json, surface errors), A3c (`--analysis-quality` passthrough).
- `static/kestrel.js` — A2 (progress stages + heartbeat), A3c (Fast/Accurate toggle in match config).
- `main.py` — A3c (`--analysis-quality` flag), A4 (import unified viz module).
- `badminton_analysis/visualization/player_positions.py` — A4 (new unified module).
- (delete) `badminton_analysis/visualization/player_positions_zh.py`, `player_positions_en.py` — A4.
- `scripts/profile_pipeline.py` — A3a (profiling harness).
- Tests: `tests/test_full_duration_video.py`, `tests/test_progress_heartbeat.py`, `tests/test_app_progress.py`, `tests/test_analysis_quality.py`, `tests/test_player_positions_i18n.py`.

---

### Task 1: A1 — Full-duration output video

**Files:**
- Modify: `badminton_analysis/system.py` — `_process_frame` non-court branch (currently `if not is_court: return frame, detect_frame_count`, ~line 331).
- Test: `tests/test_full_duration_video.py`

**Interfaces:**
- Consumes: `_process_frame(self, frame, template_gray, corners, roi_corners, frame_count, out, detect_frame_count)` — `out` is the `cv2.VideoWriter`.
- Produces: every decoded input frame is written to `out` exactly once.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_full_duration_video.py
import badminton_analysis.system as sysmod


class _Writer:
    def __init__(self):
        self.count = 0
    def write(self, frame):
        self.count += 1


def _bare_system():
    s = sysmod.BadmintonAnalysisSystem.__new__(sysmod.BadmintonAnalysisSystem)
    s.show_display = False
    s.save_images = False
    return s


def test_non_court_frame_is_written_passthrough():
    s = _bare_system()
    out = _Writer()
    # is_court_view False -> historically returned before out.write. Force non-court.
    s.is_court_view = lambda *a, **k: False
    # Minimal stubs for the pre-branch code in _process_frame:
    s.consecutive_non_court_frames = 0
    s.is_court_view_count = 0
    s.rally_active = False
    s.rally_count = 0
    s._current_rally_start = 0
    s.rally_segments = []
    s.shuttlecock_tracker = type("T", (), {"clear_trajectory": lambda self=None: None})()
    import numpy as np
    frame = np.zeros((10, 10, 3), dtype=np.uint8)
    gray = frame[:, :, 0]
    s._process_frame(frame, gray, [(0,0)]*4, [(0,0),(1,1)], 1, out, 0)
    assert out.count == 1  # non-court frame written (was 0 before the fix)
```

- [ ] **Step 2: Run to verify it fails**

Run: `PYTHONUTF8=1 .venv/Scripts/python.exe -m pytest tests/test_full_duration_video.py -v`
Expected: FAIL (`out.count == 0` — non-court frame not written).

- [ ] **Step 3: Implement**

In `_process_frame`, change the non-court early return. Find:

```python
        if not is_court:
            return frame, detect_frame_count
```

Replace with (write the raw frame so the output keeps full duration):

```python
        if not is_court:
            # Full-duration output: write the raw (un-annotated) frame instead of
            # dropping it, so the result video matches the input duration.
            if self.show_display:
                cv2.imshow('frame', frame)
                cv2.waitKey(1)
            out.write(frame)
            return frame, detect_frame_count
```

- [ ] **Step 4: Run to verify it passes**

Run: `PYTHONUTF8=1 .venv/Scripts/python.exe -m pytest tests/test_full_duration_video.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add badminton_analysis/system.py tests/test_full_duration_video.py
git commit -m "feat(match): write non-court frames so output video keeps full duration"
```

---

### Task 2: A2 — progress.json heartbeat (pipeline side)

**Files:**
- Modify: `badminton_analysis/system.py` — `__init__` (after `save_dir` is created, ~line 128), `process_video` (frame loop ~231-236, and stage transitions), `_cleanup` (~713).
- Test: `tests/test_progress_heartbeat.py`

**Interfaces:**
- Produces: `<save_dir>/progress.json` = `{"stage": str, "current_frame": int, "total_frames": int, "pct": int, "updated": float}`. Stages: `initializing`, `court_setup`, `analyzing`, `visualizing`, `encoding`, `done`. Method `_write_progress(self, stage, current_frame=None)`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_progress_heartbeat.py
import json
import os
import badminton_analysis.system as sysmod


def _sys(tmp_path):
    s = sysmod.BadmintonAnalysisSystem.__new__(sysmod.BadmintonAnalysisSystem)
    s.save_dir = str(tmp_path)
    s._total_frames = 200
    return s


def test_write_progress_emits_json(tmp_path):
    s = _sys(tmp_path)
    s._write_progress("analyzing", current_frame=50)
    d = json.load(open(os.path.join(str(tmp_path), "progress.json"), encoding="utf-8"))
    assert d["stage"] == "analyzing"
    assert d["current_frame"] == 50
    assert d["total_frames"] == 200
    assert d["pct"] == 25  # 50/200
    assert isinstance(d["updated"], (int, float))


def test_write_progress_never_raises_on_bad_dir():
    s = sysmod.BadmintonAnalysisSystem.__new__(sysmod.BadmintonAnalysisSystem)
    s.save_dir = "/nonexistent\\zzz\\path"
    s._total_frames = 0
    s._write_progress("analyzing", current_frame=1)  # must not raise


def test_write_progress_stage_only_pins_pct(tmp_path):
    s = _sys(tmp_path)
    s._write_progress("encoding")
    d = json.load(open(os.path.join(str(tmp_path), "progress.json"), encoding="utf-8"))
    assert d["stage"] == "encoding"
    assert d["pct"] == 99
```

- [ ] **Step 2: Run to verify it fails**

Run: `PYTHONUTF8=1 .venv/Scripts/python.exe -m pytest tests/test_progress_heartbeat.py -v`
Expected: FAIL (`_write_progress` undefined).

- [ ] **Step 3: Implement `_write_progress`**

Add this method to `BadmintonAnalysisSystem` (near `_cleanup`):

```python
    # Per-stage fixed pct for non-analyzing stages; analyzing derives from frames.
    _PROGRESS_STAGE_PCT = {
        "initializing": 1, "court_setup": 3, "analyzing": None,
        "visualizing": 97, "encoding": 99, "done": 100,
    }

    def _write_progress(self, stage, current_frame=None):
        """Write a progress heartbeat to <save_dir>/progress.json. Never fatal."""
        try:
            total = int(getattr(self, "_total_frames", 0) or 0)
            fixed = self._PROGRESS_STAGE_PCT.get(stage)
            if fixed is not None:
                pct = fixed
            elif total > 0 and current_frame is not None:
                pct = max(3, min(96, int(current_frame / total * 100)))
            else:
                pct = 3
            payload = {
                "stage": stage,
                "current_frame": int(current_frame or 0),
                "total_frames": total,
                "pct": int(pct),
                "updated": time.time(),
            }
            with open(os.path.join(self.save_dir, "progress.json"), "w", encoding="utf-8") as fh:
                json.dump(payload, fh)
        except Exception:
            pass
```

(`time`, `os`, `json` are already imported at the top of `system.py` — confirm; add `import json` if missing.)

- [ ] **Step 4: Wire the stage transitions in `process_video`**

- In `__init__`, right after `os.makedirs(self.save_dir, exist_ok=True)` (~line 128), add:
  ```python
        self._total_frames = 0
        self._write_progress("initializing")
  ```
- In `process_video`, right after `total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))` (~line 188), add:
  ```python
        self._total_frames = total_frames
  ```
- Right after court annotation is set (`self.court_roi_corners = roi_corners`, ~line 209), add:
  ```python
        self._write_progress("court_setup")
        progress_interval = max(1, int(fps))
  ```
- In the frame loop, after `frame_count += 1` (~line 235), add:
  ```python
            if frame_count % progress_interval == 0:
                self._write_progress("analyzing", current_frame=frame_count)
  ```
- Before `if self.analyze_technique: self._run_technique_analysis()` (~line 259), add:
  ```python
        self._write_progress("visualizing")
  ```
- In `_cleanup`, right before the audio-merge branch (`if hasattr(self, 'keep_audio')...`), add:
  ```python
        self._write_progress("encoding")
  ```
  and at the very end of `_cleanup`, add:
  ```python
        self._write_progress("done")
  ```

- [ ] **Step 5: Run to verify it passes**

Run: `PYTHONUTF8=1 .venv/Scripts/python.exe -m pytest tests/test_progress_heartbeat.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add badminton_analysis/system.py tests/test_progress_heartbeat.py
git commit -m "feat(match): frame-based progress.json heartbeat with stages"
```

---

### Task 3: A2 — web job runner reads progress + redirects output + surfaces errors

**Files:**
- Modify: `app.py` — the match analyze route's `subprocess.Popen` (~line 529) and its `track_progress` loop (~549-624).
- Test: `tests/test_app_progress.py`

**Interfaces:**
- Consumes: `<save_dir>/progress.json` from Task 2, `<save_dir>/analyze.log`.
- Produces: `job['progress']`, `job['stage']`, `job['updated']` populated from `progress.json`; on failure `job['message']` includes the tail of `analyze.log`. New helper `_read_progress_file(save_dir)` and `_log_tail(path, n=40)`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_app_progress.py
import json
import os
import app as webapp


def test_read_progress_file(tmp_path):
    p = tmp_path / "progress.json"
    p.write_text(json.dumps({"stage": "analyzing", "pct": 42, "updated": 123.0}))
    d = webapp._read_progress_file(str(tmp_path))
    assert d["stage"] == "analyzing" and d["pct"] == 42


def test_read_progress_file_missing(tmp_path):
    assert webapp._read_progress_file(str(tmp_path)) is None


def test_log_tail(tmp_path):
    p = tmp_path / "analyze.log"
    p.write_text("\n".join(f"line{i}" for i in range(100)))
    tail = webapp._log_tail(str(p), n=5)
    assert "line99" in tail and "line0" not in tail
```

- [ ] **Step 2: Run to verify it fails**

Run: `PYTHONUTF8=1 .venv/Scripts/python.exe -m pytest tests/test_app_progress.py -v`
Expected: FAIL (`_read_progress_file` / `_log_tail` undefined).

- [ ] **Step 3: Add the helpers**

Near the other helpers in `app.py` (after `_inpaintnet_weights`, ~line 184):

```python
def _read_progress_file(save_dir):
    """Read <save_dir>/progress.json written by the analysis subprocess, or None."""
    p = os.path.join(str(save_dir), 'progress.json')
    if not os.path.isfile(p):
        return None
    try:
        with open(p, encoding='utf-8') as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def _log_tail(path, n=40):
    """Return the last n lines of a log file, or '' if unreadable."""
    try:
        with open(path, encoding='utf-8', errors='replace') as fh:
            return ''.join(fh.readlines()[-n:])
    except OSError:
        return ''
```

- [ ] **Step 4: Redirect subprocess output to a log file (no undrained pipe)**

In the match analyze route, replace the `subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, ...)` (~line 529) with a log-file redirect:

```python
    log_path = os.path.join(str(save_dir), 'analyze.log')
    log_fh = open(log_path, 'w', encoding='utf-8')
    proc = subprocess.Popen(cmd, stdout=log_fh, stderr=subprocess.STDOUT,
                            text=True, cwd=str(PROJECT_ROOT), env=env)
    jobs[job_id]['proc'] = proc
    jobs[job_id]['status'] = 'running'
    jobs[job_id]['log_path'] = log_path
```

- [ ] **Step 5: Drive progress from progress.json in `track_progress`**

In `track_progress`, replace the detections-based estimate loop body (the `while job['status'] == 'running' and proc.poll() is None:` block, ~551-564) with:

```python
        while job['status'] == 'running' and proc.poll() is None:
            prog = _read_progress_file(job['save_dir'])
            if prog is not None:
                job['progress'] = int(prog.get('pct', job.get('progress', 0)))
                job['stage'] = prog.get('stage', job.get('stage'))
                job['updated'] = prog.get('updated')
                job['message'] = f"分析中... {prog.get('current_frame', 0)}/{prog.get('total_frames', 0)} 帧"
            time.sleep(1.0)
```

And in the `else:` failure branch (`分析失败 (exit=...)`, ~line 624), append the log tail so the error is visible:

```python
        else:
            job['status'] = 'error'
            job['stage'] = 'error'
            tail = _log_tail(job.get('log_path', ''), n=40)
            job['message'] = f'分析失败 (exit={proc.returncode})\n{tail}'
```

Also close `log_fh` after `proc.wait()` (add `try: log_fh.close()` / `except Exception: pass` right after the `proc.wait()` call).

- [ ] **Step 6: Expose `updated` in `/api/status`**

In `api_status` (~line 636), add `'updated': job.get('updated')` to the returned dict.

- [ ] **Step 7: Run tests + full suite**

Run: `PYTHONUTF8=1 .venv/Scripts/python.exe -m pytest tests/test_app_progress.py -q`
Expected: PASS.
Run: `PYTHONUTF8=1 .venv/Scripts/python.exe -m pytest -q`
Expected: all green.

- [ ] **Step 8: Commit**

```bash
git add app.py tests/test_app_progress.py
git commit -m "feat(match): drive job progress from progress.json + redirect subprocess log + surface errors"
```

---

### Task 4: A2 — wizard progress UI (stages + heartbeat)

**Files:**
- Modify: `static/kestrel.js` — `progressStages` (~429-431), the stage labels map (~425), and `renderStepProgress`/poll (~433-460).
- Test: `node --check` (JS syntax) + manual behavior note.

**Interfaces:**
- Consumes: `/api/status` fields `status, progress, message, stage, updated`.
- Produces: match progress screen showing stage checklist + pct + a "still working (Xs)" heartbeat.

- [ ] **Step 1: Expand match stages + labels**

In `progressStages()`, change the match branch to the real stages:

```javascript
  function progressStages() {
    return wiz.mode === 'posture' ? ['loading','analyzing','scoring','report','encoding','done']
                                  : ['court_setup','analyzing','visualizing','encoding','done'];
  }
```

In the stage-labels map (~line 425), add the new match stages (bilingual):

```javascript
    court_setup: ['球场设置', 'Court setup'], analyzing: ['分析动作', 'Analyzing'],
    visualizing: ['生成可视化', 'Visualizing'], encoding: ['转码', 'Encoding'],
    scoring: ['评分', 'Scoring'], report: ['报告', 'Report'],
    loading: ['加载模型', 'Loading model'], done: ['完成', 'Done'],
```

- [ ] **Step 2: Add a heartbeat/elapsed indicator to the poll**

In the status poll (`renderStepProgress`, where it reads the status JSON ~455-460), after updating the stage list and progress bar, add a "still working" line driven by `updated`:

```javascript
        var hb = document.getElementById('wiz-heartbeat');
        if (hb) {
          var now = Date.now() / 1000;
          var age = d.updated ? Math.max(0, Math.round(now - d.updated)) : null;
          hb.textContent = (d.status === 'running')
            ? (state.lang === 'zh'
                ? '分析进行中… ' + (d.message || '') + (age !== null ? '（' + age + '秒前更新）' : '')
                : 'Analysis in progress… ' + (d.message || '') + (age !== null ? ' (updated ' + age + 's ago)' : ''))
            : '';
        }
```

And add the `<div id="wiz-heartbeat" class="wiz-heartbeat"></div>` element to the progress screen markup in `renderStepProgress` (next to the `stage-list`).

- [ ] **Step 3: Verify JS parses**

Run: `node --check static/kestrel.js`
Expected: exit 0, no output.

- [ ] **Step 4: Commit**

```bash
git add static/kestrel.js
git commit -m "feat(match): wizard progress shows real stages + still-working heartbeat"
```

---

### Task 5: A3a — profile the pipeline (measure before optimizing)

**Files:**
- Create: `scripts/profile_pipeline.py`
- Modify: `docs/superpowers/specs/2026-07-14-match-analysis-ux-design.md` (append a "Profiling results" section).

**Interfaces:**
- Produces: recorded per-stage timings (pose / shuttle / court-view check / drawing) that Task 6 targets.

- [ ] **Step 1: Write the profiling harness**

Create `scripts/profile_pipeline.py` that runs the real pipeline on a short court-heavy segment with `show_performance_stats=True`, captures the per-frame `Frame N: pose Xs, shuttlecock Ys, ... court draw Zs` lines the pipeline already prints (see `system.py` `should_log_performance` block), and also times the `is_court_view` call separately by wrapping it. Aggregate mean per-stage ms over the segment and print a table.

```python
"""Profile per-stage cost of the match pipeline on a short segment (controller-run).
Usage: PYTHONUTF8=1 .venv/Scripts/python.exe scripts/profile_pipeline.py <video> [n_frames]
Prints mean ms/frame for: court_view_check, pose, shuttle, draw.
"""
import sys, os, time, statistics
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import cv2
import badminton_analysis.system as bsys

def main():
    video = sys.argv[1]
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 300
    bsys.load_runtime_dependencies()
    # Time is_court_view across n frames vs. a full processed frame using the
    # pipeline's own --performance-stats prints. This harness wraps the court
    # check to measure it in isolation.
    cap = cv2.VideoCapture(video)
    court_ms = []
    # (Implementer: instantiate BadmintonAnalysisSystem with show_performance_stats=True,
    #  process n frames, parse the printed per-stage timings into lists, and also
    #  measure is_court_view by timing the template-match on each grabbed frame.)
    print("court_view_check mean ms:", round(statistics.mean(court_ms), 2) if court_ms else "n/a")

if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run the profiler on a representative segment**

Run: `PYTHONUTF8=1 .venv/Scripts/python.exe scripts/profile_pipeline.py <a court-view clip> 300`
Record the per-stage mean ms/frame.

- [ ] **Step 3: Record findings in the spec**

Append a "## Profiling results (A3a)" section to the design spec with the measured table and the identified bottleneck. This determines whether Task 6's court-view-cadence win is worthwhile and whether GPU batching should be a follow-up.

- [ ] **Step 4: Commit**

```bash
git add scripts/profile_pipeline.py docs/superpowers/specs/2026-07-14-match-analysis-ux-design.md
git commit -m "chore(match): pipeline profiling harness + recorded per-stage timings"
```

---

### Task 6: A3b — lossless win: court-view check cadence

**Files:**
- Modify: `badminton_analysis/system.py` — `_process_frame` court-view check (~line 316).
- Test: `tests/test_analysis_quality.py::test_court_view_cadence_preserves_rallies`

**Interfaces:**
- Consumes: profiling from Task 5 (confirms the check is a meaningful cost; if Task 5 shows it is negligible, the controller may skip this task — record that decision).
- Produces: a `COURT_VIEW_CHECK_INTERVAL` constant; the court-view state is recomputed every N frames and held between checks.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_analysis_quality.py
import numpy as np
import badminton_analysis.system as sysmod


def test_court_view_cadence_holds_state_between_checks(monkeypatch):
    s = sysmod.BadmintonAnalysisSystem.__new__(sysmod.BadmintonAnalysisSystem)
    calls = {"n": 0}
    def fake_check(frame, tmpl, threshold=0.75):
        calls["n"] += 1
        return True
    s.is_court_view = fake_check
    s._court_view_cached = None
    s._court_view_last_frame = -10
    # First call computes; next (interval-1) calls reuse the cache.
    for f in range(1, sysmod.COURT_VIEW_CHECK_INTERVAL + 1):
        s._court_view_for_frame(np.zeros((4, 4), dtype=np.uint8), None, f)
    assert calls["n"] == 1  # only the first frame in the interval hit the model
```

- [ ] **Step 2: Run to verify it fails**

Run: `PYTHONUTF8=1 .venv/Scripts/python.exe -m pytest tests/test_analysis_quality.py::test_court_view_cadence_holds_state_between_checks -v`
Expected: FAIL (`COURT_VIEW_CHECK_INTERVAL` / `_court_view_for_frame` undefined).

- [ ] **Step 3: Implement the cadence helper**

Add the constant near the top of `system.py`:

```python
# Court-view state changes slowly (rally boundaries span >=5 frames); recompute the
# template match only every N frames and hold the result between checks. Lossless
# within the existing 5-frame rally thresholds.
COURT_VIEW_CHECK_INTERVAL = 3
```

Add the helper and use it in `_process_frame` in place of the direct `is_court = self.is_court_view(gray_frame, template_gray)` call (~line 316):

```python
    def _court_view_for_frame(self, gray_frame, template_gray, frame_count):
        if (self._court_view_cached is None
                or frame_count - self._court_view_last_frame >= COURT_VIEW_CHECK_INTERVAL):
            self._court_view_cached = self.is_court_view(gray_frame, template_gray)
            self._court_view_last_frame = frame_count
        return self._court_view_cached
```

Initialize the cache in `__init__` (near the other rally state, ~line 166):

```python
        self._court_view_cached = None
        self._court_view_last_frame = -10
```

In `_process_frame`, replace `is_court = self.is_court_view(gray_frame, template_gray)` with:

```python
        is_court = self._court_view_for_frame(gray_frame, template_gray, frame_count)
```

- [ ] **Step 4: Run test + verify rally detection unchanged on a sample**

Run: `PYTHONUTF8=1 .venv/Scripts/python.exe -m pytest tests/test_analysis_quality.py::test_court_view_cadence_holds_state_between_checks -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add badminton_analysis/system.py tests/test_analysis_quality.py
git commit -m "perf(match): recompute court-view check on a cadence (lossless within rally thresholds)"
```

---

### Task 7: A3c — Fast/Accurate quality toggle (end-to-end)

**Files:**
- Modify: `main.py` (flag + ctor wiring), `badminton_analysis/system.py` (ctor arg + stride + skip dense analytics in Fast), `app.py` (pass from wizard), `static/kestrel.js` (toggle in match config).
- Test: `tests/test_analysis_quality.py` (stride + Accurate default).

**Interfaces:**
- Consumes: nothing new.
- Produces: `BadmintonAnalysisSystem(..., analysis_quality="accurate")`; `main.py --analysis-quality {accurate,fast}`; `FAST_FRAME_STRIDE` constant; in Fast, heavy per-frame analysis runs every `FAST_FRAME_STRIDE`-th frame and `analyze_technique`/stroke recognition are disabled.

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_analysis_quality.py
import badminton_analysis.system as sysmod


def test_accurate_is_default_and_analyzes_every_frame():
    s = sysmod.BadmintonAnalysisSystem.__new__(sysmod.BadmintonAnalysisSystem)
    s.analysis_quality = "accurate"
    assert s._analyze_this_frame(1) and s._analyze_this_frame(2) and s._analyze_this_frame(3)


def test_fast_mode_strides_heavy_analysis():
    s = sysmod.BadmintonAnalysisSystem.__new__(sysmod.BadmintonAnalysisSystem)
    s.analysis_quality = "fast"
    hits = [f for f in range(1, 3 * sysmod.FAST_FRAME_STRIDE + 1) if s._analyze_this_frame(f)]
    # Only every FAST_FRAME_STRIDE-th frame is analyzed in Fast mode.
    assert hits == list(range(sysmod.FAST_FRAME_STRIDE, 3 * sysmod.FAST_FRAME_STRIDE + 1, sysmod.FAST_FRAME_STRIDE))
```

- [ ] **Step 2: Run to verify it fails**

Run: `PYTHONUTF8=1 .venv/Scripts/python.exe -m pytest tests/test_analysis_quality.py -k "quality or stride or default" -v`
Expected: FAIL (`FAST_FRAME_STRIDE` / `_analyze_this_frame` undefined).

- [ ] **Step 3: Implement the stride gate in system.py**

Add the constant near `COURT_VIEW_CHECK_INTERVAL`:

```python
FAST_FRAME_STRIDE = 3  # Fast mode analyzes every Nth court frame (quick look).
```

Add `analysis_quality="accurate"` to `__init__`'s signature and store `self.analysis_quality = analysis_quality`. Add the gate:

```python
    def _analyze_this_frame(self, frame_count):
        if self.analysis_quality != "fast":
            return True
        return frame_count % FAST_FRAME_STRIDE == 0
```

In `_process_frame`, wrap the heavy per-frame analysis (pose detection + ball detect + player update + draws — the block from `pose_t0 = time.time()` through the draw calls) so that in Fast mode a non-analyzed court frame is written raw and returns early, mirroring A1:

```python
        if not self._analyze_this_frame(frame_count):
            if self.show_display:
                cv2.imshow('frame', frame)
                cv2.waitKey(1)
            out.write(frame)
            return frame, detect_frame_count
```

Place this immediately after the `detect_frame_count += 1` / ROI setup but before `pose_t0 = time.time()`. In Fast mode, also disable the dense analytics: guard `_capture_analysis_frame` and the pre-pass/stroke recognition. In `__init__`, after computing `analyze_technique`, add:

```python
        if analysis_quality == "fast":
            self.analyze_technique = False  # dense analytics need every frame
```

(Placed after `self.analyze_technique = analyze_technique`.) This also makes `_run_shuttle_pretrack` and `_run_stroke_recognition` no-op in Fast (they already guard on `analyze_technique`).

- [ ] **Step 4: Wire main.py**

After the `--analysis-technique`-related args, add:

```python
    parser.add_argument('--analysis-quality', default='accurate', choices=['accurate', 'fast'],
                        help='accurate=每帧分析（默认）；fast=抽帧快速预览（密集分析关闭）')
```

In the `BadmintonAnalysisSystem(...)` construction, add `analysis_quality=args.analysis_quality,`.

- [ ] **Step 5: Wire app.py + kestrel.js**

- `app.py` match route: read `quality = data.get('analysis_quality', 'accurate')` and append `cmd += ['--analysis-quality', quality]`.
- `static/kestrel.js` match config (`renderConfig`, the match branch ~378-380): add a select row
  ```javascript
        fieldRow('分析质量','Analysis quality', selectHTML('cfg-quality', [['accurate','精确（每帧）','Accurate (every frame)'],['fast','快速（抽帧预览）','Fast (sampled preview)']])) +
  ```
  and in the match payload (~415-416) add `analysis_quality: document.getElementById('cfg-quality').value`.

- [ ] **Step 6: Run tests + JS check + full suite**

Run: `PYTHONUTF8=1 .venv/Scripts/python.exe -m pytest tests/test_analysis_quality.py -v`
Expected: PASS.
Run: `node --check static/kestrel.js`
Expected: exit 0.
Run: `PYTHONUTF8=1 .venv/Scripts/python.exe -m pytest -q`
Expected: all green.

- [ ] **Step 7: Commit**

```bash
git add main.py badminton_analysis/system.py app.py static/kestrel.js tests/test_analysis_quality.py
git commit -m "feat(match): Fast/Accurate analysis-quality toggle (Accurate default, every frame)"
```

---

### Task 8: A4 — unify localized visualization module

**Files:**
- Create: `badminton_analysis/visualization/player_positions.py`
- Delete: `badminton_analysis/visualization/player_positions_zh.py`, `badminton_analysis/visualization/player_positions_en.py`
- Modify: `main.py` (import) — `main_posture.py`/`app.py` too if they import these modules (grep first).
- Test: `tests/test_player_positions_i18n.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `analyze_player_positions(detections_path, output_dir=None, fps=30, language='zh')`; class `PlayerPositionVisualizer(..., language='zh')`. Output filenames unchanged (`match_heatmap.png`, `match_scatter.png`, per-rally variants).

- [ ] **Step 1: Build the unified module**

`player_positions_zh.py` and `player_positions_en.py` are byte-identical except: (a) the `_load_chinese_font()` block + `chinese_font` global (zh only); (b) translated comments; (c) a set of user-facing chart strings. Create `player_positions.py` from the **zh** module, then:

1. Add a language strings table near the top:
   ```python
   _LANG = {
       "zh": {
           "heatmap_title": "球员位置热力图",
           "court_width": "场地宽度 (米)",
           "court_length": "场地长度 (米)",
           "scatter_title": "球员位置散点图",
           # ...complete from the diff below...
       },
       "en": {
           "heatmap_title": "Player Position Heatmap",
           "court_width": "Court Width (m)",
           "court_length": "Court Length (m)",
           "scatter_title": "Player Position Scatter",
           # ...complete from the diff below...
       },
   }
   ```
   Complete `_LANG` by running `diff badminton_analysis/visualization/player_positions_zh.py badminton_analysis/visualization/player_positions_en.py` and adding a key for **every** user-facing string that differs (chart titles, axis labels, and the stats-panel text in `_add_stats_to_plot`: rally header, "upper/lower player", "avg speed", "max speed", "total distance", and the `米/秒` / `m/s` and `米` / `m` units). The diff is the authoritative source of the pairs.
2. Add `language='zh'` to `PlayerPositionVisualizer.__init__` and `analyze_player_positions(...)`; store `self.strings = _LANG.get(language, _LANG['zh'])` and `self.language = language`.
3. Replace each hardcoded zh chart string with `self.strings["<key>"]` (e.g. `plt.title(self.strings["heatmap_title"], ...)`).
4. Gate the font: keep `_load_chinese_font()` but call it only when `language == 'zh'`; for `en`, use matplotlib defaults and drop the `fontproperties=chinese_font` kwargs (or pass `None`). Make the font a per-instance attribute, not a module global, so language is honored per call.

- [ ] **Step 2: Wire callers**

`grep -rn "player_positions_zh\|player_positions_en" main.py app.py main_posture.py badminton_analysis/` and replace each import with `from badminton_analysis.visualization.player_positions import analyze_player_positions`, passing `language=args.language` (or the caller's language). In `main.py` remove the `if args.language == 'en'` import branch (~41-44) and use the unified import, calling `analyze_player_positions(..., language=args.language)`.

- [ ] **Step 3: Write the test**

```python
# tests/test_player_positions_i18n.py
from badminton_analysis.visualization.player_positions import PlayerPositionVisualizer, _LANG


def test_language_selects_string_table():
    en = PlayerPositionVisualizer.__new__(PlayerPositionVisualizer)
    en.strings = _LANG["en"]
    assert en.strings["heatmap_title"] == "Player Position Heatmap"
    zh = PlayerPositionVisualizer.__new__(PlayerPositionVisualizer)
    zh.strings = _LANG["zh"]
    assert zh.strings["heatmap_title"] == "球员位置热力图"


def test_lang_tables_have_same_keys():
    assert set(_LANG["zh"]) == set(_LANG["en"])
```

- [ ] **Step 4: Run tests + confirm no stale references**

Run: `PYTHONUTF8=1 .venv/Scripts/python.exe -m pytest tests/test_player_positions_i18n.py -v`
Expected: PASS.
Run: `grep -rn "player_positions_zh\|player_positions_en" . --include=*.py` — expected: no matches (both old modules deleted and callers updated).
Run: `PYTHONUTF8=1 .venv/Scripts/python.exe -m pytest -q`
Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add badminton_analysis/visualization/player_positions.py main.py tests/test_player_positions_i18n.py
git rm badminton_analysis/visualization/player_positions_zh.py badminton_analysis/visualization/player_positions_en.py
git commit -m "feat(match): unify player-position visualizations into one localized module"
```

---

## Validation (controller-run, after all tasks)

On a full-length match video via the web flow: confirm (1) the output video duration equals the input; (2) the wizard progress advances through `court_setup → analyzing → visualizing → encoding → done` with a live heartbeat and never sits at 0%; (3) Accurate run completes and matches prior analytics; a Fast run is materially quicker; (4) heatmap/scatter render in the selected language.

## Self-Review

**1. Spec coverage:** A1→Task 1; A2→Tasks 2 (heartbeat), 3 (app), 4 (UI); A3a measure→Task 5; A3b lossless→Task 6; A3c Fast/Accurate→Task 7; A4→Task 8. All spec sections covered.

**2. Placeholder scan:** Task 5's profiling harness has an implementer-completed measurement body (inherent to measure-first) but a concrete script skeleton, run command, and recorded-findings deliverable — not a TODO. Task 8's `_LANG` table is completed from a named, authoritative diff of two existing files. No "TBD"/"handle edge cases".

**3. Type consistency:** `_write_progress(stage, current_frame=None)` written in Task 2, consumed via `progress.json` shape in Task 3; `_read_progress_file`/`_log_tail` names consistent across Task 3 and its test; `analysis_quality`/`_analyze_this_frame`/`FAST_FRAME_STRIDE`/`COURT_VIEW_CHECK_INTERVAL` consistent across Tasks 6-7; `analyze_player_positions(..., language=)` + `_LANG` consistent across Task 8 and callers.
