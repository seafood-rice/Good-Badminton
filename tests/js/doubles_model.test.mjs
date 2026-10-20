// Pure doubles view-models (spec 2026-10-20 section 5): identity labels and colours, team titles, the who-hit
// strip, the Overview "Doubles players" rows and tiles, the peek and workspace models. Honesty rules pinned here:
// every who-hit figure is flagged unvalidated and no doubles model ever carries a score or technique number.
import test from 'node:test';
import assert from 'node:assert/strict';
import {
  IDENTITY_IDS, IDENTITY_COLORS, identityColor, doublesPlayers, playerLabel, teamLabel, teamsTitle, identityOptionLabel,
  confidenceText, whoHitModel, doublesRows, doublesTiles, doublesVideoFiles, doublesWorkspaceModel, doublesPeekModel,
  hitsInWindow,
} from '../../static/kestrel/views/doubles_model.js';
import { STRINGS, t, reasonText } from '../../static/kestrel/core/i18n.js';

const PLAYERS = [
  { id: 'P3', team: 2, name: 'Cara', coverage: 0.91, frames: 900, distance_m: 410.4, avg_speed_mps: 1.42, max_speed_mps: 4.8, hits: 20, hits_named: 15 },
  { id: 'P1', team: 1, name: 'Alice', coverage: 0.834, frames: 880, distance_m: 380, avg_speed_mps: 1.3, max_speed_mps: 4.1, hits: 18, hits_named: 12 },
  { id: 'P2', team: 1, name: null, coverage: 0.7, frames: 700, distance_m: 300, avg_speed_mps: 1.1, max_speed_mps: 3.9, hits: 10, hits_named: 4 },
  { id: 'P4', team: 2, name: 'Dan', coverage: 0.66, frames: 600, distance_m: 250, avg_speed_mps: 1.0, max_speed_mps: 3.5, hits: 9, hits_named: 5 },
];

const ok = (value) => ({ status: 'ok', value, coverage: null, reason: null });
const partial = (value, code, params = {}) => ({ status: 'partial', value, coverage: null, reason: { code, params } });
const unavailable = (code, params = {}) => ({ status: 'unavailable', value: null, coverage: null, reason: { code, params } });

// Walks every key of a model: a doubles model must never carry a score or a technique number.
function assertNoScores(model, where) {
  const bad = /score|tech|index|stroke/i;
  const walk = (v, path) => {
    if (Array.isArray(v)) v.forEach((x, i) => walk(x, `${path}[${i}]`));
    else if (v && typeof v === 'object') {
      for (const [k, x] of Object.entries(v)) {
        assert.ok(!bad.test(k), `${where}: unexpected key ${path}.${k}`);
        walk(x, `${path}.${k}`);
      }
    }
  };
  walk(model, '');
}

function strings(v, out = [], path = '') {
  if (typeof v === 'string') out.push([path, v]);
  else if (Array.isArray(v)) v.forEach((x, i) => strings(x, out, `${path}[${i}]`));
  else if (v && typeof v === 'object') for (const [k, x] of Object.entries(v)) strings(x, out, path ? `${path}.${k}` : k);
  return out;
}

function assertResolved(model, lang) {
  for (const [path, s] of strings(model)) {
    assert.ok(!/[{}]/.test(s), `${lang} ${path}: unfilled placeholder in "${s}"`);
    assert.ok(!/^(dbl|stage|reason|unit|record|mv)\.[a-z0-9_.]+$/.test(s), `${lang} ${path}: raw key "${s}"`);
  }
}

// ---------- identities, colours, labels ----------

test('identity colours are the identity video four fixed colours (doubles_render BGR as RGB)', () => {
  assert.deepEqual(IDENTITY_IDS, ['P1', 'P2', 'P3', 'P4']);
  assert.deepEqual({ ...IDENTITY_COLORS }, { P1: '#008CFF', P2: '#78DCFF', P3: '#FF5000', P4: '#FFB478' });
  assert.equal(identityColor('P3'), '#FF5000');
  for (const bad of ['P5', 'p1', '', null, undefined, 3, { id: 'P1' }]) assert.equal(identityColor(bad), null, String(bad));
});

