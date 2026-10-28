import test from 'node:test';
import assert from 'node:assert/strict';
import {
  rowsForRally, orphanLabels, applyAction, keyToAction, tally, normaliseLabels, TYPE_KEYS, STROKE_IDS,
} from '../../static/kestrel/views/strokelabels_model.js';

const FPS = 60;
const doc = (...strokes) => ({ schema: 2, strokes, removed: [] });
const st = (frame, hitter, stroke, extra = {}) => ({ frame, hitter, stroke, group: stroke, confidence: 0.6, ...extra });
const lab = (time_sec, stroke = 'smash', kind = 'label', hitter = 'lower') => ({ time_sec, hitter, stroke: kind === 'label' || kind === 'added' ? stroke : null, kind });
const labels = (...ls) => ({ schema: 1, revision: 2, split: 'train', labels: ls });
const RALLY = { start: 0, end: 10 };

test('rowsForRally: model rows are auto and limited to the rally', () => {
  const rows = rowsForRally(doc(st(60, 'lower', 'clear'), st(120, 'upper', 'lift'), st(3000, 'lower', 'drop')), labels(), RALLY, FPS);
  assert.deepEqual(rows.map((r) => [r.time, r.hitter, r.stroke, r.state, r.source]),
    [[1, 'lower', 'clear', 'auto', 'model'], [2, 'upper', 'lift', 'auto', 'model']]);
  assert.equal(rowsForRally(doc(st(60, 'lower', 'clear')), labels(), RALLY, null).length, 0);
});

test('rowsForRally: states from the labels', () => {
  const strokes = doc(st(60, 'lower', 'clear'), st(120, 'upper', 'lift'), st(180, 'lower', 'drop'), st(240, 'upper', 'drop'), st(300, 'lower', 'push'));
  const rows = rowsForRally(strokes, labels(
    lab(1.0, 'clear'), lab(2.0, 'smash'), lab(3.0, null, 'not_hit'), lab(4.0, null, 'unsure'), lab(6.0, 'net_shot', 'added', 'upper'),
  ), RALLY, FPS);
  assert.deepEqual(rows.map((r) => r.state), ['confirmed', 'corrected', 'not_hit', 'unsure', 'auto', 'added']);
  assert.equal(rows[1].stroke, 'smash');
  assert.equal(rows[1].model_stroke, 'lift');
  assert.equal(rows[5].hitter, 'upper');
  assert.equal(rows[5].label.kind, 'added');
});

test('rowsForRally: a swapped hitter is corrected; a label attaches within 0.15 s only', () => {
  const rows = rowsForRally(doc(st(60, 'lower', 'clear')), labels(lab(1.0, 'clear', 'label', 'upper')), RALLY, FPS);
  assert.equal(rows[0].state, 'corrected');
  assert.equal(rows[0].hitter, 'upper');
  const far = rowsForRally(doc(st(60, 'lower', 'clear')), labels(lab(1.4, 'smash')), RALLY, FPS);
  assert.equal(far[0].state, 'auto');
});

test('rowsForRally reads an already applied strokes.json back to the model view', () => {
  const applied = {
    schema: 2,
    strokes: [st(60, 'upper', 'smash', { source: 'owner', model_stroke: 'clear', model_group: 'clear', model_hitter: 'lower' }),
      st(600, 'lower', 'lift', { source: 'owner' })],
    removed: [st(120, 'lower', 'drop', { source: 'model' })],
  };
  const withLabels = rowsForRally(applied, labels(lab(1.0, 'smash', 'label', 'upper')), RALLY, FPS);
  assert.deepEqual(withLabels.map((r) => [r.time, r.state, r.model_stroke]), [[1, 'corrected', 'clear'], [2, 'auto', 'drop']]);
  // labels cleared locally: back to the model's own rows (the owner-added one is gone, the removed one is back)
  const none = rowsForRally(applied, labels(), RALLY, FPS);
  assert.deepEqual(none.map((r) => [r.time, r.stroke, r.hitter, r.state]), [[1, 'clear', 'lower', 'auto'], [2, 'drop', 'lower', 'auto']]);
});

