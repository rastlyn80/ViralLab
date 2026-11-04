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

/**
 * How many questions to put on screen at once.
 *
 * The holdout added 333, and the page rendered all 354 in one go:
 * an 8.4 MB screenshot, and on an iPad a scroll with no end. The
 * work is also better in small bites - a labeller twenty minutes
 * into an unbroken wall of yes/no buttons is a labeller whose
 * answers are getting worse.
 *
 * Unanswered first, because the answered ones are done and
 * scrolling past them to find work is the wrong default.
 *
 * Six VIDEOS, which is eighteen questions, after grouping cut
 * three cards per video to one. Twelve made a page nine
 * thousand pixels tall.
 */
const PER_PAGE = 6;

export const labelReview = {
  title: 'Check the uncertain labels',
  async render(root) {
    root.innerHTML = `<h1>Check what the system saw</h1>${spinner()}`;
    const d = await api.labelQueue();
    const all = d.items || [];
    // Unanswered first, holdout before the older queue: the
    // holdout is the one blocking every visual measurement.
    const order = (x) => (x.already_reviewed ? 2 : 0)
      + (x.blind ? 0 : 1) * 0.5;
    const sorted = [...all].sort((a, b) => order(a) - order(b));
    // Blind questions are grouped by video: three questions about
    // one video share one set of frames and one caption, so three
    // cards meant pressing "show the frames" three times and
    // decoding the same images three times. 111 videos, not 333
    // cards.
    const groups = [];
    const byVideo = new Map();
    for (const it of sorted) {
      if (!it.blind) { groups.push({ single: it }); continue; }
      let g = byVideo.get(it.video_id);
      if (!g) {
        g = { video: it, questions: [] };
        byVideo.set(it.video_id, g);
        groups.push(g);
      }
      g.questions.push(it);
    }
    const shown = Number(
      new URLSearchParams(location.hash.split('?')[1] || '')
        .get('show') || PER_PAGE);
    const items = groups.slice(0, shown);
    const more = groups.length - items.length;
    root.innerHTML = `
      <h1>Check what the system saw</h1>
      ${note(`<strong>${d.answered} of ${d.total} done</strong>
        &middot; ${d.publications} videos.
        ${esc(d.why_this_matters || '')}`, 'warn')}
      ${holdout(d.holdout)}
      ${card('What you are being asked', `
        <p>Say <b>what you see</b>. Whether that agrees with what the
          model said is worked out for you and shown afterwards &mdash;
          you are not being asked to agree or disagree with anything.</p>
        <p class="muted">${esc(d.two_kinds_of_evidence || '')}</p>`)}
      <div id="queue">${items.map(
    (g) => (g.single ? row(g.single) : grouped(g))).join('')}</div>
      ${more ? `<p class="op-row"><a class="btn primary"
        href="#/review/labels?show=${shown + PER_PAGE}"
        >Show ${Math.min(PER_PAGE, more)} more</a>
        <span class="muted">${more} still to go</span></p>`
    : '<p class="muted">That is all of them.</p>'}`;

    root.querySelectorAll('[data-answer]').forEach((b) => {
      b.addEventListener('click', () => send(b, root));
    });
    root.querySelectorAll('[data-frames]').forEach((b) => {
      b.addEventListener('click', () => showFrames(b));
    });
  },
};

/**
 * The prospective holdout, and why it is worth twenty minutes.
 *
 * Separated from the count above because it is a different job.
 * The older queue asks him to settle cases a model found hard;
 * this one asks him to write an answer key, and the difference
 * decides whether any visual measurement can ever be promoted.
 */
function holdout(h) {
  if (!h || !h.total) return '';
  const left = h.remaining;
  return card(`Write the answer key &mdash; ${h.answered} of ${h.total}`, `
    <p>${esc(h.what_it_is || '')}</p>
    <p><b>No model answer is shown for these.</b>
      ${esc(h.why_no_model_answer || '')}</p>
    <p class="muted">${h.publications} videos, three questions each.
      ${left ? `${left} to go.` : 'All done &mdash; thank you.'}</p>`);
}

/**
 * One video, its frames once, and every question about it.
 *
 * The first version gave each question its own card. For the
 * holdout that is 333 cards, 333 presses of "show the frames"
 * and 333 decodes of images the page already had — for 111
 * videos. Grouping is the difference between twenty minutes and
 * an hour, and the answers at the end of the long version would
 * not have been worth having anyway.
 */
