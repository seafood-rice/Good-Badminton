// Clips tab panel for the match details page: cut one clip per detected rally (POST /api/clip), list the downloads,
// and delete the generated clips (the delete dialog's "clips" scope). Clip names and server text are set as text.
import { t } from '../core/i18n.js';
import { sendJSON, errorText } from '../core/api.js';
import { h } from './frame.js';
import { openDeleteDialog } from './videotools.js';
import { isNum } from '../views/workspace_model.js';

// Cutting a whole match takes minutes: the server allows 300 s.
const CLIP_TIMEOUT_MS = 300000;
const PADDING_SEC = 1.5;

const defaultApi = { post: (url, body) => sendJSON('POST', url, body, { timeout: CLIP_TIMEOUT_MS }) };

/** A clip URL the page may link: a same-origin path (not protocol-relative) or an http(s) URL (never javascript:, data:, ...). */
const linkable = (url) => typeof url === 'string' && /^(\/(?!\/)|https?:\/\/)/i.test(url);

/** What went wrong, as text: the server's own message when the error carries one, else the bilingual generic one. */
function failureText(lang, detail) {
  const base = t('wsx.clips.failed', lang);
  return detail ? base + ': ' + detail : base;
}

/**
 * clipsPanel({stem, lang, rallyOk, api: {post(url, body)}, onDeleted}) -> {el, destroy}. `rallyOk` is whether the
 * match has rallies to cut; `onDeleted()` runs after the generated clips were deleted.
 */
export function clipsPanel({ stem, lang, rallyOk = false, api = defaultApi, onDeleted } = {}) {
  let alive = true;
  let busy = false;
  const genBtn = h('button', { type: 'button', class: 'ov-btn small primary', disabled: !rallyOk }, t('wsx.clips.generate', lang));
  const delBtn = h('button', { type: 'button', class: 'ov-btn small danger' }, t('wsx.clips.delete', lang));
  const err = h('div', { class: 'mv-err', role: 'alert' });
  const list = h('ul', { class: 'ws-clips-list' });
  const root = h('div', { class: 'ws-clips' }, [
    h('div', { class: 'ws-clips-bar' }, [genBtn, delBtn]),
    h('div', { class: 'mv-hint' }, rallyOk ? t('wsx.clips.hint', lang) : t('wsx.clips.no_rallies', lang)),
    err,
    list,
  ]);

  /** List the clips; returns how many had a linkable URL (an ok reply can carry none when every cut failed). */
  function showClips(clips) {
    list.textContent = '';
    let n = 0;
    for (const c of clips) {
      if (!c || !linkable(c.url)) continue;
      n += 1;
      list.appendChild(h('li', null, h('a', { class: 'ws-clip', href: c.url, download: true }, [
        String(c.name || c.url),
        ' ',
        isNum(c.size_mb) ? h('span', { class: 'mono' }, c.size_mb + ' MB') : null,
      ])));
    }
    return n;
  }

  genBtn.addEventListener('click', async () => {
    if (busy || genBtn.disabled) return;
    busy = true;
    genBtn.disabled = true;
    genBtn.textContent = t('wsx.clips.busy', lang);
    err.textContent = '';
    let failure = '';
    try {
      const res = await api.post('/api/clip', { video: stem, mode: 'highlights', padding: PADDING_SEC });
      if (!alive) return;
      if (res && res.ok && Array.isArray(res.clips)) {
        // The server can answer ok with no clip when every cut failed: say so instead of showing nothing.
        if (!showClips(res.clips)) failure = t('wsx.clips.none', lang);
      } else failure = failureText(lang, res && typeof res.error === 'string' ? res.error : '');
    } catch (e) {
      if (!alive) return;
      const body = e && e.body && typeof e.body.error === 'string' ? e.body.error : '';
      failure = failureText(lang, body || (e && e.messages ? errorText(e, lang) : ''));
    }
    busy = false;
    genBtn.disabled = !rallyOk;
    genBtn.textContent = t('wsx.clips.generate', lang);
    if (failure) err.textContent = failure;
  });

  delBtn.addEventListener('click', () => {
    openDeleteDialog({
      stem, lang, scope: 'clips',
      onClose: (r) => {
        if (!(r && r.deleted)) return;
        if (alive) { list.textContent = ''; err.textContent = ''; }
        if (typeof onDeleted === 'function') onDeleted();
      },
    });
  });

  return { el: root, destroy: () => { alive = false; } };
}
