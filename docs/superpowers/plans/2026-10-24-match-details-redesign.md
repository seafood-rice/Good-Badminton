# Match Details Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn `#/matches/<stem>` into the one match details page: layout A (player on top, tabs below) with
everything from the legacy match results screen, a part switcher with a Whole match view for split matches, and a
volume / mute control on the player.

**Architecture:** Vanilla ES modules, no build. The pure logic moves from `views/workspace.js` to a new DOM-free
`views/workspace_model.js` (re-exported by `workspace.js`). The video card becomes `widgets/player.js`. New widgets
`movement.js`, `technique.js`, `clips.js`, `plan.js` render one tab / card each. `workspace.js` keeps the page:
loading, header, switcher, tabs, URL handling (`setQuery`), whole-match view, timeline / strip / list / inspector.

**Tech Stack:** Vanilla JS ES modules, `h()` DOM helper (`static/kestrel/widgets/frame.js`), node:test with the
fake DOM (`tests/js/fake_dom.mjs`), Flask app (unchanged), pytest JS-contract tests.

**Spec:** `docs/superpowers/specs/2026-10-24-match-details-redesign-design.md`

## Global Constraints

- No build step, no new dependency. ES modules under `static/kestrel/`.
- Every user-visible string goes through `t(key, lang)` with zh and en entries in `static/kestrel/core/i18n.js`
  (`tests/js/i18n.test.mjs` enforces parity). Strings from files (stems, plan text, clip names, weakness
  descriptions) are set with `textContent` / `h()` children, never parsed as HTML.
- No new server endpoints. APIs used: `/api/output/<stem>/<file>`, `/api/technique/<stem>`,
  `/api/insights/video/<stem>`, `/api/matches/<stem>`, `/api/videos`, `POST /api/clip`,
  `GET|POST /api/training-plan/<stem>`, `/api/delete-preview/<stem>?scope=`, `POST /api/delete/<stem>`.
- Honesty rules stay: no point outcome; swing segments are "segments (estimated)", degraded ones "windows";
  uncertain strokes muted; opponent strokes never scored; doubles withholds stroke types and technique.
- URL: split match = primary stem; `?part=<part stem>` selects a part; no `part` = Whole match; a part's own stem
  is replaced (no history entry) by `#/matches/<primary>?part=<part>`; `tab` in `strokes|technique|clips|plan`.
- Volume prefs keys: `kestrel_volume` (0..1 string), `kestrel_muted` ('1' / '0').
- Test commands: `node --test tests/js/*.test.mjs`; full Python suite
  `PYTHONUTF8=1 ./.venv/Scripts/python.exe -B -m pytest -q -p no:cacheprovider --basetemp <unique>` (the venv lives
  in the main checkout: `../../../.venv/Scripts/python.exe` from the worktree).
- Commit after each task, message ending with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. A split match whose part has no outputs yet (status new): the Whole match card says "not analysed" and the
   pools ignore it; selecting it shows the not-analysed state, header and switcher still there. (Task 7 test.)
2. Opening an old link to a part stem: lands on the primary with that part selected, and Back does not bounce
   back to the part stem. (Task 7 test: `router.replace` called, not `navigate`.)
3. Volume when storage throws (private mode): the slider and mute still work, nothing crashes. (Task 3 test.)
4. Tab change does not reload the video (same `video.src`, no remount). (Task 6 test.)
5. A doubles run: Technique tab says withheld, Strokes tab shows the hits table, Movement shows team / player
   heatmaps, no part switcher. (Task 6 test.)

---

### Task 1: Move the pure workspace logic to `workspace_model.js`

**Files:**
- Create: `static/kestrel/views/workspace_model.js`
- Modify: `static/kestrel/views/workspace.js` (lines 1-370: the pure section)
- Test: `tests/js/workspace_model.test.mjs`

**Interfaces:**
- Produces (exported from `workspace_model.js`, and still re-exported by `workspace.js` under the same names):
  `RALLY_PAD_SEC, JOIN_FRAMES, STROKE_PAGE, summariseRallyPayload, buildWorkspaceModel, matchDate,
  matchDateApprox, listScore, rallyInput, rallyCountText, capItems, readReduced, resolveFps, soft, nameFor,
  unitKey, signalNote, techLabel, strokeName, techCode, metricLabel, clockText, num1, isNum, obj, SIDES`.

- [ ] **Step 1: Write the failing test** `tests/js/workspace_model.test.mjs`:

```js
import test from 'node:test';
import assert from 'node:assert/strict';
import * as M from '../../static/kestrel/views/workspace_model.js';
import * as W from '../../static/kestrel/views/workspace.js';

test('workspace_model: exports the pure helpers; workspace.js re-exports the same functions', () => {
  for (const name of ['summariseRallyPayload', 'buildWorkspaceModel', 'matchDate', 'matchDateApprox', 'listScore',
    'rallyInput', 'rallyCountText', 'capItems', 'readReduced']) {
    assert.equal(typeof M[name], 'function', name);
    assert.equal(W[name], M[name], name + ' re-exported');
  }
  assert.equal(M.RALLY_PAD_SEC, 0.5);
  assert.equal(M.JOIN_FRAMES, 3);
  assert.equal(M.STROKE_PAGE, 200);
});

test('workspace_model: imports without a DOM', () => {
  assert.equal(typeof globalThis.document, 'undefined');
  const m = M.buildWorkspaceModel({ rallies: { rallies: [{ start_sec: 1, end_sec: 4 }] }, strokes: [{ frame: 60, hitter: 'lower', stroke: 'smash' }], fps: 30 });
  assert.equal(m.rallies.length, 1);
  assert.equal(m.strokes[0].rallyId, 1);
});
```

- [ ] **Step 2: Run it, expect FAIL** (`Cannot find module .../workspace_model.js`):
  `node --test tests/js/workspace_model.test.mjs`

- [ ] **Step 3: Move the code.** Cut everything in `workspace.js` from `const SIDES` through `nameFor` (the whole
  `// ---------- text helpers` block, `soft`, `capItems`, `readReduced`, `reducedMotion`) into
  `workspace_model.js`, with its imports (`t, TECHNIQUE_IDS, STROKE_TYPE_IDS, STROKE_GROUP` from `../core/i18n.js`;
  `fmtDuration, fmtNumber, isApproxDate` from `../core/format.js`). Export every helper listed in Interfaces
  (add `export` to the private ones). In `workspace.js` replace the block with:

```js
import {
  RALLY_PAD_SEC, JOIN_FRAMES, STROKE_PAGE, summariseRallyPayload, buildWorkspaceModel, matchDate, matchDateApprox,
  listScore, rallyInput, rallyCountText, capItems, readReduced, resolveFps, soft, nameFor, unitKey, signalNote,
  techLabel, strokeName, techCode, metricLabel, clockText, num1, isNum, obj, SIDES,
} from './workspace_model.js';

export {
  RALLY_PAD_SEC, JOIN_FRAMES, STROKE_PAGE, summariseRallyPayload, buildWorkspaceModel, matchDate, matchDateApprox,
  listScore, rallyInput, rallyCountText, capItems, readReduced,
};
```

  Keep `LONG` and `SETTLE_MS` in `workspace.js` (DOM timing). Update the file-head comment of both files.

- [ ] **Step 4: Run** `node --test tests/js/*.test.mjs` - all pass (existing `views_model.test.mjs` imports
  from `workspace.js` and must still pass unchanged).

- [ ] **Step 5: Commit** `refactor(kestrel): move the pure workspace logic to workspace_model.js`.

---

### Task 2: New pure helpers (distribution, technique summary, weaknesses, parts, URL, pooling, plan)

**Files:**
- Modify: `static/kestrel/views/workspace_model.js`
- Modify: `static/kestrel/core/i18n.js` (none needed here: helpers return data, not text)
- Test: `tests/js/workspace_model.test.mjs`

**Interfaces (all exported from `workspace_model.js`):**
- `GROUP_ORDER = ['serve','clear','lift','smash','drop','drive','net']`
- `strokeDistribution(doc) -> {groups:[{id,count}], total, stale:boolean}`: from `strokes.json`; `doc.schema !== 2`
  -> `{groups:[], total:0, stale:true}`; `groups` in GROUP_ORDER, counts > 0 only, from `doc.groups`.
- `trackNetNote(doc) -> boolean`: `doc.shuttle_source === 'tracknet'`.
- `techniqueSummary(payload, {doubles}) -> {state:'ok'|'empty'|'stale'|'doubles', count, scored, byType:[{id,count,avg}], weaknesses:[{metric,count}]}`:
  doubles -> state 'doubles'; no payload / no `summary.stroke_count` -> 'empty'; any report without a `scored` key
  -> 'stale'; byType sorted by count desc then id, `id` is the raw key (12 types or `unknown`), `avg` number|null;
  weaknesses = `summary.recurring_weaknesses` top 3 `{metric,count}`.
