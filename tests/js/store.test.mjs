import test from 'node:test';
import assert from 'node:assert/strict';
import { createStore } from '../../static/kestrel/core/store.js';

test('get/set merge and notify', () => {
  const s = createStore({ a: 1, b: 2 });
  const seen = [];
  const off = s.subscribe((state, prev) => seen.push([state.a, prev.a]));
  s.set({ a: 5 });
  assert.deepEqual(s.get(), { a: 5, b: 2 });
  assert.deepEqual(seen, [[5, 1]]);
  off();
  s.set({ a: 6 });
  assert.equal(seen.length, 1);
});

test('no notification when nothing changed; set accepts an updater', () => {
  const s = createStore({ n: 1 });
  let calls = 0;
  s.subscribe(() => { calls++; });
  s.set({ n: 1 });
  assert.equal(calls, 0);
  s.set((st) => ({ n: st.n + 1 }));
  assert.equal(s.get().n, 2);
  assert.equal(calls, 1);
});

test('a throwing listener does not stop the others', () => {
  const s = createStore({ n: 0 });
  const orig = console.error;
  console.error = () => {};
  try {
    let ok = 0;
    s.subscribe(() => { throw new Error('boom'); });
    s.subscribe(() => { ok++; });
    s.set({ n: 1 });
    assert.equal(ok, 1);
  } finally { console.error = orig; }
});
