/**
 * The views. Each exports `render(root, params)` and owns one surface.
 *
 * Views read from `api.js` and compose `components.js`. They hold no state
 * beyond what is in the URL, so a reload always reproduces what you were
 * looking at — which matters for a review queue you come back to.
 */

import { api, bridgeMediaUrl, localBridge } from './api.js';
import {
  ageQualified, bar, card, compact, dateOnly, dateTime, empty, esc, evidence,
  postLink,
  kv, note, num, platformChip, player, secs, spinner, stat, stateChip, table,
  toast,
} from './components.js';

// ---------------------------------------------------------------- dashboard

/**
 * The home screen, answering questions rather than reporting health.
 *
 * What was here before - snapshot counts, token expiry, media
 * coverage, collection runs - is all still here, below, under "How
 * the system is doing". It is necessary and it is not what a creator
 * opens the product to find out, so it is second rather than gone.
 */
export const dashboard = {
  title: 'Home',
  async render(root) {
    root.innerHTML = `<h1>Home</h1>${spinner()}`;
    let h = null;
    const [o] = await Promise.all([
      api.overview(),
      api.home().then((d) => { h = d; }).catch(() => { h = null; }),
    ]);

    const platforms = o.platforms.map((p) => {
      const health = !p.connected
        ? stateChip('absent', null).replace('no media', 'not connected')
        : (p.needs_reauth
          ? '<span class="chip danger"><span class="dot"></span>needs re-auth</span>'
          : '<span class="chip ok"><span class="dot"></span>connected</span>');
      return card(p.platform === 'tiktok' ? 'TikTok' : 'Instagram', `
        <div class="row between" style="margin-bottom:10px">
          ${health}
          ${p.handle ? `<span class="faint mono">${esc(p.handle)}</span>` : ''}
        </div>
        ${kv([
          ['publications', num(p.publications)],
          ['snapshots', num(p.snapshots)],
          ['first post', dateOnly(p.first_published)],
          ['latest post', dateOnly(p.last_published)],
          ['token expires', p.token_expires_at
            ? dateTime(p.token_expires_at)
            : '<span class="faint">does not expire</span>'],
        ])}`);
    }).join('');

    const media = o.media.filter((m) => m.total).map((m) => {
      if (!m.media_obtainable) {
        return card(`${m.platform} media`, note(`This platform's API returns
          <b>no downloadable media</b>, so there is nothing to preserve and a
          coverage percentage would be meaningless. Matching on this platform
          uses metadata only.`, 'warn'));
      }
      const reasons = Object.entries(m.by_reason_class || {})
        .map(([k, v]) => `<span class="chip" title="${esc(k)}">${
          esc(reasonLabel(k))} ${v}</span>`).join(' ');
      return card(`${m.platform} media`, `
        <div class="row between"><span class="num" style="font-size:22px;font-weight:650">
          ${m.coverage_pct === null ? '—' : `${m.coverage_pct}%`}</span>
          <span class="muted">${num(m.preserved)} of ${num(m.preservable)} preservable</span>
        </div>
        <div style="margin:9px 0">${bar((m.preserved || 0) / (m.preservable || 1))}</div>
        <div class="row">${reasons || '<span class="faint">no gaps</span>'}</div>
        <div class="faint" style="font-size:12px;margin-top:8px">
          Publications that are not video are excluded from the denominator —
          counting an image post as missing video would overstate the gap.
        </div>`);
    }).join('');

    const runs = table(
      ['platform', 'kind', 'status', 'started', { label: 'seen', cls: 'right' },
        { label: 'new', cls: 'right' }, { label: 'snapshots', cls: 'right' }],
      o.recent_runs.map((r) => `<tr>
        <td>${platformChip(r.platform)}</td>
        <td class="muted" title="${esc(r.kind)}">${esc(runLabel(r.kind))}</td>
        <td>${stateChip(r.status, null)}</td>
        <td class="nowrap muted">${dateTime(r.started_at)}</td>
        <td class="right">${num(r.videos_seen)}</td>
        <td class="right">${num(r.videos_new)}</td>
        <td class="right">${num(r.snapshots_written)}</td>
      </tr>`),
      { empty: 'No collection runs recorded yet.' });

    const rv = o.review;
    const oneDay = o.snapshot_days <= 1;

    root.innerHTML = `
      <h1>Home</h1>
      ${h ? homeTop(h) : note(`The summary could not be loaded, so only
        system health is shown below.`, 'warn')}

      <details class="group"><summary><h2>How the system is doing</h2>
        <span class="sub">collection, media, review queue</span></summary>
      <p class="lede">System health and what needs you.
        <span class="faint">Generated ${dateTime(o.generated_at)}.</span></p>

      <div class="grid cols-4">
        ${stat('Publications',
          num(o.platforms.reduce((a, p) => a + p.publications, 0)),
          'TikTok + Instagram')}
        ${stat('Snapshots',
          num(o.platforms.reduce((a, p) => a + p.snapshots, 0)),
          `across ${o.snapshot_days} capture day${o.snapshot_days === 1 ? '' : 's'}`)}
        ${stat('Local masters', num(o.local_masters),
          `${num(o.performances)} performances`)}
        ${stat('Needs you',
          num(rv.master_links_pending + rv.publication_links_proposed),
          `${rv.master_links_pending} links · ${rv.publication_links_proposed} pairs`)}
      </div>

      ${oneDay ? note(`<strong>Every snapshot so far was captured on a single
        day.</strong> A fixed-age anchor needs two readings that bracket the
        anchor age, so <code>views@7d</code> cannot be computed for any
        publication yet and must never be reconstructed. The daily collector
        builds that history from here on.`, 'warn') : ''}

      <h2>Connected platforms</h2>
      <div class="grid cols-2">${platforms}</div>

      <h2>Media preservation</h2>
      <div class="grid cols-2">${media}</div>

      <h2>Review queue</h2>
      <div class="grid cols-3">
        ${stat('Pending master links', num(rv.master_links_pending),
          '<a href="#/review/masters">review</a>')}
        ${stat('Auto-linked, unverified', num(rv.master_links_auto_unverified),
          'machine-proposed, no human decision')}
        ${stat('Confirmed by you', num(rv.master_links_human_decided),
          'human decisions, never overwritten')}
        ${stat('Cross-platform proposals', num(rv.publication_links_proposed),
          '<a href="#/review/pairs">review</a>')}
        ${stat('Pairs confirmed', num(rv.publication_links_confirmed), '')}
        ${stat('Performances inferred', num(rv.performances_inferred),
          `${rv.performances_confirmed} confirmed`)}
      </div>

      <h2>Recent collection runs</h2>
      ${card('', runs, { flush: true })}
      </details>`;
  },
};

