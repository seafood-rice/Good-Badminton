// The legacy results page's rally summary on a rally_segments.json with signal "learned" (spec 5.4):
// a learned count is a validated count, so it reads "N rallies detected", with no swing / coarse / shuttle hedge.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const src = readFileSync(new URL('../../static/kestrel/legacy/kestrel-legacy.js', import.meta.url), 'utf8');
const start = src.indexOf("fetch('/api/output/' + stem + '/rally_segments.json')");
const from = src.indexOf('var n = d && Array.isArray', start);
const to = src.indexOf("document.getElementById('rally-info').textContent = text;", from);
const block = src.slice(from, to);                         // the pure text decision, up to the DOM write
assert.ok(start > 0 && from > start && to > from, 'rally summary block found');
const decide = new Function('d', 'zh', block + '; return { text: text, failed: failed, n: n };');

const doc = (signal, extra = {}, rallies = 2) => ({
  rallies: Array.from({ length: rallies }, (_, i) => ({ id: i + 1 })),
  detection: { signal, degraded: false, ...extra },
});

test('learned segments read as plain rallies in English and Chinese', () => {
  assert.equal(decide(doc('learned'), false).text, '2 rallies detected');
  assert.equal(decide(doc('learned', {}, 1), false).text, '1 rally detected');
  assert.equal(decide(doc('learned'), true).text, '2 个回合');
});

test('learned is not hedged like swing, shuttle or degraded', () => {
  const learned = decide(doc('learned'), false).text;
  for (const other of [doc('swing'), doc('shuttle'), doc('swing', { degraded: true })]) {
    assert.notEqual(decide(other, false).text, learned);
  }
  assert.ok(!/estimated|validated|coarse|unreliable/.test(learned));
});

test('a learned file with a model block and no rallies still gives a number', () => {
  const d = doc('learned', { model: { version: 'v1', f1: 0.6 } }, 0);
  assert.equal(decide(d, false).n, 0);
  assert.equal(decide(d, false).failed, false);
});
