// The merged Matches page's model (spec 2026-10-24 sections 3-4), pure and tested: the overview's match rows joined
// with the /api/videos file list by stem, plus one "video row" per file no match row covers (posture-only, court
// set, new, processing). Filters (search, status group, tags with AND), tag-bar counts, the row's open target and
// the client-side mirror of library.tags.normalise live here too; views/matches.js renders the result.
import { t } from '../core/i18n.js';
import { formatHash } from '../core/router.js';
import { matchRows, rowTitle } from './matches.js';

export const SYS_TAGS = Object.freeze(['match', 'drill']);
export const STATUS_FILTERS = Object.freeze(['all', 'analysed', 'needs_review', 'processing', 'not_analysed', 'posture']);
export const SORTS = Object.freeze(['date', 'name']);
export const EMPTY_FILTER = Object.freeze({ q: '', status: 'all', tags: Object.freeze([]) });

const ACTIVE_JOB = ['pending', 'running', 'reencoding'];
const VIDEO_EXT = /\.(mp4|mov|avi|mkv|webm)$/i;
const TAG_MAX = 40;
// Which row statuses each filter group keeps ('all' keeps everything).
const STATUS_GROUP = {
  analysed: ['analysed', 'needs_review'],
  needs_review: ['needs_review'],
  processing: ['processing'],
  not_analysed: ['new', 'court_set'],
  posture: ['posture'],
};

const str = (v) => (typeof v === 'string' ? v : '');
const obj = (v) => (v && typeof v === 'object' && !Array.isArray(v) ? v : {});

/** Pure: stems with a job in flight, from the /api/jobs body (an array or {jobs}); the file extension is dropped. */
export function activeStemsOf(jobs) {
  const list = Array.isArray(jobs) ? jobs : Array.isArray(obj(jobs).jobs) ? jobs.jobs : [];
  const out = new Set();
  for (const j of list) {
    if (!j || !ACTIVE_JOB.includes(j.status)) continue;
    const name = str(j.video_name).replace(VIDEO_EXT, '');
    if (name) out.add(name);
  }
  return out;
}

/** Pure: a video row's status from its /api/videos entry: an active job, then match, posture, court set, new. */
export function videoStatus(video, activeStems = new Set()) {
  const v = obj(video);
  if (str(v.name) && activeStems.has(v.name)) return 'processing';
  if (v.has_match === true) return 'analysed';
  if (v.has_posture === true) return 'posture';
  if (v.has_annotations === true) return 'court_set';
  return 'new';
}

/** Newest first, undated last, then by stem; Array.prototype.sort is stable, so equal rows keep their order. */
function byDate(a, b) {
  const da = a.date || '';
  const db = b.date || '';
  if (da !== db) return !da ? 1 : !db ? -1 : da < db ? 1 : -1;
  return a.stem < b.stem ? -1 : a.stem > b.stem ? 1 : 0;
}

function videoRow(v, activeStems) {
  return {
    kind: 'video',
    stem: v.name,
    date: typeof v.date === 'string' && v.date ? v.date : null,
    // A file date is the file's modification time, never the match date.
    dateApprox: true,
    status: videoStatus(v, activeStems),
    side: null,
    athlete: null,
    opponent: null,
    result: null,
    sets: [],
    setsText: '',
    techIndex: null,
    doubles: false,
    parts: [],
    recordDate: false,
    video: v,
  };
}

/**
 * Pure: {overview, videos, activeStems, scoped} -> {rows, videosOk}. Match rows are matchRows(overview) with `kind:
 * 'match'` and the stem's /api/videos entry as `video` (null when the file is gone), and each part its own `video`.
 * Unscoped, every video that is neither a match row nor a part of one adds a `kind: 'video'` row. Scoped, the other
 * videos may be other athletes' matches, so only the scope's match rows are listed. `videos` null (the list failed)
 * gives `videosOk: false` and match rows only. Rows are newest first, undated last.
 */
export function libraryRows({ overview, videos, activeStems = new Set(), scoped = false } = {}) {
  const videosOk = Array.isArray(videos);
  const byStem = new Map();
  for (const v of videosOk ? videos : []) {
    if (v && typeof v === 'object' && str(v.name) && !byStem.has(v.name)) byStem.set(v.name, v);
  }
  const covered = new Set();
  const rows = matchRows(overview).map((r) => {
    covered.add(r.stem);
    r.parts.forEach((p) => covered.add(p.stem));
    return { ...r, kind: 'match', video: byStem.get(r.stem) || null, parts: r.parts.map((p) => ({ ...p, video: byStem.get(p.stem) || null })) };
  });
  if (!scoped) {
    const extra = [...byStem.values()].filter((v) => !covered.has(v.name)).map((v) => videoRow(v, activeStems));
    rows.push(...extra);
  }
  rows.sort(byDate);
  return { rows, videosOk };
}

