// Overview view: header with the live sync clock, lenses, metric tiles and the widget grid, all fed by
// GET /api/insights/overview (spec 5.4) and GET /api/jobs (spec 5.5). viewModel() is pure and tested;
// mount() is a thin DOM layer over it that drives one reveal clock.
import { t, reasonText, findingText, TECHNIQUE_IDS } from '../core/i18n.js';
import { fmtNumber, fmtDate, ago } from '../core/format.js';
import { niceDomain } from '../core/geometry.js';
import { createClock, ease } from '../core/motion.js';
import { RANGES } from '../core/scope.js';
import { parseHash, formatHash } from '../core/router.js';
import { prefs, PREF_KEYS, normLang, normTheme } from '../core/prefs.js';
import { api, errorText } from '../core/api.js';
import { h, hasDOM, setAttr, setStyle, setText } from '../widgets/frame.js';
import * as kpi from '../widgets/kpi.js';
import * as radarW from '../widgets/radar.js';
import * as trendW from '../widgets/trend.js';
import * as findingsW from '../widgets/findings.js';
import * as courtW from '../widgets/courtmap.js';
import * as matchesW from '../widgets/matches.js';
import * as pipelineW from '../widgets/pipeline.js';

export const LENS_IDS = Object.freeze(['overview', 'technique', 'tactics']);
export const LENS_TILES = Object.freeze({
  overview: Object.freeze(['technique_index', 'match_win_rate', 'avg_rally', 'rally_win_rate']),
  technique: Object.freeze(['strongest', 'weakest_scored', 'inside_reference', 'techniques_scored']),
  tactics: Object.freeze(['recovery', 'net_share', 'pull_rear', 'unforced_errors']),
});
export const WIDGET_IDS = Object.freeze(['radar', 'trend', 'findings', 'court', 'matches', 'pipeline']);

const DEFAULT_REF = { lo: 70, hi: 100 };
const SEVERITIES = ['watch', 'good', 'info'];
const GLYPH = { watch: '▲', good: '●', info: '◆' };
const MAX_ROWS = 6;
const COURT_W = 6.1;
const HALF_L = 6.7;

const isNum = (v) => typeof v === 'number' && Number.isFinite(v);
const obj = (v) => (v && typeof v === 'object' && !Array.isArray(v) ? v : {});
const num1 = (v) => String(+Number(v).toFixed(1)); // 82 -> "82", 74.56 -> "74.6"
const pct1 = (share) => +(share * 100).toFixed(1);

function capFirst(s, lang) {
  return lang === 'zh' || !s ? s : s.charAt(0).toUpperCase() + s.slice(1);
}

function listJoin(items, lang) {
  return items.join(lang === 'zh' ? '、' : ', ');
}

/** Normalise an insight envelope; anything malformed is `unavailable` + `no_data`. */
function insight(x) {
  const o = obj(x);
  if (!['ok', 'partial', 'unavailable'].includes(o.status)) {
    return { status: 'unavailable', value: null, coverage: null, reason: { code: 'no_data', params: {} } };
  }
  return {
    status: o.status,
    value: o.value && typeof o.value === 'object' ? o.value : null,
    coverage: o.coverage && typeof o.coverage === 'object' ? o.coverage : null,
    reason: o.reason && typeof o.reason === 'object' ? o.reason : null,
  };
}

const usable = (ins) => ins.status !== 'unavailable' && ins.value !== null;

function reasonOf(ins, lang, fallback = 'no_data') {
  return reasonText(ins && ins.reason ? ins.reason : { code: fallback }, lang) || t('reason.unknown', lang);
}

/** Tag on a partial insight: "4 OF 7" for unscored techniques, the signal for estimated rallies, else ESTIMATE. */
function tagFor(ins, lang) {
  if (!ins || ins.status !== 'partial') return '';
  const code = ins.reason && ins.reason.code;
  if (code === 'unscored_technique') {
    const n = ins.value && isNum(ins.value.scored) ? ins.value.scored : ins.coverage && isNum(ins.coverage.n) ? ins.coverage.n : null;
    if (n !== null) return t('tag.n_of_7', lang, { n });
  }
  if (code === 'signal_swing') return t('tag.swing_est', lang);
  if (code === 'signal_degraded') return t('tag.windows', lang);
  return t('tag.estimated', lang);
}

function gapTile(id, label, ins, lang, fallback) {
  const reason = ins && ins.reason ? ins.reason : { code: fallback || 'no_data' };
  return {
    id, kind: 'gap', label, status: 'unavailable', tag: '',
    reason: reasonText(reason, lang) || t('reason.unknown', lang),
    fix: reason.code === 'no_results_entered' ? { href: '#/matches', label: t('gap.fix.enter_results', lang) } : null,
  };
}

function baseTile(id, label, ins, lang) {
  return {
    id, label, status: ins.status, tag: tagFor(ins, lang),
    reason: ins.status === 'partial' ? reasonOf(ins, lang) : '',
    value: null, text: null, decimals: 0, suffix: '', unit: '', delta: null, note: '',
  };
}

function shareTile(id, labelKey, noteKey, ins, lang) {
  const label = t(labelKey, lang);
  if (!usable(ins) || !isNum(ins.value.share)) return gapTile(id, label, ins, lang);
  const v = pct1(ins.value.share);
  return {
    ...baseTile(id, label, ins, lang),
    kind: 'ring', value: v, decimals: 0, suffix: '%',
    ring: { pct: v, lo: null, hi: null, label: t('tile.ring_aria', lang, { label, value: fmtNumber(v, 0) + '%' }) },
    note: t(noteKey, lang),
  };
}

