// Match workspace (#/matches/<stem>, spec 6.5): the annotated video, a single-lane rally timeline, the stroke
// strip and list, the stroke inspector and the rally summary. buildWorkspaceModel() is pure and tested; mount()
// is a DOM layer over it. Honesty rules: there is no point outcome, so nothing is marked won or lost; swing
// segments are "segments (estimated)", degraded ones "windows"; uncertain strokes are muted and labelled so;
// opponent strokes are shown but never scored.
import { t, TECHNIQUE_IDS } from '../core/i18n.js';
import { fmtDate, fmtDuration, fmtNumber } from '../core/format.js';
import { createClock, local } from '../core/motion.js';
import { formatHash } from '../core/router.js';
import { api, getJSON, endpoints, errorText } from '../core/api.js';
import { h, hasDOM, revealStyle, setText, setAttr } from '../widgets/frame.js';

const SIDES = ['upper', 'lower'];
const STROKE_MAP = Object.freeze({ high_clear: 'clear', drop_shot: 'drop' });
/** A stroke belongs to a rally when it falls in [start - 0.5, end + 0.5] (the server's shots_per_rally rule). */
export const RALLY_PAD_SEC = 0.5;
/** A technique report joins a stroke within this many frames of its contact frame (same side only). */
export const JOIN_FRAMES = 3;
const DEFAULT_REF = Object.freeze({ lo: 70, hi: 100 });
const LONG = { timeout: 60000 };

const isNum = (v) => typeof v === 'number' && Number.isFinite(v);
const obj = (v) => (v && typeof v === 'object' && !Array.isArray(v) ? v : {});
const round2 = (x) => Math.round(x * 100) / 100;
const num1 = (v) => String(+Number(v).toFixed(1));

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

function resolveFps(fps, payload, insights) {
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
  const mapped = STROKE_MAP[raw] || raw;
  return {
    technique: TECHNIQUE_IDS.includes(mapped) ? mapped : null,
    rawType: raw,
    overall: isNum(rep.overall_score) ? rep.overall_score : null,
    contactFrame: rep.contact_frame,
    metrics,
  };
}

/**
 * Pure workspace model. Inputs (each optional, any may be malformed):
 *   rallies     rally_segments.json payload ({rallies, detection, fps}) or a bare rallies array
 *   strokes     strokes.json payload ({strokes}) or a bare array of {frame, hitter, stroke, uncertain, confidence}
 *   techReports /api/technique payload ({summary, strokes}) or a bare array of strokes.jsonl reports
 *   fps         metadata.json video.fps (falls back to the rally payload's, then the sidecar's)
 *   insights    the /api/insights/video sidecar (rally label when there is no rally file)
 *   athleteSide 'upper' | 'lower' | null: strokes by the other side are the opponent's and are never scored
 * Returns {fps, rallies:[{id,n,start,end,len,strokeCount,degraded}],
 *   strokes:[{i,frame,time,hitter,label,uncertain,confidence,opponent,score|null,report|null,rallyId|null}], summary}.
 */
