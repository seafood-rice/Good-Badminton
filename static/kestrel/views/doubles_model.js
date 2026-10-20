// Pure doubles view-models shared by the Overview "Doubles players" widget, the Matches editor and peek, and the
// workspace (spec 2026-10-20 section 5). DOM-free. Inputs are players.json, hits.json and the `doubles` insight
// (spec 2, 4.1, 4.2), any of which may be absent or malformed: a bad field becomes null, never a made-up number.
// Honesty: every who-hit figure is flagged unvalidated whatever the payload claims, a free-text pipeline reason is
// never echoed (status codes are localized instead), and no model here carries a score or a stroke type.
import { t, reasonText } from '../core/i18n.js';
import { fmtNumber, fmtDate, isApproxDate } from '../core/format.js';

export const IDENTITY_IDS = Object.freeze(['P1', 'P2', 'P3', 'P4']);
/** The identity video's four fixed colours (visualization/doubles_render.py PLAYER_COLORS_BGR, as RGB hex). */
export const IDENTITY_COLORS = Object.freeze({ P1: '#008CFF', P2: '#78DCFF', P3: '#FF5000', P4: '#FFB478' });
export const EVIDENCE = Object.freeze(['shuttle', 'swing', 'single_candidate', 'none']);
/** players.json statuses; `missing` (no readable file) and `unknown` (an unrecognised status) are the UI's own. */
export const IDENTITY_STATUSES = Object.freeze(['ok', 'degraded', 'degraded_teams', 'error', 'missing', 'unknown']);
/** A hit window around a rally (the workspace's RALLY_PAD_SEC rule). */
export const HIT_PAD_SEC = 0.5;

const NAMED_EVIDENCE = ['shuttle', 'swing'];
const FILE_STATUSES = ['ok', 'degraded', 'degraded_teams', 'error'];
const MAX_PLAYERS = 8;
const MAX_HITS = 20000;
const MAX_RUNS = 6;
const NAME_MAX = 60;
const ID_RE = /^P\d{1,2}$/;

const isNum = (v) => typeof v === 'number' && Number.isFinite(v);
const obj = (v) => (v && typeof v === 'object' && !Array.isArray(v) ? v : {});
const clamp01 = (x) => (x < 0 ? 0 : x > 1 ? 1 : x);
const round2 = (x) => Math.round(x * 100) / 100;
const inRange = (v, lo, hi) => (isNum(v) && v >= lo && v <= hi ? v : null);
const count = (v) => (Number.isInteger(v) && v >= 0 && v <= 1e9 ? v : null);
const teamOf = (v) => (v === 1 || v === 2 ? v : null);
const idNum = (id) => Number(String(id).slice(1));

function cleanName(v) {
  if (typeof v !== 'string') return null;
  const s = v.trim();
  // eslint-disable-next-line no-control-regex
  if (!s || /[\u0000-\u001f\u007f]/.test(s)) return null;
  return s.length > NAME_MAX ? s.slice(0, NAME_MAX) : s;
}

/** Normalise an insight envelope; anything malformed is `unavailable` + `no_data`. */
function envelope(x) {
  const o = obj(x);
  if (!['ok', 'partial', 'unavailable'].includes(o.status)) return { status: 'unavailable', value: null, reason: { code: 'no_data', params: {} } };
  return {
    status: o.status,
    value: o.value && typeof o.value === 'object' && !Array.isArray(o.value) ? o.value : null,
    reason: o.reason && typeof o.reason === 'object' ? o.reason : null,
  };
}

/** Identity colour (one of the four fixed colours) or null for anything that is not P1-P4. */
export function identityColor(id) {
  return typeof id === 'string' && Object.prototype.hasOwnProperty.call(IDENTITY_COLORS, id) ? IDENTITY_COLORS[id] : null;
}

function byTeamThenId(a, b) {
  const rank = (p) => (p.team === 1 ? 0 : p.team === 2 ? 1 : 2);
  return rank(a) - rank(b) || idNum(a.id) - idNum(b.id);
}

/**
 * Pure: a players list (players.json `players`, the insight's, or an overview run's) -> normalised players
 * {id, team 1|2|null, name, athlete, coverage 0-1, frames, distance_m, avg_speed_mps, max_speed_mps, hits, hits_named},
 * every number null when missing or out of range; duplicate ids dropped; ordered team 1, team 2, unknown, by id.
 */