function overviewTiles(kpis, ref, range, lang) {
  const out = [];
  // Technique index: ring against the reference band, delta from the first to the last session.
  {
    const ins = insight(kpis.technique_index);
    const label = t('tile.technique_index', lang);
    if (!usable(ins) || !isNum(ins.value.value)) out.push(gapTile('technique_index', label, ins, lang, 'no_technique'));
    else {
      const v = ins.value.value;
      const dv = ins.value.delta;
      out.push({
        ...baseTile('technique_index', label, ins, lang),
        kind: 'ring', value: v, decimals: 1, unit: '/ 100',
        ring: { pct: v, lo: ref.lo, hi: ref.hi, label: t('tile.ring_ref_aria', lang, { label, value: fmtNumber(v, 1), lo: ref.lo, hi: ref.hi }) },
        delta: isNum(dv)
          ? {
            text: t('delta.pts', lang, { delta: (dv > 0 ? '+' : dv < 0 ? '−' : '±') + fmtNumber(Math.abs(dv), 1) }),
            tone: dv > 0 ? 'good' : dv < 0 ? 'bad' : '',
            dir: dv > 0 ? 'up' : dv < 0 ? 'down' : null,
          }
          : null,
        note: isNum(dv) ? t('tile.vs_start', lang, { range: t('range.' + range, lang) }) : '',
      });
    }
  }
  // Match win rate: from entered results only.
  {
    const ins = insight(kpis.match_win_rate);
    const label = t('tile.match_win_rate', lang);
    if (!usable(ins) || !isNum(ins.value.rate)) out.push(gapTile('match_win_rate', label, ins, lang, 'no_results_entered'));
    else {
      const v = pct1(ins.value.rate);
      out.push({
        ...baseTile('match_win_rate', label, ins, lang),
        kind: 'ring', value: v, decimals: 0, suffix: '%',
        unit: t('tile.wins_of', lang, { wins: ins.value.wins, played: ins.value.played }),
        ring: { pct: v, lo: null, hi: null, label: t('tile.ring_aria', lang, { label, value: fmtNumber(v, 0) + '%' }) },
        note: t('tile.from_results', lang),
      });
    }
  }
  // Average rally: shots when hits exist, else seconds.
  {
    const ins = insight(kpis.avg_rally);
    const label = t('tile.avg_rally', lang);
    const v = ins.value || {};
    if (!usable(ins) || (!isNum(v.shots) && !isNum(v.seconds))) out.push(gapTile('avg_rally', label, ins, lang, 'no_rallies'));
    else {
      const shots = isNum(v.shots);
      out.push({
        ...baseTile('avg_rally', label, ins, lang),
        kind: 'number', value: shots ? v.shots : v.seconds, decimals: 1,
        unit: t(shots ? 'unit.shots' : 'unit.sec', lang),
        note: shots && isNum(v.seconds) ? t('tile.rally_seconds', lang, { sec: fmtNumber(v.seconds, 1) }) : '',
      });
    }
  }
  // Rally win rate: needs a point-outcome signal, which does not exist yet.
  {
    const ins = insight(kpis.rally_win_rate);
    if (usable(ins) && isNum(ins.value.rate)) out.push(shareTile('rally_win_rate', 'tile.rally_win_rate', 'tile.from_results', { ...ins, value: { share: ins.value.rate } }, lang));
    else out.push(gapTile('rally_win_rate', t('tile.rally_win_rate', lang), ins, lang, 'no_point_outcome'));
  }
  return out;
}

function techniqueTiles(radar, kpis, lang) {
  const { lo, hi } = radar.reference;
  const scored = radar.axes.filter((a) => !a.unscored);
  const techIns = insight(kpis.technique_index);
  const noneReason = techIns.reason ? techIns : { reason: { code: 'no_technique' } };
  const out = [];
  if (!scored.length) {
    out.push(gapTile('strongest', t('tile.strongest', lang), noneReason, lang));
    out.push(gapTile('weakest_scored', t('tile.weakest', lang), noneReason, lang));
    out.push(gapTile('inside_reference', t('tile.in_range', lang), noneReason, lang));
  } else {
    const best = scored.reduce((a, b) => (b.score > a.score ? b : a));
    const worst = scored.reduce((a, b) => (b.score < a.score ? b : a));
    const named = (id, labelKey, axis, note) => {
      const label = t(labelKey, lang);
      const inRange = axis.score >= lo;
      return {
        id, kind: 'yardstick', label, status: 'ok', tag: '', reason: '',
        text: axis.label, value: axis.score, decimals: 0, suffix: '', unit: num1(axis.score),
        yard: { score: axis.score, lo, hi, label: t('tile.yard_aria', lang, { label: axis.label, value: num1(axis.score), lo, hi }) },
        delta: { text: t(inRange ? 'tile.in_range_chip' : 'tile.under_range_chip', lang), tone: inRange ? 'good' : 'bad', dir: inRange ? 'up' : 'down' },
        note,
      };
    };
    out.push(named('strongest', 'tile.strongest', best, t('tile.floor', lang, { lo })));
    out.push(named('weakest_scored', 'tile.weakest', worst, t('tile.of_scored', lang, { n: scored.length })));
    const inside = scored.filter((a) => a.score >= lo).length;
    const label = t('tile.in_range', lang);
    out.push({
      id: 'inside_reference', kind: 'ring', label, status: 'ok', tag: '', reason: '',
      value: inside, text: null, decimals: 0, suffix: ` / ${scored.length}`, unit: '',
      ring: { pct: (inside / scored.length) * 100, lo: null, hi: null, label: t('tile.ring_aria', lang, { label, value: `${inside} / ${scored.length}` }) },
      delta: null, note: t('tile.floor', lang, { lo }),
    });
  }
  const n = scored.length;
  const label = t('tile.scored', lang);
  out.push({
    id: 'techniques_scored', kind: 'ring', label, status: n === 7 ? 'ok' : 'partial', tag: '', reason: '',
    value: n, text: null, decimals: 0, suffix: ' / 7', unit: '',
    ring: { pct: (n / 7) * 100, lo: null, hi: null, label: t('tile.ring_aria', lang, { label, value: `${n} / 7` }) },
    delta: n < 7 ? { text: t('tile.need_ranges', lang, { n: 7 - n }), tone: 'mid', dir: null } : { text: t('tile.complete', lang), tone: 'good', dir: null },
    note: t('tile.reference_coverage', lang),
  });
  return out;
}

