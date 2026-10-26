// Split matches on the match details page (spec 2026-10-24 sections 5, 6, 8, 9): the part switcher, the URL rules
// (a part's own stem, an unknown part), the Whole match view and in-place part changes through setQuery.
import test from 'node:test';
import assert from 'node:assert/strict';
import { installEnv, removeEnv, tick, everything } from './fake_dom.mjs';
import { mount } from '../../static/kestrel/views/workspace.js';
import { api } from '../../static/kestrel/core/api.js';
import { t } from '../../static/kestrel/core/i18n.js';
import { formatHash, parseHash } from '../../static/kestrel/core/router.js';
import { closeTool } from '../../static/kestrel/widgets/videotools.js';

const out = (stem, f) => '/api/output/' + stem + '/' + f;

function runFiles(stem, groups, strokes) {
  return {
    [out(stem, 'rally_segments.json')]: { fps: 30, rallies: [{ id: 1, start_sec: 0, end_sec: 5 }, { id: 2, start_sec: 6, end_sec: 10 }], detection: { signal: 'courtview' } },
    [out(stem, 'strokes.json')]: { schema: 2, groups, strokes, classifier: { validated_on_owner_footage: true } },
    [out(stem, 'metadata.json')]: { video: { fps: 30 } },
    ['/api/technique/' + stem]: {
      summary: { stroke_count: 2, scored_count: 2, by_type: { smash: { count: 2, avg_score: 70 } }, recurring_weaknesses: [{ metric: 'elbow_extension', count: 1 }] },
      strokes: [{ scored: true, contact_frame: 30, player_side: 'lower', stroke_type: 'smash', group: 'smash', overall_score: 70 }],
    },
    ['/api/insights/video/' + stem]: { rally: { status: 'ok', value: { count: 2, label: 'rallies', signal: 'courtview' } }, technique: { status: 'ok', value: { by_side: { lower: { index: 71.5 } } } } },
  };
}

function routes() {
  return {
    ...runFiles('p1', { smash: 2, clear: 1 }, [
      { frame: 30, hitter: 'lower', stroke: 'smash', group: 'smash' },
      { frame: 60, hitter: 'upper', stroke: 'clear', group: 'clear' },
      { frame: 210, hitter: 'lower', stroke: 'smash', group: 'smash' },
    ]),
    ...runFiles('p2', { smash: 1, net: 2 }, [
      { frame: 30, hitter: 'upper', stroke: 'smash', group: 'smash' },
      { frame: 90, hitter: 'lower', stroke: 'net_shot', group: 'net' },
      { frame: 200, hitter: 'upper', stroke: 'net_shot', group: 'net' },
    ]),
    ...runFiles('solo', { smash: 1 }, [{ frame: 30, hitter: 'lower', stroke: 'smash', group: 'smash' }]),
    '/api/matches/solo': { upper: { name: 'Lee' }, lower: { name: 'Chung' } },
    '/api/matches/p1': {
      date: '2026-10-01', upper: { name: 'Lee' }, lower: { name: 'Chung' }, result: { winner: 'lower', sets: [[18, 21]] },
      parts: [{ stem: 'p2', sides_swapped: true }, { stem: 'p3', sides_swapped: false }],
    },
    '/api/matches/p2': { date: '2026-10-01', upper: { name: 'Chung' }, lower: { name: 'Lee' }, part_of: 'p1', sides_swapped: true },
    '/api/matches/p3': { part_of: 'p1', sides_swapped: false },
    '/api/videos': [
      { name: 'p1', has_match: true, has_posture: false, thumb: '/api/output/p1/thumb.jpg', duration_sec: 600 },
      { name: 'p2', has_match: true, has_posture: false, thumb: '/api/output/p2/thumb.jpg', duration_sec: 300 },
      { name: 'p3', has_match: false, has_posture: false, duration_sec: 90 },
    ],
  };
}

/**
 * Mount the page on a stubbed fetch. `table` is the route table, `fail` maps a URL to an HTTP error status, `hold(url)`
 * may return a promise the reply waits for (it ignores aborts, like a slow server), `before({doc})` runs before
 * mount, and `router(calls, getInst)` replaces the recording router (whose navigate never dispatches).
 */
