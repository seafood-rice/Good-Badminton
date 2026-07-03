# Kestrel Phase 2 — New Analysis Wizard & Staged Progress — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a guided "New Analysis" wizard to the Kestrel UI (Mode → Upload → Court setup → Config → Progress) and give both analysis pipelines an interactive 0–100% progress readout with named stages, so the user is never left in the dark.

**Architecture:** Frontend work extends `static/kestrel.js` + `static/kestrel.css` with a screen router (dashboard ↔ wizard) and a stepper-driven wizard, wired to the existing `/api/upload`, `/api/detect`, `/api/manual-annotate`, `/api/analyze`, `/api/posture/analyze` and status endpoints. Backend work is additive: the posture pipeline emits `PROGRESS <pct> <stage>` lines that `app.py` parses into live job progress, the match job gains stage labels, and both status endpoints expose a `stage` field the wizard localizes.

**Tech Stack:** Python 3 / Flask + OpenCV (backend), vanilla JS + CSS custom properties (frontend), pytest (backend tests).

## Global Constraints

- Bilingual **zh/en**, default `zh`; every user-facing string localized in the frontend (backend emits stage KEYS, the UI localizes them). (Spec.)
- **Offline-safe:** no CDN/external references. (Spec.)
- **Additive backend only:** the match/posture pipeline logic and all existing routes keep working; only add progress emission, a parser, a `stage` field, and stage labels. The old UI at `/` must remain functional. (Spec.)
- Kestrel is built in **parallel** at `/kestrel`; the final `/` swap is deferred to a later phase. Only `static/kestrel.*`, `app.py`, and `badminton_analysis/posture/system.py` are touched.
- Theme tokens: light `:root` + `[data-theme="dark"]`; accent `#FF5A36` and score hues constant across themes; no per-component dark CSS. (Spec.)
- **All existing tests stay green** (baseline **147**). This repo has **no JS test framework** — front-end tasks are verified with `node --check static/kestrel.js` + running the app + curl, NOT automated tests. Backend tasks are TDD with pytest via `./.venv/Scripts/python.exe -m pytest` (Windows; set `PYTHONUTF8=1` when launching `app.py` directly).
- **Shared stage vocabulary** (backend emits these keys; frontend localizes):
  - Match: `analyzing` → `encoding` → `done` (and `error`).
  - Posture: `loading` → `analyzing` → `scoring` → `report` → `encoding` → `done` (and `error`).

---

### Task 1: Posture pipeline emits staged progress

**Files:**
- Modify: `badminton_analysis/posture/system.py` (add two module helpers; instrument `process_video` at the frame loop `:156-166` and around `runner.run` `:177` and `_write_reports` `:189`)
- Test: `tests/test_posture_progress.py` (create)

**Interfaces:**
- Produces: two module-level functions in `badminton_analysis/posture/system.py`:
  - `format_progress(pct, stage) -> str` returns `"PROGRESS <int(pct)> <stage>"`.
  - `analyzing_pct(frame_count, total_frames) -> int` maps frame progress into the 5–85 band: `5 + int(min(1.0, frame_count/max(total_frames,1)) * 80)`.
  `process_video` prints `format_progress(...)` lines to stdout (flushed) at: `loading` (5) after the pose processor is built, `analyzing` (per-frame via `analyzing_pct`, throttled to every ~15 frames) inside the loop, `scoring` (88) before `runner.run`, `report` (94) before `_write_reports`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_posture_progress.py
from badminton_analysis.posture.system import format_progress, analyzing_pct


def test_format_progress_shape():
    assert format_progress(42, "analyzing") == "PROGRESS 42 analyzing"
    assert format_progress(5.0, "loading") == "PROGRESS 5 loading"


def test_analyzing_pct_bands():
    assert analyzing_pct(0, 100) == 5        # start of analyzing band
    assert analyzing_pct(100, 100) == 85     # end of analyzing band
    assert analyzing_pct(50, 100) == 45      # midpoint
    assert analyzing_pct(10, 0) == 85        # guard: total 0 -> clamped to full band
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_posture_progress.py -v`
Expected: FAIL — `ImportError: cannot import name 'format_progress'`.

- [ ] **Step 3: Add the helpers**

At module level in `badminton_analysis/posture/system.py` (near the top, after imports):

```python
def format_progress(pct, stage):
    """Progress line consumed by the web layer: 'PROGRESS <pct> <stage>'."""
    return "PROGRESS " + str(int(pct)) + " " + stage


def analyzing_pct(frame_count, total_frames):
    """Map frame progress into the 5-85 'analyzing' band."""
    frac = min(1.0, frame_count / max(total_frames, 1))
    return 5 + int(frac * 80)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_posture_progress.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Instrument `process_video`**

In `badminton_analysis/posture/system.py`, `process_video`:

After `height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))` (line ~142), capture the total:
```python
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 0
```
After `pose = self._build_pose_processor()` (line ~144), emit loading:
```python
        print(format_progress(5, "loading"), flush=True)
```
Inside the `while cap.isOpened():` loop, after `frame_count += 1` (line ~160):
```python
            if frame_count % 15 == 0:
                print(format_progress(analyzing_pct(frame_count, total_frames), "analyzing"), flush=True)
```
Immediately before `runner = PostureRunner(...)` (line ~175):
```python
        print(format_progress(88, "scoring"), flush=True)
```
Immediately before `self._write_reports(reports, summary, date=_today())` (line ~189):
```python
        print(format_progress(94, "report"), flush=True)
```

- [ ] **Step 6: Run the full suite**

Run: `./.venv/Scripts/python.exe -m pytest -q`
Expected: `149 passed` (147 baseline + 2 new). The emission lines are plain prints; no existing test asserts on posture stdout.

- [ ] **Step 7: Commit**

```bash
git add badminton_analysis/posture/system.py tests/test_posture_progress.py
git commit -m "feat(posture): emit staged PROGRESS lines during process_video"
```

---

