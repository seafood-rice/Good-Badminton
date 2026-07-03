# Kestrel Phase 3 — Results Screen (Match + Posture) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a per-video **Results screen** to the Kestrel UI so an analyzed video opens into its results — Match mode (annotated video, rally summary + clip generation, position heatmap/scatter, technique analysis) and Posture mode (annotated video, drill summary, per-rep detail with a bounded rep scrubber and on-demand "download this rep").

**Architecture:** Frontend work extends `static/kestrel.js` with a third screen (`state.screen === 'results'`) and a mode-dispatching `renderResults()`, wired to the existing results endpoints; video/image artifact URLs are built **by convention** (`/api/output/<stem>/…`) rather than read from the in-memory job, so library-loaded videos work. Backend work is a single additive route: `POST /api/posture-rep-clip/<video>` ffmpeg-crops a rep window (derived from `contact_frame ± padding` and `fps`) out of the annotated posture video. All existing routes and the old `/` UI are untouched.

**Tech Stack:** Python 3 / Flask + ffmpeg (backend), vanilla JS + CSS custom properties (frontend), pytest (backend tests).

## Global Constraints

- Bilingual **zh/en**, default `zh`; every user-facing string localized in the frontend. (Spec.)
- **Offline-safe:** no CDN/external references. (Spec.)
- **Additive backend only:** only one new route is added (`/api/posture-rep-clip`); the match/posture pipelines and all existing routes keep working; the old UI at `/` stays functional. (Spec.)
- Kestrel is built in **parallel** at `/kestrel`; the `/` swap is deferred to a later phase. Only `static/kestrel.js`, `static/kestrel.css`, `app.py`, and a new test file are touched.
- Theme tokens: light `:root` + `[data-theme="dark"]`; accent `#FF5A36` and score hues constant across themes; components read tokens only — no per-component dark CSS. (Spec.)
- **Scope boundary (locked):** Phase 3 = Results shell + routing + Match (video, rally + clips, heatmap/scatter, technique analysis) + Posture (video, drill summary, rep list + rep detail, bounded scrubber, download-this-rep). **Training Plan, Coach Report, and Notes/Export are deferred to Phase 4/5** and are NOT built here.
- **All existing tests stay green** (baseline **151** after Phase 2). This repo has **no JS test framework** — front-end tasks are verified with `node --check static/kestrel.js` + running the app + curl/dogfood, NOT automated tests. Backend tasks are TDD with pytest via `./.venv/Scripts/python.exe -m pytest` (Windows; set `PYTHONUTF8=1` when launching `app.py` directly).
- **Score scale is 0–100** across all results data (`overall_score`, `per_metric[*].score`, `mean_score`); `ideal_range` is `[min, max]`; posture `per_metric[k]` = `{measured, score, ideal_range, weight, direction, severity}`; reps carry an explicit 1-based `rep_id` and a `contact_frame`.
- **Graceful missing artifacts:** position heatmap/scatter PNGs and technique strokes may be absent on disk (they are, in the current samples) — the UI must degrade to a clear "not available" note, never a broken image or a thrown error.
- **Dogfood samples:** `IMG_1537` (match: has `rally_segments.json` with 1 rally; empty strokes + empty heatmap/scatter dirs → exercises the graceful paths) and `IMG_1270` (posture: 8 reps + reports; also has a full match artifact set → exercises the dual-mode toggle).

## Data contracts (verified against app.py + on-disk samples)

- `GET /api/technique/<stem>` → `200 {summary, strokes}` or `404`. `summary = {stroke_count, by_type:{<type>:{count, avg_score}}, recurring_weaknesses:[{metric,count}], strengths:[...]}`. `strokes = [{stroke_type, overall_score, per_metric:{<metric>:{measured, score, ideal_range}}, weaknesses:[{description,...}]}]` (may be empty).
- `POST /api/clip` body `{video:<stem>, mode:'highlights', padding:1.5}` → `{ok, mode, clips:[{name, url, size_mb}]}` or `400` if no `rally_segments.json`. (Passing the bare stem as `video` works: the route derives `save_dir`, the annotated video, and `rally_segments.json` from `.stem`.)
- `GET /api/posture/<stem>` → `200 {summary, reps}` or `404`. `summary = {stroke_type, rep_count, mean_score, best_rep:{rep_id,score}, worst_rep:{rep_id,score}, consistency, per_metric_avg:{...}, recurring_weaknesses:[{metric,count}], strengths:[{metric,count}]}`. `reps = [{rep_id, stroke_type, contact_frame, overall_score, per_metric:{<metric>:{measured, score, ideal_range, direction, severity}}, weaknesses:[{metric, measured, ideal_range, direction, severity, description}], strengths:[<metric>]}]`.
- Files via `GET /api/output/<stem>/<subpath>`: annotated match video `detect_<stem>.mp4`; heatmap `position_visualizations/heatmaps/match_heatmap.png`; scatter `position_visualizations/scatter_plots/match_scatter.png`; rally data `rally_segments.json` (`{fps, rallies:[{id,start_frame,end_frame,start_sec,end_sec}]}`); annotated posture video `posture/detect_<stem>.mp4`; posture `posture/metadata.json` (`{video:{fps,...}}`).

---

### Task 1: Results screen router + scaffold + navigation

**Files:**
- Modify: `static/kestrel.js` (state fields; `openResults`; `render` dispatch; `renderResults` shell + mode toggle; card click wiring in `renderCards`; contextual sidebar nav; wizard-completion "View Results"; `T` `nav_results` key; stubs for `renderMatchResults`/`renderPostureResults`; exports)
- Modify: `static/kestrel.css` (results shell / title / mode-toggle / back-button styles; clickable card affordance)

**Interfaces:**
- Consumes: `state`, `setScreen`, `render`, `renderSidebar`, `loadDashboard`, `renderWizard`, `pollJob`, `lib.videos` (Phases 1–2).
- Produces: `state.resultsVideo` (stem string | null), `state.resultsMode` (`'match'|'posture'`), `state.resultsModes` (array); `openResults(stem, mode, modes)` sets those and re-renders; `renderResults()` paints the shell into `#main` and dispatches to `renderMatchResults(body)` (Task 2) / `renderPostureResults(body)` (Task 3) via `#results-body`; `render()` handles the `'results'` screen. Exports add `openResults`.

