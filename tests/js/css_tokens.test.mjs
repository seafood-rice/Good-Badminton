import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, existsSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..', '..');
const read = (rel) => readFileSync(join(ROOT, rel), 'utf8');
const esc = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

const css = read('static/kestrel/tokens.css');

const TOKENS = [
  'bg', 'surface', 'surface2', 'line', 'lineHi', 'ink', 'ink2', 'ink3',
  'accent', 'accentInk', 'cyan', 'cyanInk', 'blue', 'good', 'mid', 'bad',
];
const LEGACY = ['paper', 'card', 'border', 'fill', 'muted', 'faint', 'sidebar-bg'];

// Declaration body of the first plain rule whose selector list contains `selector`.
function block(selector) {
  const re = new RegExp('(?:^|\\})\\s*([^{}@]*' + esc(selector) + '[^{}]*)\\{([^{}]*)\\}');
  const m = css.match(re);
  assert.ok(m, 'missing rule for ' + selector);
  return m[2];
}
const night = () => block(':root');
const day = () => block('[data-theme="day"]');
const decl = (body, name) => {
  const m = body.match(new RegExp('--' + esc(name) + '\\s*:\\s*([^;]+);'));
  return m ? m[1].trim() : null;
};

test('every spec 6.3 token is defined in night and day', () => {
  for (const [label, body] of [['night', night()], ['day', day()]]) {
    for (const t of TOKENS) {
      assert.ok(decl(body, t), `--${t} missing in ${label}`);
    }
  }
});

test('spec 6.3 values are verbatim', () => {
  const n = night();
  const d = day();
  assert.equal(decl(n, 'bg').toUpperCase(), '#05070A');
  assert.equal(decl(n, 'surface').toUpperCase(), '#0B1017');
  assert.equal(decl(n, 'ink3').toUpperCase(), '#7A8898');
  assert.equal(decl(n, 'accent').toUpperCase(), '#FF5A36');
  assert.equal(decl(d, 'bg').toUpperCase(), '#EDF0F3');
  assert.equal(decl(d, 'surface').toUpperCase(), '#FFFFFF');
  assert.equal(decl(d, 'cyan').toUpperCase(), '#1B7F92');
  assert.equal(decl(d, 'bad').toUpperCase(), '#C4262B');
});

test('legacy variable aliases are defined', () => {
  for (const name of LEGACY) {
    assert.match(css, new RegExp('--' + esc(name) + '\\s*:'), `--${name} alias missing`);
  }
});

test('@font-face covers the three families and the files exist', () => {
  for (const fam of ['Space Grotesk', 'Instrument Serif', 'IBM Plex Mono']) {
    assert.match(css, new RegExp("@font-face\\s*\\{[^}]*font-family:\\s*'" + fam + "'"), fam);
  }
  const urls = [...css.matchAll(/url\(\s*['"]?(\/static\/fonts\/[^'")]+)['"]?\s*\)/g)].map((m) => m[1]);
  assert.ok(urls.length >= 5, 'expected font urls, got ' + urls.length);
  for (const u of urls) {
    assert.ok(existsSync(join(ROOT, u.replace(/^\//, ''))), 'font file missing: ' + u);
  }
  for (const f of ['SpaceGrotesk-Variable.woff2', 'InstrumentSerif-Regular.woff2', 'InstrumentSerif-Italic.woff2']) {
    assert.ok(urls.some((u) => u.endsWith(f)), f + ' not referenced');
  }
});

test('prefers-reduced-motion block exists and kills motion', () => {
  const m = css.match(/@media\s*\(prefers-reduced-motion:\s*reduce\)\s*\{([\s\S]*?)\n\}/);
  assert.ok(m, 'reduced-motion block missing');
  assert.match(m[1], /animation:\s*none/);
  assert.match(m[1], /transition:\s*none/);
});

test('page assets and skeleton are in place', () => {
  for (const f of ['static/img/ground/court-aerial-night.jpg', 'static/img/ground/court-racket-top.jpg',
    'static/img/ground/CREDITS.md', 'static/fonts/FONTS.md']) {
    assert.ok(existsSync(join(ROOT, f)), f);
  }
  const html = read('kestrel.html');
  const order = ['/static/kestrel.css', '/static/kestrel/tokens.css', '/static/kestrel/shell.css', '/static/kestrel/widgets.css']
    .map((h) => html.indexOf(h));
  assert.ok(order.every((i) => i >= 0), 'stylesheet missing from kestrel.html');
  assert.deepEqual([...order].sort((a, b) => a - b), order, 'stylesheet order');
  assert.match(html, /<div id="app"><\/div>/);
  assert.match(html, /<script type="module" src="\/static\/kestrel\/main\.js"><\/script>/);
  assert.match(html, /<noscript>/);
});

test('classes and keyframes used by later tasks are defined', () => {
  const all = ['tokens', 'shell', 'widgets'].map((n) => read(`static/kestrel/${n}.css`)).join('\n');
  for (const c of ['hud', 'scan', 'ground', 'rail', 'topbar', 'page', 'gap-card', 'pill-slider', 'mono', 'serif', 'chip']) {
    assert.match(all, new RegExp('\\.' + esc(c) + '(?![\\w-])'), '.' + c);
  }
  for (const k of ['kUp', 'kFade', 'kIn', 'kPop', 'kLive', 'kFlow', 'kRing', 'kTravel']) {
    assert.match(all, new RegExp('@keyframes\\s+' + k + '\\b'), k);
  }
});

// WCAG 2.x contrast ratio
const lin = (c) => {
  const v = c / 255;
  return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4);
};
const lum = (hex) => {
  const h = hex.replace('#', '');
  const [r, g, b] = [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16));
  return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b);
};
const contrast = (a, b) => {
  const [hi, lo] = [lum(a), lum(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
};

test('ink, ink2 and ink3 hold 4.5:1 on surface in both themes', () => {
  for (const [label, body] of [['night', night()], ['day', day()]]) {
    const surface = decl(body, 'surface');
    for (const t of ['ink', 'ink2', 'ink3']) {
      const r = contrast(decl(body, t), surface);
      assert.ok(r >= 4.5, `${label} --${t} on --surface is ${r.toFixed(2)}`);
    }
  }
});
