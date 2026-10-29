/**
 * What goes with more views — yours, theirs, and the gap.
 *
 * The page the owner actually asked for: what drives views in other
 * creators' videos, and whether he is doing the same. Today it has
 * an answer to half of that, and the honest thing is to show the
 * empty half as empty rather than fill it.
 *
 * Both columns are measured identically — each video against its own
 * creator's other videos, age partialled out, corrected for testing
 * many things at once — so they can be read against each other
 * without a footnote explaining why they cannot.
 *
 * The word "viral" does not appear. Not squeamishness: nothing here
 * predicts reach, and a page headed "what makes you go viral" would
 * be promising something every number on it declines to support.
 */

import { api } from './api.js';
import { card, esc, note, spinner } from './components.js';

export const virality = {
  title: 'What goes with more views',
  async render(root) {
    root.innerHTML = `<h1>What goes with more views</h1>${spinner()}`;
    const d = await api.virality();
    const h = d.how_to_read || {};

    // The caveat used to be the first thing on the page. It is
    // true and it is not an answer, and a page that opens with a
    // disclaimer teaches the reader to scroll past the top.
    // Content first, caveat after it, where it still travels with
    // every number above it.
    root.innerHTML = `
      <h1>What goes with more views</h1>
      ${card('Where they agree', agreement(d))}
      ${note(`<strong>${esc(h.not_causal || '')}</strong>`, 'warn')}

      <section class="card">
        <h2>Your account</h2>
        <p class="sub">${d.mine.n} videos across
          ${d.mine.creators} of your accounts.
          ${d.mine.measurements_tested} measurements tested,
          <b>${d.mine.findings.length} survived correction</b>.</p>
        ${findings(d.mine)}
      </section>

      <section class="card">
        <h2>Other creators</h2>
        <p class="sub">${d.theirs.n} videos across
          ${d.theirs.creators} creators.
          ${d.theirs.measurements_tested || 0} measurements tested,
          <b>${d.theirs.findings.length} survived correction</b>.</p>
        ${d.theirs.findings.length ? findings(d.theirs)
    : note(esc(d.the_gap).replace(/\n\n/g, '<br><br>'), 'warn')}
      </section>

      ${breakout('What your breakthroughs had in common',
    d.my_breakthroughs)}
      ${breakout("What THEIR breakthroughs had in common",
    d.their_breakthroughs)}
      ${byOrigin(d)}
      ${trends(d)}

      ${card('How this is measured', `
        <p>${esc(h.same_test_both_sides || '')}</p>`)}`;
  },
};

function agreement(d) {
  if (d.in_both && d.in_both.length) {
    return `<p>These hold on <b>both</b> your account and other
      creators', which is the strongest thing this page can say:</p>
      <ul class="plain">${d.in_both.map(
    (f) => `<li><b>${esc(f.display_name)}</b>${f.describes
      ? ` &mdash; ${esc(f.describes)}` : ''}</li>`).join('')}</ul>`;
  }
  return `<p class="muted">Nothing yet. A pattern that holds on your
    account <i>and</i> on other creators' is the strongest signal this
    page can produce, and it needs findings on both sides. There are
    none on the competitor side, for the reason given below.</p>`;
}

/**
 * Breakthroughs, asked as a threshold rather than a gradient.
 *
 * A video at ten times its creator's median is a different kind of
 * event from one at 1.2 times, and what separates those two is not
 * what separates average from slightly-above-average. Asking it this
 * way produced the first competitor finding the project has had.
 */
/**
 * Your own music against other people's, asked separately.
 *
 * They are different problems. A Chopin nocturne arrives with its
 * own audience; an original composition has to earn attention from
 * the first bar with nothing borrowed. Averaging them produces a
 * description of whichever half is larger.
 */
function byOrigin(d) {
  const by = d.my_breakthroughs_by_origin;
  if (!by) return '';
  const LABEL = { repertoire: 'Other people’s music',
    own_composition: 'Your own compositions' };
  return `<section class="card">
    <h2>Split by whose music it is</h2>
    <p class="sub">These behave differently and are asked separately.
      Averaging them describes whichever half is larger.</p>
    ${Object.entries(by).map(([kind, b]) => `
      <div class="gl-entry">
        <h3>${esc(LABEL[kind] || kind)}</h3>
        <p class="muted">${b.outliers} of ${b.compared} broke past
          your usual run.</p>
        ${b.differences && b.differences.length
    ? `<ul class="plain">${b.differences.map((x) => `<li>
            <b>${esc(x.display_name)}</b> &mdash; ${esc(x.direction)}
          </li>`).join('')}</ul>`
    : (b.weak_signals && b.weak_signals.length
      ? `${note(`<b>Nothing confirmed.</b>
          ${esc(b.weak_signals_warning)}`, 'warn')}
         <ul class="plain">${b.weak_signals.map((w) => `<li>
            ${esc(w.display_name)} &mdash; ${esc(w.direction)}
            <span class="technical muted">p = ${w.p_value}</span>
          </li>`).join('')}</ul>`
      : `<p class="muted">${esc(b.why_empty || '')}</p>`)}
      </div>`).join('')}
  </section>`;
}

