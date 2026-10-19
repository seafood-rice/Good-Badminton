// mount() of the Overview against a tiny fake DOM and a fake api (no jsdom: no new dependency).
// Covers request ordering, cleanup on destroy, refresh failure, fetch de-duplication and same-scope refresh.
import test from 'node:test';
import assert from 'node:assert/strict';
import { mount, OVERVIEW_TIMEOUT_MS } from '../../static/kestrel/views/overview.js';
import { createStore } from '../../static/kestrel/core/store.js';

// ---------- fake DOM ----------
class FakeText {
  constructor(v) { this.nodeValue = String(v); this.parentNode = null; }
  get textContent() { return this.nodeValue; }
}

function parseSelector(sel) {
  return sel.split(',').map((part) => {
    const m = /^\s*\[([\w-]+)(?:="([^"]*)")?\]\s*$/.exec(part);
    if (!m) throw new Error('fake DOM: unsupported selector ' + part);
    return (el) => el.attrs && el.attrs.has(m[1]) && (m[2] === undefined || el.attrs.get(m[1]) === m[2]);
  });
}

class FakeEl {
  constructor(tag) {
    this.tagName = String(tag).toUpperCase();
    this.childNodes = [];
    this.parentNode = null;
    this.attrs = new Map();
    this.listeners = {};
    this.disabled = false;
    this.style = { setProperty(k, v) { this[k] = String(v); }, getPropertyValue(k) { return this[k] || ''; } };
    const self = this;
    this.classList = {
      toggle(c, on) {
        const set = new Set((self.getAttribute('class') || '').split(/\s+/).filter(Boolean));
        if (on) set.add(c); else set.delete(c);
        self.attrs.set('class', [...set].join(' '));
      },
      contains: (c) => (self.getAttribute('class') || '').split(/\s+/).includes(c),
    };
  }
  get firstChild() { return this.childNodes[0] || null; }
  appendChild(c) {
    if (c.parentNode) c.parentNode.removeChild(c);
    c.parentNode = this;
    this.childNodes.push(c);
    return c;
  }
  removeChild(c) {
    const i = this.childNodes.indexOf(c);
    if (i >= 0) this.childNodes.splice(i, 1);
    c.parentNode = null;
    return c;
  }
  replaceChild(n, o) {
    if (n.parentNode) n.parentNode.removeChild(n);
    const i = this.childNodes.indexOf(o);
    this.childNodes[i] = n;
    n.parentNode = this;
    o.parentNode = null;
    return o;
  }
  setAttribute(k, v) { this.attrs.set(k, String(v)); if (k === 'disabled') this.disabled = true; }
  getAttribute(k) { return this.attrs.has(k) ? this.attrs.get(k) : null; }
  hasAttribute(k) { return this.attrs.has(k); }
  addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); }
  removeEventListener(type, fn) { this.listeners[type] = (this.listeners[type] || []).filter((f) => f !== fn); }
  fire(type, extra = {}) {
    const ev = { type, target: this, preventDefault() {}, stopPropagation() {}, ...extra };
    for (const fn of [...(this.listeners[type] || [])]) fn(ev);
  }
  set textContent(v) {
    for (const c of this.childNodes) c.parentNode = null;
    this.childNodes = v === '' || v === null || v === undefined ? [] : [new FakeText(v)];
  }
  get textContent() { return this.childNodes.map((c) => c.textContent).join(''); }
  focus() { env.doc.activeElement = this; }
  contains(n) { for (let x = n; x; x = x.parentNode) if (x === this) return true; return false; }
  querySelectorAll(sel) {
    const tests = parseSelector(sel);
    const out = [];
    const walk = (n) => {
      for (const c of n.childNodes || []) {
        if (c instanceof FakeEl) {
          if (tests.some((f) => f(c))) out.push(c);
          walk(c);
        }
      }
    };
    walk(this);
    return out;
  }
  querySelector(sel) { return this.querySelectorAll(sel)[0] || null; }
  getBoundingClientRect() { return { left: 0, top: 0, width: 640, height: 236 }; }
  scrollIntoView() {}
  closest() { return null; }
}