function tacticsTiles(kpis, lang) {
  const out = [];
  {
    const ins = insight(kpis.recovery);
    const label = t('tile.recovery', lang);
    if (!usable(ins) || !isNum(ins.value.median_sec)) out.push(gapTile('recovery', label, ins, lang, 'no_hits'));
    else {
      const cov = ins.coverage;
      out.push({
        ...baseTile('recovery', label, ins, lang),
        kind: 'number', value: ins.value.median_sec, decimals: 1, unit: t('unit.sec', lang),
        note: cov && isNum(cov.n) && isNum(cov.of) ? t('tile.hits_found', lang, { n: cov.n, of: cov.of }) : '',
      });
    }
  }
  out.push(shareTile('net_share', 'tile.net_share', 'tile.of_classified', insight(kpis.net_share), lang));
  out.push(shareTile('pull_rear', 'tile.pull_rear', 'tile.opponent_drawn_back', insight(kpis.pull_rear), lang));
  {
    const ins = insight(kpis.unforced_errors);
    const label = t('tile.unforced_errors', lang);
    if (usable(ins) && isNum(ins.value.count)) {
      out.push({ ...baseTile('unforced_errors', label, ins, lang), kind: 'number', value: ins.value.count, decimals: 0 });
    } else out.push(gapTile('unforced_errors', label, ins, lang, 'no_point_outcome'));
  }
  return out;
}

function radarModel(ov, kpis, lang) {
  const r = obj(ov.radar);
  const rr = obj(r.reference);
  const reference = { lo: isNum(rr.lo) ? rr.lo : DEFAULT_REF.lo, hi: isNum(rr.hi) ? rr.hi : DEFAULT_REF.hi };
  const src = Array.isArray(r.axes) ? r.axes : [];
  const axes = TECHNIQUE_IDS.map((id) => {
    const a = obj(src.find((x) => x && x.id === id));
    const score = isNum(a.score) ? a.score : null;
    const count = isNum(a.count) ? a.count : 0;
    const label = t('technique.' + id, lang);
    const unscored = score === null;
    const below = !unscored && score < reference.lo;
    const readout = unscored
      ? { head: t('radar.readout.unscored_head', lang, { technique: label }), body: t('radar.readout.unscored_body', lang, { n: fmtNumber(count) }) }
      : {
        head: t('radar.readout.scored_head', lang, { technique: label, score: num1(score) }),
        body: t('radar.readout.scored_body', lang, {
          lo: reference.lo, hi: reference.hi, n: fmtNumber(count),
          gap: below ? t('radar.gap.under', lang, { n: num1(reference.lo - score) }) : t('radar.gap.inside', lang),
        }),
      };
    return { id, label, score, count, unscored, below, status: typeof a.status === 'string' ? a.status : unscored ? 'unavailable' : 'ok', valueText: unscored ? '—' : num1(score), readout };
  });
  const scored = axes.filter((a) => !a.unscored);
  const unscoredNames = axes.filter((a) => a.unscored).map((a) => a.label);
  const withCounts = axes.filter((a) => a.unscored && a.count > 0).map((a) => a.label);
  const techIns = insight(kpis.technique_index);
  return {
    reference,
    axes,
    scored: scored.length,
    index: usable(techIns) && isNum(techIns.value.value) ? techIns.value.value : null,
    readout: {
      head: t('radar.readout.count', lang, { n: scored.length }),
      body: scored.length < 7 && withCounts.length
        ? t('radar.readout.hint_unscored', lang, { names: listJoin(withCounts, lang) })
        : t('radar.readout.hint', lang),
    },
    aria: t('radar.aria', lang, { list: scored.map((a) => `${a.label} ${num1(a.score)}`).join(', ') })
      + (unscoredNames.length ? ' ' + t('radar.aria_unscored', lang, { names: listJoin(unscoredNames, lang) }) : ''),
  };
}

function trendModel(ov, reference, range, lang) {
  const tr = obj(ov.trend);
  const points = (Array.isArray(tr.points) ? tr.points : [])
    .filter((p) => p && isNum(p.index))
    .map((p) => ({ date: p.date, stem: p.stem, index: p.index, dateLabel: fmtDate(p.date, lang, { year: false }) }));
  const bIns = insight(tr.benchmark);
  const benchmark = usable(bIns) && isNum(bIns.value.index) ? bIns.value.index : null;
  const last = points.length ? points[points.length - 1].index : null;
  const domain = niceDomain([...points.map((p) => p.index), benchmark, reference.lo].filter(isNum));
  const sub = t('trend.sub', lang, { range: capFirst(t('range.span.' + range, lang), lang), n: points.length });
  const lo = reference.lo;
  let gapText = '';
  if (last !== null) {
    gapText = last >= lo
      ? t('trend.gap.above', lang, { n: fmtNumber(last - lo, 1), floor: lo })
      : t('trend.gap.below', lang, { n: fmtNumber(lo - last, 1), floor: lo });
  }
  const ariaParams = { sub, last: last === null ? '—' : fmtNumber(last, 1), bench: benchmark === null ? '' : fmtNumber(benchmark, 1), lo, hi: reference.hi };
  return {
    points,
    reference: { lo: reference.lo, hi: reference.hi },
    benchmark,
    benchmarkReason: benchmark === null ? reasonOf(bIns, lang, 'no_benchmark') : '',
    last,
    domain,
    sub,
    gapText,
    median: benchmark === null ? '' : t('trend.median', lang, { value: fmtNumber(benchmark, 1) }),
    hint: t('trend.hint', lang),
    aria: benchmark === null ? t('trend.aria_nobench', lang, ariaParams) : t('trend.aria', lang, ariaParams),
    empty: points.length < 2,
    emptyText: t('trend.empty', lang),
  };
}

