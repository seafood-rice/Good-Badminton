// Shared widget plumbing: tiny DOM builders, in-place setters, reveal/scan helpers and the HUD card frame.
// No DOM access at import time, so the pure modules that import this one load under `node --test`.
import { t } from '../core/i18n.js';
import { local } from '../core/motion.js';

const SVG_NS = 'http://www.w3.org/2000/svg';

export const hasDOM = () => typeof document !== 'undefined' && typeof document.createElement === 'function';

function applyAttrs(el, attrs) {
  if (!attrs) return;
  for (const [k, v] of Object.entries(attrs)) {
    if (v === undefined || v === null || v === false) continue;
    if (k === 'text') el.textContent = String(v);
    else if (k === 'class') el.setAttribute('class', String(v));
    else if (k === 'style' && typeof v === 'object') {
      for (const [p, sv] of Object.entries(v)) setStyle(el, p, sv);
    } else if (k.startsWith('on') && typeof v === 'function') el.addEventListener(k.slice(2).toLowerCase(), v);
    else el.setAttribute(k, v === true ? '' : String(v));
  }
}

function append(el, children) {
  if (children === undefined || children === null || children === false) return;
  if (Array.isArray(children)) {
    children.forEach((c) => append(el, c));
    return;
  }
  el.appendChild(typeof children === 'object' ? children : document.createTextNode(String(children)));
}

/** HTML element. Strings in `children` become text nodes (never parsed as HTML). */
export function h(tag, attrs, children) {
  const el = document.createElement(tag);
  applyAttrs(el, attrs);
  append(el, children);
  return el;
}

/** SVG element (same contract as h). */
export function s(tag, attrs, children) {
  const el = document.createElementNS(SVG_NS, tag);
  applyAttrs(el, attrs);
  append(el, children);
  return el;
}

/** Set an attribute only when it changed (cheap per-frame writes). */
export function setAttr(el, name, value) {
  if (!el || typeof el.setAttribute !== 'function') return;
  const v = String(value);
  if (el.getAttribute(name) !== v) el.setAttribute(name, v);
}

export function setStyle(el, prop, value) {
  if (!el || !el.style) return;
  const v = value === null || value === undefined ? '' : String(value);
  if (prop.startsWith('--') || prop.includes('-')) {
    if (el.style.getPropertyValue(prop) !== v) el.style.setProperty(prop, v);
  } else if (el.style[prop] !== v) el.style[prop] = v;
}

export function setText(el, text) {
  if (!el) return;
  const v = text === null || text === undefined ? '' : String(text);
  if (el.textContent !== v) el.textContent = v;
}

const clamp01 = (x) => (x < 0 ? 0 : x > 1 ? 1 : x);

/** Linear (un-eased) local progress for slot `slot`: what the scan line follows. */
export function rawLocal(p, slot) {
  return clamp01((p - slot * 0.06) / 0.45);
}

/** Fade and lift an element by eased local progress `l` (canvas: translateY((1-l)*16px)). */
export function revealStyle(el, l, { dy = 16, dx = 0 } = {}) {
  setStyle(el, 'opacity', l >= 1 ? '' : l.toFixed(3));
  const off = 1 - l;
  if (off <= 0.0005) setStyle(el, 'transform', '');
  else setStyle(el, 'transform', dx ? `translateX(${(off * dx).toFixed(1)}px)` : `translateY(${(off * dy).toFixed(1)}px)`);
}

/** Cyan scan line: visible only while the slot's linear progress is strictly between 0 and 1. */
export function updateScan(scanEl, p, slot) {
  const r = rawLocal(p, slot);
  if (r <= 0 || r >= 1) {
    setStyle(scanEl, 'display', 'none');
    return;
  }
  setStyle(scanEl, 'display', '');
  setStyle(scanEl, 'left', (r * 100).toFixed(1) + '%');
}

export function scanLine() {
  return h('div', { class: 'scan', 'aria-hidden': 'true', style: { display: 'none' } });
}

const ICON = {
  up: 'M12 19V5 M5 12l7-7 7 7',
  down: 'M12 5v14 M19 12l-7 7-7-7',
  eye: 'M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z M12 9a3 3 0 1 0 0 6 3 3 0 0 0 0-6z',
  eyeOff: 'M3 3l18 18 M10.6 5.1A10.4 10.4 0 0 1 12 5c6.5 0 10 7 10 7a17.7 17.7 0 0 1-3.2 4.2 M6.6 6.6A17.5 17.5 0 0 0 2 12s3.5 7 10 7a9.9 9.9 0 0 0 5.4-1.6',
};

export function icon(name, size = 14) {
  return s('svg', {
    width: size, height: size, viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', 'stroke-width': 2,
    'stroke-linecap': 'round', 'stroke-linejoin': 'round', 'aria-hidden': 'true', focusable: 'false',
  }, s('path', { d: ICON[name] || '' }));
}

/** Show/hide and up/down controls for customize mode (ctx.edit), or null when not editing. */
function editControls(title, ctx) {
  const ed = ctx && ctx.edit;
  if (!ed || !ed.editing) return null;
  const lang = ctx.lang;
  const btn = (name, label, disabled, fn) => h('button', {
    type: 'button', class: 'kw-ctl', 'aria-label': `${label} · ${title}`, title: label, 'data-ctl': `${ed.id}:${name}`,
    disabled: disabled ? true : null,
    onclick: (e) => { e.stopPropagation(); if (typeof fn === 'function') fn(); },
  }, icon(name === 'toggle' ? (ed.hidden ? 'eye' : 'eyeOff') : name));
  return h('div', { class: 'kw-ctls', role: 'group', 'aria-label': t('layout.editing_title', lang) }, [
    btn('up', t('layout.up', lang), ed.first, () => ed.onMove && ed.onMove(-1)),
    btn('down', t('layout.down', lang), ed.last, () => ed.onMove && ed.onMove(1)),
    btn('toggle', t(ed.hidden ? 'layout.show' : 'layout.hide', lang), false, () => ed.onToggle && ed.onToggle()),
  ]);
}

/**
 * HUD-framed card: title, subtitle, optional right-hand node, body.
 * data: {id, title, sub, flex, label, right}. ctx.slot positions it in the reveal; ctx.edit adds layout controls.
 * Returns {el, body, subEl, head, update(p)}; update fades/lifts the card and sweeps the scan line.
 */
export function build(data, ctx = {}) {
  const d = data || {};
  const slot = typeof ctx.slot === 'number' ? ctx.slot : 0;
  const hidden = !!(ctx.edit && ctx.edit.hidden);
  const scan = scanLine();
  const subEl = h('div', { class: 'card-sub' }, d.sub || '');
  const head = h('div', { class: 'kw-head' }, [
    h('div', { class: 'kw-titles' }, [h('h2', { class: 'card-title' }, d.title || ''), subEl]),
    d.right || null,
    editControls(d.title || '', ctx),
  ]);
  const body = h('div', { class: 'kw-body' });
  const el = h('section', {
    class: 'card hud kw' + (ctx.edit && ctx.edit.editing ? ' editing' : '') + (hidden ? ' kw-hidden' : ''),
    'aria-label': d.label || d.title || '',
    'data-widget': d.id || '',
    style: d.flex ? { flex: d.flex } : null,
  }, [scan, head, hidden ? h('div', { class: 'kw-hidden-tag mono' }, t('layout.hidden', ctx.lang)) : null, body]);

  function update(p) {
    revealStyle(el, local(p, slot));
    updateScan(scan, p, slot);
  }
  return { el, body, head, subEl, update };
}