/** Pure: a row's tags: system tags (Match for a match row or a video with a run, Drill with posture) then the user's. */
export function rowTags(row) {
  const r = obj(row);
  const v = obj(r.video);
  const out = [];
  if (r.kind === 'match' || v.has_match === true) out.push('match');
  if (v.has_posture === true) out.push('drill');
  for (const tag of Array.isArray(v.tags) ? v.tags : []) if (typeof tag === 'string' && tag && !out.includes(tag)) out.push(tag);
  return out;
}

export const isSysTag = (tag) => SYS_TAGS.includes(tag);

/** A tag as shown: system tags localised, user tags verbatim. */
export function tagLabel(tag, lang) {
  return isSysTag(tag) ? t('lib.sys.' + tag, lang) : str(tag);
}

function searchText(row, lang) {
  const r = obj(row);
  const parts = [r.stem, str(obj(r.video).filename)];
  if (r.kind === 'match') {
    const { title, sub } = rowTitle(r, lang);
    parts.push(title, sub);
  }
  for (const tag of rowTags(r)) parts.push(tag, tagLabel(tag, lang));
  return parts.join('\n').toLowerCase();
}

function passes(row, f, lang, tags) {
  const group = STATUS_GROUP[f.status];
  if (group && !group.includes(row.status)) return false;
  if (tags.length) {
    const own = rowTags(row);
    if (!tags.every((x) => own.includes(x))) return false;
  }
  const q = str(f.q).trim().toLowerCase();
  return !q || searchText(row, lang).includes(q);
}

const tagsOf = (f) => (Array.isArray(obj(f).tags) ? f.tags : []);

/** Pure: the rows left by filter {q, status, tags} (tags combine with AND; the search is a case-insensitive substring). */
export function filterRows(rows, filter, lang) {
  const f = { ...EMPTY_FILTER, ...obj(filter) };
  return (Array.isArray(rows) ? rows : []).filter((r) => r && passes(r, f, lang, tagsOf(f)));
}

/** Pure: a sorted copy: 'name' by stem, anything else newest first. */
export function sortRows(rows, sort) {
  const list = [...(Array.isArray(rows) ? rows : [])];
  if (sort === 'name') return list.sort((a, b) => a.stem.localeCompare(b.stem));
  return list.sort(byDate);
}

/**
 * Pure: the tag bar: one entry per tag on any row, system tags first, then user tags A-Z. `count` is how many rows
 * the filter would leave with this tag added to the active ones (for an active tag: the rows shown now).
 */
export function tagCounts(rows, filter, lang) {
  const f = { ...EMPTY_FILTER, ...obj(filter) };
  const active = tagsOf(f);
  const list = Array.isArray(rows) ? rows.filter(Boolean) : [];
  const seen = new Set();
  for (const r of list) rowTags(r).forEach((x) => seen.add(x));
  const user = [...seen].filter((x) => !isSysTag(x)).sort((a, b) => a.localeCompare(b));
  return [...SYS_TAGS.filter((x) => seen.has(x)), ...user].map((tag) => {
    const want = active.includes(tag) ? active : [...active, tag];
    return {
      tag,
      label: tagLabel(tag, lang),
      count: list.filter((r) => passes(r, f, lang, want)).length,
      active: active.includes(tag),
      sys: isSysTag(tag),
    };
  });
}

/** Pure: does the filter narrow the list at all? */
export function isFiltered(filter) {
  const f = obj(filter);
  return !!str(f.q).trim() || (!!f.status && f.status !== 'all') || tagsOf(f).length > 0;
}

/** Pure: where a row's click goes: the workspace, the posture report, the pipeline or the new-analysis wizard. */
export function openTarget(row) {
  const r = obj(row);
  if (r.kind === 'match' || r.status === 'analysed') return formatHash('matches', { stem: r.stem });
  if (r.status === 'posture') return formatHash('results', { stem: r.stem }, { mode: 'posture' });
  if (r.status === 'processing') return formatHash('pipeline');
  return formatHash('new');
}

/** The stored form of a typed tag (library.tags.normalise: trim, lower-case). */
export function normTag(raw) {
  return str(raw).trim().toLowerCase();
}

/** Pure: why a typed tag would be refused (an i18n key), or null. Mirrors badminton_analysis.library.tags.normalise. */
export function tagError(raw) {
  const clean = normTag(raw);
  if (!clean) return 'lib.tagerr.empty';
  if (clean.length > TAG_MAX) return 'lib.tagerr.long';
  if (clean.replace(/\./g, '') === '') return 'lib.tagerr.dots';
  if (isSysTag(clean)) return 'lib.tagerr.reserved';
  if (clean.includes('/') || clean.includes('\\')) return 'lib.tagerr.slash';
  // eslint-disable-next-line no-control-regex
  if (/[\u0000-\u001f\u007f]/.test(clean)) return 'lib.tagerr.control';
  return null;
}