- `weaknessLines(report) -> [{metric, measured:number|null, ideal:[lo,hi]|null, direction, severity}]` from a raw
  strokes.jsonl report's `weaknesses` (objects with a string `metric` only).
- `matchParts(record, stem) -> {split:boolean, primary:string, parts:[{stem, n, swapped}]}`: `record` is the
  **primary's** `/api/matches/<primary>` body. Part 1 is the primary (`swapped:false`), then `record.parts` in order.
  No `parts` array or empty -> `{split:false, primary:stem, parts:[]}`.
- `resolvePartQuery({split, primary, parts}, stem, query) -> {redirect:string|null, view:'single'|'whole'|'part', partStem:string|null}`:
  - not split: `view:'single'`, `partStem: stem`; `redirect` = `formatHash('matches',{stem},{...query without part})` when `query.part` is present, else null.
  - split and `stem !== primary` (opened a part's own stem): `redirect = formatHash('matches',{stem:primary},{...query, part: stem})`.
  - split, `stem === primary`, no `query.part`: `view:'whole'`, `partStem:null`.
  - split, `query.part` names a part: `view:'part'`, `partStem: query.part`.
  - split, `query.part` unknown: `view:'whole'`, `redirect` = hash without `part`.
- `TABS = ['strokes','technique','clips','plan']`; `resolveTab(query) -> one of TABS` (default 'strokes').
- `poolDistribution(dists) -> {groups, total, stale}`: sums `strokeDistribution` results, ignoring stale ones;
  `stale` true only if every input is stale.
- `poolTechnique(summaries) -> techniqueSummary shape`: only `state:'ok'` inputs; counts summed; `avg` weighted by
  the count of entries that have an avg (`sum(avg*count)/sum(count)` over those, else null), rounded to 0.1;
  weaknesses merged by metric, counts summed, top 3; state 'ok' if any input ok, else the first input's state, else 'empty'.
- `poolRallyCounts(summaries) -> {count, label, failedParts}`: `summaries` are `buildWorkspaceModel(...).summary`
  per analysed part; failed ones counted in `failedParts` and excluded; label: any 'windows' -> 'windows', else
  any 'segments' -> 'segments', else 'rallies'.
- `partTechIndex(insights, side) -> number|null`: `insights.technique.value.by_side[side].index` when finite; with
  `side` null, the single side that has a finite index, else null.
- `planSessions(plan, loc) -> [{week, phase, session}]`: `loc` 'court' | 'home'; sessions whose `location === loc`,
  in week order. `planLocations` not needed.

- [ ] **Step 1: Write failing tests** (append to `tests/js/workspace_model.test.mjs`):

```js
import { formatHash } from '../../static/kestrel/core/router.js';

test('strokeDistribution: groups in order, zeroes dropped; old schema is stale', () => {
  const d = M.strokeDistribution({ schema: 2, groups: { smash: 13, net: 9, clear: 36, serve: 16, lift: 0 }, shuttle_source: 'tracknet' });
  assert.deepEqual(d.groups.map((g) => g.id), ['serve', 'clear', 'smash', 'net']);
  assert.equal(d.total, 74);
  assert.equal(d.stale, false);
  assert.equal(M.trackNetNote({ shuttle_source: 'tracknet' }), true);
  assert.deepEqual(M.strokeDistribution({ schema: 1, groups: { smash: 3 } }), { groups: [], total: 0, stale: true });
  assert.deepEqual(M.strokeDistribution(null), { groups: [], total: 0, stale: true });
});

test('techniqueSummary: ok / empty / stale / doubles', () => {
  const pay = { summary: { stroke_count: 68, scored_count: 45, by_type: { smash: { count: 10, avg_score: 43.9 }, clear: { count: 19, avg_score: 56.4 }, unknown: { count: 9, avg_score: null } },
    recurring_weaknesses: [{ metric: 'trunk_rotation', count: 43 }, { metric: 'a', count: 3 }, { metric: 'b', count: 2 }, { metric: 'c', count: 1 }] },
    strokes: [{ scored: true }] };
  const s = M.techniqueSummary(pay);
  assert.equal(s.state, 'ok');
  assert.equal(s.count, 68);
  assert.deepEqual(s.byType.map((b) => b.id), ['clear', 'smash', 'unknown']);
  assert.equal(s.weaknesses.length, 3);
  assert.equal(M.techniqueSummary(null).state, 'empty');
  assert.equal(M.techniqueSummary({ summary: { stroke_count: 2 }, strokes: [{}] }).state, 'stale');
  assert.equal(M.techniqueSummary(pay, { doubles: true }).state, 'doubles');
});

test('weaknessLines: keeps metric objects only', () => {
  const w = M.weaknessLines({ weaknesses: [{ metric: 'trunk_rotation', measured: 177.27, ideal_range: [30, 50], direction: 'over', severity: 'severe' }, 'junk', { measured: 1 }] });
  assert.deepEqual(w, [{ metric: 'trunk_rotation', measured: 177.27, ideal: [30, 50], direction: 'over', severity: 'severe' }]);
  assert.deepEqual(M.weaknessLines(null), []);
});

test('matchParts + resolvePartQuery: single, whole, part, own-stem redirect, unknown part', () => {
  const info = M.matchParts({ date: '2026-10-10', parts: [{ stem: 'p2', sides_swapped: true }] }, 'p1');
  assert.deepEqual(info, { split: true, primary: 'p1', parts: [{ stem: 'p1', n: 1, swapped: false }, { stem: 'p2', n: 2, swapped: true }] });
  assert.deepEqual(M.matchParts({ date: 'x' }, 's'), { split: false, primary: 's', parts: [] });
  assert.deepEqual(M.resolvePartQuery(info, 'p1', {}), { redirect: null, view: 'whole', partStem: null });
  assert.deepEqual(M.resolvePartQuery(info, 'p1', { part: 'p2', tab: 'clips' }), { redirect: null, view: 'part', partStem: 'p2' });
  assert.equal(M.resolvePartQuery(info, 'p2', { tab: 'plan' }).redirect, formatHash('matches', { stem: 'p1' }, { tab: 'plan', part: 'p2' }));
  const unk = M.resolvePartQuery(info, 'p1', { part: 'zz' });
  assert.equal(unk.view, 'whole');
  assert.equal(unk.redirect, formatHash('matches', { stem: 'p1' }, {}));
  const single = M.matchParts({}, 's');
  assert.deepEqual(M.resolvePartQuery(single, 's', {}), { redirect: null, view: 'single', partStem: 's' });
  assert.equal(M.resolvePartQuery(single, 's', { part: 'x', tab: 'clips' }).redirect, formatHash('matches', { stem: 's' }, { tab: 'clips' }));
  assert.equal(M.resolveTab({ tab: 'plan' }), 'plan');
  assert.equal(M.resolveTab({ tab: 'bogus' }), 'strokes');
});

test('pooling: distribution, technique (weighted), rally counts (weakest label)', () => {
  const a = M.strokeDistribution({ schema: 2, groups: { smash: 2, clear: 1 } });
  const b = M.strokeDistribution({ schema: 2, groups: { smash: 1, net: 4 } });
  const p = M.poolDistribution([a, b, M.strokeDistribution(null)]);
  assert.deepEqual(p.groups, [{ id: 'clear', count: 1 }, { id: 'smash', count: 3 }, { id: 'net', count: 4 }]);
  assert.equal(p.total, 8);
  const t1 = { state: 'ok', count: 10, scored: 5, byType: [{ id: 'smash', count: 2, avg: 50 }], weaknesses: [{ metric: 'x', count: 2 }] };
  const t2 = { state: 'ok', count: 4, scored: 2, byType: [{ id: 'smash', count: 6, avg: 70 }, { id: 'lift', count: 1, avg: null }], weaknesses: [{ metric: 'x', count: 1 }, { metric: 'y', count: 5 }] };
  const pt = M.poolTechnique([t1, t2, { state: 'empty' }]);
  assert.equal(pt.state, 'ok');
  assert.equal(pt.count, 14);
  assert.deepEqual(pt.byType.find((x) => x.id === 'smash'), { id: 'smash', count: 8, avg: 65 });
  assert.equal(pt.byType.find((x) => x.id === 'lift').avg, null);
  assert.deepEqual(pt.weaknesses, [{ metric: 'y', count: 5 }, { metric: 'x', count: 3 }]);
  assert.equal(M.poolTechnique([{ state: 'empty' }]).state, 'empty');
  const r = M.poolRallyCounts([{ count: 3, label: 'rallies', failed: false }, { count: 2, label: 'segments', failed: false }, { count: 0, failed: true }]);
  assert.deepEqual(r, { count: 5, label: 'segments', failedParts: 1 });
});

test('partTechIndex and planSessions', () => {
  const ins = { technique: { value: { by_side: { upper: null, lower: { index: 49.5 } } } } };
  assert.equal(M.partTechIndex(ins, 'lower'), 49.5);
  assert.equal(M.partTechIndex(ins, 'upper'), null);
  assert.equal(M.partTechIndex(ins, null), 49.5);
  assert.equal(M.partTechIndex(null, null), null);
  const plan = { weeks: [{ week: 1, phase: 'Foundation', sessions: [{ name: 'a', location: 'home' }, { name: 'b', location: 'court' }] }, { week: 2, phase: 'Progression', sessions: [{ name: 'c', location: 'court' }] }] };
  assert.deepEqual(M.planSessions(plan, 'court').map((x) => [x.week, x.session.name]), [[1, 'b'], [2, 'c']]);
  assert.deepEqual(M.planSessions(null, 'home'), []);
});
```

- [ ] **Step 2: Run, expect FAIL** (`M.strokeDistribution is not a function`).

- [ ] **Step 3: Implement** in `workspace_model.js` (import `formatHash` from `../core/router.js`):

```js
export const GROUP_ORDER = Object.freeze(['serve', 'clear', 'lift', 'smash', 'drop', 'drive', 'net']);
export const TABS = Object.freeze(['strokes', 'technique', 'clips', 'plan']);

export function strokeDistribution(doc) {
  const d = obj(doc);
  if (d.schema !== 2) return { groups: [], total: 0, stale: true };
  const g = obj(d.groups);
  const groups = GROUP_ORDER.filter((id) => isNum(g[id]) && g[id] > 0).map((id) => ({ id, count: g[id] }));
  return { groups, total: groups.reduce((s, x) => s + x.count, 0), stale: false };
}

export function trackNetNote(doc) {
  return obj(doc).shuttle_source === 'tracknet';
}

export function techniqueSummary(payload, opts = {}) {
  const empty = { count: 0, scored: 0, byType: [], weaknesses: [] };
  if (obj(opts).doubles) return { state: 'doubles', ...empty };
  const p = obj(payload);
  const s = obj(p.summary);
  if (!isNum(s.stroke_count) || s.stroke_count <= 0) return { state: 'empty', ...empty };
  const reps = Array.isArray(p.strokes) ? p.strokes : [];
  if (reps.some((r) => !r || typeof r !== 'object' || !Object.prototype.hasOwnProperty.call(r, 'scored'))) return { state: 'stale', ...empty };
  const byType = Object.entries(obj(s.by_type))
    .filter(([, v]) => v && typeof v === 'object' && isNum(v.count))
    .map(([id, v]) => ({ id, count: v.count, avg: isNum(v.avg_score) ? v.avg_score : null }))
    .sort((a, b) => b.count - a.count || (a.id < b.id ? -1 : 1));
  const weaknesses = (Array.isArray(s.recurring_weaknesses) ? s.recurring_weaknesses : [])
    .filter((w) => w && typeof w.metric === 'string' && isNum(w.count))
    .slice(0, 3).map((w) => ({ metric: w.metric, count: w.count }));
  return { state: 'ok', count: s.stroke_count, scored: isNum(s.scored_count) ? s.scored_count : 0, byType, weaknesses };
}

export function weaknessLines(report) {
  const list = Array.isArray(obj(report).weaknesses) ? report.weaknesses : [];
  return list.filter((w) => w && typeof w === 'object' && typeof w.metric === 'string').map((w) => ({
    metric: w.metric,
    measured: isNum(w.measured) ? w.measured : null,
    ideal: Array.isArray(w.ideal_range) && w.ideal_range.length === 2 && w.ideal_range.every(isNum) ? [w.ideal_range[0], w.ideal_range[1]] : null,
    direction: typeof w.direction === 'string' ? w.direction : null,
    severity: typeof w.severity === 'string' ? w.severity : null,
  }));
}

export function matchParts(record, stem) {
  const list = Array.isArray(obj(record).parts) ? record.parts.filter((p) => p && typeof p.stem === 'string' && p.stem) : [];
  if (!list.length) return { split: false, primary: stem, parts: [] };
  return { split: true, primary: stem,
    parts: [{ stem, n: 1, swapped: false }, ...list.map((p, i) => ({ stem: p.stem, n: i + 2, swapped: p.sides_swapped === true }))] };
}

function without(query, key) {
  const out = { ...obj(query) };
  delete out[key];
  return out;
}

export function resolvePartQuery(info, stem, query) {
  const q = obj(query);
  const i = obj(info);
  if (!i.split) {
    return { redirect: q.part !== undefined ? formatHash('matches', { stem }, without(q, 'part')) : null, view: 'single', partStem: stem };
  }
  if (stem !== i.primary) return { redirect: formatHash('matches', { stem: i.primary }, { ...q, part: stem }), view: 'part', partStem: stem };
  if (q.part === undefined) return { redirect: null, view: 'whole', partStem: null };
  if (i.parts.some((p) => p.stem === q.part)) return { redirect: null, view: 'part', partStem: q.part };
  return { redirect: formatHash('matches', { stem: i.primary }, without(q, 'part')), view: 'whole', partStem: null };
}

export function resolveTab(query) {
  const tab = obj(query).tab;
  return TABS.includes(tab) ? tab : 'strokes';
}

export function poolDistribution(dists) {
  const list = (Array.isArray(dists) ? dists : []).filter((d) => d && !d.stale);
  if (!list.length) return { groups: [], total: 0, stale: true };
  const sum = {};
  for (const d of list) for (const g of d.groups) sum[g.id] = (sum[g.id] || 0) + g.count;
  const groups = GROUP_ORDER.filter((id) => sum[id] > 0).map((id) => ({ id, count: sum[id] }));
  return { groups, total: groups.reduce((s, x) => s + x.count, 0), stale: false };
}

export function poolTechnique(summaries) {
  const all = (Array.isArray(summaries) ? summaries : []).filter((s) => s && typeof s === 'object');
  const ok = all.filter((s) => s.state === 'ok');
  if (!ok.length) return { state: all.length ? all[0].state || 'empty' : 'empty', count: 0, scored: 0, byType: [], weaknesses: [] };
  const types = new Map();
  const weak = new Map();
  for (const s of ok) {
    for (const b of s.byType) {
      const e = types.get(b.id) || { id: b.id, count: 0, wsum: 0, wn: 0 };
      e.count += b.count;
      if (b.avg !== null) { e.wsum += b.avg * b.count; e.wn += b.count; }
      types.set(b.id, e);
    }
    for (const w of s.weaknesses) weak.set(w.metric, (weak.get(w.metric) || 0) + w.count);
  }
  const byType = [...types.values()]
    .map((e) => ({ id: e.id, count: e.count, avg: e.wn ? Math.round((e.wsum / e.wn) * 10) / 10 : null }))
    .sort((a, b) => b.count - a.count || (a.id < b.id ? -1 : 1));
  const weaknesses = [...weak.entries()].map(([metric, count]) => ({ metric, count }))
    .sort((a, b) => b.count - a.count || (a.metric < b.metric ? -1 : 1)).slice(0, 3);
  return { state: 'ok', count: ok.reduce((n, s) => n + s.count, 0), scored: ok.reduce((n, s) => n + (s.scored || 0), 0), byType, weaknesses };
}

export function poolRallyCounts(summaries) {
  const list = (Array.isArray(summaries) ? summaries : []).filter((s) => s && typeof s === 'object');
  const good = list.filter((s) => !s.failed);
  const labels = new Set(good.map((s) => s.label));
  return {
    count: good.reduce((n, s) => n + (isNum(s.count) ? s.count : 0), 0),
    label: labels.has('windows') ? 'windows' : labels.has('segments') ? 'segments' : 'rallies',
    failedParts: list.length - good.length,
  };
}

export function partTechIndex(insights, side) {
  const bySide = obj(obj(obj(obj(insights).technique).value).by_side);
  const idx = (s) => { const v = obj(bySide[s]).index; return isNum(v) ? v : null; };
  if (SIDES.includes(side)) return idx(side);
  const found = SIDES.map(idx).filter((v) => v !== null);
  return found.length === 1 ? found[0] : null;
}

export function planSessions(plan, loc) {
  const weeks = Array.isArray(obj(plan).weeks) ? plan.weeks : [];
  const out = [];
  for (const wk of weeks) {
    if (!wk || typeof wk !== 'object') continue;
    for (const s of Array.isArray(wk.sessions) ? wk.sessions : []) {
      if (s && typeof s === 'object' && s.location === loc) out.push({ week: wk.week, phase: wk.phase, session: s });
    }
  }
  return out;
}
```

  Export `SIDES` too (Task 1 import list uses it).

- [ ] **Step 4: Run** `node --test tests/js/*.test.mjs`, all pass.

- [ ] **Step 5: Commit** `feat(kestrel): pure helpers for the match details page (parts, pooling, technique, plan)`.

---

### Task 3: `widgets/player.js` with mute + volume

**Files:**
- Create: `static/kestrel/widgets/player.js`
- Modify: `static/kestrel/views/workspace.js` (`buildVideo`, `paintTime`, `paintPlay`, `togglePlay`, `seekTo`,
  video teardown in `destroy` - replaced by the player)
- Modify: `static/kestrel/core/prefs.js` (`PREF_KEYS.volume = 'kestrel_volume'`, `PREF_KEYS.muted = 'kestrel_muted'`)
- Modify: `static/kestrel/core/i18n.js` (`ws.mute`, `ws.unmute`, `ws.volume_aria` in zh and en)
- Modify: `static/kestrel/widgets.css` (`.ws-vol`, `.ws-vol-range`)
- Test: `tests/js/player.test.mjs`

**Interfaces:**
- Produces: `createPlayer({files:string[] (URLs, tried in order), lang, prefs (an object with get/set, default the
  `prefs` singleton), labels:{prev,next}, onPrev(), onNext(), onSourceFallback(idx), playersVideo:boolean}) ->
  {el (the section.ws-video-card), stage, tagsEl, sigEl, video, seekTo(sec, fromSelection), togglePlay(),
  setMuted(bool), setVolume(0..1), get playersVideo(), destroy()}`.
- `seekTo` keeps the existing semantics (fromSelection subtracts 0.4 s; pending seek until metadata).
- Exported pure helper: `parseVolume(raw) -> number in [0,1]` (default 1; junk -> 1).

- [ ] **Step 1: Failing tests** `tests/js/player.test.mjs`:

```js
import test from 'node:test';
import assert from 'node:assert/strict';
import { installEnv, removeEnv, everything } from './fake_dom.mjs';
import { createPlayer, parseVolume } from '../../static/kestrel/widgets/player.js';

function memPrefs(init = {}) {
  const m = new Map(Object.entries(init));
  return { get: (k, fb = null) => (m.has(k) ? m.get(k) : fb), set: (k, v) => { m.set(k, String(v)); return true; }, m };
}

test('parseVolume: clamps and defaults to 1', () => {
  assert.equal(parseVolume('0.4'), 0.4);
  assert.equal(parseVolume('7'), 1);
  assert.equal(parseVolume('-1'), 0);
  assert.equal(parseVolume('x'), 1);
  assert.equal(parseVolume(null), 1);
});

test('player: restores volume and mute from prefs; the mute button toggles and saves', () => {
  installEnv();
  try {
    const prefs = memPrefs({ kestrel_volume: '0.3', kestrel_muted: '1' });
    const p = createPlayer({ files: ['/a.mp4'], lang: 'en', prefs });
    assert.equal(p.video.volume, 0.3);
    assert.equal(p.video.muted, true);
    const mute = everything(p.el).find((e) => e.classList && e.classList.contains('ws-mute'));
    assert.equal(mute.getAttribute('aria-pressed'), 'true');
    mute.dispatch('click');
    assert.equal(p.video.muted, false);
    assert.equal(prefs.m.get('kestrel_muted'), '0');
    const range = everything(p.el).find((e) => e.classList && e.classList.contains('ws-vol-range'));
    range.value = '0.6';
    range.dispatch('input');
    assert.equal(p.video.volume, 0.6);
    assert.equal(prefs.m.get('kestrel_volume'), '0.6');
    range.value = '0';
    range.dispatch('input');
    assert.equal(p.video.muted, true, 'volume 0 reads as muted');
    p.destroy();
  } finally { removeEnv(); }
});

test('player: M toggles mute from inside the card, not from a text field; storage that throws is harmless', () => {
  installEnv();
  try {
    const prefs = { get: () => { throw new Error('denied'); }, set: () => { throw new Error('denied'); } };
    const p = createPlayer({ files: ['/a.mp4'], lang: 'zh', prefs });
    assert.equal(p.video.muted, false);
    p.el.dispatch('keydown', { key: 'm', target: p.video, preventDefault() {} });
    assert.equal(p.video.muted, true);
    p.el.dispatch('keydown', { key: 'm', target: { tagName: 'INPUT', type: 'text' }, preventDefault() {} });
    assert.equal(p.video.muted, true, 'typing in a text field does not toggle');
    p.destroy();
  } finally { removeEnv(); }
});

test('player: theater button moves the card under body and back', () => {
  installEnv();
  try {
    const host = document.createElement('div');
    document.body.appendChild(host);
    const p = createPlayer({ files: ['/a.mp4'], lang: 'en', prefs: memPrefs() });
    host.appendChild(p.el);
    const th = everything(p.el).find((e) => e.classList && e.classList.contains('ws-theater'));
    th.dispatch('click');
    assert.equal(p.el.parentNode, document.body);
    assert.equal(th.getAttribute('aria-pressed'), 'true');
    th.dispatch('click');
    assert.equal(p.el.parentNode, host);
    p.destroy();
  } finally { removeEnv(); }
});
```

  If the fake DOM lacks `dispatch(type, init)` passing an event object with `target` / `key`, or `FakeEl`
  lacks `volume` / `muted` / `paused`, extend `tests/js/fake_dom.mjs` minimally (plain properties; `dispatch`
  merges `init` into the event object). Check `FakeEl` first; keep existing tests green.

- [ ] **Step 2: Run, expect FAIL** (module not found).

- [ ] **Step 3: Implement `player.js`.** Move `buildVideo`'s body (stage, tags, signal tag, message, control row,
  theater / full screen with `setTheater`, `toggleFullscreen`, `onFsChange`, `onKey`, teardown, candidate fallback,
  `error` / `loadedmetadata` / `timeupdate` / `play` / `pause` listeners, `paintTime`, `paintPlay`, `togglePlay`,
  `seekTo`) from `workspace.js` into `createPlayer`, unchanged in behaviour. Add after `seek` and before
  `theaterBtn` in the control row:

