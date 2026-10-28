// Stroke model card on the Pipeline view (stroke correction spec 2026-10-28 section 6.1): the live model's
// test-split result against the owner's bar, label counts, Retrain, run history and Roll back.
// cardModel() is pure and tested; strokeModelCard() is the DOM part. GET /api/stroke-model feeds it.
import { t } from '../core/i18n.js';
import { errorText } from '../core/api.js';
import { h } from './frame.js';

const isNum = (v) => typeof v === 'number' && Number.isFinite(v);
const obj = (v) => (v && typeof v === 'object' && !Array.isArray(v) ? v : {});
const pct = (v) => (isNum(v) ? Math.round(v * 100) + '%' : '—');

const PCT_PARAMS = ['value', 'bar', 'candidate', 'live', 'unknown'];

/**
 * Pure: a server code ({code, ...numbers}) -> text in `lang`. A plain string (an entry written before codes
 * existed) is shown as is; an unknown code falls back to its name.
 */
export function describeCode(code, lang) {
  if (typeof code === 'string') return code;
  const c = obj(code);
  if (typeof c.code !== 'string') return '';
  const key = 'sm.code.' + c.code;
  const params = { ...c };
  for (const k of PCT_PARAMS) if (isNum(c[k])) params[k] = pct(c[k]);
  if (isNum(c.need) && isNum(c.have)) params.more = Math.max(0, c.need - c.have);
  const text = t(key, lang, params);
  return text === key ? c.code.replace(/_/g, ' ') : text;
}

/** Pure: one history entry -> {when, kept, rollback, text}. */
export function historyRow(entry, lang) {
  const e = obj(entry);
  const rollback = e.rollback === true;
  const kept = e.kept === true;
  const live = obj(e.live);
  const cand = obj(e.candidate);
  const when = typeof e.utc === 'string' ? e.utc.replace('T', ' ').replace(/:\d\dZ$/, '').replace('Z', '') : '';
  let text;
  if (rollback) text = t('sm.hist.rolled_back', lang);
  else {
    const move = isNum(live.accuracy) || isNum(cand.accuracy) ? ` (${pct(live.accuracy)} → ${pct(cand.accuracy)})` : '';
    text = t(kept ? 'sm.hist.kept' : 'sm.hist.rejected', lang) + move;
    const why = describeCode(e.reason, lang);
    if (!kept && why) text += ': ' + why;
  }
  return { when, kept, rollback, text };
}

/**
 * Pure: the /api/stroke-model body -> what the card shows.
 * state: 'loading' (no data), 'running' (a training or relabel job is active), 'not_enough' or 'ready'.
 */
export function cardModel(data, lang) {
  if (!data || typeof data !== 'object') return { state: 'loading' };
  const live = obj(data.live);
  const counts = obj(data.counts);
  const metrics = obj(live.metrics);
  const job = data.job && typeof data.job === 'object' ? data.job : null;
  const history = (Array.isArray(data.history) ? data.history : []).slice(-5).reverse();
  const hasModel = typeof live.version === 'string' && live.version !== '';
  const canRollback = (Array.isArray(data.history) ? data.history : []).some((e) => e && e.kept === true && e.replaced);
  const texts = (list) => (Array.isArray(list) ? list.map((x) => describeCode(x, lang)).filter(Boolean) : []);
  const missing = texts(data.missing);
  const state = job ? 'running' : data.pending ? 'loading' : data.can_retrain ? 'ready' : 'not_enough';
  return {
    state,
    hasModel,
    version: hasModel ? live.version : null,
    accuracy: pct(metrics.accuracy),
    unknown: pct(metrics.unknown_rate),
    scored: isNum(metrics.accuracy),
    barMet: live.bar_met === true,
    barReasons: texts(live.bar_reasons),
    counts: { train: counts.train || 0, test: counts.test || 0, newTrain: counts.new_train || 0 },
    missing,
    jobMessage: job && typeof job.message === 'string' ? job.message : '',
    canRetrain: state === 'ready',
    canRollback: canRollback && !job,
    history: history.map((e) => historyRow(e, lang)),
  };
}

