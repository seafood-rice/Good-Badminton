import test from 'node:test';
import assert from 'node:assert/strict';
import {
  panelState, reportFor, claimsOf, evidenceText, isFallback, otherSide, planItems, partClaims,
} from '../../static/kestrel/views/strategy_model.js';

test('panelState maps payloads', () => {
  assert.equal(panelState(null), 'loading');
  assert.equal(panelState({ enabled: false, status: 'none' }), 'off');
  assert.equal(panelState({ enabled: true, status: 'none' }), 'none');
  assert.equal(panelState({ enabled: true, status: 'pending', job: { status: 'pending' } }), 'queued');
  assert.equal(panelState({ enabled: true, status: 'ok', job: { status: 'running' }, report: {} }), 'running');
  assert.equal(panelState({ enabled: true, status: 'ok', report: {} }), 'ready');
  assert.equal(panelState({ enabled: true, status: 'partial', report: {} }), 'ready');
  assert.equal(panelState({ enabled: true, status: 'ok', stale: true, report: {} }), 'stale');
  assert.equal(panelState({ enabled: true, status: 'failed', report: {} }), 'failed');
  assert.equal(panelState({ enabled: true, status: 'none', job: { status: 'error' } }), 'failed');
  assert.equal(panelState('junk'), 'loading');
});

test('reportFor falls back to the other language', () => {
  const p = { report: { by_lang: { en: { a: 1 }, zh: { a: 2 } } } };
  assert.deepEqual(reportFor(p, 'zh'), { a: 2 });
  assert.deepEqual(reportFor({ report: { by_lang: { en: { a: 1 } } } }, 'zh'), { a: 1 });
  assert.deepEqual(reportFor({ report: { by_lang: { zh: { a: 2 } } } }, 'en'), { a: 2 });
  assert.equal(reportFor({ report: {} }, 'en'), null);
  assert.equal(reportFor(null, 'en'), null);
});

test('claimsOf flattens your_game subsections in order', () => {
  assert.deepEqual(claimsOf({ claims: [{ text: 'a', evidence: ['x'] }, { text: 5 }, null] }), [{ text: 'a', evidence: ['x'] }]);
  const yg = {
    weaknesses: { claims: [{ text: 'w', evidence: ['w1'] }] },
    movement: { claims: [{ text: 'm', evidence: 'oops' }] },
    shot_selection: { claims: [{ text: 's', evidence: ['s1', 2] }] },
  };
  assert.deepEqual(claimsOf(yg).map((c) => c.text), ['s', 'm', 'w']);
  assert.deepEqual(claimsOf(yg)[1].evidence, [], 'a string evidence is not a list');
  assert.deepEqual(partClaims(yg, 'weaknesses'), [{ text: 'w', evidence: ['w1'] }]);
  assert.deepEqual(claimsOf(null), []);
});

test('evidenceText formats values', () => {
  const facts = [
    { id: 'match.rally_median_sec', value: 7.456, unit: 's' },
    { id: 'you.seq2_top', value: [{ seq: ['lift', 'smash'], n: 3 }], unit: null },
    { id: 'you.groups', value: { smash: 4, drop: 2 }, unit: null },
    { id: 'x.none', value: null, unit: 'm' },
  ];
  assert.equal(evidenceText(['match.rally_median_sec'], facts), 'match.rally_median_sec = 7.46 s');
  assert.equal(evidenceText(['you.seq2_top', 'you.groups'], facts), 'you.seq2_top = lift > smash x3\nyou.groups = smash: 4, drop: 2');
  assert.equal(evidenceText(['x.none', 'gone'], facts), 'x.none = — m\ngone');
  assert.equal(evidenceText(['a'], { a: { value: 1, unit: '%' } }), 'a = 1 %');
  assert.equal(evidenceText('nope', facts), '');
});

test('isFallback, otherSide, planItems', () => {
  assert.equal(isFallback({ llm: false }), true);
  assert.equal(isFallback({ llm: true }), false);
  assert.equal(isFallback(null), false);
  assert.equal(otherSide({ side: 'lower' }), 'upper');
  assert.equal(otherSide({ side: 'upper' }), 'lower');
  const p = planItems({ items: [{ do: 'd', why: 'w', cue: 'c', evidence: ['e'] }, 7], training_focus: { text: 'tf', evidence: ['t'] } });
  assert.deepEqual(p.items, [{ do: 'd', why: 'w', cue: 'c', evidence: ['e'] }]);
  assert.deepEqual(p.trainingFocus, { text: 'tf', evidence: ['t'] });
  assert.equal(planItems({}).trainingFocus, null);
});

