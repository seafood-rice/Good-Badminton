// Pure parts of the Overview view and its widgets: view-models, layout helpers, geometry additions.
// The DOM-building code is thin over these and is verified in the browser.
import test from 'node:test';
import assert from 'node:assert/strict';
import {
  viewModel, LENS_TILES, WIDGET_IDS, normalizeLayout, moveWidget, toggleWidget,
} from '../../static/kestrel/views/overview.js';
import { jobsModel } from '../../static/kestrel/widgets/pipeline.js';
import { trendScale, trendLine, niceDomain, trendPath } from '../../static/kestrel/core/geometry.js';
import { reasonText } from '../../static/kestrel/core/i18n.js';

const ok = (value, coverage = null) => ({ status: 'ok', value, coverage, reason: null });
const partial = (value, code, params = {}, coverage = null) => ({ status: 'partial', value, coverage, reason: { code, params } });
const unavailable = (code, params = {}) => ({ status: 'unavailable', value: null, coverage: null, reason: { code, params } });

function grid(fill) {
  const g = [];
  for (let r = 0; r < 11; r++) {
    const row = [];
    for (let c = 0; c < 10; c++) row.push(fill(r, c));
    g.push(row);
  }
  return g;
}

function overviewFixture() {
  const pos = grid(() => 0);
  pos[0][4] = 0.5; // densest cell at the NET (row 0), column 4
  pos[8][4] = 0.3;
  pos[9][5] = 0.2;
  const pull = grid(() => 0);
  pull[10][0] = 0.6;
  pull[1][9] = 0.4;
  return {
    scope: {
      athlete: { id: 'a1', name: 'J. Tanaka', squad_id: 's1' }, squad: null, range: '30d', stems: 5, units: 5,
      date_from: '2026-09-19', date_to: '2026-10-18', generated_at: '2026-10-19T10:00:00Z',
    },
    kpis: {
      technique_index: partial({ value: 74.6, delta: 2.3, scored: 4 }, 'unscored_technique', { names: ['net', 'lift', 'drive'] }, { n: 4, of: 7 }),
      match_win_rate: ok({ wins: 3, played: 5, rate: 0.6 }, { n: 5, of: 5 }),
      avg_rally: partial({ seconds: 8.4, shots: 7.8, signal: 'swing' }, 'signal_swing'),
      rally_win_rate: unavailable('no_point_outcome'),
      recovery: partial({ median_sec: 1.3 }, 'few_hits', {}, { n: 6, of: 9 }),
      net_share: ok({ share: 0.31 }),
      pull_rear: ok({ share: 0.42 }),
      unforced_errors: unavailable('no_point_outcome'),
      strokes: ok({ classified: 4100, total: 4812 }),
    },
    radar: {
      reference: { lo: 70, hi: 100, source: 'literature' },
      axes: [
        { id: 'smash', count: 212, score: 82, status: 'ok' },
        { id: 'clear', count: 402, score: 74, status: 'ok' },
        { id: 'drop', count: 188, score: 66, status: 'ok' },
        { id: 'net', count: 530, score: null, status: 'unavailable' },
        { id: 'lift', count: 345, score: null, status: 'unavailable' },
        { id: 'drive', count: 276, score: null, status: 'unavailable' },
        { id: 'serve', count: 412, score: 80, status: 'ok' },
      ],
    },
    trend: {
      points: [
        { date: '2026-09-20', stem: 's1', index: 72.3 },
        { date: '2026-09-28', stem: 's2', index: 73.1 },
        { date: '2026-10-05', stem: 's3', index: null },
        { date: '2026-10-11', stem: 's4', index: 74.0 },
        { date: '2026-10-18', stem: 's5', index: 74.6 },
      ],
      benchmark: unavailable('no_benchmark'),
    },
    movement: { position: ok({ grid: pos, samples: 900, distance_m: 120, mean_speed_mps: 1.2, base: { x: 3.05, y: 10.05 } }), pull: partial({ grid: pull, front: 0.4, mid: 0, back: 0.6, left: 0.6, right: 0.4, n: 5 }, 'few_hits') },
    matches: [
      { stem: 'Dji 20261018 0004 D', date: '2026-10-18', date_source: 'filename', side: 'lower', athlete: { id: 'a1', name: 'J. Tanaka' }, opponent: { id: 'a2', name: 'R. Haldane' }, result: 'W', sets: '21-17, 19-21, 21-15', tech_index: 76.2, status: 'analysed', needs_review: false },
      { stem: 'm2', date: '2026-10-11', date_source: 'record', side: 'upper', athlete: 'J. Tanaka', opponent: 'S. Ito', result: 'L', sets: null, tech_index: 72.04, status: 'analysed', needs_review: true },
      { stem: 'm3', date: '2026-10-05', date_source: 'mtime', side: 'lower', athlete: null, opponent: null, result: null, sets: null, tech_index: null, status: 'processing', needs_review: false },
    ],
    findings: [
      { code: 'technique_below_reference', severity: 'watch', params: { technique: 'drop', score: 66, lo: 70 } },
      { code: 'technique_strongest', severity: 'good', params: { technique: 'smash', score: 82 } },
      { code: 'index_trend', severity: 'info', params: { delta: 2.3, range: '30d' } },
      { code: 'techniques_unscored', severity: 'info', params: { names: ['net', 'lift', 'drive'] } },
      { code: 'rally_estimated', severity: 'info', params: { signal: 'swing' } },
    ],
  };
}

