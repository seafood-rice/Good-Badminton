// Pure logic of the match workspace (#/matches/<stem>): no DOM, no network, so it imports and tests under plain
// node. buildWorkspaceModel() joins the rally, stroke and technique files into one model; the other helpers are
// the text and data helpers the DOM layer in workspace.js (and the match details page) share. Honesty rules: there
// is no point outcome, so nothing is marked won or lost; swing segments are "segments (estimated)", degraded ones
// "windows"; uncertain strokes are muted and labelled so; opponent strokes are shown but never scored.
import { t, TECHNIQUE_IDS, STROKE_TYPE_IDS, STROKE_GROUP } from '../core/i18n.js';
import { fmtDuration, fmtNumber, isApproxDate } from '../core/format.js';

export const SIDES = ['upper', 'lower'];
// Technique reports written before the unified classifier name a profile, not a stroke type.
const LEGACY_PROFILE = Object.freeze({ high_clear: 'clear', drop_shot: 'drop', smash: 'smash', serve: 'serve' });
/** A stroke belongs to a rally when it falls in [start - 0.5, end + 0.5] (the server's shots_per_rally rule). */
export const RALLY_PAD_SEC = 0.5;
/** A technique report joins a stroke within this many frames of its contact frame (same side only). */
export const JOIN_FRAMES = 3;
const DEFAULT_REF = Object.freeze({ lo: 70, hi: 100 });

export const isNum = (v) => typeof v === 'number' && Number.isFinite(v);
export const obj = (v) => (v && typeof v === 'object' && !Array.isArray(v) ? v : {});
const round2 = (x) => Math.round(x * 100) / 100;
export const num1 = (v) => String(+Number(v).toFixed(1));

/**
 * What a parsed rally_segments.json may honestly claim (mirror of badminton_analysis.insights.rally
 * .summarise_rally_payload): {count, suppressed, label, signal, degraded, failed}. `null` = no file.
 */
export function summariseRallyPayload(payload) {
  const unknown = { count: 0, suppressed: false, label: 'rallies', signal: null, degraded: false, failed: false };
  if (payload === null || payload === undefined) return unknown;
  if (typeof payload !== 'object' || Array.isArray(payload)) return { ...unknown, failed: true };
  const rallies = payload.rallies === undefined ? [] : payload.rallies;
  if (!Array.isArray(rallies)) return { ...unknown, failed: true };
  const det = obj(payload.detection);
  const signal = det.signal === undefined ? null : det.signal;
  if (signal === 'error') return { ...unknown, failed: true };
  const degraded = !!det.degraded;
  const out = { count: rallies.length, suppressed: false, label: 'rallies', signal, degraded, failed: false };
  if (degraded || signal === 'none') Object.assign(out, { suppressed: true, label: 'windows' });
  else if (signal === 'swing') Object.assign(out, { suppressed: true, label: 'segments' });
  return out;
}

function summaryFromInsights(insights) {
  const ins = obj(obj(insights).rally);
  const v = obj(ins.value);
  if (ins.status === 'unavailable' || !['rallies', 'segments', 'windows'].includes(v.label)) return null;
  return {
    count: isNum(v.count) ? v.count : 0,
    suppressed: !!v.suppressed || v.label !== 'rallies',
    label: v.label,
    signal: typeof v.signal === 'string' ? v.signal : null,
    degraded: v.label === 'windows',
    failed: false,
  };
}

export function resolveFps(fps, payload, insights) {
  if (isNum(fps) && fps > 0) return fps;
  const pf = obj(payload).fps;
  if (isNum(pf) && pf > 0) return pf;
  const inf = obj(insights).fps;
  return isNum(inf) && inf > 0 ? inf : null;
}

function rallyEntries(payload, fps) {
  const list = Array.isArray(obj(payload).rallies) ? payload.rallies : [];
  const out = [];
  list.forEach((r, idx) => {
    if (!r || typeof r !== 'object') return;
    let start = isNum(r.start_sec) ? r.start_sec : null;
    let end = isNum(r.end_sec) ? r.end_sec : null;
    if ((start === null || end === null) && fps && isNum(r.start_frame) && isNum(r.end_frame)) {
      start = r.start_frame / fps;
      end = r.end_frame / fps;
    }
    if (start === null || end === null || end < start) return;
    out.push({ id: isNum(r.id) ? r.id : idx + 1, n: out.length + 1, start: round2(start), end: round2(end), len: round2(end - start),
      degraded: !!r.degraded, strokeCount: 0 });
  });
  return out;
}

