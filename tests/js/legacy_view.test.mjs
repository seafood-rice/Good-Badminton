// views/legacy.js against a fake legacy module and a minimal fake DOM: ownership of the legacy module's
// single render target / navigation callback, and deep links that cannot be verified.
import test from 'node:test';
import assert from 'node:assert/strict';

function fakeNode(tag) {
  const classes = new Set();
  const node = {
    tagName: String(tag).toUpperCase(),
    id: '',
    className: '',
    textContent: '',
    type: '',
    href: '',
    children: [],
    parent: null,
    rooted: false, // a top-level container "in the document"
    listeners: {},
    get isConnected() { return this.parent ? this.parent.isConnected : this.rooted; },
    classList: { add: (c) => classes.add(c), remove: (c) => classes.delete(c), contains: (c) => classes.has(c) },
    appendChild(c) { c.parent = this; this.children.push(c); return c; },
    append(...cs) { cs.forEach((c) => this.appendChild(c)); },
    replaceChildren(...cs) { this.children.forEach((c) => { c.parent = null; }); this.children = []; this.append(...cs); },
    remove() {
      if (this.parent) this.parent.children = this.parent.children.filter((x) => x !== this);
      this.parent = null;
      this.rooted = false;
    },
    addEventListener(type, fn) { this.listeners[type] = fn; },
  };
  return node;
}
globalThis.document = { createElement: fakeNode };

const { mount } = await import('../../static/kestrel/views/legacy.js');

function fakeLegacy() {
  return {
    state: { lang: 'en', theme: 'dark', resultsVideo: null, resultsMode: 'match', resultsModes: [] },
    mountEl: null,
    navCb: null,
    results: [],
    setTheme(v) { this.state.theme = v; },
    setLang(v) { this.state.lang = v; },
    onNavigate(cb) { this.navCb = typeof cb === 'function' ? cb : null; },
    mountLibrary(el) { this.mountEl = el; },
    mountNew(el) { this.mountEl = el; },
    mountResults(el, stem, mode, modes) {
      this.mountEl = el;
      this.state.resultsVideo = stem;
      this.state.resultsMode = mode;
      this.state.resultsModes = modes || [mode];
      this.results.push({ stem, mode, modes });
    },
    unmount() { this.mountEl = null; },
  };
}

function deferred() {
  let resolve;
  let reject;
  const p = new Promise((res, rej) => { resolve = res; reject = rej; });
  return { p, resolve, reject };
}

function container() {
  const c = fakeNode('div');
  c.rooted = true;
  return c;
}

const route = (name, stem, query = {}) => ({ name, params: stem ? { stem } : {}, query });

test('a stale mount resolved after a newer one does not clobber it', async () => {
  const L = fakeLegacy();
  const navs = [];
  const router = { navigate: (h) => navs.push(h) };
  const loadA = deferred();
  const cA = container();
  const pA = mount(cA, { route: route('library'), lang: 'en', theme: 'night', router, loadLegacy: () => loadA.p });
  cA.remove(); // the shell tears the page down before the module arrives

  const cB = container();
  const instB = await mount(cB, { route: route('new'), lang: 'en', theme: 'night', router, loadLegacy: () => Promise.resolve(L) });
  const hostB = cB.children[0];
  assert.equal(L.mountEl, hostB);

  loadA.resolve(L);
  const instA = await pA;
  instA.destroy();
  assert.equal(L.mountEl, hostB, "B's render target survives");
  assert.ok(L.navCb, "B's navigation callback survives");
  L.navCb({ name: 'library', params: {}, query: {} });
  assert.deepEqual(navs, ['#/library']);
  assert.equal(cA.children.length, 0, 'the stale mount rendered nothing');

  instB.destroy();
  assert.equal(L.mountEl, null);
  assert.equal(L.navCb, null);
});

test('a deep link still verifying when a newer mount starts never takes over or clears it', async () => {
  const L = fakeLegacy();
  const vids = deferred();
  const api = { videos: () => vids.p };
  const cA = container();
  const pA = mount(cA, { route: route('results', 'stemX', { mode: 'match' }), lang: 'en', theme: 'night', api, loadLegacy: async () => L });
  await new Promise((r) => setTimeout(r, 0)); // A owns the module and waits on /api/videos

  const cB = container();
  const instB = await mount(cB, { route: route('library'), lang: 'en', theme: 'night', loadLegacy: async () => L });
  const hostB = cB.children[0];
  assert.equal(L.mountEl, hostB);

  vids.resolve([{ name: 'stemX', has_match: true }]);
  const instA = await pA;
  assert.deepEqual(L.results, [], 'A never mounted its results');
  instA.destroy();
  assert.equal(L.mountEl, hostB);
  assert.ok(L.navCb);
  instB.destroy();
});

test('an unverifiable deep link shows a retry panel instead of mounting the stem', async () => {
  const L = fakeLegacy();
  let calls = 0;
  const api = { videos: () => { calls += 1; return Promise.reject(new Error('offline')); } };
  const c = container();
  const inst = await mount(c, { route: route('results', '../../etc'), lang: 'en', theme: 'night', api, loadLegacy: async () => L });
  assert.deepEqual(L.results, []);
  const host = c.children[0];
  const retry = host.children.find((n) => n.tagName === 'BUTTON');
  assert.ok(retry, 'retry button');
  assert.ok(host.children.some((n) => n.tagName === 'A' && n.href === '#/library'));
  retry.listeners.click();
  await new Promise((r) => setTimeout(r, 0));
  assert.equal(calls, 2);
  assert.deepEqual(L.results, []);
  inst.destroy();
});

test('a verified deep link mounts with the modes from the library; an unknown stem does not', async () => {
  const L = fakeLegacy();
  const api = { videos: async () => [{ name: 'clip', has_match: true, has_posture: true }] };
  const c = container();
  const inst = await mount(c, { route: route('results', 'clip', { mode: 'posture' }), lang: 'en', theme: 'night', api, loadLegacy: async () => L });
  assert.deepEqual(L.results, [{ stem: 'clip', mode: 'posture', modes: ['match', 'posture'] }]);
  inst.destroy();

  const L2 = fakeLegacy();
  const c2 = container();
  const inst2 = await mount(c2, { route: route('results', 'nope'), lang: 'en', theme: 'night', api, loadLegacy: async () => L2 });
  assert.deepEqual(L2.results, []);
  assert.ok(!c2.children[0].children.some((n) => n.tagName === 'BUTTON'), 'no retry for a stem the library does not have');
  inst2.destroy();
});
