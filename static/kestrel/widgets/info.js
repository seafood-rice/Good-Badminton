// Widget help: the small "i" button on every card and tile, and the dialog it opens (palette-style scrim + pop).
// The dialog explains what the widget shows, how to read it, and where its data comes from and stops.
// Text comes from i18n keys info.<id>.what / .read / .data, always set through textContent.
// No DOM access at import time, so the module (and INFO_IDS) loads under `node --test`.
import { t } from '../core/i18n.js';
import { el, icon, trapTab, refocus } from '../shell/dom.js';

/** Card ids (frame.build data.id) and tile ids (tile view-model id) that carry help text. */
export const INFO_IDS = Object.freeze([
  'court', 'radar', 'trend', 'findings', 'matches', 'doubles', 'pipeline',
  'technique_index', 'match_win_rate', 'avg_rally', 'rally_win_rate', 'strongest', 'weakest_scored',
  'inside_reference', 'techniques_scored', 'recovery', 'net_share', 'pull_rear', 'unforced_errors',
  'doubles_distance', 'doubles_hits_named',
]);
export const INFO_SECTIONS = Object.freeze(['what', 'read', 'data']);

const INFO_ICON = 'M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20z M12 16v-4.5 M12 8h.01';

/** True when `id` has help text in `lang` (unknown ids get no button rather than an empty dialog). */
export function hasInfo(id, lang) {
  return typeof id === 'string' && INFO_SECTIONS.some((s) => has(`info.${id}.${s}`, lang));
}

const has = (key, lang) => t(key, lang) !== key;

let root = null;
let returnFocus = null;

export function isOpen() {
  return !!root;
}

export function close() {
  if (!root) return;
  window.removeEventListener('hashchange', close);
  root.remove();
  root = null;
  const back = returnFocus;
  returnFocus = null;
  refocus(back);
}

/** Open the help dialog for widget `id`, titled `title`. Focus returns to the opener on close. */
export function open(id, title, lang) {
  close();
  returnFocus = document.activeElement;
  const titleId = 'k-info-title';
  const sections = INFO_SECTIONS
    .filter((s) => has(`info.${id}.${s}`, lang))
    .map((s, i) => el('section', { class: 'info-sec', style: { animationDelay: 120 + i * 70 + 'ms' } },
      el('h3', { class: 'k-label info-sec-title', text: t('info.section.' + s, lang) }),
      el('p', { text: t(`info.${id}.${s}`, lang) })));
  const closeBtn = el('button', { type: 'button', class: 'palette-esc', 'aria-label': t('info.close', lang), onclick: () => close() }, 'Esc');
  // tabindex -1: a click on the dialog's text keeps focus inside, so Esc and the Tab trap still work.
  const dialog = el('div', {
    class: 'info-dialog', role: 'dialog', 'aria-modal': 'true', 'aria-labelledby': titleId, 'data-info': id, tabindex: '-1',
  },
    el('div', { class: 'info-head' },
      icon(INFO_ICON, 18, 2),
      el('h2', { class: 'info-title', id: titleId, text: title || '' }),
      closeBtn),
    el('div', { class: 'info-body' }, sections));
  const scrim = el('div', { class: 'scrim', 'aria-hidden': 'true', onclick: () => close() });
  root = el('div', {
    class: 'info-root',
    onkeydown: (e) => {
      if (e.key === 'Escape') {
        e.preventDefault();
        e.stopPropagation();
        close();
      } else trapTab(dialog, e);
    },
  }, scrim, dialog);
  document.body.appendChild(root);
  // Navigating away (back button, links) must not leave a dialog over the next view.
  window.addEventListener('hashchange', close, { once: true });
  closeBtn.focus();
}

/** The "i" button for widget `id` (null when it has no help text). Clicks never reach the card behind it. */
export function button(id, title, lang) {
  if (!hasInfo(id, lang)) return null;
  const label = t('info.open', lang, { title: title || '' });
  return el('button', {
    type: 'button', class: 'kw-info', 'aria-label': label, title: label, 'aria-haspopup': 'dialog', 'data-info-btn': id,
    onclick: (e) => {
      e.stopPropagation();
      e.preventDefault();
      open(id, title, lang);
    },
    onkeydown: (e) => { if (e.key === 'Enter' || e.key === ' ') e.stopPropagation(); },
  }, icon(INFO_ICON, 15, 2));
}
