// Kestrel entry: preferences, the shell (rail, top bar, ground, palette, peek), the hash router and lazy views.
// Views are loaded with dynamic import(); a missing or failing view renders a polite panel, never a blank page.
// A view module exports mount(container, ctx) -> {destroy(), update?(ctx) | setQuery?(query)} (mount may be
// async) and renders its own `.page` root; ctx is {route, query, params, scope, store, router, api, lang, theme,
// themeAttr, reduced (a live boolean getter), prefersReduced() (the same check as a function), transitionTo,
// openPeek, closePeek, openPalette, refreshAthletes, refreshJobs, toast, onOpenMatch}. router.navigate/replace take a hash or (name, params, query). The store also carries flat
// athlete / squad / range keys ('' when unset) next to `scope`.

import { parseHash, formatHash } from './core/router.js';
import { createStore } from './core/store.js';
import { prefs, PREF_KEYS, normLang, normTheme, legacyTheme } from './core/prefs.js';
import { api } from './core/api.js';
import { t } from './core/i18n.js';
import { el, prefersReduced, isTyping } from './shell/dom.js';
import { createRail, runningCount } from './shell/rail.js';
import { createTopbar, scopeFallback } from './shell/topbar.js';
import { createPalette } from './shell/palette.js';
import { createPeek } from './shell/peek.js';
import { navigateWithTransform } from './shell/transition.js';
import { createGround } from './shell/ground.js';

/** Route table: one lazy loader per view. #/matches/<stem> is the match workspace. */
export const VIEW_LOADERS = Object.freeze({
  overview: () => import('./views/overview.js'),
  matches: () => import('./views/matches.js'),
  workspace: () => import('./views/workspace.js'),
  players: () => import('./views/players.js'),
  pipeline: () => import('./views/pipeline.js'),
  library: () => import('./views/legacy.js'),
  new: () => import('./views/legacy.js'),
  results: () => import('./views/legacy.js'),
});

/** Which view renders a parsed route. */
export function viewKey(route) {
  if (!route || !route.name) return 'overview';
  if (route.name === 'matches' && route.params && route.params.stem) return 'workspace';
  return Object.prototype.hasOwnProperty.call(VIEW_LOADERS, route.name) ? route.name : 'overview';
}

const SCOPE_KEYS = ['athlete', 'squad', 'range'];
const VIEW_NAV = {
  overview: 'nav.overview', matches: 'nav.matches', workspace: 'nav.matches', players: 'nav.players',
  pipeline: 'nav.pipeline', library: 'nav.library', new: 'nav.library', results: 'nav.library',
};

/** Routes whose query carries the scope: the overview and the matches list. */
function isScopeRoute(route) {
  return !!route && (route.name === 'overview' || (route.name === 'matches' && !(route.params && route.params.stem)));
}

function scopeOf(query) {
  const out = {};
  for (const k of SCOPE_KEYS) if (query && query[k]) out[k] = query[k];
  return out;
}

/** Store patch for a scope: nested `scope` plus flat athlete / squad / range ('' when unset) for views. */
function scopePatch(scope) {
  return { scope, athlete: scope.athlete || '', squad: scope.squad || '', range: scope.range || '' };
}

function sameQuery(a, b) {
  const ka = Object.keys(a || {});
  const kb = Object.keys(b || {});
  return ka.length === kb.length && ka.every((k) => (b || {})[k] === a[k]);
}

function safeDestroy(instance) {
  try {
    if (instance && typeof instance.destroy === 'function') instance.destroy();
  } catch (e) {
    console.error('Kestrel: view destroy failed', e);
  }
}