export function doublesPlayers(list) {
  const src = Array.isArray(list) ? list : [];
  const seen = new Set();
  const out = [];
  for (const p of src) {
    if (out.length >= MAX_PLAYERS) break;
    if (!p || typeof p !== 'object' || Array.isArray(p)) continue;
    const id = typeof p.id === 'string' && ID_RE.test(p.id) ? p.id : null;
    if (!id || seen.has(id)) continue;
    seen.add(id);
    const hits = count(p.hits);
    const named = count(p.hits_named);
    out.push({
      id,
      team: teamOf(p.team),
      name: cleanName(p.name),
      athlete: cleanName(p.athlete),
      coverage: inRange(p.coverage, 0, 1),
      frames: count(p.frames),
      distance_m: inRange(p.distance_m, 0, 1e6),
      avg_speed_mps: inRange(p.avg_speed_mps, 0, 100),
      max_speed_mps: inRange(p.max_speed_mps, 0, 100),
      hits,
      hits_named: named !== null && (hits === null || named <= hits) ? named : null,
    });
  }
  return out.sort(byTeamThenId);
}

/** The mapped athlete's name, else the identity's own name, else the identity id. */
export function playerLabel(player) {
  const p = obj(player);
  return p.athlete || p.name || (typeof p.id === 'string' && p.id) || '—';
}

/** "Team 1" text, or "Team unknown" for anything but 1 or 2. */
export function teamText(team, lang) {
  const id = teamOf(team);
  return id === null ? t('dbl.team_unknown', lang) : t('dbl.team', lang, { n: id });
}

/**
 * Pure: a team label from its members' names ("Alice / P2"). `team` is 1, 2 or a {id, players:[ids]} entry; members
 * without a player entry fall back to their identity id. A team with no known members reads "Team 1"; a missing
 * team (degraded_teams: team null) reads "Team unknown".
 */
export function teamLabel(team, players, lang) {
  const tm = typeof team === 'number' ? { id: team } : obj(team);
  const id = teamOf(tm.id);
  if (id === null) return t('dbl.team_unknown', lang);
  const ps = Array.isArray(players) ? players : [];
  const ids = Array.isArray(tm.players) && tm.players.length
    ? tm.players.filter((x) => typeof x === 'string' && ID_RE.test(x)).slice(0, MAX_PLAYERS)
    : ps.filter((p) => p && p.team === id).map((p) => p.id);
  if (!ids.length) return t('dbl.team', lang, { n: id });
  return ids.map((pid) => playerLabel(ps.find((p) => p && p.id === pid) || { id: pid })).join(' / ');
}

/**
 * Pure: the match title. With team assignments, {a, b, vs: true, text: "A / B vs. C / D"} with team `first` (the
 * athlete's, default 1) first. Without any (degraded teams) the players are listed: {vs: false, text: "P1 · P2"}.
 */
export function teamsTitle(players, lang, first) {
  const ps = Array.isArray(players) ? players : [];
  if (ps.some((p) => p && teamOf(p.team) !== null)) {
    const f = teamOf(first) || 1;
    const a = teamLabel(f, ps, lang);
    const b = teamLabel(f === 1 ? 2 : 1, ps, lang);
    return { a, b, vs: true, text: t('mv.vs', lang, { a, b }) };
  }
  const text = ps.map(playerLabel).join(' · ');
  return { a: text, b: '', vs: false, text };
}

function pctText(share) {
  return isNum(share) ? fmtNumber(clamp01(share) * 100, 0) + '%' : '—';
}

/** Record editor option label: "P1 · Team 1 · coverage 83%" (team unknown and coverage omitted when missing). */
export function identityOptionLabel(player, lang) {
  const p = obj(player);
  const parts = [typeof p.id === 'string' ? p.id : '—', teamText(p.team, lang)];
  if (isNum(p.coverage)) parts.push(t('dbl.coverage', lang, { pct: pctText(p.coverage) }));
  return parts.join(' · ');
}

/** A hitter confidence as a clamped percentage, a dash when missing. */
export function confidenceText(c) {
  return isNum(c) ? pctText(c) : '—';
}

