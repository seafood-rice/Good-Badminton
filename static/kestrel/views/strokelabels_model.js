// Pure logic of the stroke-correction UI (spec 2026-10-28 sections 5.1, 5.2 and 5.4). Owner labels
// (data/stroke_labels/<stem>.json) are keyed by time; `attach` mirrors badminton_analysis/stroke/owner_labels.py so the
// page shows a label on the hit the backend attaches it to, whether or not strokes.json has been rewritten yet.
// Rows are always computed from the model's own view of strokes.json (any previous overlay undone through the
// model_* fields) plus the labels. Near = 'lower', far = 'upper'. No DOM here: widgets/strokelabels.js is the view.

import { rallyFor } from './workspace_model.js';

export const ATTACH_TOLERANCE_SEC = 0.15;
export const ROW_MARGIN_SEC = 0.25;
export const STROKE_IDS = ['short_serve', 'long_serve', 'clear', 'lift', 'smash', 'drop',
  'net_shot', 'block', 'cross_net', 'net_rush', 'drive', 'push'];
export const STATES = ['auto', 'confirmed', 'corrected', 'not_hit', 'unsure', 'added'];

// Shown on the type buttons. Letters and digits are chosen so no two types share a key.
export const TYPE_KEYS = {
  smash: 'S', clear: 'C', lift: 'L', drop: 'D', net_shot: 'N', block: 'B', net_rush: 'K', drive: 'R', push: 'U',
  cross_net: 'V', short_serve: '1', long_serve: '2',
};
const KEY_TO_TYPE = Object.fromEntries(Object.entries(TYPE_KEYS).map(([id, k]) => [k.toLowerCase(), id]));

const num = (v) => typeof v === 'number' && Number.isFinite(v);
const arr = (v) => (Array.isArray(v) ? v : []);
const obj = (v) => (v && typeof v === 'object' && !Array.isArray(v) ? v : {});
const round3 = (v) => Math.round(v * 1000) / 1000;
const other = (side) => (side === 'upper' ? 'lower' : 'upper');

/** A usable label document ({schema, revision, split, labels}); anything else reads as the empty one. */
export function normaliseLabels(doc) {
  const d = obj(doc);
  return {
    schema: 1,
    revision: Number.isInteger(d.revision) && d.revision >= 0 ? d.revision : 0,
    split: d.split === 'test' ? 'test' : 'train',
    split_locked: d.split_locked === true,
    labels: arr(d.labels).filter((l) => l && typeof l === 'object' && num(l.time_sec)),
  };
}

/** The model's own strokes: owner-added shots dropped, previous overlay undone, removed shots put back. */
function modelStrokes(strokesDoc) {
  const d = obj(strokesDoc);
  const out = [];
  for (const raw of arr(d.strokes)) {
    if (!raw || typeof raw !== 'object' || !num(raw.frame)) continue;
    if (raw.source === 'owner' && !('model_stroke' in raw)) continue;
    const s = { ...raw };
    if ('model_stroke' in s) {
      s.stroke = s.model_stroke;
      s.hitter = 'model_hitter' in s ? s.model_hitter : s.hitter;
    }
    if ('model_attributes' in s) {   // an owner hand overlaid the model's attributes: put them back
      if (s.model_attributes === null) delete s.attributes;
      else s.attributes = s.model_attributes;
    }
    out.push(s);
  }
  for (const raw of arr(d.removed)) if (raw && typeof raw === 'object' && num(raw.frame)) out.push({ ...raw });
  return out.sort((a, b) => a.frame - b.frame);
}

/** {byIndex: Map(stroke index -> label), added: [label]}: each non-added label on its nearest hit (later wins). */
function attach(labels, strokes, fps) {
  const byIndex = new Map();
  const added = [];
  const times = strokes.map((s) => s.frame / fps);
  for (const label of labels) {
    if (label.kind === 'added') {
      // added shots within the tolerance of each other are one shot: the later replaces the earlier (owner_labels.attach)
      const same = added.findIndex((a) => Math.abs(a.time_sec - label.time_sec) <= ATTACH_TOLERANCE_SEC);
      if (same < 0) added.push(label);
      else added[same] = label;
      continue;
    }
    let best = -1;
    let bestGap = ATTACH_TOLERANCE_SEC;
    times.forEach((t, i) => {
      const gap = Math.abs(t - label.time_sec);
      if (gap <= bestGap && (best < 0 || gap < bestGap)) { best = i; bestGap = gap; }
    });
    if (best >= 0) byIndex.set(best, label);
  }
  return { byIndex, added };
}

const modelHand = (s) => (typeof obj(s.attributes).backhand === 'boolean' ? s.attributes.backhand : null);
const ownerHand = (label) => (label && typeof label.backhand === 'boolean' ? label.backhand : undefined);
const validStroke = (id) => (STROKE_IDS.includes(id) ? id : null);

