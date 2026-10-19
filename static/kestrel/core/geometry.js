// Pure chart geometry (radar, trend, court grid, ring gauge). DOM-free.
// Formulas follow the design canvas (Main.dc.html renderVals).
import { ease } from './motion.js';

export function clamp(x, a, b) {
  return Math.max(a, Math.min(b, x));
}

const f1 = (v) => v.toFixed(1);
const isNum = (v) => typeof v === 'number' && Number.isFinite(v);

/** Point for axis `i` at `value` (0-100) on a radar of radius `r` centred on (cx, cy). Axis 0 points up, clockwise. */
export function radarPoint(i, value, { cx, cy, r, n = 7 }) {
  const th = (2 * Math.PI * i) / n;
  const rad = r * (value / 100);
  return [cx + rad * Math.sin(th), cy - rad * Math.cos(th)];
}

/** Polygon "x,y x,y" for scored axes only (null scores are skipped; vertex order and axis index are kept). */
export function radarPolygon(axes, scale, opts) {
  const n = (opts && opts.n) || 7;
  const out = [];
  (axes || []).forEach((axis, i) => {
    const score = axis ? axis.score : null;
    if (!isNum(score)) return;
    out.push(radarPoint(i, score * scale, { ...opts, n }).map(f1).join(','));
  });
  return out.join(' ');
}

/** Grid ring: every axis at `fraction` (0-1) of the radius. */
export function ringPolygon(fraction, opts) {
  const n = (opts && opts.n) || 7;
  const out = [];
  for (let i = 0; i < n; i++) out.push(radarPoint(i, fraction * 100, { ...opts, n }).map(f1).join(','));
  return out.join(' ');
}

/** Label position and text anchor just outside axis `i`. */
export function radarLabel(i, { cx, cy, r, n = 7, gap = 18 }) {
  const th = (2 * Math.PI * i) / n;
  const rr = r + gap;
  const x = cx + rr * Math.sin(th);
  const y = cy - rr * Math.cos(th);
  const dx = Math.sin(th);
  return {
    x: +f1(x),
    y: +f1(y + (Math.cos(th) > 0.6 ? -2 : 4)),
    anchor: dx > 0.3 ? 'start' : dx < -0.3 ? 'end' : 'middle',
  };
}

function seriesValue(p) {
  if (p && typeof p === 'object') {
    for (const k of ['index', 'value', 'y', 'score']) if (isNum(p[k])) return p[k];
    return null;
  }
  return isNum(p) ? p : null;
}

/**
 * Line path "M x y L x y ..." through the points. Points are numbers or objects carrying
 * `index`/`value`/`y`/`score`; null points are skipped without shifting the x positions.
 * x spans [pad, w - pad]; y maps yMax to `pad` and yMin to `h - pad` (defaults: the data range), clamped to the box.
 */
export function trendPath(points, { w, h, pad = 0, yMin, yMax } = {}) {
  const vals = (points || []).map(seriesValue);
  const real = vals.filter((v) => v !== null);
  if (!real.length) return '';
  const lo = isNum(yMin) ? yMin : Math.min(...real);
  const hi = isNum(yMax) ? yMax : Math.max(...real);
  const n = vals.length;
  const span = w - 2 * pad;
  const X = (i) => (n === 1 ? w / 2 : pad + i * (span / (n - 1)));
  const Y = (v) => {
    if (hi === lo) return h / 2;
    return clamp(pad + ((hi - v) / (hi - lo)) * (h - 2 * pad), 0, h);
  };
  let d = '';
  vals.forEach((v, i) => {
    if (v === null) return;
    d += (d ? 'L' : 'M') + f1(X(i)) + ' ' + f1(Y(v));
  });
  return d;
}

/**
 * Scales for a trend plot box with asymmetric padding (canvas: x 40..628, y 16..200 in a 640 x 236 viewBox).
 * x(i) spreads `n` points across [x0, x1] (one point sits in the middle); y(v) maps yMax to y0 and yMin to y1,
 * clamped into the box.
 */
