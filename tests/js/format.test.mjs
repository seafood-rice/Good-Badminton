import test from 'node:test';
import assert from 'node:assert/strict';
import { fmtNumber, fmtPercent, fmtDuration, fmtDate, ago, escHTML } from '../../static/kestrel/core/format.js';

const DASH = '—';

test('fmtNumber', () => {
  assert.equal(fmtNumber(73.94, 1), '73.9');
  assert.equal(fmtNumber(1342, 0), '1,342');
  assert.equal(fmtNumber(7, 1), '7.0');
  assert.equal(fmtNumber(null, 1), DASH);
  assert.equal(fmtNumber(undefined), DASH);
  assert.equal(fmtNumber(NaN, 1), DASH);
});

test('fmtPercent takes a 0-1 rate', () => {
  assert.equal(fmtPercent(0.6), '60%');
  assert.equal(fmtPercent(0.605), '61%');
  assert.equal(fmtPercent(0.6049, 1), '60.5%');
  assert.equal(fmtPercent(null), DASH);
});

test('fmtDuration is m:ss', () => {
  assert.equal(fmtDuration(125), '2:05');
  assert.equal(fmtDuration(0), '0:00');
  assert.equal(fmtDuration(59.6), '1:00');
  assert.equal(fmtDuration(3725), '1:02:05');
  assert.equal(fmtDuration(null), DASH);
  assert.equal(fmtDuration(-4), '0:00');
});

test('ago floors seconds into m:ss', () => {
  assert.equal(ago(245), '4:05');
  assert.equal(ago(59.9), '0:59');
  assert.equal(ago(-3), '0:00');
  assert.equal(ago(NaN), '0:00');
});

test('fmtDate by language', () => {
  assert.equal(fmtDate('2026-10-11', 'en'), '11 Oct 2026');
  assert.equal(fmtDate('2026-10-11', 'zh'), '2026年10月11日');
  assert.equal(fmtDate('2026-10-11T08:30:00Z', 'en'), '11 Oct 2026');
  assert.equal(fmtDate('2026-10-11', 'en', { year: false }), '11 Oct');
  assert.equal(fmtDate('2026-10-11', 'zh', { year: false }), '10月11日');
  assert.equal(fmtDate('', 'en'), DASH);
  assert.equal(fmtDate(null, 'en'), DASH);
  assert.equal(fmtDate('not a date', 'en'), DASH);
  assert.equal(fmtDate('2026-13-40', 'en'), DASH);
  assert.equal(fmtDate('2026-02-30', 'en'), DASH);
});

test('escHTML leaves no raw markup characters', () => {
  const out = escHTML('<a href="x">&\'');
  for (const ch of ['<', '>', '"', "'"]) assert.ok(!out.includes(ch), ch);
  assert.ok(!/&(?!amp;|lt;|gt;|quot;|#39;)/.test(out));
  assert.equal(out, '&lt;a href=&quot;x&quot;&gt;&amp;&#39;');
  assert.equal(escHTML(null), '');
  assert.equal(escHTML(5), '5');
});