/**
 * The six questions a creator opens the product with, answered from
 * counts taken at request time. Nothing here is configured.
 */
function homeTop(h) {
  const mine = h.mine.map((m) => `
    <div class="home-plat">
      <div class="home-plat-name">${esc(m.label)}</div>
      <div class="home-plat-n">${num(m.publications)}</div>
      <div class="sub">videos collected &middot; ${num(m.analysed)} measured</div>
    </div>`).join('');

  const findings = h.findings.length
    ? `<ul class="plain-list">${h.findings.map((f) => `<li>
        ${esc(f.headline)}
        <span class="chip ${f.direction === 'encouraging' ? 'ok' : 'warn'}"
          >${esc(f.platform === 'instagram' ? 'Instagram' : 'TikTok')}</span>
      </li>`).join('')}</ul>
      ${h.findings_total > h.findings.length
    ? `<p class="sub">${h.findings_total - h.findings.length} more on the
        <a href="#/hooks/retention">findings page</a>.</p>` : ''}`
    : `<p>Nothing yet. Findings need videos that have been measured and
        that carry retention data, which today means your Instagram
        account only.</p>`;

  const reports = h.reports.length
    ? `<div class="row" style="flex-wrap:wrap;gap:6px">${h.reports.map(
      (r) => `<a class="chip ${r.is_own_account ? 'ok' : ''}"
        href="#/research/${r.creator_id}/report">${
  r.is_own_account ? 'You' : esc(r.handle)} &middot; ${
  r.platform === 'tiktok' ? 'TikTok' : 'Instagram'}</a>`).join('')}</div>`
    : '';

  const tries = h.try_next.length
    ? `<ul class="plain-list">${h.try_next.map(
      (t) => `<li>${esc(t)}</li>`).join('')}</ul>`
    : `<p>Nothing worth proposing yet. Suggesting an experiment from
        this much data would be dressing a guess as a plan.</p>`;

  return `
    <section class="card home-hero">
      <h2>What we have</h2>
      <div class="home-plats">${mine}</div>
      <p class="sub">Plus ${num(h.competitors.publications)} videos from
        ${num(h.competitors.creators)} other creators we are studying.
        ${h.measurement_counts.usable} of
        ${h.measurement_counts.total} things we can measure are currently
        trustworthy enough to act on.</p>
      ${reports ? `<h3>Your reports</h3>${reports}` : ''}
    </section>

    <section class="card">
      <h2>What we have found</h2>
      ${findings}
    </section>

    <section class="card">
      <h2>How much to trust it</h2>
      <p>${esc(h.uncertain.nothing_is_proven)}</p>
      <p class="sub">${h.uncertain.measurements_we_do_not_trust} of the
        things we can measure are not reliable enough to use. They are
        listed, with the reason, in
        <a href="#/glossary">What everything means</a>.</p>
    </section>

    <section class="card">
      <h2>What you could try next</h2>
      ${tries}
    </section>`;
}

// ------------------------------------------------------------------ library

export const library = {
  title: 'Content library',
  async render(root, params) {
    const state = {
      platform: params.get('platform') || '',
      q: params.get('q') || '',
      linked: params.get('linked') || '',
      has_media: params.get('has_media') || '',
      order: params.get('order') || 'newest',
      offset: Number(params.get('offset') || 0),
      limit: 50,
    };

    root.innerHTML = `<h1>Content library</h1>
      <p class="lede">Every publication collected from your own accounts.
        Each view count carries the age it was observed at, because two
        differently-aged counts are not comparable.</p>
      <div id="lib-toolbar"></div><div id="lib-body">${spinner()}</div>`;

    document.getElementById('lib-toolbar').innerHTML = `
      <div class="toolbar">
        <div class="grow"><label for="f-q">Search caption or id</label>
          <input id="f-q" type="search" value="${esc(state.q)}"
                 placeholder="chopin, improv, 1809…"></div>
        <div class="field"><label for="f-platform">Platform</label>
          <select id="f-platform">
            <option value="">both</option>
            <option value="tiktok"${state.platform === 'tiktok' ? ' selected' : ''}>TikTok</option>
            <option value="instagram"${state.platform === 'instagram' ? ' selected' : ''}>Instagram</option>
          </select></div>
        <div class="field"><label for="f-linked">Local master</label>
          <select id="f-linked">
            <option value="">any</option>
            <option value="yes"${state.linked === 'yes' ? ' selected' : ''}>linked</option>
            <option value="no"${state.linked === 'no' ? ' selected' : ''}>not linked</option>
          </select></div>
        <div class="field"><label for="f-media">Media</label>
          <select id="f-media">
            <option value="">any</option>
            <option value="true"${state.has_media === 'true' ? ' selected' : ''}>stored</option>
            <option value="false"${state.has_media === 'false' ? ' selected' : ''}>not stored</option>
          </select></div>
        <div class="field"><label for="f-order">Order</label>
          <select id="f-order">
            <option value="newest"${state.order === 'newest' ? ' selected' : ''}>newest</option>
            <option value="oldest"${state.order === 'oldest' ? ' selected' : ''}>oldest</option>
            <option value="duration"${state.order === 'duration' ? ' selected' : ''}>longest</option>
          </select></div>
      </div>`;

    const apply = (patch) => {
      const next = new URLSearchParams();
      const merged = { ...state, ...patch, offset: patch.offset ?? 0 };
      for (const k of ['platform', 'q', 'linked', 'has_media', 'order']) {
        if (merged[k]) next.set(k, merged[k]);
      }
      if (merged.offset) next.set('offset', merged.offset);
      location.hash = `#/library${next.toString() ? `?${next}` : ''}`;
    };

    let debounce;
    document.getElementById('f-q').addEventListener('input', (e) => {
      clearTimeout(debounce);
      const v = e.target.value;
      debounce = setTimeout(() => apply({ q: v }), 350);
    });
    const bind = (id, key) => document.getElementById(id)
      .addEventListener('change', (e) => apply({ [key]: e.target.value }));
    bind('f-platform', 'platform');
    bind('f-linked', 'linked');
    bind('f-media', 'has_media');
    bind('f-order', 'order');

    const page = await api.videos({
      platform: state.platform || undefined,
      q: state.q || undefined,
      linked: state.linked || undefined,
      has_media: state.has_media || undefined,
      order: state.order,
      limit: state.limit,
      offset: state.offset,
    });

    const rows = page.items.map((v) => `<tr>
      <td>${platformChip(v.platform)}</td>
      <td class="nowrap">${dateOnly(v.posted_at)}</td>
      <td><a href="#/video/${v.id}">${esc(
        v.caption_preview || v.external_id)}</a>
        ${v.permalink ? `<div class="sub">${
    postLink(v.permalink, v.platform, { label: 'see the post' })}</div>` : ''}
      </td>
      <td class="right nowrap">${secs(v.duration_s)}</td>
      <td class="right nowrap">${ageQualified(v.latest_views, v.latest_age_days)}</td>
      <td>${v.master_links
        ? stateChip(v.master_link_verified ? 'confirmed' : 'auto',
          v.master_link_verified ? 'human' : 'matcher')
        : '<span class="faint">—</span>'}</td>
      <td class="right">${v.cross_platform_links || '<span class="faint">—</span>'}</td>
      <td>${v.has_media
        ? '<span class="chip ok"><span class="dot"></span>stored</span>'
        : stateChip(v.media_status, null)}</td>
    </tr>`);

    const from = page.total ? page.offset + 1 : 0;
    const to = Math.min(page.offset + page.limit, page.total);
    const pager = `<div class="row between" style="margin-top:12px">
      <span class="muted">${from}–${to} of ${num(page.total)}</span>
      <span class="row">
        <button ${page.offset === 0 ? 'disabled' : ''} id="prev">Previous</button>
        <button ${to >= page.total ? 'disabled' : ''} id="next">Next</button>
      </span></div>`;

    document.getElementById('lib-body').innerHTML = card('', table(
      ['platform', 'posted', 'caption', { label: 'duration', cls: 'right' },
        { label: 'latest views', cls: 'right' }, 'local master',
        { label: 'pairs', cls: 'right' }, 'media'],
      rows, { empty: 'No publications match these filters.' }),
      { flush: true }) + pager;

    document.getElementById('prev')?.addEventListener('click', () =>
      apply({ offset: Math.max(0, state.offset - state.limit) }));
    document.getElementById('next')?.addEventListener('click', () =>
      apply({ offset: state.offset + state.limit }));
  },
};

