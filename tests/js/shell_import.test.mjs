// Import-safety smoke for the DOM modules of the shell: importing them under Node (no window, no document,
// no localStorage) must not throw. Also covers the few pure helpers they export.
import test from 'node:test';
import assert from 'node:assert/strict';

const MODULES = [
  'shell/dom.js', 'shell/rail.js', 'shell/topbar.js', 'shell/palette.js', 'shell/peek.js',
  'shell/transition.js', 'shell/ground.js', 'views/legacy.js', 'main.js',
];

for (const rel of MODULES) {
  test(`${rel} imports without a DOM`, async () => {
    assert.equal(typeof globalThis.document, 'undefined');
    const mod = await import(`../../static/kestrel/${rel}`);
    assert.ok(mod && typeof mod === 'object');
  });
}

test('views/legacy.js exports mount', async () => {
  const mod = await import('../../static/kestrel/views/legacy.js');
  assert.equal(typeof mod.mount, 'function');
});

test('main.js exposes the route table without starting', async () => {
  const mod = await import('../../static/kestrel/main.js');
  assert.deepEqual(Object.keys(mod.VIEW_LOADERS).sort(),
    ['library', 'matches', 'new', 'overview', 'pipeline', 'players', 'results', 'workspace']);
  assert.equal(mod.viewKey({ name: 'matches', params: { stem: 'a b' }, query: {} }), 'workspace');
  assert.equal(mod.viewKey({ name: 'matches', params: {}, query: {} }), 'matches');
  assert.equal(mod.viewKey({ name: 'results', params: { stem: 'x' }, query: {} }), 'results');
  assert.equal(typeof mod.start, 'function');
});

test('rail: activeNav maps every route to a destination', async () => {
  const { activeNav } = await import('../../static/kestrel/shell/rail.js');
  const r = (name, stem) => ({ name, params: stem ? { stem } : {}, query: {} });
  assert.equal(activeNav(r('overview')), 'overview');
  assert.equal(activeNav(r('matches', 'x')), 'matches');
  assert.equal(activeNav(r('players')), 'players');
  assert.equal(activeNav(r('pipeline')), 'pipeline');
  assert.equal(activeNav(r('library')), 'library');
  assert.equal(activeNav(r('new')), 'library');
  assert.equal(activeNav(r('results', 'x')), 'library');
  assert.equal(activeNav(null), null);
});

test('rail: runningCount counts running and detached jobs only', async () => {
  const { runningCount } = await import('../../static/kestrel/shell/rail.js');
  assert.equal(runningCount([{ status: 'running' }, { status: 'detached' }, { status: 'completed' },
    { status: 'error' }, { status: 'queued' }, null]), 2);
  assert.equal(runningCount(undefined), 0);
  assert.equal(runningCount({ jobs: [{ status: 'running' }] }), 1);
});

test('transition: reduced motion and missing rect navigate instantly, no DOM needed', async () => {
  const { navigateWithTransform } = await import('../../static/kestrel/shell/transition.js');
  const went = [];
  const navigate = (h) => went.push(h);
  const anchor = { getBoundingClientRect: () => ({ left: 10, top: 10, width: 100, height: 40 }) };
  await navigateWithTransform(anchor, '#/matches/x', { reduced: true, navigate });
  await navigateWithTransform(null, '#/players', { reduced: false, navigate });
  await navigateWithTransform({ getBoundingClientRect: () => ({ left: 0, top: 0, width: 0, height: 0 }) }, '#/pipeline', { reduced: () => false, navigate });
  // no document under Node: falls back to an instant navigation instead of throwing
  await navigateWithTransform(anchor, '#/library', { reduced: false, navigate });
  assert.deepEqual(went, ['#/matches/x', '#/players', '#/pipeline', '#/library']);
});

test('ground: photo choice follows the route', async () => {
  const { photoFor } = await import('../../static/kestrel/shell/ground.js');
  assert.match(photoFor({ name: 'overview', params: {}, query: {} }), /court-aerial-night\.jpg$/);
  assert.match(photoFor({ name: 'matches', params: { stem: 'x' }, query: {} }), /court-racket-top\.jpg$/);
  assert.match(photoFor(null), /court-aerial-night\.jpg$/);
});

test('prefs: legacyTheme maps night/day to the legacy dark/light strings', async () => {
  const { legacyTheme } = await import('../../static/kestrel/core/prefs.js');
  assert.equal(legacyTheme('night'), 'dark');
  assert.equal(legacyTheme('dark'), 'dark');
  assert.equal(legacyTheme('day'), 'light');
  assert.equal(legacyTheme('light'), 'light');
  assert.equal(legacyTheme(undefined), 'dark');
});
