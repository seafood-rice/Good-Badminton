import test from 'node:test';
import assert from 'node:assert/strict';
import { createPrefs, normTheme, normLang, PREF_KEYS } from '../../static/kestrel/core/prefs.js';

function memory() {
  const m = new Map();
  return { getItem: (k) => (m.has(k) ? m.get(k) : null), setItem: (k, v) => m.set(k, String(v)), removeItem: (k) => m.delete(k), m };
}
const throwing = () => ({
  getItem() { throw new Error('denied'); },
  setItem() { throw new Error('quota'); },
  removeItem() { throw new Error('denied'); },
});

test('get returns the default when getItem throws', () => {
  const p = createPrefs(throwing());
  assert.equal(p.get('kestrel_lang', 'zh'), 'zh');
  assert.deepEqual(p.getJSON('kestrel_layout_v1', { a: 1 }), { a: 1 });
});

test('set and remove report failure instead of throwing', () => {
  const p = createPrefs(throwing());
  assert.equal(p.set('k', 'v'), false);
  assert.equal(p.remove('k'), false);
});

test('missing storage (no localStorage at all) behaves as empty', () => {
  const p = createPrefs(null);
  assert.equal(p.get('k', 'd'), 'd');
  assert.equal(p.set('k', 'v'), false);
});

test('round trip and defaults', () => {
  const s = memory();
  const p = createPrefs(s);
  assert.equal(p.get('lang', 'zh'), 'zh');
  assert.equal(p.set('lang', 'en'), true);
  assert.equal(p.get('lang', 'zh'), 'en');
  assert.equal(p.remove('lang'), true);
  assert.equal(p.get('lang', 'zh'), 'zh');
});

test('JSON values round trip; corrupt JSON falls back to the default', () => {
  const s = memory();
  const p = createPrefs(s);
  p.setJSON('layout', { hidden: ['radar'] });
  assert.deepEqual(p.getJSON('layout', null), { hidden: ['radar'] });
  s.setItem('layout', '{not json');
  assert.deepEqual(p.getJSON('layout', { ok: 1 }), { ok: 1 });
});

test('normTheme maps the legacy values', () => {
  assert.equal(normTheme('light'), 'day');
  assert.equal(normTheme('day'), 'day');
  assert.equal(normTheme('dark'), 'night');
  assert.equal(normTheme('night'), 'night');
  assert.equal(normTheme(null), 'night');
  assert.equal(normTheme('???'), 'night');
});

test('normLang', () => {
  assert.equal(normLang('en'), 'en');
  assert.equal(normLang('zh'), 'zh');
  assert.equal(normLang('fr'), 'zh');
  assert.equal(normLang(null), 'zh');
});

test('key names match the legacy and spec keys', () => {
  assert.equal(PREF_KEYS.lang, 'kestrel_lang');
  assert.equal(PREF_KEYS.theme, 'kestrel_theme');
  assert.equal(PREF_KEYS.layout, 'kestrel_layout_v1');
});
