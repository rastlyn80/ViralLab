/**
 * Matched pairs — two of your videos that should have done the
 * same, and did not.
 *
 * The owner's own framing, long before anything was built: what
 * distinguishes "a video that is successful with respect to
 * another which is similar and draws no traffic". ADR 0011 makes
 * it part of the evidence rather than a substitute for the
 * experiment.
 *
 * The page leads with the pairs themselves, not with the
 * statistics. Two captions and two view counts side by side is the
 * thing the owner can check against his own memory of making them,
 * and a reader who disbelieves the pairing will rightly disbelieve
 * everything computed from it.
 */

import { api } from './api.js';
import { card, esc, note, spinner } from './components.js';

export const matchedPairs = {
  title: 'Matched pairs',
  async render(root) {
    root.innerHTML = `<h1>Matched pairs</h1>${spinner()}`;
    const d = await api.matchedPairs();
    const how = d.how_it_works || {};

    if (!d.n_pairs) {
      root.innerHTML = `<h1>Matched pairs</h1>
        ${note(esc(d.why_empty || 'No pairs yet.'), 'warn')}`;
      return;
    }

    root.innerHTML = `
      <h1>Matched pairs</h1>
      <p class="lede">${esc(how.what_a_pair_is || '')}</p>

      <section class="card">
        <h2>What differed between them</h2>
        ${findings(d)}
        <p class="muted">${esc(how.the_test || '')}</p>
      </section>

      ${card(`The pairs (${d.n_pairs})`, `
        <p class="muted">Each video appears in one pair at most, so
          no video votes twice.</p>
        ${(d.pairs || []).slice(0, 25).map(pair).join('')}
        ${d.pairs.length > 25 ? `<p class="muted">${
  d.pairs.length - 25} more.</p>` : ''}`)}

      ${card('What this can and cannot say', `
        <p>${esc(how.why_bother || '')}</p>
        <p><b>${esc(how.still_not_a_cause || '')}</b></p>`)}`;
  },
};

function findings(d) {
  if ((d.differences || []).length) {
    return `<ul class="plain">${d.differences.map((f) => `<li>
      <b>${esc(f.display_name)}</b> &mdash; ${esc(f.plain)}
      <span class="technical muted">q = ${f.q_value}</span>
    </li>`).join('')}</ul>`;
  }
  // Nothing survived. Say which came closest and why that is not
  // the same as "nothing is there" - the two call for opposite
  // responses and an empty page cannot tell them apart.
  const close = d.closest || [];
  return `${note(`<strong>Nothing survived correction.</strong>
    ${esc(d.closest_warning || '')}`, 'warn')}
    ${close.length ? `<h3>Closest</h3><ul class="plain">${close.map(
    (f) => `<li>${esc(f.plain)}
      <span class="technical muted">p = ${f.p_value},
        q = ${f.q_value}</span></li>`).join('')}</ul>` : ''}`;
}

function pair(p) {
  const m = p.matched_on || {};
  const bits = [m.platform === 'tiktok' ? 'TikTok' : 'Instagram',
    m.whose_music === 'own_composition' ? 'your own composition'
      : m.whose_music === 'unstated' ? 'composer not stated'
        : m.whose_music,
    `${m.days_apart} days apart`].filter(Boolean);
  return `<div class="gl-entry">
    <p class="muted">${esc(bits.join(' · '))}</p>
    <div class="pair">
      <div class="pair-side pair-win">
        <div class="pair-n">${p.winner.views.toLocaleString()}</div>
        <div>${esc((p.winner.caption || '(no caption)')
    .replace(/\s+/g, ' ').slice(0, 90))}</div>
      </div>
      <div class="pair-side">
        <div class="pair-n">${p.loser.views.toLocaleString()}</div>
        <div>${esc((p.loser.caption || '(no caption)')
    .replace(/\s+/g, ' ').slice(0, 90))}</div>
      </div>
    </div>
  </div>`;
}
