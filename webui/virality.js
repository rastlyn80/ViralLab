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

    root.innerHTML = `
      <h1>What goes with more views</h1>
      ${note(`<strong>${esc(h.not_causal || '')}</strong>`, 'warn')}

      ${card('Where they agree', agreement(d))}

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

      ${card('How this is measured', `
        <p>${esc(h.same_test_both_sides || '')}</p>`)}`;
  },
};

function agreement(d) {
  if (d.in_both && d.in_both.length) {
    return `<p>These hold on <b>both</b> your account and other
      creators', which is the strongest thing this page can say:</p>
      <ul class="plain">${d.in_both.map(
    (f) => `<li><b>${esc(f)}</b></li>`).join('')}</ul>`;
  }
  return `<p class="muted">Nothing yet. A pattern that holds on your
    account <i>and</i> on other creators' is the strongest signal this
    page can produce, and it needs findings on both sides. There are
    none on the competitor side, for the reason given below.</p>`;
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