// Every user-visible string in a model: no unfilled {placeholder}, no raw i18n key.
function collectStrings(v, out = [], path = '') {
  if (typeof v === 'string') out.push([path, v]);
  else if (Array.isArray(v)) v.forEach((x, i) => collectStrings(x, out, `${path}[${i}]`));
  else if (v && typeof v === 'object') for (const [k, x] of Object.entries(v)) collectStrings(x, out, path ? `${path}.${k}` : k);
  return out;
}
const RAW_KEY = /^(tile|tag|reason|finding|radar|trend|heat|matches|job|stage|lens|status|range|unit|delta|gap|layout|sync|overview|pipeline|empty|findings|technique)\.[a-z0-9_.]+$/;
// Fields that carry data identifiers rather than text.
const NON_TEXT = new Set(['id', 'kind', 'status', 'stem', 'date', 'tone', 'severity', 'axis', 'href', 'mode', 'dir', 'result', 'lens', 'code']);

function assertStringsResolve(model, lang) {
  for (const [path, s] of collectStrings(model)) {
    const leaf = path.split('.').pop().replace(/\[\d+\]$/, '');
    if (NON_TEXT.has(leaf)) continue;
    assert.ok(!/[{}]/.test(s), `${lang} ${path}: unfilled placeholder in "${s}"`);
    assert.ok(!RAW_KEY.test(s), `${lang} ${path}: raw key "${s}"`);
  }
}

test('LENS_TILES lists the four tiles of each lens in order', () => {
  assert.deepEqual(LENS_TILES.overview, ['technique_index', 'match_win_rate', 'avg_rally', 'rally_win_rate']);
  assert.deepEqual(LENS_TILES.technique, ['strongest', 'weakest_scored', 'inside_reference', 'techniques_scored']);
  assert.deepEqual(LENS_TILES.tactics, ['recovery', 'net_share', 'pull_rear', 'unforced_errors']);
});

test('overview lens: tile order, kinds, and the gap tile carries its reason', () => {
  const vm = viewModel(overviewFixture(), 'overview', 'en');
  assert.equal(vm.empty, false);
  assert.deepEqual(vm.tiles.map((t) => t.id), ['technique_index', 'match_win_rate', 'avg_rally', 'rally_win_rate']);
  assert.deepEqual(vm.tiles.map((t) => t.kind), ['ring', 'ring', 'number', 'gap']);
  const gap = vm.tiles[3];
  assert.equal(gap.reason, reasonText({ code: 'no_point_outcome' }, 'en'));
  assert.ok(gap.reason.length > 10);
  const idx = vm.tiles[0];
  assert.equal(idx.value, 74.6);
  assert.equal(idx.decimals, 1);
  assert.deepEqual([idx.ring.lo, idx.ring.hi], [70, 100]);
  assert.equal(idx.ring.pct, 74.6);
  assert.equal(idx.delta.dir, 'up');
  assert.equal(idx.delta.tone, 'good');
  assert.match(idx.delta.text, /\+2\.3/);
  const win = vm.tiles[1];
  assert.equal(win.value, 60);
  assert.equal(win.ring.pct, 60);
  assert.match(win.unit, /3 of 5/);
});

