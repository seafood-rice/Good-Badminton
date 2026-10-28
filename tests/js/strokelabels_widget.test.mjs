import test from 'node:test';
import assert from 'node:assert/strict';
import { installEnv, removeEnv, tick, everything, buttonWithText } from './fake_dom.mjs';
import { t } from '../../static/kestrel/core/i18n.js';
import { strokeLabelsBlock } from '../../static/kestrel/widgets/strokelabels.js';

const FPS = 60;
const st = (frame, hitter, stroke) => ({ frame, hitter, stroke, group: stroke, confidence: 0.6 });
const strokesDoc = { schema: 2, strokes: [st(60, 'lower', 'clear'), st(120, 'upper', 'lift'), st(180, 'lower', 'drop')], removed: [] };
const lab = (time_sec, stroke, kind = 'label', hitter = 'lower') => ({ time_sec, hitter, stroke, kind });

function fakeApi({ doc, put } = {}) {
  const calls = [];
  const current = { schema: 1, revision: 0, split: 'train', split_locked: false, labels: [], ...doc };
  return {
    calls,
    get: async (stem) => { calls.push(['GET', stem]); return current; },
    put: async (stem, body) => {
      calls.push(['PUT', stem, body]);
      if (put) return put(body);
      return { revision: body.revision + 1, applied: true };
    },
  };
}

function fakePlayer() {
  const p = { seeks: [], paused: 0, video: { currentTime: 4.25, pause() { p.paused += 1; } } };
  p.seekTo = (sec, fromSelection) => p.seeks.push([sec, fromSelection]);
  return p;
}

async function make(opts = {}) {
  const api = fakeApi(opts);
  const player = fakePlayer();
  const saved = [];
  const block = strokeLabelsBlock({ stem: 's', lang: 'en', api, getPlayer: () => player, onSaved: (r) => saved.push(r) });
  await block.load();
  block.update({ strokesDoc, rally: { start: 0, end: 10 }, fps: FPS });
  document.body.appendChild(block.el);
  return { block, api, player, saved };
}

const rowsOf = (block) => everything(block.el).filter((e) => e.getAttribute('class') && e.getAttribute('class').split(' ').includes('ws-sl-row'));
const key = (block, k) => block.el.dispatch('keydown', { key: k });

test('renders a row per shot with its state, and the tally', async () => {
  installEnv();
  try {
    const { block } = await make({ doc: { revision: 3, labels: [lab(1, 'smash')] } });
    const rows = rowsOf(block);
    assert.equal(rows.length, 3);
    assert.deepEqual(rows.map((r) => r.getAttribute('data-state')), ['corrected', 'auto', 'auto']);
    assert.ok(rows[0].textContent.includes('Smash') && rows[0].textContent.includes('Clear'), 'shows the model type it replaced');
    assert.ok(block.el.textContent.includes('1 of 3 shots labelled · model right on 0%'));
    block.destroy();
  } finally { removeEnv(); }
});

test('clicking a row seeks 0.5 s before the hit and pauses', async () => {
  installEnv();
  try {
    const { block, player } = await make();
    rowsOf(block)[1].click();
    assert.deepEqual(player.seeks, [[1.5, false]]);
    assert.equal(player.paused, 1);
    assert.equal(rowsOf(block)[1].getAttribute('aria-pressed'), 'true');
    block.destroy();
  } finally { removeEnv(); }
});

test('Enter confirms the selected row, saves and advances to the next row', async () => {
  installEnv();
  try {
    const { block, api, player, saved } = await make({ doc: { revision: 4 } });
    rowsOf(block)[0].click();
    const ev = key(block, 'Enter');
    assert.equal(ev.defaultPrevented, true);
    await tick();
    const put = api.calls.find((c) => c[0] === 'PUT');
    assert.deepEqual(put[2], { revision: 4, split: 'train', labels: [lab(1, 'clear')] });
    assert.deepEqual(saved, [{ revision: 5, applied: true }]);
    const rows = rowsOf(block);
    assert.equal(rows[0].getAttribute('data-state'), 'confirmed');
    assert.equal(rows[1].getAttribute('aria-pressed'), 'true', 'advanced to the next row');
    assert.deepEqual(player.seeks.at(-1), [1.5, false]);
    block.destroy();
  } finally { removeEnv(); }
});