// -------------------------------------------------------------- video detail

export const videoDetail = {
  title: 'Publication',
  async render(root, params, id) {
    root.innerHTML = `${spinner('Loading publication')}`;
    const v = await api.video(id);

    const history = table(
      ['captured', { label: 'age', cls: 'right' }, { label: 'views', cls: 'right' },
        { label: 'likes', cls: 'right' }, { label: 'comments', cls: 'right' },
        { label: 'shares', cls: 'right' }, { label: 'saves', cls: 'right' },
        { label: 'avg watch', cls: 'right' }, { label: 'skip rate', cls: 'right' }],
      v.history.map((h) => `<tr>
        <td class="nowrap">${dateTime(h.captured_at)}</td>
        <td class="right">${h.age_days === null ? '<span class="unknown"></span>'
          : `${h.age_days}d`}</td>
        <td class="right">${num(h.views)}${h.is_rounded
          ? ' <span class="faint" title="the source rounded this figure">≈</span>' : ''}</td>
        <td class="right">${num(h.likes)}</td>
        <td class="right">${num(h.comments)}</td>
        <td class="right">${num(h.shares)}</td>
        <td class="right">${num(h.saves)}</td>
        <td class="right">${h.avg_watch_time_ms === null
          ? '<span class="unknown"></span>'
          : num(h.avg_watch_time_ms / 1000, { digits: 1, suffix: 's' })}</td>
        <td class="right">${h.skip_rate_pct === null
          ? '<span class="unknown"></span>'
          : num(h.skip_rate_pct, { digits: 1, suffix: '%' })}</td>
      </tr>`),
      { empty: 'No snapshots recorded for this publication.' });

    const anchors = Object.entries(v.anchors || {}).map(([k, state]) =>
      `<tr><td class="mono">${esc(k)}</td><td class="${
        state === 'available' ? '' : 'muted'}">${esc(state)}</td></tr>`).join('');

    const masters = v.master_links.length
      ? v.master_links.map((m) => card('', `
          <div class="row between" style="margin-bottom:8px">
            <strong>${esc(m.filename)}</strong>
            ${stateChip(m.status, m.decided_by)}
          </div>
          ${kv([
            ['performance', m.performance ? esc(m.performance)
              : '<span class="faint">ungrouped</span>'],
            ['combined score', num(m.score, { digits: 3 })],
            ['audio agreement', `${num(m.audio_similarity, { digits: 3 })}
              <span class="chip">${esc(m.audio_method || 'none')}</span>`],
            ['audio offset', m.audio_offset_s
              ? `${num(m.audio_offset_s, { digits: 2, suffix: 's' })}
                 <span class="faint">— a trimmed head or different hook</span>`
              : num(m.audio_offset_s, { digits: 2, suffix: 's' })],
            ['frame overlap', num(m.frame_overlap, { digits: 3 })],
            ['duration delta', num(m.duration_delta_s, { digits: 2, suffix: 's' })],
            ['runner-up', num(m.runner_up, { digits: 3 })],
            ['decided', m.decided_at ? dateTime(m.decided_at) : '—'],
          ])}
          ${m.audio_method && m.audio_method !== 'spectral'
            ? note(`Scored with the <b>${esc(m.audio_method)}</b> descriptor,
                which was measured and found unable to separate a
                different-hook cross-post from an unrelated performance.`, 'warn')
            : ''}
          ${m.evidence?.variant_ambiguous
            ? note(`The <b>performance</b> is confident; which export was used
                is open. The filename above is one of several tied variants.`, 'warn')
            : ''}
          ${evidence(m.evidence)}`)).join('')
      : empty('No local master linked',
        'Either this publication predates the local archive, or no master '
        + 'scored high enough to propose.');

    const pairs = v.publication_links.length
      ? table(['platform', 'posted', { label: 'duration', cls: 'right' },
        { label: 'latest views', cls: 'right' }, 'relationship', 'state'],
      v.publication_links.map((p) => `<tr>
          <td>${platformChip(p.other_platform)}</td>
          <td class="nowrap"><a href="#/video/${p.other_video_id}">${
            dateOnly(p.other_posted_at)}</a></td>
          <td class="right">${secs(p.other_duration_s)}</td>
          <td class="right nowrap">${ageQualified(
            p.other_latest_views, p.other_latest_age_days)}</td>
          <td class="muted">${esc(p.relationship_type)}</td>
          <td>${stateChip(p.status, p.decided_by)}</td>
        </tr>`))
      : empty('No cross-platform pair',
        'No counterpart has been proposed on the other platform.');

    root.innerHTML = `
      <div class="row between">
        <h1>${platformChip(v.platform)} ${dateOnly(v.posted_at)}</h1>
        <a href="#/library">&larr; library</a>
      </div>
      <p class="lede mono faint">${esc(v.external_id)}</p>

      <div class="grid cols-2">
        <div class="card">
          ${player(v.media_url, { status: v.media_status,
            reason: v.media_reason, poster: v.cover_url })}
        </div>
        <div class="card">
          <h3>Publication</h3>
          ${kv([
            ['posted', dateTime(v.posted_at)],
            ['duration', secs(v.duration_s)],
            ['creator', v.creator_handle ? esc(v.creator_handle) : '—'],
            ['whose account', v.is_own_account ? 'yours' : 'another creator'],
            ['where it came from', sourceLabel(v.source)],
            ['video file', `${stateChip(v.media_status, null)}${v.media_reason
              ? ` <span class="faint">${esc(reasonLabel(v.media_reason))}
                </span>` : ''}`],
          ])}
          ${v.permalink
    ? `<div style="margin-top:10px">${postLink(v.permalink, v.platform,
      { label: 'See this post on ' + (v.platform === 'tiktok'
        ? 'TikTok' : 'Instagram') })}</div>`
    : ''}
          <h3 style="margin-top:14px">Caption</h3>
          <div class="caption-text">${esc(v.caption || '(no caption)')}</div>
          ${v.hashtags.length ? `<div class="row" style="margin-top:9px">${
            v.hashtags.slice(0, 18).map((h) =>
              `<span class="chip">#${esc(h)}</span>`).join('')}</div>` : ''}
        </div>
      </div>

      <h2>Performance history</h2>
      ${card('', history, { flush: true })}
      ${note(`Figures are <b>cumulative lifetime</b> readings at the ages
        shown. Comparing counts taken at different ages measures elapsed
        exposure, not content quality. A dash means the platform does not
        report that metric — TikTok exposes no saves field at all, and that
        is never rendered as zero.`)}

      <h2>Fixed-age anchors</h2>
      ${card('', `<table><tbody>${anchors}</tbody></table>`, { flush: true })}

      <h2>Local master${v.master_links.length === 1 ? '' : 's'}</h2>
      <div class="grid cols-2">${masters}</div>

      <h2>Cross-platform</h2>
      ${card('', pairs, { flush: true })}`;
  },
};