const row = (over = {}) => ({ time: 1, hitter: 'lower', stroke: 'clear', state: 'auto', label: null, ...over });

test('applyAction set / confirm / swap', () => {
  let d = applyAction(labels(), row(), { type: 'set', stroke: 'smash' });
  assert.deepEqual(d.labels, [lab(1, 'smash')]);
  assert.equal(d.revision, 2, 'the revision is kept for the save');
  d = applyAction(labels(), row(), { type: 'confirm' });
  assert.deepEqual(d.labels, [lab(1, 'clear')]);
  const owned = row({ label: lab(1, 'smash'), stroke: 'smash', state: 'corrected' });
  d = applyAction(labels(lab(1, 'smash')), owned, { type: 'swap' });
  assert.deepEqual(d.labels, [lab(1, 'smash', 'label', 'upper')], 'one label per hit: the old one is replaced');
  const unknown = row({ stroke: 'unknown' });
  assert.deepEqual(applyAction(labels(), unknown, { type: 'swap' }).labels, []);
  assert.deepEqual(applyAction(labels(), unknown, { type: 'confirm' }).labels, []);
  assert.deepEqual(applyAction(labels(), unknown, { type: 'set', stroke: 'lift' }).labels, [lab(1, 'lift')]);
  assert.deepEqual(applyAction(labels(), row(), { type: 'set', stroke: 'wallop' }).labels, []);
});

test('applyAction not_hit / unsure / clear', () => {
  assert.deepEqual(applyAction(labels(), row(), { type: 'not_hit' }).labels, [lab(1, null, 'not_hit')]);
  assert.deepEqual(applyAction(labels(), row(), { type: 'unsure' }).labels, [lab(1, null, 'unsure')]);
  const owned = row({ label: lab(1, 'smash'), state: 'corrected' });
  assert.deepEqual(applyAction(labels(lab(1, 'smash'), lab(5, 'lift')), owned, { type: 'clear' }).labels, [lab(5, 'lift')]);
  assert.deepEqual(applyAction(labels(lab(1, 'smash')), owned, { type: 'not_hit' }).labels, [lab(1, null, 'not_hit')]);
});

test('applyAction add, and actions on an added row', () => {
  const d = applyAction(labels(), null, { type: 'add', time: 6.5, hitter: 'upper', stroke: 'lift' });
  assert.deepEqual(d.labels, [lab(6.5, 'lift', 'added', 'upper')]);
  assert.deepEqual(applyAction(labels(), null, { type: 'add', time: -1, hitter: 'upper', stroke: 'lift' }).labels, []);
  assert.deepEqual(applyAction(labels(), null, { type: 'add', time: 1, hitter: 'both', stroke: 'lift' }).labels, []);
  const added = row({ time: 6.5, hitter: 'upper', stroke: 'lift', state: 'added', label: lab(6.5, 'lift', 'added', 'upper') });
  const base = labels(added.label);
  assert.deepEqual(applyAction(base, added, { type: 'set', stroke: 'drop' }).labels, [lab(6.5, 'drop', 'added', 'upper')]);
  assert.deepEqual(applyAction(base, added, { type: 'swap' }).labels, [lab(6.5, 'lift', 'added', 'lower')]);
  assert.deepEqual(applyAction(base, added, { type: 'not_hit' }).labels, [], 'an added shot that is no shot goes away');
});

test('keyToAction covers every type once, plus P X ? Enter', () => {
  const keys = Object.values(TYPE_KEYS);
  assert.equal(new Set(keys).size, STROKE_IDS.length);
  for (const [id, k] of Object.entries(TYPE_KEYS)) {
    assert.deepEqual(keyToAction(k), { type: 'set', stroke: id });
    assert.deepEqual(keyToAction(k.toLowerCase()), { type: 'set', stroke: id });
  }
  assert.deepEqual(keyToAction('p'), { type: 'swap' });
  assert.deepEqual(keyToAction('X'), { type: 'not_hit' });
  assert.deepEqual(keyToAction('?'), { type: 'unsure' });
  assert.deepEqual(keyToAction('Enter'), { type: 'confirm' });
  assert.equal(keyToAction('q'), null);
  assert.equal(keyToAction(undefined), null);
});

