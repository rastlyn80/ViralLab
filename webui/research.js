/**
 * Competitor research views.
 *
 * Same architecture and design tokens as views.js (ADR 0006): plain ES
 * modules, composed from components.js, state in the URL.
 *
 * Three rules this surface has to keep, and they are not cosmetic:
 *   - fixture-backed records are labelled everywhere they appear
 *   - a figure the source does not report renders as unknown, never 0
 *   - an inferred breakthrough never looks like an established fact, and
 *     the evidence it lacks is shown next to it
 */

import { api } from './api.js';
import {
  ageQualified, card, compact, dateOnly, dateTime, empty, esc, evidence,
  kv, note, num, platformChip, secs, spinner, stat, table, toast,
} from './components.js';

const CLASS_LABEL = {
  observed_outlier: 'observed outlier',
  inferred_breakthrough: 'inferred breakthrough',
  verified_trajectory_change: 'verified trajectory change',
  rejected: 'rejected',
};
const CLASS_STYLE = {
  observed_outlier: 'accent',
  inferred_breakthrough: 'proposed',
  verified_trajectory_change: 'ok',
  rejected: '',
};

export const classChip = (c) =>
  `<span class="chip ${CLASS_STYLE[c] || ''}"><span class="dot"></span>${
    esc(CLASS_LABEL[c] || c)}</span>`;

export const fixtureChip = (on) => (on
  ? '<span class="chip warn"><span class="dot"></span>fixture data</span>'
  : '');

export function spendingBanner(state) {
  if (state.fixture_mode) {
    return note(`<strong>Fixture mode.</strong> No paid provider is
      authorised, so research runs against committed <b>synthetic</b>
      fixtures. Results are fabricated for demonstration, labelled as such
      everywhere, and cost nothing. To collect real data, authorise a
      provider and a monthly cap in <code>config/settings.yaml</code> and
      set <code>APIFY_TOKEN</code>.`, 'warn');
  }
  return note(`<strong>Live provider: ${esc(state.active_provider)}.</strong>
    Budget for ${esc(state.period)}: $${state.committed_usd.toFixed(2)}
    committed of $${state.monthly_cap_usd.toFixed(2)},
    <b>$${state.available_usd.toFixed(2)} available</b>. Research costs
    money and will be refused rather than exceed the cap.`, 'accent');
}

// ------------------------------------------------------------------ search

