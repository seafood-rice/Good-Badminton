import test from 'node:test';
import assert from 'node:assert/strict';
import { STRINGS, t, reasonText, findingText, FINDING_CODES, REASON_CODES } from '../../static/kestrel/core/i18n.js';

const hasCJK = (s) => /[一-鿿]/.test(s);
// Keys whose zh value legitimately has no Chinese characters (brand name, key chord).
const SAME_IN_BOTH = new Set(['brand', 'kbd.palette']);

test('zh and en define exactly the same keys', () => {
  assert.deepEqual(Object.keys(STRINGS.zh).sort(), Object.keys(STRINGS.en).sort());
  assert.ok(Object.keys(STRINGS.en).length > 150);
});

test('every value is a non-empty string in both languages', () => {
  for (const lang of ['zh', 'en']) {
    for (const [k, v] of Object.entries(STRINGS[lang])) {
      assert.equal(typeof v, 'string', `${lang}.${k}`);
      assert.ok(v.trim().length > 0, `${lang}.${k} empty`);
    }
  }
});

test('zh strings are really Chinese and en strings contain no Chinese', () => {
  for (const [k, v] of Object.entries(STRINGS.en)) assert.ok(!hasCJK(v), `en.${k} contains CJK`);
  const untranslated = Object.entries(STRINGS.zh)
    // Templates made only of placeholders and punctuation ("{a} · {b}") are language-neutral.
    .filter(([k, v]) => !hasCJK(v) && !SAME_IN_BOTH.has(k) && /[A-Za-z]{2,}/.test(v.replace(/\{\w+\}/g, '')))
    .map(([k]) => k);
  assert.deepEqual(untranslated, []);
});

test('every reason code has text in both languages', () => {
  assert.deepEqual([...REASON_CODES].sort(), [
    'doubles_degraded', 'doubles_error', 'doubles_run', 'few_hits', 'low_stroke_coverage', 'no_benchmark', 'no_data', 'no_hits',
    'no_players', 'no_point_outcome', 'no_positions', 'no_rallies', 'no_results_entered', 'no_strokes_file', 'no_technique',
    'not_doubles', 'signal_degraded', 'signal_swing', 'unscored_technique',
  ]);
  for (const code of REASON_CODES) {
    for (const lang of ['zh', 'en']) {
      const txt = reasonText({ code, params: { names: ['net', 'lift'] } }, lang);
      assert.ok(txt && !/[{}]/.test(txt), `${lang} ${code}: ${txt}`);
      assert.ok(STRINGS[lang]['reason.' + code], `${lang} reason.${code}`);
      const bare = reasonText({ code }, lang);
      assert.ok(bare && !/[{}]/.test(bare), `${lang} ${code} without params: ${bare}`);
    }
  }
});

test('every finding code has a title and detail in both languages', () => {
  assert.deepEqual([...FINDING_CODES].sort(), [
    'doubles_hits_unvalidated', 'doubles_identity_degraded',
    'index_trend', 'rally_estimated', 'recovery_median', 'strokes_coverage_low', 'technique_below_reference',
    'technique_strongest', 'techniques_unscored',
  ]);
  const params = {
    technique_below_reference: { technique: 'drop', score: 66, lo: 70 },
    technique_strongest: { technique: 'smash', score: 82 },
    index_trend: { delta: 2.3, range: '30d' },
    recovery_median: { sec: 1.3 },
    techniques_unscored: { names: ['net', 'lift', 'drive'] },
    strokes_coverage_low: { classified: 12, total: 40 },
    rally_estimated: { signal: 'swing' },
    doubles_identity_degraded: { status: 'degraded_teams' },
    doubles_hits_unvalidated: { named_share: 0.4 },
  };
  for (const code of FINDING_CODES) {
    for (const lang of ['zh', 'en']) {
      const out = findingText({ code, params: params[code] }, lang);
      assert.ok(out.title && !/[{}]/.test(out.title), `${lang} ${code} title: ${out.title}`);
      assert.ok(out.detail && !/[{}]/.test(out.detail), `${lang} ${code} detail: ${out.detail}`);
      assert.ok(STRINGS[lang][`finding.${code}.title`] || STRINGS[lang][`finding.${code}.title_up`], `${lang} ${code} key`);
    }
  }
});

test('findingText interpolates the numbers', () => {
  const f = { code: 'technique_below_reference', params: { technique: 'drop', score: 66, lo: 70 } };
  const en = findingText(f, 'en');
  assert.ok(en.title.includes('66'), en.title);
  assert.ok(en.title.includes('70'), en.title);
  assert.ok(/drop/i.test(en.title));
  assert.ok(en.detail.includes('4'), en.detail);
  const zh = findingText(f, 'zh');
  assert.ok(zh.title.includes('66') && zh.title.includes('70') && zh.title.includes('吊球'), zh.title);
});

