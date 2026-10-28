import test from 'node:test';
import assert from 'node:assert/strict';
import { installEnv, removeEnv, tick, everything, buttonWithText } from './fake_dom.mjs';
import { t } from '../../static/kestrel/core/i18n.js';
import { strategyPanel } from '../../static/kestrel/widgets/strategy.js';

const claim = (text, evidence = ['match.rally_median_sec']) => ({ text, evidence });
const sec = (claims, extra = {}) => ({ status: 'ok', llm: true, claims, ...extra });

function report(over = {}) {
  return {
    header: { you: 'Axelsen', opponent: 'Antonsen', date: '2026-10-10', winner: 'you', sets: [[21, 15], [21, 18]], confidence: 'medium', both_registered: false },
    summary: sec([claim('<b>x</b> won it'), claim('b'), claim('c')]),
    match_profile: sec([claim('profile')]),
    your_game: { status: 'ok', llm: true, shot_selection: { claims: [claim('shots')] }, movement: { claims: [claim('moves')] }, patterns: { claims: [claim('pat')] }, strengths: { claims: [claim('str')] }, weaknesses: { claims: [claim('weak')] } },
    game_plan: { status: 'ok', llm: true, items: [{ do: 'Attack the rear', why: 'They lift short', cue: 'Early prep', evidence: ['match.rally_median_sec'] }], training_focus: claim('Footwork') },
    scouting: sec([claim('scout')]),
    caveats: [{ code: 'no_rally_outcomes', params: {} }, { code: 'degraded_rallies', params: { share: 0.25 } }],
    ...over,
  };
}

function payload(over = {}, rep = report()) {
  return {
    enabled: true, status: 'ok', stale: false, job: null,
    report: {
      status: 'ok', model: 'qwen3.8:27b', generated_at: '2026-10-27T10:00:00Z', focus: { side: 'lower', assumed: false },
      facts: [{ id: 'match.rally_median_sec', value: 7.5, unit: 's' }],
      by_lang: { en: rep, zh: rep },
    },
    ...over,
  };
}

const fakeApi = (first, calls = []) => ({
  calls,
  get: async (url) => { calls.push(['GET', url]); return typeof first === 'function' ? first() : first; },
  post: async (url, body) => { calls.push(['POST', url, body]); return { status: 'pending', job: { status: 'pending' } }; },
});

const fakeTimers = () => {
  const tm = { pending: new Map(), n: 0, cleared: [] };
  tm.set = (fn, ms) => { tm.n += 1; tm.pending.set(tm.n, [fn, ms]); return tm.n; };
  tm.clear = (id) => { tm.cleared.push(id); tm.pending.delete(id); };
  return tm;
};

test('ready payload renders the five sections, caveats, footer and renders claim text literally', async () => {
  installEnv();
  try {
    const api = fakeApi(payload());
    const p = strategyPanel({ stem: 's t', lang: 'en', api, timers: fakeTimers() });
    await tick();
    assert.deepEqual(api.calls[0], ['GET', '/api/strategy/s%20t']);
    const details = everything(p.el).filter((e) => e.tagName === 'DETAILS');
    const classes = details.map((d) => d.getAttribute('class'));
    assert.equal(details.filter((d) => d.getAttribute('data-sec') !== 'caveats').length, 5);
    assert.ok(details.find((d) => d.getAttribute('data-sec') === 'caveats'));
    assert.ok(classes.length >= 6);
    const open = details.filter((d) => d.hasAttribute('open')).map((d) => d.getAttribute('data-sec'));
    assert.deepEqual(open.sort(), ['game_plan', 'summary']);
    const txt = p.el.textContent;
    assert.ok(txt.includes('<b>x</b> won it'), 'claim text is literal, not HTML');
    assert.ok(txt.includes('Axelsen'));
    assert.ok(txt.includes('Attack the rear'));
    assert.ok(txt.includes('Footwork'));
    assert.ok(txt.includes(t('strategy.caveat.no_rally_outcomes', 'en')));
    assert.ok(txt.includes('25%'));
    assert.ok(txt.includes(t('strategy.conf.medium', 'en')));
    assert.ok(txt.includes('AI-written from measured data · qwen3.8:27b · 2026-10-27 10:00'));
    assert.ok(!txt.includes(t('strategy.fallback', 'en')));
    const chip = everything(p.el).find((e) => e.getAttribute('title') === 'match.rally_median_sec = 7.5 s');
    assert.ok(chip, 'evidence chip carries id = value unit');
    assert.equal(everything(p.el).some((e) => e.tagName === 'SCRIPT' || e.tagName === 'B'), false);
    const a = everything(p.el).find((e) => e.tagName === 'A' && e.hasAttribute('download'));
    assert.equal(a.getAttribute('href'), '/api/strategy/s%20t.md?lang=en');
    assert.equal(buttonWithText(p.el, t('strategy.other_side', 'en')), null, 'only with both sides registered');
    p.destroy();
  } finally { removeEnv(); }
});