test('doublesPlayers: hostile entries are dropped or nulled, never fabricated', () => {
  assert.deepEqual(doublesPlayers('nope'), []);
  assert.deepEqual(doublesPlayers({ P1: {} }), []);
  const out = doublesPlayers([
    null, 7, 'P1', ['P1'],
    { id: 'P1', team: 1, name: '  Alice ', coverage: 0.8, distance_m: NaN, hits: 4, hits_named: 9 },
    { id: 'P1', team: 2, name: 'Dup' }, // duplicate id: the first wins
    { id: 'P9', team: 3, name: 7, coverage: 4, avg_speed_mps: 1e308, frames: -2, hits: 1.5 },
    { id: 12, team: 1 }, { team: 1 },
  ]);
  assert.deepEqual(out.map((p) => p.id), ['P1', 'P9']);
  const [p1, p9] = out;
  assert.equal(p1.name, 'Alice');
  assert.equal(p1.team, 1);
  assert.equal(p1.distance_m, null);
  assert.equal(p1.hits, 4);
  assert.equal(p1.hits_named, null); // more named hits than hits is inconsistent
  assert.equal(p9.team, null);
  assert.equal(p9.name, null);
  assert.equal(p9.coverage, null);
  assert.equal(p9.avg_speed_mps, null);
  assert.equal(p9.frames, null);
  assert.equal(p9.hits, null);
  // a long list is bounded
  const many = doublesPlayers(Array.from({ length: 500 }, (_, i) => ({ id: 'P' + (i % 90), team: 1 })));
  assert.ok(many.length <= 8);
});

test('doublesPlayers orders team 1, team 2, then unknown teams, by identity number', () => {
  const out = doublesPlayers([...PLAYERS, { id: 'P10', team: null }, { id: 'P5', team: 1 }]);
  assert.deepEqual(out.map((p) => p.id), ['P1', 'P2', 'P5', 'P3', 'P4', 'P10']);
});

test('playerLabel: mapped athlete, then the identity name, then the identity id', () => {
  assert.equal(playerLabel({ id: 'P1', name: 'Alice', athlete: 'A. Smith' }), 'A. Smith');
  assert.equal(playerLabel({ id: 'P1', name: 'Alice', athlete: null }), 'Alice');
  assert.equal(playerLabel({ id: 'P2', name: null }), 'P2');
  assert.equal(playerLabel(null), '—');
});

test('teamLabel: 2 + 2 names, identity fallback, degraded teams', () => {
  const ps = doublesPlayers(PLAYERS);
  assert.equal(teamLabel(1, ps, 'en'), 'Alice / P2');
  assert.equal(teamLabel({ id: 2, players: ['P3', 'P4'] }, ps, 'en'), 'Cara / Dan');
  // a team listing an identity that has no player entry falls back to the id
  assert.equal(teamLabel({ id: 2, players: ['P3', 'P7'] }, ps, 'en'), 'Cara / P7');
  assert.equal(teamLabel(1, [], 'en'), 'Team 1');
  assert.equal(teamLabel(1, [], 'zh'), t('dbl.team', 'zh', { n: 1 }));
  assert.equal(teamLabel(null, ps, 'en'), 'Team unknown');
  assert.equal(teamLabel(3, ps, 'en'), 'Team unknown');
  assert.equal(teamLabel(null, ps, 'zh'), t('dbl.team_unknown', 'zh'));
});

test('teamsTitle: "A / B vs. C / D", the athlete team first, identity list when teams are unknown', () => {
  const ps = doublesPlayers(PLAYERS);
  assert.deepEqual(teamsTitle(ps, 'en'), { a: 'Alice / P2', b: 'Cara / Dan', vs: true, text: 'Alice / P2 vs. Cara / Dan' });
  assert.equal(teamsTitle(ps, 'en', 2).text, 'Cara / Dan vs. Alice / P2');
  const noTeams = doublesPlayers(PLAYERS.map((p) => ({ ...p, team: null })));
  const nt = teamsTitle(noTeams, 'en');
  assert.equal(nt.vs, false);
  assert.equal(nt.text, 'Alice · P2 · Cara · Dan');
  assert.equal(teamsTitle([], 'en').text, '');
});

