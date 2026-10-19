// Pure model builders of the Matches, Workspace, Players and Pipeline views. The DOM layers over them are
// verified in the browser; these tests pin the joins and the honesty rules (no outcome, estimated rallies).
import test from 'node:test';
import assert from 'node:assert/strict';
import { buildWorkspaceModel, summariseRallyPayload } from '../../static/kestrel/views/workspace.js';
import { matchRows, validateRecordForm, formFromRecord, peekModel } from '../../static/kestrel/views/matches.js';
import { groupBySquad } from '../../static/kestrel/views/players.js';
import { groupJobs } from '../../static/kestrel/views/pipeline.js';

const FPS = 50;

function rallyPayload(signal = 'shuttle', extra = {}) {
  return {
    fps: FPS,
    rallies: [
      { id: 1, start_sec: 2, end_sec: 6, degraded: false },
      { id: 2, start_sec: 10, end_sec: 18, degraded: false },
      { id: 3, start_sec: 30, end_sec: 33, degraded: false },
    ],
    detection: { signal, degraded: false, ...extra },
  };
}

function strokesPayload() {
  return {
    strokes: [
      { frame: 3 * FPS, hitter: 'lower', stroke: 'serve', uncertain: false, confidence: 0.9 }, // rally 1
      { frame: 12 * FPS, hitter: 'upper', stroke: 'clear', uncertain: false, confidence: 0.8 }, // rally 2
      { frame: 14 * FPS, hitter: 'lower', stroke: 'uncertain', uncertain: true, confidence: 0.1 }, // rally 2
      { frame: 16 * FPS, hitter: 'lower', stroke: 'smash', uncertain: false, confidence: 0.7 }, // rally 2
      { frame: 24 * FPS, hitter: 'upper', stroke: 'net', uncertain: false, confidence: 0.6 }, // between rallies
    ],
  };
}

function reports() {
  return [
    // 2 frames after the lower smash at 800: joins it
    { stroke_type: 'smash', contact_frame: 16 * FPS + 2, player_side: 'lower', overall_score: 81.5,
      per_metric: { elbow_extension: { measured: 150, score: 90, ideal_range: [140, 160] } } },
    // right frame, wrong side for the upper clear at 600: must NOT join it
    { stroke_type: 'high_clear', contact_frame: 12 * FPS, player_side: 'lower', overall_score: 40 },
    // right side, 5 frames off the lower serve at 150: too far, must NOT join
    { stroke_type: 'serve', contact_frame: 3 * FPS + 5, player_side: 'lower', overall_score: 77 },
  ];
}

function hasKeyDeep(value, names, seen = new Set()) {
  if (!value || typeof value !== 'object' || seen.has(value)) return false;
  seen.add(value);
  for (const [k, v] of Object.entries(value)) {
    if (names.includes(k)) return true;
    if (hasKeyDeep(v, names, seen)) return true;
  }
  return false;
}

// ---------- workspace ----------

test('workspace: a stroke at frame/fps inside rally 2 gets rallyId 2; outside every rally it is null', () => {
  const m = buildWorkspaceModel({ rallies: rallyPayload(), strokes: strokesPayload(), techReports: [], fps: FPS, insights: null });
  const byFrame = Object.fromEntries(m.strokes.map((s) => [s.frame, s]));
  assert.equal(byFrame[12 * FPS].rallyId, 2);
  assert.equal(byFrame[16 * FPS].rallyId, 2);
  assert.equal(byFrame[3 * FPS].rallyId, 1);
  assert.equal(byFrame[24 * FPS].rallyId, null);
  assert.equal(byFrame[12 * FPS].time, 12);
  assert.deepEqual(m.rallies.map((r) => [r.id, r.start, r.end, r.len, r.strokeCount]),
    [[1, 2, 6, 4, 1], [2, 10, 18, 8, 3], [3, 30, 33, 3, 0]]);
  assert.deepEqual(m.strokes.map((s) => s.i), [1, 2, 3, 4, 5]);
});

test('workspace: technique reports join by contact_frame within 3 frames AND player_side == hitter', () => {
  const m = buildWorkspaceModel({ rallies: rallyPayload(), strokes: strokesPayload(), techReports: reports(), fps: FPS, insights: null });
  const byFrame = Object.fromEntries(m.strokes.map((s) => [s.frame, s]));
  assert.equal(byFrame[16 * FPS].score, 81.5);
  assert.equal(byFrame[16 * FPS].report.technique, 'smash');
  assert.equal(byFrame[16 * FPS].report.metrics[0].id, 'elbow_extension');
  assert.equal(byFrame[12 * FPS].score, null, 'a report on the other side never joins');
  assert.equal(byFrame[3 * FPS].score, null, 'a report 5 frames away never joins');
  // /api/technique shape ({summary, strokes}) is accepted as well as a bare list
  const m2 = buildWorkspaceModel({ rallies: rallyPayload(), strokes: strokesPayload(), techReports: { summary: {}, strokes: reports() }, fps: FPS });
  assert.equal(m2.strokes.find((s) => s.frame === 16 * FPS).score, 81.5);
});

