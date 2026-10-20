// Pipeline view (#/pipeline, spec 5.5 and 6.4): the /api/jobs list with progress bars, stage names and status
// chips, polled every 2 s while any job is in flight and every 15 s otherwise. Detached runs (found on disk)
// and failures are labelled as such; finished match runs link to their results. groupJobs() is pure and tested.
import { t } from '../core/i18n.js';
import { ago } from '../core/format.js';
import { formatHash } from '../core/router.js';
import { api, errorText } from '../core/api.js';
import { h, hasDOM, setStyle, setText } from '../widgets/frame.js';
import { ACTIVE_STATUSES, RUNNING_STATUSES as RUNNING, QUEUED_STATUSES, jobStage } from '../widgets/pipeline.js';

export const FAST_MS = 2000;
export const SLOW_MS = 15000;

const isNum = (v) => typeof v === 'number' && Number.isFinite(v);
const obj = (v) => (v && typeof v === 'object' && !Array.isArray(v) ? v : {});

function stemOf(video) {
  const s = typeof video === 'string' ? video : '';
  const base = s.split(/[\\/]/).pop() || '';
  return base.replace(/\.(mp4|mov|avi|mkv|webm|m4v)$/i, '');
}

function toneOf(status) {
  if (status === 'completed') return 'good';
  if (status === 'error') return 'bad';
  if (status === 'pending') return 'queued';
  return 'accent';
}

/**
 * Pure: jobs list (or {jobs}) -> {active, finished, running, queued, anyActive}. Rows keep the server's
 * newest-first order: {id, stem, kind, status, stage, progress 0-100, message, updated, tone, active,
 * resultsHash ('#/results/<stem>?mode=<kind>' for a completed run, else null)}.
 */
export function groupJobs(jobs) {
  const list = Array.isArray(jobs) ? jobs : Array.isArray(obj(jobs).jobs) ? jobs.jobs : [];
  const active = [];
  const finished = [];
  let running = 0;
  let queued = 0;
  for (const j of list) {
    if (!j || typeof j !== 'object') continue;
    const status = typeof j.status === 'string' ? j.status : '';
    const isActive = ACTIVE_STATUSES.includes(status);
    if (RUNNING.includes(status)) running += 1;
    if (QUEUED_STATUSES.includes(status)) queued += 1;
    let progress = isNum(j.progress) ? Math.max(0, Math.min(100, Math.round(j.progress))) : 0;
    if (status === 'completed') progress = 100;
    const stem = stemOf(j.video);
    const kind = j.kind === 'posture' ? 'posture' : 'match';
    const row = {
      id: String(j.id === undefined || j.id === null ? '' : j.id),
      stem,
      kind,
      status,
      stage: jobStage(j),
      progress,
      message: typeof j.message === 'string' ? j.message : '',
      updated: isNum(j.updated) ? j.updated : null,
      tone: toneOf(status),
      active: isActive,
      resultsHash: status === 'completed' && stem ? formatHash('results', { stem }, { mode: kind }) : null,
    };
    (isActive ? active : finished).push(row);
  }
  return { active, finished, running, queued, anyActive: active.length > 0 };
}

/** A server message, in the UI language when it is a "中文 / English" pair. */
export function messageText(message, lang) {
  const s = typeof message === 'string' ? message : '';
  const m = /^(.+?) \/ (.+)$/.exec(s);
  if (m && /[一-鿿]/.test(m[1])) return (lang === 'en' ? m[2] : m[1]).trim();
  return s;
}

function stageText(stage, lang) {
  if (!stage) return '';
  const key = 'stage.' + stage;
  const v = t(key, lang);
  return v !== key ? v : stage.replace(/_/g, ' ');
}

function stateText(row, lang) {
  if (row.status === 'pending') return t('job.state.queued', lang);
  if (row.status === 'completed') return t('job.state.done', lang);
  if (row.status === 'error') return t('job.state.error', lang);
  if (row.status === 'detached') return t('job.state.detached', lang);
  return t('job.state.running', lang, { pct: row.progress });
}

/** Pure: identity of a job list for in-place updates (order, id, status, stage; progress and message excluded). */
export function jobsKey(groups) {
  const g = obj(groups);
  return [...(Array.isArray(g.active) ? g.active : []), ...(Array.isArray(g.finished) ? g.finished : [])]
    .map((r) => `${r.id}:${r.status}:${r.stage}`).join('|');
}