function findingsModel(ov, lang) {
  const items = (Array.isArray(ov.findings) ? ov.findings : []).slice(0, 6).map((f) => {
    const { title, detail } = findingText(f, lang);
    const severity = SEVERITIES.includes(f && f.severity) ? f.severity : 'info';
    const p = obj(f && f.params);
    const techCode = f && (f.code === 'technique_below_reference' || f.code === 'technique_strongest');
    return { severity, glyph: GLYPH[severity], title, detail, axis: techCode && TECHNIQUE_IDS.includes(p.technique) ? p.technique : null };
  });
  return { items, count: t('findings.count', lang, { n: items.length }), empty: t('findings.empty', lang) };
}

function courtMode(ins, mode, lang) {
  const g = ins.value && Array.isArray(ins.value.grid) ? ins.value.grid : null;
  const shareAt = (r, c) => (g && Array.isArray(g[r]) && isNum(g[r][c]) && g[r][c] > 0 ? g[r][c] : 0);
  let max = 0;
  let best = null;
  if (g) {
    for (let r = 0; r < 11; r++) {
      for (let c = 0; c < 10; c++) {
        const v = shareAt(r, c);
        if (v > max) { max = v; best = { row: r, col: c }; }
      }
    }
  }
  const available = ins.status !== 'unavailable' && max > 0;
  const modeAria = t(`heat.mode.${mode}_aria`, lang);
  const cells = [];
  for (let r = 0; r < 11; r++) {
    for (let c = 0; c < 10; c++) {
      const share = available ? shareAt(r, c) : 0;
      cells.push({
        row: r, col: c, share, alpha: available ? share / max : 0,
        readout: t('heat.cell', lang, {
          x: ((c + 0.5) * (COURT_W / 10)).toFixed(1), y: ((r + 0.5) * (HALF_L / 11)).toFixed(1), pct: (share * 100).toFixed(1),
        }),
      });
    }
  }
  const densest = available && best
    ? { row: best.row, col: best.col, x: ((best.col + 0.5) * (COURT_W / 10)).toFixed(1), y: ((best.row + 0.5) * (HALF_L / 11)).toFixed(1), pct: (max * 100).toFixed(1) }
    : null;
  return {
    mode,
    label: t('heat.' + mode, lang),
    sub: t('heat.sub.' + mode, lang),
    status: ins.status,
    available,
    reason: available ? (ins.status === 'partial' ? reasonOf(ins, lang) : '') : reasonOf(ins, lang, mode === 'pull' ? 'no_hits' : 'no_positions'),
    tag: available ? tagFor(ins, lang) : '',
    cells,
    densest,
    readout: densest ? t('heat.densest', lang, { pct: densest.pct }) : '',
    aria: densest ? t('heat.aria', lang, { mode: modeAria, x: densest.x, y: densest.y, pct: densest.pct }) : t('heat.aria_empty', lang, { mode: modeAria }),
  };
}

const nameOf = (x) => (typeof x === 'string' ? x : x && typeof x.name === 'string' ? x.name : '');

function matchesModel(ov, lang) {
  const list = Array.isArray(ov.matches) ? ov.matches : [];
  const rows = list.slice(0, MAX_ROWS).filter((m) => m && typeof m.stem === 'string').map((m) => {
    const opp = nameOf(m.opponent);
    const status = m.needs_review ? 'needs_review' : ['analysed', 'processing', 'new'].includes(m.status) ? m.status : 'analysed';
    const result = m.result === 'W' || m.result === 'L' ? m.result : null;
    return {
      stem: m.stem,
      label: opp ? t('matches.vs', lang, { name: opp }) : m.stem,
      aria: t('matches.peek_aria', lang, { name: opp || m.stem }),
      date: fmtDate(m.date, lang, { year: false }),
      result,
      resultText: result ? t('result.' + result, lang) : t('matches.add_result', lang),
      tech: fmtNumber(m.tech_index, 1),
      status,
      statusText: t('status.' + status, lang),
      statusTone: status === 'analysed' ? 'good' : status === 'needs_review' ? 'mid' : status === 'processing' ? 'cyan' : '',
    };
  });
  return { rows, total: list.length, emptyTitle: t('matches.empty.title', lang), emptyBody: t('matches.empty.body', lang) };
}

function headerModel(ov, kpis, lang) {
  const sc = obj(ov.scope);
  const name = nameOf(sc.athlete) || nameOf(sc.squad) || t('scope.library', lang);
  const stems = isNum(sc.stems) ? sc.stems : 0;
  const st = insight(kpis.strokes);
  const total = usable(st) && isNum(st.value.total) ? st.value.total : null;
  const matchesText = t('scope.matches_count', lang, { n: fmtNumber(stems) });
  const summary = total === null ? matchesText : t('overview.summary', lang, { matches: matchesText, strokes: t('scope.strokes_count', lang, { n: fmtNumber(total) }) });
  const processing = (Array.isArray(ov.matches) ? ov.matches : []).filter((m) => m && m.status === 'processing').length;
  return {
    name,
    suffix: t('overview.title_suffix', lang),
    eyebrow: t('overview.eyebrow', lang),
    summary,
    processing: processing ? t('scope.processing', lang, { n: processing }) : '',
  };
}