function namedShareOf(share, named, total) {
  if (isNum(share) && share >= 0 && share <= 1) return share;
  if (total !== null && total > 0 && named !== null && named <= total) return named / total;
  return null;
}

/**
 * Pure: hits.json (or a bare hits array) -> the who-hit strip and hits table. A hit is named when it has a known
 * hitter id and shuttle or swing evidence (tracking/doubles.py _is_named); unnamed and single-candidate hits are
 * muted. An id that is not one of the run's identities (or P1-P4 when the players list is empty) is no name.
 * {validated: false, total, named, singleCandidate, namedShare, truncated, perPlayer{id:{hits, named}},
 *  items:[{i, frame, time, side, hitterId, team, confidence, confidenceText, evidence, named, muted, color}]}
 * sorted by time (hits without a time last).
 */
export function whoHitModel(payload, players, { fps } = {}) {
  const src = Array.isArray(payload) ? payload : Array.isArray(obj(payload).hits) ? payload.hits : [];
  const ps = Array.isArray(players) ? players : [];
  const known = new Map(ps.filter((p) => p && typeof p.id === 'string').map((p) => [p.id, p]));
  const validId = (v) => typeof v === 'string' && (known.size ? known.has(v) : IDENTITY_IDS.includes(v));
  const f = isNum(fps) && fps > 0 ? fps : null;
  const items = [];
  let truncated = false;
  for (let k = 0; k < src.length; k++) {
    const x = src[k];
    if (!x || typeof x !== 'object' || Array.isArray(x)) continue;
    if (items.length >= MAX_HITS) { truncated = true; break; }
    const frame = isNum(x.frame) && x.frame >= 0 ? x.frame : null;
    const time = isNum(x.time_sec) && x.time_sec >= 0 ? round2(x.time_sec) : frame !== null && f ? round2(frame / f) : null;
    const hitterId = validId(x.hitter_id) ? x.hitter_id : null;
    const evidence = EVIDENCE.includes(x.hitter_evidence) ? x.hitter_evidence : 'none';
    const p = hitterId ? known.get(hitterId) : null;
    const team = hitterId === null ? null : p && p.team !== null && p.team !== undefined ? teamOf(p.team) : teamOf(x.hitter_team);
    const named = hitterId !== null && NAMED_EVIDENCE.includes(evidence);
    const confidence = isNum(x.hitter_confidence) ? clamp01(x.hitter_confidence) : null;
    items.push({
      i: 0, order: k, frame, time,
      side: x.hitter === 'upper' || x.hitter === 'lower' ? x.hitter : null,
      hitterId, team, confidence, confidenceText: confidenceText(confidence), evidence, named,
      muted: !named,
      color: identityColor(hitterId),
    });
  }
  items.sort((a, b) => {
    if (a.time === null || b.time === null) return (a.time === null) - (b.time === null) || a.order - b.order;
    return a.time - b.time || (a.frame || 0) - (b.frame || 0) || a.order - b.order;
  });
  const perPlayer = {};
  let named = 0;
  let singleCandidate = 0;
  items.forEach((it, idx) => {
    it.i = idx + 1;
    delete it.order;
    if (it.named) named += 1;
    if (it.evidence === 'single_candidate') singleCandidate += 1;
    if (it.hitterId) {
      const pp = perPlayer[it.hitterId] || (perPlayer[it.hitterId] = { hits: 0, named: 0 });
      pp.hits += 1;
      if (it.named) pp.named += 1;
    }
  });
  return { validated: false, total: items.length, named, singleCandidate, namedShare: items.length ? named / items.length : null, truncated, perPlayer, items };
}

/** Pure: the hits inside a rally window {start, end} plus HIT_PAD_SEC either side; all hits for no window. */
export function hitsInWindow(items, unit) {
  const list = Array.isArray(items) ? items : [];
  const u = obj(unit);
  if (!isNum(u.start) || !isNum(u.end)) return list;
  return list.filter((h) => h.time !== null && h.time >= u.start - HIT_PAD_SEC && h.time <= u.end + HIT_PAD_SEC);
}

// ---------- identity status ----------

function statusTone(status) {
  return status === 'ok' ? 'good' : status === 'degraded' || status === 'degraded_teams' ? 'mid' : status === 'error' || status === 'missing' ? 'bad' : '';
}

