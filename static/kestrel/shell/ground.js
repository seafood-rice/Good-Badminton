// Ambient ground (spec 6.5): a blurred, dimmed photograph behind the whole page (masked so it fades toward
// the bottom; day theme drops it to --photoO) plus a glowing court, three flight arcs and a travelling
// shuttle over the top ~1000 px. Purely decorative (aria-hidden). Reduced motion stops the animation (CSS).

export const PHOTOS = Object.freeze({
  aerial: '/static/img/ground/court-aerial-night.jpg',
  racket: '/static/img/ground/court-racket-top.jpg',
});

/** Overview and most pages use the aerial court; the match workspace uses the racket photo (canvas Match). */
export function photoFor(route) {
  return route && route.name === 'matches' && route.params && route.params.stem ? PHOTOS.racket : PHOTOS.aerial;
}

// Static markup from the canvas (no user data).
const COURT_SVG = `<svg viewBox="0 0 1200 780" preserveAspectRatio="xMidYMin slice" aria-hidden="true" focusable="false">
  <defs>
    <radialGradient id="kGroundGlow" cx="50%" cy="46%" r="52%">
      <stop offset="0" stop-color="#7FC8D6" stop-opacity="0.14"/>
      <stop offset="0.6" stop-color="#7FC8D6" stop-opacity="0.06"/>
      <stop offset="1" stop-color="#7FC8D6" stop-opacity="0"/>
    </radialGradient>
  </defs>
  <ellipse cx="600" cy="400" rx="640" ry="400" fill="url(#kGroundGlow)"/>
  <path d="M300 700 L900 700 L860 330 L340 330 Z" style="fill:var(--cyan);fill-opacity:.07;stroke:none"/>
  <g style="fill:none;stroke:var(--cyan);stroke-width:2;stroke-linejoin:round;opacity:.55">
    <path d="M300 700 L900 700 L780 120 L420 120 Z"/>
    <path d="M345 700 L447 120 M855 700 L753 120 M600 700 L600 120" style="stroke-width:1.6;opacity:.8"/>
    <path d="M321 600 L879 600 M356 430 L844 430 M393 250 L807 250 M414 150 L786 150" style="stroke-width:1.6;opacity:.8"/>
  </g>
  <path d="M352 330 L848 330" style="fill:none;stroke:var(--ink);stroke-width:4;stroke-linecap:round;opacity:.45"/>
  <path d="M352 330 V300 M848 330 V300" style="fill:none;stroke:var(--ink);stroke-width:3;stroke-linecap:round;opacity:.35"/>
  <path class="flight" d="M470 650 C 540 60, 780 60, 850 610" style="fill:none;stroke:var(--cyan);stroke-width:2.4;stroke-linecap:round;opacity:.8"/>
  <path class="flight" d="M720 640 C 640 200, 520 160, 450 520" style="fill:none;stroke:var(--accent);stroke-width:2.4;stroke-linecap:round;opacity:.7;animation-delay:-7s"/>
  <path class="flight" d="M380 600 C 470 340, 700 330, 790 150" style="fill:none;stroke:var(--cyan);stroke-width:1.8;stroke-linecap:round;opacity:.5;animation-delay:-3s"/>
  <circle class="ring" cx="850" cy="610" r="16" style="fill:none;stroke:var(--cyan);stroke-width:2"/>
  <circle class="ring" cx="450" cy="520" r="16" style="fill:none;stroke:var(--accent);stroke-width:2;animation-delay:-1.7s"/>
  <circle class="ring" cx="790" cy="150" r="16" style="fill:none;stroke:var(--cyan);stroke-width:2;animation-delay:-2.6s"/>
  <circle cx="850" cy="610" r="4" style="fill:var(--cyan)"/>
  <circle cx="450" cy="520" r="4" style="fill:var(--accent)"/>
  <circle cx="790" cy="150" r="4" style="fill:var(--cyan)"/>
  <circle class="shuttle" r="5" cx="0" cy="0" style="fill:var(--ink);offset-path:path('M470 650 C 540 60, 780 60, 850 610');filter:drop-shadow(0 0 6px var(--cyan))"/>
</svg>`;

/** Returns {el, update(route)}. Place `el` as the first child of the positioned main column. */
export function createGround() {
  const root = document.createElement('div');
  root.className = 'ground';
  root.setAttribute('aria-hidden', 'true');
  const img = document.createElement('img');
  img.alt = '';
  img.decoding = 'async';
  img.setAttribute('aria-hidden', 'true');
  // A missing photo must not leave a broken-image glyph: hide it and keep the court.
  img.addEventListener('error', () => { img.hidden = true; });
  img.addEventListener('load', () => { img.hidden = false; });
  root.appendChild(img);
  root.insertAdjacentHTML('beforeend', COURT_SVG);

  function update(route) {
    const src = photoFor(route);
    if (img.getAttribute('src') !== src) img.setAttribute('src', src);
    root.classList.toggle('ws', src === PHOTOS.racket);
  }

  return { el: root, update };
}