### Task 2: Parse progress in app.py; expose `stage` on both status routes

**Files:**
- Modify: `app.py` — add `_parse_progress_line` helper; rewrite posture `track()` (`:728-752`) to consume stdout; add stage labels to match `track_progress` (`:364-...`); add `stage` to `api_status` (`:454`) and `api_posture_analyze_status` (`:759`) responses.
- Test: `tests/test_progress_parse.py` (create)

**Interfaces:**
- Consumes: posture stdout `PROGRESS <pct> <stage>` lines (Task 1).
- Produces: `_parse_progress_line(line) -> tuple[int,str] | None` in `app.py`. Both status endpoints return an added `stage` key (string or `None`). Job dicts carry a `stage` field. Posture stages advance `loading→analyzing→scoring→report→encoding→done`; match stages advance `analyzing→encoding→done`; both set `error` on failure.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_progress_parse.py
import app as webapp


def test_parse_progress_line_valid():
    assert webapp._parse_progress_line("PROGRESS 42 analyzing") == (42, "analyzing")
    assert webapp._parse_progress_line("  PROGRESS 5 loading\n") == (5, "loading")


def test_parse_progress_line_rejects_noise():
    assert webapp._parse_progress_line("Posture analysis: 8 reps") is None
    assert webapp._parse_progress_line("PROGRESS x analyzing") is None
    assert webapp._parse_progress_line("") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_progress_parse.py -v`
Expected: FAIL — `AttributeError: module 'app' has no attribute '_parse_progress_line'`.

- [ ] **Step 3: Add the parser**

Near the other helpers in `app.py` (e.g. after `_find_venv_python`):

```python
def _parse_progress_line(line):
    """Parse 'PROGRESS <pct> <stage>' emitted by a worker. None if not a progress line."""
    parts = line.strip().split()
    if len(parts) == 3 and parts[0] == "PROGRESS":
        try:
            return int(parts[1]), parts[2]
        except ValueError:
            return None
    return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_progress_parse.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Consume progress in the posture `track()`**

Replace the body of `track()` in `api_posture_analyze` (`:728-752`) so it reads stdout as it streams:

```python
    def track():
        job = jobs[job_id]
        job['stage'] = 'loading'
        for line in proc.stdout:
            parsed = _parse_progress_line(line)
            if parsed:
                job['progress'], job['stage'] = parsed[0], parsed[1]
        proc.wait()
        if proc.returncode == 0:
            job['stage'] = 'encoding'
            job['progress'] = max(job.get('progress', 0), 96)
            job['message'] = '完成!'
            vs = video_path.stem
            sd = str(save_dir)
            raw = os.path.join(sd, 'detect_' + vs + '.mp4')
            h264 = os.path.join(sd, 'detect_' + vs + '_h264.mp4')
            ffmpeg_bin = shutil.which('ffmpeg') or 'ffmpeg'
            try:
                subprocess.run([ffmpeg_bin, '-y', '-i', raw, '-c:v', 'libx264',
                                '-preset', 'fast', '-crf', '23', '-movflags', '+faststart', h264],
                               check=True, capture_output=True, timeout=600)
                if os.path.exists(h264) and os.path.getsize(h264) > 0:
                    os.replace(h264, raw)
            except Exception as e:
                print('posture ffmpeg re-encode failed: ' + str(e))
            job['status'] = 'completed'
            job['progress'] = 100
            job['stage'] = 'done'
            job['result'] = {'video_name': vs,
                             'output_video': '/api/output/' + vs + '/posture/detect_' + vs + '.mp4'}
        else:
            job['status'] = 'error'
            job['stage'] = 'error'
            job['message'] = '姿态分析失败 (exit=' + str(proc.returncode) + ')'
```

(`for line in proc.stdout` blocks until the process finishes, updating progress as lines arrive, so no separate polling thread is needed.)

- [ ] **Step 6: Add stage labels to the match job**

In `api_analyze`'s `track_progress` (`:364`):
- In the initial `jobs[job_id] = {...}` dict where the match job is created (in `api_analyze`, the `'status': 'pending'` dict), add `'stage': 'analyzing',`.
- Inside the `while job['status'] == 'running'` loop, the code already sets `job['message']`; add `job['stage'] = 'analyzing'` in that same block.
- Where it sets `job['status'] = 'reencoding'` (start of the ffmpeg step), add `job['stage'] = 'encoding'`.
- Where it sets `job['status'] = 'completed'`, add `job['stage'] = 'done'`.
- In the `else:` error branch (`job['status'] = 'error'`), add `job['stage'] = 'error'`.

- [ ] **Step 7: Expose `stage` on both status endpoints**

In `api_status` (`:454`) and `api_posture_analyze_status` (`:759`), add `'stage': job.get('stage')` to the returned JSON dict (alongside `status`, `progress`, `message`, `result`).

- [ ] **Step 8: Verify + full suite**

Run: `./.venv/Scripts/python.exe -m pytest -q`
Expected: `151 passed` (149 + 2 new). Then sanity-start the app and confirm no import error:
`PYTHONUTF8=1 ./.venv/Scripts/python.exe app.py --port 5094 &` then `curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:5094/kestrel` → 200; kill the server.

- [ ] **Step 9: Commit**

```bash
git add app.py tests/test_progress_parse.py
git commit -m "feat(api): parse posture PROGRESS lines + expose stage on status routes"
```

---

### Task 3: Screen router + wizard scaffold + Mode step

**Files:**
- Modify: `static/kestrel.js` (screen state, router, wizard state, stepper, mode step, sidebar/dashboard nav wiring), `static/kestrel.css` (wizard + stepper + choice-card styles)

**Interfaces:**
- Consumes: `state`, `t`, `render`, `renderSidebar`, `renderDashboard` (Phase 1).
- Produces: `state.screen` (`'dashboard'` | `'new'`, default `'dashboard'`); `Kestrel.setScreen(name)` re-renders; `wiz` state object `{ mode, video, step, jobId }`; `renderWizard()` paints the wizard into `#main`; `WIZ_STEPS` config drives the stepper. `render()` dispatches by `state.screen`. Exports add `setScreen`.

