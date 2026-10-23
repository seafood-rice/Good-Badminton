// The match video card: the stage (video + overlay tag slots + message), the control row (previous / play / next,
// time, seek, mute + volume, theater, full screen) and the source fallback. Used by the match workspace; the owner
// of the card paints overlay tags into `tagsEl` / `sigEl` and reacts to `onSourceFallback`. Volume and mute are
// remembered per browser (prefs), and storage that throws only means "this session".
import { t } from '../core/i18n.js';
import { prefs as sharedPrefs, PREF_KEYS } from '../core/prefs.js';
import { h, setText, setAttr } from './frame.js';
import { clockText, isNum } from '../views/workspace_model.js';

/** A stored volume as a number in [0, 1]; missing or junk reads as full volume. */
export function parseVolume(raw) {
  const n = Number(raw);
  if (raw === null || raw === undefined || raw === '' || !Number.isFinite(n)) return 1;
  return Math.max(0, Math.min(1, n));
}

/**
 * Build the video card.
 * @param {object} opts
 * @param {string[]} opts.files video URLs, tried in order
 * @param {'zh'|'en'} [opts.lang]
 * @param {{get:Function,set:Function}} [opts.prefs] volume / mute storage (default: the shared prefs)
 * @param {{prev?:string,next?:string}} [opts.labels] aria labels of the previous / next buttons
 * @param {Function} [opts.onPrev] @param {Function} [opts.onNext]
 * @param {(idx:number)=>void} [opts.onSourceFallback] called after the next candidate file is tried
 * @param {boolean} [opts.playersVideo] the first file is the players video (cleared on fallback)
 */