// ------------------------------------------------------------- review views

async function masterReviewBody(items, bridge) {
  if (!items.length) {
    return empty('Nothing pending',
      'Every proposed master link has been decided.');
  }
  const it = items[0];
  const m = it.link;
  const ev = m.evidence || {};
  const masterPlayer = bridge && it.master_local_path
    ? `<video controls preload="metadata" src="${esc(
      bridgeMediaUrl(bridge, it.master_local_path))}"></video>`
    : note(`The local master lives on <code>D:</code> on your workstation and
        is never transferred to the server, so it cannot be played here.
        ${bridge ? '' : 'Start <code>python scripts/review_ui.py</code> on that '
          + 'machine to play it side by side.'}`, '');

  return `
    <div class="row decide" data-link="${m.id}">
      <button class="primary" data-answer="confirmed">Yes, same video</button>
      <button class="warn" data-answer="variant">Same performance, different edit</button>
      <button class="danger" data-answer="rejected">No, not a match</button>
      <button data-answer="skip">Skip</button>
    </div>
    <div class="grid cols-2">
      <div class="card">
        <h3>Local master</h3>
        ${masterPlayer}
        ${kv([
          ['file', esc(m.filename)],
          ['duration', secs(m.master_duration_s)],
          ['performance', m.performance ? esc(m.performance)
            : '<span class="faint">ungrouped</span>'],
        ])}
      </div>
      <div class="card">
        <h3>${esc(it.video.platform)} publication</h3>
        ${player(it.video_media_url, { status: it.video.media_status })}
        ${kv([
          ['posted', dateOnly(it.video.posted_at)],
          ['duration', secs(it.video.duration_s)],
          ['latest views', ageQualified(it.video.latest_views,
            it.video.latest_age_days)],
        ])}
        <div class="caption-text" style="margin-top:9px">${esc(
          it.video_caption || '(no caption)')}</div>
      </div>
    </div>

    <div class="card" style="margin-top:14px">
      <h3>Evidence</h3>
      ${kv([
        ['combined score', num(m.score, { digits: 3 })],
        ['audio agreement', `${num(m.audio_similarity, { digits: 3 })}
          <span class="chip">${esc(m.audio_method || 'none')}</span>`],
        ['audio coverage', num(ev.coverage, { digits: 2 })],
        ['audio offset', num(m.audio_offset_s, { digits: 2, suffix: 's' })],
        ['frame overlap', num(m.frame_overlap, { digits: 3 })],
        ['duration delta', num(m.duration_delta_s, { digits: 2, suffix: 's' })],
        ['runner-up', num(m.runner_up, { digits: 3 })],
        ['tied variants', (ev.tied_variants || []).map(esc).join(', ')],
      ])}
      ${m.audio_method !== 'spectral'
        ? note(`Audio agreement came from the <b>${esc(m.audio_method
          || 'none')}</b> descriptor, which cannot separate a different-hook
          cross-post from an unrelated performance. Judge from the video.`, 'warn')
        : ''}
      ${ev.variant_ambiguous
        ? note(`The performance is confident; only the export is open. If this
          is the right performance but a different export, choose
          <b>different edit</b>.`, 'warn')
        : ''}
      ${evidence(ev)}
    </div>

    <p class="faint" style="font-size:13px">Saved immediately and attributed
      to you. Close the tab whenever — the queue resumes here.</p>`;
}

