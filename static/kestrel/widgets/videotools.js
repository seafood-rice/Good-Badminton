// Video tools for the merged Matches page (spec 2026-10-24 section 6): the tag editor, the tag manager and the
// delete-video dialog, ported from the legacy library screen onto the shell's dialog look (scrim + .info-dialog).
// One dialog at a time; Escape, the scrim and the close button dismiss it (not while a delete is running) and focus
// returns to the opener. Every user string (stems, tags) is set as text, never parsed as HTML.
import { t } from '../core/i18n.js';
import { api as defaultApi, errorText } from '../core/api.js';
import { h } from './frame.js';
import { trapTab, refocus } from '../shell/dom.js';
import { tagError, normTag, tagLabel, isSysTag } from '../views/library_model.js';

const MAX_SUGGEST = 5;
const UNDO_MS = 6000;

let current = null;

const str = (v) => (typeof v === 'string' ? v : '');
const userTags = (v) => (Array.isArray(v && v.tags) ? v.tags.filter((x) => typeof x === 'string' && x) : []);

/** The open dialog, if any (tests and the page use it to know whether a tool is showing). */
export function isOpen() {
  return !!current;
}

/** Close the open dialog (as a dismissal: its onClose still runs). */
export function closeTool() {
  if (current) current.close();
}

/**
 * The shared dialog frame. Returns {dialog, body, actions, close(result), busy(on)}; `onClose(result)` runs once,
 * after focus went back to the opener. A busy dialog ignores Escape and the scrim.
 */
function frame({ lang, title, sub, kind, cls, onClose }) {
  if (current) current.close();
  const opener = typeof document !== 'undefined' ? document.activeElement : null;
  const titleId = `mv-dlg-${Math.random().toString(36).slice(2, 8)}`;
  let busy = false;
  let closed = false;
  const closeBtn = h('button', { type: 'button', class: 'palette-esc', 'aria-label': t('btn.close', lang), onclick: () => { if (!busy) close(null); } }, 'Esc');
  const body = h('div', { class: 'info-body mv-dlg-body' });
  const actions = h('div', { class: 'mv-actions mv-dlg-actions' });
  const dialog = h('div', {
    class: 'info-dialog mv-dlg' + (cls ? ' ' + cls : ''), role: 'dialog', 'aria-modal': 'true', 'aria-labelledby': titleId, 'data-tool': kind, tabindex: '-1',
  }, [
    h('div', { class: 'info-head' }, [
      h('div', { class: 'mv-dlg-titles' }, [
        h('h2', { class: 'info-title', id: titleId }, title),
        sub ? h('div', { class: 'mono mv-dlg-sub' }, sub) : null,
      ]),
      closeBtn,
    ]),
    body,
    actions,
  ]);
  const scrim = h('div', { class: 'scrim', 'aria-hidden': 'true', onclick: () => { if (!busy) close(null); } });
  const root = h('div', {
    class: 'info-root mv-dlg-root',
    onkeydown: (e) => {
      if (e.key === 'Escape') {
        e.preventDefault();
        if (typeof e.stopPropagation === 'function') e.stopPropagation();
        if (!busy) close(null);
      } else trapTab(dialog, e);
    },
  }, [scrim, dialog]);
  const onHash = () => close(null);
  function close(result) {
    if (closed) return;
    closed = true;
    if (current && current.root === root) current = null;
    if (typeof window !== 'undefined' && typeof window.removeEventListener === 'function') window.removeEventListener('hashchange', onHash);
    if (root.parentNode) root.parentNode.removeChild(root);
    refocus(opener);
    if (typeof onClose === 'function') onClose(result);
  }
  document.body.appendChild(root);
  // Navigating away (back button, links) must not leave a dialog over the next view.
  if (typeof window !== 'undefined' && typeof window.addEventListener === 'function') window.addEventListener('hashchange', onHash, { once: true });
  current = { root, close: () => close(null) };
  return { root, dialog, body, actions, closeBtn, close, busy: (on) => { busy = !!on; closeBtn.disabled = busy; } };
}

/** A toast with an Undo button (the shell's toast has none). */
function undoToast(message, lang, onUndo) {
  const old = document.querySelector('.shell-toast');
  if (old && old.parentNode) old.parentNode.removeChild(old);
  let timer = null;
  const node = h('div', { class: 'toast shell-toast mv-undo', role: 'status' }, [
    h('span', null, message),
    h('button', {
      type: 'button', class: 'ov-btn small',
      onclick: () => {
        clearTimeout(timer);
        if (node.parentNode) node.parentNode.removeChild(node);
        onUndo();
      },
    }, t('lib.tagman.undo', lang)),
  ]);
  document.body.appendChild(node);
  timer = setTimeout(() => { if (node.parentNode) node.parentNode.removeChild(node); }, UNDO_MS);
  return node;
}