test('technique lens: tiles come from the scored radar axes', () => {
  const vm = viewModel(overviewFixture(), 'technique', 'en');
  assert.deepEqual(vm.tiles.map((t) => t.id), ['strongest', 'weakest_scored', 'inside_reference', 'techniques_scored']);
  const [strong, weak, inside, scored] = vm.tiles;
  assert.equal(strong.text, 'Smash');
  assert.equal(strong.kind, 'yardstick');
  assert.equal(strong.yard.score, 82);
  assert.equal(weak.text, 'Drop');
  assert.equal(weak.delta.tone, 'bad');
  assert.equal(inside.value, 3); // smash 82, clear 74, serve 80 inside 70-100; drop 66 is under
  assert.match(inside.suffix, /4/);
  assert.equal(scored.value, 4);
  assert.equal(scored.kind, 'ring');
  assert.match(scored.delta.text, /3 need ranges/);
});

test('technique lens with nothing scored degrades to gap tiles, never zeros', () => {
  const ov = overviewFixture();
  ov.radar.axes = ov.radar.axes.map((a) => ({ ...a, score: null, status: 'unavailable' }));
  ov.kpis.technique_index = unavailable('no_technique');
  const vm = viewModel(ov, 'technique', 'en');
  assert.equal(vm.tiles[0].kind, 'gap');
  assert.equal(vm.tiles[1].kind, 'gap');
  assert.equal(vm.tiles[2].kind, 'gap');
  assert.equal(vm.tiles[0].reason, reasonText({ code: 'no_technique' }, 'en'));
  assert.equal(vm.tiles[3].value, 0); // "0 / 7 scored" is a true count, not a missing value
});

test('tactics lens: tile order, shares as percentages, last tile is a gap', () => {
  const vm = viewModel(overviewFixture(), 'tactics', 'en');
  assert.deepEqual(vm.tiles.map((t) => t.id), ['recovery', 'net_share', 'pull_rear', 'unforced_errors']);
  assert.equal(vm.tiles[3].kind, 'gap');
  assert.ok(vm.tiles[3].reason);
  assert.equal(vm.tiles[1].value, 31);
  assert.equal(vm.tiles[2].value, 42);
  assert.equal(vm.tiles[0].value, 1.3);
  assert.equal(vm.tiles[0].tag, 'ESTIMATE');
  assert.match(vm.tiles[0].note, /6 of 9/);
});

test('an unknown lens falls back to the overview lens', () => {
  const vm = viewModel(overviewFixture(), 'nope', 'en');
  assert.equal(vm.lens, 'overview');
  assert.equal(vm.tiles[0].id, 'technique_index');
});

test('partial insights carry a tag', () => {
  const vm = viewModel(overviewFixture(), 'overview', 'en');
  assert.equal(vm.tiles[0].tag, '4 OF 7');
  assert.match(vm.tiles[2].tag, /EST\./);
  assert.equal(vm.tiles[1].tag, '');
  const zh = viewModel(overviewFixture(), 'overview', 'zh');
  assert.match(zh.tiles[0].tag, /4/);
  const ov = overviewFixture();
  ov.kpis.avg_rally = partial({ seconds: 9, shots: null, signal: 'degraded' }, 'signal_degraded');
  const vm2 = viewModel(ov, 'overview', 'en');
  assert.equal(vm2.tiles[2].tag, 'WINDOWS');
  assert.equal(vm2.tiles[2].value, 9);
  assert.equal(vm2.tiles[2].unit, 's');
});

test('radar: unscored axes are flagged with their counts; reference is 70-100', () => {
  const { radar } = viewModel(overviewFixture(), 'overview', 'en');
  assert.deepEqual(radar.reference, { lo: 70, hi: 100 });
  assert.equal(radar.axes.length, 7);
  const byId = Object.fromEntries(radar.axes.map((a) => [a.id, a]));
  for (const id of ['net', 'lift', 'drive']) {
    assert.equal(byId[id].unscored, true, id);
    assert.equal(byId[id].score, null);
  }
  assert.equal(byId.net.count, 530);
  assert.equal(byId.smash.unscored, false);
  assert.equal(byId.drop.below, true);
  assert.equal(byId.smash.below, false);
  assert.equal(radar.scored, 4);
  assert.match(radar.readout.head, /4 of 7/);
  assert.match(byId.net.readout.body, /530/);
  assert.match(byId.drop.readout.body, /4 pts under/);
  assert.match(radar.aria, /not scored/);
});

