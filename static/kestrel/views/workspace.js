// Match details page (#/matches/<stem>, spec 2026-10-24 sections 4 and 6), layout A: on top the annotated video
// with the rally timeline and stroke strip, beside it the movement card, the stroke inspector (doubles: the players
// panel) and the rally summary; below, the Strokes | Technique | Clips | Training plan tabs. The header carries
// Back, the Posture report link (when the video has a posture run) and a "..." menu (Delete analysis results). A
// split match (spec section 5) is always shown under its primary's stem with a part switcher (Whole match | Part N):
// `?part=<stem>` shows that part as the page above, no `part` shows the Whole match view (part cards, movement per
// part, pooled strokes and technique). The pure logic (buildWorkspaceModel and the text helpers) lives in
// workspace_model.js and is re-exported here; mount() is the DOM layer over it. Honesty rules: there is no point
// outcome, so nothing is marked won or lost; swing segments are "segments (estimated)", degraded ones "windows";
// uncertain strokes are muted and labelled so; opponent strokes are shown but never scored.
import { t, TECHNIQUE_IDS } from '../core/i18n.js';
import { fmtDate, fmtNumber, fmtDuration } from '../core/format.js';
import { createClock, local } from '../core/motion.js';
import { formatHash } from '../core/router.js';
import { api, getJSON, endpoints, errorText } from '../core/api.js';
import { h, hasDOM, revealStyle, setText, setAttr, approxMark } from '../widgets/frame.js';
import { swatch, unvalidatedBadge } from '../widgets/doubles.js';
import { createPlayer } from '../widgets/player.js';
import { movementCard, safeFile } from '../widgets/movement.js';
import { techniquePanel } from '../widgets/technique.js';
import { clipsPanel } from '../widgets/clips.js';
import { planPanel } from '../widgets/plan.js';
import { openDeleteDialog } from '../widgets/videotools.js';
import { doublesWorkspaceModel, doublesVideoFiles, hitsInWindow } from './doubles_model.js';
import {
  RALLY_PAD_SEC, JOIN_FRAMES, STROKE_PAGE, summariseRallyPayload, buildWorkspaceModel, matchDate, matchDateApprox,
  listScore, rallyInput, rallyCountText, capItems, readReduced, resolveFps, soft, nameFor, unitKey, signalNote,
  techLabel, strokeName, techCode, metricLabel, clockText, num1, isNum, obj, SIDES,
  TABS, resolveTab, strokeDistribution, trackNetNote, techniqueSummary, weaknessLines,
  matchParts, resolvePartQuery, poolDistribution, poolTechnique, poolRallyCounts, partTechIndex,
} from './workspace_model.js';

export {
  RALLY_PAD_SEC, JOIN_FRAMES, STROKE_PAGE, summariseRallyPayload, buildWorkspaceModel, matchDate, matchDateApprox,
  listScore, rallyInput, rallyCountText, capItems, readReduced,
};

const LONG = { timeout: 60000 };
const SETTLE_MS = 2600;

// ---------- DOM ----------

/**
 * Mount the match details page for ctx.params.stem. Returns {destroy(), setQuery(query)}: setQuery follows a new
 * query of the same route in place: a tab change without reloading the video, a `part` change (split matches) by
 * loading that part or the Whole match under the same header and switcher.
 */
