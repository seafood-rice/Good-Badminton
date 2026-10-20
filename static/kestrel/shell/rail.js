// Navigation rail: Overview, Matches, Players | Video library, Pipeline | Settings (language + theme popover).
// One marker glides between destinations; collapses to 68 px (persisted by main.js); a tab bar under 720 px.

import { t } from '../core/i18n.js';
import { formatHash } from '../core/router.js';
import { el, icon, ICONS, brandMark, trapTab, refocus } from './dom.js';
import { ACTIVE_STATUSES, RUNNING_STATUSES, QUEUED_STATUSES } from '../widgets/pipeline.js';

export const NAV_GROUPS = Object.freeze([
  { id: 'analyze', items: ['overview', 'matches', 'players'] },
  { id: 'manage', items: ['library', 'pipeline'] },
]);

/** The rail destination a route belongs to (library also owns the wizard and the legacy results). */
export function activeNav(route) {
  if (!route || !route.name) return null;
  switch (route.name) {
    case 'overview':
    case 'matches':
    case 'players':
    case 'pipeline':
    case 'library':
      return route.name;
    case 'new':
    case 'results':
      return 'library';
    default:
      return null;
  }
}

function jobList(jobs) {
  if (Array.isArray(jobs)) return jobs;
  if (jobs && Array.isArray(jobs.jobs)) return jobs.jobs;
  return [];
}

const countIn = (jobs, statuses) => jobList(jobs).filter((j) => j && statuses.includes(j.status)).length;

/** Jobs that are working right now (running or re-encoding in this server, or detached runs still writing progress). */
export function runningCount(jobs) {
  return countIn(jobs, RUNNING_STATUSES);
}

/** Jobs waiting to start (the server's 'pending'). */
export function queuedCount(jobs) {
  return countIn(jobs, QUEUED_STATUSES);
}

/** Jobs in flight (running + queued): drives the 2 s poll and the pipeline badge. */
export function activeCount(jobs) {
  return countIn(jobs, ACTIVE_STATUSES);
}

/** Hash for a rail destination; overview and matches carry the current scope (athlete, squad, range). */
function hrefFor(id, scope) {
  if (id === 'overview' || id === 'matches') return formatHash(id, {}, scope || {});
  return formatHash(id);
}

/**
 * opts: {onToggleCollapse(), onSetLang(lang), onSetTheme('night'|'day')}.
 * Returns {el, update({route, lang, theme, scope, jobs, collapsed}), closeSettings()}.
 */