```js
const muteBtn = h('button', { type: 'button', class: 'ws-ibtn ws-mute', 'aria-pressed': 'false', onclick: () => setMuted(!video.muted) }, '🔊');
const volRange = h('input', { type: 'range', class: 'ws-vol-range', min: '0', max: '1', step: '0.05', value: '1', 'aria-label': t('ws.volume_aria', lang) });
volRange.addEventListener('input', () => setVolume(Number(volRange.value)));
const vol = h('span', { class: 'ws-vol' }, [muteBtn, volRange]);

const safeGet = (k) => { try { return prefs.get(k, null); } catch (e) { return null; } };
const safeSet = (k, v) => { try { prefs.set(k, v); } catch (e) { /* storage unavailable: session only */ } };
function paintVolume() {
  const muted = video.muted || video.volume === 0;
  setText(muteBtn, muted ? '🔇' : '🔊');
  setAttr(muteBtn, 'aria-pressed', muted ? 'true' : 'false');
  const label = t(muted ? 'ws.unmute' : 'ws.mute', lang);
  setAttr(muteBtn, 'aria-label', label);
  setAttr(muteBtn, 'title', label + ' (M)');
  if (document.activeElement !== volRange) volRange.value = String(muted ? 0 : video.volume);
}
function setMuted(on) {
  video.muted = !!on;
  if (!on && video.volume === 0) video.volume = 0.5;
  safeSet(PREF_KEYS.muted, on ? '1' : '0');
  paintVolume();
}
function setVolume(v) {
  const x = parseVolume(v);
  video.volume = x;
  video.muted = x === 0;
  safeSet(PREF_KEYS.volume, String(x));
  safeSet(PREF_KEYS.muted, x === 0 ? '1' : '0');
  paintVolume();
}
video.volume = parseVolume(safeGet(PREF_KEYS.volume));
video.muted = safeGet(PREF_KEYS.muted) === '1';
paintVolume();
video.addEventListener('volumechange', paintVolume);
sec.addEventListener('keydown', (ev) => {
  if (ev.key !== 'm' && ev.key !== 'M') return;
  const tg = ev.target || {};
  if (tg.tagName === 'TEXTAREA' || tg.isContentEditable || (tg.tagName === 'INPUT' && !['range', 'button', 'checkbox'].includes(tg.type))) return;
  if (typeof ev.preventDefault === 'function') ev.preventDefault();
  setMuted(!(video.muted || video.volume === 0));
});

export function parseVolume(raw) {
  const n = Number(raw);
  if (raw === null || raw === undefined || raw === '' || !Number.isFinite(n)) return 1;
  return Math.max(0, Math.min(1, n));
}
```

  When the video fails (every candidate tried), the existing `for (const b of ctlRow.querySelectorAll('button,input')) b.disabled = true;`
  also disables the volume controls (they sit in the control row). `destroy()` runs the old teardown (exit full
  screen, leave theater, remove listeners) and releases the media (`pause`, `removeAttribute('src')`, `load()`).
  In `workspace.js`, `renderAll` calls `createPlayer({files: candidates, lang, onPrev: () => stepUnit(-1),
  onNext: () => stepUnit(1), labels: {prev: t('ws.prev_rally', lang), next: t('ws.next_rally', lang)},
  playersVideo: ...})`, stores it in `refs.player`, and keeps `refs.tags` / `refs.sig` / `refs.video` pointing at
  the player's elements; `seekTo` calls go to `refs.player.seekTo`; `destroy` calls `refs.player.destroy()`. The
  doubles source fallback (players video -> annotated) passes `onSourceFallback` so `paintVideoTags` reruns.

  i18n: zh `'ws.mute': '静音'`, `'ws.unmute': '取消静音'`, `'ws.volume_aria': '音量'`; en `'ws.mute': 'Mute'`,
  `'ws.unmute': 'Unmute'`, `'ws.volume_aria': 'Volume'`.

  CSS (`widgets.css`, next to `.ws-ctl`): `.ws-vol{display:inline-flex;align-items:center;gap:6px;}`
  `.ws-vol-range{width:84px;accent-color:var(--accent);}` and at `max-width:560px` hide `.ws-vol-range` (mute stays).

