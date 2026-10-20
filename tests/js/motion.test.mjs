import test from 'node:test';
import assert from 'node:assert/strict';
import { ease, local, createClock } from '../../static/kestrel/core/motion.js';

function fake() {
  let t = 1000;
  let nextId = 1;
  const queue = new Map();
  const cancelled = [];
  return {
    now: () => t,
    raf: (fn) => { const id = nextId++; queue.set(id, fn); return id; },
    caf: (id) => { cancelled.push(id); queue.delete(id); },
    cancelled,
    pending: () => queue.size,
    advance(ms) { t += ms; const fns = [...queue.values()]; queue.clear(); fns.forEach((fn) => fn(t)); },
    // Fire callbacks that were taken out of the queue (a frame already in flight).
    flushStale(fnList) { fnList.forEach((fn) => fn(t)); },
    takeQueue() { const fns = [...queue.values()]; queue.clear(); return fns; },
  };
}

test('ease is a quart ease-out', () => {
  assert.equal(ease(0), 0);
  assert.equal(ease(1), 1);
  assert.ok(Math.abs(ease(0.5) - (1 - Math.pow(0.5, 4))) < 1e-12);
  assert.ok(ease(0.2) > 0.2);
  assert.equal(ease(2), 1);
  assert.equal(ease(-1), 0);
});

test('local staggers widget i by 0.06 and spans 0.45', () => {
  assert.equal(local(0, 0), 0);
  assert.equal(local(0.45, 0), 1);
  assert.equal(local(0.06, 1), 0);
  assert.ok(Math.abs(local(0.06 + 0.225, 1) - ease(0.5)) < 1e-12);
  assert.equal(local(1, 6), 1);
  assert.ok(local(0.3, 5) < local(0.3, 0));
});

test('clock reaches p = 1 at the duration', () => {
  const f = fake();
  const frames = [];
  const clock = createClock({ duration: 2400, onFrame: (p) => frames.push(p), raf: f.raf, caf: f.caf, now: f.now });
  clock.start();
  assert.equal(frames[0], 0);
  f.advance(1200);
  assert.ok(Math.abs(frames[frames.length - 1] - 0.5) < 1e-9);
  f.advance(1200);
  assert.equal(frames[frames.length - 1], 1);
  assert.equal(f.pending(), 0);
  const n = frames.length;
  f.advance(500);
  assert.equal(frames.length, n);
});

test('overshooting the duration still ends exactly at 1, once', () => {
  const f = fake();
  const frames = [];
  const clock = createClock({ duration: 1000, onFrame: (p) => frames.push(p), raf: f.raf, caf: f.caf, now: f.now });
  clock.start();
  f.advance(5000);
  assert.equal(frames.filter((p) => p === 1).length, 1);
  assert.ok(frames.every((p) => p >= 0 && p <= 1));
});

test('a second start cancels the first: no frames from the old run', () => {
  const f = fake();
  const frames = [];
  const clock = createClock({ duration: 2400, onFrame: (p) => frames.push(p), raf: f.raf, caf: f.caf, now: f.now });
  clock.start();
  f.advance(1800); // first run is at p = 0.75
  const stale = f.takeQueue(); // a frame the first run had already scheduled
  assert.equal(stale.length, 1);
  clock.start(); // second run begins
  const afterStart = frames.length;
  assert.equal(frames[afterStart - 1], 0);
  f.flushStale(stale); // the old frame fires late
  assert.equal(frames.length, afterStart, 'stale frame produced output');
  f.advance(600);
  assert.ok(Math.abs(frames[frames.length - 1] - 0.25) < 1e-9);
  f.advance(1800);
  assert.equal(frames[frames.length - 1], 1);
});

test('a second start also cancels the pending raf handle', () => {
  const f = fake();
  const clock = createClock({ duration: 2400, onFrame: () => {}, raf: f.raf, caf: f.caf, now: f.now });
  clock.start();
  assert.equal(f.pending(), 1);
  clock.start();
  assert.ok(f.cancelled.length >= 1);
  assert.equal(f.pending(), 1);
});

test('cancel stops the clock; start after cancel works', () => {
  const f = fake();
  const frames = [];
  const clock = createClock({ duration: 1000, onFrame: (p) => frames.push(p), raf: f.raf, caf: f.caf, now: f.now });
  clock.start();
  f.advance(400);
  clock.cancel();
  const n = frames.length;
  f.advance(1000);
  assert.equal(frames.length, n);
  clock.cancel(); // idempotent
  clock.start();
  f.advance(1000);
  assert.equal(frames[frames.length - 1], 1);
});

test('reduced motion yields exactly one frame with p = 1, synchronously', () => {
  const f = fake();
  const frames = [];
  const clock = createClock({ duration: 2400, reduced: true, onFrame: (p) => frames.push(p), raf: f.raf, caf: f.caf, now: f.now });
  clock.start();
  assert.deepEqual(frames, [1]);
  assert.equal(f.pending(), 0);
  f.advance(3000);
  assert.deepEqual(frames, [1]);
});

test('reduced may be a function evaluated at start', () => {
  const f = fake();
  const frames = [];
  let reduced = false;
  const clock = createClock({ duration: 1000, reduced: () => reduced, onFrame: (p) => frames.push(p), raf: f.raf, caf: f.caf, now: f.now });
  reduced = true;
  clock.start();
  assert.deepEqual(frames, [1]);
});

test('works without caf by ignoring stale frames', () => {
  const f = fake();
  const frames = [];
  const clock = createClock({ duration: 1000, onFrame: (p) => frames.push(p), raf: f.raf, now: f.now });
  clock.start();
  const stale = f.takeQueue();
  clock.cancel();
  f.flushStale(stale);
  assert.deepEqual(frames, [0]);
});