function rallyFor(time, rallies) {
  if (time === null) return null;
  const exact = rallies.find((r) => time >= r.start && time <= r.end);
  if (exact) return exact;
  let best = null;
  let bestD = Infinity;
  for (const r of rallies) {
    const d = time < r.start ? r.start - time : time - r.end;
    if (d <= RALLY_PAD_SEC && d < bestD) { best = r; bestD = d; }
  }
  return best;
}

function reportModel(rep) {
  const metrics = Object.entries(obj(rep.per_metric))
    .filter(([, m]) => m && typeof m === 'object')
    .map(([id, m]) => ({
      id,
      score: isNum(m.score) ? m.score : null,
      measured: isNum(m.measured) ? m.measured : null,
      ideal: Array.isArray(m.ideal_range) && m.ideal_range.length === 2 && m.ideal_range.every(isNum) ? [m.ideal_range[0], m.ideal_range[1]] : null,
    }));
  const raw = typeof rep.stroke_type === 'string' ? rep.stroke_type : '';
  const technique = TECHNIQUE_IDS.includes(rep.group) ? rep.group : STROKE_GROUP[raw] || LEGACY_PROFILE[raw] || null;
  return {
    technique: TECHNIQUE_IDS.includes(technique) ? technique : null,
    rawType: raw,
    scored: rep.scored !== false,
    overall: isNum(rep.overall_score) ? rep.overall_score : null,
    contactFrame: rep.contact_frame,
    metrics,
  };
}

/**
 * Pure workspace model. Inputs (each optional, any may be malformed):
 *   rallies     rally_segments.json payload ({rallies, detection, fps}) or a bare rallies array
 *   strokes     strokes.json payload ({strokes}) or a bare array of {frame, hitter, stroke, uncertain, confidence}
 *   techReports /api/technique payload ({summary, strokes}) or a bare array of strokes.jsonl reports; a report
 *               without a `scored` key is pre-change output and is never joined
 *   fps         metadata.json video.fps (falls back to the rally payload's, then the sidecar's)
 *   insights    the /api/insights/video sidecar (rally label when there is no rally file)
 *   athleteSide 'upper' | 'lower' | null: strokes by the other side are the opponent's and are never scored
 * Returns {fps, rallies:[{id,n,start,end,len,strokeCount,degraded}],
 *   strokes:[{i,frame,time,hitter,label,type,uncertain,confidence,opponent,score|null,report|null,rallyId|null}], summary}.
 * A doubles run (insights.match_format === 'doubles') joins no technique report to a stroke: its per-side data is
 * one representative per side of a 4-player match, not a player. `summary.doubles` says so.
 */
