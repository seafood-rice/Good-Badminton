// Split-match linking (spec 2026-10-21, sections 8 and 10): the pure helpers of views/matches.js (parts sanitising,
// link facts, eligible primaries / parts) and mount() of the Matches view against a tiny fake DOM and a patched api
// (no jsdom: no new dependency): the "N parts" chip, the chevron and its kept expansion state, the link / unlink calls.
import test from 'node:test';
import assert from 'node:assert/strict';
import {
  matchRows, cleanParts, partStatus, linkInfo, linkBody, rowHasRecord, isBlankRecord, linkLabel, eligibleParts, eligiblePrimaries, mount,
} from '../../static/kestrel/views/matches.js';
import { api, ApiError } from '../../static/kestrel/core/api.js';
import { STRINGS, t } from '../../static/kestrel/core/i18n.js';

// ---------- pure helpers ----------

const P1 = { stem: 'm1', side: 'lower', status: 'analysed', tech_index: 71.5, needs_review: false };
const P2 = { stem: 'm2', side: 'upper', status: 'new', tech_index: null, needs_review: true };

test('matchRows: an unlinked row has parts [] and no record date flag', () => {
  const [row] = matchRows({ matches: [{ stem: 'a', status: 'analysed' }] });
  assert.deepEqual(row.parts, []);
  assert.equal(row.recordDate, false);
});

test('matchRows: parts pass through sanitised; non-arrays and doubles rows give []', () => {
  const [row] = matchRows({ matches: [{ stem: 'm1', date_source: 'record', parts: [P1, P2] }] });
  assert.deepEqual(row.parts, [P1, P2]);
  assert.equal(row.recordDate, true);
  for (const junk of [null, undefined, 'x', 3, {}, { 0: P1 }]) {
    assert.deepEqual(matchRows({ matches: [{ stem: 's', parts: junk }] })[0].parts, [], String(junk));
  }
  // a doubles row never carries parts (no link UI on doubles)
  assert.deepEqual(matchRows({ matches: [{ stem: 'd', doubles: true, parts: [P1, P2] }] })[0].parts, []);
});

test('cleanParts: drops bad entries and duplicate stems, defaults bad values', () => {
  const out = cleanParts([
    null, 4, 'x', {}, { stem: '' }, { stem: 7 },
    { stem: 'p', side: 'left', status: 'weird', tech_index: 'high', needs_review: 'yes' },
    { stem: 'p', side: 'upper' },
    { stem: 'q', side: 'upper', status: 'processing', tech_index: NaN },
    { stem: 'r', tech_index: 64, needs_review: true },
  ]);
  assert.deepEqual(out, [
    { stem: 'p', side: null, status: 'analysed', tech_index: null, needs_review: false },
    { stem: 'q', side: 'upper', status: 'processing', tech_index: null, needs_review: false },
    { stem: 'r', side: null, status: 'analysed', tech_index: 64, needs_review: true },
  ]);
});

test('partStatus: needs_review wins, an unknown status reads as analysed', () => {
  assert.equal(partStatus(P2), 'needs_review');
  assert.equal(partStatus({ status: 'processing' }), 'processing');
  assert.equal(partStatus({ status: 'new', needs_review: false }), 'new');
  assert.equal(partStatus({ status: 'bogus' }), 'analysed');
  assert.equal(partStatus(null), 'analysed');
});

test('linkInfo: a part reports part_of and the swap flag; a primary lists its parts; a plain record neither', () => {
  assert.deepEqual(linkInfo({ date: '2026-10-01', upper: { name: 'A' } }), { partOf: null, sidesSwapped: false, parts: [] });
  assert.deepEqual(linkInfo({ part_of: 'm1', sides_swapped: true, upper: { name: 'A' } }), { partOf: 'm1', sidesSwapped: true, parts: [] });
  assert.deepEqual(linkInfo({ part_of: 'm1', sides_swapped: 'true' }), { partOf: 'm1', sidesSwapped: false, parts: [] });
  assert.deepEqual(
    linkInfo({ date: 'd', parts: [{ stem: 'm2', sides_swapped: true }, { stem: 'm3' }, { stem: 'm2' }, { nostem: 1 }, null] }),
    { partOf: null, sidesSwapped: false, parts: [{ stem: 'm2', sides_swapped: true }, { stem: 'm3', sides_swapped: false }] },
  );
  // a part never reports parts, whatever the body holds
  assert.deepEqual(linkInfo({ part_of: 'm1', parts: [{ stem: 'z' }] }).parts, []);
  assert.deepEqual(linkInfo(null), { partOf: null, sidesSwapped: false, parts: [] });
  assert.equal(linkInfo({ part_of: '  ' }).partOf, null);
});