function all(node, pred, out = []) {
  for (const c of node.childNodes || []) {
    if (c instanceof FakeEl) {
      if (pred(c)) out.push(c);
      all(c, pred, out);
    }
  }
  return out;
}
const byClass = (node, cls) => all(node, (n) => n.classList.contains(cls));
const byAttr = (node, k, v) => all(node, (n) => n.getAttribute(k) === v)[0] || null;

// ---------- fake environment with leak counters ----------
const env = { doc: null };
const REAL = { setTimeout, clearTimeout, setInterval, clearInterval };

function installEnv() {
  const live = { timeouts: new Set(), intervals: new Set(), rafs: new Set(), winListeners: 0 };
  env.doc = { activeElement: null, createElement: (t) => new FakeEl(t), createElementNS: (ns, t) => new FakeEl(t), createTextNode: (v) => new FakeText(v) };
  globalThis.document = env.doc;
  globalThis.window = {
    addEventListener() { live.winListeners += 1; },
    removeEventListener() { live.winListeners -= 1; },
  };
  globalThis.setTimeout = (fn, ms, ...args) => {
    const id = REAL.setTimeout(() => { live.timeouts.delete(id); fn(...args); }, ms);
    live.timeouts.add(id);
    return id;
  };
  globalThis.clearTimeout = (id) => { live.timeouts.delete(id); REAL.clearTimeout(id); };
  globalThis.setInterval = (fn, ms) => { const id = REAL.setInterval(fn, ms); live.intervals.add(id); return id; };
  globalThis.clearInterval = (id) => { live.intervals.delete(id); REAL.clearInterval(id); };
  globalThis.requestAnimationFrame = (fn) => {
    const id = REAL.setTimeout(() => { live.rafs.delete(id); fn(Date.now()); }, 16);
    live.rafs.add(id);
    return id;
  };
  globalThis.cancelAnimationFrame = (id) => { live.rafs.delete(id); REAL.clearTimeout(id); };
  return {
    live,
    restore() {
      for (const id of live.timeouts) REAL.clearTimeout(id);
      for (const id of live.intervals) REAL.clearInterval(id);
      for (const id of live.rafs) REAL.clearTimeout(id);
      Object.assign(globalThis, REAL);
      delete globalThis.document;
      delete globalThis.window;
      delete globalThis.requestAnimationFrame;
      delete globalThis.cancelAnimationFrame;
    },
  };
}

const tick = (ms = 0) => new Promise((r) => REAL.setTimeout(r, ms));

// ---------- fake api and payloads ----------
function fakeApi({ jobs = [] } = {}) {
  const calls = [];
  const jobCalls = [];
  return {
    calls,
    jobCalls,
    overview(q, opts) {
      let resolve;
      let reject;
      const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
      calls.push({ q, opts, resolve, reject });
      return promise;
    },
    jobs(opts) {
      jobCalls.push(opts);
      return Promise.resolve(jobs);
    },
  };
}

const ok = (value, coverage = null) => ({ status: 'ok', value, coverage, reason: null });
function payload(name, range = '30d') {
  return {
    scope: { athlete: { id: 'a1', name }, squad: null, range, stems: 2, units: 2 },
    kpis: {
      technique_index: { status: 'partial', value: { value: 74.6, delta: 1, scored: 2 }, coverage: { n: 2, of: 7 }, reason: { code: 'unscored_technique', params: { names: ['net'] } } },
      match_win_rate: ok({ wins: 1, played: 2, rate: 0.5 }),
      avg_rally: ok({ seconds: 8, shots: 7, signal: 'shuttle' }),
      rally_win_rate: { status: 'unavailable', value: null, coverage: null, reason: { code: 'no_point_outcome', params: {} } },
    },
    radar: { reference: { lo: 70, hi: 100 }, axes: [{ id: 'smash', count: 3, score: 82 }, { id: 'drop', count: 4, score: 60 }] },
    trend: { points: [{ date: '2026-10-01', stem: 'a', index: 72 }, { date: '2026-10-09', stem: 'b', index: 74.6 }], benchmark: null },
    movement: {},
    matches: [{ stem: 'm1', date: '2026-10-09', opponent: 'X', result: 'W', tech_index: 74.6, status: 'analysed' }],
    findings: [{ code: 'technique_below_reference', severity: 'watch', params: { technique: 'drop', score: 60, lo: 70 } }],
  };
}
const title = (container) => {
  const h1 = all(container, (n) => n.tagName === 'H1')[0];
  return h1 ? h1.textContent : '';
};