- [ ] **Step 1: Add screen state + router to `kestrel.js`**

Replace `function render() { renderSidebar(); loadDashboard(); }` with:

```js
  function setScreen(name) { state.screen = name; render(); }
  function render() {
    renderSidebar();
    if (state.screen === 'new') { renderWizard(); }
    else { loadDashboard(); }
  }
```

Add `screen: 'dashboard'` to the `state` object initializer, and `var wiz = { mode: 'match', video: null, step: 'mode', jobId: null };` near `lib`.

- [ ] **Step 2: Wire nav to switch screens**

In `renderSidebar`, after the `[data-theme]` wiring, add:
```js
    s.querySelectorAll('[data-screen]').forEach(function (b) {
      b.onclick = function () { setScreen(b.getAttribute('data-screen')); };
      b.classList.toggle('active', b.getAttribute('data-screen') === state.screen);
    });
```
In `renderDashboard`, change the `+ New Analysis` affordance: replace the `<div class="page-head"><h1>…</h1></div>` line with a header that includes the button:
```js
      '<div class="page-head"><h1>' + (state.lang==='zh'?'视频库':'Video Library') + '</h1>' +
        '<button class="btn-primary" id="go-new">+ ' + (state.lang==='zh'?'新建分析':'New Analysis') + '</button></div>' +
```
and after `renderFilterBar(); renderCards();` in `renderDashboard`, add:
```js
    var gn = document.getElementById('go-new'); if (gn) gn.onclick = function () { setScreen('new'); };
```

- [ ] **Step 3: Add wizard stepper + Mode step**

Add these functions to `kestrel.js` (before `render`):

```js
  var WIZ_STEPS_MATCH = [['mode','模式','Mode'],['upload','上传','Upload'],['court','球场','Court'],['config','设置','Config'],['progress','分析','Analyze']];
  var WIZ_STEPS_POSTURE = [['mode','模式','Mode'],['upload','上传','Upload'],['config','设置','Config'],['progress','分析','Analyze']];
  function wizSteps() { return wiz.mode === 'posture' ? WIZ_STEPS_POSTURE : WIZ_STEPS_MATCH; }
  function stepperHTML() {
    var steps = wizSteps(); var curIdx = steps.findIndex(function (s) { return s[0] === wiz.step; });
    return '<div class="stepper">' + steps.map(function (s, i) {
      var cls = i < curIdx ? 'done' : (i === curIdx ? 'active' : 'pending');
      return '<div class="step ' + cls + '"><span class="step-dot mono">' + (i + 1) + '</span>' +
        '<span class="step-label">' + (state.lang === 'zh' ? s[1] : s[2]) + '</span></div>';
    }).join('<div class="step-line"></div>') + '</div>';
  }
  function goStep(step) { wiz.step = step; renderWizard(); }
  function renderWizard() {
    var main = document.getElementById('main');
    main.innerHTML =
      '<div class="page-head"><h1>' + (state.lang==='zh'?'新建分析':'New Analysis') + '</h1>' +
        '<button class="btn-ghost" id="wiz-cancel">' + (state.lang==='zh'?'取消':'Cancel') + '</button></div>' +
      stepperHTML() + '<div id="wiz-body"></div>';
    document.getElementById('wiz-cancel').onclick = function () { setScreen('dashboard'); };
    var body = document.getElementById('wiz-body');
    if (wiz.step === 'mode') { renderStepMode(body); }
    else if (wiz.step === 'upload') { renderStepUpload(body); }
    else if (wiz.step === 'court') { renderStepCourt(body); }
    else if (wiz.step === 'config') { renderStepConfig(body); }
    else if (wiz.step === 'progress') { renderStepProgress(body); }
  }
  function renderStepMode(body) {
    var zh = state.lang === 'zh';
    var cards = [
      ['match', zh?'比赛分析':'Match Analysis', zh?'全场追踪、回合检测、热力图与击球评分。需要四点球场设置。':'Full-court tracking, rally detection, heatmaps, and stroke scoring. Needs a 4-point court setup.'],
      ['posture', zh?'姿态训练':'Posture Drill', zh?'无需球场的单一动作重复练习，逐次反馈。':'Court-free single-stroke repetition practice with per-rep feedback.']
    ];
    body.innerHTML = '<div class="choice-grid">' + cards.map(function (c) {
      return '<button class="choice-card' + (wiz.mode === c[0] ? ' on' : '') + '" data-mode="' + c[0] + '">' +
        '<div class="choice-title">' + c[1] + '</div><div class="choice-desc">' + c[2] + '</div></button>';
    }).join('') + '</div>' +
      '<div class="wiz-actions"><button class="btn-primary" id="wiz-next">' + (zh?'继续':'Continue') + '</button></div>';
    body.querySelectorAll('[data-mode]').forEach(function (b) {
      b.onclick = function () { wiz.mode = b.getAttribute('data-mode'); renderWizard(); };
    });
    document.getElementById('wiz-next').onclick = function () { goStep('upload'); };
  }
```

Add stubs so `renderWizard` never throws before later tasks land (Task 4 replaces upload; Task 5 court; Task 6 config; Task 7 progress):
```js
  function renderStepUpload(body) { body.innerHTML = '<p class="muted">upload step (Task 4)</p>'; }
  function renderStepCourt(body) { body.innerHTML = '<p class="muted">court step (Task 5)</p>'; }
  function renderStepConfig(body) { body.innerHTML = '<p class="muted">config step (Task 6)</p>'; }
  function renderStepProgress(body) { body.innerHTML = '<p class="muted">progress step (Task 7)</p>'; }
```

Add `setScreen` to the exported object.

- [ ] **Step 4: Add wizard CSS to `kestrel.css`**