test('linkBody: the PUT body, an exact bool for the swap flag, null for a blank primary', () => {
  assert.deepEqual(linkBody('m1', true), { part_of: 'm1', sides_swapped: true });
  assert.deepEqual(linkBody(' m1 ', false), { part_of: 'm1', sides_swapped: false });
  assert.deepEqual(linkBody('m1', 'yes'), { part_of: 'm1', sides_swapped: false });
  assert.deepEqual(linkBody('m1'), { part_of: 'm1', sides_swapped: false });
  assert.equal(linkBody(''), null);
  assert.equal(linkBody(null), null);
});

const ROWS = matchRows({
  matches: [
    // a recorded single, already a primary with one part
    { stem: 'prim', status: 'analysed', date: '2026-10-01', date_source: 'record', athlete: 'Axelsen', opponent: 'Sindhu', parts: [P1, P2] },
    // a recorded single (named, result entered)
    { stem: 'rec', status: 'analysed', athlete: 'Lee', opponent: 'Chen', result: 'W' },
    // unrecorded singles: a plain analysed one, one needing review, one not analysed yet, a doubles run
    { stem: 'bare', status: 'analysed', date: '2026-10-02', date_source: 'mtime' },
    { stem: 'review', status: 'analysed', needs_review: true },
    { stem: 'busy', status: 'processing' },
    { stem: 'dbl', status: 'analysed', doubles: true, athlete: 'X', opponent: 'Y' },
  ],
});

test('rowHasRecord: a record date, a name or a result; not a file-mtime date', () => {
  const by = Object.fromEntries(ROWS.map((r) => [r.stem, r]));
  assert.equal(rowHasRecord(by.prim), true);
  assert.equal(rowHasRecord(by.rec), true);
  assert.equal(rowHasRecord(by.bare), false);
  assert.equal(rowHasRecord({ recordDate: true }), true);
  assert.equal(rowHasRecord({ result: 'L' }), true);
  assert.equal(rowHasRecord(null), false);
});

test('linkLabel: names and the video when the row has names, else just the video', () => {
  const by = Object.fromEntries(ROWS.map((r) => [r.stem, r]));
  assert.equal(linkLabel(by.bare, 'en'), 'bare');
  assert.equal(linkLabel(by.rec, 'en'), 'Lee vs. Chen · rec');
});

test('eligibleParts: other analysed singles without parts; record holders are flagged, not hidden', () => {
  const got = eligibleParts(ROWS, 'prim', 'en');
  assert.deepEqual(got.map((c) => [c.stem, c.hasRecord]), [['rec', true], ['bare', false], ['review', false]]);
  // never itself, never a primary that already has parts, never a doubles run or an unfinished video
  assert.ok(!got.some((c) => ['prim', 'dbl', 'busy'].includes(c.stem)));
  assert.ok(!eligibleParts(ROWS, 'bare', 'en').some((c) => c.stem === 'bare' || c.stem === 'prim'));
  assert.deepEqual(eligibleParts(null, 'x', 'en'), []);
  assert.deepEqual(eligibleParts([null, { stem: 5 }], 'x', 'en'), []);
});

test('eligiblePrimaries: other singles videos holding a record (a primary with parts is fine, a part is never a row)', () => {
  const got = eligiblePrimaries(ROWS, 'bare', 'en');
  assert.deepEqual(got.map((c) => [c.stem, c.parts]), [['prim', 2], ['rec', 0]]);
  assert.equal(got[1].label, 'Lee vs. Chen · rec');
  assert.ok(!eligiblePrimaries(ROWS, 'rec', 'en').some((c) => c.stem === 'rec'));
  assert.ok(!got.some((c) => c.stem === 'dbl'));
  assert.deepEqual(eligiblePrimaries(undefined, 'x', 'en'), []);
});

test('every link string exists in zh and en', () => {
  const keys = Object.keys(STRINGS.en).filter((k) => k.startsWith('link.'));
  assert.ok(keys.length >= 25);
  for (const k of keys) assert.ok(STRINGS.zh[k], `zh.${k}`);
  assert.equal(t('link.parts_chip', 'en', { n: 3 }), '3 parts');
  assert.equal(t('link.part_label', 'en', { n: 2 }), 'Part 2');
  assert.match(t('link.parts_chip', 'zh', { n: 3 }), /3/);
});

// ---------- fake DOM (class / tag / attribute selectors, descendant chains) ----------

const env = { doc: null };

class FakeText {
  constructor(v) { this.nodeValue = String(v); this.parentNode = null; }
  get textContent() { return this.nodeValue; }
}