function grouped(g) {
  const v = g.video;
  const left = g.questions.filter((q) => !q.already_reviewed).length;
  return `<section class="card" id="v-${v.video_id}">
    <div class="opp-head">
      <span class="chip">Answer key</span>
      <span class="chip">nothing suggested</span>
      ${left ? '' : '<span class="chip ok">done</span>'}
    </div>
    ${v.scope_rule ? `<p class="muted"><b>Which frames count.</b>
      ${esc(v.scope_rule)}</p>` : ''}
    ${v.caption ? `<p class="muted">Caption: ${esc(v.caption)}</p>` : ''}
    <p>
      ${v.frames_available
    ? `<button class="btn primary" data-frames="${v.video_id}"
        >Show the frames</button> ` : ''}
      ${v.permalink
    ? postLink(v.permalink, v.platform, { label: 'Watch the post' })
    : ''}
    </p>
    <div class="frames"></div>
    ${g.questions.map(question).join('')}
    <label class="op-field">A note, if any
      <input type="text" data-note="${v.video_id}" size="40"></label>
  </section>`;
}

function question(it) {
  const id = `${it.video_id}-${it.field}`;
  return `<div class="gl-entry">
    <h3>${esc(it.ask)}</h3>
    <p class="muted">${esc(it.rule)}</p>
    <div class="verdicts" data-id="${esc(id)}"
         data-video="${it.video_id}" data-field="${esc(it.field)}">
      ${(it.choices || ['yes', 'no', 'cannot_tell']).map((c) => `
        <button class="btn${it.your_answer === c ? ' active' : ''}"
          data-answer="${esc(c)}">${esc(label(c))}</button>`).join('')}
    </div>
    <div class="result muted" id="r-${esc(id)}">${
  it.already_reviewed ? `You said <b>${esc(label(it.your_answer || ''))}</b>.`
    : ''}</div>
  </div>`;
}

function row(it) {
  const id = `${it.video_id}-${it.field}`;
  const done = it.already_reviewed;
  return `<section class="card" id="q-${esc(id)}">
    <div class="opp-head">
      <span class="chip">${esc(it.cohort === 'own' ? 'Your video'
    : (it.cohort === 'holdout' ? 'Answer key' : 'Another creator'))}</span>
      ${it.blind
    ? '<span class="chip">nothing suggested</span>' : ''}
      ${done ? `<span class="chip ok">you said
        ${esc(label(it.your_answer || ''))}</span>` : ''}
    </div>

    <h2>${esc(it.ask)}</h2>
    <p class="sub">${esc(it.rule)}</p>
    ${it.scope_rule ? `<p class="muted"><b>Which frames count.</b>
      ${esc(it.scope_rule)}</p>` : ''}
    ${it.why_uncertain ? `<p class="muted"><b>Why this one is hard.</b>
      ${esc(it.why_uncertain)}</p>` : ''}
    ${it.your_note ? `<p class="muted"><b>Your note last time.</b>
      ${esc(it.your_note)}</p>` : ''}
    ${it.caption ? `<p class="muted">Caption: ${esc(it.caption)}</p>` : ''}

    <p>
      ${it.frames_available
    ? `<button class="btn" data-frames="${it.video_id}">${
  it.blind ? 'Show the frames' : 'Show the frames the model saw'
}</button> ` : ''}
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
  // A `window.prompt` on every answer is 333 modal dialogs for
  // the holdout, each one stopping the page and asking a question
  // whose honest answer is almost always nothing. The note is a
  // field in the card now, read if it has been filled in.
  const field = btn.closest('section').querySelector('[data-note]');
  const noteText = (field && field.value.trim()) || null;

  box.querySelectorAll('button').forEach((b) => { b.disabled = true; });
  out.textContent = 'Saving…';

  try {
    const r = await api.labelVerdict(box.dataset.video, box.dataset.field, {
      answer: btn.dataset.answer, note: noteText,
    });
    if (r.model_said === null || r.model_said === undefined) {
      // The holdout has no model answer by design, so there is
      // nothing to agree or disagree with. Saying "the model said
      // nothing — you disagree" would be nonsense.
      out.innerHTML = `Recorded: <b>${esc(label(r.you_said))}</b>.
        ${esc(String(r.remaining))} left.`;
    } else {
      const agreed = r.agreement === 'confirmed';
      out.innerHTML = `You said <b>${esc(label(r.you_said))}</b>.
        The model said <b>${esc(label(String(r.model_said)))}</b> &mdash;
        <b>${agreed ? 'you agree' : 'you disagree'}</b>.
        ${esc(String(r.remaining))} left.`;
    }
  } catch (err) {
    out.innerHTML = note(`Could not save: ${esc(String(err))}`, 'bad');
    box.querySelectorAll('button').forEach((b) => { b.disabled = false; });
  }
}
