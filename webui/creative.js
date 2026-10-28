/**
 * Make something — ideas, hooks and captions from this account's own
 * evidence.
 *
 * The page the owner asked for twice. Everything before it measured;
 * this is the first surface that produces something he can use on a
 * Tuesday afternoon.
 *
 * Two things are on screen that a generator normally hides:
 *
 *  - **the brief**, before anything is generated and without
 *    spending. A suggestion is only as good as the evidence behind
 *    it, and a thin brief explains a weak suggestion better than the
 *    suggestion does;
 *  - **what each suggestion is grounded in** — the brief item it
 *    draws on, or the word "taste" when it draws on none. A
 *    suggestion that cannot name its grounding is the model's
 *    opinion about piano videos in general, which is the thing this
 *    is built not to be.
 */

import { api } from './api.js';
import { card, esc, note, spinner } from './components.js';

const KINDS = [
  ['ideas', 'Video ideas'],
  ['hooks', 'Opening text'],
  ['captions', 'Captions'],
];

export const creative = {
  title: 'Make something',
  async render(root) {
    root.innerHTML = `<h1>Make something</h1>${spinner()}`;
    const b = await api.creativeBrief();

    root.innerHTML = `
      <h1>Make something</h1>
      ${b.enough_to_generate ? '' : note(`<strong>Not enough measured
        evidence yet.</strong> Generating anyway would produce advice
        about piano videos in general, which is what this is built not
        to do.`, 'warn')}

      <section class="card">
        <h2>What it will write from</h2>
        <p class="sub">${esc(b.what_this_is)}</p>
        <div class="two-up">
          <div>
            <h3>Your measured patterns (${b.findings.length})</h3>
            <ul class="plain">${b.findings.map((f) => `<li>
              <b>${esc(f.what)}</b> &mdash; ${esc(f.direction)}
            </li>`).join('') || '<li class="muted">None yet.</li>'}</ul>
          </div>
          <div>
            <h3>Your own best openings (${b.best_hooks.length})</h3>
            <ul class="plain">${b.best_hooks.map((h) => `<li>
              <span class="muted">${h.views.toLocaleString()}</span>
              &mdash; ${esc(h.on_screen)}
            </li>`).join('') || '<li class="muted">None yet.</li>'}</ul>
          </div>
        </div>
        ${(b.where_you_go_against_it || []).length ? `
          <h3>Where your recent work goes against this
            (${b.where_you_go_against_it.length})</h3>
          <ul class="plain">${b.where_you_go_against_it.map((g) => `<li>
            <b>${esc(g.what)}</b> &mdash; ${esc(g.direction)} in
            ${esc(g.whose)}, and you do it in
            ${esc(g.you_do_it)}.
          </li>`).join('')}</ul>
          <p class="muted">Suggestions lean towards these, because a
            suggestion about something you already do every time is
            true and useless. They are not instructions &mdash; you
            may have good reasons.</p>` : ''}
        ${(b.agreed_by_both || []).length ? `<p><b>Held on both your
          account and other creators':</b>
          ${esc(b.agreed_by_both.join(', '))}. This is the strongest
          thing the evidence can say.</p>` : ''}
        ${(b.other_creators || []).length ? `
          <h3>What other creators' breakthroughs did
            (${b.other_creators.length})</h3>
          <ul class="plain">${b.other_creators.map((f) => `<li>
            <b>${esc(f.what)}</b> &mdash; ${esc(f.direction)}
            <span class="muted technical">${esc(f.measured_on || '')}</span>
          </li>`).join('')}</ul>
          <p class="muted">Measured on other pianists, whose audience
            is not yours. Worth trying, not established for you.</p>`
    : ''}
        <p class="muted">Repertoire it can draw on:
          ${b.repertoire.map((r) => `${esc(r.composer)}`).join(', ')}.</p>
        <details><summary>What it will not do</summary>
          <ul class="plain">${(b.what_it_will_not_do || []).map(
    (x) => `<li>${esc(x)}</li>`).join('')}</ul></details>
      </section>

      <section class="card">
        <h2>Make</h2>
        <p>
          ${KINDS.map(([k, label]) => `<button class="btn" data-kind="${k}"
            ${b.enough_to_generate ? '' : 'disabled'}>${label}</button>`)
    .join(' ')}
        </p>
        <p><input id="note" style="width:100%" placeholder="Anything specific? e.g. 'for the Liszt sonata', or 'something for my own compositions'"></p>
        <p class="muted" id="cost"></p>
      </section>

      <div id="out"></div>`;

    root.querySelectorAll('[data-kind]').forEach((btn) => {
      btn.addEventListener('click', () => make(root, btn.dataset.kind));
    });
  },
};