export const reviewMasters = {
  title: 'Review · master links',
  async render(root) {
    root.innerHTML = `<h1>Review master links</h1>${spinner()}`;
    const [items, bridge] = await Promise.all([
      api.masterLinks({ status: 'pending', limit: 50 }),
      localBridge(),
    ]);

    const draw = async (list) => {
      root.innerHTML = `
        <div class="row between">
          <h1>Review master links</h1>
          <span class="chip">${list.length} pending</span>
        </div>
        <p class="lede">Is this local file the source of this publication?
          Nothing here blocks other work.</p>
        <div id="rv">${await masterReviewBody(list, bridge)}</div>`;

      root.querySelector('[data-link]')?.addEventListener('click', async (e) => {
        const btn = e.target.closest('button[data-answer]');
        if (!btn) return;
        const id = Number(e.currentTarget.dataset.link);
        const answer = btn.dataset.answer;
        root.querySelectorAll('button[data-answer]').forEach((b) => {
          b.disabled = true;
        });
        try {
          if (answer === 'skip') {
            await draw(list.slice(1));
            return;
          }
          const res = await api.decideMasterLink(id, answer);
          toast(`Recorded: ${res.status}. ${res.remaining} left.`);
          await draw(list.filter((x) => x.link.id !== id));
        } catch (err) {
          toast(err.message, true);
          root.querySelectorAll('button[data-answer]').forEach((b) => {
            b.disabled = false;
          });
        }
      });
    };
    await draw(items);
  },
};

export const reviewPairs = {
  title: 'Review · cross-platform',
  async render(root) {
    root.innerHTML = `<h1>Review cross-platform pairs</h1>${spinner()}`;
    const items = await api.publicationLinks({ status: 'proposed', limit: 50 });

    const side = (s) => `<div class="card">
      <h3>${esc(s.platform)}</h3>
      ${player(s.media_url, { poster: s.cover_url })}
      ${kv([
        ['posted', dateTime(s.posted_at)],
        ['duration', secs(s.duration_s)],
        ['latest views', ageQualified(s.latest_views, s.latest_age_days)],
        ['local master', s.master_filename ? esc(s.master_filename)
          : '<span class="faint">none</span>'],
        ['performance', s.performance ? esc(s.performance)
          : '<span class="faint">—</span>'],
      ])}
      <div class="caption-text" style="margin-top:9px">${esc(
        s.caption || '(no caption)')}</div>
    </div>`;

    const draw = (list) => {
      if (!list.length) {
        root.innerHTML = `<h1>Review cross-platform pairs</h1>${
          empty('Nothing proposed', 'Every proposed pair has been decided.')}`;
        return;
      }
      const p = list[0];
      const ev = p.evidence || {};
      const sig = ev.signals || {};
      root.innerHTML = `
        <div class="row between">
          <h1>Same video on both platforms?</h1>
          <span class="chip">${list.length} proposed</span>
        </div>
        <div class="row decide" data-pair="${p.id}">
          <button class="primary" data-answer="confirmed">Yes, same video</button>
          <button class="warn" data-answer="platform_edit">Same performance, different edit</button>
          <button class="danger" data-answer="rejected">No, different videos</button>
          <button data-answer="skip">Skip</button>
        </div>
        ${note(`This proposal rests on <b>metadata only</b> — duration, date
          and caption. TikTok returns no media file, and the cover image was
          measured and rejected as a matching signal, so nothing here can
          establish identity on its own. It is proposed for your judgement,
          not asserted.`, 'warn')}
        ${(ev.margin ?? 1) < 0.10 ? note(`The runner-up scored almost as
          well, so another publication fits nearly as convincingly.`, 'danger') : ''}

        <div class="grid cols-2">${side(p.a)}${side(p.b)}</div>

        <div class="card" style="margin-top:14px">
          <h3>Evidence</h3>
          ${kv([
            ['tier', `<span class="chip proposed">${esc(ev.tier || '?')}</span>`],
            ['combined score', num(p.score, { digits: 3 })],
            ['agreeing signals', `${ev.agreeing ?? '?'} of 3`],
            ['duration', `${num(sig.duration, { digits: 2 })}
              <span class="faint">(Δ ${
                num(ev.duration_delta_s, { digits: 2, suffix: 's' })})</span>`],
            ['date', `${num(sig.date, { digits: 2 })}
              <span class="faint">(gap ${ev.day_gap ?? '?'} day(s))</span>`],
            ['caption / hashtags', `${num(sig.caption, { digits: 2 })}
              <span class="faint">(tokens ${num(ev.token_jaccard, { digits: 2 })},
              tags ${num(ev.hashtag_jaccard, { digits: 2 })})</span>`],
            ['margin over runner-up', num(ev.margin, { digits: 2 })],
          ])}
          ${evidence(ev)}
        </div>

`;

      root.querySelector('[data-pair]').addEventListener('click', async (e) => {
        const btn = e.target.closest('button[data-answer]');
        if (!btn) return;
        const id = Number(e.currentTarget.dataset.pair);
        const answer = btn.dataset.answer;
        root.querySelectorAll('button[data-answer]').forEach((b) => {
          b.disabled = true;
        });
        try {
          if (answer === 'skip') {
            draw(list.slice(1));
            return;
          }
          const res = await api.decidePublicationLink(id, answer);
          toast(`Recorded: ${res.status}. ${res.remaining} left.`);
          draw(list.filter((x) => x.id !== id));
        } catch (err) {
          toast(err.message, true);
          root.querySelectorAll('button[data-answer]').forEach((b) => {
            b.disabled = false;
          });
        }
      });
    };
    draw(items);
  },
};

// --------------------------------------------------- competitor intelligence

