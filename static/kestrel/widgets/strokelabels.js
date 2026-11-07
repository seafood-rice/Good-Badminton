// Strokes block of the match details page (spec 2026-10-28 section 5.4): one row per detected shot of the selected
// rally, with buttons and keys to set the stroke type, swap the player, mark not-a-hit or unsure, confirm, and add a
// missed shot at the video's current time. Saves go to /api/stroke-labels/<stem>. The row logic is in
// views/strokelabels_model.js. The keys act on the block (focus is in it after a row click), so they never clash with
// the Point block's document-level keys. Everything from the files is set as text, never parsed as HTML.
import { t } from '../core/i18n.js';
import { api as kestrelApi, errorText } from '../core/api.js';
import { h } from './frame.js';
import {
  STROKE_IDS, TYPE_KEYS, normaliseLabels, rowsForRally, orphanLabels, applyAction, keyToAction, tally,
} from '../views/strokelabels_model.js';

const LONG = { timeout: 60000 };
const SEEK_BEFORE_SEC = 0.5;

const defaultApi = {
  get: (stem) => kestrelApi.getStrokeLabels(stem, LONG),
  put: (stem, body) => kestrelApi.putStrokeLabels(stem, body, LONG),
};

const clock = (sec) => {
  const m = Math.floor(sec / 60);
  const s = sec - m * 60;
  return `${m}:${s < 10 ? '0' : ''}${s.toFixed(1)}`;
};
// the labels as a set: applyAction moves a replaced label to the end, which is not a change
const labelKey = (labels) => labels.map((l) => JSON.stringify(l)).sort().join('|');
const isNum = (v) => typeof v === 'number' && Number.isFinite(v);

/**
 * strokeLabelsBlock({stem, lang, api: {get(stem), put(stem, body)}, getPlayer, playerName(side), onSaved(res)}) ->
 * {el, update({strokesDoc, rally, rallies, fps}), load(), focus(), destroy()}. `update` repaints for a new rally or a re-read
 * strokes.json; `onSaved` is told after each successful save (the page re-reads strokes.json when it was applied).
 */
