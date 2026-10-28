import test from 'node:test';
import assert from 'node:assert/strict';
import * as M from '../../static/kestrel/views/workspace_model.js';
import * as W from '../../static/kestrel/views/workspace.js';
import { formatHash } from '../../static/kestrel/core/router.js';

test('workspace_model: exports the pure helpers; workspace.js re-exports the same functions', () => {
  for (const name of ['summariseRallyPayload', 'buildWorkspaceModel', 'matchDate', 'matchDateApprox', 'listScore',
    'rallyInput', 'rallyCountText', 'capItems', 'readReduced']) {
    assert.equal(typeof M[name], 'function', name);
    assert.equal(W[name], M[name], name + ' re-exported');
  }
  assert.equal(M.RALLY_PAD_SEC, 0.5);
  assert.equal(M.JOIN_FRAMES, 3);
  assert.equal(M.STROKE_PAGE, 200);
});

test('workspace_model: imports without a DOM', () => {
  assert.equal(typeof globalThis.document, 'undefined');
  const m = M.buildWorkspaceModel({ rallies: { rallies: [{ start_sec: 1, end_sec: 4 }] }, strokes: [{ frame: 60, hitter: 'lower', stroke: 'smash' }], fps: 30 });
  assert.equal(m.rallies.length, 1);
  assert.equal(m.strokes[0].rallyId, 1);
});

test('strokeDistribution: groups in order, zeroes dropped; old schema is stale', () => {
  const d = M.strokeDistribution({ schema: 2, groups: { smash: 13, net: 9, clear: 36, serve: 16, lift: 0 }, shuttle_source: 'tracknet' });
  assert.deepEqual(d.groups.map((g) => g.id), ['serve', 'clear', 'smash', 'net']);
  assert.equal(d.total, 74);
  assert.equal(d.stale, false);
  assert.equal(M.trackNetNote({ shuttle_source: 'tracknet' }), true);
  assert.deepEqual(M.strokeDistribution({ schema: 1, groups: { smash: 3 } }), { groups: [], total: 0, stale: true });
  assert.deepEqual(M.strokeDistribution(null), { groups: [], total: 0, stale: true });
});

test('techniqueSummary: ok / empty / stale / doubles', () => {
  const pay = { summary: { stroke_count: 68, scored_count: 45, by_type: { smash: { count: 10, avg_score: 43.9 }, clear: { count: 19, avg_score: 56.4 }, unknown: { count: 9, avg_score: null } },
    recurring_weaknesses: [{ metric: 'trunk_rotation', count: 43 }, { metric: 'a', count: 3 }, { metric: 'b', count: 2 }, { metric: 'c', count: 1 }] },
    strokes: [{ scored: true }] };
  const s = M.techniqueSummary(pay);
  assert.equal(s.state, 'ok');
  assert.equal(s.count, 68);
  assert.deepEqual(s.byType.map((b) => b.id), ['clear', 'smash', 'unknown']);
  assert.equal(s.weaknesses.length, 3);
  assert.equal(M.techniqueSummary(null).state, 'empty');
  assert.equal(M.techniqueSummary({ summary: { stroke_count: 2 }, strokes: [{}] }).state, 'stale');
  assert.equal(M.techniqueSummary(pay, { doubles: true }).state, 'doubles');
});

test('weaknessLines: keeps metric objects only', () => {
  const w = M.weaknessLines({ weaknesses: [{ metric: 'trunk_rotation', measured: 177.27, ideal_range: [30, 50], direction: 'over', severity: 'severe' }, 'junk', { measured: 1 }] });
  assert.deepEqual(w, [{ metric: 'trunk_rotation', measured: 177.27, ideal: [30, 50], direction: 'over', severity: 'severe' }]);
  assert.deepEqual(M.weaknessLines(null), []);
});

test('matchParts + resolvePartQuery: single, whole, part, own-stem redirect, unknown part', () => {
  const info = M.matchParts({ date: '2026-10-10', parts: [{ stem: 'p2', sides_swapped: true }] }, 'p1');
  assert.deepEqual(info, { split: true, primary: 'p1', parts: [{ stem: 'p1', n: 1, swapped: false }, { stem: 'p2', n: 2, swapped: true }] });
  assert.deepEqual(M.matchParts({ date: 'x' }, 's'), { split: false, primary: 's', parts: [] });
  assert.deepEqual(M.resolvePartQuery(info, 'p1', {}), { redirect: null, view: 'whole', partStem: null });
  assert.deepEqual(M.resolvePartQuery(info, 'p1', { part: 'p2', tab: 'clips' }), { redirect: null, view: 'part', partStem: 'p2' });
  assert.equal(M.resolvePartQuery(info, 'p2', { tab: 'plan' }).redirect, formatHash('matches', { stem: 'p1' }, { tab: 'plan', part: 'p2' }));
  const unk = M.resolvePartQuery(info, 'p1', { part: 'zz' });
  assert.equal(unk.view, 'whole');
  assert.equal(unk.redirect, formatHash('matches', { stem: 'p1' }, {}));
  const single = M.matchParts({}, 's');
  assert.deepEqual(M.resolvePartQuery(single, 's', {}), { redirect: null, view: 'single', partStem: 's' });
  assert.equal(M.resolvePartQuery(single, 's', { part: 'x', tab: 'clips' }).redirect, formatHash('matches', { stem: 's' }, { tab: 'clips' }));
  assert.equal(M.resolveTab({ tab: 'plan' }), 'plan');
  assert.equal(M.resolveTab({ tab: 'bogus' }), 'strokes');
});

