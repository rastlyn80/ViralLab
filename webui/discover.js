/**
 * Discover: find creators nobody listed, and decide who is worth paying to
 * research.
 *
 * Two design constraints shape every screen here.
 *
 * **The price is shown before the commitment.** The estimate endpoint is a
 * free GET, so the search button can always say what it would cost. On a
 * paid provider the only control that spends money is labelled as such, and
 * a reusable prior search is offered first.
 *
 * **A ranked list invites being read as a measurement, and it is not one.**
 * A keyword feed is biased toward recent and already-popular posts, so every
 * result set carries its sampling limits, every candidate carries the
 * reasons it ranked where it did, and a missing signal says "this provider
 * does not report it" rather than rendering a zero.
 */

import { api } from './api.js';
import {
  card, compact, dateTime, empty, esc, note, num, spinner, table, toast,
} from './components.js';
import { fixtureChip, spendingBanner } from './research.js';

const TIER_LABEL = {
  small_creator_breakout: 'Small creator, big reach',
  established_breakout_candidate: 'Established, breakout candidate',
  repeatedly_relevant: 'Repeatedly relevant',
  relevant: 'Relevant',
};
const TIER_STYLE = {
  small_creator_breakout: 'ok',
  established_breakout_candidate: 'accent',
  repeatedly_relevant: 'proposed',
  relevant: '',
};

/** Collections available to save into. Loaded once per view render. */
let COLLECTIONS = [];
/** Spending posture, so the search button knows whether it spends money. */
let SPENDING = null;

const tierChip = (t) =>
  `<span class="chip ${TIER_STYLE[t] ?? ''}"><span class="dot"></span>${
    esc(TIER_LABEL[t] || t)}</span>`;

/** A figure the provider does not report. Never rendered as a number. */
const maybe = (v, fmt = (x) => num(x)) =>
  (v === null || v === undefined ? '<span class="unknown"></span>' : fmt(v));

function samplingWarning(notes) {
  if (!notes?.length) return '';
  return note(`<strong>What this result set is not.</strong>
    <ul style="margin:6px 0 0 18px">${
      notes.map((n) => `<li>${esc(n)}</li>`).join('')}</ul>`, 'warn');
}

// --------------------------------------------------------------- the view

export const discover = {
  title: 'Discover creators',
  async render(root) {
    root.innerHTML = spinner('Loading discovery');
    const [state, niches, searches, cols] = await Promise.all([
      api.researchState(), api.niches(), api.discoverySearches({ limit: 25 }),
      api.collections(),
    ]);
    COLLECTIONS = cols;
    SPENDING = state;

    root.innerHTML = `
      <h1>Discover creators</h1>
      <p class="lede">Search a concept, see who shows up, and decide who is
        worth researching in full. Discovery <b>finds</b> creators; it does
        not measure them.</p>
      ${spendingBanner(state)}
      ${card('Search', searchForm(niches))}
      <div id="estimate"></div>
      <div id="results"></div>
      ${card('Previous searches', historyTable(searches))}`;

    wireForm(root);
    wireHistory(root);

    const hint = document.getElementById('d-cost-hint');
    if (hint) {
      hint.innerHTML = state.fixture_mode
        ? 'Fixture provider — nothing is billed.'
        : '<b>This provider charges.</b> You will be shown the exact '
          + 'estimate and asked to confirm before anything runs.';
    }
  },
};

function searchForm(niches) {
  const nicheOpts = niches.map(
    (n) => `<option value="${esc(n.key)}">${esc(n.label)}</option>`).join('');
  const suggestions = niches.flatMap(
    (n) => [...n.keywords.map((k) => ({ kind: 'keyword', v: k })),
      ...n.hashtags.map((h) => ({ kind: 'hashtag', v: `#${h}` }))]);

  return `
    <form id="disc-form">
      <div class="toolbar">
        <div class="grow">
          <label for="d-value">Keyword or hashtag</label>
          <input id="d-value" placeholder="classical piano, or #pianotok"
            required>
        </div>
        <div class="field">
          <label for="d-kind">Search as</label>
          <select id="d-kind">
            <option value="keyword">Keyword</option>
            <option value="hashtag">Hashtag</option>
          </select>
        </div>
        <div class="field">
          <label for="d-platform">Platform</label>
          <select id="d-platform">
            <option value="tiktok">TikTok</option>
            <option value="instagram">Instagram</option>
          </select>
        </div>
        <div class="field">
          <label for="d-limit">Max results</label>
          <input id="d-limit" type="number" value="50" min="1" max="500">
        </div>
        <div class="field">
          <label for="d-niche">Tag as niche</label>
          <select id="d-niche">
            <option value="">(none)</option>${nicheOpts}
          </select>
        </div>
      </div>
      <div class="row" style="gap:8px;flex-wrap:wrap">
        <button class="primary" type="submit" id="d-go">Search</button>
        <span class="muted" id="d-cost-hint"></span>
        <button type="button" id="d-estimate">What would this cost?</button>
        <label class="muted" style="display:flex;align-items:center;gap:6px">
          <input type="checkbox" id="d-force"> re-run instead of reusing a
          recent identical search
        </label>
      </div>
      <p class="muted" style="margin-top:10px">Try:
        ${suggestions.slice(0, 8).map((s) =>
    `<a href="#" class="d-suggest" data-kind="${s.kind}"
            data-value="${esc(s.v)}">${esc(s.v)}</a>`).join(' · ')}</p>
    </form>`;
}

