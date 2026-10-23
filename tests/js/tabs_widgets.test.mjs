import test from 'node:test';
import assert from 'node:assert/strict';
import { installEnv, removeEnv, tick, everything, buttonWithText } from './fake_dom.mjs';
import { t } from '../../static/kestrel/core/i18n.js';
import { ApiError } from '../../static/kestrel/core/api.js';
import { techniquePanel } from '../../static/kestrel/widgets/technique.js';
import { clipsPanel } from '../../static/kestrel/widgets/clips.js';
import { planPanel } from '../../static/kestrel/widgets/plan.js';
import { isOpen, closeTool } from '../../static/kestrel/widgets/videotools.js';

// All the text under an element (the fake `everything` lists elements only, so read textContent).
const text = (el) => el.textContent;
const netErr = () => new ApiError('network', 0, { zh: '无法连接到服务', en: 'Cannot reach the server' });

test('technique panel: ok lists types and weaknesses; doubles and stale say why', () => {
  installEnv();
  try {
    const el = techniquePanel({ lang: 'en', summary: { state: 'ok', count: 68, scored: 45, byType: [{ id: 'clear', count: 19, avg: 56.4 }, { id: 'unknown', count: 9, avg: null }], weaknesses: [{ metric: 'trunk_rotation', count: 43 }] } });
    const s = text(el);
    assert.ok(s.includes('Clear'));
    assert.ok(s.includes('56.4'));
    assert.ok(s.includes(t('metric.trunk_rotation', 'en')));
    assert.ok(s.includes('68 strokes analysed · 45 scored'));
    assert.ok(s.includes(t('ws.uncertain', 'en')), 'unknown type reads as uncertain');
    assert.ok(text(techniquePanel({ lang: 'en', summary: { state: 'doubles' } })).includes(t('wsx.tech.doubles', 'en')));
    assert.ok(text(techniquePanel({ lang: 'en', summary: { state: 'stale' } })).includes(t('ws.stroke_stale', 'en')));
    const empty = text(techniquePanel({ lang: 'en', summary: { state: 'empty' }, note: 'Pooled over 2 parts' }));
    assert.ok(empty.includes(t('wsx.tech.empty', 'en')));
    assert.ok(empty.includes('Pooled over 2 parts'));
  } finally { removeEnv(); }
});

test('clips panel: generate posts highlights with padding 1.5 and lists downloads; disabled without rallies', async () => {
  installEnv();
  try {
    const calls = [];
    const api = { post: async (url, body) => { calls.push([url, body]); return { ok: true, clips: [{ name: 'rally_1.mp4', url: '/api/output/s/clips/rally_1.mp4', size_mb: 2.1 }] }; } };
    const p = clipsPanel({ stem: 's', lang: 'en', rallyOk: true, api });
    buttonWithText(p.el, t('wsx.clips.generate', 'en')).dispatch('click');
    await tick();
    assert.deepEqual(calls, [['/api/clip', { video: 's', mode: 'highlights', padding: 1.5 }]]);
    const a = everything(p.el).find((e) => e.tagName === 'A');
    assert.equal(a.getAttribute('href'), '/api/output/s/clips/rally_1.mp4');
    assert.ok(a.hasAttribute('download'));
    const off = clipsPanel({ stem: 's', lang: 'en', rallyOk: false, api });
    assert.equal(buttonWithText(off.el, t('wsx.clips.generate', 'en')).disabled, true);
    assert.ok(text(off.el).includes(t('wsx.clips.no_rallies', 'en')));
  } finally { removeEnv(); }
});

test('clips panel: a failure shows the message as text and re-enables the button', async () => {
  installEnv();
  try {
    const api = { post: async () => { throw Object.assign(new ApiError('http', 500, { zh: '服务器出错', en: 'Server error' }), { body: { error: '<i>boom</i>' } }); } };
    const p = clipsPanel({ stem: 's', lang: 'en', rallyOk: true, api });
    const btn = buttonWithText(p.el, t('wsx.clips.generate', 'en'));
    btn.dispatch('click');
    assert.equal(btn.disabled, true, 'busy while generating');
    assert.equal(btn.textContent, t('wsx.clips.busy', 'en'));
    await tick();
    const err = p.el.querySelector('.mv-err');
    assert.ok(err.textContent.includes(t('wsx.clips.failed', 'en')));
    assert.ok(err.textContent.includes('<i>boom</i>'));
    assert.equal(btn.disabled, false);
    assert.equal(btn.textContent, t('wsx.clips.generate', 'en'));
    // A 200 with ok:false is a failure too.
    const p2 = clipsPanel({ stem: 's', lang: 'en', rallyOk: true, api: { post: async () => ({ ok: false, error: 'no rallies' }) } });
    buttonWithText(p2.el, t('wsx.clips.generate', 'en')).dispatch('click');
    await tick();
    assert.ok(p2.el.querySelector('.mv-err').textContent.includes('no rallies'));
  } finally { removeEnv(); }
});

