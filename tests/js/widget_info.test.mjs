// Widget help ("i" button + dialog): every card and tile id has its three sections in both languages,
// and the ids cover what the Overview actually renders.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { STRINGS } from '../../static/kestrel/core/i18n.js';
import { INFO_IDS, INFO_SECTIONS, hasInfo } from '../../static/kestrel/widgets/info.js';

const src = (p) => readFileSync(new URL('../../static/kestrel/' + p, import.meta.url), 'utf8');

test('every info id has what / read / data text in zh and en', () => {
  for (const id of INFO_IDS) {
    for (const lang of ['zh', 'en']) {
      for (const s of INFO_SECTIONS) assert.ok(STRINGS[lang][`info.${id}.${s}`], `${lang} info.${id}.${s}`);
      assert.ok(hasInfo(id, lang), `${lang} ${id}`);
    }
  }
  for (const k of ['info.open', 'info.close', ...INFO_SECTIONS.map((s) => 'info.section.' + s)]) {
    assert.ok(STRINGS.zh[k] && STRINGS.en[k], k);
  }
});

test('no orphan info keys: each one belongs to a listed id', () => {
  const allowed = new Set(['info.open', 'info.close', ...INFO_SECTIONS.map((s) => 'info.section.' + s)]);
  for (const id of INFO_IDS) for (const s of INFO_SECTIONS) allowed.add(`info.${id}.${s}`);
  const orphans = Object.keys(STRINGS.en).filter((k) => k.startsWith('info.') && !allowed.has(k));
  assert.deepEqual(orphans, []);
});

test('unknown or missing ids get no button', () => {
  assert.equal(hasInfo('nope', 'en'), false);
  assert.equal(hasInfo(undefined, 'en'), false);
  assert.equal(hasInfo('', 'zh'), false);
});

test('every card id passed to frame.build and every tile id has help text', () => {
  const cards = new Set();
  for (const f of ['courtmap', 'radar', 'trend', 'findings', 'matches', 'doubles', 'pipeline']) {
    const m = src(`widgets/${f}.js`).match(/frame\.build\(\{\s*id: '([a-z_]+)'/);
    assert.ok(m, `${f}.js frame.build id`);
    cards.add(m[1]);
  }
  const tiles = new Set();
  for (const f of ['views/overview.js', 'views/doubles_model.js']) {
    for (const m of src(f).matchAll(/(?:baseTile|gapTile|shareTile)\('([a-z_]+)'|\bid: '([a-z_]+)', kind:/g)) tiles.add(m[1] || m[2]);
  }
  assert.ok(tiles.size >= 12, `found ${tiles.size} tile ids`);
  const missing = [...cards, ...tiles].filter((id) => !INFO_IDS.includes(id));
  assert.deepEqual(missing, []);
});