test('doubles findings show the localized status and the named share as a percentage', () => {
  const deg = findingText({ code: 'doubles_identity_degraded', params: { status: 'degraded' } }, 'en').title;
  assert.equal(deg, 'Player identities for a doubles match are only partly reliable (Identities degraded).');
  assert.ok(findingText({ code: 'doubles_identity_degraded', params: { status: 'degraded' } }, 'zh').title.includes('身份识别降级'));
  const en = findingText({ code: 'doubles_hits_unvalidated', params: { named_share: 0.4 } }, 'en').title;
  assert.equal(en, 'Who-hit for your doubles matches is unvalidated: 40% of hits were named.');
  const zh = findingText({ code: 'doubles_hits_unvalidated', params: { named_share: 0.4 } }, 'zh').title;
  assert.equal(zh, '双打击球人尚未验证：40%的击球已识别到人。');
  const bad = findingText({ code: 'doubles_hits_unvalidated', params: { named_share: null } }, 'en').title;
  assert.ok(bad.includes('—') && !/[{}]/.test(bad), bad);
});

test('index_trend reads up or down by the sign of delta', () => {
  const up = findingText({ code: 'index_trend', params: { delta: 2.34, range: '30d' } }, 'en').title;
  const down = findingText({ code: 'index_trend', params: { delta: -1.2, range: '7d' } }, 'en').title;
  assert.ok(/up/.test(up) && up.includes('2.3'), up);
  assert.ok(/down/.test(down) && down.includes('1.2') && !down.includes('-'), down);
  const zhDown = findingText({ code: 'index_trend', params: { delta: -1.2, range: 'season' } }, 'zh').title;
  assert.ok(zhDown.includes('下降'), zhDown);
});

test('techniques_unscored lists translated technique names', () => {
  const en = findingText({ code: 'techniques_unscored', params: { names: ['net', 'lift', 'drive'] } }, 'en').title;
  assert.ok(/Net/.test(en) && /Lift/.test(en) && /Drive/.test(en), en);
  const zh = findingText({ code: 'techniques_unscored', params: { names: ['net', 'lift'] } }, 'zh').title;
  assert.ok(zh.includes('网前球') && zh.includes('挑球'), zh);
});

test('unscored_technique reason names the techniques when given', () => {
  const en = reasonText({ code: 'unscored_technique', params: { names: ['net', 'lift'] } }, 'en');
  assert.ok(/Net/.test(en) && /Lift/.test(en), en);
});

test('unknown codes degrade gracefully; null reason is empty', () => {
  assert.equal(reasonText(null, 'en'), '');
  assert.ok(reasonText({ code: 'brand_new_code' }, 'en').length > 0);
  const f = findingText({ code: 'brand_new_code', params: {} }, 'en');
  assert.ok(f.title.length > 0);
  assert.ok(findingText(null, 'en').title.length > 0);
});

test('t(): interpolation, language fallback and missing keys', () => {
  assert.equal(t('technique.smash', 'en'), 'Smash');
  assert.equal(t('technique.smash', 'zh'), '杀球');
  assert.equal(t('technique.smash', 'fr'), 'Smash');
  assert.equal(t('no.such.key', 'en'), 'no.such.key');
  assert.ok(t('scope.matches_count', 'en', { n: 5 }).includes('5'));
  assert.ok(!t('scope.matches_count', 'en', {}).includes('{'));
});

test('seven techniques, status labels, lenses and shell are all present', () => {
  for (const id of ['smash', 'clear', 'drop', 'net', 'lift', 'drive', 'serve']) assert.ok(STRINGS.en['technique.' + id], id);
  for (const k of ['status.analysed', 'status.processing', 'status.new', 'status.needs_review', 'status.ok', 'status.partial', 'status.unavailable',
    'lens.overview', 'lens.technique', 'lens.tactics', 'nav.overview', 'nav.matches', 'nav.players', 'nav.library', 'nav.pipeline', 'nav.settings',
    'section.analyze', 'section.manage', 'gap.title', 'btn.retry', 'btn.new_analysis', 'tile.technique_index', 'tile.rally_win_rate',
    'empty.library.title', 'range.7d', 'range.30d', 'range.season', 'heat.position', 'heat.pull']) {
    assert.ok(STRINGS.en[k] && STRINGS.zh[k], k);
  }
});

test('doubles strings: the chip and the doubles_run reason, in both languages', () => {
  assert.equal(STRINGS.en['doubles.chip'], 'Doubles');
  assert.equal(STRINGS.zh['doubles.chip'], '双打');
  assert.equal(reasonText({ code: 'doubles_run' }, 'en'), 'Doubles match: per-player insights are not available yet.');
  assert.equal(reasonText({ code: 'doubles_run' }, 'zh'), '双打比赛：暂不提供单个球员的分析。');
});
