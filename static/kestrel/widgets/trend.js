// Technique index over time: reference band, the athlete's line (drawn with pathLength = 1), the squad benchmark
// as a dashed line when it exists, and a hover / keyboard crosshair with a tooltip.
import { t } from '../core/i18n.js';
import { local } from '../core/motion.js';
import { fmtNumber } from '../core/format.js';
import { trendScale, trendLine } from '../core/geometry.js';
import * as frame from './frame.js';
import { h, s, setAttr, setText } from './frame.js';

const W = 640;
const BOX = { x0: 40, x1: 628, y0: 16, y1: 200 };
const SPAN = BOX.x1 - BOX.x0;
const isNum = (v) => typeof v === 'number' && Number.isFinite(v);
const clamp = (x, a, b) => Math.max(a, Math.min(b, x));
const f1 = (v) => v.toFixed(1);

function legend(d, lang) {
  const item = (swatchClass, label) => h('span', { class: 'kw-legend-item' }, [h('span', { class: 'kw-swatch ' + swatchClass, 'aria-hidden': 'true' }), label]);
  return h('div', { class: 'kw-legend' }, [
    item('line', d.legendName || t('tile.technique_index', lang)),
    isNum(d.benchmark) ? item('dash', t('trend.legend.median', lang)) : null,
    item('band', t('trend.legend.reference', lang)),
  ]);
}

/**
 * data: the trend view-model {points:[{date,stem,index,dateLabel}], reference, benchmark|null, benchmarkReason,
 * domain:{min,max,ticks}, sub, aria, gapText, median, hint, empty, emptyText, legendName}.
 * Returns {el, update(p), destroy()}.
 */