- [ ] **Step 1: Add results state fields**

In the `state` object initializer (currently `{ lang, theme, screen: 'dashboard' }`) add three fields:
```js
  var state = { lang: localStorage.getItem('kestrel_lang') || 'zh',
                theme: localStorage.getItem('kestrel_theme') ||
                       (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'),
                screen: 'dashboard', resultsVideo: null, resultsMode: 'match', resultsModes: [] };
```

- [ ] **Step 2: Add the `nav_results` label to both dictionaries**

In `T`, add `nav_results` to each language:
```js
    zh: { brand: 'Kestrel', portal: '教练平台', nav_dashboard: '视频库',
          nav_new: '新建分析', nav_results: '结果', persona: 'Coach Lee', team: 'Team Falcons',
          theme_light: '浅色', theme_dark: '深色' },
    en: { brand: 'Kestrel', portal: 'Coach Portal', nav_dashboard: 'Dashboard',
          nav_new: 'New Analysis', nav_results: 'Results', persona: 'Coach Lee', team: 'Team Falcons',
          theme_light: 'Light', theme_dark: 'Dark' }
```

- [ ] **Step 3: Add `openResults`, `renderResults`, and step stubs**

Add these functions to `kestrel.js` (place them just before `function setScreen`):
```js
  function openResults(stem, mode, modes) {
    state.resultsVideo = stem;
    state.resultsMode = mode;
    state.resultsModes = (modes && modes.length) ? modes : [mode];
    state.screen = 'results';
    render();
  }
  function renderResults() {
    var main = document.getElementById('main'); var zh = state.lang === 'zh';
    var modes = state.resultsModes && state.resultsModes.length ? state.resultsModes : [state.resultsMode];
    var toggle = '';
    if (modes.length > 1) {
      toggle = '<div class="seg" role="tablist" aria-label="mode">' + modes.map(function (m) {
        var on = m === state.resultsMode;
        var lbl = m === 'posture' ? (zh ? '训练' : 'Drill') : (zh ? '比赛' : 'Match');
        return '<button class="seg-btn' + (on ? ' on' : '') + '" role="tab" aria-selected="' + on +
          '" data-rmode="' + m + '">' + lbl + '</button>';
      }).join('') + '</div>';
    }
    main.innerHTML =
      '<div class="page-head"><div class="rhead-left">' +
        '<button class="btn-ghost" id="res-back">← ' + (zh ? '视频库' : 'Library') + '</button>' +
        '<h1 class="res-title mono">' + state.resultsVideo + '</h1></div>' + toggle + '</div>' +
      '<div id="results-body"></div>';
    document.getElementById('res-back').onclick = function () { setScreen('dashboard'); };
    main.querySelectorAll('[data-rmode]').forEach(function (b) {
      b.onclick = function () { state.resultsMode = b.getAttribute('data-rmode'); renderResults(); };
    });
    var body = document.getElementById('results-body');
    if (state.resultsMode === 'posture') { renderPostureResults(body); }
    else { renderMatchResults(body); }
  }
  function renderMatchResults(body) { body.innerHTML = '<p class="muted">match results (Task 2)</p>'; }
  function renderPostureResults(body) { body.innerHTML = '<p class="muted">posture results (Task 3)</p>'; }
```

- [ ] **Step 4: Dispatch the `results` screen in `render`**

Replace `render`:
```js
  function render() {
    renderSidebar();
    if (state.screen === 'new') { renderWizard(); }
    else if (state.screen === 'results') { renderResults(); }
    else { loadDashboard(); }
  }
```

- [ ] **Step 5: Make cards open Results**

In `renderCards`, (a) add a `data-name` attribute to each `.vcard`, and (b) wire clicks after setting `innerHTML`.

Change the card's opening tag from `'<div class="vcard">'` to include the name (define `nm` at the top of the `.map` callback):
```js
    wrap.innerHTML = vids.map(function (v) {
      var nm = (v.name || '').replace(/"/g, '&quot;');
      var thumb = v.thumb ? 'background-image:url(' + v.thumb + ')' : '';
      var dur = v.duration_sec ? Math.floor(v.duration_sec/60)+':'+('0'+Math.round(v.duration_sec%60)).slice(-2) : '';
      return '<div class="vcard" role="button" tabindex="0" data-name="' + nm + '"><div class="vthumb" style="' + thumb + '">' +
        '<span class="vmode mono">' + modeLabel(v) + '</span>' +
        '<span class="vdur mono">' + dur + '</span></div>' +
        '<div class="vbody"><div class="vname">' + v.name + '</div>' +
        '<div class="vdate mono">' + (v.date||'') + '</div>' +
        '<div class="vfoot">' + statusChip(v) + '</div></div></div>';
    }).join('');
    wrap.querySelectorAll('.vcard').forEach(function (el) {
      el.onclick = function () {
        var name = el.getAttribute('data-name');
        var v = lib.videos.filter(function (x) { return x.name === name; })[0];
        if (!v) return;
        if (v.has_match || v.has_posture) {
          var modes = [];
          if (v.has_match) modes.push('match');
          if (v.has_posture) modes.push('posture');
          openResults(v.name, modes[0], modes);
        } else { setScreen('new'); }
      };
    });
```

- [ ] **Step 6: Add the contextual Results nav item**

In `renderSidebar`, the nav currently has Dashboard + New Analysis buttons. Add a Results item that appears only when a video is open. Change the two nav lines to three:
```js
        '<button class="nav-item" data-screen="dashboard">' + t('nav_dashboard') + '</button>' +
        '<button class="nav-item" data-screen="new">' + t('nav_new') + '</button>' +
        (state.resultsVideo ? '<button class="nav-item" data-screen="results">' + t('nav_results') + '</button>' : '') +
```
(The existing `[data-screen]` wiring already calls `setScreen(...)`; since `state.resultsVideo` is set whenever this button is shown, `setScreen('results')` renders correctly.)

- [ ] **Step 7: Offer "View Results" on wizard completion**