export const research = {
  title: 'Competitor research',
  async render(root) {
    root.innerHTML = `<h1>Competitor research</h1>${spinner()}`;
    const [state, creators] = await Promise.all([
      api.researchState(), api.researchedCreators(),
    ]);

    const fixtureOptions = (state.available_fixtures || [])
      .filter((f) => f.kind === 'creator')
      .map((f) => `<li><code>${esc(f.platform)}</code> /
        <code>${esc(f.value)}</code> &mdash; ${f.items} publication(s)</li>`)
      .join('');

    const rows = creators.map((c) => `<tr>
      <td>${platformChip(c.platform)}</td>
      <td><a href="#/research/${c.creator_id}">${esc(c.handle)}</a>
        ${c.is_fixture ? ' ' + fixtureChip(true) : ''}</td>
      <td class="right">${num(c.follower_count)}</td>
      <td class="right">${num(c.publications_collected)}</td>
      <td class="right">${c.breakthrough_count || '<span class="faint">—</span>'}</td>
      <td class="nowrap">${c.last_refreshed_at
        ? dateTime(c.last_refreshed_at) : '<span class="faint">never</span>'}
        ${c.is_stale
          ? ' <span class="chip warn"><span class="dot"></span>stale</span>'
          : ' <span class="chip ok"><span class="dot"></span>fresh</span>'}</td>
      <td class="right nowrap">${c.refresh_cost_usd
        ? `$${c.refresh_cost_usd.toFixed(2)}`
        : '<span class="faint">free</span>'}</td>
    </tr>`);

    root.innerHTML = `
      <h1>Competitor research</h1>
      <p class="lede">Enter a TikTok or Instagram username and research that
        creator: their publication history, how each post performed against
        their own baseline, and where their trajectory may have changed.</p>

      ${spendingBanner(state)}

      <div class="card">
        <h3>Research a creator</h3>
        <form id="research-form">
          <div class="toolbar" style="margin-bottom:10px">
            <div class="field"><label for="r-platform">Platform</label>
              <select id="r-platform">
                <option value="tiktok">TikTok</option>
                <option value="instagram">Instagram</option>
              </select></div>
            <div class="grow"><label for="r-handle">Username or profile URL</label>
              <input id="r-handle" type="text" required
                     placeholder="@demo_pianist or tiktok.com/@demo_pianist"></div>
            <div class="field"><label for="r-limit">Max publications</label>
              <select id="r-limit">
                <option value="50">50</option>
                <option value="100" selected>100</option>
                <option value="200">200</option>
              </select></div>
          </div>
          <div class="row">
            <button class="primary" type="submit" id="r-go">Research</button>
            <label class="muted" style="font-size:13px;display:flex;
                   gap:6px;align-items:center;text-transform:none;
                   letter-spacing:0">
              <input type="checkbox" id="r-refresh" style="width:auto">
              Force refresh${state.fixture_mode ? ''
                : ' (re-collects and costs money)'}
            </label>
          </div>
        </form>
        <div id="r-status"></div>
        ${fixtureOptions ? `<div class="note">Fixtures available to
          demonstrate:<ul style="margin:6px 0 0;padding-left:20px">
          ${fixtureOptions}</ul></div>` : ''}
      </div>

      <h2>Research history</h2>
      <p class="muted" style="font-size:13px;margin-top:-4px">Reopening a
        creator costs nothing. Only a refresh re-collects.</p>
      ${card('', table(
        ['platform', 'handle', { label: 'followers', cls: 'right' },
          { label: 'publications', cls: 'right' },
          { label: 'candidates', cls: 'right' }, 'last collected',
          { label: 'refresh cost', cls: 'right' }],
        rows, { empty: 'No creator has been researched yet.' }),
        { flush: true })}`;

    document.getElementById('research-form')
      .addEventListener('submit', async (e) => {
        e.preventDefault();
        const btn = document.getElementById('r-go');
        const statusEl = document.getElementById('r-status');
        const platform = document.getElementById('r-platform').value;
        const handle = document.getElementById('r-handle').value.trim();
        const limit = Number(document.getElementById('r-limit').value);
        const refresh = document.getElementById('r-refresh').checked;
        if (!handle) return;

        btn.disabled = true;
        statusEl.innerHTML = spinner('Researching');
        try {
          const res = await api.runResearch({
            platform, handle, limit, refresh,
          });
          statusEl.innerHTML = researchOutcome(res);
          if (res.status === 'ok' && res.creator_id) {
            toast(res.reused ? 'Reused existing research'
              : `Collected ${res.job?.publications_seen ?? 0} publication(s)`);
            setTimeout(() => { location.hash = `#/research/${res.creator_id}`; },
              res.reused ? 400 : 900);
          }
        } catch (err) {
          statusEl.innerHTML = note(esc(err.message), 'danger');
        } finally {
          btn.disabled = false;
        }
      });
  },
};

function researchOutcome(res) {
  if (res.status === 'refused') {
    return note(`<strong>Refused, not failed.</strong> ${esc(res.message
      || '')} Nothing was collected and nothing was spent.`, 'warn');
  }
  if (res.status === 'failed') {
    return note(`<strong>Failed.</strong> ${esc(res.message || '')}`,
      'danger');
  }
  const j = res.job;
  const errs = (j?.item_errors || []).map((e) =>
    `<li><code>${esc(e.error)}</code> ${esc(e.description || '')}</li>`)
    .join('');
  return note(`<strong>${res.reused ? 'Reused' : 'Collected'}.</strong>
    ${res.reused ? esc(res.message || '') : `
      ${j?.publications_seen ?? 0} publication(s),
      ${j?.publications_new ?? 0} new,
      ${j?.snapshots_written ?? 0} observation(s) recorded,
      ${res.breakthroughs_found} candidate(s).
      Cost ${j?.actual_usd ? `$${j.actual_usd.toFixed(4)}` : '$0.00'}.`}
    ${res.is_fixture ? ' <b>Fixture data.</b>' : ''}
    ${errs ? `<div style="margin-top:8px">The provider could not read:
      <ul style="margin:4px 0 0;padding-left:20px">${errs}</ul></div>` : ''}`,
    res.is_fixture ? 'warn' : 'accent');
}

