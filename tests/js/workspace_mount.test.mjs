// The match details page (#/matches/<stem>, spec 2026-10-24 section 6) mounted against the fake DOM with a stubbed
// fetch: layout A (player + movement on top, tabs below), tab switching in place (router.replace, setQuery), the
// header "..." menu with Delete analysis results, the Posture report link, per-stroke weakness lines and doubles.
import test from 'node:test';
import assert from 'node:assert/strict';
import { installEnv, removeEnv, tick, everything } from './fake_dom.mjs';
import { mount } from '../../static/kestrel/views/workspace.js';
import { api } from '../../static/kestrel/core/api.js';
import { t } from '../../static/kestrel/core/i18n.js';
import { closeTool } from '../../static/kestrel/widgets/videotools.js';

const out = (stem, f) => '/api/output/' + stem + '/' + f;

const REPORT = {
  scored: true, contact_frame: 60, player_side: 'upper', stroke_type: 'clear', group: 'clear', overall_score: 62,
  per_metric: { elbow_extension: { score: 50, measured: 120, ideal_range: [150, 175] } },
  weaknesses: [{ metric: 'elbow_extension', measured: 120, ideal_range: [150, 175], direction: 'low', severity: 'high' }],
};

function singles(stem = 's') {
  return {
    [out(stem, 'rally_segments.json')]: { fps: 30, rallies: [{ id: 1, start_sec: 0, end_sec: 5 }, { id: 2, start_sec: 6, end_sec: 10 }], detection: { signal: 'courtview' } },
    [out(stem, 'strokes.json')]: {
      schema: 2, shuttle_source: 'tracknet', classifier: { validated_on_owner_footage: true },
      groups: { smash: 1, clear: 1, net: 1 },
      strokes: [
        { frame: 30, hitter: 'lower', stroke: 'smash', group: 'smash', uncertain: false, confidence: 0.9 },
        { frame: 60, hitter: 'upper', stroke: 'clear', group: 'clear', uncertain: false, confidence: 0.8 },
        { frame: 210, hitter: 'lower', stroke: 'net_shot', group: 'net', uncertain: false, confidence: 0.7 },
      ],
    },
    [out(stem, 'metadata.json')]: { video: { fps: 30 } },
    ['/api/technique/' + stem]: {
      summary: { stroke_count: 1, scored_count: 1, by_type: { clear: { count: 1, avg_score: 62 } }, recurring_weaknesses: [{ metric: 'elbow_extension', count: 1 }] },
      strokes: [REPORT],
    },
    ['/api/insights/video/' + stem]: { rally: { status: 'ok', value: { count: 2, label: 'rallies', signal: 'courtview' } }, date: { value: '2026-10-01' } },
    ['/api/matches/' + stem]: { date: '2026-10-01', upper: { name: 'Lee' }, lower: { name: 'Chung' } },
    '/api/videos': [{ name: stem, has_posture: true, has_match: true }],
  };
}

function doubles(stem = 'd') {
  return {
    [out(stem, 'rally_segments.json')]: { fps: 30, rallies: [{ id: 1, start_sec: 0, end_sec: 5 }], detection: { signal: 'shuttle' } },
    [out(stem, 'metadata.json')]: { match_format: 'doubles', video: { fps: 30 } },
    [out(stem, 'players.json')]: {
      match_format: 'doubles', status: 'ok',
      identity_video: { status: 'ok', file: 'detect_d_players.mp4' },
      teams: [{ id: 1, players: ['P1', 'P2'], coverage_heatmap: 'heatmap_team1.png' }, { id: 2, players: ['P3', 'P4'] }],
      players: [
        { id: 'P1', team: 1, name: 'Ann', heatmap: 'heatmap_P1.png' }, { id: 'P2', team: 1, heatmap: 'heatmap_P2.png' },
        { id: 'P3', team: 2 }, { id: 'P4', team: 2 },
      ],
    },
    [out(stem, 'hits.json')]: { hits: [{ frame: 30, time_sec: 1, hitter: 'lower', hitter_id: 'P1', hitter_team: 1, hitter_confidence: 0.9, hitter_evidence: 'shuttle' }] },
    ['/api/insights/video/' + stem]: { match_format: 'doubles' },
    ['/api/matches/' + stem]: { format: 'doubles' },
    '/api/videos': [{ name: stem, has_posture: false, has_match: true }],
  };
}