/** A plain toast (same place and lifetime as the shell's). */
function plainToast(message) {
  const old = document.querySelector('.shell-toast');
  if (old && old.parentNode) old.parentNode.removeChild(old);
  const node = h('div', { class: 'toast shell-toast', role: 'status' }, message);
  document.body.appendChild(node);
  setTimeout(() => { if (node.parentNode) node.parentNode.removeChild(node); }, UNDO_MS);
}

// ---------- tag editor ----------

/**
 * Edit one video's tags. opts: {video (an /api/videos entry), lang, knownTags: [user tags in the library], api,
 * onClose({changed, tags})}. Each add / remove is saved at once (PUT /api/videos/<stem>/tags, whole-list
 * replacement); controls are disabled while a save is in flight so two edits never race.
 */
export function openTagEditor({ video, lang, knownTags = [], api = defaultApi, onClose } = {}) {
  const stem = str(video && video.name);
  let draft = userTags(video);
  let changed = false;
  let saving = false;
  const sys = [video && video.has_match ? 'match' : null, video && video.has_posture ? 'drill' : null].filter(Boolean);
  const f = frame({
    lang, kind: 'tags', title: t('lib.tagedit.title', lang, { name: stem }),
    onClose: () => { if (typeof onClose === 'function') onClose({ changed, tags: draft }); },
  });
  const chips = h('div', { class: 'mv-tagedit-chips', role: 'list' });
  const input = h('input', {
    type: 'text', class: 'mv-input', maxlength: '60', placeholder: t('lib.tagedit.placeholder', lang),
    'aria-label': t('lib.tagedit.placeholder', lang), autocomplete: 'off',
  });
  const addBtn = h('button', { type: 'button', class: 'ov-btn small primary' }, t('btn.add', lang));
  const err = h('div', { class: 'mv-err', role: 'alert' });
  const status = h('div', { class: 'mv-hint', role: 'status' });
  const sugg = h('div', { class: 'mv-tagedit-sugg' });
  f.body.appendChild(chips);
  f.body.appendChild(h('div', { class: 'mv-tagedit-add' }, [input, addBtn]));
  f.body.appendChild(sugg);
  f.body.appendChild(err);
  f.body.appendChild(status);
  f.actions.appendChild(h('button', { type: 'button', class: 'ov-btn small', onclick: () => f.close(null) }, t('btn.close', lang)));

  function lock(on) {
    saving = on;
    f.busy(on);
    input.disabled = on;
    addBtn.disabled = on;
    for (const b of chips.querySelectorAll('button')) b.disabled = on;
    for (const b of sugg.querySelectorAll('button')) b.disabled = on;
    status.textContent = on ? t('lib.tagedit.saving', lang) : '';
  }

  async function persist(next) {
    if (saving) return false;
    err.textContent = '';
    lock(true);
    try {
      const res = await api.setTags(stem, next);
      draft = Array.isArray(res && res.tags) ? res.tags.slice() : next.slice();
      changed = true;
      lock(false);
      draw();
      return true;
    } catch (e) {
      lock(false);
      err.textContent = e && e.messages ? errorText(e, lang) : t('lib.tagedit.save_failed', lang);
      return false;
    }
  }

  function drawSuggestions() {
    sugg.textContent = '';
    const typed = normTag(input.value);
    const pool = [...new Set(knownTags.filter((x) => typeof x === 'string' && x && !isSysTag(x)))]
      .filter((x) => !draft.includes(x) && (!typed || x.includes(typed)))
      .sort((a, b) => a.localeCompare(b))
      .slice(0, MAX_SUGGEST);
    pool.forEach((x) => sugg.appendChild(h('button', { type: 'button', class: 'k-chip mv-tag-sugg', 'data-add': x, onclick: () => add(x) }, '+ ' + x)));
  }

  function draw() {
    chips.textContent = '';
    sys.forEach((x) => chips.appendChild(h('span', { class: 'k-chip mv-tag sys', role: 'listitem', title: t('lib.tagedit.system', lang) }, tagLabel(x, lang))));
    draft.forEach((x) => chips.appendChild(h('span', { class: 'k-chip mv-tag', role: 'listitem' }, [
      x,
      h('button', {
        type: 'button', class: 'mv-tag-rm', 'data-rm': x, 'aria-label': t('lib.tagedit.remove_aria', lang, { tag: x }),
        onclick: () => persist(draft.filter((y) => y !== x)).then((ok) => { if (ok) input.focus(); }),
      }, '×'),
    ])));
    if (!sys.length && !draft.length) chips.appendChild(h('span', { class: 'mv-hint' }, t('lib.tags.none', lang)));
    drawSuggestions();
  }

  async function add(raw) {
    const why = tagError(raw);
    if (why) {
      err.textContent = t(why, lang);
      input.focus();
      return;
    }
    const tag = normTag(raw);
    if (draft.includes(tag)) {
      input.value = '';
      drawSuggestions();
      return;
    }
    // A failed save keeps what was typed so it can be retried.
    if (await persist([...draft, tag])) input.value = '';
    drawSuggestions();
    input.focus();
  }

  input.addEventListener('input', () => { err.textContent = ''; drawSuggestions(); });
  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      add(input.value);
    }
  });
  addBtn.addEventListener('click', () => add(input.value));
  draw();
  input.focus();
  return { close: () => f.close(null), input };
}