/**
 * Whether a time belongs to the selected rally. With the workspace's rally list (`rallies`) it is the workspace's own
 * rule (workspace_model.rallyFor: inside, else nearest within the pad), so the block and the summary always agree;
 * without it, the old strict window. No rally = every time.
 */
function memberOf(rally, rallies) {
  if (!rally) return () => true;
  if (Array.isArray(rallies) && rallies.length) {
    return (time) => {
      // workspace_model rounds stroke times to 2 decimals before rallyFor; do the same, or a shot on the pad's edge
      // counts in the summary but not here
      const r = rallyFor(Math.round(time * 100) / 100, rallies);
      return !!r && (r === rally || (r.id !== undefined && r.id === rally.id));
    };
  }
  return (time) => time >= rally.start - ROW_MARGIN_SEC && time <= rally.end + ROW_MARGIN_SEC;
}

/**
 * Owner labels no hit carries (the video was re-analysed or the hit is gone): [{time, hitter, stroke, kind, label}] in
 * time order, only those in `rally` (membership as for rowsForRally). `added` labels are shots of their own, never
 * orphans.
 */
export function orphanLabels(strokesDoc, labelsDoc, rally, fps, rallies) {
  if (!num(fps) || fps <= 0) return [];
  const labels = normaliseLabels(labelsDoc).labels;
  const strokes = modelStrokes(strokesDoc);
  // a label is shown on a row only when attach() gave it to one: a second label near the same hit has no row
  const carried = new Set(attach(labels, strokes, fps).byIndex.values());
  const member = memberOf(rally, rallies);
  return labels
    .filter((l) => l.kind !== 'added' && !carried.has(l))
    .filter((l) => member(l.time_sec))
    .map((l) => ({ time: round3(l.time_sec), hitter: l.hitter || null, stroke: typeof l.stroke === 'string' ? l.stroke : null, kind: l.kind, label: l }))
    .sort((a, b) => a.time - b.time);
}

/**
 * The rows of one rally: [{time, frame, hitter, stroke, model_stroke, model_hitter, backhand, model_backhand, confidence,
 * source, state, label}], in time order. `stroke` / `hitter` / `backhand` are the effective ones (the owner's when
 * labelled; `backhand` is true / false / null for unknown, `model_backhand` is the model's own). `state`: auto (model only),
 * confirmed (owner agrees with the model), corrected, not_hit (kept so it can be undone), unsure, added (a missed
 * shot). `label` is the owner label on the row, or null. `rally` is a workspace rally ({id, start, end} in seconds); null
 * gives every row. `rallies` (optional) is the workspace's rally list: membership then follows `rallyFor`.
 */
export function rowsForRally(strokesDoc, labelsDoc, rally, fps, rallies) {
  if (!num(fps) || fps <= 0) return [];
  const labels = normaliseLabels(labelsDoc).labels;
  const strokes = modelStrokes(strokesDoc);
  const { byIndex, added } = attach(labels, strokes, fps);
  const inRally = memberOf(rally, rallies);
  const rows = [];
  strokes.forEach((s, i) => {
    const time = round3(s.frame / fps);
    if (!inRally(time)) return;
    const label = byIndex.get(i) || null;
    const modelStroke = typeof s.stroke === 'string' ? s.stroke : null;
    const base = {
      time, frame: s.frame, hitter: s.hitter, stroke: modelStroke, model_stroke: modelStroke, model_hitter: s.hitter,
      backhand: modelHand(s), model_backhand: modelHand(s),
      confidence: num(s.confidence) ? s.confidence : null, source: 'model', state: 'auto', label,
    };
    if (!label) { rows.push(base); return; }
    if (label.kind === 'not_hit') { rows.push({ ...base, state: 'not_hit' }); return; }
    if (label.kind === 'unsure') { rows.push({ ...base, state: 'unsure' }); return; }
    const hand = ownerHand(label);
    const handDiffers = hand !== undefined && base.model_backhand !== null && hand !== base.model_backhand;
    const same = label.stroke === modelStroke && label.hitter === s.hitter && !handDiffers;
    rows.push({
      ...base, stroke: label.stroke, hitter: label.hitter, backhand: hand === undefined ? base.backhand : hand,
      source: 'owner', state: same ? 'confirmed' : 'corrected',
    });
  });
  for (const label of added) {
    if (!inRally(label.time_sec)) continue;
    rows.push({
      time: round3(label.time_sec), frame: Math.round(label.time_sec * fps), hitter: label.hitter, stroke: label.stroke,
      model_stroke: null, model_hitter: null, backhand: ownerHand(label) === undefined ? null : ownerHand(label),
      model_backhand: null, confidence: null, source: 'owner', state: 'added', label,
    });
  }
  return rows.sort((a, b) => a.time - b.time);
}

const sameLabel = (a, b) => a === b || (!!a && !!b && a.time_sec === b.time_sec && a.hitter === b.hitter
  && a.stroke === b.stroke && a.kind === b.kind && a.backhand === b.backhand);

