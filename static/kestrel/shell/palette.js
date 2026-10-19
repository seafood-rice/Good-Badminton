// Command palette: a pure index (buildIndex / filterIndex, tested under Node) and the dialog (Ctrl K).
// Results come from pages, athletes, matches (analysed videos) and actions. Text is set with textContent.

import { t } from '../core/i18n.js';
import { formatHash } from '../core/router.js';
import { el, icon, ICONS, trapTab, refocus } from './dom.js';

export const PAGE_IDS = Object.freeze(['overview', 'matches', 'players', 'library', 'pipeline']);
const PAGE_SECTION = { overview: 'analyze', matches: 'analyze', players: 'analyze', library: 'manage', pipeline: 'manage' };

const ACTIONS = {
  new_analysis: { label: 'action.new_analysis', hint: 'action.new_analysis.hint', route: () => formatHash('new') },
  enter_result: { label: 'action.enter_result', hint: 'action.enter_result.hint', route: () => formatHash('matches') },
};
export const ACTION_IDS = Object.freeze(Object.keys(ACTIONS));

const VIDEO_STATUS = { analyzed: 'status.analysed', court_set: 'status.processing', new: 'status.new' };

const str = (v) => (typeof v === 'string' ? v : v === null || v === undefined ? '' : String(v));

function athleteLists(athletes) {
  if (Array.isArray(athletes)) return { list: athletes, squads: [] };
  if (athletes && typeof athletes === 'object') {
    return {
      list: Array.isArray(athletes.athletes) ? athletes.athletes : [],
      squads: Array.isArray(athletes.squads) ? athletes.squads : [],
    };
  }
  return { list: [], squads: [] };
}

/**
 * Flat palette index. `athletes` is the /api/athletes body ({athletes, squads}) or an athlete array;
 * `videos` is the /api/videos body. Returns [{kind, kindLabel, label, hint, route}], kind in
 * goto | player | match | action. Labels are localized; user-entered names are kept verbatim.
 */
export function buildIndex({ pages, athletes, videos, actions, lang } = {}) {
  const out = [];
  const kindLabel = (kind) => t('palette.kind.' + kind, lang);

  const pageIds = Array.isArray(pages) ? pages.filter((p) => PAGE_IDS.includes(p)) : PAGE_IDS;
  for (const id of pageIds) {
    out.push({
      kind: 'goto', kindLabel: kindLabel('goto'), label: t('nav.' + id, lang),
      hint: t('section.' + PAGE_SECTION[id], lang), route: formatHash(id),
    });
  }

  const { list, squads } = athleteLists(athletes);
  const squadName = new Map();
  for (const s of squads) if (s && s.id !== undefined && s.id !== null) squadName.set(s.id, str(s.name));
  for (const a of list) {
    if (!a || !a.id || !str(a.name).trim()) continue;
    const squad = a.squad_id !== undefined && a.squad_id !== null && squadName.has(a.squad_id) ? squadName.get(a.squad_id) : '';
    out.push({
      kind: 'player', kindLabel: kindLabel('player'), label: str(a.name),
      hint: squad || t('players.no_squad', lang), route: formatHash('overview', {}, { athlete: a.id }),
    });
  }

  for (const v of Array.isArray(videos) ? videos : []) {
    if (!v || !str(v.name) || !(v.has_match || v.has_posture)) continue;
    const route = v.has_match
      ? formatHash('matches', { stem: v.name })
      : formatHash('results', { stem: v.name }, { mode: 'posture' });
    out.push({
      kind: 'match', kindLabel: kindLabel('match'), label: str(v.name),
      hint: t(VIDEO_STATUS[v.status] || 'status.analysed', lang), route,
    });
  }

  const actionList = Array.isArray(actions) ? actions : ACTION_IDS;
  for (const a of actionList) {
    if (typeof a === 'string') {
      const def = ACTIONS[a];
      if (!def) continue;
      out.push({ kind: 'action', kindLabel: kindLabel('action'), label: t(def.label, lang), hint: t(def.hint, lang), route: def.route() });
    } else if (a && str(a.label) && str(a.route)) {
      out.push({ kind: 'action', kindLabel: kindLabel('action'), label: str(a.label), hint: str(a.hint), route: str(a.route) });
    }
  }
  return out;
}

/** Case-insensitive substring match over label, kind (code and label) and hint. Empty query => a copy of all. */
export function filterIndex(index, query) {
  if (!Array.isArray(index)) return [];
  const q = str(query).trim().toLowerCase();
  if (!q) return index.slice();
  return index.filter((item) => item
    && [item.label, item.kind, item.kindLabel, item.hint].map(str).join(' ').toLowerCase().includes(q));
}

/**
 * Index of the option to keep active after the list is rebuilt: the same route when it is still listed
 * (a background refresh must not move the user's selection), else 0.
 */
export function keepActive(prevItems, prevActive, nextItems) {
  const prev = Array.isArray(prevItems) ? prevItems[prevActive] : null;
  if (!prev || !Array.isArray(nextItems)) return 0;
  const i = nextItems.findIndex((x) => x && x.route === prev.route && x.kind === prev.kind);
  return i < 0 ? 0 : i;
}

/**
 * The palette dialog. opts: {getLang(), getSources() -> {athletes, videos}, loadSources() -> Promise,
 * navigate(hash)}. Returns {open(), close(), toggle(), isOpen()}.
 */