function readForm() {
  const value = document.getElementById('d-value').value.trim();
  return {
    value,
    kind: document.getElementById('d-kind').value,
    platform: document.getElementById('d-platform').value,
    limit: Number(document.getElementById('d-limit').value) || 50,
    niche: document.getElementById('d-niche').value || null,
    force: document.getElementById('d-force').checked,
  };
}

function wireForm(root) {
  root.querySelectorAll('.d-suggest').forEach((a) => {
    a.addEventListener('click', (e) => {
      e.preventDefault();
      document.getElementById('d-value').value = a.dataset.value;
      document.getElementById('d-kind').value = a.dataset.kind;
    });
  });

  document.getElementById('d-estimate').addEventListener('click', async () => {
    const f = readForm();
    if (!f.value) { toast('Enter a search term', true); return; }
    const box = document.getElementById('estimate');
    box.innerHTML = spinner('Pricing');
    try {
      box.innerHTML = estimateCard(await api.discoveryEstimate({
        kind: f.kind, platform: f.platform, value: f.value, limit: f.limit,
      }));
    } catch (err) {
      box.innerHTML = note(esc(err.message), 'danger');
    }
  });

  document.getElementById('disc-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const f = readForm();
    if (!f.value) return;

    // A paid run is never started by the same click that composes it. The
    // price is fetched first - the estimate endpoint is free - and shown
    // with the remaining budget, and the owner has to agree to that exact
    // figure. Fixture mode skips this, because there is nothing to agree
    // to when nothing is billed.
    if (SPENDING && !SPENDING.fixture_mode) {
      let est;
      try {
        est = await api.discoveryEstimate({
          kind: f.kind, platform: f.platform, value: f.value, limit: f.limit,
        });
      } catch (err) {
        toast(`Could not price this search: ${err.message}`, true);
        return;
      }
      if (!est.supported) {
        toast(est.unsupported_reason, true);
        return;
      }
      if (est.reusable_job_id && !f.force) {
        const reuse = window.confirm(
          `An identical search already ran at ${dateTime(
            est.reusable_started_at)}.\n\n`
          + 'Opening the stored results costs nothing.\n\n'
          + 'OK = open the stored results (free)\n'
          + 'Cancel = go back (tick "re-run" to pay for fresh data)');
        if (reuse) {
          const box = document.getElementById('results');
          box.innerHTML = spinner('Opening stored results');
          const cands = await api.discoveryCandidates(est.reusable_job_id);
          box.innerHTML = `<h2>Stored result set</h2>
            ${note('Read from storage. Nothing was collected and nothing '
            + 'was spent.', 'accent')}
            ${card('Research a selection', expansionControls())}
            ${candidateTable(cands)}`;
          wireCandidates(box, est.reusable_job_id);
        }
        return;
      }
      const remaining = est.remaining_usd === null
        ? 'no cap configured' : `$${est.remaining_usd.toFixed(2)} remaining`;
      const agreed = window.confirm(
        `This will spend real money.\n\n`
        + `Provider: ${est.provider}\n`
        + `Search: ${f.kind} "${f.value}" on ${f.platform}\n`
        + `Up to ${f.limit} items\n`
        + `Estimated cost: $${est.estimated_usd.toFixed(4)}\n`
        + `Budget: ${remaining} of $${est.monthly_cap_usd.toFixed(2)}\n\n`
        + 'The estimate is an upper bound; you are charged what the '
        + 'provider bills.\n\nProceed?');
      if (!agreed) {
        toast('Cancelled. Nothing was collected and nothing was spent.');
        return;
      }
    }

    const btn = document.getElementById('d-go');
    btn.disabled = true;
    btn.textContent = 'Searching...';
    const box = document.getElementById('results');
    box.innerHTML = spinner('Searching');
    try {
      const res = await api.runDiscovery(f);
      box.innerHTML = resultsBlock(res);
      wireCandidates(box, res.job_id);
      document.getElementById('estimate').innerHTML = '';
    } catch (err) {
      box.innerHTML = note(esc(err.message), 'danger');
    } finally {
      btn.disabled = false;
      btn.textContent = 'Search';
    }
  });
}

