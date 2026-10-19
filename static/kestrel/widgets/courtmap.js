// Court map: a 10 x 11 cell grid of one half court (row 0 at the NET, row 10 at the baseline) in two modes,
// Position (where the player spends rally time) and Pull (where opponents were pulled to), with a hover readout.
// Cells bloom row by row from the net with the reveal; switching mode restarts the reveal.
import { t } from '../core/i18n.js';
import { cellReveal } from '../core/geometry.js';
import * as frame from './frame.js';
import { h, setAttr, setStyle, setText } from './frame.js';

const COLS = 10;
const ROWS = 11;
const MODES = ['position', 'pull'];
const THUMB_W = 78;

/**
 * data: the court view-model {position, pull, mode}; each mode {label, sub, available, reason, tag,
 * cells:[{row,col,share,alpha,readout}], readout, aria}. ctx.onCourtMode(mode) is told about switches.
 * Returns {el, update(p), setMode(mode)}.
 */
export function build(data, ctx = {}) {
  const d = data || {};
  const lang = ctx.lang;
  let mode = MODES.includes(d.mode) ? d.mode : 'position';
  const model = () => d[mode] || { cells: [], available: false, reason: t('reason.no_data', lang) };
  const card = frame.build({ id: 'court', title: t('heat.title', lang), sub: model().sub || '', flex: '4 1 300px' }, ctx);

  const thumb = h('div', { class: 'thumb', 'aria-hidden': 'true', style: { width: THUMB_W + 'px' } });
  const buttons = MODES.map((m) => h('button', {
    type: 'button', 'aria-pressed': 'false', 'data-fk': 'court:' + m, style: { width: THUMB_W + 'px', padding: '0' },
    onclick: () => setMode(m, true),
  }, (d[m] && d[m].label) || t('heat.' + m, lang)));
  const pills = h('div', { class: 'pill-slider kw-court-modes', role: 'group', 'aria-label': t('heat.mode_aria', lang) }, [thumb, ...buttons]);

  const cellEls = [];
  for (let k = 0; k < COLS * ROWS; k++) {
    const idx = k;
    cellEls.push(h('div', { class: 'cell kw-cell', onmouseenter: () => hoverCell(idx) }));
  }
  const gridEl = h('div', { class: 'kw-court-grid' }, [
    ...cellEls,
    h('div', { class: 'kw-court-net', 'aria-hidden': 'true' }),
    h('div', { class: 'kw-court-service', 'aria-hidden': 'true' }),
    h('div', { class: 'kw-court-centre', 'aria-hidden': 'true' }),
  ]);
  const gapTitle = h('strong', null, t('gap.title', lang));
  const gapReason = h('span', null, '');
  const gapEl = h('div', { class: 'gap-card kw-court-gap' }, [gapTitle, gapReason]);
  // The readout is a live region outside the role=img, so keyboard cell moves are announced.
  const readEl = h('span', { 'aria-live': 'polite' }, '');
  const tagEl = h('span', { class: 'mono kw-tag' }, '');
  // role=img summarises the grid (densest zone, or the reason it is unavailable); arrow keys walk the cells.
  const wrap = h('div', {
    class: 'kw-court-wrap', role: 'img', 'aria-label': '', tabindex: '0', 'data-fk': 'court-grid',
    onkeydown: (e) => onKey(e), onblur: () => hoverCell(null),
  }, [gridEl, gapEl]);
  const court = h('div', { class: 'kw-court', onmouseleave: () => hoverCell(null) }, [
    wrap,
    h('div', { class: 'mono kw-court-axis' }, [h('span', null, t('heat.net', lang)), readEl, h('span', null, t('heat.baseline', lang))]),
  ]);
  const scaleEl = h('div', { class: 'kw-court-scale' }, [
    t('heat.fewer', lang), h('span', { class: 'kw-court-ramp', 'aria-hidden': 'true' }), t('heat.more', lang),
  ]);

  card.body.appendChild(h('div', { class: 'kw-court-bar' }, [pills, tagEl]));
  card.body.appendChild(court);
  card.body.appendChild(scaleEl);

  let lastP = 1;
  let current = null;

  function hoverCell(k) {
    const m = model();
    current = k;
    cellEls.forEach((el, i) => el.classList.toggle('sel', i === k));
    const c = k === null || !m.available ? null : m.cells[k];
    setText(readEl, c ? c.readout : m.readout || '');
  }

  // Arrow keys move a cell cursor (Up = towards the net, row 0); Escape clears it.
  function onKey(e) {
    const m = model();
    if (!m.available) return;
    if (e.key === 'Escape') { hoverCell(null); return; }
    const step = { ArrowUp: [-1, 0], ArrowDown: [1, 0], ArrowLeft: [0, -1], ArrowRight: [0, 1] }[e.key];
    if (!step) return;
    e.preventDefault();
    const start = current !== null ? current : m.densest ? m.densest.row * COLS + m.densest.col : 0;
    const row = Math.max(0, Math.min(ROWS - 1, Math.floor(start / COLS) + (current === null ? 0 : step[0])));
    const col = Math.max(0, Math.min(COLS - 1, (start % COLS) + (current === null ? 0 : step[1])));
    hoverCell(row * COLS + col);
  }

  function paintMode() {
    const m = model();
    const i = MODES.indexOf(mode);
    setStyle(thumb, 'transform', `translateX(${i * THUMB_W}px)`);
    buttons.forEach((b, j) => setAttr(b, 'aria-pressed', j === i ? 'true' : 'false'));
    setText(card.subEl, m.sub || '');
    setAttr(wrap, 'aria-label', m.aria || '');
    setText(tagEl, m.tag || '');
    setStyle(tagEl, 'display', m.tag ? '' : 'none');
    setStyle(gapEl, 'display', m.available ? 'none' : '');
    setText(gapReason, m.reason || '');
    hoverCell(null);
  }

  function update(p) {
    lastP = p;
    card.update(p);
    const m = model();
    for (let k = 0; k < cellEls.length; k++) {
      const c = m.cells && m.cells[k];
      const row = Math.floor(k / COLS);
      const col = k % COLS;
      const l = cellReveal(row, col, p);
      const a = (c && m.available ? c.alpha : 0) * l;
      // The --accent token, mixed by share; the inline value is rewritten in place each frame.
      setStyle(cellEls[k], 'background', `color-mix(in srgb, var(--accent) ${((0.06 + a * 0.94) * 100).toFixed(1)}%, transparent)`);
      setStyle(cellEls[k], 'transform', l >= 1 ? '' : `scale(${(0.7 + 0.3 * l).toFixed(3)})`);
    }
  }

  function setMode(next, fromUser) {
    if (!MODES.includes(next) || next === mode) return;
    mode = next;
    paintMode();
    update(lastP);
    if (fromUser) {
      if (typeof ctx.onCourtMode === 'function') ctx.onCourtMode(mode);
      if (typeof ctx.requestReveal === 'function') ctx.requestReveal();
    }
  }

  paintMode();
  return { el: card.el, update, setMode: (m) => setMode(m, false) };
}