function simple(tok) {
  const checks = [];
  const re = /([.#]?[\w-]+)|\[([\w-]+)(?:="([^"]*)")?\]/g;
  let hit;
  while ((hit = re.exec(tok))) {
    const m = hit;
    if (m[1] && m[1][0] === '.') checks.push((el) => el.classList.contains(m[1].slice(1)));
    else if (m[1] && m[1][0] === '#') checks.push((el) => el.getAttribute('id') === m[1].slice(1));
    else if (m[1]) checks.push((el) => el.tagName === m[1].toUpperCase());
    else checks.push((el) => el.hasAttribute(m[2]) && (m[3] === undefined || el.getAttribute(m[2]) === m[3]));
  }
  return (el) => checks.every((c) => c(el));
}

function parseSelector(sel) {
  return sel.split(',').map((part) => part.trim().split(/\s+/).map(simple));
}

class FakeEl {
  constructor(tag) {
    this.tagName = String(tag).toUpperCase();
    this.childNodes = [];
    this.parentNode = null;
    this.attrs = new Map();
    this.listeners = {};
    this.disabled = false;
    this.value = '';
    this.checked = false;
    this.isConnected = true;
    this.style = { setProperty(k, v) { this[k] = String(v); }, getPropertyValue(k) { return this[k] || ''; } };
    const self = this;
    this.classList = {
      toggle(c, on) {
        const set = new Set((self.getAttribute('class') || '').split(/\s+/).filter(Boolean));
        if (on) set.add(c); else set.delete(c);
        self.attrs.set('class', [...set].join(' '));
      },
      contains: (c) => (self.getAttribute('class') || '').split(/\s+/).includes(c),
      add: (c) => self.classList.toggle(c, true),
      remove: (c) => self.classList.toggle(c, false),
    };
  }
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
  setAttribute(k, v) {
    this.attrs.set(k, String(v));
    if (k === 'disabled') this.disabled = true;
    if (k === 'value') this.value = String(v);
  }
  getAttribute(k) { return this.attrs.has(k) ? this.attrs.get(k) : null; }
  hasAttribute(k) { return this.attrs.has(k); }
  removeAttribute(k) { this.attrs.delete(k); }
  addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); }
  removeEventListener(type, fn) { this.listeners[type] = (this.listeners[type] || []).filter((f) => f !== fn); }
  click() {
    if (this.disabled) return;
    const ev = { type: 'click', target: this, button: 0, preventDefault() {}, stopPropagation() {} };
    for (const fn of [...(this.listeners.click || [])]) fn(ev);
  }
  set textContent(v) {
    for (const c of this.childNodes) c.parentNode = null;
    this.childNodes = v === '' || v === null || v === undefined ? [] : [new FakeText(v)];
  }
  get textContent() { return this.childNodes.map((c) => c.textContent).join(''); }
  focus() { env.doc.activeElement = this; }
  contains(n) { for (let x = n; x; x = x.parentNode) if (x === this) return true; return false; }
  querySelectorAll(sel) {
    const chains = parseSelector(sel);
    const out = [];
    const matches = (el, chain) => {
      if (!chain[chain.length - 1](el)) return false;
      let i = chain.length - 2;
      for (let a = el.parentNode; a && i >= 0; a = a.parentNode) if (a instanceof FakeEl && chain[i](a)) i -= 1;
      return i < 0;
    };
    const walk = (n) => {
      for (const c of n.childNodes || []) {
        if (c instanceof FakeEl) {
          if (chains.some((ch) => matches(c, ch))) out.push(c);
          walk(c);
        }
      }
    };
    walk(this);
    return out;
  }
  querySelector(sel) { return this.querySelectorAll(sel)[0] || null; }
  closest() { return null; }
}

const REAL = { setTimeout, clearTimeout };
function installEnv() {
  env.doc = { activeElement: null, createElement: (tag) => new FakeEl(tag), createElementNS: (ns, tag) => new FakeEl(tag), createTextNode: (v) => new FakeText(v) };
  globalThis.document = env.doc;
  globalThis.requestAnimationFrame = (fn) => REAL.setTimeout(() => fn(Date.now()), 16);
  globalThis.cancelAnimationFrame = (id) => REAL.clearTimeout(id);
}
function removeEnv() {
  delete globalThis.document;
  delete globalThis.requestAnimationFrame;
  delete globalThis.cancelAnimationFrame;
}
const tick = (ms = 0) => new Promise((r) => REAL.setTimeout(r, ms));
function everything(node, out = []) {
  for (const c of node.childNodes || []) {
    if (c instanceof FakeEl) { out.push(c); everything(c, out); }
  }
  return out;
}
const buttonWithText = (root, text) => everything(root).find((e) => e.tagName === 'BUTTON' && e.textContent === text) || null;

// ---------- mount() against a patched api ----------

