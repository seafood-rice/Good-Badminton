import test from 'node:test';
import assert from 'node:assert/strict';
import { getJSON, sendJSON, ApiError, errorText, endpoints, api } from '../../static/kestrel/core/api.js';

const resp = (status, body, ok = status >= 200 && status < 300) => ({
  ok, status, json: async () => { if (body === undefined) throw new Error('no body'); return body; },
});

const abortable = () => (url, init) => new Promise((_, reject) => {
  init.signal.addEventListener('abort', () => { const e = new Error('aborted'); e.name = 'AbortError'; reject(e); });
});

test('getJSON returns parsed JSON', async () => {
  const fetchImpl = async (url) => { assert.equal(url, '/x'); return resp(200, { a: 1 }); };
  assert.deepEqual(await getJSON('/x', { fetch: fetchImpl }), { a: 1 });
});

test('HTTP error maps to a bilingual message; bilingual server bodies pass through', async () => {
  const bad = async () => resp(400, { error: '\u53c2\u6570\u65e0\u6548 / Invalid parameter' });
  await assert.rejects(getJSON('/x', { fetch: bad }), (e) => {
    assert.ok(e instanceof ApiError);
    assert.equal(e.kind, 'http');
    assert.equal(e.status, 400);
    assert.equal(errorText(e, 'zh'), '\u53c2\u6570\u65e0\u6548');
    assert.equal(errorText(e, 'en'), 'Invalid parameter');
    return true;
  });
  const leaky = async () => resp(500, { error: 'Traceback (most recent call last): C:\\secret' });
  await assert.rejects(getJSON('/x', { fetch: leaky }), (e) => {
    assert.ok(!errorText(e, 'en').includes('Traceback'));
    assert.ok(errorText(e, 'en').length > 0 && errorText(e, 'zh').length > 0);
    return true;
  });
  const nobody = async () => resp(404, undefined);
  await assert.rejects(getJSON('/x', { fetch: nobody }), (e) => e.status === 404 && e.kind === 'http');
});

test('network failure and bad JSON are distinct kinds', async () => {
  await assert.rejects(getJSON('/x', { fetch: async () => { throw new TypeError('failed'); } }), (e) => e.kind === 'network');
  await assert.rejects(getJSON('/x', { fetch: async () => resp(200, undefined) }), (e) => e.kind === 'parse');
});

test('timeout aborts the request', async () => {
  await assert.rejects(getJSON('/x', { fetch: abortable(), timeout: 20 }), (e) => e.kind === 'timeout');
});

test('caller abort is reported as aborted, not timeout', async () => {
  const ctl = new AbortController();
  const p = getJSON('/x', { fetch: abortable(), timeout: 5000, signal: ctl.signal });
  ctl.abort();
  await assert.rejects(p, (e) => e.kind === 'aborted');
});

test('sendJSON posts a JSON body', async () => {
  let seen;
  const fetchImpl = async (url, init) => { seen = { url, init }; return resp(201, { id: 'a_1' }); };
  const out = await sendJSON('POST', '/api/athletes', { name: 'X' }, { fetch: fetchImpl });
  assert.deepEqual(out, { id: 'a_1' });
  assert.equal(seen.init.method, 'POST');
  assert.equal(seen.init.headers['Content-Type'], 'application/json');
  assert.equal(seen.init.body, '{"name":"X"}');
});

test('endpoint helpers encode stems and skip empty query values', () => {
  assert.equal(endpoints.overview({ athlete: 'a_1', range: '30d', squad: null }), '/api/insights/overview?athlete=a_1&range=30d');
  assert.equal(endpoints.overview({}), '/api/insights/overview');
  assert.equal(endpoints.videoInsights('Dji 0004 D'), '/api/insights/video/Dji%200004%20D');
  assert.equal(endpoints.match('a b'), '/api/matches/a%20b');
  assert.equal(endpoints.jobs(), '/api/jobs');
  assert.equal(endpoints.jobSlots(), '/api/jobs/slots');
  assert.equal(endpoints.cancelJob('posture_a b'), '/api/jobs/posture_a%20b/cancel');
  assert.equal(endpoints.pauseJob('posture_a b'), '/api/jobs/posture_a%20b/pause');
  assert.equal(endpoints.resumeJob('a'), '/api/jobs/a/resume');
  assert.equal(endpoints.athletes(), '/api/athletes');
  assert.equal(endpoints.athlete('a_1'), '/api/athletes/a_1');
  assert.equal(endpoints.squads(), '/api/squads');
  assert.equal(endpoints.squad('s_1'), '/api/squads/s_1');
  assert.equal(endpoints.output('a b', 'detect_a b_h264.mp4'), '/api/output/a%20b/detect_a%20b_h264.mp4');
  assert.equal(endpoints.output('a', 'sub/dir/x.json'), '/api/output/a/sub/dir/x.json');
});

test('api.players / api.hits read the doubles run files; a 404 is an http error the caller treats as absent', async () => {
  const urls = [];
  const fetchImpl = async (url) => { urls.push(url); return url.endsWith('players.json') ? resp(200, { status: 'ok' }) : resp(404, {}); };
  assert.deepEqual(await api.players('a b', { fetch: fetchImpl }), { status: 'ok' });
  await assert.rejects(api.hits('a b', { fetch: fetchImpl }), (e) => e.kind === 'http' && e.status === 404);
  assert.deepEqual(urls, ['/api/output/a%20b/players.json', '/api/output/a%20b/hits.json']);
});

test('library calls: tags, tag rename / delete, delete preview and delete (stems percent-encoded)', async () => {
  const seen = [];
  const fetchImpl = async (url, init) => { seen.push([init.method, url, init.body ? JSON.parse(init.body) : undefined]); return resp(200, { ok: true }); };
  const o = { fetch: fetchImpl };
  await api.setTags('a b', ['smash'], o);
  await api.renameTag('old', 'new', o);
  await api.deleteTag('x/y', o);
  await api.deletePreview('a b', o);
  await api.deleteVideo('a b', o);
  assert.deepEqual(seen, [
    ['PUT', '/api/videos/a%20b/tags', { tags: ['smash'] }],
    ['POST', '/api/tags/rename', { from: 'old', to: 'new' }],
    ['DELETE', '/api/tags/x%2Fy', undefined],
    ['GET', '/api/delete-preview/a%20b?scope=all', undefined],
    ['POST', '/api/delete/a%20b', { scope: 'all' }],
  ]);
  assert.equal(endpoints.videoTags('q"'), '/api/videos/q%22/tags');
});

test('deletePreview / deleteVideo keep opts when the scope is passed as undefined (it means "all")', async () => {
  const seen = [];
  const fetchImpl = async (url, init) => { seen.push([init.method, url, init.body ? JSON.parse(init.body) : undefined]); return resp(200, { ok: true }); };
  await api.deletePreview('v', undefined, { fetch: fetchImpl });
  await api.deleteVideo('v', undefined, { fetch: fetchImpl });
  await api.deletePreview('v', 'clips', { fetch: fetchImpl });
  assert.deepEqual(seen, [
    ['GET', '/api/delete-preview/v?scope=all', undefined],
    ['POST', '/api/delete/v', { scope: 'all' }],
    ['GET', '/api/delete-preview/v?scope=clips', undefined],
  ]);
});