test('radar: a missing radar block gives seven unscored axes', () => {
  const ov = overviewFixture();
  delete ov.radar;
  const { radar } = viewModel(ov, 'overview', 'en');
  assert.equal(radar.axes.length, 7);
  assert.ok(radar.axes.every((a) => a.unscored && a.count === 0));
  assert.deepEqual(radar.reference, { lo: 70, hi: 100 });
});

test('trend: reference band comes from the radar; benchmark null when unavailable', () => {
  const ov = overviewFixture();
  const { trend } = viewModel(ov, 'overview', 'en');
  assert.deepEqual(trend.reference, { lo: 70, hi: 100 });
  assert.equal(trend.benchmark, null);
  assert.equal(trend.benchmarkReason, reasonText({ code: 'no_benchmark' }, 'en'));
  assert.equal(trend.points.length, 4); // the null-index session is dropped
  assert.equal(trend.last, 74.6);
  assert.equal(trend.empty, false);
  assert.ok(trend.domain.min <= 70 && trend.domain.max >= 74.6);
  assert.equal(trend.domain.ticks.length, 6);
  ov.trend.benchmark = ok({ index: 71.2 });
  const t2 = viewModel(ov, 'overview', 'en').trend;
  assert.equal(t2.benchmark, 71.2);
  assert.match(t2.aria, /71\.2/);
  ov.trend.points = ov.trend.points.slice(0, 1);
  assert.equal(viewModel(ov, 'overview', 'en').trend.empty, true);
});

test('findings: text, severity glyph and the radar axis to focus', () => {
  const { findings } = viewModel(overviewFixture(), 'overview', 'en');
  assert.equal(findings.items.length, 5);
  assert.match(findings.items[0].title, /Drop scores 66/);
  assert.equal(findings.items[0].axis, 'drop');
  assert.equal(findings.items[0].severity, 'watch');
  assert.equal(findings.items[1].axis, 'smash');
  assert.equal(findings.items[2].axis, null);
  assert.match(findings.count, /5 findings/);
});

test('court: row 0 is at the net in both modes; densest readout in metres', () => {
  const { court } = viewModel(overviewFixture(), 'overview', 'en');
  const pos = court.position;
  assert.equal(pos.available, true);
  assert.equal(pos.cells.length, 110);
  assert.equal(pos.cells[4].row, 0);
  assert.equal(pos.cells[4].share, 0.5);
  assert.equal(pos.densest.row, 0);
  assert.equal(pos.densest.col, 4);
  assert.equal(pos.densest.x, '2.7'); // (4 + 0.5) * 0.61
  assert.equal(pos.densest.y, '0.3'); // (0 + 0.5) * 6.7 / 11: next to the net
  assert.equal(pos.densest.pct, '50.0');
  assert.match(pos.readout, /densest 50\.0%/);
  assert.match(pos.cells[4].readout, /y 0\.3 m/);
  const pull = court.pull;
  assert.equal(pull.densest.row, 10);
  assert.match(pull.densest.y, /^6\.4$/);
  assert.equal(pull.tag, 'ESTIMATE');
});

test('court: an unavailable mode keeps its reason', () => {
  const ov = overviewFixture();
  ov.movement.pull = unavailable('no_hits');
  const { court } = viewModel(ov, 'overview', 'en');
  assert.equal(court.pull.available, false);
  assert.equal(court.pull.reason, reasonText({ code: 'no_hits' }, 'en'));
  assert.equal(court.pull.cells.length, 110);
  assert.ok(court.pull.cells.every((c) => c.alpha === 0));
});

test('matches: result chips, opponent fallback and status tones', () => {
  const { matches } = viewModel(overviewFixture(), 'overview', 'en');
  assert.equal(matches.rows.length, 3);
  const [a, b, c] = matches.rows;
  assert.equal(a.stem, 'Dji 20261018 0004 D');
  assert.equal(a.label, 'vs. R. Haldane');
  assert.equal(a.result, 'W');
  assert.equal(a.resultText, 'W');
  assert.equal(a.tech, '76.2');
  assert.equal(a.date, '18 Oct');
  assert.equal(a.statusTone, 'good');
  assert.equal(b.label, 'vs. S. Ito');
  assert.equal(b.tech, '72.0');
  assert.equal(b.statusText, 'Needs review');
  assert.equal(b.statusTone, 'mid');
  assert.equal(c.label, 'm3');
  assert.equal(c.result, null);
  assert.equal(c.resultText, '+ result');
  assert.equal(c.statusText, 'Processing');
});

