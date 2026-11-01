/**
 * Kept — everything the generator has written.
 *
 * The owner, after using it: *"I just want to make sure that
 * those ideas are actually saved and retained ... otherwise we
 * are just burning credits without retaining"*. He was right.
 * Every call cost money and every result lived until the page was
 * refreshed.
 *
 * The waste is the smaller half. A suggestion nobody recorded can
 * never be connected to what happened when it was used, so
 * nothing could ever learn whether the advice was any good.
 * Saying "I posted this one, it became that video" is the first
 * half of that loop, and it is a button here.
 */

import { api } from './api.js';
import { card, esc, note, spinner } from './components.js';

const STATUS = [
  ['', 'Everything'],
  ['new', 'Not judged'],
  ['kept', 'Worth trying'],
  ['used', 'Posted'],
  ['discarded', 'Binned'],
];

const KINDS = [['', 'All'], ['ideas', 'Ideas'],
  ['hooks', 'Opening text'], ['captions', 'Captions']];

export const saved = {
  title: 'Kept',
  async render(root, params) {
    const status = params?.get('status') || '';
    const kind = params?.get('kind') || '';
    root.innerHTML = `<h1>Kept</h1>${spinner()}`;

    const d = await api.savedSuggestions({ status, kind, limit: 200 });
    const counts = d.counts || {};

    root.innerHTML = `
      <h1>Kept</h1>
      <p class="lede">${esc(d.what_this_is || '')}</p>

      <p class="op-row">${STATUS.map(([k, label]) => `
        <a class="btn${k === status ? ' active' : ''}"
          href="#/make?on=kept&status=${k}&kind=${kind}">${esc(label)}${
  counts[k] ? ` <span class="count">${counts[k]}</span>` : ''}</a>`)
    .join('')}</p>
      <p class="op-row">${KINDS.map(([k, label]) => `
        <a class="btn${k === kind ? ' active' : ''}"
          href="#/make?on=kept&status=${status}&kind=${k}"
        >${esc(label)}</a>`).join('')}</p>

      ${d.items.length ? `<section class="card" id="kept-list">
        ${d.items.map(one).join('')}
      </section>` : note(`Nothing saved yet. Anything you generate
        under &ldquo;Write something&rdquo; is kept here
        automatically.`, 'warn')}

      <p class="muted">Generating has cost
        $${(d.total_spent_usd || 0).toFixed(4)} in total.</p>`;

    root.querySelectorAll('[data-judge]').forEach((btn) => {
      btn.addEventListener('click', () => judge(root, btn));
    });
  },
};

const VERDICT_CHIP = {
  new: '', kept: 'ok', used: 'accent', discarded: '',
};

function one(s) {
  const body = s.body || {};
  const extra = [body.piece, body.device, body.shot, body.why_now]
    .filter(Boolean).map((x) => esc(String(x))).join(' &middot; ');
  return `<div class="gl-entry" data-id="${s.id}">
    <h3 class="suggestion">${esc(s.text)}</h3>
    ${extra ? `<p class="muted">${extra}</p>` : ''}
    ${s.grounded_in
    ? `<p class="muted"><b>From:</b> ${esc(s.grounded_in)}</p>` : ''}
    <p class="muted">
      <span class="chip ${VERDICT_CHIP[s.status] || ''}">${
  esc(s.status === 'new' ? 'not judged' : s.status)}</span>
      ${esc(s.kind)} &middot;
      ${esc(new Date(s.created_at).toLocaleDateString())}
      ${s.used_video_id ? `&middot; posted as video ${
    s.used_video_id}` : ''}
    </p>
    <p class="op-row">
      <button class="btn" data-judge="kept" data-id="${s.id}"
        >Worth trying</button>
      <button class="btn" data-judge="discarded" data-id="${s.id}"
        >Bin it</button>
      <label class="op-field">I posted this &mdash; video id
        <input type="number" data-used="${s.id}" min="1"></label>
      <button class="btn" data-judge="used" data-id="${s.id}"
        >Record it</button>
    </p>
  </div>`;
}

async function judge(root, btn) {
  const id = Number(btn.dataset.id);
  const status = btn.dataset.judge;
  const field = root.querySelector(`[data-used="${id}"]`);
  const usedVideoId = status === 'used' && field && field.value
    ? Number(field.value) : null;
  if (status === 'used' && !usedVideoId) {
    btn.insertAdjacentHTML('afterend',
      note('Put the video id in first.', 'warn'));
    return;
  }
  btn.disabled = true;
  try {
    await api.judgeSuggestion(id, { status, used_video_id: usedVideoId });
    await saved.render(root, new URLSearchParams(
      location.hash.split('?')[1] || ''));
  } catch (err) {
    btn.disabled = false;
    btn.insertAdjacentHTML('afterend',
      note(`Could not save that: ${esc(String(err))}`, 'bad'));
  }
}
