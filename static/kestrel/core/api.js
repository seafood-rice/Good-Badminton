// Fetch helpers with timeout, abort and bilingual error mapping, plus a URL builder per backend route.
// `fetch` is injectable (opts.fetch) so the module imports and tests cleanly under Node.

export const DEFAULT_TIMEOUT_MS = 15000;

const GENERIC = {
  network: { zh: '无法连接到服务', en: 'Cannot reach the server' },
  timeout: { zh: '请求超时', en: 'The request timed out' },
  aborted: { zh: '请求已取消', en: 'The request was cancelled' },
  parse: { zh: '服务返回的内容无法识别', en: 'The server sent an unexpected response' },
  400: { zh: '请求无效', en: 'Invalid request' },
  404: { zh: '未找到', en: 'Not found' },
  409: { zh: '已存在相同的项', en: 'That already exists' },
  5: { zh: '服务器出错', en: 'Server error' },
  http: { zh: '请求失败', en: 'The request failed' },
};

export class ApiError extends Error {
  /** kind: 'network' | 'timeout' | 'aborted' | 'parse' | 'http'; messages: {zh, en}. */
  constructor(kind, status, messages) {
    super(messages.en);
    this.name = 'ApiError';
    this.kind = kind;
    this.status = status;
    this.messages = messages;
  }
}

function httpMessages(status) {
  return GENERIC[status] || (status >= 500 ? GENERIC[5] : GENERIC.http);
}

// Server error bodies are static "<zh> / <en>" strings. Anything else (tracebacks, paths) is not shown.
function bilingualFromBody(body) {
  const msg = body && typeof body === 'object' ? body.error : null;
  if (typeof msg !== 'string' || msg.length > 200 || /[\n\r\\]/.test(msg)) return null;
  const m = /^(.+?) \/ (.+)$/.exec(msg);
  if (!m || !/[一-鿿]/.test(m[1])) return null;
  return { zh: m[1].trim(), en: m[2].trim() };
}

export function errorText(err, lang) {
  const key = lang === 'en' ? 'en' : 'zh';
  const messages = err && err.messages ? err.messages : GENERIC.http;
  return messages[key];
}

function resolveFetch(opts) {
  if (opts && typeof opts.fetch === 'function') return opts.fetch;
  if (typeof fetch === 'function') return (...args) => fetch(...args);
  throw new ApiError('network', 0, GENERIC.network);
}

async function request(method, url, body, opts = {}) {
  const fetchImpl = resolveFetch(opts);
  const timeout = opts.timeout === undefined ? DEFAULT_TIMEOUT_MS : opts.timeout;
  const ctl = new AbortController();
  let timedOut = false;
  const timer = timeout > 0 ? setTimeout(() => { timedOut = true; ctl.abort(); }, timeout) : null;
  const outer = opts.signal;
  const onOuterAbort = () => ctl.abort();
  if (outer) {
    if (outer.aborted) ctl.abort();
    else outer.addEventListener('abort', onOuterAbort, { once: true });
  }
  try {
    const init = { method, signal: ctl.signal, headers: { Accept: 'application/json' } };
    if (body !== undefined) {
      init.headers['Content-Type'] = 'application/json';
      init.body = JSON.stringify(body);
    }
    let res;
    try {
      res = await fetchImpl(url, init);
    } catch (e) {
      if (timedOut) throw new ApiError('timeout', 0, GENERIC.timeout);
      if ((outer && outer.aborted) || (e && e.name === 'AbortError')) throw new ApiError('aborted', 0, GENERIC.aborted);
      throw new ApiError('network', 0, GENERIC.network);
    }
    if (!res.ok) {
      let errBody = null;
      try { errBody = await res.json(); } catch (e) { errBody = null; }
      throw new ApiError('http', res.status, bilingualFromBody(errBody) || httpMessages(res.status));
    }
    try {
      return await res.json();
    } catch (e) {
      if (timedOut) throw new ApiError('timeout', 0, GENERIC.timeout);
      throw new ApiError('parse', res.status, GENERIC.parse);
    }
  } finally {
    if (timer) clearTimeout(timer);
    if (outer) outer.removeEventListener('abort', onOuterAbort);
  }
}

export const getJSON = (url, opts) => request('GET', url, undefined, opts);
export const sendJSON = (method, url, body, opts) => request(method, url, body, opts);

const enc = encodeURIComponent;

function qs(params) {
  const pairs = Object.entries(params || {})
    .filter(([, v]) => v !== undefined && v !== null && v !== '')
    .map(([k, v]) => enc(k) + '=' + enc(v));
  return pairs.length ? '?' + pairs.join('&') : '';
}

/** URL builders for every route the UI uses (spec sections 4, 5.4, 5.5 plus the existing library routes). */
export const endpoints = {
  overview: (q) => '/api/insights/overview' + qs({ athlete: q && q.athlete, squad: q && q.squad, range: q && q.range }),
  videoInsights: (stem) => '/api/insights/video/' + enc(stem),
  jobs: () => '/api/jobs',
  athletes: () => '/api/athletes',
  athlete: (id) => '/api/athletes/' + enc(id),
  squads: () => '/api/squads',
  squad: (id) => '/api/squads/' + enc(id),
  match: (stem) => '/api/matches/' + enc(stem),
  videos: () => '/api/videos',
  stats: () => '/api/stats',
  status: (jobId) => '/api/status/' + enc(jobId),
  output: (stem, subpath) => '/api/output/' + enc(stem) + '/' + String(subpath).split('/').map(enc).join('/'),
};

/** Ready-made calls. Every function accepts trailing `opts` ({timeout, signal, fetch}). */
export const api = {
  overview: (q, opts) => getJSON(endpoints.overview(q), opts),
  videoInsights: (stem, opts) => getJSON(endpoints.videoInsights(stem), opts),
  jobs: (opts) => getJSON(endpoints.jobs(), opts),
  athletes: (opts) => getJSON(endpoints.athletes(), opts),
  createAthlete: (data, opts) => sendJSON('POST', endpoints.athletes(), data, opts),
  updateAthlete: (id, data, opts) => sendJSON('PUT', endpoints.athlete(id), data, opts),
  deleteAthlete: (id, opts) => sendJSON('DELETE', endpoints.athlete(id), undefined, opts),
  createSquad: (data, opts) => sendJSON('POST', endpoints.squads(), data, opts),
  updateSquad: (id, data, opts) => sendJSON('PUT', endpoints.squad(id), data, opts),
  deleteSquad: (id, opts) => sendJSON('DELETE', endpoints.squad(id), undefined, opts),
  match: (stem, opts) => getJSON(endpoints.match(stem), opts),
  saveMatch: (stem, record, opts) => sendJSON('PUT', endpoints.match(stem), record, opts),
  videos: (opts) => getJSON(endpoints.videos(), opts),
  stats: (opts) => getJSON(endpoints.stats(), opts),
  // Doubles run files (spec 2026-10-20 section 2): a 404 means the file is absent (callers treat it as none).
  players: (stem, opts) => getJSON(endpoints.output(stem, 'players.json'), opts),
  hits: (stem, opts) => getJSON(endpoints.output(stem, 'hits.json'), opts),
};
