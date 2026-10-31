/**
 * Openings that worked — yours and other people's, side by side.
 *
 * Shown inside the Make hub, so its own filters are pills
 * rather than tabs: two rows of identical-looking tabs, one for
 * the hub and one for the view, is a navigation puzzle.
 *
 * BLUEPRINT M10's hook library. The generator already uses the
 * best fourteen; this is for reading rather than generating,
 * which is a different job. Seeing twenty questions together is
 * how you decide whether you like asking questions, and no
 * summary statistic does that.
 *
 * Ordered by how far each beat **its own creator's** other
 * videos, not by views. A 400-follower pianist's breakout sits
 * beside a star's, and both earned the place the same way. The
 * view count is shown anyway, because "this worked at two
 * thousand followers" and "this worked at two million" are
 * different facts and you should see which you are reading.
 */

import { api } from './api.js';
import { esc, note, spinner } from './components.js';

const WHOSE = [
  ['all', 'Everyone'],
  ['theirs', 'Other creators'],
  ['mine', 'Mine'],
];

export const openings = {
  title: 'Openings that worked',
  async render(root, params) {
    const whose = params?.get('whose') || 'all';
    const device = params?.get('device') || '';
    root.innerHTML = `<h1>Openings that worked</h1>${spinner()}`;

    const d = await api.openings({ whose, device, limit: 120 });
    const devices = d.devices || {};

    root.innerHTML = `
      <h1>Openings that worked</h1>
      <p class="lede">${esc(d.how_ordered || '')}</p>

      <p class="op-row">
        ${WHOSE.map(([k, label]) => `
          <a class="btn${k === whose ? ' active' : ''}"
            href="#/make?on=openings&whose=${k}${device
  ? `&device=${encodeURIComponent(device)}` : ''}">${esc(label)}</a>`)
    .join('')}
      </p>

      <p class="op-row">
        <a class="btn${device ? '' : ' active'}"
          href="#/make?on=openings&whose=${whose}">Any kind</a>
        ${Object.entries(devices).map(([name, n]) => `
          <a class="btn${name === device ? ' active' : ''}"
            href="#/make?on=openings&whose=${whose}&device=${
  encodeURIComponent(name)}">${esc(name)} <span class="count"
            >${n}</span></a>`).join('')}
      </p>

      ${d.openings.length ? `<section class="card">
        ${d.openings.map(one).join('')}
        <p class="muted">${esc(d.rule || '')}</p>
      </section>`
    : note(`Nothing matches that. Openings can only be read from
        videos whose on-screen text has been measured.`, 'warn')}`;
  },
};

function one(o) {
  return `<div class="gl-entry">
    <h3 class="suggestion">${esc(o.text)}</h3>
    <p class="muted">
      <span class="chip ${o.mine ? 'ok' : ''}">${esc(o.handle)}</span>
      beat ${o.beat}% of their own videos
      &middot; ${o.views.toLocaleString()} views
      &middot; ${esc(o.platform === 'tiktok' ? 'TikTok' : 'Instagram')}
      &middot; ${o.devices.map((x) => esc(x)).join(', ')}
    </p>
  </div>`;
}
