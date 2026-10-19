// Players view (#/players, spec 4 and 6.5): athletes and squads CRUD over /api/athletes and /api/squads,
// grouped by squad. Duplicate names (409) and invalid ones (400) are shown inline with the server's bilingual
// message. groupBySquad() is pure and tested.
import { t } from '../core/i18n.js';
import { fmtNumber } from '../core/format.js';
import { api, errorText } from '../core/api.js';
import { h, hasDOM } from '../widgets/frame.js';

const obj = (v) => (v && typeof v === 'object' && !Array.isArray(v) ? v : {});
const nameOf = (x) => (x && typeof x.name === 'string' ? x.name : '');
const byName = (a, b) => {
  const x = nameOf(a).toLocaleLowerCase();
  const y = nameOf(b).toLocaleLowerCase();
  return x < y ? -1 : x > y ? 1 : String(a.id).localeCompare(String(b.id));
};

/**
 * Pure: [{squad: {id, name, ...} | null, athletes: [...]}]. Squads (empty ones too) sorted by name
 * case-insensitively; athletes sorted the same way inside each; athletes without a squad, or whose squad no
 * longer exists, go under a final null squad (present only when there are such athletes).
 */
export function groupBySquad(athletes, squads) {
  const sq = (Array.isArray(squads) ? squads : []).filter((s) => s && typeof s.id === 'string').slice().sort(byName);
  const list = (Array.isArray(athletes) ? athletes : []).filter((a) => a && typeof a.id === 'string');
  const known = new Set(sq.map((s) => s.id));
  const groups = sq.map((s) => ({ squad: s, athletes: list.filter((a) => a.squad_id === s.id).sort(byName) }));
  const loose = list.filter((a) => !known.has(a.squad_id)).sort(byName);
  if (loose.length) groups.push({ squad: null, athletes: loose });
  return groups;
}

