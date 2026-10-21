// Matches view (#/matches, spec 6.5): the scope's analysed matches with result chips and an inline match-record
// editor (PUT /api/matches/<stem>); a row opens the workspace with the container transform. Also the match peek
// drawer the overview opens (buildMatchPeek). matchRows / validateRecordForm / formFromRecord / peekModel are
// pure and tested. Results come only from the record the user enters: there is no point outcome.
// Split-match linking (spec 2026-10-21): a match recorded as several videos is one expandable row ("N parts"
// chip, chevron, one sub-row per part); the editor links a video to another match or unlinks it
// (PUT /api/matches/<part> {part_of, sides_swapped} / {}). cleanParts / linkInfo / linkBody / eligibleParts /
// eligiblePrimaries are pure and tested.
import { t } from '../core/i18n.js';
import { fmtDate, fmtNumber, isApproxDate } from '../core/format.js';
import { createClock, local } from '../core/motion.js';
import { formatHash } from '../core/router.js';
import { api, errorText } from '../core/api.js';
import { h, hasDOM, revealStyle, approxMark } from '../widgets/frame.js';
import { rallyCountText } from './workspace.js';
import { IDENTITY_IDS, doublesPeekModel, identityOptionLabel, identityColor } from './doubles_model.js';

const SIDES = ['upper', 'lower'];
const OTHER = '__other';
const MAX_SETS = 5;
const NAME_MAX = 60;
const LONG = { timeout: 60000 };

const isNum = (v) => typeof v === 'number' && Number.isFinite(v);
const obj = (v) => (v && typeof v === 'object' && !Array.isArray(v) ? v : {});
const str = (v) => (typeof v === 'string' ? v : '');
const otherSide = (s) => (s === 'upper' ? 'lower' : 'upper');
const PART_STATUSES = ['analysed', 'processing', 'new'];

function realDate(s) {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(s);
  if (!m) return false;
  const d = new Date(Date.UTC(+m[1], +m[2] - 1, +m[3]));
  return d.getUTCFullYear() === +m[1] && d.getUTCMonth() === +m[2] - 1 && d.getUTCDate() === +m[3];
}

function setsText(sets) {
  return (Array.isArray(sets) ? sets : []).filter((p) => Array.isArray(p) && p.length === 2).map((p) => `${p[0]}-${p[1]}`).join(', ');
}

/**
 * Pure: overview payload -> one row per stem (the first unit wins; the aggregator lists a stem's lower side
 * first when no athlete is selected). Row: {stem, date, side, athlete, opponent, result 'W'|'L'|null,
 * sets [[own, opp]], setsText, techIndex, status, doubles, parts, recordDate}. `result` is relative to `athlete`
 * and comes from the record; `doubles` is true only for an exact `true` (a doubles run has no per-player score).
 * `parts` is the split-match part list (see cleanParts; [] for an unlinked video and always for doubles);
 * `recordDate` is true when the date comes from the user's own record.
 */
export function matchRows(overview) {
  const list = Array.isArray(obj(overview).matches) ? overview.matches : [];
  const seen = new Set();
  const rows = [];
  for (const m of list) {
    if (!m || typeof m !== 'object' || typeof m.stem !== 'string' || !m.stem || seen.has(m.stem)) continue;
    seen.add(m.stem);
    const parts = m.doubles === true ? [] : cleanParts(m.parts);
    // A split match reads as its least finished part: processing / new beat needs_review (an unlinked row keeps the old order).
    const unfinished = parts.length && (m.status === 'processing' || m.status === 'new');
    const status = unfinished ? m.status : m.needs_review ? 'needs_review' : ['analysed', 'processing', 'new'].includes(m.status) ? m.status : 'analysed';
    const sets = Array.isArray(m.sets) ? m.sets.filter((p) => Array.isArray(p) && p.length === 2 && p.every(isNum)) : [];
    const doubles = m.doubles === true;
    const row = {
      stem: m.stem,
      date: typeof m.date === 'string' ? m.date : null,
      dateApprox: isApproxDate(m.date, m.date_source),
      side: SIDES.includes(m.side) ? m.side : null,
      athlete: str(m.athlete) || null,
      opponent: str(m.opponent) || null,
      result: m.result === 'W' || m.result === 'L' ? m.result : null,
      sets,
      setsText: setsText(sets),
      // A doubles run has no per-player technique score, whatever the payload holds.
      techIndex: !doubles && isNum(m.tech_index) ? m.tech_index : null,
      status,
      doubles,
      parts,
      recordDate: m.date_source === 'record',
    };
    if (doubles) {
      // Spec 4.2: the athlete's identity and team, the partner's name and the opposing team's names.
      row.athletePlayer = typeof m.athlete_player === 'string' && IDENTITY_IDS.includes(m.athlete_player) ? m.athlete_player : null;
      row.team = m.team === 1 || m.team === 2 ? m.team : null;
      row.partner = str(m.partner) || null;
      // Spec 4.2: both teams' display names, team 1 first ("A / B"), null without team info.
      row.teams = Array.isArray(m.teams) && m.teams.length === 2 && m.teams.some((x) => str(x))
        ? m.teams.map((x) => str(x).trim().slice(0, 200) || null)
        : null;
    }
    rows.push(row);
  }
  return rows;
}

/**
 * Pure: a split match's part list from the overview row -> [{stem, side, status, tech_index, needs_review}].
 * Anything but an array gives []; entries without a stem (and repeated stems) are dropped; `side` is 'upper' |
 * 'lower' | null, `status` 'analysed' | 'processing' | 'new' (default 'analysed'), `tech_index` a finite number or
 * null, `needs_review` true only for an exact `true`. The first entry is the primary (part 1).
 */
export function cleanParts(parts) {
  if (!Array.isArray(parts)) return [];
  const seen = new Set();
  const out = [];
  for (const p of parts) {
    if (!p || typeof p !== 'object' || typeof p.stem !== 'string' || !p.stem || seen.has(p.stem)) continue;
    seen.add(p.stem);
    out.push({
      stem: p.stem,
      side: SIDES.includes(p.side) ? p.side : null,
      status: PART_STATUSES.includes(p.status) ? p.status : 'analysed',
      tech_index: isNum(p.tech_index) ? p.tech_index : null,
      needs_review: p.needs_review === true,
    });
  }
  return out;
}

/** Pure: the status a part's chip shows (a part that needs review reads as such, like a row does). */
export function partStatus(part) {
  const p = obj(part);
  return p.needs_review === true ? 'needs_review' : PART_STATUSES.includes(p.status) ? p.status : 'analysed';
}

/**
 * Pure: the link facts of a GET /api/matches/<stem> body -> {partOf: string|null, sidesSwapped: bool,
 * parts: [{stem, sides_swapped}]}. A part carries `part_of` (and `sides_swapped`); a primary with parts carries
 * `parts`. A normal record has neither, and a part never reports parts.
 */
export function linkInfo(record) {
  const r = obj(record);
  const partOf = str(r.part_of).trim() || null;
  const seen = new Set();
  const parts = [];
  for (const p of partOf ? [] : Array.isArray(r.parts) ? r.parts : []) {
    if (!p || typeof p !== 'object' || typeof p.stem !== 'string' || !p.stem || seen.has(p.stem)) continue;
    seen.add(p.stem);
    parts.push({ stem: p.stem, sides_swapped: p.sides_swapped === true });
  }
  return { partOf, sidesSwapped: partOf ? r.sides_swapped === true : false, parts };
}

/** Pure: the PUT body that links a video to a primary; null for a blank primary. `sides_swapped` is an exact bool. */
export function linkBody(partOf, sidesSwapped) {
  const p = str(partOf).trim();
  return p ? { part_of: p, sides_swapped: sidesSwapped === true } : null;
}

/**
 * Pure: does this overview row's video hold its own match record? The overview does not say so directly, so it is
 * inferred: a record-sourced date, a named athlete or opponent, an entered result or set scores. (The server has the last word:
 * a wrong guess comes back as a link error.)
 */
export function rowHasRecord(row) {
  const r = obj(row);
  return r.recordDate === true || !!r.athlete || !!r.opponent || !!r.result || (Array.isArray(r.sets) && r.sets.length > 0);
}

/** Pure: is this validated singles record blank (no date, sides or result)? Saving it clears the video's record. */
export function isBlankRecord(record) {
  const r = obj(record);
  return !r.date && !r.upper && !r.lower && !r.result;
}

const analysedRow = (r) => r.status === 'analysed' || r.status === 'needs_review';

/** Pure: "A vs. B · stem" for a row with names, else the stem: how a link picker names a video. */
export function linkLabel(row, lang) {
  const r = obj(row);
  const { title } = rowTitle(r, lang);
  return title && title !== r.stem ? `${title} · ${r.stem}` : str(r.stem);
}

/**
 * Pure: the videos a primary's editor may link as its parts: other analysed singles videos that are neither
 * parts of a match (those are never rows) nor primaries that already have parts. -> [{stem, label, hasRecord}];
 * `hasRecord` ones are listed but cannot be linked until their record is cleared.
 */
export function eligibleParts(rows, primaryStem, lang) {
  const out = [];
  for (const r of Array.isArray(rows) ? rows : []) {
    if (!r || typeof r.stem !== 'string' || r.stem === primaryStem || r.doubles || !analysedRow(r)) continue;
    if (Array.isArray(r.parts) && r.parts.length) continue;
    out.push({ stem: r.stem, label: linkLabel(r, lang), hasRecord: rowHasRecord(r) });
  }
  return out;
}

