// Video tools (spec 2026-10-24 section 6): the tag editor, the tag manager and the delete dialog against the fake DOM
// with a stub api (no network).
import test from 'node:test';
import assert from 'node:assert/strict';
import { installEnv, removeEnv, tick, everything, buttonWithText, env } from './fake_dom.mjs';
import { openTagEditor, openTagManager, openDeleteDialog, isOpen, closeTool } from '../../static/kestrel/widgets/videotools.js';
import { ApiError } from '../../static/kestrel/core/api.js';
import { t } from '../../static/kestrel/core/i18n.js';

async function withEnv(fn) {
  installEnv();
  try {
    await fn(env.doc);
  } finally {
    closeTool();
    removeEnv();
  }
}

const dialogOf = (doc) => doc.body.querySelector('.mv-dlg');
const http = (status) => new ApiError('http', status, { zh: '错误', en: 'Error' });

test('tag editor: Enter adds a normalised tag and saves the whole list; Close reports the change', async () => {
  await withEnv(async (doc) => {
    const calls = [];
    const closes = [];
    const api = { setTags: async (stem, tags) => { calls.push([stem, tags]); return { ok: true, tags: [...tags].sort() }; } };
    const opener = doc.createElement('button');
    doc.body.appendChild(opener);
    opener.focus();
    const ed = openTagEditor({ video: { name: 'a b', tags: ['final'], has_match: true }, lang: 'en', knownTags: ['final', 'smash', 'semi'], api, onClose: (r) => closes.push(r) });
    assert.ok(isOpen());
    const dlg = dialogOf(doc);
    assert.match(dlg.textContent, /Tags · a b/);
    assert.match(dlg.textContent, /Match/, 'system tag shown read-only');
    // suggestions skip tags the video already has
    assert.deepEqual(everything(dlg).filter((e) => e.hasAttribute('data-add')).map((e) => e.getAttribute('data-add')), ['semi', 'smash']);
    ed.input.value = '  Smash ';
    ed.input.dispatch('keydown', { key: 'Enter' });
    await tick(1);
    assert.deepEqual(calls, [['a b', ['final', 'smash']]]);
    assert.equal(ed.input.value, '');
    assert.ok(dlg.querySelector('[data-rm="smash"]'));
    buttonWithText(dlg, 'Close').click();
    assert.equal(isOpen(), false);
    assert.deepEqual(closes, [{ changed: true, tags: ['final', 'smash'] }]);
    assert.equal(doc.activeElement, opener, 'focus returns to the opener');
  });
});