/**
 * Pure view-model for the Overview: {lens, lenses, header, tiles, radar, trend, findings, court, matches, empty,
 * emptyKind, emptyTitle, emptyBody, newAnalysis}. Tolerates a missing or partial payload.
 */
export function viewModel(overview, lens, lang) {
  const ov = obj(overview);
  const kpis = obj(ov.kpis);
  const sc = obj(ov.scope);
  const L = LENS_IDS.includes(lens) ? lens : 'overview';
  const range = RANGES.includes(sc.range) ? sc.range : '30d';
  const radar = radarModel(ov, kpis, lang);
  let tiles;
  if (L === 'technique') tiles = techniqueTiles(radar, kpis, lang);
  else if (L === 'tactics') tiles = tacticsTiles(kpis, lang);
  else tiles = overviewTiles(kpis, radar.reference, range, lang);
  const mv = obj(ov.movement);
  const empty = !(isNum(sc.stems) && sc.stems > 0);
  const emptyKind = sc.athlete || sc.squad ? 'scope' : 'library';
  return {
    lens: L,
    range,
    lenses: LENS_IDS.map((id) => ({ id, label: t('lens.' + id, lang) })),
    header: headerModel(ov, kpis, lang),
    tiles,
    radar,
    trend: trendModel(ov, radar.reference, range, lang),
    findings: findingsModel(ov, lang),
    court: { position: courtMode(insight(mv.position), 'position', lang), pull: courtMode(insight(mv.pull), 'pull', lang) },
    matches: matchesModel(ov, lang),
    empty,
    emptyKind,
    emptyTitle: t(emptyKind === 'library' ? 'empty.library.title' : 'matches.empty.title', lang),
    emptyBody: t(emptyKind === 'library' ? 'empty.library.body' : 'matches.empty.body', lang),
    newAnalysis: t('btn.new_analysis', lang),
  };
}

// ---------- layout (customize mode, localStorage kestrel_layout_v1) ----------

/** {order, hidden} with unknown ids dropped, duplicates removed and missing widgets appended. */
export function normalizeLayout(raw) {
  const src = obj(raw);
  const order = [];
  for (const id of Array.isArray(src.order) ? src.order : []) if (WIDGET_IDS.includes(id) && !order.includes(id)) order.push(id);
  for (const id of WIDGET_IDS) if (!order.includes(id)) order.push(id);
  const hidden = [];
  for (const id of Array.isArray(src.hidden) ? src.hidden : []) if (WIDGET_IDS.includes(id) && !hidden.includes(id)) hidden.push(id);
  return { order, hidden };
}

/** Move a widget one place up (dir -1) or down (+1); a no-op at either end. Returns a new layout. */
export function moveWidget(layout, id, dir) {
  const l = normalizeLayout(layout);
  const i = l.order.indexOf(id);
  const j = i + (dir < 0 ? -1 : 1);
  if (i < 0 || j < 0 || j >= l.order.length) return l;
  const order = l.order.slice();
  [order[i], order[j]] = [order[j], order[i]];
  return { order, hidden: l.hidden };
}

/** Show a hidden widget or hide a shown one. Returns a new layout. */
export function toggleWidget(layout, id) {
  const l = normalizeLayout(layout);
  if (!WIDGET_IDS.includes(id)) return l;
  const hidden = l.hidden.includes(id) ? l.hidden.filter((x) => x !== id) : [...l.hidden, id];
  return { order: l.order, hidden };
}

// ---------- DOM ----------

const TAB_W = 110;
const TAB_STEP = 114;
const WIDGET_FLEX = { radar: '5 1 380px', trend: '7 1 460px', findings: '4 1 320px', court: '4 1 300px', matches: '5 1 480px', pipeline: '3 1 280px' };

function reducedMotion() {
  try {
    return typeof matchMedia === 'function' && matchMedia('(prefers-reduced-motion: reduce)').matches;
  } catch (e) {
    return false;
  }
}

function pushQuery(router, query) {
  const hash = formatHash('overview', {}, query);
  try {
    if (router && typeof router.navigate === 'function') {
      router.navigate('overview', {}, query);
      return;
    }
    if (router && typeof router.go === 'function') {
      router.go(hash);
      return;
    }
    if (typeof location !== 'undefined' && location.hash !== hash) location.hash = hash;
  } catch (e) {
    // navigation failure leaves the in-page state as is
  }
}

/**
 * Mount the Overview into `container`. opts: {query, store, router, onOpenMatch?}.
 * Scope (athlete, squad, range) comes from the query, else the store; lens from the query.
 * Returns {destroy(), setQuery(query)}.
 */