test('pooling: distribution, technique (weighted), rally counts (weakest label)', () => {
  const a = M.strokeDistribution({ schema: 2, groups: { smash: 2, clear: 1 } });
  const b = M.strokeDistribution({ schema: 2, groups: { smash: 1, net: 4 } });
  const p = M.poolDistribution([a, b, M.strokeDistribution(null)]);
  assert.deepEqual(p.groups, [{ id: 'clear', count: 1 }, { id: 'smash', count: 3 }, { id: 'net', count: 4 }]);
  assert.equal(p.total, 8);
  const t1 = { state: 'ok', count: 10, scored: 5, byType: [{ id: 'smash', count: 2, avg: 50 }], weaknesses: [{ metric: 'x', count: 2 }] };
  const t2 = { state: 'ok', count: 4, scored: 2, byType: [{ id: 'smash', count: 6, avg: 70 }, { id: 'lift', count: 1, avg: null }], weaknesses: [{ metric: 'x', count: 1 }, { metric: 'y', count: 5 }] };
  const pt = M.poolTechnique([t1, t2, { state: 'empty' }]);
  assert.equal(pt.state, 'ok');
  assert.equal(pt.count, 14);
  assert.deepEqual(pt.byType.find((x) => x.id === 'smash'), { id: 'smash', count: 8, avg: 65 });
  assert.equal(pt.byType.find((x) => x.id === 'lift').avg, null);
  assert.deepEqual(pt.weaknesses, [{ metric: 'y', count: 5 }, { metric: 'x', count: 3 }]);
  assert.equal(M.poolTechnique([{ state: 'empty' }]).state, 'empty');
  const r = M.poolRallyCounts([{ count: 3, label: 'rallies', failed: false }, { count: 2, label: 'segments', failed: false }, { count: 0, failed: true }]);
  assert.deepEqual(r, { count: 5, label: 'segments', failedParts: 1 });
});

test('partTechIndex and planSessions', () => {
  const ins = { technique: { value: { by_side: { upper: null, lower: { index: 49.5 } } } } };
  assert.equal(M.partTechIndex(ins, 'lower'), 49.5);
  assert.equal(M.partTechIndex(ins, 'upper'), null);
  assert.equal(M.partTechIndex(ins, null), 49.5);
  assert.equal(M.partTechIndex(null, null), null);
  const plan = { weeks: [{ week: 1, phase: 'Foundation', sessions: [{ name: 'a', location: 'home' }, { name: 'b', location: 'court' }] }, { week: 2, phase: 'Progression', sessions: [{ name: 'c', location: 'court' }] }] };
  assert.deepEqual(M.planSessions(plan, 'court').map((x) => [x.week, x.session.name]), [[1, 'b'], [2, 'c']]);
  assert.deepEqual(M.planSessions(null, 'home'), []);
});

test('workspace_model: a learned segmentation counts as rallies, not segments or windows', () => {
  const payload = { rallies: [{ id: 1, start_sec: 1, end_sec: 4 }, { id: 2, start_sec: 9, end_sec: 12 }],
    detection: { signal: 'learned', attempted_signal: 'learned', degraded: false, model: { version: 'v1', mode: 'gap', f1: 0.8 } } };
  const s = M.summariseRallyPayload(payload);
  assert.deepEqual(s, { count: 2, suppressed: false, label: 'rallies', signal: 'learned', degraded: false, failed: false });
  assert.equal(M.unitKey(s), 'ws.unit.rally');
  assert.equal(M.rallyCountText(s, 'en'), M.rallyCountText({ ...s, signal: 'courtview' }, 'en'));
  for (const lang of ['en', 'zh']) {
    const note = M.signalNote(s, lang, 2);
    assert.ok(note && note !== 'ws.signal_note.learned', lang + ' note is translated');
    assert.notEqual(note, M.signalNote({ ...s, signal: 'swing', label: 'segments' }, lang, 2));
  }
  const m = M.buildWorkspaceModel({ rallies: payload, strokes: [{ frame: 60, hitter: 'lower', stroke: 'smash' }], fps: 30 });
  assert.equal(m.rallies.length, 2);
  assert.equal(m.summary.label, 'rallies');
});
