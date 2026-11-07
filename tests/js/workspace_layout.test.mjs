import test from 'node:test';
import assert from 'node:assert/strict';
import {
  DEFAULT_LAYOUT, CARD_IDS, normaliseLayout, moveCard, toggleCollapsed, stepCard, defaultLayout,
} from '../../static/kestrel/views/workspace_layout.js';

test('default layout is the existing one: video and timeline main; movement, landing, inspector, summary side', () => {
  assert.deepEqual(DEFAULT_LAYOUT.order.main, ['video', 'timeline']);
  assert.deepEqual(DEFAULT_LAYOUT.order.side, ['movement', 'landing', 'inspector', 'summary']);
  assert.deepEqual(DEFAULT_LAYOUT.collapsed, []);
  assert.equal(CARD_IDS.length, 6);
});

test('normalise: junk becomes the default', () => {
  for (const junk of [null, undefined, 5, 'x', [], {}, { order: 3 }, { order: [] }]) {
    assert.deepEqual(normaliseLayout(junk), defaultLayout());
  }
});

test('normalise: unknown ids dropped, duplicates removed, missing ids go to their default zone', () => {
  const n = normaliseLayout({ order: { main: ['summary', 'bogus', 'video', 'video', 7], side: ['video', 'landing'] }, collapsed: ['nope', 'landing', 'landing', 3] });
  assert.deepEqual(n.order.main, ['summary', 'video', 'timeline']);
  assert.deepEqual(n.order.side, ['landing', 'movement', 'inspector']);
  assert.deepEqual(n.collapsed, ['landing']);
});

test('normalise: a partial old layout keeps what it has', () => {
  const n = normaliseLayout({ order: { main: ['timeline', 'video'] } });
  assert.deepEqual(n.order.main, ['timeline', 'video']);
  assert.deepEqual(n.order.side, ['movement', 'landing', 'inspector', 'summary']);
});

test('moveCard within a zone and between zones; the input is not mutated', () => {
  const base = defaultLayout();
  const a = moveCard(base, 'summary', 'side', 0);
  assert.deepEqual(a.order.side, ['summary', 'movement', 'landing', 'inspector']);
  assert.deepEqual(base.order.side, ['movement', 'landing', 'inspector', 'summary']);
  const b = moveCard(base, 'summary', 'main', 1);
  assert.deepEqual(b.order.main, ['video', 'summary', 'timeline']);
  assert.deepEqual(b.order.side, ['movement', 'landing', 'inspector']);
});

test('moveCard clamps the index and ignores unknown ids / zones', () => {
  const base = defaultLayout();
  assert.equal(moveCard(base, 'video', 'side', 99).order.side.at(-1), 'video');
  assert.equal(moveCard(base, 'video', 'side', -4).order.side[0], 'video');
  assert.deepEqual(moveCard(base, 'video', 'main', 'x').order.main, ['timeline', 'video']);
  assert.deepEqual(moveCard(base, 'zzz', 'main', 0), base);
  assert.deepEqual(moveCard(base, 'video', 'nowhere', 0), base);
});

test('toggleCollapsed adds then removes', () => {
  const a = toggleCollapsed(defaultLayout(), 'movement');
  assert.deepEqual(a.collapsed, ['movement']);
  assert.deepEqual(toggleCollapsed(a, 'movement').collapsed, []);
  assert.deepEqual(toggleCollapsed(a, 'bogus').collapsed, ['movement']);
});

test('stepCard: up / down in the zone, left / right across, edges do nothing', () => {
  const base = defaultLayout();
  let r = stepCard(base, 'landing', 'up');
  assert.deepEqual([r.moved, r.zone, r.index], [true, 'side', 0]);
  assert.deepEqual(r.layout.order.side, ['landing', 'movement', 'inspector', 'summary']);
  assert.equal(stepCard(base, 'movement', 'up').moved, false);
  assert.equal(stepCard(base, 'summary', 'down').moved, false);
  r = stepCard(base, 'timeline', 'right');
  assert.deepEqual([r.moved, r.zone, r.index], [true, 'side', 1]);
  r = stepCard(base, 'summary', 'left');
  assert.deepEqual([r.moved, r.zone, r.index], [true, 'main', 2]);
  assert.equal(stepCard(base, 'video', 'left').moved, false);
  assert.equal(stepCard(base, 'movement', 'right').moved, false);
});