In `pollJob`, the `d.status === 'completed'` branch currently renders a done message + a "Back to Library" button. Replace that branch's `prog-actions` block with a primary "View Results" button plus the existing Library button:
```js
        if (d.status === 'completed') {
          clearInterval(iv);
          document.getElementById('prog-actions').innerHTML =
            '<div class="prog-done">✓ ' + (zh?'分析完成':'Analysis complete') + '</div>' +
            '<button class="btn-primary" id="prog-results">' + (zh?'查看结果':'View Results') + '</button>' +
            '<button class="btn-ghost" id="prog-lib">' + (zh?'返回视频库':'Back to Library') + '</button>';
          document.getElementById('prog-results').onclick = function () {
            var stem = wiz.video.replace(/\.[^.]+$/, ''); var m = wiz.mode;
            wiz.step = 'mode'; wiz.video = null; wiz.jobId = null;
            openResults(stem, m, [m]);
          };
          document.getElementById('prog-lib').onclick = function () { wiz.step = 'mode'; wiz.video = null; wiz.jobId = null; setScreen('dashboard'); };
        } else if (d.status === 'error') {
```
(Leave the `else if (d.status === 'error')` block unchanged.)

- [ ] **Step 8: Export `openResults`**

Add `openResults: openResults` to the returned object literal (alongside `setScreen`).

- [ ] **Step 9: Add results-shell + clickable-card CSS to `kestrel.css`**

Append:
```css
.rhead-left{display:flex;align-items:center;gap:14px;}
.res-title{font-size:20px;font-weight:700;color:var(--ink);margin:0;}
.vcard{cursor:pointer;}
.vcard:focus-visible{outline:2px solid var(--accent);outline-offset:2px;}
.res-grid{display:grid;grid-template-columns:minmax(0,1.4fr) minmax(240px,1fr);gap:20px;align-items:start;margin-bottom:20px;}
.res-video{width:100%;border-radius:var(--radius-md);background:#000;display:block;}
.res-section{margin-bottom:26px;}
.res-section h2{font-size:15px;font-weight:700;color:var(--ink);margin:0 0 12px;}
@media (max-width:820px){.res-grid{grid-template-columns:1fr;}}
```

- [ ] **Step 10: Verify + commit**

Run: `node --check static/kestrel.js` → exit 0.
Start `PYTHONUTF8=1 ./.venv/Scripts/python.exe app.py --port 5096 &`; `curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:5096/kestrel` → 200; `curl -s http://127.0.0.1:5096/static/kestrel.js` and grep for `openResults`, `renderResults`, `data-rmode`. Kill the server.
```bash
git add static/kestrel.js static/kestrel.css
git commit -m "feat(ui): Kestrel results screen router + scaffold + navigation"
```

---

### Task 2: Match Results (video, rally + clips, heatmap/scatter, technique)

**Files:**
- Modify: `static/kestrel.js` (`renderMatchResults` replacing the Task 1 stub; shared helpers `scoreHue`, `metricLabel`, `metricBarHTML`, `METRIC_LABELS`)
- Modify: `static/kestrel.css` (viz images, clip list, technique summary/stroke-list/metric-bar styles)

**Interfaces:**
- Consumes: `state.resultsVideo`, `t`, `GET /api/technique/<stem>`, `POST /api/clip`, files via `/api/output/<stem>/…`.
- Produces: `scoreHue(score) -> css var string`; `metricLabel(key) -> localized string`; `metricBarHTML(key, m) -> html string` (used by Task 3 too); `renderMatchResults(body)` paints the match results.

- [ ] **Step 1: Add shared score/metric helpers** (place before `renderMatchResults`)

```js
  var METRIC_LABELS = {
    elbow_extension:['肘部伸展','Elbow extension'], trunk_rotation:['躯干旋转','Trunk rotation'],
    wrist_flexion:['手腕屈曲','Wrist flexion'], knee_flexion:['膝盖弯曲','Knee flexion'],
    hip_shoulder_separation:['髋肩分离','Hip–shoulder separation'], weight_transfer:['重心转移','Weight transfer']
  };
  function metricLabel(k) { var m = METRIC_LABELS[k]; return m ? (state.lang==='zh'?m[0]:m[1]) : String(k).replace(/_/g,' '); }
  function scoreHue(s) { return s >= 70 ? 'var(--good)' : (s >= 40 ? 'var(--mid)' : 'var(--bad)'); }
  function metricBarHTML(key, m) {
    var score = Math.max(0, Math.min(100, Number(m.score) || 0));
    var measured = (m.measured === null || m.measured === undefined) ? '—' : (Math.round(m.measured * 10) / 10);
    var ideal = (m.ideal_range && m.ideal_range.length === 2) ? (m.ideal_range[0] + '–' + m.ideal_range[1]) : '';
    return '<div class="metric"><div class="metric-head"><span class="metric-name">' + metricLabel(key) + '</span>' +
      '<span class="metric-val mono">' + measured + (ideal ? ' <span class="metric-ideal">(' + (state.lang==='zh'?'理想':'ideal') + ' ' + ideal + ')</span>' : '') + '</span></div>' +
      '<div class="metric-track"><div class="metric-fill" style="width:' + score + '%;background:' + scoreHue(score) + '"></div></div></div>';
  }
```

- [ ] **Step 2: Implement `renderMatchResults`** (replace the Task 1 stub)

