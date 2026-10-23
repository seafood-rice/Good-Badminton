// Training plan tab panel for the match details page: the generated plan by week, an on-court / at-home toggle,
// expandable exercise details and Regenerate. Everything that comes from the plan file (names, descriptions,
// steps, URLs) is set as text or as an attribute value, never parsed as HTML.
import { t } from '../core/i18n.js';
import { getJSON, sendJSON } from '../core/api.js';
import { h } from './frame.js';
import { planSessions, obj, isNum } from '../views/workspace_model.js';

const PLAN_WEEKS = 4;
const POST_TIMEOUT_MS = 60000;

const defaultApi = {
  get: (url) => getJSON(url),
  post: (url, body) => sendJSON('POST', url, body, { timeout: POST_TIMEOUT_MS }),
};

const planUrl = (stem) => '/api/training-plan/' + encodeURIComponent(stem);

// Equipment strings are the plan generator's English wording; zh readers get these translations (unknown: verbatim).
const EQUIP_ZH = {
  racket: '球拍', 'racket, shuttles': '球拍、羽毛球', 'resistance band': '弹力带', 'medicine ball': '药球', dumbbell: '哑铃', none: '无器械',
};

const str = (v) => (typeof v === 'string' ? v : '');

/** A {zh, en} block of lines in the reader's language, falling back to the other one. */
function localList(block, lang) {
  const b = obj(block);
  const first = lang === 'zh' ? b.zh : b.en;
  const second = lang === 'zh' ? b.en : b.zh;
  const pick = Array.isArray(first) && first.length ? first : second;
  return (Array.isArray(pick) ? pick : []).filter((x) => typeof x === 'string' && x);
}

function sessionName(s, lang) {
  const d = obj(s.detail);
  return lang === 'zh' && str(d.name_zh) ? d.name_zh : str(s.name);
}

function phaseText(phase, lang) {
  const key = 'wsx.plan.phase.' + phase;
  const v = t(key, lang);
  return v !== key ? v : str(phase);
}

function difficultyText(d, lang) {
  const key = 'wsx.plan.diff.' + d;
  const v = t(key, lang);
  return v !== key ? v : String(d);
}

const isWebUrl = (u) => /^https?:\/\//i.test(u);

function guideLink(url, lang) {
  if (!isWebUrl(url)) return null;
  const youtube = /^https?:\/\/(www\.)?youtube\.com\/watch\?v=[\w-]{6,}/i.test(url);
  return h('a', { class: 'ws-plan-link', href: url, target: '_blank', rel: 'noopener' }, '▶ ' + t(youtube ? 'wsx.plan.youtube' : 'wsx.plan.guide', lang));
}

function section(title, tag, lines) {
  if (!lines.length) return null;
  return [h('div', { class: 'k-label' }, title), h(tag, { class: 'ws-plan-list' }, lines.map((x) => h('li', null, x)))];
}

function detailBody(session, lang) {
  const d = session.detail;
  if (!d || typeof d !== 'object') return [h('div', { class: 'mv-hint' }, t('wsx.plan.old', lang))];
  const desc = lang === 'zh' ? (str(d.description_zh) || str(d.description)) : (str(d.description) || str(d.description_zh));
  const equip = str(d.equipment) ? (lang === 'zh' ? EQUIP_ZH[d.equipment] || d.equipment : d.equipment) : '';
  return [
    h('div', { class: 'ws-plan-chips' }, [
      str(d.difficulty) ? h('span', { class: 'k-chip' }, difficultyText(d.difficulty, lang)) : null,
      equip ? h('span', { class: 'k-chip' }, equip) : null,
    ]),
    desc ? h('p', { class: 'ws-plan-desc' }, desc) : null,
    section(t('wsx.plan.how', lang), 'ol', localList(d.instructions, lang)),
    section(t('wsx.plan.cues', lang), 'ul', localList(d.coaching_cues, lang)),
    section(t('wsx.plan.mistakes', lang), 'ul', localList(d.common_mistakes, lang)),
    guideLink(str(d.video_url), lang),
  ];
}

function metaText(s, lang) {
  const parts = [t('wsx.plan.meta', lang, { sets: s.sets, reps: s.reps, freq: s.frequency_per_week })];
  if (isNum(s.duration_min) && s.duration_min > 0) parts.push(t('wsx.plan.min', lang, { n: s.duration_min }));
  return parts.join(' · ');
}

