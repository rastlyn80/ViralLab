/**
 * How long — which lengths went with doing better.
 *
 * The owner asked which duration is ideal. The honest answer
 * needed two checks first, and both are on the page because
 * both change how the result should be read.
 *
 * The length stored is the real length — checked against 100
 * actual files, never more than a second out. And length IS
 * already tested by the main analysis and is a finding on
 * neither side, which sounds like the answer and is not: that
 * test can only see "longer is better" or "shorter is better",
 * and a best length in the middle cancels to nothing under it.
 *
 * Hence bands. And hence the headline leading with the null
 * rather than burying it under five rows of numbers that each
 * look like they mean something.
 */

import { api } from './api.js';
import { esc, spinner } from './components.js';

export const howLong = {
  title: 'How long',
  async render(root) {
    root.innerHTML = `<h1>How long</h1>${spinner()}`;
    const d = await api.howLong();

    root.innerHTML = `
      <h1>How long</h1>
      <p class="lede">${esc(d.headline || '')}</p>

      <section class="card">
        <h2>Your videos</h2>
        ${table(d.yours)}
      </section>

      <section class="card">
        <h2>Other creators</h2>
        ${table(d.other_creators)}
        <p class="muted">${esc(d.this_one_can_be_checked_on_others
    || '')}</p>
        ${(d.holds_on_both || []).length
    ? `<p><b>Holds on both:</b> ${esc(d.holds_on_both.join(', '))}.
         That is the strongest thing this can say.</p>`
    : '<p class="muted">Nothing holds on both sides.</p>'}
      </section>

      <section class="card">
        <h2>How to read this</h2>
        <p><b>Why bands and not a trend.</b>
          ${esc(d.why_bands_and_not_a_trend || '')}</p>
        <p><b>Where the bands came from.</b>
          ${esc(d.where_the_bands_came_from || '')}</p>
        <p><b>The confound.</b> ${esc(d.the_confound || '')}</p>
      </section>`;
  },
};

/**
 * One row per band.
 *
 * "Against your usual" is the median robust distance from the
 * run of posts around it, which is the same measure every other
 * page here uses. A band is only called out when it survives
 * being corrected against the other four — otherwise five
 * numbers produce one that looks interesting by arithmetic
 * alone.
 */
function table(bands) {
  if (!bands || !bands.length) return '<p class="muted">Nothing yet.</p>';
  return `<table class="grid"><thead><tr>
      <th>length</th><th>videos</th><th>against the usual</th><th></th>
    </tr></thead><tbody>${bands.map((b) => `<tr>
      <td>${esc(b.clock)}</td>
      <td>${b.publications}</td>
      <td class="${b.median_vs_usual > 0 ? 'ok' : ''}">${
  b.median_vs_usual === null ? '—'
    : (b.median_vs_usual > 0 ? '+' : '') + b.median_vs_usual}</td>
      <td>${b.separates
    ? '<span class="chip ok">stands out</span>'
    : (b.tested ? '<span class="chip">no different</span>'
      : '<span class="chip">too few to test</span>')}</td>
    </tr>`).join('')}</tbody></table>`;
}