/**
 * A new document with `action` applied to `row`'s label (the old owner label of that row is replaced). Actions:
 * {type:'set', stroke} | {type:'swap'} | {type:'not_hit'} | {type:'unsure'} | {type:'confirm'} | {type:'clear'} |
 * {type:'hand', backhand} | {type:'hand-toggle'} | {type:'add', time, hitter, stroke}. `hand` writes the shown stroke
 * and hitter with the owner's forehand/backhand; `set`, `swap` and `confirm` keep the row label's own hand. An action that needs a stroke type the row does not have (swap or confirm of an
 * unknown shot) leaves the document as it is.
 */
export function applyAction(labelsDoc, row, action) {
  const doc = normaliseLabels(labelsDoc);
  const a = obj(action);
  if (a.type === 'add') {
    if (!num(a.time) || a.time < 0 || !validStroke(a.stroke) || (a.hitter !== 'upper' && a.hitter !== 'lower')) return doc;
    // adding again at the same moment replaces the earlier added shot instead of stacking copies
    const others = doc.labels.filter((l) => !(l.kind === 'added' && Math.abs(l.time_sec - a.time) <= ATTACH_TOLERANCE_SEC));
    return { ...doc, labels: [...others, { time_sec: round3(a.time), hitter: a.hitter, stroke: a.stroke, kind: 'added' }] };
  }
  const r = obj(row);
  if (!num(r.time)) return doc;
  const rest = doc.labels.filter((l) => !sameLabel(l, r.label));
  const isAdded = r.state === 'added';
  const keep = r.label && (r.label.kind === 'label' || r.label.kind === 'added') ? ownerHand(r.label) : undefined;
  const make = (kind, stroke, hitter, hand = keep) => {
    const label = { time_sec: round3(r.time), hitter, stroke: kind === 'label' || kind === 'added' ? stroke : null, kind };
    if ((kind === 'label' || kind === 'added') && hand !== undefined) label.backhand = hand;
    return label;
  };
  let next = null;
  switch (a.type) {
    case 'set':
      if (!validStroke(a.stroke) || !r.hitter) return doc;
      next = make(isAdded ? 'added' : 'label', a.stroke, r.hitter);
      break;
    case 'swap':
      if (!validStroke(r.stroke) || (r.hitter !== 'upper' && r.hitter !== 'lower')) return doc;
      next = make(isAdded ? 'added' : 'label', r.stroke, other(r.hitter));
      break;
    case 'confirm':
      if (isAdded) return doc;
      if (!validStroke(r.stroke) || !r.hitter) return doc;
      next = make('label', r.stroke, r.hitter);
      break;
    case 'hand':
    case 'hand-toggle': {
      const hand = a.type === 'hand' ? a.backhand : r.backhand !== true;
      if (r.state === 'not_hit' || r.state === 'unsure') return doc;   // no stroke to attach a hand to
      if (typeof hand !== 'boolean' || !validStroke(r.stroke) || !r.hitter) return doc;
      next = make(isAdded ? 'added' : 'label', r.stroke, r.hitter, hand);
      break;
    }
    case 'not_hit':
      next = isAdded ? null : make('not_hit', null, r.hitter);   // an added shot that is no shot just goes away
      break;
    case 'unsure':
      if (isAdded) return doc;
      next = make('unsure', null, r.hitter);
      break;
    case 'clear':
      next = null;
      break;
    default:
      return doc;
  }
  if (next && !next.hitter) return doc;
  return { ...doc, labels: next ? [...rest, next] : rest };
}

/** The action of a key press (type letters and digits as on the buttons, P swap, H forehand/backhand toggle, X not a hit, ? unsure, Enter confirm). */
export function keyToAction(key) {
  if (key === 'Enter') return { type: 'confirm' };
  if (key === '?') return { type: 'unsure' };
  const k = typeof key === 'string' ? key.toLowerCase() : '';
  if (k === 'p') return { type: 'swap' };
  if (k === 'h') return { type: 'hand-toggle' };
  if (k === 'x') return { type: 'not_hit' };
  const id = KEY_TO_TYPE[k];
  return id ? { type: 'set', stroke: id } : null;
}

/**
 * {labelled, total, agree, agreeShare}: of the model's `total` detected shots, how many carry an owner type label,
 * on how many the owner's type equals the model's, and that share (null while nothing is labelled).
 */
export function tally(strokesDoc, labelsDoc, fps) {
  const strokes = modelStrokes(strokesDoc);
  const labels = normaliseLabels(labelsDoc).labels;
  const { byIndex } = num(fps) && fps > 0 ? attach(labels, strokes, fps) : { byIndex: new Map() };
  let labelled = 0;
  let agree = 0;
  for (const [i, label] of byIndex) {
    if (label.kind !== 'label') continue;
    labelled += 1;
    if (label.stroke === strokes[i].stroke) agree += 1;
  }
  return { labelled, total: strokes.length, agree, agreeShare: labelled ? agree / labelled : null };
}
