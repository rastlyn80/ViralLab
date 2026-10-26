/**
 * Platform connections: paste credentials, authorise spending.
 *
 * Two rules shape this page.
 *
 * Values are never displayed, because the API never returns them. What is
 * shown is whether a slot is filled and an 8-character fingerprint, which
 * is enough to confirm that the key stored is the key you meant to paste
 * without the secret appearing on screen, in a screenshot, or in the DOM.
 *
 * Storing a token and authorising money are separate actions, and the page
 * does not blur them. Pasting an Apify token does not start any spending;
 * the budget panel is a distinct form with its own confirmation, because a
 * paid dependency must never appear as a side effect of filling in a field.
 */

import { api } from './api.js';
import { card, esc, note, spinner, toast } from './components.js';

const PLATFORM_LABEL = {
  instagram: 'Instagram / Meta',
  tiktok: 'TikTok',
  ai: 'AI vision providers (all optional)',
};

const PROVIDERS = [
  { id: 'apify', label: 'Apify',
    what: 'TikTok competitor research. Pay-as-you-go per actor run.' },
  { id: 'openai', label: 'OpenAI', what: 'Cloud vision, per image.' },
  { id: 'gemini', label: 'Google Gemini',
    what: 'Cloud vision. Accepts video natively.' },
  { id: 'anthropic', label: 'Anthropic', what: 'Cloud vision, per image.' },
];

export const settings = {
  title: 'Platform connections',
  async render(root) {
    root.innerHTML = spinner('Loading connections');
    const view = await api.credentials();
    draw(root, view);
  },
};

function draw(root, view) {
  const byPlatform = {};
  view.credentials.forEach((c) => {
    (byPlatform[c.platform] ||= []).push(c);
  });

  root.innerHTML = `
    <h1>Platform connections</h1>
    <p class="muted">Paste keys here instead of editing <code>.env</code> on
      the server. Values are stored write-only — this page can confirm which
      key is saved, and cannot show it back to you or to anyone who opens
      your browser.</p>
    ${view.env_path_writable ? '' : note(`The server cannot write to its
      credential file, so saving will fail. Check permissions on
      <code>.env</code>.`, 'danger')}
    ${spendingCard(view.spending)}
    ${Object.entries(byPlatform).map(([platform, rows]) => card(
    esc(PLATFORM_LABEL[platform] || platform), slotTable(rows))).join('')}
    ${note(`A credential is only half of what a paid feature needs. The
      provider must also be authorised above, with a monthly cap over $0.
      Both, deliberately — either alone would let a request reach a paid
      endpoint you had not approved.`, '')}`;

  wire(root, view);
}

function spendingCard(sp) {
  const live = sp.research_provider !== 'fixture';
  return card('Spending authorisation', `
    <div class="row" style="gap:18px;align-items:flex-start;flex-wrap:wrap">
      <div>
        <div class="label">Monthly cap</div>
        <div class="value">$${(sp.monthly_cap_usd ?? 0).toFixed(2)}</div>
      </div>
      <div>
        <div class="label">Authorised providers</div>
        <div class="value">${sp.providers_authorised.length
    ? sp.providers_authorised.map(esc).join(', ')
    : '<span class="faint">none</span>'}</div>
      </div>
      <div>
        <div class="label">TikTok research</div>
        <div class="value">${live
    ? `<span class="chip ok"><span class="dot"></span>${esc(
      sp.research_provider)}</span>`
    : '<span class="chip">demonstration data</span>'}</div>
      </div>
    </div>
    <p class="muted" style="margin-top:8px">${esc(sp.research_reason)}</p>

    <details style="margin-top:12px">
      <summary><b>Change spending authorisation</b></summary>
      <p class="muted" style="margin-top:8px">This is the setting that
        permits real charges. Nothing else on this page does.</p>
      <div class="row" style="gap:10px;align-items:flex-end;flex-wrap:wrap">
        <label>Monthly cap (USD)
          <input id="cap" type="number" min="0" max="1000" step="1"
            value="${(sp.monthly_cap_usd ?? 0).toFixed(2)}"
            style="width:120px">
        </label>
      </div>
      <div style="margin-top:10px">
        ${PROVIDERS.map((p) => `
          <label class="row" style="gap:8px;margin:5px 0">
            <input type="checkbox" class="prov" value="${p.id}"
              ${sp.providers_authorised.includes(p.id) ? 'checked' : ''}>
            <span><b>${esc(p.label)}</b>
              <span class="faint">— ${esc(p.what)}</span></span>
          </label>`).join('')}
      </div>
      <button id="save-spending" class="warn" style="margin-top:10px">
        Authorise this spending</button>
      <div id="spending-result" style="margin-top:8px"></div>
    </details>`);
}

