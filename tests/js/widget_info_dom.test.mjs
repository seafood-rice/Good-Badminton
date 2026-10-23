// Widget help against a tiny fake DOM (no jsdom: no new dependency): the "i" button on cards and tiles, the
// dialog's close paths and focus return, textContent-only text, and the court map's NET / BASELINE placement.
import test from 'node:test';
import assert from 'node:assert/strict';
import { STRINGS } from '../../static/kestrel/core/i18n.js';

// ---------- fake DOM ----------
class FakeText {
  constructor(v) { this.nodeValue = String(v); this.parentNode = null; }
  get textContent() { return this.nodeValue; }
}

class FakeEl {
  constructor(tag) {
    this.tagName = String(tag).toUpperCase();
    this.childNodes = [];
    this.parentNode = null;
    this.attrs = new Map();
    this.listeners = {};
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
  remove() { if (this.parentNode) this.parentNode.removeChild(this); }
  setAttribute(k, v) { this.attrs.set(k, String(v)); }
  getAttribute(k) { return this.attrs.has(k) ? this.attrs.get(k) : null; }
  addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); }
  set textContent(v) {
    for (const c of this.childNodes) c.parentNode = null;
    this.childNodes = v === '' || v === null || v === undefined ? [] : [new FakeText(v)];
  }
  get textContent() { return this.childNodes.map((c) => c.textContent).join(''); }
  get isConnected() { for (let x = this; x; x = x.parentNode) if (x === doc.body) return true; return false; }
  focus() { doc.activeElement = this; }
  contains(n) { for (let x = n; x; x = x.parentNode) if (x === this) return true; return false; }
}

const doc = {
  body: new FakeEl('body'),
  activeElement: null,
  createElement: (t) => new FakeEl(t),
  createElementNS: (ns, t) => new FakeEl(t),
  createTextNode: (v) => new FakeText(v),
};
const winListeners = {};
globalThis.document = doc;
globalThis.window = {
  addEventListener(type, fn) { (winListeners[type] ||= []).push(fn); },
  removeEventListener(type, fn) { winListeners[type] = (winListeners[type] || []).filter((f) => f !== fn); },
};

