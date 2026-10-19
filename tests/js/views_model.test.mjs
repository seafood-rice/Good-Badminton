// Pure model builders of the Matches, Workspace, Players and Pipeline views. The DOM layers over them are
// verified in the browser; these tests pin the joins and the honesty rules (no outcome, estimated rallies).
import test from 'node:test';
import assert from 'node:assert/strict';
import {
  buildWorkspaceModel, summariseRallyPayload, capItems, STROKE_PAGE, matchDate, readReduced,
} from '../../static/kestrel/views/workspace.js';
import { matchRows, validateRecordForm, formFromRecord, peekModel, rowTitle } from '../../static/kestrel/views/matches.js';
import { groupBySquad } from '../../static/kestrel/views/players.js';
import { groupJobs, jobsKey, createPoller, FAST_MS, SLOW_MS } from '../../static/kestrel/views/pipeline.js';
import { viewModel } from '../../static/kestrel/views/overview.js';

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

// ---------- Task 11 review fixes ----------

test('workspace: the 0.5 s pad gives a stroke 0.3 s after a rally end that rally; 0.7 s after it none', () => {
  const m = buildWorkspaceModel({
    rallies: rallyPayload(),
    strokes: { strokes: [
      { frame: 6.3 * FPS, hitter: 'lower', stroke: 'net' }, // rally 1 ends at 6 s
      { frame: 6.7 * FPS, hitter: 'lower', stroke: 'net' },
      { frame: 9.8 * FPS, hitter: 'upper', stroke: 'net' }, // 0.2 s before rally 2 starts
    ] },
    fps: FPS,
  });
  assert.deepEqual(m.strokes.map((s) => s.rallyId), [1, null, 2]);
  assert.equal(m.rallies[0].strokeCount, 1);
});

test('workspace: degraded runs withhold labels unless the file says otherwise; unknown hitters are not opponents', () => {
  const deg = buildWorkspaceModel({ rallies: rallyPayload('swing', { degraded: true }), strokes: strokesPayload(), fps: FPS });
  assert.ok(deg.strokes.every((s) => s.uncertain));
  const kept = buildWorkspaceModel({ rallies: rallyPayload('swing', { degraded: true, stroke_labels_withheld: false }), strokes: strokesPayload(), fps: FPS });
  assert.equal(kept.strokes[0].label, 'serve');
  const m = buildWorkspaceModel({ strokes: { strokes: [{ frame: 10, hitter: null, stroke: 'net' }] }, fps: FPS, athleteSide: 'lower' });
  assert.equal(m.strokes[0].opponent, false);
  assert.equal(m.strokes[0].score, null);
});

test('capItems: pages of 200, grown so the selected stroke is always present', () => {
  const items = Array.from({ length: 1000 }, (_, k) => ({ i: k + 1 }));
  const a = capItems(items);
  assert.equal(STROKE_PAGE, 200);
  assert.equal(a.items.length, 200);
  assert.equal(a.rest, 800);
  const b = capItems(items, 200, (s) => s.i === 450);
  assert.equal(b.limit, 600);
  assert.equal(b.items.length, 600);
  assert.ok(b.items.some((s) => s.i === 450));
  assert.deepEqual(capItems(items.slice(0, 5)), { items: items.slice(0, 5), rest: 0, limit: 200 });
  assert.deepEqual(capItems(null).items, []);
  assert.equal(capItems(items, 400).items.length, 400);
});

test('matchDate: the record date, else the sidecar date, never anything else', () => {
  assert.equal(matchDate({ date: '2026-10-11' }, { date: { value: '2026-10-10', source: 'filename' } }), '2026-10-11');
  assert.equal(matchDate({}, { date: { value: '2026-10-10', source: 'filename' } }), '2026-10-10');
  assert.equal(matchDate({ date: 'junk' }, { date: { value: null, source: 'mtime' } }), null);
  assert.equal(matchDate(null, null), null);
});