```js
  function renderMatchResults(body) {
    var zh = state.lang === 'zh'; var stem = state.resultsVideo;
    var vurl = '/api/output/' + stem + '/detect_' + stem + '.mp4';
    var heat = '/api/output/' + stem + '/position_visualizations/heatmaps/match_heatmap.png';
    var scat = '/api/output/' + stem + '/position_visualizations/scatter_plots/match_scatter.png';
    body.innerHTML =
      '<div class="res-grid"><div><video class="res-video" controls src="' + vurl + '"></video></div>' +
        '<div><div class="res-section" id="rally-box"><h2>' + (zh?'回合':'Rallies') + '</h2>' +
          '<p class="muted" id="rally-info">' + (zh?'加载中…':'Loading…') + '</p>' +
          '<button class="btn-primary" id="clip-btn" disabled>' + (zh?'生成回合剪辑':'Generate clips') + '</button>' +
          '<div id="clip-list" class="clip-list"></div></div></div></div>' +
      '<div class="res-section"><h2>' + (zh?'位置可视化':'Position visualization') + '</h2>' +
        '<div class="viz-row"><div class="viz-cell" id="viz-heat"><img class="res-viz" src="' + heat + '" alt="heatmap"><div class="viz-cap mono">' + (zh?'热力图':'Heatmap') + '</div></div>' +
          '<div class="viz-cell" id="viz-scat"><img class="res-viz" src="' + scat + '" alt="scatter"><div class="viz-cap mono">' + (zh?'散点图':'Scatter') + '</div></div></div></div>' +
      '<div class="res-section" id="tech-box"><h2>' + (zh?'技术分析':'Technique analysis') + '</h2>' +
        '<p class="muted" id="tech-status">' + (zh?'加载中…':'Loading…') + '</p>' +
        '<div id="tech-summary"></div><div id="tech-strokes" class="stroke-list"></div><div id="tech-detail" class="rep-detail"></div></div>';

    // Heatmap/scatter: hide the cell and show a note if the image is missing.
    ['viz-heat','viz-scat'].forEach(function (id) {
      var cell = document.getElementById(id); var img = cell.querySelector('img');
      img.onerror = function () { cell.innerHTML = '<div class="viz-missing">' + (zh?'暂无可视化':'Not available') + '</div>'; };
    });

    // Rally summary + clip generation.
    fetch('/api/output/' + stem + '/rally_segments.json').then(function (r) { return r.ok ? r.json() : null; }).then(function (d) {
      var n = d && d.rallies ? d.rallies.length : 0;
      document.getElementById('rally-info').textContent = zh ? (n + ' 个回合') : (n + ' rallies detected');
      var btn = document.getElementById('clip-btn'); btn.disabled = n === 0;
    }).catch(function () { document.getElementById('rally-info').textContent = zh?'暂无回合数据':'No rally data'; document.getElementById('clip-btn').disabled = true; });
    document.getElementById('clip-btn').onclick = function () {
      var btn = document.getElementById('clip-btn'); btn.disabled = true; btn.textContent = zh?'生成中…':'Generating…';
      fetch('/api/clip', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({ video: stem, mode:'highlights', padding:1.5 }) })
        .then(function (r) { return r.json(); }).then(function (d) {
          btn.textContent = zh?'生成回合剪辑':'Generate clips'; btn.disabled = false;
          if (d.ok && d.clips) { document.getElementById('clip-list').innerHTML = d.clips.map(function (c) {
            return '<a class="clip-item" href="' + c.url + '" download>⬇ ' + c.name + ' <span class="mono">' + c.size_mb + 'MB</span></a>'; }).join(''); }
          else { alert(d.error || 'error'); } })
        .catch(function () { btn.textContent = zh?'生成回合剪辑':'Generate clips'; btn.disabled = false; });
    };

    // Technique analysis.
    fetch('/api/technique/' + stem).then(function (r) { return r.ok ? r.json() : null; }).then(function (d) {
      var status = document.getElementById('tech-status');
      if (!d || !d.summary || !d.summary.stroke_count) { status.textContent = zh?'暂无技术分析数据':'No technique data yet'; return; }
      status.style.display = 'none';
      var s = d.summary;
      var chips = Object.keys(s.by_type || {}).map(function (k) {
        var bt = s.by_type[k]; return '<span class="chip-metric"><b>' + k + '</b> ' + bt.count + '× · ' + (Math.round(bt.avg_score) ) + '</span>'; }).join('');
      var weak = (s.recurring_weaknesses || []).slice(0,3).map(function (w) { return '<li>' + metricLabel(w.metric) + ' ×' + w.count + '</li>'; }).join('');
      document.getElementById('tech-summary').innerHTML =
        '<div class="tech-sum"><div class="mono">' + (zh?'击球数':'Strokes') + ': ' + s.stroke_count + '</div>' +
        '<div class="chip-row">' + chips + '</div>' + (weak ? '<div class="weak"><span class="muted">' + (zh?'常见问题':'Recurring') + '</span><ul>' + weak + '</ul></div>' : '') + '</div>';
      var strokes = d.strokes || [];
      document.getElementById('tech-strokes').innerHTML = strokes.map(function (st, i) {
        return '<button class="stroke-row" data-i="' + i + '"><span class="stroke-type">' + st.stroke_type + '</span>' +
          '<span class="score-chip mono" style="background:' + scoreHue(st.overall_score) + '">' + Math.round(st.overall_score) + '</span></button>'; }).join('');
      document.querySelectorAll('.stroke-row').forEach(function (b) { b.onclick = function () {
        document.querySelectorAll('.stroke-row').forEach(function (x) { x.classList.remove('on'); }); b.classList.add('on');
        var st = strokes[Number(b.getAttribute('data-i'))];
        var bars = Object.keys(st.per_metric || {}).map(function (k) { return metricBarHTML(k, st.per_metric[k]); }).join('');
        var ws = (st.weaknesses || []).map(function (w) { return '<li>' + (w.description || metricLabel(w.metric)) + '</li>'; }).join('');
        document.getElementById('tech-detail').innerHTML = bars + (ws ? '<ul class="weak-list">' + ws + '</ul>' : '');
      }; });
    }).catch(function () { document.getElementById('tech-status').textContent = zh?'技术分析加载失败':'Failed to load technique'; });
  }
```

- [ ] **Step 3: Add match-results CSS**

