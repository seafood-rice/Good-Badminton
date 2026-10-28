import test from 'node:test';
import assert from 'node:assert/strict';
import { installEnv, removeEnv, tick, everything } from './fake_dom.mjs';
import { cardModel, describeCode, historyRow, strokeModelCard } from '../../static/kestrel/widgets/strokemodel.js';
import { groupJobs, jobActions } from '../../static/kestrel/views/pipeline.js';

const live = (extra = {}) => ({ version: '2026-10-01T00:00:00Z', metrics: { accuracy: 0.9, unknown_rate: 0.05 }, bar_met: true, bar_reasons: [], ...extra });
const body = (extra = {}) => ({ live: live(), counts: { train: 70, test: 25, new_train: 60 }, can_retrain: true, missing: [], history: [], job: null, ...extra });

test('card states: loading, not enough labels, ready, running', () => {
  assert.equal(cardModel(null, 'en').state, 'loading');
  assert.equal(cardModel(body({ pending: true, live: null }), 'en').state, 'loading');
  const low = cardModel(body({ can_retrain: false, missing: ['3 more'] }), 'en');
  assert.equal(low.state, 'not_enough');
  assert.equal(low.canRetrain, false);
  assert.deepEqual(low.missing, ['3 more']);
  const ready = cardModel(body(), 'en');
  assert.equal(ready.state, 'ready');
  assert.equal(ready.canRetrain, true);
  const running = cardModel(body({ job: { id: 'train_strokes', status: 'running', message: 'x' } }), 'en');
  assert.equal(running.state, 'running');
  assert.equal(running.canRetrain, false);
  assert.equal(running.canRollback, false);
});

test('bar met / not met and no model', () => {
  assert.equal(cardModel(body(), 'en').barMet, true);
  const not = cardModel(body({ live: live({ bar_met: false, bar_reasons: ['recall'] }) }), 'en');
  assert.equal(not.barMet, false);
  assert.deepEqual(not.barReasons, ['recall']);
  const none = cardModel(body({ live: { version: null, metrics: null, bar_met: false, bar_reasons: ['no model'] } }), 'en');
  assert.equal(none.hasModel, false);
  assert.equal(none.scored, false);
});

test('history rows: newest first, kept / rejected / rolled back, rollback availability', () => {
  const history = [
    { utc: '2026-10-01T10:00:00Z', kept: true, replaced: 'h1', live: { accuracy: 0.7 }, candidate: { accuracy: 0.8 } },
    { utc: '2026-10-02T10:00:00Z', kept: false, reason: 'candidate accuracy 0.5 < live 0.8', live: { accuracy: 0.8 }, candidate: { accuracy: 0.5 } },
    { utc: '2026-10-03T10:00:00Z', kept: true, rollback: true, reason: 'rolled back' },
  ];
  const m = cardModel(body({ history }), 'en');
  assert.equal(m.history[0].rollback, true);
  assert.equal(m.history[1].kept, false);
  assert.match(m.history[1].text, /Rejected \(80% → 50%\): candidate accuracy/);
  assert.match(m.history[2].text, /Run kept|New model kept \(70% → 80%\)/);
  assert.equal(m.canRollback, true);
  assert.equal(cardModel(body({ history: [history[1]] }), 'en').canRollback, false);
  assert.equal(historyRow({ utc: '2026-10-01T10:00:00Z', kept: true }, 'zh').when, '2026-10-01 10:00');
});

test('pipeline: training jobs are their own kind; a running one can be cancelled but not paused', () => {
  const g = groupJobs([{ id: 'train_strokes', video: 'stroke-classifier', kind: 'training', status: 'running', progress: 0 }]);
  assert.equal(g.active[0].kind, 'training');
  assert.deepEqual(jobActions(g.active[0]), ['cancel']);
  assert.deepEqual(jobActions({ status: 'running', kind: 'strategy' }), []);
  assert.deepEqual(jobActions({ status: 'running', kind: 'match' }), ['pause']);
  assert.deepEqual(jobActions({ status: 'pending', kind: 'training' }), ['cancel']);
});