```css
.page-head{display:flex;align-items:center;justify-content:space-between;gap:16px;margin-bottom:24px;}
.btn-primary{background:var(--accent);color:#fff;border:none;border-radius:var(--radius-sm);
  padding:10px 18px;font:700 14px inherit;cursor:pointer;transition:background .15s,transform .1s;}
.btn-primary:hover{background:var(--accent-dark);} .btn-primary:active{transform:scale(.97);}
.btn-ghost{background:transparent;color:var(--muted);border:1px solid var(--border);
  border-radius:var(--radius-sm);padding:9px 16px;font:600 14px inherit;cursor:pointer;}
.btn-ghost:hover{color:var(--ink);border-color:var(--faint);}
.stepper{display:flex;align-items:center;margin-bottom:28px;max-width:720px;}
.step{display:flex;align-items:center;gap:8px;}
.step-dot{width:28px;height:28px;border-radius:50%;display:flex;align-items:center;justify-content:center;
  font-size:13px;background:var(--fill);color:var(--faint);border:1px solid var(--border);}
.step-label{font-size:13px;font-weight:600;color:var(--faint);white-space:nowrap;}
.step.active .step-dot{background:var(--accent);color:#fff;border-color:var(--accent);}
.step.active .step-label{color:var(--ink);}
.step.done .step-dot{background:var(--accent-soft);color:var(--accent);border-color:transparent;}
.step.done .step-label{color:var(--muted);}
.step-line{flex:1;height:2px;background:var(--border);margin:0 10px;min-width:16px;}
.choice-grid{display:grid;grid-template-columns:1fr 1fr;gap:14px;max-width:640px;margin-bottom:24px;}
.choice-card{text-align:left;background:var(--card);border:2px solid var(--border);border-radius:var(--radius-md);
  padding:18px;cursor:pointer;font:inherit;transition:border-color .15s,transform .1s;}
.choice-card:hover{border-color:var(--faint);} .choice-card:active{transform:scale(.99);}
.choice-card.on{border-color:var(--accent);background:var(--accent-soft);}
.choice-title{font-size:15px;font-weight:700;color:var(--ink);}
.choice-desc{font-size:12.5px;color:var(--muted);margin-top:6px;line-height:1.5;}
.wiz-actions{display:flex;gap:10px;margin-top:8px;}
```

- [ ] **Step 5: Verify + commit**

Run: `node --check static/kestrel.js` → exit 0.
Start `PYTHONUTF8=1 ./.venv/Scripts/python.exe app.py --port 5095 &`; `curl -s http://127.0.0.1:5095/kestrel` → 200; grep served `kestrel.js` for `setScreen`, `renderWizard`, `renderStepMode`. Kill server.
```bash
git add static/kestrel.js static/kestrel.css
git commit -m "feat(ui): Kestrel screen router + wizard scaffold + mode step"
```

---

### Task 4: Upload step (drag/drop + pick existing)

**Files:**
- Modify: `static/kestrel.js` (`renderStepUpload`), `static/kestrel.css` (dropzone + picker styles)

**Interfaces:**
- Consumes: `wiz`, `goStep`, `t`, `POST /api/upload` (returns `{ok, filename}`), `GET /api/videos`.
- Produces: sets `wiz.video` (a filename string) then advances — to `court` when `wiz.mode==='match'`, else `config`.

- [ ] **Step 1: Implement `renderStepUpload`** (replace the Task 3 stub)

```js
  function afterUpload() { goStep(wiz.mode === 'match' ? 'court' : 'config'); }
  function renderStepUpload(body) {
    var zh = state.lang === 'zh';
    body.innerHTML =
      '<div class="dropzone" id="dz"><div class="dz-plus">+</div>' +
        '<div class="dz-main">' + (zh?'拖拽视频到此，或点击选择':'Drag & drop a video, or click to browse') + '</div>' +
        '<div class="dz-sub mono">MP4 · MOV · AVI · ≤ 1GB</div></div>' +
      '<input type="file" id="dz-file" accept="video/*" hidden>' +
      '<div class="or-line">' + (zh?'或从已有视频选择':'or pick an existing video') + '</div>' +
      '<div id="pick-list" class="pick-list muted">' + (zh?'加载中…':'Loading…') + '</div>' +
      '<div class="wiz-actions"><button class="btn-ghost" id="wiz-back">' + (zh?'返回':'Back') + '</button>' +
        '<button class="btn-primary" id="wiz-next" disabled>' + (zh?'继续':'Continue') + '</button></div>';
    var nextBtn = document.getElementById('wiz-next');
    function selectVideo(fn) { wiz.video = fn; nextBtn.disabled = false;
      document.querySelectorAll('.pick-item').forEach(function (el) { el.classList.toggle('on', el.getAttribute('data-fn') === fn); }); }
    var dz = document.getElementById('dz'), fileInput = document.getElementById('dz-file');
    dz.onclick = function () { fileInput.click(); };
    dz.ondragover = function (e) { e.preventDefault(); dz.classList.add('drag'); };
    dz.ondragleave = function () { dz.classList.remove('drag'); };
    dz.ondrop = function (e) { e.preventDefault(); dz.classList.remove('drag'); if (e.dataTransfer.files[0]) doUpload(e.dataTransfer.files[0]); };
    fileInput.onchange = function () { if (fileInput.files[0]) doUpload(fileInput.files[0]); };
    function doUpload(file) {
      dz.querySelector('.dz-main').textContent = (zh?'上传中…':'Uploading…');
      var fd = new FormData(); fd.append('file', file);
      fetch('/api/upload', { method: 'POST', body: fd }).then(function (r) { return r.json(); })
        .then(function (d) { if (d.ok) { selectVideo(d.filename); dz.querySelector('.dz-main').textContent = '✓ ' + d.filename; }
          else { dz.querySelector('.dz-main').textContent = (zh?'上传失败':'Upload failed'); } })
        .catch(function () { dz.querySelector('.dz-main').textContent = (zh?'上传失败':'Upload failed'); });
    }
    fetch('/api/videos').then(function (r) { return r.json(); }).then(function (vids) {
      var list = document.getElementById('pick-list');
      if (!vids || !vids.length) { list.textContent = (zh?'暂无视频':'No videos yet'); return; }
      list.classList.remove('muted');
      list.innerHTML = vids.map(function (v) {
        return '<button class="pick-item" data-fn="' + v.filename + '">🎬 ' + v.name +
          ' <span class="mono pick-sz">' + v.size_mb + 'MB</span></button>'; }).join('');
      list.querySelectorAll('.pick-item').forEach(function (b) { b.onclick = function () { selectVideo(b.getAttribute('data-fn')); }; });
    }).catch(function () { document.getElementById('pick-list').textContent = (zh?'加载失败':'Failed to load'); });
    document.getElementById('wiz-back').onclick = function () { goStep('mode'); };
    nextBtn.onclick = function () { if (wiz.video) afterUpload(); };
  }
```