async function withPage(fn, { stem = 'p1', query = {}, table = routes(), fail = {}, hold = null, before = null, router: makeRouter = null } = {}) {
  const { doc } = installEnv();
  if (before) before({ doc });
  const savedApi = { ...api };
  const calls = { replace: [], navigate: [], fetched: [] };
  globalThis.fetch = async (url) => {
    calls.fetched.push(url);
    const wait = hold ? hold(url) : null;
    if (wait) await wait;
    if (fail[url]) return { ok: false, status: fail[url], json: async () => ({}) };
    const body = table[url];
    if (body === undefined) return { ok: false, status: 404, json: async () => ({}) };
    return { ok: true, status: 200, json: async () => body };
  };
  const container = doc.createElement('div');
  doc.body.appendChild(container);
  let inst = null;
  const router = makeRouter ? makeRouter(calls, () => inst)
    : { replace: (h) => calls.replace.push(h), navigate: (h) => calls.navigate.push(h) };
  inst = mount(container, { params: { stem }, query, lang: 'en', reduced: true, router });
  try {
    await settle();
    await fn({ container, inst, calls, doc });
  } finally {
    inst.destroy();
    closeTool();
    Object.assign(api, savedApi);
    delete globalThis.fetch;
    removeEnv();
  }
}

async function settle() { for (let i = 0; i < 12; i++) await tick(); }

const partBtn = (c, text) => c.querySelectorAll('.ws-parts button').find((b) => b.textContent.startsWith(text));
const panel = (c) => c.querySelector('[role="tabpanel"]');

test('whole match: one card per part, the unanalysed part says so, movement per analysed part, pooled numbers', async () => {
  await withPage(async ({ container, calls }) => {
    assert.equal(calls.replace.length, 0);
    const cards = container.querySelectorAll('.ws-part-card');
    assert.equal(cards.length, 3);
    assert.match(cards[2].textContent, new RegExp(t('wsx.parts.not_analysed', 'en')));
    assert.match(cards[1].textContent, /Part 2/);
    assert.match(cards[1].textContent, /Sides swapped/);
    assert.match(cards[0].textContent, /71\.5/);
    assert.match(cards[0].textContent, /10:00/);
    assert.equal(cards[0].querySelector('img').getAttribute('src'), '/api/output/p1/thumb.jpg');
    assert.ok(cards[2].querySelector('.mv-thumb'), 'blank poster tile without a thumb');
    assert.equal(container.querySelector('.ws-video-card'), null);
    assert.equal(container.querySelectorAll('.ws-move img').length, 4);
    // switcher: Whole match pressed
    assert.equal(partBtn(container, 'Whole match').getAttribute('aria-pressed'), 'true');
    // pooled distribution: p1 (3) + p2 (3)
    assert.match(panel(container).textContent, /6 classified · 0 uncertain/);
    assert.equal(panel(container).querySelectorAll('.ws-dist-seg').length, 3);
    // header: parts count and pooled rallies; no posture link, no delete menu
    const head = container.querySelector('.ws-head');
    assert.match(head.textContent, /3 parts/);
    assert.match(head.textContent, /4 rallies/);
    assert.match(head.textContent, /Chung/);
    assert.equal(head.querySelector('.ws-menu-btn'), null);
    // technique: pooled with the note
    container.querySelectorAll('[role="tab"]')[1].click();
    assert.match(panel(container).textContent, /Pooled across the analysed parts/);
    assert.match(panel(container).textContent, /4 strokes analysed/);
    // clips: per video, one button per part that opens it on the same tab
    container.querySelectorAll('[role="tab"]')[2].click();
    assert.match(panel(container).textContent, /per video/);
    const btn = panel(container).querySelectorAll('button').find((b) => b.textContent === 'Part 2');
    btn.click();
    assert.equal(calls.navigate.at(-1), formatHash('matches', { stem: 'p1' }, { part: 'p2', tab: 'clips' }));
    // a card opens its part
    cards[1].click();
    assert.equal(calls.navigate.at(-1), formatHash('matches', { stem: 'p1' }, { tab: 'clips', part: 'p2' }));
  });
});