// ---------------------------------------------------------------- profile

export const researchCreator = {
  title: 'Creator',
  async render(root, params, id) {
    root.innerHTML = spinner('Loading creator');
    const order = params.get('order') || 'relative';
    const q = params.get('q') || '';

    const [detail, pubs, bt] = await Promise.all([
      api.researchCreator(id),
      api.researchPublications(id, { order, q, limit: 60 }),
      api.researchBreakthroughs(id),
    ]);

    const s = detail.summary;
    const caps = detail.capabilities;
    const missing = (caps?.missing_creator_fields || []);

    root.innerHTML = `
      <div class="row between">
        <h1>${platformChip(detail.platform)} ${esc(detail.handle)}
          ${fixtureChip(detail.is_fixture)}</h1>
        <div class="row" style="gap:8px">
          <a href="#/research/${detail.id}/timeline"><button>Timeline
            </button></a>
          <a href="#/research/${detail.id}/report"><button>Report</button></a>
        </div>
        <a href="#/research">&larr; research</a>
      </div>
      <p class="lede">${esc(detail.display_name || '')}
        ${detail.profile_url
          ? `<a href="${esc(detail.profile_url)}" target="_blank"
               rel="noopener">profile</a>` : ''}</p>

      ${detail.is_fixture ? note(`<strong>This record is fixture
        data.</strong> Synthetic, fabricated for demonstration, and not
        collected from any platform or about any real person.`, 'warn') : ''}

      <div class="grid cols-4">
        ${stat('Followers', num(detail.follower_count),
          missing.includes('follower_count')
            ? 'this source does not report followers'
            : 'latest observation')}
        ${stat('Publications', num(s.publications),
          `${s.with_views} with a view count`)}
        ${stat('Median views', compact(s.median_views),
          'across all publications')}
        ${stat('Median views, recent', compact(s.median_views_recent),
          'last 12 publications')}
      </div>

      <div class="grid cols-2" style="margin-top:14px">
        <div class="card">
          <h3>Collection</h3>
          ${kv([
            ['research status', esc(detail.research_status)],
            ['last collected', detail.last_refreshed_at
              ? dateTime(detail.last_refreshed_at) : 'never'],
            ['freshness', detail.is_stale
              ? '<span class="chip warn"><span class="dot"></span>stale</span>'
              : '<span class="chip ok"><span class="dot"></span>fresh</span>'],
            ['source', `<span class="chip">${esc(detail.source)}</span>`],
            ['found via', esc(detail.discovered_via || '—')],
            ['provider', esc(detail.last_job?.provider || '—')],
            ['refresh would cost', detail.refresh_cost_usd
              ? `$${detail.refresh_cost_usd.toFixed(2)}` : 'nothing'],
          ])}
        </div>
        <div class="card">
          <h3>Publication span</h3>
          ${kv([
            ['earliest', dateOnly(s.first_published)],
            ['latest', dateOnly(s.last_published)],
            ['observed at', dateTime(s.observed_at)],
            ['age span', s.age_span_days
              ? `${Math.round(s.age_span_days)} days` : '—'],
            ['highest views', compact(s.top_views)],
            ['median likes', compact(s.median_likes)],
          ])}
        </div>
      </div>

      ${s.caveats.length ? `<div class="card" style="margin-top:14px">
        <h3>What these figures do and do not mean</h3>
        <ul class="muted" style="margin:0;padding-left:18px;font-size:13px">
          ${s.caveats.map((c) => `<li>${esc(c)}</li>`).join('')}
        </ul></div>` : ''}

      ${followerHistory(detail)}

      <h2>Breakthrough analysis</h2>
      ${breakthroughSection(bt)}

      <h2>Publications</h2>
      <div id="pub-toolbar"></div>
      <div id="pub-body"></div>`;

    renderPublications(id, pubs, order, q);
  },
};

