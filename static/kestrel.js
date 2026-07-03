window.Kestrel = (function () {
  'use strict';
  var state = { lang: localStorage.getItem('kestrel_lang') || 'zh',
                theme: localStorage.getItem('kestrel_theme') ||
                       (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light') };
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
      '<div class="page-head"><h1>' + (state.lang==='zh'?'视频库':'Video Library') + '</h1></div>' +
      '<div class="stat-row">' + tiles.map(function (t2) {
        var val = s[t2[0]]; if (val === null || val === undefined) val = '—';
        return '<div class="stat"><div class="stat-label mono">' + t2[1] +
               '</div><div class="stat-val mono">' + val + '</div></div>';
      }).join('') + '</div>' +
      '<div class="filter-bar" id="filter-bar"></div>' +
      '<div id="cards" class="card-grid"></div>';
    renderFilterBar();
    renderCards();
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
  }
  function render() { renderSidebar(); loadDashboard(); }
  function init() {
    applyTheme();
    document.documentElement.lang = state.lang;
    render();
  }
  document.addEventListener('DOMContentLoaded', init);
  return { state: state, applyTheme: applyTheme, t: t, setLang: setLang,
           setTheme: setTheme, renderSidebar: renderSidebar,
           renderDashboard: renderDashboard, applyFilters: applyFilters,
           loadDashboard: loadDashboard };
})();
