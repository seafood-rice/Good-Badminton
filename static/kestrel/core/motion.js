// Motion helpers: easing, staggered local progress and the reveal clock.
// DOM-free: requestAnimationFrame and time are injected (defaults resolved lazily at start()).

function clamp01(x) {
  return x < 0 ? 0 : x > 1 ? 1 : x;
}

/** Quart ease-out, input clamped to 0-1. */
export function ease(x) {
  return 1 - Math.pow(1 - clamp01(x), 4);
}

/** Local progress of widget `i` for global progress `p`: each widget starts 0.06 later and spans 0.45. */
export function local(p, i) {
  return ease(clamp01((p - i * 0.06) / 0.45));
}

function defaultRaf() {
  if (typeof requestAnimationFrame === 'function') {
    return { raf: (fn) => requestAnimationFrame(fn), caf: (id) => cancelAnimationFrame(id) };
  }
  return { raf: (fn) => setTimeout(() => fn(Date.now()), 16), caf: (id) => clearTimeout(id) };
}

function defaultNow() {
  return typeof performance !== 'undefined' && typeof performance.now === 'function'
    ? performance.now()
    : Date.now();
}

/**
 * One progress clock per view entrance. `onFrame(p)` receives linear progress 0..1.
 * `start()` cancels any running clock first; stale frames from a cancelled run never reach `onFrame`.
 * With `reduced` (boolean or function) `start()` calls `onFrame(1)` once, synchronously.
 */
export function createClock({ duration = 2400, reduced = false, onFrame, raf, caf, now } = {}) {
  let generation = 0;
  let handle = null;
  let running = false;
  let cancelFrame = null;

  function cancel() {
    generation += 1;
    if (handle !== null && cancelFrame) cancelFrame(handle);
    handle = null;
    running = false;
  }

  function start() {
    cancel();
    const emit = typeof onFrame === 'function' ? onFrame : () => {};
    const isReduced = typeof reduced === 'function' ? reduced() : reduced;
    if (isReduced) {
      emit(1);
      return;
    }
    const fallback = raf ? null : defaultRaf();
    const schedule = raf || fallback.raf;
    cancelFrame = caf || (fallback ? fallback.caf : null);
    const clockNow = now || defaultNow;
    const mine = generation + 1;
    generation = mine;
    running = true;
    const t0 = clockNow();

    const step = () => {
      if (mine !== generation) return;
      const p = duration > 0 ? clamp01((clockNow() - t0) / duration) : 1;
      if (p < 1) {
        handle = schedule(step);
      } else {
        handle = null;
        running = false;
      }
      emit(p);
    };

    emit(0);
    if (mine !== generation) return;
    handle = schedule(step);
  }

  return { start, cancel, isRunning: () => running };
}
