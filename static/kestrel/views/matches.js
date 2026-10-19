// Matches view (#/matches, spec 6.5): the scope's analysed matches with result chips and an inline match-record
// editor (PUT /api/matches/<stem>); a row opens the workspace with the container transform. Also the match peek
// drawer the overview opens (buildMatchPeek). matchRows / validateRecordForm / formFromRecord / peekModel are
// pure and tested. Results come only from the record the user enters: there is no point outcome.
import { t } from '../core/i18n.js';
import { fmtDate, fmtNumber, isApproxDate } from '../core/format.js';
import { createClock, local } from '../core/motion.js';
import { formatHash } from '../core/router.js';
import { api, errorText } from '../core/api.js';
import { h, hasDOM, revealStyle, approxMark } from '../widgets/frame.js';
import { rallyCountText } from './workspace.js';

const SIDES = ['upper', 'lower'];
const OTHER = '__other';
const MAX_SETS = 5;
const NAME_MAX = 60;
const LONG = { timeout: 60000 };

const isNum = (v) => typeof v === 'number' && Number.isFinite(v);
const obj = (v) => (v && typeof v === 'object' && !Array.isArray(v) ? v : {});
const str = (v) => (typeof v === 'string' ? v : '');
const otherSide = (s) => (s === 'upper' ? 'lower' : 'upper');

function realDate(s) {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(s);
  if (!m) return false;
  const d = new Date(Date.UTC(+m[1], +m[2] - 1, +m[3]));
  return d.getUTCFullYear() === +m[1] && d.getUTCMonth() === +m[2] - 1 && d.getUTCDate() === +m[3];
}

function setsText(sets) {
  return (Array.isArray(sets) ? sets : []).filter((p) => Array.isArray(p) && p.length === 2).map((p) => `${p[0]}-${p[1]}`).join(', ');
}

/**
 * Pure: overview payload -> one row per stem (the first unit wins; the aggregator lists a stem's lower side
 * first when no athlete is selected). Row: {stem, date, side, athlete, opponent, result 'W'|'L'|null,
 * sets [[own, opp]], setsText, techIndex, status, doubles}. `result` is relative to `athlete` and comes from the
 * record; `doubles` is true only for an exact `true` (a doubles run has no per-player score).
 */
export function matchRows(overview) {
  const list = Array.isArray(obj(overview).matches) ? overview.matches : [];
  const seen = new Set();
  const rows = [];
  for (const m of list) {
    if (!m || typeof m !== 'object' || typeof m.stem !== 'string' || !m.stem || seen.has(m.stem)) continue;
    seen.add(m.stem);
    const status = m.needs_review ? 'needs_review' : ['analysed', 'processing', 'new'].includes(m.status) ? m.status : 'analysed';
    const sets = Array.isArray(m.sets) ? m.sets.filter((p) => Array.isArray(p) && p.length === 2 && p.every(isNum)) : [];
    rows.push({
      stem: m.stem,
      date: typeof m.date === 'string' ? m.date : null,
      dateApprox: isApproxDate(m.date, m.date_source),
      side: SIDES.includes(m.side) ? m.side : null,
      athlete: str(m.athlete) || null,
      opponent: str(m.opponent) || null,
      result: m.result === 'W' || m.result === 'L' ? m.result : null,
      sets,
      setsText: setsText(sets),
      techIndex: isNum(m.tech_index) ? m.tech_index : null,
      status,
      doubles: m.doubles === true,
    });
  }
  return rows;
}

function cleanSide(side, errors, key) {
  const s = obj(side);
  const id = str(s.athlete_id).trim();
  if (id) return { athlete_id: id };
  const name = str(s.name).trim();
  if (!name) return null;
  // eslint-disable-next-line no-control-regex
  if (name.length > NAME_MAX || /[\u0000-\u001f\u007f]/.test(name)) {
    errors[key] = 'record.err.name';
    return null;
  }
  return { name };
}

/**
 * Pure: editor form {date, upper:{athlete_id, name}, lower:{...}, winner: ''|'upper'|'lower', sets: "21-18, 19-21"}
 * -> {record: {date, upper, lower, result} | null, errors: {field: i18n key}}. Blank sides map to null; set scores
 * are [upper, lower] pairs; a winner is required when sets are present.
 */
