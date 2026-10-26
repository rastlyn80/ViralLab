/**
 * AI models, routing policy and per-video analysis planning.
 *
 * The one thing every screen here must answer, above all others: **did my
 * media leave this machine, and what did it cost?** So `hosting` is shown as
 * a prominent badge everywhere a provider appears, a plan states its cost
 * before anything runs, and a paid request requires an explicit, itemised
 * confirmation.
 *
 * Providers that cannot run are shown rather than hidden, with the reason.
 * "No local option exists for this task" and "the local option is not
 * installed" look identical if you only list what works, and they call for
 * opposite responses.
 */

import { api } from './api.js';
import {
  card, dateTime, empty, esc, note, num, spinner, toast,
} from './components.js';

const HOST_CHIP = (h) => (h === 'local'
  ? '<span class="chip ok" title="Runs on your own machine. Nothing is '
    + 'uploaded and nothing is billed."><span class="dot"></span>local</span>'
  : '<span class="chip warn" title="Sends data to a third party and may '
    + 'cost money."><span class="dot"></span>cloud</span>');

const yesno = (v) => (v === null || v === undefined
  ? '<span class="unknown"></span>' : (v ? 'yes' : 'no'));

export const aiModels = {
  title: 'AI models',
  async render(root) {
    root.innerHTML = spinner('Loading providers');
    const [providers, policy, task] = await Promise.all([
      api.aiProviders(), api.aiPolicy(), api.aiTask(),
    ]);

    const anyCloudAuthorised = policy.categories.some(
      (c) => c.authorised && c.category.startsWith('cloud'));

    root.innerHTML = `
      <h1>AI models and routing</h1>
      <p class="lede">ViralLab is not tied to one model vendor. These
        providers are not interchangeable, and the differences below are
        read from each vendor's own documentation rather than assumed.</p>

      ${note(`<strong>Mode: <code>${esc(policy.mode)}</code>.</strong>
        ${esc(policy.note)}`, policy.mode === 'local_only' ? 'ok' : 'warn')}
      ${anyCloudAuthorised ? '' : note(`No cloud analysis category is
        authorised, so nothing here can spend money. A category with no
        configured cap is <b>not</b> authorised — an absent setting is
        never read as "unlimited".`, 'accent')}

      ${card('Spending categories', categoryTable(policy))}
      ${card('Providers', providerTable(providers))}
      ${card('What the visual task asks for', taskBlock(task))}`;
  },
};

function categoryTable(policy) {
  return `<table><thead><tr>
      <th>Category</th><th class="num">Cap</th>
      <th class="num">Committed</th><th class="num">Remaining</th>
      <th>Status</th></tr></thead><tbody>
    ${policy.categories.map((c) => `<tr>
      <td><code>${esc(c.category)}</code></td>
      <td class="num">$${c.cap_usd.toFixed(2)}</td>
      <td class="num">$${c.committed_usd.toFixed(4)}</td>
      <td class="num">$${c.remaining_usd.toFixed(2)}</td>
      <td>${c.authorised
    ? '<span class="chip ok"><span class="dot"></span>authorised</span>'
    : '<span class="chip"><span class="dot"></span>not authorised</span>'}
        ${c.note ? `<div class="muted">${esc(c.note)}</div>` : ''}</td>
    </tr>`).join('')}</tbody></table>
    <p class="muted">Overall monthly cap:
      <b>$${policy.monthly_cap_usd.toFixed(2)}</b>. Each category is bounded
      by its own cap and by that total. Discovery and cloud analysis are
      separately authorised because they are different kinds of spending.</p>`;
}

function providerTable(providers) {
  return `<div class="card flush" style="margin:-15px"><table>
    <thead><tr>
      <th>Provider</th><th>Where</th><th>Available</th>
      <th>Accepts</th><th class="num">Max images</th>
      <th class="num">$/Mtok in</th><th>Verified</th>
    </tr></thead><tbody>
    ${providers.map((p) => `<tr>
      <td><b>${esc(p.provider)}</b>
        <div class="muted mono">${esc(p.model)}</div></td>
      <td>${HOST_CHIP(p.hosting)}</td>
      <td>${p.available
    ? '<span class="chip ok"><span class="dot"></span>ready</span>'
    : '<span class="chip warn"><span class="dot"></span>no</span>'}</td>
      <td class="muted">${esc((p.accepts || []).join(', '))}
        ${p.accepts_video_natively
    ? '<div><span class="chip accent"><span class="dot"></span>native video'
      + '</span></div>' : ''}</td>
      <td class="num">${p.max_images_per_request === null ? '—'
    : num(p.max_images_per_request)}</td>
      <td class="num">${p.usd_per_mtok_in === null ? '—'
    : `$${p.usd_per_mtok_in}`}</td>
      <td class="muted">${esc(p.verified_on || '—')}
        ${p.source_url ? `<br><a href="${esc(p.source_url)}"
          target="_blank" rel="noreferrer">docs</a>` : ''}</td>
    </tr>
    <tr><td colspan="7" class="muted">
      ${p.available ? '' : `<b>Unavailable:</b> ${esc(
    p.unavailable_reason || '')}<br>`}
      ${(p.documented_limits || []).length
    ? `<b>Documented limits:</b> ${p.documented_limits.map(
      (l) => esc(l)).join(' · ')}` : ''}
      ${(p.notes || []).length
    ? `<br><b>Notes:</b> ${p.notes.map((n) => esc(n)).join(' · ')}` : ''}
    </td></tr>`).join('')}
    </tbody></table></div>`;
}

