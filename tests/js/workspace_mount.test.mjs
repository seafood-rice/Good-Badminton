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

async function withPage(fn, { routes = singles(), stem = 's', query = {}, before } = {}) {
  const { doc } = installEnv();
  if (before) before({ doc });
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

test('distribution: classified strokes and the uncertain rest, so the numbers add up to the header', async () => {
  const routes = singles();
  routes[out('s', 'strokes.json')].strokes.push({ frame: 240, hitter: 'lower', stroke: 'smash', group: 'smash', uncertain: true });
  await withPage(async ({ container }) => {
    const head = container.querySelector('.ws-head').textContent;
    assert.match(head, /4 strokes/);
    const dist = container.querySelector('.ws-dist');
    assert.match(dist.textContent, /3 classified · 1 uncertain/);
    assert.ok(dist.querySelector('.ws-dist-seg.tc-uncertain'), 'muted uncertain segment');
    assert.equal(dist.querySelectorAll('.ws-dist-seg').length, 4);
    assert.equal(t('wsx.dist.total', 'zh', { n: 3, u: 1 }), '已分类 3 次 · 不确定 1 次');
  }, { routes });
});

test('inspector: a weakness with a measured value and no ideal range shows the measured value; with neither, the metric', async () => {
  const routes = singles();
  routes['/api/technique/s'].strokes = [{ ...REPORT, weaknesses: [{ metric: 'elbow_extension', measured: 120 }, { metric: 'knee_flexion' }] }];
  await withPage(async ({ container }) => {
    container.querySelectorAll('.ws-st')[1].click();
    const lines = container.querySelectorAll('.ws-insp .ws-weak li').map((li) => li.textContent);
    assert.deepEqual(lines, ['Elbow extension · measured 120', 'Knee flexion']);
    assert.equal(t('wsx.weak_line_m', 'zh', { metric: '肘部伸展', m: 120 }), '肘部伸展 · 实测 120');
  }, { routes });
});

test('doubles: an identity video that was not made says why (the reason as text); none without a reason', async () => {
  const routes = doubles();
  routes[out('d', 'players.json')].identity_video = { status: 'failed', file: null, reason: '<b>render</b> failed' };
  await withPage(async ({ container }) => {
    const panel = container.querySelector('.dbl-players');
    assert.equal(panel.querySelector('.ws-ident-link'), null);
    const line = panel.querySelector('.ws-ident-none');
    assert.ok(line, 'unavailable line');
    assert.ok(line.classList.contains('kw-muted'));
    assert.equal(line.textContent, 'Player identity video not available: <b>render</b> failed');
    assert.equal(line.querySelector('b'), null, 'never parsed as HTML');
  }, { routes, stem: 'd' });
  const quiet = doubles();
  quiet[out('d', 'players.json')].identity_video = { status: 'skipped', file: null, reason: null };
  await withPage(async ({ container }) => {
    assert.equal(container.querySelector('.dbl-players .ws-ident-none'), null);
  }, { routes: quiet, stem: 'd' });
  await withPage(async ({ container }) => {
    assert.equal(container.querySelector('.dbl-players .ws-ident-none'), null, 'status ok: the link, no line');
  }, { routes: doubles(), stem: 'd' });
});

test('doubles: an identity video file name that leaves the output folder gets no link', async () => {
  for (const file of ['../detect_d_players.mp4', 'sub\\detect_d_players.mp4', '/detect_d_players.mp4']) {
    const routes = doubles();
    routes[out('d', 'players.json')].identity_video = { status: 'ok', file };
    await withPage(async ({ container }) => {
      assert.equal(container.querySelector('.dbl-players .ws-ident-link'), null, file);
    }, { routes, stem: 'd' });
  }
});

test('the side column is labelled as the match details side panel', async () => {
  await withPage(async ({ container }) => {
    const side = container.querySelector('aside.ws-side');
    assert.equal(side.getAttribute('aria-label'), t('wsx.side_aria', 'en'));
    assert.equal(t('wsx.side_aria', 'en'), 'Match details side panel');
    assert.equal(t('wsx.side_aria', 'zh'), '比赛详情侧栏');
  });
});

// ---------- rally outcome labelling (spec 2026-10-27 section 7) ----------

const outcomesDoc = (rallies, status = 'incomplete') => ({
  schema: 1, rallies, summary: { entries: rallies.length, reconciliation: { status } },
});

function labelled(stem = 's') {
  const routes = singles(stem);
  routes['/api/rally-labels/' + stem] = { schema: 1, revision: 4, labels: [] };
  routes[out(stem, 'rally_outcomes.json')] = outcomesDoc([
    { id: 1, segment_id: 2, winner_side: 'upper', outcome: null, source: 'auto', confidence: 0.8, reviewed: false },
  ]);
  return routes;
}

const saveStub = (saved, impl) => {
  api.saveRallyLabels = async (stem, doc) => {
    saved.push({ stem, doc });
    return impl ? impl(doc) : { revision: doc.revision + 1, outcomes_rebuilt: [stem] };
  };
};
const settleSave = async () => { for (let i = 0; i < 8; i++) await tick(); };
const pt = (c) => c.querySelector('.ws-pt');
const verdictBtn = (c, v) => c.querySelector('[data-verdict="' + v + '"]');

test('the Point block renders for the selected rally with the record names, the key hint and the tally', async () => {
  await withPage(async ({ container }) => {
    const block = pt(container);
    assert.ok(block, 'Point block');
    assert.equal(verdictBtn(container, 'lower').textContent, 'Chung won');
    assert.equal(verdictBtn(container, 'upper').textContent, 'Lee won');
    assert.equal(verdictBtn(container, 'not_point').textContent, 'Not a point end');
    assert.equal(verdictBtn(container, 'unsure').textContent, 'Unsure');
    assert.equal(block.querySelector('[data-outcome]'), null, 'no outcome chips before a winner is picked');
    assert.match(block.textContent, /Keys: N near/);
    assert.ok(container.querySelector('.ws-pt-add'), 'Add point end here button');
    assert.match(container.querySelector('.ws-tally').textContent, /Near 0 – Far 1 · 1 of 1 labelled/);
    assert.equal(container.querySelector('.ws-rc').getAttribute('data-status'), 'incomplete');
  }, { routes: labelled() });
});

test('no labels and no outcomes file: the page still renders, the block offers the buttons, no badge', async () => {
  await withPage(async ({ container }) => {
    assert.ok(verdictBtn(container, 'lower'));
    assert.equal(container.querySelector('.ws-rc'), null);
    assert.equal(container.querySelector('.ws-tally').textContent, t('ws.pt.tally_none', 'en'));
  });
});

test('clicking Near won saves verdict lower with the current revision, advances and shows the auto result greyed', async () => {
  const saved = [];
  await withPage(async ({ container }) => {
    saveStub(saved);
    verdictBtn(container, 'lower').click();
    await settleSave();
    assert.equal(saved.length, 1);
    assert.equal(saved[0].stem, 's');
    assert.equal(saved[0].doc.revision, 4);
    assert.deepEqual(saved[0].doc.labels, [{ start_sec: 0, end_sec: 5, verdict: 'lower', outcome: null, added: false }]);
    // advanced to segment 2, which only has the automatic result (far = upper): greyed, tagged auto
    assert.match(container.querySelector('.ws-sum').textContent, /2 of 2/);
    assert.ok(container.querySelector('.ws-pt-autotag'), 'auto tag');
    assert.ok(verdictBtn(container, 'upper').classList.contains('auto'));
    assert.equal(verdictBtn(container, 'upper').classList.contains('on'), false);
    const lane = container.querySelectorAll('.ws-rb');
    assert.equal(lane[0].getAttribute('data-state'), 'near');
    assert.equal(lane[1].getAttribute('data-state'), 'far');
    assert.equal(lane[1].getAttribute('data-source'), 'auto');
    // a click on the greyed auto button confirms it, building on the revision the server answered (5)
    verdictBtn(container, 'upper').click();
    await settleSave();
    assert.equal(saved[1].doc.revision, 5);
    assert.equal(saved[1].doc.labels.length, 2);
    assert.equal(saved[1].doc.labels[1].verdict, 'upper');
  }, { routes: labelled() });
});

test('an outcome chip saves the same winner with the outcome and stays on the segment', async () => {
  const saved = [];
  await withPage(async ({ container }) => {
    saveStub(saved);
    verdictBtn(container, 'upper').click();
    await settleSave();
    container.querySelectorAll('.ws-rb')[0].click(); // back on segment 1, a winner is picked: the chips show
    await tick();
    const chip = container.querySelector('[data-outcome="forced_error"]');
    assert.ok(chip);
    chip.click();
    await settleSave();
    const last = saved[saved.length - 1].doc.labels;
    assert.equal(last.length, 1, 'the segment label is replaced, not duplicated');
    assert.equal(last[0].verdict, 'upper');
    assert.equal(last[0].outcome, 'forced_error');
    assert.match(container.querySelector('.ws-sum').textContent, /1 of 2/);
    assert.equal(container.querySelector('[data-outcome="forced_error"]').classList.contains('on'), true);
  }, { routes: labelled() });
});

test('a 409 reloads the labels, says so and keeps the place; the next save uses the reloaded revision', async () => {
  const saved = [];
  const routes = labelled();
  await withPage(async ({ container, calls }) => {
    saveStub(saved, () => { throw Object.assign(new Error('stale'), { status: 409 }); });
    routes['/api/rally-labels/s'] = { schema: 1, revision: 9, labels: [] };
    verdictBtn(container, 'lower').click();
    await settleSave();
    assert.equal(calls.fetched.filter((u) => u === '/api/rally-labels/s').length, 2, 'initial load plus the reload');
    assert.equal(pt(container).querySelector('.ws-pt-notice').textContent, t('ws.pt.conflict', 'en'));
    assert.match(container.querySelector('.ws-sum').textContent, /1 of 2/, 'still on the same segment');
    saveStub(saved);
    verdictBtn(container, 'lower').click();
    await settleSave();
    assert.equal(saved[saved.length - 1].doc.revision, 9);
  }, { routes });
});

test('keys: F saves a far win; other keys, and keys typed into an input, are left alone', async () => {
  const saved = [];
  let keydown = null;
  const before = ({ doc }) => { doc.addEventListener = (type, fn) => { if (type === 'keydown') keydown = fn; }; };
  await withPage(async ({ container }) => {
    saveStub(saved);
    assert.ok(keydown, 'a keydown listener while a video shows');
    const press = (key, target, extra = {}) => {
      let prevented = false;
      keydown({ key, target: target || container, preventDefault: () => { prevented = true; }, ...extra });
      return prevented;
    };
    assert.equal(press('f', { tagName: 'INPUT' }), false, 'typing in an input is left alone');
    assert.equal(press('f', null, { ctrlKey: true }), false, 'Ctrl+F is the browser\'s');
    assert.equal(press('q'), false);
    assert.equal(press('F'), true);
    await settleSave();
    assert.equal(saved.length, 1);
    assert.equal(saved[0].doc.labels[0].verdict, 'upper');
  }, { routes: labelled(), before });
});

test('Add point end here prompts for the winner at the video time and saves an added point', async () => {
  const saved = [];
  await withPage(async ({ container }) => {
    saveStub(saved);
    container.querySelector('video').currentTime = 52.4;
    container.querySelector('.ws-pt-add').click();
    assert.match(container.querySelector('.ws-pt-addbox').textContent, /Point ending at/);
    container.querySelector('[data-add="lower"]').click();
    await settleSave();
    assert.deepEqual(saved[0].doc.labels, [{ start_sec: null, end_sec: 52.4, verdict: 'lower', outcome: null, added: true }]);
    assert.equal(container.querySelector('.ws-pt-addbox'), null, 'the prompt closes after the save');
  }, { routes: labelled() });
});

test('doubles: the same controls read near team / far team', async () => {
  await withPage(async ({ container }) => {
    assert.equal(verdictBtn(container, 'lower').textContent, 'Near team won');
    assert.equal(verdictBtn(container, 'upper').textContent, 'Far team won');
  }, { routes: doubles(), stem: 'd' });
});

test('a 409 with reason unreadable (no revision) shows the unreadable notice and does not reload', async () => {
  const saved = [];
  await withPage(async ({ container, calls }) => {
    saveStub(saved, () => { throw Object.assign(new Error('x'), { status: 409, body: { error: 'x', reason: 'unreadable' } }); });
    verdictBtn(container, 'lower').click();
    await settleSave();
    assert.equal(pt(container).querySelector('.ws-pt-notice').textContent, t('ws.pt.unreadable', 'en'));
    assert.equal(calls.fetched.filter((u) => u === '/api/rally-labels/s').length, 1, 'no reload');
    // a body with a revision and no reason is a stale save: reload, and re-read the outcomes
    saveStub(saved, () => { throw Object.assign(new Error('x'), { status: 409, body: { error: 'x', revision: 7 } }); });
    const outcomesBefore = calls.fetched.filter((u) => u.endsWith('rally_outcomes.json')).length;
    verdictBtn(container, 'lower').click();
    await settleSave();
    assert.equal(calls.fetched.filter((u) => u === '/api/rally-labels/s').length, 2);
    assert.equal(calls.fetched.filter((u) => u.endsWith('rally_outcomes.json')).length, outcomesBefore + 1);
    assert.equal(pt(container).querySelector('.ws-pt-notice').textContent, t('ws.pt.conflict', 'en'));
  }, { routes: labelled() });
});

test('keys still label from a focused range slider; a repeat keydown is ignored; an unsure verdict shows pressed', async () => {
  const saved = [];
  let keydown = null;
  const before = ({ doc }) => { doc.addEventListener = (type, fn) => { if (type === 'keydown') keydown = fn; }; };
  await withPage(async ({ container }) => {
    saveStub(saved);
    const press = (key, extra) => { let p = false; keydown({ key, target: container, preventDefault: () => { p = true; }, ...extra }); return p; };
    assert.equal(press('u', { target: { tagName: 'INPUT', type: 'range' }, repeat: true }), false, 'repeat ignored');
    assert.equal(press('u', { target: { tagName: 'INPUT', type: 'text' } }), false, 'text input ignored');
    assert.equal(press('u', { target: { tagName: 'INPUT', type: 'range' } }), true, 'range slider does not swallow keys');
    await settleSave();
    assert.equal(saved.length, 1);
    assert.equal(saved[0].doc.labels[0].verdict, 'unsure');
    container.querySelectorAll('.ws-rb')[0].click();
    await tick();
    // segment 1 has no auto result; segment 2 has one: unsure there still shows pressed
    container.querySelectorAll('.ws-rb')[1].click();
    await tick();
    verdictBtn(container, 'unsure').click();
    await settleSave();
    container.querySelectorAll('.ws-rb')[1].click();
    await tick();
    assert.equal(verdictBtn(container, 'unsure').getAttribute('aria-pressed'), 'true');
    assert.ok(verdictBtn(container, 'upper').classList.contains('auto'), 'the auto result is still shown');
  }, { routes: labelled(), before });
});

test('added points are listed with their time and winner; removing one saves the labels without it', async () => {
  const saved = [];
  const routes = labelled();
  routes['/api/rally-labels/s'] = {
    schema: 1, revision: 4,
    labels: [
      { start_sec: null, end_sec: 52.4, verdict: 'lower', outcome: null, added: true },
      { start_sec: 0, end_sec: 5, verdict: 'upper', outcome: null, added: false },
    ],
  };
  await withPage(async ({ container }) => {
    saveStub(saved);
    const items = container.querySelectorAll('.ws-added-item');
    assert.equal(items.length, 1, 'only the added point is listed');
    assert.match(items[0].textContent, /0:52/);
    assert.match(items[0].textContent, /Chung won/);
    items[0].querySelector('.ws-added-rm').click();
    await settleSave();
    assert.equal(saved.length, 1);
    assert.equal(saved[0].doc.revision, 4);
    assert.deepEqual(saved[0].doc.labels.map((l) => l.added), [false]);
    assert.equal(container.querySelectorAll('.ws-added-item').length, 0);
  }, { routes });
});
