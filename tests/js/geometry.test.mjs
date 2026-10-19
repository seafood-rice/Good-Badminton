import test from 'node:test';
import assert from 'node:assert/strict';
import {
  radarPoint, radarPolygon, ringPolygon, trendPath, gridCells, arcDash, clamp,
  sparkPath, radarLabel, cellReveal,
} from '../../static/kestrel/core/geometry.js';

const O = { cx: 160, cy: 150, r: 105 };

test('radarPoint: axis 0 points straight up', () => {
  assert.deepEqual(radarPoint(0, 100, O), [160, 45]);
  assert.deepEqual(radarPoint(0, 50, O), [160, 97.5]);
  assert.deepEqual(radarPoint(0, 0, O), [160, 150]);
});

test('radarPoint: axes advance clockwise by 2*pi/7', () => {
  const [x, y] = radarPoint(1, 100, O);
  const th = (2 * Math.PI) / 7;
  assert.ok(Math.abs(x - (160 + 105 * Math.sin(th))) < 1e-9);
  assert.ok(Math.abs(y - (150 - 105 * Math.cos(th))) < 1e-9);
  assert.ok(x > 160 && y < 150);
  assert.deepEqual(radarPoint(0, 100, { ...O, n: 4 }), [160, 45]);
  const [x4] = radarPoint(1, 100, { ...O, n: 4 });
  assert.ok(Math.abs(x4 - 265) < 1e-9);
});

test('radarPolygon skips null scores and keeps axis order', () => {
  const full = radarPolygon(
    [{ score: 100 }, { score: 50 }, { score: 80 }, { score: 60 }, { score: 70 }, { score: 90 }, { score: 40 }], 1, O);
  assert.equal(full.split(' ').length, 7);
  const axes = [{ score: 100 }, { score: null }, { score: 80 }, { score: 60 }, { score: undefined }, { score: 90 }, { score: 40 }];
  const poly = radarPolygon(axes, 1, O);
  const pts = poly.split(' ');
  assert.equal(pts.length, 5);
  assert.equal(pts[0], '160.0,45.0');
  const expected = [0, 2, 3, 5, 6].map((i) => radarPoint(i, axes[i].score, O).map((v) => v.toFixed(1)).join(','));
  assert.deepEqual(pts, expected);
});

test('radarPolygon scales values (reveal from the centre)', () => {
  assert.equal(radarPolygon([{ score: 100 }, { score: 100 }, { score: 100 }], 0, O), '160.0,150.0 160.0,150.0 160.0,150.0');
  assert.equal(radarPolygon([{ score: 100 }], 0.5, O), '160.0,97.5');
  assert.equal(radarPolygon([], 1, O), '');
});

test('ringPolygon draws all seven vertices at the fraction of the radius', () => {
  const ring = ringPolygon(0.5, O).split(' ');
  assert.equal(ring.length, 7);
  assert.equal(ring[0], '160.0,97.5');
  assert.equal(ringPolygon(1, { ...O, n: 4 }).split(' ').length, 4);
});

test('radarLabel anchors by side of the chart', () => {
  assert.equal(radarLabel(0, O).anchor, 'middle');
  assert.equal(radarLabel(1, O).anchor, 'start');
  assert.equal(radarLabel(6, O).anchor, 'end');
  assert.ok(radarLabel(0, O).y < 45);
});

test('trendPath: two points, one M and one L, max point sits on the top padding', () => {
  const d = trendPath([60, 80], { w: 640, h: 236, pad: 16 });
  assert.ok(d.startsWith('M'));
  assert.equal((d.match(/L/g) || []).length, 1);
  assert.equal((d.match(/M/g) || []).length, 1);
  const nums = d.match(/-?\d+(\.\d+)?/g).map(Number);
  assert.equal(nums[0], 16); // first x at pad
  assert.equal(nums[2], 640 - 16); // last x at w - pad
  assert.equal(nums[3], 16); // max value -> y = pad
  assert.equal(nums[1], 236 - 16); // min value -> y = h - pad
});

test('trendPath honours explicit yMin/yMax, accepts {index} points, and handles degenerate input', () => {
  const d = trendPath([{ index: 85 }, { index: 60 }], { w: 100, h: 100, pad: 10, yMin: 60, yMax: 85 });
  assert.equal(d, 'M10.0 10.0L90.0 90.0');
  assert.equal(trendPath([], { w: 100, h: 100, pad: 10 }), '');
  const single = trendPath([70], { w: 100, h: 100, pad: 10 });
  assert.ok(/^M/.test(single) && !single.includes('L'));
  const flat = trendPath([70, 70, 70], { w: 100, h: 100, pad: 10 });
  assert.ok(!flat.includes('NaN'));
});

test('trendPath skips null points without shifting x positions', () => {
  const d = trendPath([60, null, 80], { w: 120, h: 100, pad: 10, yMin: 60, yMax: 80 });
  assert.equal(d, 'M10.0 90.0L110.0 10.0');
});

test('gridCells: alpha = share / maxShare * reveal, row-major', () => {
  const grid = [[0.1, 0.2], [0.4, 0.3]];
  const cells = gridCells(grid, { reveal: 0.5 });
  assert.equal(cells.length, 4);
  assert.deepEqual(cells[0], { row: 0, col: 0, share: 0.1, alpha: (0.1 / 0.4) * 0.5 });
  const max = cells.find((c) => c.share === 0.4);
  assert.equal(max.alpha, 0.5);
  assert.equal(max.row, 1);
  assert.equal(max.col, 0);
  assert.equal(gridCells(grid)[2].alpha, 1);
});

test('gridCells: empty or all-zero grids give zero alpha, not NaN', () => {
  assert.deepEqual(gridCells([], { reveal: 1 }), []);
  assert.deepEqual(gridCells(null), []);
  assert.ok(gridCells([[0, 0]], { reveal: 1 }).every((c) => c.alpha === 0));
});

test('cellReveal staggers rows from the net', () => {
  const early = cellReveal(10, 0, 0.2);
  const late = cellReveal(0, 4, 0.2);
  assert.ok(late > early);
  assert.equal(cellReveal(10, 9, 1), 1);
  assert.equal(cellReveal(0, 0, 0), 0);
});

test('arcDash', () => {
  assert.equal(arcDash(50, 100), '50 100');
  assert.equal(arcDash(0, 188.5), '0 188.5');
  assert.equal(arcDash(150, 100), '100 100');
  assert.equal(arcDash(-5, 100), '0 100');
  assert.equal(arcDash(NaN, 100), '0 100');
});

test('clamp', () => {
  assert.equal(clamp(5, 0, 1), 1);
  assert.equal(clamp(-5, 0, 1), 0);
  assert.equal(clamp(0.4, 0, 1), 0.4);
});

test('sparkPath', () => {
  const d = sparkPath([1, 2, 3], { w: 96, h: 32 });
  assert.ok(d.startsWith('M'));
  assert.equal((d.match(/L/g) || []).length, 2);
  assert.equal(sparkPath([5], { w: 96, h: 32 }), '');
  assert.ok(!sparkPath([4, 4, 4], { w: 96, h: 32 }).includes('NaN'));
});
