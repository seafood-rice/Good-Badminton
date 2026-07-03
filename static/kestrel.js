window.Kestrel = (function () {
  'use strict';
  var state = { lang: localStorage.getItem('kestrel_lang') || 'zh',
                theme: localStorage.getItem('kestrel_theme') ||
                       (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'),
                screen: 'dashboard' };
  function applyTheme() {
    document.documentElement.setAttribute('data-theme', state.theme);
  }
  var T = {
    zh: { brand: 'Kestrel', portal: '教练平台', nav_dashboard: '视频库',
          nav_new: '新建分析', persona: 'Coach Lee', team: 'Team Falcons',
          theme_light: '浅色', theme_dark: '深色' },
    en: { brand: 'Kestrel', portal: 'Coach Portal', nav_dashboard: 'Dashboard',
          nav_new: 'New Analysis', persona: 'Coach Lee', team: 'Team Falcons',
          theme_light: 'Light', theme_dark: 'Dark' }
  };
  function t(key) { return (T[state.lang] && T[state.lang][key]) || key; }
  function setLang(lang) { state.lang = lang; localStorage.setItem('kestrel_lang', lang);
    document.documentElement.lang = lang; render(); }
  function setTheme(theme) { state.theme = theme; localStorage.setItem('kestrel_theme', theme);
    applyTheme(); renderSidebar(); }
  var lib = { videos: [], filter: { q: '', mode: 'all', status: 'all', sort: 'date' } };
  var wiz = { mode: 'match', video: null, step: 'mode', jobId: null };
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
      var thumb = v.thumb ? 'background-image:url(' + v.thumb + ')' : '';
      var dur = v.duration_sec ? Math.floor(v.duration_sec/60)+':'+('0'+Math.round(v.duration_sec%60)).slice(-2) : '';
      return '<div class="vcard"><div class="vthumb" style="' + thumb + '">' +
        '<span class="vmode mono">' + modeLabel(v) + '</span>' +
        '<span class="vdur mono">' + dur + '</span></div>' +
        '<div class="vbody"><div class="vname">' + v.name + '</div>' +
        '<div class="vdate mono">' + (v.date||'') + '</div>' +
        '<div class="vfoot">' + statusChip(v) + '</div></div></div>';
    }).join('');
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
  function renderStepProgress(body) { body.innerHTML = '<p class="muted">progress step (Task 7)</p>'; }
  function setScreen(name) { state.screen = name; render(); }
  function render() {
    renderSidebar();
    if (state.screen === 'new') { renderWizard(); }
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
           loadDashboard: loadDashboard, setScreen: setScreen };
})();
