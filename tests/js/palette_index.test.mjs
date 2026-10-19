import test from 'node:test';
import assert from 'node:assert/strict';
import { buildIndex, filterIndex, keepActive } from '../../static/kestrel/shell/palette.js';
import {
  pickSquad, pickAthlete, withRange, athletesInSquad, initials, scopeFallback, rangeOf,
} from '../../static/kestrel/shell/topbar.js';

const ATHLETES = {
  athletes: [
    { id: 'a_1', name: 'S. Ito', squad_id: 's_1', matches: 2 },
    { id: 'a_2', name: 'J. Tanaka', squad_id: 's_1', matches: 5 },
    { id: 'a_3', name: 'Free Agent', squad_id: null, matches: 0 },
  ],
  squads: [{ id: 's_1', name: "Senior men's singles", athletes: 2 }],
};
const VIDEOS = [
  { name: 'Ito vs Tanaka 2026', has_match: true, has_posture: false, status: 'analyzed' },
  { name: 'drill', has_match: false, has_posture: true, status: 'analyzed' },
  { name: 'raw upload', has_match: false, has_posture: false, status: 'new' },
];

const byKind = (index, kind) => index.filter((i) => i.kind === kind);

test('buildIndex returns pages, athletes, matches and actions (en)', () => {
  const index = buildIndex({ athletes: ATHLETES, videos: VIDEOS, lang: 'en' });
  const pages = byKind(index, 'goto');
  assert.deepEqual(pages.map((p) => p.label), ['Overview', 'Matches', 'Players', 'Video library', 'Pipeline']);
  assert.equal(pages[0].route, '#/overview');
  assert.equal(pages[3].route, '#/library');
  assert.equal(pages[0].kindLabel, 'GO TO');
  assert.equal(pages[0].hint, 'Analyze');
  assert.equal(pages[4].hint, 'Manage');

  const players = byKind(index, 'player');
  assert.equal(players.length, 3);
  const ito = players.find((p) => p.label === 'S. Ito');
  assert.equal(ito.hint, "Senior men's singles");
  assert.equal(ito.route, '#/overview?athlete=a_1');
  assert.equal(ito.kindLabel, 'PLAYER');
  assert.equal(players.find((p) => p.label === 'Free Agent').hint, 'No squad');

  const matches = byKind(index, 'match');
  assert.equal(matches.length, 2, 'only analysed videos are listed');
  assert.equal(matches[0].label, 'Ito vs Tanaka 2026');
  assert.equal(matches[0].route, '#/matches/Ito%20vs%20Tanaka%202026');
  assert.equal(matches[0].hint, 'Analysed');
  assert.equal(matches[1].route, '#/results/drill?mode=posture');

  const actions = byKind(index, 'action');
  assert.deepEqual(actions.map((a) => a.label), ['New analysis', 'Enter a match result']);
  assert.equal(actions[0].route, '#/new');
  assert.equal(actions[0].hint, 'Upload and analyse');
  for (const item of index) {
    for (const k of ['kind', 'label', 'hint', 'route']) assert.equal(typeof item[k], 'string', k);
  }
});

test('buildIndex localizes labels in Chinese', () => {
  const index = buildIndex({ athletes: ATHLETES, videos: VIDEOS, lang: 'zh' });
  const pages = byKind(index, 'goto');
  assert.equal(pages[0].label, '总览');
  assert.equal(pages[0].kindLabel, '前往');
  assert.equal(byKind(index, 'action')[0].label, '新建分析');
  assert.equal(byKind(index, 'player')[0].kindLabel, '球员');
  // user-entered names are kept verbatim (rendered with textContent)
  assert.ok(byKind(index, 'player').some((p) => p.label === 'S. Ito'));
});

