/**
 * Confirming the labels Claude was unsure about.
 *
 * This is the shortest path from "the vision results are not
 * independent" to "they are". Twenty-one questions across eighteen
 * publications, each one flagged ambiguous by the labeller itself.
 *
 * Design decisions that matter:
 *
 *  - **The model's answer is hidden until you have answered.** Showing
 *    "Claude said No" above the buttons would turn a judgement into an
 *    agreement test, and agreement is exactly the thing already in
 *    doubt. It is revealed immediately afterwards, because knowing
 *    whether you disagreed is the point of the exercise.
 *  - **"I cannot tell either" is a first-class answer**, not a skip.
 *    It says the question is ambiguous rather than that the model was
 *    wrong, which is a different and useful fact.
 *  - **The rule is on screen every time.** "Can you see the piano
 *    keys" was answered differently by two models and by the labeller
 *    because "a piano is present" is not the same claim, and nobody
 *    should have to remember which they are being asked.
 */

import { api } from './api.js';
import { card, esc, note, postLink, spinner } from './components.js';

export const labelReview = {
  title: 'Check the uncertain labels',
  async render(root) {
    root.innerHTML = `<h1>Check the uncertain labels</h1>${spinner()}`;
    const d = await api.labelQueue();
    const items = d.items || [];
    root.innerHTML = `
      <h1>Check the uncertain labels</h1>
      ${note(`<strong>${d.answered} of ${d.total} done</strong>
        &middot; ${d.publications} videos.
        ${esc(d.why_this_matters || '')}`, 'warn')}
      ${card('Two kinds of evidence', esc(d.two_kinds_of_evidence || ''))}
      <div id="queue">${items.map(row).join('')}</div>`;

    root.querySelectorAll('[data-verdict]').forEach((b) => {
      b.addEventListener('click', () => answer(b, root));
    });
    root.querySelectorAll('[data-frames]').forEach((b) => {
      b.addEventListener('click', () => showFrames(b));
    });
  },
};

function row(it) {
  const id = `${it.video_id}-${it.field}`;
  const done = it.already_reviewed;
  return `<section class="card" id="q-${esc(id)}">
    <div class="opp-head">
      <span class="chip">${esc(it.cohort === 'own' ? 'Your video'
    : 'Another creator')}</span>
      ${done ? `<span class="chip ok">answered: ${esc(it.your_verdict)}
        </span>` : ''}
    </div>

    <h2>${esc(it.ask)}</h2>
    <p class="sub">${esc(it.rule)}</p>
    <p class="muted"><b>Why this one is hard.</b>
      ${esc(it.why_uncertain)}</p>
    ${it.caption ? `<p class="muted">Caption: ${esc(it.caption)}</p>` : ''}

    <p>
      ${it.frames_available
    ? `<button class="btn" data-frames="${it.video_id}">Show the
         four frames the model saw</button>`
    : (it.permalink
      ? postLink(it.permalink, it.platform, { label: 'Watch the post' })
      : `<span class="faint">No link available
         ${esc(it.permalink_missing_because || '')}</span>`)}
      ${it.frames_available && it.permalink
    ? ' ' + postLink(it.permalink, it.platform,
      { label: 'or watch the post' }) : ''}
    </p>
    <div class="frames" id="f-${esc(id)}"></div>

    <div class="verdicts" data-id="${esc(id)}"
         data-video="${it.video_id}" data-field="${esc(it.field)}">
      <button class="btn" data-verdict="confirmed">${it.model_omitted
    ? 'Yes &mdash; right to leave it blank'
    : 'Yes &mdash; as labelled'}</button>
      <button class="btn" data-verdict="overturned">${it.model_omitted
    ? 'No &mdash; it should have a value'
    : 'No &mdash; the label is wrong'}</button>
      <button class="btn" data-verdict="cannot_tell">I cannot tell
        either</button>
    </div>
    <div class="result muted" id="r-${esc(id)}">${done
    ? `You said <b>${esc(it.your_verdict)}</b>. The model
       ${it.model_omitted ? 'left this blank'
    : `said <b>${esc(String(it.model_label))}</b>`}.` : ''}</div>
  </section>`;
}

async function showFrames(btn) {
  const vid = btn.dataset.frames;
  const host = btn.closest('section').querySelector('.frames');
  host.innerHTML = spinner('Decoding');
  try {
    const d = await api.labelFrames(vid);
    host.innerHTML = `<p class="muted">${esc(d.note)}</p>
      <div class="framestrip">${(d.frames || []).map((f) => `
        <figure><img alt="frame at ${f.at_s}s"
          src="data:image/jpeg;base64,${f.jpeg_b64}">
        <figcaption>${f.at_s}s</figcaption></figure>`).join('')}</div>`;
  } catch (err) {
    host.innerHTML = note(`Could not load the frames: ${esc(String(err))}`,
      'bad');
  }
}

async function answer(btn, root) {
  const box = btn.closest('.verdicts');
  const verdict = btn.dataset.verdict;
  const video = box.dataset.video;
  const field = box.dataset.field;
  const out = root.querySelector(`#r-${CSS.escape(box.dataset.id)}`);

  let ownerLabel = null;
  if (verdict === 'overturned') {
    // Overturning without saying what is right records that the model
    // was wrong without recording the truth, which helps nobody.
    ownerLabel = window.prompt(
      'What should the answer be? (yes / no, or the correct value)');
    if (!ownerLabel) return;
  }
  const noteText = window.prompt('Anything worth noting? (optional)') || null;

  box.querySelectorAll('button').forEach((b) => { b.disabled = true; });
  out.textContent = 'Saving…';
  try {
    const r = await api.labelVerdict(video, field, {
      verdict, owner_label: ownerLabel, note: noteText,
    });
    out.innerHTML = `Saved. <b>${esc(r.remaining)}</b> left.`;
  } catch (err) {
    out.innerHTML = note(`Could not save: ${esc(String(err))}`, 'bad');
    box.querySelectorAll('button').forEach((b) => { b.disabled = false; });
  }
}