test('readReduced accepts a boolean or a function', () => {
  assert.equal(readReduced({ reduced: true }), true);
  assert.equal(readReduced({ reduced: false }), false);
  assert.equal(readReduced({ reduced: () => true }), true);
  const live = { get reduced() { return true; } };
  assert.equal(readReduced(live), true);
});

test('peekModel with no athlete side: sets read from the lower side, the first-named side of the title', () => {
  const pm = peekModel({
    stem: 's', record: { upper: { name: 'S. Ito' }, lower: { name: 'J. Tanaka' }, result: { winner: 'upper', sets: [[21, 18], [19, 21]] } },
    insights: null, athletes: null, athleteId: '',
  });
  assert.equal(pm.side, null);
  assert.equal(pm.first, 'lower');
  assert.deepEqual(pm.sets, [[18, 21], [21, 19]]);
  assert.equal(pm.result, null, 'no W/L without an athlete');
  assert.equal(pm.winner, 'upper');
  const up = peekModel({ record: { upper: { athlete_id: 'a1' }, result: { winner: 'upper', sets: [[21, 18]] } }, athleteId: 'a1' });
  assert.equal(up.first, 'upper');
  assert.deepEqual(up.sets, [[21, 18]]);
  assert.equal(up.result, 'W');
});

test('rowTitle: names when known, else the video name with the side labels as a subtitle', () => {
  assert.deepEqual(rowTitle({ stem: 'Dji 0005', side: 'lower', athlete: null, opponent: null }, 'en'),
    { title: 'Dji 0005', sub: 'Lower side vs. Upper side' });
  assert.deepEqual(rowTitle({ stem: 'v', side: 'lower', athlete: 'J. Tanaka', opponent: null }, 'en'),
    { title: 'J. Tanaka vs. Upper side', sub: 'v' });
  assert.equal(rowTitle({ stem: 'v', side: 'upper', athlete: 'A', opponent: 'B' }, 'zh').title, 'A 对 B');
});

test('jobsKey ignores progress and message, follows id, status and stage', () => {
  const base = [{ id: '1', video: 'v', status: 'running', stage: 'analyzing', progress: 10, message: 'a' }];
  const k = jobsKey(groupJobs(base));
  assert.equal(jobsKey(groupJobs([{ ...base[0], progress: 55, message: 'b' }])), k);
  assert.notEqual(jobsKey(groupJobs([{ ...base[0], stage: 'encoding' }])), k);
  assert.notEqual(jobsKey(groupJobs([{ ...base[0], status: 'completed' }])), k);
  assert.notEqual(jobsKey(groupJobs([...base, { id: '2', video: 'w', status: 'pending' }])), k);
  assert.equal(jobsKey(null), '');
});

function fakeTimers() {
  const pending = new Map();
  let seq = 0;
  return {
    pending,
    setTimer: (fn, ms) => { seq += 1; pending.set(seq, { fn, ms }); return seq; },
    clearTimer: (id) => { pending.delete(id); },
    next() { const [id, v] = [...pending.entries()][0]; pending.delete(id); return v; },
  };
}
const tick = () => new Promise((r) => setImmediate(r));

test('createPoller: 2 s while a job is active, 15 s otherwise or when hidden; errors keep polling', async () => {
  const timers = fakeTimers();
  let active = true;
  let hidden = false;
  let fail = false;
  const seen = [];
  const p = createPoller({
    fetch: async () => { if (fail) throw new Error('down'); return active; },
    onData: (d) => { seen.push(d); return d; },
    onError: () => { seen.push('err'); return false; },
    isHidden: () => hidden,
    setTimer: timers.setTimer, clearTimer: timers.clearTimer,
  });
  await p.poll();
  assert.equal(timers.pending.size, 1);
  assert.equal(timers.next().ms, FAST_MS);
  active = false;
  await p.poll();
  assert.equal(timers.next().ms, SLOW_MS);
  active = true;
  hidden = true;
  await p.poll();
  assert.equal(timers.next().ms, SLOW_MS);
  hidden = false;
  fail = true;
  await p.poll();
  assert.equal(timers.next().ms, SLOW_MS);
  assert.deepEqual(seen, [true, false, true, 'err']);
});

