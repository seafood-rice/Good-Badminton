// Top bar: squad / athlete scope pickers, palette button, 7D / 30D / Season slider, theme and notifications.
// Scope lives in the hash query (athlete, squad, range); the pure helpers below compute the next query.

import { t } from '../core/i18n.js';
import { RANGES } from '../core/scope.js';
import { el, icon, ICONS, refocus } from './dom.js';

export const TOPBAR_RANGES = Object.freeze(['7d', '30d', 'season']);
export const DEFAULT_RANGE = '30d';
const RANGE_W = 58;

function lists(data) {
  return {
    athletes: data && Array.isArray(data.athletes) ? data.athletes.filter((a) => a && a.id) : [],
    squads: data && Array.isArray(data.squads) ? data.squads.filter((s) => s && s.id) : [],
  };
}

function clean(query) {
  const out = {};
  for (const [k, v] of Object.entries(query || {})) {
    if (v !== undefined && v !== null && v !== '') out[k] = v;
  }
  return out;
}

/** Select a squad (null = all squads). An athlete outside the new squad is dropped. */
export function pickSquad(query, squadId, data) {
  const next = clean({ ...query, squad: squadId || undefined });
  if (squadId && next.athlete) {
    const a = lists(data).athletes.find((x) => x.id === next.athlete);
    if (!a || a.squad_id !== squadId) delete next.athlete;
  }
  return next;
}

/** Select an athlete (null = all athletes). The squad follows the athlete's own squad. */
export function pickAthlete(query, athleteId, data) {
  const next = clean({ ...query, athlete: athleteId || undefined });
  if (athleteId && next.squad) {
    const a = lists(data).athletes.find((x) => x.id === athleteId);
    if (a && a.squad_id) next.squad = a.squad_id;
    else delete next.squad;
  }
  return next;
}

/** Set the range when it is a known one; anything else leaves the query unchanged. */
export function withRange(query, range) {
  return RANGES.includes(range) ? clean({ ...query, range }) : clean({ ...query });
}

/** The range the slider shows: the query's range when valid, else the default (30D). */
export function rangeOf(query) {
  const r = query && query.range;
  return RANGES.includes(r) ? r : DEFAULT_RANGE;
}

/** Athletes of a squad, in the server's (name) order; a null squad means everyone. */
export function athletesInSquad(data, squadId) {
  const { athletes } = lists(data);
  return squadId ? athletes.filter((a) => a.squad_id === squadId) : athletes;
}

/** "J. Tanaka" -> "JT"; one word -> its first two characters; empty -> "?". */
export function initials(name) {
  const s = typeof name === 'string' ? name.trim() : '';
  if (!s) return '?';
  const parts = s.split(/[\s.·]+/).filter(Boolean);
  if (parts.length >= 2) {
    return (Array.from(parts[0])[0] + Array.from(parts[parts.length - 1])[0]).toUpperCase();
  }
  return Array.from(parts[0] || s).slice(0, 2).join('').toUpperCase();
}

/**
 * Spec 7: a selected athlete or squad that no longer exists falls back. Returns null when the scope is
 * valid, else {query (without the dropped keys), dropped: ['athlete'|'squad', ...]}. Only call it with
 * athlete data that loaded successfully.
 */
export function scopeFallback(query, data) {
  const { athletes, squads } = lists(data);
  const dropped = [];
  const next = clean(query);
  if (next.athlete && !athletes.some((a) => a.id === next.athlete)) {
    delete next.athlete;
    dropped.push('athlete');
  }
  if (next.squad && !squads.some((s) => s.id === next.squad)) {
    delete next.squad;
    dropped.push('squad');
  }
  return dropped.length ? { query: next, dropped } : null;
}

/** Name of the squad / athlete in scope, or null. */
function nameOf(list, id) {
  const hit = id ? list.find((x) => x.id === id) : null;
  return hit ? String(hit.name || '') : null;
}

/**
 * opts: {onScope(nextScopeQuery), onPalette(), onToggleTheme(), onNotifications()}.
 * Returns {el, update({lang, theme, scope, athletes, running})}.
 */