/** planPanel({stem, lang, api: {get(url), post(url, body)}}) -> {el, destroy}. */
export function planPanel({ stem, lang, api = defaultApi } = {}) {
  let alive = true;
  let plan = null;
  let loc = 'court';
  let busy = false;
  const note = h('div', { class: 'mv-hint', role: 'status' }, t('state.loading', lang));
  const err = h('div', { class: 'mv-err', role: 'alert' });
  const weeksEl = h('div', { class: 'ws-plan-weeks' });
  const toggle = (id, key) => h('button', {
    type: 'button', class: 'ws-chip', 'data-loc': id, 'aria-pressed': 'false',
    onclick: () => { if (loc !== id) { loc = id; draw(); } },
  }, t(key, lang));
  const courtBtn = toggle('court', 'wsx.plan.court');
  const homeBtn = toggle('home', 'wsx.plan.home');
  const regenBtn = h('button', { type: 'button', class: 'ov-btn small', onclick: () => regenerate() }, t('wsx.plan.regen', lang));
  const bar = h('div', { class: 'ws-plan-bar', hidden: true }, [
    h('div', { class: 'ws-plan-loc', role: 'group' }, [courtBtn, homeBtn]),
    regenBtn,
  ]);
  const root = h('div', { class: 'ws-plan' }, [bar, err, note, weeksEl]);

  function row(entry) {
    const s = entry.session;
    const name = sessionName(s, lang);
    const detail = h('div', { class: 'ws-plan-detail', hidden: true });
    const btn = h('button', { type: 'button', class: 'ws-plan-row', 'aria-expanded': 'false' }, [
      h('span', { class: 'ws-plan-name' }, name),
      h('span', { class: 'ws-plan-meta mono' }, metaText(s, lang)),
    ]);
    btn.addEventListener('click', () => {
      const open = btn.getAttribute('aria-expanded') !== 'true';
      btn.setAttribute('aria-expanded', open ? 'true' : 'false');
      detail.hidden = !open;
      detail.textContent = '';
      if (open) for (const n of detailBody(s, lang).flat()) if (n) detail.appendChild(n);
    });
    return h('div', { class: 'ws-plan-item' }, [btn, detail]);
  }

  function draw() {
    for (const [btn, id] of [[courtBtn, 'court'], [homeBtn, 'home']]) {
      btn.setAttribute('aria-pressed', loc === id ? 'true' : 'false');
      btn.classList.toggle('on', loc === id);
    }
    weeksEl.textContent = '';
    const entries = planSessions(plan, loc);
    note.textContent = entries.length ? '' : t('wsx.plan.nothing', lang);
    let last = null;
    let box = null;
    for (const e of entries) {
      if (box === null || e.week !== last) {
        last = e.week;
        box = h('div', { class: 'ws-plan-week' }, h('div', { class: 'ws-plan-week-head mono' }, t('wsx.plan.week', lang, { n: e.week, phase: phaseText(e.phase, lang) })));
        weeksEl.appendChild(box);
      }
      box.appendChild(row(e));
    }
  }

  function show(p) {
    plan = p;
    bar.hidden = false;
    draw();
  }

  async function regenerate() {
    if (busy) return;
    busy = true;
    regenBtn.disabled = true;
    regenBtn.textContent = t('wsx.plan.regen_busy', lang);
    err.textContent = '';
    let failed = false;
    try {
      const p = await api.post(planUrl(stem), { weeks: PLAN_WEEKS });
      if (!alive) return;
      if (p && Array.isArray(p.weeks)) show(p);
      else failed = true;
    } catch (e) {
      if (!alive) return;
      failed = true;
    }
    busy = false;
    regenBtn.disabled = false;
    regenBtn.textContent = t('wsx.plan.regen', lang);
    if (failed) err.textContent = t('wsx.plan.regen_failed', lang);
  }

  Promise.resolve().then(() => api.get(planUrl(stem))).then((p) => {
    if (!alive) return;
    if (p && Array.isArray(p.weeks)) show(p);
    else note.textContent = t('wsx.plan.none', lang);
  }, (e) => {
    if (!alive) return;
    note.textContent = t(e && e.status === 404 ? 'wsx.plan.none' : 'wsx.plan.failed', lang);
  });

  return { el: root, destroy: () => { alive = false; } };
}