export function createPlayer(opts = {}) {
  const lang = opts.lang === 'en' ? 'en' : 'zh';
  const store = opts.prefs || sharedPrefs;
  const files = Array.isArray(opts.files) ? opts.files : [];
  const labels = opts.labels || {};
  const onPrev = typeof opts.onPrev === 'function' ? opts.onPrev : null;
  const onNext = typeof opts.onNext === 'function' ? opts.onNext : null;
  const onSourceFallback = typeof opts.onSourceFallback === 'function' ? opts.onSourceFallback : null;

  let destroyed = false;
  let theater = false;
  let pendingSeek;
  let playersVideo = !!opts.playersVideo;

  const sec = h('section', { class: 'card hud ws-video-card', 'aria-label': t('ws.video', lang) });
  const stage = h('div', { class: 'ws-stage' });
  const video = h('video', { class: 'ws-video', preload: 'metadata', playsinline: true });
  const tagsEl = h('div', { class: 'ws-tags' });
  const sigEl = h('div', { class: 'ws-tag-r' });
  const msg = h('div', { class: 'ws-video-msg', role: 'status', hidden: true });
  stage.appendChild(video);
  stage.appendChild(tagsEl);
  stage.appendChild(sigEl);
  stage.appendChild(msg);

  const playBtn = h('button', { type: 'button', class: 'ws-play', 'aria-label': t('ws.play', lang), onclick: () => togglePlay() }, '▶');
  const time = h('span', { class: 'mono ws-time' }, '0:00');
  const seek = h('input', { type: 'range', class: 'ws-seek', min: '0', max: '0', step: '0.1', value: '0', 'aria-label': t('ws.seek_aria', lang) });
  seek.addEventListener('input', () => seekTo(Number(seek.value), false));

  // Theater mode: the whole video card fills the browser window (it is moved under <body> so no ancestor
  // transform can shrink a fixed box) and goes back to its place on exit. Full screen asks the browser to show
  // the same card, so the overlay tags and controls stay available.
  const theaterBtn = h('button', { type: 'button', class: 'ws-ibtn ws-theater', 'aria-pressed': 'false', 'aria-label': t('ws.theater', lang), title: t('ws.theater', lang), onclick: () => setTheater(!theater) }, '▭');
  const fsBtn = h('button', { type: 'button', class: 'ws-ibtn ws-fs', 'aria-pressed': 'false', 'aria-label': t('ws.fullscreen', lang), title: t('ws.fullscreen', lang), onclick: () => toggleFullscreen() }, '⛶');
  const place = h('span', { class: 'ws-video-slot', hidden: true });
  const paintModeButtons = () => {
    const fs = document.fullscreenElement === sec;
    setAttr(theaterBtn, 'aria-pressed', theater ? 'true' : 'false');
    setAttr(fsBtn, 'aria-pressed', fs ? 'true' : 'false');
    const tl = t(theater ? 'ws.theater_exit' : 'ws.theater', lang);
    setAttr(theaterBtn, 'aria-label', tl);
    setAttr(theaterBtn, 'title', tl);
    const fl = t(fs ? 'ws.fullscreen_exit' : 'ws.fullscreen', lang);
    setAttr(fsBtn, 'aria-label', fl);
    setAttr(fsBtn, 'title', fl);
  };
  function setTheater(on) {
    on = !!on;
    if (on === theater || destroyed) return;
    theater = on;
    if (on) {
      if (sec.parentNode) sec.parentNode.insertBefore(place, sec);
      document.body.appendChild(sec);
      document.body.classList.add('ws-theater-open');
      sec.classList.add('theater');
    } else {
      sec.classList.remove('theater');
      document.body.classList.remove('ws-theater-open');
      if (place.parentNode) place.parentNode.replaceChild(sec, place);
    }
    paintModeButtons();
  }
  function toggleFullscreen() {
    try {
      if (document.fullscreenElement) {
        if (document.exitFullscreen) document.exitFullscreen();
      } else if (sec.requestFullscreen) {
        const r = sec.requestFullscreen();
        if (r && typeof r.catch === 'function') r.catch(() => {});
      }
    } catch (e) {
      // ignore: the browser refused full screen
    }
  }
  const onFsChange = () => {
    sec.classList.toggle('is-fs', document.fullscreenElement === sec);
    paintModeButtons();
  };
  const onKey = (ev) => {
    if (ev.key === 'Escape' && theater && !document.fullscreenElement) setTheater(false);
  };
  const docEvents = typeof document.addEventListener === 'function' && typeof document.removeEventListener === 'function';
  if (docEvents) {
    document.addEventListener('fullscreenchange', onFsChange);
    document.addEventListener('keydown', onKey);
  }

  // Mute and volume. Volume 0 counts as muted; unmuting from volume 0 restores half volume.
  const muteBtn = h('button', { type: 'button', class: 'ws-ibtn ws-mute', 'aria-pressed': 'false', onclick: () => setMuted(!(video.muted || video.volume === 0)) }, '🔊');
  const volRange = h('input', { type: 'range', class: 'ws-vol-range', min: '0', max: '1', step: '0.05', value: '1', 'aria-label': t('ws.volume_aria', lang) });
  volRange.addEventListener('input', () => setVolume(Number(volRange.value)));
  const vol = h('span', { class: 'ws-vol' }, [muteBtn, volRange]);

  const safeGet = (k) => { try { return store.get(k, null); } catch (e) { return null; } };
  const safeSet = (k, v) => { try { store.set(k, v); } catch (e) { /* storage unavailable: this session only */ } };
  function paintVolume() {
    const muted = video.muted || video.volume === 0;
    setText(muteBtn, muted ? '🔇' : '🔊');
    setAttr(muteBtn, 'aria-pressed', muted ? 'true' : 'false');
    const label = t(muted ? 'ws.unmute' : 'ws.mute', lang);
    setAttr(muteBtn, 'aria-label', label);
    setAttr(muteBtn, 'title', label + ' (M)');
    if (document.activeElement !== volRange) volRange.value = String(muted ? 0 : video.volume);
  }
  function setMuted(on) {
    video.muted = !!on;
    if (!on && video.volume === 0) video.volume = 0.5;
    safeSet(PREF_KEYS.muted, on ? '1' : '0');
    if (!on) safeSet(PREF_KEYS.volume, String(video.volume));
    paintVolume();
  }
  function setVolume(v) {
    const x = parseVolume(v);
    video.volume = x;
    video.muted = x === 0;
    safeSet(PREF_KEYS.volume, String(x));
    safeSet(PREF_KEYS.muted, x === 0 ? '1' : '0');
    paintVolume();
  }
  video.volume = parseVolume(safeGet(PREF_KEYS.volume));
  video.muted = safeGet(PREF_KEYS.muted) === '1';
  paintVolume();
  video.addEventListener('volumechange', paintVolume);
  sec.addEventListener('keydown', (ev) => {
    if (ev.key !== 'm' && ev.key !== 'M') return;
    // Ctrl / Alt / Meta + M belong to the browser and the OS, not to the mute toggle.
    if (ev.ctrlKey || ev.metaKey || ev.altKey) return;
    const tg = ev.target || {};
    if (tg.tagName === 'TEXTAREA' || tg.isContentEditable || (tg.tagName === 'INPUT' && !['range', 'button', 'checkbox'].includes(tg.type))) return;
    if (typeof ev.preventDefault === 'function') ev.preventDefault();
    setMuted(!(video.muted || video.volume === 0));
  });

  const ctlRow = h('div', { class: 'ws-ctl' }, [
    h('button', { type: 'button', class: 'ws-ibtn', 'aria-label': labels.prev || t('ws.prev_rally', lang), onclick: () => { if (onPrev) onPrev(); } }, '⏮'),
    playBtn,
    h('button', { type: 'button', class: 'ws-ibtn', 'aria-label': labels.next || t('ws.next_rally', lang), onclick: () => { if (onNext) onNext(); } }, '⏭'),
    time,
    seek,
    vol,
    theaterBtn,
    fsBtn,
  ]);
  sec.appendChild(stage);
  sec.appendChild(ctlRow);

  // ----- source and fallback -----
  let idx = 0;
  let gotStatus = false;
  const fail = (key) => {
    if (destroyed || idx >= files.length) return;
    idx += 1;
    playersVideo = false;
    if (idx < files.length) {
      video.src = files[idx];
      if (onSourceFallback) onSourceFallback(idx);
      return;
    }
    video.removeAttribute('src');
    video.hidden = true;
    msg.hidden = false;
    setText(msg, t(key, lang));
    for (const b of ctlRow.querySelectorAll('button,input')) b.disabled = true;
  };
  // MediaError codes: 2 network, 3 decode, 4 source not supported (a 404 also lands here). A file that
  // reached loadedmetadata exists, so a later failure is a playback problem, not a missing video.
  video.addEventListener('error', () => {
    const code = video.error && video.error.code;
    fail(code === 2 ? 'ws.video_network' : gotStatus || code === 3 ? 'ws.video_unplayable' : 'ws.video_missing');
  });
  video.addEventListener('loadedmetadata', () => {
    gotStatus = true;
    // An undecodable MPEG-4 Part 2 track loads with the right duration but no picture.
    if (!video.videoWidth && !video.videoHeight) {
      fail('ws.video_unplayable');
      return;
    }
    setAttr(seek, 'max', isNum(video.duration) ? video.duration.toFixed(1) : '0');
    if (pendingSeek !== undefined) {
      const s = pendingSeek;
      pendingSeek = undefined;
      seekTo(s, false);
    }
    paintTime();
  });
  video.addEventListener('timeupdate', paintTime);
  video.addEventListener('play', paintPlay);
  video.addEventListener('pause', paintPlay);

  function paintTime() {
    const cur = isNum(video.currentTime) ? video.currentTime : 0;
    setText(time, `${clockText(cur)} / ${clockText(isNum(video.duration) ? video.duration : null)}`);
    if (document.activeElement !== seek) seek.value = String(cur.toFixed(1));
  }

  function paintPlay() {
    const playing = !video.paused;
    setText(playBtn, playing ? '❚❚' : '▶');
    setAttr(playBtn, 'aria-label', t(playing ? 'ws.pause' : 'ws.play', lang));
  }

  function togglePlay() {
    if (video.hidden) return;
    try {
      if (video.paused) {
        const p = video.play();
        if (p && typeof p.catch === 'function') p.catch(() => {});
      } else video.pause();
    } catch (e) {
      // ignore: playback is best effort
    }
  }

  function seekTo(sec_, fromSelection = true) {
    if (!isNum(sec_)) return;
    const target = Math.max(0, sec_ - (fromSelection ? 0.4 : 0));
    try {
      if (video.readyState >= 1) video.currentTime = target;
      else pendingSeek = target;
    } catch (e) {
      pendingSeek = target;
    }
    paintTime();
  }

  function destroy() {
    if (destroyed) return;
    if (docEvents) {
      document.removeEventListener('fullscreenchange', onFsChange);
      document.removeEventListener('keydown', onKey);
    }
    try {
      if (document.fullscreenElement === sec && document.exitFullscreen) document.exitFullscreen();
    } catch (e) {
      // ignore
    }
    if (theater) setTheater(false);
    destroyed = true;
    if (sec.parentNode === document.body) document.body.removeChild(sec);
    try {
      video.pause();
      video.removeAttribute('src');
      video.load(); // releases the media connection
    } catch (e) {
      // ignore
    }
  }

  if (files.length) video.src = files[0];

  return {
    el: sec,
    stage,
    tagsEl,
    sigEl,
    video,
    seekTo,
    togglePlay,
    setMuted,
    setVolume,
    get playersVideo() { return playersVideo; },
    destroy,
  };
}