async function make(root, kind) {
  const out = root.querySelector('#out');
  const noteText = root.querySelector('#note').value.trim();
  root.querySelectorAll('[data-kind]').forEach((b) => {
    b.disabled = true;
  });
  out.innerHTML = `<section class="card">${spinner('Writing')}</section>`;

  try {
    const r = await api.creativeGenerate({
      kind, count: 6, note: noteText || null,
    });
    root.querySelector('#cost').textContent = r.billed_usd
      ? `That cost $${r.billed_usd.toFixed(4)}.` : '';

    if (r.blocked) {
      out.innerHTML = `<section class="card">
        ${note(`<strong>Nothing to show.</strong> ${esc(r.why || '')}`,
    'warn')}${removed(r)}</section>`;
      return;
    }
    out.innerHTML = `<section class="card">
      <h2>${esc(KINDS.find((k) => k[0] === kind)[1])}</h2>
      ${(r.items || []).map((it) => item(kind, it)).join('')}
      <p class="muted">${esc(r.grounding || '')}</p>
      ${removed(r)}
    </section>`;
  } catch (err) {
    out.innerHTML = `<section class="card">
      ${note(`Could not generate: ${esc(String(err))}`, 'bad')}</section>`;
  } finally {
    root.querySelectorAll('[data-kind]').forEach((b) => {
      b.disabled = false;
    });
  }
}

/**
 * What the rule layer took out, shown rather than hidden.
 *
 * A suggestion removed silently looks like a model that wrote four
 * things when asked for five. Naming the rule and quoting the words
 * lets the owner judge the gate — including when it is wrong, which
 * has already happened once on this project with the strategist
 * over-blocking a perfectly reasonable question.
 */
function removed(r) {
  const v = r.violations || [];
  if (!v.length) return '';
  const RULES = {
    virality_promise: 'promised reach',
    causal_language: 'said one thing causes another',
    invented_statistic: 'quoted a number that is not in the brief',
  };
  return `<details class="muted"><summary>${v.length} suggestion${
    v.length === 1 ? '' : 's'} removed before you saw ${
    v.length === 1 ? 'it' : 'them'}</summary>
    <ul class="plain">${v.map((x) => `<li>
      <b>${esc(RULES[x.kind] || x.kind)}</b>
      ${x.suggestion ? ` &mdash; ${esc(x.suggestion)}` : ''}
      <br><span class="muted">${esc((x.excerpt || '').slice(0, 160))}</span>
    </li>`).join('')}</ul></details>`;
}

function item(kind, it) {
  const ground = it.grounded_in
    ? `<p class="muted"><b>From:</b> ${esc(it.grounded_in)}</p>` : '';
  if (kind === 'hooks') {
    return `<div class="gl-entry">
      <h3 class="suggestion">${esc(it.text || '')}</h3>
      <p class="muted">${esc(it.device || '')}${it.piece
    ? ` &middot; ${esc(it.piece)}` : ''}</p>
      ${ground}</div>`;
  }
  if (kind === 'captions') {
    return `<div class="gl-entry">
      <h3 class="suggestion">${esc(it.first_line || '')}</h3>
      <p>${esc(it.caption || '')}</p>
      <p class="muted">${(it.hashtags || []).map(
    (h) => esc(h.startsWith('#') ? h : `#${h}`)).join(' ')}</p>
      ${ground}</div>`;
  }
  return `<div class="gl-entry">
    <h3 class="suggestion">${esc(it.idea || '')}</h3>
    ${it.piece ? `<p class="muted">${esc(it.piece)}</p>` : ''}
    ${it.shot ? `<p><b>Shot:</b> ${esc(it.shot)}</p>` : ''}
    ${it.why_now ? `<p class="muted">${esc(it.why_now)}</p>` : ''}
    ${ground}</div>`;
}
