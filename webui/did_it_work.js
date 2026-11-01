/**
 * Did it work? — the suggestions he posted, and what happened.
 *
 * The Kept tab has had an "I posted this" button since the
 * suggestions table was added, and nothing ever read what it
 * recorded. This is the page that makes the button worth
 * pressing.
 *
 * Deliberately unexciting. The honest reading of a good number
 * here is weak — he chose which suggestions to film, so a win
 * is a win for his taste and the generator together — and the
 * page says that before it shows the count, rather than under
 * it. The verdict text comes from the server, which also owns
 * the threshold below which there is no verdict at all.
 */

import { api } from './api.js';
import { esc, note, spinner } from './components.js';

export const didItWork = {
  title: 'Did it work?',
  async render(root) {
    root.innerHTML = `<h1>Did it work?</h1>${spinner()}`;
    const d = await api.didItWork();
    const waiting = d.waiting_for_a_baseline || [];

    root.innerHTML = `
      <h1>Did it work?</h1>
      <p class="lede">${esc(d.what_this_is || '')}</p>

      <section class="card">
        <h2>${esc(d.verdict || '')}</h2>
        ${d.scored ? `<p class="muted">
          <span class="chip ok">${d.beat_your_usual} beat your
            usual</span>
          <span class="chip">${d.did_not} did not</span>
          ${d.p_value === null ? ''
    : `<span class="chip">p = ${d.p_value}</span>`}
          <span class="chip">${esc(d.grade || '')}</span>
        </p>` : ''}
      </section>

      <section class="card">
        <h2>Why a good result here would not prove much</h2>
        <ul class="plain">${(d.why_this_is_not_proof || [])
    .map((x) => `<li>${esc(x)}</li>`).join('')}</ul>
        <p><b>What it is good for:</b>
          ${esc(d.what_it_is_good_for || '')}</p>
      </section>

      ${d.used && d.used.length ? `<section class="card">
        <h2>Each one</h2>
        ${d.used.map(one).join('')}
      </section>` : ''}

      ${waiting.length ? `<section class="card">
        <h2>Not scored yet (${waiting.length})</h2>
        ${waiting.map(pending).join('')}
      </section>` : ''}

      ${!d.scored && !waiting.length
    ? note(`Nothing to score yet. Under <b>Kept</b>, press
        &ldquo;Record it&rdquo; on a suggestion you posted and put
        in the publication&rsquo;s id.`, 'warn') : ''}`;
  },
};

function one(e) {
  return `<div class="gl-entry">
    <h3 class="suggestion">${esc(e.suggestion)}</h3>
    <p class="muted">
      <span class="chip ${e.beat_your_usual ? 'ok' : ''}">${
  e.beat_your_usual ? 'beat your usual' : 'below your usual'}</span>
      ${esc(e.plain || '')}
    </p>
    <p class="muted">${esc(e.kind)}
      ${e.grounded_in ? `&middot; from ${esc(e.grounded_in)}` : ''}
      &middot; posted as video ${e.video_id}
      ${e.posted_at ? `&middot; ${
    esc(new Date(e.posted_at).toLocaleDateString())}` : ''}
    </p>
  </div>`;
}

function pending(e) {
  return `<div class="gl-entry">
    <h3 class="suggestion">${esc(e.suggestion)}</h3>
    <p class="muted">${esc(e.why_not_scored || '')}</p>
  </div>`;
}