function statusFields(status, lang) {
  const s = IDENTITY_STATUSES.includes(status) ? status : 'unknown';
  return { status: s, statusText: t('dbl.status.' + s, lang), detail: s === 'ok' ? '' : t('dbl.status_detail.' + s, lang), tone: statusTone(s) };
}

/** The identity status a `doubles` insight envelope stands for (its value's status, else its reason code). */
function insightStatus(ins) {
  const v = obj(ins.value);
  if (FILE_STATUSES.includes(v.status)) return v.status;
  const code = obj(ins.reason).code;
  if (ins.status === 'unavailable') return code === 'doubles_error' ? 'error' : 'missing';
  if (code === 'doubles_degraded' && FILE_STATUSES.includes(obj(obj(ins.reason).params).status)) return obj(ins.reason).params.status;
  return 'unknown';
}

// ---------- per-player rows ----------

function playerRow(p, lang) {
  const share = namedShareOf(null, p.hits_named, p.hits);
  return {
    id: p.id,
    label: playerLabel(p),
    team: p.team,
    teamText: teamText(p.team, lang),
    color: identityColor(p.id),
    coverage: p.coverage,
    coverageText: pctText(p.coverage),
    distance_m: p.distance_m,
    distanceText: p.distance_m === null ? '—' : `${fmtNumber(p.distance_m, 0)} ${t('unit.m', lang)}`,
    avgSpeedText: p.avg_speed_mps === null ? '—' : fmtNumber(p.avg_speed_mps, 1),
    maxSpeedText: p.max_speed_mps === null ? '—' : fmtNumber(p.max_speed_mps, 1),
    hits: p.hits,
    hitsText: p.hits === null ? '—' : fmtNumber(p.hits),
    hits_named: p.hits_named,
    namedShare: share,
    namedText: pctText(share),
  };
}

function summaryModel(raw, lang) {
  const s = obj(raw);
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return null;
  const distance = inRange(s.distance_m, 0, 1e7);
  const hits = count(s.hits);
  const named = count(s.hits_named);
  const share = namedShareOf(s.named_share, named, hits);
  const avg = inRange(s.avg_speed_mps, 0, 100);
  const max = inRange(s.max_speed_mps, 0, 100);
  if ([distance, hits, avg, max].every((v) => v === null)) return null;
  return {
    matches: count(s.matches) || 0,
    distance_m: distance,
    distanceText: distance === null ? '—' : `${fmtNumber(distance, 0)} ${t('unit.m', lang)}`,
    avgSpeedText: avg === null ? '—' : fmtNumber(avg, 1),
    maxSpeedText: max === null ? '—' : fmtNumber(max, 1),
    hits,
    hitsText: hits === null ? '—' : fmtNumber(hits),
    hits_named: named !== null && (hits === null || named <= hits) ? named : null,
    namedShare: share,
    namedText: pctText(share),
  };
}

function dateRank(iso) {
  return typeof iso === 'string' && /^\d{4}-\d{2}-\d{2}/.test(iso) ? iso.slice(0, 10) : '';
}

/**
 * Pure: the overview response's `doubles` insight (spec 4.2) -> the "Doubles players" widget model
 * {show, status, reason, matches, sub, unvalidated: true, unvalidatedText, athlete: summary|null, runs, more}.
 * Runs are newest first (undated last), one per stem; each has its identity status, its localized detail when not
 * ok, the team title and per-player rows. `show` is true only with doubles matches in scope.
 */