test('identityOptionLabel: "P1 · Team 1 · coverage 83%" and its degraded forms', () => {
  const [p1] = doublesPlayers(PLAYERS);
  assert.equal(identityOptionLabel(p1, 'en'), 'P1 · Team 1 · coverage 83%');
  assert.equal(identityOptionLabel({ ...p1, team: null }, 'en'), 'P1 · Team unknown · coverage 83%');
  assert.equal(identityOptionLabel({ ...p1, coverage: null }, 'en'), 'P1 · Team 1');
  assert.equal(identityOptionLabel({ id: 'P4' }, 'en'), 'P4 · Team unknown');
  assert.match(identityOptionLabel(p1, 'zh'), /^P1 · .+ · .*83%$/);
});

test('confidenceText: a clamped percentage, a dash when missing', () => {
  assert.equal(confidenceText(0.834), '83%');
  assert.equal(confidenceText(1.7), '100%');
  assert.equal(confidenceText(-1), '0%');
  for (const bad of [null, undefined, NaN, '0.5', Infinity]) assert.equal(confidenceText(bad), '—');
});

// ---------- who hit ----------

const HITS = {
  hitter_attribution: { validated: true }, // a payload claiming validation is not believed
  hits: [
    { frame: 500, time_sec: 10, hitter: 'upper', hitter_id: 'P3', hitter_team: 2, hitter_confidence: 0.91, hitter_evidence: 'shuttle' },
    { frame: 100, time_sec: 2, hitter: 'lower', hitter_id: 'P1', hitter_team: 1, hitter_confidence: 0.5, hitter_evidence: 'single_candidate' },
    { frame: 250, hitter: 'lower', hitter_id: null, hitter_team: null, hitter_confidence: 0.2, hitter_evidence: 'none' },
    { frame: 300, time_sec: 6, hitter: 'upper', hitter_id: 'P9', hitter_team: 2, hitter_confidence: 0.8, hitter_evidence: 'swing' },
    { frame: 350, time_sec: 7, hitter: 'lower', hitter_id: 42, hitter_team: 'x', hitter_confidence: 'high', hitter_evidence: 'magic' },
    { frame: 400, time_sec: 8, hitter: 'lower', hitter_id: 'P2', hitter_team: 1, hitter_confidence: 0.75, hitter_evidence: 'swing' },
    null, 'junk', [1, 2],
  ],
};

test('whoHitModel: sorted by time, coloured per identity, muted when unnamed or the only candidate', () => {
  const m = whoHitModel(HITS, doublesPlayers(PLAYERS), { fps: 50 });
  assert.equal(m.validated, false);
  assert.equal(m.total, 6);
  assert.deepEqual(m.items.map((x) => x.time), [2, 5, 6, 7, 8, 10]); // frame 250 / 50 fps = 5 s
  assert.deepEqual(m.items.map((x) => x.i), [1, 2, 3, 4, 5, 6]);
  const [single, none, unknown, wrong, swing, shuttle] = m.items;
  assert.equal(single.hitterId, 'P1');
  assert.equal(single.muted, true);
  assert.equal(single.named, false);
  assert.equal(single.color, '#008CFF');
  assert.equal(single.confidenceText, '50%');
  assert.equal(none.hitterId, null);
  assert.equal(none.muted, true);
  assert.equal(none.color, null);
  assert.equal(none.team, null);
  // an id that is not one of the run's identities is not a name
  assert.equal(unknown.hitterId, null);
  assert.equal(unknown.muted, true);
  assert.equal(unknown.team, null);
  assert.equal(wrong.hitterId, null);
  assert.equal(wrong.evidence, 'none');
  assert.equal(wrong.confidence, null);
  assert.equal(wrong.confidenceText, '—');
  assert.equal(swing.named, true);
  assert.equal(swing.muted, false);
  assert.equal(swing.team, 1);
  assert.equal(swing.color, '#78DCFF');
  assert.equal(shuttle.team, 2);
  assert.equal(shuttle.evidence, 'shuttle');
  assert.equal(m.named, 2);
  assert.equal(m.singleCandidate, 1);
  assert.equal(m.namedShare, 2 / 6);
  assert.deepEqual(m.perPlayer.P1, { hits: 1, named: 0 });
  assert.deepEqual(m.perPlayer.P3, { hits: 1, named: 1 });
});