export function createTopbar(opts) {
  const header = el('header', { class: 'topbar' });
  let state = { lang: null, theme: 'night', scope: {}, athletes: null, running: 0 };
  let refs = null;
  let menu = null;

  function closeMenu(focusBack = true) {
    if (!menu) return;
    const { pop, button, onDoc } = menu;
    pop.remove();
    button.setAttribute('aria-expanded', 'false');
    document.removeEventListener('pointerdown', onDoc, true);
    menu = null;
    if (focusBack) refocus(button);
  }

  function openMenu(wrap, button, options) {
    if (menu && menu.button === button) {
      closeMenu();
      return;
    }
    closeMenu(false);
    const pop = el('div', { class: 'tb-menu', role: 'menu', 'aria-label': button.getAttribute('aria-label') || '' });
    for (const o of options) {
      pop.appendChild(el('button', {
        type: 'button', role: 'menuitemradio', 'aria-checked': o.checked ? 'true' : 'false', tabindex: '-1',
        onclick: () => {
          closeMenu();
          o.pick();
        },
      }, el('span', { class: 'val', text: o.label }), o.sub ? el('span', { class: 'sub', text: o.sub }) : null));
    }
    pop.addEventListener('keydown', (e) => {
      const btns = [...pop.querySelectorAll('button')];
      const i = btns.indexOf(document.activeElement);
      if (e.key === 'Escape') {
        e.preventDefault();
        e.stopPropagation();
        closeMenu();
      } else if (e.key === 'ArrowDown') {
        e.preventDefault();
        btns[(i + 1) % btns.length].focus();
      } else if (e.key === 'ArrowUp') {
        e.preventDefault();
        btns[(i - 1 + btns.length) % btns.length].focus();
      } else if (e.key === 'Home') {
        e.preventDefault();
        btns[0].focus();
      } else if (e.key === 'End') {
        e.preventDefault();
        btns[btns.length - 1].focus();
      } else if (e.key === 'Tab') {
        closeMenu(false);
      }
    });
    const onDoc = (e) => {
      if (!wrap.contains(e.target)) closeMenu(false);
    };
    document.addEventListener('pointerdown', onDoc, true);
    wrap.appendChild(pop);
    button.setAttribute('aria-expanded', 'true');
    menu = { pop, button, onDoc };
    const checked = pop.querySelector('[aria-checked="true"]') || pop.querySelector('button');
    if (checked) checked.focus();
  }

  function squadOptions() {
    const L = state.lang;
    const { squads } = lists(state.athletes);
    const cur = state.scope.squad || null;
    return [{ label: t('scope.all_squads', L), checked: !cur, pick: () => opts.onScope(pickSquad(state.scope, null, state.athletes)) }]
      .concat(squads.map((s) => ({
        label: String(s.name || ''),
        sub: Number.isFinite(s.athletes) ? t('players.athletes_count', L, { n: s.athletes }) : '',
        checked: cur === s.id,
        pick: () => opts.onScope(pickSquad(state.scope, s.id, state.athletes)),
      })));
  }

  function athleteOptions() {
    const L = state.lang;
    const cur = state.scope.athlete || null;
    return [{ label: t('scope.all_athletes', L), checked: !cur, pick: () => opts.onScope(pickAthlete(state.scope, null, state.athletes)) }]
      .concat(athletesInSquad(state.athletes, state.scope.squad || null).map((a) => ({
        label: String(a.name || ''),
        sub: Number.isFinite(a.matches) ? t('players.matches_count', L, { n: a.matches }) : '',
        checked: cur === a.id,
        pick: () => opts.onScope(pickAthlete(state.scope, a.id, state.athletes)),
      })));
  }

  function build() {
    closeMenu(false);
    const L = state.lang;
    const squadVal = el('span', { class: 'val' });
    const squadBtn = el('button', {
      type: 'button', class: 'tb-btn', 'aria-haspopup': 'menu', 'aria-expanded': 'false',
    }, el('span', { class: 'k-label', text: t('topbar.squad', L) }), squadVal, icon(ICONS.chevron, 14, 2));
    const squadWrap = el('div', { class: 'tb-pick' }, squadBtn);
    squadBtn.addEventListener('click', () => openMenu(squadWrap, squadBtn, squadOptions()));

    const avatar = el('span', { class: 'tb-avatar', 'aria-hidden': 'true' });
    const athleteVal = el('span', { class: 'val' });
    const athleteBtn = el('button', {
      type: 'button', class: 'tb-btn athlete', 'aria-haspopup': 'menu', 'aria-expanded': 'false',
    }, avatar, athleteVal, icon(ICONS.chevron, 14, 2));
    const athleteWrap = el('div', { class: 'tb-pick' }, athleteBtn);
    athleteBtn.addEventListener('click', () => openMenu(athleteWrap, athleteBtn, athleteOptions()));

    const scopeNav = el('nav', { class: 'tb-scope', 'aria-label': t('topbar.scope', L) },
      squadWrap, el('span', { class: 'tb-sep', 'aria-hidden': 'true' }, '/'), athleteWrap);

    const search = el('button', {
      type: 'button', class: 'tb-btn tb-search', 'aria-label': t('palette.title', L), 'aria-keyshortcuts': 'Control+K',
      onclick: () => opts.onPalette(),
    }, icon(ICONS.search, 16, 2), el('span', { class: 'hint', text: t('topbar.search', L) }),
    el('span', { class: 'kbd', 'aria-hidden': 'true', text: t('kbd.palette', L) }));

    const thumb = el('div', { class: 'thumb', 'aria-hidden': 'true', style: { width: RANGE_W + 'px' } });
    const rangeBtns = TOPBAR_RANGES.map((r) => el('button', {
      type: 'button', class: 'rng', 'aria-pressed': 'false', text: t('range.' + r, L),
      onclick: () => {
        if (rangeOf(state.scope) !== r) opts.onScope(withRange(state.scope, r));
      },
    }));
    const range = el('div', { class: 'pill-slider tb-range', role: 'group', 'aria-label': t('topbar.range', L) }, thumb, ...rangeBtns);

    const themeBtn = el('button', { type: 'button', class: 'ibtn', 'aria-label': t('topbar.theme', L), onclick: () => opts.onToggleTheme() });
    const badge = el('span', { class: 'badge', 'aria-hidden': 'true', hidden: true });
    const bell = el('button', {
      type: 'button', class: 'ibtn', 'aria-label': t('topbar.notifications', L), onclick: () => opts.onNotifications(),
    }, icon(ICONS.bell, 18, 1.8), badge);

    header.replaceChildren(scopeNav, el('div', { class: 'spacer' }), search, range, themeBtn, bell);
    refs = { squadBtn, squadVal, athleteBtn, athleteVal, avatar, thumb, rangeBtns, themeBtn, badge, bell, themeShown: null };
  }

  function paint() {
    const L = state.lang;
    const { athletes, squads } = lists(state.athletes);
    const squadName = nameOf(squads, state.scope.squad);
    const athleteName = nameOf(athletes, state.scope.athlete);
    refs.squadVal.textContent = squadName || t('scope.all_squads', L);
    refs.squadBtn.setAttribute('aria-label', t('topbar.squad', L) + ': ' + refs.squadVal.textContent);
    refs.athleteVal.textContent = athleteName || t('scope.all_athletes', L);
    refs.avatar.textContent = athleteName ? initials(athleteName) : '';
    refs.avatar.hidden = !athleteName;
    refs.athleteBtn.classList.toggle('athlete', !!athleteName);
    refs.athleteBtn.setAttribute('aria-label', t('topbar.athlete', L) + ': ' + refs.athleteVal.textContent);

    const cur = rangeOf(state.scope);
    const idx = TOPBAR_RANGES.indexOf(cur);
    refs.rangeBtns.forEach((b, i) => b.setAttribute('aria-pressed', i === idx ? 'true' : 'false'));
    refs.thumb.style.opacity = idx < 0 ? '0' : '1';
    refs.thumb.style.transform = 'translateX(' + Math.max(0, idx) * RANGE_W + 'px)';

    const night = state.theme !== 'day';
    if (refs.themeShown !== night) {
      refs.themeBtn.replaceChildren(icon(night ? ICONS.sun : ICONS.moon, 18, 1.8));
      refs.themeShown = night;
    }
    refs.badge.hidden = !(state.running > 0);
  }

  function update(next) {
    const prevLang = state.lang;
    state = { ...state, ...next, scope: { ...(next && next.scope ? next.scope : state.scope) } };
    if (!refs || state.lang !== prevLang) build();
    paint();
  }

  return { el: header, update, closeMenu: () => closeMenu(false) };
}