// ---------- tag manager ----------

/**
 * Rename or delete user tags across the library. opts: {videos (the /api/videos list), lang, api,
 * onClose({changed}), onChanged() (after an Undo re-applied a deleted tag)}. A rename onto an existing tag merges.
 */
export function openTagManager({ videos = [], lang, api = defaultApi, onClose, onChanged } = {}) {
  const list = (Array.isArray(videos) ? videos : []).filter((v) => v && str(v.name)).map((v) => ({ name: v.name, tags: userTags(v) }));
  let changed = false;
  let editing = null;
  let renameDraft = null;      // what was typed for `editing`: kept when a rename fails
  let confirming = null;
  let busy = false;
  const f = frame({
    lang, kind: 'tagman', title: t('lib.tagman.title', lang),
    onClose: () => { if (typeof onClose === 'function') onClose({ changed }); },
  });
  const note = h('div', { class: 'mv-hint' }, t('lib.tagman.system_note', lang));
  const rowsEl = h('ul', { class: 'mv-tagman' });
  const err = h('div', { class: 'mv-err form', role: 'alert' });
  const status = h('div', { class: 'mv-hint', role: 'status' });
  f.body.appendChild(note);
  f.body.appendChild(rowsEl);
  f.body.appendChild(err);
  f.body.appendChild(status);
  f.actions.appendChild(h('button', { type: 'button', class: 'ov-btn small', onclick: () => f.close(null) }, t('btn.close', lang)));

  function counts() {
    const c = new Map();
    for (const v of list) for (const x of v.tags) c.set(x, (c.get(x) || 0) + 1);
    return [...c.entries()].sort((a, b) => a[0].localeCompare(b[0]));
  }

  /** While a rename / delete runs every control is disabled in place (a redraw would drop the typed name). */
  function setBusy(on) {
    busy = on;
    f.busy(on);
    if (on) for (const c of rowsEl.querySelectorAll('button,input')) c.disabled = true;
  }

  async function doRename(from, to) {
    if (busy) return;
    renameDraft = str(to);
    const why = tagError(to);
    if (why) { err.textContent = t(why, lang); return; }
    const target = normTag(to);
    if (target === from) { editing = null; draw(); return; }
    setBusy(true);
    err.textContent = '';
    try {
      const res = await api.renameTag(from, target);
      for (const v of list) {
        if (!v.tags.includes(from)) continue;
        v.tags = [...new Set(v.tags.map((x) => (x === from ? target : x)))].sort();
      }
      changed = true;
      editing = null;
      renameDraft = null;
      status.textContent = t(res && res.merged ? 'lib.tagman.merged' : 'lib.tagman.renamed', lang, { tag: target });
    } catch (e) {
      err.textContent = e && e.messages && e.status !== 409 ? errorText(e, lang) : t('lib.tagman.failed', lang);
    }
    setBusy(false);
    draw();
  }

  async function doDelete(tag) {
    if (busy) return;
    setBusy(true);
    err.textContent = '';
    const stems = list.filter((v) => v.tags.includes(tag)).map((v) => v.name);
    try {
      await api.deleteTag(tag);
      for (const v of list) v.tags = v.tags.filter((x) => x !== tag);
      changed = true;
      confirming = null;
      undoToast(t('lib.tagman.deleted', lang, { tag }), lang, () => undo(tag, stems));
    } catch (e) {
      err.textContent = e && e.messages ? errorText(e, lang) : t('lib.tagman.failed', lang);
    }
    setBusy(false);
    draw();
  }

  /** Put a deleted tag back on the videos that had it, from their current tags (they may have changed since). */
  async function undo(tag, stems) {
    let oks = [];
    try {
      const fresh = await api.videos({ timeout: 60000 });
      const byName = new Map((Array.isArray(fresh) ? fresh : []).filter((v) => v && v.name).map((v) => [v.name, v]));
      oks = await Promise.all(stems.filter((s) => byName.has(s)).map((s) => {
        const tags = userTags(byName.get(s));
        return api.setTags(s, tags.includes(tag) ? tags : [...tags, tag]).then(() => true, () => false);
      }));
      if (oks.length < stems.length) oks.push(false);
    } catch (e) {
      oks = [false];
    }
    for (const v of list) if (stems.includes(v.name) && !v.tags.includes(tag)) v.tags = [...v.tags, tag].sort();
    if (oks.some((ok) => !ok)) plainToast(t('lib.tagman.undo_failed', lang));
    if (current && current.root === f.root) draw();
    if (typeof onChanged === 'function') onChanged();
  }

  function draw() {
    rowsEl.textContent = '';
    const rows = counts();
    if (!rows.length) rowsEl.appendChild(h('li', { class: 'mv-hint' }, t('lib.tagman.empty', lang)));
    for (const [tag, n] of rows) {
      const li = h('li', { class: 'mv-tagman-row', 'data-tag': tag });
      if (editing === tag) {
        const input = h('input', { type: 'text', class: 'mv-input small', value: tag, maxlength: '60', 'aria-label': t('lib.tagman.rename_aria', lang, { tag }) });
        input.value = renameDraft !== null ? renameDraft : tag;
        const save = h('button', { type: 'button', class: 'ov-btn small primary', 'data-save': tag, disabled: busy, onclick: () => doRename(tag, input.value) }, t('btn.save', lang));
        input.addEventListener('keydown', (e) => {
          if (e.key === 'Enter') { e.preventDefault(); if (!busy) doRename(tag, input.value); }
          if (e.key === 'Escape') { e.preventDefault(); if (typeof e.stopPropagation === 'function') e.stopPropagation(); editing = null; draw(); }
        });
        li.appendChild(input);
        li.appendChild(save);
        li.appendChild(h('button', { type: 'button', class: 'ov-btn small', onclick: () => { editing = null; err.textContent = ''; draw(); } }, t('btn.cancel', lang)));
        rowsEl.appendChild(li);
        input.focus();
        continue;
      }
      li.appendChild(h('span', { class: 'k-chip mv-tag' }, tag));
      li.appendChild(h('span', { class: 'mono mv-hint' }, t('lib.tagman.count', lang, { n })));
      if (confirming === tag) {
        li.appendChild(h('span', { class: 'mv-tagman-q' }, t('lib.tagman.confirm', lang, { tag, n })));
        li.appendChild(h('button', { type: 'button', class: 'ov-btn small danger', 'data-confirm': tag, disabled: busy, onclick: () => doDelete(tag) }, t('btn.delete', lang)));
        li.appendChild(h('button', { type: 'button', class: 'ov-btn small', onclick: () => { confirming = null; draw(); } }, t('btn.cancel', lang)));
      } else {
        li.appendChild(h('button', {
          type: 'button', class: 'ov-btn small', 'data-ren': tag, disabled: busy, 'aria-label': t('lib.tagman.rename_aria', lang, { tag }),
          onclick: () => { editing = tag; renameDraft = null; confirming = null; err.textContent = ''; draw(); },
        }, t('btn.rename', lang)));
        li.appendChild(h('button', {
          type: 'button', class: 'ov-btn small danger', 'data-del': tag, disabled: busy, 'aria-label': t('lib.tagman.delete_aria', lang, { tag }),
          onclick: () => { confirming = tag; editing = null; err.textContent = ''; draw(); },
        }, t('btn.delete', lang)));
      }
      rowsEl.appendChild(li);
    }
  }

  draw();
  f.closeBtn.focus();
  return { close: () => f.close(null) };
}