function slotTable(rows) {
  return `<table><thead><tr>
      <th>Credential</th><th>Status</th><th>What it unlocks</th><th></th>
    </tr></thead><tbody>
    ${rows.map((c) => `<tr data-slot="${esc(c.key)}">
      <td>
        <b>${esc(c.label)}</b>
        ${c.paid ? '<span class="chip warn">costs money</span>' : ''}
        <div class="faint" style="font-size:12px"><code>${esc(c.key)}</code></div>
        ${c.where ? `<div class="faint" style="font-size:12px">${esc(c.where)}</div>` : ''}
      </td>
      <td>
        ${c.is_set
    ? `<span class="chip ok"><span class="dot"></span>set</span>
           <div class="faint" style="font-size:12px">fingerprint
             <code>${esc(c.fingerprint || '')}</code></div>
           <div class="faint" style="font-size:12px">${esc(c.source || '')}</div>`
    : '<span class="chip">not set</span>'}
      </td>
      <td style="max-width:360px">
        <div style="font-size:13px">${esc(c.purpose)}</div>
        ${c.is_set ? '' : `<div class="faint" style="font-size:12px;margin-top:4px">
          Without it: ${esc(c.blocked_without)}</div>`}
      </td>
      <td style="white-space:nowrap">
        <input type="password" class="cred-input" placeholder="paste value"
          autocomplete="off" spellcheck="false" style="width:180px">
        <button class="save-cred primary">Save</button>
        ${c.is_set ? '<button class="clear-cred danger">Clear</button>' : ''}
      </td>
    </tr>`).join('')}</tbody></table>`;
}

function wire(root, view) {
  root.querySelectorAll('tr[data-slot]').forEach((tr) => {
    const key = tr.dataset.slot;
    const input = tr.querySelector('.cred-input');

    tr.querySelector('.save-cred')?.addEventListener('click', async () => {
      const value = input.value.trim();
      if (!value) {
        toast('Nothing pasted.');
        return;
      }
      const btn = tr.querySelector('.save-cred');
      btn.disabled = true;
      try {
        await api.setCredential(key, value);
        // Clear it from the DOM immediately. A secret should not sit in a
        // form field after it has been stored.
        input.value = '';
        toast(`${key} saved.`);
        await settings.render(root);
      } catch (err) {
        toast(`Could not save ${key}: ${err.message}`);
      } finally {
        btn.disabled = false;
      }
    });

    tr.querySelector('.clear-cred')?.addEventListener('click', async () => {
      if (!window.confirm(
        `Remove ${key}?\n\nWhatever depends on it stops working until you `
        + 'paste it again.')) return;
      try {
        await api.clearCredential(key);
        toast(`${key} removed.`);
        await settings.render(root);
      } catch (err) {
        toast(`Could not remove ${key}: ${err.message}`);
      }
    });
  });

  const saveSpending = root.querySelector('#save-spending');
  saveSpending?.addEventListener('click', async () => {
    const cap = Number(root.querySelector('#cap').value);
    const providers = [...root.querySelectorAll('.prov:checked')]
      .map((el) => el.value);
    const out = root.querySelector('#spending-result');

    if (cap > 0 && providers.length === 0) {
      out.innerHTML = note(`A cap with no provider authorises nothing.
        Tick at least one provider, or set the cap to $0 to switch paid
        features off.`, 'warn');
      return;
    }
    const agreed = window.confirm(
      'Authorise real spending?\n\n'
      + `Monthly cap: $${cap.toFixed(2)}\n`
      + `Providers: ${providers.join(', ') || 'none'}\n\n`
      + 'ViralLab will never exceed the cap without asking again, but '
      + 'within it, requests to these providers will be billed to your '
      + 'account.\n\nProceed?');
    if (!agreed) {
      toast('Cancelled. Nothing was authorised.');
      return;
    }
    saveSpending.disabled = true;
    try {
      await api.setSpending({
        monthly_cap_usd: cap, providers_authorised: providers, confirm: true,
      });
      toast('Spending authorisation updated.');
      await settings.render(root);
    } catch (err) {
      out.innerHTML = note(esc(err.message), 'danger');
    } finally {
      saveSpending.disabled = false;
    }
  });
}