export function mount(container, opts = {}) {
  const noop = { destroy() {}, setQuery() {} };
  if (!container || !hasDOM()) return noop;
  const { store, router } = opts;

  const state = {
    query: { ...obj(opts.query) },
    data: null,
    jobs: null,
    error: null,
    lastOk: null,
    loadedKey: null,
    editing: false,
    courtMode: 'position',
    layout: normalizeLayout(prefs.getJSON(PREF_KEYS.layout, null)),
    destroyed: false,
  };
  let widgets = []; // [{update(p), destroy?()}]
  let refs = {}; // radar, pipeline, tabs ...
  let lastP = 1;
  let reqId = 0;
  let ctl = null;
  let jobsTimer = null;
  let activeJobs = new Set();

  const storeState = () => {
    try {
      return store && typeof store.get === 'function' ? obj(store.get()) : {};
    } catch (e) {
      return {};
    }
  };
  const lang = () => normLang(storeState().lang || prefs.get(PREF_KEYS.lang));
  const theme = () => normTheme(storeState().theme || prefs.get(PREF_KEYS.theme));
  function scope() {
    const s = storeState();
    const q = state.query;
    const range = RANGES.includes(q.range) ? q.range : RANGES.includes(s.range) ? s.range : '30d';
    return {
      athlete: q.athlete || s.athlete || '',
      squad: q.squad || s.squad || '',
      range,
      lens: LENS_IDS.includes(q.lens) ? q.lens : 'overview',
    };
  }
  const scopeKey = (sc) => [sc.athlete, sc.squad, sc.range].join('|');

  const root = h('div', { class: 'page ov', 'data-view': 'overview' });
  container.appendChild(root);

  const clock = createClock({ reduced: reducedMotion, onFrame: (p) => { lastP = p; paint(p); } });

  function paint(p) {
    if (refs.head) {
      const e = ease(Math.max(0, Math.min(1, p / 0.4)));
      setStyle(refs.head, 'opacity', e >= 1 ? '' : e.toFixed(3));
      setStyle(refs.head, 'transform', e >= 1 ? '' : `translateY(${((1 - e) * 10).toFixed(1)}px)`);
    }
    for (const w of widgets) {
      try {
        w.update(p);
      } catch (err) {
        console.error('widget update failed', err);
      }
    }
  }

  function reveal() {
    clock.start();
  }

  function settle() {
    clock.cancel();
    lastP = 1;
    paint(1);
  }

  // ----- header -----
  function syncText() {
    const L = lang();
    const eyebrow = t('overview.eyebrow', L);
    if (state.lastOk === null) return `${eyebrow} · ${t('sync.never', L)}`;
    return `${eyebrow} · ${t('sync.synced', L, { time: ago((Date.now() - state.lastOk) / 1000) })}`;
  }

  function buildHeader(vm) {
    const L = lang();
    const sync = h('span', null, syncText());
    refs.sync = sync;
    const left = h('div', { class: 'ov-head-l' }, [
      h('div', { class: 'mono ov-eyebrow' }, [h('span', { class: 'ov-sync-dot', 'aria-hidden': 'true' }), sync]),
      h('h1', { class: 'page-title serif' }, [vm.header.name, h('span', { class: 'ov-title-suffix' }, vm.header.suffix)]),
      state.data
        ? h('div', { class: 'ov-summary' }, [
          h('span', null, vm.header.summary),
          vm.header.processing ? h('span', { class: 'mono ov-pill' }, vm.header.processing) : null,
        ])
        : null,
    ]);
    const canCustomize = state.data && !vm.empty;
    const right = h('div', { class: 'ov-actions' }, [
      canCustomize
        ? h('button', {
          type: 'button', class: 'ov-btn' + (state.editing ? ' on' : ''), 'aria-pressed': state.editing ? 'true' : 'false',
          'data-ctl': 'customize', onclick: toggleEditing,
        }, t(state.editing ? 'layout.done' : 'layout.customize', L))
        : null,
      h('a', { href: '#/new', class: 'ov-btn primary' }, t('btn.new_analysis', L)),
    ]);
    const head = h('div', { class: 'ov-head' }, [left, right]);
    refs.head = head;
    return head;
  }

  function buildAlert() {
    const L = lang();
    if (!state.error || !state.data) return null;
    const when = state.lastOk === null ? '' : new Date(state.lastOk).toLocaleTimeString(L === 'zh' ? 'zh-CN' : 'en-GB', { hour: '2-digit', minute: '2-digit' });
    return h('div', { class: 'ov-alert', role: 'alert' }, [
      h('strong', null, t('error.refresh.title', L)),
      h('span', null, t('error.refresh.body', L, { time: when })),
      h('button', { type: 'button', class: 'ov-btn small', onclick: () => load({ reveal: false }) }, t('btn.retry', L)),
    ]);
  }

  // ----- lenses -----
  function buildTabs(vm) {
    const L = lang();
    const idx = LENS_IDS.indexOf(vm.lens);
    const slider = h('div', { class: 'ov-tab-slider', 'aria-hidden': 'true', style: { width: TAB_W + 'px', transform: `translateX(${idx * TAB_STEP}px)` } });
    const tabs = vm.lenses.map((ln, i) => h('button', {
      type: 'button', role: 'tab', class: 'ov-tab', id: 'ov-tab-' + ln.id,
      'aria-selected': i === idx ? 'true' : 'false', 'aria-controls': 'ov-tiles', tabindex: i === idx ? '0' : '-1',
      style: { width: TAB_W + 'px' },
      onclick: () => setLens(ln.id),
      onkeydown: (e) => {
        const d = e.key === 'ArrowRight' ? 1 : e.key === 'ArrowLeft' ? -1 : 0;
        if (!d) return;
        e.preventDefault();
        const next = LENS_IDS[(LENS_IDS.indexOf(scope().lens) + d + LENS_IDS.length) % LENS_IDS.length];
        setLens(next);
        const btn = refs.tabs && refs.tabs[LENS_IDS.indexOf(next)];
        if (btn && typeof btn.focus === 'function') btn.focus();
      },
    }, ln.label));
    refs.tabs = tabs;
    refs.tabSlider = slider;
    return h('div', { class: 'ov-tabs', role: 'tablist', 'aria-label': t('lens.aria', L) }, [slider, ...tabs]);
  }

  function paintTabs(lensId) {
    const idx = LENS_IDS.indexOf(lensId);
    setStyle(refs.tabSlider, 'transform', `translateX(${idx * TAB_STEP}px)`);
    (refs.tabs || []).forEach((b, i) => {
      setAttr(b, 'aria-selected', i === idx ? 'true' : 'false');
      setAttr(b, 'tabindex', i === idx ? '0' : '-1');
    });
  }

  // ----- tiles and widgets -----
  function ctxFor(slot, extra) {
    return {
      lang: lang(),
      theme: theme(),
      slot,
      onOpenMatch,
      onFocusAxis,
      requestReveal: reveal,
      onCourtMode: (m) => { state.courtMode = m; },
      ...extra,
    };
  }

  function buildTiles(vm) {
    const section = h('section', { class: 'ov-tiles', id: 'ov-tiles', role: 'tabpanel', 'aria-label': t('tiles.aria', lang()) });
    const built = vm.tiles.map((tile, i) => kpi.build({ ...tile, flex: i === 0 ? '1.25 1 220px' : '1 1 220px' }, ctxFor(i * 0.7)));
    built.forEach((b) => section.appendChild(b.el));
    refs.tilesSection = section;
    refs.tileWidgets = built;
    return section;
  }

  function buildWidget(id, vm, ctx) {
    switch (id) {
      case 'radar': return radarW.build(vm.radar, ctx);
      case 'trend': return trendW.build({ ...vm.trend, legendName: vm.header.name }, ctx);
      case 'findings': return findingsW.build(vm.findings, ctx);
      case 'court': return courtW.build({ ...vm.court, mode: state.courtMode }, ctx);
      case 'matches': return matchesW.build(vm.matches, ctx);
      case 'pipeline': return pipelineW.build(pipelineW.jobsModel(state.jobs, lang()), ctx);
      default: return null;
    }
  }

  function buildGrid(vm) {
    const grid = h('div', { class: 'ov-grid' });
    const visible = state.layout.order.filter((id) => state.editing || !state.layout.hidden.includes(id));
    const built = [];
    visible.forEach((id, pos) => {
      const edit = {
        editing: state.editing,
        id,
        hidden: state.layout.hidden.includes(id),
        first: pos === 0,
        last: pos === visible.length - 1,
        onMove: (dir) => changeLayout(moveWidget(state.layout, id, dir), `${id}:${dir < 0 ? 'up' : 'down'}`),
        onToggle: () => changeLayout(toggleWidget(state.layout, id), `${id}:toggle`),
      };
      let w = null;
      try {
        w = buildWidget(id, vm, ctxFor(1 + pos, { edit }));
      } catch (err) {
        console.error('widget build failed', id, err);
      }
      if (!w || !w.el) return;
      if (w.el.style && !w.el.style.flex) w.el.style.flex = WIDGET_FLEX[id] || '1 1 300px';
      grid.appendChild(w.el);
      built.push(w);
      refs[id] = w;
    });
    refs.gridWidgets = built;
    return grid;
  }

  function buildEditBanner() {
    if (!state.editing) return null;
    const L = lang();
    return h('div', { class: 'ov-edit', role: 'status' }, [
      h('strong', null, t('layout.editing_title', L)),
      h('span', null, t('layout.editing_body', L)),
      h('button', { type: 'button', class: 'ov-btn small cyan', onclick: () => changeLayout(normalizeLayout(null), 'customize') }, t('layout.reset', L)),
    ]);
  }

  function buildStatusCard(kind) {
    const L = lang();
    if (kind === 'error') {
      return h('div', { class: 'card ov-state', role: 'alert' }, [
        h('div', { class: 'k-label' }, t('error.load.title', L)),
        h('div', { class: 'ov-state-body' }, errorText(state.error, L)),
        h('button', { type: 'button', class: 'ov-btn small', onclick: () => load({ reveal: true }) }, t('btn.retry', L)),
      ]);
    }
    return h('div', { class: 'card ov-state ov-loading', 'aria-busy': 'true' }, [
      h('div', { class: 'k-label' }, t('state.loading', L)),
      h('div', { class: 'ov-skel' }),
      h('div', { class: 'ov-skel short' }),
    ]);
  }

  function buildEmpty(vm) {
    return h('div', { class: 'card ov-state ov-empty' }, [
      h('div', { class: 'ov-empty-title' }, vm.emptyTitle),
      h('div', { class: 'ov-state-body' }, vm.emptyBody),
      h('a', { href: '#/new', class: 'ov-btn primary' }, vm.newAnalysis),
    ]);
  }

  function destroyWidgets() {
    for (const w of widgets) if (w && typeof w.destroy === 'function') { try { w.destroy(); } catch (e) { /* ignore */ } }
    widgets = [];
  }

  function render({ reveal: doReveal = true } = {}) {
    if (state.destroyed) return;
    const vm = viewModel(state.data, scope().lens, lang());
    destroyWidgets();
    refs = {};
    root.textContent = '';
    root.appendChild(buildHeader(vm));
    const alert = buildAlert();
    if (alert) root.appendChild(alert);
    if (!state.data) {
      root.appendChild(buildStatusCard(state.error ? 'error' : 'loading'));
    } else if (vm.empty) {
      root.appendChild(buildEmpty(vm));
    } else {
      const banner = buildEditBanner();
      if (banner) root.appendChild(banner);
      root.appendChild(buildTabs(vm));
      root.appendChild(buildTiles(vm));
      root.appendChild(buildGrid(vm));
      widgets = [...refs.tileWidgets, ...refs.gridWidgets];
    }
    if (doReveal) reveal();
    else settle();
  }

  function renderTiles() {
    if (!refs.tilesSection || !state.data) return;
    const vm = viewModel(state.data, scope().lens, lang());
    const old = refs.tilesSection;
    const fresh = buildTiles(vm);
    if (old.parentNode) old.parentNode.replaceChild(fresh, old);
    widgets = [...refs.tileWidgets, ...(refs.gridWidgets || [])];
    paintTabs(vm.lens);
  }

  // ----- interactions -----
  function onOpenMatch(stem) {
    if (typeof opts.onOpenMatch === 'function') {
      opts.onOpenMatch(stem);
      return;
    }
    try {
      if (router && typeof router.navigate === 'function') router.navigate('matches', { stem }, {});
      else if (typeof location !== 'undefined') location.hash = formatHash('matches', { stem });
    } catch (e) {
      // ignore
    }
  }

  function onFocusAxis(id) {
    const r = refs.radar;
    if (!r || typeof r.focus !== 'function') return;
    r.focus(id);
    if (r.el && typeof r.el.scrollIntoView === 'function') {
      try {
        r.el.scrollIntoView({ block: 'nearest', behavior: reducedMotion() ? 'auto' : 'smooth' });
      } catch (e) {
        // older browsers: no options object
      }
    }
  }

  function setLens(id) {
    if (!LENS_IDS.includes(id) || id === scope().lens) return;
    state.query = { ...state.query, lens: id };
    renderTiles();
    reveal();
    pushQuery(router, state.query);
  }

  function toggleEditing() {
    state.editing = !state.editing;
    render({ reveal: false });
    focusCtl('customize');
  }

  function changeLayout(next, focusKey) {
    state.layout = normalizeLayout(next);
    prefs.setJSON(PREF_KEYS.layout, state.layout);
    render({ reveal: false });
    focusCtl(focusKey);
  }

  function focusCtl(key) {
    if (!key || typeof root.querySelector !== 'function') return;
    const safe = String(key).replace(/["\\]/g, '');
    const el = root.querySelector(`[data-ctl="${safe}"]`);
    if (el && !el.disabled && typeof el.focus === 'function') el.focus();
  }

  // ----- data -----
  async function load({ reveal: doReveal = true } = {}) {
    const sc = scope();
    const mine = ++reqId;
    if (ctl && typeof ctl.abort === 'function') ctl.abort();
    ctl = typeof AbortController === 'function' ? new AbortController() : null;
    try {
      const data = await api.overview({ athlete: sc.athlete, squad: sc.squad, range: sc.range }, ctl ? { signal: ctl.signal } : undefined);
      if (state.destroyed || mine !== reqId) return;
      state.data = data && typeof data === 'object' ? data : {};
      state.error = null;
      state.lastOk = Date.now();
      state.loadedKey = scopeKey(sc);
      render({ reveal: doReveal });
    } catch (err) {
      if (state.destroyed || mine !== reqId) return;
      if (err && err.kind === 'aborted') return;
      state.error = err || { kind: 'network' };
      // Keep the last good data on screen; only the header gains the retry.
      render({ reveal: !state.data });
    }
  }

  async function pollJobs() {
    jobsTimer = null;
    try {
      const list = await api.jobs();
      if (state.destroyed) return;
      const jobs = Array.isArray(list) ? list : [];
      const active = new Set(jobs.filter((j) => j && pipelineW.ACTIVE_STATUSES.includes(j.status)).map((j) => j.id));
      const finished = [...activeJobs].some((id) => !active.has(id));
      activeJobs = active;
      state.jobs = jobs;
      if (refs.pipeline && typeof refs.pipeline.setJobs === 'function') refs.pipeline.setJobs(pipelineW.jobsModel(jobs, lang()));
      if (finished) load({ reveal: false });
    } catch (e) {
      // the pipeline card keeps its last list; the next poll retries
    } finally {
      if (!state.destroyed) jobsTimer = setTimeout(pollJobs, activeJobs.size ? 2000 : 15000);
    }
  }

  function setQuery(query) {
    if (state.destroyed) return;
    const before = scope();
    state.query = { ...obj(query) };
    const after = scope();
    if (scopeKey(after) !== scopeKey(before) || (state.loadedKey !== null && scopeKey(after) !== state.loadedKey)) {
      load({ reveal: true });
      return;
    }
    if (after.lens !== before.lens) {
      renderTiles();
      reveal();
    }
  }

  const onHash = () => {
    if (typeof location === 'undefined') return;
    const route = parseHash(location.hash);
    if (route.name === 'overview') setQuery(route.query);
  };
  if (typeof window !== 'undefined' && typeof window.addEventListener === 'function') window.addEventListener('hashchange', onHash);

  let unsubscribe = null;
  if (store && typeof store.subscribe === 'function') {
    unsubscribe = store.subscribe((next, prev) => {
      const n = obj(next);
      const p = obj(prev);
      if (n.lang !== p.lang) {
        render({ reveal: false });
        return;
      }
      if (n.athlete !== p.athlete || n.squad !== p.squad || n.range !== p.range) {
        if (scopeKey(scope()) !== state.loadedKey) load({ reveal: true });
      }
    });
  }

  const syncTimer = setInterval(() => setText(refs.sync, syncText()), 1000);

  render({ reveal: false });
  load({ reveal: true });
  pollJobs();

  function destroy() {
    if (state.destroyed) return;
    state.destroyed = true;
    clock.cancel();
    if (ctl && typeof ctl.abort === 'function') ctl.abort();
    if (jobsTimer) clearTimeout(jobsTimer);
    clearInterval(syncTimer);
    if (typeof window !== 'undefined' && typeof window.removeEventListener === 'function') window.removeEventListener('hashchange', onHash);
    if (typeof unsubscribe === 'function') unsubscribe();
    destroyWidgets();
    if (root.parentNode) root.parentNode.removeChild(root);
  }

  return { destroy, setQuery };
}