/**
 * Pure: the matches a record-less video may be linked to: other singles videos that hold a record. A part is
 * never a row, so it is never offered. -> [{stem, label, parts}] (`parts` = how many videos the match has now).
 */
export function eligiblePrimaries(rows, partStem, lang) {
  const out = [];
  for (const r of Array.isArray(rows) ? rows : []) {
    if (!r || typeof r.stem !== 'string' || r.stem === partStem || r.doubles || !rowHasRecord(r)) continue;
    out.push({ stem: r.stem, label: linkLabel(r, lang), parts: Array.isArray(r.parts) ? r.parts.length : 0 });
  }
  return out;
}

/** "21-18, 19-21" -> {sets: [[21, 18], [19, 21]], bad}. Pairs keep the order typed; at most MAX_SETS. */
function parseSets(text) {
  const raw = str(text).trim();
  const sets = [];
  if (!raw) return { sets, bad: false };
  for (const p of raw.split(/[,;，、]/).map((x) => x.trim())) {
    const m = /^(\d{1,2})\s*[-–—:]\s*(\d{1,2})$/.exec(p);
    if (!m) return { sets: [], bad: true };
    sets.push([Number(m[1]), Number(m[2])]);
  }
  return sets.length > MAX_SETS ? { sets: [], bad: true } : { sets, bad: false };
}

function cleanSide(side, errors, key) {
  const s = obj(side);
  const id = str(s.athlete_id).trim();
  if (id) return { athlete_id: id };
  const name = str(s.name).trim();
  if (!name) return null;
  // eslint-disable-next-line no-control-regex
  if (name.length > NAME_MAX || /[\u0000-\u001f\u007f]/.test(name)) {
    errors[key] = 'record.err.name';
    return null;
  }
  return { name };
}

/**
 * Pure: editor form {date, upper:{athlete_id, name}, lower:{...}, winner: ''|'upper'|'lower', sets: "21-18, 19-21"}
 * -> {record: {date, upper, lower, result} | null, errors: {field: i18n key}}. Blank sides map to null; set scores
 * are [upper, lower] pairs; a winner is required when sets are present.
 */
export function validateRecordForm(form) {
  const f = obj(form);
  const errors = {};
  const dateRaw = str(f.date).trim();
  let date = null;
  if (dateRaw) {
    if (realDate(dateRaw)) date = dateRaw;
    else errors.date = 'record.err.date';
  }
  const upper = cleanSide(f.upper, errors, 'upper');
  const lower = cleanSide(f.lower, errors, 'lower');
  if (upper && lower && upper.athlete_id && upper.athlete_id === lower.athlete_id) errors.lower = 'record.err.same';

  const setsRaw = str(f.sets).trim();
  const sets = [];
  if (setsRaw) {
    const parts = setsRaw.split(/[,;，、]/).map((p) => p.trim());
    for (const p of parts) {
      const m = /^(\d{1,2})\s*[-–—:]\s*(\d{1,2})$/.exec(p);
      if (!m) { errors.sets = 'record.err.sets'; break; }
      sets.push([Number(m[1]), Number(m[2])]);
    }
    if (!errors.sets && sets.length > MAX_SETS) errors.sets = 'record.err.sets';
  }
  const winner = SIDES.includes(f.winner) ? f.winner : null;
  if (sets.length && !winner && !errors.sets) errors.winner = 'record.err.winner';
  const result = winner ? { winner, sets: errors.sets ? [] : sets } : null;
  if (Object.keys(errors).length) return { record: null, errors };
  return { record: { date, upper, lower, result }, errors };
}

/** Pure: a stored record (or {}) -> the editor form validateRecordForm reads. */
export function formFromRecord(record) {
  const r = obj(record);
  const side = (s) => {
    const e = obj(r[s]);
    return { athlete_id: str(e.athlete_id), name: str(e.athlete_id) ? '' : str(e.name) };
  };
  const res = obj(r.result);
  return {
    date: str(r.date),
    upper: side('upper'),
    lower: side('lower'),
    winner: SIDES.includes(res.winner) ? res.winner : '',
    sets: setsText(res.sets),
  };
}

/**
 * Pure: doubles editor form {date, players: {P1: {athlete_id, name}, ...}, winner_team: ''|'1'|'2', sets: "21-18"}
 * -> {record: {date, format: 'doubles', players: {P1..P4: entry|null}, result: {winner_team, sets}|null} | null,
 * errors: {field: i18n key}} (spec 3). Blank identities map to null; sets are team-1-first pairs; a winning team is
 * required when sets are present; the same athlete may not be mapped to two identities.
 */
export function validateDoublesRecordForm(form) {
  const f = obj(form);
  const errors = {};
  const dateRaw = str(f.date).trim();
  let date = null;
  if (dateRaw) {
    if (realDate(dateRaw)) date = dateRaw;
    else errors.date = 'record.err.date';
  }
  const map = obj(f.players);
  const players = {};
  const used = new Set();
  for (const pid of IDENTITY_IDS) {
    const entry = cleanSide(map[pid], errors, pid);
    if (entry && entry.athlete_id) {
      if (used.has(entry.athlete_id)) errors[pid] = 'dbl.record.err.same';
      used.add(entry.athlete_id);
    }
    players[pid] = entry;
  }
  const parsed = parseSets(f.sets);
  if (parsed.bad) errors.sets = 'record.err.sets';
  const wt = String(f.winner_team === undefined || f.winner_team === null ? '' : f.winner_team);
  const winner = wt === '1' ? 1 : wt === '2' ? 2 : null;
  if (parsed.sets.length && winner === null) errors.winner = 'record.err.winner';
  if (Object.keys(errors).length) return { record: null, errors };
  return { record: { date, format: 'doubles', players, result: winner ? { winner_team: winner, sets: parsed.sets } : null }, errors };
}

/** Pure: a stored record (or {}) -> the doubles editor form; a singles record's sides and winner are not carried over. */
export function formFromDoublesRecord(record) {
  const r = obj(record);
  const map = obj(r.players);
  const players = {};
  for (const pid of IDENTITY_IDS) {
    const e = obj(map[pid]);
    players[pid] = { athlete_id: str(e.athlete_id), name: str(e.athlete_id) ? '' : str(e.name) };
  }
  const res = obj(r.result);
  return {
    date: str(r.date),
    players,
    winner_team: res.winner_team === 1 || res.winner_team === 2 ? String(res.winner_team) : '',
    sets: res.winner_team === 1 || res.winner_team === 2 ? setsText(res.sets) : '',
  };
}

/**
 * Pure: the doubles editor's winning-team options [{value: '1'|'2', label: "Team 1 · A / B"}]. Each member reads
 * as the identity picker's current choice (registry name for an athlete id, else the typed name), else its
 * players.json label (`label`: the name, else the id), else its id. A team with no known members: "Team 1".
 */
export function winnerTeamOptions(players, picks, athletes, lang) {
  const ps = Array.isArray(players) ? players : [];
  const pk = obj(picks);
  const nameOf = (p) => {
    const e = obj(pk[p.id]);
    const aid = str(e.athlete_id).trim();
    return (aid && registryName(athletes, aid)) || str(e.name).trim().slice(0, NAME_MAX) || str(p.label) || str(p.id);
  };
  return [1, 2].map((n) => {
    const names = ps.filter((p) => p && typeof p.id === 'string' && p.team === n).map(nameOf).filter(Boolean);
    const team = t('dbl.team', lang, { n });
    return { value: String(n), label: names.length ? t('dbl.record.team_option', lang, { team, names: names.join(' / ') }) : team };
  });
}

function registryName(athletes, id) {
  const a = (Array.isArray(obj(athletes).athletes) ? athletes.athletes : []).find((x) => x && x.id === id);
  return a && typeof a.name === 'string' && a.name ? a.name : null;
}

/**
 * Pure: the peek summary for one stem from its match record, its insights sidecar and the athlete registry.
 * {stem, date, side, first, names{upper, lower}, winner, result 'W'|'L'|null, sets (oriented to `first`), techIndex, techBySide,
 *  doubles, rally{count, label, suppressed, signal, failed, available}, strokes{classified,total}|null}.
 * A doubles run (sidecar `match_format: 'doubles'`) has no per-player score: techIndex / techBySide stay null whatever
 * the sidecar holds, and the peek says so instead.
 */
export function peekModel({ stem, record, insights, athletes, athleteId } = {}, lang) {
  const rec = obj(record);
  const ins = obj(insights);
  const out = singlesPeek(stem, rec, ins, athletes, athleteId);
  // A doubles run (sidecar) or a doubles record: teams, identity status and who-hit shares; never a score.
  if (ins.match_format === 'doubles' || rec.format === 'doubles') {
    const dbl = doublesPeekModel({ record: rec, insights: ins, athletes, athleteId }, lang);
    return {
      ...out,
      doubles: true,
      techIndex: null,
      techBySide: { upper: null, lower: null },
      side: null,
      winner: null,
      result: dbl.result,
      sets: dbl.sets,
      dbl,
    };
  }
  return { ...out, dbl: null };
}