/** Mount the shell into `root` (#app) and start the router. Returns {store, router, dispatch}. */
export function start(root) {
  if (!root) return null;

  // ---- preferences (shared with the legacy screens through localStorage) ----
  const applyLang = (L) => { document.documentElement.lang = L; };
  const applyTheme = (th) => { document.documentElement.setAttribute('data-theme', legacyTheme(th)); };
  const lang0 = normLang(prefs.get(PREF_KEYS.lang, 'zh'));
  const theme0 = normTheme(prefs.get(PREF_KEYS.theme, 'dark'));
  applyLang(lang0);
  applyTheme(theme0);
  try {
    if ('scrollRestoration' in history) history.scrollRestoration = 'manual';
  } catch (e) {
    // ignore
  }

  const route0 = parseHash(location.hash);
  const store = createStore({
    lang: lang0,
    theme: theme0,
    route: route0,
    query: route0.query,
    ...scopePatch(isScopeRoute(route0) ? scopeOf(route0.query) : {}),
    athletes: { athletes: [], squads: [] },
    athletesStatus: 'idle',
    videos: null,
    jobs: [],
    jobsStatus: 'idle',
    collapsed: prefs.get(PREF_KEYS.rail) === 'collapsed',
  });

  // ---- router ----
  const normalize = (h) => {
    const r = parseHash(h);
    return formatHash(r.name, r.params, r.query);
  };
  // navigate / replace accept a hash ("#/matches/x") or (name, params, query) like formatHash.
  const toHash = (a, params, query) => (typeof a === 'string' && a.startsWith('#') ? a : formatHash(a, params, query));
  const router = {
    /** Push a hash; the same hash again re-renders the current view. */
    navigate(a, params, query) {
      const hash = toHash(a, params, query);
      if (location.hash && normalize(location.hash) === normalize(hash)) {
        dispatch({ force: true });
        return;
      }
      location.hash = hash;
    },
    go(hash) {
      router.navigate(hash);
    },
    /** Replace the current history entry (no new Back step) and re-render. */
    replace(a, params, query) {
      const hash = toHash(a, params, query);
      try {
        history.replaceState(history.state, '', hash);
      } catch (e) {
        location.replace(hash);
        return;
      }
      dispatch();
    },
    current: () => parseHash(location.hash),
    href: (name, params, query) => formatHash(name, params, query),
    parse: parseHash,
    format: formatHash,
  };

  // ---- shell parts ----
  const peek = createPeek({ getLang: () => store.get().lang });
  const palette = createPalette({
    getLang: () => store.get().lang,
    getSources: () => ({ athletes: store.get().athletes, videos: store.get().videos }),
    loadSources: () => Promise.all([loadAthletes(), loadVideos()]),
    navigate: (hash) => router.navigate(hash),
  });
  const rail = createRail({
    onToggleCollapse: () => toggleCollapse(),
    onSetLang: (L) => setLang(L),
    onSetTheme: (th) => setTheme(th),
  });
  const topbar = createTopbar({
    onScope: (scope) => onScope(scope),
    onPalette: () => palette.open(),
    onToggleTheme: () => setTheme(store.get().theme === 'day' ? 'night' : 'day'),
    onNotifications: () => router.navigate(formatHash('pipeline')),
  });
  const ground = createGround();
  const viewRoot = el('main', { id: 'k-view', class: 'k-view', tabindex: '-1' });
  const skip = el('button', { type: 'button', class: 'shell-skip', onclick: () => viewRoot.focus() });
  root.replaceChildren(el('div', { class: 'k-app' }, skip, rail.el, el('div', { class: 'k-main' }, ground.el, topbar.el, viewRoot)));

  function paintShell() {
    const s = store.get();
    skip.textContent = t('a11y.skip', s.lang);
    rail.update({ route: s.route, lang: s.lang, theme: s.theme, scope: s.scope, jobs: s.jobs, collapsed: s.collapsed });
    topbar.update({ lang: s.lang, theme: s.theme, scope: s.scope, athletes: s.athletes, running: runningCount(s.jobs) });
    ground.update(s.route);
  }

  store.subscribe((s, prev) => {
    if (s.jobs !== prev.jobs || s.athletes !== prev.athletes || s.theme !== prev.theme || s.collapsed !== prev.collapsed) {
      paintShell();
    }
  });

  // ---- preferences actions ----
  function setLang(next) {
    const L = normLang(next);
    if (L === store.get().lang) return;
    prefs.set(PREF_KEYS.lang, L);
    applyLang(L);
    store.set({ lang: L });
    palette.close();
    paintShell();
    dispatch({ force: true, keepScroll: true });
  }

  function setTheme(next) {
    const th = normTheme(next);
    prefs.set(PREF_KEYS.theme, legacyTheme(th));
    applyTheme(th);
    store.set({ theme: th });
  }

  function toggleCollapse() {
    const collapsed = !store.get().collapsed;
    prefs.set(PREF_KEYS.rail, collapsed ? 'collapsed' : 'expanded');
    store.set({ collapsed });
  }

  // ---- toast (spec 7: scope fallback) ----
  let toastTimer = null;
  function toast(message) {
    const old = document.querySelector('.shell-toast');
    if (old) old.remove();
    const node = el('div', { class: 'toast shell-toast', role: 'status', text: message });
    document.body.appendChild(node);
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => node.remove(), 6000);
  }

  // ---- data the shell needs: athletes (pickers, palette), videos (palette), jobs (badges) ----
  let athletesReq = null;
  function loadAthletes() {
    if (athletesReq) return athletesReq;
    athletesReq = api.athletes({ timeout: 15000 })
      .then((data) => {
        const norm = {
          athletes: data && Array.isArray(data.athletes) ? data.athletes : [],
          squads: data && Array.isArray(data.squads) ? data.squads : [],
        };
        store.set({ athletes: norm, athletesStatus: 'ok' });
        checkScope(store.get().route);
        return norm;
      })
      .catch(() => {
        // Keep the last good list; the pickers fall back to "all".
        store.set({ athletesStatus: 'error' });
        return store.get().athletes;
      })
      .finally(() => { athletesReq = null; });
    return athletesReq;
  }

  function loadVideos() {
    return api.videos({ timeout: 15000 })
      .then((vids) => {
        store.set({ videos: Array.isArray(vids) ? vids : [] });
        return store.get().videos;
      })
      .catch(() => store.get().videos);
  }

  let jobTimer = null;
  let jobsInFlight = false;
  function pollJobs() {
    clearTimeout(jobTimer);
    jobTimer = null;
    if (jobsInFlight) return Promise.resolve(store.get().jobs);
    if (typeof document !== 'undefined' && document.hidden) {
      jobTimer = setTimeout(pollJobs, 15000);
      return Promise.resolve(store.get().jobs);
    }
    jobsInFlight = true;
    return api.jobs({ timeout: 10000 })
      .then((data) => {
        const jobs = Array.isArray(data) ? data : data && Array.isArray(data.jobs) ? data.jobs : [];
        store.set({ jobs, jobsStatus: 'ok' });
      })
      .catch(() => {
        store.set({ jobsStatus: 'error' });
      })
      .then(() => {
        jobsInFlight = false;
        // Spec 6.4: every 2 s while a job runs, 15 s otherwise.
        jobTimer = setTimeout(pollJobs, runningCount(store.get().jobs) > 0 ? 2000 : 15000);
        return store.get().jobs;
      });
  }
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden) pollJobs();
  });

  // ---- scope ----
  function fallbackName(query) {
    const s = store.get();
    const squad = query.squad ? s.athletes.squads.find((x) => x.id === query.squad) : null;
    return squad ? String(squad.name || '') : t('scope.library', s.lang);
  }

  /** Spec 7: a deleted athlete / squad in scope falls back with a toast. True when the hash was replaced. */
  function checkScope(route) {
    const s = store.get();
    if (s.athletesStatus !== 'ok') return false;
    const fb = scopeFallback(isScopeRoute(route) ? route.query : s.scope, s.athletes);
    if (!fb) return false;
    toast(t('toast.scope_fallback', s.lang, { scope: fallbackName(fb.query) }));
    if (isScopeRoute(route)) {
      router.replace(formatHash(route.name, route.params, fb.query));
      return true;
    }
    store.set(scopePatch(scopeOf(fb.query)));
    paintShell();
    return false;
  }

  function onScope(nextScope) {
    const route = store.get().route;
    const scope = scopeOf(nextScope);
    if (isScopeRoute(route)) {
      const rest = {};
      for (const [k, v] of Object.entries(route.query || {})) if (!SCOPE_KEYS.includes(k)) rest[k] = v;
      router.navigate(formatHash(route.name, route.params, { ...scope, ...rest }));
    } else {
      // Pages without a scope keep it for the next visit to the overview / matches.
      store.set(scopePatch(scope));
      paintShell();
    }
  }

  // ---- views ----
  function transitionTo(anchorEl, hash) {
    return navigateWithTransform(anchorEl, hash, { reduced: prefersReduced, navigate: (h) => router.navigate(h) });
  }

  function makeCtx(route) {
    const s = store.get();
    return {
      route,
      query: { ...route.query },
      params: { ...route.params },
      scope: { ...s.scope },
      store,
      router,
      api,
      lang: s.lang,
      theme: s.theme,
      themeAttr: legacyTheme(s.theme),
      // Live boolean (re-read on every access); prefersReduced() is the same check as a function.
      get reduced() { return prefersReduced(); },
      prefersReduced,
      transitionTo,
      openPeek: (content, opts) => peek.open(content, opts),
      closePeek: () => peek.close(),
      openPalette: () => palette.open(),
      refreshAthletes: loadAthletes,
      refreshJobs: pollJobs,
      toast,
      // Overview contract (Task 9, spec 6.5): a match row opens the match peek; its "Open match workspace" link
      // runs the container transform from the same row. If the peek cannot be built, go straight to the workspace.
      onOpenMatch: (stem, anchorEl) => {
        const active = document.activeElement;
        const anchor = anchorEl || (active && active !== document.body ? active : null);
        const fallback = () => transitionTo(anchor, formatHash('matches', { stem }));
        VIEW_LOADERS.matches()
          .then((mod) => {
            if (!mod || typeof mod.buildMatchPeek !== 'function') throw new Error('no match peek');
            // Closing the drawer aborts the peek's fetches.
            const ac = typeof AbortController === 'function' ? new AbortController() : null;
            const node = mod.buildMatchPeek({ stem }, { ...makeCtx(store.get().route), anchorEl: anchor, signal: ac ? ac.signal : null });
            peek.open(node, { label: t('peek.aria', store.get().lang), onClose: () => { if (ac) ac.abort(); } });
          })
          .catch((e) => {
            console.warn('Kestrel: match peek unavailable', e);
            fallback();
          });
      },
    };
  }

  function unavailable(host, key, failed) {
    const L = store.get().lang;
    host.replaceChildren(el('div', { class: 'page' }, el('section', { class: 'card shell-unavail', role: 'status' },
      el('div', { class: 'k-label', text: t(VIEW_NAV[key] || 'nav.overview', L) }),
      el('h1', { class: 'shell-unavail-title', text: t('view.unavailable.title', L) }),
      el('p', { class: 'card-sub', text: t(failed ? 'view.failed.body' : 'view.unavailable.body', L) }),
      el('div', { class: 'shell-actions' },
        el('button', { type: 'button', class: 'shell-btn primary', text: t('btn.retry', L), onclick: () => dispatch({ force: true }) }),
        el('a', { class: 'shell-btn', href: formatHash('overview', {}, store.get().scope), text: t('nav.overview', L) }),
        el('a', { class: 'shell-btn', href: formatHash('library'), text: t('nav.library', L) })))));
  }

  function restoreScroll(y) {
    if (typeof y !== 'number') return;
    const go = () => { if (Math.abs(window.scrollY - y) > 2) window.scrollTo(0, y); };
    requestAnimationFrame(go);
    setTimeout(go, 400); // views fill in after their data arrives
  }

  let current = null;
  let gen = 0;

  function teardown() {
    if (!current) return;
    const c = current;
    current = null;
    safeDestroy(c.instance);
    c.page.remove();
  }

  async function mountView(route, key, restoreY) {
    const myGen = ++gen;
    teardown();
    const L = store.get().lang;
    // Views render their own `.page` root, so the host is a plain wrapper (a second .page would double the padding).
    const page = el('div', { class: 'view-host page-enter', 'data-view': key },
      el('div', { class: 'page' }, el('p', { class: 'shell-loading', role: 'status', text: t('state.loading', L) })));
    viewRoot.appendChild(page);
    current = { key, stem: (route.params && route.params.stem) || null, route, page, instance: null };
    if (typeof restoreY !== 'number') window.scrollTo(0, 0);

    let mod;
    try {
      mod = await VIEW_LOADERS[key]();
    } catch (e) {
      if (myGen !== gen) return;
      console.warn(`Kestrel: the "${key}" view is not available yet`, e);
      unavailable(page, key, false);
      restoreScroll(restoreY);
      return;
    }
    if (myGen !== gen) return;
    if (!mod || typeof mod.mount !== 'function') {
      unavailable(page, key, false);
      restoreScroll(restoreY);
      return;
    }
    page.replaceChildren();
    try {
      const instance = await mod.mount(page, makeCtx(route));
      if (myGen !== gen) {
        safeDestroy(instance);
        return;
      }
      current.instance = instance && typeof instance === 'object' ? instance : null;
    } catch (e) {
      if (myGen !== gen) return;
      console.error(`Kestrel: the "${key}" view failed to mount`, e);
      unavailable(page, key, true);
    }
    restoreScroll(restoreY);
  }

  // History entries get an id so Back / Forward can restore the scroll position they left.
  const scrollByEntry = new Map();
  let entrySeq = 0;
  let currentEntry = null;
  function entry() {
    const st = history.state;
    if (st && typeof st === 'object' && typeof st.kEntry === 'number') return { id: st.kEntry, isNew: false };
    const id = Date.now() * 1000 + (entrySeq++ % 1000);
    try {
      history.replaceState({ ...(st && typeof st === 'object' ? st : {}), kEntry: id }, '');
    } catch (e) {
      // ignore: scroll restoration is best effort
    }
    return { id, isNew: true };
  }

  function dispatch(opts = {}) {
    const route = parseHash(location.hash);
    const { id, isNew } = entry();
    let restoreY;
    if (opts.keepScroll) restoreY = window.scrollY;
    else if (!isNew && !opts.force && scrollByEntry.has(id)) restoreY = scrollByEntry.get(id);
    currentEntry = id;

    const patch = { route, query: route.query };
    if (isScopeRoute(route)) Object.assign(patch, scopePatch(scopeOf(route.query)));
    store.set(patch);
    peek.close();
    palette.close();
    topbar.closeMenu();
    rail.closeSettings();
    paintShell();
    if (checkScope(route)) return; // replaced the hash; that dispatch renders

    const key = viewKey(route);
    const stem = (route.params && route.params.stem) || null;
    if (!opts.force && current && current.key === key && current.stem === stem) {
      // Same view, new query: views that can follow it in place do so (update(ctx) or setQuery(query)).
      const inst = current.instance;
      if (inst && (typeof inst.update === 'function' || typeof inst.setQuery === 'function')) {
        current.route = route;
        try {
          if (typeof inst.update === 'function') inst.update(makeCtx(route));
          else inst.setQuery({ ...route.query });
        } catch (e) {
          console.error(`Kestrel: the "${key}" view failed to update`, e);
        }
        return;
      }
      if (sameQuery(current.route.query, route.query)) {
        current.route = route;
        return;
      }
    }
    mountView(route, key, restoreY);
  }

  // ---- global listeners ----
  window.addEventListener('hashchange', () => {
    if (currentEntry !== null) scrollByEntry.set(currentEntry, window.scrollY);
    dispatch();
  });

  document.addEventListener('keydown', (e) => {
    if ((e.ctrlKey || e.metaKey) && !e.altKey && !e.shiftKey && (e.key === 'k' || e.key === 'K')) {
      e.preventDefault();
      palette.toggle();
      return;
    }
    if (e.key === '[' && !e.ctrlKey && !e.metaKey && !e.altKey && !isTyping(e.target)
        && !palette.isOpen() && !peek.isOpen() && !document.querySelector('.modal-overlay, .pop')) {
      toggleCollapse();
    }
  });

  // <a data-xf href="#/..."> anywhere in a view runs the container transform.
  document.addEventListener('click', (e) => {
    if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    const a = e.target && e.target.closest ? e.target.closest('a[data-xf]') : null;
    if (!a) return;
    const href = a.getAttribute('href') || '';
    if (!href.startsWith('#/')) return;
    e.preventDefault();
    transitionTo(a, href);
  });

  paintShell();
  dispatch();
  loadAthletes();
  pollJobs();
  return { store, router, dispatch };
}

if (typeof window !== 'undefined' && typeof document !== 'undefined') {
  const boot = () => start(document.getElementById('app'));
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot, { once: true });
  else boot();
}
