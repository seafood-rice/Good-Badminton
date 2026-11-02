// Shot landing card for the match details page (landing spec 2026-11-02 section 6): a full court drawn as SVG (far
// half on top), one mark per estimated landing coloured by hitter, a hitter filter, and each side's front / mid /
// rear shares. Round = from the shuttle and the receiver, hollow = from the receiver's feet only, square = floor.
// Every string is set as text.
import { t } from '../core/i18n.js';
import { h, s } from './frame.js';
import { COURT_W, COURT_L, SIDES, visibleDots } from '../views/landing_model.js';

const PAD = 1;                 // metres of margin around the court
const SCALE = 10;              // viewBox units per metre
const NET_Y = 6.7;
const SHORT_M = 1.98;          // short service line, from the net
const LONG_DOUBLES_M = 0.76;   // doubles long service line, from the baseline
const SINGLES_M = 0.46;        // singles sideline, inside the doubles one

const px = (m) => +((m + PAD) * SCALE).toFixed(1);
const line = (x1, y1, x2, y2, cls) => s('line', { x1: px(x1), y1: px(y1), x2: px(x2), y2: px(y2), class: cls });

function courtLines() {
  const W = COURT_W;
  const L = COURT_L;
  return [
    s('rect', { x: px(0), y: px(0), width: W * SCALE, height: L * SCALE, class: 'ws-land-court' }),
    line(SINGLES_M, 0, SINGLES_M, L, 'ws-land-line'), line(W - SINGLES_M, 0, W - SINGLES_M, L, 'ws-land-line'),
    line(0, NET_Y - SHORT_M, W, NET_Y - SHORT_M, 'ws-land-line'), line(0, NET_Y + SHORT_M, W, NET_Y + SHORT_M, 'ws-land-line'),
    line(0, LONG_DOUBLES_M, W, LONG_DOUBLES_M, 'ws-land-line'), line(0, L - LONG_DOUBLES_M, W, L - LONG_DOUBLES_M, 'ws-land-line'),
    line(W / 2, 0, W / 2, NET_Y - SHORT_M, 'ws-land-line'), line(W / 2, NET_Y + SHORT_M, W / 2, L, 'ws-land-line'),
    line(-0.3, NET_Y, W + 0.3, NET_Y, 'ws-land-net'),
  ];
}

/** A stroke's name; one without a translation (unknown, uncertain, an old type) reads as its id. */
function strokeLabel(id, lang) {
  if (!id) return '–';
  const key = 'stroke.' + id;
  const text = t(key, lang);
  return text && text !== key ? text : id.replace(/_/g, ' ');
}

function mark(d, lang) {
  const cls = 'ws-land-dot ' + d.hitter + (d.source === 'feet' ? ' hollow' : '');
  const title = s('title', null, t('wsx.land.dot', lang, { stroke: strokeLabel(d.stroke, lang),
    source: t('wsx.land.src.' + d.source, lang) }));
  if (d.source === 'floor') {
    return s('rect', { x: px(d.x) - 3, y: px(d.y) - 3, width: 6, height: 6, class: cls }, [title]);
  }
  return s('circle', { cx: px(d.x), cy: px(d.y), r: 3, class: cls }, [title]);
}

const pct = (v) => Math.round(v * 100) + '%';

/** landingCard({lang, model, names: {upper, lower}}) -> section element. `model` from landingModel (or null while loading). */
export function landingCard({ lang, model, names = {} } = {}) {
  const sec = h('section', { class: 'card hud ws-land', 'aria-label': t('wsx.land.title', lang) });
  sec.appendChild(h('h2', { class: 'card-title' }, [
    t('wsx.land.title', lang), ' ',
    h('span', { class: 'k-chip mid ws-land-est', title: t('wsx.land.estimate_title', lang) }, t('wsx.land.estimate', lang)),
  ]));
  if (!model) {
    sec.appendChild(h('p', { class: 'ws-land-empty' }, t('wsx.land.loading', lang)));
    return sec;
  }
  if (!model.available) {
    sec.appendChild(h('p', { class: 'ws-land-empty' }, t('wsx.land.reason.' + (model.reason || 'no_data'), lang)));
    return sec;
  }
  const nameOf = (side) => names[side] || t('side.' + side, lang);
  let filter = 'both';
  const dotsG = s('g', { class: 'ws-land-dots' });
  const svg = s('svg', {
    viewBox: `0 0 ${(COURT_W + 2 * PAD) * SCALE} ${(COURT_L + 2 * PAD) * SCALE}`, class: 'ws-land-svg', role: 'img',
    'aria-label': '',
  }, [...courtLines(), dotsG]);
  const buttons = ['both', ...SIDES].map((f) => h('button', {
    type: 'button', class: 'ws-land-filter', 'aria-pressed': f === filter ? 'true' : 'false', 'data-filter': f,
    onclick: () => { filter = f; paint(); },
  }, [f === 'both' ? t('wsx.land.both', lang) : nameOf(f)].concat(f === 'both' ? [] : [h('span', { class: 'ws-land-key ' + f, 'aria-hidden': 'true' })])));

  function paint() {
    const shown = visibleDots(model, filter);
    dotsG.textContent = '';
    for (const d of shown) dotsG.appendChild(mark(d, lang));
    svg.setAttribute('aria-label', t('wsx.land.aria', lang, { n: shown.length }));
    for (const b of buttons) b.setAttribute('aria-pressed', b.getAttribute('data-filter') === filter ? 'true' : 'false');
  }

  const bands = h('ul', { class: 'ws-land-bands' }, model.bands.map((b) => h('li', null, [
    h('span', { class: 'ws-land-key ' + b.side, 'aria-hidden': 'true' }),
    h('strong', null, b.name || nameOf(b.side)), ' ',
    b.shares
      ? t('wsx.land.bands', lang, { front: pct(b.shares.front), mid: pct(b.shares.mid), rear: pct(b.shares.rear), n: b.n })
      : t('wsx.land.bands_none', lang),
  ])));
  sec.appendChild(h('div', { class: 'ws-land-filters', role: 'group', 'aria-label': t('wsx.land.filter_aria', lang) }, buttons));
  sec.appendChild(svg);
  sec.appendChild(h('p', { class: 'ws-land-legend mono' }, t('wsx.land.legend', lang)));
  sec.appendChild(bands);
  paint();
  return sec;
}