- [ ] **Step 2: Add dropzone/picker CSS**

```css
.dropzone{max-width:560px;border:2px dashed var(--border);border-radius:var(--radius-lg);
  padding:44px 24px;display:flex;flex-direction:column;align-items:center;gap:10px;
  background:var(--card);cursor:pointer;transition:border-color .2s;}
.dropzone:hover,.dropzone.drag{border-color:var(--accent);}
.dz-plus{width:48px;height:48px;border-radius:50%;border:2px solid var(--accent);color:var(--accent);
  display:flex;align-items:center;justify-content:center;font-size:24px;}
.dz-main{font-size:15px;font-weight:700;color:var(--ink);} .dz-sub{font-size:12px;color:var(--faint);}
.or-line{margin:20px 0 10px;font-size:12.5px;color:var(--faint);}
.pick-list{display:flex;flex-wrap:wrap;gap:8px;max-width:720px;margin-bottom:20px;}
.pick-item{background:var(--card);border:1px solid var(--border);border-radius:var(--radius-pill);
  padding:7px 14px;font:13px inherit;color:var(--ink);cursor:pointer;transition:border-color .15s,background .15s;}
.pick-item:hover{border-color:var(--faint);} .pick-item.on{border-color:var(--accent);background:var(--accent-soft);}
.pick-sz{color:var(--faint);font-size:11px;margin-left:4px;}
```

- [ ] **Step 3: Verify + commit**

`node --check static/kestrel.js` → 0; start app, `curl /kestrel` 200, grep for `renderStepUpload`, `doUpload`. Kill server.
```bash
git add static/kestrel.js static/kestrel.css
git commit -m "feat(ui): wizard upload step with drag/drop + existing-video picker"
```

---

### Task 5: Court step (match only) — detect + manual annotation

**Files:**
- Modify: `static/kestrel.js` (`renderStepCourt` + a ported canvas annotator), `static/kestrel.css` (court/canvas styles)

**Interfaces:**
- Consumes: `wiz.video`, `goStep`, `POST /api/detect` `{video}` → `{success, ...}` (writes `auto_court_preview.png` on success), `GET /api/template/<stem>` (image for manual annotation), `POST /api/manual-annotate` `{video, corners:[[x,y]×4]}` → `{ok, preview_path}`.
- Produces: advances to `config` once the court is accepted (auto-detected or manually annotated).

**Port note:** The proven auto-detect + 4-point canvas annotation logic lives in `web_ui.html`: `doDetect` (`:310-336`), `loadAnnoImage` (`:344-372`), `ac.onclick` (`:374-380`), `redrawAnno` (`:382-405`), `updateSteps` (`:406-419`), `undoPoint`/`resetPoints` (`:420-421`), `submitPoints` (`:423-445`). Re-implement that same coordinate-scaling + click-capture + submit flow inside `renderStepCourt`, restyled with Kestrel tokens and localized. Do NOT depend on `web_ui.html`'s global vars — keep annotation state local to the step.

- [ ] **Step 1: Implement `renderStepCourt`** (replace the Task 3 stub)

