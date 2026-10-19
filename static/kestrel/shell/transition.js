// Container transform (spec 6.4, canvas `go()`): a surface-coloured layer grows from the clicked element's
// rect to the full viewport in 800 ms, then the hash changes and the new view reveals from the same surface
// (the layer fades out over 1.3 s). Reduced motion, a missing rect or no DOM => instant navigation.
// The layer is always removed, even when navigation throws or does not happen.

import { prefersReduced } from './dom.js';

export const GROW_MS = 800;
export const REVEAL_MS = 1300;
const EASE = 'cubic-bezier(.7,0,.2,1)';

let busy = false;

function defaultNavigate(hash) {
  if (typeof location !== 'undefined') location.hash = hash;
}

function isReduced(reduced) {
  if (typeof reduced === 'function') return !!reduced();
  if (reduced === undefined || reduced === null) return prefersReduced();
  return !!reduced;
}

function rectOf(anchorEl) {
  try {
    const r = anchorEl && typeof anchorEl.getBoundingClientRect === 'function' ? anchorEl.getBoundingClientRect() : null;
    return r && r.width > 0 && r.height > 0 ? r : null;
  } catch (e) {
    return null;
  }
}

/**
 * Navigate to `hashTarget`, growing a layer out of `anchorEl` first.
 * opts: {reduced: boolean | () => boolean (default: the media query), navigate(hash) (default: location.hash)}.
 * Resolves once navigation has been requested.
 */
export function navigateWithTransform(anchorEl, hashTarget, opts = {}) {
  const navigate = typeof opts.navigate === 'function' ? opts.navigate : defaultNavigate;
  const rect = rectOf(anchorEl);
  const hasDom = typeof document !== 'undefined' && !!document.body;
  if (isReduced(opts.reduced) || !rect || !hasDom || busy) {
    navigate(hashTarget);
    return Promise.resolve();
  }

  busy = true;
  const layer = document.createElement('div');
  layer.className = 'xf-layer';
  layer.setAttribute('aria-hidden', 'true');
  Object.assign(layer.style, {
    left: rect.left + 'px', top: rect.top + 'px', width: rect.width + 'px', height: rect.height + 'px',
  });
  document.body.appendChild(layer);

  let removed = false;
  const remove = () => {
    if (removed) return;
    removed = true;
    layer.remove();
  };
  // Safety net: whatever happens below, the layer is gone shortly after the reveal.
  const safety = setTimeout(() => { remove(); busy = false; }, GROW_MS + REVEAL_MS + 400);

  return new Promise((resolve) => {
    // Commit the start rect, then grow on the next frame (a transition needs a prior style).
    void layer.offsetWidth;
    const grow = () => {
      layer.style.transition = ['left', 'top', 'width', 'height'].map((p) => `${p} ${GROW_MS / 1000}s ${EASE}`)
        .concat(`border-radius ${GROW_MS / 1000}s ${EASE}`).join(',');
      Object.assign(layer.style, { left: '0px', top: '0px', width: '100vw', height: '100vh', borderRadius: '0px' });
    };
    if (typeof requestAnimationFrame === 'function') requestAnimationFrame(grow);
    else grow();

    setTimeout(() => {
      try {
        navigate(hashTarget);
      } catch (e) {
        console.error('navigation failed', e);
      } finally {
        busy = false;
        layer.classList.add('reveal');
        setTimeout(() => {
          clearTimeout(safety);
          remove();
        }, REVEAL_MS);
        resolve();
      }
    }, GROW_MS);
  });
}
