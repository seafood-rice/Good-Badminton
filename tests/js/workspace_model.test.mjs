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