export function trendScale(n, { x0, x1, y0, y1, yMin, yMax }) {
  const x = (i) => (n <= 1 ? (x0 + x1) / 2 : x0 + i * ((x1 - x0) / (n - 1)));
  const y = (v) => {
    if (yMax === yMin) return (y0 + y1) / 2;
    return clamp(y0 + ((yMax - v) / (yMax - yMin)) * (y1 - y0), Math.min(y0, y1), Math.max(y0, y1));
  };
  return { x, y };
}

/** Line through a series inside a trendScale box: {d, pts:[{i, x, y, value}]}; null values are skipped in place. */
export function trendLine(points, box) {
  const vals = (points || []).map(seriesValue);
  const { x, y } = trendScale(vals.length, box);
  const pts = [];
  let d = '';
  vals.forEach((v, i) => {
    if (v === null) return;
    const px = x(i);
    const py = y(v);
    pts.push({ i, x: px, y: py, value: v });
    d += (d ? 'L' : 'M') + f1(px) + ' ' + f1(py);
  });
  return { d, pts };
}

/**
 * A y domain for a 0-100 score chart: step-aligned, at least `minSpan` wide, inside [floor, ceil],
 * with six evenly spaced ticks from the top. Empty input gives the canvas default 60-85.
 */
export function niceDomain(values, { step = 5, minSpan = 20, floor = 0, ceil = 100 } = {}) {
  const real = (values || []).filter(isNum);
  if (!real.length) return { min: 60, max: 85, ticks: [85, 80, 75, 70, 65, 60] };
  let lo = Math.floor(Math.min(...real) / step) * step;
  let hi = Math.ceil(Math.max(...real) / step) * step;
  if (hi - lo < minSpan) {
    const extra = minSpan - (hi - lo);
    const below = Math.floor(extra / 2 / step) * step;
    lo -= below;
    hi += extra - below;
  }
  const tick = Math.max(step, Math.ceil((hi - lo) / 5 / step) * step);
  hi = lo + 5 * tick;
  if (hi > ceil) {
    hi = ceil;
    lo = ceil - 5 * tick;
  }
  if (lo < floor) {
    lo = floor;
    hi = floor + 5 * tick;
  }
  const ticks = [];
  for (let k = 0; k <= 5; k++) ticks.push(hi - k * tick);
  return { min: lo, max: hi, ticks };
}

/** Sparkline path in a w x h box (canvas defaults: 96 x 32, 2 px side padding, 4 px vertical). */
export function sparkPath(values, { w = 96, h = 32, padX = 2, padY = 4 } = {}) {
  const vals = (values || []).map((v) => (isNum(v) ? v : null));
  const real = vals.filter((v) => v !== null);
  if (real.length < 2) return '';
  const mn = Math.min(...real);
  const mx = Math.max(...real);
  const n = vals.length;
  let d = '';
  vals.forEach((v, i) => {
    if (v === null) return;
    const x = padX + i * ((w - 2 * padX) / (n - 1));
    const y = h - padY - ((v - mn) / (mx - mn || 1)) * (h - 2 * padY);
    d += (d ? 'L' : 'M') + f1(x) + ' ' + f1(y);
  });
  return d;
}

/** Flatten a grid of shares (rows from the net) to cells; alpha = share / maxShare * reveal. */
export function gridCells(grid, { reveal = 1 } = {}) {
  if (!Array.isArray(grid)) return [];
  let max = 0;
  for (const row of grid) for (const v of row || []) if (isNum(v) && v > max) max = v;
  const cells = [];
  grid.forEach((row, r) => {
    (row || []).forEach((v, c) => {
      const share = isNum(v) ? v : 0;
      cells.push({ row: r, col: c, share, alpha: max > 0 ? (share / max) * reveal : 0 });
    });
  });
  return cells;
}

/** Row-by-row bloom (from the net outwards from the centre column) for global progress `p`, 0-1. */
export function cellReveal(row, col, p) {
  const eH = clamp(p * 1.4, 0, 1);
  return ease(clamp((eH - (row * 0.045 + Math.abs(col - 4.5) * 0.02)) / 0.45, 0, 1));
}

/** stroke-dasharray value "filled circumference" for a ring gauge of `pct` (0-100). */
export function arcDash(pct, circumference) {
  const p = isNum(pct) ? clamp(pct, 0, 100) : 0;
  const filled = +((p / 100) * circumference).toFixed(2);
  return filled + ' ' + +Number(circumference).toFixed(2);
}
