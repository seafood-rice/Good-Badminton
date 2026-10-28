// Pure logic for the Strategy tab on the match details page (spec 2026-10-27 sections 5 and 8.3): which state the
// panel is in, which language's report to show, a section's claims flattened, and a claim's evidence as text. No DOM,
// so it loads under `node --test`. Every function tolerates missing or malformed input (the report comes off disk).

export const YOUR_GAME_PARTS = Object.freeze(['shot_selection', 'movement', 'patterns', 'strengths', 'weaknesses']);

const QUEUED = ['pending', 'queued'];
const RUNNING = ['running', 'resuming', 'paused'];

export const obj = (v) => (v && typeof v === 'object' && !Array.isArray(v) ? v : {});
const arr = (v) => (Array.isArray(v) ? v : []);
const isNum = (v) => typeof v === 'number' && Number.isFinite(v);

/** The panel's state from a GET /api/strategy/<stem> payload (null while it has not loaded). */
export function panelState(payload) {
  if (!payload || typeof payload !== 'object') return 'loading';
  const status = typeof payload.status === 'string' ? payload.status : 'none';
  const job = obj(payload.job);
  const js = typeof job.status === 'string' ? job.status : '';
  if (QUEUED.includes(js)) return 'queued';
  if (RUNNING.includes(js)) return 'running';
  if (js === 'error') return 'failed';
  const hasReport = !!payload.report && typeof payload.report === 'object';
  if (payload.enabled === false && !hasReport) return 'off';
  if (status === 'failed') return 'failed';
  if (status === 'ok' || status === 'partial') return payload.stale ? 'stale' : 'ready';
  if (hasReport && payload.stale) return 'stale';
  return 'none';
}

/** The per-language report: by_lang[lang], else the other language, else null. */
export function reportFor(payload, lang) {
  const by = obj(obj(obj(payload).report).by_lang);
  const other = lang === 'zh' ? 'en' : 'zh';
  const pick = by[lang] && typeof by[lang] === 'object' ? by[lang] : by[other];
  return pick && typeof pick === 'object' ? pick : null;
}

function claimList(v) {
  const list = Array.isArray(v) ? v : Array.isArray(obj(v).claims) ? v.claims : [];
  const out = [];
  for (const c of list) {
    if (!c || typeof c !== 'object' || typeof c.text !== 'string') continue;
    out.push({ text: c.text, evidence: arr(c.evidence).filter((x) => typeof x === 'string') });
  }
  return out;
}

/** A flat [{text, evidence}] from a section's claims, or from the your_game subsections in order. */
export function claimsOf(section) {
  const s = obj(section);
  if (Array.isArray(s.claims)) return claimList(s.claims);
  const out = [];
  for (const k of YOUR_GAME_PARTS) out.push(...claimList(s[k]));
  return out;
}

/** The claims of one your_game subsection ([] when absent). */
export const partClaims = (section, part) => claimList(obj(section)[part]);

/** True when a section was written by the deterministic fallback, not the model. */
export const isFallback = (section) => !!section && typeof section === 'object' && section.llm === false;

function valueText(v) {
  if (v === null || v === undefined) return '—';
  if (isNum(v)) return String(Math.round(v * 100) / 100);
  if (typeof v === 'string' || typeof v === 'boolean') return String(v);
  if (Array.isArray(v)) return v.map(valueText).join(', ');
  if (typeof v === 'object') {
    if (Array.isArray(v.seq)) return v.seq.join(' > ') + (isNum(v.n) ? ' x' + v.n : '');
    return Object.entries(v).map(([k, x]) => k + ': ' + valueText(x)).join(', ');
  }
  return String(v);
}