export function buildWorkspaceModel(input = {}) {
  const { rallies, strokes, techReports, fps, insights, athleteSide } = obj(input);
  const payload = Array.isArray(rallies) ? { rallies } : rallies;
  const hasFile = payload !== undefined && payload !== null;
  const base = hasFile ? summariseRallyPayload(payload) : summaryFromInsights(insights) || summariseRallyPayload(null);
  const f = resolveFps(fps, payload, insights);
  const det = obj(obj(payload).detection);
  // Degraded runs withhold stroke labels unless the file says otherwise (older files carry no flag).
  const withheld = typeof det.stroke_labels_withheld === 'boolean' ? det.stroke_labels_withheld : !!det.degraded;
  const units = hasFile && !base.failed ? rallyEntries(payload, f) : [];
  const side = SIDES.includes(athleteSide) ? athleteSide : null;

  const doubles = obj(insights).match_format === 'doubles';
  const reps = (doubles ? [] : Array.isArray(techReports) ? techReports : Array.isArray(obj(techReports).strokes) ? techReports.strokes : [])
    // A report without `scored` predates the one stroke classifier (rule-classifier label): treat it as absent.
    .filter((r) => r && typeof r === 'object' && Object.prototype.hasOwnProperty.call(r, 'scored')
      && isNum(r.contact_frame) && SIDES.includes(r.player_side));

  // A bare array (test helpers) counts as a current (schema 2) file; an object must say schema 2.
  const sdoc = Array.isArray(strokes) ? null : obj(strokes);
  const current = sdoc === null || sdoc.schema === 2;
  const rawStrokes = Array.isArray(strokes) ? strokes : Array.isArray(obj(strokes).strokes) ? strokes.strokes : [];
  const strokeNotice = rawStrokes.length === 0 ? null : !current ? 'stale'
    : sdoc === null ? null
    : sdoc.classifier === null ? 'no_model'
    : obj(sdoc.classifier).validated_on_owner_footage === true ? null : 'unverified';
  const list = rawStrokes.filter((s) => s && typeof s === 'object').map((s, k) => {
    const frame = isNum(s.frame) ? s.frame : null;
    const time = frame !== null && f ? round2(frame / f) : isNum(s.time_sec) ? round2(s.time_sec) : null;
    const hitter = SIDES.includes(s.hitter) ? s.hitter : null;
    const raw = typeof s.stroke === 'string' ? s.stroke : '';
    const known = current && STROKE_TYPE_IDS.includes(raw);
    const group = known ? (typeof s.group === 'string' ? s.group : STROKE_GROUP[raw]) : null;
    const uncertain = withheld || s.uncertain === true || !known || !TECHNIQUE_IDS.includes(group);
    const opponent = side !== null && hitter !== null && hitter !== side;
    let report = null;
    if (!opponent && hitter !== null && frame !== null) {
      let bestD = Infinity;
      for (const r of reps) {
        const d = Math.abs(r.contact_frame - frame);
        if (r.player_side === hitter && d <= JOIN_FRAMES && d < bestD) { report = r; bestD = d; }
      }
    }
    const rep = report ? reportModel(report) : null;
    const unit = rallyFor(time, units);
    if (unit) unit.strokeCount += 1;
    return {
      i: k + 1,
      frame,
      time,
      hitter,
      label: uncertain ? 'uncertain' : group,
      type: uncertain ? 'uncertain' : raw,
      uncertain,
      confidence: isNum(s.confidence) ? s.confidence : null,
      opponent,
      score: rep && rep.overall !== null ? rep.overall : null,
      report: rep,
      rallyId: unit ? unit.id : null,
    };
  });

  const nUnc = list.filter((s) => s.uncertain).length;
  return {
    fps: f,
    rallies: units,
    strokes: list,
    summary: {
      ...base,
      countShown: base.suppressed || base.failed ? null : base.count,
      withheld,
      strokeNotice,
      doubles,
      hasFile,
      strokes: { total: list.length, uncertain: nUnc, classified: list.length - nUnc },
      scored: list.filter((s) => s.score !== null).length,
      outside: units.length ? list.filter((s) => s.time !== null && s.rallyId === null).length : 0,
      reference: { ...DEFAULT_REF },
    },
  };
}

/**
 * The date a match is shown under: the match record's date, else the insights sidecar's `date.value` (filename
 * date, or the video's mtime when the name has none). Never the run files' own mtime. ISO string or null.
 */
export function matchDate(record, insights) {
  const iso = /^\d{4}-\d{2}-\d{2}$/;
  const r = obj(record).date;
  if (typeof r === 'string' && iso.test(r)) return r;
  const v = obj(obj(insights).date).value;
  return typeof v === 'string' && iso.test(v.slice(0, 10)) ? v.slice(0, 10) : null;
}

/** Pure: true when matchDate() fell back to a sidecar date taken from a file mtime (shown as "approx."). */
export function matchDateApprox(record, insights) {
  const r = obj(record).date;
  if (typeof r === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(r)) return false;
  return isApproxDate(matchDate(null, insights), obj(obj(insights).date).source);
}

/**
 * Pure: what the stroke list's score column shows. A report joins a stroke by frame and side only, so its
 * technique may differ from the stroke's label (a 'net' stroke next to a 'clear' report): then the list shows
 * no score, only a muted "as <technique>" (the inspector says "scored as"). {score: number|null, as: id|null}.
 */
export function listScore(stroke) {
  const s = obj(stroke);
  const rep = s.report && typeof s.report === 'object' ? s.report : null;
  if (!rep || !isNum(s.score)) return { score: null, as: null };
  if (rep.technique && rep.technique === s.label) return { score: s.score, as: null };
  return { score: null, as: rep.technique || null };
}

/** buildWorkspaceModel's `rallies` for a rally file that exists but could not be read: summarises as failed. */
const RALLY_UNREADABLE = Object.freeze({ rallies: null });

/**
 * Pure: the soft() result of fetching rally_segments.json -> buildWorkspaceModel's `rallies` input. A 404 is
 * an absent file (undefined: "No rallies detected"); a file that exists but fails to parse, or any other
 * failed fetch, is RALLY_UNREADABLE ("Rally detection failed"), never mistaken for "no rallies".
 */
export function rallyInput(res) {
  const r = obj(res);
  if (r.ok) return r.data === null || r.data === undefined ? RALLY_UNREADABLE : r.data;
  if (r.status === 404) return undefined;
  return RALLY_UNREADABLE;
}

