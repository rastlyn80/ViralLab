/**
 * Before you post — measure a draft while it can still change.
 *
 * Everything else in the product looks backwards at videos that
 * are already out. This is the only page that looks at one in
 * time to do something about it, which is why it is also the one
 * most at risk of overclaiming.
 *
 * So: **no score.** BLUEPRINT M10 writes that constraint into the
 * capability's own row — "a checklist of Validated and Associated
 * features present in a draft, never a score" — because a number
 * out of ten reads as a chance of success and nothing measured
 * here can support that.
 *
 * What goes against the evidence is listed first. A page that
 * opens with everything you got right is a page you stop reading
 * before the useful part.
 */

import { api } from './api.js';
import { card, esc, note, spinner } from './components.js';

const VERDICT = {
  'goes against': { label: 'Worth changing', cls: 'warn' },
  matches: { label: 'Matches', cls: 'ok' },
  'not measured': { label: 'No level to clear', cls: '' },
};

export const draft = {
  title: 'Before you post',
  async render(root) {
    root.innerHTML = `
      <h1>Before you post</h1>
      <p class="lede">Measure a video you have not posted yet
        against what has actually worked — on your account and on
        other creators'.</p>

      <section class="card">
        <h2>The draft</h2>
        <p><label>The video file
          <input type="file" id="d-file" accept="video/*"></label></p>
        <p><label>The caption you are thinking of<br>
          <textarea id="d-caption" rows="4" style="width:100%"
            placeholder="Chopin Nocturne in E flat, op. 9 no. 2..."
          ></textarea></label></p>
        <p class="muted">The file is measured on the server and
          deleted straight afterwards. It never joins the videos the
          findings are built from — a draft has no views, and one
          sitting at the bottom of every ranking would drag the very
          findings it is being judged against.</p>
        <p><button class="btn primary" id="d-go">Check it</button></p>
      </section>

      <div id="d-out"></div>`;

    root.querySelector('#d-go').addEventListener('click',
      () => run(root));
  },
};

async function run(root) {
  const button = root.querySelector('#d-go');
  const out = root.querySelector('#d-out');
  const file = root.querySelector('#d-file').files[0] || null;
  const caption = root.querySelector('#d-caption').value.trim();

  if (!file && !caption) {
    out.innerHTML = note('Give a video, a caption, or both.', 'warn');
    return;
  }
  button.disabled = true;
  button.textContent = 'Measuring';
  out.innerHTML = `<section class="card">${
    spinner(file ? 'Reading the video' : 'Reading the caption')
  }</section>`;

  try {
    const d = await api.checkDraft({ file, caption });
    out.innerHTML = report(d);
  } catch (err) {
    out.innerHTML = `<section class="card">${
      note(`Could not check it: ${esc(String(err))}`, 'bad')}</section>`;
  } finally {
    button.disabled = false;
    button.textContent = 'Check it';
  }
}

function report(d) {
  const s = d.summary || {};
  const checks = d.checks || [];
  const headline = s.goes_against
    ? `${s.goes_against} thing${s.goes_against === 1 ? '' : 's'} worth
       changing, ${s.matches} already matching.`
    : `Nothing here goes against the evidence. ${s.matches} checks
       matched.`;

  return `
    ${note(`<strong>${esc(headline)}</strong>`,
    s.goes_against ? 'warn' : 'ok')}

    <section class="card">
      <h2>What the evidence says about this draft</h2>
      ${checks.length ? checks.map(line).join('')
    : '<p class="muted">Nothing could be checked.</p>'}
      <p class="muted">${esc(d.never_a_score || '')}</p>
    </section>

    ${(d.could_not_measure || []).length ? card('Not measured', `
      <ul class="plain">${d.could_not_measure.map(
    (m) => `<li>${esc(m)}</li>`).join('')}</ul>
      <p class="muted">Unmeasured is not the same as wrong.</p>`) : ''}

    ${card('What was measured', `
      <table><tbody>${Object.entries(d.measured || {})
    .map(([k, v]) => `<tr><td>${esc(k)}</td>
      <td class="right mono">${esc(String(v))}</td></tr>`)
    .join('')}</tbody></table>`, { flush: false })}`;
}

function line(c) {
  const v = VERDICT[c.verdict] || VERDICT.matches;
  return `<div class="gl-entry">
    <h3 class="suggestion">${esc(c.what)}
      <span class="chip ${v.cls}">${esc(v.label)}</span></h3>
    <p>${esc(c.detail)}</p>
    <p class="muted">${esc(c.evidence)}</p>
  </div>`;
}