/** Mount the players view. Returns {destroy()}. */
export function mount(container, ctx = {}) {
  const noop = { destroy() {} };
  if (!container || !hasDOM()) return noop;
  const lang = ctx.lang === 'en' ? 'en' : 'zh';
  const state = { data: null, error: null, destroyed: false, renaming: null, busy: false };
  const root = h('div', { class: 'page pl', 'data-view': 'players' });
  container.appendChild(root);

  const toast = (key) => { if (typeof ctx.toast === 'function') ctx.toast(t(key, lang)); };

  function focusKey(key) {
    if (!key) return;
    const safe = String(key).replace(/["\\]/g, '\\$&');
    const node = root.querySelector(`[data-k="${safe}"]`);
    if (node && typeof node.focus === 'function') node.focus();
  }

  async function reload(focus, refreshShell = false) {
    try {
      const data = await api.athletes({ timeout: 15000 });
      if (state.destroyed) return;
      state.data = {
        athletes: Array.isArray(obj(data).athletes) ? data.athletes : [],
        squads: Array.isArray(obj(data).squads) ? data.squads : [],
      };
      state.error = null;
    } catch (e) {
      if (state.destroyed) return;
      state.error = e;
    }
    render();
    focusKey(focus);
    // Keep the shell's pickers and palette in step with the edit.
    if (refreshShell && typeof ctx.refreshAthletes === 'function') ctx.refreshAthletes();
  }

  /** Run a mutation; on failure show the server message in `errEl` and keep the form as typed. */
  async function mutate(fn, errEl, okKey, focus) {
    if (state.busy) return false;
    state.busy = true;
    if (errEl) errEl.textContent = '';
    try {
      await fn();
    } catch (e) {
      state.busy = false;
      if (state.destroyed) return false;
      if (errEl) errEl.textContent = errorText(e, lang);
      return false;
    }
    state.busy = false;
    if (state.destroyed) return true;
    state.renaming = null;
    if (okKey) toast(okKey);
    await reload(focus, true);
    return true;
  }

  function addForm(kind) {
    const isAthlete = kind === 'athlete';
    const id = `pl-new-${kind}`;
    const input = h('input', { type: 'text', class: 'mv-input', id, 'data-k': id, maxlength: '60', placeholder: t(isAthlete ? 'players.name_placeholder' : 'players.squad_placeholder', lang) });
    const err = h('span', { class: 'mv-err', role: 'alert', id: id + '-err' });
    input.setAttribute('aria-describedby', id + '-err');
    const squads = obj(state.data).squads || [];
    const sel = isAthlete ? h('select', { class: 'mv-input', 'aria-label': t('players.squad', lang) }, [
      h('option', { value: '' }, t('players.no_squad', lang)),
      ...squads.map((s) => h('option', { value: s.id }, nameOf(s))),
    ]) : null;
    const form = h('form', { class: 'pl-add', novalidate: true }, [
      h('label', { for: id, class: 'k-label' }, t(isAthlete ? 'players.add_athlete' : 'players.add_squad', lang)),
      h('div', { class: 'pl-add-row' }, [input, sel, h('button', { type: 'submit', class: 'ov-btn small primary' }, t('btn.add', lang))]),
      err,
    ]);
    form.addEventListener('submit', (e) => {
      e.preventDefault();
      const name = input.value.trim();
      if (!name) {
        err.textContent = t('record.err.name', lang);
        input.focus();
        return;
      }
      const body = isAthlete ? { name, ...(sel && sel.value ? { squad_id: sel.value } : {}) } : { name };
      mutate(() => (isAthlete ? api.createAthlete(body) : api.createSquad(body)), err, 'toast.saved', id);
    });
    return form;
  }

  function renameControl(kind, item) {
    const key = `${kind}:${item.id}`;
    const err = h('span', { class: 'mv-err', role: 'alert' });
    if (state.renaming !== key) {
      return {
        el: h('button', {
          type: 'button', class: 'ov-btn small', 'data-k': 'rn-' + key, 'aria-label': t('players.rename_aria', lang, { name: nameOf(item) }),
          onclick: () => { state.renaming = key; render(); focusKey('rn-in-' + key); },
        }, t('btn.rename', lang)),
      };
    }
    const input = h('input', { type: 'text', class: 'mv-input', maxlength: '60', value: nameOf(item), 'data-k': 'rn-in-' + key,
      'aria-label': t('players.rename_aria', lang, { name: nameOf(item) }) });
    const form = h('form', { class: 'pl-rename', novalidate: true }, [
      input,
      h('button', { type: 'submit', class: 'ov-btn small primary' }, t('btn.save', lang)),
      h('button', { type: 'button', class: 'ov-btn small', onclick: () => { state.renaming = null; render(); focusKey('rn-' + key); } }, t('btn.cancel', lang)),
      err,
    ]);
    input.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') { e.preventDefault(); state.renaming = null; render(); focusKey('rn-' + key); }
    });
    form.addEventListener('submit', (e) => {
      e.preventDefault();
      const name = input.value.trim();
      if (!name) { err.textContent = t('record.err.name', lang); return; }
      if (name === nameOf(item)) { state.renaming = null; render(); focusKey('rn-' + key); return; }
      mutate(() => (kind === 'athlete' ? api.updateAthlete(item.id, { name }) : api.updateSquad(item.id, { name })), err, 'toast.saved', 'rn-' + key);
    });
    return { el: form };
  }

  function deleteButton(kind, item) {
    const err = h('span', { class: 'mv-err', role: 'alert' });
    const btn = h('button', {
      type: 'button', class: 'ov-btn small danger', 'aria-label': t('players.delete_aria', lang, { name: nameOf(item) }),
      onclick: () => {
        const msg = t(kind === 'athlete' ? 'players.delete_confirm' : 'players.squad_delete_confirm', lang, { name: nameOf(item) });
        if (typeof confirm === 'function' && !confirm(msg)) return;
        mutate(() => (kind === 'athlete' ? api.deleteAthlete(item.id) : api.deleteSquad(item.id)), err, 'toast.deleted', kind === 'athlete' ? 'pl-new-athlete' : 'pl-new-squad');
      },
    }, t('btn.delete', lang));
    return h('span', { class: 'pl-del' }, [btn, err]);
  }

  function athleteRow(a, squads) {
    const err = h('span', { class: 'mv-err', role: 'alert' });
    const sel = h('select', { class: 'mv-input small', 'data-k': 'sq-' + a.id, 'aria-label': t('players.squad_aria', lang, { name: nameOf(a) }) }, [
      h('option', { value: '' }, t('players.no_squad', lang)),
      ...squads.map((s) => h('option', { value: s.id }, nameOf(s))),
    ]);
    sel.value = squads.some((s) => s.id === a.squad_id) ? a.squad_id : '';
    sel.addEventListener('change', () => {
      mutate(() => api.updateAthlete(a.id, { squad_id: sel.value || null }), err, 'toast.saved', 'sq-' + a.id);
    });
    const rename = renameControl('athlete', a);
    const renaming = state.renaming === `athlete:${a.id}`;
    return h('li', { class: 'pl-row' }, [
      h('span', { class: 'tb-avatar', 'aria-hidden': 'true' }, nameOf(a).trim().charAt(0).toUpperCase() || '?'),
      renaming ? rename.el : h('span', { class: 'pl-name' }, nameOf(a)),
      h('span', { class: 'mono kw-muted pl-count' }, t('players.matches_count', lang, { n: fmtNumber(a.matches || 0) })),
      sel,
      renaming ? null : rename.el,
      deleteButton('athlete', a),
      err,
    ]);
  }

  function render() {
    if (state.destroyed) return;
    root.textContent = '';
    const d = state.data;
    root.appendChild(h('div', { class: 'ov-head' }, h('div', { class: 'ov-head-l' }, [
      h('div', { class: 'mono ov-eyebrow' }, t('players.eyebrow', lang)),
      h('h1', { class: 'page-title serif' }, t('players.title', lang)),
      d ? h('div', { class: 'ov-summary' }, t('players.sub', lang, { athletes: fmtNumber(d.athletes.length), squads: fmtNumber(d.squads.length) })) : null,
    ])));
    if (state.error) {
      root.appendChild(h('div', { class: d ? 'ov-alert' : 'card ov-state', role: 'alert' }, [
        h('strong', null, t(d ? 'error.refresh.title' : 'error.load.title', lang)),
        h('span', null, errorText(state.error, lang)),
        h('button', { type: 'button', class: 'ov-btn small', onclick: () => reload() }, t('btn.retry', lang)),
      ]));
    }
    if (!d) {
      if (!state.error) root.appendChild(h('div', { class: 'card ov-state', role: 'status', 'aria-busy': 'true' }, h('div', { class: 'k-label' }, t('state.loading', lang))));
      return;
    }
    root.appendChild(h('section', { class: 'card hud pl-forms', 'aria-label': t('players.add_athlete', lang) }, [addForm('athlete'), addForm('squad')]));
    if (!d.athletes.length && !d.squads.length) {
      root.appendChild(h('div', { class: 'card ov-state ov-empty' }, [
        h('div', { class: 'ov-empty-title' }, t('players.empty.title', lang)),
        h('div', { class: 'ov-state-body' }, t('players.empty.body', lang)),
      ]));
      return;
    }
    const squads = d.squads.slice().sort(byName);
    for (const g of groupBySquad(d.athletes, d.squads)) {
      const title = g.squad ? nameOf(g.squad) : t('players.no_squad', lang);
      const headBits = [
        h('div', { class: 'kw-titles' }, [
          h('h2', { class: 'card-title' }, title),
          h('div', { class: 'card-sub' }, g.squad ? t('players.athletes_count', lang, { n: fmtNumber(g.athletes.length) }) : t('players.unassigned_hint', lang)),
        ]),
      ];
      if (g.squad) {
        const rn = renameControl('squad', g.squad);
        headBits.push(h('div', { class: 'pl-actions' }, [rn.el, deleteButton('squad', g.squad)]));
      }
      const list = h('ul', { class: 'pl-list' }, g.athletes.map((a) => athleteRow(a, squads)));
      root.appendChild(h('section', { class: 'card hud pl-group', 'aria-label': title }, [
        h('div', { class: 'kw-head' }, headBits),
        g.athletes.length ? list : h('div', { class: 'kw-note' }, t('players.squad_empty', lang)),
      ]));
    }
  }

  render();
  reload();

  return {
    destroy() {
      if (state.destroyed) return;
      state.destroyed = true;
      if (root.parentNode) root.parentNode.removeChild(root);
    },
  };
}