test('a part opened by its own stem rewrites the hash to the primary (replace, never navigate)', async () => {
  await withPage(async ({ calls }) => {
    assert.deepEqual(calls.replace, [formatHash('matches', { stem: 'p1' }, { part: 'p2' })]);
    assert.equal(calls.navigate.length, 0);
  }, { stem: 'p2' });
});

test('part view: the part video under the same header, Part 2 pressed with its swapped chip; setQuery switches in place', async () => {
  await withPage(async ({ container, inst, calls }) => {
    const card = container.querySelector('.ws-video-card');
    assert.ok(card);
    const video = card.querySelector('video');
    assert.equal(video.src, '/api/output/p2/detect_p2.mp4');
    const p2 = partBtn(container, 'Part 2');
    assert.equal(p2.getAttribute('aria-pressed'), 'true');
    assert.ok(p2.querySelector('.k-chip'), 'swapped chip');
    assert.equal(partBtn(container, 'Whole match').getAttribute('aria-pressed'), 'false');
    // the header keeps the primary's frame; the body uses the part's resolved names
    assert.match(container.querySelector('h1').textContent, /Chung.*Lee/);
    assert.ok(container.querySelector('.ws-head .ws-menu-btn'), 'delete menu on a part');
    const root = container.firstChild;
    let paused = false;
    video.pause = () => { paused = true; };
    inst.setQuery({ part: 'p1' });
    await settle();
    assert.equal(container.firstChild, root, 'no remount');
    assert.equal(container.querySelector('video').src, '/api/output/p1/detect_p1.mp4');
    assert.ok(paused, 'the previous part video was torn down');
    assert.equal(container.querySelectorAll('video').length, 1);
    assert.equal(partBtn(container, 'Part 1').getAttribute('aria-pressed'), 'true');
    assert.equal(calls.replace.length, 0);
    // the switcher navigates (a history entry); the current part does nothing
    partBtn(container, 'Part 1').click();
    assert.equal(calls.navigate.length, 0);
    partBtn(container, 'Whole match').click();
    assert.deepEqual(calls.navigate, ['#/matches/p1']);
    inst.setQuery({});
    await settle();
    assert.equal(container.querySelector('video'), null);
    assert.equal(container.querySelectorAll('.ws-part-card').length, 3);
  }, { query: { part: 'p2' } });
});

test('an unknown part falls back to the whole match with the part dropped from the hash', async () => {
  await withPage(async ({ calls }) => {
    assert.deepEqual(calls.replace, [formatHash('matches', { stem: 'p1' }, {})]);
  }, { query: { part: 'zz' } });
});

test('a part without outputs: header and switcher stay, the body says it is not analysed', async () => {
  await withPage(async ({ container }) => {
    assert.ok(container.querySelector('.ws-head'));
    assert.ok(container.querySelector('.ws-parts'));
    assert.equal(partBtn(container, 'Part 3').getAttribute('aria-pressed'), 'true');
    const body = container.querySelector('.ws-body');
    assert.match(body.textContent, new RegExp(t('wsx.parts.not_analysed', 'en')));
    assert.equal(container.querySelector('video'), null);
  }, { query: { part: 'p3' } });
});

test('a match without parts keeps the single-video page (no switcher)', async () => {
  await withPage(async ({ container, calls }) => {
    assert.equal(container.querySelector('.ws-parts'), null);
    assert.ok(container.querySelector('.ws-video-card'));
    assert.equal(calls.replace.length, 0);
    assert.equal(everything(container).some((e) => e.textContent === 'Whole match'), false);
  }, { stem: 'solo' });
});

test('a part change from a card or the switcher puts focus on the pressed switcher button', async () => {
  await withPage(async ({ container, inst, doc, calls }) => {
    const card = container.querySelectorAll('.ws-part-card')[1];
    card.focus();
    card.click();
    assert.deepEqual(calls.navigate, [formatHash('matches', { stem: 'p1' }, { part: 'p2' })]);
    inst.setQuery({ part: 'p2' });
    assert.equal(doc.activeElement, partBtn(container, 'Part 2'), 'focus on the pressed part, not lost with the old body');
    await settle();
    assert.equal(doc.activeElement, partBtn(container, 'Part 2'), 'still there once the part rendered');
    partBtn(container, 'Whole match').click();
    inst.setQuery({});
    assert.equal(doc.activeElement, partBtn(container, 'Whole match'));
    assert.equal(partBtn(container, 'Whole match').getAttribute('aria-pressed'), 'true');
    // a part change that did not start on the page (Back / a link) leaves focus alone
    doc.activeElement = null;
    inst.setQuery({ part: 'p2' });
    assert.equal(doc.activeElement, null);
  });
});

