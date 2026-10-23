// The merged Matches page's pure model (spec 2026-10-24 sections 3-4): match rows joined with the /api/videos list,
// video-only rows, filters, tag counts, open targets and tag validation.
import test from 'node:test';
import assert from 'node:assert/strict';
import {
  videoStatus, libraryRows, rowTags, tagLabel, filterRows, sortRows, tagCounts, isFiltered, openTarget,
  activeStemsOf, tagError, normTag, STATUS_FILTERS, EMPTY_FILTER,
} from '../../static/kestrel/views/library_model.js';

const vid = (name, extra = {}) => ({
  name, filename: name + '.mp4', status: 'new', has_match: false, has_posture: false, has_annotations: false,
  date: '2026-09-01', duration_sec: 60, thumb: '/api/output/' + encodeURIComponent(name) + '/thumb.jpg', tags: [], ...extra,
});

const OV = {
  matches: [
    { stem: 'p1', date: '2026-10-01', date_source: 'record', side: 'lower', athlete: 'Axelsen', opponent: 'Lee', result: 'W', status: 'analysed', tech_index: 70,
      parts: [{ stem: 'p1', side: 'lower', status: 'analysed' }, { stem: 'p2', side: 'upper', status: 'analysed' }] },
    { stem: 'solo', date: '2026-09-20', status: 'analysed', side: 'lower', parts: [] },
  ],
};
const VIDS = [
  vid('p1', { status: 'analyzed', has_match: true, tags: ['final'] }),
  vid('p2', { status: 'analyzed', has_match: true }),
  vid('solo', { status: 'analyzed', has_match: true, has_posture: true, tags: ['smash', 'final'] }),
  vid('drill1', { status: 'analyzed', has_posture: true, date: '2026-09-25' }),
  vid('fresh', { date: '2026-10-05' }),
  vid('court', { status: 'court_set', has_annotations: true, date: '2026-08-01' }),
  vid('undated', { date: null }),
];

test('videoStatus: an active job wins, then match, posture, court set, new', () => {
  assert.equal(videoStatus(vid('a', { has_match: true }), new Set(['a'])), 'processing');
  assert.equal(videoStatus(vid('a', { has_match: true, has_posture: true })), 'analysed');
  assert.equal(videoStatus(vid('a', { has_posture: true })), 'posture');
  assert.equal(videoStatus(vid('a', { has_annotations: true })), 'court_set');
  assert.equal(videoStatus(vid('a')), 'new');
  assert.equal(videoStatus(null), 'new');
});

test('libraryRows: split parts fold into their match; every other video becomes a video row', () => {
  const { rows, videosOk } = libraryRows({ overview: OV, videos: VIDS });
  assert.equal(videosOk, true);
  assert.deepEqual(rows.map((r) => [r.kind, r.stem]), [
    ['video', 'fresh'], ['match', 'p1'], ['video', 'drill1'], ['match', 'solo'], ['video', 'court'], ['video', 'undated'],
  ]);
  const p1 = rows.find((r) => r.stem === 'p1');
  assert.equal(p1.video.name, 'p1');
  assert.deepEqual(p1.parts.map((p) => p.video && p.video.name), ['p1', 'p2']);
  assert.ok(!rows.some((r) => r.stem === 'p2'), 'a part never shows on its own');
  const drill = rows.find((r) => r.stem === 'drill1');
  assert.equal(drill.status, 'posture');
  assert.equal(drill.dateApprox, true);
  assert.equal(drill.result, null);
  assert.deepEqual(drill.sets, []);
  assert.equal(rows.find((r) => r.stem === 'court').status, 'court_set');
});

test('libraryRows: scoped shows only the scoped match rows (other videos may be other athletes’ matches)', () => {
  const { rows } = libraryRows({ overview: { matches: [OV.matches[1]] }, videos: VIDS, scoped: true });
  assert.deepEqual(rows.map((r) => r.stem), ['solo']);
  assert.equal(rows[0].video.name, 'solo', 'the video still supplies the thumbnail and tags');
});

test('libraryRows: a failed video list gives match rows only; junk input is tolerated', () => {
  const { rows, videosOk } = libraryRows({ overview: OV, videos: null });
  assert.equal(videosOk, false);
  assert.deepEqual(rows.map((r) => r.stem), ['p1', 'solo']);
  assert.equal(rows[0].video, null);
  assert.deepEqual(libraryRows({ overview: null, videos: [null, 3, {}, { name: '' }] }).rows, []);
});

test('libraryRows: a video with an active job reads as processing', () => {
  const { rows } = libraryRows({ overview: { matches: [] }, videos: [vid('fresh')], activeStems: new Set(['fresh']) });
  assert.equal(rows[0].status, 'processing');
});

test('activeStemsOf: pending / running / re-encoding jobs, by video file stem', () => {
  const got = activeStemsOf([
    { status: 'running', video_name: 'a b.mp4' }, { status: 'pending', video_name: 'c.MOV' },
    { status: 'done', video_name: 'd.mp4' }, { status: 'reencoding', video_name: 'e' }, null,
  ]);
  assert.deepEqual([...got].sort(), ['a b', 'c', 'e']);
  assert.deepEqual([...activeStemsOf({ jobs: [{ status: 'running', video_name: 'x.mp4' }] })], ['x']);
  assert.equal(activeStemsOf(undefined).size, 0);
});

test('rowTags and tagLabel: system tags first, localised; user tags verbatim', () => {
  const { rows } = libraryRows({ overview: OV, videos: VIDS });
  const by = Object.fromEntries(rows.map((r) => [r.stem, r]));
  assert.deepEqual(rowTags(by.solo), ['match', 'drill', 'smash', 'final']);
  assert.deepEqual(rowTags(by.drill1), ['drill']);
  assert.deepEqual(rowTags(by.fresh), []);
  assert.deepEqual(rowTags(libraryRows({ overview: OV, videos: null }).rows[0]), ['match']);
  assert.equal(tagLabel('match', 'en'), 'Match');
  assert.equal(tagLabel('drill', 'zh'), '训练');
  assert.equal(tagLabel('final', 'zh'), 'final');
});