// ---------- image viewer ----------

/** Show one image large in the shared dialog frame (Escape, the scrim and Close dismiss it; focus returns). */
export function openImageDialog({ lang, src, title } = {}) {
  const f = frame({ lang, kind: 'image', cls: 'ws-img-dialog', title: str(title) });
  f.body.appendChild(h('img', { src: str(src), alt: str(title) }));
  f.actions.appendChild(h('button', { type: 'button', class: 'ov-btn small', onclick: () => f.close(null) }, t('btn.close', lang)));
  f.closeBtn.focus();
  return { close: () => f.close(null) };
}

// ---------- delete video ----------

const SCOPE_TEXT = {
  all: { title: 'lib.del.title', warn: 'lib.del.warn' },
  match: { title: 'wsx.del.match.title', warn: 'wsx.del.match.warn' },
  clips: { title: 'wsx.del.clips.title', warn: 'wsx.del.clips.warn' },
};

/**
 * Delete a video and every output (scope "all", the default), a match's analysis results ("match") or only its
 * generated clips ("clips"). opts: {stem, lang, api, onClose({deleted}), scope}. The confirm button stays disabled
 * until the preview lists something to delete; a 409 means an analysis is running on the video.
 */
export function openDeleteDialog({ stem, lang, api = defaultApi, onClose, scope = 'all' } = {}) {
  const name = str(stem);
  const text = SCOPE_TEXT[scope] || SCOPE_TEXT.all;
  let deleted = false;
  const f = frame({
    lang, kind: 'delete', title: t(text.title, lang), sub: name,
    onClose: () => { if (typeof onClose === 'function') onClose({ deleted }); },
  });
  const rows = h('div', { class: 'mv-del-rows mv-hint', role: 'status' }, t('state.loading', lang));
  const err = h('div', { class: 'mv-err form', role: 'alert' });
  const cancel = h('button', { type: 'button', class: 'ov-btn small', onclick: () => f.close(null) }, t('btn.cancel', lang));
  const confirmBtn = h('button', { type: 'button', class: 'ov-btn small danger mv-del-go', disabled: true }, t('lib.del.confirm', lang));
  f.body.appendChild(h('p', { class: 'mv-del-warn' }, t(text.warn, lang)));
  f.body.appendChild(rows);
  f.body.appendChild(err);
  f.actions.appendChild(cancel);
  f.actions.appendChild(confirmBtn);

  const line = (label, n, mb, cls) => h('div', { class: 'mv-del-row' + (cls ? ' ' + cls : '') }, [
    h('span', null, label), h('span', { class: 'mono' }, t('lib.del.files', lang, { n, mb })),
  ]);

  api.deletePreview(name, scope).then((d) => {
    if (!f.root.parentNode) return;      // dismissed while the preview loaded
    if (!d || !d.ok) { rows.textContent = t('lib.del.preview_failed', lang); return; }
    if (!d.total_files) { rows.textContent = t('lib.del.nothing', lang); return; }
    rows.textContent = '';
    rows.classList.remove('mv-hint');
    for (const g of Array.isArray(d.groups) ? d.groups : []) {
      const key = 'lib.del.group.' + str(g && g.key);
      const label = t(key, lang);
      rows.appendChild(line(label === key ? str(g && g.key) : label, g.files, g.size_mb));
    }
    rows.appendChild(line(t('lib.del.total', lang), d.total_files, d.total_size_mb, 'total'));
    confirmBtn.disabled = false;
  }, () => {
    if (f.root.parentNode) rows.textContent = t('lib.del.preview_failed', lang);
  });

  confirmBtn.addEventListener('click', async () => {
    if (confirmBtn.disabled) return;
    f.busy(true);
    confirmBtn.disabled = true;
    cancel.disabled = true;
    confirmBtn.textContent = t('lib.del.busy', lang);
    err.textContent = '';
    try {
      await api.deleteVideo(name, scope);
      deleted = true;
      f.busy(false);
      f.close({ deleted: true });
    } catch (e) {
      f.busy(false);
      confirmBtn.disabled = false;
      cancel.disabled = false;
      confirmBtn.textContent = t('lib.del.confirm', lang);
      const kind = e && e.kind;
      err.textContent = e && e.status === 409 ? t('lib.del.running', lang)
        : kind === 'network' || kind === 'timeout' ? t('lib.del.network', lang) : t('lib.del.failed', lang);
    }
  });
  cancel.focus();
  return { close: () => f.close(null), confirmBtn };
}