- [ ] **Step 4: Run** `node --test tests/js/*.test.mjs`, all pass.

- [ ] **Step 5: Commit** `feat(kestrel): player widget with mute and volume (remembered per browser)`.

---

### Task 4: Movement card and scoped delete dialog

**Files:**
- Create: `static/kestrel/widgets/movement.js`
- Modify: `static/kestrel/widgets/videotools.js` (`openDeleteDialog` gains `scope`)
- Modify: `static/kestrel/core/i18n.js`, `static/kestrel/widgets.css`
- Test: `tests/js/movement.test.mjs`, `tests/js/videotools.test.mjs` (new case)

**Interfaces:**
- `movementCard({lang, items:[{stem, caption|null}], doubles:{teams:[{id, file}], players:[{id,label,file}]}|null}) -> HTMLElement`
  (`section.card.hud.ws-move`): for each item two figures, `match_heatmap.png` and `match_scatter.png` under
  `endpoints.output(stem, 'position_visualizations/heatmaps/match_heatmap.png')` and
  `.../scatter_plots/match_scatter.png`, captions `t('wsx.move.heatmap')` / `t('wsx.move.scatter')`, prefixed with
  the item caption (e.g. "Part 1") when given. `img` `error` replaces the figure body with
  `div.ws-move-missing` text `t('wsx.move.missing')`. Each figure is a `button.ws-move-fig` that opens
  `openImageDialog({lang, src, title})` (Escape / scrim / close button closes; focus returns). Doubles: one figure
  per team (`t('dbl.team', {n})`) and per player (`label`), from `players.json` `teams[].coverage_heatmap` and
  `players[].heatmap`.
