// "Not yet available" tile: dashed, hatched, says why, links to the fix when one exists. Never drawn as zero.
import { t } from '../core/i18n.js';
import { local } from '../core/motion.js';
import { h, revealStyle, scanLine, updateScan } from './frame.js';
import * as info from './info.js';

/**
 * data: {id, label, reason, fix: {href, label}|null, flex}; ctx.slot positions it in the reveal.
 * Returns {el, update(p)}.
 */
export function build(data, ctx = {}) {
  const d = data || {};
  const lang = ctx.lang;
  const slot = typeof ctx.slot === 'number' ? ctx.slot : 0;
  const scan = scanLine();
  const fix = d.fix && d.fix.href ? h('a', { href: d.fix.href, class: 'kw-gap-fix' }, d.fix.label || '') : null;
  const el = h('div', {
    class: 'gap-card hud kw-gap',
    role: 'group',
    'aria-label': `${d.label || ''}: ${t('gap.title', lang)}`,
    'data-tile': d.id || '',
    style: d.flex ? { flex: d.flex } : null,
  }, [
    scan,
    h('div', { class: 'kw-tile-label' }, [h('span', null, d.label || ''), info.button(d.id, d.label || '', lang)]),
    h('span', { class: 'mono kw-gap-dash', 'aria-hidden': 'true' }, '—'),
    h('span', { class: 'kw-gap-title' }, t('gap.title', lang)),
    h('span', { class: 'reason' }, d.reason || t('reason.unknown', lang)),
    fix,
  ]);

  function update(p) {
    revealStyle(el, local(p, slot));
    updateScan(scan, p, slot);
  }
  return { el, update };
}
