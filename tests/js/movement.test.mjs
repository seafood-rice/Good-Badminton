import test from 'node:test';
import assert from 'node:assert/strict';
import { installEnv, removeEnv, tick, everything, env } from './fake_dom.mjs';
import { movementCard } from '../../static/kestrel/widgets/movement.js';
import { isOpen, closeTool } from '../../static/kestrel/widgets/videotools.js';

test('movement: one heatmap + scatter per item, captioned by part; a broken image says not available', () => {
  installEnv();
  try {
    const card = movementCard({ lang: 'en', items: [{ stem: 'p 1', caption: 'Part 1' }, { stem: 'p2', caption: 'Part 2' }] });
    const imgs = everything(card).filter((e) => e.tagName === 'IMG');
    assert.equal(imgs.length, 4);
    assert.ok(imgs[0].getAttribute('src').includes('/api/output/p%201/position_visualizations/heatmaps/match_heatmap.png'));
    assert.ok(everything(card).some((e) => e.textContent && e.textContent.startsWith('Part 2')));
    imgs[1].dispatch('error');
    assert.ok(everything(card).some((e) => e.classList && e.classList.contains('ws-move-missing')));
  } finally { removeEnv(); }
});

test('movement: doubles shows team and player heatmaps', () => {
  installEnv();
  try {
    const card = movementCard({ lang: 'en', items: [{ stem: 'd', caption: null }],
      doubles: { teams: [{ id: 1, file: 'team1.png' }], players: [{ id: 'P1', label: 'Ann', file: 'p1.png' }] } });
    const srcs = everything(card).filter((e) => e.tagName === 'IMG').map((e) => e.getAttribute('src'));
    assert.ok(srcs.some((s) => s.endsWith('/team1.png')));
    assert.ok(srcs.some((s) => s.endsWith('/p1.png')));
  } finally { removeEnv(); }
});

test('movement: a figure opens the enlarge dialog; Escape closes it and focus returns', async () => {
  installEnv();
  try {
    const card = movementCard({ lang: 'en', items: [{ stem: 's', caption: null }] });
    env.doc.body.appendChild(card);
    const fig = everything(card).find((e) => e.tagName === 'BUTTON' && e.classList.contains('ws-move-fig'));
    fig.focus();
    fig.dispatch('click');
    assert.ok(isOpen());
    const big = env.doc.body.querySelector('.ws-img-dialog img');
    assert.ok(big.getAttribute('src').endsWith('/match_heatmap.png'));
    env.doc.body.querySelector('.ws-img-dialog').dispatch('keydown', { key: 'Escape' });
    assert.equal(isOpen(), false);
    assert.equal(env.doc.activeElement, fig);
    await tick();
  } finally { closeTool(); removeEnv(); }
});

test('movement: a figure whose image failed no longer opens the enlarge dialog', async () => {
  installEnv();
  try {
    const card = movementCard({ lang: 'en', items: [{ stem: 's', caption: null }] });
    env.doc.body.appendChild(card);
    const fig = everything(card).find((e) => e.tagName === 'BUTTON' && e.classList.contains('ws-move-fig'));
    fig.querySelector('img').dispatch('error');
    assert.equal(fig.disabled, true);
    assert.ok(fig.classList.contains('missing'), 'no zoom cursor (.ws-move-fig.missing)');
    assert.equal(fig.hasAttribute('aria-label'), false, 'no "enlarge" name for a figure that cannot enlarge');
    fig.dispatch('click');
    assert.equal(isOpen(), false);
    await tick();
  } finally { closeTool(); removeEnv(); }
});

test('movement: doubles heatmap names with "..", a backslash or a leading "/" are skipped', () => {
  installEnv();
  try {
    const card = movementCard({ lang: 'en', items: [{ stem: 'd', caption: null }], doubles: {
      teams: [{ id: 1, file: '../secret.png' }, { id: 2, file: 'team2.png' }],
      players: [
        { id: 'P1', label: 'A', file: 'sub\\p1.png' }, { id: 'P2', label: 'B', file: '/etc/p2.png' },
        { id: 'P3', label: 'C', file: 'a/../p3.png' }, { id: 'P4', label: 'D', file: 'heatmaps/p4.png' },
      ],
    } });
    const srcs = everything(card).filter((e) => e.tagName === 'IMG').map((e) => e.getAttribute('src'));
    assert.equal(srcs.length, 4, 'heatmap + scatter + team2 + P4');
    assert.ok(srcs.some((s) => s.endsWith('/d/team2.png')));
    assert.ok(srcs.some((s) => s.endsWith('/d/heatmaps/p4.png')));
    assert.equal(srcs.some((s) => /secret|p1\.png|p2\.png|p3\.png/.test(s)), false);
  } finally { removeEnv(); }
});
