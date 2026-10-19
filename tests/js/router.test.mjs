import test from 'node:test';
import assert from 'node:assert/strict';
import { parseHash, formatHash, ROUTE_NAMES } from '../../static/kestrel/core/router.js';

test('parseHash decodes a stem with spaces', () => {
  assert.deepEqual(parseHash('#/matches/Dji%2020260919100627%200004%20D'),
    { name: 'matches', params: { stem: 'Dji 20260919100627 0004 D' }, query: {} });
});

test('parseHash: list routes carry no params', () => {
  for (const n of ['matches', 'players', 'pipeline', 'library', 'new', 'overview']) {
    assert.deepEqual(parseHash('#/' + n), { name: n, params: {}, query: {} });
  }
});

test('parseHash: unknown, empty and malformed hashes default to overview', () => {
  const ov = { name: 'overview', params: {}, query: {} };
  assert.deepEqual(parseHash('#/nonsense'), ov);
  assert.deepEqual(parseHash(''), ov);
  assert.deepEqual(parseHash('#'), ov);
  assert.deepEqual(parseHash('#/'), ov);
  assert.deepEqual(parseHash(undefined), ov);
  assert.deepEqual(parseHash('#/matches/%E0%A4%A'), ov);
  assert.deepEqual(parseHash('#/players/extra'), ov);
  assert.deepEqual(parseHash('#/results'), ov);
  assert.deepEqual(parseHash('#/matches/a/b'), ov);
});

test('parseHash parses the overview query', () => {
  assert.deepEqual(parseHash('#/overview?athlete=a_1&range=7d&lens=technique'),
    { name: 'overview', params: {}, query: { athlete: 'a_1', range: '7d', lens: 'technique' } });
  assert.deepEqual(parseHash('#/?range=30d').query, { range: '30d' });
  assert.deepEqual(parseHash('#/results/x%20y?mode=match'),
    { name: 'results', params: { stem: 'x y' }, query: { mode: 'match' } });
  assert.deepEqual(parseHash('#/overview?q=a%26b%3Dc&empty=').query, { q: 'a&b=c' });
});

test('formatHash percent-encodes stems and round-trips', () => {
  const h = formatHash('matches', { stem: 'a b/c' });
  assert.equal(h, '#/matches/a%20b%2Fc');
  assert.deepEqual(parseHash(h), { name: 'matches', params: { stem: 'a b/c' }, query: {} });
  const stem = 'Dji 20260919100627 0004 D';
  assert.equal(parseHash(formatHash('results', { stem }, { mode: 'match' })).params.stem, stem);
});

test('formatHash writes the query in insertion order and drops empty values', () => {
  assert.equal(formatHash('overview', {}, { athlete: 'a_1', range: '7d', lens: 'technique', squad: null, x: '' }),
    '#/overview?athlete=a_1&range=7d&lens=technique');
  assert.equal(formatHash('overview'), '#/overview');
  const q = { athlete: 'a b', range: '30d' };
  assert.deepEqual(parseHash(formatHash('overview', {}, q)).query, q);
});

test('formatHash falls back to overview for unknown routes', () => {
  assert.equal(formatHash('nonsense'), '#/overview');
  assert.equal(formatHash('results', {}), '#/overview');
  assert.ok(ROUTE_NAMES.includes('overview'));
});
