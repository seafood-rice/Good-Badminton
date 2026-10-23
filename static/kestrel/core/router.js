// Hash router: parse/format only (navigation and listeners live in main.js). DOM-free.
//   #/overview?athlete=&squad=&range=&lens=   #/matches   #/matches/<stem>   #/players
//   #/pipeline   #/new   #/results/<stem>?mode=   (#/library is a retired alias of #/matches)

// stem: false = no segment, 'optional' = may carry a stem, 'required' = needs a stem.
const ROUTES = {
  overview: { stem: false },
  matches: { stem: 'optional' },
  players: { stem: false },
  pipeline: { stem: false },
  new: { stem: false },
  results: { stem: 'required' },
};

export const ROUTE_NAMES = Object.keys(ROUTES);

const overview = (query = {}) => ({ name: 'overview', params: {}, query });

function parseQuery(qs) {
  const query = {};
  if (!qs) return query;
  for (const pair of qs.split('&')) {
    if (!pair) continue;
    const eq = pair.indexOf('=');
    const rawKey = eq < 0 ? pair : pair.slice(0, eq);
    const rawVal = eq < 0 ? '' : pair.slice(eq + 1);
    try {
      const key = decodeURIComponent(rawKey);
      const val = decodeURIComponent(rawVal);
      if (key && val !== '') query[key] = val;
    } catch (e) {
      // skip a malformed pair, keep the rest
    }
  }
  return query;
}

/** "#/matches/<stem>?x=y" -> {name, params, query}. Unknown or malformed hashes give the overview. */
export function parseHash(hash) {
  let h = typeof hash === 'string' ? hash : '';
  if (h.startsWith('#')) h = h.slice(1);
  const q = h.indexOf('?');
  const pathPart = q < 0 ? h : h.slice(0, q);
  const queryPart = q < 0 ? '' : h.slice(q + 1);
  const segments = pathPart.replace(/^\/+/, '').split('/');
  while (segments.length && segments[segments.length - 1] === '') segments.pop();
  if (!segments.length) return overview(parseQuery(queryPart));

  let decoded;
  try {
    decoded = segments.map(decodeURIComponent);
  } catch (e) {
    return overview();
  }
  const [name, stem, ...rest] = decoded;
  // The video library page was merged into Matches: its bare hash (with or without a query) still works.
  if (name === 'library' && stem === undefined) return { name: 'matches', params: {}, query: parseQuery(queryPart) };
  const route = ROUTES[name];
  if (!route || rest.length) return overview();
  if (stem === undefined) {
    return route.stem === 'required' ? overview() : { name, params: {}, query: parseQuery(queryPart) };
  }
  if (!route.stem || stem === '') return overview();
  return { name, params: { stem }, query: parseQuery(queryPart) };
}

/** Build a hash; stems and query values are percent-encoded; empty query values are dropped. */
export function formatHash(name, params = {}, query = {}) {
  const route = ROUTES[name];
  if (!route) return '#/overview';
  const stem = params && params.stem;
  const hasStem = route.stem && stem !== undefined && stem !== null && stem !== '';
  if (route.stem === 'required' && !hasStem) return '#/overview';
  let out = '#/' + name + (hasStem ? '/' + encodeURIComponent(stem) : '');
  const pairs = Object.entries(query || {})
    .filter(([, v]) => v !== undefined && v !== null && v !== '')
    .map(([k, v]) => encodeURIComponent(k) + '=' + encodeURIComponent(v));
  if (pairs.length) out += '?' + pairs.join('&');
  return out;
}