export function build(data, ctx = {}) {
  const d = data || {};
  const lang = ctx.lang;
  const slot = typeof ctx.slot === 'number' ? ctx.slot : 0;
  const card = frame.build({ id: 'trend', title: t('trend.title', lang), sub: d.sub || '', flex: '7 1 460px', right: legend(d, lang) }, ctx);
  const pts = Array.isArray(d.points) ? d.points : [];

  if (d.empty || pts.length < 2) {
    card.body.appendChild(h('div', { class: 'kw-note' }, d.emptyText || t('trend.empty', lang)));
    return { el: card.el, update: card.update, destroy() {} };
  }

  const dom = d.domain || { min: 60, max: 85, ticks: [85, 80, 75, 70, 65, 60] };
  const box = { ...BOX, yMin: dom.min, yMax: dom.max };
  const sc = trendScale(pts.length, box);
  const line = trendLine(pts, box);
  const ref = d.reference || { lo: 70, hi: 100 };
  const bandTop = sc.y(Math.min(ref.hi, dom.max));
  const bandBottom = sc.y(Math.max(ref.lo, dom.min));
  const hasBench = isNum(d.benchmark);
  const benchY = hasBench ? sc.y(d.benchmark) : 0;
  const n = pts.length;

  const lineEl = s('path', { d: line.d, pathLength: 1, class: 'kw-trend-line', 'stroke-dasharray': '0 1' });
  const area = s('path', { d: line.d + `L${BOX.x1} ${BOX.y1}L${BOX.x0} ${BOX.y1}Z`, class: 'kw-trend-area' });
  const bench = hasBench ? s('path', { d: `M${BOX.x0} ${f1(benchY)}H${BOX.x1}`, class: 'kw-trend-bench', 'stroke-dasharray': `0 ${SPAN}` }) : null;
  const cross = s('path', { d: '', class: 'kw-trend-cross' });
  const endHalo = s('circle', { cx: 0, cy: 0, r: 9, class: 'kw-trend-end-halo' });
  const endDot = s('circle', { cx: 0, cy: 0, r: 4.5, class: 'kw-trend-end' });
  const lastText = s('text', { x: 0, y: 0, 'text-anchor': 'end', class: 'mono kw-trend-last' }, '');
  const tipRect = s('rect', { x: 0, y: 0, width: 118, height: 46, rx: 8, class: 'kw-trend-tip' });
  const tipDate = s('text', { x: 0, y: 0, class: 'mono kw-trend-tip-date' }, '');
  const tipBench = s('tspan', { dx: 8, class: 'kw-trend-tip-bench' }, '');
  const tipVal = s('text', { x: 0, y: 0, class: 'mono kw-trend-tip-val' }, ['', tipBench]);
  const tip = s('g', { class: 'kw-trend-tipg', style: 'opacity:0' }, [tipRect, tipDate, tipVal]);

  const grid = dom.ticks.map((v) => `M${BOX.x0} ${f1(sc.y(v))}H${BOX.x1}`).join('');
  const tickLabels = dom.ticks.map((v) => s('text', { x: 30, y: f1(sc.y(v) + 4), 'text-anchor': 'end', class: 'mono kw-axis-text' }, String(v)));
  const mid = Math.round((n - 1) / 2);
  const xLabel = (i, x, anchor) => s('text', { x, y: 226, 'text-anchor': anchor, class: 'mono kw-axis-text' }, pts[i].dateLabel || '');

  const svg = s('svg', {
    viewBox: `0 0 ${W} 236`, role: 'img', 'aria-label': d.aria || '', class: 'kw-trend', tabindex: '0',
  }, [
    s('rect', { x: BOX.x0, y: f1(bandTop), width: SPAN, height: f1(Math.max(0, bandBottom - bandTop)), rx: 4, class: 'kw-trend-band' }),
    s('path', { d: grid, class: 'kw-trend-grid' }),
    ...tickLabels,
    area, bench, lineEl, cross, endHalo, endDot, lastText, tip,
    xLabel(0, BOX.x0, 'start'),
    xLabel(mid, (BOX.x0 + BOX.x1) / 2, 'middle'),
    xLabel(n - 1, BOX.x1, 'end'),
  ]);
  card.body.appendChild(svg);
  card.body.appendChild(h('div', { class: 'kw-foot' }, [
    d.gapText ? h('span', { class: 'kw-foot-strong' }, d.gapText) : null,
    d.median ? h('span', null, d.median) : d.benchmarkReason ? h('span', { class: 'kw-foot-muted' }, d.benchmarkReason) : null,
    h('span', { class: 'kw-foot-muted' }, d.hint || t('trend.hint', lang)),
  ]));

  let hover = null;
  let lt = 0;

  function paintHover() {
    const i = hover === null ? n - 1 : hover;
    const p = pts[i];
    const hx = sc.x(i);
    const hy = sc.y(p.index);
    setAttr(endHalo, 'cx', f1(hx)); setAttr(endHalo, 'cy', f1(hy));
    setAttr(endDot, 'cx', f1(hx)); setAttr(endDot, 'cy', f1(hy));
    const endO = lt > 0.95 ? '1' : '0';
    setAttr(endHalo, 'opacity', endO);
    setAttr(endDot, 'opacity', endO);
    setAttr(lastText, 'x', f1(Math.min(hx - 14, 612)));
    setAttr(lastText, 'y', f1(hy - 16));
    setAttr(lastText, 'opacity', lt > 0.95 && hover === null ? '1' : '0');
    setText(lastText, fmtNumber(p.index, 1));
    setAttr(cross, 'd', hover === null ? '' : `M${f1(hx)} ${BOX.y0}V${BOX.y1}`);
    const tx = clamp(hx - 59, 44, 522);
    const ty = clamp(hy - 62, 8, 150);
    setAttr(tip, 'style', `opacity:${hover === null ? 0 : 1}`);
    setAttr(tipRect, 'x', f1(tx)); setAttr(tipRect, 'y', f1(ty));
    setAttr(tipDate, 'x', f1(tx + 10)); setAttr(tipDate, 'y', f1(ty + 18));
    setAttr(tipVal, 'x', f1(tx + 10)); setAttr(tipVal, 'y', f1(ty + 36));
    setText(tipDate, p.dateLabel || '');
    if (tipVal.firstChild) tipVal.firstChild.nodeValue = fmtNumber(p.index, 1);
    setText(tipBench, hasBench ? fmtNumber(d.benchmark, 1) : '');
  }

  function indexAt(clientX) {
    const r = typeof svg.getBoundingClientRect === 'function' ? svg.getBoundingClientRect() : null;
    if (!r || !r.width) return null;
    const xv = ((clientX - r.left) / r.width) * W;
    return clamp(Math.round((xv - BOX.x0) / (SPAN / (n - 1))), 0, n - 1);
  }
  const onMove = (e) => {
    const i = indexAt(e.clientX);
    if (i !== null && i !== hover) { hover = i; paintHover(); }
  };
  const onLeave = () => { if (hover !== null) { hover = null; paintHover(); } };
  const onKey = (e) => {
    const cur = hover === null ? n - 1 : hover;
    let next = null;
    if (e.key === 'ArrowLeft') next = Math.max(0, cur - 1);
    else if (e.key === 'ArrowRight') next = Math.min(n - 1, cur + 1);
    else if (e.key === 'Home') next = 0;
    else if (e.key === 'End') next = n - 1;
    else if (e.key === 'Escape') { onLeave(); return; }
    if (next === null) return;
    e.preventDefault();
    hover = next;
    paintHover();
  };
  const moveEvt = typeof window !== 'undefined' && 'PointerEvent' in window ? 'pointermove' : 'mousemove';
  const leaveEvt = moveEvt === 'pointermove' ? 'pointerleave' : 'mouseleave';
  svg.addEventListener(moveEvt, onMove);
  svg.addEventListener(leaveEvt, onLeave);
  svg.addEventListener('keydown', onKey);
  svg.addEventListener('blur', onLeave);

  function update(p) {
    card.update(p);
    lt = local(p, slot);
    setAttr(lineEl, 'stroke-dasharray', `${lt.toFixed(3)} 1`);
    setAttr(area, 'fill-opacity', (0.12 * lt).toFixed(3));
    if (bench) setAttr(bench, 'stroke-dasharray', lt >= 0.999 ? '5 5' : `${(lt * SPAN).toFixed(1)} ${SPAN}`);
    paintHover();
  }

  function destroy() {
    svg.removeEventListener(moveEvt, onMove);
    svg.removeEventListener(leaveEvt, onLeave);
    svg.removeEventListener('keydown', onKey);
    svg.removeEventListener('blur', onLeave);
  }

  return { el: card.el, update, destroy };
}
