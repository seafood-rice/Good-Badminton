import test from 'node:test';
import assert from 'node:assert/strict';
import { rangeBounds, pickRange } from '../../static/kestrel/core/scope.js';

test('rangeBounds anchors on the reference date', () => {
  assert.deepEqual(rangeBounds('30d', '2026-10-18'), { from: '2026-09-19', to: '2026-10-18' });
  assert.deepEqual(rangeBounds('7d', '2026-10-18'), { from: '2026-10-12', to: '2026-10-18' });
  assert.deepEqual(rangeBounds('season', '2026-10-18'), { from: '2026-01-01', to: '2026-10-18' });
  assert.deepEqual(rangeBounds('all', '2026-10-18'), { from: null, to: '2026-10-18' });
});

test('rangeBounds crosses month and year boundaries', () => {
  assert.equal(rangeBounds('7d', '2026-01-03').from, '2025-12-28');
  assert.equal(rangeBounds('30d', '2024-03-10').from, '2024-02-10');
  assert.equal(rangeBounds('7d', '2026-10-18T22:00:00Z').to, '2026-10-18');
});

test('rangeBounds rejects bad input', () => {
  assert.throws(() => rangeBounds('90d', '2026-10-18'), RangeError);
  assert.throws(() => rangeBounds('7d', 'nope'), RangeError);
  assert.deepEqual(rangeBounds('all', null), { from: null, to: null });
});

const pts = [
  { date: '2026-01-05', index: 60 }, { date: '2026-09-10', index: 66 },
  { date: '2026-10-02', index: 70 }, { date: '2026-10-12', index: 72 }, { date: '2026-10-18', index: 74 },
];

test('pickRange anchors on the latest point so old footage still shows', () => {
  assert.deepEqual(pickRange(pts, '7d').map((p) => p.date), ['2026-10-12', '2026-10-18']);
  assert.deepEqual(pickRange(pts, '30d').map((p) => p.date), ['2026-10-02', '2026-10-12', '2026-10-18']);
  assert.equal(pickRange(pts, 'season').length, 5);
  assert.equal(pickRange(pts, 'all').length, 5);
  const old = [{ date: '2025-03-01' }, { date: '2025-03-20' }];
  assert.equal(pickRange(old, '7d').length, 1);
});

test('pickRange tolerates empty, unsorted and undated input', () => {
  assert.deepEqual(pickRange([], '30d'), []);
  assert.deepEqual(pickRange(null, '30d'), []);
  const shuffled = [pts[3], pts[0], pts[4]];
  assert.deepEqual(pickRange(shuffled, '7d').map((p) => p.date), ['2026-10-12', '2026-10-18']);
  assert.deepEqual(pickRange([{ date: null }, { date: '2026-10-18' }], '7d').map((p) => p.date), ['2026-10-18']);
});
