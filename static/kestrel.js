window.Kestrel = (function () {
  'use strict';
  var state = { lang: localStorage.getItem('kestrel_lang') || 'zh',
                theme: localStorage.getItem('kestrel_theme') ||
                       (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'),
                screen: 'dashboard', resultsVideo: null, resultsMode: 'match', resultsModes: [] };
  function applyTheme() {
    document.documentElement.setAttribute('data-theme', state.theme);
  }
  var T = {
    zh: { brand: 'Kestrel', portal: '教练平台', nav_dashboard: '视频库',
          nav_new: '新建分析', nav_results: '结果', persona: 'Coach Lee', team: 'Team Falcons',
          theme_light: '浅色', theme_dark: '深色' },
    en: { brand: 'Kestrel', portal: 'Coach Portal', nav_dashboard: 'Dashboard',
          nav_new: 'New Analysis', nav_results: 'Results', persona: 'Coach Lee', team: 'Team Falcons',
          theme_light: 'Light', theme_dark: 'Dark' }
  };
  function t(key) { return (T[state.lang] && T[state.lang][key]) || key; }
  function setLang(lang) { state.lang = lang; localStorage.setItem('kestrel_lang', lang);
    document.documentElement.lang = lang; render(); }
  function setTheme(theme) { state.theme = theme; localStorage.setItem('kestrel_theme', theme);
    applyTheme(); renderSidebar(); }
  var lib = { videos: [], filter: { q: '', mode: 'all', status: 'all', sort: 'date' } };
  var wiz = { mode: 'match', video: null, step: 'mode', jobId: null, pollIv: null };
  var postureCtx = { stem: null, reps: [], fps: 30, sel: -1 };
  function loadDashboard() {
    Promise.all([
      fetch('/api/stats').then(function (r) { return r.json(); }),
      fetch('/api/videos').then(function (r) { return r.json(); })
    ]).then(function (res) {
      lib.stats = res[0]; lib.videos = res[1] || []; renderDashboard();
    }).catch(function () {
      document.getElementById('main').innerHTML = '<p class="error">' +
        (state.lang === 'zh' ? '加载失败' : 'Failed to load') + '</p>';
    });
  }
  function filteredVideos() {
    var f = lib.filter;
    var out = lib.videos.filter(function (v) {
      if (f.q && v.name.toLowerCase().indexOf(f.q.toLowerCase()) === -1) return false;
      if (f.mode === 'match' && !v.has_match) return false;
      if (f.mode === 'posture' && !v.has_posture) return false;
      if (f.status !== 'all' && v.status !== f.status) return false;
      return true;
    });
    out.sort(function (a, b) {
      return f.sort === 'name' ? a.name.localeCompare(b.name)
                               : (b.date || '').localeCompare(a.date || '');
    });
    return out;
  }
  function applyFilters(patch) { Object.assign(lib.filter, patch); renderCards(); }
  // Localized filter options: [value, zh, en]. Labels stay consistent with the
  // card badges (modeLabel / statusChip) so the same term names the same thing.
  var FILTER_DEFS = {
    mode:   [['all', '全部', 'All'], ['match', '比赛', 'Match'], ['posture', '训练', 'Drill']],
    status: [['all', '全部', 'All'], ['analyzed', '已完成', 'Analyzed'],
             ['court_set', '已标注', 'Court set'], ['new', '未分析', 'New']],
    sort:   [['date', '日期', 'Date'], ['name', '名称', 'Name']]
  };
  var FILTER_GROUP_LABEL = { mode: ['类型', 'Type'], status: ['状态', 'Status'], sort: ['排序', 'Sort'] };
  function _loc(pair, zhIdx, enIdx) { return state.lang === 'zh' ? pair[zhIdx] : pair[enIdx]; }
  function segGroup(key) {
    var cur = lib.filter[key];
    var label = _loc(FILTER_GROUP_LABEL[key], 0, 1);
    var btns = FILTER_DEFS[key].map(function (o) {
      var on = o[0] === cur;
      return '<button class="seg-btn' + (on ? ' on' : '') + '" role="tab" aria-selected="' + on +
        '" data-fkey="' + key + '" data-fval="' + o[0] + '">' + _loc(o, 1, 2) + '</button>';
    }).join('');
    return '<div class="seg-group"><span class="seg-label">' + label + '</span>' +
      '<div class="seg" role="tablist" aria-label="' + label + '">' + btns + '</div></div>';
  }
  function searchHTML() {
    var ph = state.lang === 'zh' ? '搜索视频…' : 'Search videos…';
    var q = lib.filter.q || '';
    return '<div class="search">' +
      '<svg class="search-ic" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="11" cy="11" r="7"/><line x1="21" y1="21" x2="16.5" y2="16.5"/></svg>' +
      '<input id="f-q" type="search" aria-label="' + ph + '" placeholder="' + ph + '" value="' + q.replace(/"/g, '&quot;') + '">' +
      '<button class="search-clear' + (q ? '' : ' hide') + '" id="f-clear" aria-label="' +
        (state.lang === 'zh' ? '清除' : 'Clear') + '">&times;</button></div>';
  }
  function renderFilterBar() {
    var bar = document.getElementById('filter-bar'); if (!bar) return;
    bar.innerHTML = searchHTML() + segGroup('mode') + segGroup('status') + segGroup('sort');
    var q = bar.querySelector('#f-q'), clr = bar.querySelector('#f-clear');
    q.oninput = function (e) { clr.classList.toggle('hide', !e.target.value); applyFilters({ q: e.target.value }); };
    clr.onclick = function () { applyFilters({ q: '' }); renderFilterBar(); bar.querySelector('#f-q').focus(); };
    bar.querySelectorAll('.seg-btn').forEach(function (b) {
      b.onclick = function () {
        var patch = {}; patch[b.getAttribute('data-fkey')] = b.getAttribute('data-fval');
        applyFilters(patch); renderFilterBar();
      };
    });
  }
  function modeLabel(v) { return v.has_posture && !v.has_match ? (state.lang==='zh'?'训练':'Drill')
                                                               : (state.lang==='zh'?'比赛':'Match'); }
  function statusChip(v) {
    var map = { analyzed:['已完成','Analyzed','var(--good)'],
                court_set:['已标注','Court set','var(--mid)'],
                new:['未分析','New','var(--faint)'] };
    var m = map[v.status] || map.new;
    return '<span class="chip" style="color:' + m[2] + '">' + (state.lang==='zh'?m[0]:m[1]) + '</span>';
  }
  function renderDashboard() {
    var s = lib.stats || {};
    var tiles = [['videos', state.lang==='zh'?'视频总数':'Videos'],
                 ['analyzed', state.lang==='zh'?'已分析':'Analyzed'],
                 ['rallies', state.lang==='zh'?'回合数':'Rallies'],
                 ['avg_technique_score', state.lang==='zh'?'技术均分':'Avg Score']];
    var main = document.getElementById('main');
    main.innerHTML =
      '<div class="page-head"><h1>' + (state.lang==='zh'?'视频库':'Video Library') + '</h1>' +
        '<button class="btn-primary" id="go-new">+ ' + (state.lang==='zh'?'新建分析':'New Analysis') + '</button></div>' +
      '<div class="stat-row">' + tiles.map(function (t2) {
        var val = s[t2[0]]; if (val === null || val === undefined) val = '—';
        return '<div class="stat"><div class="stat-label mono">' + t2[1] +
               '</div><div class="stat-val mono">' + val + '</div></div>';
      }).join('') + '</div>' +
      '<div class="filter-bar" id="filter-bar"></div>' +
      '<div id="cards" class="card-grid"></div>';
    renderFilterBar();
    renderCards();
    var gn = document.getElementById('go-new'); if (gn) gn.onclick = function () { setScreen('new'); };
  }
  function renderCards() {
    var wrap = document.getElementById('cards'); if (!wrap) return;
    var vids = filteredVideos();
    if (!vids.length) { wrap.innerHTML = '<p class="muted">' +
      (state.lang==='zh'?'暂无视频':'No videos') + '</p>'; return; }
    wrap.innerHTML = vids.map(function (v) {
      var nm = (v.name || '').replace(/"/g, '&quot;');
      // Single-quote the CSS url(): an UNQUOTED url() cannot contain spaces, so a
      // stem like "Dji 2026 0010 D" made the whole declaration invalid and the
      // parser dropped it, leaving a blank thumbnail. The server now percent-encodes
      // the stem, and single quotes keep this valid even inside the style="..."
      // attribute below (double quotes would terminate the attribute).
      var thumb = v.thumb ? "background-image:url('" + v.thumb + "')" : '';
      var dur = v.duration_sec ? Math.floor(v.duration_sec/60)+':'+('0'+Math.round(v.duration_sec%60)).slice(-2) : '';
      return '<div class="vcard" role="button" tabindex="0" data-name="' + nm + '"><div class="vthumb" style="' + thumb + '">' +
        '<span class="vmode mono">' + modeLabel(v) + '</span>' +
        '<span class="vdur mono">' + dur + '</span>' +
        '<button class="vdel" data-del="' + nm + '" aria-label="' + (state.lang==='zh'?'删除':'Delete') + '">🗑</button></div>' +
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
    wrap.querySelectorAll('.vdel').forEach(function (b) {
      b.onclick = function (e) {
        e.stopPropagation();
        var name = b.getAttribute('data-del');
        openDeleteModal(name, 'all', function () {
          if (state.resultsVideo === name) { state.resultsVideo = null; }
          loadDashboard();
        });
      };
    });
  }
  function renderSidebar() {
    var s = document.getElementById('sidebar');
    s.className = 'sidebar';
    s.innerHTML =
      '<div class="brand"><img src="/static/img/kestrel-mark.svg" width="24" height="24">' +
        '<span class="brand-name">' + t('brand') + '</span></div>' +
      '<div class="portal">' + t('portal') + '</div>' +
      '<nav class="nav">' +
        '<button class="nav-item" data-screen="dashboard">' + t('nav_dashboard') + '</button>' +
        '<button class="nav-item" data-screen="new">' + t('nav_new') + '</button>' +
        (state.resultsVideo ? '<button class="nav-item" data-screen="results">' + t('nav_results') + '</button>' : '') +
      '</nav><div class="spacer"></div>' +
      '<div class="side-toggles">' +
        '<button data-lang="zh">中</button><button data-lang="en">EN</button>' +
        '<button data-theme="light">' + t('theme_light') + '</button>' +
        '<button data-theme="dark">' + t('theme_dark') + '</button>' +
      '</div>' +
      '<div class="persona"><div class="avatar mono">KL</div>' +
        '<div><div class="persona-name">' + t('persona') + '</div>' +
        '<div class="persona-team">' + t('team') + '</div></div></div>';
    s.querySelectorAll('[data-lang]').forEach(function (b) {
      b.onclick = function () { setLang(b.getAttribute('data-lang')); }; });
    s.querySelectorAll('[data-theme]').forEach(function (b) {
      b.onclick = function () { setTheme(b.getAttribute('data-theme')); }; });
    s.querySelectorAll('[data-screen]').forEach(function (b) {
      b.onclick = function () { setScreen(b.getAttribute('data-screen')); };
      b.classList.toggle('active', b.getAttribute('data-screen') === state.screen);
    });
  }
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
      img.onerror = function () {
        panel.innerHTML = '<div class="court-fail">⚠️ ' + (zh?'无法加载标注图片，请返回重新选择视频':'Cannot load the annotation image — go back and re-select the video') + '</div>';
      };
      var c = document.getElementById('anno');
      c.onclick = function (e) { if (!annoImg || clicks.length >= 4) return; var r = c.getBoundingClientRect();
        var cx = (e.clientX - r.left) * (c.width / r.width), cy = (e.clientY - r.top) * (c.height / r.height);
        clicks.push([Math.round(cx / sx), Math.round(cy / sy)]); redraw(); updateSteps(); };
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
          else { btn.disabled = false; btn.textContent = zh?'提交':'Submit'; alert(zh ? (d.error || '提交失败') : 'Submit failed'); } })
        .catch(function () { btn.disabled = false; btn.textContent = zh?'提交':'Submit'; alert(zh ? '网络错误，请重试' : 'Network error — please try again'); });
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
  function fieldRow(labelZh, labelEn, controlHTML) {
    return '<label class="field"><span class="field-label">' + (state.lang==='zh'?labelZh:labelEn) + '</span>' + controlHTML + '</label>';
  }
  function selectHTML(id, opts) {
    return '<select id="' + id + '" class="field-select">' + opts.map(function (o) {
      return '<option value="' + o[0] + '">' + (state.lang==='zh'?o[1]:o[2]) + '</option>'; }).join('') + '</select>';
  }
  function renderStepConfig(body) {
    var zh = state.lang === 'zh';
    var isPosture = wiz.mode === 'posture';
    var fields;
    if (isPosture) {
      fields =
        fieldRow('击球类型','Stroke', selectHTML('cfg-stroke', [['high_clear','高远球','High clear'],['smash','杀球','Smash'],['drop_shot','吊球','Drop shot'],['serve','发球','Serve']])) +
        fieldRow('持拍手','Dominant hand', selectHTML('cfg-hand', [['right','右手','Right'],['left','左手','Left']])) +
        fieldRow('姿态模型','Pose model', selectHTML('cfg-pose', [['yolo-pose','YOLO Pose (快)','YOLO Pose (fast)'],['rtmpose','RTMPose (准)','RTMPose (accurate)'],['rtmo','RTMO','RTMO']])) +
        fieldRow('教练报告文字润色','Report polish', selectHTML('cfg-llm', [['off','关闭','Off'],['on','开启','On']]));
    } else {
      fields =
        fieldRow('语言','Language', selectHTML('cfg-lang', [['zh','中文','Chinese'],['en','English','English']])) +
        fieldRow('姿态模型','Pose model', selectHTML('cfg-pose', [['yolo-pose','YOLO Pose (快)','YOLO Pose (fast)'],['rtmpose','RTMPose (准)','RTMPose (accurate)']])) +
        fieldRow('分析质量','Analysis quality', selectHTML('cfg-quality', [['accurate','精确（完整分析）','Accurate (full analytics)'],['fast','快速（抽帧预览）','Fast (sampled preview)']])) +
        '<label class="field field-check"><input type="checkbox" id="cfg-tech" checked><span class="field-label">' + (zh?'技术分析':'Technique analysis') + '</span></label>';
    }
    body.innerHTML = '<div class="cfg-form">' + fields + '</div>' +
      (isPosture ? '<div id="cfg-model-chips" class="chip-row"></div>' : '') +
      '<div class="wiz-actions"><button class="btn-ghost" id="wiz-back">' + (zh?'返回':'Back') + '</button>' +
        '<button class="btn-primary" id="wiz-start">' + (zh?'开始分析':'Start Analysis') + '</button></div>';
    document.getElementById('wiz-back').onclick = function () { goStep(wiz.mode === 'match' ? 'court' : 'upload'); };
    document.getElementById('wiz-start').onclick = startAnalysis;
    // Availability chips: non-blocking, so a fetch failure just renders nothing.
    if (isPosture) {
      fetch('/api/models').then(function (r) { return r.ok ? r.json() : null; }).then(function (d) {
        var el = document.getElementById('cfg-model-chips');
        if (!el || !d) return;
        var rktOn = !!d.racket, qmOn = !!d.quality, liftOn = !!d.lift;
        el.innerHTML =
          '<span class="chip" style="color:' + (rktOn ? 'var(--good)' : 'var(--faint)') + '">' +
            (zh ? (rktOn ? '球拍检测模型 ✓' : '球拍检测模型 未安装') : (rktOn ? 'Racket model ✓' : 'Racket model not installed')) +
          '</span>' +
          '<span class="chip" style="color:' + (qmOn ? 'var(--good)' : 'var(--faint)') + '">' +
            (zh ? (qmOn ? 'AI 评分模型 ✓' : 'AI 评分模型 未安装') : (qmOn ? 'AI scoring model ✓' : 'AI scoring model not installed')) +
          '</span>' +
          '<span class="chip" style="color:' + (liftOn ? 'var(--good)' : 'var(--faint)') + '">' +
            (zh ? (liftOn ? '3D 提升模型 ✓' : '3D 提升模型 未安装') : (liftOn ? '3D lifting model ✓' : '3D lifting model not installed')) +
          '</span>';
      }).catch(function () {});
    }
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
        pose_family: document.getElementById('cfg-pose').value, analyze_technique: document.getElementById('cfg-tech').checked,
        analysis_quality: document.getElementById('cfg-quality').value };
    }
    fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) })
      .then(function (r) { return r.json(); })
      .then(function (d) { if (d.ok) { wiz.jobId = d.job_id; goStep('progress'); }
        else { btn.disabled = false; btn.textContent = state.lang==='zh'?'开始分析':'Start Analysis'; alert(state.lang==='zh' ? (d.error || '启动失败') : 'Failed to start analysis'); } })
      .catch(function () { btn.disabled = false; btn.textContent = state.lang==='zh'?'开始分析':'Start Analysis'; alert(state.lang==='zh' ? '网络错误，请重试' : 'Network error — please try again'); });
  }
  var STAGE_LABELS = {
    loading: ['加载模型', 'Loading model'], analyzing: ['分析动作', 'Analyzing'],
    initializing: ['初始化', 'Initializing'],
    court_setup: ['球场设置', 'Court setup'], visualizing: ['生成可视化', 'Visualizing'],
    scoring: ['评分', 'Scoring'], report: ['生成报告', 'Building report'],
    encoding: ['转码视频', 'Encoding video'], done: ['完成', 'Done'], error: ['失败', 'Failed']
  };
  function progressStages() {
    return wiz.mode === 'posture' ? ['loading','analyzing','scoring','report','encoding','done']
                                  : ['initializing','court_setup','analyzing','visualizing','encoding','done'];
  }
  function renderStepProgress(body) {
    var zh = state.lang === 'zh';
    body.innerHTML =
      '<div class="prog-wrap"><div class="prog-track"><div class="prog-fill" id="prog-fill"></div></div>' +
        '<div class="prog-pct mono" id="prog-pct">0%</div></div>' +
      '<ul class="stage-list" id="stage-list"></ul>' +
      '<div id="wiz-heartbeat" class="wiz-heartbeat"></div>' +
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
    if (wiz.pollIv) { clearInterval(wiz.pollIv); }
    var fails = 0;
    var iv = wiz.pollIv = setInterval(function () {
      if (!document.getElementById('prog-fill')) { clearInterval(iv); if (wiz.pollIv === iv) { wiz.pollIv = null; } return; }
      fetch(url).then(function (r) { return r.json(); }).then(function (d) {
        var fill = document.getElementById('prog-fill'); if (!fill) { return; }
        fails = 0;
        var pct = d.progress || 0;
        fill.style.width = pct + '%';
        document.getElementById('prog-pct').textContent = pct + '%';
        renderStages(d.stage || 'analyzing');
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
        if (d.status === 'completed') {
          clearInterval(iv); if (wiz.pollIv === iv) { wiz.pollIv = null; }
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
          clearInterval(iv); if (wiz.pollIv === iv) { wiz.pollIv = null; } renderStages('error');
          document.getElementById('prog-actions').innerHTML =
            '<div class="prog-err">' + (d.message || (zh?'分析失败':'Analysis failed')) + '</div>' +
            '<button class="btn-ghost" id="prog-retry">' + (zh?'返回设置':'Back to Config') + '</button>';
          document.getElementById('prog-retry').onclick = function () { goStep('config'); };
        }
      }).catch(function () {
        fails += 1;
        if (fails >= 3) {
          clearInterval(iv); if (wiz.pollIv === iv) { wiz.pollIv = null; }
          var pa = document.getElementById('prog-actions');
          if (pa) { pa.innerHTML = '<div class="prog-err">' + (zh?'状态查询失败':'Status check failed') + '</div>'; }
        }
      });
    }, 1500);
  }
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
  var METRIC_LABELS = {
    elbow_extension:['肘部伸展','Elbow extension'], trunk_rotation:['躯干旋转','Trunk rotation'],
    wrist_flexion:['手腕屈曲','Wrist flexion'], knee_flexion:['膝盖弯曲','Knee flexion'],
    hip_shoulder_separation:['髋肩分离','Hip–shoulder separation'], weight_transfer:['重心转移','Weight transfer']
  };
  function metricLabel(k) { var m = METRIC_LABELS[k]; return m ? (state.lang==='zh'?m[0]:m[1]) : String(k).replace(/_/g,' '); }
  var STROKE_TYPE_LABELS = {
    high_clear: ['高远球', 'High clear'], smash: ['杀球', 'Smash'],
    drop_shot: ['吊球', 'Drop shot'], serve: ['发球', 'Serve']
  };
  function strokeTypeLabel(k) { var m = STROKE_TYPE_LABELS[k]; return m ? (state.lang==='zh'?m[0]:m[1]) : String(k).replace(/_/g,' '); }
  // Coarse stroke-type vocabulary from the BST rally labeler (Task 6 outputs/<stem>/strokes.json),
  // distinct from STROKE_TYPE_LABELS above (which covers the biomechanics posture-drill stroke set).
  var STROKE_COARSE_LABELS = {
    serve: ['发球', 'Serve'], clear: ['高远球', 'Clear'], smash: ['杀球', 'Smash'],
    drop: ['吊球', 'Drop'], drive: ['平抽球', 'Drive'], net: ['网前球', 'Net']
  };
  var STROKE_COARSE_ORDER = ['serve', 'clear', 'smash', 'drop', 'drive', 'net'];
  function strokeCoarseLabel(k) {
    var m = STROKE_COARSE_LABELS[k];
    return m ? (state.lang === 'zh' ? m[0] : m[1]) : String(k == null ? '' : k).replace(/_/g, ' ');
  }
  function weaknessLineHTML(w) {
    var zh = state.lang === 'zh';
    if (w.description && (!zh || !w.metric)) return '<li>' + w.description + '</li>';
    if (!w.metric) return '';
    var parts = [metricLabel(w.metric)];
    if (w.measured !== null && w.measured !== undefined) parts.push((zh ? '实测 ' : 'measured ') + (Math.round(w.measured * 10) / 10));
    if (Array.isArray(w.ideal_range) && w.ideal_range.length >= 2) parts.push((zh ? '理想 ' : 'ideal ') + w.ideal_range[0] + '–' + w.ideal_range[1]);
    return '<li>' + parts.join(' · ') + '</li>';
  }
  function scoreHue(s) { return s >= 70 ? 'var(--good)' : (s >= 40 ? 'var(--mid)' : 'var(--bad)'); }
  function scoreChipHTML(score) {
    var sc = (score === null || score === undefined) ? null : Math.round(score);
    return '<span class="score-chip mono" style="background:' + (sc === null ? 'var(--faint)' : scoreHue(sc)) + '">' + (sc === null ? '—' : sc) + '</span>';
  }
  function metricBarHTML(key, m) {
    var score = Math.max(0, Math.min(100, Number(m.score) || 0));
    var measured = (m.measured === null || m.measured === undefined) ? '—' : (Math.round(m.measured * 10) / 10);
    var ideal = (m.ideal_range && m.ideal_range.length === 2) ? (m.ideal_range[0] + '–' + m.ideal_range[1]) : '';
    return '<div class="metric"><div class="metric-head"><span class="metric-name">' + metricLabel(key) + '</span>' +
      '<span class="metric-val mono">' + measured + (ideal ? ' <span class="metric-ideal">(' + (state.lang==='zh'?'理想':'ideal') + ' ' + ideal + ')</span>' : '') + '</span></div>' +
      '<div class="metric-track"><div class="metric-fill" style="width:' + score + '%;background:' + scoreHue(score) + '"></div></div></div>';
  }
  function renderMatchResults(body) {
    var zh = state.lang === 'zh'; var stem = state.resultsVideo;
    var vurl = '/api/output/' + stem + '/detect_' + stem + '.mp4';
    var heat = '/api/output/' + stem + '/position_visualizations/heatmaps/match_heatmap.png';
    var scat = '/api/output/' + stem + '/position_visualizations/scatter_plots/match_scatter.png';
    body.innerHTML =
      '<div class="res-grid"><div><video class="res-video" controls src="' + vurl + '"></video></div>' +
        '<div><div class="res-section" id="rally-box"><h2>' + (zh?'回合':'Rallies') + '</h2>' +
          '<p class="muted" id="rally-info">' + (zh?'加载中…':'Loading…') + '</p>' +
          '<button class="btn-primary" id="clip-btn" disabled>' + (zh?'生成回合剪辑':'Generate clips') + '</button> ' +
          '<button class="btn-ghost danger-link" id="clips-del">🗑 ' + (zh?'删除剪辑':'Delete clips') + '</button>' +
          '<div id="clip-list" class="clip-list"></div></div>' +
          '<div class="res-section" id="dist-box" style="display:none"><h3 class="res-subhead">' + (zh?'击球分布':'Stroke distribution') + '</h3>' +
            '<p class="mono" id="dist-list"></p></div></div></div>' +
      '<div class="res-section" id="timeline-box" style="display:none"><h2>' + (zh?'击球类型时间线':'Stroke timeline') + '</h2>' +
        '<div id="stroke-timeline" class="stroke-timeline"></div></div>' +
      '<div class="res-section"><h2>' + (zh?'位置可视化':'Position visualization') + '</h2>' +
        '<div class="viz-row"><div class="viz-cell" id="viz-heat"><img class="res-viz" src="' + heat + '" alt="heatmap"><div class="viz-cap mono">' + (zh?'热力图':'Heatmap') + '</div></div>' +
          '<div class="viz-cell" id="viz-scat"><img class="res-viz" src="' + scat + '" alt="scatter"><div class="viz-cap mono">' + (zh?'散点图':'Scatter') + '</div></div></div></div>' +
      '<div class="res-section" id="tech-box"><h2>' + (zh?'技术分析':'Technique analysis') + '</h2>' +
        '<p class="muted" id="tech-status">' + (zh?'加载中…':'Loading…') + '</p>' +
        '<div id="tech-summary"></div><div id="tech-strokes" class="stroke-list"></div><div id="tech-detail" class="rep-detail"></div></div>' +
      '<div class="res-section" id="plan-box"></div>' +
      '<div class="res-section"><button class="btn-ghost danger-link" id="match-del">🗑 ' + (zh?'删除比赛结果':'Delete match results') + '</button></div>';
    renderPlanPanel(stem, 'match');
    document.getElementById('match-del').onclick = function () {
      openDeleteModal(stem, 'match', function () { afterModeDelete('match'); });
    };
    document.getElementById('clips-del').onclick = function () {
      openDeleteModal(stem, 'clips', function () { renderResults(); });
    };

    // Heatmap/scatter: hide the cell and show a note if the image is missing.
    ['viz-heat','viz-scat'].forEach(function (id) {
      var cell = document.getElementById(id); var img = cell.querySelector('img');
      img.onerror = function () { cell.innerHTML = '<div class="viz-missing">' + (zh?'暂无可视化':'Not available') + '</div>'; };
    });

    // Rally summary + clip generation.
    fetch('/api/output/' + stem + '/rally_segments.json').then(function (r) { return r.ok ? r.json() : null; }).then(function (d) {
      var n = d && d.rallies ? d.rallies.length : 0;
      document.getElementById('rally-info').textContent = zh ? (n + ' 个回合') : (n + (n === 1 ? ' rally detected' : ' rallies detected'));
      var btn = document.getElementById('clip-btn'); btn.disabled = n === 0;
    }).catch(function () { document.getElementById('rally-info').textContent = zh?'暂无回合数据':'No rally data'; document.getElementById('clip-btn').disabled = true; });
    document.getElementById('clip-btn').onclick = function () {
      var btn = document.getElementById('clip-btn'); btn.disabled = true; btn.textContent = zh?'生成中…':'Generating…';
      fetch('/api/clip', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({ video: stem, mode:'highlights', padding:1.5 }) })
        .then(function (r) { return r.json(); }).then(function (d) {
          btn.textContent = zh?'生成回合剪辑':'Generate clips'; btn.disabled = false;
          if (d.ok && d.clips) { document.getElementById('clip-list').innerHTML = d.clips.map(function (c) {
            return '<a class="clip-item" href="' + c.url + '" download>⬇ ' + c.name + ' <span class="mono">' + c.size_mb + 'MB</span></a>'; }).join(''); }
          else { alert(zh ? (d.error || '生成失败') : 'Clip generation failed'); } })
        .catch(function () { btn.textContent = zh?'生成回合剪辑':'Generate clips'; btn.disabled = false; alert(zh ? '网络错误，请重试' : 'Network error — please try again'); });
    };

    // Rally stroke-type timeline + distribution (BST; Task 6 outputs/<stem>/strokes.json).
    // Presence-keyed: this file only exists when BST weights were present at analysis
    // time, so a 404/absent/malformed response must render nothing (no error, no empty box).
    fetch('/api/output/' + stem + '/strokes.json').then(function (r) { return r.ok ? r.json() : null; }).then(function (d) {
      if (!d) return;
      var strokes = Array.isArray(d.strokes) ? d.strokes : [];
      if (d && d.shuttle_source === 'tracknet') {
        var prov = document.getElementById('dist-list');
        if (prov) {
          var note = (state.lang === 'zh' ? '密集羽毛球追踪：TrackNetV3' : 'Dense shuttle tracking: TrackNetV3');
          prov.insertAdjacentHTML('afterend', '<div class="prov-note faint">' + note + '</div>');
        }
      }
      if (strokes.length) {
        document.getElementById('stroke-timeline').innerHTML = strokes.map(function (s, i) {
          var uncertain = !!(s && s.uncertain);
          var label = strokeCoarseLabel(s && s.stroke);
          var title = uncertain ? ' title="' + (zh ? '不确定' : 'Uncertain') + '"' : '';
          var item = '<span class="timeline-item' + (uncertain ? ' muted' : '') + '"' + title + '>' + label + '</span>';
          return i < strokes.length - 1 ? item + '<span class="timeline-arrow">→</span>' : item;
        }).join('');
        var tbox = document.getElementById('timeline-box'); if (tbox) tbox.style.display = '';
      }
      var dist = (d.distribution && typeof d.distribution === 'object') ? d.distribution : null;
      if (dist) {
        // "uncertain" is already shown muted in the timeline above; exclude it here
        // so it doesn't fall through strokeCoarseLabel() untranslated in the distribution line.
        var keys = STROKE_COARSE_ORDER.filter(function (k) { return dist[k] > 0; })
          .concat(Object.keys(dist).filter(function (k) { return k !== 'uncertain' && STROKE_COARSE_ORDER.indexOf(k) === -1 && dist[k] > 0; }));
        if (keys.length) {
          document.getElementById('dist-list').textContent = keys.map(function (k) { return strokeCoarseLabel(k) + ' ' + dist[k]; }).join(' · ');
          var dbox = document.getElementById('dist-box'); if (dbox) dbox.style.display = '';
        }
      }
    }).catch(function () {});

    // Technique analysis.
    fetch('/api/technique/' + stem).then(function (r) { return r.ok ? r.json() : null; }).then(function (d) {
      var status = document.getElementById('tech-status');
      if (!d || !d.summary || !d.summary.stroke_count) { status.textContent = zh?'暂无技术分析数据':'No technique data yet'; return; }
      status.style.display = 'none';
      var s = d.summary;
      var chips = Object.keys(s.by_type || {}).map(function (k) {
        var bt = s.by_type[k]; return '<span class="chip-metric"><b>' + strokeTypeLabel(k) + '</b> ' + bt.count + '× · ' + (bt.avg_score == null ? '—' : Math.round(bt.avg_score)) + '</span>'; }).join('');
      var weak = (s.recurring_weaknesses || []).slice(0,3).map(function (w) { return '<li>' + metricLabel(w.metric) + ' ×' + w.count + '</li>'; }).join('');
      document.getElementById('tech-summary').innerHTML =
        '<div class="tech-sum"><div class="mono">' + (zh?'击球数':'Strokes') + ': ' + s.stroke_count + '</div>' +
        '<div class="chip-row">' + chips + '</div>' + (weak ? '<div class="weak"><span class="muted">' + (zh?'常见问题':'Recurring') + '</span><ul>' + weak + '</ul></div>' : '') + '</div>';
      var strokes = d.strokes || [];
      document.getElementById('tech-strokes').innerHTML = strokes.map(function (st, i) {
        return '<button class="stroke-row" data-i="' + i + '"><span class="stroke-type">' + strokeTypeLabel(st.stroke_type) + '</span>' +
          scoreChipHTML(st.overall_score) + '</button>'; }).join('');
      document.querySelectorAll('.stroke-row').forEach(function (b) { b.onclick = function () {
        document.querySelectorAll('.stroke-row').forEach(function (x) { x.classList.remove('on'); }); b.classList.add('on');
        var st = strokes[Number(b.getAttribute('data-i'))];
        var bars = Object.keys(st.per_metric || {}).map(function (k) { return metricBarHTML(k, st.per_metric[k]); }).join('');
        var ws = (st.weaknesses || []).map(weaknessLineHTML).join('');
        document.getElementById('tech-detail').innerHTML = bars + (ws ? '<ul class="weak-list">' + ws + '</ul>' : '');
      }; });
    }).catch(function () { document.getElementById('tech-status').textContent = zh?'技术分析加载失败':'Failed to load technique'; });
  }
  // Model-usage provenance for a posture run: which optional trained models (racket
  // detector, AI quality scorer) actually ran, so a silently-degraded run (stale
  // server, missing weights) is visible instead of invisible. '' when metadata is
  // missing/old (pre-dates these blocks) or both blocks are absent.
  function provenanceHTML(meta) {
    if (!meta) return '';
    var zh = state.lang === 'zh';
    var racket = (meta.racket && typeof meta.racket === 'object') ? meta.racket : null;
    var quality = (meta.quality && typeof meta.quality === 'object') ? meta.quality : null;
    if (!racket && !quality) return '';
    var lines = [];
    if (racket) {
      if (racket.model) {
        var det = racket.detected_frames || 0, inf = racket.inferred_frames || 0, total = det + inf;
        lines.push(zh ? ('球拍检测：' + det + '/' + total + ' 帧（其余手腕推断）')
                      : ('Racket detection: ' + det + '/' + total + ' frames (rest wrist-inferred)'));
      } else {
        lines.push(zh ? '球拍检测：未启用（使用手腕推断）' : 'Racket detection: off (wrist inference)');
      }
    }
    if (quality) {
      if (quality.model) {
        var scored = quality.scored_reps || 0;
        lines.push(zh ? ('AI 评分：已启用（' + scored + ' 次动作已评分）')
                      : ('AI scoring: on (' + scored + ' reps scored)'));
      } else {
        lines.push(zh ? 'AI 评分：未启用' : 'AI scoring: off');
      }
    }
    var reps = (meta.reps && typeof meta.reps === 'object') ? meta.reps : null;
    if (reps && reps.gated && reps.filtered_non_overhead > 0) {
      // Floor case: an overhead drill (high clear / smash / drop shot) excluded a
      // swing that never got above the shoulder. The old copy said "only full
      // overhead clears counted", which stopped being true once smash and drop
      // shot were gated too.
      lines.push(zh ? ('已排除 ' + reps.filtered_non_overhead + ' 个非头顶挥拍（本项仅统计头顶动作）')
        : (reps.filtered_non_overhead + ' non-overhead swing(s) excluded (this drill counts overhead swings only)'));
    }
    if (reps && reps.gated && reps.filtered_overhead > 0) {
      // Ceiling case: a serve drill excluded a swing for BEING overhead -- the
      // opposite of the message above.
      lines.push(zh ? ('已排除 ' + reps.filtered_overhead + ' 个头顶挥拍（本项仅统计低手动作）')
        : (reps.filtered_overhead + ' overhead swing(s) excluded (this drill counts underarm swings only)'));
    }
    return '<div class="provenance muted mono">' + lines.map(function (l) { return '<div>' + l + '</div>'; }).join('') + '</div>';
  }
  // Legend for the drill-summary panel: static bilingual explanations of what each
  // summary item and per-metric score means, plus a numeric ideal-range/weight table
  // rendered FROM the loaded rep data so it always matches what this run actually used
  // (ranges differ per stroke type -- badminton_analysis/analysis/reference_ranges.py).
  // The static text never depends on rep data; only the table does, and it is omitted
  // gracefully when there are no reps yet. Collapsed by default (#legend-panel hidden).
  var LEGEND_METRIC_ORDER = ['elbow_extension', 'trunk_rotation', 'wrist_flexion',
    'knee_flexion', 'hip_shoulder_separation', 'weight_transfer'];
  var METRIC_MEANING = {
    elbow_extension: [
      '触球瞬间手肘的伸展角度（肩–肘–腕夹角）；角度越大，手臂伸得越直。',
      'Elbow angle at contact (shoulder-elbow-wrist); larger means a straighter arm.'
    ],
    trunk_rotation: [
      '肩线相对画面水平线的角度，用作躯干旋转幅度的替代指标。',
      'Angle of the line between the shoulders, relative to horizontal in the frame — a stand-in for how far the trunk has rotated.'
    ],
    wrist_flexion: [
      '手腕处的角度（前臂–腕–拍头连线夹角），代表触球瞬间手腕/拍面的屈曲程度。',
      'Angle at the wrist (forearm-wrist-racket-head line) — the degree of wrist/racket-face flexion at contact.'
    ],
    knee_flexion: [
      '膝盖处的角度（髋–膝–踝夹角）；角度越小，屈膝越深、蹬地越充分。',
      'Knee angle (hip-knee-ankle); a smaller angle means a deeper bend and more leg load.'
    ],
    hip_shoulder_separation: [
      '肩线与髋线两个角度之差（折算到 0–90° 范围内），代表挥拍时肩部相对髋部多转了多少。',
      'Difference between the shoulder-line and hip-line angles, folded into 0–90 degrees — how far the shoulders lead the hips.'
    ],
    weight_transfer: [
      '从动作起始到触球瞬间，髋部中点的水平位移量除以肩宽得到的比值；数值越大，重心前移越多。',
      'Horizontal hip-center displacement from the start of the rep to contact, divided by shoulder width; larger means more forward transfer.'
    ]
  };
  function legendSummaryHTML(zh) {
    var items = [
      [zh ? '次数' : 'Reps',
       zh ? '本次训练检测到的挥拍次数（含姿态识别失败、无法评分的次数）。'
          : 'Number of swings detected in this drill run (includes reps that could not be scored, e.g. failed pose detection).'],
      [zh ? '最终评分' : 'Final score',
       zh ? '各次动作最终评分（0–100，即加权综合得分）的平均值。'
          : 'Average of the per-rep final scores (0–100, the weighted biomechanical score).'],
      [zh ? '一致性' : 'Consistency',
       zh ? '各次综合得分的标准差；数值越小，动作越稳定一致。'
          : 'Standard deviation of the per-rep overall scores; a smaller number means more consistent reps.'],
      [zh ? '最佳 · 最差' : 'Best · Worst',
       zh ? '最终评分最高的一次和最低的一次。'
          : 'The highest-scoring and lowest-scoring reps by final score.'],
      [zh ? 'AI 评分（实验）' : 'AI score (beta)',
       zh ? '独立训练的动作质量模型给出的评分（见下方「评分方法」）；实验性，未计入最终评分，仅供参考，仅在该模型可用时显示。'
          : 'Score from a separately trained form-quality model (see Scoring below); experimental and not part of the final score — shown for reference only, and only when that model is available.']
    ];
    return '<ul class="plan-list">' + items.map(function (it) {
      return '<li><b>' + it[0] + '</b> — ' + it[1] + '</li>';
    }).join('') + '</ul>';
  }
  function legendMetricTableHTML(reps, zh) {
    var first = (Array.isArray(reps) && reps.length && reps[0] && reps[0].per_metric &&
                 typeof reps[0].per_metric === 'object') ? reps[0].per_metric : null;
    if (!first) { return ''; }
    var keys = LEGEND_METRIC_ORDER.filter(function (k) { return first[k]; });
    Object.keys(first).forEach(function (k) { if (keys.indexOf(k) === -1) { keys.push(k); } });
    if (!keys.length) { return ''; }
    var rows = keys.map(function (k) {
      var m = first[k] || {};
      var ideal = (Array.isArray(m.ideal_range) && m.ideal_range.length >= 2)
        ? (m.ideal_range[0] + '–' + m.ideal_range[1]) : '—';
      var weight = (typeof m.weight === 'number') ? (Math.round(m.weight * 100) + '%') : '—';
      return '<tr><td>' + metricLabel(k) + '</td><td class="mono">' + ideal + '</td><td class="mono">' + weight + '</td></tr>';
    }).join('');
    return '<table class="legend-table"><thead><tr><th>' + (zh ? '指标' : 'Metric') +
      '</th><th>' + (zh ? '理想区间' : 'Ideal range') + '</th><th>' + (zh ? '权重' : 'Weight') +
      '</th></tr></thead><tbody>' + rows + '</tbody></table>';
  }
  function legendMetricNoteHTML(zh) {
    return '<p class="legend-note">' + (zh
        ? '方向：实测值低于区间下限记为「偏低」，高于上限记为「偏高」。'
        : 'Direction: "under" means the measured value is below the ideal range; "over" means above it.') +
      '</p><p class="legend-note">' + (zh
        ? '以上角度均为摄像机画面的二维投影角度（非真实三维关节角），理想区间为初版经验值，会随数据持续校准，且因挥拍类型而异。'
        : 'All angles are 2D projections from the camera view (not true 3D joint angles); ideal ranges are first-release estimates, get refined over time, and vary by stroke type.') +
      '</p>';
  }
  function legendScoringHTML(zh) {
    var items = zh ? [
      '单项评分（0–100）：实测值落在理想区间内得 100 分；超出区间后按超出量线性递减，超出量达到一个区间宽度时降为 0 分。',
      '最终评分（即综合评分）：本次动作各项分数按权重加权平均（缺少测量值的项目不计入），四舍五入到 1 位小数——这就是生物力学启发式评分，训练概览与逐次列表中显示的最终评分均来自它。',
      '分数配色：≥70 绿色（良好），40–69 橙色（一般），<40 红色（需改进）——用于最终评分、逐次分数徽章和分项进度条。',
      'AI 评分（实验性）：来自另一个独立训练的模型，用于预测专家教练打出的 1–7 分技术评级，并换算为 0–100 分：(原始分 - 1) / 6 * 100，超出范围会截断。该模型基于域外（非本次训练场景）的挥拍数据训练，单次动作的评分不可靠，仅供参考——不计入最终评分。'
    ] : [
      'Per-metric score (0–100): 100 if the measured value falls inside the ideal range; otherwise it decays linearly, reaching 0 once the deviation equals one full range-width beyond the boundary.',
      'Final score (the overall rep score): the per-metric scores for that rep, combined into a weighted average (metrics with no measurement are excluded), rounded to 1 decimal — this is the biomechanical heuristic, and it is the final score shown in the drill summary and per-rep list.',
      'Score colors: green ≥70 (good), orange 40–69 (fair), red <40 (needs work) — used for the final score, per-rep score badges, and metric bars.',
      'AI score (experimental): from a separately trained model that predicts an expert coach rating on a 1–7 scale, then rescales it to 0–100 via (raw - 1) / 6 * 100, clamped to that range. It is trained on out-of-domain footage; per-rep values are unreliable and shown for reference only — it is NOT part of the final score.'
    ];
    return '<ul class="plan-list">' + items.map(function (s) { return '<li>' + s + '</li>'; }).join('') + '</ul>';
  }
  function legendHTML(reps) {
    var zh = state.lang === 'zh';
    var table = legendMetricTableHTML(reps, zh);
    var meanings = LEGEND_METRIC_ORDER.map(function (k) {
      var mm = METRIC_MEANING[k];
      return '<li><b>' + metricLabel(k) + '</b> — ' + (mm ? (zh ? mm[0] : mm[1]) : '') + '</li>';
    }).join('');
    return '<button class="btn-ghost legend-toggle" id="legend-toggle" type="button" aria-expanded="false" aria-controls="legend-panel">' +
        (zh ? '▸ 说明' : '▸ Legend') +
      '</button>' +
      '<div class="plan-detail legend-panel" id="legend-panel" hidden>' +
        '<div class="plan-sub">' + (zh ? '概览指标' : 'Summary items') + '</div>' +
        legendSummaryHTML(zh) +
        '<div class="plan-sub">' + (zh ? '分项指标' : 'Per-metric') + '</div>' +
        '<ul class="plan-list">' + meanings + '</ul>' +
        (table || '<p class="muted">' + (zh ? '暂无逐次数据，无法显示理想区间/权重表。' : 'No per-rep data yet, so the ideal-range/weight table is unavailable.') + '</p>') +
        legendMetricNoteHTML(zh) +
        '<div class="plan-sub">' + (zh ? '评分方法' : 'Scoring') + '</div>' +
        legendScoringHTML(zh) +
      '</div>';
  }
  function bindLegendToggle() {
    var btn = document.getElementById('legend-toggle');
    var panel = document.getElementById('legend-panel');
    if (!btn || !panel) { return; }
    btn.onclick = function () {
      var zh = state.lang === 'zh';
      var show = !!panel.hidden;
      panel.hidden = !show;
      btn.setAttribute('aria-expanded', show ? 'true' : 'false');
      btn.textContent = (show ? '▾ ' : '▸ ') + (zh ? '说明' : 'Legend');
    };
  }
  function renderPostureResults(body) {
    var zh = state.lang === 'zh'; var stem = state.resultsVideo;
    postureCtx = { stem: stem, reps: [], fps: 30, sel: -1 };
    var vurl = '/api/output/' + stem + '/posture/detect_' + stem + '.mp4';
    body.innerHTML =
      '<div class="res-grid"><div><video id="posture-video" class="res-video" controls src="' + vurl + '"></video>' +
        '<div id="rep-scrubber" class="scrubber"></div></div>' +
        '<div><div class="res-section" id="drill-summary"><p class="muted">' + (zh?'加载中…':'Loading…') + '</p></div>' +
          '<div class="res-section"><h2>' + (zh?'逐次':'Reps') + '</h2><ul id="rep-list" class="rep-list"></ul></div></div></div>' +
      '<div class="res-section rep-detail" id="rep-detail"></div>' +
      '<div class="res-section" id="plan-box"></div>' +
      '<div class="res-section" id="report-box"></div>' +
      '<div class="res-section"><button class="btn-ghost danger-link" id="posture-del">🗑 ' + (zh?'删除训练结果':'Delete drill results') + '</button></div>';
    renderPlanPanel(stem, 'posture');
    renderCoachReport(stem);
    document.getElementById('posture-del').onclick = function () {
      openDeleteModal(stem, 'posture', function () { afterModeDelete('posture'); });
    };
    fetch('/api/posture/' + stem).then(function (r) { return r.ok ? r.json() : null; }).then(function (d) {
      if (!d || !d.summary) { document.getElementById('drill-summary').innerHTML = '<p class="muted">' + (zh?'暂无姿态数据':'No posture data yet') + '</p>'; return; }
      var s = d.summary; postureCtx.reps = d.reps || [];
      var metaReady = fetch('/api/output/' + stem + '/posture/metadata.json')
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (meta) {
          if (meta && meta.video && meta.video.fps) postureCtx.fps = meta.video.fps;
          var sumEl = document.getElementById('drill-summary');
          var prov = provenanceHTML(meta);
          // insertAdjacentHTML (not innerHTML +=) so it does not reparse/replace the
          // existing children of #drill-summary -- an innerHTML += there would destroy
          // and recreate #legend-toggle, silently dropping its click handler.
          if (sumEl && prov) sumEl.insertAdjacentHTML('beforeend', prov);
        })
        .catch(function () {});
      var weak = (s.recurring_weaknesses || []).slice(0,3).map(function (w) { return '<li>' + metricLabel(w.metric) + ' ×' + w.count + '</li>'; }).join('');
      // Primary tile = the FINAL score (heuristic). Falls back to mean_score for
      // outputs written before mean_final_score existed.
      var finalMeanRaw = (s.mean_final_score === null || s.mean_final_score === undefined) ? s.mean_score : s.mean_final_score;
      var mean = (finalMeanRaw === null || finalMeanRaw === undefined) ? null : Math.round(finalMeanRaw);
      var cons = (s.consistency === null || s.consistency === undefined) ? null : Math.round(s.consistency * 10) / 10;
      var aiMean = (s.mean_ai_score === null || s.mean_ai_score === undefined) ? null : Math.round(s.mean_ai_score);
      document.getElementById('drill-summary').innerHTML =
        '<h2>' + (zh?'训练概览':'Drill summary') + '</h2>' +
        '<div class="dsum-grid mono">' +
          '<div><span>' + (zh?'次数':'Reps') + '</span><b>' + s.rep_count + '</b></div>' +
          '<div><span>' + (zh?'最终评分':'Final score') + '</span><b' + (mean === null ? '' : ' style="color:' + scoreHue(mean) + '"') + '>' + (mean === null ? '—' : mean) + '</b></div>' +
          '<div><span>' + (zh?'一致性':'Consistency') + '</span><b>' + (cons === null ? '—' : cons) + '</b></div>' +
          '<div><span>' + (zh?'最佳':'Best') + '</span><b>' + (s.best_rep ? '#' + s.best_rep.rep_id : '—') + '</b></div>' +
          '<div><span>' + (zh?'最差':'Worst') + '</span><b>' + (s.worst_rep ? '#' + s.worst_rep.rep_id : '—') + '</b></div>' +
        '</div>' +
        // AI tile: de-emphasized (muted, own row) and labeled experimental -- it is
        // NOT part of the final score above (see compute_final_score in writer.py).
        (aiMean !== null ? '<div class="dsum-ai muted mono">' +
            '<span class="dsum-ai-label">' + (zh?'AI 评分（实验）':'AI score (beta)') + '</span>' +
            '<span class="dsum-ai-val">' + aiMean + '</span>' +
            '<div class="dsum-ai-cap">' + (zh?'实验性，未计入最终评分':'experimental — not part of the final score') + '</div>' +
          '</div>' : '') +
        (weak ? '<div class="weak"><span class="muted">' + (zh?'常见问题':'Recurring') + '</span><ul>' + weak + '</ul></div>' : '') +
        legendHTML(postureCtx.reps);
      bindLegendToggle();
      document.getElementById('rep-list').innerHTML = postureCtx.reps.map(function (rep, i) {
        var repFinal = (rep.final_score === null || rep.final_score === undefined) ? rep.overall_score : rep.final_score;
        return '<li><button class="rep-row" data-i="' + i + '"><span class="mono">#' + rep.rep_id + '</span>' +
          scoreChipHTML(repFinal) +
          (rep.ai_score !== undefined && rep.ai_score !== null ? '<span class="ai-chip ai-chip-beta mono" title="' +
            (zh?'AI 评分（实验），未计入最终评分':'AI score (beta) — experimental, not part of the final score') +
            '">AI·beta ' + Math.round(rep.ai_score) + '</span>' : '') +
          '</button></li>'; }).join('');
      document.querySelectorAll('.rep-row').forEach(function (b) { b.onclick = function () { selectRep(postureCtx.reps, Number(b.getAttribute('data-i'))); }; });
      if (postureCtx.reps.length) {
        metaReady.then(function () { if (document.getElementById('rep-detail')) selectRep(postureCtx.reps, 0); });
      }
    }).catch(function () { document.getElementById('drill-summary').innerHTML = '<p class="muted">' + (zh?'加载失败':'Failed to load') + '</p>'; });
  }
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
  function selectRep(reps, i) {
    postureCtx.sel = i; var rep = reps[i]; var zh = state.lang === 'zh';
    document.querySelectorAll('.rep-row').forEach(function (x) { x.classList.toggle('on', Number(x.getAttribute('data-i')) === i); });
    var bars = Object.keys(rep.per_metric || {}).map(function (k) { return metricBarHTML(k, rep.per_metric[k]); }).join('');
    var ws = (rep.weaknesses || []).map(weaknessLineHTML).join('');
    var repFinal = (rep.final_score === null || rep.final_score === undefined) ? rep.overall_score : rep.final_score;
    document.getElementById('rep-detail').innerHTML =
      '<h2>' + (zh?('第 ' + rep.rep_id + ' 次详情'):('Rep #' + rep.rep_id)) + '</h2>' +
      (repFinal !== null && repFinal !== undefined ? '<p class="final-line">' + (zh?'最终评分：':'Final score: ') +
        '<b class="mono" style="color:' + scoreHue(repFinal) + '">' + repFinal + '</b>/100</p>' : '') +
      (rep.ai_score !== undefined && rep.ai_score !== null ? '<p class="ai-line">' + (zh?'AI 动作评分（实验性，未计入最终评分）：':'AI form score (experimental — not in the final score): ') + '<b class="mono">' + rep.ai_score + '</b>/100</p>' : '') +
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
        else { alert(zh ? (d.error || '生成失败') : 'Clip generation failed'); } })
      .catch(function () { btn.disabled = false; btn.textContent = '⬇ ' + (zh?'下载本次':'Download this rep'); alert(zh ? '网络错误，请重试' : 'Network error — please try again'); });
  }
  var planCtx = { stem: null, mode: 'match', plan: null, loc: 'court' };
  var PHASE_LABELS = { Foundation: ['基础期', 'Foundation'], Progression: ['进阶期', 'Progression'] };
  function phaseLabel(p) { var m = PHASE_LABELS[p]; return m ? (state.lang==='zh'?m[0]:m[1]) : p; }
  function sessionName(s) {
    if (state.lang === 'zh' && s.detail && s.detail.name_zh) { return s.detail.name_zh; }
    return s.name;
  }
  function renderPlanPanel(stem, mode) {
    planCtx = { stem: stem, mode: mode, plan: null, loc: 'court' };
    var box = document.getElementById('plan-box'); if (!box) { return; }
    var zh = state.lang === 'zh';
    box.innerHTML = '<h2>' + (zh?'训练计划':'Training Plan') + '</h2><p class="muted">' + (zh?'加载中…':'Loading…') + '</p>';
    var url = (mode === 'posture' ? '/api/posture-plan/' : '/api/training-plan/') + stem;
    fetch(url).then(function (r) { return r.ok ? r.json() : null; }).then(function (d) {
      var b = document.getElementById('plan-box'); if (!b) { return; }
      if (!d || !d.weeks) {
        b.innerHTML = '<h2>' + (zh?'训练计划':'Training Plan') + '</h2><p class="muted">' + (zh?'暂无训练计划（需先完成分析）':'No training plan yet (run an analysis first)') + '</p>';
        return;
      }
      planCtx.plan = d;
      b.innerHTML =
        '<div class="plan-head"><h2>' + (zh?'训练计划':'Training Plan') + '</h2>' +
          '<div class="seg" role="tablist" aria-label="location">' +
            '<button class="seg-btn" data-loc="court" role="tab">' + (zh?'场上':'On-Court') + '</button>' +
            '<button class="seg-btn" data-loc="home" role="tab">' + (zh?'居家':'At-Home') + '</button>' +
          '</div>' +
          '<button class="btn-ghost" id="plan-regen">↻ ' + (zh?'重新生成':'Regenerate') + '</button></div>' +
        '<div id="plan-weeks"></div>';
      b.querySelectorAll('[data-loc]').forEach(function (t) {
        t.onclick = function () { planCtx.loc = t.getAttribute('data-loc'); renderPlanWeeks(); };
      });
      document.getElementById('plan-regen').onclick = regeneratePlan;
      renderPlanWeeks();
    }).catch(function () {
      var b = document.getElementById('plan-box');
      if (b) { b.innerHTML = '<h2>' + (zh?'训练计划':'Training Plan') + '</h2><p class="muted">' + (zh?'加载失败':'Failed to load') + '</p>'; }
    });
  }
  function renderPlanWeeks() {
    var wrap = document.getElementById('plan-weeks'); if (!wrap || !planCtx.plan) { return; }
    var zh = state.lang === 'zh';
    document.querySelectorAll('#plan-box [data-loc]').forEach(function (t) {
      t.classList.toggle('on', t.getAttribute('data-loc') === planCtx.loc);
      t.setAttribute('aria-selected', t.getAttribute('data-loc') === planCtx.loc ? 'true' : 'false');
    });
    planCtx.flat = [];
    var html = '';
    (planCtx.plan.weeks || []).forEach(function (wk) {
      var rows = (wk.sessions || []).filter(function (s) { return s.location === planCtx.loc; });
      if (!rows.length) { return; }
      html += '<div class="plan-week"><div class="plan-week-head mono">' +
        (zh ? ('第 ' + wk.week + ' 周') : ('Week ' + wk.week)) + ' · ' + phaseLabel(wk.phase) + '</div>';
      rows.forEach(function (s) {
        var si = planCtx.flat.length;
        planCtx.flat.push({ week: wk.week, phase: wk.phase, session: s });
        html += '<button class="plan-row" data-si="' + si + '">' +
          '<span class="plan-name">' + sessionName(s) + '</span>' +
          '<span class="plan-meta mono">' + s.sets + '×' + s.reps +
            ' · ' + s.frequency_per_week + '×/' + (zh?'周':'wk') +
            (s.duration_min ? ' · ' + s.duration_min + 'min' : '') + '</span></button>' +
          '<div class="plan-detail" id="plan-detail-' + si + '" hidden></div>';
      });
      html += '</div>';
    });
    wrap.innerHTML = html || '<p class="muted">' + (zh?'本视图暂无训练项目':'Nothing scheduled for this view') + '</p>';
    wrap.querySelectorAll('.plan-row').forEach(function (r) {
      r.onclick = function () { togglePlanDetail(Number(r.getAttribute('data-si'))); };
    });
  }
  var EQUIP_LABELS = { 'racket': '球拍', 'racket, shuttles': '球拍、羽毛球', 'resistance band': '弹力带',
    'medicine ball': '药球', 'dumbbell': '哑铃', 'none': '无器械' };
  var DIFF_LABELS = { beginner: ['入门', 'Beginner'], intermediate: ['进阶', 'Intermediate'], advanced: ['高级', 'Advanced'] };
  function equipLabel(e) { return state.lang === 'zh' ? (EQUIP_LABELS[e] || e) : e; }
  function diffLabel(d) { var m = DIFF_LABELS[d]; return m ? (state.lang==='zh'?m[0]:m[1]) : d; }
  function localList(block) {
    if (!block) { return []; }
    var zh = state.lang === 'zh';
    return (zh ? (block.zh || block.en) : (block.en || block.zh)) || [];
  }
  function videoGuideHTML(url) {
    if (!url) { return ''; }
    var zh = state.lang === 'zh';
    var watch = /youtube\.com\/watch\?v=([\w-]{6,})/.exec(url);
    var openLink = '<a class="video-link" href="' + url + '" target="_blank" rel="noopener">▶ ' +
      (watch ? (zh?'在 YouTube 打开':'Open on YouTube') : (zh?'查找视频教学':'Find video guides')) + '</a>';
    if (watch && navigator.onLine) {
      return '<div class="video-wrap"><iframe class="video-embed" src="https://www.youtube-nocookie.com/embed/' + watch[1] + '" ' +
        'title="' + (zh ? '视频教学' : 'video guide') + '" loading="lazy" allowfullscreen referrerpolicy="strict-origin-when-cross-origin"></iframe></div>' + openLink;
    }
    if (watch && !navigator.onLine) {
      return '<div class="video-offline muted">' + (zh?'离线状态，视频暂不可用':'Video unavailable offline') + '</div>' + openLink;
    }
    return openLink;
  }
  function togglePlanDetail(si) {
    var el = document.getElementById('plan-detail-' + si); if (!el) { return; }
    if (!el.hidden) { el.hidden = true; el.innerHTML = ''; return; }
    var zh = state.lang === 'zh';
    var entry = planCtx.flat && planCtx.flat[si]; if (!entry) { return; }
    var d = entry.session.detail;
    if (!d) {
      el.innerHTML = '<p class="muted">' + (zh?'此计划是旧版本生成的——点击「重新生成」查看动作详解。':'This plan was generated before exercise details existed — hit Regenerate to see them.') + '</p>';
      el.hidden = false; return;
    }
    var desc = zh ? (d.description_zh || d.description) : (d.description || d.description_zh);
    var ins = localList(d.instructions).map(function (s) { return '<li>' + s + '</li>'; }).join('');
    var cues = localList(d.coaching_cues).map(function (s) { return '<li>' + s + '</li>'; }).join('');
    var mis = localList(d.common_mistakes).map(function (s) { return '<li>' + s + '</li>'; }).join('');
    el.innerHTML =
      '<div class="chip-row">' +
        (d.difficulty ? '<span class="chip-metric">' + diffLabel(d.difficulty) + '</span>' : '') +
        (d.equipment ? '<span class="chip-metric">' + equipLabel(d.equipment) + '</span>' : '') + '</div>' +
      (desc ? '<p class="plan-desc">' + desc + '</p>' : '') +
      (ins ? '<div class="plan-sub">' + (zh?'怎么做':'How to do it') + '</div><ol class="plan-list">' + ins + '</ol>' : '') +
      (cues ? '<div class="plan-sub">' + (zh?'要点提示':'Coaching cues') + '</div><ul class="plan-list">' + cues + '</ul>' : '') +
      (mis ? '<div class="plan-sub">' + (zh?'常见错误':'Common mistakes') + '</div><ul class="plan-list">' + mis + '</ul>' : '') +
      videoGuideHTML(d.video_url);
    el.hidden = false;
  }
  function regeneratePlan() {
    var zh = state.lang === 'zh';
    var btn = document.getElementById('plan-regen'); if (!btn) { return; }
    btn.disabled = true; btn.textContent = zh?'生成中…':'Regenerating…';
    var url = (planCtx.mode === 'posture' ? '/api/posture-plan/' : '/api/training-plan/') + planCtx.stem;
    fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ weeks: 4 }) })
      .then(function (r) { return r.ok ? r.json() : null; }).then(function (d) {
        btn.disabled = false; btn.textContent = '↻ ' + (zh?'重新生成':'Regenerate');
        if (d && d.weeks) { planCtx.plan = d; renderPlanWeeks(); }
        else { alert(zh?'生成失败':'Failed to regenerate'); }
      }).catch(function () {
        btn.disabled = false; btn.textContent = '↻ ' + (zh?'重新生成':'Regenerate');
        alert(zh ? '网络错误，请重试' : 'Network error — please try again');
      });
  }
  var reportCtx = { stem: null, lang: 'zh-Hans' };
  var REPORT_LANG_TABS = [['en', 'EN'], ['zh-Hant', '繁體'], ['zh-Hans', '簡體']];
  function renderCoachReport(stem) {
    reportCtx.stem = stem;
    var box = document.getElementById('report-box'); if (!box) { return; }
    var zh = state.lang === 'zh';
    box.innerHTML =
      '<div class="plan-head"><h2>' + (zh?'教练报告':'Coach Report') + '</h2>' +
        '<div class="seg" role="tablist" aria-label="report language">' + REPORT_LANG_TABS.map(function (t) {
          return '<button class="seg-btn" data-rlang="' + t[0] + '" role="tab">' + t[1] + '</button>'; }).join('') + '</div>' +
        '<span id="report-dl"></span>' +
        '<button class="btn-ghost danger-link" id="report-del">🗑 ' + (zh?'删除报告':'Delete reports') + '</button></div>' +
      '<div id="report-body"><p class="muted">' + (zh?'加载中…':'Loading…') + '</p></div>';
    box.querySelectorAll('[data-rlang]').forEach(function (b) {
      b.onclick = function () { loadCoachReport(b.getAttribute('data-rlang')); };
    });
    var rd = document.getElementById('report-del');
    if (rd) { rd.onclick = function () { openDeleteModal(stem, 'reports', function () { renderResults(); }); }; }
    loadCoachReport(reportCtx.lang);
  }
  function loadCoachReport(lang) {
    reportCtx.lang = lang;
    var zh = state.lang === 'zh';
    document.querySelectorAll('#report-box [data-rlang]').forEach(function (b) {
      b.classList.toggle('on', b.getAttribute('data-rlang') === lang);
      b.setAttribute('aria-selected', b.getAttribute('data-rlang') === lang ? 'true' : 'false');
    });
    fetch('/api/posture-report/' + reportCtx.stem + '?lang=' + lang)
      .then(function (r) { return r.ok ? r.json() : null; }).then(function (d) {
        var body = document.getElementById('report-body'); if (!body) { return; }
        if (!d) {
          body.innerHTML = '<p class="muted">' + (zh?'暂无教练报告（完成姿态分析后生成）':'No coach report yet (produced by a posture analysis)') + '</p>';
          var dl0 = document.getElementById('report-dl'); if (dl0) { dl0.innerHTML = ''; }
          return;
        }
        var h = d.header || {}, s = d.summary || {};
        var html = '<div class="report-head mono">' + (h.stroke_label || h.stroke || '') +
          ' · ' + (h.rep_count || 0) + (lang === 'en' ? ' reps' : ' 次') + (h.date ? ' · ' + h.date : '') + '</div>';
        if (s.verdict_text) { html += '<p class="report-verdict">' + s.verdict_text + '</p>'; }
        (d.strengths || []).forEach(function (x) {
          html += '<div class="report-card good"><b>' + (x.metric_label || x.metric) + '</b>' +
            (x.impact_label ? ' <span class="chip-metric">' + x.impact_label + '</span>' : '') +
            '<div>' + (x.text || '') + '</div></div>';
        });
        (d.weaknesses || []).forEach(function (x) {
          var ideal = (Array.isArray(x.ideal_range) && x.ideal_range.length >= 2) ? (x.ideal_range[0] + '–' + x.ideal_range[1]) : '';
          html += '<div class="report-card bad"><b>' + (x.metric_label || x.metric) + '</b>' +
            (x.impact_label ? ' <span class="chip-metric">' + x.impact_label + '</span>' : '') +
            '<div class="mono report-nums">' + (x.measured !== null && x.measured !== undefined ? (Math.round(x.measured * 10) / 10) : '—') +
              (ideal ? ' (' + ideal + ')' : '') + '</div>' +
            (x.mechanism_text ? '<div>' + x.mechanism_text + '</div>' : '') +
            (x.drill_text ? '<div class="report-drill">' + x.drill_text + '</div>' : '') + '</div>';
        });
        body.innerHTML = html;
        var dl = document.getElementById('report-dl');
        if (dl) {
          dl.innerHTML =
            '<a class="video-link" href="/api/output/' + reportCtx.stem + '/posture/coach_report_' + lang + '.html" target="_blank" rel="noopener">HTML</a> ' +
            '<a class="video-link" href="/api/output/' + reportCtx.stem + '/posture/coach_report_' + lang + '.pdf" target="_blank" rel="noopener">PDF</a>';
        }
      }).catch(function () {
        var body = document.getElementById('report-body');
        if (body) { body.innerHTML = '<p class="muted">' + (zh?'加载失败':'Failed to load') + '</p>'; }
        var dl = document.getElementById('report-dl');
        if (dl) { dl.innerHTML = ''; }
      });
  }
  var DELETE_SCOPE_TITLES = {
    all: ['删除整个视频', 'Delete entire video'],
    match: ['删除比赛结果', 'Delete match results'],
    posture: ['删除训练结果', 'Delete drill results'],
    clips: ['删除生成的剪辑', 'Delete generated clips'],
    reports: ['删除教练报告', 'Delete coach reports']
  };
  var DELETE_GROUP_LABELS = {
    source: ['源视频与球场模板', 'Source video & court template'],
    match: ['比赛分析结果（含回合剪辑）', 'Match results (incl. rally clips)'],
    posture: ['训练分析结果（含逐次剪辑与报告）', 'Drill results (incl. rep clips & reports)'],
    thumb: ['缩略图', 'Thumbnail'],
    clips_rally: ['回合剪辑', 'Rally clips'],
    clips_rep: ['逐次剪辑', 'Rep clips'],
    reports: ['教练报告', 'Coach reports']
  };
  var _delGen = 0;
  var _delBusy = false;
  function _delEsc(e) { if (e.key === 'Escape' && !_delBusy) { closeDeleteModal(); } }
  function closeDeleteModal() {
    _delGen += 1;
    _delBusy = false;
    var m = document.getElementById('del-modal');
    if (m) { m.remove(); }
    document.removeEventListener('keydown', _delEsc);
  }
  function openDeleteModal(stem, scope, onDone) {
    closeDeleteModal();
    var gen = _delGen;
    var zh = state.lang === 'zh';
    var title = DELETE_SCOPE_TITLES[scope];
    var wrap = document.createElement('div');
    wrap.id = 'del-modal';
    wrap.className = 'modal-overlay';
    wrap.innerHTML =
      '<div class="modal-card" role="dialog" aria-modal="true">' +
        '<h3 class="modal-title">' + (zh ? title[0] : title[1]) + '</h3>' +
        '<div class="modal-sub mono">' + stem + '</div>' +
        '<div id="del-rows" class="modal-rows muted">' + (zh?'加载中…':'Loading…') + '</div>' +
        '<div id="del-err" class="modal-err"></div>' +
        '<div class="modal-actions">' +
          '<button class="btn-ghost" id="del-cancel">' + (zh?'取消':'Cancel') + '</button>' +
          '<button class="btn-danger" id="del-confirm" disabled>' + (zh?'永久删除':'Delete permanently') + '</button>' +
        '</div></div>';
    document.body.appendChild(wrap);
    wrap.onclick = function (e) { if (!_delBusy && e.target === wrap) { closeDeleteModal(); } };
    document.addEventListener('keydown', _delEsc);
    var cancelBtn = document.getElementById('del-cancel');
    var confirmBtn = document.getElementById('del-confirm');
    cancelBtn.onclick = function () { if (!_delBusy) { closeDeleteModal(); } };
    cancelBtn.focus();
    fetch('/api/delete-preview/' + encodeURIComponent(stem) + '?scope=' + scope)
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) {
        if (gen !== _delGen) { return; }
        var rows = document.getElementById('del-rows'); if (!rows) { return; }
        if (!d || !d.ok) { rows.textContent = zh?'预览失败':'Preview failed'; return; }
        if (!d.total_files) { rows.textContent = zh?'没有可删除的文件':'Nothing to delete'; return; }
        rows.classList.remove('muted');
        rows.innerHTML = d.groups.map(function (g) {
          var lbl = DELETE_GROUP_LABELS[g.key];
          return '<div class="modal-row"><span>' + (lbl ? (zh?lbl[0]:lbl[1]) : g.key) + '</span>' +
            '<span class="mono">' + g.files + (zh?' 个文件':' files') + ' · ' + g.size_mb + 'MB</span></div>';
        }).join('') +
          '<div class="modal-row modal-total"><span>' + (zh?'合计':'Total') + '</span>' +
          '<span class="mono">' + d.total_files + (zh?' 个文件':' files') + ' · ' + d.total_size_mb + 'MB</span></div>';
        confirmBtn.disabled = false;
      }).catch(function () {
        if (gen !== _delGen) { return; }
        var rows = document.getElementById('del-rows');
        if (rows) { rows.textContent = zh?'预览失败':'Preview failed'; }
      });
    confirmBtn.onclick = function () {
      _delBusy = true;
      confirmBtn.disabled = true;
      cancelBtn.disabled = true;
      confirmBtn.textContent = zh?'删除中…':'Deleting…';
      var err0 = document.getElementById('del-err');
      if (err0) { err0.textContent = ''; }
      fetch('/api/delete/' + encodeURIComponent(stem), { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ scope: scope }) })
        .then(function (r) { return r.json().then(function (d) { return { s: r.status, d: d }; }); })
        .then(function (res) {
          if (gen !== _delGen) { return; }
          if (res.d && res.d.ok) { closeDeleteModal(); onDone(); return; }
          _delBusy = false;
          confirmBtn.disabled = false;
          cancelBtn.disabled = false;
          confirmBtn.textContent = zh?'永久删除':'Delete permanently';
          var err = document.getElementById('del-err');
          if (err) {
            err.textContent = res.s === 409
              ? (zh?'该视频正在分析中，请等待完成后再删除':'Analysis is running — wait for it to finish')
              : (zh?'删除失败':'Delete failed');
          }
        })
        .catch(function () {
          if (gen !== _delGen) { return; }
          _delBusy = false;
          confirmBtn.disabled = false;
          cancelBtn.disabled = false;
          confirmBtn.textContent = zh?'永久删除':'Delete permanently';
          var err = document.getElementById('del-err');
          if (err) { err.textContent = zh?'网络错误，请重试':'Network error — please try again'; }
        });
    };
  }
  function afterModeDelete(deletedMode) {
    var stem = state.resultsVideo;
    fetch('/api/videos').then(function (r) { return r.json(); }).then(function (vids) {
      var v = (vids || []).filter(function (x) { return x.name === stem; })[0];
      var remaining = [];
      if (v && v.has_match) { remaining.push('match'); }
      if (v && v.has_posture) { remaining.push('posture'); }
      remaining = remaining.filter(function (m) { return m !== deletedMode; });
      if (remaining.length) { openResults(stem, remaining[0], remaining); }
      else { state.resultsVideo = null; setScreen('dashboard'); }
    }).catch(function () { state.resultsVideo = null; setScreen('dashboard'); });
  }
  function setScreen(name) { state.screen = name; render(); }
  function render() {
    renderSidebar();
    if (state.screen === 'new') { renderWizard(); }
    else if (state.screen === 'results') { renderResults(); }
    else { loadDashboard(); }
  }
  function init() {
    applyTheme();
    document.documentElement.lang = state.lang;
    render();
  }
  document.addEventListener('DOMContentLoaded', init);
  return { state: state, applyTheme: applyTheme, t: t, setLang: setLang,
           setTheme: setTheme, renderSidebar: renderSidebar,
           renderDashboard: renderDashboard, applyFilters: applyFilters,
           loadDashboard: loadDashboard, setScreen: setScreen, openResults: openResults };
})();
