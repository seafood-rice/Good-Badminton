// Strategy tab panel for the match details page (spec 2026-10-27 section 8.3): the LLM-written, number-checked match
// report as collapsible sections, claims with evidence chips, caveats and a footer, plus Regenerate, Export Markdown
// and "write from the other side". Everything that comes from the report file (names, claims, plan items) is set as
// text or as an attribute value, never parsed as HTML. The state logic is in views/strategy_model.js.
import { t } from '../core/i18n.js';
import { getJSON, sendJSON } from '../core/api.js';
import { h } from './frame.js';
import {
  panelState, reportFor, claimsOf, partClaims, evidenceText, isFallback, otherSide, planItems, obj, YOUR_GAME_PARTS,
} from '../views/strategy_model.js';

const POLL_MS = 3000;
const POST_TIMEOUT_MS = 60000;

const defaultApi = {
  get: (url) => getJSON(url),
  post: (url, body) => sendJSON('POST', url, body, { timeout: POST_TIMEOUT_MS }),
};
const defaultTimers = { set: (fn, ms) => setTimeout(fn, ms), clear: (id) => clearTimeout(id) };

const strategyUrl = (stem) => '/api/strategy/' + encodeURIComponent(stem);
const str = (v) => (typeof v === 'string' ? v : '');

function setsText(sets) {
  if (!Array.isArray(sets)) return '';
  return sets.filter((s) => Array.isArray(s) && s.length === 2).map((s) => s[0] + '-' + s[1]).join(', ');
}

function caveatText(c, lang) {
  if (typeof c === 'string') return c;
  const o = obj(c);
  const code = str(o.code);
  const p = { ...obj(o.params) };
  if (typeof p.share === 'number') p.share = Math.round(p.share * 100) + '%';
  if (Array.isArray(p.files)) p.files = p.files.join(', ');
  const key = 'strategy.caveat.' + code;
  const v = t(key, lang, p);
  return v !== key ? v : (str(o.text) || code);
}

