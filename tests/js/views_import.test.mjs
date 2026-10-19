// Import-safety smoke for the Task 11 views: importing them under Node (no window, no document, no
// localStorage) must not throw, each exports mount(), and mount() without a DOM is a harmless no-op.
import test from 'node:test';
import assert from 'node:assert/strict';

const VIEWS = {
  'views/matches.js': ['mount', 'matchRows', 'validateRecordForm', 'formFromRecord', 'peekModel', 'buildMatchPeek', 'rowTitle'],
  'views/workspace.js': ['mount', 'buildWorkspaceModel', 'summariseRallyPayload', 'rallyCountText', 'capItems', 'matchDate', 'readReduced'],
  'views/players.js': ['mount', 'groupBySquad'],
  'views/pipeline.js': ['mount', 'groupJobs', 'messageText', 'jobsKey', 'createPoller'],
};

for (const [rel, names] of Object.entries(VIEWS)) {
  test(`${rel} imports without a DOM and exports ${names.join(', ')}`, async () => {
    assert.equal(typeof globalThis.document, 'undefined');
    const mod = await import(`../../static/kestrel/${rel}`);
    for (const n of names) assert.equal(typeof mod[n], 'function', `${rel} ${n}`);
    const inst = mod.mount({}, { lang: 'en', params: { stem: 'x' }, query: {} });
    assert.equal(typeof inst.destroy, 'function');
    inst.destroy();
  });
}

test('main.js still loads every view lazily', async () => {
  const { VIEW_LOADERS } = await import('../../static/kestrel/main.js');
  for (const key of ['matches', 'workspace', 'players', 'pipeline']) {
    const mod = await VIEW_LOADERS[key]();
    assert.equal(typeof mod.mount, 'function', key);
  }
});

test('pipeline messageText picks the UI language from a bilingual server message', async () => {
  const { messageText } = await import('../../static/kestrel/views/pipeline.js');
  assert.equal(messageText('分析完成! / Analysis complete', 'en'), 'Analysis complete');
  assert.equal(messageText('分析完成! / Analysis complete', 'zh'), '分析完成!');
  assert.equal(messageText('分析失败 (exit=1)', 'en'), '分析失败 (exit=1)');
  assert.equal(messageText(null, 'en'), '');
});

test('rallyCountText keeps the honest label', async () => {
  const { rallyCountText } = await import('../../static/kestrel/views/workspace.js');
  assert.match(rallyCountText({ count: 21, label: 'segments', suppressed: true, signal: 'swing' }, 'en'), /21 play segments \(estimated\)/);
  assert.match(rallyCountText({ count: 4, label: 'windows', suppressed: true }, 'en'), /coarse windows/);
  assert.match(rallyCountText({ count: 9, label: 'rallies', signal: 'shuttle' }, 'en'), /not yet validated/);
  assert.equal(rallyCountText({ failed: true }, 'en'), 'Rally detection failed');
  assert.ok(!/rall/i.test(rallyCountText({ count: 21, label: 'segments' }, 'en').replace('segments', '')));
});
