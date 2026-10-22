import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { strokeNotice, timelineItems, distributionText, techniqueStale, noticeText } from '../../static/kestrel/legacy/strokes.js';

test('schema-2 strokes render 12-type names and group distribution', () => {
  const d = { schema: 2, classifier: { validated_on_owner_footage: true },
    strokes: [{ stroke: 'push', group: 'drive' }, { stroke: 'unknown', group: null }],
    groups: { drive: 1 } };
  assert.deepEqual(timelineItems(d, 'en'), [{ text: 'Push', muted: false }, { text: 'Uncertain', muted: true }]);
  assert.equal(distributionText(d, 'zh'), '平抽球 1');
  assert.equal(strokeNotice(d), null);
});

test('old strokes.json is stale: no labels, re-label notice', () => {
  const d = { strokes: [{ stroke: 'net' }], distribution: { net: 1 } };
  assert.deepEqual(timelineItems(d, 'en'), []);
  assert.equal(distributionText(d, 'en'), '');
  assert.equal(strokeNotice(d), 'stale');
});

test('notices', () => {
  assert.equal(strokeNotice({ schema: 2, classifier: null, strokes: [] }), 'no_model');
  assert.equal(strokeNotice({ schema: 2, classifier: {}, strokes: [] }), 'unverified');
});

test('technique reports without `scored` are stale (old rule-classifier labels)', () => {
  assert.equal(techniqueStale({ summary: {}, strokes: [{ stroke_type: 'smash', scored: true }] }), false);
  assert.equal(techniqueStale({ summary: {}, strokes: [{ stroke_type: 'smash', scored: false }] }), false);
  assert.equal(techniqueStale({ summary: {}, strokes: [{ stroke_type: 'smash', scored: true }, { stroke_type: 'high_clear' }] }), true);
  assert.equal(techniqueStale({ summary: {}, strokes: [null] }), true);
  assert.equal(techniqueStale(null), false);
  assert.equal(noticeText('stale', 'en'), 'Stroke labels out of date — re-label this result.');
});

test('legacy technique box: stale notice instead of old labels; labels escaped', () => {
  const src = readFileSync(new URL('../../static/kestrel/legacy/kestrel-legacy.js', import.meta.url), 'utf8');
  const at = src.indexOf("fetch('/api/technique/'");
  const block = src.slice(at, src.indexOf('.catch(', at + 200));
  const stale = block.indexOf("LS.techniqueStale(d)");
  assert.ok(stale > 0 && block.indexOf("LS.noticeText('stale'", stale) > stale, 'stale notice in the technique box');
  assert.ok(stale < block.indexOf('techniqueLabel('), 'stale check comes before any label is rendered');
  const uses = block.match(/[\w.]*\(?techniqueLabel\([^)]*\)/g) || [];
  assert.ok(uses.length >= 2);
  for (const u of uses) assert.ok(u.startsWith('escHTML(techniqueLabel('), u);
});