test('the Pipeline maps a strategy job to its own kind and the match details link', async () => {
  const { groupJobs } = await import('../../static/kestrel/views/pipeline.js');
  const g = groupJobs([{ id: 's', video: 'v1.mp4', kind: 'strategy', status: 'completed', progress: 100 }]);
  assert.equal(g.finished[0].kind, 'strategy');
  assert.equal(g.finished[0].resultsHash, '#/matches/v1');
});

test('the Pipeline never offers Pause on a strategy row, but still Cancel when queued', async () => {
  const { jobActions } = await import('../../static/kestrel/views/pipeline.js');
  assert.deepEqual(jobActions({ kind: 'strategy', status: 'running' }), []);
  assert.deepEqual(jobActions({ kind: 'strategy', status: 'pending' }), ['cancel']);
  assert.deepEqual(jobActions({ kind: 'match', status: 'running' }), ['pause']);
  assert.deepEqual(jobActions({ kind: 'posture', status: 'running' }), ['pause']);
  assert.deepEqual(jobActions({ kind: 'match', status: 'paused' }), ['resume']);
  assert.deepEqual(jobActions({ kind: 'match', status: 'pending' }), ['cancel']);
  assert.deepEqual(jobActions({ kind: 'match', status: 'completed' }), []);
});

test('stage.strategy and the doubles focus caveat exist in both languages', async () => {
  const { STRINGS } = await import('../../static/kestrel/core/i18n.js');
  assert.equal(STRINGS.zh['stage.strategy'], '策略报告');
  assert.equal(STRINGS.en['stage.strategy'], 'Strategy report');
  assert.equal(STRINGS.zh['strategy.caveat.focus_assumed_team'], '默认将第 1 队视为你方');
  assert.equal(STRINGS.en['strategy.caveat.focus_assumed_team'], 'Team 1 is assumed to be your team');
});

test('tacticsView shapes the effectiveness and opening tables and their empty states', async () => {
  const { tacticsView } = await import('../../static/kestrel/views/strategy_model.js');
  assert.equal(tacticsView(null), null);
  assert.equal(tacticsView({ facts: [] }), null);
  const v = tacticsView({
    tables: {
      effectiveness: {
        you: { lift: { effective: 1, neutral: 0, ineffective: 1, unknown: 0, n: 2 }, smash: { effective: 4, neutral: 1, ineffective: 1, unknown: 0, n: 6 }, bad: 'x' },
        opponent: {},
      },
      openings: {
        complete: 6, total: 8, neutral: 1, insufficient: false, won_by: { you: 3, opponent: 2 }, opening_winner_won_point: { n: 5, won: 4 },
        rows: [{ rally: 3, advantage: 'you', point_winner: 'opponent', n_shots: 9, shots: [{ hitter: 'you', stroke: 'long_serve', zone: { depth: 'rear', side: 'left' }, sent_to: null }, null] }, null],
      },
    },
  });
  assert.deepEqual(v.eff.you.map((r) => [r.stroke, r.n, r.few]), [['smash', 6, false], ['lift', 2, true], ['bad', 0, true]]);
  assert.deepEqual(v.eff.opponent, []);
  assert.equal(v.openings.complete, 6);
  assert.equal(v.openings.insufficient, false);
  assert.deepEqual(v.openings.wonBy, { you: 3, opponent: 2 });
  assert.deepEqual(v.openings.winPoint, { n: 5, won: 4 });
  assert.equal(v.openings.rows.length, 1);
  assert.equal(v.openings.rows[0].pointWinner, 'opponent');
  assert.deepEqual(v.openings.rows[0].shots[0].zone, { depth: 'rear', side: 'left' });
  assert.equal(v.openings.rows[0].shots[0].sentTo, null);
  const empty = tacticsView({ tables: { effectiveness: { you: {}, opponent: {} }, openings: { complete: 0, total: 0, rows: [], insufficient: true } } });
  assert.deepEqual(empty.eff, { you: [], opponent: [] });
  assert.equal(empty.openings.insufficient, true);
  assert.deepEqual(empty.openings.rows, []);
  assert.equal(tacticsView({ tables: { openings: 'junk' } }).openings.complete, 0);
});
