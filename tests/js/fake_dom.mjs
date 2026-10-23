// A tiny fake DOM for mount() tests (no jsdom: no new dependency). Class / tag / id / attribute selectors and
// descendant chains; a body; event listeners with dispatch(); focus tracking. Same approach as split_link.test.mjs.

export const env = { doc: null };

export class FakeText {
  constructor(v) { this.nodeValue = String(v); this.parentNode = null; }
  get textContent() { return this.nodeValue; }
}

function simple(tok) {
  const checks = [];
  // Attribute tests first, so the words inside a quoted value are never read as tag / class tokens.
  const re = /\[([\w-]+)(?:="((?:[^"\\]|\\.)*)")?\]|([.#]?[\w-]+)/g;
  let hit;
  while ((hit = re.exec(tok))) {
    const m = hit;
    if (m[1]) checks.push((el) => el.hasAttribute(m[1]) && (m[2] === undefined || el.getAttribute(m[1]) === m[2].replace(/\\(.)/g, '$1')));
    else if (m[3][0] === '.') checks.push((el) => el.classList.contains(m[3].slice(1)));
    else if (m[3][0] === '#') checks.push((el) => el.getAttribute('id') === m[3].slice(1));
    else checks.push((el) => el.tagName === m[3].toUpperCase());
  }
  return (el) => checks.every((c) => c(el));
}

// Attribute values may hold escaped quotes / backslashes ([data-x="a\"b"]): split on commas and spaces outside them.
function parseSelector(sel) {
  const out = [];
  let chain = [];
  let cur = '';
  let inQ = false;
  const flush = () => { if (cur.trim()) chain.push(simple(cur.trim())); cur = ''; };
  const s = sel;
  for (let i = 0; i < s.length; i++) {
    const c = s[i];
    if (c === '\\' && inQ) { cur += c + s[i + 1]; i++; continue; }
    if (c === '"') inQ = !inQ;
    if (!inQ && c === ',') { flush(); out.push(chain); chain = []; continue; }
    if (!inQ && /\s/.test(c)) { flush(); continue; }
    cur += c;
  }
  flush();
  out.push(chain);
  return out.filter((ch) => ch.length);
}

export class FakeEl {
  constructor(tag) {
    this.tagName = String(tag).toUpperCase();
    this.childNodes = [];
    this.parentNode = null;
    this.attrs = new Map();
    this.listeners = {};
    this.disabled = false;
    this.hidden = false;
    this.value = '';
    this.checked = false;
    this.style = { setProperty(k, v) { this[k] = String(v); }, getPropertyValue(k) { return this[k] || ''; } };
    const self = this;
    this.classList = {
      toggle(c, on) {
        const set = new Set((self.getAttribute('class') || '').split(/\s+/).filter(Boolean));
        const want = on === undefined ? !set.has(c) : on;
        if (want) set.add(c); else set.delete(c);
        self.attrs.set('class', [...set].join(' '));
      },
      contains: (c) => (self.getAttribute('class') || '').split(/\s+/).includes(c),
      add: (c) => self.classList.toggle(c, true),
      remove: (c) => self.classList.toggle(c, false),
    };
  }
  get isConnected() {
    for (let x = this; x; x = x.parentNode) if (env.doc && x === env.doc.body) return true;
    return false;
  }
  get children() { return this.childNodes.filter((c) => c instanceof FakeEl); }
  get firstChild() { return this.childNodes[0] || null; }
  appendChild(c) {
    if (c.parentNode) c.parentNode.removeChild(c);
    c.parentNode = this;
    this.childNodes.push(c);
    return c;
  }
  insertBefore(c, ref) {
    if (c.parentNode) c.parentNode.removeChild(c);
    const i = ref ? this.childNodes.indexOf(ref) : -1;
    c.parentNode = this;
    if (i < 0) this.childNodes.push(c); else this.childNodes.splice(i, 0, c);
    return c;
  }
  removeChild(c) {
    const i = this.childNodes.indexOf(c);
    if (i >= 0) this.childNodes.splice(i, 1);
    c.parentNode = null;
    return c;
  }
  remove() { if (this.parentNode) this.parentNode.removeChild(this); }
  replaceChildren(...nodes) {
    for (const c of this.childNodes) c.parentNode = null;
    this.childNodes = [];
    nodes.forEach((n) => this.appendChild(n));
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
  /** Fire `type` here and bubble it up the parents (like a real event). */
  dispatch(type, extra = {}) {
    let stopped = false;
    const ev = { type, target: this, button: 0, key: undefined, defaultPrevented: false, preventDefault() { this.defaultPrevented = true; }, stopPropagation() { stopped = true; }, ...extra };
    for (let n = this; n && !stopped; n = n.parentNode) {
      for (const fn of [...((n.listeners && n.listeners[type]) || [])]) fn(ev);
    }
    return ev;
  }
  click() {
    if (this.disabled) return;
    this.dispatch('click');
  }
  set textContent(v) {
    for (const c of this.childNodes) c.parentNode = null;
    this.childNodes = v === '' || v === null || v === undefined ? [] : [new FakeText(v)];
  }
  get textContent() { return this.childNodes.map((c) => c.textContent).join(''); }
  focus() { env.doc.activeElement = this; }
  getClientRects() { return [1]; }
  getBoundingClientRect() { return { left: 0, top: 0, right: 0, bottom: 0, width: 0, height: 0 }; }
  contains(n) { for (let x = n; x; x = x.parentNode) if (x === this) return true; return false; }
  closest(sel) {
    const chains = parseSelector(sel);
    for (let x = this; x instanceof FakeEl; x = x.parentNode) if (chains.some((ch) => ch[ch.length - 1](x))) return x;
    return null;
  }
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
}

const REAL = { setTimeout, clearTimeout };

/** Install document / window / storage globals; returns {doc, storage}. */
export function installEnv() {
  const body = new FakeEl('body');
  const store = new Map();
  env.doc = {
    activeElement: null,
    body,
    createElement: (tag) => new FakeEl(tag),
    createElementNS: (ns, tag) => new FakeEl(tag),
    createTextNode: (v) => new FakeText(v),
    querySelector: (sel) => body.querySelector(sel),
    querySelectorAll: (sel) => body.querySelectorAll(sel),
    addEventListener() {},
    removeEventListener() {},
  };
  globalThis.document = env.doc;
  globalThis.requestAnimationFrame = (fn) => REAL.setTimeout(() => fn(Date.now()), 16);
  globalThis.cancelAnimationFrame = (id) => REAL.clearTimeout(id);
  globalThis.localStorage = {
    getItem: (k) => (store.has(k) ? store.get(k) : null),
    setItem: (k, v) => store.set(k, String(v)),
    removeItem: (k) => store.delete(k),
  };
  return { doc: env.doc, storage: store };
}

export function removeEnv() {
  delete globalThis.document;
  delete globalThis.requestAnimationFrame;
  delete globalThis.cancelAnimationFrame;
  delete globalThis.localStorage;
  env.doc = null;
}

export const tick = (ms = 0) => new Promise((r) => REAL.setTimeout(r, ms));

export function everything(node, out = []) {
  for (const c of node.childNodes || []) {
    if (c instanceof FakeEl) { out.push(c); everything(c, out); }
  }
  return out;
}

export const byText = (root, tag, text) => everything(root).find((e) => e.tagName === tag && e.textContent === text) || null;
export const buttonWithText = (root, text) => byText(root, 'BUTTON', text);
