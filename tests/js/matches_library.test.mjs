// The merged Matches page (spec 2026-10-24): mount() against the fake DOM with a patched api. Every video shows,
// split parts only inside their match, the video list is optional, scope hides video rows, table / grid, filters,
// the "..." menu and the delete flow.
import test from 'node:test';
import assert from 'node:assert/strict';
import { installEnv, removeEnv, tick, everything, buttonWithText, env } from './fake_dom.mjs';
import { mount } from '../../static/kestrel/views/matches.js';
import { api, ApiError } from '../../static/kestrel/core/api.js';
import { closeTool } from '../../static/kestrel/widgets/videotools.js';

const vid = (name, extra = {}) => ({
  name, filename: name + '.mp4', status: 'new', has_match: false, has_posture: false, has_annotations: false,
  date: '2026-09-01', duration_sec: 75, thumb: '/api/output/' + encodeURIComponent(name) + '/thumb.jpg', tags: [], ...extra,
});

const ODD = 'odd "name" x';
const OV = () => ({
  matches: [
    { stem: 'm1', date: '2026-10-01', date_source: 'record', side: 'lower', athlete: 'Axelsen', opponent: 'Lee', result: 'W', status: 'analysed', tech_index: 71.5,
      parts: [{ stem: 'm1', side: 'lower', status: 'analysed' }, { stem: 'm2', side: 'upper', status: 'analysed' }] },
    { stem: ODD, date: '2026-09-15', status: 'analysed', side: 'lower', parts: [] },
  ],
});
const VIDS = () => [
  vid('m1', { status: 'analyzed', has_match: true, tags: ['final'] }),
  vid('m2', { status: 'analyzed', has_match: true }),
  vid(ODD, { status: 'analyzed', has_match: true, has_posture: true }),
  vid('drill1', { status: 'analyzed', has_posture: true, date: '2026-09-20', tags: ['footwork'] }),
  vid('fresh', { date: '2026-10-05' }),
];

const rowStems = (c) => everything(c).filter((e) => e.hasAttribute('data-row')).map((e) => e.getAttribute('data-row'));

async function withView(fn, { overview = OV, videos = VIDS, ctx: extra = {}, storage = {} } = {}) {
  const { doc } = installEnv();
  for (const [k, v] of Object.entries(storage)) globalThis.localStorage.setItem(k, v);
  const saved = { ...api };
  const calls = { overview: 0, videos: 0, nav: [], toasts: [], deleted: [] };
  api.overview = async () => { calls.overview += 1; return overview(calls.overview); };
  api.videos = async () => { calls.videos += 1; return videos(calls.videos); };
  api.match = async () => ({});
  const container = doc.createElement('div');
  doc.body.appendChild(container);
  const ctx = { lang: 'en', query: {}, toast: (m) => calls.toasts.push(m), transitionTo: (a, hash) => calls.nav.push(hash), ...extra };
  const inst = mount(container, ctx);
  try {
    await tick(5);
    await fn({ container, inst, calls, ctx, doc });
  } finally {
    inst.destroy();
    closeTool();
    Object.assign(api, saved);
    removeEnv();
  }
}

test('every video shows once: matches, posture-only and new videos; a split part only inside its match', async () => {
  await withView(async ({ container }) => {
    assert.deepEqual(rowStems(container), ['fresh', 'm1', 'drill1', ODD]);
    const txt = container.textContent;
    assert.match(txt, /5 videos · 4 analysed/);
    const drill = container.querySelector('[data-row="drill1"]');
    assert.match(drill.textContent, /Posture/);
    assert.match(drill.textContent, /footwork/);
    const fresh = container.querySelector('[data-row="fresh"]');
    assert.match(fresh.textContent, /New/);
    assert.ok(everything(fresh).some((e) => e.tagName === 'A' && e.textContent === 'Analyse' && e.getAttribute('href') === '#/new'));
    assert.equal(fresh.querySelector('[data-rec="fresh"]'), null, 'no record editor for a video that is not a match');
    const img = container.querySelector('[data-row="m1"] img');
    assert.equal(img.getAttribute('src'), '/api/output/m1/thumb.jpg');
    assert.match(container.querySelector('[data-row="m1"]').textContent, /1:15/, 'duration overlay');
  });
});