export function createRail(opts) {
  const aside = el('aside', { class: 'rail' });
  let state = { route: null, lang: null, theme: 'night', scope: {}, jobs: [], collapsed: false };
  let refs = null;
  let pop = null;
  let placedOnce = false;

  function closeSettings(focusBack = true) {
    if (!pop) return;
    pop.root.remove();
    document.removeEventListener('pointerdown', pop.onDoc, true);
    refs.settings.setAttribute('aria-expanded', 'false');
    refs.settings.classList.remove('open');
    const back = refs.settings;
    pop = null;
    if (focusBack) refocus(back);
  }

  function openSettings() {
    if (pop) {
      closeSettings();
      return;
    }
    const L = state.lang;
    const seg = (label, options) => el('div', { class: 'rail-pop-row' },
      el('div', { class: 'k-label', text: label }),
      el('div', { class: 'seg2', role: 'group', 'aria-label': label }, ...options.map((o) => el('button', {
        type: 'button', 'aria-pressed': o.on ? 'true' : 'false', text: o.label,
        onclick: () => {
          closeSettings();
          o.pick();
        },
      }))));
    const root = el('div', {
      class: 'rail-pop', role: 'dialog', 'aria-label': t('settings.title', L),
      onkeydown: (e) => {
        if (e.key === 'Escape') {
          e.preventDefault();
          e.stopPropagation();
          closeSettings();
        } else {
          trapTab(root, e);
        }
      },
    },
    el('div', { class: 'rail-pop-title', text: t('settings.title', L) }),
    seg(t('settings.language', L), [
      { label: t('lang.zh', L), on: L === 'zh', pick: () => opts.onSetLang('zh') },
      { label: t('lang.en', L), on: L === 'en', pick: () => opts.onSetLang('en') },
    ]),
    seg(t('settings.theme', L), [
      { label: t('theme.night', L), on: state.theme !== 'day', pick: () => opts.onSetTheme('night') },
      { label: t('theme.day', L), on: state.theme === 'day', pick: () => opts.onSetTheme('day') },
    ]));
    const narrow = typeof matchMedia === 'function' && matchMedia('(max-width: 719px)').matches;
    if (!narrow) {
      const r = aside.getBoundingClientRect();
      root.style.left = Math.round(r.right + 8) + 'px';
    }
    const onDoc = (e) => {
      if (!root.contains(e.target) && !refs.settings.contains(e.target)) closeSettings(false);
    };
    document.addEventListener('pointerdown', onDoc, true);
    document.body.appendChild(root);
    refs.settings.setAttribute('aria-expanded', 'true');
    refs.settings.classList.add('open');
    pop = { root, onDoc };
    const first = root.querySelector('[aria-pressed="true"]') || root.querySelector('button');
    if (first) first.focus();
  }

  function navItem(id) {
    const L = state.lang;
    const label = t('nav.' + id, L);
    const a = el('a', { class: 'rail-i', href: hrefFor(id, state.scope), title: label, 'data-nav': id },
      icon(ICONS[id], 20, 1.7), el('span', { class: 'label', text: label }));
    if (id === 'pipeline') {
      a.appendChild(el('span', { class: 'rail-badge', 'aria-hidden': 'true', hidden: true },
        el('span', { class: 'dot' }), el('span', { class: 'n' })));
    }
    return a;
  }

  function build() {
    closeSettings(false);
    const L = state.lang;
    const collapseBtn = el('button', {
      type: 'button', class: 'rail-btn collapse', onclick: () => opts.onToggleCollapse(), 'aria-keyshortcuts': '[',
    });
    const brand = el('a', { class: 'rail-brand', href: hrefFor('overview', state.scope), 'aria-label': t('nav.home', L) },
      el('span', { class: 'rail-mark', 'aria-hidden': 'true' }, brandMark()),
      el('span', { class: 'txt', 'aria-hidden': 'true' },
        el('span', { class: 'name', text: t('brand', L) }),
        el('span', { class: 'sub', text: t('brand.tagline', L).toUpperCase() })));
    const marker = el('div', { class: 'rail-marker off', 'aria-hidden': 'true' });
    const nav = el('nav', { class: 'rail-nav', 'aria-label': t('nav.primary', L) }, marker);
    const links = {};
    for (const g of NAV_GROUPS) {
      nav.appendChild(el('div', { class: 'rail-group', 'aria-hidden': 'true', text: t('section.' + g.id, L).toUpperCase() }));
      for (const id of g.items) {
        links[id] = navItem(id);
        nav.appendChild(links[id]);
      }
    }
    const settings = el('button', {
      type: 'button', class: 'rail-i', 'aria-haspopup': 'dialog', 'aria-expanded': 'false', title: t('nav.settings', L),
      onclick: () => openSettings(),
    }, icon(ICONS.settings, 20, 1.7), el('span', { class: 'label', text: t('nav.settings', L) }));
    const foot = el('div', { class: 'rail-foot' }, settings);
    aside.replaceChildren(el('div', { class: 'rail-brandrow' }, brand, collapseBtn), nav, foot);
    refs = { collapseBtn, brand, marker, nav, links, settings, collapsedShown: null };
    placedOnce = false;
  }

  function placeMarker() {
    if (!refs) return;
    const id = activeNav(state.route);
    const link = id ? refs.links[id] : null;
    if (!link || !link.offsetParent) {
      refs.marker.classList.add('off');
      return;
    }
    if (!placedOnce) refs.marker.style.transition = 'none';
    refs.marker.style.transform = 'translateY(' + link.offsetTop + 'px)';
    refs.marker.classList.remove('off');
    if (!placedOnce) {
      void refs.marker.offsetHeight; // commit the jump before re-enabling the glide
      refs.marker.style.transition = '';
      placedOnce = true;
    }
  }

  function paint() {
    const L = state.lang;
    const id = activeNav(state.route);
    for (const [key, a] of Object.entries(refs.links)) {
      a.setAttribute('href', hrefFor(key, state.scope));
      if (key === id) a.setAttribute('aria-current', 'page');
      else a.removeAttribute('aria-current');
    }
    refs.brand.setAttribute('href', hrefFor('overview', state.scope));

    const running = runningCount(state.jobs);
    const queued = queuedCount(state.jobs);
    const pipe = refs.links.pipeline;
    const badge = pipe.querySelector('.rail-badge');
    const active = running + queued;
    badge.hidden = active === 0;
    badge.querySelector('.n').textContent = active ? String(active) : '';
    const pipeLabel = t('nav.pipeline', L);
    if (running || queued) pipe.setAttribute('aria-label', pipeLabel + ', ' + t('pipeline.sub', L, { running, queued }));
    else pipe.removeAttribute('aria-label');

    if (refs.collapsedShown !== state.collapsed) {
      aside.classList.toggle('collapsed', !!state.collapsed);
      refs.collapseBtn.replaceChildren(icon(state.collapsed ? ICONS.expand : ICONS.collapse, 18, 1.8));
      refs.collapseBtn.setAttribute('aria-label', t(state.collapsed ? 'nav.expand' : 'nav.collapse', L));
      refs.collapseBtn.setAttribute('aria-expanded', state.collapsed ? 'false' : 'true');
      refs.collapsedShown = state.collapsed;
    }
    // Layout must be current before measuring the active item.
    if (typeof requestAnimationFrame === 'function') requestAnimationFrame(placeMarker);
    else placeMarker();
  }

  function update(next) {
    const prevLang = state.lang;
    state = { ...state, ...next };
    if (!refs || state.lang !== prevLang) build();
    paint();
  }

  if (typeof window !== 'undefined' && typeof window.addEventListener === 'function') {
    window.addEventListener('resize', () => placeMarker());
  }

  return { el: aside, update, closeSettings: () => closeSettings(false), placeMarker };
}