export function validateRecordForm(form) {
  const f = obj(form);
  const errors = {};
  const dateRaw = str(f.date).trim();
  let date = null;
  if (dateRaw) {
    if (realDate(dateRaw)) date = dateRaw;
    else errors.date = 'record.err.date';
  }
  const upper = cleanSide(f.upper, errors, 'upper');
  const lower = cleanSide(f.lower, errors, 'lower');
  if (upper && lower && upper.athlete_id && upper.athlete_id === lower.athlete_id) errors.lower = 'record.err.same';

  const setsRaw = str(f.sets).trim();
  const sets = [];
  if (setsRaw) {
    const parts = setsRaw.split(/[,;，、]/).map((p) => p.trim());
    for (const p of parts) {
      const m = /^(\d{1,2})\s*[-–—:]\s*(\d{1,2})$/.exec(p);
      if (!m) { errors.sets = 'record.err.sets'; break; }
      sets.push([Number(m[1]), Number(m[2])]);
    }
    if (!errors.sets && sets.length > MAX_SETS) errors.sets = 'record.err.sets';
  }
  const winner = SIDES.includes(f.winner) ? f.winner : null;
  if (sets.length && !winner && !errors.sets) errors.winner = 'record.err.winner';
  const result = winner ? { winner, sets: errors.sets ? [] : sets } : null;
  if (Object.keys(errors).length) return { record: null, errors };
  return { record: { date, upper, lower, result }, errors };
}

/** Pure: a stored record (or {}) -> the editor form validateRecordForm reads. */
export function formFromRecord(record) {
  const r = obj(record);
  const side = (s) => {
    const e = obj(r[s]);
    return { athlete_id: str(e.athlete_id), name: str(e.athlete_id) ? '' : str(e.name) };
  };
  const res = obj(r.result);
  return {
    date: str(r.date),
    upper: side('upper'),
    lower: side('lower'),
    winner: SIDES.includes(res.winner) ? res.winner : '',
    sets: setsText(res.sets),
  };
}

function registryName(athletes, id) {
  const a = (Array.isArray(obj(athletes).athletes) ? athletes.athletes : []).find((x) => x && x.id === id);
  return a && typeof a.name === 'string' && a.name ? a.name : null;
}

/**
 * Pure: the peek summary for one stem from its match record, its insights sidecar and the athlete registry.
 * {stem, date, side, first, names{upper, lower}, winner, result 'W'|'L'|null, sets (oriented to `first`), techIndex, techBySide,
 *  doubles, rally{count, label, suppressed, signal, failed, available}, strokes{classified,total}|null}.
 * A doubles run (sidecar `match_format: 'doubles'`) has no per-player score: techIndex / techBySide stay null whatever
 * the sidecar holds, and the peek says so instead.
 */
export function peekModel({ stem, record, insights, athletes, athleteId } = {}) {
  const rec = obj(record);
  const ins = obj(insights);
  const names = {};
  for (const s of SIDES) {
    const e = obj(rec[s]);
    names[s] = (typeof e.athlete_id === 'string' && registryName(athletes, e.athlete_id)) || str(e.name) || null;
  }
  const side = athleteId ? SIDES.find((s) => obj(rec[s]).athlete_id === athleteId) || null : null;
  const res = obj(rec.result);
  const winner = SIDES.includes(res.winner) ? res.winner : null;
  const sets = Array.isArray(res.sets) ? res.sets.filter((p) => Array.isArray(p) && p.length === 2) : [];
  const doubles = ins.match_format === 'doubles';
  const bySide = doubles ? {} : obj(obj(obj(ins.technique).value).by_side);
  const idx = (s) => (isNum(obj(bySide[s]).index) ? bySide[s].index : null);
  const rIns = obj(ins.rally);
  const rv = obj(rIns.value);
  const rallyOk = rIns.status !== 'unavailable' && ['rallies', 'segments', 'windows'].includes(rv.label);
  const sIns = obj(ins.strokes);
  const sv = obj(sIns.value);
  return {
    stem: str(stem),
    date: str(rec.date) || str(obj(ins.date).value) || null,
    // A record date is the user's own; only a sidecar date from a file mtime is approximate.
    dateApprox: !str(rec.date) && isApproxDate(obj(ins.date).value, obj(ins.date).source),
    side,
    doubles,
    names,
    winner,
    result: side && winner ? (winner === side ? 'W' : 'L') : null,
    // Sets read from the first-named side (the athlete's, else the lower side, as the title does).
    first: side || 'lower',
    sets: (side || 'lower') === 'lower' ? sets.map((p) => [p[1], p[0]]) : sets,
    techIndex: side ? idx(side) : null,
    techBySide: { upper: idx('upper'), lower: idx('lower') },
    rally: rallyOk
      ? { available: true, count: isNum(rv.count) ? rv.count : 0, label: rv.label, suppressed: !!rv.suppressed || rv.label !== 'rallies', signal: typeof rv.signal === 'string' ? rv.signal : null, failed: false }
      : { available: false, count: null, label: null, suppressed: false, signal: null, failed: false },
    strokes: sIns.status !== 'unavailable' && isNum(sv.total) ? { classified: isNum(sv.classified) ? sv.classified : 0, total: sv.total } : null,
  };
}