const messageOf = (message, lang) => {
  const s = typeof message === 'string' ? message : '';
  const m = /^(.+?) \/ (.+)$/.exec(s);
  if (m && /[一-鿿]/.test(m[1])) return (lang === 'en' ? m[2] : m[1]).trim();
  return s;
};

/**
 * The card. opts: {lang, api: {retrainStrokeModel(), rollbackStrokeModel()}, onChanged()}.
 * Returns {el, update(data)}; update() re-renders from a GET /api/stroke-model body.
 */
export function strokeModelCard({ lang, api, onChanged } = {}) {
  const el = h('section', { class: 'card hud pv-sec sm-card', 'aria-label': t('sm.title', lang), 'data-card': 'stroke-model' });
  let model = { state: 'loading' };
  let error = '';

  async function run(call, btn) {
    if (btn) btn.disabled = true;
    error = '';
    try {
      await call();
    } catch (e) {
      error = errorText(e, lang);
    }
    render();
    if (typeof onChanged === 'function') onChanged();
  }

  function render() {
    el.textContent = '';
    el.appendChild(h('h2', { class: 'card-title' }, t('sm.title', lang)));
    if (model.state === 'loading') {
      el.appendChild(h('div', { class: 'kw-note', role: 'status' }, t('state.loading', lang)));
      return;
    }
    el.appendChild(h('div', { class: 'sm-version mono kw-muted' }, model.hasModel ? t('sm.version', lang, { v: model.version }) : t('sm.no_model', lang)));
    if (model.scored) {
      el.appendChild(h('div', { class: 'sm-metrics' }, [
        h('span', { class: 'k-chip ' + (model.barMet ? 'good' : 'bad'), 'data-sm': 'bar' }, t(model.barMet ? 'sm.bar_met' : 'sm.bar_not_met', lang)),
        h('span', { class: 'mono' }, t('sm.metrics', lang, { acc: model.accuracy, unk: model.unknown })),
      ]));
      if (!model.barMet && model.barReasons.length) el.appendChild(h('div', { class: 'kw-foot-muted' }, model.barReasons.join('; ')));
    } else if (model.hasModel) {
      el.appendChild(h('div', { class: 'kw-foot-muted' }, t('sm.unscored', lang)));
    }
    el.appendChild(h('div', { class: 'sm-counts mono' }, t('sm.counts', lang, model.counts)));
    if (model.state === 'running') {
      el.appendChild(h('div', { class: 'pv-msg', 'data-sm': 'running' }, messageOf(model.jobMessage, lang) || t('sm.running', lang)));
    } else if (model.state === 'not_enough') {
      el.appendChild(h('div', { class: 'kw-foot-muted', 'data-sm': 'missing' }, [t('sm.missing', lang), ' ', model.missing.join('; ')]));
    }
    if (error) el.appendChild(h('div', { class: 'pv-msg bad', role: 'alert' }, error));
    const retrain = h('button', {
      type: 'button', class: 'ov-btn small primary', 'data-sm': 'retrain', disabled: !model.canRetrain,
      onclick: (e) => run(() => api.retrainStrokeModel({ timeout: 10000 }), e.currentTarget),
    }, t('sm.retrain', lang));
    const rollback = h('button', {
      type: 'button', class: 'ov-btn small', 'data-sm': 'rollback', disabled: !model.canRollback,
      onclick: (e) => run(() => api.rollbackStrokeModel({ timeout: 10000 }), e.currentTarget),
    }, t('sm.rollback', lang));
    el.appendChild(h('div', { class: 'pv-foot' }, [retrain, rollback]));
    if (model.history.length) {
      el.appendChild(h('div', { class: 'k-label' }, t('sm.history', lang)));
      el.appendChild(h('ul', { class: 'pv-list sm-history' }, model.history.map((r) => h('li', { class: 'sm-hist-row ' + (r.kept ? 'kept' : 'rejected') }, [
        h('span', { class: 'mono kw-muted' }, r.when), ' ', r.text,
      ]))));
    }
  }

  render();
  return {
    el,
    update(data) {
      model = cardModel(data, lang);
      render();
    },
  };
}