function countingStore(initial) {
  const store = createStore(initial);
  const real = store.subscribe;
  let subs = 0;
  store.subscribe = (fn) => {
    subs += 1;
    const un = real(fn);
    return () => { subs -= 1; un(); };
  };
  store.subs = () => subs;
  return store;
}

// ---------- tests ----------
test('the overview fetch uses the long timeout and an abort signal', async () => {
  const fake = installEnv();
  try {
    const api = fakeApi();
    const view = mount(new FakeEl('main'), { query: {}, api, reduced: true });
    assert.equal(OVERVIEW_TIMEOUT_MS, 60000);
    assert.equal(api.calls.length, 1);
    assert.equal(api.calls[0].opts.timeout, 60000);
    assert.ok(api.calls[0].opts.signal);
    view.destroy();
  } finally {
    fake.restore();
  }
});

test('responses resolved in reverse order: the older one never overwrites the newer view', async () => {
  const fake = installEnv();
  try {
    const api = fakeApi();
    const container = new FakeEl('main');
    const view = mount(container, { query: { range: '30d' }, api, reduced: true });
    view.setQuery({ range: '7d' });
    assert.equal(api.calls.length, 2);
    assert.equal(api.calls[1].q.range, '7d');
    api.calls[1].resolve(payload('Newer', '7d'));
    await tick();
    api.calls[0].resolve(payload('Older'));
    await tick();
    assert.match(title(container), /Newer/);
    assert.doesNotMatch(title(container), /Older/);
    view.destroy();
  } finally {
    fake.restore();
  }
});

test('destroy() leaves no timers, intervals, frames, listeners or subscriptions', async () => {
  for (const withStore of [false, true]) {
    const fake = installEnv();
    try {
      const api = fakeApi();
      const store = withStore ? countingStore({ lang: 'en', jobs: [] }) : null;
      const container = new FakeEl('main');
      const view = mount(container, { query: {}, api, store, reduced: false });
      api.calls[0].resolve(payload('Tanaka'));
      await tick(40); // the reveal clock is mid-run, the sync interval and (without a store) the jobs poll are armed
      assert.ok(fake.live.rafs.size > 0, 'clock running before destroy');
      assert.ok(fake.live.intervals.size > 0, 'sync interval armed');
      view.destroy();
      assert.equal(fake.live.timeouts.size, 0, `timeouts (store: ${withStore})`);
      assert.equal(fake.live.intervals.size, 0, 'intervals');
      assert.equal(fake.live.rafs.size, 0, 'animation frames');
      assert.equal(fake.live.winListeners, 0, 'window listeners');
      if (store) assert.equal(store.subs(), 0, 'store subscriptions');
      assert.equal(container.childNodes.length, 0);
    } finally {
      fake.restore();
    }
  }
});

test('a failed refresh keeps the last good data and shows the retry alert', async () => {
  const fake = installEnv();
  try {
    const api = fakeApi();
    const container = new FakeEl('main');
    const view = mount(container, { query: { range: '30d' }, api, reduced: true, store: countingStore({ lang: 'en', jobs: [] }) });
    api.calls[0].resolve(payload('Tanaka'));
    await tick();
    const tilesBefore = all(container, (n) => n.hasAttribute('data-tile')).length;
    assert.equal(tilesBefore, 4);
    view.setQuery({ range: '7d' });
    api.calls[1].reject({ kind: 'network', status: 0, messages: { zh: '无法连接到服务', en: 'Cannot reach the server' } });
    await tick();
    assert.match(title(container), /Tanaka/);
    assert.equal(all(container, (n) => n.hasAttribute('data-tile')).length, 4);
    const alert = byAttr(container, 'role', 'alert');
    assert.ok(alert, 'retry alert shown');
    const retry = all(alert, (n) => n.tagName === 'BUTTON')[0];
    assert.equal(retry.textContent, 'Retry');
    retry.fire('click');
    assert.equal(api.calls.length, 3);
    assert.equal(api.calls[2].opts.timeout, 60000);
    view.destroy();
  } finally {
    fake.restore();
  }
});