export function doublesRows(raw, lang) {
  const ins = envelope(raw);
  const v = obj(ins.value);
  const matches = count(v.matches) || 0;
  const seen = new Set();
  const runs = [];
  for (const r of Array.isArray(v.runs) ? v.runs : []) {
    if (!r || typeof r !== 'object' || typeof r.stem !== 'string' || !r.stem || seen.has(r.stem)) continue;
    seen.add(r.stem);
    const players = doublesPlayers(r.players);
    const title = teamsTitle(players, lang);
    runs.push({
      stem: r.stem,
      dateIso: dateRank(r.date) || null,
      date: fmtDate(r.date, lang, { year: false }),
      dateApprox: isApproxDate(r.date, r.date_source),
      ...statusFields(r.status, lang),
      unvalidated: true,
      title: title.text || r.stem,
      players: players.map((p) => playerRow(p, lang)),
    });
  }
  runs.sort((a, b) => (b.dateIso || '').localeCompare(a.dateIso || '') || (a.stem < b.stem ? -1 : a.stem > b.stem ? 1 : 0));
  const athlete = summaryModel(v.athlete, lang);
  return {
    show: ins.status !== 'unavailable' && matches > 0,
    status: ins.status,
    reason: ins.status === 'ok' ? '' : reasonText(ins.reason || { code: 'no_data' }, lang) || t('reason.unknown', lang),
    matches,
    sub: t('dbl.widget.sub', lang, { n: fmtNumber(matches) }),
    unvalidated: true,
    unvalidatedText: t('dbl.unvalidated', lang),
    athlete: athlete ? { ...athlete, label: t('dbl.athlete_summary', lang, { n: fmtNumber(athlete.matches) }) } : null,
    runs: runs.slice(0, MAX_RUNS),
    more: Math.max(0, runs.length - MAX_RUNS),
  };
}

/**
 * Pure: Tactics-lens tiles from the selected athlete's doubles summary (spec 5): {distance, named}, each a tile
 * view-model or null. The named-hits tile is always tagged unvalidated; no summary (no athlete mapped) gives none.
 */
export function doublesTiles(raw, lang) {
  const ins = envelope(raw);
  const s = ins.status === 'unavailable' ? null : summaryModel(obj(ins.value).athlete, lang);
  if (!s) return { distance: null, named: null };
  const partialReason = ins.status === 'partial' ? reasonText(ins.reason || { code: 'no_data' }, lang) || t('reason.unknown', lang) : '';
  const base = { status: ins.status, reason: partialReason, text: null, delta: null, suffix: '', unit: '', decimals: 0 };
  const distance = s.distance_m === null ? null : {
    ...base, id: 'doubles_distance', kind: 'number', label: t('dbl.tile.distance', lang), tag: t('dbl.tag', lang),
    value: s.distance_m, unit: t('unit.m', lang), note: t('dbl.tile.across', lang, { n: fmtNumber(s.matches) }),
  };
  let named = null;
  if (s.namedShare !== null && s.hits) {
    const label = t('dbl.tile.hits_named', lang);
    const pct = +(s.namedShare * 100).toFixed(1);
    named = {
      ...base, id: 'doubles_hits_named', kind: 'ring', label, tag: t('dbl.tag_unvalidated', lang),
      value: pct, suffix: '%',
      unit: s.hits_named === null ? '' : t('dbl.tile.named_of', lang, { named: fmtNumber(s.hits_named), total: fmtNumber(s.hits) }),
      ring: { pct, lo: null, hi: null, label: t('tile.ring_aria', lang, { label, value: fmtNumber(pct, 0) + '%' }) },
      note: t('dbl.unvalidated', lang),
    };
  }
  return { distance, named };
}

// ---------- record, peek and workspace ----------

function registryName(athletes, id) {
  const a = (Array.isArray(obj(athletes).athletes) ? athletes.athletes : []).find((x) => x && x.id === id);
  return a && typeof a.name === 'string' && a.name ? a.name : null;
}

/** {P1: name|null, ...} from a doubles record's identity map (registry name for an athlete id, else the typed name). */
function recordNames(record, athletes) {
  const map = obj(obj(record).players);
  const out = {};
  for (const pid of IDENTITY_IDS) {
    const e = obj(map[pid]);
    out[pid] = (typeof e.athlete_id === 'string' && (registryName(athletes, e.athlete_id) || null)) || cleanName(e.name) || null;
  }
  return out;
}

function athletePid(record, athleteId) {
  if (!athleteId) return null;
  const map = obj(obj(record).players);
  return IDENTITY_IDS.find((pid) => obj(map[pid]).athlete_id === athleteId) || null;
}

/** Players with the record's names applied; identities only the record knows are added without a team. */
function withNames(players, names) {
  const out = players.map((p) => ({ ...p, athlete: names[p.id] || p.athlete || null }));
  for (const pid of IDENTITY_IDS) {
    if (names[pid] && !out.some((p) => p.id === pid)) out.push(doublesPlayers([{ id: pid, athlete: names[pid] }])[0]);
  }
  return out.sort(byTeamThenId);
}