/** Dispatch a bubbling event from `target`; returns the event. */
function dispatch(target, type, extra = {}) {
  const ev = { type, target, stopped: false, preventDefault() {}, stopPropagation() { this.stopped = true; }, ...extra };
  for (let n = target; n && !ev.stopped; n = n.parentNode) {
    for (const fn of [...((n.listeners && n.listeners[type]) || [])]) fn(ev);
  }
  return ev;
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

const info = await import('../../static/kestrel/widgets/info.js');
const frame = await import('../../static/kestrel/widgets/frame.js');
const kpi = await import('../../static/kestrel/widgets/kpi.js');
const court = await import('../../static/kestrel/widgets/courtmap.js');

function openFrom(btn) {
  btn.focus();
  dispatch(btn, 'click', { button: 0 });
  return doc.body.childNodes.find((n) => n.classList && n.classList.contains('info-root'));
}

test('cards and tiles carry exactly one "i" button when they have help text, none otherwise', () => {
  const card = frame.build({ id: 'court', title: 'Court map' }, { lang: 'en' });
  assert.equal(byClass(card.head, 'kw-info').length, 1);
  assert.equal(byClass(card.head, 'kw-info')[0].getAttribute('aria-label'), 'About Court map');
  assert.equal(byClass(frame.build({ id: 'nope', title: 'X' }, { lang: 'en' }).head, 'kw-info').length, 0);

  const tile = kpi.build({ id: 'recovery', kind: 'number', label: 'Recovery to base', value: 1.2 }, { lang: 'en' });
  assert.equal(byClass(tile.el, 'kw-info').length, 1);
  const gapTile = kpi.build({ id: 'rally_win_rate', kind: 'gap', label: 'Rally win rate', reason: 'x' }, { lang: 'zh' });
  assert.equal(byClass(gapTile.el, 'kw-info').length, 1);
  assert.equal(byClass(gapTile.el, 'kw-info')[0].getAttribute('data-info-btn'), 'rally_win_rate');
});

test('a click on the button opens the dialog and never reaches the card', () => {
  const card = frame.build({ id: 'radar', title: 'Technique profile' }, { lang: 'en' });
  doc.body.appendChild(card.el);
  let cardClicks = 0;
  card.el.addEventListener('click', () => { cardClicks += 1; });
  const root = openFrom(byClass(card.head, 'kw-info')[0]);
  assert.equal(cardClicks, 0);
  assert.ok(root && info.isOpen());
  const dialog = byClass(root, 'info-dialog')[0];
  assert.equal(dialog.getAttribute('data-info'), 'radar');
  assert.equal(dialog.getAttribute('role'), 'dialog');
  assert.equal(dialog.getAttribute('tabindex'), '-1');
  assert.equal(byClass(dialog, 'info-sec').length, 3);
  info.close();
  card.el.remove();
});

test('Esc, the scrim, the Esc button and a route change each close it and return focus to the opener', () => {
  const card = frame.build({ id: 'trend', title: 'Technique index over time' }, { lang: 'en' });
  doc.body.appendChild(card.el);
  const btn = byClass(card.head, 'kw-info')[0];
  const closers = {
    escape: (root) => dispatch(byClass(root, 'info-dialog')[0], 'keydown', { key: 'Escape' }),
    scrim: (root) => dispatch(byClass(root, 'scrim')[0], 'click'),
    escButton: (root) => dispatch(byClass(root, 'palette-esc')[0], 'click'),
    hashchange: () => (winListeners.hashchange || []).forEach((fn) => fn({ type: 'hashchange' })),
  };
  for (const [name, closeIt] of Object.entries(closers)) {
    const root = openFrom(btn);
    assert.ok(info.isOpen(), name);
    assert.notEqual(doc.activeElement, btn, `${name}: focus moves into the dialog`);
    closeIt(root);
    assert.equal(info.isOpen(), false, name);
    assert.equal(root.parentNode, null, `${name}: root removed`);
    assert.equal(doc.activeElement, btn, `${name}: focus returned`);
    assert.equal((winListeners.hashchange || []).length, 0, `${name}: hashchange listener released`);
  }
  card.el.remove();
});

test('help text is set as text, never parsed as markup', () => {
  const saved = STRINGS.en['info.court.what'];
  STRINGS.en['info.court.what'] = '<img src=x onerror=alert(1)>';
  try {
    const card = frame.build({ id: 'court', title: '<b>t</b>' }, { lang: 'en' });
    const root = openFrom(byClass(card.head, 'kw-info')[0]);
    const p = all(root, (n) => n.tagName === 'P')[0];
    assert.equal(p.textContent, '<img src=x onerror=alert(1)>');
    assert.ok(p.childNodes.every((c) => c instanceof FakeText));
    assert.equal(all(root, (n) => n.tagName === 'IMG' || n.tagName === 'B').length, 0);
    info.close();
  } finally {
    STRINGS.en['info.court.what'] = saved;
  }
});

test('court map: NET above the grid, BASELINE below it, readout on its own line', () => {
  const w = court.build({ position: { cells: [], available: false, reason: 'r' }, mode: 'position' }, { lang: 'en' });
  const c = byClass(w.el, 'kw-court')[0];
  const kids = c.childNodes.filter((n) => n instanceof FakeEl);
  assert.ok(kids[0].classList.contains('kw-court-axis-top'));
  assert.equal(kids[0].textContent, 'NET');
  assert.ok(kids[1].classList.contains('kw-court-wrap'));
  assert.ok(kids[2].classList.contains('kw-court-axis'));
  assert.equal(kids[2].textContent, 'BASELINE');
  assert.ok(kids[3].classList.contains('kw-court-read'));
});
