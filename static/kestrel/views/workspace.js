// Match workspace (#/matches/<stem>, spec 6.5): the annotated video, a single-lane rally timeline, the stroke
// strip and list, the stroke inspector and the rally summary. The pure logic (buildWorkspaceModel and the text
// helpers) lives in workspace_model.js and is re-exported here; mount() is the DOM layer over it. Honesty rules:
// there is no point outcome, so nothing is marked won or lost; swing segments are "segments (estimated)",
// degraded ones "windows"; uncertain strokes are muted and labelled so; opponent strokes are shown but never scored.
import { t, TECHNIQUE_IDS } from '../core/i18n.js';
import { fmtDate, fmtNumber } from '../core/format.js';
import { createClock, local } from '../core/motion.js';
import { formatHash } from '../core/router.js';
import { api, getJSON, endpoints, errorText } from '../core/api.js';
import { h, hasDOM, revealStyle, setText, setAttr, approxMark } from '../widgets/frame.js';
import { swatch, unvalidatedBadge } from '../widgets/doubles.js';
import { createPlayer } from '../widgets/player.js';
import { doublesWorkspaceModel, doublesVideoFiles, hitsInWindow } from './doubles_model.js';
import {
  RALLY_PAD_SEC, JOIN_FRAMES, STROKE_PAGE, summariseRallyPayload, buildWorkspaceModel, matchDate, matchDateApprox,
  listScore, rallyInput, rallyCountText, capItems, readReduced, resolveFps, soft, nameFor, unitKey, signalNote,
  techLabel, strokeName, techCode, metricLabel, clockText, num1, isNum, obj, SIDES,
} from './workspace_model.js';

export {
  RALLY_PAD_SEC, JOIN_FRAMES, STROKE_PAGE, summariseRallyPayload, buildWorkspaceModel, matchDate, matchDateApprox,
  listScore, rallyInput, rallyCountText, capItems, readReduced,
};

const LONG = { timeout: 60000 };
const SETTLE_MS = 2600;

// ---------- DOM ----------

