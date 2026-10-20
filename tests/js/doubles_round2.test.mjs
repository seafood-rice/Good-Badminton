// Doubles fix round 2: count wording that reads right for one match/hit, winning-team labels that follow the
// identity pickers, and a workspace Players panel whose hit counts come from the same hit list as the strip.
import test from 'node:test';
import assert from 'node:assert/strict';
import { STRINGS, t } from '../../static/kestrel/core/i18n.js';
import { winnerTeamOptions } from '../../static/kestrel/views/matches.js';
import { doublesWorkspaceModel } from '../../static/kestrel/views/doubles_model.js';

test('doubles count strings never read "1 doubles matches" or "1 hits"', () => {
  for (const lang of ['en', 'zh']) {
    for (const key of Object.keys(STRINGS[lang]).filter((k) => k.startsWith('dbl.'))) {
      const out = t(key, lang, { n: 1, named: 1, total: 1, title: 'Rally 1', pct: '100%', i: 1, stem: 'v', team: 'Team 1', names: 'A', time: '0:01', player: 'P1' });
      assert.doesNotMatch(out, /\b1 (doubles matches|hits)\b/, `${lang} ${key}: ${out}`);
    }
  }
  assert.equal(t('dbl.widget.sub', 'en', { n: 1 }), 'Doubles matches in scope: 1');
  assert.equal(t('dbl.widget.sub', 'zh', { n: 1 }), '范围内的双打比赛：1');
});

const PLAYERS = [
  { id: 'P1', team: 1, label: 'Alice' }, { id: 'P2', team: 1, label: 'P2' },
  { id: 'P3', team: 2, label: 'Cara' }, { id: 'P4', team: 2, label: 'P4' },
];
const ATHLETES = { athletes: [{ id: 'a1', name: 'J. Tanaka' }] };

test('winnerTeamOptions: picker names first (athlete, then typed name), then players.json names, then ids', () => {
  const opts = winnerTeamOptions(PLAYERS, { P1: { athlete_id: 'a1', name: '' }, P4: { athlete_id: '', name: ' Dee ' } }, ATHLETES, 'en');
  assert.deepEqual(opts, [{ value: '1', label: 'Team 1 · J. Tanaka / P2' }, { value: '2', label: 'Team 2 · Cara / Dee' }]);
  const plain = winnerTeamOptions(PLAYERS, {}, ATHLETES, 'en');
  assert.deepEqual(plain.map((o) => o.label), ['Team 1 · Alice / P2', 'Team 2 · Cara / P4']);
  const unknownAthlete = winnerTeamOptions(PLAYERS, { P1: { athlete_id: 'a_gone' } }, ATHLETES, 'en');
  assert.equal(unknownAthlete[0].label, 'Team 1 · Alice / P2');
  const noTeams = winnerTeamOptions([{ id: 'P1', team: null, label: 'Alice' }], {}, ATHLETES, 'en');
  assert.deepEqual(noTeams.map((o) => o.label), ['Team 1', 'Team 2']);
  assert.deepEqual(winnerTeamOptions(null, null, null, 'en').map((o) => o.value), ['1', '2']);
});

test('doublesWorkspaceModel: Players panel hits come from the loaded hit list, like the strip', () => {
  const playersFile = { status: 'ok', players: [
    { id: 'P1', team: 1, hits: 0, hits_named: 0 }, { id: 'P2', team: 1, hits: 0, hits_named: 0 },
    { id: 'P3', team: 2, hits: 0, hits_named: 0 }, { id: 'P4', team: 2, hits: 7, hits_named: 7 },
  ] };
  const hits = { hits: [
    { frame: 10, time_sec: 0.3, hitter_id: 'P1', hitter_evidence: 'shuttle' },
    { frame: 20, time_sec: 0.7, hitter_id: 'P1', hitter_evidence: 'single_candidate' },
    { frame: 30, time_sec: 1.0, hitter_id: 'P3', hitter_evidence: 'swing' },
    { frame: 40, time_sec: 1.3, hitter_id: null, hitter_evidence: 'none' },
  ] };
  const m = doublesWorkspaceModel({ playersFile, hits, lang: 'en' });
  const by = Object.fromEntries(m.players.map((p) => [p.id, [p.hits, p.hits_named]]));
  assert.deepEqual(by, { P1: [2, 1], P2: [0, 0], P3: [1, 1], P4: [0, 0] });
  for (const p of m.players) {
    const s = m.whoHit.perPlayer[p.id] || { hits: 0, named: 0 };
    assert.deepEqual([p.hits, p.hits_named], [s.hits, s.named], p.id);
  }
  // no hit list: players.json counts stand
  const none = doublesWorkspaceModel({ playersFile, hits: null, lang: 'en' });
  assert.deepEqual(none.players.find((p) => p.id === 'P4').hits, 7);
});