/**
 * Pure: the peek model painted before the record and sidecar arrive. `seed` is {stem} or an overview match row:
 * a doubles row (`doubles: true`) gives the doubles layout straight away, titled like its Matches row
 * ("A / B vs. C / D" from `teams` or the athlete's perspective); anything else the singles one.
 */
export function peekSeed(seed, stem, athletes, athleteId, lang) {
  const sd = obj(seed);
  if (sd.doubles !== true) return peekModel({ stem, record: {}, insights: null, athletes, athleteId }, lang);
  const base = peekModel({ stem, record: { format: 'doubles' }, insights: null, athletes, athleteId }, lang);
  const [row] = matchRows({ matches: [{ ...sd, stem: str(stem) || str(sd.stem) || '?' }] });
  const title = row ? rowTitle(row, lang).title : '';
  if (title && title !== row.stem) base.dbl.title = { ...base.dbl.title, text: title, vs: true };
  return base;
}

function singlesPeek(stem, rec, ins, athletes, athleteId) {
  const names = {};
  for (const s of SIDES) {
    const e = obj(rec[s]);
    names[s] = (typeof e.athlete_id === 'string' && registryName(athletes, e.athlete_id)) || str(e.name) || null;
  }
  const side = athleteId ? SIDES.find((s) => obj(rec[s]).athlete_id === athleteId) || null : null;
  const res = obj(rec.result);
  const winner = SIDES.includes(res.winner) ? res.winner : null;
  const sets = Array.isArray(res.sets) ? res.sets.filter((p) => Array.isArray(p) && p.length === 2) : [];
  const doubles = ins.match_format === 'doubles';
  const bySide = doubles ? {} : obj(obj(obj(ins.technique).value).by_side);
  const idx = (s) => (isNum(obj(bySide[s]).index) ? bySide[s].index : null);
  const rIns = obj(ins.rally);
  const rv = obj(rIns.value);
  const rallyOk = rIns.status !== 'unavailable' && ['rallies', 'segments', 'windows'].includes(rv.label);
  const sIns = obj(ins.strokes);
  const sv = obj(sIns.value);
  return {
    stem: str(stem),
    date: str(rec.date) || str(obj(ins.date).value) || null,
    // A record date is the user's own; only a sidecar date from a file mtime is approximate.
    dateApprox: !str(rec.date) && isApproxDate(obj(ins.date).value, obj(ins.date).source),
    side,
    doubles,
    names,
    winner,
    result: side && winner ? (winner === side ? 'W' : 'L') : null,
    // Sets read from the first-named side (the athlete's, else the lower side, as the title does).
    first: side || 'lower',
    sets: (side || 'lower') === 'lower' ? sets.map((p) => [p[1], p[0]]) : sets,
    techIndex: side ? idx(side) : null,
    techBySide: { upper: idx('upper'), lower: idx('lower') },
    rally: rallyOk
      ? { available: true, count: isNum(rv.count) ? rv.count : 0, label: rv.label, suppressed: !!rv.suppressed || rv.label !== 'rallies', signal: typeof rv.signal === 'string' ? rv.signal : null, failed: false }
      : { available: false, count: null, label: null, suppressed: false, signal: null, failed: false },
    strokes: sIns.status !== 'unavailable' && isNum(sv.total) ? { classified: isNum(sv.classified) ? sv.classified : 0, total: sv.total } : null,
  };
}

/**
 * Pure: {title, sub} for a matches row. With a name on either side the title is "A vs. B" (a missing name
 * falls back to its side label) and the video name is the subtitle; with no names at all every row would read
 * "Lower side vs. Upper side", so the video name is the title and the side labels the muted subtitle.
 */
export function rowTitle(row, lang) {
  const r = obj(row);
  if (r.doubles) return doublesRowTitle(r, lang);
  const a = r.side || 'lower';
  const sides = { a: t('ws.side.' + a, lang), b: t('ws.side.' + otherSide(a), lang) };
  if (!r.athlete && !r.opponent) return { title: str(r.stem), sub: t('mv.sides_sub', lang, sides) };
  return { title: t('mv.vs', lang, { a: r.athlete || sides.a, b: r.opponent || sides.b }), sub: str(r.stem) };
}

const IDENTITY_LABEL = /^P\d{1,2}$/;

/** True when a team label ("Alice / P2") holds at least one real name, not only identity ids. */
function hasRealName(label) {
  return typeof label === 'string' && label.split(' / ').some((part) => part.trim() && !IDENTITY_LABEL.test(part.trim()));
}

/**
 * Doubles row title. With the athlete's perspective: "Athlete / Partner vs. Opponents" (a missing side falls back
 * to the row's `teams` label, then the team label). Without one (no-scope rows): both `teams` labels, team 1 first,
 * with a neutral "Doubles · <video>" subtitle. Only when no name at all is known: the video name, with the
 * "no athletes mapped yet" subtitle.
 */
function doublesRowTitle(r, lang) {
  const teams = Array.isArray(r.teams) ? r.teams : [null, null];
  const own = [r.athlete || r.athletePlayer, r.partner].filter(Boolean).join(' / ');
  const team = r.team === 2 ? 2 : r.team === 1 ? 1 : null;
  const label = (n) => (n ? t('dbl.team', lang, { n }) : t('dbl.team_unknown', lang));
  if (own || r.opponent) {
    const other = team ? (team === 1 ? 2 : 1) : null;
    const a = own || (team && teams[team - 1]) || label(team);
    const b = r.opponent || (other && teams[other - 1]) || label(other);
    return { title: t('mv.vs', lang, { a, b }), sub: str(r.stem) };
  }
  if (teams.some(hasRealName)) {
    return {
      title: t('mv.vs', lang, { a: teams[0] || label(1), b: teams[1] || label(2) }),
      sub: t('dbl.row.sub', lang, { stem: str(r.stem) }),
    };
  }
  return { title: str(r.stem), sub: t('dbl.row.no_names', lang) };
}

// ---------- shared DOM helpers ----------

const num1 = (v) => (isNum(v) ? String(+v.toFixed(1)) : '—');
const sideLabel = (s, lang) => t('ws.side.' + s, lang);

function langOf(ctx) {
  return obj(ctx).lang === 'en' ? 'en' : 'zh';
}

function storeState(ctx) {
  const st = obj(ctx).store;
  try {
    return st && typeof st.get === 'function' ? obj(st.get()) : {};
  } catch (e) {
    return {};
  }
}

function go(ctx, anchor, hash) {
  if (typeof obj(ctx).transitionTo === 'function') ctx.transitionTo(anchor, hash);
  else if (typeof location !== 'undefined') location.hash = hash;
}

/**
 * The match peek drawer content (canvas Main: peek). `row` is {stem} or an overview match row; the rest is
 * fetched (sidecar + record) and filled in. ctx: the shell ctx plus `anchorEl` (the row that opened it), used
 * as the origin of the container transform into the workspace. Returns a Node for ctx.openPeek().
 */
