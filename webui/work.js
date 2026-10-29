/**
 * Collect data — the platform doing its own research.
 *
 * The owner's objection, in his words: "the platform should be the
 * place of research, not coding sessions." The analysis already
 * lived here; the jobs that feed it were scripts someone ran over
 * SSH, which meant every refresh needed me.
 *
 * Three rules this page is built around:
 *
 *  - **Say what it costs before it is pressed.** Free and paid are
 *    separated on screen, not distinguished by a word in a
 *    paragraph. A button that quietly spends is the worst thing
 *    this page could contain.
 *  - **Say how long.** "Run this" and "run this for four hours"
 *    are different decisions, and only one of them should be made
 *    by accident.
 *  - **Say where the answer appears.** A collection that finishes
 *    and changes nothing visible is indistinguishable from one
 *    that failed.
 */

import { api } from './api.js';
import { card, esc, note, spinner } from './components.js';

let timer = null;

export const work = {
  title: 'Collect data',
  async render(root) {
    root.innerHTML = `<h1>Collect data</h1>${spinner()}`;
    await draw(root);
  },
};

async function draw(root) {
  const d = await api.operations();
  const ops = d.operations || [];
  const history = d.history || [];
  const live = history.filter((h) => h.is_live);
  const free = ops.filter((o) => !o.costs_money);
  const paid = ops.filter((o) => o.costs_money);

  root.innerHTML = `
    <h1>Collect data</h1>
    <p class="lede">${esc(d.what_this_is || '')}</p>

    ${live.length ? `<section class="card">
      <h2>Happening now</h2>
      ${live.map(running).join('')}
    </section>` : ''}

    <section class="card">
      <h2>Free</h2>
      <p class="sub">These cost nothing. Run them whenever.</p>
      ${free.map(choice).join('')}
    </section>

    <section class="card">
      <h2>Costs money</h2>
      <p class="sub">Each one says what it costs before you press
        it, and stops at the limit you set.</p>
      ${paid.map(choice).join('')}
    </section>

    ${history.length ? card('Earlier runs', `
      <ul class="plain">${history.filter((h) => !h.is_live)
    .slice(0, 10).map(past).join('')}</ul>`) : ''}`;

  root.querySelectorAll('[data-run]').forEach((btn) => {
    btn.addEventListener('click', () => go(root, btn));
  });
  root.querySelectorAll('[data-stop]').forEach((btn) => {
    btn.addEventListener('click', () => halt(root, btn.dataset.stop));
  });

  // Refresh while something is going, and stop when nothing is.
  clearTimeout(timer);
  if (live.length) {
    timer = setTimeout(() => {
      if (document.body.contains(root)) draw(root);
    }, 4000);
  }
}

function choice(o) {
  const fields = (o.params || []).map((p) => `
    <label class="op-field">${esc(p.label)}
      <input type="number" data-param="${esc(p.key)}"
        value="${p.default}" min="${p.minimum}" max="${p.maximum}">
    </label>`).join('');
  return `<div class="gl-entry" data-op="${esc(o.key)}">
    <h3 class="suggestion">${esc(o.title)}</h3>
    <p>${esc(o.what_it_does)}</p>
    <p class="muted">Takes ${esc(o.how_long)}.${o.cost_note
    ? ` ${esc(o.cost_note)}` : ''}${o.look_at
    ? ` Shows up under &ldquo;${esc(o.look_at)}&rdquo;.` : ''}</p>
    ${(o.needs || []).length ? `<p class="muted">${
    o.needs.map((n) => esc(n)).join(' ')}</p>` : ''}
    <p class="op-row">${fields}
      <button class="btn primary" data-run="${esc(o.key)}"
        ${o.is_running ? 'disabled' : ''}>${
  o.is_running ? 'Already running' : 'Start'}</button></p>
  </div>`;
}

function running(h) {
  const bar = h.percent === null || h.percent === undefined ? ''
    : `<div class="op-bar"><span style="width:${h.percent}%"></span></div>`;
  return `<div class="gl-entry">
    <h3 class="suggestion">${esc(h.title)}</h3>
    <p>${esc(h.progress_note || 'Working')}${h.total
    ? ` &mdash; ${h.done || 0} of ${h.total}` : ''}</p>
    ${bar}
    <p><button class="btn" data-stop="${h.id}">Stop</button></p>
  </div>`;
}

const OUTCOME = {
  ok: 'Finished',
  partial: 'Finished early',
  failed: 'Did not finish',
  cancelled: 'Stopped by you',
};

function past(h) {
  const when = h.finished_at
    ? new Date(h.finished_at).toLocaleString() : '';
  return `<li>
    <b>${esc(h.title)}</b> &mdash; ${esc(OUTCOME[h.status] || h.status)}
    <span class="muted">${esc(when)}</span>
    ${h.cost_usd ? `<span class="muted">· cost $${
  h.cost_usd.toFixed(4)}</span>` : ''}
    ${h.error ? `<br><span class="muted">${esc(h.error)}</span>` : ''}
  </li>`;
}

async function go(root, btn) {
  const entry = btn.closest('[data-op]');
  const params = {};
  entry.querySelectorAll('[data-param]').forEach((input) => {
    params[input.dataset.param] = Number(input.value);
  });
  btn.disabled = true;
  btn.textContent = 'Starting';
  try {
    await api.runOperation({ kind: entry.dataset.op, params });
    await draw(root);
  } catch (err) {
    btn.disabled = false;
    btn.textContent = 'Start';
    entry.insertAdjacentHTML('beforeend',
      note(`Could not start: ${esc(String(err))}`, 'bad'));
  }
}

async function halt(root, id) {
  try {
    await api.stopOperation(id);
  } catch (err) {
    // Already finished between the render and the click. Redrawing
    // shows the truth, which is more useful than an error about it.
  }
  await draw(root);
}