export function strokeLabelsBlock({
  stem, lang = 'en', api = defaultApi, getPlayer = () => null, playerName = null, onSaved = null,
} = {}) {
  let alive = true;
  let labelsDoc = normaliseLabels(null);
  let props = { strokesDoc: null, rally: null, rallies: null, fps: null };
  let selected = null; // the selected row's time
  let paintedSelected = null; // the selection at the last paint: scroll only when it changed
  let paintedScope; // the rally id (or 'all') at the last paint: the list keeps its scroll while this is unchanged
  let listEl = null; // the shot list of the last paint
  let listScroll = 0; // its last scroll position, kept by its scroll events (a detached list reads 0)
  let notice = '';
  let saving = false;
  let addPrompt = null; // {time, hitter}

  const el = h('div', { class: 'ws-sl', role: 'group', 'aria-label': t('strokes.aria', lang), tabindex: '-1' });

  const side = (s) => (playerName ? playerName(s) : '') || t(s === 'lower' ? 'strokes.near' : 'strokes.far', lang);
  const typeText = (id) => (id && id !== 'unknown' ? t('stroke.' + id, lang) : t('strokes.unknown', lang));
  const rows = () => rowsForRally(props.strokesDoc, labelsDoc, props.rally, props.fps, props.rallies);
  const orphans = () => orphanLabels(props.strokesDoc, labelsDoc, props.rally, props.fps, props.rallies);

  function seekToRow(row) {
    const player = getPlayer();
    if (!player || !isNum(row.time)) return;
    if (typeof player.seekTo === 'function') player.seekTo(Math.max(0, row.time - SEEK_BEFORE_SEC), false);
    const video = player.video;
    if (video && typeof video.pause === 'function') video.pause();
  }

  // A repaint replaces the clicked button, so focus goes back to the block: the keys listen on it.
  function focusBlock() {
    if (typeof el.focus === 'function') el.focus({ preventScroll: true });
  }

  function select(row, seek) {
    selected = row ? row.time : null;
    if (row && seek) seekToRow(row);
    paint();
    focusBlock();
  }

  /** Select the row after `time` (seeking to it); false when there is none. */
  function selectAfter(time) {
    const next = rows().find((r) => r.time > time);
    if (!next) return false;
    select(next, true);
    return true;
  }

  async function reloadLabels() {
    try {
      labelsDoc = normaliseLabels(await api.get(stem));
    } catch (err) {
      // keep what is shown; the next save will say what is wrong
    }
  }

  /**
   * Save `doc` (the labels with an action applied); then move on to the next row when `advance`. `addedAt` is the
   * time of a shot just added: it is selected and confirmed, or, when it lies outside the shown rally, the notice says
   * where to find it (otherwise an add looks like it did nothing, and a retry used to stack copies).
   */
  async function save(doc, advance, addedAt = null) {
    if (saving || !alive) return;
    const rowsBefore = rows();
    const at = rowsBefore.findIndex((r) => r.time === selected);
    saving = true;
    notice = '';
    let saved = false;
    try {
      const res = await api.put(stem, { revision: doc.revision, split: doc.split, labels: doc.labels });
      if (!alive) return;
      const revision = res && Number.isInteger(res.revision) ? res.revision : doc.revision + 1;
      labelsDoc = { ...doc, revision };
      saved = true;
      if (res && res.apply_skipped) {
        const key = 'strokes.skipped.' + (['job_active', 'no_strokes'].includes(res.apply_skipped) ? res.apply_skipped : 'other');
        notice = t(key, lang);
      }
      if (onSaved) onSaved(res || {});
    } catch (err) {
      if (!alive) return;
      if (err && err.status === 409) {
        const body = err.body && typeof err.body === 'object' ? err.body : {};
        await reloadLabels();
        if (body.reason === 'unreadable') notice = t('strokes.unreadable', lang);
        else if (body.reason === 'split_locked') notice = t('strokes.split_locked', lang);
        else notice = t('strokes.conflict', lang);
      } else if (!(err && err.kind === 'aborted')) {
        notice = t('strokes.save_failed', lang, { error: errorText(err, lang) });
      }
    } finally {
      saving = false;
    }
    if (!alive) return;
    addPrompt = null;
    if (saved && isNum(addedAt) && !notice) {
      const row = rows().find((r) => r.state === 'added' && Math.abs(r.time - addedAt) < 0.002);
      if (row) {
        selected = row.time;
        notice = t('strokes.added', lang, { time: clock(row.time) });
      } else {
        notice = t('strokes.added_outside', lang, { time: clock(addedAt) });
      }
    }
    if (saved && advance && at >= 0) {
      const next = rows().find((r) => r.time > rowsBefore[at].time);
      if (next) {
        selected = next.time;
        seekToRow(next);
      }
    }
    paint();
    focusBlock();
  }

  function act(row, action, advance = true) {
    if (!row) return;
    const before = labelKey(labelsDoc.labels);
    const doc = applyAction(labelsDoc, row, action);
    // an action the row cannot take (e.g. swapping an unknown shot) changes nothing: no save
    if (labelKey(doc.labels) === before) {
      if (action.type === 'confirm' && ['confirmed', 'corrected'].includes(row.state)) selectAfter(row.time);   // just move on
      return;
    }
    save(doc, advance && !['swap', 'clear', 'hand', 'hand-toggle'].includes(action.type));
  }

  function openAdd() {
    const player = getPlayer();
    const now = player && player.video && isNum(player.video.currentTime) ? player.video.currentTime : 0;
    addPrompt = { time: Math.round(now * 1000) / 1000, hitter: 'lower' };
    notice = '';
    paint();
  }

  function setSplit(split) {
    if (labelsDoc.split === split || labelsDoc.split_locked) return;
    save({ ...labelsDoc, split }, false);
  }

  function onKey(e) {
    if (!alive || e.ctrlKey || e.metaKey || e.altKey || e.repeat) return;
    const tg = e.target;
    if (tg && (tg.isContentEditable || ['TEXTAREA', 'SELECT', 'INPUT'].includes(tg.tagName))) return;
    const action = keyToAction(e.key);
    const row = rows().find((r) => r.time === selected);
    if (!action || !row || addPrompt) return;
    e.preventDefault();
    if (typeof e.stopPropagation === 'function') e.stopPropagation();   // the Point block's document keys stay out of it
    act(row, action);
  }
  el.addEventListener('keydown', onKey);

  const stateChip = (row) => h('span', { class: `k-chip ws-sl-state st-${row.state}` }, t('strokes.state.' + row.state, lang));

  const handText = (b) => t(b ? 'strokes.hand.bh' : 'strokes.hand.fh', lang);

  function rowNode(row) {
    const on = row.time === selected;
    const dim = row.state === 'not_hit' || row.state === 'unsure';
    const type = typeText(row.stroke);
    const btn = h('button', {
      type: 'button', class: 'ws-sl-row' + (on ? ' on' : '') + (dim ? ' dim' : ''), 'data-time': String(row.time),
      'data-state': row.state, 'aria-pressed': on ? 'true' : 'false',
      'aria-label': t('strokes.row_aria', lang, { time: clock(row.time), player: side(row.hitter), type }),
      onclick: () => select(row, true),
    }, [
      h('span', { class: 'mono ws-sl-time' }, clock(row.time)),
      h('span', { class: 'ws-sl-player' }, row.hitter ? side(row.hitter) : '—'),
      h('span', { class: 'ws-sl-type' + (row.state === 'not_hit' ? ' struck' : '') }, type),
      row.backhand === null ? null : h('span', {
        class: 'k-chip ws-sl-hand' + (row.source === 'owner' && row.label && typeof row.label.backhand === 'boolean' ? ' owner' : ''),
        'data-hand': row.backhand ? 'backhand' : 'forehand',
        'aria-label': t('strokes.hand.aria', lang, { hand: t(row.backhand ? 'strokes.hand.backhand' : 'strokes.hand.forehand', lang) }),
      }, handText(row.backhand)),
      stateChip(row),
      row.state === 'corrected' ? h('span', { class: 'kw-muted ws-sl-was' }, t('strokes.model_was', lang, { type: typeText(row.model_stroke) })) : null,
      row.confidence !== null && row.state === 'auto' ? h('span', { class: 'mono kw-muted ws-sl-conf' }, `${Math.round(row.confidence * 100)}%`) : null,
    ]);
    return btn;
  }

  // An owner label no shot carries any more: shown so it can be seen and cleared.
  function orphanNode(o) {
    const what = o.stroke ? typeText(o.stroke) : t('strokes.state.' + (o.kind === 'not_hit' ? 'not_hit' : 'unsure'), lang);
    return h('div', { class: 'ws-sl-row ws-sl-orphan dim', 'data-time': String(o.time), 'data-state': 'orphaned' }, [
      h('span', { class: 'mono ws-sl-time' }, clock(o.time)),
      h('span', { class: 'ws-sl-player' }, o.hitter ? side(o.hitter) : '—'),
      h('span', { class: 'ws-sl-type' }, what),
      h('span', { class: 'k-chip ws-sl-state st-orphaned' }, t('strokes.state.orphaned', lang)),
      h('button', {
        type: 'button', class: 'ws-chip ws-pt-btn ws-sl-orphan-clear', 'data-action': 'clear-orphan',
        onclick: () => act({ time: o.time, label: o.label, state: 'orphaned' }, { type: 'clear' }, false),
      }, t('strokes.clear', lang)),
    ]);
  }

  function typeButtons(onPick, current) {
    return h('div', { class: 'ws-sl-types', role: 'group' }, STROKE_IDS.map((id) => h('button', {
      type: 'button', class: 'ws-chip ws-sl-type-btn' + (current === id ? ' on' : ''), 'data-stroke': id,
      'aria-pressed': current === id ? 'true' : 'false', onclick: () => onPick(id),
    }, [typeText(id), ' ', h('span', { class: 'mono ws-sl-key' }, TYPE_KEYS[id])])));
  }

  function tallyNode() {
    const tl = tally(props.strokesDoc, labelsDoc, props.fps);
    if (!tl.labelled) return h('div', { class: 'ws-sl-tally kw-foot-muted' }, t('strokes.tally_none', lang, { total: tl.total }));
    return h('div', { class: 'ws-sl-tally kw-foot-muted' }, t('strokes.tally', lang, {
      labelled: tl.labelled, total: tl.total, share: `${Math.round(tl.agreeShare * 100)}%`,
    }));
  }

  function paint() {
    if (!alive) return;
    // a repaint rebuilds the list: carry its scroll over, unless another rally (or All shots) is now shown
    const scope = props.rally && props.rally.id !== undefined ? props.rally.id : 'all';
    const keepScroll = scope === paintedScope ? listScroll : 0;
    listScroll = keepScroll;
    listEl = null;
    paintedScope = scope;
    el.textContent = '';
    const locked = labelsDoc.split_locked;
    el.appendChild(h('div', { class: 'ws-sec-head' }, [
      h('div', { class: 'k-label' }, t('strokes.title', lang)),
      h('div', { class: 'ws-sl-split', role: 'group', 'aria-label': t('strokes.split', lang), title: locked ? t('strokes.split_locked', lang) : null },
        [['train'], ['test']].map(([id]) => h('button', {
          type: 'button', class: 'ws-chip ws-sl-split-btn' + (labelsDoc.split === id ? ' on' : ''), 'data-split': id,
          'aria-pressed': labelsDoc.split === id ? 'true' : 'false', disabled: locked && labelsDoc.split !== id ? true : null,
          onclick: () => setSplit(id),
        }, t('strokes.split.' + id, lang)))),
    ]));
    const list = rows();
    const lost = orphans();
    if (!list.length && !lost.length) {
      el.appendChild(h('div', { class: 'kw-note ws-sl-empty' }, t(props.rally ? 'strokes.empty' : 'strokes.empty_all', lang)));
    } else {
      listEl = h('div', { class: 'ws-sl-list' }, [...list.map(rowNode), ...lost.map(orphanNode)]);
      el.appendChild(listEl);
      const mine = listEl;
      mine.addEventListener('scroll', () => { if (mine === listEl && mine.isConnected !== false) listScroll = mine.scrollTop; });
      mine.scrollTop = keepScroll;
      // the workspace may move this block into a new card right after the repaint, which drops the scroll: put it back
      if (keepScroll && typeof requestAnimationFrame === 'function') {
        requestAnimationFrame(() => { if (mine === listEl && alive && mine.scrollTop !== keepScroll) mine.scrollTop = keepScroll; });
      }
      const on = el.querySelector && el.querySelector('.ws-sl-row.on');
      if (selected !== paintedSelected && on && typeof on.scrollIntoView === 'function') on.scrollIntoView({ block: 'nearest' });
    }
    paintedSelected = selected;
    const row = list.find((r) => r.time === selected);
    if (row && !addPrompt) {
      el.appendChild(h('div', { class: 'ws-sl-actions' }, [
        typeButtons((id) => act(row, { type: 'set', stroke: id }), row.state === 'not_hit' || row.state === 'unsure' ? null : row.stroke),
        h('div', { class: 'ws-pt-row ws-sl-more' }, [
          ['confirm', 'strokes.confirm'], ['hand:false', 'strokes.hand.forehand'], ['hand:true', 'strokes.hand.backhand'], ['swap', 'strokes.swap'], ['not_hit', 'strokes.not_hit'], ['unsure', 'strokes.unsure'],
          ...(row.label ? [['clear', 'strokes.clear']] : []),
        ].map(([type, key]) => {
          const hand = type.startsWith('hand:') ? type.slice(5) === 'true' : null;
          const on = hand !== null && row.backhand === hand;
          return h('button', {
            type: 'button', class: 'ws-chip ws-pt-btn' + (on ? ' on' : ''), 'data-action': hand === null ? type : (hand ? 'backhand' : 'forehand'),
            'aria-pressed': hand === null ? null : (on ? 'true' : 'false'),
            onclick: () => act(row, hand === null ? { type } : { type: 'hand', backhand: hand }),
          }, t(key, lang));
        })),
      ]));
    }
    if (addPrompt) {
      el.appendChild(h('div', { class: 'ws-pt ws-pt-addbox ws-sl-addbox', role: 'group', 'aria-label': t('strokes.add', lang) }, [
        h('div', { class: 'k-label' }, t('strokes.add_prompt', lang, { time: clock(addPrompt.time) })),
        h('div', { class: 'ws-pt-row' }, ['lower', 'upper'].map((s) => h('button', {
          type: 'button', class: 'ws-chip ws-pt-btn' + (addPrompt.hitter === s ? ' on' : ''), 'data-hitter': s,
          'aria-pressed': addPrompt.hitter === s ? 'true' : 'false',
          onclick: () => { addPrompt = { ...addPrompt, hitter: s }; paint(); },
        }, side(s)))),
        typeButtons((id) => save(applyAction(labelsDoc, null, { type: 'add', time: addPrompt.time, hitter: addPrompt.hitter, stroke: id }), false, addPrompt.time), null),
        h('button', { type: 'button', class: 'ws-chip ws-pt-btn', 'data-add': 'cancel', onclick: () => { addPrompt = null; paint(); } }, t('strokes.add_cancel', lang)),
      ]));
    } else {
      el.appendChild(h('button', { type: 'button', class: 'ws-pt-add ws-sl-add', onclick: openAdd }, t('strokes.add', lang)));
    }
    el.appendChild(tallyNode());
    if (notice) el.appendChild(h('div', { class: 'ws-pt-notice', role: 'status' }, notice));
    el.appendChild(h('div', { class: 'kw-foot-muted' }, t('strokes.keys', lang)));
  }

  paint();

  return {
    el,
    update(next = {}) {
      props = { ...props, ...next };
      if (selected !== null && !rows().some((r) => r.time === selected)) selected = null;
      addPrompt = null;
      paint();
    },
    async load() {
      await reloadLabels();
      paint();
    },
    focus: focusBlock,
    destroy() { alive = false; },
  };
}
