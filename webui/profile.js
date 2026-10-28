/**
 * What your videos look like — the library described, not judged.
 *
 * 483 publications, 31 questions each, 15,445 measurements. Until
 * this page existed none of it was visible: every other surface
 * reads a different method version, so the data sat in a table
 * nobody queried and the product stayed exactly as vague as before
 * it was collected.
 *
 * Two deliberate refusals:
 *
 *  - **No outcomes anywhere on this page.** Not one view count, not
 *    one skip rate. The moment a count sits beside a performance
 *    number a reader connects them, and that connection belongs on
 *    "What to try next", where it arrives with a grade, a q-value
 *    and its counterexamples.
 *  - **The grade is on every row, in the open.** "55% show a face"
 *    rests on a measurement that cleared a bar set in advance;
 *    "35% are warmly lit" rests on a model's opinion nobody has
 *    checked. Printing both in the same typeface would be the
 *    quietly dishonest thing to do.
 */

import { api } from './api.js';
import { card, esc, note, spinner } from './components.js';

const GRADE = {
  verified: { label: 'checked', cls: 'ok',
    help: 'Measured against labels it was not tuned on.' },
  indicative: { label: "the model's reading", cls: '',
    help: 'Worth seeing. Never counted toward any finding.' },
};

export const visualProfile = {
  title: 'What your videos look like',
  async render(root) {
    root.innerHTML = `<h1>What your videos look like</h1>${spinner()}`;
    const d = await api.visualProfile();
    const h = d.how_to_read || {};

    root.innerHTML = `
      <h1>What your videos look like</h1>
      ${note(`<strong>${d.publications_described} of your videos,
        ${d.questions_shown} questions each.</strong>
        ${esc(h.this_is_description || '')}`, 'warn')}

      ${card('How to read this', `
        <ul class="plain">
          <li><span class="chip ok">checked</span>
            ${esc(h.verified || '')}</li>
          <li><span class="chip">the model's reading</span>
            ${esc(h.indicative || '')}</li>
          <li>${esc(h.withheld || '')}</li>
        </ul>`)}

      ${(d.groups || []).map(group).join('')}`;
  },
};

function group(g) {
  return `<section class="card">
    <h2>${esc(g.title)}</h2>
    ${g.items.map(row).join('')}
  </section>`;
}

function row(it) {
  const g = GRADE[it.reliability] || GRADE.indicative;
  const chip = `<span class="chip ${g.cls}" title="${esc(g.help)}"
    >${esc(g.label)}</span>`;

  const body = it.kind === 'yes_no' ? yesNo(it) : choice(it);
  return `<div class="gl-entry">
    <h3>${esc(it.asks)} ${chip}</h3>
    ${body}
    ${it.interpretive ? `<p class="muted">This one is a reading
      rather than an observation — there is no fact to check it
      against, so it can never be more than the model's view.</p>`
    : ''}
    <div class="technical">
      <p class="muted"><b>${esc(it.feature)}</b> —
        ${esc(it.basis || 'no basis recorded')}</p>
    </div>
  </div>`;
}

function yesNo(it) {
  const pct = Math.round(it.share * 100);
  return `<div class="hilo">
    <div><span class="tag">Yes</span> ${it.yes} of ${it.answered}
      videos <b>(${pct}%)</b></div>
  </div>
  ${bar([{ label: 'yes', n: it.yes, share: it.share },
    { label: 'no', n: it.answered - it.yes, share: 1 - it.share }])}`;
}

function choice(it) {
  return `<p class="muted">${it.answered} videos answered.</p>
    ${bar(it.values)}`;
}

/** A plain proportion bar. No library, no animation, no colour key. */
function bar(values) {
  return `<table class="profilebar"><tbody>${values.map((v) => `
    <tr>
      <td class="nowrap">${esc(v.label)}</td>
      <td style="width:100%">
        <div class="pbar" style="width:${Math.max(1,
    Math.round(v.share * 100))}%"></div>
      </td>
      <td class="right nowrap">${v.n} &middot;
        ${Math.round(v.share * 100)}%</td>
    </tr>`).join('')}</tbody></table>`;
}