test('buildIndex tolerates missing or malformed inputs', () => {
  const index = buildIndex({ lang: 'en' });
  assert.equal(byKind(index, 'goto').length, 5);
  assert.equal(byKind(index, 'action').length, 2);
  assert.equal(byKind(index, 'player').length, 0);
  assert.equal(byKind(index, 'match').length, 0);
  const odd = buildIndex({ athletes: [{ id: 'a_9', name: 'Array Form' }, null, { id: 'x' }], videos: [null, {}], lang: 'en' });
  assert.deepEqual(byKind(odd, 'player').map((p) => p.label), ['Array Form']);
  assert.equal(byKind(odd, 'match').length, 0);
  assert.equal(buildIndex().length, 7);
});

test('buildIndex honours explicit pages and actions', () => {
  const index = buildIndex({ pages: ['players'], actions: ['enter_result'], lang: 'en' });
  assert.deepEqual(index.map((i) => i.label), ['Players', 'Enter a match result']);
  const custom = buildIndex({ pages: [], actions: [{ id: 'x', label: 'Custom', hint: 'Do it', route: '#/pipeline' }], lang: 'en' });
  assert.deepEqual(custom, [{ kind: 'action', kindLabel: 'ACTION', label: 'Custom', hint: 'Do it', route: '#/pipeline' }]);
});

test('user-derived text is not escaped or interpreted by the model', () => {
  const index = buildIndex({ athletes: { athletes: [{ id: 'a_x', name: '<b>bold</b>' }], squads: [] }, lang: 'en' });
  assert.equal(byKind(index, 'player')[0].label, '<b>bold</b>');
});

test('filterIndex is case-insensitive over label, kind and hint', () => {
  const index = buildIndex({ athletes: ATHLETES, videos: VIDEOS, lang: 'en' });
  const ito = filterIndex(index, 'ito');
  assert.deepEqual(ito.map((i) => i.label), ['S. Ito', 'Ito vs Tanaka 2026']);
  assert.deepEqual(filterIndex(index, 'ITO'), ito);
  assert.deepEqual(filterIndex(index, '  Ito  '), ito);
  // kind: the code and the localized kind label both match (plus the "Players" page by label)
  assert.deepEqual(filterIndex(index, 'player').map((i) => i.kind), ['goto', 'player', 'player', 'player']);
  assert.equal(filterIndex(index, 'go to').length, 5);
  // hint
  assert.deepEqual(filterIndex(index, 'senior men').map((i) => i.label), ['S. Ito', 'J. Tanaka']);
  assert.deepEqual(filterIndex(index, 'upload and').map((i) => i.label), ['New analysis']);
});

test('filterIndex: empty query returns all, no match returns []', () => {
  const index = buildIndex({ athletes: ATHLETES, videos: VIDEOS, lang: 'en' });
  assert.deepEqual(filterIndex(index, ''), index);
  assert.deepEqual(filterIndex(index, '   '), index);
  assert.deepEqual(filterIndex(index, undefined), index);
  assert.notEqual(filterIndex(index, ''), index, 'returns a copy');
  assert.deepEqual(filterIndex(index, 'zzzz-no-such-thing'), []);
  assert.deepEqual(filterIndex(null, 'x'), []);
});

test('filterIndex matches Chinese kind labels', () => {
  const index = buildIndex({ athletes: ATHLETES, lang: 'zh' });
  assert.deepEqual(filterIndex(index, '球员').map((i) => i.kind), ['goto', 'player', 'player', 'player']);
});

// ---- router-driven scope-picker helpers (shell/topbar.js) ----

test('pickSquad sets the squad and drops an athlete from another squad', () => {
  assert.deepEqual(pickSquad({ range: '7d' }, 's_1', ATHLETES), { range: '7d', squad: 's_1' });
  assert.deepEqual(pickSquad({ athlete: 'a_1' }, 's_1', ATHLETES), { athlete: 'a_1', squad: 's_1' });
  assert.deepEqual(pickSquad({ athlete: 'a_3', range: '30d' }, 's_1', ATHLETES), { range: '30d', squad: 's_1' });
  assert.deepEqual(pickSquad({ athlete: 'a_1', squad: 's_1' }, null, ATHLETES), { athlete: 'a_1' });
});