export const competitors = {
  title: 'What we cannot do yet',
  async render(root) {
    root.innerHTML = `<h1>Competitor intelligence</h1>${spinner()}`;
    const { capabilities } = await api.capabilities();
    const planned = capabilities.filter((c) => !c.implemented);

    const cards = planned.map((c) => card('', `
      <div class="row between" style="margin-bottom:8px">
        <strong>${esc(c.title)}</strong>
        <span class="chip">Phase ${esc(c.phase)}</span>
      </div>
      <p class="muted" style="margin:0 0 10px">${esc(c.summary)}</p>
      ${Object.keys(c.counts).length ? `<div class="row" style="margin-bottom:10px">${
        Object.entries(c.counts).map(([k, v]) =>
          `<span class="chip" title="${esc(k)}">${
    esc(reasonLabel(k))}: ${v}</span>`).join('')
      }</div>` : ''}
      <h3>Not available yet because</h3>
      <ul class="muted" style="margin:0;padding-left:18px;font-size:13px">
        ${c.blocked_by.map((b) => `<li>${esc(b)}</li>`).join('')}
      </ul>`)).join('');

    const done = capabilities.filter((c) => c.implemented);
    root.innerHTML = `
      <h1>What we cannot do yet</h1>
      <p class="lede">Every capability, whether it works and what
        stops it. The page is built from the database rather than
        written down, because a page about limits that is itself out
        of date is worse than no page.</p>

      ${note(`<strong>Nothing here is fabricated.</strong> Each count
        is read from the database when you load the page. A
        capability says it works only when something measured says
        so.`, 'accent')}

      ${planned.length ? `<h2>Not there yet (${planned.length})</h2>
        <div class="grid cols-2">${cards}</div>`
    : `<p class="muted">Everything listed is working.</p>`}

      <h2>Working (${done.length})</h2>
      ${card('', `<ul class="plain">${done.map((c) => `<li>
        <b>${esc(c.title)}</b>
        ${Object.entries(c.counts || {}).map(([k, v]) =>
    `<span class="chip">${esc(reasonLabel(k))}: ${v}</span>`).join(' ')}
        ${(c.blocked_by || []).length ? `<br><span class="muted">
          ${c.blocked_by.map((b) => esc(b)).join('; ')}</span>` : ''}
      </li>`).join('')}</ul>`)}

      <p class="faint" style="font-size:13px">Plan:
        <code>DEVELOPMENT_PLAN.md</code> ·
        Vision: <code>docs/PRODUCT_VISION.md</code> §3.A–C</p>`;
  },
};

export const notFound = {
  title: 'Not found',
  async render(root) {
    root.innerHTML = `<h1>Not found</h1>${empty('No such view',
      'Check the link, or go back to the dashboard.')}`;
  },
};


// ------------------------------------------------------- feature catalogue

/**
 * M4. Every feature with the grade it earned, and Unavailable rendered as
 * Unavailable rather than as a blank.
 *
 * The blank is the thing this screen exists to prevent. A feature with no
 * extractor, a feature withheld by a benchmark and a feature that simply
 * measured zero all look identical as an empty cell, and only one of them
 * means "we could tell you but the answer is nothing". So every row shows
 * its grade, and every Unavailable row shows the reason it is unavailable.
 */
export const featureCatalogue = {
  title: 'Feature catalogue',
  async render(root, params) {
    const grade = params?.grade || '';
    const modality = params?.modality || '';
    root.innerHTML = `<h1>Feature catalogue</h1>${spinner()}`;
    const c = await api.featureCatalogue({ grade, modality });

    const s = c.summary;
    const tiles = c.grades.map((g) =>
      stat(g, s.by_grade[g] ?? 0, GRADE_BLURB[g])).join('');

    const filters = [
      `<a href="#/features" class="chip${!grade && !modality ? ' ok' : ''}">all ${s.features}</a>`,
      ...c.grades.map((g) => `<a href="#/features?grade=${g}"
        class="chip${grade === g ? ' ok' : ''}">${esc(g)} ${s.by_grade[g] ?? 0}</a>`),
      ...c.modalities
        .filter((m) => s.by_modality[m])
        .map((m) => `<a href="#/features?modality=${m}"
          class="chip${modality === m ? ' ok' : ''}">${esc(m)}</a>`),
    ].join(' ');

    const rows = c.features.map((f) => {
      const unavailable = f.grade === 'Unavailable';
      const why = f.grade_reason || '';
      const platforms = f.platforms
        .filter((p) => !p.available)
        .map((p) => `<span class="chip danger" title="${esc(p.reason || '')}"
          >no ${esc(p.platform)}</span>`).join(' ');
      // Plain name first, description second, variable name last and
      // small. A reader who does not already know the key has no way
      // into a table that leads with it.
      const plain = f.plain || {};
      return `<tr${unavailable ? ' class="is-unavailable"' : ''}>
        <td><strong>${esc(f.display_name || f.name)}</strong>${f.withheld
          ? ' <span class="chip warn">withheld</span>' : ''}
          <div class="sub">${esc(plain.measures || f.describes)}</div>
          ${plain.not_a_measure_of
    ? `<div class="sub"><em>Not a measure of ${
      esc(plain.not_a_measure_of)}</em></div>` : ''}
          <div class="gl-key technical"><code>${esc(f.name)}</code>${
  f.method_version ? ` &middot; ${esc(f.method_version)}` : ''}</div></td>
        <td>${esc(f.modality)}</td>
        <td>${gradeChip(f.grade)}</td>
        <td class="num">${unavailable && !f.coverage
          ? '<span class="sub">none</span>' : num(f.coverage)}</td>
        <td><div class="sub technical">${esc(why)}</div>
          <div class="sub">${esc(plainWhy(f))}</div>${platforms}</td>
        <td>${f.evidence.length
          ? `<span class="chip ok">${f.evidence.length} finding(s)</span>
             <div class="technical">${evidence(f.evidence,
    'Exact figures')}</div>`
          : '<span class="sub">not tested</span>'}</td>
      </tr>`;
    }).join('');

    root.innerHTML = `
      <h1>Feature catalogue</h1>
      ${note(`Every feature this project can measure, with the grade it has
        earned from evidence. For plain-English definitions of all of
        them, see <a href="#/glossary">What everything means</a>. A grade is derived on every request from
        extractor coverage, the withheld list and the stored association
        results - it is never written down, so it cannot go stale.
        <strong>${s.usable_in_a_recommendation}</strong> of
        ${s.features} may drive a recommendation.`)}
      <div class="stats">${tiles}</div>
      <div class="filters">${filters}</div>
      ${card(`Features (${c.showing} shown)`, table(
        ['Feature', 'Modality', 'Grade', 'Coverage', 'Why this grade',
         'Evidence'],
        [rows].filter(Boolean),
        { empty: 'No feature matches this filter.' }), { flush: true })}
      ${card('What the grades mean', kv(
        c.grades.map((g) => [g, c.grade_meanings[g]])))}`;
  },
};

