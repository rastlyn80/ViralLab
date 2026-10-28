/**
 * Confirming the labels Claude was unsure about.
 *
 * ## The mistake this version fixes
 *
 * The first version hid the model's label so it could not anchor the
 * judgement - right - and then asked the reviewer to *confirm or
 * overturn* it. Those two decisions contradict each other: you cannot
 * agree with something you have not been shown.
 *
 * The owner's first pass proved it. Four of eight "overturned"
 * answers gave exactly the value the model had already given, and
 * three "confirmed" ones carried notes describing the opposite of
 * what was being confirmed - "the face IS visible" filed as
 * confirming that no face was visible.
 *
 * So the page now asks the only question a reviewer can actually
 * answer: **what do you see?** Agreement is computed on the server by
 * comparing that answer to the stored label, and the model's answer
 * appears immediately afterwards. Nothing is anchored and nothing is
 * ambiguous.
 *
 * Your note from the previous pass is shown beside each question, so
 * a second run is three minutes of clicking rather than retyping.
 */

import { api } from './api.js';
import { card, esc, note, postLink, spinner } from './components.js';

const PRETTY = {
  yes: 'Yes', no: 'No', cannot_tell: 'I cannot tell',
};

function label(choice) {
  return PRETTY[choice] || choice.replace(/_/g, ' ');
}

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
      ${card('What you are being asked', `
        <p>Say <b>what you see</b>. Whether that agrees with what the
          model said is worked out for you and shown afterwards &mdash;
          you are not being asked to agree or disagree with anything.</p>
        <p class="muted">${esc(d.two_kinds_of_evidence || '')}</p>`)}
      <div id="queue">${items.map(row).join('')}</div>`;

    root.querySelectorAll('[data-answer]').forEach((b) => {
      b.addEventListener('click', () => send(b, root));
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
      ${done ? `<span class="chip ok">you said
        ${esc(label(it.your_answer || ''))}</span>` : ''}
    </div>

    <h2>${esc(it.ask)}</h2>
    <p class="sub">${esc(it.rule)}</p>
    ${it.scope_rule ? `<p class="muted"><b>Which frames count.</b>
      ${esc(it.scope_rule)}</p>` : ''}
    <p class="muted"><b>Why this one is hard.</b>
      ${esc(it.why_uncertain)}</p>
    ${it.your_note ? `<p class="muted"><b>Your note last time.</b>
      ${esc(it.your_note)}</p>` : ''}
    ${it.caption ? `<p class="muted">Caption: ${esc(it.caption)}</p>` : ''}

    <p>
      ${it.frames_available
    ? `<button class="btn" data-frames="${it.video_id}">Show the
         frames the model saw</button> ` : ''}
      ${it.permalink
    ? postLink(it.permalink, it.platform, { label: 'Watch the post' })
    : (it.frames_available ? ''
      : `<span class="faint">No link
         ${esc(it.permalink_missing_because || '')}</span>`)}
    </p>
    <div class="frames"></div>

    <div class="verdicts" data-id="${esc(id)}"
         data-video="${it.video_id}" data-field="${esc(it.field)}">
      ${(it.choices || ['yes', 'no', 'cannot_tell']).map((c) => `
        <button class="btn" data-answer="${esc(c)}">${esc(label(c))}
        </button>`).join('')}
    </div>
    <div class="result muted" id="r-${esc(id)}"></div>
  </section>`;
}

async function showFrames(btn) {
  const host = btn.closest('section').querySelector('.frames');
  host.innerHTML = spinner('Decoding');
  try {
    const d = await api.labelFrames(btn.dataset.frames);
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

async function send(btn, root) {
  const box = btn.closest('.verdicts');
  const out = root.querySelector(`#r-${CSS.escape(box.dataset.id)}`);
  const noteText = window.prompt('Anything worth noting? (optional)') || null;

  box.querySelectorAll('button').forEach((b) => { b.disabled = true; });
  out.textContent = 'Saving…';
  try {
    const r = await api.labelVerdict(box.dataset.video, box.dataset.field, {
      answer: btn.dataset.answer, note: noteText,
    });
    const agreed = r.agreement === 'confirmed';
    out.innerHTML = `You said <b>${esc(label(r.you_said))}</b>.
      The model said <b>${esc(r.model_said === null ? 'nothing'
    : label(String(r.model_said)))}</b> &mdash;
      <b>${agreed ? 'you agree' : 'you disagree'}</b>.
      ${esc(String(r.remaining))} left.`;
  } catch (err) {
    out.innerHTML = note(`Could not save: ${esc(String(err))}`, 'bad');
    box.querySelectorAll('button').forEach((b) => { b.disabled = false; });
  }
}
