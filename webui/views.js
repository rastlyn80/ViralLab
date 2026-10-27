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
  kv, note, num, platformChip, player, secs, spinner, stat, stateChip, table,
  toast,
} from './components.js';

// ---------------------------------------------------------------- dashboard

export const dashboard = {
  title: 'Dashboard',
  async render(root) {
    root.innerHTML = `<h1>Dashboard</h1>${spinner()}`;
    const o = await api.overview();

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
        .map(([k, v]) => `<span class="chip">${esc(k)} ${v}</span>`).join(' ');
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
        <td class="muted">${esc(r.kind)}</td>
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
      <h1>Dashboard</h1>
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
      ${card('', runs, { flush: true })}`;
  },
};

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
        v.caption_preview || v.external_id)}</a></td>
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
            ['own account', v.is_own_account ? 'yes' : 'no'],
            ['provenance', `<span class="chip">${esc(v.source)}</span>`],
            ['media', `${stateChip(v.media_status, null)}${v.media_reason
              ? ` <span class="faint">${esc(v.media_reason)}</span>` : ''}`],
          ])}
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
  title: 'Competitor intelligence',
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
          `<span class="chip">${esc(k.replace(/_/g, ' '))}: ${v}</span>`).join('')
      }</div>` : ''}
      <h3>Not available yet because</h3>
      <ul class="muted" style="margin:0;padding-left:18px;font-size:13px">
        ${c.blocked_by.map((b) => `<li>${esc(b)}</li>`).join('')}
      </ul>`)).join('');

    root.innerHTML = `
      <h1>Competitor intelligence</h1>
      <p class="lede">Discovering successful creators, understanding their
        breakthroughs, and turning that into recommendations is the central
        value of ViralLab. None of it is built yet, and this page shows the
        real state rather than a demo.</p>

      ${note(`<strong>Nothing here is fabricated.</strong> There are no
        competitor creators in the database and no discovery has run, so this
        section reports what each capability needs rather than showing
        example results. Controls appear when the thing behind them works.`,
        'accent')}

      <div class="grid cols-2">${cards}</div>

      <h2>What has to happen first</h2>
      ${card('', `
        <ol class="muted" style="margin:0;padding-left:20px">
          <li><strong>Authorise a budget.</strong> Official APIs expose no
            competitor data, so discovery needs the validated third-party
            provider. <code>spending.monthly_cap_usd</code> is
            <code>0.00</code> and <code>providers_authorised</code> is empty,
            and the collector refuses to spend against either.</li>
          <li><strong>Discovery adapter.</strong> Keyword, hashtag, niche,
            genre and composer searches against that provider, writing into
            the same tables as own-account collection.</li>
          <li><strong>Creator reconciliation.</strong> Tie a TikTok handle to
            an Instagram handle only on evidence, never on a coincidental
            name match.</li>
          <li><strong>Rolling baselines.</strong> Breakthrough detection
            compares a publication against that creator's own baseline at a
            comparable age — which needs the baselines to exist.</li>
        </ol>`)}

      <p class="faint" style="font-size:13px">Plan:
        <code>DEVELOPMENT_PLAN.md</code> Phase 4 ·
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
      return `<tr${unavailable ? ' class="is-unavailable"' : ''}>
        <td><code>${esc(f.name)}</code>${f.withheld
          ? ' <span class="chip warn">withheld</span>' : ''}
          <div class="sub">${esc(f.describes)}</div></td>
        <td>${esc(f.modality)}</td>
        <td>${gradeChip(f.grade)}</td>
        <td class="num">${unavailable && !f.coverage
          ? '<span class="sub">none</span>' : num(f.coverage)}</td>
        <td><div class="sub">${esc(why)}</div>${platforms}</td>
        <td>${f.evidence.length
          ? evidence(f.evidence, `${f.evidence.length} finding(s)`)
          : '<span class="sub">not tested</span>'}</td>
      </tr>`;
    }).join('');

    root.innerHTML = `
      <h1>Feature catalogue</h1>
      ${note(`Every feature this project can measure, with the grade it has
        earned from evidence. A grade is derived on every request from
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