/** strategyPanel({stem, lang, api: {get(url), post(url, body)}, timers: {set, clear}}) -> {el, destroy}. */
export function strategyPanel({ stem, lang, api = defaultApi, timers = defaultTimers } = {}) {
  let alive = true;
  let payload = null;
  let loadError = false;
  let actionError = '';
  let busy = false;
  let timer = null;

  const root = h('div', { class: 'ws-strat' });

  const evChips = (ids, facts) => {
    const list = (ids || []).filter((x) => typeof x === 'string');
    if (!list.length) return null;
    return h('span', { class: 'ws-strat-ev' }, list.map((id) => h('span', {
      class: 'k-chip ws-strat-chip mono', title: evidenceText([id], facts),
    }, id)));
  };

  const claimsList = (claims, facts) => h('ul', { class: 'ws-strat-claims' }, claims.map((c) => h('li', null, [
    h('span', { class: 'ws-strat-text' }, c.text), evChips(c.evidence, facts),
  ])));

  function sectionBox(id, section, body, open) {
    const tag = isFallback(section) ? h('span', { class: 'k-chip ws-strat-fb' }, t('strategy.fallback', lang)) : null;
    return h('details', { class: 'ws-strat-sec', 'data-sec': id, open: open ? true : null }, [
      h('summary', { class: 'ws-strat-sum' }, [t('strategy.sec.' + id, lang), tag]),
      h('div', { class: 'ws-strat-body' }, body),
    ]);
  }

  function headerBlock(rep, doc) {
    const hd = obj(rep.header);
    const names = hd.you || hd.opponent ? t('strategy.vs', lang, { you: str(hd.you) || '—', opp: str(hd.opponent) || '—' }) : '';
    const sets = setsText(hd.sets);
    const result = hd.winner === 'you' || hd.winner === 'opponent' ? t('strategy.result.' + hd.winner, lang, { sets }).trim() : '';
    const conf = ['high', 'medium', 'low'].includes(hd.confidence) ? hd.confidence : null;
    return h('div', { class: 'ws-strat-head' }, [
      names ? h('div', { class: 'ws-strat-names' }, names) : null,
      h('div', { class: 'ws-strat-meta' }, [
        str(hd.date) ? h('span', { class: 'mono' }, hd.date) : null,
        result ? h('span', null, result) : null,
        conf ? h('span', { class: 'k-chip ws-strat-conf', 'data-conf': conf }, t('strategy.conf.' + conf, lang)) : null,
      ]),
      obj(doc.focus).assumed ? h('div', { class: 'mv-hint' }, t('strategy.focus_assumed', lang)) : null,
    ]);
  }

  function planBlock(section, facts) {
    const { items, trainingFocus } = planItems(section);
    const row = (label, text) => (text ? h('div', { class: 'ws-strat-plan-row' }, [h('span', { class: 'k-label' }, label), h('span', null, text)]) : null);
    return [
      h('ol', { class: 'ws-strat-plan' }, items.map((it) => h('li', null, [
        row(t('strategy.plan.do', lang), it.do), row(t('strategy.plan.why', lang), it.why), row(t('strategy.plan.cue', lang), it.cue),
        evChips(it.evidence, facts),
      ]))),
      trainingFocus ? h('div', { class: 'ws-strat-focus' }, [
        h('div', { class: 'k-label' }, t('strategy.training_focus', lang)),
        h('div', null, [h('span', { class: 'ws-strat-text' }, trainingFocus.text), evChips(trainingFocus.evidence, facts)]),
      ]) : null,
    ];
  }

  function yourGameBlock(section, facts) {
    const out = [];
    for (const part of YOUR_GAME_PARTS) {
      const claims = partClaims(section, part);
      if (!claims.length) continue;
      out.push(h('div', { class: 'k-label' }, t('strategy.sec.' + part, lang)), claimsList(claims, facts));
    }
    return out;
  }

  function reportBlocks(doc, rep) {
    const facts = doc.facts;
    const out = [headerBlock(rep, doc)];
    const simple = (id, open) => {
      const claims = claimsOf(rep[id]);
      if (claims.length || rep[id]) out.push(sectionBox(id, rep[id], claimsList(claims, facts), open));
    };
    simple('summary', true);
    simple('match_profile', false);
    if (rep.your_game) out.push(sectionBox('your_game', rep.your_game, yourGameBlock(rep.your_game, facts), false));
    if (rep.game_plan) out.push(sectionBox('game_plan', rep.game_plan, planBlock(rep.game_plan, facts), true));
    simple('scouting', false);
    const cav = Array.isArray(rep.caveats) ? rep.caveats : Array.isArray(obj(rep.caveats).items) ? rep.caveats.items : [];
    if (cav.length) {
      out.push(h('details', { class: 'ws-strat-sec', 'data-sec': 'caveats' }, [
        h('summary', { class: 'ws-strat-sum' }, t('strategy.sec.caveats', lang)),
        h('ul', { class: 'ws-strat-claims ws-strat-caveats' }, cav.map((c) => h('li', null, caveatText(c, lang)))),
      ]));
    }
    const when = str(doc.generated_at).replace('T', ' ').slice(0, 16);
    out.push(h('div', { class: 'ws-strat-foot mv-hint' }, t('strategy.footer', lang, { model: str(doc.model), time: when })));
    return out;
  }

  function reasonText(doc) {
    const code = str(obj(doc.reason).code) || str(obj(payload.job).reason);
    const key = 'strategy.reason.' + code;
    return code && t(key, lang) !== key ? t(key, lang) : t('strategy.reason.unknown', lang);
  }

  function draw() {
    root.textContent = '';
    if (!payload) {
      root.appendChild(h('div', { class: 'mv-hint', role: 'status' }, t(loadError ? 'strategy.load_failed' : 'state.loading', lang)));
      return;
    }
    const state = panelState(payload);
    const doc = obj(payload.report);
    const rep = reportFor(payload, lang);
    const showReport = !!rep && ['ready', 'stale', 'failed'].includes(state);
    if (state === 'off') {
      root.appendChild(h('div', { class: 'mv-hint' }, t('strategy.state.off', lang)));
      return;
    }
    const bar = h('div', { class: 'ws-strat-bar' }, [
      state === 'queued' || state === 'running' ? null : h('button', {
        type: 'button', class: 'ov-btn small', disabled: busy ? true : null, onclick: () => regenerate(),
      }, t(state === 'none' ? 'strategy.generate' : 'strategy.regen', lang)),
      showReport && obj(rep.header).both_registered === true ? h('button', {
        type: 'button', class: 'ov-btn small', disabled: busy ? true : null, onclick: () => regenerate({ focus: otherSide(doc.focus) }),
      }, t('strategy.other_side', lang)) : null,
      showReport ? h('a', {
        class: 'ov-btn small', href: strategyUrl(stem) + '.md?lang=' + encodeURIComponent(lang), download: true,
      }, t('strategy.export', lang)) : null,
    ]);
    root.appendChild(bar);
    if (actionError) root.appendChild(h('div', { class: 'mv-err', role: 'alert' }, actionError));
    if (state === 'queued' || state === 'running') {
      root.appendChild(h('div', { class: 'mv-hint', role: 'status' }, [
        t('strategy.state.' + state, lang), ' ', h('a', { class: 'ws-strat-link', href: '#/pipeline' }, t('strategy.pipeline_link', lang)),
      ]));
      return;
    }
    if (state === 'none') root.appendChild(h('div', { class: 'mv-hint' }, t('strategy.state.none', lang)));
    if (state === 'stale') root.appendChild(h('div', { class: 'ws-strat-banner', role: 'status' }, t('strategy.state.stale', lang)));
    if (state === 'failed') root.appendChild(h('div', { class: 'mv-err', role: 'alert' }, reasonText(doc)));
    if (showReport) for (const n of reportBlocks(doc, rep)) if (n) root.appendChild(n);
  }

  function schedule() {
    if (timer !== null) { timers.clear(timer); timer = null; }
    if (!alive) return;
    const s = panelState(payload);
    if (s === 'queued' || s === 'running') timer = timers.set(() => { timer = null; load(); }, POLL_MS);
  }

  async function load() {
    try {
      const p = await api.get(strategyUrl(stem));
      if (!alive) return;
      if (p && typeof p === 'object') { payload = p; loadError = false; } else if (!payload) loadError = true;
    } catch (e) {
      if (!alive) return;
      if (!payload) loadError = true;
    }
    draw();
    schedule();
  }

  async function regenerate(body = {}) {
    if (busy) return;
    busy = true;
    actionError = '';
    draw();
    try {
      const r = await api.post(strategyUrl(stem) + '/regenerate', body);
      if (!alive) return;
      busy = false;
      const job = r && typeof r.job === 'object' && r.job ? r.job : { status: 'pending' };
      payload = { ...obj(payload), enabled: true, status: 'pending', job };
    } catch (e) {
      if (!alive) return;
      busy = false;
      if (e && e.status === 409) payload = { ...obj(payload), enabled: true, status: 'pending', job: { status: 'pending' } };
      else actionError = t('strategy.regen_failed', lang);
    }
    draw();
    schedule();
  }

  draw();
  Promise.resolve().then(load);

  return {
    el: root,
    destroy: () => {
      alive = false;
      if (timer !== null) { timers.clear(timer); timer = null; }
    },
  };
}
