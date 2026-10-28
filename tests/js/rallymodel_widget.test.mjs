import test from 'node:test';
import assert from 'node:assert/strict';
import { installEnv, removeEnv, tick, everything } from './fake_dom.mjs';
import { cardModel, historyRow, rallyModelCard, totalsLines } from '../../static/kestrel/widgets/rallymodel.js';
import { describeCode } from '../../static/kestrel/widgets/strokemodel.js';
import { groupJobs, jobActions, jobMessage } from '../../static/kestrel/views/pipeline.js';
import { STRINGS } from '../../static/kestrel/core/i18n.js';

const NONE = { segmenter: null, decoder: null };
const body = (extra = {}) => ({
  live: NONE, counts: { matches: 2, points: 49, new_points: 49 }, can_retrain: true, missing: [],
  history: [], job: null, can_rollback: false, ...extra,
});
// The first real run on the owner's two labelled matches: both models lose their keep rules.
const REJECTED_RUN = {
  utc: '2026-10-28T09:30:00Z', kept: false, replaced: null, points: 49,
  models: {
    segmenter: { kept: false, reason: { code: 'lower_f1', candidate: 0.4444, swing: 0.6481 } },
    decoder: { kept: false, reason: { code: 'precision_unreachable', target: 0.9 } },
  },
  totals: { seg_f1: 0.4444, swing_f1: 0.6481, points: 49, dec_claimed: 0, dec_correct: 0, dec_precision: null,
    rules_claimed: 4, rules_correct: 3, rules_precision: 0.75, threshold: null },
};

test('card states: loading, not enough, ready, running', () => {
  assert.equal(cardModel(null, 'en').state, 'loading');
  assert.equal(cardModel(body({ pending: true, live: null }), 'en').state, 'loading');
  const low = cardModel(body({ can_retrain: false, missing: [{ code: 'matches', need: 2, have: 1 }] }), 'en');
  assert.equal(low.state, 'not_enough');
  assert.deepEqual(low.missing, ['Label points on another match']);
  assert.equal(cardModel(body(), 'en').state, 'ready');
  const running = cardModel(body({ can_rollback: true, job: { id: 'resegment_rallies', status: 'running', message: 'x' } }), 'en');
  assert.equal(running.state, 'running');
  assert.equal(running.canRetrain, false);
  assert.equal(running.canRollback, false);
  assert.equal(cardModel(body({ can_rollback: true }), 'en').canRollback, true);
});

test('with no kept model the card says the live behaviour is swing and the rules', () => {
  const m = cardModel(body(), 'en');
  assert.equal(m.live.segmenter.live, false);
  assert.match(m.live.segmenter.text, /not in use \(swing segments\)/);
  assert.match(m.live.decoder.text, /not in use \(rule cascade\)/);
  assert.match(cardModel(body(), 'zh').live.segmenter.text, /摆动分段/);
});

test('live models show their held-out scores against swing and the rules', () => {
  const live = {
    segmenter: { version: '20261028T000000Z', mode: 'gap', heldout: { f1: 0.7, swing_f1: 0.5 } },
    decoder: { version: '20261028T000001Z', heldout: { precision: 0.92, correct: 18, rules_correct: 3 } },
  };
  const m = cardModel(body({ live }), 'en');
  assert.equal(m.live.segmenter.text, 'Segmenter 20261028T000000Z: held-out point-end F1 70%, swing segments 50%');
  assert.equal(m.live.decoder.text, 'Winner decoder 20261028T000001Z: precision 92%, 18 correct claims, rules 3');
  assert.match(cardModel(body({ live }), 'zh').live.decoder.text, /准确率 92%，判对 18 个，规则判对 3 个/);
});

test('a rejected run says why, with the numbers, in both languages', () => {
  const en = cardModel(body({ history: [REJECTED_RUN] }), 'en');
  assert.equal(en.lastRun.when, '2026-10-28 09:30');
  assert.deepEqual(en.lastRun.models.map((x) => x.text), [
    'Segmenter: rejected (point-end F1 44% is below the swing segments 65%)',
    'Winner decoder: rejected (no confidence threshold reaches 90% held-out precision)',
  ]);
  assert.deepEqual(en.lastRun.numbers, [
    'Segmenter: held-out point-end F1 44%, swing segments 65%',
    'Winner decoder: 0/0 correct claims (precision —), rules 3/4',
  ]);
  const zh = cardModel(body({ history: [REJECTED_RUN] }), 'zh');
  assert.equal(zh.lastRun.models[0].text, '分段模型: 未采用 (得分点 F1 44%，低于摆动分段 65%)');
  assert.match(zh.lastRun.models[1].text, /没有任何置信度阈值能达到 90%/);
  assert.match(zh.lastRun.numbers[1], /规则判对 3\/4 个/);
  assert.equal(en.history[0].kept, false);
  assert.match(en.history[0].text, /Segmenter: rejected .*; Winner decoder: rejected/);
});

test('history: newest first, kept (one model), rolled back', () => {
  const kept = { utc: '2026-10-29T10:00:00Z', kept: true, points: 60, models: {
    segmenter: { kept: true, reason: { code: 'f1_ok', candidate: 0.8, swing: 0.6 } },
    decoder: { kept: false, reason: { code: 'fewer_correct', correct: 2, rules: 3 } } } };
  const rolled = { utc: '2026-10-30T10:00:00Z', kept: true, rollback: true };
  const m = cardModel(body({ history: [REJECTED_RUN, kept, rolled], can_rollback: true }), 'en');
  assert.equal(m.history[0].rollback, true);
  assert.equal(m.history[1].kept, true);
  assert.match(m.history[1].text, /Segmenter: kept \(point-end F1 80%, not below the swing segments 60%\); Winner decoder: rejected \(2 correct claims, fewer than the rules 3\)/);
  assert.equal(m.lastRun.models[0].kept, true);                 // the rollback is not a run
  assert.equal(historyRow(rolled, 'zh').text, '已回滚');
  assert.equal(historyRow({ utc: '2026-10-01T10:00:00Z', kept: true }, 'en').text, 'kept');
});

