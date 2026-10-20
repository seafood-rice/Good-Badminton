// Peek drawer: a 420 px panel that slides in from the right over a blurred scrim (700 ms).
// Esc, the scrim, the close button or any [data-peek-close] element inside the content close it.
// Focus is trapped while open and returns to the element that opened it.

import { t } from '../core/i18n.js';
import { el, icon, ICONS, trapTab, refocus } from './dom.js';

/** opts: {getLang()}. Returns {open(contentEl, {label, onClose}), close(), isOpen()}. */
export function createPeek(opts = {}) {
  let root = null;
  let panel = null;
  let returnFocus = null;
  let onCloseCb = null;

  const lang = () => (opts.getLang ? opts.getLang() : 'zh');

  function close() {
    if (!root) return;
    const cb = onCloseCb;
    const back = returnFocus;
    root.remove();
    root = null;
    panel = null;
    onCloseCb = null;
    returnFocus = null;
    refocus(back);
    if (typeof cb === 'function') {
      try {
        cb();
      } catch (e) {
        console.error('peek onClose failed', e);
      }
    }
  }

  function onKey(e) {
    if (e.key === 'Escape') {
      e.preventDefault();
      e.stopPropagation();
      close();
      return;
    }
    trapTab(panel, e);
  }

  /** Show `content` (a Node) in the drawer. Opening again swaps the content and keeps the first opener. */
  function open(content, { label, onClose } = {}) {
    const L = lang();
    const keepFocus = root ? returnFocus : document.activeElement;
    if (root) {
      const prevCb = onCloseCb;
      onCloseCb = null;
      root.remove();
      root = null;
      if (typeof prevCb === 'function') {
        try { prevCb(); } catch (e) { console.error('peek onClose failed', e); }
      }
    }
    returnFocus = keepFocus;
    onCloseCb = onClose || null;
    const closeBtn = el('button', { type: 'button', class: 'ibtn peek-close', 'aria-label': t('btn.close', L), onclick: () => close() },
      icon(ICONS.close, 18, 2));
    const body = el('div', { class: 'peek-body' });
    if (content) body.appendChild(content);
    panel = el('aside', {
      class: 'peek', role: 'dialog', 'aria-modal': 'true', 'aria-label': label || t('peek.aria', L), tabindex: '-1',
    }, el('div', { class: 'peek-bar' }, closeBtn), body);
    panel.addEventListener('click', (e) => {
      const hit = e.target && e.target.closest ? e.target.closest('[data-peek-close]') : null;
      if (hit && panel.contains(hit)) close();
    });
    const scrim = el('div', { class: 'scrim', 'aria-hidden': 'true', onclick: () => close() });
    root = el('div', { class: 'peek-root', onkeydown: onKey }, scrim, panel);
    document.body.appendChild(root);
    closeBtn.focus();
    return { close, el: panel };
  }

  return { open, close, isOpen: () => !!root };
}