function followerHistory(detail) {
  const obs = detail.follower_observations || [];
  if (obs.length > 1) {
    const rows = obs.map((o) => `<tr>
      <td class="nowrap">${dateTime(o.captured_at)}</td>
      <td class="right">${num(o.follower_count)}</td>
      <td class="muted">${esc(o.source)}</td></tr>`);
    return `<h2>Follower observations</h2>${card('', table(
      ['observed', { label: 'followers', cls: 'right' }, 'source'], rows),
      { flush: true })}`;
  }
  return `<h2>Follower observations</h2>${note(obs.length === 1
    ? `<strong>One observation only.</strong> A single follower count is not
       a history, so no claim can be made that any publication caused
       follower growth. Research this creator again later and a trajectory
       becomes visible.`
    : `<strong>No follower history.</strong> This source reports no
       account-level figures at all, so follower growth cannot be observed
       or claimed.`, 'warn')}`;
}

function breakthroughSection(bt) {
  const b = bt.breakthrough;
  const chips = `<div class="row" style="margin-bottom:10px">
    <span class="chip">${bt.publications_considered} publication(s)
      considered</span>
    <span class="chip">${esc(bt.method_version)}</span>
    <span class="chip ${bt.has_repeated_observations ? 'ok' : ''}">
      repeated observations: ${bt.has_repeated_observations ? 'yes' : 'no'}
    </span>
    <span class="chip ${bt.has_follower_history ? 'ok' : ''}">
      follower history: ${bt.has_follower_history ? 'yes' : 'no'}</span>
  </div>`;

  const main = b ? card('', `
    <div class="row between" style="margin-bottom:10px">
      <strong>Change point at publication ${(b.publication_index ?? 0) + 1}
        of ${b.publications_total ?? '?'}</strong>
      ${classChip(b.classification)}
    </div>
    ${kv([
      ['published', dateOnly(b.posted_at)],
      ['views', compact(b.observed_views)],
      ['median before', compact(b.baseline_views)],
      ['median after', compact(b.median_after)],
      ['step', b.step_ratio ? `<b>${b.step_ratio}x</b>` : '—'],
      ['sustained afterwards', b.sustained === null ? '—'
        : (b.sustained ? 'yes' : 'no')],
      ['confidence', num(b.confidence, { digits: 2 })],
    ])}
    ${b.caption ? `<div class="caption-text" style="margin-top:9px">${
      esc(b.caption)}</div>` : ''}
    ${note(`<strong>This is an ${esc(CLASS_LABEL[b.classification]
      || b.classification)}, not an established cause.</strong> It says the
      creator's own performance level stepped up around here and stayed up.
      It does not say this publication caused it.`, 'warn')}
    ${b.missing_evidence.length ? `<h3 style="margin-top:12px">Evidence this
      classification lacks</h3>
      <ul class="muted" style="margin:0;padding-left:18px;font-size:13px">
        ${b.missing_evidence.map((m) => `<li>${esc(m)}</li>`).join('')}
      </ul>` : ''}
    ${evidence(b.evidence)}`)
    : empty('No change point detected',
      (bt.refusals || []).join(' '));

  const outliers = bt.outliers.length ? table(
    ['published', { label: 'index', cls: 'right' },
      { label: 'views', cls: 'right' },
      { label: 'vs own baseline', cls: 'right' },
      { label: 'robust z', cls: 'right' }, 'classification'],
    bt.outliers.map((o) => `<tr>
      <td class="nowrap">${dateOnly(o.posted_at)}</td>
      <td class="right">${(o.publication_index ?? 0) + 1}</td>
      <td class="right">${compact(o.observed_views)}</td>
      <td class="right">${o.relative_performance
        ? `${o.relative_performance.toFixed(1)}x` : '—'}</td>
      <td class="right">${num(o.robust_z, { digits: 1 })}</td>
      <td>${classChip(o.classification)}</td>
    </tr>`)) : empty('No outliers',
    'No publication stands out against its own neighbours.');

  return `${chips}${main}
    <h3 style="margin-top:18px">Outliers against the creator's own baseline</h3>
    ${note(`An outlier is a fact about one publication relative to the posts
      around it. It is <b>not</b> a trajectory change, and an old post with
      a high cumulative count is not evidence of an early breakthrough.`)}
    ${card('', outliers, { flush: true })}
    ${bt.caveats.length ? `<div class="card" style="margin-top:14px">
      <h3>Standing limitations</h3>
      <ul class="muted" style="margin:0;padding-left:18px;font-size:13px">
        ${bt.caveats.map((c) => `<li>${esc(c)}</li>`).join('')}
      </ul></div>` : ''}`;
}