test('a type key sets the stroke; keys do not bubble to the page and need a selected row', async () => {
  installEnv();
  try {
    const { block, api } = await make();
    let leaked = 0;
    document.body.addEventListener('keydown', () => { leaked += 1; });
    const none = key(block, 's');
    assert.equal(none.defaultPrevented, false, 'no row selected: the key is left to the page');
    assert.equal(leaked, 1);
    rowsOf(block)[1].click();
    leaked = 0;
    key(block, 's');
    await tick();
    assert.equal(leaked, 0, 'handled keys stop here, so the Point block keys never see them');
    assert.deepEqual(api.calls.find((c) => c[0] === 'PUT')[2].labels, [lab(2, 'smash', 'label', 'upper')]);
    block.destroy();
  } finally { removeEnv(); }
});

test('a 409 reloads the labels and says so', async () => {
  installEnv();
  try {
    const conflict = Object.assign(new Error('stale'), { status: 409, body: { reason: 'stale', revision: 7 } });
    const { block, api } = await make({ put: () => { throw conflict; } });
    rowsOf(block)[0].click();
    api.calls.length = 0;
    key(block, 'x');
    await tick();
    assert.deepEqual(api.calls.map((c) => c[0]), ['PUT', 'GET']);
    assert.ok(block.el.textContent.includes(t('strokes.conflict', 'en')));
    block.destroy();
  } finally { removeEnv(); }
});

test('a save skipped by the server shows why; an unreadable store is reported', async () => {
  installEnv();
  try {
    const skipped = await make({ put: (b) => ({ revision: b.revision + 1, applied: false, apply_skipped: 'job_active' }) });
    rowsOf(skipped.block)[0].click();
    key(skipped.block, 'c');
    await tick();
    assert.ok(skipped.block.el.textContent.includes(t('strokes.skipped.job_active', 'en')));
    const bad = await make({ put: () => { throw Object.assign(new Error('x'), { status: 409, body: { reason: 'unreadable' } }); } });
    rowsOf(bad.block)[0].click();
    key(bad.block, 'c');
    await tick();
    assert.ok(bad.block.el.textContent.includes(t('strokes.unreadable', 'en')));
  } finally { removeEnv(); }
});

test('an action the row cannot take does not save (swap of an unknown shot)', async () => {
  installEnv();
  try {
    const api = fakeApi();
    const block = strokeLabelsBlock({ stem: 's', lang: 'en', api, getPlayer: () => null });
    block.update({ strokesDoc: { schema: 2, strokes: [st(60, 'lower', 'unknown')] }, rally: { start: 0, end: 10 }, fps: FPS });
    document.body.appendChild(block.el);
    rowsOf(block)[0].click();
    key(block, 'p');
    await tick();
    assert.equal(api.calls.filter((c) => c[0] === 'PUT').length, 0);
  } finally { removeEnv(); }
});

test('Add missed shot here uses the player time, then a player and a type', async () => {
  installEnv();
  try {
    const { block, api } = await make();
    buttonWithText(block.el, t('strokes.add', 'en')).click();
    assert.ok(block.el.textContent.includes('0:04.3') || block.el.textContent.includes('0:04.2'));
    everything(block.el).find((e) => e.getAttribute('data-hitter') === 'upper').click();
    everything(block.el).find((e) => e.getAttribute('data-stroke') === 'net_shot').click();
    await tick();
    assert.deepEqual(api.calls.find((c) => c[0] === 'PUT')[2].labels, [lab(4.25, 'net_shot', 'added', 'upper')]);
    const rows = rowsOf(block);
    assert.equal(rows.at(-1).getAttribute('data-state'), 'added');
    block.destroy();
  } finally { removeEnv(); }
});