test('a failed video list keeps the matches and says so (Retry reloads both)', async () => {
  let fail = true;
  await withView(async ({ container, calls }) => {
    assert.deepEqual(rowStems(container), ['m1', ODD]);
    const banner = container.querySelector('.mv-videos-failed');
    assert.ok(banner);
    assert.match(banner.textContent, /could not be listed/);
    fail = false;
    buttonWithText(banner, 'Retry').click();
    await tick(5);
    assert.equal(calls.overview, 2);
    assert.equal(container.querySelector('.mv-videos-failed'), null);
    assert.equal(rowStems(container).length, 4);
  }, { videos: () => { if (fail) throw new ApiError('timeout', 0, { zh: '请求超时', en: 'The request timed out' }); return VIDS(); } });
});

test('scoped: only the scope’s matches, a note and a link back to every video', async () => {
  await withView(async ({ container }) => {
    assert.deepEqual(rowStems(container), ['m1']);
    const note = container.querySelector('.mv-scope-note');
    assert.match(note.textContent, /Showing matches for Axel Sen only/);
    assert.equal(note.querySelector('a').getAttribute('href'), '#/matches');
    assert.match(container.textContent, /1 matches/);
  }, {
    overview: () => ({ matches: [OV().matches[0]] }),
    ctx: { query: { athlete: 'a1' }, store: { get: () => ({ athletes: { athletes: [{ id: 'a1', name: 'Axel Sen' }], squads: [] } }) } },
  });
});

test('grid view: cards with the same rows; the choice is remembered', async () => {
  await withView(async ({ container }) => {
    const gridBtn = container.querySelector('[data-view-btn="grid"]');
    gridBtn.click();
    assert.equal(gridBtn.getAttribute('aria-pressed'), 'true');
    const cards = everything(container).filter((e) => e.classList.contains('mv-cardv'));
    assert.deepEqual(cards.map((c) => c.getAttribute('data-row')), ['fresh', 'm1', 'drill1', ODD]);
    assert.equal(container.querySelector('table'), null);
    assert.equal(globalThis.localStorage.getItem('kestrel_matches_view'), 'grid');
    // the split match's parts open as a panel under its card
    container.querySelector('[data-expand="m1"]').click();
    assert.deepEqual(everything(container).filter((e) => e.classList.contains('mv-gpart')).map((e) => e.getAttribute('data-part')), ['m1', 'm2']);
  });
});

test('a remembered grid choice is read back on mount', async () => {
  await withView(async ({ container }) => {
    assert.ok(container.querySelector('.mv-grid'));
    assert.equal(container.querySelector('[data-view-btn="grid"]').getAttribute('aria-pressed'), 'true');
  }, { storage: { kestrel_matches_view: 'grid' } });
});

test('search, status and tag filters narrow the list; filtering to nothing offers Clear filters', async () => {
  await withView(async ({ container, doc }) => {
    const search = container.querySelector('.mv-search');
    search.value = 'axel';
    search.dispatch('input');
    assert.deepEqual(rowStems(container), ['m1']);
    assert.match(container.textContent, /1 of 4 videos/);
    assert.ok(container.querySelector('.mv-search') === search, 'the search box survives a list re-render');
    search.value = '';
    search.dispatch('input');

    const status = container.querySelector('.mv-status');
    status.value = 'posture';
    status.dispatch('change');
    assert.deepEqual(rowStems(container), ['drill1']);
    status.value = 'all';
    status.dispatch('change');

    container.querySelector('[data-tagchip="final"]').click();
    assert.deepEqual(rowStems(container), ['m1']);
    assert.equal(container.querySelector('[data-tagchip="final"]').getAttribute('aria-pressed'), 'true');
    assert.equal(container.querySelector('[data-tagchip="footwork"]').disabled, true, 'a chip that would leave nothing is disabled');

    search.value = 'zzz';
    search.dispatch('input');
    assert.deepEqual(rowStems(container), []);
    assert.match(container.textContent, /No videos match these filters/);
    buttonWithText(container, 'Clear filters').click();
    assert.equal(rowStems(container).length, 4);
    assert.equal(doc.activeElement, container.querySelector('.mv-search'));
  });
});

