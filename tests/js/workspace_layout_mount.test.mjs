// The match workspace's movable cards mounted against the fake DOM: default order, keyboard moves, collapse, reset,
// a saved layout, drag with pointer events, and the video keeping its time and playing state across a move.
import test from 'node:test';
import assert from 'node:assert/strict';
import { installEnv, removeEnv, tick } from './fake_dom.mjs';
import { mount } from '../../static/kestrel/views/workspace.js';
import { api } from '../../static/kestrel/core/api.js';
import { PREF_KEYS } from '../../static/kestrel/core/prefs.js';
import { closeTool } from '../../static/kestrel/widgets/videotools.js';

const KEY = PREF_KEYS.wsLayout;
const out = (stem, f) => '/api/output/' + stem + '/' + f;

function routes(stem = 's') {
  return {
    [out(stem, 'rally_segments.json')]: { fps: 30, rallies: [{ id: 1, start_sec: 0, end_sec: 5 }], detection: { signal: 'courtview' } },
    [out(stem, 'strokes.json')]: {
      schema: 2, groups: { smash: 1 },
      strokes: [{ frame: 30, hitter: 'lower', stroke: 'smash', group: 'smash', uncertain: false, confidence: 0.9 }],
    },
    [out(stem, 'metadata.json')]: { video: { fps: 30 } },
    ['/api/insights/video/' + stem]: { rally: { status: 'ok', value: { count: 1, label: 'rallies', signal: 'courtview' } } },
    ['/api/matches/' + stem]: { upper: { name: 'Lee' }, lower: { name: 'Chung' } },
    '/api/videos': [{ name: stem, has_posture: false, has_match: true }],
  };
}

async function withPage(fn, { seed, before } = {}) {
  const { doc, storage } = installEnv();
  if (seed !== undefined) storage.set(KEY, typeof seed === 'string' ? seed : JSON.stringify(seed));
  if (before) before({ doc });
  const savedApi = { ...api };
  const r = routes();
  globalThis.fetch = async (url) => {
    const body = r[url];
    if (body === undefined) return { ok: false, status: 404, json: async () => ({}) };
    return { ok: true, status: 200, json: async () => body };
  };
  const container = doc.createElement('div');
  doc.body.appendChild(container);
  const inst = mount(container, { params: { stem: 's' }, query: {}, lang: 'en', reduced: true, router: { replace() {}, navigate() {} } });
  try {
    for (let i = 0; i < 10; i++) await tick();
    await fn({ container, doc, storage, inst });
  } finally {
    inst.destroy();
    closeTool();
    Object.assign(api, savedApi);
    delete globalThis.fetch;
    removeEnv();
  }
}

const zone = (c, z) => c.querySelector(z === 'main' ? '.ws-main' : '.ws-side');
const order = (c, z) => zone(c, z).children.filter((e) => e.getAttribute('data-card')).map((e) => e.getAttribute('data-card'));
const grip = (c, id) => c.querySelector('[data-card="' + id + '"] .ws-grip');
const saved = (storage) => JSON.parse(storage.get(KEY));
const key = (el, k) => el.dispatch('keydown', { key: k });

test('default render order is unchanged: video, timeline | movement, landing, inspector, summary', async () => {
  await withPage(async ({ container, storage }) => {
    assert.deepEqual(order(container, 'main'), ['video', 'timeline']);
    assert.deepEqual(order(container, 'side'), ['movement', 'landing', 'inspector', 'summary']);
    assert.ok(container.querySelector('.ws-main .ws-video-card'));
    assert.ok(container.querySelector('.ws-side .ws-sum'));
    assert.equal(storage.has(KEY), false, 'nothing saved until the owner moves something');
    const g = grip(container, 'movement');
    assert.equal(g.tagName, 'BUTTON');
    assert.equal(g.getAttribute('aria-label'), 'Move Movement');
    const tg = container.querySelector('[data-card="movement"] .ws-cf-toggle');
    assert.equal(tg.getAttribute('aria-expanded'), 'true');
  });
});