const GRADE_BLURB = {
  Unavailable: 'never usable; the gap is shown',
  Observed: 'measured; may describe',
  Hypothesised: 'may only become an experiment',
  Associated: 'survived correction and the ad covariate',
  Validated: 'survived a pre-registered experiment',
};

function gradeChip(grade) {
  const cls = {
    Unavailable: 'danger', Observed: '', Hypothesised: 'warn',
    Associated: 'ok', Validated: 'ok',
  }[grade] ?? '';
  return `<span class="chip ${cls}">${esc(grade)}</span>`;
}


// ------------------------------------------------------------- glossary

/**
 * What everything means, in English.
 *
 * This page exists because the rest of the product was unusable
 * without it. Every other surface shows measurements; a reader who
 * does not already know what `background_edge_density` is has no way
 * in, and "it is in the code" is not an answer. So: one page, every
 * measurement, plain language first, the variable name last and small.
 *
 * The most important line in each entry is "This is NOT a measure
 * of". A name like `text_region_score` invites exactly one wrong
 * reading, and the only reliable defence is to print the wrong
 * reading next to the right one.
 */
export const glossary = {
  title: 'What everything means',
  async render(root, params) {
    const q = (params?.q || '').toLowerCase();
    root.innerHTML = `<h1>What everything means</h1>${spinner()}`;
    const [lexicon, cat] = await Promise.all([
      api.featureLexicon(), api.featureCatalogue(),
    ]);

    const grades = {};
    for (const f of cat.features) grades[f.name] = f;

    const match = (key, p) => !q
      || key.toLowerCase().includes(q)
      || (p.display_name || '').toLowerCase().includes(q)
      || (p.measures || '').toLowerCase().includes(q);

    const outcomes = Object.entries(lexicon.outcomes)
      .filter(([k, p]) => match(k, p))
      .map(([k, p]) => entry(k, p, null, true)).join('');

    const groups = {};
    for (const [k, p] of Object.entries(lexicon.features)) {
      if (!match(k, p)) continue;
      const m = grades[k]?.modality || 'other';
      (groups[m] = groups[m] || []).push(entry(k, p, grades[k], false));
    }

    const sections = Object.entries(groups)
      .sort((a, b) => b[1].length - a[1].length)
      .map(([m, items]) => `<section class="card">
        <h2>${esc(m[0].toUpperCase() + m.slice(1))} (${items.length})</h2>
        ${items.join('')}</section>`).join('');

    const total = Object.keys(lexicon.features).length;
    root.innerHTML = `
      <h1>What everything means</h1>
      ${note(`Every measurement ViralLab can take, in plain English.
        You should never need to know a variable name to read this
        product &mdash; if you meet one anywhere else, it is listed
        here. <strong>${total}</strong> measurements and
        <strong>${Object.keys(lexicon.outcomes).length}</strong>
        outcomes.`)}
      <div class="filters">
        <input id="gl-q" class="search" type="search" placeholder="Search
          for a word, e.g. detail, text, speech, skip" value="${esc(q)}">
      </div>
      ${outcomes ? `<section class="card">
        <h2>What we measure success by</h2>
        ${note(`These are the outcomes. Everything else is something
          about the video that might relate to one of them.`, '')}
        ${outcomes}</section>` : ''}
      ${sections || empty('Nothing matches', 'Try a different word.')}`;

    const box = document.getElementById('gl-q');
    box?.addEventListener('change', () => {
      location.hash = `#/glossary${box.value
        ? `?q=${encodeURIComponent(box.value)}` : ''}`;
    });
  },
};

function entry(key, p, cat, isOutcome) {
  const bits = [];
  if (p.measures) bits.push(`<p>${esc(p.measures)}</p>`);
  if (p.high_means || p.low_means) {
    bits.push(`<div class="hilo">
      ${p.high_means ? `<div><span class="tag">Higher</span>
        ${esc(p.high_means)}</div>` : ''}
      ${p.low_means ? `<div><span class="tag">Lower</span>
        ${esc(p.low_means)}</div>` : ''}</div>`);
  }
  if (isOutcome && p.better_when && p.better_when !== 'unknown') {
    bits.push(`<p class="sub"><span class="tag">Good when</span>
      this number is ${esc(p.better_when)}.</p>`);
  }
  // The line that stops a name being misread. Styled to be noticed.
  if (p.not_a_measure_of) {
    bits.push(`<p class="not-a-measure"><strong>This is NOT a measure
      of</strong> ${esc(p.not_a_measure_of)}</p>`);
  }
  if (p.method) {
    bits.push(`<p class="sub"><span class="tag">How</span>
      ${esc(p.method)}</p>`);
  }
  if (p.limitations) bits.push(note(esc(p.limitations), 'warn'));

  const grade = cat ? `<span class="chip ${
    cat.grade === 'Unavailable' ? 'danger'
      : (cat.grade === 'Associated' || cat.grade === 'Validated' ? 'ok'
        : '')}">${esc(cat.grade)}</span>` : '';
  const where = (cat?.platforms || []).filter((x) => !x.available)
    .map((x) => `<span class="chip danger">not on ${esc(x.platform)}</span>`)
    .join(' ');

  return `<div class="gl-entry">
    <h3>${esc(p.display_name)} ${grade} ${where}</h3>
    ${bits.join('')}
    ${cat?.coverage ? `<div class="gl-key">Measured on ${
    cat.coverage} of your publications.</div>` : ''}
    <div class="gl-key technical">stored as <code>${esc(key)}</code></div>
  </div>`;
}


/**
 * Why a feature has the grade it does, without the arithmetic.
 *
 * `grade_reason` from the server is precise and quotes coefficients;
 * precise is what Technical Details is for. This is the same fact in
 * the words a creator needs: can I act on this, or not.
 */
