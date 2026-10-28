// The scoring-format control in the DOUBLES match editor (it existed only in the singles editor): the form helpers
// carry `scoring` through, and mount() of the Matches view saves it on a doubles record.
import test from 'node:test';
import assert from 'node:assert/strict';
import { installEnv, removeEnv, tick, everything } from './fake_dom.mjs';
import { api } from '../../static/kestrel/core/api.js';
import { STRINGS } from '../../static/kestrel/core/i18n.js';
import { mount, validateDoublesRecordForm, formFromDoublesRecord, SCORING_PRESETS } from '../../static/kestrel/views/matches.js';

const PLAYERS = { P1: { name: 'A' }, P2: { name: 'B' }, P3: { name: 'C' }, P4: { name: 'D' } };
const STORED = { date: '2026-10-11', format: 'doubles', players: PLAYERS, result: null };

test('doubles form helpers: scoring round-trips and is absent when the form has none', () => {
  const rec = { ...STORED, scoring: { preset: 'custom', points_to_win: 11, win_by: 1, cap: null, best_of: 1, change_ends_after_game: false, change_ends_at: 6 } };
  const form = formFromDoublesRecord(rec);
  assert.deepEqual(validateDoublesRecordForm(form).record.scoring, rec.scoring);
  assert.equal('scoring' in formFromDoublesRecord(STORED), false);
  assert.equal('scoring' in validateDoublesRecordForm(formFromDoublesRecord(STORED)).record, false);
  const bad = validateDoublesRecordForm({ ...form, scoring: { ...form.scoring, win_by: '7' } });
  assert.equal(bad.errors.scoring, 'scoring.err');
  assert.equal(bad.record, null);
});

const OV = () => ({
  matches: [{ stem: 'd1', date: '2026-10-11', status: 'analysed', doubles: true, format: 'doubles', teams: null, parts: [] }],
});

async function withView(fn, { record = STORED } = {}) {
  const { doc } = installEnv();
  const saved = { ...api };
  const calls = { save: [], toasts: [] };
  api.overview = async () => OV();
  api.videos = async () => [];
  api.match = async () => record;
  api.videoInsights = async () => ({});
  api.saveMatch = async (stem, body) => { calls.save.push([stem, body]); return { ok: true }; };
  const container = doc.createElement('div');
  doc.body.appendChild(container);
  const inst = mount(container, { lang: 'en', query: {}, toast: (m) => calls.toasts.push(m) });
  try {
    await tick(5);
    container.querySelector('[data-rec="d1"]').click();
    await tick(10);
    await fn({ container, calls });
  } finally {
    inst.destroy();
    Object.assign(api, saved);
    removeEnv();
  }
}

const submit = async (container) => {
  container.querySelector('form.mv-form').dispatch('submit');
  await tick(10);
};
const control = (container, key) => everything(container).find((e) => (e.getAttribute('id') || '').endsWith(`-${key}`));

test('the doubles editor has the scoring fieldset with the default preset', async () => {
  await withView(async ({ container }) => {
    assert.ok(container.querySelector('.mv-scoring'), 'scoring fieldset in the doubles editor');
    assert.equal(control(container, 'preset').value, 'bwf_21x3');
    assert.equal(control(container, 'points_to_win').value, '21');
    assert.match(container.querySelector('.mv-scoring').textContent, /Scoring format/);
  });
});

test('saving a doubles record without touching the format keeps it without one', async () => {
  await withView(async ({ container, calls }) => {
    await submit(container);
    assert.equal(calls.save.length, 1);
    assert.equal(calls.save[0][1].format, 'doubles');
    assert.equal('scoring' in calls.save[0][1], false);
  });
});

test('picking a preset saves it on the doubles record', async () => {
  await withView(async ({ container, calls }) => {
    const preset = control(container, 'preset');
    preset.value = 'one_game_30';
    preset.dispatch('change');
    assert.equal(control(container, 'points_to_win').value, '30');
    await submit(container);
    const body = calls.save[0][1];
    assert.equal(body.format, 'doubles');
    assert.equal(body.scoring.points_to_win, 30);
    assert.equal(body.scoring.best_of, 1);
    assert.equal(body.scoring.preset, 'one_game_30');
  });
});

test('an existing format is shown and kept; editing a field makes it custom', async () => {
  await withView(async ({ container, calls }) => {
    assert.equal(control(container, 'preset').value, '15x3');
    assert.equal(control(container, 'points_to_win').value, '15');
    const pts = control(container, 'points_to_win');
    pts.value = '11';
    pts.dispatch('input');
    assert.equal(control(container, 'preset').value, 'custom');
    await submit(container);
    assert.equal(calls.save[0][1].scoring.points_to_win, 11);
    assert.equal(calls.save[0][1].scoring.preset, 'custom');
  }, { record: { ...STORED, scoring: SCORING_PRESETS['15x3'] } });
});

test('an invalid format shows its error and is not saved', async () => {
  await withView(async ({ container, calls }) => {
    const pts = control(container, 'points_to_win');
    pts.value = '99';
    pts.dispatch('input');
    await submit(container);
    assert.equal(calls.save.length, 0);
    assert.match(container.querySelector('.mv-scoring .mv-err').textContent, /Invalid scoring format/);
  });
});

test('a result impossible under the format warns in the editor and after saving, but is saved', async () => {
  await withView(async ({ container, calls }) => {
    const preset = control(container, 'preset');
    preset.value = 'one_game_30';
    preset.dispatch('change');
    const sets = container.querySelector('#mv-sets');
    sets.value = '31-29';
    sets.dispatch('input');
    assert.match(container.querySelector('.mv-warn').textContent, /not possible under this format/);
    const winner = container.querySelector('#mv-winner');
    winner.value = '1';
    await submit(container);
    assert.equal(calls.save.length, 1);
    assert.deepEqual(calls.save[0][1].result.sets, [[31, 29]]);
    assert.ok(calls.toasts.includes(STRINGS.en['scoring.warn']));
  });
});
