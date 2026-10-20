// Tiny observable store: shallow-merge state, notify subscribers only when a value actually changed.

export function createStore(initial = {}) {
  let state = { ...initial };
  const listeners = new Set();

  const get = () => state;

  function set(patch) {
    const next = typeof patch === 'function' ? patch(state) : patch;
    if (!next || typeof next !== 'object') return;
    const keys = Object.keys(next);
    if (!keys.some((k) => !Object.is(state[k], next[k]))) return;
    const prev = state;
    state = { ...state, ...next };
    for (const fn of [...listeners]) {
      try {
        fn(state, prev);
      } catch (e) {
        console.error('store listener failed', e);
      }
    }
  }

  function subscribe(fn) {
    listeners.add(fn);
    return () => listeners.delete(fn);
  }

  return { get, set, subscribe };
}
