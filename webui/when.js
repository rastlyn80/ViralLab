/**
 * When you post — the hours and days, and what they cannot say.
 *
 * The owner asked the chat, on a Monday afternoon, what time he
 * should post in the UK. It said it had no access to posting
 * times. Every publication has carried one since the first
 * collector; nothing had ever looked.
 *
 * The page leads with the caveats rather than burying them,
 * because the shape of this question invites a wrong reading.
 * "Afternoon is your best slot" is a sentence a reader will act
 * on; "afternoon ran 0.09 deviations above your usual and does
 * not separate from the rest" is the same fact and does not read
 * as an instruction.
 *
 * The hours are shown and not tested. Twenty-four tests on a few
 * hundred publications would find an hour whether or not one is
 * there, so the testing happens on four parts of the day and the
 * hourly chart is labelled descriptive.
 */

import { api } from './api.js';
import { bar, esc, spinner } from './components.js';

export const when = {
  title: 'When you post',
  async render(root) {
    root.innerHTML = `<h1>When you post</h1>${spinner()}`;
    const d = await api.whenToPost();

    root.innerHTML = `
      <h1>When you post</h1>
      <p class="lede">${esc(d.headline || '')}</p>

      <section class="card">
        <h2>Parts of the day</h2>
        <p class="muted">Your local time (${esc(d.timezone || '')}),
          across ${d.publications_scored} publications.</p>
        ${(d.parts_of_day || []).map((p) => band(p, p.clock)).join('')}
      </section>

      <section class="card">
        <h2>Days of the week</h2>
        ${(d.days_of_week || []).map((p) => band(p, '')).join('')}
      </section>

      <section class="card">
        <h2>By the hour</h2>
        <p class="muted">${esc(d.by_hour_note || '')}</p>
        ${hours(d.by_hour || [])}
      </section>

      <section class="card">
        <h2>What this cannot tell you</h2>
        <p><b>About your audience.</b> ${esc(d.about_your_audience || '')}</p>
        <p><b>The confound.</b> ${esc(d.the_confound || '')}</p>
        <p><b>Can it be checked on other creators?</b>
          ${esc(d.can_this_be_replicated || '')}</p>
      </section>`;
  },
};

function band(p, clock) {
  const med = p.median_vs_your_usual;
  const chip = p.separates
    ? `<span class="chip ${med > 0 ? 'ok' : 'warn'}">stands out</span>`
    : (p.tested ? '<span class="chip">no different</span>'
      : '<span class="chip">too few to test</span>');
  return `<div class="gl-entry">
    <h3>${esc(p.when)}${clock ? ` <span class="muted">${esc(clock)}</span>`
    : ''} ${chip}</h3>
    <p class="muted">${esc(p.plain || '')}</p>
  </div>`;
}

/**
 * A bar per hour. Width is the share of publications, so the
 * chart reads as "when do I post" first and "how did it go"
 * second — which is the right order, because an hour with four
 * posts behind it should not look like an hour with ninety.
 */
function hours(rows) {
  const most = Math.max(1, ...rows.map((r) => r.publications));
  return `<table class="grid"><thead><tr>
      <th>hour</th><th>posts</th><th>vs your usual</th>
    </tr></thead><tbody>${rows.map((r) => `<tr>
      <td>${String(r.hour).padStart(2, '0')}:00</td>
      <td>${bar(r.publications / most)} ${r.publications}</td>
      <td class="${r.median_vs_your_usual > 0 ? 'ok' : ''}">${
  r.median_vs_your_usual > 0 ? '+' : ''}${r.median_vs_your_usual}</td>
    </tr>`).join('')}</tbody></table>`;
}