const OV = (prim = [P1, P2]) => ({
  matches: [
    { stem: 'm1', date: '2026-10-01', date_source: 'record', side: 'lower', athlete: 'Axelsen', opponent: 'Sindhu', result: 'W', status: 'analysed', tech_index: 71.5, parts: prim },
    { stem: 'x', status: 'analysed', side: 'lower', tech_index: 60, parts: [] },
  ],
});

async function withView(fn, { overview = () => OV(), match = async () => ({}), saveMatch = async () => ({ ok: true }) } = {}) {
  installEnv();
  const saved = { overview: api.overview, match: api.match, saveMatch: api.saveMatch, videoInsights: api.videoInsights };
  const calls = { save: [], overview: 0, toasts: [] };
  api.overview = async () => { calls.overview += 1; return typeof overview === 'function' ? overview(calls.overview) : overview; };
  api.match = async (stem) => match(stem);
  api.saveMatch = async (stem, body) => { calls.save.push([stem, body]); return saveMatch(stem, body, calls); };
  const container = new FakeEl('div');
  const ctx = { lang: 'en', query: {}, toast: (m) => calls.toasts.push(m) };
  const inst = mount(container, ctx);
  try {
    await tick(5);
    await fn({ container, inst, calls, ctx });
  } finally {
    inst.destroy();
    Object.assign(api, saved);
    removeEnv();
  }
}

test('mount: a split match shows an "N parts" chip and a collapsed chevron; the chevron toggles sub-rows', async () => {
  await withView(async ({ container }) => {
    const chev = container.querySelector('[data-expand="m1"]');
    assert.ok(chev, 'chevron on the split row');
    assert.equal(chev.getAttribute('aria-expanded'), 'false');
    assert.equal(chev.tagName, 'BUTTON');
    assert.equal(container.querySelector('[data-expand="x"]'), null, 'an unlinked row has no chevron');
    assert.ok(everything(container).some((e) => e.classList.contains('k-chip') && e.textContent === '2 parts'));
    assert.equal(everything(container).filter((e) => e.classList.contains('mv-part-row')).length, 0);

    chev.click();
    const open = container.querySelector('[data-expand="m1"]');
    assert.equal(open.getAttribute('aria-expanded'), 'true');
    assert.equal(env.doc.activeElement, open, 'focus stays on the chevron after the re-render');
    const subs = everything(container).filter((e) => e.classList.contains('mv-part-row'));
    assert.deepEqual(subs.map((r) => r.getAttribute('data-part')), ['m1', 'm2']);
    assert.match(subs[0].textContent, /Part 1/);
    assert.match(subs[1].textContent, /Part 2/);
    assert.match(subs[1].textContent, /Needs review/i);
    assert.ok(buttonWithText(subs[1], 'Open'), 'Open button on a sub-row');
    assert.ok(subs[1].querySelector('[data-rec="m2"]'), 'part 2 has a Link button, part 1 does not');
    assert.equal(subs[0].querySelector('[data-rec="m1"]'), null);
  });
});

test('mount: the expansion state survives a reload of the list', async () => {
  await withView(async ({ container, inst, ctx }) => {
    container.querySelector('[data-expand="m1"]').click();
    assert.equal(everything(container).filter((e) => e.classList.contains('mv-part-row')).length, 2);
    inst.update({ ...ctx, query: { athlete: 'a1' } });      // a scope change reloads the list
    await tick(5);
    assert.equal(container.querySelector('[data-expand="m1"]').getAttribute('aria-expanded'), 'true');
    assert.equal(everything(container).filter((e) => e.classList.contains('mv-part-row')).length, 2);
    container.querySelector('[data-expand="m1"]').click();
    assert.equal(everything(container).filter((e) => e.classList.contains('mv-part-row')).length, 0);
  });
});