/**
 * Poll loop with injectable timers: fetch(), then onData(data) / onError(err), each returning whether a job is
 * in flight; the next poll is FAST_MS away while one is (and the page is visible), SLOW_MS otherwise. A poll
 * never overlaps another; stop() cancels the pending timer and ignores any response still in flight.
 * Returns {poll(), stop(), isStopped()}.
 */
export function createPoller({ fetch, onData, onError, isHidden = () => false, setTimer = setTimeout, clearTimer = clearTimeout } = {}) {
  let timer = null;
  let inFlight = false;
  let stopped = false;
  function schedule(active) {
    if (stopped) return;
    timer = setTimer(poll, active && !isHidden() ? FAST_MS : SLOW_MS);
  }
  async function poll() {
    if (timer !== null) clearTimer(timer);
    timer = null;
    if (inFlight || stopped) return;
    inFlight = true;
    let active = false;
    try {
      const data = await fetch();
      if (stopped) return;
      active = !!onData(data);
    } catch (e) {
      if (stopped) return;
      active = typeof onError === 'function' ? !!onError(e) : false;
    } finally {
      inFlight = false;
    }
    schedule(active);
  }
  function stop() {
    stopped = true;
    if (timer !== null) clearTimer(timer);
    timer = null;
  }
  return { poll, stop, isStopped: () => stopped };
}