test('whoHitModel: no players list accepts only P1-P4; missing time sorts last; empty input', () => {
  const m = whoHitModel({ hits: [{ frame: 10, hitter_id: 'P4', hitter_evidence: 'swing' }, { time_sec: 1, hitter_id: 'P7', hitter_evidence: 'swing' }] }, [], {});
  assert.equal(m.items[0].hitterId, null); // P7 with time 1 s first
  assert.equal(m.items[1].hitterId, 'P4');
  assert.equal(m.items[1].time, null); // frame without fps: no time
  const empty = whoHitModel(null, null);
  assert.deepEqual([empty.total, empty.named, empty.namedShare, empty.items.length], [0, 0, null, 0]);
  assert.equal(whoHitModel([{ time_sec: 3, hitter_id: 'P1', hitter_evidence: 'shuttle' }], []).total, 1); // bare array
});

test('whoHitModel: a huge hit list is bounded', () => {
  const hits = Array.from({ length: 60000 }, (_, i) => ({ frame: i, time_sec: i / 50, hitter_id: 'P1', hitter_evidence: 'swing' }));
  const m = whoHitModel({ hits }, doublesPlayers(PLAYERS));
  assert.ok(m.items.length <= 20000);
  assert.equal(m.truncated, true);
});

test('hitsInWindow keeps hits within the rally window plus the 0.5 s pad', () => {
  const { items } = whoHitModel(HITS, doublesPlayers(PLAYERS), { fps: 50 });
  assert.deepEqual(hitsInWindow(items, { start: 5.6, end: 7.2 }).map((x) => x.time), [6, 7]);
  assert.deepEqual(hitsInWindow(items, { start: 5.4, end: 7.6 }).map((x) => x.time), [5, 6, 7, 8]);
  assert.equal(hitsInWindow(items, null).length, items.length);
});

// ---------- Overview: Doubles players ----------

function overviewDoubles(extra = {}) {
  return ok({
    matches: 3,
    athlete: { distance_m: 790.4, avg_speed_mps: 1.36, max_speed_mps: 4.8, frames: 1780, hits: 38, hits_named: 27, named_share: 0.7108, matches: 2, grid: null, samples: 0 },
    runs: [
      { stem: 'old', date: '2026-09-01', date_source: 'filename', status: 'ok', validated: false, teams: [], players: PLAYERS },
      { stem: 'new', date: '2026-10-11', date_source: 'mtime', status: 'degraded_teams', validated: true,
        players: PLAYERS.map((p) => ({ ...p, team: null, technique_index: 88, score: 9, athlete: p.id === 'P3' ? 'S. Ito' : null })) },
      { stem: 'mid', date: '2026-09-20', date_source: 'record', status: 'error', players: [] },
      { stem: 'old', date: '2026-09-01', status: 'ok' }, // duplicate stem
      { stem: '', status: 'ok' }, null,
    ],
    ...extra,
  });
}

test('doublesRows: newest run first, players by team, unvalidated always, status text kept', () => {
  const m = doublesRows(overviewDoubles(), 'en');
  assert.equal(m.show, true);
  assert.equal(m.matches, 3);
  assert.equal(m.unvalidated, true);
  assert.deepEqual(m.runs.map((r) => r.stem), ['new', 'mid', 'old']);
  const [nw, mid, old] = m.runs;
  assert.equal(nw.unvalidated, true); // validated: true in the payload is not believed
  assert.equal(nw.status, 'degraded_teams');
  assert.equal(nw.statusText, t('dbl.status.degraded_teams', 'en'));
  assert.ok(nw.detail.length > 10);
  assert.equal(nw.tone, 'mid');
  assert.equal(nw.dateApprox, true);
  assert.equal(nw.players.find((p) => p.id === 'P3').label, 'S. Ito');
  assert.equal(nw.players.find((p) => p.id === 'P3').teamText, 'Team unknown');
  assert.equal(mid.tone, 'bad');
  assert.equal(mid.players.length, 0);
  assert.equal(old.status, 'ok');
  assert.equal(old.detail, '');
  assert.equal(old.title, 'Alice / P2 vs. Cara / Dan');
  assert.deepEqual(old.players.map((p) => p.id), ['P1', 'P2', 'P3', 'P4']);
  const p1 = old.players[0];
  assert.equal(p1.color, '#008CFF');
  assert.equal(p1.distanceText, '380 m');
  assert.equal(p1.avgSpeedText, '1.3');
  assert.equal(p1.maxSpeedText, '4.1');
  assert.equal(p1.hitsText, '18');
  assert.equal(p1.namedText, '67%');
  assert.equal(m.athlete.distanceText, '790 m');
  assert.equal(m.athlete.namedText, '71%');
  assert.equal(m.athlete.matches, 2);
  assertNoScores(m, 'doublesRows');
  for (const lang of ['en', 'zh']) assertResolved(doublesRows(overviewDoubles(), lang), lang);
});