test('mount: a record-less video links itself to a match (PUT {part_of, sides_swapped}), toasts, expands the match', async () => {
  let linked = false;
  await withView(async ({ container, calls }) => {
    container.querySelector('[data-rec="x"]').click();
    await tick(5);
    const dropdown = everything(container).find((e) => e.tagName === 'SELECT' && /-m-sel$/.test(e.getAttribute('id') || ''));
    assert.ok(dropdown, 'the part-of dropdown');
    assert.equal(dropdown.disabled, false);
    const values = everything(dropdown).filter((o) => o.tagName === 'OPTION').map((o) => o.getAttribute('value'));
    assert.deepEqual(values, ['', 'm1'], 'only record holders are offered, never itself');

    const go = buttonWithText(container.querySelector('.mv-link'), 'Link');
    go.click();                                                   // nothing chosen yet
    assert.equal(calls.save.length, 0);
    assert.match(container.textContent, /Choose a video first/);

    dropdown.value = 'm1';
    const swap = everything(container).find((e) => e.tagName === 'INPUT' && e.getAttribute('type') === 'checkbox' && /-m-swap$/.test(e.getAttribute('id')));
    assert.equal(swap.checked, false, 'sides swapped defaults off');
    swap.checked = true;
    go.click();
    await tick(10);
    assert.deepEqual(calls.save, [['x', { part_of: 'm1', sides_swapped: true }]]);
    assert.deepEqual(calls.toasts, ['Linked']);
    assert.equal(container.querySelector('[data-expand="m1"]').getAttribute('aria-expanded'), 'true', 'the match opens to show the new part');
    assert.equal(env.doc.activeElement, container.querySelector('[data-expand="m1"]'));
    assert.equal(container.querySelector('.mv-editor'), null, 'the editor closed');
  }, {
    overview: () => (linked ? OV([P1, { ...P2, stem: 'x' }]) : OV()),
    saveMatch: async () => { linked = true; return {}; },
  });
});

test('mount: a video with its own record has the part-of dropdown disabled with the clear-first hint', async () => {
  await withView(async ({ container }) => {
    container.querySelector('[data-rec="m1"]').click();
    await tick(5);
    const dropdown = everything(container).find((e) => e.tagName === 'SELECT' && /-m-sel$/.test(e.getAttribute('id') || ''));
    assert.equal(dropdown.disabled, true);
    assert.match(container.textContent, /Clear the record first to link it/);
    // the primary side: the video "x" is offered as a part; m2/m1 never are; existing parts have Unlink buttons
    const partSel = everything(container).find((e) => e.tagName === 'SELECT' && /-p-sel$/.test(e.getAttribute('id') || ''));
    assert.deepEqual(everything(partSel).filter((o) => o.tagName === 'OPTION').map((o) => o.getAttribute('value')), ['', 'x']);
    assert.ok(buttonWithText(container, 'Unlink'));
  }, { match: async (stem) => (stem === 'm1' ? { date: '2026-10-01', upper: { name: 'Sindhu' }, lower: { name: 'Axelsen' }, parts: [{ stem: 'm2', sides_swapped: true }] } : {}) });
});

test('mount: the primary editor links a part and unlinks one (PUT {} on the part)', async () => {
  await withView(async ({ container, calls }) => {
    container.querySelector('[data-rec="m1"]').click();
    await tick(5);
    const partSel = everything(container).find((e) => e.tagName === 'SELECT' && /-p-sel$/.test(e.getAttribute('id') || ''));
    partSel.value = 'x';
    const linkBtn = everything(container).filter((e) => e.tagName === 'BUTTON' && e.textContent === 'Link')[0];
    linkBtn.click();
    await tick(10);
    assert.deepEqual(calls.save[0], ['x', { part_of: 'm1', sides_swapped: false }]);

    container.querySelector('[data-rec="m1"]').click();
    await tick(5);
    buttonWithText(container, 'Unlink').click();
    await tick(10);
    assert.deepEqual(calls.save[1], ['m2', {}]);
    assert.equal(calls.toasts[1], 'Unlinked');
  }, { match: async (stem) => (stem === 'm1' ? { date: '2026-10-01', upper: { name: 'Sindhu' }, lower: { name: 'Axelsen' }, parts: [{ stem: 'm2', sides_swapped: false }] } : {}) });
});

test('mount: a part\'s own editor (from its sub-row) shows "Linked to <primary>" and unlinks with {}', async () => {
  await withView(async ({ container, calls }) => {
    container.querySelector('[data-expand="m1"]').click();
    container.querySelector('[data-rec="m2"]').click();
    assert.equal(container.querySelector('[data-rec="m2"]').getAttribute('aria-expanded'), 'true');
    await tick(5);
    assert.match(container.querySelector('.mv-editor').textContent, /Linked to m1/);
    assert.match(container.querySelector('.mv-editor').textContent, /sides swapped/);
    assert.equal(everything(container).filter((e) => e.tagName === 'FORM').length, 0, 'no record form for a part');
    buttonWithText(container.querySelector('.mv-editor'), 'Unlink').click();
    await tick(10);
    assert.deepEqual(calls.save, [['m2', {}]]);
  }, { match: async (stem) => (stem === 'm2' ? { part_of: 'm1', sides_swapped: true, date: '2026-10-01' } : {}) });
});

