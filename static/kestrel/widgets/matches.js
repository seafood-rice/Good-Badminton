// Recent matches table: opponent, date, result chip, technique index, status. A row opens the peek (ctx.onOpenMatch).
import { t } from '../core/i18n.js';
import { local } from '../core/motion.js';
import * as frame from './frame.js';
import { h, revealStyle } from './frame.js';

/**
 * data: the matches view-model {rows:[{stem,label,aria,date,result,resultText,tech,statusText,statusTone}],
 * emptyTitle, emptyBody}. Returns {el, update(p)}.
 */
export function build(data, ctx = {}) {
  const d = data || {};
  const lang = ctx.lang;
  const slot = typeof ctx.slot === 'number' ? ctx.slot : 0;
  const rows = Array.isArray(d.rows) ? d.rows : [];
  // The activating element goes along so the shell can grow its container transform from it.
  const open = (stem, anchor) => { if (typeof ctx.onOpenMatch === 'function') ctx.onOpenMatch(stem, anchor || null); };
  const card = frame.build({
    id: 'matches',
    title: t('matches.title', lang),
    sub: t('matches.subtitle', lang),
    flex: '5 1 480px',
    right: h('a', { href: '#/matches', class: 'kw-link' }, t('matches.view_all', lang)),
  }, ctx);

  if (!rows.length) {
    card.body.appendChild(h('div', { class: 'kw-note' }, [
      h('strong', null, d.emptyTitle || t('matches.empty.title', lang)), ' ', d.emptyBody || t('matches.empty.body', lang),
    ]));
    return { el: card.el, update: card.update };
  }

  const th = (key, cls) => h('th', { scope: 'col', class: cls || null }, t(key, lang));
  const trs = rows.map((r) => {
    const btn = h('button', {
      type: 'button', class: 'kw-row-open', 'aria-label': r.aria || r.label, 'data-fk': 'match:' + r.stem,
      onclick: (e) => { if (e && typeof e.stopPropagation === 'function') e.stopPropagation(); open(r.stem, btn); },
    }, r.label || r.stem);
    return h('tr', {
      class: 'trow',
      // A click elsewhere on the row opens the same match (the button stops its own click from bubbling).
      onclick: () => open(r.stem, btn),
    }, [
      h('td', null, btn),
      h('td', { class: 'mono kw-muted' }, r.date || '—'),
      h('td', null, h('span', { class: 'mono kw-res' + (r.result === 'W' ? ' win' : r.result === 'L' ? ' loss' : ' none') }, r.resultText || '')),
      h('td', { class: 'mono kw-num' }, r.tech || '—'),
      h('td', null, h('span', { class: 'k-chip' + (r.statusTone ? ' ' + r.statusTone : '') }, [h('span', { class: 'kw-dot', 'aria-hidden': 'true' }), r.statusText || ''])),
    ]);
  });
  const table = h('table', { class: 'table kw-table' }, [
    h('thead', null, h('tr', null, [
      th('matches.col.opponent'), th('matches.col.date'), th('matches.col.result'), th('matches.col.tech', 'kw-num'), th('matches.col.status'),
    ])),
    h('tbody', null, trs),
  ]);
  card.body.appendChild(h('div', { class: 'kw-scroll' }, table));

  function update(p) {
    card.update(p);
    trs.forEach((tr, i) => revealStyle(tr, local(p, slot + i * 0.4), { dx: -10 }));
  }
  return { el: card.el, update };
}
