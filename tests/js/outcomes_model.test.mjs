// The pure model of rally-outcome labelling (spec 2026-10-27 sections 5.1, 5.2 and 7), including parity with the
// backend's re-attachment (badminton_analysis/outcomes/labels.py `attach`).
import test from 'node:test';
import assert from 'node:assert/strict';
import {
  normaliseLabels, labelForSegment, applyVerdict, addPoint, segmentState, tally, pooledTally, reconciliationStatus,
  keyToAction,
} from '../../static/kestrel/views/outcomes_model.js';

const doc = (labels, revision = 3) => ({ schema: 1, revision, labels });
const label = (end, verdict = 'lower', extra = {}) => ({ start_sec: end - 10, end_sec: end, verdict, outcome: null, added: false, ...extra });
const seg = (id, start, end) => ({ id, start, end });

test('normaliseLabels: junk reads as the empty document', () => {
  assert.deepEqual(normaliseLabels(null), { schema: 1, revision: 0, labels: [] });
  assert.deepEqual(normaliseLabels({ revision: 'x', labels: 5 }), { schema: 1, revision: 0, labels: [] });
  assert.equal(normaliseLabels(doc([label(5)], 7)).revision, 7);
});

test('labelForSegment: attaches when start - 1 <= end_sec <= end + 1', () => {
  const s = seg(1, 10, 20);
  assert.equal(labelForSegment(doc([label(20.9)]), s).end_sec, 20.9);
  assert.equal(labelForSegment(doc([label(21)]), s).end_sec, 21);
  assert.equal(labelForSegment(doc([label(21.1)]), s), null);
  assert.equal(labelForSegment(doc([label(9)]), s).end_sec, 9);
  assert.equal(labelForSegment(doc([label(8.9)]), s), null);
});

test('attach parity: a label made on 20-30 s re-attaches to the new 26-31 segment, not to 18-25', () => {
  const segs = [seg(1, 18, 25), seg(2, 26, 31)];
  const d = doc([{ start_sec: 20, end_sec: 30, verdict: 'upper', outcome: null, added: false }]);
  assert.equal(labelForSegment(d, segs[1], segs).verdict, 'upper');
  assert.equal(labelForSegment(d, segs[0], segs), null);
});

test('attach parity: a label fitting no segment is not attached; an added point never attaches', () => {
  const segs = [seg(1, 18, 25), seg(2, 26, 31)];
  assert.equal(labelForSegment(doc([label(50)]), segs[0], segs), null);
  assert.equal(labelForSegment(doc([label(50)]), segs[1], segs), null);
  assert.equal(labelForSegment(doc([label(22, 'lower', { added: true, start_sec: null })]), segs[0], segs), null);
});

test('attach parity: the nearest segment end wins when two segments fit', () => {
  const segs = [seg(1, 18, 25), seg(2, 26, 31)];
  const d = doc([label(25.6)]);
  assert.ok(labelForSegment(d, segs[0], segs));
  assert.equal(labelForSegment(d, segs[1], segs), null);
});

test('attach parity: a later label wins on the same segment', () => {
  const segs = [seg(1, 18, 25)];
  const d = doc([label(24, 'lower'), label(25, 'upper')]);
  assert.equal(labelForSegment(d, segs[0], segs).verdict, 'upper');
});

test('applyVerdict replaces the segment label, stores its times, is immutable and drops the outcome for non-winners', () => {
  const s = seg(1, 10, 20);
  const before = doc([label(20, 'lower'), label(50, 'upper')], 5);
  const snapshot = JSON.stringify(before);
  const after = applyVerdict(before, s, 'upper', 'winner', [s, seg(2, 40, 60)]);
  assert.equal(JSON.stringify(before), snapshot, 'input untouched');
  assert.equal(after.revision, 5);
  assert.equal(after.labels.length, 2);
  assert.deepEqual(after.labels[1], { start_sec: 10, end_sec: 20, verdict: 'upper', outcome: 'winner', added: false });
  assert.equal(applyVerdict(after, s, 'not_point', 'winner', [s]).labels.find((l) => l.end_sec === 20).outcome, null);
  assert.equal(applyVerdict(after, s, 'unsure', 'forced_error', [s]).labels.find((l) => l.end_sec === 20).outcome, null);
});

test('applyVerdict keeps a neighbour segment label that also fits this segment window', () => {
  const segs = [seg(1, 18, 25), seg(2, 26, 31)];
  const d = doc([label(25.2, 'lower')]); // nearest end: segment 1
  const after = applyVerdict(d, segs[1], 'upper', null, segs);
  assert.equal(after.labels.length, 2);
  assert.equal(labelForSegment(after, segs[0], segs).verdict, 'lower');
  assert.equal(labelForSegment(after, segs[1], segs).verdict, 'upper');
});

test('applyVerdict replaces a label made on an older segmentation of the same time', () => {
  const segs = [seg(1, 18, 25), seg(2, 26, 31)];
  const d = doc([{ start_sec: 20, end_sec: 30, verdict: 'lower', outcome: null, added: false }]);
  const after = applyVerdict(d, segs[1], 'upper', null, segs);
  assert.equal(after.labels.length, 1);
  assert.equal(after.labels[0].verdict, 'upper');
});