test('empty library (stems 0) and missing data yield empty: true', () => {
  const ov = overviewFixture();
  ov.scope.stems = 0;
  assert.equal(viewModel(ov, 'overview', 'en').empty, true);
  assert.equal(viewModel(null, 'overview', 'en').empty, true);
  assert.equal(viewModel({}, 'technique', 'zh').empty, true);
  const vm = viewModel(null, 'overview', 'en');
  assert.equal(vm.emptyKind, 'library');
  ov.scope.athlete = { id: 'a9', name: 'Nobody' };
  assert.equal(viewModel(ov, 'overview', 'en').emptyKind, 'scope');
});

test('header: athlete name, summary line and processing count', () => {
  const { header } = viewModel(overviewFixture(), 'overview', 'en');
  assert.equal(header.name, 'J. Tanaka');
  assert.match(header.summary, /5 matches/);
  assert.match(header.summary, /4,812 strokes/);
  assert.match(header.processing, /\+1 processing/);
  const ov = overviewFixture();
  ov.scope.athlete = null;
  assert.equal(viewModel(ov, 'overview', 'en').header.name, 'Whole library');
  ov.scope.squad = { id: 's1', name: 'Senior men' };
  assert.equal(viewModel(ov, 'overview', 'en').header.name, 'Senior men');
});

test('every tile and finding string resolves in zh and en, in every lens', () => {
  const variants = [overviewFixture()];
  const sparse = overviewFixture();
  for (const k of Object.keys(sparse.kpis)) sparse.kpis[k] = unavailable('no_data');
  sparse.radar.axes = sparse.radar.axes.map((a) => ({ ...a, score: null }));
  sparse.trend = { points: [], benchmark: unavailable('no_benchmark') };
  sparse.movement = {};
  sparse.findings = [{ code: 'brand_new', severity: 'info', params: {} }];
  variants.push(sparse);
  for (const ov of variants) {
    for (const lang of ['zh', 'en']) {
      for (const lens of ['overview', 'technique', 'tactics']) {
        const vm = viewModel(ov, lens, lang);
        assertStringsResolve(vm, lang);
        for (const tile of vm.tiles) {
          assert.ok(tile.label && tile.label.length > 0, `${lang} ${tile.id} label`);
          if (tile.kind === 'gap') assert.ok(tile.reason.length > 0, `${lang} ${tile.id} reason`);
          if (lang === 'zh') assert.match(tile.label, /[一-鿿]/, `zh ${tile.id} label`);
        }
        for (const f of vm.findings.items) assert.ok(f.title && f.detail, `${lang} finding`);
      }
    }
  }
});

test('layout: normalise, move and toggle widgets (kestrel_layout_v1 shape)', () => {
  assert.deepEqual(WIDGET_IDS, ['radar', 'trend', 'findings', 'court', 'matches', 'pipeline']);
  const def = normalizeLayout(null);
  assert.deepEqual(def, { order: WIDGET_IDS.slice(), hidden: [] });
  // Unknown ids dropped, missing ids appended, duplicates removed, bad hidden entries ignored.
  const n = normalizeLayout({ order: ['trend', 'bogus', 'trend', 'radar'], hidden: ['matches', 'nope', 7] });
  assert.deepEqual(n.order, ['trend', 'radar', 'findings', 'court', 'matches', 'pipeline']);
  assert.deepEqual(n.hidden, ['matches']);
  assert.deepEqual(normalizeLayout('garbage'), def);
  const up = moveWidget(def, 'trend', -1);
  assert.deepEqual(up.order.slice(0, 2), ['trend', 'radar']);
  assert.deepEqual(moveWidget(def, 'radar', -1), def); // already first
  assert.deepEqual(moveWidget(def, 'pipeline', 1), def); // already last
  assert.deepEqual(def.order, WIDGET_IDS); // inputs are not mutated
  const hid = toggleWidget(def, 'court');
  assert.deepEqual(hid.hidden, ['court']);
  assert.deepEqual(toggleWidget(hid, 'court').hidden, []);
});