test('filterRows: search, status groups and tags (AND)', () => {
  const { rows } = libraryRows({ overview: OV, videos: VIDS });
  const stems = (f) => filterRows(rows, { ...EMPTY_FILTER, ...f }, 'en').map((r) => r.stem);
  assert.deepEqual(stems({}), rows.map((r) => r.stem));
  assert.deepEqual(stems({ q: 'axel' }), ['p1'], 'athlete name');
  assert.deepEqual(stems({ q: 'DRILL' }), ['drill1', 'solo'], 'stem and the Drill tag label');
  assert.deepEqual(filterRows(rows, { ...EMPTY_FILTER, q: '训练' }, 'zh').map((r) => r.stem), ['drill1', 'solo']);
  assert.deepEqual(stems({ status: 'analysed' }), ['p1', 'solo']);
  assert.deepEqual(stems({ status: 'not_analysed' }), ['fresh', 'court', 'undated']);
  assert.deepEqual(stems({ status: 'posture' }), ['drill1']);
  assert.deepEqual(stems({ tags: ['final'] }), ['p1', 'solo']);
  assert.deepEqual(stems({ tags: ['final', 'smash'] }), ['solo']);
  assert.deepEqual(stems({ tags: ['final'], status: 'posture' }), []);
  assert.deepEqual(STATUS_FILTERS, ['all', 'analysed', 'needs_review', 'processing', 'not_analysed', 'posture']);
});

test('filterRows: needs_review counts as analysed and has its own group', () => {
  const rows = libraryRows({ overview: { matches: [{ stem: 'r', status: 'analysed', needs_review: true }] }, videos: [] }).rows;
  assert.equal(filterRows(rows, { ...EMPTY_FILTER, status: 'analysed' }, 'en').length, 1);
  assert.equal(filterRows(rows, { ...EMPTY_FILTER, status: 'needs_review' }, 'en').length, 1);
  assert.equal(filterRows(rows, { ...EMPTY_FILTER, status: 'processing' }, 'en').length, 0);
});

test('sortRows: date keeps newest first (undated last); name sorts by stem', () => {
  const { rows } = libraryRows({ overview: OV, videos: VIDS });
  assert.deepEqual(sortRows(rows, 'name').map((r) => r.stem), ['court', 'drill1', 'fresh', 'p1', 'solo', 'undated']);
  assert.deepEqual(sortRows([...rows].reverse(), 'date').map((r) => r.stem), rows.map((r) => r.stem));
});

test('tagCounts: system tags first, counts given the other filters, active flags', () => {
  const { rows } = libraryRows({ overview: OV, videos: VIDS });
  const got = tagCounts(rows, { ...EMPTY_FILTER, tags: ['final'] }, 'en');
  assert.deepEqual(got.map((c) => [c.tag, c.count, c.active, c.sys]), [
    ['match', 2, false, true], ['drill', 1, false, true], ['final', 2, true, false], ['smash', 1, false, false],
  ]);
  assert.equal(got[0].label, 'Match');
  // with a status filter the counts shrink
  const post = tagCounts(rows, { ...EMPTY_FILTER, status: 'posture' }, 'en');
  assert.equal(post.find((c) => c.tag === 'drill').count, 1);
  assert.equal(post.find((c) => c.tag === 'final').count, 0);
});

test('isFiltered: any search, status or tag', () => {
  assert.equal(isFiltered(EMPTY_FILTER), false);
  assert.equal(isFiltered({ ...EMPTY_FILTER, q: '  ' }), false);
  assert.equal(isFiltered({ ...EMPTY_FILTER, q: 'x' }), true);
  assert.equal(isFiltered({ ...EMPTY_FILTER, status: 'posture' }), true);
  assert.equal(isFiltered({ ...EMPTY_FILTER, tags: ['a'] }), true);
});

test('openTarget: workspace, posture report, pipeline or the wizard; stems percent-encoded', () => {
  assert.equal(openTarget({ kind: 'match', stem: 'a b' }), '#/matches/a%20b');
  assert.equal(openTarget({ kind: 'video', stem: 'x', status: 'analysed' }), '#/matches/x');
  assert.equal(openTarget({ kind: 'video', stem: 'x"y', status: 'posture' }), '#/results/x%22y?mode=posture');
  assert.equal(openTarget({ kind: 'video', stem: 'x', status: 'processing' }), '#/pipeline');
  assert.equal(openTarget({ kind: 'video', stem: 'x', status: 'new' }), '#/new');
  assert.equal(openTarget({ kind: 'video', stem: 'x', status: 'court_set' }), '#/new');
});

test('tagError / normTag mirror library.tags.normalise', () => {
  assert.equal(normTag('  Smash '), 'smash');
  assert.equal(tagError(''), 'lib.tagerr.empty');
  assert.equal(tagError('   '), 'lib.tagerr.empty');
  assert.equal(tagError('x'.repeat(41)), 'lib.tagerr.long');
  assert.equal(tagError('x'.repeat(40)), null);
  assert.equal(tagError('...'), 'lib.tagerr.dots');
  assert.equal(tagError('Match'), 'lib.tagerr.reserved');
  assert.equal(tagError('a/b'), 'lib.tagerr.slash');
  assert.equal(tagError('a\\b'), 'lib.tagerr.slash');
  assert.equal(tagError('a\u0007b'), 'lib.tagerr.control');
  assert.equal(tagError('net kill'), null);
});