test('one scope change (store patch + setQuery, as main.js does) issues one fetch', async () => {
  const fake = installEnv();
  try {
    const api = fakeApi();
    const store = countingStore({ lang: 'en', jobs: [], athlete: '', squad: '', range: '30d' });
    const view = mount(new FakeEl('main'), { query: { range: '30d' }, api, store, router: { navigate() {} }, reduced: true });
    assert.equal(api.calls.length, 1);
    store.set({ range: '7d' });
    view.setQuery({ range: '7d' });
    assert.equal(api.calls.length, 2);
    // the other order
    view.setQuery({ range: 'season' });
    store.set({ range: 'season' });
    assert.equal(api.calls.length, 3);
    // a lens-only change never fetches
    view.setQuery({ range: 'season', lens: 'tactics' });
    assert.equal(api.calls.length, 3);
    // with a router the shell owns hashchange: the view adds no window listener
    assert.equal(fake.live.winListeners, 0);
    view.destroy();
  } finally {
    fake.restore();
  }
});

test('a finished job (from store.jobs) refetches with the long timeout and keeps the pinned axis and focus', async () => {
  const fake = installEnv();
  try {
    const api = fakeApi();
    const store = countingStore({ lang: 'en', jobs: [{ id: 'j1', status: 'running', progress: 10 }] });
    const container = new FakeEl('main');
    const view = mount(container, { query: {}, api, store, reduced: true });
    api.calls[0].resolve(payload('Tanaka'));
    await tick();
    assert.equal(api.jobCalls.length, 0, 'the shell polls jobs; the view does not');
    const chip = byAttr(container, 'data-fk', 'axis:drop');
    chip.fire('click');
    chip.focus();
    assert.equal(chip.getAttribute('aria-pressed'), 'true');
    store.set({ jobs: [{ id: 'j1', status: 'completed', progress: 100 }] });
    assert.equal(api.calls.length, 2);
    assert.equal(api.calls[1].opts.timeout, 60000);
    api.calls[1].resolve(payload('Tanaka'));
    await tick();
    const chip2 = byAttr(container, 'data-fk', 'axis:drop');
    assert.notEqual(chip2, chip, 'the view re-rendered');
    assert.equal(chip2.getAttribute('aria-pressed'), 'true', 'pinned axis kept');
    assert.equal(env.doc.activeElement, chip2, 'focus restored');
    assert.match(byClass(container, 'kw-readout')[0].textContent, /Drop 60/);
    view.destroy();
  } finally {
    fake.restore();
  }
});

test('a partial tile shows its reason as visible text', async () => {
  const fake = installEnv();
  try {
    const api = fakeApi();
    const container = new FakeEl('main');
    const view = mount(container, { query: {}, api, reduced: true, store: countingStore({ lang: 'en', jobs: [] }) });
    api.calls[0].resolve(payload('Tanaka'));
    await tick();
    const tile = byAttr(container, 'data-tile', 'technique_index');
    const reason = byClass(tile, 'kw-tile-reason')[0];
    assert.ok(reason, 'reason element');
    assert.match(reason.textContent, /no reference range yet: Net/);
    const opened = [];
    view.destroy();
    // onOpenMatch receives the stem and the activating element (main.js uses it for the container transform).
    const container2 = new FakeEl('main');
    const view2 = mount(container2, { query: {}, api, reduced: true, store: countingStore({ lang: 'en', jobs: [] }), onOpenMatch: (stem, el) => opened.push([stem, el && el.tagName]) });
    api.calls[1].resolve(payload('Tanaka'));
    await tick();
    byClass(container2, 'kw-row-open')[0].fire('click');
    assert.deepEqual(opened, [['m1', 'BUTTON']]);
    view2.destroy();
  } finally {
    fake.restore();
  }
});
