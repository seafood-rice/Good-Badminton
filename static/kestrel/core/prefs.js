// localStorage wrapper that tolerates missing or throwing storage (private mode, quota, SecurityError).
// The storage is injected (an object, or a function returning one) so the module imports cleanly under Node.

export const PREF_KEYS = Object.freeze({
  lang: 'kestrel_lang',
  theme: 'kestrel_theme',
  layout: 'kestrel_layout_v1',
  rail: 'kestrel_rail',
  matchesView: 'kestrel_matches_view',
});

/** Legacy values: 'light' -> day, 'dark' -> night. Anything unknown is night. */
export function normTheme(v) {
  return v === 'day' || v === 'light' ? 'day' : 'night';
}

/** The string stored in `kestrel_theme` and set as `data-theme` (legacy-compatible): night -> 'dark', day -> 'light'. */
export function legacyTheme(v) {
  return normTheme(v) === 'day' ? 'light' : 'dark';
}

/** The app default is Chinese (as in the legacy UI); only 'en' selects English. */
export function normLang(v) {
  return v === 'en' ? 'en' : 'zh';
}

export function createPrefs(storage) {
  const resolve = () => {
    try {
      const s = typeof storage === 'function' ? storage() : storage;
      return s || null;
    } catch (e) {
      return null;
    }
  };

  function get(key, fallback = null) {
    try {
      const s = resolve();
      if (!s) return fallback;
      const v = s.getItem(key);
      return v === null || v === undefined ? fallback : v;
    } catch (e) {
      return fallback;
    }
  }

  function set(key, value) {
    try {
      const s = resolve();
      if (!s) return false;
      s.setItem(key, String(value));
      return true;
    } catch (e) {
      return false;
    }
  }

  function remove(key) {
    try {
      const s = resolve();
      if (!s) return false;
      s.removeItem(key);
      return true;
    } catch (e) {
      return false;
    }
  }

  function getJSON(key, fallback = null) {
    const raw = get(key, null);
    if (raw === null) return fallback;
    try {
      return JSON.parse(raw);
    } catch (e) {
      return fallback;
    }
  }

  function setJSON(key, value) {
    try {
      return set(key, JSON.stringify(value));
    } catch (e) {
      return false;
    }
  }

  return { get, set, remove, getJSON, setJSON };
}

function browserStorage() {
  // Reading `localStorage` itself can throw (blocked cookies); callers swallow that.
  return typeof localStorage !== 'undefined' ? localStorage : null;
}

/** Shared instance over the browser's localStorage, resolved lazily on each call. */
export const prefs = createPrefs(browserStorage);