/**
 * Pure: {title, sub} for a matches row. With a name on either side the title is "A vs. B" (a missing name
 * falls back to its side label) and the video name is the subtitle; with no names at all every row would read
 * "Lower side vs. Upper side", so the video name is the title and the side labels the muted subtitle.
 */
export function rowTitle(row, lang) {
  const r = obj(row);
  const a = r.side || 'lower';
  const sides = { a: t('ws.side.' + a, lang), b: t('ws.side.' + otherSide(a), lang) };
  if (!r.athlete && !r.opponent) return { title: str(r.stem), sub: t('mv.sides_sub', lang, sides) };
  return { title: t('mv.vs', lang, { a: r.athlete || sides.a, b: r.opponent || sides.b }), sub: str(r.stem) };
}

// ---------- shared DOM helpers ----------

const num1 = (v) => (isNum(v) ? String(+v.toFixed(1)) : '—');
const sideLabel = (s, lang) => t('ws.side.' + s, lang);

function langOf(ctx) {
  return obj(ctx).lang === 'en' ? 'en' : 'zh';
}

function storeState(ctx) {
  const st = obj(ctx).store;
  try {
    return st && typeof st.get === 'function' ? obj(st.get()) : {};
  } catch (e) {
    return {};
  }
}

function go(ctx, anchor, hash) {
  if (typeof obj(ctx).transitionTo === 'function') ctx.transitionTo(anchor, hash);
  else if (typeof location !== 'undefined') location.hash = hash;
}

/**
 * The match peek drawer content (canvas Main: peek). `row` is {stem} or an overview match row; the rest is
 * fetched (sidecar + record) and filled in. ctx: the shell ctx plus `anchorEl` (the row that opened it), used
 * as the origin of the container transform into the workspace. Returns a Node for ctx.openPeek().
 */