test('mount: a link error is shown in place, the button comes back and the list refreshes quietly', async () => {
  await withView(async ({ container, calls }) => {
    container.querySelector('[data-rec="x"]').click();
    await tick(5);
    const dropdown = everything(container).find((e) => e.tagName === 'SELECT' && /-m-sel$/.test(e.getAttribute('id') || ''));
    dropdown.value = 'm1';
    const go = buttonWithText(container.querySelector('.mv-link'), 'Link');
    const before = calls.overview;
    go.click();
    await tick(10);
    assert.match(container.textContent, /That match was not found/);
    assert.equal(go.disabled, false);
    assert.ok(container.querySelector('.mv-editor'), 'the editor stays open');
    assert.equal(calls.overview, before + 1, 'the list was refreshed');
    assert.deepEqual(calls.toasts, []);
  }, { saveMatch: async () => { throw new ApiError('http', 400, { zh: '找不到这场比赛', en: 'That match was not found' }); } });
});

// ---------- QA round: single editor, collapse, focus, sets-only record, status order, clear confirmation, stale pickers ----------

const PRIM_REC = { date: '2026-10-01', upper: { name: 'Sindhu' }, lower: { name: 'Axelsen' }, parts: [{ stem: 'm2', sides_swapped: false }] };
const primMatch = async (stem) => (stem === 'm1' ? PRIM_REC : {});
const ids = (root) => everything(root).map((e) => e.getAttribute('id')).filter(Boolean);
const editors = (root) => everything(root).filter((e) => e.classList.contains('mv-editor'));

test('rowHasRecord: a record holding only set scores counts; isBlankRecord reads the validated form record', () => {
  assert.equal(rowHasRecord({ sets: [[21, 18]] }), true);
  assert.equal(rowHasRecord({ sets: [] }), false);
  assert.equal(rowHasRecord(matchRows({ matches: [{ stem: 'q', sets: [[21, 18], [19, 21]] }] })[0]), true);
  assert.equal(isBlankRecord({ date: null, upper: null, lower: null, result: null }), true);
  assert.equal(isBlankRecord({ date: '2026-10-01', upper: null, lower: null, result: null }), false);
  assert.equal(isBlankRecord({ date: null, upper: { name: 'A' }, lower: null, result: null }), false);
  assert.equal(isBlankRecord({ date: null, upper: null, lower: null, result: { winner: 'upper', sets: [] } }), false);
  assert.equal(isBlankRecord(null), true);
});

test('matchRows: a split row that is still processing / new reads that, not needs_review; unlinked rows are unchanged', () => {
  const rows = matchRows({ matches: [
    { stem: 'a', status: 'processing', needs_review: true, parts: [P1, P2] },
    { stem: 'b', status: 'new', needs_review: true, parts: [P1, P2] },
    { stem: 'c', status: 'analysed', needs_review: true, parts: [P1, P2] },
    { stem: 'd', status: 'processing', needs_review: true },
    { stem: 'e', status: 'new', needs_review: true, parts: [] },
  ] });
  assert.deepEqual(rows.map((r) => r.status), ['processing', 'new', 'needs_review', 'needs_review', 'needs_review']);
});

test('mount: the "N parts" chip has its own class (not the doubles chip class)', async () => {
  await withView(async ({ container }) => {
    const chip = everything(container).find((e) => e.classList.contains('k-chip') && e.textContent === '2 parts');
    assert.ok(chip.classList.contains('mv-parts-chip'));
    assert.ok(!chip.classList.contains('kw-doubles'));
  });
});

test('mount: expanded split row with the primary editor open renders exactly one editor and no duplicate ids', async () => {
  await withView(async ({ container }) => {
    container.querySelector('[data-expand="m1"]').click();
    container.querySelector('[data-rec="m1"]').click();
    await tick(5);
    assert.equal(editors(container).length, 1);
    const all = ids(container);
    assert.equal(new Set(all).size, all.length, 'ids are unique');
    const subs = everything(container).filter((e) => e.classList.contains('mv-part-row'));
    assert.ok(!subs[0].classList.contains('open'), 'the primary sub-row is not the editor row');
  }, { match: primMatch });
});

test('mount: collapsing the chevron keeps the primary\'s own editor open', async () => {
  await withView(async ({ container }) => {
    container.querySelector('[data-expand="m1"]').click();
    container.querySelector('[data-rec="m1"]').click();
    await tick(5);
    container.querySelector('[data-expand="m1"]').click();           // collapse
    await tick(5);
    assert.equal(container.querySelector('[data-expand="m1"]').getAttribute('aria-expanded'), 'false');
    assert.equal(editors(container).length, 1);
    assert.equal(container.querySelector('[data-rec="m1"]').getAttribute('aria-expanded'), 'true');
  }, { match: primMatch });
});

