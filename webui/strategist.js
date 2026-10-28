/**
 * M8 — Ask ViralLab.
 *
 * A chat over the research database, with three things on screen that
 * a chat window normally hides:
 *
 *  - **what it cannot do**, before the first question rather than in
 *    a footnote, because the limits are the reason to trust it;
 *  - **which tools it called**, so an answer can be traced to the
 *    evidence it came from;
 *  - **any number it stated that no tool returned**, marked in the
 *    answer itself. That check runs in code after the model has
 *    finished, so it catches the case where the model did not
 *    cooperate.
 *
 * A blocked answer shows the refusal and says the draft was logged.
 * Hiding the fact that a draft was refused would make the enforcement
 * invisible, and invisible enforcement is indistinguishable from none.
 */

import { api } from './api.js';
import { card, esc, note, spinner } from './components.js';

let conversationId = null;

export const strategist = {
  title: 'Ask ViralLab',
  async render(root) {
    root.innerHTML = `<h1>Ask ViralLab</h1>${spinner()}`;
    const s = await api.strategistStatus();

    root.innerHTML = `
      <h1>Ask ViralLab</h1>
      ${s.available ? '' : note(`<strong>Not running.</strong>
        ${esc(s.why_not || '')}`, 'warn')}
      ${card('What it will not do', `
        <ul class="plain">${(s.what_it_cannot_do || []).map(
    (x) => `<li>${esc(x)}</li>`).join('')}</ul>
        <p class="muted">Spending cap for the chat:
          $${(s.strategy_cap_usd || 0).toFixed(2)}.
          ${s.turns_logged} questions asked so far. Every one is logged
          with the evidence it was answered from.</p>`)}

      <section class="card">
        <label for="q"><b>Your question</b></label>
        <textarea id="q" rows="3" style="width:100%"
          placeholder="How many of my videos show sheet music?"
          ${s.available ? '' : 'disabled'}></textarea>
        <p>
          <button class="btn" id="askbtn"
            ${s.available ? '' : 'disabled'}>Ask</button>
          <span class="muted" id="cost"></span>
        </p>
      </section>
      <div id="thread"></div>`;

    const btn = root.querySelector('#askbtn');
    if (btn) btn.addEventListener('click', () => ask(root));
    const box = root.querySelector('#q');
    if (box) {
      box.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) ask(root);
      });
    }
  },
};

async function ask(root) {
  const box = root.querySelector('#q');
  const thread = root.querySelector('#thread');
  const question = (box.value || '').trim();
  if (!question) return;

  const btn = root.querySelector('#askbtn');
  btn.disabled = true;
  const holder = document.createElement('div');
  holder.innerHTML = `<section class="card">
    <p class="muted">You asked</p><p><b>${esc(question)}</b></p>
    ${spinner('Thinking')}</section>`;
  thread.prepend(holder);

  try {
    const r = await api.strategistAsk({
      question, conversation_id: conversationId,
    });
    conversationId = r.conversation_id;
    holder.innerHTML = render(question, r);
    root.querySelector('#cost').textContent =
      r.refused_before_spending
        ? 'Refused before anything was asked — cost nothing.'
        : `This answer cost $${(r.billed_usd || 0).toFixed(4)}.`;
    box.value = '';
  } catch (err) {
    holder.innerHTML = `<section class="card">
      ${note(`Could not answer: ${esc(String(err))}`, 'bad')}</section>`;
  } finally {
    btn.disabled = false;
  }
}

function render(question, r) {
  const tools = (r.tool_calls || []).map((t) => t.name);
  const unique = [...new Set(tools)];

  return `<section class="card">
    <p class="muted">You asked</p>
    <p><b>${esc(question)}</b></p>

    ${r.blocked
    ? note(`<strong>Refused.</strong> ${esc(r.text || r.refusal || '')}`,
      'warn')
    : `<div class="answer">${marked(r.text || '', r.unverified_numbers)}
       </div>`}

    ${unique.length ? `<p class="muted">Looked it up with:
      ${unique.map((t) => `<code>${esc(t)}</code>`).join(', ')}</p>` : ''}

    ${(r.unverified_numbers || []).length ? note(
    `<strong>Unverified figures.</strong> These numbers do not appear
     in anything the tools returned, so treat them as the model's own:
     ${r.unverified_numbers.map((n) => `<b>${esc(n)}</b>`).join(', ')}.`,
    'warn') : ''}

    ${(r.violations || []).length ? `<div class="technical">
      ${note(`Caught by the rule checks:
        ${r.violations.map((v) => esc(v.kind)).join(', ')}`, '')}
    </div>` : ''}
  </section>`;
}

/** Paragraphs, with any unverified figure marked where it appears. */
function marked(text, unverified) {
  const paras = esc(text).split(/\n\s*\n/).filter(Boolean);
  let html = paras.map((p) => `<p>${p.replace(/\n/g, '<br>')}</p>`).join('');
  for (const n of unverified || []) {
    // Mark it in place, so a reader meets the warning at the number
    // rather than underneath the answer.
    html = html.replace(
      new RegExp(`(?<![\\w.])${n.replace('.', '\\.')}(?![\\w.])`, 'g'),
      `<mark title="not found in any tool result">${n}</mark>`);
  }
  return html;
}