test('a fallback section carries the tag; failed shows the reason and keeps fallback content', async () => {
  installEnv();
  try {
    const rep = report({ scouting: { status: 'fallback', llm: false, claims: [claim('fallback scout')] } });
    const pl = payload({ status: 'failed' }, rep);
    pl.report.status = 'failed';
    pl.report.reason = { code: 'llm_unreachable', params: {} };
    const p = strategyPanel({ stem: 's', lang: 'en', api: fakeApi(pl), timers: fakeTimers() });
    await tick();
    const txt = p.el.textContent;
    assert.ok(txt.includes(t('strategy.reason.llm_unreachable', 'en')));
    assert.ok(txt.includes('fallback scout'));
    assert.equal(txt.split(t('strategy.fallback', 'en')).length - 1, 1);
    const unk = strategyPanel({ stem: 's', lang: 'en', api: fakeApi(payload({ status: 'failed', report: { status: 'failed', reason: { code: 'weird' }, by_lang: {} } })), timers: fakeTimers() });
    await tick();
    assert.ok(unk.el.textContent.includes(t('strategy.reason.unknown', 'en')));
  } finally { removeEnv(); }
});

test('off, none, queued and stale states', async () => {
  installEnv();
  try {
    const off = strategyPanel({ stem: 's', lang: 'en', api: fakeApi({ enabled: false, status: 'none' }), timers: fakeTimers() });
    const none = strategyPanel({ stem: 's', lang: 'en', api: fakeApi({ enabled: true, status: 'none' }), timers: fakeTimers() });
    const tm = fakeTimers();
    const queued = strategyPanel({ stem: 's', lang: 'en', api: fakeApi({ enabled: true, status: 'pending', job: { status: 'pending' } }), timers: tm });
    const stale = strategyPanel({ stem: 's', lang: 'en', api: fakeApi(payload({ stale: true })), timers: fakeTimers() });
    await tick();
    assert.ok(off.el.textContent.includes(t('strategy.state.off', 'en')));
    assert.equal(buttonWithText(off.el, t('strategy.regen', 'en')), null);
    assert.ok(none.el.textContent.includes(t('strategy.state.none', 'en')));
    assert.ok(buttonWithText(none.el, t('strategy.generate', 'en')));
    assert.ok(queued.el.textContent.includes(t('strategy.state.queued', 'en')));
    assert.equal(everything(queued.el).find((e) => e.tagName === 'A').getAttribute('href'), '#/pipeline');
    assert.equal(tm.pending.size, 1, 'polls while queued');
    assert.equal([...tm.pending.values()][0][1], 3000);
    assert.ok(stale.el.textContent.includes(t('strategy.state.stale', 'en')));
    assert.ok(stale.el.textContent.includes('Axelsen'), 'a stale report is still shown');
    queued.destroy();
  } finally { removeEnv(); }
});