// ---------- text helpers shared by the DOM layer ----------

/** Rally-count sentence with the honest label (spec 3). */
export function rallyCountText(summary, lang) {
  const s = obj(summary);
  if (s.failed) return t('ws.count.failed', lang);
  const n = isNum(s.count) ? s.count : 0;
  if (!n) return t('ws.count.none', lang);
  if (s.label === 'windows') return t('ws.count.windows', lang, { n: fmtNumber(n) });
  if (s.label === 'segments') return t('ws.count.segments', lang, { n: fmtNumber(n) });
  if (s.signal === 'shuttle') return t('ws.count.rallies_shuttle', lang, { n: fmtNumber(n) });
  return t('ws.count.rallies', lang, { n: fmtNumber(n) });
}

export function unitKey(summary) {
  const l = obj(summary).label;
  return l === 'windows' ? 'ws.unit.window' : l === 'segments' ? 'ws.unit.segment' : 'ws.unit.rally';
}

export function signalNote(summary, lang, nRallies) {
  const s = obj(summary);
  if (s.failed) return t('ws.signal_note.failed', lang);
  if (!nRallies) return t('ws.signal_note.none', lang);
  if (s.label === 'windows') return t('ws.signal_note.degraded', lang);
  if (s.label === 'segments') return t('ws.signal_note.swing', lang);
  if (s.signal === 'shuttle') return t('ws.signal_note.shuttle_hedged', lang);
  if (s.signal === 'courtview') return t('ws.signal_note.courtview', lang);
  return '';
}

export function techLabel(id, lang) {
  return id === 'uncertain' ? t('ws.uncertain', lang) : t('technique.' + id, lang);
}

/** A stroke's display name: its 12-type name (not the group name). */
export function strokeName(s, lang) {
  return s.type === 'uncertain' ? t('ws.uncertain', lang) : t('stroke.' + s.type, lang);
}

export function techCode(id, lang) {
  if (id === 'uncertain') return '?';
  const label = techLabel(id, lang);
  return lang === 'zh' ? label.charAt(0) : label.slice(0, 2).toUpperCase();
}

export function metricLabel(id, lang) {
  const key = 'metric.' + id;
  const v = t(key, lang);
  return v !== key ? v : String(id).replace(/_/g, ' ');
}

export function clockText(sec) {
  return isNum(sec) ? fmtDuration(sec) : '—';
}

function reducedMotion() {
  try {
    return typeof matchMedia === 'function' && matchMedia('(prefers-reduced-motion: reduce)').matches;
  } catch (e) {
    return false;
  }
}

/** ctx.reduced may be a boolean (main.js getter) or a function; otherwise the media query decides. */
export function readReduced(ctx) {
  const r = obj(ctx).reduced;
  if (typeof r === 'function') return !!r();
  if (typeof r === 'boolean') return r;
  return reducedMotion();
}

/** Strokes per page in the strip and the list: 1000+ strokes never become 1000 tab stops at once. */
export const STROKE_PAGE = 200;

/**
 * Pure: the first `limit` items (grown in whole pages so the selected item, when `isSelected` finds one, is
 * always included). Returns {items, rest, limit}.
 */
export function capItems(items, limit = STROKE_PAGE, isSelected = null) {
  const list = Array.isArray(items) ? items : [];
  let lim = Number.isFinite(limit) && limit >= 1 ? Math.floor(limit) : STROKE_PAGE;
  if (typeof isSelected === 'function') {
    const k = list.findIndex(isSelected);
    if (k >= lim) lim = Math.ceil((k + 1) / STROKE_PAGE) * STROKE_PAGE;
  }
  return { items: list.slice(0, lim), rest: Math.max(0, list.length - lim), limit: lim };
}

/** GET that never throws: {ok, data, status, error}. */
export function soft(promise) {
  return promise.then((data) => ({ ok: true, data, status: 200, error: null }))
    .catch((error) => ({ ok: false, data: null, status: error && error.status ? error.status : 0, error }));
}

export function nameFor(record, side, athletes) {
  const entry = obj(obj(record)[side]);
  if (typeof entry.athlete_id === 'string') {
    const a = (Array.isArray(obj(athletes).athletes) ? athletes.athletes : []).find((x) => x && x.id === entry.athlete_id);
    if (a && typeof a.name === 'string' && a.name) return a.name;
  }
  return typeof entry.name === 'string' && entry.name ? entry.name : null;
}
