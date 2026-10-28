// Rally model card on the Pipeline view (rally-winner-learning spec 7.3): the live point-end segmenter and winner
// decoder with their held-out scores against the swing segments and the rule cascade, label counts, Retrain, the
// last run's decision with the numbers, run history and Roll back. cardModel() is pure and tested; rallyModelCard()
// is the DOM part. GET /api/rally-model feeds it. Reason codes are rendered here, in the UI language.
import { t } from '../core/i18n.js';
import { errorText } from '../core/api.js';
import { h } from './frame.js';
import { describeCode } from './strokemodel.js';

const isNum = (v) => typeof v === 'number' && Number.isFinite(v);
const obj = (v) => (v && typeof v === 'object' && !Array.isArray(v) ? v : {});
const pct = (v) => (isNum(v) ? Math.round(v * 100) + '%' : '—');
const int = (v) => (isNum(v) ? String(Math.round(v)) : '?');
const MODELS = ['segmenter', 'decoder'];

/** Pure: a model's reason ({code, ...numbers}) -> text in `lang`, without the model name prefix. */
const reasonText = (reason, lang) => describeCode(reason, lang);

/** Pure: the live segmenter / decoder -> one line each ({live, text}). */
function liveLines(live, lang) {
  const l = obj(live);
  const seg = l.segmenter && typeof l.segmenter === 'object' ? l.segmenter : null;
  const dec = l.decoder && typeof l.decoder === 'object' ? l.decoder : null;
  const sh = obj(seg && seg.heldout);
  const dh = obj(dec && dec.heldout);
  return {
    segmenter: seg
      ? { live: true, text: t('rm.live.segmenter', lang, { v: seg.version || '?', f1: pct(sh.f1), swing: pct(sh.swing_f1) }) }
      : { live: false, text: t('rm.live.none.segmenter', lang) },
    decoder: dec
      ? { live: true, text: t('rm.live.decoder', lang, { v: dec.version || '?', precision: pct(dh.precision), correct: int(dh.correct), rules: int(dh.rules_correct) }) }
      : { live: false, text: t('rm.live.none.decoder', lang) },
  };
}

/** Pure: the held-out numbers of a history entry's `totals` -> two lines (segmenter, decoder). */
export function totalsLines(totals, lang) {
  const x = obj(totals);
  if (!isNum(x.points)) return [];
  return [
    t('rm.last.seg', lang, { f1: pct(x.seg_f1), swing: pct(x.swing_f1) }),
    t('rm.last.dec', lang, {
      correct: int(x.dec_correct), claimed: int(x.dec_claimed), precision: pct(x.dec_precision),
      rcorrect: int(x.rules_correct), rclaimed: int(x.rules_claimed),
    }),
  ];
}

const whenOf = (utc) => (typeof utc === 'string' ? utc.replace('T', ' ').replace(/:\d\dZ$/, '').replace('Z', '') : '');

/** Pure: each model of a run -> {name, kept, text} (the verdict, and for a rejection the reason with its numbers). */
function modelVerdicts(entry, lang) {
  const models = obj(obj(entry).models);
  return MODELS.filter((name) => models[name] && typeof models[name] === 'object').map((name) => {
    const m = models[name];
    const kept = m.kept === true;
    const why = reasonText(m.reason, lang);
    const head = t('rm.model.' + name, lang) + ': ' + t(kept ? 'rm.kept' : 'rm.rejected', lang);
    return { name, kept, text: why ? head + ' (' + why + ')' : head };
  });
}

/** Pure: one history entry -> {when, kept, rollback, text}. */
export function historyRow(entry, lang) {
  const e = obj(entry);
  const rollback = e.rollback === true;
  const when = whenOf(e.utc);
  if (rollback) return { when, kept: true, rollback: true, text: t('rm.hist.rolled_back', lang) };
  const verdicts = modelVerdicts(e, lang);
  const kept = verdicts.some((v) => v.kept) || (!verdicts.length && e.kept === true);
  const text = verdicts.length ? verdicts.map((v) => v.text).join('; ') : t(kept ? 'rm.kept' : 'rm.rejected', lang);
  return { when, kept, rollback: false, text };
}