```css
.viz-row{display:flex;gap:16px;flex-wrap:wrap;}
.viz-cell{flex:1;min-width:220px;}
.res-viz{width:100%;border:1px solid var(--border);border-radius:var(--radius-md);display:block;}
.viz-cap{font-size:11px;color:var(--faint);margin-top:6px;text-align:center;}
.viz-missing{padding:28px;border:1px dashed var(--border);border-radius:var(--radius-md);color:var(--faint);text-align:center;font-size:13px;}
.clip-list{display:flex;flex-direction:column;gap:6px;margin-top:10px;}
.clip-item{font-size:13px;color:var(--accent);text-decoration:none;}
.clip-item:hover{text-decoration:underline;}
.tech-sum{margin-bottom:14px;} .chip-row{display:flex;flex-wrap:wrap;gap:6px;margin:8px 0;}
.chip-metric{background:var(--fill);border-radius:var(--radius-pill);padding:4px 10px;font-size:12px;color:var(--ink);}
.weak span{font-size:12px;} .weak ul,.weak-list{margin:6px 0 0;padding-left:18px;font-size:13px;color:var(--muted);}
.stroke-list{display:flex;flex-direction:column;gap:6px;margin-bottom:14px;}
.stroke-row{display:flex;align-items:center;justify-content:space-between;background:var(--card);border:1px solid var(--border);
  border-radius:var(--radius-sm);padding:10px 14px;font:inherit;cursor:pointer;transition:border-color .15s;}
.stroke-row:hover{border-color:var(--faint);} .stroke-row.on{border-color:var(--accent);background:var(--accent-soft);}
.stroke-type{font-size:14px;color:var(--ink);font-weight:600;}
.score-chip{color:#fff;border-radius:var(--radius-pill);padding:2px 10px;font-size:12px;font-weight:700;}
.metric{margin-bottom:12px;} .metric-head{display:flex;justify-content:space-between;font-size:12.5px;margin-bottom:4px;}
.metric-name{color:var(--ink);} .metric-val{color:var(--muted);} .metric-ideal{color:var(--faint);}
.metric-track{height:8px;background:var(--fill);border-radius:var(--radius-pill);overflow:hidden;}
.metric-fill{height:100%;border-radius:var(--radius-pill);transition:width .4s ease;}
.rep-detail{margin-top:8px;}
```

- [ ] **Step 4: Verify + commit**

Run: `node --check static/kestrel.js` → 0. Start app on port 5096; open `/kestrel`, click the `IMG_1537` card → confirm the match video plays, rally count shows "1", heatmap/scatter show "Not available" (dirs empty), technique shows "No technique data yet" (empty strokes). Grep served JS for `renderMatchResults`, `metricBarHTML`. Kill server.
```bash
git add static/kestrel.js static/kestrel.css
git commit -m "feat(ui): match results — video, rally clips, heatmap/scatter, technique"
```

---

### Task 3: Posture Results (video, drill summary, rep list + rep detail)

**Files:**
- Modify: `static/kestrel.js` (`renderPostureResults` replacing the Task 1 stub; `selectRep`)
- Modify: `static/kestrel.css` (drill summary / rep list / rep detail styles)

**Interfaces:**
- Consumes: `state.resultsVideo`, `t`, `metricBarHTML`, `scoreHue`, `metricLabel` (Task 2), `GET /api/posture/<stem>`, posture video via `/api/output/<stem>/posture/…`.
- Produces: `renderPostureResults(body)` paints the posture results; `selectRep(reps, i)` fills `#rep-detail`. A module-scoped `postureCtx` object holds `{stem, reps, fps, sel}` for Task 5 to consume.

- [ ] **Step 1: Add the posture context holder** (place near `var wiz = …`)

```js
  var postureCtx = { stem: null, reps: [], fps: 30, sel: -1 };
```

- [ ] **Step 2: Implement `renderPostureResults` + `selectRep`** (replace the Task 1 stub)

```js
  function renderPostureResults(body) {
    var zh = state.lang === 'zh'; var stem = state.resultsVideo;
    postureCtx = { stem: stem, reps: [], fps: 30, sel: -1 };
    var vurl = '/api/output/' + stem + '/posture/detect_' + stem + '.mp4';
    body.innerHTML =
      '<div class="res-grid"><div><video id="posture-video" class="res-video" controls src="' + vurl + '"></video>' +
        '<div id="rep-scrubber" class="scrubber"></div></div>' +
        '<div><div class="res-section" id="drill-summary"><p class="muted">' + (zh?'加载中…':'Loading…') + '</p></div>' +
          '<div class="res-section"><h2>' + (zh?'逐次':'Reps') + '</h2><ul id="rep-list" class="rep-list"></ul></div></div></div>' +
      '<div class="res-section" id="rep-detail" class="rep-detail"></div>';
    fetch('/api/posture/' + stem).then(function (r) { return r.ok ? r.json() : null; }).then(function (d) {
      if (!d || !d.summary) { document.getElementById('drill-summary').innerHTML = '<p class="muted">' + (zh?'暂无姿态数据':'No posture data yet') + '</p>'; return; }
      var s = d.summary; postureCtx.reps = d.reps || [];
      var weak = (s.recurring_weaknesses || []).slice(0,3).map(function (w) { return '<li>' + metricLabel(w.metric) + ' ×' + w.count + '</li>'; }).join('');
      document.getElementById('drill-summary').innerHTML =
        '<h2>' + (zh?'训练概览':'Drill summary') + '</h2>' +
        '<div class="dsum-grid mono">' +
          '<div><span>' + (zh?'次数':'Reps') + '</span><b>' + s.rep_count + '</b></div>' +
          '<div><span>' + (zh?'平均分':'Mean') + '</span><b style="color:' + scoreHue(s.mean_score) + '">' + Math.round(s.mean_score) + '</b></div>' +
          '<div><span>' + (zh?'一致性':'Consistency') + '</span><b>' + (Math.round(s.consistency*10)/10) + '</b></div>' +
          '<div><span>' + (zh?'最佳':'Best') + '</span><b>#' + (s.best_rep&&s.best_rep.rep_id) + '</b></div>' +
          '<div><span>' + (zh?'最差':'Worst') + '</span><b>#' + (s.worst_rep&&s.worst_rep.rep_id) + '</b></div>' +
        '</div>' + (weak ? '<div class="weak"><span class="muted">' + (zh?'常见问题':'Recurring') + '</span><ul>' + weak + '</ul></div>' : '');
      document.getElementById('rep-list').innerHTML = postureCtx.reps.map(function (rep, i) {
        return '<li><button class="rep-row" data-i="' + i + '"><span class="mono">#' + rep.rep_id + '</span>' +
          '<span class="score-chip mono" style="background:' + scoreHue(rep.overall_score) + '">' + Math.round(rep.overall_score) + '</span></button></li>'; }).join('');
      document.querySelectorAll('.rep-row').forEach(function (b) { b.onclick = function () { selectRep(postureCtx.reps, Number(b.getAttribute('data-i'))); }; });
      if (postureCtx.reps.length) { selectRep(postureCtx.reps, 0); }
    }).catch(function () { document.getElementById('drill-summary').innerHTML = '<p class="muted">' + (zh?'加载失败':'Failed to load') + '</p>'; });
  }
  function selectRep(reps, i) {
    postureCtx.sel = i; var rep = reps[i];
    document.querySelectorAll('.rep-row').forEach(function (x) { x.classList.toggle('on', Number(x.getAttribute('data-i')) === i); });
    var bars = Object.keys(rep.per_metric || {}).map(function (k) { return metricBarHTML(k, rep.per_metric[k]); }).join('');
    var ws = (rep.weaknesses || []).map(function (w) { return '<li>' + (w.description || metricLabel(w.metric)) + '</li>'; }).join('');
    document.getElementById('rep-detail').innerHTML =
      '<h2>' + (state.lang==='zh'?'第 ':'Rep #') + rep.rep_id + (state.lang==='zh'?' 次详情':'') + '</h2>' +
      bars + (ws ? '<ul class="weak-list">' + ws + '</ul>' : '');
  }
```