function renderPublications(id, page, order, q) {
  document.getElementById('pub-toolbar').innerHTML = `
    <div class="toolbar">
      <div class="grow"><label for="p-q">Search caption, hashtag or music</label>
        <input id="p-q" type="search" value="${esc(q)}"
               placeholder="chopin, nocturne, #piano"></div>
      <div class="field"><label for="p-order">Sort by</label>
        <select id="p-order">
          <option value="relative"${order === 'relative' ? ' selected' : ''}>
            performance vs own baseline</option>
          <option value="newest"${order === 'newest' ? ' selected' : ''}>newest</option>
          <option value="oldest"${order === 'oldest' ? ' selected' : ''}>oldest</option>
          <option value="views"${order === 'views' ? ' selected' : ''}>raw views</option>
        </select></div>
    </div>`;

  const rows = page.items.map((p) => `<tr>
    <td class="nowrap">${dateOnly(p.posted_at)}</td>
    <td>${p.thumbnail_url
      ? `<img src="${esc(p.thumbnail_url)}" alt="" loading="lazy"
             style="width:44px;height:78px;object-fit:cover;border-radius:4px;
             background:var(--surface-2)">`
      : '<span class="faint">—</span>'}</td>
    <td>${esc((p.caption || '').slice(0, 90))}
      ${p.music_title ? `<div class="faint" style="font-size:12px">&#9834;
        ${esc(p.music_title)}</div>` : ''}</td>
    <td class="right nowrap">${secs(p.duration_s)}</td>
    <td class="right nowrap">${ageQualified(p.views, p.age_days)}</td>
    <td class="right">${num(p.likes)}</td>
    <td class="right">${num(p.saves)}</td>
    <td class="right nowrap">${p.relative_performance
      ? `<b>${p.relative_performance.toFixed(1)}x</b>`
      : '<span class="faint">no baseline</span>'}</td>
    <td>${p.breakthrough ? classChip(p.breakthrough) : ''}</td>
  </tr>`);

  document.getElementById('pub-body').innerHTML = card('', table(
    ['posted', 'cover', 'caption', { label: 'length', cls: 'right' },
      { label: 'views', cls: 'right' }, { label: 'likes', cls: 'right' },
      { label: 'saves', cls: 'right' },
      { label: 'vs baseline', cls: 'right' }, ''],
    rows, { empty: 'No publications match.' }), { flush: true })
    + `<div class="row between" style="margin-top:10px">
        <span class="muted">${page.items.length} of ${page.total}</span>
      </div>`
    + (page.notes.length ? `<div class="card" style="margin-top:12px">
        <h3>Reading this table</h3>
        <ul class="muted" style="margin:0;padding-left:18px;font-size:13px">
          ${page.notes.map((n) => `<li>${esc(n)}</li>`).join('')}
        </ul></div>` : '');

  const apply = (patch) => {
    const sp = new URLSearchParams();
    const merged = { order, q, ...patch };
    if (merged.order) sp.set('order', merged.order);
    if (merged.q) sp.set('q', merged.q);
    location.hash = `#/research/${id}${sp.toString() ? `?${sp}` : ''}`;
  };
  let t;
  document.getElementById('p-q').addEventListener('input', (e) => {
    clearTimeout(t);
    const v = e.target.value;
    t = setTimeout(() => apply({ q: v }), 350);
  });
  document.getElementById('p-order').addEventListener('change', (e) =>
    apply({ order: e.target.value }));
}
