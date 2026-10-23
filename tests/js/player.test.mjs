import test from 'node:test';
import assert from 'node:assert/strict';
import { installEnv, removeEnv, everything } from './fake_dom.mjs';
import { createPlayer, parseVolume } from '../../static/kestrel/widgets/player.js';

function memPrefs(init = {}) {
  const m = new Map(Object.entries(init));
  return { get: (k, fb = null) => (m.has(k) ? m.get(k) : fb), set: (k, v) => { m.set(k, String(v)); return true; }, m };
}

test('parseVolume: clamps and defaults to 1', () => {
  assert.equal(parseVolume('0.4'), 0.4);
  assert.equal(parseVolume('7'), 1);
  assert.equal(parseVolume('-1'), 0);
  assert.equal(parseVolume('x'), 1);
  assert.equal(parseVolume(null), 1);
});

test('player: restores volume and mute from prefs; the mute button toggles and saves', () => {
  installEnv();
  try {
    const prefs = memPrefs({ kestrel_volume: '0.3', kestrel_muted: '1' });
    const p = createPlayer({ files: ['/a.mp4'], lang: 'en', prefs });
    assert.equal(p.video.volume, 0.3);
    assert.equal(p.video.muted, true);
    const mute = everything(p.el).find((e) => e.classList && e.classList.contains('ws-mute'));
    assert.equal(mute.getAttribute('aria-pressed'), 'true');
    mute.dispatch('click');
    assert.equal(p.video.muted, false);
    assert.equal(prefs.m.get('kestrel_muted'), '0');
    const range = everything(p.el).find((e) => e.classList && e.classList.contains('ws-vol-range'));
    range.value = '0.6';
    range.dispatch('input');
    assert.equal(p.video.volume, 0.6);
    assert.equal(prefs.m.get('kestrel_volume'), '0.6');
    range.value = '0';
    range.dispatch('input');
    assert.equal(p.video.muted, true, 'volume 0 reads as muted');
    p.destroy();
  } finally { removeEnv(); }
});

test('player: M toggles mute from inside the card, not from a text field; storage that throws is harmless', () => {
  installEnv();
  try {
    const prefs = { get: () => { throw new Error('denied'); }, set: () => { throw new Error('denied'); } };
    const p = createPlayer({ files: ['/a.mp4'], lang: 'zh', prefs });
    assert.equal(p.video.muted, false);
    p.el.dispatch('keydown', { key: 'm', target: p.video, preventDefault() {} });
    assert.equal(p.video.muted, true);
    p.el.dispatch('keydown', { key: 'm', target: { tagName: 'INPUT', type: 'text' }, preventDefault() {} });
    assert.equal(p.video.muted, true, 'typing in a text field does not toggle');
    p.destroy();
  } finally { removeEnv(); }
});

test('player: Ctrl / Alt / Meta + M leave mute alone', () => {
  installEnv();
  try {
    const p = createPlayer({ files: ['/a.mp4'], lang: 'en', prefs: memPrefs() });
    for (const mod of ['ctrlKey', 'altKey', 'metaKey']) {
      const ev = p.el.dispatch('keydown', { key: 'm', target: p.video, [mod]: true });
      assert.equal(p.video.muted, false, mod);
      assert.equal(ev.defaultPrevented, false, mod + ' keeps its browser meaning');
    }
    p.el.dispatch('keydown', { key: 'M', target: p.video, shiftKey: true });
    assert.equal(p.video.muted, true, 'Shift+M still toggles');
    p.destroy();
  } finally { removeEnv(); }
});

test('player: when every source fails, the mute button and volume slider are disabled with the other controls', () => {
  installEnv();
  try {
    const p = createPlayer({ files: ['/a.mp4', '/b.mp4'], lang: 'en', prefs: memPrefs() });
    const find = (cls) => everything(p.el).find((e) => e.classList && e.classList.contains(cls));
    p.video.error = { code: 4 };
    p.video.dispatch('error');
    assert.equal(p.video.src, '/b.mp4', 'second candidate tried');
    assert.equal(find('ws-mute').disabled, false);
    p.video.dispatch('error');
    assert.equal(find('ws-mute').disabled, true);
    assert.equal(find('ws-vol-range').disabled, true);
    assert.equal(find('ws-play').disabled, true);
    p.destroy();
  } finally { removeEnv(); }
});

test('player: theater button moves the card under body and back', () => {
  installEnv();
  try {
    const host = document.createElement('div');
    document.body.appendChild(host);
    const p = createPlayer({ files: ['/a.mp4'], lang: 'en', prefs: memPrefs() });
    host.appendChild(p.el);
    const th = everything(p.el).find((e) => e.classList && e.classList.contains('ws-theater'));
    th.dispatch('click');
    assert.equal(p.el.parentNode, document.body);
    assert.equal(th.getAttribute('aria-pressed'), 'true');
    th.dispatch('click');
    assert.equal(p.el.parentNode, host);
    p.destroy();
  } finally { removeEnv(); }
});

test('player: M does nothing once every source has failed', () => {
  installEnv();
  try {
    const prefs = memPrefs();
    const p = createPlayer({ files: ['/a.mp4'], lang: 'en', prefs });
    p.video.error = { code: 4 };
    p.video.dispatch('error');
    const ev = p.el.dispatch('keydown', { key: 'm', target: p.el });
    assert.equal(p.video.muted, false);
    assert.equal(ev.defaultPrevented, false);
    assert.equal(prefs.m.has('kestrel_muted'), false, 'nothing saved');
    p.destroy();
  } finally { removeEnv(); }
});