test('regenerate posts, switches to queued and polls; destroy clears the timer', async () => {
  installEnv();
  try {
    const tm = fakeTimers();
    const api = fakeApi(payload());
    const p = strategyPanel({ stem: 's', lang: 'en', api, timers: tm });
    await tick();
    assert.equal(tm.pending.size, 0, 'no polling when ready');
    buttonWithText(p.el, t('strategy.regen', 'en')).dispatch('click');
    await tick();
    assert.deepEqual(api.calls.at(-1), ['POST', '/api/strategy/s/regenerate', {}]);
    assert.ok(p.el.textContent.includes(t('strategy.state.queued', 'en')));
    assert.equal(tm.pending.size, 1);
    const id = [...tm.pending.keys()][0];
    p.destroy();
    assert.ok(tm.cleared.includes(id));
    assert.equal(tm.pending.size, 0);
  } finally { removeEnv(); }
});

test('other side posts the flipped focus; zh uses zh text and falls back to the other language', async () => {
  installEnv();
  try {
    const rep = report();
    rep.header.both_registered = true;
    const api = fakeApi(payload({}, rep));
    const p = strategyPanel({ stem: 's', lang: 'zh', api, timers: fakeTimers() });
    await tick();
    buttonWithText(p.el, t('strategy.other_side', 'zh')).dispatch('click');
    await tick();
    assert.deepEqual(api.calls.at(-1), ['POST', '/api/strategy/s/regenerate', { focus: 'upper' }]);
    const pl = payload();
    delete pl.report.by_lang.zh;
    const q = strategyPanel({ stem: 's', lang: 'zh', api: fakeApi(pl), timers: fakeTimers() });
    await tick();
    assert.ok(q.el.textContent.includes('Axelsen'));
    assert.ok(q.el.textContent.includes(t('strategy.sec.summary', 'zh')));
  } finally { removeEnv(); }
});

test('a load failure and a failed regenerate show messages', async () => {
  installEnv();
  try {
    const bad = { get: async () => { throw Object.assign(new Error('x'), { status: 500 }); }, post: async () => { throw new Error('x'); } };
    const p = strategyPanel({ stem: 's', lang: 'en', api: bad, timers: fakeTimers() });
    await tick();
    assert.ok(p.el.textContent.includes(t('strategy.load_failed', 'en')));
    const api = { get: async () => payload(), post: async () => { throw new Error('x'); } };
    const q = strategyPanel({ stem: 's', lang: 'en', api, timers: fakeTimers() });
    await tick();
    buttonWithText(q.el, t('strategy.regen', 'en')).dispatch('click');
    await tick();
    assert.ok(q.el.textContent.includes(t('strategy.regen_failed', 'en')));
  } finally { removeEnv(); }
});

test('a doubles run with an assumed focus names the team, not the lower side', async () => {
  installEnv();
  try {
    const data = payload();
    data.report.focus = { side: null, team: 1, assumed: true };
    const p = strategyPanel({ stem: 's', lang: 'en', api: fakeApi(data), timers: fakeTimers() });
    await tick();
    const txt = p.el.textContent;
    assert.ok(txt.includes('Team 1 is assumed to be your team'));
    assert.equal(txt.includes('lower-court'), false);
    p.destroy();
  } finally { removeEnv(); }
});

const TABLES = {
  effectiveness: {
    you: { smash: { effective: 4, neutral: 1, ineffective: 1, unknown: 0, n: 6 }, lift: { effective: 1, neutral: 0, ineffective: 0, unknown: 0, n: 1 } },
    opponent: {},
  },
  openings: {
    complete: 6, total: 8, neutral: 1, insufficient: false, won_by: { you: 3, opponent: 2 }, opening_winner_won_point: { n: 5, won: 4 },
    rows: [{ rally: 3, advantage: 'you', point_winner: 'opponent', n_shots: 9, shots: [
      { hitter: 'you', stroke: 'long_serve', zone: { depth: 'rear', side: 'left' }, sent_to: { depth: 'mid', side: 'centre' } },
      { hitter: 'opponent', stroke: '<i>x</i>', zone: null, sent_to: null },
    ] }],
  },
};

