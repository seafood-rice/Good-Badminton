// Date-range scope rules (spec 5.4). DOM-free; dates are ISO "YYYY-MM-DD" handled in UTC.

export const RANGES = ['7d', '30d', 'season', 'all'];
const DAY_MS = 86400000;

function parseIso(iso) {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(typeof iso === 'string' ? iso : '');
  if (!m) return null;
  const y = +m[1];
  const mo = +m[2];
  const d = +m[3];
  const t = Date.UTC(y, mo - 1, d);
  const dt = new Date(t);
  if (dt.getUTCFullYear() !== y || dt.getUTCMonth() !== mo - 1 || dt.getUTCDate() !== d) return null;
  return t;
}

function toIso(t) {
  return new Date(t).toISOString().slice(0, 10);
}

/**
 * Bounds of `range` anchored on `referenceIso` (the latest date in the selection):
 * 7d / 30d = reference minus N-1 days, season = Jan 1 of the reference year, all = no lower bound.
 * Throws RangeError on an unknown range or an unparseable reference date.
 */
export function rangeBounds(range, referenceIso) {
  if (!RANGES.includes(range)) throw new RangeError(`unknown range: ${range}`);
  if (referenceIso === null || referenceIso === undefined) {
    if (range === 'all') return { from: null, to: null };
    throw new RangeError('reference date required');
  }
  const t = parseIso(referenceIso);
  if (t === null) throw new RangeError(`bad reference date: ${referenceIso}`);
  const to = toIso(t);
  if (range === 'all') return { from: null, to };
  if (range === 'season') return { from: `${to.slice(0, 4)}-01-01`, to };
  const days = range === '7d' ? 7 : 30;
  return { from: toIso(t - (days - 1) * DAY_MS), to };
}

/** Points ({date, ...}) inside `range`, anchored on the latest dated point. Input order is kept; undated points drop out. */
export function pickRange(points, range) {
  if (!Array.isArray(points)) return [];
  const dated = points
    .map((p) => ({ p, t: p ? parseIso(p.date) : null }))
    .filter((o) => o.t !== null);
  if (!dated.length) return [];
  const latest = Math.max(...dated.map((o) => o.t));
  const { from, to } = rangeBounds(range, toIso(latest));
  return dated
    .filter((o) => {
      const d = toIso(o.t);
      return (from === null || d >= from) && d <= to;
    })
    .map((o) => o.p);
}