function make(data, api) {
  const changed = [];
  const card = strokeModelCard({ lang: 'en', api, onChanged: () => changed.push(1) });
  card.update(data);
  document.body.appendChild(card.el);
  return { card, changed };
}
const btn = (card, name) => everything(card.el).find((e) => e.getAttribute && e.getAttribute('data-sm') === name);

test('retrain button calls the API and reports the change; a refusal shows its text', async () => {
  installEnv();
  try {
    const calls = [];
    const api = { retrainStrokeModel: async () => { calls.push('retrain'); return { job_id: 'train_strokes' }; }, rollbackStrokeModel: async () => {} };
    const { card, changed } = make(body(), api);
    const b = btn(card, 'retrain');
    assert.equal(b.hasAttribute('disabled'), false);
    b.dispatch('click', { currentTarget: b });
    await tick(5);
    assert.deepEqual(calls, ['retrain']);
    assert.equal(changed.length, 1);
    const failing = { retrainStrokeModel: async () => { const e = new Error('busy'); e.status = 409; e.body = { error: '正在训练 / training is running' }; throw e; } };
    const second = make(body(), failing);
    const b2 = btn(second.card, 'retrain');
    b2.dispatch('click', { currentTarget: b2 });
    await tick(5);
    assert.ok(everything(second.card.el).some((e) => e.getAttribute && e.getAttribute('role') === 'alert'));
  } finally {
    removeEnv();
  }
});

test('retrain is disabled with not enough labels and the missing text is shown', () => {
  installEnv();
  try {
    const { card } = make(body({ can_retrain: false, missing: ['5 more train labels'] }), {});
    assert.equal(btn(card, 'retrain').hasAttribute('disabled'), true);
    assert.match(btn(card, 'missing').textContent, /5 more train labels/);
  } finally {
    removeEnv();
  }
});

test('codes with numbers are rendered in the UI language; plain strings pass through', () => {
  const tl = { code: 'train_labels', need: 50, have: 3 };
  assert.equal(describeCode(tl, 'en'), '47 more train labels needed (50 new in total)');
  assert.match(describeCode(tl, 'zh'), /还差 47 个训练标注/);
  assert.equal(describeCode({ code: 'accuracy', value: 0.81, bar: 0.85 }, 'en'), '7-group accuracy 81% is below the bar 85%');
  assert.match(describeCode({ code: 'lower_accuracy', candidate: 0.5, live: 0.8 }, 'zh'), /50%.*80%/);
  assert.equal(describeCode('old text', 'en'), 'old text');
  assert.equal(describeCode({ code: 'brand_new_code' }, 'en'), 'brand new code');
  assert.equal(describeCode(null, 'en'), '');
  const m = cardModel(body({
    can_retrain: false,
    missing: [tl, { code: 'test_shots', need: 20, have: 5 }],
    live: live({ bar_met: false, bar_reasons: [{ code: 'group_few', group: 'drive', need: 20, have: 7 }, { code: 'no_model' }] }),
    history: [{ utc: '2026-10-02T10:00:00Z', kept: false, reason: { code: 'lower_accuracy', candidate: 0.5, live: 0.8 }, live: { accuracy: 0.8 }, candidate: { accuracy: 0.5 } }],
  }), 'en');
  assert.deepEqual(m.missing, ['47 more train labels needed (50 new in total)', '15 more labelled test shots needed (20 in total)']);
  assert.deepEqual(m.barReasons, ['drive: fewer than 20 test hits (7)', 'No model yet']);
  assert.match(m.history[0].text, /Rejected \(80% → 50%\): New model accuracy 50% is below the current 80%/);
  const zh = cardModel(body({ can_retrain: false, missing: [tl] }), 'zh');
  assert.match(zh.missing[0], /还差 47/);
});