```js
  function renderStepCourt(body) {
    var zh = state.lang === 'zh';
    var stem = wiz.video.replace(/\.[^.]+$/, '');
    body.innerHTML =
      '<div id="court-auto" class="court-panel"><p class="muted">' + (zh?'正在自动检测球场…':'Auto-detecting court…') + '</p></div>' +
      '<div class="wiz-actions"><button class="btn-ghost" id="wiz-back">' + (zh?'返回':'Back') + '</button>' +
        '<button class="btn-primary" id="wiz-next" disabled>' + (zh?'继续':'Continue') + '</button></div>';
    var nextBtn = document.getElementById('wiz-next');
    document.getElementById('wiz-back').onclick = function () { goStep('upload'); };
    nextBtn.onclick = function () { goStep('config'); };
    var clicks = [], annoImg = null, sx = 1, sy = 1;

    function showManual() {
      var panel = document.getElementById('court-auto');
      panel.innerHTML =
        '<div class="court-fail">⚠️ ' + (zh?'请按顺序点击球场四个角点：左上 → 右上 → 右下 → 左下':'Click the 4 court corners in order: TL → TR → BR → BL') + '</div>' +
        '<canvas id="anno" class="anno-canvas"></canvas>' +
        '<div class="anno-tools"><span id="anno-steps" class="mono"></span>' +
          '<button class="btn-ghost" id="anno-undo">' + (zh?'撤销':'Undo') + '</button>' +
          '<button class="btn-ghost" id="anno-reset">' + (zh?'清空':'Reset') + '</button>' +
          '<button class="btn-primary" id="anno-submit" disabled>' + (zh?'提交':'Submit') + '</button></div>';
      var img = new Image(); img.crossOrigin = 'anonymous';
      img.src = '/api/template/' + stem + '?' + Date.now();
      img.onload = function () {
        annoImg = img; var c = document.getElementById('anno');
        var scale = Math.min(1, 900 / img.naturalWidth); sx = scale; sy = scale;
        c.width = Math.round(img.naturalWidth * scale); c.height = Math.round(img.naturalHeight * scale);
        redraw(); updateSteps();
      };
      var c = document.getElementById('anno');
      c.onclick = function (e) { if (!annoImg || clicks.length >= 4) return; var r = c.getBoundingClientRect();
        clicks.push([Math.round((e.clientX - r.left) / sx), Math.round((e.clientY - r.top) / sy)]); redraw(); updateSteps(); };
      document.getElementById('anno-undo').onclick = function () { clicks.pop(); redraw(); updateSteps(); };
      document.getElementById('anno-reset').onclick = function () { clicks = []; redraw(); updateSteps(); };
      document.getElementById('anno-submit').onclick = submitCorners;
    }
    function redraw() {
      var c = document.getElementById('anno'); if (!c) return; var ctx = c.getContext('2d');
      ctx.clearRect(0, 0, c.width, c.height); if (annoImg) ctx.drawImage(annoImg, 0, 0, c.width, c.height);
      var cols = ['#FF5A36', '#3FAE6A', '#4E8FE0', '#E8A93B'];
      clicks.forEach(function (p, i) { var x = p[0]*sx, y = p[1]*sy; ctx.fillStyle = cols[i];
        ctx.beginPath(); ctx.arc(x, y, 7, 0, Math.PI*2); ctx.fill(); ctx.strokeStyle = '#fff'; ctx.lineWidth = 2; ctx.stroke(); });
      if (clicks.length > 1) { ctx.strokeStyle = 'rgba(255,90,54,.7)'; ctx.lineWidth = 2; ctx.beginPath();
        clicks.forEach(function (p, i) { var x = p[0]*sx, y = p[1]*sy; i ? ctx.lineTo(x, y) : ctx.moveTo(x, y); });
        if (clicks.length === 4) ctx.closePath(); ctx.stroke(); }
    }
    function updateSteps() {
      var labels = zh ? ['左上','右上','右下','左下'] : ['TL','TR','BR','BL'];
      document.getElementById('anno-steps').textContent = labels.map(function (l, i) { return (i < clicks.length ? '✓' : '·') + l; }).join('  ');
      document.getElementById('anno-submit').disabled = clicks.length !== 4;
    }
    function submitCorners() {
      var btn = document.getElementById('anno-submit'); btn.disabled = true; btn.textContent = zh?'提交中…':'Submitting…';
      fetch('/api/manual-annotate', { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ video: wiz.video, corners: clicks }) }).then(function (r) { return r.json(); })
        .then(function (d) { if (d.ok) { document.getElementById('court-auto').innerHTML =
              '<div class="court-ok">✓ ' + (zh?'球场已标注':'Court annotated') + '</div>' +
              (d.preview_path ? '<img class="court-preview" src="' + d.preview_path + '?' + Date.now() + '">' : '');
            nextBtn.disabled = false; }
          else { btn.disabled = false; btn.textContent = zh?'提交':'Submit'; alert(d.error || 'error'); } })
        .catch(function () { btn.disabled = false; btn.textContent = zh?'提交':'Submit'; });
    }

    fetch('/api/detect', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ video: wiz.video }) }).then(function (r) { return r.json(); })
      .then(function (d) {
        if (d && d.success) {
          document.getElementById('court-auto').innerHTML =
            '<div class="court-ok">✓ ' + (zh?'球场自动检测成功':'Court auto-detected') + '</div>' +
            '<img class="court-preview" src="/api/output/' + stem + '/auto_court_preview.png?' + Date.now() + '">' +
            '<button class="btn-ghost" id="court-manual">' + (zh?'手动调整':'Adjust manually') + '</button>';
          nextBtn.disabled = false;
          document.getElementById('court-manual').onclick = showManual;
        } else { showManual(); }
      }).catch(function () { showManual(); });
  }
```

- [ ] **Step 2: Add court/canvas CSS**

```css
.court-panel{max-width:940px;margin-bottom:20px;}
.court-ok{color:var(--good);font-weight:700;margin-bottom:10px;}
.court-fail{color:var(--mid);font-weight:600;margin-bottom:12px;}
.court-preview{max-width:100%;border:1px solid var(--border);border-radius:var(--radius-md);display:block;margin-bottom:12px;}
.anno-canvas{max-width:100%;border-radius:var(--radius-md);background:#000;cursor:crosshair;display:block;}
.anno-tools{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-top:12px;}
.anno-tools .mono{font-size:12px;color:var(--muted);margin-right:auto;}
```

- [ ] **Step 3: Verify + commit**

`node --check static/kestrel.js` → 0; start app, `curl /kestrel` 200, grep for `renderStepCourt`, `submitCorners`. Kill server.
```bash
git add static/kestrel.js static/kestrel.css
git commit -m "feat(ui): wizard court step (auto-detect + manual 4-point annotation)"
```

---

### Task 6: Config step + submit to analysis

**Files:**
- Modify: `static/kestrel.js` (`renderStepConfig`), `static/kestrel.css` (form-field styles)

**Interfaces:**
- Consumes: `wiz`, `goStep`, `POST /api/analyze` `{video, language, pose_family, analyze_technique}` → `{ok, job_id}`, `POST /api/posture/analyze` `{video, stroke_type, dominant_hand, pose_family, report_llm}` → `{ok, job_id}`.
- Produces: sets `wiz.jobId` to the returned `job_id` and advances to `progress`.

- [ ] **Step 1: Implement `renderStepConfig`** (replace the Task 3 stub)

