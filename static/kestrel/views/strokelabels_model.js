// Pure logic of the stroke-correction UI (spec 2026-10-28 sections 5.1, 5.2 and 5.4). Owner labels
// (data/stroke_labels/<stem>.json) are keyed by time; `attach` mirrors badminton_analysis/stroke/owner_labels.py so the
// page shows a label on the hit the backend attaches it to, whether or not strokes.json has been rewritten yet.
// Rows are always computed from the model's own view of strokes.json (any previous overlay undone through the
// model_* fields) plus the labels. Near = 'lower', far = 'upper'. No DOM here: widgets/strokelabels.js is the view.

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
    if (label.kind === 'added') { added.push(label); continue; }
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

const validStroke = (id) => (STROKE_IDS.includes(id) ? id : null);

/**
 * The rows of one rally: [{time, frame, hitter, stroke, model_stroke, model_hitter, confidence, source, state, label}],
 * in time order. `stroke` / `hitter` are the effective ones (the owner's when labelled). `state`: auto (model only),
 * confirmed (owner agrees with the model), corrected, not_hit (kept so it can be undone), unsure, added (a missed
 * shot). `label` is the owner label on the row, or null. `rally` is {start, end} in seconds; null gives every row.
 */
export function rowsForRally(strokesDoc, labelsDoc, rally, fps) {
  if (!num(fps) || fps <= 0) return [];
  const labels = normaliseLabels(labelsDoc).labels;
  const strokes = modelStrokes(strokesDoc);
  const { byIndex, added } = attach(labels, strokes, fps);
  const inRally = (time) => !rally || (time >= rally.start - ROW_MARGIN_SEC && time <= rally.end + ROW_MARGIN_SEC);
  const rows = [];
  strokes.forEach((s, i) => {
    const time = round3(s.frame / fps);
    if (!inRally(time)) return;
    const label = byIndex.get(i) || null;
    const modelStroke = typeof s.stroke === 'string' ? s.stroke : null;
    const base = {
      time, frame: s.frame, hitter: s.hitter, stroke: modelStroke, model_stroke: modelStroke, model_hitter: s.hitter,
      confidence: num(s.confidence) ? s.confidence : null, source: 'model', state: 'auto', label,
    };
    if (!label) { rows.push(base); return; }
    if (label.kind === 'not_hit') { rows.push({ ...base, state: 'not_hit' }); return; }
    if (label.kind === 'unsure') { rows.push({ ...base, state: 'unsure' }); return; }
    const same = label.stroke === modelStroke && label.hitter === s.hitter;
    rows.push({ ...base, stroke: label.stroke, hitter: label.hitter, source: 'owner', state: same ? 'confirmed' : 'corrected' });
  });
  for (const label of added) {
    if (!inRally(label.time_sec)) continue;
    rows.push({
      time: round3(label.time_sec), frame: Math.round(label.time_sec * fps), hitter: label.hitter, stroke: label.stroke,
      model_stroke: null, model_hitter: null, confidence: null, source: 'owner', state: 'added', label,
    });
  }
  return rows.sort((a, b) => a.time - b.time);
}

const sameLabel = (a, b) => a === b || (!!a && !!b && a.time_sec === b.time_sec && a.hitter === b.hitter
  && a.stroke === b.stroke && a.kind === b.kind);

/**
 * A new document with `action` applied to `row`'s label (the old owner label of that row is replaced). Actions:
 * {type:'set', stroke} | {type:'swap'} | {type:'not_hit'} | {type:'unsure'} | {type:'confirm'} | {type:'clear'} |
 * {type:'add', time, hitter, stroke}. An action that needs a stroke type the row does not have (swap or confirm of an
 * unknown shot) leaves the document as it is.
 */
export function applyAction(labelsDoc, row, action) {
  const doc = normaliseLabels(labelsDoc);
  const a = obj(action);
  if (a.type === 'add') {
    if (!num(a.time) || a.time < 0 || !validStroke(a.stroke) || (a.hitter !== 'upper' && a.hitter !== 'lower')) return doc;
    return { ...doc, labels: [...doc.labels, { time_sec: round3(a.time), hitter: a.hitter, stroke: a.stroke, kind: 'added' }] };
  }
  const r = obj(row);
  if (!num(r.time)) return doc;
  const rest = doc.labels.filter((l) => !sameLabel(l, r.label));
  const isAdded = r.state === 'added';
  const make = (kind, stroke, hitter) => ({ time_sec: round3(r.time), hitter, stroke: kind === 'label' || kind === 'added' ? stroke : null, kind });
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

/** The action of a key press (type letters and digits as on the buttons, P swap, X not a hit, ? unsure, Enter confirm). */
export function keyToAction(key) {
  if (key === 'Enter') return { type: 'confirm' };
  if (key === '?') return { type: 'unsure' };
  const k = typeof key === 'string' ? key.toLowerCase() : '';
  if (k === 'p') return { type: 'swap' };
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
