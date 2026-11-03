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

    ${needs(d.needs || {})}

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

    ${(d.automatic || []).length ? card('Happens on its own', `
      <p class="sub">${esc(d.automatic_note || '')}</p>
      <ul class="plain">${d.automatic.map((a) => `<li>
        <b>${esc(a.title)}</b>
        <span class="muted">every ${a.every_hours} hours &middot;
          ${a.last_finished
    ? `last ran ${new Date(a.last_finished).toLocaleString()}`
    : 'not run yet'}</span>
      </li>`).join('')}</ul>`) : ''}

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

/**
 * One control per parameter, drawn from the `kind` the server
 * sends rather than assumed to be a number.
 *
 * Every parameter was an integer until the owner asked how to
 * add creators he already admires, which needs a handle. The
 * server derives `kind` from the parameter's type, so a new kind
 * of parameter cannot be added without this learning to draw it.
 */
function field(p) {
  const label = `${esc(p.label)}${p.help
    ? ` <span class="muted">${esc(p.help)}</span>` : ''}`;
  if (p.kind === 'text') {
    return `<label class="op-field">${label}
      <input type="text" data-param="${esc(p.key)}" data-text="1"
        placeholder="${esc(p.default || '')}" size="18">
    </label>`;
  }
  if (p.kind === 'choice') {
    return `<label class="op-field">${label}
      <select data-param="${esc(p.key)}" data-text="1">
        ${(p.options || []).map((x) => `<option value="${esc(x)}"${
  x === p.default ? ' selected' : ''}>${esc(x)}</option>`).join('')}
      </select>
    </label>`;
  }
  return `<label class="op-field">${label}
    <input type="number" data-param="${esc(p.key)}"
      value="${p.default}" min="${p.minimum}" max="${p.maximum}">
  </label>`;
}

function choice(o) {
  const fields = (o.params || []).map(field).join('');
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

/**
 * What the evidence is short of, and which button fixes it.
 *
 * The owner asked how he could feed the system more work by
 * other creators. Any paragraph I write to answer that is stale
 * by the second reading, so the server works it out from the
 * corpus and this renders it.
 *
 * The headline number is not "how many creators do we follow" —
 * it is how many are deep enough to count. A creator with seven
 * posts is in the database and in no finding, because every
 * result compares a video against the others on the same
 * account. That distinction was invisible, and it is the whole
 * of what makes collection worth doing.
 */
function needs(n) {
  if (!n.creators_followed) return '';
  const chip = (w) => {
    if (w === 'blocking') {
      return ' <span class="chip warn">nothing paid can run</span>';
    }
    if (w === 'cannot run') {
      return ' <span class="chip warn">cannot run yet</span>';
    }
    return w === 'high' ? ' <span class="chip ok">worth it now</span>' : '';
  };
  const jobs = (n.next || []).map((j) => `<li>
    <b>${esc(j.do)}</b>${chip(j.worth)}
    <br><span class="muted">${esc(j.because)}</span>
    ${j.blocked_because
    ? `<br><span class="warn">${esc(j.blocked_because)}</span>` : ''}
  </li>`).join('');
  return `<section class="card">
    <h2>Where the evidence is thin</h2>
    <p class="op-row">
      <span class="chip ok">${n.creators_that_count} creators count</span>
      <span class="chip ${n.creators_too_thin ? 'warn' : ''}">${
  n.creators_too_thin} too thin to count</span>
      <span class="chip">${n.publications_watched} of ${
  n.publications_total} videos watched</span>
    </p>
    <p class="muted">${esc(n.how_to_read || '')}</p>
    <ul class="plain">${jobs}</ul>
  </section>
  ${n.provider && n.provider.exhausted ? note(`<b>Collecting is
    paused.</b> Your data provider has reached the monthly limit set
    on your own account there &mdash;
    $${n.provider.used_usd} of $${n.provider.limit_usd} used. Every
    button below that costs money will fail until you raise it or
    their month resets. Nothing has been charged.`, 'warn') : ''}`;
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
  partial: 'Stopped early',
  failed: 'Did not finish',
  cancelled: 'Stopped by you',
};

/**
 * One past run, with what actually happened to it.
 *
 * `progress_note` is shown for anything that did not simply
 * finish, because "Stopped early" on its own tells him nothing
 * he can act on. The run that prompted this took ten seconds,
 * collected nothing because the platform was throttling us, and
 * said "Finished" — five times in a row.
 */
function past(h) {
  const when = h.finished_at
    ? new Date(h.finished_at).toLocaleString() : '';
  const why = h.error || (h.status === 'ok' ? '' : h.progress_note);
  return `<li>
    <b>${esc(h.title)}</b> &mdash; <span class="${
  h.status === 'partial' ? 'warn' : ''}">${
  esc(OUTCOME[h.status] || h.status)}</span>
    <span class="muted">${esc(when)}</span>
    ${h.cost_usd ? `<span class="muted">· cost $${
  h.cost_usd.toFixed(4)}</span>` : ''}
    ${why ? `<br><span class="muted">${esc(why)}</span>` : ''}
  </li>`;
}

async function go(root, btn) {
  const entry = btn.closest('[data-op]');
  const params = {};
  entry.querySelectorAll('[data-param]').forEach((input) => {
    params[input.dataset.param] = input.dataset.text
      ? input.value.trim() : Number(input.value);
  });
  btn.disabled = true;
  btn.textContent = 'Starting';
  try {
    await api.runOperation({ kind: entry.dataset.op, params });
    await draw(root);
  } catch (err) {
    btn.disabled = false;
    btn.textContent = 'Start';
    // `detail` is the server's own sentence, written for him -
    // "Their handle does not look right" names the field to fix.
    // `String(err)` would prefix it with "Error:" and help nobody.
    entry.insertAdjacentHTML('beforeend',
      note(`Could not start: ${esc(err.detail || String(err))}`, 'bad'));
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