export function createPalette(opts) {
  let root = null;
  let returnFocus = null;
  let items = [];
  let active = 0;
  let input = null;
  let list = null;
  let empty = null;
  let seq = 0;
  const listId = 'k-palette-list';

  const lang = () => (opts.getLang ? opts.getLang() : 'zh');

  function index() {
    const src = opts.getSources ? opts.getSources() || {} : {};
    return buildIndex({ athletes: src.athletes, videos: src.videos, lang: lang() });
  }

  function setActive(i) {
    if (!list) return;
    const links = list.querySelectorAll('a[role="option"]');
    if (!links.length) {
      active = 0;
      input.removeAttribute('aria-activedescendant');
      return;
    }
    active = Math.max(0, Math.min(links.length - 1, i));
    links.forEach((a, j) => a.setAttribute('aria-selected', j === active ? 'true' : 'false'));
    input.setAttribute('aria-activedescendant', links[active].id);
    links[active].scrollIntoView({ block: 'nearest' });
  }

  function choose(item) {
    if (!item) return;
    close();
    opts.navigate(item.route);
  }

  // refresh: a background data refresh (keep the active option, do not replay the entrance stagger).
  function renderList({ refresh = false } = {}) {
    if (!root) return;
    const prevItems = items;
    const prevActive = active;
    items = filterIndex(index(), input.value);
    list.replaceChildren();
    items.forEach((item, i) => {
      const a = el('a', {
        class: 'palette-item', role: 'option', id: 'k-pal-opt-' + i, href: item.route, tabindex: '-1',
        'aria-selected': 'false',
        onclick: (e) => {
          if (e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
          e.preventDefault();
          choose(item);
        },
        onmousemove: () => { if (active !== i) setActive(i); },
      },
      el('span', { class: 'kind', text: item.kindLabel || item.kind }),
      el('span', { class: 'lbl', text: item.label }),
      el('span', { class: 'hint', text: item.hint }));
      const li = el('li', { role: 'none', style: refresh ? { animation: 'none' } : { animationDelay: Math.min(i, 14) * 50 + 'ms' } }, a);
      list.appendChild(li);
    });
    const none = items.length === 0;
    empty.hidden = !none;
    empty.textContent = none ? t('palette.empty', lang(), { query: input.value.trim() }) : '';
    setActive(refresh ? keepActive(prevItems, prevActive, items) : 0);
  }

  function onKey(e) {
    if (!root) return;
    if (e.key === 'Escape') {
      e.preventDefault();
      e.stopPropagation();
      close();
    } else if (e.key === 'ArrowDown') {
      e.preventDefault();
      setActive(active + 1);
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      setActive(active - 1);
    } else if (e.key === 'Home' && e.ctrlKey) {
      e.preventDefault();
      setActive(0);
    } else if (e.key === 'End' && e.ctrlKey) {
      e.preventDefault();
      setActive(items.length - 1);
    } else if (e.key === 'Enter' && e.target === input) {
      e.preventDefault();
      choose(items[active]);
    } else {
      trapTab(root, e);
    }
  }

  function open() {
    if (root) {
      input.focus();
      return;
    }
    const L = lang();
    returnFocus = document.activeElement;
    input = el('input', {
      type: 'text', role: 'combobox', 'aria-expanded': 'true', 'aria-controls': listId, 'aria-autocomplete': 'list',
      'aria-label': t('palette.title', L), placeholder: t('palette.placeholder', L), autocomplete: 'off', spellcheck: 'false',
      oninput: () => renderList(),
    });
    list = el('ul', { class: 'palette-list', id: listId, role: 'listbox', 'aria-label': t('palette.title', L) });
    empty = el('div', { class: 'palette-empty', role: 'status', hidden: true });
    const dialog = el('div', { class: 'palette', role: 'dialog', 'aria-modal': 'true', 'aria-label': t('palette.title', L) },
      el('div', { class: 'palette-head' },
        icon(ICONS.search, 18, 2),
        input,
        el('button', { type: 'button', class: 'palette-esc', 'aria-label': t('palette.close', L), onclick: () => close() }, 'Esc')),
      list,
      empty,
      el('div', { class: 'palette-foot' },
        el('span', { text: t('palette.hint.filter', L) }),
        el('span', { text: t('palette.hint.open', L) }),
        el('span', { class: 'r', text: t('palette.hint.all', L) })));
    const scrim = el('div', { class: 'scrim', 'aria-hidden': 'true', onclick: () => close() });
    root = el('div', { class: 'palette-root', onkeydown: onKey }, scrim, dialog);
    document.body.appendChild(root);
    renderList();
    input.focus();

    // Refresh athletes and videos in the background; re-filter if still open.
    const mine = ++seq;
    if (opts.loadSources) {
      Promise.resolve()
        .then(() => opts.loadSources())
        .then(() => { if (root && mine === seq) renderList({ refresh: true }); })
        .catch(() => {});
    }
  }

  function close() {
    if (!root) return;
    seq += 1;
    root.remove();
    root = null;
    input = null;
    list = null;
    empty = null;
    items = [];
    const back = returnFocus;
    returnFocus = null;
    refocus(back);
  }

  return {
    open,
    close,
    toggle: () => (root ? close() : open()),
    isOpen: () => !!root,
  };
}