- `openDeleteDialog({stem, lang, api, onClose, scope='all'})`: scope `all` unchanged; `match` -> title
  `wsx.del.match.title`, warning `wsx.del.match.warn`; `clips` -> `wsx.del.clips.title`, `wsx.del.clips.warn`.
  Preview and delete send the scope (`/api/delete-preview/<stem>?scope=<scope>`, body `{scope}`). Group labels
  for `clips_rally` / `clips_rep` added as `lib.del.group.clips_rally` / `lib.del.group.clips_rep` if missing.

- [ ] **Step 1: Failing tests.** `tests/js/movement.test.mjs`:

```js
import test from 'node:test';
import assert from 'node:assert/strict';
import { installEnv, removeEnv, everything } from './fake_dom.mjs';
import { movementCard } from '../../static/kestrel/widgets/movement.js';

test('movement: one heatmap + scatter per item, captioned by part; a broken image says not available', () => {
  installEnv();
  try {
    const card = movementCard({ lang: 'en', items: [{ stem: 'p 1', caption: 'Part 1' }, { stem: 'p2', caption: 'Part 2' }] });
    const imgs = everything(card).filter((e) => e.tagName === 'IMG');
    assert.equal(imgs.length, 4);
    assert.ok(imgs[0].getAttribute('src').includes('/api/output/p%201/position_visualizations/heatmaps/match_heatmap.png'));
    assert.ok(everything(card).some((e) => e.textContent && e.textContent.startsWith('Part 2')));
    imgs[1].dispatch('error');
    assert.ok(everything(card).some((e) => e.classList && e.classList.contains('ws-move-missing')));
  } finally { removeEnv(); }
});

test('movement: doubles shows team and player heatmaps', () => {
  installEnv();
  try {
    const card = movementCard({ lang: 'en', items: [{ stem: 'd', caption: null }],
      doubles: { teams: [{ id: 1, file: 'team1.png' }], players: [{ id: 'P1', label: 'Ann', file: 'p1.png' }] } });
    const srcs = everything(card).filter((e) => e.tagName === 'IMG').map((e) => e.getAttribute('src'));
    assert.ok(srcs.some((s) => s.endsWith('/team1.png')));
    assert.ok(srcs.some((s) => s.endsWith('/p1.png')));
  } finally { removeEnv(); }
});
```

  `videotools.test.mjs` new case (follow the file's existing patched-api style for the delete dialog test at line 91):
  `openDeleteDialog({stem:'s', lang:'en', api, scope:'clips'})` requests `deletePreview('s', 'clips')` (or the
  fetch URL with `scope=clips`) and the title text equals `t('wsx.del.clips.title','en')`.

- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement** both (read `videotools.js` `openDeleteDialog` and the `api` methods it calls first;
  thread `scope` through whatever api helper builds the preview / delete request, defaulting to `'all'`). i18n
  (zh / en): `wsx.move.title` 位置可视化 / Movement; `wsx.move.heatmap` 热力图 / Heatmap; `wsx.move.scatter` 散点图 /
  Scatter; `wsx.move.missing` 暂无可视化 / Not available; `wsx.move.enlarge` 放大 {name} / Enlarge {name};
  `wsx.del.match.title` 删除比赛分析结果 / Delete analysis results; `wsx.del.match.warn` 这会删除该视频的比赛分析结果
  （含回合剪辑），视频文件保留。/ This deletes the match analysis results for this video (rally clips included). The
  video file stays.; `wsx.del.clips.title` 删除生成的剪辑 / Delete generated clips; `wsx.del.clips.warn` 这会删除已生成的
  回合剪辑。/ This deletes the generated rally clips.
  CSS: `.ws-move-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(130px,1fr));gap:8px;}`,
  `.ws-move-fig` (reset button, image `width:100%;aspect-ratio:auto;border-radius:4px`), `.ws-move-missing`,
  `.ws-img-dialog img{max-width:min(90vw,1200px);max-height:80vh}`.
- [ ] **Step 4: Run** `node --test tests/js/*.test.mjs`, all pass.
- [ ] **Step 5: Commit** `feat(kestrel): movement card and scoped delete dialog`.

---

### Task 5: Technique, Clips and Training plan tab widgets

**Files:**
- Create: `static/kestrel/widgets/technique.js`, `static/kestrel/widgets/clips.js`, `static/kestrel/widgets/plan.js`
- Modify: `static/kestrel/core/i18n.js`, `static/kestrel/widgets.css`
- Test: `tests/js/tabs_widgets.test.mjs`

**Interfaces:**
- `techniquePanel({lang, summary /* techniqueSummary or poolTechnique result */, note:string|null}) -> HTMLElement`:
  state ok -> count line `wsx.tech.count` ("{n} strokes analysed · {scored} scored"), a table of `byType`
  (stroke name via `t('stroke.'+id)` or `t('ws.uncertain')` for `unknown`, count, avg `num1` or "—"), recurring
  weaknesses list (`metricLabel` + "×count"); 'empty' -> `wsx.tech.empty`; 'stale' -> `ws.stroke_stale`;
  'doubles' -> `wsx.tech.doubles`.
- `clipsPanel({stem, lang, rallyOk:boolean, api /* {post(url, body)} via core/api.js sendJSON */, onDeleted}) -> {el, destroy}`:
  button "Generate rally clips" (`wsx.clips.generate`, disabled when `!rallyOk` with hint `wsx.clips.no_rallies`),
  busy text `wsx.clips.busy`; POST `/api/clip` `{video: stem, mode: 'highlights', padding: 1.5}`; on
  `{ok:true, clips}` lists links `a[href=c.url][download]` with text `c.name` + `c.size_mb` MB; on error shows
  `div.mv-err` with `wsx.clips.failed` (server `error` text appended as text); "Delete clips" opens
  `openDeleteDialog({stem, lang, scope:'clips', onClose: ({deleted}) => deleted && clear list})`.
- `planPanel({stem, lang, api}) -> {el, destroy}`: loads `GET /api/training-plan/<stem>` (404 -> `wsx.plan.none`,
  other error -> `wsx.plan.failed`); location toggle (`wsx.plan.court` / `wsx.plan.home`, `aria-pressed`
  buttons); Regenerate (`POST` `{weeks: 4}`, busy `wsx.plan.regen_busy`, failure inline `wsx.plan.regen_failed`);
  rows from `planSessions(plan, loc)` grouped by week heading (`wsx.plan.week` "Week {n} · {phase}", phase via
  `wsx.plan.phase.Foundation` / `.Progression`, unknown phase verbatim); each row a button
  (`aria-expanded`) showing name (zh: `detail.name_zh` if present), `sets×reps · freq×/wk · N min`; expanded
  detail: difficulty / equipment chips, description (zh: `description_zh`), lists "How to do it" / "Coaching cues"
  / "Common mistakes" (from `detail.instructions|coaching_cues|common_mistakes` `{zh,en}`), and a video guide
  link (`a[target=_blank][rel=noopener]`, text `wsx.plan.youtube` when the URL is a YouTube watch URL else
  `wsx.plan.guide`); no detail -> `wsx.plan.old`. Empty location -> `wsx.plan.nothing`. All file text set as text.

- [ ] **Step 1: Failing tests** `tests/js/tabs_widgets.test.mjs`:

```js
import test from 'node:test';
import assert from 'node:assert/strict';
import { installEnv, removeEnv, tick, everything, buttonWithText } from './fake_dom.mjs';
import { t } from '../../static/kestrel/core/i18n.js';
import { techniquePanel } from '../../static/kestrel/widgets/technique.js';
import { clipsPanel } from '../../static/kestrel/widgets/clips.js';
import { planPanel } from '../../static/kestrel/widgets/plan.js';

const text = (el) => everything(el).map((e) => (e.nodeValue !== undefined ? e.nodeValue : '')).join(' ');

test('technique panel: ok lists types and weaknesses; doubles and stale say why', () => {
  installEnv();
  try {
    const el = techniquePanel({ lang: 'en', summary: { state: 'ok', count: 68, scored: 45, byType: [{ id: 'clear', count: 19, avg: 56.4 }, { id: 'unknown', count: 9, avg: null }], weaknesses: [{ metric: 'trunk_rotation', count: 43 }] } });
    const s = text(el);
    assert.ok(s.includes('Clear'));
    assert.ok(s.includes('56.4'));
    assert.ok(s.includes(t('metric.trunk_rotation', 'en')));
    assert.ok(text(techniquePanel({ lang: 'en', summary: { state: 'doubles' } })).includes(t('wsx.tech.doubles', 'en')));
    assert.ok(text(techniquePanel({ lang: 'en', summary: { state: 'stale' } })).includes(t('ws.stroke_stale', 'en')));
  } finally { removeEnv(); }
});

test('clips panel: generate posts highlights with padding 1.5 and lists downloads; disabled without rallies', async () => {
  installEnv();
  try {
    const calls = [];
    const api = { post: async (url, body) => { calls.push([url, body]); return { ok: true, clips: [{ name: 'rally_1.mp4', url: '/api/output/s/clips/rally_1.mp4', size_mb: 2.1 }] }; } };
    const p = clipsPanel({ stem: 's', lang: 'en', rallyOk: true, api });
    buttonWithText(p.el, t('wsx.clips.generate', 'en')).dispatch('click');
    await tick();
    assert.deepEqual(calls, [['/api/clip', { video: 's', mode: 'highlights', padding: 1.5 }]]);
    const a = everything(p.el).find((e) => e.tagName === 'A');
    assert.equal(a.getAttribute('href'), '/api/output/s/clips/rally_1.mp4');
    assert.ok(a.hasAttribute('download'));
    const off = clipsPanel({ stem: 's', lang: 'en', rallyOk: false, api });
    assert.equal(buttonWithText(off.el, t('wsx.clips.generate', 'en')).disabled, true);
  } finally { removeEnv(); }
});

test('plan panel: court / home toggle, details expand, file text is text', async () => {
  installEnv();
  try {
    const plan = { weeks: [{ week: 1, phase: 'Foundation', sessions: [
      { name: '<b>Shadow</b>', location: 'court', sets: 3, reps: 10, frequency_per_week: 2, duration_min: 8, detail: { instructions: { en: ['Step one'] }, video_url: 'https://www.youtube.com/watch?v=abcdefg' } },
      { name: 'Band', location: 'home', sets: 3, reps: 15, frequency_per_week: 3 }] }] };
    const api = { get: async () => plan, post: async () => plan };
    const p = planPanel({ stem: 's', lang: 'en', api });
    await tick();
    assert.ok(text(p.el).includes('<b>Shadow</b>'), 'name shown as text');
    assert.ok(!text(p.el).includes('Band'));
    buttonWithText(p.el, t('wsx.plan.home', 'en')).dispatch('click');
    assert.ok(text(p.el).includes('Band'));
    buttonWithText(p.el, t('wsx.plan.court', 'en')).dispatch('click');
    const row = everything(p.el).find((e) => e.tagName === 'BUTTON' && e.getAttribute('aria-expanded') === 'false');
    row.dispatch('click');
    assert.ok(text(p.el).includes('Step one'));
    assert.ok(everything(p.el).some((e) => e.tagName === 'A' && e.getAttribute('href') === 'https://www.youtube.com/watch?v=abcdefg'));
  } finally { removeEnv(); }
});
```

  (`api` is injectable; the default uses `getJSON` / `sendJSON` from `core/api.js` with a 60 s timeout for POST
  `/api/clip`. Read `core/api.js` for the exact helper names and `ApiError` shape; surface `error.body.error`
  text when present.)

- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement** the three widgets and i18n keys (zh / en):
  `wsx.tech.count` 已分析 {n} 次击球 · {scored} 次已评分 / {n} strokes analysed · {scored} scored;
  `wsx.tech.col.type` 类型 / Type; `wsx.tech.col.count` 次数 / Count; `wsx.tech.col.avg` 平均分 / Avg score;
  `wsx.tech.weak` 常见问题 / Recurring weaknesses; `wsx.tech.empty` 暂无技术分析数据 / No technique data yet;
  `wsx.tech.doubles` 双打不提供技术评分 / Technique scoring withheld for doubles;
  `wsx.clips.generate` 生成回合剪辑 / Generate rally clips; `wsx.clips.busy` 生成中… / Generating…;
  `wsx.clips.no_rallies` 没有可剪辑的回合 / No rallies to clip; `wsx.clips.failed` 剪辑生成失败 / Clip generation failed;
  `wsx.clips.delete` 删除剪辑… / Delete clips…; `wsx.clips.hint` 每个回合一个片段（前后各留 1.5 秒）。/ One clip per rally (1.5 s padding).;
  `wsx.clips.size` {mb} MB / {mb} MB;
  `wsx.plan.court` 场上 / On-court; `wsx.plan.home` 居家 / At-home; `wsx.plan.regen` 重新生成 / Regenerate;
  `wsx.plan.regen_busy` 生成中… / Regenerating…; `wsx.plan.regen_failed` 生成失败 / Failed to regenerate;
  `wsx.plan.none` 暂无训练计划（需先完成技术分析）/ No training plan yet (needs technique analysis);
  `wsx.plan.failed` 训练计划加载失败 / Failed to load the training plan; `wsx.plan.nothing` 本视图暂无训练项目 / Nothing scheduled for this view;
  `wsx.plan.week` 第 {n} 周 · {phase} / Week {n} · {phase}; `wsx.plan.phase.Foundation` 基础期 / Foundation;
  `wsx.plan.phase.Progression` 进阶期 / Progression; `wsx.plan.meta` {sets}×{reps} · 每周 {freq} 次 / {sets}×{reps} · {freq}×/wk;
  `wsx.plan.min` {n} 分钟 / {n} min; `wsx.plan.how` 怎么做 / How to do it; `wsx.plan.cues` 要点提示 / Coaching cues;
  `wsx.plan.mistakes` 常见错误 / Common mistakes; `wsx.plan.youtube` 在 YouTube 打开 / Open on YouTube;
  `wsx.plan.guide` 查找视频教学 / Find video guides; `wsx.plan.old` 此计划生成于动作详解之前——点「重新生成」查看。/ This plan predates exercise details: hit Regenerate to see them.;
  `wsx.plan.diff.beginner|intermediate|advanced` 入门/进阶/高级 / Beginner/Intermediate/Advanced (unknown verbatim).
  CSS: `.ws-tech-table`, `.ws-weak`, `.ws-clips-list`, `.ws-plan-week`, `.ws-plan-row`, `.ws-plan-detail` using the
  existing tokens (`--line`, `--surface2`, `--ink2`); reuse `.table`, `.k-chip`, `.ov-btn`, `.ws-chip`.
- [ ] **Step 4: Run** `node --test tests/js/*.test.mjs`, all pass.
- [ ] **Step 5: Commit** `feat(kestrel): technique, clips and training plan panels`.

---

### Task 6: Layout A on the single-video page

**Files:**
- Modify: `static/kestrel/views/workspace.js`, `static/kestrel/views/matches.js` (drop the
  `lib.menu.classic` item at line ~892), `static/kestrel/core/i18n.js` (drop `ws.results_link`, `lib.menu.classic`;
  add `wsx.tab.*`, `wsx.menu*`, `wsx.posture`, `wsx.weak_title`, `wsx.dist.*`, `wsx.tracknet`, `wsx.identity_video`),
  `static/kestrel/widgets.css`
- Test: `tests/js/workspace_mount.test.mjs`, `tests/js/matches_library.test.mjs` (line 165 expectation)

**Interfaces:**
- Consumes: Tasks 1-5 (`createPlayer`, `movementCard`, `techniquePanel`, `clipsPanel`, `planPanel`,
  `openDeleteDialog({scope:'match'})`, `strokeDistribution`, `trackNetNote`, `techniqueSummary`, `weaknessLines`,
  `resolveTab`, `TABS`).
- Produces: `mount(container, ctx)` returns `{destroy(), setQuery(query)}`; `setQuery` switches the tab in place
  (Task 7 extends it for parts).

Page (single video): breadcrumb; header (as today, minus `ws.results_link`; actions: Back, Posture report link
`formatHash('results',{stem},{mode:'posture'})` only when `/api/videos` row for the stem has `has_posture`, a
"..." menu button `button.ws-menu-btn[aria-haspopup=menu]` opening `div.ws-menu[role=menu]` with
`button[role=menuitem]` "Delete analysis results…" (danger) -> `openDeleteDialog({stem, lang, scope:'match',
onClose: ({deleted}) => deleted && ctx.router.navigate('#/matches')})`; Escape / outside click close the menu
and focus returns to the button); top band `div.ws-top` = left `div.ws-main` [player, timeline] + right
`aside.ws-side` [movement card, inspector slot, summary slot]; tab bar `div.ws-tabs[role=tablist]` with four
`button[role=tab][aria-selected][aria-controls]` (arrow keys move between tabs) and one `div[role=tabpanel]`.
Tab bodies: strokes = distribution card (`wsx.dist.title`, bar of group counts with `techLabel` names, TrackNet
note `wsx.tracknet` when `trackNetNote`) + the existing list section; technique = `techniquePanel`; clips =
`clipsPanel` (created on first open, kept); plan = `planPanel` (created on first open, kept). Switching a tab
calls `ctx.router.replace(formatHash('matches',{stem},{...query, tab}))` (default tab omitted from the URL) and
renders the panel without touching the player. `setQuery(q)` with a new tab does the same render without
calling the router.

Inspector: after the metrics, when the stroke's raw report has weaknesses (keep the raw report on the model's
`report` as `report.raw` in `reportModel`, or look it up by contact frame), a list `ul.ws-weak` titled
`wsx.weak_title` with one line per `weaknessLines` item: `metricLabel · measured {m} · ideal {lo}–{hi}`.