export function mount(container, ctx = {}) {
  const noop = { destroy() {}, setQuery() {} };
  if (!container || !hasDOM()) return noop;
  const stem = String(obj(ctx.params).stem || '');
  const lang = ctx.lang === 'en' ? 'en' : 'zh';
  const store = ctx.store && typeof ctx.store.get === 'function' ? ctx.store : null;
  const S = () => (store ? obj(store.get()) : {});

  const state = {
    destroyed: false,
    // The route's query (kept so a tab change rewrites the hash with the rest of it) and the open tab.
    query: { ...obj(ctx.query) },
    tab: resolveTab(ctx.query),
    // The stem whose data is shown (the route's stem for a single video, the selected part's for a split match).
    stem,
    // Split matches: `info` = matchParts() of the primary's record (null until the record is read), `view` =
    // 'single' | 'whole' | 'part', `partStem` the selected part, `head` = the primary's {record, names,
    // athleteSide} the header always shows, `headInsights` the primary's sidecar (date fallback), `whole` the
    // per-part summaries of the Whole match view (kept for the visit), `waiting` = a redirect is pending.
    info: null,
    split: false,
    view: 'single',
    partStem: null,
    head: null,
    headInsights: null,
    whole: null,
    waiting: false,
    // A part change started from the page (switcher, card): the part it asked for ('' = Whole match), whose
    // switcher button gets focus when that part is the next one shown. null = none; any next view clears it.
    focusPart: null,
    // That stem's /api/videos row (has_posture, thumb, ...) or null; `videos` is the whole list (null: failed).
    videos: null,
    video: null,
    strokesDoc: null,
    tech: null,
    playersFile: null,
    model: null,
    record: {},
    athleteSide: null,
    names: { upper: null, lower: null },
    insights: null,
    unit: null, // rally id, or 'all'
    sel: null, // stroke i
    filter: 'all',
    stripLimit: STROKE_PAGE,
    listLimit: STROKE_PAGE,
    // Doubles runs (spec 2026-10-20 section 5): the doubles model, the selected hit (its i) and the hit caps.
    dbl: null,
    hitSel: null,
    hitStripLimit: STROKE_PAGE,
    hitListLimit: STROKE_PAGE,
  };
  const refs = {};
  // Tab bodies, built on first open and kept (the clips and plan panels hold their own state and requests).
  let panels = {};
  let menu = null;
  // The /api/matches soft() results read so far, by stem (a stem's record is fetched once per visit).
  const recs = {};
  let ctl = null;
  let sections = [];
  let settleTimer = null;

  const root = h('div', { class: 'page ws', 'data-view': 'workspace' });
  container.appendChild(root);
  const clock = createClock({
    reduced: () => readReduced(ctx),
    onFrame: (p) => sections.forEach((el, i) => revealStyle(el, local(p, i * 1.5), { dy: 10 })),
  });

  const sideName = (side) => state.names[side] || t('ws.side.' + side, lang);

  function unitTitle(r) {
    return t(unitKey(state.model.summary), lang, { n: r.n });
  }

  function unitStrokes() {
    const m = state.model;
    if (state.unit === 'all' || state.unit === null) return m.strokes;
    return m.strokes.filter((s) => s.rallyId === state.unit);
  }

  function currentUnit() {
    return state.unit === 'all' ? null : state.model.rallies.find((r) => r.id === state.unit) || null;
  }

  function currentStroke() {
    return state.sel === null ? null : state.model.strokes.find((s) => s.i === state.sel) || null;
  }

  // ----- doubles helpers -----
  function unitHits() {
    if (!state.dbl) return [];
    return hitsInWindow(state.dbl.whoHit.items, currentUnit());
  }

  function currentHit() {
    return state.dbl && state.hitSel !== null ? state.dbl.whoHit.items.find((x) => x.i === state.hitSel) || null : null;
  }

  function hitPlayerText(hit) {
    if (!hit || !hit.hitterId) return t('dbl.ws.unnamed', lang);
    const p = state.dbl.players.find((x) => x.id === hit.hitterId);
    return p ? p.label : hit.hitterId;
  }

  /** The doubles title: "A / B vs. C / D" (athlete's team first), else the identity list, else the stem. */
  function doublesTitleText() {
    return (state.dbl && state.dbl.title.text) || stem;
  }

  // ----- teardown, loading / error / not found -----
  /**
   * Release what one rendered view holds before another replaces it: the open menu (and its document listener),
   * the kept tab panels (their requests), the player (its media connection and document listeners) and the reveal.
   */
  function teardown() {
    closeMenu(false);
    destroyPanels();
    if (refs.player) refs.player.destroy();
    Object.assign(refs, { player: null, video: null, tags: null, sig: null, tabPanel: null, tabBtns: null });
    clock.cancel();
    sections = [];
  }

  /** A loading / not found / error card. `retry` reloads (default: the whole page). */
  function statusCard(kind, err, { title = 'ws.not_found.title', s = stem, retry = () => load() } = {}) {
    const card = h('div', { class: 'card ov-state', role: kind === 'loading' ? 'status' : 'alert', 'aria-busy': kind === 'loading' ? 'true' : null });
    if (kind === 'loading') {
      card.appendChild(h('div', { class: 'k-label' }, t('state.loading', lang)));
      card.appendChild(h('div', { class: 'ov-skel' }));
    } else if (kind === 'notfound') {
      card.appendChild(h('div', { class: 'ov-empty-title' }, t(title, lang)));
      card.appendChild(h('div', { class: 'ov-state-body' }, t('ws.not_found.body', lang, { stem: s })));
      card.appendChild(h('a', { href: '#/matches', class: 'ov-btn small' }, t('nav.matches', lang)));
    } else {
      card.appendChild(h('div', { class: 'k-label' }, t('error.load.title', lang)));
      card.appendChild(h('div', { class: 'ov-state-body' }, errorText(err, lang)));
      card.appendChild(h('button', { type: 'button', class: 'ov-btn small', onclick: retry }, t('btn.retry', lang)));
    }
    return card;
  }

  /** The whole page as one state card (before there is a header to show). */
  function renderStatus(kind, err) {
    teardown();
    refs.framed = false;
    root.textContent = '';
    root.appendChild(breadcrumb(stem));
    root.appendChild(statusCard(kind, err));
  }

  /**
   * Split matches: a state card in the page body under the kept header and switcher (a part loading, a part with
   * no outputs, a part that failed to load).
   */
  function renderBodyStatus(kind, err) {
    teardown();
    if (!refs.framed) frame();
    swapNode('crumb', breadcrumb(crumbText()));
    swapNode('head', buildHead());
    paintSwitcher();
    const s = state.partStem || stem;
    refs.body.textContent = '';
    refs.body.appendChild(statusCard(kind, err, { title: 'wsx.parts.not_analysed', s, retry: () => showView() }));
  }

  // ----- page frame: breadcrumb, header, switcher (split matches), body, live region -----
  /** Build the frame once; the views swap the breadcrumb and header nodes and refill the body. */
  function frame() {
    root.textContent = '';
    refs.crumb = h('nav', { class: 'ws-crumbs' });
    refs.head = h('div', { class: 'ws-head' });
    refs.switcher = state.split ? buildSwitcher() : null;
    refs.body = h('div', { class: 'ws-body' });
    // One persistent polite live region: a short line per selection, not the whole inspector.
    refs.live = h('div', { class: 'ws-sr', role: 'status', 'aria-live': 'polite' });
    [refs.crumb, refs.head, refs.switcher, refs.body, refs.live].forEach((n) => { if (n) root.appendChild(n); });
    refs.framed = true;
  }

  function swapNode(key, node) {
    const old = refs[key];
    if (old && old.parentNode === root) root.replaceChild(node, old);
    refs[key] = node;
  }

  // ----- split matches: the part switcher -----
  /** The selected part ('' = Whole match). */
  const currentPart = () => (state.view === 'whole' ? '' : state.partStem || '');

  /** Open a part ('' = Whole match) through the router (a history entry); the page follows via setQuery. */
  function goPart(partStem, tab) {
    const q = { ...state.query };
    delete q.part;
    if (tab !== undefined) delete q.tab;
    if (partStem) q.part = partStem;
    if (tab !== undefined && tab !== 'strokes') q.tab = tab;
    if (partStem === currentPart() && (tab === undefined || tab === state.tab)) return;
    const hash = formatHash('matches', { stem: state.info.primary }, q);
    // The body is rebuilt for the new part: without this, focus would drop to <body>.
    state.focusPart = partStem || '';
    if (ctx.router && typeof ctx.router.navigate === 'function') ctx.router.navigate(hash);
    else setQuery(q);
  }

  function buildSwitcher() {
    refs.partBtns = new Map();
    const nav = h('nav', { class: 'ws-parts', 'aria-label': t('wsx.parts.aria', lang) });
    const add = (key, label, swapped) => {
      const btn = h('button', {
        type: 'button', class: 'ws-part-btn', 'aria-pressed': 'false', 'data-part': key, onclick: () => goPart(key),
      }, [label, swapped ? h('span', { class: 'k-chip mid' }, t('wsx.parts.swapped', lang)) : null]);
      refs.partBtns.set(key, btn);
      nav.appendChild(btn);
    };
    add('', t('wsx.parts.whole', lang), false);
    state.info.parts.forEach((p) => add(p.stem, t('wsx.parts.n', lang, { n: p.n }), p.swapped));
    return nav;
  }

  function paintSwitcher() {
    const cur = currentPart();
    for (const [key, btn] of refs.partBtns || []) setPressed(btn, key === cur);
  }

  function breadcrumb(current) {
    const s = S();
    const scope = obj(s.scope);
    const athlete = (Array.isArray(obj(s.athletes).athletes) ? s.athletes.athletes : []).find((a) => a && a.id === scope.athlete);
    return h('nav', { class: 'ws-crumbs', 'aria-label': t('ws.breadcrumb', lang) }, [
      h('a', { href: formatHash('overview', {}, scope) }, athlete ? athlete.name : t('nav.overview', lang)),
      h('span', { 'aria-hidden': 'true' }, '/'),
      h('a', { href: formatHash('matches', {}, scope) }, t('nav.matches', lang)),
      h('span', { 'aria-hidden': 'true' }, '/'),
      h('span', { 'aria-current': 'page' }, current),
    ]);
  }

  // ----- header -----
  function titleSides(side = state.athleteSide) {
    // The selected athlete first; otherwise lower then upper (the canvas reads "Lower · Upper").
    const a = side || 'lower';
    return [a, a === 'lower' ? 'upper' : 'lower'];
  }

  /** Names and athlete side of a record (athlete ids resolved through the store). */
  function sidesOf(record) {
    const st = S();
    const athletes = obj(st.athletes);
    const athleteId = obj(st.scope).athlete || st.athlete || '';
    return {
      names: { upper: nameFor(record, 'upper', athletes), lower: nameFor(record, 'lower', athletes) },
      athleteSide: athleteId ? SIDES.find((sd) => obj(obj(record)[sd]).athlete_id === athleteId) || null : null,
      athleteId,
      athletes,
    };
  }

  /**
   * What the header shows: a split match's header always reads the primary's record (names in the primary's
   * frame); a single video's is its own.
   */
  function headCtx() {
    if (state.split && state.head) return { ...state.head, insights: state.headInsights || state.insights };
    return { record: state.record, names: state.names, athleteSide: state.athleteSide, insights: state.insights };
  }

  function crumbText() {
    if (state.dbl) return doublesTitleText();
    const hd = headCtx();
    const nm = (side) => hd.names[side] || t('ws.side.' + side, lang);
    const [a, b] = titleSides(hd.athleteSide);
    return t('ws.title', lang, { a: nm(a), b: nm(b) });
  }

  /** Doubles header: teams title, team-oriented result and sets, the doubles chip and the unvalidated badge. */
  function buildDoublesHead() {
    const m = state.model;
    const d = state.dbl;
    const hd = headCtx();
    const rec = obj(hd.record);
    const ins = obj(hd.insights);
    const date = fmtDate(matchDate(rec, ins), lang);
    const meta = h('div', { class: 'ws-meta' });
    if (d.sets.length) meta.appendChild(h('span', { class: 'mono ws-sets' }, d.sets.map((p) => `${p[0]}–${p[1]}`).join(' · ')));
    if (d.result) meta.appendChild(h('span', { class: 'k-chip ' + (d.result === 'W' ? 'good' : 'bad') }, t(d.result === 'W' ? 'ws.result.win' : 'ws.result.loss', lang)));
    else if (d.winnerTeam) {
      const tm = d.teams.find((x) => x.id === d.winnerTeam);
      meta.appendChild(h('span', { class: 'k-chip' }, t('result.side_won', lang, { name: tm ? tm.label : t('dbl.team', lang, { n: d.winnerTeam }) })));
    }
    meta.appendChild(h('span', null, t('ws.summary_line', lang, {
      rallies: rallyCountText(m.summary, lang), strokes: t('dbl.ws.hits_count', lang, { n: fmtNumber(d.whoHit.total) }),
    })));
    if (m.summary.label !== 'rallies') {
      meta.appendChild(h('span', { class: 'mono ov-pill' }, t(m.summary.label === 'windows' ? 'tag.windows' : 'tag.swing_est', lang)));
    }
    meta.appendChild(h('span', { class: 'k-chip' }, t('doubles.chip', lang)));
    meta.appendChild(unvalidatedBadge(lang));
    const title = d.title.vs
      ? [d.title.a, h('span', { class: 'ws-vs' }, lang === 'zh' ? '对' : 'vs.'), d.title.b]
      : doublesTitleText();
    return h('div', { class: 'ws-head' }, [
      h('div', { class: 'ws-head-l' }, [
        h('div', { class: 'mono ov-eyebrow' }, [t('ws.eyebrow', lang, { date }), matchDateApprox(rec, ins) ? approxMark(lang) : null]),
        h('h1', { class: 'page-title serif ws-title dbl-title' }, title),
        meta,
        h('div', { class: 'kw-foot-muted', role: 'note' }, t('dbl.strokes_withheld', lang)),
      ]),
      headActions(),
    ]);
  }

  /**
   * Back; for a shown video (not the Whole match, not a part without outputs): Posture report when /api/videos says
   * it has a posture run, and the "..." menu.
   */
  function headActions() {
    const s = state.stem;
    const shown = state.view !== 'whole' && !!state.model;
    return h('div', { class: 'ov-actions' }, [
      h('a', { href: formatHash('overview', {}, obj(S().scope)), class: 'ov-btn' }, t('ws.back', lang)),
      shown && state.video && state.video.has_posture
        ? h('a', { href: formatHash('results', { stem: s }, { mode: 'posture' }), class: 'ov-btn' }, t('wsx.posture', lang))
        : null,
      shown ? menuButton() : null,
    ]);
  }

  // ----- header "..." menu (the look and keys of the Matches row menu) -----
  function closeMenu(focusBack = true) {
    if (!menu) return;
    const { node, btn, onDoc } = menu;
    menu = null;
    if (node.parentNode) node.parentNode.removeChild(node);
    if (typeof document.removeEventListener === 'function') document.removeEventListener('pointerdown', onDoc, true);
    btn.setAttribute('aria-expanded', 'false');
    if (focusBack) focusNode(btn);
  }

  function menuItems() {
    const s = state.stem;
    return [{
      key: 'wsx.menu.delete', danger: true,
      run: () => openDeleteDialog({
        stem: s, lang, scope: 'match',
        // The match's analysis is gone: its details page has nothing left to show.
        onClose: ({ deleted } = {}) => {
          if (deleted && !state.destroyed && ctx.router && typeof ctx.router.navigate === 'function') ctx.router.navigate('#/matches');
        },
      }),
    }];
  }

  function openMenu(btn, wrap) {
    if (menu) { closeMenu(); return; }
    const node = h('div', { class: 'ws-menu', role: 'menu', 'aria-label': t('wsx.menu', lang) });
    const buttons = menuItems().map((it) => h('button', {
      type: 'button', role: 'menuitem', class: 'mv-menu-i' + (it.danger ? ' danger' : ''), 'data-item': it.key,
      // Focus goes back to the "..." button first, so the dialog returns focus there when it closes.
      onclick: () => { closeMenu(); it.run(); },
    }, t(it.key, lang)));
    buttons.forEach((b) => node.appendChild(b));
    node.addEventListener('keydown', (e) => {
      const i = buttons.indexOf(document.activeElement);
      if (e.key === 'Escape') { e.preventDefault(); if (typeof e.stopPropagation === 'function') e.stopPropagation(); closeMenu(); }
      else if (e.key === 'ArrowDown') { e.preventDefault(); focusNode(buttons[(i + 1) % buttons.length]); }
      else if (e.key === 'ArrowUp') { e.preventDefault(); focusNode(buttons[(i - 1 + buttons.length) % buttons.length]); }
      else if (e.key === 'Tab') closeMenu();
    });
    const onDoc = (e) => { if (menu && !node.contains(e.target) && !btn.contains(e.target)) closeMenu(false); };
    if (typeof document.addEventListener === 'function') document.addEventListener('pointerdown', onDoc, true);
    wrap.appendChild(node);
    btn.setAttribute('aria-expanded', 'true');
    menu = { node, btn, onDoc };
    focusNode(buttons[0]);
  }

  function menuButton() {
    const wrap = h('span', { class: 'ws-menu-wrap' });
    const btn = h('button', {
      type: 'button', class: 'ov-btn ws-menu-btn', 'aria-haspopup': 'menu', 'aria-expanded': 'false',
      'aria-label': t('wsx.menu', lang), title: t('wsx.menu', lang),
      onclick: () => openMenu(btn, wrap),
    }, h('span', { 'aria-hidden': 'true' }, '⋯'));
    wrap.appendChild(btn);
    return wrap;
  }

  /** The Whole match meta: parts, pooled rallies (weakest honest label) and strokes, failed-part note. */
  function wholeMeta(meta) {
    const parts = state.whole.parts;
    const ok = parts.filter((p) => p.ok);
    const pr = poolRallyCounts(ok.map((p) => p.summary));
    const strokes = ok.reduce((n, p) => n + p.strokes.total, 0);
    meta.appendChild(h('span', { class: 'k-chip' }, t('wsx.parts.count', lang, { n: parts.length })));
    meta.appendChild(h('span', null, t('ws.summary_line', lang, {
      rallies: rallyCountText({ count: pr.count, label: pr.label, signal: null }, lang),
      strokes: t('scope.strokes_count', lang, { n: fmtNumber(strokes) }),
    })));
    if (pr.label !== 'rallies') meta.appendChild(h('span', { class: 'mono ov-pill' }, t(pr.label === 'windows' ? 'tag.windows' : 'tag.swing_est', lang)));
    if (pr.failedParts) meta.appendChild(h('span', { class: 'kw-muted' }, t('wsx.parts.failed_note', lang, { n: pr.failedParts })));
  }

  function buildHead() {
    if (state.dbl) return buildDoublesHead();
    const m = state.model;
    const hd = headCtx();
    const nm = (side) => hd.names[side] || t('ws.side.' + side, lang);
    const [a, b] = titleSides(hd.athleteSide);
    const rec = obj(hd.record);
    const res = obj(rec.result);
    const ins = obj(hd.insights);
    const dateIso = matchDate(rec, ins);
    const date = fmtDate(dateIso, lang);
    const meta = h('div', { class: 'ws-meta' });
    if (Array.isArray(res.sets) && res.sets.length) {
      const pairs = res.sets.filter((p) => Array.isArray(p) && p.length === 2)
        .map((p) => (a === 'lower' ? `${p[1]}–${p[0]}` : `${p[0]}–${p[1]}`));
      meta.appendChild(h('span', { class: 'mono ws-sets' }, pairs.join(' · ')));
    }
    if (SIDES.includes(res.winner)) {
      if (hd.athleteSide) {
        const win = res.winner === hd.athleteSide;
        meta.appendChild(h('span', { class: 'k-chip ' + (win ? 'good' : 'bad') }, t(win ? 'ws.result.win' : 'ws.result.loss', lang)));
      } else {
        meta.appendChild(h('span', { class: 'k-chip' }, t('result.side_won', lang, { name: nm(res.winner) })));
      }
    }
    if (state.view === 'whole') {
      if (state.whole) wholeMeta(meta);
    } else if (m) {
      // A single video, or the selected part: its own rally / stroke line.
      meta.appendChild(h('span', null, t('ws.summary_line', lang, {
        rallies: rallyCountText(m.summary, lang), strokes: t('scope.strokes_count', lang, { n: fmtNumber(m.summary.strokes.total) }),
      })));
      if (m.summary.label !== 'rallies') {
        meta.appendChild(h('span', { class: 'mono ov-pill' }, t(m.summary.label === 'windows' ? 'tag.windows' : 'tag.swing_est', lang)));
      }
      if (m.summary.doubles) meta.appendChild(h('span', { class: 'k-chip' }, t('doubles.chip', lang)));
    }
    const head = h('div', { class: 'ws-head' }, [
      h('div', { class: 'ws-head-l' }, [
        h('div', { class: 'mono ov-eyebrow' }, [t('ws.eyebrow', lang, { date }),
          matchDateApprox(rec, ins) ? approxMark(lang) : null]),
        h('h1', { class: 'page-title serif ws-title' }, [nm(a), h('span', { class: 'ws-vs' }, lang === 'zh' ? '对' : 'vs.'), nm(b)]),
        meta,
        // A doubles run has no per-player score: say so instead of showing one.
        m && m.summary.doubles ? h('div', { class: 'kw-foot-muted', role: 'note' }, t('reason.doubles_run', lang)) : null,
      ]),
      headActions(),
    ]);
    return head;
  }

  // ----- video -----
  function buildVideo() {
    // detect_<stem>.mp4 is where app.py leaves the browser-compatible transcode (it replaces the raw file);
    // detect_<stem>_h264.mp4 is tried second for runs where the replace step did not happen.
    // A doubles run plays detect_<stem>_players.mp4 first, only when players.json says its render is ok; if that
    // file cannot be played the annotated video follows.
    const s = state.stem;
    const files = state.dbl
      ? doublesVideoFiles(s, state.dbl.identityVideoOk ? { status: 'ok' } : null)
      : [`detect_${s}.mp4`, `detect_${s}_h264.mp4`];
    const player = createPlayer({
      files: files.map((f) => endpoints.output(s, f)),
      lang,
      labels: { prev: t('ws.prev_rally', lang), next: t('ws.next_rally', lang) },
      onPrev: () => stepUnit(-1),
      onNext: () => stepUnit(1),
      // The players video was dropped: repaint the overlay tags without its "players video" tag.
      onSourceFallback: () => { if (state.dbl) paintVideoTags(); },
      playersVideo: !!(state.dbl && state.dbl.identityVideoOk),
    });
    Object.assign(refs, { player, video: player.video, tags: player.tagsEl, sig: player.sigEl });
    return player.el;
  }

  function seekTo(sec, fromSelection = true) {
    if (refs.player) refs.player.seekTo(sec, fromSelection);
  }

  function paintVideoTags() {
    if (!refs.tags) return;
    refs.tags.textContent = '';
    const u = currentUnit();
    const st = currentStroke();
    if (u) refs.tags.appendChild(h('span', { class: 'mono ws-tag' }, unitTitle(u).toUpperCase()));
    if (state.dbl) {
      if (refs.player && refs.player.playersVideo) refs.tags.appendChild(h('span', { class: 'mono ws-tag' }, t('dbl.ws.players_video', lang).toUpperCase()));
      const hit = currentHit();
      if (hit) {
        const inUnit = unitHits();
        const pos = inUnit.findIndex((x) => x.i === hit.i);
        refs.tags.appendChild(h('span', { class: 'mono ws-tag accent' }, t('dbl.ws.hit_tag', lang, {
          i: pos >= 0 ? pos + 1 : hit.i, n: pos >= 0 ? inUnit.length : state.dbl.whoHit.total, player: hitPlayerText(hit),
        }).toUpperCase()));
      }
    }
    if (st) {
      const inUnit = unitStrokes();
      const pos = inUnit.findIndex((x) => x.i === st.i);
      refs.tags.appendChild(h('span', { class: 'mono ws-tag accent' }, t('ws.tag.stroke', lang, {
        i: pos >= 0 ? pos + 1 : st.i, n: pos >= 0 ? inUnit.length : state.model.strokes.length, technique: strokeName(st, lang).toUpperCase(),
      })));
    }
    const s = state.model.summary;
    const sig = s.signal ? t('signal.' + s.signal, lang) : '';
    setText(refs.sig, sig && sig !== 'signal.' + s.signal ? sig.toUpperCase() : '');
    refs.sig.hidden = !refs.sig.textContent;
  }

  // ----- in-place helpers -----
  function setPressed(el, on) {
    if (!el) return;
    el.classList.toggle('on', !!on);
    if (el.hasAttribute('aria-pressed')) el.setAttribute('aria-pressed', on ? 'true' : 'false');
  }

  function focusNode(node) {
    if (!node || typeof node.focus !== 'function') return;
    try { node.focus({ preventScroll: true }); } catch (e) { node.focus(); }
  }

  /** Scroll `child` into view inside the scroll box `box` only (never the page). */
  function revealIn(box, child, axis) {
    if (!box || !child) return;
    if (axis === 'x') {
      const l = child.offsetLeft;
      const r = l + child.offsetWidth;
      if (l < box.scrollLeft || r > box.scrollLeft + box.clientWidth) box.scrollLeft = Math.max(0, l - box.clientWidth / 2);
      return;
    }
    const top = child.offsetTop;
    const bottom = top + child.offsetHeight;
    if (top < box.scrollTop + 32 || bottom > box.scrollTop + box.clientHeight) box.scrollTop = Math.max(0, top - box.clientHeight / 2);
  }

  function unitLabelText() {
    const l = state.model.summary.label;
    return t(l === 'windows' ? 'ws.label.windows' : l === 'segments' ? 'ws.label.segments' : 'ws.label.rallies', lang);
  }

  // ----- timeline (lane built once per load; selection updates it in place) -----
  function buildTimeline() {
    const m = state.model;
    const sec = h('section', { class: 'card hud ws-timeline', 'aria-label': t('ws.timeline', lang) });
    const chip = m.summary.label === 'rallies' ? null
      : h('span', { class: 'k-chip mid' }, t(m.summary.label === 'windows' ? 'ws.label.windows' : 'ws.label.segments', lang));
    sec.appendChild(h('div', { class: 'ws-sec-head' }, [h('h2', { class: 'card-title' }, t('ws.timeline', lang)), chip]));
    const lane = h('div', { class: 'ws-lane', role: 'group', 'aria-label': t('ws.timeline', lang) });
    refs.laneBtns = new Map();
    m.rallies.forEach((r, i) => {
      const title = unitTitle(r);
      const btn = h('button', {
        type: 'button',
        class: 'ws-rb rb' + (m.summary.label === 'windows' || r.degraded ? ' coarse' : ''),
        'aria-pressed': 'false',
        'aria-label': t('ws.unit_aria', lang, { title, n: r.strokeCount, sec: num1(r.len) }),
        title: `${title} · ${clockText(r.start)}–${clockText(r.end)}`,
        style: { flex: `${Math.max(r.len, 0.5)} 1 0`, animationDelay: `${Math.min(0.2 + i * 0.03, 1.2).toFixed(2)}s` },
        onclick: () => selectUnit(r.id, true),
      }, String(r.n));
      refs.laneBtns.set(r.id, btn);
      lane.appendChild(btn);
    });
    refs.allBtn = h('button', {
      type: 'button', class: 'ws-chip', 'aria-pressed': 'false', onclick: () => selectUnit('all', false),
    }, t('ws.all_strokes', lang));
    refs.laneScroll = m.rallies.length ? h('div', { class: 'ws-lane-scroll' }, lane) : null;
    if (refs.laneScroll) sec.appendChild(refs.laneScroll);
    sec.appendChild(h('div', { class: 'ws-lane-foot' }, [
      refs.allBtn,
      h('span', { class: 'card-sub' }, m.rallies.length ? `${t('ws.timeline_hint', lang)} ${t('ws.timeline_lane', lang)}` : signalNote(m.summary, lang, 0)),
    ]));
    refs.stripHost = h('div', { class: 'ws-strip-wrap' });
    sec.appendChild(refs.stripHost);
    refs.outsideNote = m.summary.outside
      ? h('div', { class: 'kw-foot-muted' }, t('ws.outside_note', lang, { n: m.summary.outside, label: unitLabelText() }))
      : null;
    if (refs.outsideNote) sec.appendChild(refs.outsideNote);
    return sec;
  }

  function paintLane() {
    for (const [id, btn] of refs.laneBtns || []) setPressed(btn, state.unit === id);
    setPressed(refs.allBtn, state.unit === 'all');
    if (refs.outsideNote) refs.outsideNote.hidden = state.unit === 'all';
    const on = refs.laneBtns && refs.laneBtns.get(state.unit);
    if (on) revealIn(refs.laneScroll, on, 'x');
  }

  /** "Show N more" button for a capped list. */
  function moreButton(rest, onMore) {
    return h('button', { type: 'button', class: 'ws-chip ws-more', onclick: onMore },
      t('ws.show_more', lang, { n: fmtNumber(Math.min(STROKE_PAGE, rest)), rest: fmtNumber(rest) }));
  }

  // ----- stroke strip (rebuilt when the unit changes; capped) -----
  // ----- doubles: who-hit strip and hits table (capped like the stroke strip and list) -----
  function selectHit(i, from) {
    settle();
    const prev = state.hitSel;
    state.hitSel = i;
    if (prev !== i) {
      const ps = refs.hitBtns && refs.hitBtns.get(prev);
      if (ps) setPressed(ps, false);
      if (refs.hitBtns && !refs.hitBtns.has(i)) renderHitStrip();
      else setPressed(refs.hitBtns && refs.hitBtns.get(i), true);
      const pr = refs.hitRows && refs.hitRows.get(prev);
      if (pr) { pr.tr.classList.remove('on'); pr.btn.removeAttribute('aria-current'); }
      if (refs.hitRows && !refs.hitRows.has(i)) renderHitRows();
      const n = refs.hitRows && refs.hitRows.get(i);
      if (n) {
        n.tr.classList.add('on');
        n.btn.setAttribute('aria-current', 'true');
        if (from !== 'list') revealIn(refs.tableWrap, n.tr, 'y');
      }
      paintVideoTags();
      const hit = currentHit();
      if (refs.live && hit) setText(refs.live, t('dbl.ws.hit_aria', lang, { n: hit.i, time: clockText(hit.time), player: hitPlayerText(hit) }));
    }
    const hit = currentHit();
    if (hit && hit.time !== null) seekTo(hit.time, true);
  }

  function hitTitle(hit) {
    const ev = t('dbl.evidence.' + hit.evidence, lang);
    return `${hitPlayerText(hit)} · ${clockText(hit.time)} · ${ev} · ${hit.confidenceText}`;
  }

  function renderHitStrip(focusFirstNew) {
    const host = refs.stripHost;
    if (!host) return;
    const hits = unitHits();
    const u = currentUnit();
    const head = u ? t('dbl.ws.hits_in', lang, { title: unitTitle(u), n: hits.length }) : t('dbl.ws.all_hits', lang, { n: hits.length });
    const before = refs.hitBtns ? refs.hitBtns.size : 0;
    const cap = capItems(hits, state.hitStripLimit, (x) => x.i === state.hitSel);
    state.hitStripLimit = cap.limit;
    host.textContent = '';
    host.appendChild(h('div', { class: 'ws-sec-head' }, [
      h('h3', { class: 'ws-h3' }, [t('dbl.ws.who_hit', lang), ' · ', head]),
      unvalidatedBadge(lang),
    ]));
    host.appendChild(h('div', { class: 'card-sub' }, t('dbl.ws.who_hit_hint', lang)));
    refs.hitBtns = new Map();
    if (!hits.length) {
      host.appendChild(h('div', { class: 'kw-note' }, t('dbl.ws.no_hits', lang)));
      return;
    }
    const strip = h('div', { class: 'ws-strip', role: 'group', 'aria-label': t('dbl.ws.who_hit', lang) });
    cap.items.forEach((x) => {
      const btn = h('button', {
        type: 'button',
        class: 'ws-st ws-hit' + (x.muted ? ' muted' : '') + (x.color ? '' : ' nobody') + (state.hitSel === x.i ? ' on' : ''),
        style: x.color ? { '--pc': x.color } : null,
        'aria-pressed': state.hitSel === x.i ? 'true' : 'false',
        'aria-label': t('dbl.ws.hit_aria', lang, { n: x.i, time: clockText(x.time), player: hitPlayerText(x) }),
        title: hitTitle(x),
        onclick: () => selectHit(x.i, 'strip'),
      }, x.hitterId || '?');
      refs.hitBtns.set(x.i, btn);
      strip.appendChild(btn);
    });
    if (cap.rest) {
      strip.appendChild(moreButton(cap.rest, () => {
        state.hitStripLimit += STROKE_PAGE;
        renderHitStrip(true);
      }));
    }
    host.appendChild(strip);
    if (focusFirstNew) focusNode([...refs.hitBtns.values()][before]);
  }

  function renderHitRows(focusFirstNew) {
    const wrap = refs.tableWrap;
    if (!wrap) return;
    const hits = unitHits();
    const before = refs.hitRows ? refs.hitRows.size : 0;
    const keepTop = focusFirstNew ? wrap.scrollTop : 0;
    const cap = capItems(hits, state.hitListLimit, (x) => x.i === state.hitSel);
    state.hitListLimit = cap.limit;
    refs.hitRows = new Map();
    wrap.textContent = '';
    if (!hits.length) {
      wrap.appendChild(h('div', { class: 'kw-note ws-empty' }, t('dbl.ws.no_hits', lang)));
      return;
    }
    const rows = cap.items.map((x) => {
      const btn = h('button', {
        type: 'button', class: 'ws-row-btn',
        'aria-label': t('dbl.ws.hit_aria', lang, { n: x.i, time: clockText(x.time), player: hitPlayerText(x) }),
        'aria-current': state.hitSel === x.i ? 'true' : null,
        onclick: () => selectHit(x.i, 'list'),
      }, [swatch(x.color), hitPlayerText(x)]);
      const tr = h('tr', { class: 'ws-row' + (state.hitSel === x.i ? ' on' : '') + (x.muted ? ' unc' : '') }, [
        h('td', { class: 'mono kw-muted' }, String(x.i)),
        h('td', { class: 'mono kw-muted' }, clockText(x.time)),
        h('td', null, btn),
        h('td', { class: 'kw-muted' }, x.team ? t('dbl.team', lang, { n: x.team }) : '—'),
        h('td', { class: 'mono kw-num' }, x.confidenceText),
        h('td', { class: 'kw-muted' }, t('dbl.evidence.' + x.evidence, lang)),
      ]);
      refs.hitRows.set(x.i, { tr, btn });
      return tr;
    });
    wrap.appendChild(h('table', { class: 'table ws-table' }, [
      h('thead', null, h('tr', null, [
        h('th', { scope: 'col' }, '#'), h('th', { scope: 'col' }, t('dbl.col.time', lang)), h('th', { scope: 'col' }, t('dbl.col.player', lang)),
        h('th', { scope: 'col' }, t('dbl.col.team', lang)), h('th', { scope: 'col', class: 'kw-num' }, t('dbl.col.confidence', lang)),
        h('th', { scope: 'col' }, t('dbl.col.evidence', lang)),
      ])),
      h('tbody', null, rows),
    ]));
    if (cap.rest) {
      wrap.appendChild(h('div', { class: 'ws-more-row' }, moreButton(cap.rest, () => {
        state.hitListLimit += STROKE_PAGE;
        renderHitRows(true);
      })));
    }
    wrap.scrollTop = keepTop;
    if (focusFirstNew) {
      const added = [...refs.hitRows.values()][before];
      if (added) focusNode(added.btn);
    }
  }

  function renderStrip(focusFirstNew) {
    if (state.dbl) {
      renderHitStrip(focusFirstNew);
      return;
    }
    const host = refs.stripHost;
    if (!host) return;
    const strokes = unitStrokes();
    const u = currentUnit();
    const head = u ? t('ws.unit_strokes', lang, { title: unitTitle(u), n: strokes.length }) : t('ws.all_strokes_title', lang, { n: strokes.length });
    const before = refs.stripBtns ? refs.stripBtns.size : 0;
    const cap = capItems(strokes, state.stripLimit, (s) => s.i === state.sel);
    state.stripLimit = cap.limit;
    host.textContent = '';
    host.appendChild(h('div', { class: 'ws-sec-head' }, [h('h3', { class: 'ws-h3' }, head), h('span', { class: 'card-sub' }, t('ws.pick_stroke', lang))]));
    refs.stripBtns = new Map();
    if (!strokes.length) {
      host.appendChild(h('div', { class: 'kw-note' }, t('ws.no_strokes', lang)));
      return;
    }
    const strip = h('div', { class: 'ws-strip' });
    cap.items.forEach((s) => {
      const btn = h('button', {
        type: 'button',
        class: `ws-st tc-${s.label}` + (s.uncertain ? ' unc' : '') + (s.opponent ? ' opp' : '') + (state.sel === s.i ? ' on' : ''),
        'aria-pressed': state.sel === s.i ? 'true' : 'false',
        'aria-label': t('ws.stroke_aria', lang, { n: s.i, technique: strokeName(s, lang) }) + ', ' + (s.hitter ? sideName(s.hitter) : '—'),
        title: `${strokeName(s, lang)} · ${clockText(s.time)}`,
        onclick: () => selectStroke(s.i, 'strip'),
      }, techCode(s.label, lang));
      refs.stripBtns.set(s.i, btn);
      strip.appendChild(btn);
    });
    if (cap.rest) {
      strip.appendChild(moreButton(cap.rest, () => {
        state.stripLimit += STROKE_PAGE;
        renderStrip(true);
      }));
    }
    host.appendChild(strip);
    if (focusFirstNew) focusNode([...refs.stripBtns.values()][before]);
  }

  // ----- stroke list (shell built once; filters rebuilt per unit; rows capped) -----
  function buildList() {
    if (state.dbl) {
      // Doubles: the hits table (time, player, team, confidence, evidence); stroke types are withheld.
      const dsec = h('section', { class: 'card hud ws-list', 'aria-label': t('dbl.ws.hits_title', lang) });
      refs.filterHost = h('div', { class: 'ws-filters' }, h('span', { class: 'k-chip' }, t('dbl.strokes_withheld', lang)));
      dsec.appendChild(h('div', { class: 'ws-sec-head' }, [h('h2', { class: 'card-title' }, t('dbl.ws.hits_title', lang)), refs.filterHost]));
      refs.tableWrap = h('div', { class: 'ws-table-wrap' });
      dsec.appendChild(refs.tableWrap);
      return dsec;
    }
    const sec = h('section', { class: 'card hud ws-list', 'aria-label': t('ws.strokes_in_rally', lang) });
    refs.filterHost = h('div', { class: 'ws-filters', role: 'group', 'aria-label': t('ws.filter_aria', lang) });
    sec.appendChild(h('div', { class: 'ws-sec-head' }, [h('h2', { class: 'card-title' }, t('ws.strokes_in_rally', lang)), refs.filterHost]));
    const notice = state.model.summary.strokeNotice;
    if (notice) sec.appendChild(h('div', { class: 'kw-foot-muted', id: 'ws-stroke-notice' }, t('ws.stroke_' + notice, lang)));
    refs.tableWrap = h('div', { class: 'ws-table-wrap' });
    sec.appendChild(refs.tableWrap);
    return sec;
  }

  function renderFilters() {
    if (state.dbl) return; // no stroke types on doubles: the list head carries the "withheld" note instead
    const all = unitStrokes();
    const present = new Set(all.map((s) => s.label));
    const ids = ['all', ...TECHNIQUE_IDS.filter((id) => present.has(id)), ...(present.has('uncertain') ? ['uncertain'] : [])];
    if (!ids.includes(state.filter)) state.filter = 'all';
    refs.filterHost.textContent = '';
    refs.filterBtns = new Map();
    ids.forEach((id) => {
      const btn = h('button', {
        type: 'button', class: 'ws-chip' + (state.filter === id ? ' on' : ''), 'aria-pressed': state.filter === id ? 'true' : 'false',
        onclick: () => {
          if (state.filter === id) return;
          state.filter = id;
          state.listLimit = STROKE_PAGE;
          for (const [k, b] of refs.filterBtns) setPressed(b, k === id);
          renderRows();
        },
      }, id === 'all' ? t('ws.filter.all', lang) : techLabel(id, lang));
      refs.filterBtns.set(id, btn);
      refs.filterHost.appendChild(btn);
    });
  }

  /** The list's score cell: the score, or a muted "as <technique>" when the report is for another technique. */
  function scoreCell(ls, lo) {
    if (ls.score !== null) return h('td', { class: 'mono kw-num' + (ls.score < lo ? ' ws-bad' : '') }, num1(ls.score));
    if (ls.as) {
      const label = techLabel(ls.as, lang);
      return h('td', { class: 'kw-num kw-muted' },
        h('span', { class: 'ws-as', title: t('ws.list_as_title', lang, { technique: label }) }, t('ws.list_as', lang, { technique: label })));
    }
    return h('td', { class: 'mono kw-num kw-muted' }, '—');
  }

  function renderRows(focusFirstNew) {
    if (state.dbl) {
      renderHitRows(focusFirstNew);
      return;
    }
    const wrap = refs.tableWrap;
    if (!wrap) return;
    const all = unitStrokes();
    const shown = state.filter === 'all' ? all : all.filter((s) => s.label === state.filter);
    const before = refs.listRows ? refs.listRows.size : 0;
    const keepTop = focusFirstNew ? wrap.scrollTop : 0;
    const cap = capItems(shown, state.listLimit, (s) => s.i === state.sel);
    state.listLimit = cap.limit;
    const lo = state.model.summary.reference.lo;
    refs.listRows = new Map();
    const rows = cap.items.map((s) => {
      const btn = h('button', {
        type: 'button', class: 'ws-row-btn',
        'aria-label': t('ws.stroke_aria', lang, { n: s.i, technique: strokeName(s, lang) }),
        'aria-current': state.sel === s.i ? 'true' : null,
        onclick: () => selectStroke(s.i, 'list'),
      }, [h('span', { class: `ws-dot tc-${s.label}`, 'aria-hidden': 'true' }), strokeName(s, lang)]);
      const tr = h('tr', { class: 'ws-row' + (state.sel === s.i ? ' on' : '') + (s.uncertain ? ' unc' : '') }, [
        h('td', { class: 'mono kw-muted' }, String(s.i)),
        h('td', { class: 'mono kw-muted' }, clockText(s.time)),
        h('td', null, btn),
        h('td', { class: 'kw-muted' }, [s.hitter ? sideName(s.hitter) : '—', s.opponent ? h('span', { class: 'ws-opp' }, t('ws.opponent_tag', lang)) : null]),
        scoreCell(listScore(s), lo),
      ]);
      refs.listRows.set(s.i, { tr, btn });
      return tr;
    });
    wrap.textContent = '';
    if (!shown.length) {
      wrap.appendChild(h('div', { class: 'kw-note ws-empty' }, t('ws.no_strokes', lang)));
      return;
    }
    wrap.appendChild(h('table', { class: 'table ws-table' }, [
      h('thead', null, h('tr', null, [
        h('th', { scope: 'col' }, '#'), h('th', { scope: 'col' }, t('ws.col.time', lang)), h('th', { scope: 'col' }, t('ws.col.technique', lang)),
        h('th', { scope: 'col' }, t('ws.col.player', lang)), h('th', { scope: 'col', class: 'kw-num' }, t('ws.col.score', lang)),
      ])),
      h('tbody', null, rows),
    ]));
    if (cap.rest) {
      wrap.appendChild(h('div', { class: 'ws-more-row' }, moreButton(cap.rest, () => {
        state.listLimit += STROKE_PAGE;
        renderRows(true);
      })));
    }
    wrap.scrollTop = keepTop;
    if (focusFirstNew) {
      const added = [...refs.listRows.values()][before];
      if (added) focusNode(added.btn);
    }
  }

  // ----- inspector -----
  function yard(score, lo, hi, cls) {
    const pct = Math.max(0, Math.min(100, score));
    return h('div', { class: 'ws-yard ' + (cls || ''), 'aria-hidden': 'true' }, [
      h('div', { class: 'ws-yard-band', style: { left: lo + '%', width: Math.max(0, hi - lo) + '%' } }),
      h('div', { class: 'ws-yard-mark', style: { left: `calc(${pct}% - 2px)` } }),
    ]);
  }

  /** Doubles Players panel: identity status (with its localized reason), then one block per player. */
  function buildPlayersPanel() {
    const d = state.dbl;
    const sec = h('section', { class: 'card hud ws-insp dbl-players', 'aria-label': t('dbl.ws.players', lang) });
    sec.appendChild(h('div', { class: 'ws-sec-head' }, [
      h('h2', { class: 'card-title' }, t('dbl.ws.players', lang)),
      h('span', { class: 'k-chip' + (d.tone ? ' ' + d.tone : '') }, d.statusText),
    ]));
    if (d.detail) sec.appendChild(h('div', { class: 'ws-note dashed', role: 'note' }, d.detail));
    const wh = d.whoHit;
    sec.appendChild(h('div', { class: 'dbl-named' }, [
      unvalidatedBadge(lang),
      h('span', { class: 'kw-muted' }, wh.total
        ? t('dbl.ws.named_line', lang, { named: fmtNumber(wh.named), total: fmtNumber(wh.total), pct: wh.namedShare === null ? '—' : `${fmtNumber(wh.namedShare * 100, 0)}%` })
        : t('dbl.ws.no_hits', lang)),
    ]));
    // The identity render (players labelled on the video), only when players.json says it was made.
    const iv = obj(obj(state.playersFile).identity_video);
    // Its name must stay inside the run's output folder (same check as the heatmap names); a bad one gets no link.
    if (iv.status === 'ok' && safeFile(iv.file)) {
      sec.appendChild(h('a', { class: 'ws-ident-link', href: endpoints.output(state.stem, iv.file), target: '_blank', rel: 'noopener' }, t('wsx.identity_video', lang)));
    } else if (iv.status !== 'ok' && typeof iv.reason === 'string' && iv.reason) {
      // Not made (skipped, failed): the run's own reason, as text.
      sec.appendChild(h('div', { class: 'kw-muted ws-ident-none' }, t('wsx.identity_unavailable', lang, { reason: iv.reason })));
    }
    if (!d.players.length) {
      sec.appendChild(h('div', { class: 'kw-note' }, t('dbl.no_players', lang)));
      return sec;
    }
    const list = h('ul', { class: 'dbl-plist' });
    d.players.forEach((p) => {
      const stat = (key, value) => h('span', null, [h('span', { class: 'kw-muted' }, t(key, lang) + ' '), h('span', { class: 'mono' }, value)]);
      list.appendChild(h('li', { class: 'dbl-prow' }, [
        h('div', { class: 'dbl-who' }, [
          swatch(p.color),
          h('strong', { class: 'dbl-name' }, p.label),
          p.label !== p.id ? h('span', { class: 'mono kw-muted' }, p.id) : null,
          h('span', { class: 'k-chip' + (p.team ? '' : ' mid') }, p.teamText),
        ]),
        h('div', { class: 'dbl-stats' }, [
          stat('dbl.col.coverage', p.coverageText),
          stat('dbl.col.distance', p.distanceText),
          stat('dbl.col.speed', `${p.avgSpeedText} / ${p.maxSpeedText}`),
          stat('dbl.col.hits', p.hitsText),
          stat('dbl.col.named', p.namedText),
        ]),
      ]));
    });
    sec.appendChild(list);
    return sec;
  }

  function buildInspector() {
    if (state.dbl) return buildPlayersPanel();
    const st = currentStroke();
    const { lo, hi } = state.model.summary.reference;
    const sec = h('section', { class: 'card hud ws-insp', 'aria-label': t('ws.inspector', lang) });
    sec.appendChild(h('div', { class: 'k-label' }, t('ws.inspector', lang)));
    if (!st) {
      sec.appendChild(h('div', { class: 'kw-note' }, state.model.strokes.length ? t('ws.pick_none', lang) : t('ws.empty.body', lang)));
      return sec;
    }
    const unit = state.model.rallies.find((r) => r.id === st.rallyId);
    sec.appendChild(h('h2', { class: 'ws-insp-title' }, t('ws.stroke_title', lang, { n: st.i, technique: strokeName(st, lang) })));
    sec.appendChild(h('div', { class: 'card-sub' }, [st.hitter ? sideName(st.hitter) : '—', clockText(st.time), unit ? unitTitle(unit) : null].filter(Boolean).join(' · ')));
    if (st.uncertain) sec.appendChild(h('div', { class: 'ws-note' }, t('ws.uncertain_note', lang)));
    if (st.opponent) {
      sec.appendChild(h('div', { class: 'ws-note dashed' }, t('ws.not_scored_note', lang)));
      return sec;
    }
    const rep = st.report;
    if (rep && rep.scored === false) {
      sec.appendChild(h('div', { class: 'ws-note dashed' }, t('ws.no_reference', lang)));
      return sec;
    }
    if (!rep || rep.overall === null) {
      sec.appendChild(h('div', { class: 'ws-note dashed' }, t('ws.no_report', lang)));
      return sec;
    }
    const below = rep.overall < lo;
    const above = rep.overall > hi;
    sec.appendChild(h('div', { class: 'ws-score' }, [
      h('span', { class: 'mono ws-score-num' }, num1(rep.overall)),
      h('span', { class: 'kw-tile-unit' }, '/ 100'),
      h('span', { class: 'k-chip ' + (below ? 'bad' : 'good') }, t(below ? 'ws.verdict.below' : above ? 'ws.verdict.above' : 'ws.verdict.inside', lang)),
    ]));
    sec.appendChild(yard(rep.overall, lo, hi, 'big'));
    sec.appendChild(h('div', { class: 'mono ws-yard-axis' }, [h('span', null, '0'), h('span', null, t('ws.reference', lang, { lo, hi })), h('span', null, '100')]));
    if (rep.technique && rep.technique !== st.label) {
      sec.appendChild(h('div', { class: 'kw-foot-muted' }, t('ws.scored_as', lang, { technique: techLabel(rep.technique, lang) })));
    }
    const metrics = h('div', { class: 'ws-metrics' });
    rep.metrics.forEach((mt) => {
      const measured = mt.measured === null ? '—' : num1(mt.measured);
      metrics.appendChild(h('div', { class: 'ws-metric' }, [
        h('div', { class: 'ws-metric-head' }, [
          h('span', { class: 'kw-muted' }, metricLabel(mt.id, lang)),
          h('span', { class: 'mono ws-metric-val' + (mt.score !== null && mt.score < lo ? ' ws-bad' : '') }, mt.score === null ? '—' : num1(mt.score)),
        ]),
        mt.score === null ? h('div', { class: 'ws-yard small empty', 'aria-hidden': 'true' }) : yard(mt.score, lo, hi, 'small'),
        h('div', { class: 'mono ws-metric-sub' }, mt.ideal ? t('ws.metric_measured', lang, { measured, lo: mt.ideal[0], hi: mt.ideal[1] }) : measured),
      ]));
    });
    if (rep.metrics.length) sec.appendChild(metrics);
    const weak = weaknessLines(rep.raw);
    if (weak.length) {
      sec.appendChild(h('div', { class: 'k-label' }, t('wsx.weak_title', lang)));
      sec.appendChild(h('ul', { class: 'ws-weak' }, weak.map((w) => {
        const label = metricLabel(w.metric, lang);
        const m = w.measured === null ? '—' : num1(w.measured);
        if (w.ideal) return h('li', null, t('wsx.weak_line', lang, { metric: label, m, lo: num1(w.ideal[0]), hi: num1(w.ideal[1]) }));
        return h('li', null, w.measured !== null ? t('wsx.weak_line_m', lang, { metric: label, m }) : label);
      })));
    }
    return sec;
  }

  // ----- rally summary -----
  function buildSummary() {
    const m = state.model;
    const u = currentUnit();
    const sec = h('section', { class: 'card hud ws-sum', 'aria-label': t('ws.summary', lang) });
    sec.appendChild(h('h2', { class: 'card-title' }, t('ws.summary', lang)));
    const dl = h('dl', { class: 'ws-dl' });
    const row = (k, v) => { dl.appendChild(h('dt', null, k)); dl.appendChild(h('dd', { class: 'mono' }, v)); };
    const strokes = unitStrokes();
    if (state.dbl) {
      // Doubles: hits per unit and who hit last; no stroke labels.
      const hits = unitHits();
      if (u) {
        row(unitTitle({ n: '' }).trim(), t('ws.summary.of', lang, { n: u.n, total: m.rallies.length }));
        row(t('dbl.ws.hits_title', lang), String(hits.length));
        row(t('ws.summary.duration', lang), `${num1(u.len)} s`);
        row(t('ws.summary.span', lang), `${clockText(u.start)}–${clockText(u.end)}`);
      } else {
        row(t('dbl.ws.hits_title', lang), String(hits.length));
      }
      const lastHit = hits[hits.length - 1];
      if (lastHit) row(t('dbl.ws.last_hit', lang), hitPlayerText(lastHit));
    } else if (u) {
      row(unitTitle({ n: '' }).trim(), t('ws.summary.of', lang, { n: u.n, total: m.rallies.length }));
      row(t('ws.summary.strokes', lang), String(u.strokeCount));
      row(t('ws.summary.duration', lang), `${num1(u.len)} s`);
      row(t('ws.summary.span', lang), `${clockText(u.start)}–${clockText(u.end)}`);
    } else {
      row(t('ws.summary.strokes', lang), String(strokes.length));
    }
    const last = strokes[strokes.length - 1];
    if (last && !state.dbl) row(t('ws.summary.last_stroke', lang), t('ws.by', lang, { label: strokeName(last, lang), name: last.hitter ? sideName(last.hitter) : '—' }));
    sec.appendChild(dl);
    const note = signalNote(m.summary, lang, m.rallies.length);
    if (note) sec.appendChild(h('div', { class: 'ws-sum-note' }, note));
    return sec;
  }

  // ----- movement card -----
  /** Doubles heatmaps from players.json: team coverage maps and per-player maps (entries without a file skipped). */
  function doublesHeatmaps() {
    const pf = obj(state.playersFile);
    const label = (id) => {
      const p = state.dbl ? state.dbl.players.find((x) => x.id === id) : null;
      return p ? p.label : id;
    };
    return {
      teams: (Array.isArray(pf.teams) ? pf.teams : []).filter((tm) => tm && typeof tm.coverage_heatmap === 'string' && tm.coverage_heatmap)
        .map((tm) => ({ id: tm.id, file: tm.coverage_heatmap })),
      players: (Array.isArray(pf.players) ? pf.players : []).filter((p) => p && typeof p.heatmap === 'string' && p.heatmap)
        .map((p) => ({ id: p.id, label: label(p.id), file: p.heatmap })),
    };
  }

  function buildMovement() {
    return movementCard({ lang, items: [{ stem: state.stem, caption: null }], doubles: state.dbl ? doublesHeatmaps() : null });
  }

  // ----- strokes tab: distribution by group (+ the TrackNet provenance note) -----
  /**
   * One video's distribution by default; the Whole match passes the pooled one. `strokes` is every detected stroke
   * (the header's count): the groups hold the classified ones only, so the rest shows as uncertain. `hasStrokes`
   * tells a stale strokes file (labels out of date) from no strokes at all; `tracknet` adds the provenance note.
   */
  function distributionCard({
    d = strokeDistribution(state.strokesDoc),
    strokes = state.model ? state.model.summary.strokes.total : 0,
    hasStrokes = Array.isArray(obj(state.strokesDoc).strokes) && state.strokesDoc.strokes.length > 0,
    tracknet = trackNetNote(state.strokesDoc),
  } = {}) {
    const u = Math.max(0, strokes - d.total);
    const all = d.total + u;
    const sec = h('section', { class: 'card hud ws-dist', 'aria-label': t('wsx.dist.title', lang) });
    sec.appendChild(h('div', { class: 'ws-sec-head' }, [
      h('h2', { class: 'card-title' }, t('wsx.dist.title', lang)),
      d.total ? h('span', { class: 'card-sub' }, t('wsx.dist.total', lang, { n: fmtNumber(d.total), u: fmtNumber(u) })) : null,
    ]));
    if (!d.total) {
      sec.appendChild(h('div', { class: 'kw-note' }, t(d.stale && hasStrokes ? 'ws.stroke_stale' : 'ws.empty.title', lang)));
    } else {
      // The classified groups, then a muted uncertain segment: the bar adds up to the header's stroke count.
      const segs = [...d.groups, ...(u ? [{ id: 'uncertain', count: u }] : [])];
      const text = segs.map((g) => `${techLabel(g.id, lang)} ${g.count}`).join(', ');
      sec.appendChild(h('div', { class: 'ws-dist-bar', role: 'img', 'aria-label': text }, segs.map((g) => h('span', {
        class: `ws-dist-seg tc-${g.id}`, style: { flex: `${g.count} 1 0` }, title: `${techLabel(g.id, lang)} · ${g.count}`,
      }))));
      sec.appendChild(h('ul', { class: 'ws-dist-legend' }, segs.map((g) => h('li', g.id === 'uncertain' ? { class: 'kw-muted' } : null, [
        h('span', { class: `ws-dot tc-${g.id}`, 'aria-hidden': 'true' }),
        techLabel(g.id, lang),
        h('span', { class: 'mono kw-muted' }, `${g.count} · ${Math.round((g.count / all) * 100)}%`),
      ]))));
    }
    if (tracknet) sec.appendChild(h('div', { class: 'kw-foot-muted' }, t('wsx.tracknet', lang)));
    return sec;
  }

  // ----- tabs -----
  const tabId = (id) => 'ws-tab-' + id;

  function buildTabs() {
    refs.tabBtns = new Map();
    const bar = h('div', { class: 'ws-tabs', role: 'tablist', 'aria-label': t('wsx.tabs_aria', lang) });
    TABS.forEach((id, k) => {
      const btn = h('button', {
        type: 'button', role: 'tab', class: 'ws-tab', id: tabId(id), 'aria-controls': 'ws-tabpanel', 'aria-selected': 'false', tabindex: '-1',
        onclick: () => selectTab(id, true),
        onkeydown: (e) => {
          const n = TABS.length;
          const to = e.key === 'ArrowRight' ? (k + 1) % n : e.key === 'ArrowLeft' ? (k - 1 + n) % n
            : e.key === 'Home' ? 0 : e.key === 'End' ? n - 1 : -1;
          if (to < 0) return;
          e.preventDefault();
          selectTab(TABS[to], true);
          focusNode(refs.tabBtns.get(TABS[to]));
        },
      }, t('wsx.tab.' + id, lang));
      refs.tabBtns.set(id, btn);
      bar.appendChild(btn);
    });
    refs.tabPanel = h('div', { class: 'ws-tabpanel', role: 'tabpanel', id: 'ws-tabpanel', tabindex: '0' });
    return h('div', { class: 'ws-tabwrap' }, [bar, refs.tabPanel]);
  }

  /** The open tab's body: built on first open and kept, so switching back keeps its state. */
  function tabBody(id) {
    if (panels[id]) return panels[id].el;
    const s = state.stem;
    let p;
    if (state.view === 'whole') {
      p = { el: wholeTabBody(id) };
    } else if (id === 'technique') {
      p = { el: techniquePanel({ lang, summary: techniqueSummary(state.tech, { doubles: !!state.dbl || state.model.summary.doubles }) }) };
    } else if (id === 'clips') {
      const sm = state.model.summary;
      p = clipsPanel({ stem: s, lang, rallyOk: sm.hasFile && !sm.failed && sm.count > 0 });
    } else if (id === 'plan') {
      p = planPanel({ stem: s, lang });
    } else {
      // Strokes: the distribution card (singles only) above the stroke list (doubles: the hits table).
      p = { el: h('div', { class: 'ws-strokes-tab' }, [state.dbl ? null : distributionCard(), refs.listSec]) };
    }
    panels[id] = p;
    return p.el;
  }

  function renderTab() {
    for (const [id, btn] of refs.tabBtns || []) {
      const on = id === state.tab;
      btn.setAttribute('aria-selected', on ? 'true' : 'false');
      btn.setAttribute('tabindex', on ? '0' : '-1');
      btn.classList.toggle('on', on);
    }
    const panel = refs.tabPanel;
    if (!panel) return;
    panel.setAttribute('aria-labelledby', tabId(state.tab));
    const body = tabBody(state.tab);
    if (panel.firstChild !== body) {
      panel.textContent = '';
      panel.appendChild(body);
    }
  }

  /** Open a tab in place. From the page (`fromUser`) the hash follows via router.replace; the default tab is left out. */
  function selectTab(id, fromUser) {
    const tab = TABS.includes(id) ? id : 'strokes';
    if (fromUser) {
      const q = { ...state.query };
      if (tab === 'strokes') delete q.tab; else q.tab = tab;
      state.query = q;
    }
    if (tab !== state.tab) {
      state.tab = tab;
      // No tab bar while a view loads (teardown clears it): the next render opens state.tab.
      if (refs.tabPanel) renderTab();
    }
    if (fromUser && ctx.router && typeof ctx.router.replace === 'function') ctx.router.replace(formatHash('matches', { stem }, state.query));
  }

  function destroyPanels() {
    for (const p of Object.values(panels)) if (p && typeof p.destroy === 'function') p.destroy();
    panels = {};
  }

  // ----- render -----
  /** Start a view: release the previous one, (re)build the frame, refresh breadcrumb, header and switcher. */
  function beginView() {
    teardown();
    if (!refs.framed) frame();
    root.classList.remove('settled');
    swapNode('crumb', breadcrumb(crumbText()));
    const head = buildHead();
    swapNode('head', head);
    paintSwitcher();
    refs.body.textContent = '';
    setText(refs.live, '');
    return head;
  }

  function startReveal() {
    clock.start();
    // Entrance animations play once; anything built after this (a new strip, more rows) appears at once.
    clearTimeout(settleTimer);
    settleTimer = setTimeout(settle, SETTLE_MS);
  }

  /** One video (a single match, or the selected part of a split match). */
  function renderAll() {
    if (state.destroyed) return;
    const head = beginView();
    const video = buildVideo();
    const timeline = buildTimeline();
    refs.listSec = buildList();
    const move = buildMovement();
    refs.inspHost = h('div', { class: 'ws-slot' });
    refs.sumHost = h('div', { class: 'ws-slot' });
    const main = h('div', { class: 'ws-main' }, [video, timeline]);
    const side = h('aside', { class: 'ws-side', 'aria-label': t('wsx.side_aria', lang) }, [move, refs.inspHost, refs.sumHost]);
    refs.body.appendChild(h('div', { class: 'ws-top' }, [main, side]));
    const tabs = buildTabs();
    refs.body.appendChild(tabs);
    sections = [head, video, timeline, move, refs.inspHost, refs.sumHost, tabs];
    renderTab();
    paintLane();
    renderStrip();
    renderFilters();
    renderRows();
    renderSide(false);
    startReveal();
  }

  // ----- Whole match view (split matches) -----
  function partCard(p) {
    const v = obj(p.video);
    const poster = typeof v.thumb === 'string' && v.thumb
      ? h('img', { class: 'ws-part-poster', src: v.thumb, alt: '', loading: 'lazy' })
      : h('span', { class: 'mv-thumb ws-part-poster', 'aria-hidden': 'true' });
    const line = (text, cls) => h('span', { class: 'ws-part-line' + (cls ? ' ' + cls : '') }, text);
    const title = t('wsx.parts.n', lang, { n: p.n });
    return h('button', { type: 'button', class: 'ws-part-card', 'data-part': p.stem, onclick: () => goPart(p.stem) }, [
      poster,
      h('span', { class: 'ws-part-title' }, [
        h('strong', null, title),
        isNum(v.duration_sec) ? h('span', { class: 'mono kw-muted' }, fmtDuration(v.duration_sec)) : null,
        p.swapped ? h('span', { class: 'k-chip mid' }, t('wsx.parts.swapped', lang)) : null,
      ]),
      p.ok
        ? [
          line(rallyCountText(p.summary, lang)),
          line(t('scope.strokes_count', lang, { n: fmtNumber(p.strokes.total) })),
          line([t('wsx.parts.tech', lang), ' ', h('span', { class: 'mono' }, p.techIndex === null ? '—' : num1(p.techIndex))]),
        ]
        : line(t(p.reason === 'error' ? 'error.load.title' : 'wsx.parts.not_analysed', lang), 'kw-muted'),
    ]);
  }

  /** The per-part strokes table under the pooled distribution. */
  function partsTable() {
    const rows = state.whole.parts.map((p) => h('tr', null, [
      h('td', null, t('wsx.parts.n', lang, { n: p.n })),
      h('td', { class: 'mono kw-num' }, p.ok ? fmtNumber(p.strokes.total) : '—'),
      h('td', { class: 'mono kw-num' }, p.ok ? fmtNumber(p.strokes.uncertain) : '—'),
    ]));
    return h('section', { class: 'card hud ws-list' }, h('table', { class: 'table ws-table' }, [
      h('thead', null, h('tr', null, [
        h('th', { scope: 'col' }, t('wsx.parts.col.part', lang)),
        h('th', { scope: 'col', class: 'kw-num' }, t('ws.summary.strokes', lang)),
        h('th', { scope: 'col', class: 'kw-num' }, t('wsx.parts.col.uncertain', lang)),
      ])),
      h('tbody', null, rows),
    ]));
  }

  /** Whole match tab bodies: pooled strokes and technique; clips and plans point at the parts. */
  function wholeTabBody(id) {
    const ok = state.whole.parts.filter((p) => p.ok);
    if (id === 'technique') {
      return techniquePanel({ lang, summary: poolTechnique(ok.map((p) => p.techSummary)), note: t('wsx.parts.pooled', lang) });
    }
    if (id === 'clips' || id === 'plan') {
      return h('div', { class: 'ws-part-pick' }, [
        h('p', { class: 'kw-note' }, t('wsx.parts.per_video', lang)),
        h('div', { class: 'ws-part-links' }, state.whole.parts.map((p) => h('button', {
          type: 'button', class: 'ov-btn small', onclick: () => goPart(p.stem, id),
        }, t('wsx.parts.n', lang, { n: p.n })))),
      ]);
    }
    const d = poolDistribution(ok.map((p) => p.dist));
    return h('div', { class: 'ws-strokes-tab' }, [
      distributionCard({
        d, strokes: ok.reduce((n, p) => n + p.strokes.total, 0), hasStrokes: ok.some((p) => p.strokes.total > 0), tracknet: ok.some((p) => p.tracknet),
      }),
      partsTable(),
    ]);
  }

  function renderWhole() {
    if (state.destroyed) return;
    const head = beginView();
    const parts = state.whole.parts;
    const cards = h('div', { class: 'ws-part-cards' }, parts.map(partCard));
    // Heatmap + scatter per analysed part, captioned "Part N" (images are never merged across parts).
    const move = movementCard({ lang, items: parts.filter((p) => p.ok).map((p) => ({ stem: p.stem, caption: t('wsx.parts.n', lang, { n: p.n }) })) });
    const tabs = buildTabs();
    [cards, move, tabs].forEach((n) => refs.body.appendChild(n));
    sections = [head, cards, move, tabs];
    renderTab();
    startReveal();
  }

  function settle() {
    clearTimeout(settleTimer);
    settleTimer = null;
    root.classList.add('settled');
  }

  function renderSide(announce) {
    const swap = (host, node) => { host.textContent = ''; host.appendChild(node); };
    swap(refs.inspHost, buildInspector());
    swap(refs.sumHost, buildSummary());
    paintVideoTags();
    if (announce && refs.live) {
      const st = currentStroke();
      setText(refs.live, st
        ? [t('ws.stroke_title', lang, { n: st.i, technique: strokeName(st, lang) }), st.score !== null ? num1(st.score) : null].filter(Boolean).join(', ')
        : '');
    }
  }

  function selectUnit(id, seek) {
    settle();
    if (state.unit !== id) {
      state.unit = id;
      state.stripLimit = STROKE_PAGE;
      state.listLimit = STROKE_PAGE;
      state.hitStripLimit = STROKE_PAGE;
      state.hitListLimit = STROKE_PAGE;
      const list = unitStrokes();
      if (!list.some((s) => s.i === state.sel)) state.sel = list.length ? list[0].i : null;
      if (state.dbl) {
        const hits = unitHits();
        if (!hits.some((x) => x.i === state.hitSel)) state.hitSel = hits.length ? hits[0].i : null;
      }
      paintLane();
      renderStrip();
      renderFilters();
      renderRows();
      renderSide(true);
    }
    const u = currentUnit();
    if (seek && u) seekTo(u.start, false);
  }

  function selectStroke(i, from) {
    settle();
    const prev = state.sel;
    state.sel = i;
    if (prev !== i) {
      setPressed(refs.stripBtns && refs.stripBtns.get(prev), false);
      const p = refs.listRows && refs.listRows.get(prev);
      if (p) { p.tr.classList.remove('on'); p.btn.removeAttribute('aria-current'); }
      if (refs.stripBtns && !refs.stripBtns.has(i)) renderStrip();
      else setPressed(refs.stripBtns && refs.stripBtns.get(i), true);
      const visible = state.filter === 'all' || (currentStroke() && currentStroke().label === state.filter);
      if (visible && refs.listRows && !refs.listRows.has(i)) renderRows();
      const n = refs.listRows && refs.listRows.get(i);
      if (n) {
        n.tr.classList.add('on');
        n.btn.setAttribute('aria-current', 'true');
        if (from !== 'list') revealIn(refs.tableWrap, n.tr, 'y');
      }
      renderSide(true);
    }
    const st = currentStroke();
    if (st && st.time !== null) seekTo(st.time, true);
  }

  function stepUnit(dir) {
    const rs = state.model.rallies;
    if (!rs.length) return;
    const pos = rs.findIndex((r) => r.id === state.unit);
    const next = pos < 0 ? (dir > 0 ? 0 : rs.length - 1) : Math.max(0, Math.min(rs.length - 1, pos + dir));
    selectUnit(rs[next].id, true);
  }

  // ----- data -----
  const isAborted = (r) => !!(r && r.error && r.error.kind === 'aborted');

  /** Abort whatever is loading and start a new request group; returns its controller (compare to `ctl` later). */
  function newRequest() {
    if (ctl && typeof ctl.abort === 'function') ctl.abort();
    ctl = typeof AbortController === 'function' ? new AbortController() : {};
    return ctl;
  }
  const reqOpts = (c) => ({ ...LONG, ...(typeof c.abort === 'function' ? { signal: c.signal } : {}) });
  const stale = (mine) => state.destroyed || mine !== ctl;

  /** A stem's /api/matches record, fetched once per visit. */
  async function recordOf(s, o) {
    if (!recs[s]) {
      const r = await soft(api.match(s, o));
      if (isAborted(r)) return r;
      recs[s] = r;
    }
    return recs[s];
  }

  /**
   * Fetch one stem's run files (rally_segments, strokes, metadata, technique, insights, match record; for a doubles
   * run also players.json and hits.json). Never throws: {status: 'ok' | 'notfound' | 'error' | 'aborted', error,
   * ...the soft() results}. The single video and a split match's part both load through it.
   */
  async function fetchStem(s, o) {
    const out = (f) => soft(getJSON(endpoints.output(s, f), o));
    const [rally, strokes, meta, tech, ins, rec] = await Promise.all([
      out('rally_segments.json'), out('strokes.json'), out('metadata.json'),
      soft(getJSON('/api/technique/' + encodeURIComponent(s), o)),
      soft(api.videoInsights(s, o)),
      recordOf(s, o),
    ]);
    if ([rally, strokes, meta, tech, ins, rec].some(isAborted)) return { status: 'aborted' };
    const fileish = [rally, strokes, meta, ins];
    if (fileish.every((r) => !r.ok && r.status === 404)) return { status: 'notfound' };
    if (fileish.every((r) => !r.ok)) return { status: 'error', error: (fileish.find((r) => r.status !== 404) || fileish[0]).error };
    // A doubles run (by the run's own metadata or sidecar, not by the record) also reads players.json and hits.json.
    let pl = null;
    let hi = null;
    if (obj(meta.data).match_format === 'doubles' || obj(ins.data).match_format === 'doubles') {
      [pl, hi] = await Promise.all([soft(api.players(s, o)), soft(api.hits(s, o))]);
      if ([pl, hi].some(isAborted)) return { status: 'aborted' };
    }
    return { status: 'ok', rally, strokes, meta, tech, ins, rec, pl, hi };
  }

  /** Put one stem's fetched data (fetchStem 'ok') into state: models, selection; the tab bodies start over. */
  function applyStem(s, d) {
    state.stem = s;
    state.video = (state.videos || []).find((v) => v && v.name === s) || null;
    // A part's record is the resolved one (its sides already swapped), so the body names each side right.
    state.record = d.rec.ok ? obj(d.rec.data) : {};
    state.insights = d.ins.ok ? obj(d.ins.data) : null;
    state.strokesDoc = d.strokes.ok ? d.strokes.data : null;
    state.tech = d.tech.ok ? d.tech.data : null;
    // 404 = absent; an unreadable file is treated the same (status "No players data"), never as ok.
    state.playersFile = d.pl && d.pl.ok && d.pl.data && typeof d.pl.data === 'object' && !Array.isArray(d.pl.data) ? d.pl.data : null;
    const sides = sidesOf(state.record);
    state.names = sides.names;
    state.athleteSide = sides.athleteSide;
    const fps = obj(obj(d.meta.data).video).fps;
    state.dbl = d.pl || d.hi ? doublesWorkspaceModel({
      playersFile: state.playersFile,
      hits: d.hi && d.hi.ok ? d.hi.data : null,
      insights: state.insights,
      fps: resolveFps(fps, rallyInput(d.rally), state.insights),
      record: state.record,
      athletes: sides.athletes,
      athleteId: sides.athleteId,
      lang,
    }) : null;
    state.model = buildWorkspaceModel({
      rallies: rallyInput(d.rally),
      strokes: state.strokesDoc,
      techReports: state.tech,
      fps,
      insights: state.insights,
      athleteSide: state.athleteSide,
    });
    const m = state.model;
    state.unit = m.rallies.length ? m.rallies[0].id : 'all';
    const first = unitStrokes()[0];
    state.sel = first ? first.i : null;
    state.filter = 'all';
    state.stripLimit = STROKE_PAGE;
    state.listLimit = STROKE_PAGE;
    state.hitStripLimit = STROKE_PAGE;
    state.hitListLimit = STROKE_PAGE;
    const firstHit = unitHits()[0];
    state.hitSel = firstHit ? firstHit.i : null;
  }

  /** Nothing of a video is shown (a part loading or missing, the Whole match). */
  function clearVideoState(s) {
    Object.assign(state, { stem: s, video: null, model: null, dbl: null, strokesDoc: null, tech: null, playersFile: null, insights: null });
  }

  /**
   * Load and show one video: the single match (whole-page state cards) or the selected part of a split match (state
   * cards inside the body, header and switcher kept).
   */
  async function loadPart(s) {
    const mine = newRequest();
    clearVideoState(s);
    if (state.split) renderBodyStatus('loading');
    else renderStatus('loading');
    const res = await fetchStem(s, reqOpts(mine));
    if (stale(mine) || res.status === 'aborted') return;
    if (res.status !== 'ok') {
      if (state.split) renderBodyStatus(res.status, res.error);
      else renderStatus(res.status, res.error);
      return;
    }
    applyStem(s, res);
    if (state.split && s === state.info.primary) state.headInsights = state.insights;
    renderAll();
  }

  /**
   * The lighter per-part load of the Whole match: rally_segments, strokes, metadata, technique and insights. A part
   * whose files all fail comes back with ok false and is left out of the pools; its `reason` is 'notfound' (every
   * failure a 404: not analysed yet) or 'error' (unreadable). Returns null when aborted.
   */
  async function loadPartSummary(p, o, athleteSide) {
    const out = (f) => soft(getJSON(endpoints.output(p.stem, f), o));
    const [rally, strokes, meta, tech, ins] = await Promise.all([
      out('rally_segments.json'), out('strokes.json'), out('metadata.json'),
      soft(getJSON('/api/technique/' + encodeURIComponent(p.stem), o)),
      soft(api.videoInsights(p.stem, o)),
    ]);
    if ([rally, strokes, meta, tech, ins].some(isAborted)) return null;
    const video = (state.videos || []).find((v) => v && v.name === p.stem) || null;
    const fileish = [rally, strokes, meta, ins];
    if (!fileish.some((r) => r.ok)) {
      // Every file 404: not analysed yet; any other failure (5xx, network): the part could not be read.
      return { ...p, video, ok: false, reason: fileish.every((r) => r.status === 404) ? 'notfound' : 'error' };
    }
    const insights = ins.ok ? obj(ins.data) : null;
    const doc = strokes.ok ? strokes.data : null;
    const m = buildWorkspaceModel({ rallies: rallyInput(rally), strokes: doc, fps: obj(obj(meta.data).video).fps, insights });
    // The athlete's side in this part: the primary's, flipped when the part's sides are swapped.
    const side = athleteSide ? (p.swapped ? (athleteSide === 'lower' ? 'upper' : 'lower') : athleteSide) : null;
    return {
      ...p,
      video,
      ok: true,
      insights,
      summary: m.summary,
      strokes: { total: m.summary.strokes.total, uncertain: m.summary.strokes.uncertain },
      dist: strokeDistribution(doc),
      tracknet: trackNetNote(doc),
      techSummary: techniqueSummary(tech.ok ? tech.data : null, { doubles: m.summary.doubles }),
      techIndex: partTechIndex(insights, side),
    };
  }

  /** The Whole match: every part's summary in parallel (once per visit), then the cards, movement and pools. */
  async function loadWhole() {
    const mine = newRequest();
    clearVideoState(state.info.primary);
    if (!state.whole) {
      if (refs.framed) renderBodyStatus('loading');
      else renderStatus('loading');
      const o = reqOpts(mine);
      const side = state.head ? state.head.athleteSide : null;
      const parts = await Promise.all(state.info.parts.map((p) => loadPartSummary(p, o, side)));
      if (stale(mine) || parts.some((p) => p === null)) return;
      state.whole = { parts };
      if (parts[0].ok) state.headInsights = parts[0].insights;
    }
    renderWhole();
  }

  /**
   * Resolve the view from the query (spec section 5): a redirect goes through router.replace (the router then
   * remounts, or calls setQuery for the same stem); otherwise load the single video, a part or the Whole match.
   */
  function showView() {
    const r = resolvePartQuery(state.info, stem, state.query);
    if (r.redirect && ctx.router && typeof ctx.router.replace === 'function') {
      state.waiting = true;
      newRequest();
      ctx.router.replace(r.redirect);
      return;
    }
    state.waiting = false;
    state.view = r.view;
    state.partStem = r.view === 'whole' ? null : r.partStem;
    state.tab = resolveTab(state.query);
    if (r.view === 'whole') loadWhole();
    else loadPart(r.partStem || stem);
    // Both loaders render the switcher's new state synchronously (a loading card or the cached Whole match). A
    // press whose navigate never arrived must not pull focus on a later change (Back, a link) to another part.
    const want = state.focusPart;
    state.focusPart = null;
    if (want !== null && want === currentPart()) focusNode(refs.partBtns && refs.partBtns.get(want));
  }

  /** Read the match record (and a part's primary's), the video list, then show the view the query asks for. */
  async function load() {
    const mine = newRequest();
    const o = reqOpts(mine);
    state.info = null;
    state.whole = null;
    // A (re)load reads every record again (Retry after a failed read must not reuse it).
    Object.keys(recs).forEach((k) => { delete recs[k]; });
    renderStatus('loading');
    // The video list is soft: a failure only hides the Posture report link and the part posters.
    const [rec, vids] = await Promise.all([recordOf(stem, o), soft(api.videos(o))]);
    if (stale(mine) || isAborted(rec) || isAborted(vids)) return;
    state.videos = vids.ok && Array.isArray(vids.data) ? vids.data : null;
    // A part's record names its primary (part_of): the parts list and the header come from the primary's record.
    const partOf = rec.ok ? obj(rec.data).part_of : null;
    let primary = stem;
    let prec = rec;
    if (typeof partOf === 'string' && partOf && partOf !== stem) {
      primary = partOf;
      prec = await recordOf(partOf, o);
      if (stale(mine) || isAborted(prec)) return;
    }
    const precord = prec.ok ? obj(prec.data) : {};
    state.info = matchParts(precord, primary);
    state.split = state.info.split;
    if (state.split) {
      const sides = sidesOf(precord);
      state.head = { record: precord, names: sides.names, athleteSide: sides.athleteSide };
    }
    showView();
  }

  load();

  /**
   * Same route, new query (main.js): a `part` change (or a pending redirect) re-resolves the view in place; a tab
   * change opens that tab; the router is not called again. While the record still loads, load() reads the query.
   */
  function setQuery(query) {
    if (state.destroyed) return;
    const prev = state.query;
    state.query = { ...obj(query) };
    if (!state.info) {
      state.tab = resolveTab(state.query);
      return;
    }
    if (state.waiting || state.query.part !== prev.part) {
      showView();
      return;
    }
    selectTab(resolveTab(state.query), false);
  }

  function destroy() {
    if (state.destroyed) return;
    state.destroyed = true;
    teardown();
    clearTimeout(settleTimer);
    if (ctl && typeof ctl.abort === 'function') ctl.abort();
    if (root.parentNode) root.parentNode.removeChild(root);
  }

  return { destroy, setQuery };
}
