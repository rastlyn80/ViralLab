/**
 * M7 — what to try next, and what finding out would cost.
 *
 * The page is ordered by how quickly something could be *answered*,
 * not by how strong its evidence is, because "what should I do next"
 * and "what is best established" have different answers and only one
 * of them is the question a creator is asking.
 *
 * Three things are deliberately not hidden behind a disclosure:
 *
 *  - **how long it would take**, because an eighteen-month commitment
 *    described as an experiment is a trap, and the number is the main
 *    reason to pick one of these over another;
 *  - **the counterexamples**, because a moderate correlation means
 *    most publications do not follow it, and a page showing only the
 *    supporters would mislead by omission while being literally true;
 *  - **the grade**, because an Observed difference and an Associated
 *    relationship justify very different amounts of effort.
 *
 * Exact statistics stay behind `.technical`, as everywhere else.
 */

import { api } from './api.js';
import {
  card, esc, evidence, note, postLink, spinner,
} from './components.js';

const FEASIBILITY = {
  practical: { label: 'Worth starting', cls: 'ok' },
  slow: { label: 'Slow', cls: 'warn' },
  impractical: { label: 'Not realistic yet', cls: 'bad' },
  unknown: { label: 'Unscoped', cls: '' },
};

const GRADE_HELP = {
  Observed: 'Seen in the data. Nothing measured about whether it helps.',
  Hypothesised: 'A relationship that has not survived correction yet.',
  Associated: 'Survived correction, and controlled for paid promotion.',
  Validated: 'Confirmed by a pre-registered experiment.',
};

export const opportunities = {
  title: 'What to try next',
  async render(root) {
    root.innerHTML = `<h1>What to try next</h1>${spinner()}`;
    const data = await api.opportunities({ collection_id: 2 });
    const s = data.summary || {};
    const items = data.opportunities || [];

    root.innerHTML = `
      <h1>What to try next</h1>
      ${note(`<strong>${esc(s.headline || '')}</strong> ${esc(s.caveat || '')}`,
    'warn')}
      ${card('How to read this page', `
        <ul class="plain">
          <li><b>Worth starting</b> means the experiment could finish
            within nine months at the rate you currently post.</li>
          <li><b>Counterexamples</b> are your own videos that do
            <i>not</i> fit the pattern. Every one of these patterns has
            them; a page without them would be flattering you.</li>
          <li>Nothing here predicts anything, and nothing here has been
            shown to <i>cause</i> anything.</li>
        </ul>`)}
      ${items.length ? items.map(render).join('')
    : note('No opportunity is supported by the evidence yet.')}`;
  },
};

function render(o) {
  const e = o.experiment || {};
  const f = FEASIBILITY[e.feasibility] || FEASIBILITY.unknown;
  const faster = e.faster_alternative;

  return `<section class="card opp">
    <div class="opp-head">
      <span class="chip ${f.cls}">${esc(f.label)}</span>
      <span class="chip" title="${esc(GRADE_HELP[o.grade] || '')}"
        >${esc(o.grade)}</span>
      ${e.months_to_answer != null
    ? `<span class="muted">${esc(String(Math.ceil(
      (faster && faster.months_to_answer) || e.months_to_answer)))}
        months to an answer</span>` : ''}
    </div>

    <h2>${esc(o.approach)}</h2>
    <p class="sub">${esc(o.motivating_pattern)}</p>

    ${card('What the experiment would be', `
      <p>${esc(e.change || '')}</p>
      <p class="muted">${esc(e.feasibility_note || '')}</p>
      ${faster && faster.note
    ? `<p class="muted">${esc(faster.note)}</p>` : ''}
      <p class="muted"><b>Stop rule.</b> ${esc(e.stopping_rule || '')}</p>`,
  { flush: true })}

    <div class="two-up">
      ${examples('Your videos that fit this', o.supporting_examples, o)}
      ${examples('Your videos that do not', o.counterexamples, o)}
    </div>

    ${(o.limitations || []).length ? card('What this does not show',
    `<ul class="plain">${o.limitations.map(
      (l) => `<li>${esc(l)}</li>`).join('')}</ul>`, { flush: true }) : ''}

    <div class="technical">
      ${evidence({
    feature: o.feature,
    outcome: o.outcome || '(no outcome measured)',
    platform: o.platform,
    n: o.n,
    grade: o.grade,
    source: o.source,
    publications_needed: e.publications_needed,
    publications_needed_if_contrasted: faster
      ? faster.publications_needed : null,
    posting_rate_per_month: e.posting_rate_per_month,
    metrics_to_collect: e.metrics_to_collect,
    traceable_video_ids: o.traceable_video_ids,
  }, 'Exact figures')}
    </div>
  </section>`;
}

function examples(title, list, o) {
  if (!list || !list.length) {
    return `<div><h3>${esc(title)}</h3>
      <p class="muted">Not enough publications carry this measurement
        to show examples.</p></div>`;
  }
  return `<div><h3>${esc(title)}</h3>
    <ul class="plain">${list.map((x) => `<li>
      ${x.permalink ? postLink(x.permalink, o.platform, { label: 'Watch' })
    : '<span class="faint">no link</span>'}
      <span class="muted">${esc(x.note)}</span>
    </li>`).join('')}</ul></div>`;
}