- [ ] **Step 3: Add posture-results CSS**

```css
.dsum-grid{display:grid;grid-template-columns:1fr 1fr;gap:10px 16px;margin-bottom:12px;}
.dsum-grid > div{display:flex;flex-direction:column;} .dsum-grid span{font-size:11px;color:var(--faint);}
.dsum-grid b{font-size:20px;color:var(--ink);}
.rep-list{list-style:none;display:flex;flex-direction:column;gap:6px;margin:0;padding:0;}
.rep-row{width:100%;display:flex;align-items:center;justify-content:space-between;background:var(--card);
  border:1px solid var(--border);border-radius:var(--radius-sm);padding:8px 12px;font:inherit;cursor:pointer;transition:border-color .15s;}
.rep-row:hover{border-color:var(--faint);} .rep-row.on{border-color:var(--accent);background:var(--accent-soft);}
```

- [ ] **Step 4: Verify + commit**

Run: `node --check static/kestrel.js` → 0. Start app; open `IMG_1270` → toggle to **Drill** → confirm the posture video loads, drill summary shows 8 reps / mean score / best #8 / worst #5, the rep list renders 8 rows, clicking a rep shows metric bars + weakness suggestions. Grep served JS for `renderPostureResults`, `selectRep`. Kill server.
```bash
git add static/kestrel.js static/kestrel.css
git commit -m "feat(ui): posture results — drill summary + per-rep metric detail"
```

---

### Task 4: Backend — `POST /api/posture-rep-clip/<video>` (TDD)

**Files:**
- Modify: `app.py` (`_rep_clip_window` helper; `api_posture_rep_clip` route)
- Test: `tests/test_app_rep_clip.py` (create)

**Interfaces:**
- Consumes: `drill_reps.jsonl` (per rep `rep_id`, `contact_frame`), `posture/metadata.json` (`video.fps`), the annotated posture video `posture/detect_<stem>.mp4`.
- Produces: `_rep_clip_window(contact_frame, fps, padding=1.5) -> (start_sec, duration_sec)` (floats, start clamped ≥ 0, fps ≤ 0 falls back to 30); `POST /api/posture-rep-clip/<video_name>` body `{rep_id}` → `{ok, url}` (200), `{error}` with 400 (missing `rep_id`) / 404 (no reps file, rep not found, or no annotated video) / 500 (ffmpeg failure).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_app_rep_clip.py
import json
import pytest

import app as webapp


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(webapp, "OUTPUTS", tmp_path)
    webapp.app.config["TESTING"] = True
    return webapp.app.test_client()


def _write_reps(tmp_path, video, with_video=False):
    out = tmp_path / video / "posture"
    out.mkdir(parents=True, exist_ok=True)
    with open(out / "drill_reps.jsonl", "w", encoding="utf-8") as f:
        f.write(json.dumps({"rep_id": 1, "contact_frame": 90, "overall_score": 50}) + "\n")
        f.write(json.dumps({"rep_id": 2, "contact_frame": 300, "overall_score": 70}) + "\n")
    (out / "metadata.json").write_text(json.dumps({"video": {"fps": 30.0}}), encoding="utf-8")
    if with_video:
        (out / ("detect_" + video + ".mp4")).write_bytes(b"x")
    return out


def test_rep_clip_window_math():
    assert webapp._rep_clip_window(90, 30.0, 1.5) == (1.5, 3.0)     # center 3.0s -> start 1.5, dur 3.0
    assert webapp._rep_clip_window(0, 30.0, 1.5) == (0.0, 3.0)      # clamp start at 0
    assert webapp._rep_clip_window(90, 0, 1.5) == (1.5, 3.0)        # fps<=0 -> fallback 30


def test_rep_clip_400_without_rep_id(client, tmp_path):
    _write_reps(tmp_path, "drill1", with_video=True)
    r = client.post("/api/posture-rep-clip/drill1", json={})
    assert r.status_code == 400


def test_rep_clip_404_without_reps_file(client):
    r = client.post("/api/posture-rep-clip/nope", json={"rep_id": 1})
    assert r.status_code == 404


def test_rep_clip_404_when_rep_missing(client, tmp_path):
    _write_reps(tmp_path, "drill1", with_video=True)
    r = client.post("/api/posture-rep-clip/drill1", json={"rep_id": 999})
    assert r.status_code == 404


def test_rep_clip_404_without_annotated_video(client, tmp_path):
    _write_reps(tmp_path, "drill1", with_video=False)
    r = client.post("/api/posture-rep-clip/drill1", json={"rep_id": 1})
    assert r.status_code == 404
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_app_rep_clip.py -v`
Expected: FAIL — `AttributeError: module 'app' has no attribute '_rep_clip_window'`.

- [ ] **Step 3: Add `_rep_clip_window`**

Near the other helpers in `app.py` (e.g. after `_parse_progress_line`):
```python
def _rep_clip_window(contact_frame, fps, padding=1.5):
    """Seconds window (start, duration) around a rep's contact frame, start clamped to 0."""
    if not fps or fps <= 0:
        fps = 30.0
    center = (contact_frame or 0) / fps
    start = max(0.0, center - padding)
    return round(start, 3), round(padding * 2, 3)