async function withPage(fn, { routes = singles(), stem = 's', query = {} } = {}) {
  const { doc } = installEnv();
  const savedApi = { ...api };
  const calls = { replace: [], navigate: [], fetched: [] };
  globalThis.fetch = async (url) => {
    calls.fetched.push(url);
    const body = routes[url];
    if (body === undefined) return { ok: false, status: 404, json: async () => ({}) };
    return { ok: true, status: 200, json: async () => body };
  };
  const container = doc.createElement('div');
  doc.body.appendChild(container);
  const router = { replace: (h) => calls.replace.push(h), navigate: (h) => calls.navigate.push(h) };
  const inst = mount(container, { params: { stem }, query, lang: 'en', reduced: true, router });
  try {
    for (let i = 0; i < 10; i++) await tick();
    await fn({ container, inst, calls, doc });
  } finally {
    inst.destroy();
    closeTool();
    Object.assign(api, savedApi);
    delete globalThis.fetch;
    removeEnv();
  }
}

const tabs = (c) => c.querySelectorAll('[role="tab"]');
const tabByText = (c, text) => tabs(c).find((b) => b.textContent === text);
const panel = (c) => c.querySelector('[role="tabpanel"]');

test('layout A: player and movement on top, four tabs, a Posture report link and no link to the classic results', async () => {
  await withPage(async ({ container }) => {
    const top = container.querySelector('.ws-top');
    assert.ok(top, '.ws-top');
    const card = top.querySelector('.ws-video-card');
    assert.ok(card && card.querySelector('.ws-mute'), 'video card with the mute button');
    assert.ok(top.querySelector('.ws-move'), 'movement card in the top band');
    assert.equal(container.querySelector('.ws-grid'), null);
    assert.equal(tabs(container).length, 4);
    assert.deepEqual(tabs(container).map((b) => b.textContent), ['Strokes', 'Technique', 'Clips', 'Training plan']);
    assert.equal(tabs(container)[0].getAttribute('aria-selected'), 'true');
    assert.equal(everything(container).some((e) => e.textContent === 'Open the full results'), false);
    const posture = container.querySelector('a[href="#/results/s?mode=posture"]');
    assert.ok(posture, 'posture link');
    assert.equal(posture.textContent, 'Posture report');
    // strokes tab: the distribution card (with the TrackNet note) above the stroke list
    const p = panel(container);
    assert.ok(p.querySelector('.ws-dist-bar'));
    assert.equal(p.querySelectorAll('.ws-dist-seg').length, 3);
    assert.match(p.textContent, /Stroke distribution/);
    assert.match(p.textContent, /Dense shuttle tracking: TrackNetV3/);
    assert.ok(p.querySelector('.ws-list'));
  });
});

test('no posture run (or no video list): no Posture report link', async () => {
  const routes = singles();
  delete routes['/api/videos'];
  await withPage(async ({ container }) => {
    assert.ok(container.querySelector('.ws-top'), 'the page still renders');
    assert.equal(everything(container).some((e) => e.tagName === 'A' && e.textContent === 'Posture report'), false);
  }, { routes });
});

test('tabs switch in place: router.replace for a click, none for setQuery; the video is untouched', async () => {
  await withPage(async ({ container, inst, calls }) => {
    const video = container.querySelector('video');
    const src = video.src;
    tabByText(container, 'Technique').click();
    assert.deepEqual(calls.replace, ['#/matches/s?tab=technique']);
    assert.equal(tabByText(container, 'Technique').getAttribute('aria-selected'), 'true');
    assert.ok(panel(container).querySelector('.ws-tech'), 'technique panel shown');
    assert.equal(panel(container).querySelector('.ws-list'), null);
    assert.equal(container.querySelector('video'), video);
    assert.equal(video.src, src);
    inst.setQuery({ tab: 'clips' });
    assert.ok(panel(container).querySelector('.ws-clips'), 'clips panel shown');
    assert.equal(calls.replace.length, 1, 'setQuery does not call the router');
    assert.equal(tabByText(container, 'Clips').getAttribute('aria-selected'), 'true');
    // back to the default tab: the tab is left out of the URL
    tabByText(container, 'Strokes').click();
    assert.equal(calls.replace[1], '#/matches/s');
    assert.ok(panel(container).querySelector('.ws-list'));
    // arrow keys move between tabs
    tabByText(container, 'Strokes').dispatch('keydown', { key: 'ArrowLeft' });
    assert.equal(tabByText(container, 'Training plan').getAttribute('aria-selected'), 'true');
    assert.equal(globalThis.document.activeElement, tabByText(container, 'Training plan'));
    assert.ok(panel(container).querySelector('.ws-plan'));
    assert.equal(container.querySelector('video'), video);
  });
});

test('a tab in the URL opens on that tab', async () => {
  await withPage(async ({ container }) => {
    assert.equal(tabByText(container, 'Technique').getAttribute('aria-selected'), 'true');
    assert.ok(panel(container).querySelector('.ws-tech'));
  }, { query: { tab: 'technique' } });
});