test('totalsLines needs the numbers; a missing one shows as a dash or ?', () => {
  assert.deepEqual(totalsLines({}, 'en'), []);
  const lines = totalsLines({ points: 5, seg_f1: null, swing_f1: 0.5, dec_claimed: null, dec_correct: null, rules_claimed: 1, rules_correct: 1 }, 'en');
  assert.equal(lines[0], 'Segmenter: held-out point-end F1 —, swing segments 50%');
  assert.match(lines[1], /\?\/\? correct claims/);
});

test('counts are not percentages; percentages are', () => {
  assert.equal(describeCode({ code: 'fewer_correct', correct: 2, rules: 3 }, 'en'), '2 correct claims, fewer than the rules 3');
  assert.equal(describeCode({ code: 'low_precision', value: 0.8, bar: 0.85 }, 'en'), 'held-out precision 80% is below the bar 85%');
  assert.equal(describeCode({ code: 'new_points', need: 20, have: 5 }, 'en'), '15 more point labels needed (20 new in total)');
  assert.equal(describeCode({ code: 'lower_f1', candidate: null, swing: 0.5 }, 'en'), 'point-end F1 ? is below the swing segments 50%');
});

test('pipeline: a rally job row localises its reason code and names the model', () => {
  const g = groupJobs([{ id: 'train_rally', video: 'rally-models', kind: 'training', status: 'completed',
    message: '回合模型未采用 / rally models not kept',
    reason_code: { code: 'lower_f1', candidate: 0.4444, swing: 0.6481, model: 'segmenter' } }]);
  const row = g.finished[0];
  assert.equal(row.kind, 'training');
  assert.equal(jobMessage(row, 'en'), 'rally models not kept: Segmenter: point-end F1 44% is below the swing segments 65%');
  assert.match(jobMessage(row, 'zh'), /^回合模型未采用: 分段模型: 得分点 F1 44%，低于摆动分段 65%$/);
  assert.deepEqual(jobActions({ status: 'running', kind: 'training' }), ['cancel']);
  assert.deepEqual(jobActions({ status: 'pending', kind: 'training' }), ['cancel']);
});

test('every rally card string exists in both languages', () => {
  const keys = (lang) => Object.keys(STRINGS[lang]).filter((k) => k.startsWith('rm.') || k.startsWith('stage.rally_'));
  assert.deepEqual(keys('zh').sort(), keys('en').sort());
  assert.ok(keys('en').length > 30);
  for (const k of keys('zh')) assert.match(STRINGS.zh[k], /[一-鿿]/, k);
});

function make(data, api) {
  const changed = [];
  const card = rallyModelCard({ lang: 'en', api, onChanged: () => changed.push(1) });
  card.update(data);
  document.body.appendChild(card.el);
  return { card, changed };
}
const btn = (card, name) => everything(card.el).find((e) => e.getAttribute && e.getAttribute('data-rm') === name);

test('retrain and rollback buttons call the API; a refusal shows its text', async () => {
  installEnv();
  try {
    const calls = [];
    const api = {
      retrainRallyModel: async () => { calls.push('retrain'); return { job_id: 'train_rally' }; },
      rollbackRallyModel: async () => { calls.push('rollback'); return { ok: true }; },
    };
    const { card, changed } = make(body({ can_rollback: true }), api);
    const b = btn(card, 'retrain');
    assert.equal(b.hasAttribute('disabled'), false);
    b.dispatch('click', { currentTarget: b });
    await tick(5);
    const r = btn(card, 'rollback');
    r.dispatch('click', { currentTarget: r });
    await tick(5);
    assert.deepEqual(calls, ['retrain', 'rollback']);
    assert.equal(changed.length, 2);
    const failing = { retrainRallyModel: async () => { const e = new Error('busy'); e.status = 409; e.body = { error: '正在训练 / training is running' }; throw e; } };
    const second = make(body(), failing);
    const b2 = btn(second.card, 'retrain');
    b2.dispatch('click', { currentTarget: b2 });
    await tick(5);
    assert.ok(everything(second.card.el).some((e) => e.getAttribute && e.getAttribute('role') === 'alert'));
  } finally {
    removeEnv();
  }
});

test('card text: the last run and the live lines are on the page; retrain is disabled when not enough', () => {
  installEnv();
  try {
    const { card } = make(body({ history: [REJECTED_RUN], can_retrain: false, missing: [{ code: 'new_points', need: 20, have: 3 }] }), {});
    const text = everything(card.el).map((e) => e.textContent || '').join('\n');
    assert.match(text, /Segmenter: rejected \(point-end F1 44% is below the swing segments 65%\)/);
    assert.match(text, /Winner decoder: 0\/0 correct claims/);
    assert.match(text, /not in use \(swing segments\)/);
    assert.equal(btn(card, 'retrain').hasAttribute('disabled'), true);
    assert.match(btn(card, 'missing').textContent, /17 more point labels needed/);
    assert.equal(btn(card, 'rollback').hasAttribute('disabled'), true);
  } finally {
    removeEnv();
  }
});