test('workspace: opponent strokes are shown but never scored; uncertain strokes are labelled uncertain', () => {
  const m = buildWorkspaceModel({
    rallies: rallyPayload(), strokes: strokesPayload(),
    techReports: [...reports(), { stroke_type: 'high_clear', contact_frame: 12 * FPS + 1, player_side: 'upper', overall_score: 66 }],
    fps: FPS, insights: null, athleteSide: 'lower',
  });
  const clear = m.strokes.find((s) => s.frame === 12 * FPS);
  assert.equal(clear.opponent, true);
  assert.equal(clear.score, null);
  assert.equal(clear.report, null);
  const unc = m.strokes.find((s) => s.frame === 14 * FPS);
  assert.equal(unc.uncertain, true);
  assert.equal(unc.label, 'uncertain');
  assert.equal(m.strokes.find((s) => s.frame === 16 * FPS).score, 81.5);
  // with no selected athlete both sides are scored
  const both = buildWorkspaceModel({
    rallies: rallyPayload(), strokes: strokesPayload(),
    techReports: [{ stroke_type: 'high_clear', contact_frame: 12 * FPS + 1, player_side: 'upper', overall_score: 66 }], fps: FPS,
  });
  assert.equal(both.strokes.find((s) => s.frame === 12 * FPS).score, 66);
});

test('workspace: the swing signal labels rallies "segments" and suppresses the count', () => {
  const m = buildWorkspaceModel({ rallies: rallyPayload('swing'), strokes: strokesPayload(), techReports: [], fps: FPS, insights: null });
  assert.equal(m.summary.label, 'segments');
  assert.equal(m.summary.suppressed, true);
  assert.equal(m.summary.signal, 'swing');
  const deg = buildWorkspaceModel({ rallies: rallyPayload('swing', { degraded: true }), strokes: null, techReports: null, fps: FPS });
  assert.equal(deg.summary.label, 'windows');
  assert.equal(deg.summary.suppressed, true);
  const none = buildWorkspaceModel({ rallies: rallyPayload('none'), fps: FPS });
  assert.equal(none.summary.label, 'windows');
  const shuttle = buildWorkspaceModel({ rallies: rallyPayload('shuttle'), fps: FPS });
  assert.equal(shuttle.summary.label, 'rallies');
  assert.equal(shuttle.summary.suppressed, false);
  assert.equal(shuttle.summary.count, 3);
  const err = buildWorkspaceModel({ rallies: { rallies: [], detection: { signal: 'error' } }, fps: FPS });
  assert.equal(err.summary.failed, true);
  assert.equal(err.rallies.length, 0);
});

test('workspace: stroke labels withheld by a degraded run make every stroke uncertain', () => {
  const m = buildWorkspaceModel({
    rallies: rallyPayload('none', { degraded: true, stroke_labels_withheld: true }), strokes: strokesPayload(), fps: FPS,
  });
  assert.ok(m.strokes.every((s) => s.uncertain && s.label === 'uncertain'));
});

test('workspace: no won / lost / outcome key anywhere in the model', () => {
  const m = buildWorkspaceModel({
    rallies: rallyPayload('swing'), strokes: strokesPayload(), techReports: reports(), fps: FPS,
    insights: { rally: { status: 'partial', value: { count: 3, label: 'segments', suppressed: true } } }, athleteSide: 'lower',
  });
  assert.equal(hasKeyDeep(m, ['won', 'lost', 'outcome', 'winner']), false);
});

test('workspace: tolerates missing and malformed inputs', () => {
  const m = buildWorkspaceModel({});
  assert.deepEqual(m.rallies, []);
  assert.deepEqual(m.strokes, []);
  assert.equal(m.summary.label, 'rallies');
  const m2 = buildWorkspaceModel({ rallies: 'junk', strokes: { strokes: [null, { frame: 'x' }] }, techReports: 5, fps: 0 });
  assert.equal(m2.rallies.length, 0);
  assert.equal(m2.strokes.length, 1);
  assert.equal(m2.strokes[0].time, null);
  assert.equal(m2.strokes[0].rallyId, null);
  // fps falls back to the rally payload's own fps
  const m3 = buildWorkspaceModel({ rallies: rallyPayload(), strokes: strokesPayload() });
  assert.equal(m3.strokes[1].rallyId, 2);
  // the insights sidecar supplies the label when there is no rally file
  const m4 = buildWorkspaceModel({ insights: { rally: { status: 'partial', value: { count: 9, label: 'segments', suppressed: true, signal: 'swing' } } } });
  assert.equal(m4.summary.label, 'segments');
  assert.equal(m4.summary.suppressed, true);
});

