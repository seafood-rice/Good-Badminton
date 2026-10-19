// Metric tile: count-up number, ring gauge / yardstick / sparkline, delta chip and note.
// Tiles of kind "gap" are delegated to gap.js. Every number comes from the tile view-model.
import { ease, local } from '../core/motion.js';
import { fmtNumber } from '../core/format.js';
import { arcDash, sparkPath } from '../core/geometry.js';
import * as gap from './gap.js';
import { h, s, setAttr, setStyle, setText, revealStyle, scanLine, updateScan, icon } from './frame.js';

const R = 30;
const C = 2 * Math.PI * R;
const isNum = (v) => typeof v === 'number' && Number.isFinite(v);
const clamp01 = (x) => (x < 0 ? 0 : x > 1 ? 1 : x);

function ringSvg(ring) {
  const circle = (cls, extra) => s('circle', { cx: 36, cy: 36, r: R, transform: 'rotate(-90 36 36)', class: cls, ...extra });
  const track = circle('kw-ring-track');
  const band = isNum(ring.lo) && isNum(ring.hi) && ring.hi > ring.lo
    ? circle('kw-ring-band', {
      'stroke-dasharray': `${(((ring.hi - ring.lo) / 100) * C).toFixed(2)} ${C.toFixed(2)}`,
      'stroke-dashoffset': (-(ring.lo / 100) * C).toFixed(2),
    })
    : null;
  const arc = circle('kw-ring-arc', { 'stroke-dasharray': `0 ${C.toFixed(2)}` });
  const svg = s('svg', { width: 72, height: 72, viewBox: '0 0 72 72', role: 'img', 'aria-label': ring.label || '', class: 'ring-gauge' }, [track, band, arc]);
  return { svg, band, arc };
}

function yardstick(yard) {
  const lo = isNum(yard.lo) ? yard.lo : 0;
  const hi = isNum(yard.hi) ? yard.hi : 100;
  const band = h('div', { class: 'kw-yard-band', style: { left: lo + '%', width: Math.max(0, hi - lo) + '%' } });
  const mark = h('div', { class: 'kw-yard-mark', style: { left: 'calc(0% - 1.5px)' } });
  const el = h('div', { class: 'kw-yard', role: 'img', 'aria-label': yard.label || '' }, [band, mark]);
  return { el, band, mark };
}

/** data: a tile view-model (views/overview.js); ctx.slot positions it in the reveal. Returns {el, update(p)}. */
export function build(data, ctx = {}) {
  const tile = data || {};
  if (tile.kind === 'gap') return gap.build(tile, ctx);
  const slot = typeof ctx.slot === 'number' ? ctx.slot : 0;
  const scan = scanLine();

  const valueEl = h('span', { class: 'mono kpi-value' }, '');
  const unitEl = tile.unit ? h('span', { class: 'kw-tile-unit' }, tile.unit) : null;
  let ring = null;
  let yard = null;
  let spark = null;
  let visual = null;
  if (tile.kind === 'ring' && tile.ring) {
    ring = ringSvg(tile.ring);
    visual = ring.svg;
  } else if (tile.kind === 'yardstick' && tile.yard) {
    yard = yardstick(tile.yard);
    visual = yard.el;
  } else if (Array.isArray(tile.spark) && tile.spark.length >= 2) {
    const path = s('path', { d: sparkPath(tile.spark), pathLength: 1, class: 'kw-spark-line', 'stroke-dasharray': '0 1' });
    spark = path;
    visual = s('svg', { width: 96, height: 32, viewBox: '0 0 96 32', fill: 'none', role: 'img', 'aria-label': tile.sparkLabel || tile.label || '', class: 'kw-spark' }, path);
  }

  const delta = tile.delta && tile.delta.text
    ? h('span', { class: 'k-chip' + (tile.delta.tone ? ' ' + tile.delta.tone : '') }, [
      tile.delta.dir ? icon(tile.delta.dir === 'down' ? 'down' : 'up', 12) : null,
      tile.delta.text,
    ])
    : null;
  const note = tile.note ? h('span', { class: 'kw-tile-note' }, tile.note) : null;

  const el = h('div', {
    class: 'kpi hud kw-tile',
    'data-tile': tile.id || '',
    title: tile.status === 'partial' && tile.reason ? tile.reason : null,
    style: tile.flex ? { flex: tile.flex } : null,
  }, [
    scan,
    h('div', { class: 'kw-tile-label' }, [
      h('span', null, tile.label || ''),
      tile.tag ? h('span', { class: 'mono kw-tag' }, tile.tag) : null,
    ]),
    h('div', { class: 'kw-tile-main' }, [h('div', { class: 'kw-tile-num' }, [valueEl, unitEl]), visual]),
    delta || note ? h('div', { class: 'kw-tile-foot' }, [delta, note]) : null,
  ]);

  const decimals = isNum(tile.decimals) ? tile.decimals : 0;
  const suffix = tile.suffix || '';

  function update(p) {
    const l = local(p, slot);
    revealStyle(el, l);
    updateScan(scan, p, slot);
    if (typeof tile.text === 'string' && tile.text) setText(valueEl, tile.text);
    else if (isNum(tile.value)) setText(valueEl, fmtNumber(tile.value * l, decimals) + suffix);
    else setText(valueEl, '—');
    // Rings and lines draw on a slightly longer local curve (canvas: (p - i*0.05) / 0.6).
    const rl = ease(clamp01((p - slot * 0.07) / 0.6));
    if (ring) {
      setAttr(ring.arc, 'stroke-dasharray', arcDash((isNum(tile.ring.pct) ? tile.ring.pct : 0) * rl, C));
      if (ring.band) setAttr(ring.band, 'stroke-opacity', (0.35 * rl).toFixed(2));
    }
    if (yard) {
      const score = isNum(tile.yard.score) ? Math.max(0, Math.min(100, tile.yard.score)) : 0;
      setStyle(yard.mark, 'left', `calc(${(score * rl).toFixed(2)}% - 1.5px)`);
      setStyle(yard.band, 'opacity', (0.4 * rl).toFixed(2));
    }
    if (spark) setAttr(spark, 'stroke-dasharray', `${rl.toFixed(3)} 1`);
  }
  return { el, update };
}