/** W/L from the record's winning team relative to the athlete's team; sets [own, opp], else team-1-first. */
function recordResult(record, team) {
  const res = obj(obj(record).result);
  const winnerTeam = teamOf(res.winner_team);
  const sets = (Array.isArray(res.sets) ? res.sets : []).filter((p) => Array.isArray(p) && p.length === 2 && p.every(isNum)).slice(0, 5);
  const own = teamOf(team);
  return {
    winnerTeam,
    result: own !== null && winnerTeam !== null ? (winnerTeam === own ? 'W' : 'L') : null,
    sets: own === 2 ? sets.map((p) => [p[1], p[0]]) : sets.map((p) => [p[0], p[1]]),
    setsOwnFirst: own !== null,
  };
}

/**
 * Pure: the peek's doubles block from the match record and the per-video sidecar's `doubles` insight:
 * {status, statusText, detail, tone, players, title, teams[{id, label}], athletePlayer, team, winnerTeam, result,
 *  sets, hits{total, named, namedShare, singleCandidate}, namedText, unvalidated: true}. No scores.
 */
export function doublesPeekModel({ record, insights, athletes, athleteId } = {}, lang) {
  const ins = envelope(obj(insights).doubles);
  const v = obj(ins.value);
  const players = withNames(doublesPlayers(v.players), recordNames(record, athletes));
  const pid = athletePid(record, athleteId);
  const me = players.find((p) => p.id === pid);
  const team = me ? me.team : null;
  const h = obj(v.hits);
  const total = count(h.total);
  const named = count(h.named);
  const namedShare = ins.status === 'unavailable' ? null : namedShareOf(h.named_share, named, total);
  return {
    ...statusFields(insightStatus(ins), lang),
    players: players.map((p) => playerRow(p, lang)),
    title: teamsTitle(players, lang, team),
    teams: [1, 2].map((id) => ({ id, label: teamLabel(id, players, lang) })),
    athletePlayer: pid,
    team,
    ...recordResult(record, team),
    hits: { total, named: named !== null && (total === null || named <= total) ? named : null, namedShare, singleCandidate: count(h.single_candidate) },
    namedText: pctText(namedShare),
    unvalidated: true,
  };
}

/** Pure: the video files to try, in order: the identity video only when its status is ok, then the annotated one. */
export function doublesVideoFiles(stem, identityVideo) {
  const s = String(stem);
  const annotated = [`detect_${s}.mp4`, `detect_${s}_h264.mp4`];
  return obj(identityVideo).status === 'ok' ? [`detect_${s}_players.mp4`, ...annotated] : annotated;
}

/**
 * Pure: the workspace's doubles model. playersFile is players.json (null when absent or unreadable), hits is
 * hits.json, insights the per-video sidecar (its `doubles` insight is the fallback for players and the identity
 * video), record the match record. {status, statusText, detail, tone, players (rows), title, teams, identityVideoOk,
 * whoHit, athletePlayer, team, winnerTeam, result, sets, setsOwnFirst, unvalidated: true}.
 */
export function doublesWorkspaceModel({ playersFile, hits, insights, fps, record, athletes, athleteId, lang } = {}) {
  const file = playersFile && typeof playersFile === 'object' && !Array.isArray(playersFile) ? playersFile : null;
  const ins = envelope(obj(insights).doubles);
  const iv = obj(ins.value);
  let status;
  let list;
  let video;
  if (file) {
    status = FILE_STATUSES.includes(file.status) ? file.status : 'unknown';
    list = file.players;
    video = file.identity_video;
  } else {
    status = insightStatus(ins);
    list = iv.players;
    video = iv.identity_video;
  }
  const players = withNames(doublesPlayers(list), recordNames(record, athletes));
  const pid = athletePid(record, athleteId);
  const me = players.find((p) => p.id === pid);
  const team = me ? me.team : null;
  return {
    ...statusFields(status, lang),
    players: players.map((p) => playerRow(p, lang)),
    title: teamsTitle(players, lang, team),
    teams: [1, 2].map((id) => ({ id, label: teamLabel(id, players, lang) })),
    identityVideoOk: obj(video).status === 'ok',
    whoHit: whoHitModel(hits, players, { fps }),
    athletePlayer: pid,
    team,
    ...recordResult(record, team),
    unvalidated: true,
  };
}