test('doublesRows: hidden without doubles matches; an all-degraded run keeps the reason', () => {
  assert.equal(doublesRows(undefined, 'en').show, false);
  assert.equal(doublesRows(unavailable('no_data'), 'en').show, false);
  assert.equal(doublesRows(ok({ matches: 0, athlete: null, runs: [] }), 'en').show, false);
  const deg = partial({ matches: 1, athlete: null, runs: [{ stem: 'd', date: '2026-10-01', status: 'degraded', players: PLAYERS }] }, 'doubles_degraded', { status: 'degraded' });
  const m = doublesRows(deg, 'en');
  assert.equal(m.show, true);
  assert.equal(m.status, 'partial');
  assert.ok(m.reason.length > 0);
  assert.equal(m.reason, reasonText({ code: 'doubles_degraded', params: { status: 'degraded' } }, 'en') || t('reason.unknown', 'en'));
  assert.equal(m.runs[0].status, 'degraded');
  assert.notEqual(m.runs[0].status, 'ok');
  assert.ok(m.runs[0].detail.length > 0);
  assert.equal(m.athlete, null);
  const zh = doublesRows(deg, 'zh');
  assert.ok(zh.reason.length > 0);
  assertResolved(zh, 'zh');
});

test('doublesRows: a malformed run status is "unknown", never ok; long lists are capped', () => {
  const runs = Array.from({ length: 9 }, (_, i) => ({ stem: 's' + i, date: `2026-10-0${i + 1}`, status: i === 0 ? 'great' : 'ok', players: [] }));
  const m = doublesRows(ok({ matches: 9, athlete: null, runs }), 'en');
  assert.ok(m.runs.length <= 6);
  assert.equal(m.more, 9 - m.runs.length);
  const s0 = doublesRows(ok({ matches: 1, athlete: null, runs: [runs[0]] }), 'en').runs[0];
  assert.equal(s0.status, 'unknown');
  assert.equal(s0.statusText, t('dbl.status.unknown', 'en'));
});

test('doublesTiles: distance and hits-named tiles from the athlete summary, flagged unvalidated', () => {
  const { distance, named } = doublesTiles(overviewDoubles(), 'en');
  assert.equal(distance.id, 'doubles_distance');
  assert.equal(distance.kind, 'number');
  assert.equal(distance.value, 790.4);
  assert.equal(distance.label, 'Distance covered');
  assert.match(distance.note, /2 doubles matches/);
  assert.equal(named.id, 'doubles_hits_named');
  assert.equal(named.kind, 'ring');
  assert.equal(named.label, 'Hits named');
  assert.equal(named.value, 71.1);
  assert.match(named.unit, /27 of 38/);
  assert.equal(named.tag, t('dbl.tag_unvalidated', 'en'));
  const none = doublesTiles(ok({ matches: 2, athlete: null, runs: [] }), 'en');
  assert.deepEqual(none, { distance: null, named: null });
  // a summary without hits gives no hits tile rather than 0%
  const noHits = doublesTiles(ok({ matches: 1, athlete: { distance_m: 10, hits: 0, hits_named: 0, named_share: null, matches: 1 }, runs: [] }), 'en');
  assert.equal(noHits.named, null);
  assert.equal(noHits.distance.value, 10);
  for (const lang of ['en', 'zh']) assertResolved(doublesTiles(overviewDoubles(), lang), lang);
});

// ---------- workspace and peek ----------

test('doublesVideoFiles: the identity video only when its status is ok, the annotated video otherwise', () => {
  assert.deepEqual(doublesVideoFiles('m 1', { status: 'ok', file: '../../evil.mp4' }),
    ['detect_m 1_players.mp4', 'detect_m 1.mp4', 'detect_m 1_h264.mp4']);
  for (const iv of [{ status: 'failed', file: 'detect_m 1_players.mp4' }, { status: 'skipped' }, null, 'ok']) {
    assert.deepEqual(doublesVideoFiles('m 1', iv), ['detect_m 1.mp4', 'detect_m 1_h264.mp4']);
  }
});