function estimateCard(e) {
  if (!e.supported) {
    return note(`<strong>Not possible.</strong> ${esc(e.unsupported_reason)}`,
      'danger');
  }
  const reuse = e.reusable_job_id
    ? note(`An identical search ran at ${dateTime(e.reusable_started_at)}.
        Opening it again is <b>free</b>; tick "re-run" only if you want
        fresh data. <a href="#" class="open-job"
        data-job="${e.reusable_job_id}">Open stored results</a>`, 'accent')
    : '';
  const price = e.is_fixture
    ? '<b>$0.00</b> — fixture provider, nothing is billed'
    : `<b>$${e.estimated_usd.toFixed(4)}</b> estimated`;
  return card('Estimate', `
    ${reuse}
    <p>${price} for up to ${num(e.limit)} items from
      <code>${esc(e.provider)}</code>.</p>
    ${e.is_fixture ? '' : `<p class="muted">Month-to-date budget:
      ${e.remaining_usd === null ? 'no cap configured'
    : `$${e.remaining_usd.toFixed(2)} remaining of
         $${e.monthly_cap_usd.toFixed(2)}`}.</p>`}
    ${samplingWarning(e.coverage_warnings)}`);
}

function resultsBlock(res) {
  if (res.status === 'refused') {
    return note(`<strong>Refused.</strong> ${esc(res.message)}`, 'danger');
  }
  if (res.status === 'failed') {
    return note(`<strong>Failed.</strong> ${esc(res.message)}`, 'danger');
  }
  if (res.status === 'in_flight') {
    return note(`<strong>Already running.</strong> ${esc(res.message)}`,
      'warn');
  }

  const head = res.status === 'reused'
    ? note(`<strong>Reused.</strong> ${esc(res.message)}`, 'accent')
    : note(`Found <b>${num(res.publications_seen)}</b> publication(s) from
        <b>${num(res.creators_seen)}</b> creator(s)
        (${num(res.creators_new)} new).
        ${res.is_fixture ? 'Fixture provider: nothing was billed.'
    : `Billed: $${(res.actual_usd ?? 0).toFixed(4)}.`}`, 'ok');

  const errs = res.item_errors?.length
    ? note(`<strong>${res.item_errors.length} item(s) the provider could not
        return.</strong> Kept rather than dropped, because a private account
        must not look like an empty one:
        <ul style="margin:6px 0 0 18px">${res.item_errors.slice(0, 5).map(
    (x) => `<li>${esc(JSON.stringify(x))}</li>`).join('')}</ul>`, 'warn')
    : '';

  return `
    <h2>Candidates ${fixtureChip(res.is_fixture)}</h2>
    ${head}
    ${errs}
    ${samplingWarning(res.coverage_notes)}
    ${card('Research a selection', expansionControls(), { flush: false })}
    ${candidateTable(res.candidates)}`;
}

function expansionControls() {
  return `
    <div class="toolbar">
      <div class="field">
        <label for="x-max">Max creators</label>
        <input id="x-max" type="number" value="3" min="1" max="50">
      </div>
      <div class="field">
        <label for="x-spend">Max spend ($)</label>
        <input id="x-spend" type="number" value="" step="0.01" min="0"
          placeholder="no extra limit">
      </div>
      <div style="align-self:end">
        <button type="button" id="x-plan">Plan expansion</button>
      </div>
    </div>
    <p class="muted">Researching a creator collects their full publication
      history. It is deliberately not automatic for everyone a search
      returns — that is the easiest way to spend a lot on accounts that turn
      out to be irrelevant.</p>
    <div id="x-result"></div>
    <hr style="border:none;border-top:1px solid var(--line);margin:14px 0">
    <div class="toolbar" style="margin-bottom:6px">
      <div class="field">
        <label for="s-collection">Save selected to</label>
        <select id="s-collection">
          ${COLLECTIONS.length
    ? COLLECTIONS.map((c) => `<option value="${c.id}">${esc(c.name)} (${
      esc(c.purpose)})</option>`).join('')
    : '<option value="">no collections yet</option>'}
        </select>
      </div>
      <div style="align-self:end">
        <button type="button" id="s-save"${COLLECTIONS.length ? ''
    : ' disabled'}>Save to collection</button>
      </div>
    </div>
    <p class="muted">Saving costs nothing and collects nothing. It records
      that you want to come back to this creator${COLLECTIONS.length ? ''
    : ' — create a collection first on the Collections page'}.</p>
    <div id="s-result"></div>`;
}

