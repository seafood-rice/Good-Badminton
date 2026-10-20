// Doubles overview rows on the Matches view and the peek seed (doubles addendum 4.2, fix round 1): a no-scope
// doubles row is titled from its `teams` labels and never claims "no athletes mapped yet" when names are known;
// a doubles row opens the peek in the doubles layout; the hits-named tile counts "N of M" over the same rows.
import test from 'node:test';
import assert from 'node:assert/strict';
import { matchRows, rowTitle, peekSeed } from '../../static/kestrel/views/matches.js';
import { doublesTiles } from '../../static/kestrel/views/doubles_model.js';
import { findingText } from '../../static/kestrel/core/i18n.js';

const ROW = { stem: 'd1', doubles: true, format: 'doubles', athlete: null, athlete_player: null, team: null, partner: null,
  opponent: null, teams: ['Alice / Bob', 'J. Tanaka / Dee'], status: 'analysed', side: null };

test('matchRows keeps a doubles row\'s two team labels; junk teams become null', () => {
  const [a, b, c, d] = matchRows({ matches: [
    ROW,
    { ...ROW, stem: 'd2', teams: ['P1 / P2', null] },
    { ...ROW, stem: 'd3', teams: 'Alice' },
    { ...ROW, stem: 'd4', teams: [null, ''] },
  ] });
  assert.deepEqual(a.teams, ['Alice / Bob', 'J. Tanaka / Dee']);
  assert.deepEqual(b.teams, ['P1 / P2', null]);
  assert.equal(c.teams, null);
  assert.equal(d.teams, null);
  assert.equal(matchRows({ matches: [{ stem: 's', teams: ['x', 'y'] }] })[0].teams, undefined);   // singles: no key
});

test('rowTitle: a no-scope doubles row with names lists both teams and a neutral subtitle', () => {
  const [row] = matchRows({ matches: [ROW] });
  assert.deepEqual(rowTitle(row, 'en'), { title: 'Alice / Bob vs. J. Tanaka / Dee', sub: 'Doubles · d1' });
  const zh = rowTitle(row, 'zh');
  assert.equal(zh.sub, '双打 · d1');
  assert.ok(!zh.sub.includes('还没有') && !rowTitle(row, 'en').sub.includes('no athletes'));
  const [half] = matchRows({ matches: [{ ...ROW, teams: ['P1 / Pip', null] }] });
  assert.deepEqual(rowTitle(half, 'en'), { title: 'P1 / Pip vs. Team 2', sub: 'Doubles · d1' });
});

test('rowTitle: identity ids only (or no teams) keep the video name and the no-names subtitle', () => {
  for (const teams of [['P1 / P2', 'P3 / P4'], null]) {
    const [row] = matchRows({ matches: [{ ...ROW, teams }] });
    assert.deepEqual(rowTitle(row, 'en'), { title: 'd1', sub: 'Doubles · no athletes mapped yet' });
  }
});

test('rowTitle: the athlete perspective falls back to the other team\'s label when no opponent is named', () => {
  const [row] = matchRows({ matches: [{ ...ROW, athlete: 'J. Tanaka', athlete_player: 'P3', team: 2, partner: 'Dee',
    opponent: null, teams: ['P1 / P2', 'J. Tanaka / Dee'] }] });
  assert.deepEqual(rowTitle(row, 'en'), { title: 'J. Tanaka / Dee vs. P1 / P2', sub: 'd1' });
});

test('peekSeed: a doubles overview row opens in the doubles layout, titled from its teams', () => {
  const pm = peekSeed(ROW, 'd1', { athletes: [] }, '', 'en');
  assert.equal(pm.doubles, true);
  assert.ok(pm.dbl, 'doubles model');
  assert.equal(pm.dbl.title.text, 'Alice / Bob vs. J. Tanaka / Dee');
  assert.equal(pm.techIndex, null);
  const bare = peekSeed({ stem: 'd1', doubles: true }, 'd1', { athletes: [] }, '', 'en');
  assert.ok(bare.dbl && !bare.dbl.title.text);            // the painter falls back to the video name
  const singles = peekSeed({ stem: 's', side: 'upper', athlete: 'A' }, 's', { athletes: [] }, '', 'en');
  assert.equal(singles.dbl, null);
  assert.equal(peekSeed(null, 's', {}, '', 'en').dbl, null);
});

test('doublesTiles: "N of M" uses hits_named_of, the hits of the rows that report a named count', () => {
  const athlete = { distance_m: 80, avg_speed_mps: 1.2, max_speed_mps: 4, frames: 200, hits: 20, hits_named: 4,
    hits_named_of: 10, named_share: 0.4, matches: 2, grid: null, samples: null };
  const ins = { status: 'ok', value: { matches: 2, athlete, runs: [] }, coverage: null, reason: null };
  const { named } = doublesTiles(ins, 'en');
  assert.equal(named.value, 40);
  assert.equal(named.unit, 'Named: 4 of 10');
  const legacy = doublesTiles({ ...ins, value: { ...ins.value, athlete: { ...athlete, hits_named_of: undefined, named_share: 0.2 } } }, 'en');
  assert.equal(legacy.named.unit, 'Named: 4 of 20');       // older payloads fall back to all hits
});

test('doubles_identity_degraded shows the localized identity status, never the raw token', () => {
  const en = findingText({ code: 'doubles_identity_degraded', params: { status: 'degraded_teams' } }, 'en').title;
  assert.equal(en, 'Player identities for a doubles match are only partly reliable (Teams unknown).');
  const zh = findingText({ code: 'doubles_identity_degraded', params: { status: 'degraded_teams' } }, 'zh').title;
  assert.ok(zh.includes('无法区分队伍') && !zh.includes('degraded_teams'), zh);
  const odd = findingText({ code: 'doubles_identity_degraded', params: { status: 'weird' } }, 'en').title;
  assert.ok(odd.includes('Identity status unknown') && !odd.includes('weird'), odd);
});
