// Pure logic of the rally-outcome labelling UI (spec 2026-10-27 sections 5.1, 5.2 and 7). Owner labels
// (data/rally_labels/<stem>.json) are keyed by time; `labelForSegment` mirrors badminton_analysis/outcomes/labels.py
// `attach` so the page shows a label on the same segment the backend attaches it to. Near = 'lower', far = 'upper'
// (the convention of every labelling sheet). No DOM here: workspace.js is the view over it.

export const ATTACH_TOLERANCE_SEC = 1.0;
export const SIDE_OF_VERDICT = { lower: 'near', upper: 'far' };
const SIDES = ['upper', 'lower'];

const num = (v) => typeof v === 'number' && Number.isFinite(v);
const arr = (v) => (Array.isArray(v) ? v : []);
const obj = (v) => (v && typeof v === 'object' && !Array.isArray(v) ? v : {});

/** A usable label document ({schema, revision, labels}); anything else reads as the empty one. */
export function normaliseLabels(doc) {
  const d = obj(doc);
  return { schema: 1, revision: Number.isInteger(d.revision) && d.revision >= 0 ? d.revision : 0, labels: arr(d.labels) };
}

/** The segment (of `segments`) a non-added label attaches to: its tolerance window holds the end, nearest end first. */
function bestSegment(label, segments) {
  let best = null;
  for (const seg of segments) {
    if (seg.start - ATTACH_TOLERANCE_SEC <= label.end_sec && label.end_sec <= seg.end + ATTACH_TOLERANCE_SEC) {
      if (best === null || Math.abs(seg.end - label.end_sec) < Math.abs(best.end - label.end_sec)) best = seg;
    }
  }
  return best;
}

const sameSeg = (a, b) => a === b || (a && b && a.id === b.id && a.start === b.start && a.end === b.end);

/**
 * The label that attaches to `seg` (a later list entry wins), or null. `segments` is the whole segment list
 * ({id, start, end}); without it `seg` is the only candidate, so the nearest-end rule cannot see its neighbours.
 */
export function labelForSegment(labelsDoc, seg, segments) {
  if (!seg || !num(seg.start) || !num(seg.end)) return null;
  const all = Array.isArray(segments) && segments.length ? segments : [seg];
  let found = null;
  for (const label of normaliseLabels(labelsDoc).labels) {
    if (!label || label.added || !num(label.end_sec)) continue;
    if (sameSeg(bestSegment(label, all), all.find((s) => sameSeg(s, seg)) || seg)) found = label;
  }
  return found;
}

const keepOutcome = (verdict, outcome) => (verdict === 'upper' || verdict === 'lower' ? outcome || null : null);

/** A new document where `seg`'s label is replaced by this verdict (the old ones on that segment are dropped). */
export function applyVerdict(labelsDoc, seg, verdict, outcome = null, segments) {
  const doc = normaliseLabels(labelsDoc);
  const all = Array.isArray(segments) && segments.length ? segments : [seg];
  const mine = all.find((s) => sameSeg(s, seg)) || seg;
  const labels = doc.labels.filter((label) => {
    if (!label || label.added || !num(label.end_sec)) return true;
    return !sameSeg(bestSegment(label, all), mine);
  });
  labels.push({ start_sec: seg.start, end_sec: seg.end, verdict, outcome: keepOutcome(verdict, outcome), added: false });
  return { ...doc, labels };
}

/** A new document with an added point end at `timeSec`. */
export function addPoint(labelsDoc, timeSec, verdict, outcome = null) {
  const doc = normaliseLabels(labelsDoc);
  return {
    ...doc,
    labels: [...doc.labels, { start_sec: null, end_sec: timeSec, verdict, outcome: keepOutcome(verdict, outcome), added: true }],
  };
}

const stateOfSide = (side) => (side === 'lower' ? 'near' : side === 'upper' ? 'far' : 'unknown');

/**
 * How the page shows one segment: {state: 'near'|'far'|'unknown'|'rejected', source: 'owner'|'auto'|null, outcome,
 * auto: {winner_side, confidence}|null, verdict}. The owner's label (labelsDoc) beats the built file (outcomesDoc)
 * so a click shows at once; an `unsure` verdict keeps the automatic result when there is one.
 */
export function segmentState(outcomesDoc, labelsDoc, seg, segments) {
  const label = labelForSegment(labelsDoc, seg, segments);
  const verdict = label ? label.verdict : null;
  if (verdict === 'not_point') return { state: 'rejected', source: 'owner', outcome: null, auto: null, verdict };
  if (verdict === 'lower' || verdict === 'upper') {
    return { state: stateOfSide(verdict), source: 'owner', outcome: label.outcome || null, auto: null, verdict };
  }
  const entry = arr(obj(outcomesDoc).rallies).find((r) => r && r.segment_id === seg.id) || null;
  const side = entry && SIDES.includes(entry.winner_side) ? entry.winner_side : null;
  if (side && entry.source === 'auto') {
    return {
      state: stateOfSide(side), source: 'auto', outcome: entry.outcome || null, verdict,
      auto: { winner_side: side, confidence: num(entry.confidence) ? entry.confidence : null },
    };
  }
  // An owner entry in the built file that the local labels do not know yet (another tab saved it).
  if (side && entry.source === 'owner' && !label) {
    return { state: stateOfSide(side), source: 'owner', outcome: entry.outcome || null, auto: null, verdict };
  }
  return { state: 'unknown', source: verdict ? 'owner' : null, outcome: null, auto: null, verdict };
}

/** {near, far, labelled, entries} of a built rally_outcomes.json (zeros when there is none). */
export function tally(outcomesDoc) {
  const rallies = arr(obj(outcomesDoc).rallies);
  let near = 0;
  let far = 0;
  for (const r of rallies) {
    if (r && r.winner_side === 'lower') near += 1;
    else if (r && r.winner_side === 'upper') far += 1;
  }
  return { near, far, labelled: near + far, entries: rallies.length };
}

/** The tallies of several parts added up in the primary's frame: a part with swapped sides exchanges near and far. */
export function pooledTally(docs, swappedFlags = []) {
  const out = { near: 0, far: 0, labelled: 0, entries: 0 };
  arr(docs).forEach((doc, i) => {
    const t = tally(doc);
    const swapped = !!arr(swappedFlags)[i];
    out.near += swapped ? t.far : t.near;
    out.far += swapped ? t.near : t.far;
    out.labelled += t.labelled;
    out.entries += t.entries;
  });
  return out;
}

/** The reconciliation status of a built file ('consistent' | 'over' | 'short' | 'incomplete'), or null (hide it). */
export function reconciliationStatus(outcomesDoc) {
  const status = obj(obj(obj(outcomesDoc).summary).reconciliation).status;
  return ['consistent', 'over', 'short', 'incomplete'].includes(status) ? status : null;
}

/** A key as a labelling action: {verdict} for n / f / x / u, {outcome} for 1 / 2 / 3, else null. */
export function keyToAction(key) {
  switch (typeof key === 'string' ? key.toLowerCase() : '') {
    case 'n': return { verdict: 'lower' };
    case 'f': return { verdict: 'upper' };
    case 'x': return { verdict: 'not_point' };
    case 'u': return { verdict: 'unsure' };
    case '1': return { outcome: 'winner' };
    case '2': return { outcome: 'forced_error' };
    case '3': return { outcome: 'unforced_error' };
    default: return null;
  }
}