test('createPoller: a poll never overlaps; stop() clears the timer and ignores a late response', async () => {
  const timers = fakeTimers();
  let release;
  let calls = 0;
  let data = 0;
  const p = createPoller({
    fetch: () => { calls += 1; return new Promise((r) => { release = r; }); },
    onData: () => { data += 1; return true; },
    setTimer: timers.setTimer, clearTimer: timers.clearTimer,
  });
  const first = p.poll();
  p.poll(); // in flight: ignored
  assert.equal(calls, 1);
  release(true);
  await first;
  assert.equal(data, 1);
  assert.equal(timers.pending.size, 1);
  const second = timers.next().fn();
  p.stop();
  release(true);
  await second;
  await tick();
  assert.equal(data, 1, 'a response after stop() is dropped');
  assert.equal(timers.pending.size, 0, 'nothing is rescheduled after stop()');
  assert.equal(p.isStopped(), true);
});

// ---------- overview integration fixes ----------

const okIns = (value) => ({ status: 'ok', value, coverage: null, reason: null });
const noData = { status: 'unavailable', value: null, coverage: null, reason: { code: 'no_data', params: {} } };

test('overview: with no athlete in scope the win-rate gap asks for an athlete', () => {
  const vm = viewModel({ scope: { athlete: null, squad: null, range: '30d', stems: 2 }, kpis: { match_win_rate: noData } }, 'overview', 'en');
  const tile = vm.tiles.find((x) => x.id === 'match_win_rate');
  assert.equal(tile.kind, 'gap');
  assert.equal(tile.reason, 'Pick an athlete and record match results to see a win rate.');
  const withAthlete = viewModel({ scope: { athlete: { id: 'a', name: 'A' }, range: '30d', stems: 2 }, kpis: { match_win_rate: noData } }, 'overview', 'en');
  assert.equal(withAthlete.tiles.find((x) => x.id === 'match_win_rate').reason, 'This data could not be read.');
});

test('overview: avg rally shows seconds when shots is null, with the signal tag', () => {
  const vm = viewModel({
    scope: { range: '30d', stems: 1 },
    kpis: { avg_rally: okIns({ seconds: 18.46, shots: null, signal: 'swing' }) },
  }, 'overview', 'en');
  const tile = vm.tiles.find((x) => x.id === 'avg_rally');
  assert.equal(tile.value, 18.46);
  assert.equal(tile.unit, 's');
  assert.equal(tile.tag, 'SWING EST.');
  const shots = viewModel({ scope: { range: '30d', stems: 1 }, kpis: { avg_rally: okIns({ seconds: 9, shots: 7.5, signal: 'shuttle' }) } }, 'overview', 'en');
  assert.equal(shots.tiles.find((x) => x.id === 'avg_rally').unit, 'shots');
});

test('overview: recent matches keep one row per stem without an athlete, every unit with one', () => {
  const rows = [
    { stem: 'v1', side: 'lower', date: '2026-10-10', opponent: 'B', status: 'analysed' },
    { stem: 'v1', side: 'upper', date: '2026-10-10', opponent: 'A', status: 'analysed' },
    { stem: 'v2', side: 'lower', date: '2026-10-09', opponent: null, status: 'analysed' },
  ];
  const all = viewModel({ scope: { athlete: null, range: '30d', stems: 2 }, matches: rows }, 'overview', 'en');
  assert.deepEqual(all.matches.rows.map((r) => r.stem), ['v1', 'v2']);
  assert.equal(all.matches.total, 2);
  const one = viewModel({ scope: { athlete: { id: 'a', name: 'A' }, range: '30d', stems: 2 }, matches: rows }, 'overview', 'en');
  assert.equal(one.matches.rows.length, 3);
});