test('clips panel: Delete clips opens the clips-scoped dialog; a dismissed dialog keeps the list', async () => {
  installEnv();
  try {
    const p = clipsPanel({
      stem: 's', lang: 'en', rallyOk: true,
      api: { post: async () => ({ ok: true, clips: [{ name: 'a.mp4', url: '/u/a.mp4', size_mb: 1 }] }) },
    });
    buttonWithText(p.el, t('wsx.clips.generate', 'en')).dispatch('click');
    await tick();
    assert.ok(everything(p.el).some((e) => e.tagName === 'A'));
    buttonWithText(p.el, t('wsx.clips.delete', 'en')).dispatch('click');
    assert.ok(isOpen());
    assert.ok(document.body.querySelector('.mv-dlg').textContent.includes(t('wsx.del.clips.title', 'en')));
    closeTool();
    assert.ok(everything(p.el).some((e) => e.tagName === 'A'));
  } finally { closeTool(); removeEnv(); }
});

test('plan panel: court / home toggle, details expand, file text is text', async () => {
  installEnv();
  try {
    const plan = { weeks: [{ week: 1, phase: 'Foundation', sessions: [
      { name: '<b>Shadow</b>', location: 'court', sets: 3, reps: 10, frequency_per_week: 2, duration_min: 8, detail: { instructions: { en: ['Step one'] }, video_url: 'https://www.youtube.com/watch?v=abcdefg' } },
      { name: 'Band', location: 'home', sets: 3, reps: 15, frequency_per_week: 3 }] }] };
    const api = { get: async () => plan, post: async () => plan };
    const p = planPanel({ stem: 's', lang: 'en', api });
    await tick();
    assert.ok(text(p.el).includes('<b>Shadow</b>'), 'name shown as text');
    assert.ok(!text(p.el).includes('Band'));
    buttonWithText(p.el, t('wsx.plan.home', 'en')).dispatch('click');
    assert.ok(text(p.el).includes('Band'));
    buttonWithText(p.el, t('wsx.plan.court', 'en')).dispatch('click');
    const row = everything(p.el).find((e) => e.tagName === 'BUTTON' && e.getAttribute('aria-expanded') === 'false');
    row.dispatch('click');
    assert.ok(text(p.el).includes('Step one'));
    assert.ok(everything(p.el).some((e) => e.tagName === 'A' && e.getAttribute('href') === 'https://www.youtube.com/watch?v=abcdefg'));
  } finally { removeEnv(); }
});

test('plan panel: week heading, meta line, not-found and failure states, regenerate', async () => {
  installEnv();
  try {
    const plan = { weeks: [{ week: 2, phase: 'Progression', sessions: [
      { name: 'Shadow', location: 'court', sets: 3, reps: 10, frequency_per_week: 2, duration_min: 8 }] }] };
    const posts = [];
    const api = { get: async () => plan, post: async (url, body) => { posts.push([url, body]); return plan; } };
    const p = planPanel({ stem: 'a b', lang: 'en', api });
    await tick();
    assert.ok(text(p.el).includes('Week 2 · Progression'));
    assert.ok(text(p.el).includes('3×10 · 2×/wk · 8 min'));
    // A session without detail points at Regenerate.
    everything(p.el).find((e) => e.getAttribute('aria-expanded') === 'false').dispatch('click');
    assert.ok(text(p.el).includes(t('wsx.plan.old', 'en')));
    buttonWithText(p.el, t('wsx.plan.regen', 'en')).dispatch('click');
    await tick();
    assert.deepEqual(posts, [['/api/training-plan/a%20b', { weeks: 4 }]]);

    const none = planPanel({ stem: 's', lang: 'en', api: { get: async () => { throw new ApiError('http', 404, { zh: '未找到', en: 'Not found' }); } } });
    await tick();
    assert.ok(text(none.el).includes(t('wsx.plan.none', 'en')));
    const bad = planPanel({ stem: 's', lang: 'en', api: { get: async () => { throw netErr(); } } });
    await tick();
    assert.ok(text(bad.el).includes(t('wsx.plan.failed', 'en')));

    const failing = planPanel({ stem: 's', lang: 'en', api: { get: async () => plan, post: async () => { throw netErr(); } } });
    await tick();
    buttonWithText(failing.el, t('wsx.plan.regen', 'en')).dispatch('click');
    await tick();
    assert.ok(text(failing.el).includes(t('wsx.plan.regen_failed', 'en')));
    assert.ok(text(failing.el).includes('Shadow'), 'the old plan stays');
    buttonWithText(failing.el, t('wsx.plan.home', 'en')).dispatch('click');
    assert.ok(text(failing.el).includes(t('wsx.plan.nothing', 'en')));
  } finally { removeEnv(); }
});
