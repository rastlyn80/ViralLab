/**
 * Am I doing it? — recent publications against the account's own
 * breakthrough findings.
 *
 * The middle clause of what the owner asked for: research what
 * separates the videos that broke out, *check that he is following
 * the same*, then suggest. The first and third had pages. This one
 * did not, so a finding could sit on a dashboard for a month while
 * every video published in that month went the other way.
 *
 * The page is built to resist being flattering, because a page like
 * this is where flattery is easiest and most useless:
 *
 *  - a finding every recent publication already clears is shown as
 *    **no longer separating them**, not as a pass. On this account
 *    that is three findings out of four, and calling them passes
 *    would be the single most misleading thing the page could say;
 *  - there is **no total and no score**. The findings overlap, so
 *    adding them up would count one choice several times;
 *  - the **line** each finding is judged against is printed, so it
 *    can be argued with.
 */

import { api } from './api.js';
import {
  card, dateOnly, esc, note, platformChip, spinner,
} from './components.js';

const VERDICT = {
  following: { label: 'Doing it', cls: 'ok' },
  mixed: { label: 'Sometimes', cls: 'warn' },
  'not following': { label: 'Going against it', cls: 'bad' },
  'not enough measured': { label: 'Too few measured', cls: '' },
};

export const following = {
  title: 'Am I doing it?',
  async render(root) {
    root.innerHTML = `<h1>Am I doing it?</h1>${spinner()}`;
    const d = await api.adherence();
    const s = d.summary || {};
    const findings = d.findings || [];
    const how = d.how_to_read || {};

    if (!findings.length) {
      root.innerHTML = `<h1>Am I doing it?</h1>
        ${note(esc(s.headline || 'Nothing to check yet.'), 'warn')}`;
      return;
    }

    root.innerHTML = `
      <h1>Am I doing it?</h1>
      <p class="lede">Your last ${d.recent} publications, checked
        against the differences measured on your own breakthroughs.</p>
      ${note(`<strong>${esc(s.headline || '')}</strong>`,
    s.not_following ? 'bad' : 'warn')}

      <section class="card">
        <h2>Each finding, against your recent work</h2>
        ${findings.map(finding).join('')}
      </section>

      ${card('Publication by publication', `
        <p class="muted">A publication with no vision analysis is
          unmeasured, not failing &mdash; it counts towards neither
          side.</p>
        ${(d.publications || []).map(pub).join('')}`)}

      ${card('How to read this page', `
        <ul class="plain">
          <li><b>The line.</b> ${esc(how.threshold || '')}</li>
          <li><b>No score.</b> ${esc(how.not_a_score || '')}</li>
          <li><b>Not causes.</b> ${esc(how.not_causal || '')}</li>
          <li><b>Unmeasured.</b> ${esc(how.unmeasured || '')}</li>
        </ul>`)}`;
  },
};

function finding(f) {
  // A finding nothing recent falls on the wrong side of is reported
  // as spent rather than passed. Showing "9 of 9" with a tick would
  // tell the owner he is doing well at something that has stopped
  // being a question.
  const v = f.separates
    ? (VERDICT[f.verdict] || VERDICT.mixed)
    : { label: 'No longer separates your work', cls: '' };
  const counts = f.measured
    ? `${f.followed} of ${f.measured} measured`
    : 'none measured';
  return `<div class="gl-entry">
    <h3 class="suggestion">${esc(f.display_name)}
      <span class="chip ${v.cls}">${esc(v.label)}</span></h3>
    <p>${esc(f.plain || '')}</p>
    <p class="muted">${esc(counts)}${f.unmeasured
    ? `, ${f.unmeasured} not analysed` : ''}.
      Your typical recent value <b>${fmt(f.your_recent_typical)}</b>,
      the line <b>${fmt(f.threshold)}</b>
      (breakthroughs ${fmt(f.breakout_level)},
      everything else ${fmt(f.usual_level)}).</p>
  </div>`;
}

function pub(p) {
  const bits = [];
  if (p.misses.length) {
    bits.push(`<p><b>Goes against:</b> ${esc(p.misses.join(', '))}</p>`);
  }
  if (p.follows.length) {
    bits.push(`<p class="muted"><b>Follows:</b>
      ${esc(p.follows.join(', '))}</p>`);
  }
  if (!p.n_checked) {
    bits.push('<p class="muted">Not analysed yet.</p>');
  }
  return `<div class="gl-entry">
    <h3 class="suggestion">${esc((p.caption || '(no caption)')
    .replace(/\s+/g, ' ').slice(0, 90))}</h3>
    <p class="muted">${dateOnly(p.posted_at)}
      ${platformChip(p.platform)}
      ${p.views === null ? '' : `&middot; ${p.views.toLocaleString()} views`}
      ${p.n_checked ? `&middot; follows ${p.n_follows} of ${p.n_checked}`
    : ''}</p>
    ${bits.join('')}
  </div>`;
}

const fmt = (v) => (v === null || v === undefined ? '—' : String(v));
