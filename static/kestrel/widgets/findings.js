// "What stands out": server findings in words, sliding in one after another.
// A finding about a technique focuses that axis on the radar (ctx.onFocusAxis).
import { t } from '../core/i18n.js';
import { local } from '../core/motion.js';
import * as frame from './frame.js';
import { h, revealStyle } from './frame.js';

/**
 * data: the findings view-model {items:[{severity, glyph, title, detail, axis}], count, empty}.
 * Returns {el, update(p)}.
 */
export function build(data, ctx = {}) {
  const d = data || {};
  const lang = ctx.lang;
  const slot = typeof ctx.slot === 'number' ? ctx.slot : 0;
  const items = Array.isArray(d.items) ? d.items : [];
  const card = frame.build({
    id: 'findings',
    title: t('findings.title', lang),
    sub: t('findings.subtitle', lang),
    flex: '4 1 320px',
    right: h('span', { class: 'mono kw-count' }, d.count || ''),
  }, ctx);

  const rows = items.map((f, i) => {
    const canFocus = !!f.axis && typeof ctx.onFocusAxis === 'function';
    const content = [
      h('span', { class: 'kw-find-glyph', 'aria-hidden': 'true' }, f.glyph || ''),
      h('span', { class: 'kw-find-text' }, [
        h('span', { class: 'kw-find-title' }, f.title || ''),
        h('span', { class: 'kw-find-detail' }, f.detail || ''),
      ]),
    ];
    const cls = 'kw-find ' + (f.severity || 'info');
    return canFocus
      ? h('button', { type: 'button', class: cls + ' focusable', 'data-fk': 'find:' + i, onclick: () => ctx.onFocusAxis(f.axis) }, content)
      : h('div', { class: cls }, content);
  });
  card.body.appendChild(
    rows.length ? h('div', { class: 'kw-find-list' }, rows) : h('div', { class: 'kw-note' }, d.empty || t('findings.empty', lang)),
  );

  function update(p) {
    card.update(p);
    rows.forEach((row, i) => revealStyle(row, local(p, slot + i * 0.6), { dx: -14 }));
  }
  return { el: card.el, update };
}