test('the "..." menu of an odd stem lists the right items; Escape closes it back to its button', async () => {
  await withView(async ({ container, doc, calls }) => {
    const btn = container.querySelector(`[data-menu="${ODD.replace(/"/g, '\\"')}"]`);
    assert.ok(btn, 'menu button found by an escaped selector');
    btn.click();
    const menu = doc.body.querySelector('.mv-menu');
    assert.deepEqual(everything(menu).map((e) => e.getAttribute('data-item')), ['lib.menu.tags', 'lib.menu.classic', 'lib.menu.posture', 'lib.menu.delete']);
    assert.equal(btn.getAttribute('aria-expanded'), 'true');
    menu.dispatch('keydown', { key: 'Escape' });
    assert.equal(doc.body.querySelector('.mv-menu'), null);
    assert.equal(doc.activeElement, btn);
    // posture report goes to the legacy results screen
    btn.click();
    doc.body.querySelector('[data-item="lib.menu.posture"]').click();
    assert.deepEqual(calls.nav, ['#/results/odd%20%22name%22%20x?mode=posture']);
    // a new video has only tags and delete
    container.querySelector('[data-menu="fresh"]').click();
    assert.deepEqual(everything(doc.body.querySelector('.mv-menu')).map((e) => e.getAttribute('data-item')), ['lib.menu.tags', 'lib.menu.delete']);
  });
});

test('Delete video: dialog, delete, toast, reload; focus moves to the next row', async () => {
  let gone = false;
  await withView(async ({ container, doc, calls }) => {
    api.deletePreview = async () => ({ ok: true, groups: [{ key: 'source', files: 1, size_mb: 3 }], total_files: 1, total_size_mb: 3 });
    api.deleteVideo = async (stem) => { calls.deleted.push(stem); gone = true; return { ok: true }; };
    container.querySelector('[data-menu="drill1"]').click();
    doc.body.querySelector('[data-item="lib.menu.delete"]').click();
    await tick(1);
    const dlg = doc.body.querySelector('.mv-dlg');
    assert.ok(dlg);
    buttonWithText(dlg, 'Delete permanently').click();
    await tick(10);
    assert.deepEqual(calls.deleted, ['drill1']);
    assert.deepEqual(calls.toasts, ['drill1 deleted']);
    assert.equal(calls.overview, 2, 'reloaded');
    assert.ok(!rowStems(container).includes('drill1'));
    const focusedRow = doc.activeElement && doc.activeElement.closest('[data-row]');
    assert.equal(focusedRow && focusedRow.getAttribute('data-row'), ODD, 'focus on the next row');
  }, { videos: () => VIDS().filter((v) => !(gone && v.name === 'drill1')) });
});

test('clicking a row opens its target: workspace, posture report or the wizard', async () => {
  await withView(async ({ container, calls }) => {
    container.querySelector('[data-open="m1"]').click();
    container.querySelector('[data-open="drill1"]').click();
    container.querySelector('[data-open="fresh"]').click();
    assert.deepEqual(calls.nav, ['#/matches/m1', '#/results/drill1?mode=posture', '#/new']);
  });
});

test('an empty library says so and offers a new analysis', async () => {
  await withView(async ({ container }) => {
    assert.match(container.textContent, /No videos yet/);
    assert.ok(everything(container).some((e) => e.tagName === 'A' && e.getAttribute('href') === '#/new'));
  }, { overview: () => ({ matches: [] }), videos: () => [] });
});

test('a video with a running job (store.jobs, the /api/jobs rows) reads Processing and opens the pipeline', async () => {
  await withView(async ({ container, calls }) => {
    const fresh = container.querySelector('[data-row="fresh"]');
    assert.match(fresh.textContent, /Processing/);
    container.querySelector('[data-open="fresh"]').click();
    assert.deepEqual(calls.nav, ['#/pipeline']);
  }, { ctx: { store: { get: () => ({ jobs: [{ id: 'fresh', video: 'fresh', status: 'running' }] }) } } });
});

test('an open record editor survives typing in search: same node, typed value kept, no refetch, focus stays', async () => {
  let fetches = 0;
  await withView(async ({ container, doc }) => {
    api.match = async () => { fetches += 1; return { date: '2026-10-01' }; };
    container.querySelector('[data-rec="m1"]').click();
    await tick(5);
    const date = container.querySelector('#mv-date');
    assert.ok(date, 'editor open');
    date.value = '2026-10-09';
    const search = container.querySelector('.mv-search');
    search.focus();
    search.value = 'Lee';
    search.dispatch('input');
    await tick(5);
    assert.equal(container.querySelector('#mv-date'), date, 'the same editor, moved into the new row');
    assert.equal(date.value, '2026-10-09');
    assert.equal(fetches, 1);
    assert.equal(doc.activeElement, search);
  });
});