test('summariseRallyPayload mirrors the server rules', () => {
  assert.deepEqual(summariseRallyPayload(null), { count: 0, suppressed: false, label: 'rallies', signal: null, degraded: false, failed: false });
  assert.equal(summariseRallyPayload([]).failed, true);
  assert.equal(summariseRallyPayload({ rallies: [{}, {}] }).count, 2);
  assert.equal(summariseRallyPayload({ rallies: [{}], detection: { signal: 'courtview' } }).label, 'rallies');
});

// ---------- matches ----------

test('matchRows: one row per stem, outcome only from the entered result', () => {
  const rows = matchRows({
    matches: [
      { stem: 'b', date: '2026-10-11', side: 'lower', athlete: 'J. Tanaka', opponent: 'S. Ito', result: 'L', sets: [[18, 21], [21, 19]], tech_index: 72, status: 'analysed', needs_review: false },
      { stem: 'b', date: '2026-10-11', side: 'upper', athlete: 'S. Ito', opponent: 'J. Tanaka', result: 'W', sets: [[21, 18], [19, 21]], tech_index: 70, status: 'analysed', needs_review: false },
      { stem: 'a', date: null, side: 'lower', athlete: null, opponent: null, result: null, sets: null, tech_index: null, status: 'processing', needs_review: true },
      null, { nostem: true },
    ],
  });
  assert.equal(rows.length, 2);
  assert.equal(rows[0].stem, 'b');
  assert.equal(rows[0].result, 'L');
  assert.equal(rows[0].setsText, '18-21, 21-19');
  assert.equal(rows[0].techIndex, 72);
  assert.equal(rows[1].result, null);
  assert.equal(rows[1].status, 'needs_review');
  assert.deepEqual(matchRows(null), []);
});

test('validateRecordForm: sets must be complete pairs', () => {
  const base = { date: '', upper: { athlete_id: '', name: '' }, lower: { athlete_id: '', name: '' }, winner: 'upper' };
  const bad = validateRecordForm({ ...base, sets: '21-' });
  assert.equal(bad.record, null);
  assert.ok(bad.errors.sets);
  for (const s of ['21', 'abc', '21-18,', '21-18, 22', '100-2', '21-18, 1-2, 3-4, 5-6, 7-8, 9-10']) {
    assert.ok(validateRecordForm({ ...base, sets: s }).errors.sets, s);
  }
  const good = validateRecordForm({ ...base, sets: '21-18, 19-21, 21-15' });
  assert.deepEqual(good.errors, {});
  assert.deepEqual(good.record.result, { winner: 'upper', sets: [[21, 18], [19, 21], [21, 15]] });
});

test('validateRecordForm: a winner is required when sets are present; blank sides map to null', () => {
  const r = validateRecordForm({ date: '', upper: { athlete_id: '', name: '  ' }, lower: null, winner: '', sets: '21-18' });
  assert.equal(r.record, null);
  assert.ok(r.errors.winner);
  const ok = validateRecordForm({ date: '2026-10-11', upper: { athlete_id: '', name: '  ' }, lower: { athlete_id: 'a_1', name: '' }, winner: '', sets: '' });
  assert.deepEqual(ok.errors, {});
  assert.deepEqual(ok.record, { date: '2026-10-11', upper: null, lower: { athlete_id: 'a_1' }, result: null });
  const named = validateRecordForm({ date: '', upper: { athlete_id: '', name: ' S. Ito ' }, lower: { athlete_id: '', name: '' }, winner: 'lower', sets: '' });
  assert.deepEqual(named.record, { date: null, upper: { name: 'S. Ito' }, lower: null, result: { winner: 'lower', sets: [] } });
});

test('validateRecordForm: bad dates and the same athlete on both sides are rejected', () => {
  const base = { upper: { athlete_id: '', name: '' }, lower: { athlete_id: '', name: '' }, winner: '', sets: '' };
  assert.ok(validateRecordForm({ ...base, date: '2026-02-30' }).errors.date);
  assert.ok(validateRecordForm({ ...base, date: '11/10/2026' }).errors.date);
  const same = validateRecordForm({ ...base, date: '', upper: { athlete_id: 'a', name: '' }, lower: { athlete_id: 'a', name: '' } });
  assert.ok(same.errors.lower);
  assert.ok(validateRecordForm({ ...base, upper: { athlete_id: '', name: 'x'.repeat(61) } }).errors.upper);
});