export function buildMatchPeek(row, ctx = {}) {
  const lang = langOf(ctx);
  const stem = str(obj(row).stem);
  const root = h('div', { class: 'mp' });
  const hash = formatHash('matches', { stem });

  /** mode: 'loading' | 'ok' | 'failed' (both fetches failed) | 'partial' (the record could not be read). */
  function paint(pm, mode) {
    const loading = mode === 'loading';
    const had = document.activeElement && root.contains(document.activeElement)
      ? (document.activeElement.classList.contains('mp-open') ? '.mp-open' : document.activeElement.hasAttribute('data-peek-close') ? '[data-peek-close]' : null)
      : null;
    root.textContent = '';
    const [a, b] = [pm.first, otherSide(pm.first)];
    const nm = (s) => pm.names[s] || sideLabel(s, lang);
    const dbl = pm.dbl;
    root.appendChild(h('div', null, [
      h('div', { class: 'mono k-label' }, [t('peek.match', lang, { date: fmtDate(pm.date, lang).toUpperCase() }),
        pm.dateApprox ? approxMark(lang) : null]),
      h('h2', { class: 'mp-title' }, dbl ? dbl.title.text || stem : t('peek.title', lang, { a: nm(a), b: nm(b) })),
    ]));
    if (dbl) paintDoubles(pm, mode);
    else paintSingles(pm, mode, a, b, nm);
    root.appendChild(h('p', { class: 'mp-hint' }, t('peek.hint', lang)));
    const open = h('a', {
      href: hash, class: 'ov-btn primary mp-open',
      onclick: (e) => {
        if (e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
        e.preventDefault();
        const anchor = ctx.anchorEl && ctx.anchorEl.isConnected ? ctx.anchorEl : null;
        if (typeof ctx.closePeek === 'function') ctx.closePeek();
        go(ctx, anchor, hash);
      },
    }, t('peek.open', lang));
    root.appendChild(h('div', { class: 'mp-actions' }, [open, h('button', { type: 'button', class: 'ov-btn', 'data-peek-close': '' }, t('btn.close', lang))]));
    // A repaint replaces the buttons: keep keyboard focus where it was.
    const back = had ? root.querySelector(had) : null;
    if (back) back.focus();
  }

  const tile = (label, value, sub, i) => h('div', { class: 'mp-tile', style: { animationDelay: `${(0.12 + i * 0.08).toFixed(2)}s` } }, [
    h('div', { class: 'kw-muted' }, label),
    h('div', { class: 'mono mp-val' }, value),
    sub ? h('div', { class: 'mp-sub' }, sub) : null,
  ]);

  function rallyTiles(pm, dash, from) {
    const r = pm.rally;
    const rallyHead = !r.available ? t('peek.segments', lang) : r.label === 'rallies' ? t('peek.rallies', lang) : r.label === 'windows' ? t('peek.windows', lang) : t('peek.segments', lang);
    const sig = r.signal ? t('signal.' + r.signal, lang) : '';
    return [
      tile(rallyHead, r.available ? fmtNumber(r.count) : dash, r.available ? rallyCountText(r, lang) : null, from),
      tile(t('peek.signal', lang), r.available ? (sig && sig !== 'signal.' + r.signal ? sig : '—') : dash, null, from + 2),
    ];
  }

  /** Doubles peek (spec 5): teams, identity status, named-hit share with the unvalidated badge; no scores. */
  function paintDoubles(pm, mode) {
    const d = pm.dbl;
    const loading = mode === 'loading';
    const chips = h('div', { class: 'mp-chips' });
    const winLabel = d.winnerTeam ? (d.teams.find((x) => x.id === d.winnerTeam) || {}).label || t('dbl.team', lang, { n: d.winnerTeam }) : '';
    if (pm.result) chips.appendChild(h('span', { class: 'k-chip ' + (pm.result === 'W' ? 'good' : 'bad') }, t(pm.result === 'W' ? 'ws.result.win' : 'ws.result.loss', lang)));
    else if (d.winnerTeam) chips.appendChild(h('span', { class: 'k-chip' }, t('result.side_won', lang, { name: winLabel })));
    else if (mode === 'ok') chips.appendChild(h('span', { class: 'k-chip' }, t('matches.no_result', lang)));
    chips.appendChild(h('span', { class: 'k-chip' }, t('doubles.chip', lang)));
    chips.appendChild(h('span', { class: 'k-chip mid', title: t('dbl.unvalidated_title', lang) }, t('dbl.unvalidated', lang)));
    if (mode === 'failed' || mode === 'partial') chips.appendChild(h('span', { class: 'kw-foot-muted', role: 'status' }, t('peek.load_failed', lang)));
    if (pm.sets.length) chips.appendChild(h('span', { class: 'mono kw-muted' }, pm.sets.map((p) => `${p[0]}–${p[1]}`).join(' · ')));
    root.appendChild(chips);
    const dash = loading ? t('state.loading', lang) : '—';
    const named = d.hits.namedShare !== null
      ? t('dbl.peek.named_sub', lang, { named: fmtNumber(d.hits.named), total: fmtNumber(d.hits.total) })
      : null;
    const [rally, signal] = rallyTiles(pm, dash, 1);
    root.appendChild(h('div', { class: 'mp-grid' }, [
      tile(t('dbl.peek.identity', lang), loading ? dash : d.statusText, loading ? null : d.detail || null, 0),
      rally,
      tile(t('dbl.peek.named', lang), loading && d.namedText === '—' ? dash : d.namedText, named ? `${named} · ${t('dbl.tag_unvalidated', lang)}` : null, 2),
      signal,
    ]));
    root.appendChild(h('div', { class: 'mp-box' }, [
      h('div', { class: 'mono k-label' }, t('dbl.peek.teams', lang)),
      ...d.teams.map((tm) => h('div', null, [`${t('dbl.team', lang, { n: tm.id })}: `, h('strong', null, tm.label)])),
    ]));
  }

  function paintSingles(pm, mode, a, b, nm) {
    const loading = mode === 'loading';
    const chips = h('div', { class: 'mp-chips' });
    if (pm.result) chips.appendChild(h('span', { class: 'k-chip ' + (pm.result === 'W' ? 'good' : 'bad') }, t(pm.result === 'W' ? 'ws.result.win' : 'ws.result.loss', lang)));
    else if (pm.winner) chips.appendChild(h('span', { class: 'k-chip' }, t('result.side_won', lang, { name: nm(pm.winner) })));
    else if (mode === 'ok') chips.appendChild(h('span', { class: 'k-chip' }, t('matches.no_result', lang)));
    if (pm.doubles) chips.appendChild(h('span', { class: 'k-chip' }, t('doubles.chip', lang)));
    if (mode === 'failed' || mode === 'partial') chips.appendChild(h('span', { class: 'kw-foot-muted', role: 'status' }, t('peek.load_failed', lang)));
    if (pm.sets.length) chips.appendChild(h('span', { class: 'mono kw-muted' }, pm.sets.map((p) => `${p[0]}–${p[1]}`).join(' · ')));
    root.appendChild(chips);

    const dash = loading ? t('state.loading', lang) : '—';
    const tech = pm.side ? num1(pm.techIndex)
      : [pm.techBySide.lower, pm.techBySide.upper].some(isNum) ? `${num1(pm.techBySide[a])} · ${num1(pm.techBySide[b])}` : '—';
    const [rally, signal] = rallyTiles(pm, dash, 1);
    root.appendChild(h('div', { class: 'mp-grid' }, [
      tile(t('peek.tech', lang), loading && tech === '—' ? dash : tech, pm.doubles ? t('reason.doubles_run', lang) : null, 0),
      rally,
      tile(t('peek.strokes', lang), pm.strokes ? fmtNumber(pm.strokes.total) : dash,
        pm.strokes ? t('tile.classified_of', lang, { classified: fmtNumber(pm.strokes.classified), total: fmtNumber(pm.strokes.total) }) : null, 2),
      signal,
    ]));
    root.appendChild(h('div', { class: 'mp-box' }, [
      h('div', { class: 'mono k-label' }, t('peek.athletes', lang)),
      h('div', null, [
        `${sideLabel('lower', lang)}: `, h('strong', null, pm.names.lower || '—'), ' · ',
        `${sideLabel('upper', lang)}: `, h('strong', null, pm.names.upper || '—'),
      ]),
    ]));
  }

  const s = storeState(ctx);
  const athleteId = obj(s.scope).athlete || s.athlete || '';
  const seed = obj(row);
  // An overview row that says doubles opens in the doubles layout at once (titled from its teams), not the
  // singles one, until the record and sidecar arrive.
  const base = peekSeed(seed, stem, s.athletes, athleteId, lang);
  if (!base.dbl && (seed.athlete || seed.opponent)) {
    const sd = SIDES.includes(seed.side) ? seed.side : 'lower';
    base.names = { [sd]: str(seed.athlete) || null, [otherSide(sd)]: str(seed.opponent) || null };
  }
  if (seed.date) {
    base.date = seed.date;
    base.dateApprox = isApproxDate(seed.date, seed.date_source);
  }
  paint(base, 'loading');

  // ctx.signal (main.js aborts it when the drawer closes) cancels both fetches.
  const signal = ctx.signal || null;
  const A = ctx.api || api;
  if (stem && typeof A.videoInsights === 'function') {
    const o = { ...LONG, ...(signal ? { signal } : {}) };
    const settle = (p) => p.then((data) => ({ ok: true, data }), (error) => ({ ok: false, error }));
    Promise.all([settle(A.videoInsights(stem, o)), settle(A.match(stem, o))]).then(([ins, rec]) => {
      if ((signal && signal.aborted) || [ins, rec].some((r) => !r.ok && r.error && r.error.kind === 'aborted')) return;
      // GET /api/matches returns {} without a record; a 404 is an unknown video, i.e. no record either.
      const recOk = rec.ok || (rec.error && rec.error.status === 404);
      const mode = !ins.ok && !recOk ? 'failed' : !recOk ? 'partial' : 'ok';
      const pm = peekModel({ stem, record: rec.ok ? rec.data : {}, insights: ins.ok ? ins.data : null, athletes: storeState(ctx).athletes, athleteId }, lang);
      if (mode === 'failed') {
        pm.names = base.names;
        pm.date = base.date;
        pm.dateApprox = base.dateApprox;
        if (base.dbl && !pm.dbl) {
          pm.dbl = base.dbl;
          pm.doubles = true;
        } else if (base.dbl && pm.dbl && !pm.dbl.title.text) pm.dbl.title = base.dbl.title;
      }
      paint(pm, mode);
    });
  }
  return root;
}

// ---------- the Matches view ----------

function reducedMotion(ctx) {
  try {
    const r = obj(ctx).reduced;
    if (typeof r === 'function') return !!r();
    if (typeof r === 'boolean') return r;
    return typeof matchMedia === 'function' && matchMedia('(prefers-reduced-motion: reduce)').matches;
  } catch (e) {
    return false;
  }
}

/** Mount the matches list. Returns {destroy(), update(ctx)}. */
export function mount(container, ctx = {}) {
  const noop = { destroy() {}, update() {} };
  if (!container || !hasDOM()) return noop;
  const lang = langOf(ctx);
  // `expanded`: stems of the split matches whose part sub-rows are open (kept across list reloads).
  const state = { ctx, rows: null, error: null, editing: null, destroyed: false, scopeKey: null, expanded: new Set() };
  let ctl = null;
  let reqId = 0;
  let animated = [];

  const root = h('div', { class: 'page mv', 'data-view': 'matches' });
  container.appendChild(root);
  const clock = createClock({
    reduced: () => reducedMotion(ctx),
    onFrame: (p) => animated.forEach((el, i) => revealStyle(el, local(p, i === 0 ? 0 : 1 + i * 0.3), { dy: 10 })),
  });

  function scope() {
    const q = obj(state.ctx.query);
    const s = storeState(state.ctx);
    return { athlete: q.athlete || s.athlete || '', squad: q.squad || s.squad || '' };
  }


  function header() {
    const n = state.rows ? state.rows.length : 0;
    return h('div', { class: 'ov-head' }, [
      h('div', { class: 'ov-head-l' }, [
        h('div', { class: 'mono ov-eyebrow' }, t('mv.eyebrow', lang)),
        h('h1', { class: 'page-title serif' }, t('nav.matches', lang)),
        state.rows ? h('div', { class: 'ov-summary' }, t('mv.sub', lang, { n: fmtNumber(n) })) : null,
      ]),
      h('div', { class: 'ov-actions' }, [h('a', { href: '#/new', class: 'ov-btn primary' }, t('btn.new_analysis', lang))]),
    ]);
  }

  function resultChip(r) {
    if (r.result) return h('span', { class: 'mono kw-res ' + (r.result === 'W' ? 'win' : 'loss') }, t('result.' + r.result, lang));
    return h('span', { class: 'mono kw-res none' }, t('matches.add_result', lang));
  }

  function statusChip(r) {
    const tone = r.status === 'analysed' ? 'good' : r.status === 'needs_review' ? 'mid' : r.status === 'processing' ? 'cyan' : '';
    return h('span', { class: 'k-chip' + (tone ? ' ' + tone : '') }, [h('span', { class: 'kw-dot', 'aria-hidden': 'true' }), t('status.' + r.status, lang)]);
  }

  const attrSel = (attr, stem) => `[${attr}="${String(stem).replace(/["\\]/g, '\\$&')}"]`;

  /** Focus a match row's control after a reload: its parts chevron when it has one, else its record button. */
  function focusRow(stem) {
    const el = root.querySelector(attrSel('data-expand', stem)) || root.querySelector(attrSel('data-rec', stem));
    if (el && typeof el.focus === 'function') el.focus();
  }

  function toggleExpand(stem) {
    if (state.expanded.has(stem)) {
      state.expanded.delete(stem);
      // A non-primary part's editor lives under its sub-row: close it with the sub-rows (the primary's own stays).
      const row = (state.rows || []).find((x) => x.stem === stem);
      if (row && row.parts.some((p, i) => i > 0 && p.stem === state.editing)) state.editing = null;
    } else state.expanded.add(stem);
    render();
    const btn = root.querySelector(attrSel('data-expand', stem));
    if (btn) btn.focus();
  }

  /** One sub-row of an expanded split match: part label, video, side, tech index, status, Open and (parts 2+) Link. */
  function partRow(r, p, i) {
    const label = t('link.part_label', lang, { n: i + 1 });
    const tr = h('tr', { class: 'mv-part-row' + (i > 0 && state.editing === p.stem ? ' open' : ''), 'data-part': p.stem });
    const open = h('button', {
      type: 'button', class: 'ov-btn small', 'aria-label': `${t('mv.open_aria', lang, { name: p.stem })} (${label})`,
      onclick: () => openRow({ stem: p.stem }, tr),
    }, t('link.open', lang));
    const linkBtn = i === 0 ? null : h('button', {
      type: 'button', class: 'ov-btn small', 'aria-expanded': state.editing === p.stem ? 'true' : 'false',
      'aria-label': t('link.part_aria', lang, { name: p.stem }), 'data-rec': p.stem,
      onclick: () => toggleEditor(p.stem),
    }, t('link.part_btn', lang));
    [
      h('td', { class: 'mv-name mv-part-name' }, [h('span', { class: 'k-chip' }, label), ' ', h('span', { class: 'mv-part-stem' }, p.stem)]),
      h('td', { colspan: '3', class: 'mono kw-muted' }, p.side ? sideLabel(p.side, lang) : '\u2014'),
      h('td', { class: 'mono kw-num' }, num1(p.tech_index)),
      h('td', null, statusChip({ status: partStatus(p) })),
      h('td', { class: 'kw-num mv-part-actions' }, [open, linkBtn]),
    ].forEach((td) => tr.appendChild(td));
    return tr;
  }

  function openRow(r, anchor) {
    go(state.ctx, anchor, formatHash('matches', { stem: r.stem }));
  }

  function render({ reveal = false } = {}) {
    if (state.destroyed) return;
    root.textContent = '';
    const head = header();
    root.appendChild(head);
    animated = [head];
    if (state.error && !state.rows) {
      const card = h('div', { class: 'card ov-state', role: 'alert' }, [
        h('div', { class: 'k-label' }, t('error.load.title', lang)),
        h('div', { class: 'ov-state-body' }, errorText(state.error, lang)),
        h('button', { type: 'button', class: 'ov-btn small', onclick: () => load() }, t('btn.retry', lang)),
      ]);
      root.appendChild(card);
      return;
    }
    if (!state.rows) {
      root.appendChild(h('div', { class: 'card ov-state', role: 'status', 'aria-busy': 'true' }, [
        h('div', { class: 'k-label' }, t('state.loading', lang)), h('div', { class: 'ov-skel' }),
      ]));
      return;
    }
    if (state.error) {
      root.appendChild(h('div', { class: 'ov-alert', role: 'alert' }, [
        h('strong', null, t('error.refresh.title', lang)), h('span', null, errorText(state.error, lang)),
        h('button', { type: 'button', class: 'ov-btn small', onclick: () => load() }, t('btn.retry', lang)),
      ]));
    }
    if (!state.rows.length) {
      const sc = scope();
      root.appendChild(h('div', { class: 'card ov-state ov-empty' }, [
        h('div', { class: 'ov-empty-title' }, t(sc.athlete || sc.squad ? 'matches.empty.title' : 'mv.empty.title', lang)),
        h('div', { class: 'ov-state-body' }, t(sc.athlete || sc.squad ? 'matches.empty.body' : 'mv.empty.body', lang)),
        h('a', { href: '#/new', class: 'ov-btn primary' }, t('btn.new_analysis', lang)),
      ]));
      return;
    }
    const th = (key, cls) => h('th', { scope: 'col', class: cls || null }, t(key, lang));
    const body = h('tbody');
    state.rows.forEach((r) => {
      const { title: name, sub } = rowTitle(r, lang);
      const tr = h('tr', {
        class: 'trow mv-row' + (state.editing === r.stem ? ' open' : ''),
        onclick: (e) => {
          if (e.target && typeof e.target.closest === 'function' && e.target.closest('button,a,input,select')) return;
          openRow(r, tr);
        },
      });
      const openBtn = h('button', { type: 'button', class: 'kw-row-open', 'aria-label': t('mv.open_aria', lang, { name }), onclick: () => openRow(r, tr) }, name);
      // A split match (2+ videos): a chevron toggles one sub-row per part (the first part is the primary video).
      const split = r.parts.length >= 2;
      const expanded = split && state.expanded.has(r.stem);
      const chev = split ? h('button', {
        type: 'button', class: 'mv-chev', 'aria-expanded': expanded ? 'true' : 'false', 'data-expand': r.stem,
        'aria-label': t(expanded ? 'link.collapse_aria' : 'link.expand_aria', lang, { name }),
        onclick: () => toggleExpand(r.stem),
      }, h('span', { 'aria-hidden': 'true' }, '\u25B8')) : null;
      const recBtn = h('button', {
        type: 'button', class: 'ov-btn small', 'aria-expanded': state.editing === r.stem ? 'true' : 'false',
        'aria-label': t('mv.record_aria', lang, { name }), 'data-rec': r.stem,
        onclick: () => toggleEditor(r.stem),
      }, t('mv.record_btn', lang));
      [
        h('td', { class: 'mv-name' }, [chev, openBtn,
          split ? h('span', { class: 'k-chip cyan mv-parts-chip' }, t('link.parts_chip', lang, { n: r.parts.length })) : null,
          r.doubles ? h('span', { class: 'k-chip kw-doubles' }, t('doubles.chip', lang)) : null, h('div', { class: 'mv-sub' }, sub)]),
        h('td', { class: 'mono kw-muted kw-col-date' }, [fmtDate(r.date, lang), r.dateApprox ? approxMark(lang) : null]),
        h('td', null, resultChip(r)),
        h('td', { class: 'mono kw-muted' }, r.sets.length ? r.sets.map((p) => `${p[0]}–${p[1]}`).join(' · ') : '—'),
        h('td', { class: 'mono kw-num' }, num1(r.techIndex)),
        h('td', null, statusChip(r)),
        h('td', { class: 'kw-num' }, recBtn),
      ].forEach((td) => tr.appendChild(td));
      body.appendChild(tr);
      animated.push(tr);
      if (state.editing === r.stem) {
        const cell = h('td', { colspan: '7', class: 'mv-edit-cell' });
        body.appendChild(h('tr', { class: 'mv-edit-row' }, cell));
        buildEditor(cell, r, name);
      }
      if (expanded) {
        r.parts.forEach((p, i) => {
          body.appendChild(partRow(r, p, i));
          // part 1 is the primary: its editor is the main row's, never a second one here
          if (i > 0 && state.editing === p.stem) {
            const pcell = h('td', { colspan: '7', class: 'mv-edit-cell' });
            body.appendChild(h('tr', { class: 'mv-edit-row' }, pcell));
            buildEditor(pcell, { stem: p.stem, doubles: false }, p.stem);
          }
        });
      }
    });
    const table = h('table', { class: 'table kw-table mv-table' }, [
      h('thead', null, h('tr', null, [
        th('mv.col.match'), th('matches.col.date', 'kw-col-date'), th('matches.col.result'), th('mv.col.sets'), th('matches.col.tech', 'kw-num'),
        th('matches.col.status'), th('mv.col.record', 'kw-num'),
      ])),
      body,
    ]);
    root.appendChild(h('section', { class: 'card hud mv-card', 'aria-label': t('nav.matches', lang) }, h('div', { class: 'kw-scroll' }, table)));
    if (reveal) clock.start();
    else { clock.cancel(); animated.forEach((el) => revealStyle(el, 1)); }
  }

  function toggleEditor(stem) {
    state.editing = state.editing === stem ? null : stem;
    render();
    const safe = String(stem).replace(/["\\]/g, '\\$&');
    const btn = root.querySelector(`[data-rec="${safe}"]`);
    if (state.editing) {
      const first = root.querySelector('.mv-edit-cell input, .mv-edit-cell select');
      if (first && typeof first.focus === 'function') first.focus();
    } else if (btn) btn.focus();
  }

  // ----- record editor -----
  /** An athlete picker (registry select + "other" name input) for one side or one doubles identity. */
  function pickerControl(key, labelText, cur, athletes, errId, labelNode) {
    const list = Array.isArray(obj(athletes).athletes) ? athletes.athletes : [];
    const known = cur.athlete_id && list.some((a) => a && a.id === cur.athlete_id);
    const initial = known ? cur.athlete_id : cur.name || cur.athlete_id ? OTHER : '';
    const sel = h('select', { class: 'mv-input', id: `mv-${key}`, 'aria-describedby': errId });
    sel.appendChild(h('option', { value: '' }, t('record.unspecified', lang)));
    list.forEach((a) => { if (a && a.id) sel.appendChild(h('option', { value: a.id }, String(a.name || a.id))); });
    sel.appendChild(h('option', { value: OTHER }, t('record.athlete_other', lang)));
    sel.value = initial;
    const input = h('input', {
      type: 'text', class: 'mv-input', maxlength: String(NAME_MAX), placeholder: t('record.name_placeholder', lang),
      'aria-label': `${labelText} · ${t('record.name_placeholder', lang)}`, value: cur.name || '',
    });
    input.hidden = sel.value !== OTHER;
    sel.addEventListener('change', () => { input.hidden = sel.value !== OTHER; if (!input.hidden) input.focus(); });
    return {
      el: h('div', { class: 'mv-field' }, [h('label', { for: `mv-${key}` }, labelNode || labelText), sel, input,
        h('span', { class: 'mv-err', id: errId, role: 'alert' })]),
      read: () => ({ athlete_id: sel.value && sel.value !== OTHER ? sel.value : '', name: sel.value === OTHER ? input.value : '' }),
      sel,
      input,
    };
  }

  function sideControl(side, form, athletes, errId) {
    return pickerControl(side, t('record.' + side, lang), obj(form[side]), athletes, errId);
  }

  function buildEditor(cell, row, name) {
    if (row.doubles) {
      buildDoublesEditor(cell, row, name);
      return;
    }
    const box = h('div', { class: 'mv-editor', role: 'group', 'aria-label': t('record.title', lang, { name }) });
    box.appendChild(h('div', { class: 'k-label' }, t('record.title', lang, { name })));
    const status = h('div', { class: 'kw-note', role: 'status' }, t('state.loading', lang));
    box.appendChild(status);
    cell.appendChild(box);
    const stem = row.stem;
    const s0 = storeState(state.ctx);
    const needAthletes = s0.athletesStatus !== 'ok' && typeof state.ctx.refreshAthletes === 'function'
      ? state.ctx.refreshAthletes().catch(() => null) : Promise.resolve(null);
    Promise.all([
      api.match(stem, LONG).then((r) => ({ ok: true, rec: obj(r) })).catch((e) => (e && e.status === 404 ? { ok: true, rec: {} } : { ok: false, err: e })),
      needAthletes,
      candidateRows(),
    ]).then(([res, , cands]) => {
      if (state.destroyed || state.editing !== stem || !box.isConnected) return;
      if (!res.ok) {
        status.textContent = errorText(res.err, lang);
        box.appendChild(h('button', { type: 'button', class: 'ov-btn small', onclick: () => { state.editing = null; toggleEditor(stem); } }, t('btn.retry', lang)));
        return;
      }
      const link = linkInfo(res.rec);
      if (link.partOf) {
        // A linked part holds no record of its own: show the link, not the form.
        status.textContent = '';
        fillLinkedPartEditor(box, row, link);
        return;
      }
      const has = Object.keys(res.rec).length > 0;
      status.textContent = has ? '' : t('record.none_yet', lang);
      fillEditor(box, row, formFromRecord(res.rec), has, link.parts.length);
      box.appendChild(linkSection(row, link, has, cands));
    });
  }

  // ----- split-match linking (spec 2026-10-21 section 8) -----
  /** The rows the link pickers draw from: always the unscoped list (a scoped one hides the other videos). */
  function candidateRows() {
    const sc = scope();
    if (!sc.athlete && !sc.squad) return Promise.resolve(state.rows || []);
    return api.overview({ range: 'all' }, LONG).then((d) => matchRows(d), () => state.rows || []);
  }

  /** PUT the link (or `{}` to unlink); on success toast, reload the list and put focus back on the match's row. */
  async function saveLink(partStem, body, primaryStem, toastKey, errEl, btn, refresh) {
    errEl.textContent = '';
    btn.disabled = true;
    try {
      await api.saveMatch(partStem, body, LONG);
      if (state.destroyed) return;
      if (typeof state.ctx.toast === 'function') state.ctx.toast(t(toastKey, lang));
      if (body.part_of) state.expanded.add(primaryStem);
      state.editing = null;
      await load({ reveal: false });
      focusRow(primaryStem);
    } catch (e) {
      if (state.destroyed) return;
      errEl.textContent = errorText(e, lang);
      btn.disabled = false;
      // The list may be stale (a primary deleted elsewhere): refresh it quietly so the editor stays open, then
      // repopulate the pickers from it (the error text stays).
      await load({ reveal: false, quiet: true });
      if (typeof refresh === 'function' && !state.destroyed) await refresh();
    }
  }

  /** Dropdown + "sides swapped" checkbox + Link button. `onLink(value, swapped, errEl, btn)` runs on a valid pick.
   * setOptions(options) repopulates the dropdown (keeping the pick if it is still offered) and the disabled state. */
  function linkControls(uid, selectLabel, { disabled = false, onLink }) {
    const sel = h('select', { class: 'mv-input', id: `${uid}-sel`, 'aria-describedby': `${uid}-err` });
    const check = h('input', { type: 'checkbox', id: `${uid}-swap`, 'aria-describedby': `${uid}-swaphint` });
    const err = h('span', { class: 'mv-err', id: `${uid}-err`, role: 'alert' });
    const btn = h('button', { type: 'button', class: 'ov-btn small primary' }, t('link.btn', lang));
    function setOptions(options) {
      const keep = sel.value;
      sel.textContent = '';
      sel.appendChild(h('option', { value: '' }, t('link.pick_none', lang)));
      options.forEach((o) => sel.appendChild(h('option', { value: o.value, disabled: o.disabled }, o.label)));
      sel.value = options.some((o) => o.value === keep && !o.disabled) ? keep : '';
      const off = disabled || !options.length;
      sel.disabled = off;
      check.disabled = off;
      btn.disabled = off;
    }
    btn.addEventListener('click', () => {
      if (!sel.value) { err.textContent = t('link.pick_required', lang); sel.focus(); return; }
      onLink(sel.value, check.checked, err, btn);
    });
    const el = h('div', { class: 'mv-fields' }, [
      h('div', { class: 'mv-field' }, [h('label', { for: `${uid}-sel` }, selectLabel), sel]),
      h('div', { class: 'mv-field mv-check' }, [
        h('label', { for: `${uid}-swap` }, [check, ' ', t('link.swapped', lang)]),
        h('span', { class: 'mv-hint', id: `${uid}-swaphint` }, t('link.swapped_hint', lang)),
      ]),
      h('div', { class: 'mv-actions mv-link-go' }, btn),
      err,
    ]);
    return { el, sel, check, btn, setOptions };
  }

  /** The link block under a singles editor: link videos as parts (record holders) and/or link this video to a match. */
  function linkSection(row, link, hasRecord, cands) {
    const uid = `mv-link-${Math.random().toString(36).slice(2, 8)}`;
    const sec = h('div', { class: 'mv-link', role: 'group', 'aria-label': t('link.section_title', lang) });
    let partCtl = null;
    let noCands = null;
    if (hasRecord) {
      sec.appendChild(h('div', { class: 'k-label' }, t('link.section_title', lang)));
      sec.appendChild(h('div', { class: 'mv-hint' }, t('link.section_hint', lang)));
      if (link.parts.length) {
        const list = h('ul', { class: 'mv-link-list', 'aria-label': t('link.parts_title', lang) });
        link.parts.forEach((p, i) => {
          const errEl = h('span', { class: 'mv-err', role: 'alert' });
          const btn = h('button', {
            type: 'button', class: 'ov-btn small', 'aria-label': t('link.unlink_aria', lang, { name: p.stem }),
            onclick: () => saveLink(p.stem, {}, row.stem, 'link.toast.unlinked', errEl, btn),
          }, t('link.unlink', lang));
          list.appendChild(h('li', null, [
            h('span', { class: 'k-chip' }, t('link.part_label', lang, { n: i + 2 })), ' ',
            h('span', { class: 'mv-part-stem' }, p.stem), ' ',
            h('span', { class: 'mv-hint' }, t(p.sides_swapped ? 'link.swapped_yes' : 'link.swapped_no', lang)), ' ', btn, errEl,
          ]));
        });
        sec.appendChild(list);
      }
      partCtl = linkControls(`${uid}-p`, t('link.pick_part', lang), {
        onLink: (stem, swapped, errEl, btn) => saveLink(stem, linkBody(row.stem, swapped), row.stem, 'link.toast.linked', errEl, btn, refresh),
      });
      sec.appendChild(partCtl.el);
      noCands = h('div', { class: 'mv-hint' }, t('link.no_candidates', lang));
      sec.appendChild(noCands);
    }
    // "This video is part of another match": enabled only for a video with no record of its own.
    sec.appendChild(h('div', { class: 'k-label' }, t('link.part_of_title', lang)));
    const primCtl = linkControls(`${uid}-m`, t('link.pick_primary', lang), {
      disabled: hasRecord,
      onLink: (stem, swapped, errEl, btn) => saveLink(row.stem, linkBody(stem, swapped), stem, 'link.toast.linked', errEl, btn, refresh),
    });
    sec.appendChild(primCtl.el);
    const noPrims = h('div', { class: 'mv-hint' }, t('link.no_primaries', lang));
    sec.appendChild(noPrims);
    if (hasRecord) sec.appendChild(h('div', { class: 'mv-hint' }, t('link.has_record_hint', lang)));

    /** (Re)fill both pickers from a candidate row list. */
    function paint(rows) {
      if (partCtl) {
        const parts = eligibleParts(rows, row.stem, lang);
        partCtl.setOptions(parts.map((c) => ({ value: c.stem, label: c.hasRecord ? `${c.label} ${t('link.has_record_suffix', lang)}` : c.label, disabled: c.hasRecord })));
        noCands.hidden = parts.length > 0;
      }
      const prim = eligiblePrimaries(rows, row.stem, lang);
      primCtl.setOptions(prim.map((c) => ({ value: c.stem, label: c.label })));
      noPrims.hidden = hasRecord || prim.length > 0;
    }
    // After a failed link the list was refreshed: drop options that vanished (the error text stays).
    function refresh() {
      return candidateRows().then((rows) => { if (!state.destroyed && sec.isConnected) paint(rows); });
    }
    paint(cands);
    return sec;
  }

  /** The editor of a linked part (reached from its sub-row): "Linked to <primary>", the swap flag and Unlink. */
  function fillLinkedPartEditor(box, row, link) {
    const errEl = h('span', { class: 'mv-err form', role: 'alert' });
    const unlink = h('button', {
      type: 'button', class: 'ov-btn small danger', 'aria-label': t('link.unlink_aria', lang, { name: row.stem }),
      onclick: () => saveLink(row.stem, {}, link.partOf, 'link.toast.unlinked', errEl, unlink),
    }, t('link.unlink', lang));
    box.appendChild(h('div', { class: 'mv-link' }, [
      h('div', { class: 'k-label' }, t('link.part_of_title', lang)),
      h('p', { class: 'mv-linked' }, [
        t('link.linked_to', lang, { primary: link.partOf }), ' \u00B7 ',
        h('span', { class: 'mv-hint' }, t(link.sidesSwapped ? 'link.swapped_yes' : 'link.swapped_no', lang)),
      ]),
      h('div', { class: 'mv-actions' }, [
        unlink,
        h('button', { type: 'button', class: 'ov-btn small', onclick: () => toggleEditor(row.stem) }, t('btn.cancel', lang)),
      ]),
      errEl,
    ]));
    unlink.focus();
  }

  function fillEditor(box, row, form, hasRecord, linkedParts = 0) {
    const athletes = storeState(state.ctx).athletes;
    const errDate = `mv-err-date-${Math.random().toString(36).slice(2, 8)}`;
    const date = h('input', { type: 'date', class: 'mv-input', id: 'mv-date', value: form.date, 'aria-describedby': errDate });
    const upper = sideControl('upper', form, athletes, errDate + '-u');
    const lower = sideControl('lower', form, athletes, errDate + '-l');
    const winner = h('select', { class: 'mv-input', id: 'mv-winner', 'aria-describedby': errDate + '-w' }, [
      h('option', { value: '' }, t('record.winner_none', lang)),
      h('option', { value: 'upper' }, t('side.upper', lang)),
      h('option', { value: 'lower' }, t('side.lower', lang)),
    ]);
    winner.value = form.winner;
    const sets = h('input', { type: 'text', class: 'mv-input mono', id: 'mv-sets', value: form.sets, placeholder: '21-18, 19-21, 21-15', 'aria-describedby': `${errDate}-s ${errDate}-sh` });
    const err = (id) => h('span', { class: 'mv-err', id, role: 'alert' });
    const errs = { date: err(errDate), winner: err(errDate + '-w'), sets: err(errDate + '-s') };
    const formErr = h('div', { class: 'mv-err form', role: 'alert' });
    const saveBtn = h('button', { type: 'submit', class: 'ov-btn small primary' }, t('btn.save', lang));
    const fields = h('div', { class: 'mv-fields' }, [
      h('div', { class: 'mv-field' }, [h('label', { for: 'mv-date' }, t('record.date', lang)), date, errs.date]),
      upper.el,
      lower.el,
      h('div', { class: 'mv-field' }, [h('label', { for: 'mv-winner' }, t('record.winner', lang)), winner, errs.winner]),
      h('div', { class: 'mv-field wide' }, [h('label', { for: 'mv-sets' }, t('record.sets', lang)), sets,
        h('span', { class: 'mv-hint', id: errDate + '-sh' }, t('record.sets_order', lang)), errs.sets]),
    ]);
    const formEl = h('form', { class: 'mv-form', novalidate: true }, [
      fields, formErr,
      h('div', { class: 'mv-actions' }, [
        saveBtn,
        hasRecord ? h('button', {
          type: 'button', class: 'ov-btn small danger',
          onclick: () => { if (confirmClear()) submit({}); },
        }, t('record.clear', lang)) : null,
        h('button', { type: 'button', class: 'ov-btn small', onclick: () => toggleEditor(row.stem) }, t('btn.cancel', lang)),
      ]),
    ]);
    // Clearing (or saving blank) a primary's record unlinks its parts: say so before doing it.
    const confirmClear = () => typeof confirm !== 'function'
      || confirm(linkedParts > 0 ? t('link.clear_unlinks_confirm', lang, { n: linkedParts }) : t('record.clear_confirm', lang));
    const sideErr = { upper: upper.el.querySelector('.mv-err'), lower: lower.el.querySelector('.mv-err') };
    const ctlOf = { date, upper: upper.sel, lower: lower.sel, winner, sets };

    function showErrors(errors) {
      for (const k of ['date', 'winner', 'sets']) errs[k].textContent = errors[k] ? t(errors[k], lang) : '';
      for (const k of SIDES) sideErr[k].textContent = errors[k] ? t(errors[k], lang) : '';
      for (const [k, c] of Object.entries(ctlOf)) c.setAttribute('aria-invalid', errors[k] ? 'true' : 'false');
      const firstBad = Object.keys(ctlOf).find((k) => errors[k]);
      if (firstBad) ctlOf[firstBad].focus();
    }

    async function submit(record) {
      formErr.textContent = '';
      saveBtn.disabled = true;
      try {
        await api.saveMatch(row.stem, record, LONG);
        if (state.destroyed) return;
        if (typeof state.ctx.toast === 'function') state.ctx.toast(t('toast.saved', lang));
        state.editing = null;
        await load({ reveal: false });
        const safe = String(row.stem).replace(/["\\]/g, '\\$&');
        const btn = root.querySelector(`[data-rec="${safe}"]`);
        if (btn) btn.focus();
      } catch (e) {
        if (state.destroyed) return;
        formErr.textContent = errorText(e, lang);
        saveBtn.disabled = false;
      }
    }

    formEl.addEventListener('submit', (e) => {
      e.preventDefault();
      const out = validateRecordForm({ date: date.value, upper: upper.read(), lower: lower.read(), winner: winner.value, sets: sets.value });
      showErrors(out.errors);
      if (!out.record) return;
      if (linkedParts > 0 && isBlankRecord(out.record) && !confirmClear()) return;
      submit(out.record);
    });
    box.appendChild(formEl);
    const first = formEl.querySelector('input,select');
    if (first) first.focus();
  }

  // ----- doubles record editor (spec 3 and 5): four identity pickers, a winning team, sets team-1-first -----
  function buildDoublesEditor(cell, row, name) {
    const box = h('div', { class: 'mv-editor', role: 'group', 'aria-label': t('record.title', lang, { name }) });
    box.appendChild(h('div', { class: 'k-label' }, [t('record.title', lang, { name }), ' ', h('span', { class: 'k-chip' }, t('doubles.chip', lang))]));
    const status = h('div', { class: 'kw-note', role: 'status' }, t('state.loading', lang));
    box.appendChild(status);
    cell.appendChild(box);
    const stem = row.stem;
    const s0 = storeState(state.ctx);
    const needAthletes = s0.athletesStatus !== 'ok' && typeof state.ctx.refreshAthletes === 'function'
      ? state.ctx.refreshAthletes().catch(() => null) : Promise.resolve(null);
    Promise.all([
      api.match(stem, LONG).then((r) => ({ ok: true, rec: obj(r) })).catch((e) => (e && e.status === 404 ? { ok: true, rec: {} } : { ok: false, err: e })),
      // The identity labels (team, coverage) come from the sidecar's doubles insight; without it the ids still work.
      api.videoInsights(stem, LONG).then((d) => obj(d), () => null),
      needAthletes,
    ]).then(([res, ins]) => {
      if (state.destroyed || state.editing !== stem || !box.isConnected) return;
      if (!res.ok) {
        status.textContent = errorText(res.err, lang);
        box.appendChild(h('button', { type: 'button', class: 'ov-btn small', onclick: () => { state.editing = null; toggleEditor(stem); } }, t('btn.retry', lang)));
        return;
      }
      const has = Object.keys(res.rec).length > 0;
      status.textContent = has ? '' : t('record.none_yet', lang);
      const pm = doublesPeekModel({ record: {}, insights: ins || {} }, lang);
      fillDoublesEditor(box, row, formFromDoublesRecord(res.rec), has, pm.players, pm.status === 'missing' || !ins);
    });
  }

  function fillDoublesEditor(box, row, form, hasRecord, players, noIdentityData) {
    const athletes = storeState(state.ctx).athletes;
    const uid = `mv-dbl-${Math.random().toString(36).slice(2, 8)}`;
    const err = (id) => h('span', { class: 'mv-err', id, role: 'alert' });
    const date = h('input', { type: 'date', class: 'mv-input', id: 'mv-date', value: form.date, 'aria-describedby': uid + '-d' });
    const pickers = {};
    const fieldsIds = h('div', { class: 'mv-fields' });
    for (const pid of IDENTITY_IDS) {
      const p = players.find((x) => x.id === pid) || { id: pid };
      const label = identityOptionLabel(p, lang);
      const color = identityColor(pid);
      const labelNode = [h('span', { class: 'dbl-swatch', 'aria-hidden': 'true', style: color ? { '--pc': color } : null }), label];
      pickers[pid] = pickerControl(pid, label, obj(form.players[pid]), athletes, `${uid}-${pid}`, labelNode);
      fieldsIds.appendChild(pickers[pid].el);
    }
    const readPicks = () => Object.fromEntries(IDENTITY_IDS.map((pid) => [pid, pickers[pid].read()]));
    const teamOpts = winnerTeamOptions(players, readPicks(), athletes, lang).map((o) => h('option', { value: o.value }, o.label));
    const winner = h('select', { class: 'mv-input', id: 'mv-winner', 'aria-describedby': uid + '-w' }, [
      h('option', { value: '' }, t('record.winner_none', lang)),
      ...teamOpts,
    ]);
    winner.value = form.winner_team;
    // The team labels follow the identity pickers as they change (the selected value is kept).
    const relabel = () => {
      winnerTeamOptions(players, readPicks(), athletes, lang).forEach((o, i) => { teamOpts[i].textContent = o.label; });
    };
    for (const pid of IDENTITY_IDS) {
      pickers[pid].sel.addEventListener('change', relabel);
      pickers[pid].input.addEventListener('input', relabel);
    }
    const sets = h('input', { type: 'text', class: 'mv-input mono', id: 'mv-sets', value: form.sets, placeholder: '21-18, 19-21, 21-15', 'aria-describedby': `${uid}-s ${uid}-sh` });
    const errs = { date: err(uid + '-d'), winner: err(uid + '-w'), sets: err(uid + '-s') };
    const formErr = h('div', { class: 'mv-err form', role: 'alert' });
    const saveBtn = h('button', { type: 'submit', class: 'ov-btn small primary' }, t('btn.save', lang));
    const formEl = h('form', { class: 'mv-form', novalidate: true }, [
      h('div', { class: 'mv-fields' }, [h('div', { class: 'mv-field' }, [h('label', { for: 'mv-date' }, t('record.date', lang)), date, errs.date])]),
      h('div', { class: 'k-label' }, t('dbl.record.identities', lang)),
      h('div', { class: 'mv-hint' }, t(noIdentityData ? 'dbl.record.no_identity_data' : 'dbl.record.identity_hint', lang)),
      fieldsIds,
      h('div', { class: 'mv-fields' }, [
        h('div', { class: 'mv-field' }, [h('label', { for: 'mv-winner' }, t('dbl.record.winner_team', lang)), winner, errs.winner]),
        h('div', { class: 'mv-field wide' }, [h('label', { for: 'mv-sets' }, t('record.sets', lang)), sets,
          h('span', { class: 'mv-hint', id: uid + '-sh' }, t('dbl.record.sets_order', lang)), errs.sets]),
      ]),
      formErr,
      h('div', { class: 'mv-actions' }, [
        saveBtn,
        hasRecord ? h('button', {
          type: 'button', class: 'ov-btn small danger',
          onclick: () => { if (typeof confirm !== 'function' || confirm(t('record.clear_confirm', lang))) submit({}); },
        }, t('record.clear', lang)) : null,
        h('button', { type: 'button', class: 'ov-btn small', onclick: () => toggleEditor(row.stem) }, t('btn.cancel', lang)),
      ]),
    ]);
    const ctlOf = { date, ...Object.fromEntries(IDENTITY_IDS.map((pid) => [pid, pickers[pid].sel])), winner, sets };
    const errOf = { ...errs, ...Object.fromEntries(IDENTITY_IDS.map((pid) => [pid, pickers[pid].el.querySelector('.mv-err')])) };

    function showErrors(errors) {
      for (const [k, el] of Object.entries(errOf)) if (el) el.textContent = errors[k] ? t(errors[k], lang) : '';
      for (const [k, c] of Object.entries(ctlOf)) c.setAttribute('aria-invalid', errors[k] ? 'true' : 'false');
      const firstBad = Object.keys(ctlOf).find((k) => errors[k]);
      if (firstBad) ctlOf[firstBad].focus();
    }

    async function submit(record) {
      formErr.textContent = '';
      saveBtn.disabled = true;
      try {
        await api.saveMatch(row.stem, record, LONG);
        if (state.destroyed) return;
        if (typeof state.ctx.toast === 'function') state.ctx.toast(t('toast.saved', lang));
        state.editing = null;
        await load({ reveal: false });
        const safe = String(row.stem).replace(/["\\]/g, '\\$&');
        const btn = root.querySelector(`[data-rec="${safe}"]`);
        if (btn) btn.focus();
      } catch (e) {
        if (state.destroyed) return;
        formErr.textContent = errorText(e, lang);
        saveBtn.disabled = false;
      }
    }

    formEl.addEventListener('submit', (e) => {
      e.preventDefault();
      const out = validateDoublesRecordForm({
        date: date.value,
        players: Object.fromEntries(IDENTITY_IDS.map((pid) => [pid, pickers[pid].read()])),
        winner_team: winner.value,
        sets: sets.value,
      });
      showErrors(out.errors);
      if (out.record) submit(out.record);
    });
    box.appendChild(formEl);
    const first = formEl.querySelector('input,select');
    if (first) first.focus();
  }

  // ----- data -----
  async function load({ reveal = true, quiet = false } = {}) {
    const sc = scope();
    const mine = ++reqId;
    if (ctl && typeof ctl.abort === 'function') ctl.abort();
    ctl = typeof AbortController === 'function' ? new AbortController() : null;
    if (!state.rows) render();
    try {
      const data = await api.overview({ athlete: sc.athlete, squad: sc.squad, range: 'all' }, { ...LONG, ...(ctl ? { signal: ctl.signal } : {}) });
      if (state.destroyed || mine !== reqId) return;
      state.rows = matchRows(data);
      state.error = null;
      state.scopeKey = sc.athlete + '|' + sc.squad;
      // The open editor may belong to a row or to one of a split match's parts.
      if (state.editing && !state.rows.some((r) => r.stem === state.editing || r.parts.some((p) => p.stem === state.editing))) state.editing = null;
      if (quiet) return;     // a background refresh after a failed link: keep the editor and its error on screen
      render({ reveal });
    } catch (err) {
      if (state.destroyed || mine !== reqId || (err && err.kind === 'aborted')) return;
      state.error = err || { kind: 'network' };
      render({ reveal: false });
    }
  }

  load();

  return {
    update(next) {
      if (state.destroyed) return;
      state.ctx = next || state.ctx;
      const sc = scope();
      if (sc.athlete + '|' + sc.squad !== state.scopeKey) {
        state.editing = null;
        load();
      }
    },
    destroy() {
      if (state.destroyed) return;
      state.destroyed = true;
      clock.cancel();
      if (ctl && typeof ctl.abort === 'function') ctl.abort();
      if (root.parentNode) root.parentNode.removeChild(root);
    },
  };
}
