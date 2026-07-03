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
  var wiz = { mode: 'match', video: null, step: 'mode', jobId: null };
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
            '<button class="btn-primary" id="prog-results">' + (zh?'查看结果':'View Results') + '</button>' +
            '<button class="btn-ghost" id="prog-lib">' + (zh?'返回视频库':'Back to Library') + '</button>';
          document.getElementById('prog-results').onclick = function () {
            var stem = wiz.video.replace(/\.[^.]+$/, ''); var m = wiz.mode;
            wiz.step = 'mode'; wiz.video = null; wiz.jobId = null;
            openResults(stem, m, [m]);
          };
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
  function renderPostureResults(body) {
    var zh = state.lang === 'zh'; var stem = state.resultsVideo;
    postureCtx = { stem: stem, reps: [], fps: 30, sel: -1 };
    var vurl = '/api/output/' + stem + '/posture/detect_' + stem + '.mp4';
    body.innerHTML =
      '<div class="res-grid"><div><video id="posture-video" class="res-video" controls src="' + vurl + '"></video>' +
        '<div id="rep-scrubber" class="scrubber"></div></div>' +
        '<div><div class="res-section" id="drill-summary"><p class="muted">' + (zh?'加载中…':'Loading…') + '</p></div>' +
          '<div class="res-section"><h2>' + (zh?'逐次':'Reps') + '</h2><ul id="rep-list" class="rep-list"></ul></div></div></div>' +
      '<div class="res-section rep-detail" id="rep-detail"></div>';
    fetch('/api/posture/' + stem).then(function (r) { return r.ok ? r.json() : null; }).then(function (d) {
      if (!d || !d.summary) { document.getElementById('drill-summary').innerHTML = '<p class="muted">' + (zh?'暂无姿态数据':'No posture data yet') + '</p>'; return; }
      var s = d.summary; postureCtx.reps = d.reps || [];
      fetch('/api/output/' + stem + '/posture/metadata.json').then(function (r) { return r.ok ? r.json() : null; })
        .then(function (meta) { if (meta && meta.video && meta.video.fps) postureCtx.fps = meta.video.fps; }).catch(function () {});
      var weak = (s.recurring_weaknesses || []).slice(0,3).map(function (w) { return '<li>' + metricLabel(w.metric) + ' ×' + w.count + '</li>'; }).join('');
      var mean = (s.mean_score === null || s.mean_score === undefined) ? null : Math.round(s.mean_score);
      var cons = (s.consistency === null || s.consistency === undefined) ? null : Math.round(s.consistency * 10) / 10;
      document.getElementById('drill-summary').innerHTML =
        '<h2>' + (zh?'训练概览':'Drill summary') + '</h2>' +
        '<div class="dsum-grid mono">' +
          '<div><span>' + (zh?'次数':'Reps') + '</span><b>' + s.rep_count + '</b></div>' +
          '<div><span>' + (zh?'平均分':'Mean') + '</span><b' + (mean === null ? '' : ' style="color:' + scoreHue(mean) + '"') + '>' + (mean === null ? '—' : mean) + '</b></div>' +
          '<div><span>' + (zh?'一致性':'Consistency') + '</span><b>' + (cons === null ? '—' : cons) + '</b></div>' +
          '<div><span>' + (zh?'最佳':'Best') + '</span><b>' + (s.best_rep ? '#' + s.best_rep.rep_id : '—') + '</b></div>' +
          '<div><span>' + (zh?'最差':'Worst') + '</span><b>' + (s.worst_rep ? '#' + s.worst_rep.rep_id : '—') + '</b></div>' +
        '</div>' + (weak ? '<div class="weak"><span class="muted">' + (zh?'常见问题':'Recurring') + '</span><ul>' + weak + '</ul></div>' : '');
      document.getElementById('rep-list').innerHTML = postureCtx.reps.map(function (rep, i) {
        return '<li><button class="rep-row" data-i="' + i + '"><span class="mono">#' + rep.rep_id + '</span>' +
          scoreChipHTML(rep.overall_score) + '</button></li>'; }).join('');
      document.querySelectorAll('.rep-row').forEach(function (b) { b.onclick = function () { selectRep(postureCtx.reps, Number(b.getAttribute('data-i'))); }; });
      if (postureCtx.reps.length) { selectRep(postureCtx.reps, 0); }
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