function candidateTable(cands) {
  if (!cands?.length) {
    return empty('No creators', 'The search returned no publications.');
  }
  const rows = cands.map((c) => `
    <tr>
      <td><input type="checkbox" class="c-pick" value="${c.creator_id}"></td>
      <td>
        <div><a href="#/research/${c.creator_id}"><b>${esc(c.handle)}</b></a>
          ${fixtureChip(c.is_fixture)}</div>
        <div class="muted">${esc(c.display_name || '')}</div>
      </td>
      <td>${tierChip(c.tier)}</td>
      <td class="num">${c.followers_available
    ? maybe(c.follower_count, compact)
    : '<span class="unknown"></span>'}</td>
      <td class="num">${maybe(c.sampled_best_views, compact)}${
  c.counts_rounded ? ' <span class="chip warn">rounded</span>' : ''}</td>
      <td class="num">${maybe(c.best_views_per_follower,
    (v) => `${v}x`)}</td>
      <td class="num">${c.sampled_publications}</td>
      <td class="num">${c.already_researched
    ? `<span class="chip ok">researched</span>`
    : `$${c.estimated_research_usd.toFixed(4)}`}</td>
      <td><button class="c-why" data-id="${c.creator_id}">Why?</button></td>
    </tr>
    <tr class="c-reasons" id="why-${c.creator_id}" hidden>
      <td colspan="9">
        <ul style="margin:0 0 0 18px">${
  c.reasons.map((r) => `<li>${esc(r)}</li>`).join('')
  || '<li class="muted">No distinguishing signal.</li>'}</ul>
        <table style="margin-top:8px">
          <thead><tr><th>Signal</th><th class="num">Value</th>
            <th>Note</th></tr></thead>
          <tbody>${c.signals.map((s) => `<tr>
            <td>${esc(s.label)}</td>
            <td class="num">${s.available ? maybe(s.value, (v) => v)
    : '<span class="unknown"></span>'}</td>
            <td class="muted">${esc(s.note || '')}</td></tr>`).join('')}
          </tbody>
        </table>
      </td>
    </tr>`).join('');

  return `<div class="card flush"><table>
    <thead><tr>
      <th></th><th>Creator</th><th>Why interesting</th>
      <th class="num">Followers</th><th class="num">Best sampled views</th>
      <th class="num">Views/follower</th><th class="num">In sample</th>
      <th class="num">Research cost</th><th></th>
    </tr></thead>
    <tbody>${rows}</tbody></table></div>
    <p class="muted">"In sample" is how many of a creator's publications
      <em>this feed showed</em>, not how many they have. Repeated appearance
      is relevance to the term, not evidence of consistent performance.</p>`;
}

function wireCandidates(root, jobId) {
  root.querySelectorAll('.c-why').forEach((b) => {
    b.addEventListener('click', () => {
      const row = document.getElementById(`why-${b.dataset.id}`);
      row.hidden = !row.hidden;
      b.textContent = row.hidden ? 'Why?' : 'Hide';
    });
  });

  const saveBtn = document.getElementById('s-save');
  if (saveBtn) {
    saveBtn.addEventListener('click', async () => {
      const sel = document.getElementById('s-collection');
      const collectionId = sel?.value;
      if (!collectionId) { toast('Create a collection first', true); return; }
      const picked = [...root.querySelectorAll('.c-pick:checked')];
      if (!picked.length) { toast('Select some creators first', true); return; }
      const out = document.getElementById('s-result');
      out.innerHTML = '';
      let saved = 0;
      for (const box of picked) {
        const row = box.closest('tr');
        const tier = row?.querySelector('.chip')?.textContent?.trim() || '';
        try {
          await api.addCollectionMember(collectionId, {
            creator_id: Number(box.value),
            reason: tier ? `discovery: ${tier}` : 'discovery',
            discovered_by_job_id: jobId,
          });
          saved += 1;
        } catch (err) {
          out.insertAdjacentHTML('beforeend',
            note(esc(err.message), 'danger'));
        }
      }
      if (saved) {
        out.insertAdjacentHTML('beforeend', note(
          `Saved ${saved} creator(s). <a href="#/collections/${
            collectionId}">Open the collection</a>`, 'ok'));
      }
    });
  }

  const planBtn = document.getElementById('x-plan');
  if (!planBtn) return;
  planBtn.addEventListener('click', async () => {
    const picked = [...root.querySelectorAll('.c-pick:checked')]
      .map((i) => Number(i.value));
    const body = {
      job_id: jobId,
      creator_ids: picked,
      max_creators: Number(document.getElementById('x-max').value) || null,
      max_spend_usd: document.getElementById('x-spend').value
        ? Number(document.getElementById('x-spend').value) : null,
    };
    const box = document.getElementById('x-result');
    box.innerHTML = spinner('Planning');
    try {
      const plan = await api.expansionPlan(body);
      box.innerHTML = planBlock(plan);
      wirePlan(box);
    } catch (err) {
      box.innerHTML = note(esc(err.message), 'danger');
    }
  });
}