export function buildMatchPeek(row, ctx = {}) {
  const lang = langOf(ctx);
  const stem = str(obj(row).stem);
  const root = h('div', { class: 'mp' });
  const hash = formatHash('matches', { stem });

  /** mode: 'loading' | 'ok' | 'failed' (both fetches failed) | 'partial' (the record could not be read). */
  function paint(pm, mode) {
    const loading = mode === 'loading';
    const had = document.activeElement && root.contains(document.activeElement)
      ? (document.activeElement.classList.contains('mp-open') ? '.mp-open' : document.activeElement.hasAttribute('data-peek-close') ? '[data-peek-close]' : null)
      : null;
    root.textContent = '';
    const [a, b] = [pm.first, otherSide(pm.first)];
    const nm = (s) => pm.names[s] || sideLabel(s, lang);
    root.appendChild(h('div', null, [
      h('div', { class: 'mono k-label' }, [t('peek.match', lang, { date: fmtDate(pm.date, lang).toUpperCase() }),
        pm.dateApprox ? approxMark(lang) : null]),
      h('h2', { class: 'mp-title' }, t('peek.title', lang, { a: nm(a), b: nm(b) })),
    ]));
    const chips = h('div', { class: 'mp-chips' });
    if (pm.result) chips.appendChild(h('span', { class: 'k-chip ' + (pm.result === 'W' ? 'good' : 'bad') }, t(pm.result === 'W' ? 'ws.result.win' : 'ws.result.loss', lang)));
    else if (pm.winner) chips.appendChild(h('span', { class: 'k-chip' }, t('result.side_won', lang, { name: nm(pm.winner) })));
    else if (mode === 'ok') chips.appendChild(h('span', { class: 'k-chip' }, t('matches.no_result', lang)));
    if (pm.doubles) chips.appendChild(h('span', { class: 'k-chip' }, t('doubles.chip', lang)));
    if (mode === 'failed' || mode === 'partial') chips.appendChild(h('span', { class: 'kw-foot-muted', role: 'status' }, t('peek.load_failed', lang)));
    if (pm.sets.length) chips.appendChild(h('span', { class: 'mono kw-muted' }, pm.sets.map((p) => `${p[0]}–${p[1]}`).join(' · ')));
    root.appendChild(chips);

    const dash = loading ? t('state.loading', lang) : '—';
    const r = pm.rally;
    const rallyHead = !r.available ? t('peek.segments', lang) : r.label === 'rallies' ? t('peek.rallies', lang) : r.label === 'windows' ? t('peek.windows', lang) : t('peek.segments', lang);
    const tech = pm.side ? num1(pm.techIndex)
      : [pm.techBySide.lower, pm.techBySide.upper].some(isNum) ? `${num1(pm.techBySide[a])} · ${num1(pm.techBySide[b])}` : '—';
    const tile = (label, value, sub, i) => h('div', { class: 'mp-tile', style: { animationDelay: `${(0.12 + i * 0.08).toFixed(2)}s` } }, [
      h('div', { class: 'kw-muted' }, label),
      h('div', { class: 'mono mp-val' }, value),
      sub ? h('div', { class: 'mp-sub' }, sub) : null,
    ]);
    const sig = r.signal ? t('signal.' + r.signal, lang) : '';
    root.appendChild(h('div', { class: 'mp-grid' }, [
      tile(t('peek.tech', lang), loading && tech === '—' ? dash : tech, pm.doubles ? t('reason.doubles_run', lang) : null, 0),
      tile(rallyHead, r.available ? fmtNumber(r.count) : dash, r.available ? rallyCountText(r, lang) : null, 1),
      tile(t('peek.strokes', lang), pm.strokes ? fmtNumber(pm.strokes.total) : dash,
        pm.strokes ? t('tile.classified_of', lang, { classified: fmtNumber(pm.strokes.classified), total: fmtNumber(pm.strokes.total) }) : null, 2),
      tile(t('peek.signal', lang), r.available ? (sig && sig !== 'signal.' + r.signal ? sig : '—') : dash, null, 3),
    ]));
    root.appendChild(h('div', { class: 'mp-box' }, [
      h('div', { class: 'mono k-label' }, t('peek.athletes', lang)),
      h('div', null, [
        `${sideLabel('lower', lang)}: `, h('strong', null, pm.names.lower || '—'), ' · ',
        `${sideLabel('upper', lang)}: `, h('strong', null, pm.names.upper || '—'),
      ]),
    ]));
    root.appendChild(h('p', { class: 'mp-hint' }, t('peek.hint', lang)));
    const open = h('a', {
      href: hash, class: 'ov-btn primary mp-open',
      onclick: (e) => {
        if (e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
        e.preventDefault();
        const anchor = ctx.anchorEl && ctx.anchorEl.isConnected ? ctx.anchorEl : null;
        if (typeof ctx.closePeek === 'function') ctx.closePeek();
        go(ctx, anchor, hash);
      },
    }, t('peek.open', lang));
    root.appendChild(h('div', { class: 'mp-actions' }, [open, h('button', { type: 'button', class: 'ov-btn', 'data-peek-close': '' }, t('btn.close', lang))]));
    // A repaint replaces the buttons: keep keyboard focus where it was.
    const back = had ? root.querySelector(had) : null;
    if (back) back.focus();
  }

  const s = storeState(ctx);
  const athleteId = obj(s.scope).athlete || s.athlete || '';
  const seed = obj(row);
  const base = peekModel({ stem, record: {}, insights: null, athletes: s.athletes, athleteId });
  if (seed.athlete || seed.opponent) {
    const sd = SIDES.includes(seed.side) ? seed.side : 'lower';
    base.names = { [sd]: str(seed.athlete) || null, [otherSide(sd)]: str(seed.opponent) || null };
  }
  if (seed.date) {
    base.date = seed.date;
    base.dateApprox = isApproxDate(seed.date, seed.date_source);
  }
  paint(base, 'loading');

  // ctx.signal (main.js aborts it when the drawer closes) cancels both fetches.
  const signal = ctx.signal || null;
  const A = ctx.api || api;
  if (stem && typeof A.videoInsights === 'function') {
    const o = { ...LONG, ...(signal ? { signal } : {}) };
    const settle = (p) => p.then((data) => ({ ok: true, data }), (error) => ({ ok: false, error }));
    Promise.all([settle(A.videoInsights(stem, o)), settle(A.match(stem, o))]).then(([ins, rec]) => {
      if ((signal && signal.aborted) || [ins, rec].some((r) => !r.ok && r.error && r.error.kind === 'aborted')) return;
      // GET /api/matches returns {} without a record; a 404 is an unknown video, i.e. no record either.
      const recOk = rec.ok || (rec.error && rec.error.status === 404);
      const mode = !ins.ok && !recOk ? 'failed' : !recOk ? 'partial' : 'ok';
      const pm = peekModel({ stem, record: rec.ok ? rec.data : {}, insights: ins.ok ? ins.data : null, athletes: storeState(ctx).athletes, athleteId });
      if (mode === 'failed') {
        pm.names = base.names;
        pm.date = base.date;
        pm.dateApprox = base.dateApprox;
      }
      paint(pm, mode);
    });
  }
  return root;
}

// ---------- the Matches view ----------

function reducedMotion(ctx) {
  try {
    const r = obj(ctx).reduced;
    if (typeof r === 'function') return !!r();
    if (typeof r === 'boolean') return r;
    return typeof matchMedia === 'function' && matchMedia('(prefers-reduced-motion: reduce)').matches;
  } catch (e) {
    return false;
  }
}

/** Mount the matches list. Returns {destroy(), update(ctx)}. */
export function mount(container, ctx = {}) {
  const noop = { destroy() {}, update() {} };
  if (!container || !hasDOM()) return noop;
  const lang = langOf(ctx);
  const state = { ctx, rows: null, error: null, editing: null, destroyed: false, scopeKey: null };
  let ctl = null;
  let reqId = 0;
  let animated = [];

  const root = h('div', { class: 'page mv', 'data-view': 'matches' });
  container.appendChild(root);
  const clock = createClock({
    reduced: () => reducedMotion(ctx),
    onFrame: (p) => animated.forEach((el, i) => revealStyle(el, local(p, i === 0 ? 0 : 1 + i * 0.3), { dy: 10 })),
  });

  function scope() {
    const q = obj(state.ctx.query);
    const s = storeState(state.ctx);
    return { athlete: q.athlete || s.athlete || '', squad: q.squad || s.squad || '' };
  }


  function header() {
    const n = state.rows ? state.rows.length : 0;
    return h('div', { class: 'ov-head' }, [
      h('div', { class: 'ov-head-l' }, [
        h('div', { class: 'mono ov-eyebrow' }, t('mv.eyebrow', lang)),
        h('h1', { class: 'page-title serif' }, t('nav.matches', lang)),
        state.rows ? h('div', { class: 'ov-summary' }, t('mv.sub', lang, { n: fmtNumber(n) })) : null,
      ]),
      h('div', { class: 'ov-actions' }, [h('a', { href: '#/new', class: 'ov-btn primary' }, t('btn.new_analysis', lang))]),
    ]);
  }

  function resultChip(r) {
    if (r.result) return h('span', { class: 'mono kw-res ' + (r.result === 'W' ? 'win' : 'loss') }, t('result.' + r.result, lang));
    return h('span', { class: 'mono kw-res none' }, t('matches.add_result', lang));
  }

  function statusChip(r) {
    const tone = r.status === 'analysed' ? 'good' : r.status === 'needs_review' ? 'mid' : r.status === 'processing' ? 'cyan' : '';
    return h('span', { class: 'k-chip' + (tone ? ' ' + tone : '') }, [h('span', { class: 'kw-dot', 'aria-hidden': 'true' }), t('status.' + r.status, lang)]);
  }

  function openRow(r, anchor) {
    go(state.ctx, anchor, formatHash('matches', { stem: r.stem }));
  }

  function render({ reveal = false } = {}) {
    if (state.destroyed) return;
    root.textContent = '';
    const head = header();
    root.appendChild(head);
    animated = [head];
    if (state.error && !state.rows) {
      const card = h('div', { class: 'card ov-state', role: 'alert' }, [
        h('div', { class: 'k-label' }, t('error.load.title', lang)),
        h('div', { class: 'ov-state-body' }, errorText(state.error, lang)),
        h('button', { type: 'button', class: 'ov-btn small', onclick: () => load() }, t('btn.retry', lang)),
      ]);
      root.appendChild(card);
      return;
    }
    if (!state.rows) {
      root.appendChild(h('div', { class: 'card ov-state', role: 'status', 'aria-busy': 'true' }, [
        h('div', { class: 'k-label' }, t('state.loading', lang)), h('div', { class: 'ov-skel' }),
      ]));
      return;
    }
    if (state.error) {
      root.appendChild(h('div', { class: 'ov-alert', role: 'alert' }, [
        h('strong', null, t('error.refresh.title', lang)), h('span', null, errorText(state.error, lang)),
        h('button', { type: 'button', class: 'ov-btn small', onclick: () => load() }, t('btn.retry', lang)),
      ]));
    }
    if (!state.rows.length) {
      const sc = scope();
      root.appendChild(h('div', { class: 'card ov-state ov-empty' }, [
        h('div', { class: 'ov-empty-title' }, t(sc.athlete || sc.squad ? 'matches.empty.title' : 'mv.empty.title', lang)),
        h('div', { class: 'ov-state-body' }, t(sc.athlete || sc.squad ? 'matches.empty.body' : 'mv.empty.body', lang)),
        h('a', { href: '#/new', class: 'ov-btn primary' }, t('btn.new_analysis', lang)),
      ]));
      return;
    }
    const th = (key, cls) => h('th', { scope: 'col', class: cls || null }, t(key, lang));
    const body = h('tbody');
    state.rows.forEach((r) => {
      const { title: name, sub } = rowTitle(r, lang);
      const tr = h('tr', {
        class: 'trow mv-row' + (state.editing === r.stem ? ' open' : ''),
        onclick: (e) => {
          if (e.target && typeof e.target.closest === 'function' && e.target.closest('button,a,input,select')) return;
          openRow(r, tr);
        },
      });
      const openBtn = h('button', { type: 'button', class: 'kw-row-open', 'aria-label': t('mv.open_aria', lang, { name }), onclick: () => openRow(r, tr) }, name);
      const recBtn = h('button', {
        type: 'button', class: 'ov-btn small', 'aria-expanded': state.editing === r.stem ? 'true' : 'false',
        'aria-label': t('mv.record_aria', lang, { name }), 'data-rec': r.stem,
        onclick: () => toggleEditor(r.stem),
      }, t('mv.record_btn', lang));
      [
        h('td', { class: 'mv-name' }, [openBtn, r.doubles ? h('span', { class: 'k-chip kw-doubles' }, t('doubles.chip', lang)) : null, h('div', { class: 'mv-sub' }, sub)]),
        h('td', { class: 'mono kw-muted kw-col-date' }, [fmtDate(r.date, lang), r.dateApprox ? approxMark(lang) : null]),
        h('td', null, resultChip(r)),
        h('td', { class: 'mono kw-muted' }, r.sets.length ? r.sets.map((p) => `${p[0]}–${p[1]}`).join(' · ') : '—'),
        h('td', { class: 'mono kw-num' }, num1(r.techIndex)),
        h('td', null, statusChip(r)),
        h('td', { class: 'kw-num' }, recBtn),
      ].forEach((td) => tr.appendChild(td));
      body.appendChild(tr);
      animated.push(tr);
      if (state.editing === r.stem) {
        const cell = h('td', { colspan: '7', class: 'mv-edit-cell' });
        body.appendChild(h('tr', { class: 'mv-edit-row' }, cell));
        buildEditor(cell, r, name);
      }
    });
    const table = h('table', { class: 'table kw-table mv-table' }, [
      h('thead', null, h('tr', null, [
        th('mv.col.match'), th('matches.col.date', 'kw-col-date'), th('matches.col.result'), th('mv.col.sets'), th('matches.col.tech', 'kw-num'),
        th('matches.col.status'), th('mv.col.record', 'kw-num'),
      ])),
      body,
    ]);
    root.appendChild(h('section', { class: 'card hud mv-card', 'aria-label': t('nav.matches', lang) }, h('div', { class: 'kw-scroll' }, table)));
    if (reveal) clock.start();
    else { clock.cancel(); animated.forEach((el) => revealStyle(el, 1)); }
  }

  function toggleEditor(stem) {
    state.editing = state.editing === stem ? null : stem;
    render();
    const safe = String(stem).replace(/["\\]/g, '\\$&');
    const btn = root.querySelector(`[data-rec="${safe}"]`);
    if (state.editing) {
      const first = root.querySelector('.mv-edit-cell input, .mv-edit-cell select');
      if (first && typeof first.focus === 'function') first.focus();
    } else if (btn) btn.focus();
  }

  // ----- record editor -----
  function sideControl(side, form, athletes, errId) {
    const list = Array.isArray(obj(athletes).athletes) ? athletes.athletes : [];
    const cur = obj(form[side]);
    const known = cur.athlete_id && list.some((a) => a && a.id === cur.athlete_id);
    const initial = known ? cur.athlete_id : cur.name || cur.athlete_id ? OTHER : '';
    const sel = h('select', { class: 'mv-input', id: `mv-${side}`, 'aria-describedby': errId });
    sel.appendChild(h('option', { value: '' }, t('record.unspecified', lang)));
    list.forEach((a) => { if (a && a.id) sel.appendChild(h('option', { value: a.id }, String(a.name || a.id))); });
    sel.appendChild(h('option', { value: OTHER }, t('record.athlete_other', lang)));
    sel.value = initial;
    const input = h('input', {
      type: 'text', class: 'mv-input', maxlength: String(NAME_MAX), placeholder: t('record.name_placeholder', lang),
      'aria-label': `${t('record.' + side, lang)} · ${t('record.name_placeholder', lang)}`, value: cur.name || '',
    });
    input.hidden = sel.value !== OTHER;
    sel.addEventListener('change', () => { input.hidden = sel.value !== OTHER; if (!input.hidden) input.focus(); });
    return {
      el: h('div', { class: 'mv-field' }, [h('label', { for: `mv-${side}` }, t('record.' + side, lang)), sel, input,
        h('span', { class: 'mv-err', id: errId, role: 'alert' })]),
      read: () => ({ athlete_id: sel.value && sel.value !== OTHER ? sel.value : '', name: sel.value === OTHER ? input.value : '' }),
      sel,
    };
  }

  function buildEditor(cell, row, name) {
    const box = h('div', { class: 'mv-editor', role: 'group', 'aria-label': t('record.title', lang, { name }) });
    box.appendChild(h('div', { class: 'k-label' }, t('record.title', lang, { name })));
    const status = h('div', { class: 'kw-note', role: 'status' }, t('state.loading', lang));
    box.appendChild(status);
    cell.appendChild(box);
    const stem = row.stem;
    const s0 = storeState(state.ctx);
    const needAthletes = s0.athletesStatus !== 'ok' && typeof state.ctx.refreshAthletes === 'function'
      ? state.ctx.refreshAthletes().catch(() => null) : Promise.resolve(null);
    Promise.all([
      api.match(stem, LONG).then((r) => ({ ok: true, rec: obj(r) })).catch((e) => (e && e.status === 404 ? { ok: true, rec: {} } : { ok: false, err: e })),
      needAthletes,
    ]).then(([res]) => {
      if (state.destroyed || state.editing !== stem || !box.isConnected) return;
      if (!res.ok) {
        status.textContent = errorText(res.err, lang);
        box.appendChild(h('button', { type: 'button', class: 'ov-btn small', onclick: () => { state.editing = null; toggleEditor(stem); } }, t('btn.retry', lang)));
        return;
      }
      status.textContent = Object.keys(res.rec).length ? '' : t('record.none_yet', lang);
      fillEditor(box, row, formFromRecord(res.rec), Object.keys(res.rec).length > 0);
    });
  }

  function fillEditor(box, row, form, hasRecord) {
    const athletes = storeState(state.ctx).athletes;
    const errDate = `mv-err-date-${Math.random().toString(36).slice(2, 8)}`;
    const date = h('input', { type: 'date', class: 'mv-input', id: 'mv-date', value: form.date, 'aria-describedby': errDate });
    const upper = sideControl('upper', form, athletes, errDate + '-u');
    const lower = sideControl('lower', form, athletes, errDate + '-l');
    const winner = h('select', { class: 'mv-input', id: 'mv-winner', 'aria-describedby': errDate + '-w' }, [
      h('option', { value: '' }, t('record.winner_none', lang)),
      h('option', { value: 'upper' }, t('side.upper', lang)),
      h('option', { value: 'lower' }, t('side.lower', lang)),
    ]);
    winner.value = form.winner;
    const sets = h('input', { type: 'text', class: 'mv-input mono', id: 'mv-sets', value: form.sets, placeholder: '21-18, 19-21, 21-15', 'aria-describedby': `${errDate}-s ${errDate}-sh` });
    const err = (id) => h('span', { class: 'mv-err', id, role: 'alert' });
    const errs = { date: err(errDate), winner: err(errDate + '-w'), sets: err(errDate + '-s') };
    const formErr = h('div', { class: 'mv-err form', role: 'alert' });
    const saveBtn = h('button', { type: 'submit', class: 'ov-btn small primary' }, t('btn.save', lang));
    const fields = h('div', { class: 'mv-fields' }, [
      h('div', { class: 'mv-field' }, [h('label', { for: 'mv-date' }, t('record.date', lang)), date, errs.date]),
      upper.el,
      lower.el,
      h('div', { class: 'mv-field' }, [h('label', { for: 'mv-winner' }, t('record.winner', lang)), winner, errs.winner]),
      h('div', { class: 'mv-field wide' }, [h('label', { for: 'mv-sets' }, t('record.sets', lang)), sets,
        h('span', { class: 'mv-hint', id: errDate + '-sh' }, t('record.sets_order', lang)), errs.sets]),
    ]);
    const formEl = h('form', { class: 'mv-form', novalidate: true }, [
      fields, formErr,
      h('div', { class: 'mv-actions' }, [
        saveBtn,
        hasRecord ? h('button', {
          type: 'button', class: 'ov-btn small danger',
          onclick: () => { if (typeof confirm !== 'function' || confirm(t('record.clear_confirm', lang))) submit({}); },
        }, t('record.clear', lang)) : null,
        h('button', { type: 'button', class: 'ov-btn small', onclick: () => toggleEditor(row.stem) }, t('btn.cancel', lang)),
      ]),
    ]);
    const sideErr = { upper: upper.el.querySelector('.mv-err'), lower: lower.el.querySelector('.mv-err') };
    const ctlOf = { date, upper: upper.sel, lower: lower.sel, winner, sets };

    function showErrors(errors) {
      for (const k of ['date', 'winner', 'sets']) errs[k].textContent = errors[k] ? t(errors[k], lang) : '';
      for (const k of SIDES) sideErr[k].textContent = errors[k] ? t(errors[k], lang) : '';
      for (const [k, c] of Object.entries(ctlOf)) c.setAttribute('aria-invalid', errors[k] ? 'true' : 'false');
      const firstBad = Object.keys(ctlOf).find((k) => errors[k]);
      if (firstBad) ctlOf[firstBad].focus();
    }

    async function submit(record) {
      formErr.textContent = '';
      saveBtn.disabled = true;
      try {
        await api.saveMatch(row.stem, record, LONG);
        if (state.destroyed) return;
        if (typeof state.ctx.toast === 'function') state.ctx.toast(t('toast.saved', lang));
        state.editing = null;
        await load({ reveal: false });
        const safe = String(row.stem).replace(/["\\]/g, '\\$&');
        const btn = root.querySelector(`[data-rec="${safe}"]`);
        if (btn) btn.focus();
      } catch (e) {
        if (state.destroyed) return;
        formErr.textContent = errorText(e, lang);
        saveBtn.disabled = false;
      }
    }

    formEl.addEventListener('submit', (e) => {
      e.preventDefault();
      const out = validateRecordForm({ date: date.value, upper: upper.read(), lower: lower.read(), winner: winner.value, sets: sets.value });
      showErrors(out.errors);
      if (out.record) submit(out.record);
    });
    box.appendChild(formEl);
    const first = formEl.querySelector('input,select');
    if (first) first.focus();
  }

  // ----- data -----
  async function load({ reveal = true } = {}) {
    const sc = scope();
    const mine = ++reqId;
    if (ctl && typeof ctl.abort === 'function') ctl.abort();
    ctl = typeof AbortController === 'function' ? new AbortController() : null;
    if (!state.rows) render();
    try {
      const data = await api.overview({ athlete: sc.athlete, squad: sc.squad, range: 'all' }, { ...LONG, ...(ctl ? { signal: ctl.signal } : {}) });
      if (state.destroyed || mine !== reqId) return;
      state.rows = matchRows(data);
      state.error = null;
      state.scopeKey = sc.athlete + '|' + sc.squad;
      if (state.editing && !state.rows.some((r) => r.stem === state.editing)) state.editing = null;
      render({ reveal });
    } catch (err) {
      if (state.destroyed || mine !== reqId || (err && err.kind === 'aborted')) return;
      state.error = err || { kind: 'network' };
      render({ reveal: false });
    }
  }

  load();

  return {
    update(next) {
      if (state.destroyed) return;
      state.ctx = next || state.ctx;
      const sc = scope();
      if (sc.athlete + '|' + sc.squad !== state.scopeKey) {
        state.editing = null;
        load();
      }
    },
    destroy() {
      if (state.destroyed) return;
      state.destroyed = true;
      clock.cancel();
      if (ctl && typeof ctl.abort === 'function') ctl.abort();
      if (root.parentNode) root.parentNode.removeChild(root);
    },
  };
}
