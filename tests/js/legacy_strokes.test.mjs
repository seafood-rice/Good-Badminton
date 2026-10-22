import test from 'node:test';
import assert from 'node:assert/strict';
import { strokeNotice, timelineItems, distributionText } from '../../static/kestrel/legacy/strokes.js';

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