function planBlock(plan) {
  if (!plan.candidates.length) {
    return note(`Nothing to research. ${esc(plan.notes.join(' '))}`, 'warn');
  }
  return `
    ${note(`<b>${plan.candidates.length}</b> creator(s), estimated total
      <b>$${plan.total_estimated_usd.toFixed(4)}</b>.
      ${plan.within_budget ? '' : '<b>Trimmed to fit the ceiling.</b>'}`,
  plan.within_budget ? 'accent' : 'warn')}
    <ul>${plan.candidates.map((c) => `<li>${esc(c.handle)} —
      $${c.estimated_research_usd.toFixed(4)}</li>`).join('')}</ul>
    ${plan.notes.map((n) => `<p class="muted">${esc(n)}</p>`).join('')}
    <button class="primary" id="x-run">Research these
      ${plan.candidates.length}</button>
    <div id="x-run-result"></div>`;
}

function wirePlan(box) {
  const btn = document.getElementById('x-run');
  if (!btn) return;
  btn.addEventListener('click', async () => {
    const handles = [...box.querySelectorAll('li')]
      .map((li) => li.textContent.split('—')[0].trim()).filter(Boolean);
    btn.disabled = true;
    const out = document.getElementById('x-run-result');
    out.innerHTML = '';
    for (const handle of handles) {
      out.insertAdjacentHTML('beforeend',
        `<p class="muted">Researching ${esc(handle)}...</p>`);
      try {
        const platform = document.getElementById('d-platform').value;
        const r = await api.runResearch({ platform, handle, limit: 200 });
        out.insertAdjacentHTML('beforeend', note(
          `${esc(handle)}: ${esc(r.status)} —
           <a href="#/research/${r.creator_id}">open</a>`,
          r.status === 'ok' ? 'ok' : 'warn'));
      } catch (err) {
        out.insertAdjacentHTML('beforeend',
          note(`${esc(handle)}: ${esc(err.message)}`, 'danger'));
      }
    }
    btn.disabled = false;
    btn.textContent = 'Done';
  });
}

function historyTable(searches) {
  if (!searches.length) {
    return empty('No searches yet', 'Run one above.');
  }
  return table(
    ['When', 'Search', 'Platform', 'Results', 'Cost', ''],
    searches.map((s) => `<tr>
      <td>${dateTime(s.started_at)}</td>
      <td><b>${esc(s.value)}</b> <span class="muted">${esc(s.kind)}</span>
        ${fixtureChip(s.is_fixture)}
        ${s.niche ? `<span class="chip">${esc(s.niche)}</span>` : ''}</td>
      <td>${esc(s.platform)}</td>
      <td class="num">${num(s.publications)} pubs /
        ${num(s.creators)} creators</td>
      <td class="num">${s.actual_usd === null ? '<span class="unknown"></span>'
    : `$${s.actual_usd.toFixed(4)}`}</td>
      <td><button class="open-job" data-job="${s.job_id}">Open
        ${s.reusable ? '(free)' : ''}</button></td>
    </tr>`));
}

function wireHistory(root) {
  root.addEventListener('click', async (e) => {
    const btn = e.target.closest('.open-job');
    if (!btn) return;
    e.preventDefault();
    const box = document.getElementById('results');
    box.innerHTML = spinner('Opening stored results');
    try {
      const cands = await api.discoveryCandidates(btn.dataset.job);
      box.innerHTML = `
        <h2>Stored result set</h2>
        ${note(`Read from storage. Nothing was collected and nothing was
          spent.`, 'accent')}
        ${card('Research a selection', expansionControls())}
        ${candidateTable(cands)}`;
      wireCandidates(box, Number(btn.dataset.job));
      box.scrollIntoView({ behavior: 'smooth', block: 'start' });
    } catch (err) {
      box.innerHTML = note(esc(err.message), 'danger');
    }
  });
}
