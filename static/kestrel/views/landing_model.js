// Shot-landing view model (landing spec 2026-11-02 section 6): the /landings payload -> dots on a full court
// (far half on top, court metres) and each side's front / mid / rear shares. Pure; never throws.

export const COURT_W = 6.1;
export const COURT_L = 13.4;
export const SIDES = ['upper', 'lower'];
const SOURCES = ['shuttle_feet', 'feet', 'floor'];
const DEPTHS = ['front', 'mid', 'rear'];

const obj = (v) => (v && typeof v === 'object' && !Array.isArray(v) ? v : {});
const num = (v) => (typeof v === 'number' && Number.isFinite(v) ? v : null);
const int = (v) => (Number.isInteger(v) && v >= 0 ? v : 0);

/**
 * landingModel(payload, {names}) -> {available, reason, dots, bands, total}.
 * dots: [{x, y, hitter, source, out, stroke, time}] with x, y in court metres (x clamped to a 1 m margin so an
 * out-of-court floor landing still shows at the edge). bands: [{side, name, n, shares: {front, mid, rear}|null}].
 * reason ('no_landings' | 'no_data') is set when nothing can be drawn.
 */
export function landingModel(payload, { names = {} } = {}) {
  const p = obj(payload);
  const dots = [];
  for (const d of Array.isArray(p.landings) ? p.landings : []) {
    const r = obj(d);
    const x = num(r.x);
    const y = num(r.y);
    if (x === null || y === null || !SIDES.includes(r.hitter)) continue;
    dots.push({
      x: Math.min(COURT_W + 1, Math.max(-1, x)), y: Math.min(COURT_L + 1, Math.max(-1, y)),
      hitter: r.hitter, source: SOURCES.includes(r.source) ? r.source : 'feet', out: r.out === true,
      stroke: typeof r.stroke === 'string' ? r.stroke : null, time: num(r.time),
    });
  }
  const bandsIn = obj(p.bands);
  const bands = SIDES.map((side) => {
    const b = obj(bandsIn[side]);
    const n = int(b.n);
    const shares = n ? Object.fromEntries(DEPTHS.map((k) => [k, int(b[k]) / n])) : null;
    return { side, name: typeof names[side] === 'string' && names[side] ? names[side] : null, n, shares };
  });
  const hasPayload = Array.isArray(p.landings);
  return {
    available: dots.length > 0,
    reason: dots.length ? null : hasPayload ? 'no_landings' : 'no_data',
    dots,
    bands,
    total: dots.length,
  };
}

/**
 * poolLandings(payloads, swapped) -> one payload in the first part's frame. A swapped part's sides are exchanged and
 * its points turned 180 degrees about the court centre (its near player is the primary's far one). Unusable payloads
 * are skipped; none usable gives null.
 */
export function poolLandings(payloads, swapped = []) {
  const flip = { upper: 'lower', lower: 'upper' };
  let any = false;
  const landings = [];
  const bands = { upper: { n: 0, front: 0, mid: 0, rear: 0 }, lower: { n: 0, front: 0, mid: 0, rear: 0 } };
  (Array.isArray(payloads) ? payloads : []).forEach((payload, i) => {
    const p = obj(payload);
    if (!Array.isArray(p.landings)) return;
    any = true;
    const sw = !!swapped[i];
    for (const d of p.landings) {
      const r = obj(d);
      const x = num(r.x);
      const y = num(r.y);
      if (x === null || y === null) continue;
      landings.push(sw ? { ...r, x: COURT_W - x, y: COURT_L - y, hitter: flip[r.hitter], side: flip[r.side] } : r);
    }
    const b = obj(p.bands);
    for (const side of SIDES) {
      const into = bands[sw ? flip[side] : side];
      for (const k of ['n', ...DEPTHS]) into[k] += int(obj(b[side])[k]);
    }
  });
  return any ? { landings, bands } : null;
}

/** The dots one filter shows: 'both', or one side's hits. */
export function visibleDots(model, filter) {
  const dots = obj(model).dots || [];
  return SIDES.includes(filter) ? dots.filter((d) => d.hitter === filter) : dots;
}