export function buildWorkspaceModel(input = {}) {
  const { rallies, strokes, techReports, fps, insights, athleteSide } = obj(input);
  const payload = Array.isArray(rallies) ? { rallies } : rallies;
  const hasFile = payload !== undefined && payload !== null;
  const base = hasFile ? summariseRallyPayload(payload) : summaryFromInsights(insights) || summariseRallyPayload(null);
  const f = resolveFps(fps, payload, insights);
  const det = obj(obj(payload).detection);
  const withheld = det.stroke_labels_withheld === true;
  const units = hasFile && !base.failed ? rallyEntries(payload, f) : [];
  const side = SIDES.includes(athleteSide) ? athleteSide : null;

  const reps = (Array.isArray(techReports) ? techReports : Array.isArray(obj(techReports).strokes) ? techReports.strokes : [])
    .filter((r) => r && typeof r === 'object' && isNum(r.contact_frame) && SIDES.includes(r.player_side));

  const rawStrokes = Array.isArray(strokes) ? strokes : Array.isArray(obj(strokes).strokes) ? strokes.strokes : [];
  const list = rawStrokes.filter((s) => s && typeof s === 'object').map((s, k) => {
    const frame = isNum(s.frame) ? s.frame : null;
    const time = frame !== null && f ? round2(frame / f) : isNum(s.time_sec) ? round2(s.time_sec) : null;
    const hitter = SIDES.includes(s.hitter) ? s.hitter : null;
    const raw = typeof s.stroke === 'string' ? s.stroke : '';
    const mapped = STROKE_MAP[raw] || raw;
    const known = TECHNIQUE_IDS.includes(mapped);
    const uncertain = withheld || s.uncertain === true || !known;
    const opponent = side !== null && hitter !== side;
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
      label: uncertain ? 'uncertain' : mapped,
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
      hasFile,
      strokes: { total: list.length, uncertain: nUnc, classified: list.length - nUnc },
      scored: list.filter((s) => s.score !== null).length,
      outside: units.length ? list.filter((s) => s.time !== null && s.rallyId === null).length : 0,
      reference: { ...DEFAULT_REF },
    },
  };
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

function unitKey(summary) {
  const l = obj(summary).label;
  return l === 'windows' ? 'ws.unit.window' : l === 'segments' ? 'ws.unit.segment' : 'ws.unit.rally';
}

function signalNote(summary, lang, nRallies) {
  const s = obj(summary);
  if (s.failed) return t('ws.signal_note.failed', lang);
  if (!nRallies) return t('ws.signal_note.none', lang);
  if (s.label === 'windows') return t('ws.signal_note.degraded', lang);
  if (s.label === 'segments') return t('ws.signal_note.swing', lang);
  if (s.signal === 'shuttle') return t('ws.signal_note.shuttle_hedged', lang);
  if (s.signal === 'courtview') return t('ws.signal_note.courtview', lang);
  return '';
}

function techLabel(id, lang) {
  return id === 'uncertain' ? t('ws.uncertain', lang) : t('technique.' + id, lang);
}

function techCode(id, lang) {
  if (id === 'uncertain') return '?';
  const label = techLabel(id, lang);
  return lang === 'zh' ? label.charAt(0) : label.slice(0, 2).toUpperCase();
}

function metricLabel(id, lang) {
  const key = 'metric.' + id;
  const v = t(key, lang);
  return v !== key ? v : String(id).replace(/_/g, ' ');
}

function clockText(sec) {
  return isNum(sec) ? fmtDuration(sec) : '—';
}

function reducedMotion() {
  try {
    return typeof matchMedia === 'function' && matchMedia('(prefers-reduced-motion: reduce)').matches;
  } catch (e) {
    return false;
  }
}

/** GET that never throws: {ok, data, status, error}. */
function soft(promise) {
  return promise.then((data) => ({ ok: true, data, status: 200, error: null }))
    .catch((error) => ({ ok: false, data: null, status: error && error.status ? error.status : 0, error }));
}

function nameFor(record, side, athletes) {
  const entry = obj(obj(record)[side]);
  if (typeof entry.athlete_id === 'string') {
    const a = (Array.isArray(obj(athletes).athletes) ? athletes.athletes : []).find((x) => x && x.id === entry.athlete_id);
    if (a && typeof a.name === 'string' && a.name) return a.name;
  }
  return typeof entry.name === 'string' && entry.name ? entry.name : null;
}

// ---------- DOM ----------

/** Mount the workspace for ctx.params.stem. Returns {destroy()}. */
export function mount(container, ctx = {}) {
  const noop = { destroy() {} };
  if (!container || !hasDOM()) return noop;
  const stem = String(obj(ctx.params).stem || '');
  const lang = ctx.lang === 'en' ? 'en' : 'zh';
  const store = ctx.store && typeof ctx.store.get === 'function' ? ctx.store : null;
  const S = () => (store ? obj(store.get()) : {});

  const state = {
    destroyed: false,
    model: null,
    record: {},
    athleteSide: null,
    names: { upper: null, lower: null },
    insights: null,
    unit: null, // rally id, or 'all'
    sel: null, // stroke i
    filter: 'all',
    firstPaint: true,
  };
  const refs = {};
  let ctl = null;
  let sections = [];

  const root = h('div', { class: 'page ws', 'data-view': 'workspace' });
  container.appendChild(root);
  const clock = createClock({
    reduced: () => (typeof ctx.reduced === 'function' ? ctx.reduced() : reducedMotion()),
    onFrame: (p) => sections.forEach((el, i) => revealStyle(el, local(p, i * 1.5), { dy: 10 })),
  });

  const sideName = (side) => state.names[side] || t('ws.side.' + side, lang);

  function unitTitle(r) {
    return t(unitKey(state.model.summary), lang, { n: r.n });
  }

  function unitStrokes() {
    const m = state.model;
    if (state.unit === 'all' || state.unit === null) return m.strokes;
    return m.strokes.filter((s) => s.rallyId === state.unit);
  }

  function currentUnit() {
    return state.unit === 'all' ? null : state.model.rallies.find((r) => r.id === state.unit) || null;
  }

  function currentStroke() {
    return state.sel === null ? null : state.model.strokes.find((s) => s.i === state.sel) || null;
  }

  // ----- loading / error / not found -----
  function renderStatus(kind, err) {
    root.textContent = '';
    const card = h('div', { class: 'card ov-state', role: kind === 'loading' ? 'status' : 'alert', 'aria-busy': kind === 'loading' ? 'true' : null });
    if (kind === 'loading') {
      card.appendChild(h('div', { class: 'k-label' }, t('state.loading', lang)));
      card.appendChild(h('div', { class: 'ov-skel' }));
    } else if (kind === 'notfound') {
      card.appendChild(h('div', { class: 'ov-empty-title' }, t('ws.not_found.title', lang)));
      card.appendChild(h('div', { class: 'ov-state-body' }, t('ws.not_found.body', lang, { stem })));
      card.appendChild(h('a', { href: '#/matches', class: 'ov-btn small' }, t('nav.matches', lang)));
    } else {
      card.appendChild(h('div', { class: 'k-label' }, t('error.load.title', lang)));
      card.appendChild(h('div', { class: 'ov-state-body' }, errorText(err, lang)));
      card.appendChild(h('button', { type: 'button', class: 'ov-btn small', onclick: () => load() }, t('btn.retry', lang)));
    }
    root.appendChild(breadcrumb(stem));
    root.appendChild(card);
  }

  function breadcrumb(current) {
    const s = S();
    const scope = obj(s.scope);
    const athlete = (Array.isArray(obj(s.athletes).athletes) ? s.athletes.athletes : []).find((a) => a && a.id === scope.athlete);
    return h('nav', { class: 'ws-crumbs', 'aria-label': t('ws.breadcrumb', lang) }, [
      h('a', { href: formatHash('overview', {}, scope) }, athlete ? athlete.name : t('nav.overview', lang)),
      h('span', { 'aria-hidden': 'true' }, '/'),
      h('a', { href: formatHash('matches', {}, scope) }, t('nav.matches', lang)),
      h('span', { 'aria-hidden': 'true' }, '/'),
      h('span', { 'aria-current': 'page' }, current),
    ]);
  }

  // ----- header -----
  function titleSides() {
    // The selected athlete first; otherwise lower then upper (the canvas reads "Lower · Upper").
    const a = state.athleteSide || 'lower';
    return [a, a === 'lower' ? 'upper' : 'lower'];
  }

  function buildHead() {
    const m = state.model;
    const [a, b] = titleSides();
    const rec = obj(state.record);
    const res = obj(rec.result);
    const ins = obj(state.insights);
    const dateIso = typeof rec.date === 'string' ? rec.date : obj(ins.date).value;
    const date = fmtDate(dateIso, lang);
    const title = t('ws.title', lang, { a: sideName(a), b: sideName(b) });
    const meta = h('div', { class: 'ws-meta' });
    if (Array.isArray(res.sets) && res.sets.length) {
      const pairs = res.sets.filter((p) => Array.isArray(p) && p.length === 2)
        .map((p) => (a === 'lower' ? `${p[1]}–${p[0]}` : `${p[0]}–${p[1]}`));
      meta.appendChild(h('span', { class: 'mono ws-sets' }, pairs.join(' · ')));
    }
    if (SIDES.includes(res.winner)) {
      if (state.athleteSide) {
        const win = res.winner === state.athleteSide;
        meta.appendChild(h('span', { class: 'k-chip ' + (win ? 'good' : 'bad') }, t(win ? 'ws.result.win' : 'ws.result.loss', lang)));
      } else {
        meta.appendChild(h('span', { class: 'k-chip' }, t('result.side_won', lang, { name: sideName(res.winner) })));
      }
    }
    meta.appendChild(h('span', null, t('ws.summary_line', lang, {
      rallies: rallyCountText(m.summary, lang), strokes: t('scope.strokes_count', lang, { n: fmtNumber(m.summary.strokes.total) }),
    })));
    if (m.summary.label !== 'rallies') {
      meta.appendChild(h('span', { class: 'mono ov-pill' }, t(m.summary.label === 'windows' ? 'tag.windows' : 'tag.swing_est', lang)));
    }
    const head = h('div', { class: 'ws-head' }, [
      h('div', { class: 'ws-head-l' }, [
        h('div', { class: 'mono ov-eyebrow' }, t('ws.eyebrow', lang, { date })),
        h('h1', { class: 'page-title serif ws-title' }, [sideName(a), h('span', { class: 'ws-vs' }, lang === 'zh' ? '对' : 'vs.'), sideName(b)]),
        meta,
      ]),
      h('div', { class: 'ov-actions' }, [
        h('a', { href: formatHash('overview', {}, obj(S().scope)), class: 'ov-btn' }, t('ws.back', lang)),
        h('a', { href: formatHash('results', { stem }, { mode: 'match' }), class: 'ov-btn' }, t('ws.results_link', lang)),
      ]),
    ]);
    refs.title = title;
    return head;
  }

  // ----- video -----
  function buildVideo() {
    const sec = h('section', { class: 'card hud ws-video-card', 'aria-label': t('ws.video', lang) });
    const stage = h('div', { class: 'ws-stage' });
    const video = h('video', { class: 'ws-video', preload: 'metadata', playsinline: true });
    const tags = h('div', { class: 'ws-tags' });
    const sig = h('div', { class: 'ws-tag-r' });
    const msg = h('div', { class: 'ws-video-msg', role: 'status', hidden: true });
    stage.appendChild(video);
    stage.appendChild(tags);
    stage.appendChild(sig);
    stage.appendChild(msg);
    const playBtn = h('button', { type: 'button', class: 'ws-play', 'aria-label': t('ws.play', lang), onclick: togglePlay }, '▶');
    const time = h('span', { class: 'mono ws-time' }, '0:00');
    const seek = h('input', { type: 'range', class: 'ws-seek', min: '0', max: '0', step: '0.1', value: '0', 'aria-label': t('ws.seek_aria', lang) });
    seek.addEventListener('input', () => seekTo(Number(seek.value), false));
    const ctlRow = h('div', { class: 'ws-ctl' }, [
      h('button', { type: 'button', class: 'ws-ibtn', 'aria-label': t('ws.prev_rally', lang), onclick: () => stepUnit(-1) }, '⏮'),
      playBtn,
      h('button', { type: 'button', class: 'ws-ibtn', 'aria-label': t('ws.next_rally', lang), onclick: () => stepUnit(1) }, '⏭'),
      time,
      seek,
    ]);
    sec.appendChild(stage);
    sec.appendChild(ctlRow);
    Object.assign(refs, { video, tags, sig, msg, playBtn, time, seek, ctlRow, stage });

    // detect_<stem>.mp4 is where app.py leaves the browser-compatible transcode (it replaces the raw file);
    // detect_<stem>_h264.mp4 is tried second for runs where the replace step did not happen.
    const candidates = [`detect_${stem}.mp4`, `detect_${stem}_h264.mp4`].map((f) => endpoints.output(stem, f));
    let idx = 0;
    let gotStatus = false;
    const fail = (key) => {
      if (state.destroyed || idx >= candidates.length) return;
      idx += 1;
      if (idx < candidates.length) {
        video.src = candidates[idx];
        return;
      }
      video.removeAttribute('src');
      video.hidden = true;
      msg.hidden = false;
      setText(msg, t(key, lang));
      for (const b of ctlRow.querySelectorAll('button,input')) b.disabled = true;
    };
    video.addEventListener('error', () => fail(gotStatus ? 'ws.video_unplayable' : 'ws.video_missing'));
    video.addEventListener('loadedmetadata', () => {
      gotStatus = true;
      // An undecodable MPEG-4 Part 2 track loads with the right duration but no picture.
      if (!video.videoWidth && !video.videoHeight) {
        fail('ws.video_unplayable');
        return;
      }
      setAttr(seek, 'max', isNum(video.duration) ? video.duration.toFixed(1) : '0');
      if (refs.pendingSeek !== undefined) {
        const s = refs.pendingSeek;
        refs.pendingSeek = undefined;
        seekTo(s, false);
      }
      paintTime();
    });
    video.addEventListener('timeupdate', paintTime);
    video.addEventListener('play', paintPlay);
    video.addEventListener('pause', paintPlay);
    video.src = candidates[0];
    return sec;
  }

  function paintTime() {
    const v = refs.video;
    if (!v) return;
    const cur = isNum(v.currentTime) ? v.currentTime : 0;
    setText(refs.time, `${clockText(cur)} / ${clockText(isNum(v.duration) ? v.duration : null)}`);
    if (document.activeElement !== refs.seek) refs.seek.value = String(cur.toFixed(1));
  }

  function paintPlay() {
    const v = refs.video;
    const playing = v && !v.paused;
    setText(refs.playBtn, playing ? '❚❚' : '▶');
    setAttr(refs.playBtn, 'aria-label', t(playing ? 'ws.pause' : 'ws.play', lang));
  }

  function togglePlay() {
    const v = refs.video;
    if (!v || v.hidden) return;
    try {
      if (v.paused) {
        const p = v.play();
        if (p && typeof p.catch === 'function') p.catch(() => {});
      } else v.pause();
    } catch (e) {
      // ignore: playback is best effort
    }
  }

  function seekTo(sec, fromSelection = true) {
    const v = refs.video;
    if (!v || !isNum(sec)) return;
    const target = Math.max(0, sec - (fromSelection ? 0.4 : 0));
    try {
      if (v.readyState >= 1) v.currentTime = target;
      else refs.pendingSeek = target;
    } catch (e) {
      refs.pendingSeek = target;
    }
    paintTime();
  }

  function paintVideoTags() {
    if (!refs.tags) return;
    refs.tags.textContent = '';
    const u = currentUnit();
    const st = currentStroke();
    if (u) refs.tags.appendChild(h('span', { class: 'mono ws-tag' }, unitTitle(u).toUpperCase()));
    if (st) {
      const inUnit = unitStrokes();
      const pos = inUnit.findIndex((x) => x.i === st.i);
      refs.tags.appendChild(h('span', { class: 'mono ws-tag accent' }, t('ws.tag.stroke', lang, {
        i: pos >= 0 ? pos + 1 : st.i, n: pos >= 0 ? inUnit.length : state.model.strokes.length, technique: techLabel(st.label, lang).toUpperCase(),
      })));
    }
    const s = state.model.summary;
    const sig = s.signal ? t('signal.' + s.signal, lang) : '';
    setText(refs.sig, sig && sig !== 'signal.' + s.signal ? sig.toUpperCase() : '');
    refs.sig.hidden = !refs.sig.textContent;
  }

  // ----- timeline + strip -----
  function buildTimeline() {
    const m = state.model;
    const sec = h('section', { class: 'card hud ws-timeline', 'aria-label': t('ws.timeline', lang) });
    const chip = m.summary.label === 'rallies' ? null
      : h('span', { class: 'k-chip mid' }, t(m.summary.label === 'windows' ? 'ws.label.windows' : 'ws.label.segments', lang));
    sec.appendChild(h('div', { class: 'ws-sec-head' }, [h('h2', { class: 'card-title' }, t('ws.timeline', lang)), chip]));
    const lane = h('div', { class: 'ws-lane', role: 'group', 'aria-label': t('ws.timeline', lang) });
    m.rallies.forEach((r, i) => {
      const title = unitTitle(r);
      const pressed = state.unit === r.id;
      lane.appendChild(h('button', {
        type: 'button',
        class: 'ws-rb rb' + (m.summary.label === 'windows' || r.degraded ? ' coarse' : '') + (pressed ? ' on' : ''),
        'data-k': 'r' + r.id,
        'aria-pressed': pressed ? 'true' : 'false',
        'aria-label': t('ws.unit_aria', lang, { title, n: r.strokeCount, sec: num1(r.len) }),
        title: `${title} · ${clockText(r.start)}–${clockText(r.end)}`,
        style: { flex: `${Math.max(r.len, 0.5)} 1 0`, animationDelay: state.firstPaint ? `${Math.min(0.2 + i * 0.03, 1.2).toFixed(2)}s` : '0s' },
        onclick: () => selectUnit(r.id, true, 'r' + r.id),
      }, String(r.n)));
    });
    const allPressed = state.unit === 'all';
    const allBtn = h('button', {
      type: 'button', class: 'ws-chip' + (allPressed ? ' on' : ''), 'data-k': 'rall', 'aria-pressed': allPressed ? 'true' : 'false',
      onclick: () => selectUnit('all', false, 'rall'),
    }, t('ws.all_strokes', lang));
    if (m.rallies.length) sec.appendChild(h('div', { class: 'ws-lane-scroll' }, lane));
    sec.appendChild(h('div', { class: 'ws-lane-foot' }, [
      allBtn,
      h('span', { class: 'card-sub' }, m.rallies.length ? `${t('ws.timeline_hint', lang)} ${t('ws.timeline_lane', lang)}` : signalNote(m.summary, lang, 0)),
    ]));

    // stroke strip for the selected unit
    const strokes = unitStrokes();
    const u = currentUnit();
    const head = u
      ? t('ws.unit_strokes', lang, { title: unitTitle(u), n: strokes.length })
      : t('ws.all_strokes_title', lang, { n: strokes.length });
    const strip = h('div', { class: 'ws-strip' });
    strokes.forEach((s) => {
      const pressed = state.sel === s.i;
      strip.appendChild(h('button', {
        type: 'button',
        class: `ws-st tc-${s.label}` + (s.uncertain ? ' unc' : '') + (s.opponent ? ' opp' : '') + (pressed ? ' on' : ''),
        'data-k': 's' + s.i,
        'aria-pressed': pressed ? 'true' : 'false',
        'aria-label': t('ws.stroke_aria', lang, { n: s.i, technique: techLabel(s.label, lang) }) + ', ' + (s.hitter ? sideName(s.hitter) : '—'),
        title: `${techLabel(s.label, lang)} · ${clockText(s.time)}`,
        onclick: () => selectStroke(s.i, 's' + s.i),
      }, techCode(s.label, lang)));
    });
    const stripSec = h('div', { class: 'ws-strip-wrap' }, [
      h('div', { class: 'ws-sec-head' }, [h('h3', { class: 'ws-h3' }, head), h('span', { class: 'card-sub' }, t('ws.pick_stroke', lang))]),
      strokes.length ? strip : h('div', { class: 'kw-note' }, t('ws.no_strokes', lang)),
    ]);
    sec.appendChild(stripSec);
    if (m.summary.outside && state.unit !== 'all') {
      sec.appendChild(h('div', { class: 'kw-foot-muted' }, t('ws.outside_note', lang, {
        n: m.summary.outside, label: t(m.summary.label === 'windows' ? 'ws.label.windows' : m.summary.label === 'segments' ? 'ws.label.segments' : 'ws.label.rallies', lang),
      })));
    }
    return sec;
  }

  // ----- stroke list -----
  function buildList() {
    const all = unitStrokes();
    const present = [...new Set(all.map((s) => s.label))];
    const ids = ['all', ...TECHNIQUE_IDS.filter((id) => present.includes(id)), ...(present.includes('uncertain') ? ['uncertain'] : [])];
    if (!ids.includes(state.filter)) state.filter = 'all';
    const sec = h('section', { class: 'card hud ws-list', 'aria-label': t('ws.strokes_in_rally', lang) });
    const filters = h('div', { class: 'ws-filters', role: 'group', 'aria-label': t('ws.filter_aria', lang) }, ids.map((id) => h('button', {
      type: 'button', class: 'ws-chip' + (state.filter === id ? ' on' : ''), 'data-k': 'f' + id, 'aria-pressed': state.filter === id ? 'true' : 'false',
      onclick: () => { state.filter = id; refresh('f' + id); },
    }, id === 'all' ? t('ws.filter.all', lang) : techLabel(id, lang))));
    sec.appendChild(h('div', { class: 'ws-sec-head' }, [h('h2', { class: 'card-title' }, t('ws.strokes_in_rally', lang)), filters]));
    const shown = state.filter === 'all' ? all : all.filter((s) => s.label === state.filter);
    const lo = state.model.summary.reference.lo;
    const rows = shown.map((s) => h('tr', { class: 'ws-row' + (state.sel === s.i ? ' on' : '') + (s.uncertain ? ' unc' : '') }, [
      h('td', { class: 'mono kw-muted' }, String(s.i)),
      h('td', { class: 'mono kw-muted' }, clockText(s.time)),
      h('td', null, h('button', {
        type: 'button', class: 'ws-row-btn', 'data-k': 'l' + s.i,
        'aria-label': t('ws.stroke_aria', lang, { n: s.i, technique: techLabel(s.label, lang) }),
        onclick: () => selectStroke(s.i, 'l' + s.i),
      }, [h('span', { class: `ws-dot tc-${s.label}`, 'aria-hidden': 'true' }), techLabel(s.label, lang)])),
      h('td', { class: 'kw-muted' }, [s.hitter ? sideName(s.hitter) : '—', s.opponent ? h('span', { class: 'ws-opp' }, t('ws.opponent_tag', lang)) : null]),
      h('td', { class: 'mono kw-num' + (s.score !== null && s.score < lo ? ' ws-bad' : '') + (s.score === null ? ' kw-muted' : '') }, s.score === null ? '—' : num1(s.score)),
    ]));
    const table = h('table', { class: 'table ws-table' }, [
      h('thead', null, h('tr', null, [
        h('th', { scope: 'col' }, '#'), h('th', { scope: 'col' }, t('ws.col.time', lang)), h('th', { scope: 'col' }, t('ws.col.technique', lang)),
        h('th', { scope: 'col' }, t('ws.col.player', lang)), h('th', { scope: 'col', class: 'kw-num' }, t('ws.col.score', lang)),
      ])),
      h('tbody', null, rows),
    ]);
    sec.appendChild(h('div', { class: 'ws-table-wrap' }, shown.length ? table : h('div', { class: 'kw-note ws-empty' }, t('ws.no_strokes', lang))));
    return sec;
  }

  // ----- inspector -----
  function yard(score, lo, hi, cls) {
    const pct = Math.max(0, Math.min(100, score));
    return h('div', { class: 'ws-yard ' + (cls || ''), 'aria-hidden': 'true' }, [
      h('div', { class: 'ws-yard-band', style: { left: lo + '%', width: Math.max(0, hi - lo) + '%' } }),
      h('div', { class: 'ws-yard-mark', style: { left: `calc(${pct}% - 2px)` } }),
    ]);
  }

  function buildInspector() {
    const st = currentStroke();
    const { lo, hi } = state.model.summary.reference;
    const sec = h('section', { class: 'card hud ws-insp', 'aria-label': t('ws.inspector', lang), 'aria-live': 'polite' });
    sec.appendChild(h('div', { class: 'k-label' }, t('ws.inspector', lang)));
    if (!st) {
      sec.appendChild(h('div', { class: 'kw-note' }, state.model.strokes.length ? t('ws.pick_none', lang) : t('ws.empty.body', lang)));
      return sec;
    }
    const unit = state.model.rallies.find((r) => r.id === st.rallyId);
    sec.appendChild(h('h2', { class: 'ws-insp-title' }, t('ws.stroke_title', lang, { n: st.i, technique: techLabel(st.label, lang) })));
    sec.appendChild(h('div', { class: 'card-sub' }, [st.hitter ? sideName(st.hitter) : '—', clockText(st.time), unit ? unitTitle(unit) : null].filter(Boolean).join(' · ')));
    if (st.uncertain) sec.appendChild(h('div', { class: 'ws-note' }, t('ws.uncertain_note', lang)));
    if (st.opponent) {
      sec.appendChild(h('div', { class: 'ws-note dashed' }, t('ws.not_scored_note', lang)));
      return sec;
    }
    const rep = st.report;
    if (!rep || rep.overall === null) {
      sec.appendChild(h('div', { class: 'ws-note dashed' }, t('ws.no_report', lang)));
      return sec;
    }
    const below = rep.overall < lo;
    const above = rep.overall > hi;
    sec.appendChild(h('div', { class: 'ws-score' }, [
      h('span', { class: 'mono ws-score-num' }, num1(rep.overall)),
      h('span', { class: 'kw-tile-unit' }, '/ 100'),
      h('span', { class: 'k-chip ' + (below ? 'bad' : 'good') }, t(below ? 'ws.verdict.below' : above ? 'ws.verdict.above' : 'ws.verdict.inside', lang)),
    ]));
    sec.appendChild(yard(rep.overall, lo, hi, 'big'));
    sec.appendChild(h('div', { class: 'mono ws-yard-axis' }, [h('span', null, '0'), h('span', null, t('ws.reference', lang, { lo, hi })), h('span', null, '100')]));
    if (rep.technique && rep.technique !== st.label) {
      sec.appendChild(h('div', { class: 'kw-foot-muted' }, t('ws.scored_as', lang, { technique: techLabel(rep.technique, lang) })));
    }
    const metrics = h('div', { class: 'ws-metrics' });
    rep.metrics.forEach((mt) => {
      const measured = mt.measured === null ? '—' : num1(mt.measured);
      metrics.appendChild(h('div', { class: 'ws-metric' }, [
        h('div', { class: 'ws-metric-head' }, [
          h('span', { class: 'kw-muted' }, metricLabel(mt.id, lang)),
          h('span', { class: 'mono ws-metric-val' + (mt.score !== null && mt.score < lo ? ' ws-bad' : '') }, mt.score === null ? '—' : num1(mt.score)),
        ]),
        mt.score === null ? h('div', { class: 'ws-yard small empty', 'aria-hidden': 'true' }) : yard(mt.score, lo, hi, 'small'),
        h('div', { class: 'mono ws-metric-sub' }, mt.ideal ? t('ws.metric_measured', lang, { measured, lo: mt.ideal[0], hi: mt.ideal[1] }) : measured),
      ]));
    });
    if (rep.metrics.length) sec.appendChild(metrics);
    return sec;
  }

  // ----- rally summary -----
  function buildSummary() {
    const m = state.model;
    const u = currentUnit();
    const sec = h('section', { class: 'card hud ws-sum', 'aria-label': t('ws.summary', lang) });
    sec.appendChild(h('h2', { class: 'card-title' }, t('ws.summary', lang)));
    const dl = h('dl', { class: 'ws-dl' });
    const row = (k, v) => { dl.appendChild(h('dt', null, k)); dl.appendChild(h('dd', { class: 'mono' }, v)); };
    const strokes = unitStrokes();
    if (u) {
      row(unitTitle({ n: '' }).trim(), t('ws.summary.of', lang, { n: u.n, total: m.rallies.length }));
      row(t('ws.summary.shots', lang), String(u.strokeCount));
      row(t('ws.summary.duration', lang), `${num1(u.len)} s`);
      row(t('ws.summary.span', lang), `${clockText(u.start)}–${clockText(u.end)}`);
    } else {
      row(t('ws.summary.shots', lang), String(strokes.length));
    }
    const last = strokes[strokes.length - 1];
    if (last) row(t('ws.summary.last_stroke', lang), t('ws.by', lang, { label: techLabel(last.label, lang), name: last.hitter ? sideName(last.hitter) : '—' }));
    sec.appendChild(dl);
    const note = signalNote(m.summary, lang, m.rallies.length);
    if (note) sec.appendChild(h('div', { class: 'ws-sum-note' }, note));
    return sec;
  }

  // ----- render -----
  function renderAll() {
    if (state.destroyed) return;
    root.textContent = '';
    root.appendChild(breadcrumb(t('ws.title', lang, { a: sideName(titleSides()[0]), b: sideName(titleSides()[1]) })));
    const head = buildHead();
    root.appendChild(head);
    const video = buildVideo();
    refs.timelineHost = h('div', { class: 'ws-slot' });
    refs.listHost = h('div', { class: 'ws-slot' });
    refs.inspHost = h('div', { class: 'ws-slot' });
    refs.sumHost = h('div', { class: 'ws-slot' });
    const main = h('div', { class: 'ws-main' }, [video, refs.timelineHost, refs.listHost]);
    const side = h('aside', { class: 'ws-side', 'aria-label': t('ws.inspector', lang) }, [refs.inspHost, refs.sumHost]);
    root.appendChild(h('div', { class: 'ws-grid' }, [main, side]));
    sections = [head, video, refs.timelineHost, refs.inspHost, refs.listHost, refs.sumHost];
    refresh(null);
    state.firstPaint = false;
    clock.start();
  }

  function refresh(focusKey) {
    if (state.destroyed || !refs.timelineHost) return;
    const swap = (host, node) => { host.textContent = ''; host.appendChild(node); };
    swap(refs.timelineHost, buildTimeline());
    swap(refs.listHost, buildList());
    swap(refs.inspHost, buildInspector());
    swap(refs.sumHost, buildSummary());
    paintVideoTags();
    if (focusKey) {
      const safe = String(focusKey).replace(/["\\]/g, '');
      const node = root.querySelector(`[data-k="${safe}"]`);
      if (node && typeof node.focus === 'function') {
        try { node.focus({ preventScroll: true }); } catch (e) { node.focus(); }
      }
    }
  }

  function selectUnit(id, seek, focusKey) {
    state.unit = id;
    const list = unitStrokes();
    if (!list.some((s) => s.i === state.sel)) state.sel = list.length ? list[0].i : null;
    refresh(focusKey);
    const u = currentUnit();
    if (seek && u) seekTo(u.start, false);
  }

  function selectStroke(i, focusKey) {
    state.sel = i;
    refresh(focusKey);
    const st = currentStroke();
    if (st && st.time !== null) seekTo(st.time, true);
  }

  function stepUnit(dir) {
    const rs = state.model.rallies;
    if (!rs.length) return;
    const pos = rs.findIndex((r) => r.id === state.unit);
    const next = pos < 0 ? (dir > 0 ? 0 : rs.length - 1) : Math.max(0, Math.min(rs.length - 1, pos + dir));
    selectUnit(rs[next].id, true, null);
  }

  // ----- data -----
  async function load() {
    if (ctl && typeof ctl.abort === 'function') ctl.abort();
    ctl = typeof AbortController === 'function' ? new AbortController() : null;
    const o = { ...LONG, ...(ctl ? { signal: ctl.signal } : {}) };
    renderStatus('loading');
    const out = (f) => soft(getJSON(endpoints.output(stem, f), o));
    const [rally, strokes, meta, tech, ins, rec] = await Promise.all([
      out('rally_segments.json'), out('strokes.json'), out('metadata.json'),
      soft(getJSON('/api/technique/' + encodeURIComponent(stem), o)),
      soft(api.videoInsights(stem, o)),
      soft(api.match(stem, o)),
    ]);
    if (state.destroyed) return;
    const all = [rally, strokes, meta, tech, ins, rec];
    if (all.some((r) => r.error && r.error.kind === 'aborted')) return;
    const fileish = [rally, strokes, meta, ins];
    if (fileish.every((r) => !r.ok && r.status === 404)) {
      renderStatus('notfound');
      return;
    }
    if (fileish.every((r) => !r.ok)) {
      renderStatus('error', (fileish.find((r) => r.status !== 404) || fileish[0]).error);
      return;
    }
    state.record = rec.ok ? obj(rec.data) : {};
    state.insights = ins.ok ? obj(ins.data) : null;
    const s = S();
    const athletes = obj(s.athletes);
    state.names = { upper: nameFor(state.record, 'upper', athletes), lower: nameFor(state.record, 'lower', athletes) };
    const athleteId = obj(s.scope).athlete || s.athlete || '';
    state.athleteSide = athleteId ? SIDES.find((sd) => obj(state.record[sd]).athlete_id === athleteId) || null : null;
    const fps = obj(obj(meta.data).video).fps;
    state.model = buildWorkspaceModel({
      rallies: rally.ok ? rally.data : undefined,
      strokes: strokes.ok ? strokes.data : null,
      techReports: tech.ok ? tech.data : null,
      fps,
      insights: state.insights,
      athleteSide: state.athleteSide,
    });
    const m = state.model;
    state.unit = m.rallies.length ? m.rallies[0].id : 'all';
    const first = unitStrokes()[0];
    state.sel = first ? first.i : null;
    renderAll();
  }

  load();

  function destroy() {
    if (state.destroyed) return;
    state.destroyed = true;
    clock.cancel();
    if (ctl && typeof ctl.abort === 'function') ctl.abort();
    const v = refs.video;
    if (v) {
      try {
        v.pause();
        v.removeAttribute('src');
        v.load(); // releases the media connection
      } catch (e) {
        // ignore
      }
    }
    if (root.parentNode) root.parentNode.removeChild(root);
  }

  return { destroy };
}