/**
 * Pure: the /api/rally-model body -> what the card shows.
 * state: 'loading' (no data), 'running' (a training, relabel or re-segment job is active), 'not_enough' or 'ready'.
 */
export function cardModel(data, lang) {
  if (!data || typeof data !== 'object') return { state: 'loading' };
  const counts = obj(data.counts);
  const job = data.job && typeof data.job === 'object' ? data.job : null;
  const all = Array.isArray(data.history) ? data.history : [];
  const history = all.slice(-5).reverse();
  const texts = (list) => (Array.isArray(list) ? list.map((x) => describeCode(x, lang)).filter(Boolean) : []);
  const lastRun = [...all].reverse().find((e) => e && typeof e === 'object' && !e.rollback && e.models && typeof e.models === 'object');
  const state = job ? 'running' : data.pending ? 'loading' : data.can_retrain ? 'ready' : 'not_enough';
  return {
    state,
    live: liveLines(data.live, lang),
    counts: { matches: counts.matches || 0, points: counts.points || 0, newPoints: counts.new_points || 0 },
    missing: texts(data.missing),
    jobMessage: job && typeof job.message === 'string' ? job.message : '',
    canRetrain: state === 'ready',
    canRollback: data.can_rollback === true && !job,
    lastRun: lastRun
      ? { when: whenOf(lastRun.utc), models: modelVerdicts(lastRun, lang), numbers: totalsLines(lastRun.totals, lang) }
      : null,
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
 * The card. opts: {lang, api: {retrainRallyModel(), rollbackRallyModel()}, onChanged()}.
 * Returns {el, update(data)}; update() re-renders from a GET /api/rally-model body.
 */
export function rallyModelCard({ lang, api, onChanged } = {}) {
  const el = h('section', { class: 'card hud pv-sec sm-card', 'aria-label': t('rm.title', lang), 'data-card': 'rally-model' });
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
    el.appendChild(h('h2', { class: 'card-title' }, t('rm.title', lang)));
    if (model.state === 'loading') {
      el.appendChild(h('div', { class: 'kw-note', role: 'status' }, t('state.loading', lang)));
      return;
    }
    el.appendChild(h('div', { class: 'sm-version mono kw-muted', 'data-rm': 'live' },
      MODELS.map((name) => h('div', { 'data-rm': 'live-' + name }, model.live[name].text))));
    el.appendChild(h('div', { class: 'sm-counts mono' }, t('rm.counts', lang, model.counts)));
    if (model.lastRun) {
      el.appendChild(h('div', { class: 'k-label' }, t('rm.last', lang, { when: model.lastRun.when })));
      el.appendChild(h('ul', { class: 'pv-list sm-history', 'data-rm': 'last' }, [
        ...model.lastRun.models.map((m) => h('li', { class: 'sm-hist-row ' + (m.kept ? 'kept' : 'rejected') }, m.text)),
        ...model.lastRun.numbers.map((n) => h('li', { class: 'sm-hist-row mono' }, n)),
      ]));
    }
    if (model.state === 'running') {
      el.appendChild(h('div', { class: 'pv-msg', 'data-rm': 'running' }, messageOf(model.jobMessage, lang) || t('rm.running', lang)));
    } else if (model.state === 'not_enough') {
      el.appendChild(h('div', { class: 'kw-foot-muted', 'data-rm': 'missing' }, [t('rm.missing', lang), ' ', model.missing.join('; ')]));
    }
    if (error) el.appendChild(h('div', { class: 'pv-msg bad', role: 'alert' }, error));
    const retrain = h('button', {
      type: 'button', class: 'ov-btn small primary', 'data-rm': 'retrain', disabled: !model.canRetrain,
      onclick: (e) => run(() => api.retrainRallyModel({ timeout: 10000 }), e.currentTarget),
    }, t('rm.retrain', lang));
    const rollback = h('button', {
      type: 'button', class: 'ov-btn small', 'data-rm': 'rollback', disabled: !model.canRollback,
      onclick: (e) => run(() => api.rollbackRallyModel({ timeout: 10000 }), e.currentTarget),
    }, t('rm.rollback', lang));
    el.appendChild(h('div', { class: 'pv-foot' }, [retrain, rollback]));
    if (model.history.length) {
      el.appendChild(h('div', { class: 'k-label' }, t('rm.history', lang)));
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