test('the header menu: Delete analysis results opens the match-scoped dialog; Escape closes the menu', async () => {
  await withPage(async ({ container, doc }) => {
    const scopes = [];
    api.deletePreview = (stem, scope) => { scopes.push([stem, scope]); return new Promise(() => {}); };
    const btn = container.querySelector('.ws-menu-btn');
    assert.equal(btn.getAttribute('aria-haspopup'), 'menu');
    btn.click();
    assert.equal(btn.getAttribute('aria-expanded'), 'true');
    let menu = container.querySelector('.ws-menu');
    assert.equal(menu.getAttribute('role'), 'menu');
    menu.dispatch('keydown', { key: 'Escape' });
    assert.equal(container.querySelector('.ws-menu'), null);
    assert.equal(doc.activeElement, btn);
    btn.click();
    menu = container.querySelector('.ws-menu');
    const item = menu.querySelector('[role="menuitem"]');
    assert.equal(item.textContent, 'Delete analysis results…');
    item.click();
    const dlg = doc.body.querySelector('[data-tool="delete"]');
    assert.ok(dlg, 'delete dialog');
    assert.equal(dlg.querySelector('.info-title').textContent, t('wsx.del.match.title', 'en'));
    assert.deepEqual(scopes, [['s', 'match']]);
    assert.equal(container.querySelector('.ws-menu'), null);
  });
});

test('inspector: the stroke with a report lists its weaknesses', async () => {
  await withPage(async ({ container }) => {
    assert.equal(container.querySelector('.ws-insp .ws-weak'), null, 'stroke 1 has no report');
    const st = container.querySelectorAll('.ws-st');
    st[1].click();
    const weak = container.querySelector('.ws-insp .ws-weak');
    assert.ok(weak, 'weakness list');
    assert.match(weak.textContent, /Elbow extension · measured 120 · ideal 150–175/);
    assert.match(container.querySelector('.ws-insp').textContent, /Weaknesses/);
  });
});

test('doubles: technique withheld, identity video link, team and player heatmaps, hits table on the strokes tab', async () => {
  await withPage(async ({ container }) => {
    const link = container.querySelector('a[href="/api/output/d/detect_d_players.mp4"]');
    assert.ok(link, 'identity video link');
    assert.equal(link.getAttribute('target'), '_blank');
    assert.equal(link.textContent, 'Player identity video');
    const srcs = container.querySelectorAll('.ws-move img').map((i) => i.getAttribute('src'));
    assert.ok(srcs.includes('/api/output/d/heatmap_team1.png'));
    assert.ok(srcs.includes('/api/output/d/heatmap_P1.png'));
    assert.equal(srcs.filter((s) => s.includes('heatmap_team')).length, 1, 'a team without a file is skipped');
    assert.equal(panel(container).querySelector('.ws-dist-bar'), null, 'no distribution card on doubles');
    assert.ok(panel(container).querySelector('.ws-list'));
    assert.equal(container.querySelectorAll('.ws-hit').length, 1);
    tabByText(container, 'Technique').click();
    assert.match(panel(container).textContent, new RegExp(t('wsx.tech.doubles', 'en')));
    assert.equal(everything(container).some((e) => e.tagName === 'A' && e.textContent === 'Posture report'), false);
  }, { routes: doubles(), stem: 'd' });
});

test('a confirmed Delete analysis results navigates to the Matches list', async () => {
  await withPage(async ({ container, doc, calls }) => {
    const deleted = [];
    api.deletePreview = async () => ({ ok: true, groups: [{ key: 'match', files: 2, size_mb: 1 }], total_files: 2, total_size_mb: 1 });
    api.deleteVideo = async (stem, scope) => { deleted.push([stem, scope]); return { ok: true }; };
    container.querySelector('.ws-menu-btn').click();
    container.querySelector('.ws-menu [role="menuitem"]').click();
    await tick(1);
    const go = doc.body.querySelector('.mv-del-go');
    assert.equal(go.disabled, false);
    go.click();
    await tick(5);
    assert.deepEqual(deleted, [['s', 'match']]);
    assert.deepEqual(calls.navigate, ['#/matches']);
  });
});

test('setQuery with the tab already open is a no-op (same panel node, no router call)', async () => {
  await withPage(async ({ container, inst, calls }) => {
    const panelEl = container.querySelector('[role="tabpanel"]');
    const body = panelEl.firstChild;
    inst.setQuery({});
    inst.setQuery({ tab: 'strokes' });
    assert.equal(panelEl.firstChild, body);
    assert.equal(container.querySelector('[role="tabpanel"]'), panelEl);
    assert.equal(calls.replace.length, 0);
    assert.equal(calls.navigate.length, 0);
  });
});