test('formFromRecord round-trips through validateRecordForm', () => {
  const rec = { date: '2026-10-11', upper: { name: 'S. Ito' }, lower: { athlete_id: 'a_1' }, result: { winner: 'upper', sets: [[21, 18], [19, 21]] } };
  const form = formFromRecord(rec);
  assert.equal(form.sets, '21-18, 19-21');
  assert.deepEqual(validateRecordForm(form).record, rec);
  const empty = formFromRecord({});
  assert.deepEqual(validateRecordForm(empty).record, { date: null, upper: null, lower: null, result: null });
});

test('peekModel: honest rally label and no point outcome', () => {
  const pm = peekModel({
    stem: 's1',
    record: { date: '2026-10-11', upper: { name: 'S. Ito' }, lower: { athlete_id: 'a1' }, result: { winner: 'upper', sets: [[21, 18]] } },
    insights: {
      date: { value: '2026-10-10', source: 'filename' },
      rally: { status: 'partial', value: { count: 21, label: 'segments', suppressed: true, signal: 'swing' } },
      strokes: { status: 'ok', value: { total: 118, classified: 27, uncertain: 91 } },
      technique: { status: 'partial', value: { by_side: { upper: { index: 60.2 }, lower: { index: 71.44 } } } },
    },
    athletes: { athletes: [{ id: 'a1', name: 'J. Tanaka' }], squads: [] },
    athleteId: 'a1',
  });
  assert.equal(pm.date, '2026-10-11');
  assert.equal(pm.side, 'lower');
  assert.equal(pm.names.lower, 'J. Tanaka');
  assert.equal(pm.names.upper, 'S. Ito');
  assert.equal(pm.result, 'L');
  assert.equal(pm.techIndex, 71.44);
  assert.equal(pm.rally.label, 'segments');
  assert.equal(pm.rally.suppressed, true);
  assert.equal(pm.strokes.classified, 27);
  assert.equal(pm.strokes.total, 118);
  assert.equal(hasKeyDeep(pm, ['won', 'lost']), false);
});

// ---------- players ----------

test('groupBySquad: squads sorted case-insensitively, unassigned athletes under a null squad last', () => {
  const groups = groupBySquad(
    [
      { id: 'a1', name: 'zed', squad_id: 's1' },
      { id: 'a2', name: 'Amy', squad_id: 's1' },
      { id: 'a3', name: 'bob', squad_id: null },
      { id: 'a4', name: 'Al', squad_id: 'gone' },
      { id: 'a5', name: 'carl', squad_id: 's2' },
    ],
    [{ id: 's1', name: 'seniors' }, { id: 's2', name: 'Juniors' }, { id: 's3', name: 'empty' }],
  );
  assert.deepEqual(groups.map((g) => (g.squad ? g.squad.name : null)), ['empty', 'Juniors', 'seniors', null]);
  assert.deepEqual(groups[2].athletes.map((a) => a.name), ['Amy', 'zed']);
  assert.deepEqual(groups[3].athletes.map((a) => a.name), ['Al', 'bob']);
  assert.deepEqual(groups[0].athletes, []);
  assert.deepEqual(groupBySquad(null, null), []);
});

// ---------- pipeline ----------

test('groupJobs: active jobs first, finished after, newest-first order kept within each', () => {
  const g = groupJobs([
    { id: '1', video: 'v1', kind: 'match', status: 'completed', stage: 'done', progress: 100, message: '', updated: 30 },
    { id: '2', video: 'v2', kind: 'match', status: 'running', stage: 'analyzing', progress: 41.7, message: '', updated: 20 },
    { id: '3', video: 'v3', kind: 'posture', status: 'error', stage: 'error', progress: 12, message: '分析失败 (exit=1)', updated: 10 },
    { id: '4', video: 'v4', kind: 'match', status: 'detached', stage: null, progress: 7, message: '', updated: 5 },
    { id: '5', video: 'v5', kind: 'match', status: 'pending', stage: null, progress: 'x', message: '', updated: 1 },
    null,
  ]);
  assert.deepEqual(g.active.map((j) => j.id), ['2', '4', '5']);
  assert.deepEqual(g.finished.map((j) => j.id), ['1', '3']);
  assert.equal(g.anyActive, true);
  assert.equal(g.running, 2);
  assert.equal(g.queued, 1);
  assert.equal(g.active[0].progress, 42);
  assert.equal(g.active[2].progress, 0);
  assert.equal(g.finished[0].stem, 'v1');
  assert.equal(g.finished[0].resultsHash, '#/results/v1?mode=match');
  assert.equal(g.finished[1].resultsHash, null, 'a failed job has no results link');
  assert.equal(groupJobs([]).anyActive, false);
  assert.equal(groupJobs({ jobs: [{ id: 'x', status: 'running' }] }).active.length, 1);
});
