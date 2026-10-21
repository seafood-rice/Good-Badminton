// Ambient ground (spec 6.5): a blurred, dimmed photograph behind the whole page (masked so it fades toward
// the bottom; day theme drops it to --photoO). Purely decorative (aria-hidden) and static: the court
// drawing, flight arcs and travelling shuttle that used to sit over it were removed.

export const PHOTOS = Object.freeze({
  aerial: '/static/img/ground/court-aerial-night.jpg',
  racket: '/static/img/ground/court-racket-top.jpg',
});

/** Overview and most pages use the aerial court; the match workspace uses the racket photo (canvas Match). */
export function photoFor(route) {
  return route && route.name === 'matches' && route.params && route.params.stem ? PHOTOS.racket : PHOTOS.aerial;
}

/** Returns {el, update(route)}. Place `el` as the first child of the positioned main column. */
export function createGround() {
  const root = document.createElement('div');
  root.className = 'ground';
  root.setAttribute('aria-hidden', 'true');
  const img = document.createElement('img');
  img.alt = '';
  img.decoding = 'async';
  img.setAttribute('aria-hidden', 'true');
  // A missing photo must not leave a broken-image glyph: hide it.
  img.addEventListener('error', () => { img.hidden = true; });
  img.addEventListener('load', () => { img.hidden = false; });
  root.appendChild(img);

  function update(route) {
    const src = photoFor(route);
    if (img.getAttribute('src') !== src) img.setAttribute('src', src);
    root.classList.toggle('ws', src === PHOTOS.racket);
  }

  return { el: root, update };
}