test('zh: a part reads 第 N 段 like the Matches list', () => {
  assert.equal(t('wsx.parts.n', 'zh', { n: 2 }), '第 2 段');
  assert.equal(t('wsx.parts.n', 'zh', { n: 2 }), t('link.part_label', 'zh', { n: 2 }));
});

test('whole match: a part whose files all fail with a server error says it could not load; opening it shows the error', async () => {
  const fail = {};
  for (const f of ['rally_segments.json', 'strokes.json', 'metadata.json']) fail[out('p2', f)] = 500;
  fail['/api/insights/video/p2'] = 503;
  fail['/api/technique/p2'] = 500;
  await withPage(async ({ container, inst }) => {
    const cards = container.querySelectorAll('.ws-part-card');
    assert.match(cards[1].textContent, new RegExp(t('error.load.title', 'en')));
    assert.doesNotMatch(cards[1].textContent, new RegExp(t('wsx.parts.not_analysed', 'en')));
    assert.match(cards[2].textContent, new RegExp(t('wsx.parts.not_analysed', 'en')), 'every file 404: still not analysed');
    assert.doesNotMatch(cards[2].textContent, new RegExp(t('error.load.title', 'en')));
    inst.setQuery({ part: 'p2' });
    await settle();
    assert.ok(container.querySelector('.ws-head'), 'header kept');
    assert.ok(container.querySelector('.ws-parts'), 'switcher kept');
    assert.equal(partBtn(container, 'Part 2').getAttribute('aria-pressed'), 'true');
    const alert = container.querySelector('.ws-body [role="alert"]');
    assert.ok(alert, 'error state in the body');
    assert.match(alert.textContent, new RegExp(t('error.load.title', 'en')));
    assert.ok(alert.querySelectorAll('button').some((b) => b.textContent === t('btn.retry', 'en')));
    assert.equal(container.querySelector('video'), null);
  }, { fail });
});

test('a part press whose navigate never lands does not move focus on a later part change from elsewhere', async () => {
  await withPage(async ({ container, inst, doc, calls }) => {
    // The recording router swallows navigate: no setQuery follows the press.
    container.querySelectorAll('.ws-part-card')[1].click();
    assert.equal(calls.navigate.length, 1);
    doc.activeElement = null;
    inst.setQuery({ part: 'p3' });
    await settle();
    assert.equal(doc.activeElement, null, 'a Back-style change to another part leaves focus alone');
    inst.setQuery({ part: 'p2' });
    await settle();
    assert.equal(doc.activeElement, null, 'the stale press is not honoured later either');
  });
});

test('whole match: a degraded part makes the pooled header read windows (the windows pill, no rally count)', async () => {
  const table = routes();
  table[out('p2', 'rally_segments.json')] = { ...table[out('p2', 'rally_segments.json')], detection: { signal: 'courtview', degraded: true } };
  await withPage(async ({ container }) => {
    const head = container.querySelector('.ws-head');
    assert.match(head.textContent, new RegExp(t('ws.count.windows', 'en', { n: 4 })));
    const pill = head.querySelector('.ov-pill');
    assert.ok(pill, 'windows pill');
    assert.equal(pill.textContent, t('tag.windows', 'en'));
    assert.doesNotMatch(head.textContent, /\d+ rallies/);
  }, { table });
});

test('whole match: a part whose rally file is unreadable is noted in the header and left out of the pooled count', async () => {
  await withPage(async ({ container }) => {
    const head = container.querySelector('.ws-head');
    assert.match(head.textContent, new RegExp(t('wsx.parts.failed_note', 'en', { n: 1 }).replace(/[()]/g, '\\$&')));
    assert.match(head.textContent, /(^|\D)2 rallies/, 'Part 1 only');
    assert.doesNotMatch(head.textContent, /(^|\D)4 rallies/);
  }, { fail: { [out('p2', 'rally_segments.json')]: 500 } });
});

