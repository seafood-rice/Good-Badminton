// Legacy screens (spec 6.6): #/library, #/new and #/results/<stem>?mode= render the former kestrel.js
// (now legacy/kestrel-legacy.js) into the shell's page. The legacy module is imported lazily on first use.

import { formatHash } from '../core/router.js';
import { t } from '../core/i18n.js';
import { api } from '../core/api.js';
import { prefs, PREF_KEYS, normLang, legacyTheme } from '../core/prefs.js';

const MODES = ['match', 'posture'];
// A stem is interpolated into legacy HTML strings; a hash-supplied stem must not carry markup.
const SAFE_STEM = /^[^<>"`\u0000-\u001f]+$/;

// The legacy module keeps one render target and one navigation callback, so only the newest mount owns them.
let owner = 0;

let legacyPromise = null;
function loadLegacy() {
  if (!legacyPromise) {
    legacyPromise = import('../legacy/kestrel-legacy.js').catch((e) => {
      legacyPromise = null;
      throw e;
    });
  }
  return legacyPromise;
}

function videoModes(v) {
  const modes = [];
  if (v && v.has_match) modes.push('match');
  if (v && v.has_posture) modes.push('posture');
  return modes;
}

function notFound(host, lang, stem, onRetry) {
  host.replaceChildren();
  const p = document.createElement('p');
  p.className = 'muted';
  p.textContent = t('error.load.title', lang) + ': ' + stem;
  const a = document.createElement('a');
  a.className = 'btn-ghost';
  a.href = formatHash('library');
  a.textContent = '← ' + t('nav.library', lang);
  host.append(p, a);
  if (typeof onRetry === 'function') {
    const b = document.createElement('button');
    b.type = 'button';
    b.className = 'btn-ghost';
    b.textContent = t('btn.retry', lang);
    b.addEventListener('click', onRetry);
    host.append(b);
  }
}

/**
 * mount(container, ctx) where ctx = {route, router, lang, theme, api?, loadLegacy?, ...}; a bare route object is
 * accepted too. `ctx.api` and `ctx.loadLegacy` (() => Promise<legacy module>) are injectable for tests.
 * Returns {destroy(), update(ctx)}.
 */
export async function mount(container, ctxOrRoute) {
  const ctx = ctxOrRoute && ctxOrRoute.route ? ctxOrRoute : { route: ctxOrRoute };
  let route = ctx.route && ctx.route.name ? ctx.route : { name: 'library', params: {}, query: {} };
  const lang = normLang(ctx.lang || prefs.get(PREF_KEYS.lang, 'zh'));
  const theme = legacyTheme(ctx.theme || prefs.get(PREF_KEYS.theme, 'night'));
  const L = await (typeof ctx.loadLegacy === 'function' ? ctx.loadLegacy() : loadLegacy());
  // The shell removed this page while the module loaded: a newer mount owns the legacy screens now.
  if (container.isConnected === false) return { destroy() {}, update() {} };
  const token = ++owner;
  const owns = () => owner === token;
  const videosApi = ctx.api && typeof ctx.api.videos === 'function' ? ctx.api : api;

  let alive = true;
  container.classList.add('k-legacy');
  const host = document.createElement('div');
  host.id = 'main';
  host.className = 'legacy-host';
  container.appendChild(host);

  // Language and theme are shared through localStorage; bring the legacy state in line before rendering.
  if (L.state.theme !== theme) L.setTheme(theme);
  if (L.state.lang !== lang) L.setLang(lang);

  L.onNavigate((r) => {
    if (!alive || !owns() || !r) return;
    const hash = formatHash(r.name, r.params || {}, r.query || {});
    if (ctx.router && typeof ctx.router.navigate === 'function') ctx.router.navigate(hash);
    else location.hash = hash;
  });

  async function render(next) {
    route = next;
    if (route.name === 'new') {
      L.mountNew(host);
      return;
    }
    if (route.name !== 'results') {
      L.mountLibrary(host);
      return;
    }
    const stem = route.params && route.params.stem;
    const asked = MODES.includes(route.query && route.query.mode) ? route.query.mode : null;
    const known = L.state.resultsVideo === stem && Array.isArray(L.state.resultsModes) && L.state.resultsModes.length;
    if (known && SAFE_STEM.test(stem)) {
      // Opened from inside the legacy screens: the stem came from the server's list.
      L.mountResults(host, stem, asked || L.state.resultsModes[0]);
      return;
    }
    // Deep link: confirm the stem against the library and learn which result modes it has.
    host.textContent = t('state.loading', lang);
    let modes = null;
    try {
      const vids = await videosApi.videos({ timeout: 15000 });
      const v = (Array.isArray(vids) ? vids : []).find((x) => x && x.name === stem);
      modes = v ? videoModes(v) : [];
    } catch (e) {
      modes = null; // library unreachable: the stem cannot be verified
    }
    if (!alive || !owns() || !host.isConnected || route.params.stem !== stem) return;
    if (modes === null) {
      // Never mount an unverified hash stem: it reaches /api/output paths and legacy HTML.
      notFound(host, lang, stem || '', () => { if (alive && owns()) render(route); });
      return;
    }
    if (!SAFE_STEM.test(stem || '') || !modes.length) {
      notFound(host, lang, stem || '');
      return;
    }
    const mode = asked && modes.includes(asked) ? asked : modes[0];
    L.mountResults(host, stem, mode, modes);
  }

  await render(route);

  return {
    update(next) {
      const r = next && next.route ? next.route : next;
      if (!alive || !owns() || !r) return;
      const sameScreen = r.name === route.name
        && (r.params && r.params.stem) === (route.params && route.params.stem)
        && (r.query && r.query.mode) === (route.query && route.query.mode);
      if (!sameScreen) render(r);
    },
    destroy() {
      if (!alive) return;
      alive = false;
      // A stale instance must not clear the target or callback of a newer mount.
      if (owns()) {
        L.onNavigate(null);
        L.unmount();
      }
      host.remove();
      container.classList.remove('k-legacy');
    },
  };
}