test('addPoint appends an added label with no start time', () => {
  const before = doc([label(5)], 2);
  const after = addPoint(before, 52.4, 'lower', 'winner');
  assert.equal(before.labels.length, 1);
  assert.deepEqual(after.labels[1], { start_sec: null, end_sec: 52.4, verdict: 'lower', outcome: 'winner', added: true });
  assert.equal(after.revision, 2);
  assert.equal(addPoint(before, 9, 'unsure', 'winner').labels[1].outcome, null);
});

const outcomes = (rallies) => ({ schema: 1, rallies, summary: { reconciliation: { status: 'consistent' } } });

test('segmentState: owner label beats the built file; near = lower, far = upper', () => {
  const s = seg(1, 10, 20);
  const built = outcomes([{ segment_id: 1, winner_side: 'upper', outcome: null, source: 'auto', confidence: 0.8 }]);
  assert.deepEqual(segmentState(built, doc([label(20, 'lower', { outcome: 'winner' })]), s),
    { state: 'near', source: 'owner', outcome: 'winner', auto: null, verdict: 'lower' });
  assert.equal(segmentState(built, doc([label(20, 'upper')]), s).state, 'far');
  assert.deepEqual(segmentState(built, doc([label(20, 'not_point')]), s),
    { state: 'rejected', source: 'owner', outcome: null, auto: null, verdict: 'not_point' });
});

test('segmentState: the auto result shows when there is no owner label, and survives an unsure verdict', () => {
  const s = seg(1, 10, 20);
  const built = outcomes([{ segment_id: 1, winner_side: 'upper', source: 'auto', confidence: 0.8 }]);
  const none = segmentState(built, doc([]), s);
  assert.equal(none.state, 'far');
  assert.equal(none.source, 'auto');
  assert.deepEqual(none.auto, { winner_side: 'upper', confidence: 0.8 });
  const unsure = segmentState(built, doc([label(20, 'unsure')]), s);
  assert.equal(unsure.state, 'far');
  assert.equal(unsure.source, 'auto');
});

test('segmentState: unknown when nothing is known; an unsure verdict alone is an owner-reviewed unknown', () => {
  const s = seg(1, 10, 20);
  assert.deepEqual(segmentState(null, doc([]), s), { state: 'unknown', source: null, outcome: null, auto: null, verdict: null });
  assert.equal(segmentState(outcomes([]), doc([label(20, 'unsure')]), s).source, 'owner');
  assert.equal(segmentState(outcomes([{ segment_id: 1, winner_side: null, source: null }]), doc([]), s).state, 'unknown');
});

test('segmentState: an owner entry in the built file that the local labels lack still shows', () => {
  const built = outcomes([{ segment_id: 1, winner_side: 'lower', outcome: 'winner', source: 'owner' }]);
  const st = segmentState(built, doc([]), seg(1, 10, 20));
  assert.equal(st.state, 'near');
  assert.equal(st.source, 'owner');
  assert.equal(st.outcome, 'winner');
});

test('tally counts near (lower) and far (upper) wins and the labelled share', () => {
  const built = outcomes([
    { winner_side: 'lower' }, { winner_side: 'lower' }, { winner_side: 'upper' }, { winner_side: null },
  ]);
  assert.deepEqual(tally(built), { near: 2, far: 1, labelled: 3, entries: 4 });
  assert.deepEqual(tally(null), { near: 0, far: 0, labelled: 0, entries: 0 });
});

test('pooledTally adds parts up in the primary frame: a swapped part exchanges near and far', () => {
  const a = outcomes([{ winner_side: 'lower' }, { winner_side: 'upper' }, { winner_side: 'upper' }]);
  const b = outcomes([{ winner_side: 'lower' }, { winner_side: 'lower' }, { winner_side: null }]);
  assert.deepEqual(pooledTally([a, b], [false, false]), { near: 3, far: 2, labelled: 5, entries: 6 });
  assert.deepEqual(pooledTally([a, b], [false, true]), { near: 1, far: 4, labelled: 5, entries: 6 });
  assert.deepEqual(pooledTally([a, null], []), { near: 1, far: 2, labelled: 3, entries: 3 });
});

test('reconciliationStatus: only the four visible statuses; no_result and absent hide the badge', () => {
  const withStatus = (status) => ({ summary: { reconciliation: { status } } });
  for (const s of ['consistent', 'over', 'short', 'incomplete']) assert.equal(reconciliationStatus(withStatus(s)), s);
  assert.equal(reconciliationStatus(withStatus('no_result')), null);
  assert.equal(reconciliationStatus({}), null);
  assert.equal(reconciliationStatus(null), null);
});

test('keyToAction maps n f x u (any case) to verdicts and 1 2 3 to outcomes', () => {
  assert.deepEqual(keyToAction('n'), { verdict: 'lower' });
  assert.deepEqual(keyToAction('N'), { verdict: 'lower' });
  assert.deepEqual(keyToAction('f'), { verdict: 'upper' });
  assert.deepEqual(keyToAction('X'), { verdict: 'not_point' });
  assert.deepEqual(keyToAction('u'), { verdict: 'unsure' });
  assert.deepEqual(keyToAction('1'), { outcome: 'winner' });
  assert.deepEqual(keyToAction('2'), { outcome: 'forced_error' });
  assert.deepEqual(keyToAction('3'), { outcome: 'unforced_error' });
  for (const k of ['a', '4', 'Enter', ' ', '', undefined, null]) assert.equal(keyToAction(k), null);
});
