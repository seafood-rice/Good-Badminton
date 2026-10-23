// Technique tab panel for the match details page: what techniqueSummary / poolTechnique worked out (stroke count,
// the per-type table, the recurring weaknesses), or the reason there is nothing to show. Text only.
import { t } from '../core/i18n.js';
import { h } from './frame.js';
import { metricLabel, num1, isNum } from '../views/workspace_model.js';

const REASON = { empty: 'wsx.tech.empty', stale: 'ws.stroke_stale', doubles: 'wsx.tech.doubles' };

function typeName(id, lang) {
  if (id === 'unknown' || id === 'uncertain') return t('ws.uncertain', lang);
  const key = 'stroke.' + id;
  const v = t(key, lang);
  return v !== key ? v : String(id);
}

/** techniquePanel({lang, summary: {state, count, scored, byType, weaknesses}, note: string | null}) -> element. */
export function techniquePanel({ lang, summary, note = null } = {}) {
  const s = summary && typeof summary === 'object' ? summary : {};
  const root = h('div', { class: 'ws-tech' });
  if (s.state !== 'ok') {
    root.appendChild(h('div', { class: 'kw-note' }, t(REASON[s.state] || REASON.empty, lang)));
    if (note) root.appendChild(h('div', { class: 'mv-hint' }, note));
    return root;
  }
  root.appendChild(h('div', { class: 'ws-tech-count' }, t('wsx.tech.count', lang, { n: s.count, scored: s.scored })));
  if (note) root.appendChild(h('div', { class: 'mv-hint' }, note));
  const rows = (Array.isArray(s.byType) ? s.byType : []).map((r) => h('tr', null, [
    h('td', null, typeName(r.id, lang)),
    h('td', { class: 'mono' }, String(r.count)),
    h('td', { class: 'mono' }, isNum(r.avg) ? num1(r.avg) : '—'),
  ]));
  root.appendChild(h('table', { class: 'table ws-tech-table' }, [
    h('thead', null, h('tr', null, [
      h('th', { scope: 'col' }, t('wsx.tech.col.type', lang)),
      h('th', { scope: 'col' }, t('wsx.tech.col.count', lang)),
      h('th', { scope: 'col' }, t('wsx.tech.col.avg', lang)),
    ])),
    h('tbody', null, rows),
  ]));
  const weak = Array.isArray(s.weaknesses) ? s.weaknesses : [];
  if (weak.length) {
    root.appendChild(h('div', { class: 'k-label' }, t('wsx.tech.weak', lang)));
    root.appendChild(h('ul', { class: 'ws-weak' }, weak.map((w) => h('li', null, [
      metricLabel(w.metric, lang), ' ', h('span', { class: 'mono' }, '×' + w.count),
    ]))));
  }
  return root;
}