test('keyboard: ArrowUp moves a card up in its zone, the DOM follows, the pref is saved, focus stays, it is announced', async () => {
  await withPage(async ({ container, doc, storage }) => {
    const g = grip(container, 'landing');
    key(g, 'ArrowUp');
    assert.deepEqual(order(container, 'side'), ['landing', 'movement', 'inspector', 'summary']);
    assert.deepEqual(saved(storage).order.side, ['landing', 'movement', 'inspector', 'summary']);
    assert.equal(doc.activeElement, grip(container, 'landing'));
    const live = container.querySelector('.ws-sr');
    assert.match(live.textContent, /moved to the side column, position 1 of 4/);
    key(grip(container, 'landing'), 'ArrowUp');
    assert.deepEqual(order(container, 'side')[0], 'landing', 'top edge: no change');
  });
});

test('keyboard: ArrowLeft / ArrowRight move a card to the other zone', async () => {
  await withPage(async ({ container, storage }) => {
    key(grip(container, 'summary'), 'ArrowLeft');
    assert.deepEqual(order(container, 'main'), ['video', 'timeline', 'summary']);
    assert.deepEqual(order(container, 'side'), ['movement', 'landing', 'inspector']);
    assert.deepEqual(saved(storage).order.main, ['video', 'timeline', 'summary']);
    assert.ok(zone(container, 'main').querySelector('.ws-sum'), 'the summary card itself moved with its frame');
    key(grip(container, 'video'), 'ArrowRight');
    assert.deepEqual(order(container, 'side'), ['video', 'movement', 'landing', 'inspector']);
    assert.deepEqual(order(container, 'main'), ['timeline', 'summary']);
  });
});

test('collapse hides the body, keeps the header, sets aria-expanded and saves', async () => {
  await withPage(async ({ container, storage }) => {
    const frame = container.querySelector('[data-card="movement"]');
    const tg = frame.querySelector('.ws-cf-toggle');
    tg.click();
    assert.equal(tg.getAttribute('aria-expanded'), 'false');
    assert.equal(frame.querySelector('.ws-cf-body').hidden, true);
    assert.ok(frame.classList.contains('collapsed'));
    assert.equal(frame.querySelector('.ws-cf-title').textContent, 'Movement');
    assert.deepEqual(saved(storage).collapsed, ['movement']);
    tg.click();
    assert.equal(frame.querySelector('.ws-cf-body').hidden, false);
    assert.deepEqual(saved(storage).collapsed, []);
  });
});

test('Reset layout restores the default order and clears collapsed', async () => {
  await withPage(async ({ container, storage }) => {
    key(grip(container, 'summary'), 'ArrowLeft');
    container.querySelector('[data-card="landing"] .ws-cf-toggle').click();
    const reset = container.querySelector('.ws-head .ws-layout-reset');
    assert.equal(reset.textContent, 'Reset layout');
    reset.click();
    assert.deepEqual(order(container, 'main'), ['video', 'timeline']);
    assert.deepEqual(order(container, 'side'), ['movement', 'landing', 'inspector', 'summary']);
    assert.equal(container.querySelector('[data-card="landing"] .ws-cf-body').hidden, false);
    const left = storage.has(KEY) ? saved(storage) : { order: { main: ['video', 'timeline'] }, collapsed: [] };
    assert.deepEqual(left.collapsed, []);
    assert.deepEqual(left.order.main, ['video', 'timeline']);
  });
});

test('a saved layout is applied on mount; junk is normalised', async () => {
  await withPage(async ({ container }) => {
    assert.deepEqual(order(container, 'main'), ['summary', 'video']);
    assert.deepEqual(order(container, 'side'), ['timeline', 'movement', 'landing', 'inspector']);
    assert.equal(container.querySelector('[data-card="landing"] .ws-cf-body').hidden, true);
  }, { seed: { order: { main: ['summary', 'video', 'bogus'], side: ['timeline'] }, collapsed: ['landing'] } });
  await withPage(async ({ container }) => {
    assert.deepEqual(order(container, 'main'), ['video', 'timeline']);
  }, { seed: '{not json' });
});

// A fake browser: moving a node that holds the video resets playback, as a detach can.
function detachBreaksVideo(container) {
  const video = container.querySelector('video');
  let plays = 0;
  video.paused = false;
  video.currentTime = 42.5;
  video.play = () => { plays += 1; video.paused = false; };
  for (const z of [zone(container, 'main'), zone(container, 'side')]) {
    const orig = z.insertBefore.bind(z);
    z.insertBefore = (c, ref) => {
      if (c.contains(video)) { video.paused = true; video.currentTime = 0; }
      return orig(c, ref);
    };
  }
  return { video, plays: () => plays };
}