/** Mount the workspace for ctx.params.stem. Returns {destroy()}. */
export function mount(container, ctx = {}) {
  const noop = { destroy() {} };
  if (!container || !hasDOM()) return noop;
  const stem = String(obj(ctx.params).stem || '');
  const lang = ctx.lang === 'en' ? 'en' : 'zh';
  const store = ctx.store && typeof ctx.store.get === 'function' ? ctx.store : null;
  const S = () => (store ? obj(store.get()) : {});

  const state = {
    destroyed: false,
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

  // ----- loading / error / not found -----
  function renderStatus(kind, err) {
    root.textContent = '';
    const card = h('div', { class: 'card ov-state', role: kind === 'loading' ? 'status' : 'alert', 'aria-busy': kind === 'loading' ? 'true' : null });
    if (kind === 'loading') {
      card.appendChild(h('div', { class: 'k-label' }, t('state.loading', lang)));
      card.appendChild(h('div', { class: 'ov-skel' }));
    } else if (kind === 'notfound') {
      card.appendChild(h('div', { class: 'ov-empty-title' }, t('ws.not_found.title', lang)));
      card.appendChild(h('div', { class: 'ov-state-body' }, t('ws.not_found.body', lang, { stem })));
      card.appendChild(h('a', { href: '#/matches', class: 'ov-btn small' }, t('nav.matches', lang)));
    } else {
      card.appendChild(h('div', { class: 'k-label' }, t('error.load.title', lang)));
      card.appendChild(h('div', { class: 'ov-state-body' }, errorText(err, lang)));
      card.appendChild(h('button', { type: 'button', class: 'ov-btn small', onclick: () => load() }, t('btn.retry', lang)));
    }
    root.appendChild(breadcrumb(stem));
    root.appendChild(card);
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
  function titleSides() {
    // The selected athlete first; otherwise lower then upper (the canvas reads "Lower · Upper").
    const a = state.athleteSide || 'lower';
    return [a, a === 'lower' ? 'upper' : 'lower'];
  }

  /** Doubles header: teams title, team-oriented result and sets, the doubles chip and the unvalidated badge. */
  function buildDoublesHead() {
    const m = state.model;
    const d = state.dbl;
    const rec = obj(state.record);
    const ins = obj(state.insights);
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
      h('div', { class: 'ov-actions' }, [
        h('a', { href: formatHash('overview', {}, obj(S().scope)), class: 'ov-btn' }, t('ws.back', lang)),
        h('a', { href: formatHash('results', { stem }, { mode: 'match' }), class: 'ov-btn' }, t('ws.results_link', lang)),
      ]),
    ]);
  }

  function buildHead() {
    if (state.dbl) return buildDoublesHead();
    const m = state.model;
    const [a, b] = titleSides();
    const rec = obj(state.record);
    const res = obj(rec.result);
    const ins = obj(state.insights);
    const dateIso = matchDate(rec, ins);
    const date = fmtDate(dateIso, lang);
    const meta = h('div', { class: 'ws-meta' });
    if (Array.isArray(res.sets) && res.sets.length) {
      const pairs = res.sets.filter((p) => Array.isArray(p) && p.length === 2)
        .map((p) => (a === 'lower' ? `${p[1]}–${p[0]}` : `${p[0]}–${p[1]}`));
      meta.appendChild(h('span', { class: 'mono ws-sets' }, pairs.join(' · ')));
    }
    if (SIDES.includes(res.winner)) {
      if (state.athleteSide) {
        const win = res.winner === state.athleteSide;
        meta.appendChild(h('span', { class: 'k-chip ' + (win ? 'good' : 'bad') }, t(win ? 'ws.result.win' : 'ws.result.loss', lang)));
      } else {
        meta.appendChild(h('span', { class: 'k-chip' }, t('result.side_won', lang, { name: sideName(res.winner) })));
      }
    }
    meta.appendChild(h('span', null, t('ws.summary_line', lang, {
      rallies: rallyCountText(m.summary, lang), strokes: t('scope.strokes_count', lang, { n: fmtNumber(m.summary.strokes.total) }),
    })));
    if (m.summary.label !== 'rallies') {
      meta.appendChild(h('span', { class: 'mono ov-pill' }, t(m.summary.label === 'windows' ? 'tag.windows' : 'tag.swing_est', lang)));
    }
    if (m.summary.doubles) meta.appendChild(h('span', { class: 'k-chip' }, t('doubles.chip', lang)));
    const head = h('div', { class: 'ws-head' }, [
      h('div', { class: 'ws-head-l' }, [
        h('div', { class: 'mono ov-eyebrow' }, [t('ws.eyebrow', lang, { date }),
          matchDateApprox(rec, ins) ? approxMark(lang) : null]),
        h('h1', { class: 'page-title serif ws-title' }, [sideName(a), h('span', { class: 'ws-vs' }, lang === 'zh' ? '对' : 'vs.'), sideName(b)]),
        meta,
        // A doubles run has no per-player score: say so instead of showing one.
        m.summary.doubles ? h('div', { class: 'kw-foot-muted', role: 'note' }, t('reason.doubles_run', lang)) : null,
      ]),
      h('div', { class: 'ov-actions' }, [
        h('a', { href: formatHash('overview', {}, obj(S().scope)), class: 'ov-btn' }, t('ws.back', lang)),
        h('a', { href: formatHash('results', { stem }, { mode: 'match' }), class: 'ov-btn' }, t('ws.results_link', lang)),
      ]),
    ]);
    return head;
  }

  // ----- video -----
  function buildVideo() {
    // detect_<stem>.mp4 is where app.py leaves the browser-compatible transcode (it replaces the raw file);
    // detect_<stem>_h264.mp4 is tried second for runs where the replace step did not happen.
    // A doubles run plays detect_<stem>_players.mp4 first, only when players.json says its render is ok; if that
    // file cannot be played the annotated video follows.
    const files = state.dbl
      ? doublesVideoFiles(stem, state.dbl.identityVideoOk ? { status: 'ok' } : null)
      : [`detect_${stem}.mp4`, `detect_${stem}_h264.mp4`];
    const player = createPlayer({
      files: files.map((f) => endpoints.output(stem, f)),
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

  // ----- render -----
  function renderAll() {
    if (state.destroyed) return;
    root.textContent = '';
    root.classList.remove('settled');
    root.appendChild(breadcrumb(state.dbl ? doublesTitleText() : t('ws.title', lang, { a: sideName(titleSides()[0]), b: sideName(titleSides()[1]) })));
    const head = buildHead();
    root.appendChild(head);
    const video = buildVideo();
    const timeline = buildTimeline();
    const list = buildList();
    refs.inspHost = h('div', { class: 'ws-slot' });
    refs.sumHost = h('div', { class: 'ws-slot' });
    // One persistent polite live region: a short line per selection, not the whole inspector.
    refs.live = h('div', { class: 'ws-sr', role: 'status', 'aria-live': 'polite' });
    const main = h('div', { class: 'ws-main' }, [video, timeline, list]);
    const side = h('aside', { class: 'ws-side', 'aria-label': t('ws.inspector', lang) }, [refs.inspHost, refs.sumHost]);
    root.appendChild(h('div', { class: 'ws-grid' }, [main, side]));
    root.appendChild(refs.live);
    sections = [head, video, timeline, refs.inspHost, list, refs.sumHost];
    paintLane();
    renderStrip();
    renderFilters();
    renderRows();
    renderSide(false);
    clock.start();
    // Entrance animations play once; anything built after this (a new strip, more rows) appears at once.
    clearTimeout(settleTimer);
    settleTimer = setTimeout(settle, SETTLE_MS);
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
  async function load() {
    if (ctl && typeof ctl.abort === 'function') ctl.abort();
    ctl = typeof AbortController === 'function' ? new AbortController() : null;
    const o = { ...LONG, ...(ctl ? { signal: ctl.signal } : {}) };
    renderStatus('loading');
    const out = (f) => soft(getJSON(endpoints.output(stem, f), o));
    const [rally, strokes, meta, tech, ins, rec] = await Promise.all([
      out('rally_segments.json'), out('strokes.json'), out('metadata.json'),
      soft(getJSON('/api/technique/' + encodeURIComponent(stem), o)),
      soft(api.videoInsights(stem, o)),
      soft(api.match(stem, o)),
    ]);
    if (state.destroyed) return;
    const all = [rally, strokes, meta, tech, ins, rec];
    if (all.some((r) => r.error && r.error.kind === 'aborted')) return;
    const fileish = [rally, strokes, meta, ins];
    if (fileish.every((r) => !r.ok && r.status === 404)) {
      renderStatus('notfound');
      return;
    }
    if (fileish.every((r) => !r.ok)) {
      renderStatus('error', (fileish.find((r) => r.status !== 404) || fileish[0]).error);
      return;
    }
    state.record = rec.ok ? obj(rec.data) : {};
    state.insights = ins.ok ? obj(ins.data) : null;
    const s = S();
    const athletes = obj(s.athletes);
    state.names = { upper: nameFor(state.record, 'upper', athletes), lower: nameFor(state.record, 'lower', athletes) };
    const athleteId = obj(s.scope).athlete || s.athlete || '';
    state.athleteSide = athleteId ? SIDES.find((sd) => obj(state.record[sd]).athlete_id === athleteId) || null : null;
    const fps = obj(obj(meta.data).video).fps;
    // A doubles run (by the run's own metadata or sidecar, not by the record) also reads players.json and hits.json.
    state.dbl = null;
    if (obj(meta.data).match_format === 'doubles' || obj(state.insights).match_format === 'doubles') {
      const mine = ctl;
      const [pl, hi] = await Promise.all([soft(api.players(stem, o)), soft(api.hits(stem, o))]);
      if (state.destroyed || mine !== ctl || [pl, hi].some((r) => r.error && r.error.kind === 'aborted')) return;
      state.dbl = doublesWorkspaceModel({
        // 404 = absent; an unreadable file is treated the same (status "No players data"), never as ok.
        playersFile: pl.ok ? pl.data : null,
        hits: hi.ok ? hi.data : null,
        insights: state.insights,
        fps: resolveFps(fps, rallyInput(rally), state.insights),
        record: state.record,
        athletes,
        athleteId,
        lang,
      });
    }
    state.model = buildWorkspaceModel({
      rallies: rallyInput(rally),
      strokes: strokes.ok ? strokes.data : null,
      techReports: tech.ok ? tech.data : null,
      fps,
      insights: state.insights,
      athleteSide: state.athleteSide,
    });
    const m = state.model;
    state.unit = m.rallies.length ? m.rallies[0].id : 'all';
    const first = unitStrokes()[0];
    state.sel = first ? first.i : null;
    state.hitStripLimit = STROKE_PAGE;
    state.hitListLimit = STROKE_PAGE;
    const firstHit = unitHits()[0];
    state.hitSel = firstHit ? firstHit.i : null;
    renderAll();
  }

  load();

  function destroy() {
    if (state.destroyed) return;
    state.destroyed = true;
    clock.cancel();
    clearTimeout(settleTimer);
    if (ctl && typeof ctl.abort === 'function') ctl.abort();
    if (refs.player) refs.player.destroy();
    if (root.parentNode) root.parentNode.removeChild(root);
  }

  return { destroy };
}