test('tactics section renders both tables from report.tables, literal text, in both languages', async () => {
  installEnv();
  try {
    for (const lang of ['en', 'zh']) {
      const pl = payload();
      pl.report.tables = TABLES;
      pl.report.by_lang.en.caveats = [{ code: 'stroke_model', params: { accuracy: 0.81, bar_met: false } }, { code: 'positions_unusable', params: { sides: ['opponent'] } },
        { code: 'openings_incomplete', params: { complete: 2, total: 6 } }, { code: 'owner_labelled', params: { share: 0.25 } }];
      pl.report.by_lang.zh = pl.report.by_lang.en;
      const p = strategyPanel({ stem: 's', lang, api: fakeApi(pl), timers: fakeTimers() });
      await tick();
      const sec = everything(p.el).find((e) => e.tagName === 'DETAILS' && e.getAttribute('data-sec') === 'tactics');
      assert.ok(sec, 'tactics section present');
      const txt = sec.textContent;
      assert.ok(txt.includes(t('strategy.sec.tactics', lang)));
      assert.ok(txt.includes(t('stroke.smash', lang)));
      assert.ok(txt.includes(t('strategy.tactics.few', lang)), 'lift has 1 attempt');
      assert.ok(txt.includes(t('stroke.long_serve', lang)));
      assert.ok(txt.includes('<i>x</i>'), 'unknown stroke ids are text, not HTML');
      assert.equal(everything(sec).some((e) => e.tagName === 'I'), false);
      assert.equal(everything(sec).filter((e) => e.tagName === 'TABLE').length, 2);
      assert.ok(!txt.includes(t('strategy.tactics.open_few', lang)));
      const all = p.el.textContent;
      assert.ok(all.includes(t('strategy.caveat.stroke_model', lang, { accuracy: '81%', bar: t('strategy.bar.not_met', lang) })));
      assert.ok(all.includes(t('strategy.caveat.positions_unusable', lang, { sides: t('strategy.who.opponent', lang) })));
      assert.ok(all.includes(t('strategy.caveat.openings_incomplete', lang, { complete: 2, total: 6 })));
      assert.ok(all.includes(t('strategy.caveat.owner_labelled', lang, { share: '25%' })));
      p.destroy();
    }
  } finally { removeEnv(); }
});

test('tactics section shows empty states when nothing could be measured, and is absent without tables', async () => {
  installEnv();
  try {
    const pl = payload();
    pl.report.tables = {
      effectiveness: { you: {}, opponent: {} },
      openings: { complete: 0, total: 0, neutral: 0, insufficient: true, won_by: { you: 0, opponent: 0 }, opening_winner_won_point: { n: 0, won: 0 }, rows: [] },
    };
    const p = strategyPanel({ stem: 's', lang: 'en', api: fakeApi(pl), timers: fakeTimers() });
    await tick();
    const sec = everything(p.el).find((e) => e.tagName === 'DETAILS' && e.getAttribute('data-sec') === 'tactics');
    const txt = sec.textContent;
    assert.ok(txt.includes(t('strategy.tactics.no_eff', 'en')));
    assert.ok(txt.includes(t('strategy.tactics.no_openings', 'en')));
    assert.ok(txt.includes(t('strategy.tactics.open_few', 'en')));
    assert.equal(everything(sec).some((e) => e.tagName === 'TABLE'), false);
    const old = strategyPanel({ stem: 's', lang: 'en', api: fakeApi(payload()), timers: fakeTimers() });
    await tick();
    assert.equal(everything(old.el).some((e) => e.tagName === 'DETAILS' && e.getAttribute('data-sec') === 'tactics'), false);
  } finally { removeEnv(); }
});
