// Technique radar: seven axes, the reference band, scored axes joined, unscored axes dashed.
// Opens from the centre with the reveal. Only real data: no "preview full coverage" switch.
import { t } from '../core/i18n.js';
import { local } from '../core/motion.js';
import { radarPoint, radarPolygon, ringPolygon, radarLabel } from '../core/geometry.js';
import * as frame from './frame.js';
import { h, s, setAttr, setText } from './frame.js';

const O = { cx: 160, cy: 150, r: 105, n: 7 };
const f1 = (v) => v.toFixed(1);
const isNum = (v) => typeof v === 'number' && Number.isFinite(v);

const pt = (i, v) => radarPoint(i, v, O).map(f1).join(' ');
const spoke = (i) => `M${O.cx} ${O.cy}L${pt(i, 100)}`;
const loop = (v) => 'M' + Array.from({ length: O.n }, (_, i) => pt(i, v)).join('L') + 'Z';

/**
 * data: the radar view-model {reference, axes:[{id,label,score,count,unscored,below,valueText,readout}], readout, aria}.
 * Returns {el, update(p), focus(id)}.
 */
export function build(data, ctx = {}) {
  const d = data || {};
  const lang = ctx.lang;
  const axes = Array.isArray(d.axes) ? d.axes : [];
  const ref = d.reference || { lo: 70, hi: 100 };
  const card = frame.build({ id: 'radar', title: t('radar.title', lang), sub: t('radar.subtitle', lang), flex: '5 1 380px' }, ctx);
  const slot = typeof ctx.slot === 'number' ? ctx.slot : 0;

  const scored = axes.map((a, i) => (a.unscored ? -1 : i)).filter((i) => i >= 0);
  const unscored = axes.map((a, i) => (a.unscored ? i : -1)).filter((i) => i >= 0);

  const band = s('path', { d: loop(ref.hi) + loop(ref.lo), 'fill-rule': 'evenodd', class: 'kw-radar-band' });
  const focusSpoke = s('path', { d: '', class: 'kw-radar-focus-spoke' });
  const shape = s('polygon', { points: '', class: 'kw-radar-shape' });
  const dotsOk = s('path', { d: '', class: 'kw-radar-dot' });
  const dotsWarnHalo = s('path', { d: '', class: 'kw-radar-dot-halo' });
  const dotsWarn = s('path', { d: '', class: 'kw-radar-dot bad' });
  const focusHalo = s('path', { d: '', class: 'kw-radar-focus-halo' });
  const focusDot = s('path', { d: '', class: 'kw-radar-focus-dot' });

  const labels = axes.map((a, i) => {
    const pos = radarLabel(i, O);
    const val = s('tspan', { dx: 5, class: 'kw-radar-val' }, a.valueText || '—');
    const text = s('text', { x: pos.x, y: pos.y, 'text-anchor': pos.anchor, class: 'kw-radar-label' }, [a.label || a.id, val]);
    return { text, val };
  });

  const svg = s('svg', { viewBox: '0 0 320 300', role: 'img', 'aria-label': d.aria || '', class: 'kw-radar' }, [
    s('polygon', { points: ringPolygon(1, O), class: 'kw-radar-grid' }),
    s('polygon', { points: ringPolygon(0.75, O), class: 'kw-radar-grid' }),
    s('polygon', { points: ringPolygon(0.5, O), class: 'kw-radar-grid' }),
    s('polygon', { points: ringPolygon(0.25, O), class: 'kw-radar-grid' }),
    s('path', { d: scored.map(spoke).join(''), class: 'kw-radar-grid' }),
    s('path', { d: unscored.map(spoke).join(''), class: 'kw-radar-unscored' }),
    band, focusSpoke, shape, dotsOk, dotsWarnHalo, dotsWarn, focusHalo, focusDot,
    ...labels.map((l) => l.text),
  ]);

  const readHead = h('strong', null, '');
  const readBody = h('span', null, '');
  const readout = h('div', { class: 'kw-readout', 'aria-live': 'polite' }, [readHead, ' ', readBody]);

  let hover = null; // axis index under the pointer / keyboard focus
  let lock = null; // axis index pinned by a click or a finding
  let eR = 0;

  const chips = axes.map((a, i) => h('button', {
    type: 'button',
    class: 'kw-axis-chip' + (a.unscored ? ' unscored' : ''),
    'aria-pressed': 'false',
    onmouseenter: () => setHover(i),
    onmouseleave: () => setHover(null),
    onfocus: () => setHover(i),
    onblur: () => setHover(null),
    onclick: () => { lock = lock === i ? null : i; paintFocus(); },
  }, a.label || a.id));
  const chipRow = h('div', { class: 'kw-axis-chips', role: 'group', 'aria-label': t('radar.inspect', lang) }, chips);

  card.body.appendChild(svg);
  card.body.appendChild(chipRow);
  card.body.appendChild(readout);

  function setHover(i) {
    hover = i;
    paintFocus();
  }

  const dots = (idx) => idx.map((i) => 'M' + pt(i, axes[i].score * eR) + 'h0').join('');

  function paintFocus() {
    const f = hover !== null ? hover : lock;
    const fa = f !== null ? axes[f] : null;
    const scoredFocus = fa && !fa.unscored && isNum(fa.score);
    setAttr(focusSpoke, 'd', scoredFocus ? spoke(f) : '');
    const fd = scoredFocus ? 'M' + pt(f, fa.score * eR) + 'h0' : '';
    setAttr(focusDot, 'd', fd);
    setAttr(focusHalo, 'd', fd);
    labels.forEach((l, i) => {
      const a = axes[i];
      setAttr(l.text, 'class', 'kw-radar-label' + (f === i ? ' focus' : a.unscored ? ' unscored' : ''));
      setAttr(l.val, 'class', 'kw-radar-val' + (a.below ? ' bad' : a.unscored ? ' unscored' : ''));
    });
    chips.forEach((c, i) => {
      setAttr(c, 'aria-pressed', lock === i ? 'true' : 'false');
      c.classList.toggle('focus', f === i);
    });
    const r = fa ? fa.readout : d.readout;
    setText(readHead, r ? r.head : '');
    setText(readBody, r ? r.body : '');
  }

  function update(p) {
    card.update(p);
    eR = local(p, slot);
    setAttr(band, 'style', `fill-opacity:${(0.16 * eR).toFixed(3)};stroke-opacity:${(0.55 * eR).toFixed(3)}`);
    setAttr(shape, 'points', radarPolygon(axes, eR, O));
    setAttr(dotsOk, 'd', dots(scored.filter((i) => !axes[i].below)));
    const warn = dots(scored.filter((i) => axes[i].below));
    setAttr(dotsWarn, 'd', warn);
    setAttr(dotsWarnHalo, 'd', warn);
    paintFocus();
  }

  /** Pin an axis by technique id (findings call this through ctx.onFocusAxis). */
  function focus(id) {
    const i = axes.findIndex((a) => a.id === id);
    if (i < 0) return;
    lock = i;
    paintFocus();
  }

  paintFocus();
  return { el: card.el, update, focus };
}