/** "id = value unit" lines for evidence ids; an id without a fact stays as the bare id. `facts` is a list or a map. */
export function evidenceText(ids, facts) {
  const map = new Map();
  if (Array.isArray(facts)) {
    for (const f of facts) if (f && typeof f.id === 'string') map.set(f.id, f);
  } else if (facts && typeof facts === 'object') {
    for (const [k, f] of Object.entries(facts)) map.set(k, obj(f));
  }
  return arr(ids).filter((id) => typeof id === 'string').map((id) => {
    const f = map.get(id);
    if (!f) return id;
    const unit = typeof f.unit === 'string' && f.unit ? ' ' + f.unit : '';
    return id + ' = ' + valueText(f.value) + unit;
  }).join('\n');
}

/** The other side's name for the "write from the other side" action. */
export function otherSide(focus) {
  return obj(focus).side === 'upper' ? 'lower' : 'upper';
}

/** Game plan items as [{do, why, cue, evidence}] and the optional training focus claim. */
export function planItems(section) {
  const items = [];
  for (const it of arr(obj(section).items)) {
    if (!it || typeof it !== 'object') continue;
    const s = (v) => (typeof v === 'string' ? v : '');
    items.push({ do: s(it.do), why: s(it.why), cue: s(it.cue), evidence: arr(it.evidence).filter((x) => typeof x === 'string') });
  }
  const tf = obj(obj(section).training_focus);
  return { items, trainingFocus: typeof tf.text === 'string' && tf.text ? { text: tf.text, evidence: arr(tf.evidence).filter((x) => typeof x === 'string') } : null };
}

export const MIN_ATTEMPTS = 5;

const count = (v) => (isNum(v) ? v : 0);

function effRows(cells) {
  const out = [];
  for (const [stroke, c] of Object.entries(obj(cells))) {
    const o = obj(c);
    const n = count(o.n);
    out.push({
      stroke, effective: count(o.effective), neutral: count(o.neutral), ineffective: count(o.ineffective),
      unknown: count(o.unknown), n, few: n < MIN_ATTEMPTS,
    });
  }
  return out.sort((a, b) => b.n - a.n || (a.stroke < b.stroke ? -1 : 1));
}

const zoneOf = (z) => (z && typeof z === 'object' && typeof z.depth === 'string' && typeof z.side === 'string' ? { depth: z.depth, side: z.side } : null);
const whoOf = (v) => (v === 'you' || v === 'opponent' ? v : null);

/** The rally-tactics tables of a report document (`doc.tables`), shaped for drawing; null for a report without them.
 *  { eff: {you: [row], opponent: [row]}, openings: {complete, total, insufficient, neutral, wonBy, winPoint, rows} } */
export function tacticsView(doc) {
  const tables = obj(obj(doc).tables);
  if (!('effectiveness' in tables) && !('openings' in tables)) return null;
  const eff = obj(tables.effectiveness);
  const op = obj(tables.openings);
  const wonBy = obj(op.won_by);
  const wp = obj(op.opening_winner_won_point);
  const complete = count(op.complete);
  return {
    eff: { you: effRows(eff.you), opponent: effRows(eff.opponent) },
    openings: {
      complete, total: count(op.total), neutral: count(op.neutral),
      insufficient: op.insufficient === true || complete < MIN_ATTEMPTS,
      wonBy: { you: count(wonBy.you), opponent: count(wonBy.opponent) },
      winPoint: { n: count(wp.n), won: count(wp.won) },
      rows: arr(op.rows).filter((r) => r && typeof r === 'object').map((r) => ({
        rally: typeof r.rally === 'number' || typeof r.rally === 'string' ? r.rally : '',
        advantage: r.advantage === 'neutral' ? 'neutral' : whoOf(r.advantage),
        pointWinner: whoOf(r.point_winner),
        nShots: count(r.n_shots),
        shots: arr(r.shots).filter((s) => s && typeof s === 'object').map((s) => ({
          hitter: whoOf(s.hitter), stroke: typeof s.stroke === 'string' ? s.stroke : '', zone: zoneOf(s.zone), sentTo: zoneOf(s.sent_to),
        })),
      })),
    },
  };
}
