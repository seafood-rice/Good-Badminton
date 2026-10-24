// Movement card for the match details page: the match heatmap and scatter images of one video, or of each linked
// part (captioned "Part N"), plus a doubles run's team and player heatmaps. A missing image says so in place; a
// figure opens the image large in the shared dialog. Every string is set as text.
import { t } from '../core/i18n.js';
import { endpoints } from '../core/api.js';
import { h } from './frame.js';
import { openImageDialog } from './videotools.js';

const HEATMAP = 'position_visualizations/heatmaps/match_heatmap.png';
const SCATTER = 'position_visualizations/scatter_plots/match_scatter.png';

const str = (v) => (typeof v === 'string' ? v : '');
const list = (v) => (Array.isArray(v) ? v : []);
/** A players.json file name that stays inside the run's output folder (no "..", no backslash, not absolute), else ''. */
export const safeFile =(v) => { const f = str(v); return f && !f.includes('..') && !f.includes('\\') && !f.startsWith('/') ? f : ''; };

/**
 * One image figure: a button that enlarges it; a load error swaps the image for a "not available" note and the
 * button goes inert (disabled, no "enlarge" name, no zoom cursor).
 */
function figure({ lang, src, name }) {
  let broken = false;
  const img = h('img', { src, alt: name, loading: 'lazy' });
  const btn = h('button', {
    type: 'button', class: 'ws-move-fig', 'aria-label': t('wsx.move.enlarge', lang, { name }),
    onclick: () => { if (!broken) openImageDialog({ lang, src, title: name }); },
  }, [img, h('span', { class: 'ws-move-cap mono' }, name)]);
  img.addEventListener('error', () => {
    broken = true;
    btn.disabled = true;
    btn.removeAttribute('aria-label');
    btn.classList.add('missing');
    btn.replaceChildren(h('div', { class: 'ws-move-missing' }, t('wsx.move.missing', lang)), h('span', { class: 'ws-move-cap mono' }, name));
  });
  return btn;
}

/**
 * movementCard({lang, items: [{stem, caption|null}], doubles: {teams: [{id, file}], players: [{id, label, file}]}|null}).
 * Doubles files are read from the first item's output folder.
 */
export function movementCard({ lang, items, doubles = null } = {}) {
  const sec = h('section', { class: 'card hud ws-move', 'aria-label': t('wsx.move.title', lang) });
  sec.appendChild(h('h2', { class: 'card-title' }, t('wsx.move.title', lang)));
  const grid = h('div', { class: 'ws-move-grid' });
  const entries = list(items).filter((it) => it && str(it.stem));
  for (const it of entries) {
    const pre = str(it.caption) ? str(it.caption) + ' · ' : '';
    grid.appendChild(figure({ lang, src: endpoints.output(it.stem, HEATMAP), name: pre + t('wsx.move.heatmap', lang) }));
    grid.appendChild(figure({ lang, src: endpoints.output(it.stem, SCATTER), name: pre + t('wsx.move.scatter', lang) }));
  }
  const base = entries.length ? entries[0].stem : '';
  if (doubles && base) {
    for (const tm of list(doubles.teams)) {
      if (!tm || !safeFile(tm.file)) continue;
      grid.appendChild(figure({ lang, src: endpoints.output(base, tm.file), name: t('dbl.team', lang, { n: tm.id }) }));
    }
    for (const p of list(doubles.players)) {
      if (!p || !safeFile(p.file)) continue;
      grid.appendChild(figure({ lang, src: endpoints.output(base, p.file), name: str(p.label) || str(p.id) }));
    }
  }
  sec.appendChild(grid);
  return sec;
}