function taskBlock(task) {
  return `
    <p>Frames sampled: <b>${task.hook_frames}</b> across the first
      ${task.hook_seconds}s, plus later frames at
      ${task.later_offsets_s.join('s, ')}s — so a single frame can never
      stand in for the whole video, and the model can say whether the
      opening is representative.</p>
    ${note(`<strong>What this task will not answer.</strong>
      <ul style="margin:6px 0 0 18px">${task.refusals.map(
    (r) => `<li>${esc(r)}</li>`).join('')}</ul>`, 'warn')}
    <details style="margin-top:10px"><summary>Response schema
      (${Object.keys(task.schema.properties || {}).length} fields)</summary>
      <pre class="mono" style="max-height:340px;overflow:auto">${
  esc(JSON.stringify(task.schema, null, 1))}</pre></details>
    <details><summary>Prompt</summary>
      <pre class="mono">${esc(task.prompt)}</pre></details>`;
}

// ------------------------------------------------- per-video analysis plan

export const aiPlan = {
  title: 'Analyse video',
  async render(root, params, id) {
    root.innerHTML = spinner('Planning');
    const [plan, policy] = await Promise.all([
      api.aiPlan({ video_id: id }), api.aiPolicy(),
    ]);

    root.innerHTML = `
      <div class="row" style="justify-content:space-between;align-items:start">
        <h1>Visual analysis plan</h1>
        <div class="row" style="gap:8px">
          <a href="#/hooks/${id}"><button>Hook detail</button></a>
          <a href="#/ai"><button>AI models</button></a>
        </div>
      </div>
      ${note(`<strong>Mode <code>${esc(plan.mode)}</code>.</strong>
        ${esc(plan.reason)}`, plan.refused ? 'warn' : 'accent')}
      <div class="grid cols-4" style="margin:14px 0">
        ${stat('Frames', num(plan.frames_planned),
    plan.frame_timestamps.map((t) => `${t}s`).join(', '))}
        ${stat('Estimated cost', plan.estimated_usd
    ? `$${plan.estimated_usd.toFixed(4)}` : 'free')}
        ${stat('Your own media', plan.is_own_media ? 'yes' : 'no',
    plan.is_own_media ? '' : 'never sent to cloud by default')}
        ${stat('Category', plan.category, plan.category_authorised
    ? `cap $${plan.category_cap_usd.toFixed(2)}` : 'not authorised')}
      </div>
      ${card('Providers considered', consideredTable(plan))}
      ${plan.refused ? '' : card('Run it', runBlock(plan))}`;

    const btn = document.getElementById('ai-run');
    if (btn) {
      btn.addEventListener('click', async () => {
        const paid = plan.requires_authorisation;
        if (paid) {
          const agreed = window.confirm(
            'This sends your frames to a third-party provider and costs '
            + 'money.\n\n'
            + `Provider: ${plan.providers.join(', ')}\n`
            + `Frames: ${plan.frames_planned}\n`
            + `Estimated: $${plan.estimated_usd.toFixed(4)}\n`
            + `Category: ${plan.category} `
            + `(cap $${plan.category_cap_usd.toFixed(2)})\n\n`
            + 'The estimate is an upper bound; you are charged what the '
            + 'provider bills.\n\nProceed?');
          if (!agreed) {
            toast('Cancelled. Nothing was sent and nothing was spent.');
            return;
          }
        }
        btn.disabled = true;
        try {
          await api.aiAnalyse({ video_id: Number(id), mode: plan.mode,
            confirm_paid: paid });
          toast('Analysis complete');
          await aiPlan.render(root, params, id);
        } catch (err) {
          document.getElementById('ai-run-result').innerHTML =
            note(esc(err.message), err.status === 501 ? 'warn' : 'danger');
        } finally {
          btn.disabled = false;
        }
      });
    }
  },
};

const stat = (label, value, sub) => `
  <div class="card stat"><div class="label">${esc(label)}</div>
    <div class="value">${value}</div>
    ${sub ? `<div class="sub">${esc(sub)}</div>` : ''}</div>`;

function consideredTable(plan) {
  if (!plan.considered.length) return '<p class="muted">None.</p>';
  return `<table><thead><tr>
      <th>Provider</th><th>Where</th><th class="num">Estimate</th>
      <th>Outcome</th></tr></thead><tbody>
    ${plan.considered.map((c) => `<tr>
      <td><b>${esc(c.provider)}</b></td>
      <td>${HOST_CHIP(c.hosting)}</td>
      <td class="num">${c.estimated_usd ? `$${c.estimated_usd.toFixed(4)}`
    : 'free'}</td>
      <td>${c.chosen
    ? '<span class="chip ok"><span class="dot"></span>chosen</span>'
    : `<span class="muted">${esc(c.excluded_because
      || c.unavailable_reason || 'not chosen')}</span>`}</td>
    </tr>`).join('')}</tbody></table>
    <p class="muted">Every provider weighed, and why. A routing choice
      nobody can explain is a routing choice nobody can trust.</p>`;
}

function runBlock(plan) {
  return `
    ${plan.requires_authorisation
    ? note(`<strong>This costs money.</strong> You will be shown the exact
        estimate and asked to confirm before anything is sent.`, 'warn')
    : note('Runs locally. Nothing is uploaded and nothing is billed.', 'ok')}
    <button class="primary" id="ai-run">Analyse
      ${plan.requires_authorisation
    ? `(~$${plan.estimated_usd.toFixed(4)})` : '(free)'}</button>
    <div id="ai-run-result" style="margin-top:10px"></div>`;
}