function breakout(title, b) {
  if (!b) return '';
  if (!b.differences || !b.differences.length) {
    return card(title, `<p class="muted">${esc(b.why_empty
      || 'Nothing survived correction.')}</p>`
      + unverified(b) + confounded(b));
  }
  const h = b.how_to_read || {};
  return `<section class="card">
    <h2>${esc(title)}</h2>
    <p class="sub">${b.outliers} of ${b.compared} publications broke
      past their own creator's recent run. These are what they had in
      common.</p>
    <ul class="plain">${b.differences.map((x) => `<li>
      <b>${esc(x.display_name)}</b> &mdash; ${esc(x.direction)}
      (${x.outliers_mean} against ${x.others_mean})
      <span class="technical muted"> q = ${x.q_value},
        ${x.n_outliers} against ${x.n_others}</span>
    </li>`).join('')}</ul>
    <p class="muted">${esc(h.still_not_causal || '')}</p>
    ${unverified(b)}
    ${confounded(b)}
  </section>`;
}

/**
 * Differences that are about the method rather than the video.
 *
 * Shown, and shown as not-findings. On the deep competitor corpus
 * the two largest differences by far were the gap since the
 * previous post — and breaking out is defined as beating a trailing
 * window of earlier posts, so a long gap means an older and smaller
 * audience to beat. The advice that appears to fall out of it, post
 * less often, is exactly what an artefact of the outlier definition
 * would look like.
 *
 * Hidden entirely, the next person measures it again and believes
 * it. Mixed in with the findings, this one would have been the
 * headline.
 */
/**
 * Measurements held back because the QUESTION is unproven.
 *
 * Different from `confounded`, and the difference is what the
 * owner should do about it. A confounded measurement needs a
 * better method. An unverified one needs him to confirm more
 * labels — which is a thing he can actually go and do, so the
 * block says so.
 */
function unverified(b) {
  const rows = b.not_verified_enough || [];
  if (!rows.length) return '';
  return `<div class="not-a-measure">
    <h3>Not counted yet</h3>
    <ul class="plain">${rows.map((x) => `<li>
      <b>${esc(x.display_name)}</b> &mdash; ${esc(x.direction || '')}
      ${x.outliers_mean !== undefined
    ? `(${x.outliers_mean} against ${x.others_mean})` : ''}
      <span class="technical muted">${esc(x.reliability || '')}</span>
    </li>`).join('')}</ul>
    <p class="muted">${esc(b.not_verified_warning || '')}</p>
    <p><a href="#/review/labels">Check uncertain labels</a></p>
  </div>`;
}

function confounded(b) {
  const rows = b.cadence_against_the_baseline || [];
  if (!rows.length) return '';
  return `<div class="not-a-measure">
    <h3>Measured, and not a finding</h3>
    <ul class="plain">${rows.map((x) => `<li>
      <b>${esc(x.display_name)}</b> &mdash; ${esc(x.direction)}
      (${x.outliers_mean} against ${x.others_mean})
      <span class="technical muted"> q = ${x.q_value}</span>
    </li>`).join('')}</ul>
    <p class="muted">${esc(b.cadence_warning || '')}</p>
  </div>`;
}

/**
 * Time trends, kept apart from content on purpose.
 *
 * "Publications before this" is a count of elapsed time wearing a
 * content name, and it came out positive on this account and
 * negative on other creators' - which is what an imperfectly
 * controlled time trend does and what a property of a video does
 * not. Worth knowing, impossible to act on, so it has its own
 * heading.
 */
function trends(d) {
  const rows = [...(d.mine.account_trends || []).map((f) => ['Yours', f]),
    ...(d.theirs.account_trends || []).map((f) => ['Theirs', f])];
  if (!rows.length) return '';
  return `<section class="card">
    <h2>Account trends, not video properties</h2>
    <p class="sub">${esc(d.mine.trends_note
    || d.theirs.trends_note || '')}</p>
    <ul class="plain">${rows.map(([who, f]) => `<li><b>${esc(who)}:</b>
      ${esc(f.display_name)} &mdash;
      ${f.statistics.rho_controlling_for_age > 0 ? 'later' : 'earlier'}
      videos did better</li>`).join('')}</ul>
  </section>`;
}

function findings(side) {
  if (!side.findings.length) {
    return note(esc(side.why_empty || 'Nothing survived correction.'), '');
  }
  return side.findings.map((f) => {
    const s = f.statistics;
    const dir = f.direction === 'positive' ? 'more' : 'less';
    return `<div class="gl-entry">
      <h3>${esc(f.display_name)}
        <span class="chip">${esc(f.grade)}</span></h3>
      <p>${esc(f.plain)}</p>
      <p class="muted">${esc(f.describes)}</p>
      <div class="hilo">
        <div><span class="tag">Direction</span> ${esc(dir)} of it went
          with doing better</div>
        <div><span class="tag">Strength</span>
          ${Math.abs(s.rho_controlling_for_age).toFixed(2)} of a
          possible 1.00</div>
      </div>
      <div class="technical">
        <p class="muted">rho ${s.rho_controlling_for_age} controlling
          for age (${s.rho_uncontrolled} uncontrolled),
          q = ${s.q_value}, n = ${f.n}.</p>
        <ul class="plain">${(f.limitations || []).map(
      (l) => `<li>${esc(l)}</li>`).join('')}</ul>
      </div>
    </div>`;
  }).join('');
}