test('the train/test switch saves the split, and is disabled with a reason when locked', async () => {
  installEnv();
  try {
    const { block, api } = await make();
    buttonWithText(block.el, 'Test').click();
    await tick();
    assert.equal(api.calls.find((c) => c[0] === 'PUT')[2].split, 'test');
    block.destroy();
    const locked = await make({ doc: { split: 'test', split_locked: true } });
    const train = buttonWithText(locked.block.el, 'Train');
    assert.equal(train.disabled, true);
    const group = everything(locked.block.el).find((e) => e.getAttribute('data-split') === 'train').parentNode;
    assert.equal(group.getAttribute('title'), t('strokes.split_locked', 'en'));
    train.click();
    await tick();
    assert.equal(locked.api.calls.filter((c) => c[0] === 'PUT').length, 0);
    locked.block.destroy();
  } finally { removeEnv(); }
});

test('an empty rally says so', async () => {
  installEnv();
  try {
    const block = strokeLabelsBlock({ stem: 's', lang: 'en', api: fakeApi(), getPlayer: () => null });
    block.update({ strokesDoc: null, rally: { start: 0, end: 10 }, fps: FPS });
    assert.ok(block.el.textContent.includes(t('strokes.empty', 'en')));
  } finally { removeEnv(); }
});

test('orphaned owner labels are listed with a badge and can be cleared', async () => {
  installEnv();
  try {
    const { block, api } = await make({ doc: { revision: 6, labels: [lab(1, 'clear'), lab(8, 'smash', 'label', 'upper')] } });
    const orphan = rowsOf(block).find((r) => r.getAttribute('data-state') === 'orphaned');
    assert.ok(orphan, 'an orphan row');
    assert.ok(orphan.textContent.includes('0:08.0') && orphan.textContent.includes('Smash'));
    assert.ok(orphan.textContent.includes(t('strokes.state.orphaned', 'en')));
    const clear = everything(orphan).find((e) => e.getAttribute('data-action') === 'clear-orphan');
    clear.click();
    await tick();
    const put = api.calls.find((c) => c[0] === 'PUT');
    assert.deepEqual(put[2], { revision: 6, split: 'train', labels: [lab(1, 'clear')] });
    assert.equal(rowsOf(block).some((r) => r.getAttribute('data-state') === 'orphaned'), false);
    block.destroy();
  } finally { removeEnv(); }
});

test('with no rally selected the block lists every shot and keys keep working', async () => {
  installEnv();
  try {
    const { block, api } = await make();
    block.update({ strokesDoc, rally: null, rallies: [{ id: 1, start: 0, end: 1.5 }], fps: FPS });
    const rows = rowsOf(block);
    assert.equal(rows.length, 3, 'shots outside every rally are listed too');
    rows[2].click();
    key(block, 'c');
    await tick();
    assert.deepEqual(api.calls.find((c) => c[0] === 'PUT')[2].labels, [lab(3, 'clear')]);
    block.destroy();
  } finally { removeEnv(); }
});

test('the selected row is scrolled into view', async () => {
  installEnv();
  try {
    const { block } = await make();
    const seen = [];
    const orig = everything(block.el).find((e) => e.getAttribute('class') && e.getAttribute('class').includes('ws-sl-row'));
    assert.ok(orig);
    // every painted row gets the hook; the selected one is the one asked to scroll
    const proto = Object.getPrototypeOf(orig);
    const had = Object.prototype.hasOwnProperty.call(proto, 'scrollIntoView');
    const old = proto.scrollIntoView;
    proto.scrollIntoView = function scroll() { seen.push(this.getAttribute('data-time')); };
    try {
      rowsOf(block)[1].click();
    } finally {
      if (had) proto.scrollIntoView = old; else delete proto.scrollIntoView;
    }
    assert.deepEqual(seen, ['2']);
    block.destroy();
  } finally { removeEnv(); }
});