test('video: moving the video card keeps currentTime and resumes playback when it was playing', async () => {
  await withPage(async ({ container }) => {
    const { video, plays } = detachBreaksVideo(container);
    key(grip(container, 'video'), 'ArrowRight');
    assert.ok(zone(container, 'side').querySelector('video'));
    assert.equal(video.currentTime, 42.5);
    assert.equal(video.paused, false);
    assert.equal(plays(), 1);
  });
});

test('video: a paused video stays paused (time kept, no play call)', async () => {
  await withPage(async ({ container }) => {
    const { video, plays } = detachBreaksVideo(container);
    video.paused = true;
    key(grip(container, 'video'), 'ArrowRight');
    assert.equal(video.currentTime, 42.5);
    assert.equal(video.paused, true);
    assert.equal(plays(), 0);
  });
});

test('video: moving another card around the video never relocates the video node', async () => {
  await withPage(async ({ container }) => {
    const { video, plays } = detachBreaksVideo(container);
    key(grip(container, 'timeline'), 'ArrowUp');
    assert.deepEqual(order(container, 'main'), ['timeline', 'video']);
    assert.equal(video.currentTime, 42.5, 'untouched');
    assert.equal(video.paused, false);
    assert.equal(plays(), 0);
  });
});

test('drag: pointerdown on the grip, a drop marker follows the pointer, pointerup drops, Escape cancels', async () => {
  const listeners = {};
  const before = ({ doc }) => {
    doc.addEventListener = (type, fn) => { (listeners[type] ||= []).push(fn); };
    doc.removeEventListener = (type, fn) => { listeners[type] = (listeners[type] || []).filter((f) => f !== fn); };
  };
  const fire = (type, ev) => [...(listeners[type] || [])].forEach((f) => f({ preventDefault() {}, ...ev }));
  await withPage(async ({ container, storage }) => {
    // Rects: main column x 0-500, side x 600-900; frames stacked 100 px each.
    const rect = (l, t, w, h) => () => ({ left: l, top: t, right: l + w, bottom: t + h, width: w, height: h });
    zone(container, 'main').getBoundingClientRect = rect(0, 0, 500, 300);
    zone(container, 'side').getBoundingClientRect = rect(600, 0, 300, 400);
    order(container, 'main').forEach((id, i) => { container.querySelector('[data-card="' + id + '"]').getBoundingClientRect = rect(0, i * 100, 500, 100); });
    order(container, 'side').forEach((id, i) => { container.querySelector('[data-card="' + id + '"]').getBoundingClientRect = rect(600, i * 100, 300, 100); });

    grip(container, 'summary').dispatch('pointerdown', { button: 0, clientX: 650, clientY: 350, pointerId: 1 });
    assert.ok(container.querySelector('[data-card="summary"]').classList.contains('dragging'));
    fire('pointermove', { clientX: 100, clientY: 20 });
    const marker = container.querySelector('.ws-drop-marker');
    assert.ok(marker, 'drop marker');
    assert.equal(marker.parentNode, zone(container, 'main'));
    assert.equal(zone(container, 'main').children[0], marker, 'before the first card');
    fire('pointermove', { clientX: 100, clientY: 120 });
    assert.equal(zone(container, 'main').children.indexOf(container.querySelector('.ws-drop-marker')), 1, 'between video and timeline');
    fire('keydown', { key: 'Escape' });
    assert.equal(container.querySelector('.ws-drop-marker'), null);
    assert.deepEqual(order(container, 'side'), ['movement', 'landing', 'inspector', 'summary']);
    assert.equal(storage.has(KEY), false, 'cancelled: nothing saved');
    assert.equal((listeners.pointermove || []).length, 0, 'listeners removed');

    grip(container, 'summary').dispatch('pointerdown', { button: 0, clientX: 650, clientY: 350, pointerId: 1 });
    fire('pointermove', { clientX: 100, clientY: 120 });
    fire('pointerup', { clientX: 100, clientY: 120 });
    assert.deepEqual(order(container, 'main'), ['video', 'summary', 'timeline']);
    assert.deepEqual(saved(storage).order.main, ['video', 'summary', 'timeline']);
    assert.equal(container.querySelector('.ws-drop-marker'), null);
  }, { before });
});
