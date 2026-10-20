// "Doubles players" card (spec 2026-10-20 section 5): per doubles run a table of its players (name or identity,
// team chip, distance, average/max speed, hits, named share) with the identity status and the "who hit:
// unvalidated" badge; the selected athlete's doubles summary on top. A run title opens the match (ctx.onOpenMatch).
import { t } from '../core/i18n.js';
import { local } from '../core/motion.js';
import * as frame from './frame.js';
import { h, revealStyle, approxMark } from './frame.js';

/** The four-colour identity swatch (colours are the fixed IDENTITY_COLORS constants, never payload text). */
export function swatch(color) {
  return h('span', { class: 'dbl-swatch' + (color ? '' : ' none'), 'aria-hidden': 'true', style: color ? { '--pc': color } : null });
}

/** The muted "Who hit: unvalidated" badge every who-hit figure carries. */
export function unvalidatedBadge(lang) {
  return h('span', { class: 'k-chip mid dbl-unval', title: t('dbl.unvalidated_title', lang) }, t('dbl.unvalidated', lang));
}

/** A player table for one run (rows from doubles_model playerRow). */
export function playersTable(players, lang) {
  const th = (key, cls) => h('th', { scope: 'col', class: cls || null }, t(key, lang));
  return h('table', { class: 'table kw-table dbl-table' }, [
    h('thead', null, h('tr', null, [
      th('dbl.col.player'), th('dbl.col.team'), th('dbl.col.distance', 'kw-num'), th('dbl.col.speed', 'kw-num'),
      th('dbl.col.hits', 'kw-num'), th('dbl.col.named', 'kw-num'),
    ])),
    h('tbody', null, (Array.isArray(players) ? players : []).map((p) => h('tr', null, [
      h('td', { class: 'dbl-who' }, [swatch(p.color), h('span', { class: 'dbl-name' }, p.label), p.label !== p.id ? h('span', { class: 'mono kw-muted' }, p.id) : null]),
      h('td', null, h('span', { class: 'k-chip' + (p.team ? '' : ' mid') }, p.teamText)),
      h('td', { class: 'mono kw-num' }, p.distanceText),
      h('td', { class: 'mono kw-num' }, `${p.avgSpeedText} / ${p.maxSpeedText}`),
      h('td', { class: 'mono kw-num' }, p.hitsText),
      h('td', { class: 'mono kw-num' }, p.namedText),
    ]))),
  ]);
}

/** data: doubles_model doublesRows(). Returns {el, update(p)}. */
export function build(data, ctx = {}) {
  const d = data || {};
  const lang = ctx.lang;
  const slot = typeof ctx.slot === 'number' ? ctx.slot : 0;
  const open = (stem, anchor) => { if (typeof ctx.onOpenMatch === 'function') ctx.onOpenMatch(stem, anchor || null); };
  const card = frame.build({
    id: 'doubles', title: t('dbl.widget.title', lang), sub: d.sub || '', flex: '7 1 520px', right: unvalidatedBadge(lang),
  }, ctx);
  const blocks = [];
  if (d.reason) card.body.appendChild(h('div', { class: 'kw-foot-muted', role: 'note' }, d.reason));
  if (d.athlete) {
    const a = d.athlete;
    const box = h('div', { class: 'dbl-summary' }, [
      h('div', { class: 'k-label' }, a.label),
      h('div', { class: 'dbl-summary-row mono' }, [
        h('span', null, [h('span', { class: 'kw-muted' }, t('dbl.col.distance', lang) + ' '), a.distanceText]),
        h('span', null, [h('span', { class: 'kw-muted' }, t('dbl.col.speed', lang) + ' '), `${a.avgSpeedText} / ${a.maxSpeedText}`]),
        h('span', null, [h('span', { class: 'kw-muted' }, t('dbl.col.hits', lang) + ' '), a.hitsText]),
        h('span', null, [h('span', { class: 'kw-muted' }, t('dbl.col.named', lang) + ' '), a.namedText]),
      ]),
    ]);
    card.body.appendChild(box);
    blocks.push(box);
  }
  (Array.isArray(d.runs) ? d.runs : []).forEach((r) => {
    const btn = h('button', {
      type: 'button', class: 'kw-row-open', 'data-fk': 'dbl:' + r.stem, 'aria-label': t('matches.peek_aria', lang, { name: r.title }),
      onclick: () => open(r.stem, btn),
    }, r.title);
    const block = h('div', { class: 'dbl-run' }, [
      h('div', { class: 'dbl-run-head' }, [
        btn,
        h('span', { class: 'mono kw-muted' }, [r.date, r.dateApprox ? approxMark(lang) : null]),
        h('span', { class: 'k-chip' + (r.tone ? ' ' + r.tone : '') }, r.statusText),
      ]),
      r.detail ? h('div', { class: 'kw-foot-muted' }, r.detail) : null,
      r.players.length ? h('div', { class: 'kw-scroll' }, playersTable(r.players, lang)) : h('div', { class: 'kw-note' }, t('dbl.no_players', lang)),
    ]);
    card.body.appendChild(block);
    blocks.push(block);
  });
  if (d.more) card.body.appendChild(h('div', { class: 'kw-foot-muted' }, t('dbl.more_runs', lang, { n: d.more })));
  card.body.appendChild(h('a', { href: '#/matches', class: 'kw-link kw-push' }, t('matches.view_all', lang)));

  function update(p) {
    card.update(p);
    blocks.forEach((b, i) => revealStyle(b, local(p, slot + i * 0.4), { dy: 10 }));
  }
  return { el: card.el, update };
}