Doubles: players panel gets the identity video link (`a[href=endpoints.output(stem, identity_video.file)]
[target=_blank]`, text `wsx.identity_video`) when `players.json` `identity_video.status === 'ok'` (needs the raw
players file: keep it as `state.playersFile`); movement card gets the team / player heatmaps; technique tab
shows `techniqueSummary(null,{doubles:true})`; strokes tab = hits table (no distribution card).

- [ ] **Step 1: Failing tests** `tests/js/workspace_mount.test.mjs`. Patch `api` / `getJSON` the way
  `tests/js/overview_mount.test.mjs` and `matches_library.test.mjs` do (read both first; reuse their fetch /
  api patching). Fixture: rally_segments (2 rallies, signal courtview), strokes.json (schema 2, 3 strokes, groups,
  `shuttle_source: 'tracknet'`), metadata (fps 30), technique payload (one report with `scored`, `weaknesses`),
  insights, match record `{date, upper:{name:'Lee'}, lower:{name:'Chung'}}`, videos `[{name:'s', has_posture:true,
  has_match:true}]`. Tests:
  1. renders `.ws-top` with a `.ws-video-card` (containing `.ws-mute`) and a `.ws-move`; four `[role=tab]`;
     no element with text `Open the full results`; a Posture report link with `#/results/s?mode=posture`.
  2. clicking the Technique tab calls `router.replace('#/matches/s?tab=technique')`, shows the technique panel, and
     `video.src` is unchanged; `setQuery({tab:'clips'})` shows the clips panel without a router call.
  3. the menu's Delete analysis results opens a dialog with `[data-tool=delete]` whose title is
     `t('wsx.del.match.title','en')` (do not confirm).
  4. selecting the stroke with a report shows `.ws-weak` with the metric label.
  5. doubles fixture (metadata `match_format: 'doubles'`, players.json with `identity_video:{status:'ok',file:'detect_s_players.mp4'}`,
     teams / players heatmaps): Technique tab text includes `t('wsx.tech.doubles','en')`; the identity link exists.
  Update `matches_library.test.mjs:165` to expect `['lib.menu.tags', 'lib.menu.posture', 'lib.menu.delete']`.

- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement** in `workspace.js` (restructure `renderAll`; keep timeline / strip / list / inspector /
  summary code; remove both `ws.results_link` anchors; add `menuButton()`, `buildTabs()`, `renderTab()`,
  `distributionCard()`; load `/api/videos` softly in `load()` alongside the other fetches). In `matches.js` remove
  the classic item. i18n (zh / en): `wsx.tab.strokes` 击球 / Strokes; `wsx.tab.technique` 技术 / Technique;
  `wsx.tab.clips` 剪辑 / Clips; `wsx.tab.plan` 训练计划 / Training plan; `wsx.tabs_aria` 比赛详情 / Match details;
  `wsx.menu` 更多 / More; `wsx.menu.delete` 删除分析结果… / Delete analysis results…; `wsx.posture` 姿态报告 /
  Posture report; `wsx.weak_title` 问题 / Weaknesses; `wsx.weak_line` {metric} · 实测 {m} · 理想 {lo}–{hi} /
  {metric} · measured {m} · ideal {lo}–{hi}; `wsx.dist.title` 击球分布 / Stroke distribution; `wsx.dist.total`
  共 {n} 次 / {n} strokes; `wsx.tracknet` 密集羽毛球追踪：TrackNetV3 / Dense shuttle tracking: TrackNetV3;
  `wsx.identity_video` 球员身份视频 / Player identity video. Remove `ws.results_link` and `lib.menu.classic` from
  both languages (grep first that nothing else uses them).
  CSS: `.ws-top{display:flex;flex-wrap:wrap;gap:16px;align-items:flex-start;}` (same flex basis as `.ws-grid`,
  which it replaces), `.ws-tabs`, `.ws-tab[aria-selected=true]` accent underline, `.ws-tabpanel{margin-top:12px}`,
  `.ws-menu` (reuse the `.mv-menu` look from PR #28), `.ws-dist-bar` (a flex row of segments sized by count with
  `tc-<group>` colours used by `.ws-dot`), `.ws-weak`.
- [ ] **Step 4: Run** `node --test tests/js/*.test.mjs`, all pass.
- [ ] **Step 5: Commit** `feat(kestrel): match details layout A with tabs, movement, clips, plan; drop the classic results links`.