test('pickAthlete sets the athlete and keeps the squad coherent', () => {
  assert.deepEqual(pickAthlete({ squad: 's_1' }, 'a_2', ATHLETES), { squad: 's_1', athlete: 'a_2' });
  assert.deepEqual(pickAthlete({ squad: 's_1' }, 'a_3', ATHLETES), { athlete: 'a_3' });
  assert.deepEqual(pickAthlete({ athlete: 'a_1', range: 'season' }, null, ATHLETES), { range: 'season' });
});

test('withRange only accepts known ranges; rangeOf defaults to 30d', () => {
  assert.deepEqual(withRange({ athlete: 'a_1' }, '7d'), { athlete: 'a_1', range: '7d' });
  assert.deepEqual(withRange({ range: '7d' }, 'bogus'), { range: '7d' });
  assert.equal(rangeOf({}), '30d');
  assert.equal(rangeOf({ range: 'season' }), 'season');
  assert.equal(rangeOf({ range: 'nope' }), '30d');
});

test('athletesInSquad filters by squad; null means everyone', () => {
  assert.deepEqual(athletesInSquad(ATHLETES, 's_1').map((a) => a.id), ['a_1', 'a_2']);
  assert.equal(athletesInSquad(ATHLETES, null).length, 3);
  assert.deepEqual(athletesInSquad(undefined, 's_1'), []);
});

test('initials', () => {
  assert.equal(initials('J. Tanaka'), 'JT');
  assert.equal(initials('lin dan'), 'LD');
  assert.equal(initials('Viktor Axelsen Jr'), 'VJ');
  assert.equal(initials('Momota'), 'MO');
  assert.equal(initials('林丹'), '林丹');
  assert.equal(initials(''), '?');
  assert.equal(initials(null), '?');
});

test('scopeFallback drops a deleted athlete or squad, else returns null', () => {
  assert.equal(scopeFallback({ athlete: 'a_1', squad: 's_1' }, ATHLETES), null);
  assert.deepEqual(scopeFallback({ athlete: 'a_gone', squad: 's_1', range: '7d' }, ATHLETES),
    { query: { squad: 's_1', range: '7d' }, dropped: ['athlete'] });
  assert.deepEqual(scopeFallback({ squad: 's_gone' }, ATHLETES), { query: {}, dropped: ['squad'] });
  assert.equal(scopeFallback({}, ATHLETES), null);
});

test('keepActive: a background refresh keeps the selected route active', () => {
  const before = buildIndex({ athletes: ATHLETES, lang: 'en' });
  const ito = before.findIndex((i) => i.label === 'S. Ito');
  // the refresh adds videos (new rows before the actions) and keeps the athlete
  const after = buildIndex({ athletes: ATHLETES, videos: VIDEOS, lang: 'en' });
  assert.equal(after[keepActive(before, ito, after)].label, 'S. Ito');
  const action = before.findIndex((i) => i.label === 'New analysis');
  assert.equal(after[keepActive(before, action, after)].label, 'New analysis');
  // the active row disappeared, or nothing was active: back to the first row
  const gone = buildIndex({ lang: 'en' });
  assert.equal(keepActive(before, ito, gone), 0);
  assert.equal(keepActive([], 0, after), 0);
  assert.equal(keepActive(null, 3, after), 0);
});

test('filterIndex requires every word, in any order (Task 11 review)', () => {
  const videos = [
    { name: 'Dji 20260919104815 0005 D', has_match: true, has_posture: false, status: 'analyzed' },
    { name: 'Dji 20260919100627 0004 D', has_match: true, has_posture: false, status: 'analyzed' },
  ];
  const index = buildIndex({ athletes: ATHLETES, videos, lang: 'en' });
  assert.deepEqual(filterIndex(index, 'dji 0005').map((i) => i.label), ['Dji 20260919104815 0005 D']);
  assert.deepEqual(filterIndex(index, '0005   DJI').map((i) => i.label), ['Dji 20260919104815 0005 D']);
  assert.equal(filterIndex(index, 'dji').length, 2);
  assert.deepEqual(filterIndex(index, 'dji 9999'), []);
});