test('a fast part switch: a slow Part 2 that ignores the abort never replaces Part 1', async () => {
  let holding = false;
  let release;
  const gate = new Promise((r) => { release = r; });
  const hold = (url) => (holding && url.includes('p2') ? gate : null);
  await withPage(async ({ container, inst }) => {
    holding = true;
    inst.setQuery({ part: 'p2' });
    await tick();
    inst.setQuery({ part: 'p1' });
    await settle();
    assert.equal(container.querySelector('video').src, '/api/output/p1/detect_p1.mp4');
    release();
    await settle();
    assert.equal(container.querySelectorAll('video').length, 1);
    assert.equal(container.querySelector('video').src, '/api/output/p1/detect_p1.mp4');
    assert.equal(partBtn(container, 'Part 1').getAttribute('aria-pressed'), 'true');
    assert.equal(partBtn(container, 'Part 2').getAttribute('aria-pressed'), 'false');
  }, { hold });
});

// A shown video holds two keydown listeners (the player's Escape and the rally-label keys) and one fullscreenchange.
test('the player releases its document listeners: two keydown and one fullscreenchange while a part shows, none otherwise', async () => {
  const live = { keydown: [], fullscreenchange: [] };
  const before = ({ doc }) => {
    doc.addEventListener = (type, fn) => { if (live[type]) live[type].push(fn); };
    doc.removeEventListener = (type, fn) => {
      const i = live[type] ? live[type].indexOf(fn) : -1;
      if (i >= 0) live[type].splice(i, 1);
    };
  };
  const count = () => `${live.keydown.length}/${live.fullscreenchange.length}`;
  await withPage(async ({ inst }) => {
    assert.equal(count(), '0/0', 'Whole match: no player');
    inst.setQuery({ part: 'p1' });
    await settle();
    assert.equal(count(), '2/1');
    inst.setQuery({ part: 'p2' });
    await settle();
    assert.equal(count(), '2/1', 'the previous part player let go of its listeners');
    inst.setQuery({});
    await settle();
    assert.equal(count(), '0/0', 'back on the Whole match');
    inst.setQuery({ part: 'p1' });
    await settle();
    assert.equal(count(), '2/1');
    inst.destroy();
    assert.equal(count(), '0/0', 'after destroy');
  }, { before });
});

test('a router that dispatches on replace: an unknown part redirects once, then the Whole match renders', async () => {
  const router = (calls, getInst) => ({
    navigate: (h) => calls.navigate.push(h),
    replace: (h) => {
      calls.replace.push(h);
      const r = parseHash(h);
      // Same stem: the router calls the mounted page's setQuery synchronously (main.js does the same).
      if (r.name === 'matches' && r.params.stem === 'p1') getInst().setQuery(r.query);
    },
  });
  await withPage(async ({ container, calls }) => {
    assert.deepEqual(calls.replace, ['#/matches/p1']);
    assert.equal(container.querySelectorAll('.ws-part-card').length, 3);
    assert.equal(partBtn(container, 'Whole match').getAttribute('aria-pressed'), 'true');
    assert.equal(container.querySelector('video'), null);
  }, { query: { part: 'zz' }, router });
});

test('whole match: the pooled point tally exchanges near and far for a swapped part and shows the match reconciliation', async () => {
  const rc = { status: 'short' };
  const doc = (winners) => ({
    schema: 1, rallies: winners.map((w, i) => ({ id: i + 1, segment_id: i + 1, winner_side: w })), summary: { reconciliation: rc },
  });
  const table = routes();
  table[out('p1', 'rally_outcomes.json')] = doc(['lower', 'lower', 'upper']);
  table[out('p2', 'rally_outcomes.json')] = doc(['lower', 'upper', null]); // p2 has swapped sides: its lower is p1's upper
  await withPage(async ({ container }) => {
    const card = container.querySelector('.ws-pt-whole');
    assert.ok(card, 'pooled card');
    assert.match(card.querySelector('.ws-tally').textContent, /Near 3 – Far 2 · 5 of 6 labelled/);
    assert.equal(card.querySelector('.ws-rc').getAttribute('data-status'), 'short');
  }, { table });
  await withPage(async ({ container }) => {
    assert.equal(container.querySelector('.ws-pt-whole'), null, 'no outcome files: no pooled card');
  });
});