function plainWhy(f) {
  switch (f.grade) {
    case 'Associated':
      return `Measured across ${f.coverage} of your videos, and the
        relationship held after allowing for video length, how long it
        has been online, and paid promotion. Worth testing.`;
    case 'Validated':
      return 'Confirmed by a planned experiment.';
    case 'Hypothesised':
      return `There is a hint of something here, but it did not survive
        the checks that guard against finding patterns by chance. Only
        worth treating as an idea to test.`;
    case 'Observed':
      return f.coverage
        ? `Measured on ${f.coverage} of your videos. Nothing yet relates
           it to whether people watch.`
        : 'Nothing measured yet.';
    default:
      return f.not_built_reason
        ? 'Nothing measures this yet, so there is nothing to show.'
        : (f.withheld
          ? `We can produce a number for this, but it was wrong too
             often when checked against openings a person had labelled,
             so it is not used for anything.`
          : 'Not available for this account.');
  }
}


/**
 * Why a video has no stored copy, in words.
 *
 * These come from the collectors as short codes. A reader seeing
 * `not_a_video` on a dashboard has to guess whether that is a problem;
 * "image post, nothing to download" tells them it is not.
 */
/** How we came to have this publication, in words. */
function sourceLabel(key) {
  return ({
    official_api: 'the platform’s own API',
    provider: 'a third-party research provider',
    local_master: 'your local archive',
    own_api: 'the platform’s own API',
  })[key] || String(key || '').replace(/_/g, ' ');
}


/** What a collection run was doing, in words rather than a job name. */
function runLabel(kind) {
  return ({
    surface_backfill: 'filling in missing view breakdowns',
    daily: 'daily refresh',
    backfill: 'back-catalogue collection',
    metrics: 'metrics refresh',
    discovery: 'finding new creators',
    research: 'researching a creator',
    media: 'downloading video files',
  })[kind] || String(kind || '').replace(/_/g, ' ');
}


function reasonLabel(key) {
  return ({
    not_a_video: 'image post, nothing to download',
    api_restricted: 'platform will not supply the file',
    expired_url: 'download link expired before we fetched it',
    download_failed: 'download failed',
    no_media_url: 'platform returned no file',
    unsupported: 'format we cannot handle',
    too_large: 'file above the size limit',
  })[key] || String(key).replace(/_/g, ' ');
}


// ------------------------------------------------- cohort comparison (M6)

/**
 * You against a saved group of creators.
 *
 * The composition is rendered first and never collapsed, because the
 * brief is explicit that audience size, data age, completeness and
 * platform must be on the same screen as the comparison. A reader who
 * cannot see that the cohort runs from 64 followers to 2.7 million
 * cannot read the comparison correctly, and a reader who has to click
 * to find out will not.
 *
 * The limitation is rendered before the findings, not after. "No
 * retention data exists for anyone but you" changes what every row
 * below means, so it goes where it will be read.
 */
export const cohortCompare = {
  title: 'You against other creators',
  async render(root, params, id) {
    root.innerHTML = `<h1>You against other creators</h1>${spinner()}`;
    const c = await api.cohortComparison(id, {
      platform: params?.get?.('platform') || 'tiktok',
    });

    const comp = c.composition || {};
    const range = comp.follower_range;

    const memberRows = (c.members || []).map((m) => `<tr>
      <td><a href="#/research/${m.creator_id}">${esc(m.handle)}</a></td>
      <td class="right">${m.followers === null || m.followers === undefined
    ? '<span class="unknown"></span>' : num(m.followers)}</td>
      <td class="right">${num(m.publications)}</td>
      <td class="right">${m.measured
    ? num(m.measured)
    : '<span class="faint">none yet</span>'}</td>
      <td class="nowrap muted">${m.oldest_post
    ? `${dateOnly(m.oldest_post)} – ${dateOnly(m.newest_post)}` : '—'}</td>
    </tr>`);

    const diffs = (c.differences || []).map((d) => {
      const pct = d.owner_percentile === null ? null
        : Math.round(d.owner_percentile * 100);
      return `<div class="gl-entry">
        <h3>${esc(d.plain)}</h3>
        <div class="hilo">
          <div><span class="tag">You</span> ${fmt(d.owner_median)}
            across ${num(d.owner_n)} videos</div>
          <div><span class="tag">Them</span> ${fmt(d.cohort_median)}
            typically${d.cohort_range
  ? `, ranging ${fmt(d.cohort_range[0])} to ${fmt(d.cohort_range[1])}
     across ${d.cohort_members} creators` : ''}</div>
          ${pct === null ? '' : `<div><span class="tag">Rank</span>
            higher than ${pct}% of them</div>`}
        </div>
        <p class="sub">${esc(d.note)}</p>
        <div class="technical">${evidence(d, 'Exact figures')}</div>
      </div>`;
    }).join('');

    root.innerHTML = `
      <h1>You against other creators</h1>
      ${note(`<strong>${esc(c.what_this_cannot_say)}</strong>`, 'warn')}

      <section class="card">
        <h2>Who you are being compared with</h2>
        <p class="sub">${esc(c.collection_name)} &middot;
          ${comp.members} creators on
          ${comp.platform === 'tiktok' ? 'TikTok' : 'Instagram'} &middot;
          ${num(comp.publications_in_cohort)} videos held,
          ${num(comp.publications_measured)} measured${range
  ? ` &middot; ${num(range[0])} to ${num(range[1])} followers` : ''}.
          ${c.meets_minimum
    ? '' : `<span class="chip warn">fewer than
            ${comp.minimum_required} creators have enough measured
            videos, so read this as provisional</span>`}</p>
        ${table(['Creator', { label: 'Followers', cls: 'right' },
    { label: 'Videos held', cls: 'right' },
    { label: 'Measured', cls: 'right' }, 'Posted between'],
  memberRows, { empty: 'This cohort has no members.' })}
      </section>

      ${diffs ? `<section class="card">
        <h2>How your openings differ</h2>
        ${diffs}</section>`
    : card('How your openings differ', note(`Not enough of the cohort
        has been measured yet to compare anything. Collecting a
        creator's videos and measuring them are separate steps.`, ''))}

      ${card('What this does and does not show',
    `<ul style="margin:0 0 0 18px">${(c.limitations || []).map(
      (l) => `<li>${esc(l)}</li>`).join('')}</ul>`)}`;
  },
};

/** A measurement value, rounded to something a person can read. */
function fmt(v) {
  if (v === null || v === undefined) return '—';
  const a = Math.abs(v);
  if (a >= 100) return num(Math.round(v));
  if (a >= 1) return v.toFixed(2);
  return v.toFixed(3);
}