test('tally counts labelled shots and agreement with the model', () => {
  const strokes = doc(st(60, 'lower', 'clear'), st(120, 'upper', 'lift'), st(180, 'lower', 'drop'), st(240, 'upper', 'drop'));
  const t = tally(strokes, labels(lab(1, 'clear'), lab(2, 'smash'), lab(3, null, 'not_hit'), lab(9, 'lift', 'added')), FPS);
  assert.deepEqual(t, { labelled: 2, total: 4, agree: 1, agreeShare: 0.5 });
  assert.deepEqual(tally(strokes, labels(), FPS), { labelled: 0, total: 4, agree: 0, agreeShare: null });
  assert.equal(normaliseLabels({ split: 'bogus', revision: -3, labels: [null, { time_sec: 'x' }] }).split, 'train');
});

test('rowsForRally with the workspace rally list uses its membership (inside, else nearest within 0.5 s)', () => {
  const rallies = [{ id: 1, start: 0, end: 5 }, { id: 2, start: 6, end: 10 }];
  const strokes = doc(st(60, 'lower', 'clear'), st(162, 'upper', 'lift'), st(325, 'lower', 'drop'), st(1200, 'lower', 'push'));
  // 5.4 s is 0.4 s past rally 1 (outside the old 0.25 s window, inside the workspace's 0.5 s pad); 5.42 s is nearer rally 1
  const one = rowsForRally(strokes, labels(lab(5.45, 'smash', 'added')), rallies[0], FPS, rallies);
  assert.deepEqual(one.map((r) => r.time), [1, 2.7, 5.417, 5.45]);
  const two = rowsForRally(strokes, labels(), rallies[1], FPS, rallies);
  assert.deepEqual(two.map((r) => r.time), []);
  // a shot outside every rally belongs to no rally ...
  assert.equal(one.concat(two).some((r) => r.time === 20), false);   // 20 s
  // ... but the all view lists it
  assert.deepEqual(rowsForRally(strokes, labels(), null, FPS, rallies).map((r) => r.time), [1, 2.7, 5.417, 20]);
});

test('rowsForRally rounds times like the workspace, so a shot on the pad edge counts in both', () => {
  // seen live on 0003: the shot at 3.353 s is 0.5003 s past rally 1 (end 2.853 s); the workspace rounds it to 3.35
  const rallies = [{ id: 1, start: 0.05, end: 2.853 }, { id: 2, start: 4.555, end: 7.291 }];
  const rows = rowsForRally(doc(st(201.18, 'lower', 'net_rush')), labels(), rallies[0], FPS, rallies);
  assert.deepEqual(rows.map((r) => r.time), [3.353]);
});

test('orphanLabels: labels no hit carries, scoped to the rally; added labels are never orphans', () => {
  const rallies = [{ id: 1, start: 0, end: 5 }, { id: 2, start: 6, end: 10 }];
  const strokes = doc(st(60, 'lower', 'clear'));
  const ls = labels(lab(1.0, 'clear'), lab(3.0, 'smash'), lab(7.0, null, 'not_hit', 'upper'), lab(4.0, 'drop', 'added'));
  const all = orphanLabels(strokes, ls, null, FPS, rallies);
  assert.deepEqual(all.map((o) => [o.time, o.hitter, o.stroke, o.kind]), [[3, 'lower', 'smash', 'label'], [7, 'upper', null, 'not_hit']]);
  assert.deepEqual(orphanLabels(strokes, ls, rallies[1], FPS, rallies).map((o) => o.time), [7]);
  assert.deepEqual(orphanLabels(strokes, ls, null, null, rallies), []);
  assert.equal(all[0].label, ls.labels[1]);
});