---

### Task 7: Split matches: part switcher, URL, Whole match view

**Files:**
- Modify: `static/kestrel/views/workspace.js`, `static/kestrel/core/i18n.js`, `static/kestrel/widgets.css`
- Test: `tests/js/workspace_parts.test.mjs`

**Interfaces:**
- Consumes: `matchParts`, `resolvePartQuery`, `poolDistribution`, `poolTechnique`, `poolRallyCounts`,
  `partTechIndex`, `strokeDistribution`, `techniqueSummary`, `summariseRallyPayload`, `buildWorkspaceModel`,
  `movementCard`, `techniquePanel`.
- `setQuery(query)`: a `part` change re-resolves the view (load the part or show Whole match) without a remount;
  a `tab` change as in Task 6.

Load order: fetch `/api/matches/<stem>`; if it has `part_of`, fetch the primary's record; `info =
matchParts(primaryRecord, primary)`; `r = resolvePartQuery(info, stem, ctx.query)`; if `r.redirect`,
`ctx.router.replace(r.redirect)` and stop (the router remounts or calls setQuery). The header always uses the
primary's record (names in the primary's frame); a selected part's body uses that part's resolved record
(`/api/matches/<part>`, so swapped parts get the right names per side). Switcher: `nav.ws-parts` with buttons
`Whole match` (`wsx.parts.whole`) and `Part N` (`wsx.parts.n`) plus a `k-chip` `wsx.parts.swapped` on swapped
parts; `aria-pressed` on the current; clicking calls `ctx.router.navigate(formatHash('matches',{stem:primary},
{...query, part}))` (Whole match: no `part`).

Whole match view: per part load (soft) rally_segments, strokes.json, metadata, technique, insights. Body:
- `div.ws-part-cards`: one `button.ws-part-card` per part: poster `img` from `/api/videos` `thumb` (blank
  `.mv-thumb` tile without), title `Part N`, duration (`fmtDuration(duration_sec)`), rally line
  (`rallyCountText(summary)`), strokes count (`scope.strokes_count`), tech index (`partTechIndex(insights,
  athleteSideInPart)` with `num1`, else "—", label `wsx.parts.tech`), `wsx.parts.swapped` chip, and when the part
  has no outputs `wsx.parts.not_analysed`. Click -> navigate to `?part=<stem>`.
- `movementCard({items: parts.map(p => ({stem:p.stem, caption: t('wsx.parts.n',{n:p.n})}))})`.
- Tabs: strokes = pooled distribution card + per-part table (`wsx.parts.col.part`, strokes, uncertain);
  technique = `techniquePanel({summary: poolTechnique(per-part summaries), note: t('wsx.parts.pooled')})`;
  clips / plan = `p.kw-note` `wsx.parts.per_video` + one `button` per part ("Part N") that navigates to
  `?part=<stem>&tab=<clips|plan>`.
- Header meta line on Whole match: `wsx.parts.count` ("{n} parts") + pooled rallies (`rallyCountText` of
  `{count, label, signal:null}` from `poolRallyCounts`) + pooled stroke count; when `failedParts` > 0 add
  `wsx.parts.failed_note`. No Posture link, no delete menu on Whole match.
- Part view = the Task 6 page for the part stem, under the same header + switcher. A part with no outputs shows
  the existing not-found card text inside the page body (header and switcher stay).

- [ ] **Step 1: Failing tests** `tests/js/workspace_parts.test.mjs` (same api patching as Task 6). Fixture: `p1`
  record `{date, upper:{name:'Lee'}, lower:{name:'Chung'}, result:{...}, parts:[{stem:'p2', sides_swapped:true},
  {stem:'p3', sides_swapped:false}]}`; `p2` record = resolved with `part_of:'p1'`, `sides_swapped:true`, upper /
  lower exchanged; `p3` has no outputs (all 404). Tests:
  1. mount `p1` with `{}` -> Whole match: three `.ws-part-card`, p3's card has `t('wsx.parts.not_analysed')`, no
     `.ws-video-card`, `.ws-move` has 4 images (p1 and p2; p3 images may 404 -> missing), the pooled distribution
     sums p1 + p2.
  2. mount `p2` (its own stem) -> `router.replace` called once with `formatHash('matches',{stem:'p1'},{part:'p2'})`,
     `router.navigate` not called.
  3. mount `p1` with `{part:'p2'}` -> a `.ws-video-card` whose first source is `/api/output/p2/detect_p2.mp4`; the
     switcher's Part 2 button has `aria-pressed=true` and a swapped chip.
  4. `setQuery({part:'p1'})` on that instance -> the video source switches to p1 and no remount (same root node).
  5. mount `p1` with `{part:'zz'}` -> `router.replace(formatHash('matches',{stem:'p1'},{}))`.
  6. mount `p1` with `{part:'p3'}` -> header and switcher present, body shows the not-analysed text.

- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement.** Keep per-part loading in one function `loadPart(partStem)` that returns the same
  state the single-video path builds today, so the single view and the part view share code; the Whole match
  loader `loadWhole(info)` calls a lighter `loadPartSummary(stem)` per part in parallel. Abort in-flight loads on
  `setQuery` / `destroy` (existing `AbortController` pattern). i18n (zh / en): `wsx.parts.whole` 整场比赛 / Whole
  match; `wsx.parts.n` 第 {n} 部分 / Part {n}; `wsx.parts.aria` 比赛分段 / Match parts; `wsx.parts.swapped` 已换边 /
  Sides swapped; `wsx.parts.tech` 技术指数 / Tech index; `wsx.parts.not_analysed` 尚未分析 / Not analysed yet;
  `wsx.parts.count` {n} 段视频 / {n} parts; `wsx.parts.pooled` 合并所有已分析的分段 / Pooled across the analysed parts;
  `wsx.parts.per_video` 剪辑和训练计划按视频生成，请选择分段：/ Clips and training plans are per video. Pick a part:;
  `wsx.parts.col.part` 分段 / Part; `wsx.parts.col.uncertain` 不确定 / Uncertain; `wsx.parts.failed_note`
  {n} 段回合检测失败，未计入 / {n} part(s) with failed rally detection left out.
  CSS: `.ws-parts` (segmented buttons like `.ws-chip` group), `.ws-part-cards{display:grid;grid-template-columns:
  repeat(auto-fill,minmax(220px,1fr));gap:12px}`, `.ws-part-card` (card look, poster 16:9, focus ring).
- [ ] **Step 4: Run** `node --test tests/js/*.test.mjs`, all pass.
- [ ] **Step 5: Commit** `feat(kestrel): split matches get a part switcher and a whole match view`.

---

### Task 8: Verification, STATE row, PR, merge, deploy

**Files:** `.ai/STATE.md` (Active work row while the PR is open; delete it in the merge-ready commit if merged
in the same session), `.claude/launch.json` (worktree preview entry if missing).

- [ ] **Step 1:** `node --test tests/js/*.test.mjs` - all pass; `../../../.venv/Scripts/python.exe -B -m pytest -q -p no:cacheprovider tests/test_kestrel_js.py tests/test_doubles_kestrel.py --basetemp <scratch>/pt1` - pass.
- [ ] **Step 2:** qa-reviewer pass over the branch diff against the spec; fix findings.
- [ ] **Step 3:** Live check: junction `videos`, `outputs`, `data` from the main checkout into the worktree;
  `preview_start` the 5071 entry; check a single match, the split match `DJI_20261010090046_0010_D` (+ part
  `DJI_20261010090508_0011_D`), its part's own stem link, a doubles match if one exists, volume / mute / M /
  theater / full screen, every tab; generate clips only on a short video and only if the owner's data allows
  (it writes `outputs/<stem>/clips`, existing clips are overwritten by the same names - skip if unsure);
  never confirm a delete. Screenshot proof. Remove the junctions afterwards.
- [ ] **Step 4:** Full pytest in the background: `PYTHONUTF8=1 ../../../.venv/Scripts/python.exe -B -m pytest -q -p no:cacheprovider --basetemp <scratch>/full`.
- [ ] **Step 5:** Commit, push the branch to `origin`, open the PR against `seafood-rice/Good-Badminton` main, bind
  it, read CI. Merge (owner pre-approved) with a squash merge, delete the STATE row in main if needed.
- [ ] **Step 6:** Deploy: check `/api/jobs` on 5050 has no active job; from the main checkout (updated to
  origin/main) run `python scripts/docker_build.py`, then `docker compose -p kestrel up -d --force-recreate app`;
  verify `http://localhost:5050/#/matches/<split primary>` serves the new page.