```

- [ ] **Step 4: Add the route**

Place it after `api_posture` (the `/api/posture/<video_name>` route):
```python
@app.route('/api/posture-rep-clip/<video_name>', methods=['POST'])
def api_posture_rep_clip(video_name):
    """On-demand ffmpeg crop of a single rep window from the annotated posture video."""
    data = request.json or {}
    rep_id = data.get('rep_id')
    if rep_id is None:
        return jsonify({'error': '需要 rep_id'}), 400
    out_dir = OUTPUTS / video_name / 'posture'
    reps_path = out_dir / 'drill_reps.jsonl'
    if not reps_path.exists():
        return jsonify({'error': '没有 rep 数据'}), 404
    rep = None
    with open(reps_path, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            if r.get('rep_id') == rep_id:
                rep = r
                break
    if rep is None:
        return jsonify({'error': 'rep 不存在'}), 404
    video_file = out_dir / ('detect_' + video_name + '.mp4')
    if not video_file.exists():
        return jsonify({'error': '没有标注视频'}), 404
    fps = 30.0
    meta_path = out_dir / 'metadata.json'
    if meta_path.exists():
        try:
            with open(meta_path, encoding='utf-8') as f:
                fps = (json.load(f).get('video', {}) or {}).get('fps') or 30.0
        except Exception:
            fps = 30.0
    start, duration = _rep_clip_window(rep.get('contact_frame', 0), fps)
    clip_dir = out_dir / 'rep_clips'
    clip_dir.mkdir(exist_ok=True)
    out_file = clip_dir / ('rep_' + str(rep_id) + '.mp4')
    ffmpeg_bin = shutil.which('ffmpeg') or 'ffmpeg'
    try:
        subprocess.run([ffmpeg_bin, '-y', '-ss', str(start), '-i', str(video_file),
                        '-t', str(duration), '-c:v', 'libx264', '-preset', 'fast',
                        '-crf', '23', '-movflags', '+faststart', str(out_file)],
                       check=True, capture_output=True, timeout=120)
    except Exception as e:
        return jsonify({'error': 'ffmpeg 失败: ' + str(e)}), 500
    return jsonify({'ok': True,
                    'url': '/api/output/' + video_name + '/posture/rep_clips/rep_' + str(rep_id) + '.mp4'})
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_app_rep_clip.py -v`
Expected: PASS (5 tests). (The success/ffmpeg path is covered by dogfooding in Task 5, not a unit test.)

- [ ] **Step 6: Run the full suite**

Run: `./.venv/Scripts/python.exe -m pytest -q`
Expected: `156 passed` (151 baseline + 5 new).

- [ ] **Step 7: Commit**

```bash
git add app.py tests/test_app_rep_clip.py
git commit -m "feat(api): posture rep-clip route (contact-frame window + ffmpeg crop)"
```

---

### Task 5: Rep bounded scrubber + "Download this rep"

**Files:**
- Modify: `static/kestrel.js` (fetch `fps`; bounded playback binding; scrubber controls + download in `selectRep`)
- Modify: `static/kestrel.css` (scrubber controls + download button styles)

**Interfaces:**
- Consumes: `postureCtx` (Task 3), the `#posture-video` element, `#rep-scrubber` container, `POST /api/posture-rep-clip/<stem>` (Task 4), `posture/metadata.json` (`video.fps`).
- Produces: bounded playback of the selected rep window in `#posture-video`; scrubber controls (replay / play-pause / frame-step) in `#rep-scrubber`; a "Download this rep" button that calls the rep-clip route and downloads the returned file. Adds `REP_PAD` constant and `repWindow(rep)`.

- [ ] **Step 1: Fetch `fps` when posture results load**

In `renderPostureResults`, inside the `.then(function (d) { … })` success block, right after `postureCtx.reps = d.reps || [];`, add a metadata fetch that fills `postureCtx.fps`:
```js
      fetch('/api/output/' + stem + '/posture/metadata.json').then(function (r) { return r.ok ? r.json() : null; })
        .then(function (meta) { if (meta && meta.video && meta.video.fps) postureCtx.fps = meta.video.fps; }).catch(function () {});
```

- [ ] **Step 2: Add the window helper + bound-playback wiring** (place before `selectRep`)

```js
  var REP_PAD = 1.5;
  function repWindow(rep) {
    var fps = postureCtx.fps || 30; var center = (rep.contact_frame || 0) / fps;
    return { start: Math.max(0, center - REP_PAD), end: center + REP_PAD };
  }
  function bindRepWindow(rep) {
    var v = document.getElementById('posture-video'); if (!v) return;
    var w = repWindow(rep);
    v.currentTime = w.start;
    v.ontimeupdate = function () { if (v.currentTime >= w.end) { v.pause(); } };
    var play = v.play(); if (play && play.catch) play.catch(function () {});
  }
```

- [ ] **Step 3: Extend `selectRep` with scrubber controls + download** (replace the Task 3 `selectRep`)

```js
  function selectRep(reps, i) {
    postureCtx.sel = i; var rep = reps[i]; var zh = state.lang === 'zh';
    document.querySelectorAll('.rep-row').forEach(function (x) { x.classList.toggle('on', Number(x.getAttribute('data-i')) === i); });
    var bars = Object.keys(rep.per_metric || {}).map(function (k) { return metricBarHTML(k, rep.per_metric[k]); }).join('');
    var ws = (rep.weaknesses || []).map(function (w) { return '<li>' + (w.description || metricLabel(w.metric)) + '</li>'; }).join('');
    document.getElementById('rep-detail').innerHTML =
      '<h2>' + (zh?('第 ' + rep.rep_id + ' 次详情'):('Rep #' + rep.rep_id)) + '</h2>' +
      bars + (ws ? '<ul class="weak-list">' + ws + '</ul>' : '');
    // Scrubber controls under the video.
    var sc = document.getElementById('rep-scrubber');
    if (sc) {
      sc.innerHTML =
        '<button class="btn-ghost" id="sc-replay">⏮ ' + (zh?'重播本次':'Replay rep') + '</button>' +
        '<button class="btn-ghost" id="sc-back">−1f</button>' +
        '<button class="btn-ghost" id="sc-fwd">+1f</button>' +
        '<button class="btn-primary" id="sc-dl">⬇ ' + (zh?'下载本次':'Download this rep') + '</button>';
      document.getElementById('sc-replay').onclick = function () { bindRepWindow(rep); };
      var v = document.getElementById('posture-video'); var step = 1 / (postureCtx.fps || 30);
      document.getElementById('sc-back').onclick = function () { if (v) { v.pause(); v.currentTime = Math.max(0, v.currentTime - step); } };
      document.getElementById('sc-fwd').onclick = function () { if (v) { v.pause(); v.currentTime = v.currentTime + step; } };
      document.getElementById('sc-dl').onclick = function () { downloadRep(rep); };
    }
    bindRepWindow(rep);
  }
  function downloadRep(rep) {
    var zh = state.lang === 'zh'; var btn = document.getElementById('sc-dl');
    btn.disabled = true; btn.textContent = zh?'生成中…':'Preparing…';
    fetch('/api/posture-rep-clip/' + postureCtx.stem, { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({ rep_id: rep.rep_id }) })
      .then(function (r) { return r.json(); }).then(function (d) {
        btn.disabled = false; btn.textContent = '⬇ ' + (zh?'下载本次':'Download this rep');
        if (d.ok && d.url) { var a = document.createElement('a'); a.href = d.url; a.download = 'rep_' + rep.rep_id + '.mp4'; document.body.appendChild(a); a.click(); a.remove(); }
        else { alert(d.error || 'error'); } })
      .catch(function () { btn.disabled = false; btn.textContent = '⬇ ' + (zh?'下载本次':'Download this rep'); });
  }
```
(Note: `selectRep` now references `postureCtx.stem`, set in `renderPostureResults` Task 3.)

- [ ] **Step 4: Add scrubber CSS**

```css
.scrubber{display:flex;flex-wrap:wrap;gap:8px;margin-top:10px;align-items:center;}
.scrubber .btn-ghost{padding:6px 12px;font-size:13px;}
.scrubber .btn-primary{margin-left:auto;}
```

- [ ] **Step 5: Verify + commit**

Run: `node --check static/kestrel.js` → 0. Start app; open `IMG_1270` → **Drill** → select a rep → confirm the video jumps to the rep window and stops at its end, `⏮ Replay rep` re-plays the window, `−1f/+1f` step frames, and **Download this rep** produces and downloads `rep_<id>.mp4` (this exercises the Task 4 ffmpeg success path end-to-end). Grep served JS for `downloadRep`, `bindRepWindow`. Kill server.
```bash
git add static/kestrel.js static/kestrel.css
git commit -m "feat(ui): posture rep bounded scrubber + download-this-rep"
```

---

## Self-Review

- **Spec coverage (Phase 3):** Screen 3 Results routing + per-video open (Task 1) ✓; Match results — annotated video, rally summary + clip generation via `/api/clip`, position heatmap/scatter with graceful-missing handling, Technique Analysis (summary + stroke list + per-stroke metric bars + suggestions) (Task 2) ✓; Posture results — annotated video, drill summary, rep list + per-rep metric detail (Task 3) ✓; per-rep **bounded scrubber** + **on-demand crop** "Download this rep" incl. new `/api/posture-rep-clip` (Tasks 4–5) ✓; dual-mode Match|Drill toggle when a video has both (Task 1) ✓; bilingual throughout, offline-safe, light+dark via tokens, old `/` UI untouched ✓. **Deferred to Phase 4/5 by the locked scope:** Training Plan panel, Coach Report panel, Notes, Export — intentionally not in this plan.
- **Minor deviation from spec:** the spec says the wizard "auto-advances to Results on completion"; this plan instead shows a prominent **View Results** button on the completion screen (one click) alongside Back to Library — safer than yanking the user off the completion state. Same destination via `openResults`.
- **Placeholder scan:** Task 1 intentionally ships stub `renderMatchResults`/`renderPostureResults` bodies that Tasks 2–3 replace (each names its task, renders harmless text) so `renderResults` never throws mid-phase — sequenced, not a gap. All backend steps carry full test + impl code. No TBD/TODO.
- **Type consistency:** `state.resultsVideo/resultsMode/resultsModes` used identically across Tasks 1–5; `openResults(stem, mode, modes)` signature consistent (Task 1 producer; Task 1 card wiring + Task 1 wizard completion consumers); shared helpers `scoreHue`/`metricLabel`/`metricBarHTML` defined in Task 2 and consumed in Task 3; `postureCtx = {stem, reps, fps, sel}` defined in Task 3 and consumed in Task 5; `selectRep(reps, i)` signature consistent between Task 3 (defines) and Task 5 (replaces); `_rep_clip_window(contact_frame, fps, padding)` and the `{rep_id}` → `{ok, url}` contract match between Task 4 (producer) and Task 5 (consumer); score scale (0–100), `ideal_range` `[min,max]`, and `rep_id`/`contact_frame` field names match the verified data contracts.

## Notes for the executor

- Base commit for Phase 3 is `00cdf64` (Phase 2 tip).
- No JS test framework: front-end tasks verify with `node --check` + dogfooding against a running app; the backend task is TDD with pytest.
- Dogfood data: `IMG_1537` exercises the match graceful-degradation paths (real rally, empty heatmap/scatter, empty strokes); `IMG_1270` exercises full posture results + the dual-mode toggle (it is analyzed in both modes). To see a **populated** technique stroke list you must run a fresh match analysis that produces non-empty `strokes.jsonl` — none exists on disk yet.
- The rep-clip success path needs `ffmpeg` on PATH; the pytest suite deliberately does not exercise it (only validation/404s + the pure window helper), so tests stay green without ffmpeg.
- Deferred Phase-1 polish (escaping `v.name` in card rendering; negative-cache for failed thumbnails) remains out of scope — Task 1 escapes only the new `data-name` attribute value.