```js
  function fieldRow(labelZh, labelEn, controlHTML) {
    return '<label class="field"><span class="field-label">' + (state.lang==='zh'?labelZh:labelEn) + '</span>' + controlHTML + '</label>';
  }
  function selectHTML(id, opts) {
    return '<select id="' + id + '" class="field-select">' + opts.map(function (o) {
      return '<option value="' + o[0] + '">' + (state.lang==='zh'?o[1]:o[2]) + '</option>'; }).join('') + '</select>';
  }
  function renderStepConfig(body) {
    var zh = state.lang === 'zh';
    var fields;
    if (wiz.mode === 'posture') {
      fields =
        fieldRow('击球类型','Stroke', selectHTML('cfg-stroke', [['high_clear','高远球','High clear'],['smash','杀球','Smash'],['drop_shot','吊球','Drop shot'],['serve','发球','Serve']])) +
        fieldRow('持拍手','Dominant hand', selectHTML('cfg-hand', [['right','右手','Right'],['left','左手','Left']])) +
        fieldRow('姿态模型','Pose model', selectHTML('cfg-pose', [['yolo-pose','YOLO Pose (快)','YOLO Pose (fast)'],['rtmpose','RTMPose (准)','RTMPose (accurate)'],['rtmo','RTMO','RTMO']])) +
        fieldRow('教练报告文字润色','Report polish', selectHTML('cfg-llm', [['off','关闭','Off'],['on','开启','On']]));
    } else {
      fields =
        fieldRow('语言','Language', selectHTML('cfg-lang', [['zh','中文','Chinese'],['en','English','English']])) +
        fieldRow('姿态模型','Pose model', selectHTML('cfg-pose', [['yolo-pose','YOLO Pose (快)','YOLO Pose (fast)'],['rtmpose','RTMPose (准)','RTMPose (accurate)']])) +
        '<label class="field field-check"><input type="checkbox" id="cfg-tech" checked><span class="field-label">' + (zh?'技术分析':'Technique analysis') + '</span></label>';
    }
    body.innerHTML = '<div class="cfg-form">' + fields + '</div>' +
      '<div class="wiz-actions"><button class="btn-ghost" id="wiz-back">' + (zh?'返回':'Back') + '</button>' +
        '<button class="btn-primary" id="wiz-start">' + (zh?'开始分析':'Start Analysis') + '</button></div>';
    document.getElementById('wiz-back').onclick = function () { goStep(wiz.mode === 'match' ? 'court' : 'upload'); };
    document.getElementById('wiz-start').onclick = startAnalysis;
  }
  function startAnalysis() {
    var btn = document.getElementById('wiz-start'); btn.disabled = true;
    btn.textContent = state.lang==='zh'?'启动中…':'Starting…';
    var url, payload;
    if (wiz.mode === 'posture') {
      url = '/api/posture/analyze';
      payload = { video: wiz.video, stroke_type: document.getElementById('cfg-stroke').value,
        dominant_hand: document.getElementById('cfg-hand').value, pose_family: document.getElementById('cfg-pose').value,
        report_llm: document.getElementById('cfg-llm').value };
    } else {
      url = '/api/analyze';
      payload = { video: wiz.video, language: document.getElementById('cfg-lang').value,
        pose_family: document.getElementById('cfg-pose').value, analyze_technique: document.getElementById('cfg-tech').checked };
    }
    fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) })
      .then(function (r) { return r.json(); })
      .then(function (d) { if (d.ok) { wiz.jobId = d.job_id; goStep('progress'); }
        else { btn.disabled = false; btn.textContent = state.lang==='zh'?'开始分析':'Start Analysis'; alert(d.error || 'error'); } })
      .catch(function () { btn.disabled = false; btn.textContent = state.lang==='zh'?'开始分析':'Start Analysis'; });
  }
```

- [ ] **Step 2: Add form CSS**

```css
.cfg-form{display:flex;flex-direction:column;gap:16px;max-width:460px;margin-bottom:24px;}
.field{display:flex;flex-direction:column;gap:6px;}
.field-label{font-size:13px;font-weight:600;color:var(--ink);}
.field-select{height:38px;padding:0 12px;background:var(--card);border:1px solid var(--border);
  border-radius:var(--radius-sm);color:var(--ink);font:14px inherit;}
.field-select:focus{outline:none;border-color:var(--accent);box-shadow:0 0 0 3px var(--accent-soft);}
.field-check{flex-direction:row;align-items:center;gap:8px;cursor:pointer;}
```

- [ ] **Step 3: Verify + commit**

`node --check static/kestrel.js` → 0; start app, `curl /kestrel` 200, grep for `renderStepConfig`, `startAnalysis`. Kill server.
```bash
git add static/kestrel.js static/kestrel.css
git commit -m "feat(ui): wizard config step + submit to analyze/posture routes"
```

---

### Task 7: Progress step — bar + localized stage checklist + polling

**Files:**
- Modify: `static/kestrel.js` (`renderStepProgress`, polling), `static/kestrel.css` (progress bar + checklist styles)

**Interfaces:**
- Consumes: `wiz.jobId`, `wiz.mode`, `setScreen`, `GET /api/status/<jobId>` (match) and `GET /api/posture-analyze-status/<jobId>` (posture), each returning `{status, progress, stage, message, result}`.
- Produces: terminal step — on `completed`, shows a success panel with a button that returns to the dashboard (Phase 3 replaces this with the Results screen).

- [ ] **Step 1: Implement `renderStepProgress` + polling** (replace the Task 3 stub)