/** Mount the pipeline view. Returns {destroy()}. */
export function mount(container, ctx = {}) {
  const noop = { destroy() {} };
  if (!container || !hasDOM()) return noop;
  const lang = ctx.lang === 'en' ? 'en' : 'zh';
  const state = { groups: null, error: null, destroyed: false };
  const root = h('div', { class: 'page pv', 'data-view': 'pipeline' });
  container.appendChild(root);
  const bars = new Map(); // job id -> {bar, pctEl, stateEl, row}

  function header() {
    const g = state.groups;
    return h('div', { class: 'ov-head' }, [
      h('div', { class: 'ov-head-l' }, [
        h('div', { class: 'mono ov-eyebrow' }, [h('span', { class: 'ov-sync-dot', 'aria-hidden': 'true' }), t('pv.eyebrow', lang)]),
        h('h1', { class: 'page-title serif' }, t('nav.pipeline', lang)),
        g ? h('div', { class: 'ov-summary' }, t('pv.sub', lang, { running: g.running, queued: g.queued, sec: (g.anyActive ? FAST_MS : SLOW_MS) / 1000 })) : null,
      ]),
      h('div', { class: 'ov-actions' }, [h('a', { href: '#/new', class: 'ov-btn primary' }, t('btn.new_analysis', lang))]),
    ]);
  }

  function jobRow(r) {
    const title = stageText(r.stage, lang) || t('job.kind.' + r.kind, lang);
    const bar = h('i', { class: 'kw-bar-' + r.tone, style: { width: r.progress + '%' } });
    const stateEl = h('span', { class: 'k-chip ' + (r.tone === 'queued' ? '' : r.tone) }, stateText(r, lang));
    // Message and "updated" text always get a node, so a poll can change them in place.
    const msgEl = h('div', { class: 'pv-msg' + (r.status === 'error' ? ' bad' : '') });
    const updEl = h('span', { class: 'mono kw-muted' });
    const li = h('li', { class: 'pv-job' + (r.status === 'error' ? ' err' : '') }, [
      h('div', { class: 'pv-top' }, [
        h('div', { class: 'pv-titles' }, [
          h('span', { class: 'kw-job-title' }, title),
          h('span', { class: 'kw-job-target' }, [r.stem || '—', ' · ', t('job.kind.' + r.kind, lang)]),
        ]),
        stateEl,
      ]),
      r.status === 'completed' ? null : h('div', {
        class: 'bar', role: 'progressbar', 'aria-valuenow': String(r.progress), 'aria-valuemin': '0', 'aria-valuemax': '100',
        'aria-label': t('job.progress_aria', lang, { stage: title }),
      }, bar),
      r.status === 'detached' ? h('div', { class: 'kw-foot-muted' }, t('pv.detached_note', lang)) : null,
      msgEl,
      h('div', { class: 'pv-foot' }, [
        updEl,
        r.resultsHash ? h('a', { href: r.resultsHash, class: 'kw-link', 'data-xf': '' }, t('pv.open_results', lang)) : null,
      ]),
    ]);
    const refs = { bar, stateEl, msgEl, updEl, row: r };
    paintText(refs, r);
    bars.set(r.id, refs);
    return li;
  }

  function paintText(refs, r) {
    const msg = messageText(r.message, lang);
    setText(refs.msgEl, msg);
    refs.msgEl.hidden = !msg;
    setText(refs.updEl, r.updated !== null ? t('pv.updated', lang, { time: ago(Math.max(0, Date.now() / 1000 - r.updated)) }) : '');
    refs.updEl.hidden = r.updated === null;
  }

  function section(key, rows) {
    return h('section', { class: 'card hud pv-sec', 'aria-label': t(key, lang) }, [
      h('h2', { class: 'card-title' }, t(key, lang)),
      h('ul', { class: 'pv-list' }, rows.map(jobRow)),
    ]);
  }

  function render() {
    if (state.destroyed) return;
    root.textContent = '';
    bars.clear();
    root.appendChild(header());
    const g = state.groups;
    if (state.error) {
      root.appendChild(h('div', { class: g ? 'ov-alert' : 'card ov-state', role: 'alert' }, [
        h('strong', null, t(g ? 'error.refresh.title' : 'error.load.title', lang)),
        h('span', null, `${errorText(state.error, lang)} ${t('pv.load_error', lang)}`),
        h('button', { type: 'button', class: 'ov-btn small', onclick: () => poll() }, t('btn.retry', lang)),
      ]));
    }
    if (!g) {
      if (!state.error) root.appendChild(h('div', { class: 'card ov-state', role: 'status', 'aria-busy': 'true' }, h('div', { class: 'k-label' }, t('state.loading', lang))));
      return;
    }
    if (!g.active.length && !g.finished.length) {
      root.appendChild(h('div', { class: 'card ov-state ov-empty' }, [
        h('div', { class: 'ov-state-body' }, t('pv.empty', lang)),
        h('a', { href: '#/new', class: 'ov-btn primary' }, t('btn.new_analysis', lang)),
      ]));
      return;
    }
    if (g.active.length) root.appendChild(section('pv.active', g.active));
    if (g.finished.length) root.appendChild(section('pv.finished', g.finished));
  }

  /** Same job ids in the same order and the same statuses: update bars in place (no re-render, no flicker). */
  function patch(next) {
    const prev = state.groups;
    if (!prev || state.error) return false;
    if (jobsKey(prev) !== jobsKey(next)) return false;
    const rows = [...next.active, ...next.finished];
    if (!rows.every((r) => bars.has(r.id))) return false;
    for (const r of rows) {
      const b = bars.get(r.id);
      setStyle(b.bar, 'width', r.progress + '%');
      if (b.bar.parentNode) b.bar.parentNode.setAttribute('aria-valuenow', String(r.progress));
      setText(b.stateEl, stateText(r, lang));
      paintText(b, r);
      b.row = r;
    }
    state.groups = next;
    return true;
  }

  const poller = createPoller({
    fetch: () => api.jobs({ timeout: 10000 }),
    onData: (data) => {
      const next = groupJobs(data);
      const wasActive = !!(state.groups && state.groups.anyActive);
      if (!patch(next)) {
        state.groups = next;
        state.error = null;
        render();
      }
      // A job started or finished: let the shell's badge catch up now rather than at its next 15 s poll.
      if (typeof ctx.refreshJobs === 'function' && wasActive !== next.anyActive) ctx.refreshJobs();
      return next.anyActive;
    },
    onError: (e) => {
      state.error = e;
      render();
      return !!(state.groups && state.groups.anyActive);
    },
    isHidden: () => typeof document !== 'undefined' && !!document.hidden,
  });
  const poll = () => poller.poll();

  const onVisible = () => { if (typeof document !== 'undefined' && !document.hidden) poll(); };
  if (typeof document !== 'undefined' && typeof document.addEventListener === 'function') document.addEventListener('visibilitychange', onVisible);

  render();
  poll();

  return {
    destroy() {
      if (state.destroyed) return;
      state.destroyed = true;
      poller.stop();
      if (typeof document !== 'undefined' && typeof document.removeEventListener === 'function') document.removeEventListener('visibilitychange', onVisible);
      if (root.parentNode) root.parentNode.removeChild(root);
    },
  };
}