const PLAYERS_FILE = {
  match_format: 'doubles', status: 'ok', reason: 'C:\\secret\\path', players: PLAYERS,
  teams: [{ id: 1, players: ['P1', 'P2'] }, { id: 2, players: ['P3', 'P4'] }], validated: false,
  identity_video: { status: 'ok', file: 'detect_d_players.mp4', reason: null },
};
const DOUBLES_RECORD = {
  date: '2026-10-11', format: 'doubles',
  players: { P1: { athlete_id: 'a1' }, P2: { name: 'Bea' }, P3: null },
  result: { winner_team: 2, sets: [[21, 18], [19, 21], [15, 21]] },
};
const ATHLETES = { athletes: [{ id: 'a1', name: 'J. Tanaka' }] };

test('doublesWorkspaceModel: players with mapped names, identity video, who hit, oriented result', () => {
  const m = doublesWorkspaceModel({ playersFile: PLAYERS_FILE, hits: HITS, insights: null, fps: 50, record: DOUBLES_RECORD, athletes: ATHLETES, athleteId: 'a1', lang: 'en' });
  assert.equal(m.status, 'ok');
  assert.equal(m.identityVideoOk, true);
  assert.deepEqual(m.players.map((p) => p.label), ['J. Tanaka', 'Bea', 'Cara', 'Dan']);
  assert.equal(m.title.text, 'J. Tanaka / Bea vs. Cara / Dan');
  assert.equal(m.athletePlayer, 'P1');
  assert.equal(m.team, 1);
  assert.equal(m.result, 'L');
  assert.deepEqual(m.sets, [[21, 18], [19, 21], [15, 21]]); // own team is team 1: unchanged
  assert.equal(m.whoHit.total, 6);
  assert.equal(m.unvalidated, true);
  assert.ok(!JSON.stringify(m).includes('secret')); // the pipeline's free-text reason is never echoed
  assertNoScores(m, 'doublesWorkspaceModel');
  // the athlete on team 2 reads the sets from their side
  const t2 = doublesWorkspaceModel({ playersFile: PLAYERS_FILE, record: { ...DOUBLES_RECORD, players: { P4: { athlete_id: 'a1' } } }, athletes: ATHLETES, athleteId: 'a1', lang: 'en' });
  assert.equal(t2.result, 'W');
  assert.deepEqual(t2.sets, [[18, 21], [21, 19], [21, 15]]);
  assert.equal(t2.title.text, 'Cara / J. Tanaka vs. Alice / P2');
});

test('doublesWorkspaceModel: absent or degraded players.json never reads as ok', () => {
  const missing = doublesWorkspaceModel({ playersFile: null, hits: null, insights: { doubles: unavailable('no_players') }, lang: 'en' });
  assert.equal(missing.status, 'missing');
  assert.equal(missing.identityVideoOk, false);
  assert.ok(missing.detail.length > 0);
  const fromInsight = doublesWorkspaceModel({ playersFile: null, insights: { doubles: partial({ status: 'degraded_teams', players: PLAYERS, identity_video: { status: 'ok', file: 'x' } }, 'doubles_degraded', { status: 'degraded_teams' }) }, lang: 'en' });
  assert.equal(fromInsight.status, 'degraded_teams');
  assert.equal(fromInsight.players.length, 4);
  assert.equal(fromInsight.identityVideoOk, true);
  const err = doublesWorkspaceModel({ playersFile: { status: 'error', reason: 'boom', players: [] }, lang: 'en' });
  assert.equal(err.status, 'error');
  assert.equal(err.tone, 'bad');
  const weird = doublesWorkspaceModel({ playersFile: { status: 'fine', players: 'P1' }, lang: 'en' });
  assert.equal(weird.status, 'unknown');
  assert.equal(weird.players.length, 0);
  // no winner team: no W/L, team-1-first sets
  const nores = doublesWorkspaceModel({ playersFile: PLAYERS_FILE, record: { format: 'doubles', players: {}, result: { winner_team: 3, sets: [[1, 2]] } }, lang: 'en' });
  assert.equal(nores.result, null);
  assert.equal(nores.winnerTeam, null);
  for (const lang of ['en', 'zh']) assertResolved(doublesWorkspaceModel({ playersFile: PLAYERS_FILE, hits: HITS, record: DOUBLES_RECORD, lang }), lang);
});