```js
  var STAGE_LABELS = {
    loading: ['加载模型', 'Loading model'], analyzing: ['分析动作', 'Analyzing'],
    scoring: ['评分', 'Scoring'], report: ['生成报告', 'Building report'],
    encoding: ['转码视频', 'Encoding video'], done: ['完成', 'Done'], error: ['失败', 'Failed']
  };
  function progressStages() {
    return wiz.mode === 'posture' ? ['loading','analyzing','scoring','report','encoding','done']
                                  : ['analyzing','encoding','done'];
  }
  function renderStepProgress(body) {
    var zh = state.lang === 'zh';
    body.innerHTML =
      '<div class="prog-wrap"><div class="prog-track"><div class="prog-fill" id="prog-fill"></div></div>' +
        '<div class="prog-pct mono" id="prog-pct">0%</div></div>' +
      '<ul class="stage-list" id="stage-list"></ul>' +
      '<div class="wiz-actions" id="prog-actions"></div>';
    pollJob();
  }
  function renderStages(curStage) {
    var stages = progressStages(); var curIdx = stages.indexOf(curStage);
    if (curStage === 'done') curIdx = stages.length;
    document.getElementById('stage-list').innerHTML = stages.map(function (st, i) {
      var cls = i < curIdx ? 'done' : (i === curIdx ? 'active' : 'pending');
      var mark = i < curIdx ? '✓' : (i === curIdx ? '●' : '○');
      return '<li class="stage ' + cls + '"><span class="stage-mark">' + mark + '</span>' +
        (state.lang === 'zh' ? STAGE_LABELS[st][0] : STAGE_LABELS[st][1]) + '</li>';
    }).join('');
  }
  function pollJob() {
    var zh = state.lang === 'zh';
    var url = (wiz.mode === 'posture' ? '/api/posture-analyze-status/' : '/api/status/') + wiz.jobId;
    var iv = setInterval(function () {
      fetch(url).then(function (r) { return r.json(); }).then(function (d) {
        var pct = d.progress || 0;
        document.getElementById('prog-fill').style.width = pct + '%';
        document.getElementById('prog-pct').textContent = pct + '%';
        renderStages(d.stage || 'analyzing');
        if (d.status === 'completed') {
          clearInterval(iv);
          document.getElementById('prog-actions').innerHTML =
            '<div class="prog-done">✓ ' + (zh?'分析完成':'Analysis complete') + '</div>' +
            '<button class="btn-primary" id="prog-lib">' + (zh?'返回视频库':'Back to Library') + '</button>';
          document.getElementById('prog-lib').onclick = function () { wiz.step = 'mode'; wiz.video = null; wiz.jobId = null; setScreen('dashboard'); };
        } else if (d.status === 'error') {
          clearInterval(iv); renderStages('error');
          document.getElementById('prog-actions').innerHTML =
            '<div class="prog-err">' + (d.message || (zh?'分析失败':'Analysis failed')) + '</div>' +
            '<button class="btn-ghost" id="prog-retry">' + (zh?'返回设置':'Back to Config') + '</button>';
          document.getElementById('prog-retry').onclick = function () { goStep('config'); };
        }
      }).catch(function () { clearInterval(iv);
        document.getElementById('prog-actions').innerHTML = '<div class="prog-err">' + (zh?'状态查询失败':'Status check failed') + '</div>'; });
    }, 1500);
  }
```

- [ ] **Step 2: Add progress CSS**

```css
.prog-wrap{display:flex;align-items:center;gap:14px;max-width:560px;margin-bottom:20px;}
.prog-track{flex:1;height:10px;background:var(--fill);border-radius:var(--radius-pill);overflow:hidden;}
.prog-fill{height:100%;width:0;background:var(--accent);border-radius:var(--radius-pill);transition:width .4s ease;}
.prog-pct{font-size:14px;font-weight:600;color:var(--ink);min-width:44px;text-align:right;}
.stage-list{list-style:none;display:flex;flex-direction:column;gap:10px;max-width:560px;margin-bottom:24px;}
.stage{display:flex;align-items:center;gap:10px;font-size:14px;color:var(--faint);}
.stage-mark{width:20px;text-align:center;}
.stage.active{color:var(--ink);font-weight:600;} .stage.active .stage-mark{color:var(--accent);}
.stage.done{color:var(--muted);} .stage.done .stage-mark{color:var(--good);}
.prog-done{color:var(--good);font-weight:700;margin-right:12px;align-self:center;}
.prog-err{color:var(--bad);font-weight:600;margin-right:12px;align-self:center;}
```

- [ ] **Step 3: Verify + commit**

`node --check static/kestrel.js` → 0; start app, `curl /kestrel` 200, grep for `renderStepProgress`, `pollJob`, `STAGE_LABELS`. Kill server.
```bash
git add static/kestrel.js static/kestrel.css
git commit -m "feat(ui): wizard progress step with staged checklist + live polling"
```

---

## Self-Review

- **Spec coverage (Phase 2):** New Analysis wizard Mode→Upload→Court→Config→Progress (Tasks 3–7) ✓; court reuses existing detect + manual annotation (Task 5) ✓; config toggles incl. technique checkbox + posture LLM (Task 6) ✓; interactive 0–100% progress with named stages (Task 7) ✓; posture progress backend emission (Task 1) + parse/stage-expose (Task 2) ✓; match stage labels (Task 2) ✓; bilingual throughout (frontend localizes stage keys) ✓; parallel `/kestrel` build, no live-app changes ✓. Deferred to Phase 3: the actual Results screen (Task 7 ends at a "Back to Library" success panel).
- **Placeholder scan:** Task 3 intentionally ships stub `renderStep*` bodies that Tasks 4–7 replace — each stub names its task and renders harmless text, so the wizard never throws mid-phase; this is sequenced, not a placeholder gap. All backend steps carry full test + impl code. No TBD/TODO.
- **Type consistency:** `wiz` fields (`mode`,`video`,`step`,`jobId`) consistent across Tasks 3–7; `goStep`/`setScreen`/`renderWizard`/`renderStep*` names consistent; stage keys (`loading/analyzing/scoring/report/encoding/done/error`) identical between Task 1 emission, Task 2 job stages, and Task 7 `STAGE_LABELS`/`progressStages`; status JSON `{status,progress,stage,message,result}` consistent between Task 2 (producer) and Task 7 (consumer); `format_progress`/`analyzing_pct`/`_parse_progress_line` signatures match across Tasks 1–2.

## Notes for the executor

- Base commit for Phase 2 is `7da4ab9`.
- No JS test framework: front-end tasks verify with `node --check` + curl against a running app; backend tasks are TDD with pytest.
- Deferred-from-Phase-1 polish (negative-cache for failed thumbnail decodes; escaping `v.name` in card rendering) is NOT in Phase 2 scope — leave for a later cleanup pass unless a task naturally touches it.