test('tag editor: a reserved or bad tag is refused locally; a failed save keeps the typed text', async () => {
  await withEnv(async (doc) => {
    let n = 0;
    const api = { setTags: async () => { n += 1; throw new ApiError('network', 0, { zh: '无法连接到服务', en: 'Cannot reach the server' }); } };
    const ed = openTagEditor({ video: { name: 'v', tags: [] }, lang: 'en', api });
    const dlg = dialogOf(doc);
    ed.input.value = 'Match';
    ed.input.dispatch('keydown', { key: 'Enter' });
    await tick(1);
    assert.equal(n, 0);
    assert.match(dlg.textContent, /Reserved/);
    ed.input.value = 'a/b';
    ed.input.dispatch('keydown', { key: 'Enter' });
    await tick(1);
    assert.match(dlg.textContent, /cannot contain \//);
    ed.input.value = 'drop shot';
    ed.input.dispatch('keydown', { key: 'Enter' });
    await tick(1);
    assert.equal(n, 1);
    assert.equal(ed.input.value, 'drop shot', 'kept for a retry');
    assert.match(dlg.textContent, /Cannot reach the server/);
    assert.equal(ed.input.disabled, false, 'controls unlocked again');
  });
});

test('tag editor: removing a tag saves the list without it; Escape closes', async () => {
  await withEnv(async (doc) => {
    const calls = [];
    const api = { setTags: async (stem, tags) => { calls.push(tags); return { ok: true, tags }; } };
    let closed = null;
    openTagEditor({ video: { name: 'v', tags: ['a', 'b'] }, lang: 'en', api, onClose: (r) => { closed = r; } });
    const dlg = dialogOf(doc);
    dlg.querySelector('[data-rm="a"]').click();
    await tick(1);
    assert.deepEqual(calls, [['b']]);
    dlg.dispatch('keydown', { key: 'Escape' });
    assert.equal(isOpen(), false);
    assert.deepEqual(closed, { changed: true, tags: ['b'] });
  });
});

test('delete dialog: confirm waits for the preview, then deletes and reports it', async () => {
  await withEnv(async (doc) => {
    const calls = [];
    let release;
    const preview = new Promise((r) => { release = r; });
    const api = {
      deletePreview: (stem) => { calls.push(['preview', stem]); return preview; },
      deleteVideo: async (stem) => { calls.push(['delete', stem]); return { ok: true }; },
    };
    let closed = null;
    const d = openDeleteDialog({ stem: 'x"y', lang: 'en', api, onClose: (r) => { closed = r; } });
    const dlg = dialogOf(doc);
    assert.match(dlg.textContent, /x"y/);
    assert.equal(d.confirmBtn.disabled, true);
    release({ ok: true, groups: [{ key: 'source', files: 1, size_mb: 120.5 }, { key: 'weird', files: 2, size_mb: 1 }], total_files: 3, total_size_mb: 121.5 });
    await tick(1);
    assert.equal(d.confirmBtn.disabled, false);
    assert.match(dlg.textContent, /Source video & court template/);
    assert.match(dlg.textContent, /weird/, 'an unknown group shows its key');
    assert.match(dlg.textContent, /3 files · 121.5 MB/);
    d.confirmBtn.click();
    await tick(1);
    assert.deepEqual(calls, [['preview', 'x"y'], ['delete', 'x"y']]);
    assert.equal(isOpen(), false);
    assert.deepEqual(closed, { deleted: true });
  });
});

test('delete dialog: 409 says an analysis is running and re-enables the buttons; nothing to delete keeps confirm off', async () => {
  await withEnv(async (doc) => {
    const api = {
      deletePreview: async () => ({ ok: true, groups: [{ key: 'match', files: 4, size_mb: 2 }], total_files: 4, total_size_mb: 2 }),
      deleteVideo: async () => { throw http(409); },
    };
    const d = openDeleteDialog({ stem: 'v', lang: 'en', api });
    await tick(1);
    d.confirmBtn.click();
    await tick(1);
    const dlg = dialogOf(doc);
    assert.match(dlg.textContent, /Analysis is running/);
    assert.equal(d.confirmBtn.disabled, false);
    assert.equal(buttonWithText(dlg, 'Cancel').disabled, false);
    assert.ok(isOpen());
    closeTool();

    const d2 = openDeleteDialog({ stem: 'v', lang: 'en', api: { deletePreview: async () => ({ ok: true, groups: [], total_files: 0 }) } });
    await tick(1);
    assert.match(dialogOf(doc).textContent, /Nothing to delete/);
    assert.equal(d2.confirmBtn.disabled, true);
  });
});

test('delete dialog: scope "clips" previews and deletes only that scope, with its own title', async () => {
  await withEnv(async (doc) => {
    const calls = [];
    const api = {
      deletePreview: async (stem, scope) => { calls.push(['preview', stem, scope]); return { ok: true, groups: [{ key: 'clips_rally', files: 3, size_mb: 9 }], total_files: 3, total_size_mb: 9 }; },
      deleteVideo: async (stem, scope) => { calls.push(['delete', stem, scope]); return { ok: true }; },
    };
    const d = openDeleteDialog({ stem: 's', lang: 'en', api, scope: 'clips' });
    await tick(1);
    const dlg = dialogOf(doc);
    assert.equal(dlg.querySelector('.info-title').textContent, t('wsx.del.clips.title', 'en'));
    assert.match(dlg.textContent, new RegExp(t('wsx.del.clips.warn', 'en').slice(0, 20)));
    assert.match(dlg.textContent, /Rally clips/);
    d.confirmBtn.click();
    await tick(1);
    assert.deepEqual(calls, [['preview', 's', 'clips'], ['delete', 's', 'clips']]);
  });
});

test('delete dialog: scope "match" uses the match title; the default scope stays "all"', async () => {
  await withEnv(async (doc) => {
    const calls = [];
    const api = { deletePreview: async (stem, scope) => { calls.push(scope); return { ok: true, groups: [], total_files: 0 }; } };
    openDeleteDialog({ stem: 's', lang: 'en', api, scope: 'match' });
    assert.equal(dialogOf(doc).querySelector('.info-title').textContent, t('wsx.del.match.title', 'en'));
    closeTool();
    openDeleteDialog({ stem: 's', lang: 'en', api });
    assert.equal(dialogOf(doc).querySelector('.info-title').textContent, t('lib.del.title', 'en'));
    assert.deepEqual(calls, ['match', 'all']);
  });
});

test('tag manager: rename reports a merge; delete confirms, then Undo puts the tag back', async () => {
  await withEnv(async (doc) => {
    const calls = [];
    const api = {
      renameTag: async (from, to) => { calls.push(['rename', from, to]); return { ok: true, merged: true, affected: 1 }; },
      deleteTag: async (tag) => { calls.push(['delete', tag]); return { ok: true, affected: 2 }; },
      videos: async () => [{ name: 'a', tags: ['x'] }, { name: 'b', tags: [] }],
      setTags: async (stem, tags) => { calls.push(['set', stem, tags]); return { ok: true, tags }; },
    };
    let changedAfterUndo = 0;
    const videos = [{ name: 'a', tags: ['final', 'semi'] }, { name: 'b', tags: ['final'] }, { name: 'c', tags: [] }];
    openTagManager({ videos, lang: 'en', api, onChanged: () => { changedAfterUndo += 1; } });
    const dlg = dialogOf(doc);
    assert.match(dlg.textContent, /final.*2 videos/s);
    dlg.querySelector('[data-ren="semi"]').click();
    const input = dlg.querySelector('[data-tag="semi"] input');
    input.value = 'Final';
    dlg.querySelector('[data-save="semi"]').click();
    await tick(1);
    assert.deepEqual(calls[0], ['rename', 'semi', 'final']);
    assert.match(dlg.textContent, /Merged into "final"/);
    assert.equal(dlg.querySelector('[data-tag="semi"]'), null);

    dlg.querySelector('[data-del="final"]').click();
    assert.match(dlg.textContent, /Remove "final" from 2 videos\?/);
    dlg.querySelector('[data-confirm="final"]').click();
    await tick(1);
    assert.deepEqual(calls[1], ['delete', 'final']);
    assert.match(dlg.textContent, /No tags yet/);
    const toast = doc.body.querySelector('.mv-undo');
    assert.ok(toast, 'undo toast');
    buttonWithText(toast, 'Undo').click();
    await tick(5);
    assert.deepEqual(calls.slice(2), [['set', 'a', ['x', 'final']], ['set', 'b', ['final']]]);
    assert.equal(changedAfterUndo, 1);
    assert.match(dlg.textContent, /final/);
  });
});

test('tag manager: a double click on Delete sends one request', async () => {
  await withEnv(async (doc) => {
    let n = 0;
    let release;
    const api = { deleteTag: () => { n += 1; return new Promise((r) => { release = r; }); } };
    openTagManager({ videos: [{ name: 'a', tags: ['x'] }], lang: 'en', api });
    const dlg = dialogOf(doc);
    dlg.querySelector('[data-del="x"]').click();
    const go = dlg.querySelector('[data-confirm="x"]');
    go.click();
    go.disabled = false;      // even if the button were re-enabled, the busy guard holds
    go.click();
    assert.equal(n, 1);
    release({ ok: true });
    await tick(1);
  });
});

test('tag editor: Escape during a save is ignored; the close reports the saved change', async () => {
  await withEnv(async (doc) => {
    let release;
    const api = { setTags: (stem, tags) => new Promise((r) => { release = () => r({ ok: true, tags }); }) };
    let closed = null;
    const ed = openTagEditor({ video: { name: 'v', tags: [] }, lang: 'en', api, onClose: (r) => { closed = r; } });
    ed.input.value = 'smash';
    ed.input.dispatch('keydown', { key: 'Enter' });
    const dlg = dialogOf(doc);
    dlg.dispatch('keydown', { key: 'Escape' });
    assert.ok(isOpen(), 'still open while saving');
    assert.equal(closed, null);
    release();
    await tick(1);
    dlg.dispatch('keydown', { key: 'Escape' });
    assert.equal(isOpen(), false);
    assert.deepEqual(closed, { changed: true, tags: ['smash'] });
  });
});
