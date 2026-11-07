// Pure layout logic for the match workspace's movable cards (no DOM). The layout is
// {order: {main: [id...], side: [id...]}, collapsed: [id...]}; every card id appears exactly once in `order`.

export const CARD_IDS = Object.freeze(['video', 'timeline', 'movement', 'landing', 'inspector', 'summary']);
export const ZONES = Object.freeze(['main', 'side']);

const DEFAULT_ORDER = Object.freeze({
  main: Object.freeze(['video', 'timeline']),
  side: Object.freeze(['movement', 'landing', 'inspector', 'summary']),
});

export const DEFAULT_LAYOUT = Object.freeze({ order: DEFAULT_ORDER, collapsed: Object.freeze([]) });

const defaultZoneOf = (id) => (DEFAULT_ORDER.main.includes(id) ? 'main' : 'side');

export function defaultLayout() {
  return { order: { main: [...DEFAULT_ORDER.main], side: [...DEFAULT_ORDER.side] }, collapsed: [] };
}

/** Anything stored (junk, an older or partial layout) -> a valid layout: unknown ids dropped, duplicates removed, missing ids appended to their default zone. */
export function normaliseLayout(raw) {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return defaultLayout();
  const order = raw.order && typeof raw.order === 'object' && !Array.isArray(raw.order) ? raw.order : null;
  if (!order) return defaultLayout();
  const out = { order: { main: [], side: [] }, collapsed: [] };
  const seen = new Set();
  for (const z of ZONES) {
    if (!Array.isArray(order[z])) continue;
    for (const id of order[z]) {
      if (typeof id === 'string' && CARD_IDS.includes(id) && !seen.has(id)) { seen.add(id); out.order[z].push(id); }
    }
  }
  for (const id of CARD_IDS) if (!seen.has(id)) out.order[defaultZoneOf(id)].push(id);
  if (Array.isArray(raw.collapsed)) {
    for (const id of raw.collapsed) if (CARD_IDS.includes(id) && !out.collapsed.includes(id)) out.collapsed.push(id);
  }
  return out;
}

/** Move a card to `index` of `zone` (clamped to the zone as it is once the card has left it). Returns a new layout. */
export function moveCard(layout, id, zone, index) {
  const l = normaliseLayout(layout);
  if (!CARD_IDS.includes(id) || !ZONES.includes(zone)) return l;
  for (const z of ZONES) l.order[z] = l.order[z].filter((x) => x !== id);
  const n = l.order[zone].length;
  const i = Number.isFinite(index) ? Math.max(0, Math.min(n, Math.trunc(index))) : n;
  l.order[zone].splice(i, 0, id);
  return l;
}

export function toggleCollapsed(layout, id) {
  const l = normaliseLayout(layout);
  if (!CARD_IDS.includes(id)) return l;
  l.collapsed = l.collapsed.includes(id) ? l.collapsed.filter((x) => x !== id) : [...l.collapsed, id];
  return l;
}

export function locate(layout, id) {
  for (const z of ZONES) {
    const i = layout.order[z].indexOf(id);
    if (i >= 0) return { zone: z, index: i };
  }
  return null;
}

/** Keyboard step: 'up' / 'down' within the zone, 'left' / 'right' to the other zone (same index, clamped). Returns {layout, zone, index, moved}. */
export function stepCard(layout, id, dir) {
  const l = normaliseLayout(layout);
  const at = locate(l, id);
  if (!at) return { layout: l, zone: null, index: -1, moved: false };
  const stay = { layout: l, zone: at.zone, index: at.index, moved: false };
  let zone = at.zone;
  let index = at.index;
  if (dir === 'up') index -= 1;
  else if (dir === 'down') index += 1;
  else if (dir === 'left' && zone === 'side') zone = 'main';
  else if (dir === 'right' && zone === 'main') zone = 'side';
  else return stay;
  if (zone === at.zone && (index < 0 || index > l.order[zone].length - 1)) return stay;
  const next = moveCard(l, id, zone, index);
  const to = locate(next, id);
  return { layout: next, zone: to.zone, index: to.index, moved: true };
}