test('mount: collapsing still closes a non-primary part\'s editor', async () => {
  await withView(async ({ container }) => {
    container.querySelector('[data-expand="m1"]').click();
    container.querySelector('[data-rec="m2"]').click();
    await tick(5);
    assert.equal(editors(container).length, 1);
    container.querySelector('[data-expand="m1"]').click();
    assert.equal(editors(container).length, 0);
  }, { match: async (stem) => (stem === 'm2' ? { part_of: 'm1', sides_swapped: false } : {}) });
});

test('mount: a linked part\'s editor takes keyboard focus on its Unlink button', async () => {
  await withView(async ({ container }) => {
    container.querySelector('[data-expand="m1"]').click();
    container.querySelector('[data-rec="m2"]').click();
    await tick(5);
    const a = env.doc.activeElement;
    assert.ok(a && a.tagName === 'BUTTON' && a.textContent === 'Unlink');
    assert.ok(container.querySelector('.mv-editor').contains(a));
  }, { match: async (stem) => (stem === 'm2' ? { part_of: 'm1', sides_swapped: true } : {}) });
});

function withConfirm(answer) {
  const asked = [];
  globalThis.confirm = (msg) => { asked.push(msg); return answer; };
  return { asked, restore() { delete globalThis.confirm; } };
}
const blankTheForm = (container) => {
  for (const id of ['mv-date', 'mv-upper', 'mv-lower', 'mv-winner', 'mv-sets']) everything(container).find((e) => e.getAttribute('id') === id).value = '';
};
const submitForm = (container) => everything(container).find((e) => e.tagName === 'FORM').listeners.submit[0]({ preventDefault() {} });

test('mount: clearing a primary that has linked videos asks first and says they will be unlinked', async () => {
  for (const [answer, saves] of [[false, 0], [true, 1]]) {
    const c = withConfirm(answer);
    try {
      await withView(async ({ container, calls }) => {
        container.querySelector('[data-rec="m1"]').click();
        await tick(5);
        buttonWithText(container.querySelector('.mv-editor'), 'Clear record').click();
        assert.equal(c.asked.length, 1);
        assert.match(c.asked[0], /1 linked video/);
        assert.match(c.asked[0], /unlink/i);
        await tick(10);
        assert.equal(calls.save.length, saves);
        if (saves) assert.deepEqual(calls.save[0], ['m1', {}]);
      }, { match: primMatch });
    } finally { c.restore(); }
  }
});

test('mount: saving a primary\'s record with every field empty asks first (it unlinks the parts)', async () => {
  for (const [answer, saves] of [[false, 0], [true, 1]]) {
    const c = withConfirm(answer);
    try {
      await withView(async ({ container, calls }) => {
        container.querySelector('[data-rec="m1"]').click();
        await tick(5);
        blankTheForm(container);
        submitForm(container);
        await tick(10);
        assert.equal(c.asked.length, 1);
        assert.match(c.asked[0], /linked video/);
        assert.equal(calls.save.length, saves);
      }, { match: primMatch });
    } finally { c.restore(); }
  }
});

test('mount: a non-blank save of a primary with parts does not ask; a record without parts keeps the plain clear confirm', async () => {
  let c = withConfirm(true);
  try {
    await withView(async ({ container, calls }) => {
      container.querySelector('[data-rec="m1"]').click();
      await tick(5);
      submitForm(container);                     // the stored record is unchanged: not blank
      await tick(10);
      assert.equal(c.asked.length, 0);
      assert.equal(calls.save.length, 1);
    }, { match: primMatch });
  } finally { c.restore(); }
  c = withConfirm(true);
  try {
    await withView(async ({ container, calls }) => {
      container.querySelector('[data-rec="x"]').click();
      await tick(5);
      buttonWithText(container.querySelector('.mv-editor'), 'Clear record').click();
      assert.deepEqual(c.asked, [t('record.clear_confirm', 'en')]);
      await tick(10);
      assert.deepEqual(calls.save[0], ['x', {}]);
    }, { match: async (stem) => (stem === 'x' ? { date: '2026-10-05', upper: { name: 'A' } } : {}) });
  } finally { c.restore(); }
});

test('mount: after a link error the pickers are rebuilt from the refreshed list and the error stays', async () => {
  await withView(async ({ container }) => {
    container.querySelector('[data-rec="x"]').click();
    await tick(5);
    const dropdown = everything(container).find((e) => e.tagName === 'SELECT' && /-m-sel$/.test(e.getAttribute('id') || ''));
    const optionValues = () => everything(dropdown).filter((o) => o.tagName === 'OPTION').map((o) => o.getAttribute('value'));
    assert.deepEqual(optionValues(), ['', 'm1']);
    dropdown.value = 'm1';
    buttonWithText(container.querySelector('.mv-link'), 'Link').click();
    await tick(15);
    assert.match(container.textContent, /That match was not found/);
    assert.deepEqual(optionValues(), [''], 'the vanished match is no longer offered');
    assert.equal(dropdown.value, '');
    assert.equal(dropdown.disabled, true);
    assert.equal(editors(container).length, 1, 'the editor stays open');
  }, {
    overview: (n) => (n === 1 ? OV() : { matches: [{ stem: 'x', status: 'analysed', side: 'lower', parts: [] }] }),
    saveMatch: async () => { throw new ApiError('http', 400, { zh: '找不到这场比赛', en: 'That match was not found' }); },
  });
});

