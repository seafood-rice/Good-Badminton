import test from 'node:test';
import assert from 'node:assert/strict';
import { installEnv, removeEnv, everything } from './fake_dom.mjs';
import { landingModel, poolLandings, visibleDots, COURT_W, COURT_L } from '../../static/kestrel/views/landing_model.js';
import { landingCard } from '../../static/kestrel/widgets/landing.js';

const PAYLOAD = {
  landings: [
    { rally: 1, time: 2, hitter: 'lower', stroke: 'clear', x: 3, y: 2, side: 'upper', depth: 'rear', source: 'shuttle_feet', out: false },
    { rally: 1, time: 3, hitter: 'upper', stroke: 'drop', x: 1, y: 8, side: 'lower', depth: 'front', source: 'feet', out: false },
    { rally: 1, time: 4, hitter: 'lower', stroke: 'smash', x: -4, y: 20, side: 'lower', depth: null, source: 'floor', out: true },
    { hitter: 'nobody', x: 1, y: 1 }, { hitter: 'lower', x: 'a', y: 1 }, null,
  ],
  bands: { lower: { n: 2, front: 0, mid: 1, rear: 1 }, upper: { n: 0 } },
};

test('landing model: keeps usable dots, clamps far ones to the margin, shares per side', () => {
  const m = landingModel(PAYLOAD, { names: { lower: 'Ann' } });
  assert.equal(m.available, true);
  assert.equal(m.total, 3);
  assert.deepEqual([m.dots[2].x, m.dots[2].y], [-1, COURT_L + 1]);
  assert.equal(m.dots[1].source, 'feet');
  const lower = m.bands.find((b) => b.side === 'lower');
  assert.deepEqual(lower.shares, { front: 0, mid: 0.5, rear: 0.5 });
  assert.equal(lower.name, 'Ann');
  assert.equal(m.bands.find((b) => b.side === 'upper').shares, null);
  assert.equal(visibleDots(m, 'upper').length, 1);
  assert.equal(visibleDots(m, 'both').length, 3);
});

test('landing model: reasons when nothing can be drawn', () => {
  assert.equal(landingModel({ landings: [] }).reason, 'no_landings');
  assert.equal(landingModel(null).reason, 'no_data');
  assert.equal(landingModel('junk').available, false);
});

test('pool: a swapped part is turned round and its sides exchanged', () => {
  const a = { landings: [{ hitter: 'lower', side: 'upper', x: 1, y: 2 }], bands: { lower: { n: 1, rear: 1 }, upper: {} } };
  const b = { landings: [{ hitter: 'lower', side: 'upper', x: 1, y: 2 }], bands: { lower: { n: 1, mid: 1 }, upper: {} } };
  const pooled = poolLandings([a, b, null], [false, true, false]);
  assert.equal(pooled.landings.length, 2);
  const turned = pooled.landings[1];
  assert.equal(turned.hitter, 'upper');
  assert.equal(turned.side, 'lower');
  assert.ok(Math.abs(turned.x - (COURT_W - 1)) < 1e-9 && Math.abs(turned.y - (COURT_L - 2)) < 1e-9);
  assert.deepEqual(pooled.bands.lower, { n: 1, front: 0, mid: 0, rear: 1 });
  assert.deepEqual(pooled.bands.upper, { n: 1, front: 0, mid: 1, rear: 0 });
  assert.equal(poolLandings([null, {}], [false, false]), null);
});

test('landing card: marks per source, the filter narrows them, shares listed', () => {
  installEnv();
  try {
    const card = landingCard({ lang: 'en', model: landingModel(PAYLOAD), names: { lower: 'Ann', upper: 'Bea' } });
    const marks = () => everything(card).filter((e) => e.classList && e.classList.contains('ws-land-dot'));
    assert.equal(marks().length, 3);
    assert.equal(marks().filter((e) => String(e.tagName).toLowerCase() === 'rect').length, 1);
    assert.equal(marks().filter((e) => e.classList.contains('hollow')).length, 1);
    const upper = everything(card).find((e) => e.tagName === 'BUTTON' && e.getAttribute('data-filter') === 'upper');
    upper.dispatch('click');
    assert.equal(marks().length, 1);
    assert.equal(upper.getAttribute('aria-pressed'), 'true');
    const text = everything(card).map((e) => e.textContent || '').join(' ');
    assert.ok(text.includes('rear 50%') && text.includes('Bea'));
  } finally { removeEnv(); }
});

test('landing card: loading and empty states say so', () => {
  installEnv();
  try {
    const loading = landingCard({ lang: 'en', model: null });
    assert.ok(everything(loading).some((e) => e.textContent === 'Working out landings…'));
    const empty = landingCard({ lang: 'zh', model: landingModel({ landings: [] }) });
    assert.ok(everything(empty).some((e) => e.classList && e.classList.contains('ws-land-empty')));
  } finally { removeEnv(); }
});

test('landing card: a stroke without a translation reads as its id, not a key', () => {
  installEnv();
  try {
    const model = landingModel({ landings: [{ hitter: 'lower', stroke: 'unknown', x: 3, y: 2, source: 'feet' }], bands: {} });
    const card = landingCard({ lang: 'en', model });
    const titles = everything(card).filter((e) => String(e.tagName).toLowerCase() === 'title').map((e) => e.textContent);
    assert.deepEqual(titles, ['unknown · receiver feet only']);
  } finally { removeEnv(); }
});