test('jobsModel: running and queued counts, stage labels and clamped percentages', () => {
  const jobs = [
    { id: 'j1', video: 'Dji 0004.mp4', kind: 'match', status: 'running', stage: 'strokes', progress: 64, message: 'x', updated: 3 },
    { id: 'j2', video: 'b.mp4', kind: 'posture', status: 'pending', stage: null, progress: 0, message: '', updated: 2 },
    { id: 'j3', video: 'c.mp4', kind: 'match', status: 'completed', stage: 'done', progress: 100, message: '', updated: 1 },
    { id: 'j4', video: 'd.mp4', kind: 'match', status: 'detached', stage: 'some_new_stage', progress: 140, message: '', updated: 0 },
  ];
  const m = jobsModel(jobs, 'en');
  assert.equal(m.running, 2);
  assert.equal(m.queued, 1);
  assert.equal(m.active, true);
  assert.match(m.sub, /2 running · 1 queued/);
  assert.equal(m.rows[0].title, 'Stroke detection');
  assert.equal(m.rows[0].state, 'Running 64%');
  assert.equal(m.rows[0].pct, 64);
  assert.equal(m.rows[1].state, 'Queued');
  assert.equal(m.rows[2].state, 'Done');
  assert.equal(m.rows[2].tone, 'good');
  assert.equal(m.rows[3].pct, 100);
  assert.equal(m.rows[3].title, 'Match analysis'); // unknown stage falls back to the job kind
  const empty = jobsModel(null, 'zh');
  assert.equal(empty.rows.length, 0);
  assert.equal(empty.active, false);
  assert.ok(empty.emptyText.length > 0);
  assertStringsResolve(m, 'en');
  assertStringsResolve(jobsModel(jobs, 'zh'), 'zh');
});

test('geometry: trendScale/trendLine use an asymmetric plot box (canvas: 40..628 x 16..200)', () => {
  const box = { x0: 40, x1: 628, y0: 16, y1: 200, yMin: 60, yMax: 85 };
  const s = trendScale(12, box);
  assert.equal(s.x(0), 40);
  assert.equal(s.x(11), 628);
  assert.equal(s.y(85), 16);
  assert.equal(s.y(60), 200);
  assert.ok(Math.abs(s.y(72.5) - 108) < 1e-9);
  assert.equal(s.y(200), 16); // clamped into the box
  assert.equal(s.y(-5), 200);
  assert.equal(trendScale(1, box).x(0), 334); // a single point sits in the middle
  const line = trendLine([60, null, 85], box);
  assert.equal(line.d, 'M40.0 200.0L628.0 16.0');
  assert.deepEqual(line.pts.map((p) => p.i), [0, 2]);
  assert.equal(trendLine([], box).d, '');
  // Objects with `index` work like trendPath's series values.
  assert.equal(trendLine([{ index: 60 }, { index: 85 }], box).d, 'M40.0 200.0L628.0 16.0');
  // The existing symmetric trendPath is unchanged.
  assert.equal(trendPath([0, 10], { w: 100, h: 50, pad: 5 }), 'M5.0 45.0L95.0 5.0');
});

test('geometry: niceDomain gives six step-aligned ticks inside 0-100', () => {
  const d = niceDomain([68.9, 73.9, 70]);
  assert.deepEqual(d, { min: 60, max: 85, ticks: [85, 80, 75, 70, 65, 60] });
  const top = niceDomain([95, 99]);
  assert.equal(top.max, 100);
  assert.equal(top.ticks.length, 6);
  assert.ok(top.min <= 95);
  const bottom = niceDomain([1, 3]);
  assert.equal(bottom.min, 0);
  assert.ok(bottom.max >= 20);
  const wide = niceDomain([10, 95]);
  assert.ok(wide.min <= 10 && wide.max >= 95);
  assert.equal(wide.ticks.length, 6);
  assert.equal(wide.ticks[0] - wide.ticks[1], wide.ticks[1] - wide.ticks[2]);
  assert.deepEqual(niceDomain([]), { min: 60, max: 85, ticks: [85, 80, 75, 70, 65, 60] });
});