// ---------- scoring format in the match editor (spec 2026-10-28 section 4) ----------

const fire = (el, type) => (el.listeners[type] || []).forEach((f) => f({}));
const byIdSuffix = (root, suffix) => everything(root).find((e) => (e.getAttribute('id') || '').endsWith(suffix));

test('mount: the editor offers the format presets; an untouched default saves no scoring', async () => {
  await withView(async ({ container, calls }) => {
    container.querySelector('[data-rec="x"]').click();
    await tick(5);
    const preset = byIdSuffix(container, '-preset');
    const options = everything(preset).filter((o) => o.tagName === 'OPTION').map((o) => o.getAttribute('value'));
    assert.deepEqual(options, ['bwf_21x3', 'one_game_30', '15x3', 'custom']);
    assert.equal(preset.value, 'bwf_21x3');
    everything(container).find((e) => e.getAttribute('id') === 'mv-date').value = '2026-10-01';
    submitForm(container);
    await tick(10);
    assert.equal('scoring' in calls.save[0][1], false);
  });
});

test('mount: picking a preset fills the fields and saves the scoring; editing a field makes it custom', async () => {
  await withView(async ({ container, calls }) => {
    container.querySelector('[data-rec="x"]').click();
    await tick(5);
    const preset = byIdSuffix(container, '-preset');
    preset.value = 'one_game_30';
    fire(preset, 'change');
    assert.equal(byIdSuffix(container, '-points_to_win').value, '30');
    assert.equal(byIdSuffix(container, '-best_of').value, '1');
    assert.equal(byIdSuffix(container, '-change_ends_at').value, '15');
    assert.equal(byIdSuffix(container, '-cap').value, '');
    everything(container).find((e) => e.getAttribute('id') === 'mv-date').value = '2026-10-01';
    submitForm(container);
    await tick(10);
    assert.deepEqual(calls.save[0][1].scoring, { preset: 'one_game_30', points_to_win: 30, win_by: 1, cap: null, best_of: 1, change_ends_after_game: false, change_ends_at: 15 });

  }, { overview: () => ({ matches: [{ stem: 'x', status: 'analysed', side: 'lower', parts: [] }] }) });
});

test('mount: editing a field switches the selector to custom, and a bad value is an error, not a save', async () => {
  await withView(async ({ container, calls }) => {
    container.querySelector('[data-rec="x"]').click();
    await tick(5);
    const points = byIdSuffix(container, '-points_to_win');
    points.value = '99';
    fire(points, 'input');
    assert.equal(byIdSuffix(container, '-preset').value, 'custom');
    submitForm(container);
    await tick(10);
    assert.equal(calls.save.length, 0);
    assert.match(container.querySelector('.mv-scoring').textContent, /Invalid scoring format/);
  });
});

test('mount: a stored scoring reloads into the form, and a result impossible under it warns but still saves', async () => {
  await withView(async ({ container, calls }) => {
    container.querySelector('[data-rec="m1"]').click();
    await tick(5);
    assert.equal(byIdSuffix(container, '-preset').value, 'one_game_30');
    assert.equal(byIdSuffix(container, '-points_to_win').value, '30');
    const warn = container.querySelector('.mv-warn');
    assert.equal(warn.textContent, '', 'a legal 30-13 does not warn');
    const sets = everything(container).find((e) => e.getAttribute('id') === 'mv-sets');
    sets.value = '31-29';
    fire(sets, 'input');
    assert.match(warn.textContent, /not possible under this format/);
    submitForm(container);
    await tick(10);
    assert.equal(calls.save.length, 1, 'saved as typed');
    assert.deepEqual(calls.save[0][1].result.sets, [[31, 29]]);
    assert.equal(calls.save[0][1].scoring.preset, 'one_game_30');
    assert.ok(calls.toasts.some((m) => /not possible under this format/.test(m)));
  }, { match: async () => ({ date: '2026-10-01', upper: { name: 'S' }, lower: { name: 'A' }, result: { winner: 'upper', sets: [[30, 13]] }, scoring: { preset: 'one_game_30', points_to_win: 30, win_by: 1, cap: null, best_of: 1, change_ends_after_game: false, change_ends_at: 15 } }) });
});