test('doublesPeekModel: teams, identity status, named-hit share with the unvalidated flag, no scores', () => {
  const insights = {
    match_format: 'doubles',
    technique: ok({ by_side: { lower: { index: 71 } } }),
    doubles: ok({ status: 'ok', validated: true, players: PLAYERS.map((p) => ({ ...p, score: 80 })), teams: [], hits: { total: 40, named: 30, named_share: 0.75, single_candidate: 6 } }),
  };
  const pm = doublesPeekModel({ record: DOUBLES_RECORD, insights, athletes: ATHLETES, athleteId: 'a1' }, 'en');
  assert.equal(pm.status, 'ok');
  assert.equal(pm.title.text, 'J. Tanaka / Bea vs. Cara / Dan');
  assert.equal(pm.result, 'L');
  assert.equal(pm.winnerTeam, 2);
  assert.deepEqual(pm.hits, { total: 40, named: 30, namedShare: 0.75, singleCandidate: 6 });
  assert.equal(pm.namedText, '75%');
  assert.equal(pm.unvalidated, true);
  assert.deepEqual(pm.teams.map((x) => x.label), ['J. Tanaka / Bea', 'Cara / Dan']);
  assertNoScores(pm, 'doublesPeekModel');
  // no athlete in scope: team-1-first sets, a team winner and no W/L
  const anon = doublesPeekModel({ record: DOUBLES_RECORD, insights, athletes: ATHLETES, athleteId: '' }, 'en');
  assert.equal(anon.result, null);
  assert.deepEqual(anon.sets, DOUBLES_RECORD.result.sets);
  // degraded or missing identity data keeps its status and has no share to show
  const deg = doublesPeekModel({ record: {}, insights: { doubles: unavailable('doubles_error', { reason: 'x' }) } }, 'en');
  assert.equal(deg.status, 'error');
  assert.equal(deg.hits.namedShare, null);
  assert.equal(deg.namedText, '—');
  const none = doublesPeekModel({ record: {}, insights: { doubles: unavailable('no_players') } }, 'en');
  assert.equal(none.status, 'missing');
  // a share computed from counts when the payload has none; inconsistent counts give none
  assert.equal(doublesPeekModel({ insights: { doubles: ok({ status: 'ok', hits: { total: 4, named: 1 } }) } }, 'en').hits.namedShare, 0.25);
  assert.equal(doublesPeekModel({ insights: { doubles: ok({ status: 'ok', hits: { total: 4, named: 9 } }) } }, 'en').hits.namedShare, null);
  for (const lang of ['en', 'zh']) assertResolved(doublesPeekModel({ record: DOUBLES_RECORD, insights, athletes: ATHLETES, athleteId: 'a1' }, lang), lang);
});

// ---------- strings ----------

test('every doubles string exists in zh and en', () => {
  const keys = Object.keys(STRINGS.en).filter((k) => k.startsWith('dbl.'));
  assert.ok(keys.length >= 40, `only ${keys.length} dbl.* keys`);
  for (const k of keys) assert.ok(STRINGS.zh[k], `zh ${k}`);
  for (const k of Object.keys(STRINGS.zh).filter((x) => x.startsWith('dbl.'))) assert.ok(STRINGS.en[k], `en ${k}`);
  for (const s of ['doubles_identity', 'doubles_heatmaps', 'doubles_video', 'visualizing']) {
    assert.ok(STRINGS.en['stage.' + s] && STRINGS.zh['stage.' + s], 'stage.' + s);
  }
  for (const st of ['ok', 'degraded', 'degraded_teams', 'error', 'missing', 'unknown']) {
    assert.ok(STRINGS.en['dbl.status.' + st], 'dbl.status.' + st);
  }
  for (const ev of ['shuttle', 'swing', 'single_candidate', 'none']) assert.ok(STRINGS.en['dbl.evidence.' + ev], 'dbl.evidence.' + ev);
});

test('doublesRows: a run with no readable players.json keeps its "missing" status', () => {
  const ins = partial({ matches: 1, athlete: null, runs: [{ stem: 'gone', date: '2026-10-01', status: 'missing', players: [] }] }, 'doubles_degraded', { status: 'degraded' });
  assert.equal(doublesRows(ins, 'en').runs[0].status, 'missing');
});
