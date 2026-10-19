// Number, time, date and HTML formatting helpers. DOM-free.

const DASH = '—';
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

function toNum(v) {
  if (v === null || v === undefined || v === '') return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}

/** Fixed-decimal number with thousands separators; a dash for missing values. */
export function fmtNumber(v, decimals = 0) {
  const n = toNum(v);
  if (n === null) return DASH;
  return n.toLocaleString('en-US', { minimumFractionDigits: decimals, maximumFractionDigits: decimals });
}

/** A 0-1 rate as a percentage. */
export function fmtPercent(rate, decimals = 0) {
  const n = toNum(rate);
  if (n === null) return DASH;
  return fmtNumber(n * 100, decimals) + '%';
}

const pad2 = (n) => String(n).padStart(2, '0');

/** Seconds as m:ss (h:mm:ss from one hour). Rounds to the nearest second. */
export function fmtDuration(seconds) {
  const n = toNum(seconds);
  if (n === null) return DASH;
  const total = Math.max(0, Math.round(n));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  return h > 0 ? `${h}:${pad2(m)}:${pad2(s)}` : `${m}:${pad2(s)}`;
}

/** Elapsed seconds as a running m:ss clock (floors, never negative). */
export function ago(seconds) {
  const n = toNum(seconds);
  const total = n === null ? 0 : Math.max(0, Math.floor(n));
  return `${Math.floor(total / 60)}:${pad2(total % 60)}`;
}

/** ISO date (or datetime) as "11 Oct 2026" / "2026年10月11日"; `{year: false}` drops the year. */
export function fmtDate(iso, lang, { year = true } = {}) {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(typeof iso === 'string' ? iso : '');
  if (!m) return DASH;
  const y = +m[1];
  const mo = +m[2];
  const d = +m[3];
  const dt = new Date(Date.UTC(y, mo - 1, d));
  if (dt.getUTCFullYear() !== y || dt.getUTCMonth() !== mo - 1 || dt.getUTCDate() !== d) return DASH;
  if (lang === 'zh') return (year ? `${y}年` : '') + `${mo}月${d}日`;
  return `${d} ${MONTHS[mo - 1]}` + (year ? ` ${y}` : '');
}

/** Escape text for interpolation into HTML (element content and quoted attributes). */
export function escHTML(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}
