// Analysis pipeline card, fed by GET /api/jobs (spec 5.5). Bars and percentages follow the real job progress;
// setJobs() swaps the rows in place when the view polls.
import { t } from '../core/i18n.js';
import { local } from '../core/motion.js';
import * as frame from './frame.js';
import { h, setStyle, setText } from './frame.js';

/** Job statuses that count as in flight (app.py: pending, running, reencoding; detached runs found on disk). */
export const ACTIVE_STATUSES = Object.freeze(['pending', 'running', 'reencoding', 'detached']);
/** The working subset of ACTIVE_STATUSES; the rest (QUEUED_STATUSES) are waiting to start. */
export const RUNNING_STATUSES = Object.freeze(['running', 'reencoding', 'detached']);
export const QUEUED_STATUSES = Object.freeze(['pending']);
const RUNNING = RUNNING_STATUSES;
const MAX_ROWS = 4;

const known = (key, lang) => {
  const v = t(key, lang);
  return v !== key ? v : null;
};

function basename(p) {
  const s = typeof p === 'string' ? p : '';
  const parts = s.split(/[\\/]/);
  return parts[parts.length - 1] || '';
}

/** Doubles steps inside the `visualizing` stage (app.py _PROGRESS_SUBSTAGES; progress.json `substage`). */
export const DOUBLES_STAGES = Object.freeze(['doubles_identity', 'doubles_heatmaps', 'doubles_video']);

/**
 * Pure: the stage a job is in, with the doubles sub-stages resolved. /api/jobs carries `stage` and a bilingual
 * message ("识别球员身份... / Identifying players... 40/100 帧"); a `substage` field wins when present, else the
 * message's Chinese prefix names the sub-stage (the zh stage strings match app.py's). null when there is none.
 */
export function jobStage(job) {
  const j = job && typeof job === 'object' ? job : {};
  if (DOUBLES_STAGES.includes(j.substage)) return j.substage;
  const stage = typeof j.stage === 'string' && j.stage ? j.stage : null;
  if (stage === 'visualizing' && typeof j.message === 'string') {
    const hit = DOUBLES_STAGES.find((s) => j.message.startsWith(t('stage.' + s, 'zh')));
    if (hit) return hit;
  }
  return stage;
}

/** Pure: jobs list -> {rows:[{id,title,target,state,pct,tone}], running, queued, active, sub, emptyText}. */
export function jobsModel(jobs, lang) {
  const list = Array.isArray(jobs) ? jobs.filter((j) => j && typeof j === 'object') : [];
  let running = 0;
  let queued = 0;
  const rows = list.map((j) => {
    const status = typeof j.status === 'string' ? j.status : '';
    if (RUNNING.includes(status)) running += 1;
    if (QUEUED_STATUSES.includes(status)) queued += 1;
    const raw = Number(j.progress);
    let pct = Number.isFinite(raw) ? Math.max(0, Math.min(100, Math.round(raw))) : 0;
    if (status === 'completed') pct = 100;
    let state;
    let tone;
    if (status === 'pending') { state = t('job.state.queued', lang); tone = 'queued'; }
    else if (status === 'completed') { state = t('job.state.done', lang); tone = 'good'; }
    else if (status === 'error') { state = t('job.state.error', lang); tone = 'bad'; }
    else if (status === 'cancelled') { state = t('job.state.cancelled', lang); tone = 'queued'; }
    else if (status === 'detached') { state = t('job.state.detached', lang); tone = 'accent'; }
    else { state = t('job.state.running', lang, { pct }); tone = 'accent'; }
    const st = jobStage(j);
    const stage = st ? known('stage.' + st, lang) : null;
    const kind = typeof j.kind === 'string' ? known('job.kind.' + j.kind, lang) : null;
    return {
      id: String(j.id === undefined ? '' : j.id),
      title: stage || kind || t('job.kind.match', lang),
      target: basename(j.video) || '—',
      state, pct, tone,
    };
  });
  return {
    rows,
    running,
    queued,
    active: running + queued > 0,
    sub: t('pipeline.sub', lang, { running, queued }),
    emptyText: t('pipeline.empty', lang),
  };
}

/** data: a jobsModel. Returns {el, update(p), setJobs(model)}. */
export function build(data, ctx = {}) {
  const lang = ctx.lang;
  const slot = typeof ctx.slot === 'number' ? ctx.slot : 0;
  let model = data || jobsModel([], lang);
  const card = frame.build({ id: 'pipeline', title: t('pipeline.title', lang), sub: model.sub || '', flex: '3 1 280px' }, ctx);
  const list = h('div', { class: 'kw-jobs' });
  const more = h('div', { class: 'kw-foot-muted' }, '');
  card.body.appendChild(list);
  card.body.appendChild(more);
  card.body.appendChild(h('a', { href: '#/pipeline', class: 'kw-link kw-push' }, t('pipeline.open', lang)));

  let bars = [];
  let lastP = 1;

  function renderRows() {
    setText(card.subEl, model.sub || '');
    list.textContent = '';
    bars = [];
    const rows = (model.rows || []).slice(0, MAX_ROWS);
    if (!rows.length) list.appendChild(h('div', { class: 'kw-note' }, model.emptyText || t('pipeline.empty', lang)));
    rows.forEach((r) => {
      const bar = h('i', { class: 'kw-bar-' + r.tone });
      bars.push({ bar, pct: r.pct });
      list.appendChild(h('div', { class: 'kw-job' }, [
        h('div', { class: 'kw-job-top' }, [h('span', { class: 'kw-job-title' }, r.title), h('span', { class: 'mono kw-muted' }, r.state)]),
        h('div', { class: 'kw-job-target' }, r.target),
        h('div', {
          class: 'bar', role: 'progressbar', 'aria-valuenow': r.pct, 'aria-valuemin': 0, 'aria-valuemax': 100,
          'aria-label': t('job.progress_aria', lang, { stage: r.title }),
        }, bar),
      ]));
    });
    const extra = (model.rows || []).length - rows.length;
    setText(more, extra > 0 ? t('pipeline.more', lang, { n: extra }) : '');
    setStyle(more, 'display', extra > 0 ? '' : 'none');
  }

  function update(p) {
    lastP = p;
    card.update(p);
    const l = local(p, slot);
    bars.forEach(({ bar, pct }) => setStyle(bar, 'width', (pct * l).toFixed(1) + '%'));
  }

  function setJobs(next) {
    model = next || jobsModel([], lang);
    renderRows();
    update(lastP);
  }

  renderRows();
  return { el: card.el, update, setJobs };
}
