/**
 * Am I doing it? — recent publications against breakthrough findings.
 *
 * The middle clause of what the owner asked for: research what
 * separates the videos that broke out, *check that he is following
 * the same*, then suggest. The first and third had pages. This one
 * did not, so a finding could sit on a dashboard for a month while
 * every video published in that month went the other way.
 *
 * Two sides, because his requirement names other creators first and
 * his own account second:
 *
 *  - **his own breakthroughs**, which can only ever tell him to
 *    repeat what he has already done;
 *  - **other creators' breakthroughs**, which can point at a
 *    strategy his own history never could, and are measured on
 *    people whose audience is not his.
 *
 * The competitor tab is shown even while it is empty. A tab that
 * vanishes when it has nothing to say would hide the most important
 * fact about this project's evidence.
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

const SIDES = [
  ['mine', 'Against my own breakthroughs'],
  ['theirs', "Against other creators' breakthroughs"],
];

export const following = {
  title: 'Am I doing it?',
  // The side lives in the URL rather than in a variable, so the
  // competitor view can be linked to and survives a reload.
  async render(root, params) {
    const against = params && params.get('against') === 'theirs'
      ? 'theirs' : 'mine';
    root.innerHTML = `<h1>Am I doing it?</h1>${spinner()}`;
    const d = await api.adherence(against);
    const s = d.summary || {};
    const findings = d.findings || [];
    const how = d.how_to_read || {};
    const src = d.source || {};

    // Both sides are offered even when one of them is empty. A tab
    // that disappears when it has nothing to say hides the most
    // important fact about this project's evidence: the competitor
    // half is thin, and that is a data problem rather than a
    // finding that nothing about other creators matters.
    const tabs = `<p>${SIDES.map(([k, label]) => `
      <a class="btn${k === against ? ' active' : ''}"
        href="#/following?against=${k}">${label}</a>`).join(' ')}</p>`;

    if (!findings.length) {
      root.innerHTML = `<h1>Am I doing it?</h1>${tabs}
        ${note(esc(s.headline || 'Nothing to check yet.'), 'warn')}
        <p class="muted">${esc(src.why_it_matters || '')}</p>`;
      return;
    }

    root.innerHTML = `
      <h1>Am I doing it?</h1>
      ${tabs}
      <p class="lede">Your last ${d.recent} publications, checked
        against the differences measured on
        ${esc(src.whose || 'your own breakthroughs')}
        (${src.outliers} breakthroughs among ${src.compared}
        publications, ${src.creators} creators).</p>
      ${note(`<strong>${esc(s.headline || '')}</strong>`,
    s.not_following ? 'bad' : 'warn')}
      <p class="muted">${esc(src.why_it_matters || '')}</p>

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
    ${f.source_breakouts ? `<p class="muted technical">The line is
      drawn from ${f.source_breakouts} breakthroughs against
      ${f.source_others} other publications.</p>` : ''}
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
