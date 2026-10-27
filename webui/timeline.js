/**
 * The creator breakthrough timeline, and the research report.
 *
 * Drawn as inline SVG with no charting library: the data is a few dozen
 * points, the server already computed every derived value, and a 90 KB
 * dependency to draw dots would be the wrong trade on a box with 184 MiB
 * free (ADR 0006).
 *
 * The chart's job is to make three things impossible to confuse:
 *
 *   - an **outlier candidate** (a fact about one publication) is a hollow
 *     ring on that point;
 *   - a **change point** (a claim about the creator's level) is a labelled
 *     vertical rule;
 *   - a **sustained run** is a shaded band behind the points.
 *
 * And one thing impossible to miss: in `lifetime_exploratory` mode the
 * y-axis is lifetime views at mixed ages, which is **not** a trajectory.
 * That mode gets a dashed line rather than a solid one, a warning header,
 * and an age readout on every point.
 */

import { api } from './api.js';
import {
  card, compact, dateTime, esc, note, num, spinner, toast,
} from './components.js';
import { classChip, fixtureChip } from './research.js';

const W = 860;
const H = 300;
const PAD = { top: 18, right: 18, bottom: 34, left: 58 };

// ------------------------------------------------------------------- chart

function scale(points, mode) {
  const xs = points.map((p) => new Date(p.published_at).getTime());
  const key = (p) => (mode === 'fixed_age'
    ? p.views_at_anchor ?? p.views : p.views);
  const ys = points.map(key).filter((v) => v !== null && v !== undefined);
  const x0 = Math.min(...xs);
  const x1 = Math.max(...xs);
  const y1 = Math.max(...ys, 1);
  const innerW = W - PAD.left - PAD.right;
  const innerH = H - PAD.top - PAD.bottom;
  // Log y: view counts in this niche span three orders of magnitude, and a
  // linear axis renders every ordinary publication as a flat line at zero.
  const ly1 = Math.log10(Math.max(y1, 10));
  const ly0 = 0;
  return {
    x: (t) => PAD.left + (x1 === x0 ? innerW / 2
      : ((t - x0) / (x1 - x0)) * innerW),
    y: (v) => {
      const lv = Math.log10(Math.max(v || 1, 1));
      return PAD.top + innerH - ((lv - ly0) / (ly1 - ly0)) * innerH;
    },
    key, x0, x1, y1, ly1,
  };
}

function yTicks(s) {
  const out = [];
  for (let e = 0; e <= Math.ceil(s.ly1); e += 1) {
    const v = 10 ** e;
    if (v > s.y1 * 3) break;
    out.push({ v, y: s.y(v) });
  }
  return out;
}

function chart(tl) {
  const pts = tl.points.filter((p) => p.published_at);
  if (pts.length < 2) {
    return `<p class="muted">Not enough publications to plot.</p>`;
  }
  const s = scale(pts, tl.mode);
  const exploratory = tl.mode !== 'fixed_age';

  const bands = tl.sustained_runs.map((r) => {
    const a = s.x(new Date(r.start_at).getTime());
    const b = s.x(new Date(r.end_at).getTime());
    const fill = r.kind === 'above' ? 'var(--ok)' : 'var(--warn)';
    return `<rect x="${a}" y="${PAD.top}" width="${Math.max(b - a, 2)}"
      height="${H - PAD.top - PAD.bottom}" fill="${fill}" opacity="0.08">
      <title>${r.publications} consecutive publications ${esc(r.kind)} the
        creator's own baseline (median ${r.median_relative}x)</title>
    </rect>`;
  }).join('');

  const path = pts.filter((p) => s.key(p) !== null)
    .map((p, i) => `${i === 0 ? 'M' : 'L'}${
      s.x(new Date(p.published_at).getTime()).toFixed(1)},${
      s.y(s.key(p)).toFixed(1)}`).join(' ');

  const dots = pts.map((p) => {
    const v = s.key(p);
    if (v === null || v === undefined) return '';
    const cx = s.x(new Date(p.published_at).getTime());
    const cy = s.y(v);
    const rel = p.relative_performance;
    const colour = p.is_change_point ? 'var(--accent)'
      : (rel !== null && rel >= 2 ? 'var(--ok)'
        : (rel !== null && rel <= 0.5 ? 'var(--warn)' : 'var(--fg-muted)'));
    const ring = p.is_outlier_candidate
      ? `<circle cx="${cx}" cy="${cy}" r="7" fill="none"
          stroke="var(--ok)" stroke-width="1.6" opacity="0.9"/>` : '';
    const title = [
      dateTime(p.published_at),
      `${num(v)} views${tl.mode === 'fixed_age'
        ? ` at ${p.anchor_days}d` : ' (lifetime)'}`,
      p.age_days === null ? '' : `observed at ${p.age_days} days old`,
      rel === null ? 'no baseline yet' : `${rel}x this creator's baseline`,
      p.observation_count > 1 ? `${p.observation_count} observations`
        : 'observed once',
      p.is_outlier_candidate ? 'OUTLIER CANDIDATE' : '',
      p.is_change_point ? 'CHANGE POINT' : '',
    ].filter(Boolean).join(' · ');
    return `${ring}<circle class="tl-dot" cx="${cx}" cy="${cy}" r="4"
      fill="${colour}" data-video="${p.video_id}" style="cursor:pointer">
      <title>${esc(title)}</title></circle>`;
  }).join('');

  let rule = '';
  if (tl.change_point?.at) {
    const cx = s.x(new Date(tl.change_point.at).getTime());
    rule = `<line x1="${cx}" x2="${cx}" y1="${PAD.top}"
        y2="${H - PAD.bottom}" stroke="var(--accent)" stroke-width="1.5"
        stroke-dasharray="4 3"/>
      <text x="${cx + 4}" y="${PAD.top + 11}" font-size="10.5"
        fill="var(--accent)">change point</text>`;
  }

  const gridY = yTicks(s).map((t) => `
    <line x1="${PAD.left}" x2="${W - PAD.right}" y1="${t.y}" y2="${t.y}"
      stroke="var(--line)" stroke-width="1"/>
    <text x="${PAD.left - 6}" y="${t.y + 3.5}" font-size="10.5"
      text-anchor="end" fill="var(--fg-muted)">${compact(t.v)}</text>`).join('');

  const xLabels = [s.x0, s.x1].map((t, i) => `
    <text x="${i === 0 ? PAD.left : W - PAD.right}" y="${H - 12}"
      font-size="10.5" fill="var(--fg-muted)"
      text-anchor="${i === 0 ? 'start' : 'end'}">${
    new Date(t).toISOString().slice(0, 10)}</text>`).join('');

  return `
    <svg viewBox="0 0 ${W} ${H}" width="100%" role="img"
      aria-label="Publication performance over time" id="tl-svg">
      ${gridY}${bands}${rule}
      <path d="${path}" fill="none" stroke="var(--fg-faint)"
        stroke-width="1.4"${exploratory ? ' stroke-dasharray="5 4"' : ''}/>
      ${dots}${xLabels}
    </svg>
    <p class="muted" style="margin-top:4px">
      ${exploratory
    ? `Dashed line: these are <b>lifetime</b> totals at mixed ages, joined
       only to make the sequence readable. The line is not a trajectory.`
    : `Solid line: every point is views at ${
      pts.find((p) => p.anchor_days)?.anchor_days ?? ''} days of age, so the
       points are comparable.`}
      Hollow ring = outlier candidate. Dashed rule = change point.
      Shaded band = sustained run. Click a point to open the publication.
    </p>`;
}

function frequencyChart(buckets) {
  if (!buckets.length) return '';
  const max = Math.max(...buckets.map((b) => b.publications), 1);
  const bw = Math.max(6, Math.min(26, Math.floor(820 / buckets.length) - 3));
  const h = 86;
  const bars = buckets.map((b, i) => {
    const bh = (b.publications / max) * (h - 20);
    return `<rect x="${i * (bw + 3) + 2}" y="${h - 16 - bh}" width="${bw}"
      height="${Math.max(bh, 1)}" fill="var(--accent)" opacity="0.55">
      <title>${esc(b.month)}: ${b.publications} publication(s)</title></rect>`;
  }).join('');
  const labels = buckets.map((b, i) => (
    i % Math.ceil(buckets.length / 8) === 0
      ? `<text x="${i * (bw + 3) + 2}" y="${h - 3}" font-size="9.5"
          fill="var(--fg-muted)">${esc(b.month.slice(2))}</text>` : ''))
    .join('');
  return `<svg viewBox="0 0 ${buckets.length * (bw + 3) + 6} ${h}"
    width="100%" role="img" aria-label="Publications per month">
    ${bars}${labels}</svg>`;
}

// -------------------------------------------------------------- the views

export const creatorTimeline = {
  title: 'Creator timeline',
  async render(root, params, id) {
    root.innerHTML = spinner('Building timeline');
    const tl = await api.creatorTimeline(id);

    const modeKind = tl.mode === 'fixed_age' ? 'ok' : 'warn';
    root.innerHTML = `
      <div class="row" style="justify-content:space-between;align-items:start">
        <h1>${esc(tl.handle)} — timeline ${fixtureChip(tl.is_fixture)}</h1>
        <div class="row" style="gap:8px">
          <a href="#/research/${id}"><button>Profile</button></a>
          <a href="#/research/${id}/report"><button>Report</button></a>
        </div>
      </div>
      ${note(`<strong>${esc(tl.mode_label)}</strong>`, modeKind)}
      <div class="grid cols-4" style="margin-bottom:14px">
        ${statCard('Publications', num(tl.points.length))}
        ${statCard('Outlier candidates', num(tl.outlier_candidates),
    'single publications, not trends')}
        ${statCard('Observed twice', num(tl.repeatedly_observed),
    tl.repeatedly_observed ? 'growth is visible' : 'no growth visible yet')}
        ${statCard('Age-anchored', `${num(tl.anchored_points)}/${
      num(tl.points.length)}`, 'comparable points')}
      </div>
      ${card('Performance over time', chart(tl), { flush: false })}
      ${tl.change_point ? changePointCard(tl.change_point) : ''}
      ${card('Publication frequency', `
        ${frequencyChart(tl.frequency)}
        <p class="muted">Median ${tl.median_days_between_posts ?? '—'} days
          between publications. Computed only from publications the
          provider returned.</p>`)}
      ${tl.sustained_runs.length ? card('Sustained runs',
    runsTable(tl.sustained_runs)) : ''}
      ${card('What this chart cannot tell you', `<ul style="margin:0 0 0 18px">
        ${tl.caveats.map((c) => `<li>${esc(c)}</li>`).join('')}</ul>`)}`;

    root.querySelectorAll('.tl-dot').forEach((dot) => {
      dot.addEventListener('click', () => {
        const vid = dot.dataset.video;
        if (vid && vid !== '0') location.hash = `#/video/${vid}`;
        else toast('No stored publication for this point', true);
      });
    });
  },
};

const statCard = (label, value, sub) => `
  <div class="card stat"><div class="label">${esc(label)}</div>
    <div class="value">${value}</div>
    ${sub ? `<div class="sub">${esc(sub)}</div>` : ''}</div>`;

function changePointCard(cp) {
  const missing = cp.missing_evidence?.length
    ? `<h4 style="margin:12px 0 4px">Evidence this classification lacks</h4>
       <ul style="margin:0 0 0 18px">${cp.missing_evidence.map(
    (m) => `<li>${esc(m)}</li>`).join('')}</ul>`
    : '';
  return card(`Change point ${classChip(cp.classification)}`, `
    <div class="grid cols-3">
      ${statCard('Median before', compact(cp.median_before ?? 0))}
      ${statCard('Median after', compact(cp.median_after ?? 0))}
      ${statCard('Step', cp.step_ratio ? `${cp.step_ratio.toFixed(1)}x` : '—')}
    </div>
    <p style="margin-top:10px">Sustained afterwards:
      <b>${cp.sustained === null ? 'unknown' : (cp.sustained ? 'yes' : 'no')}
      </b>.</p>
    ${note(`<strong>Confidence ${cp.confidence ?? '—'} is not a
      probability.</strong> ${esc(cp.confidence_note)}`, 'warn')}
    ${missing}`);
}

function runsTable(runs) {
  return `<table><thead><tr><th>Period</th><th>Direction</th>
    <th class="num">Publications</th><th class="num">Median vs baseline</th>
    </tr></thead><tbody>${runs.map((r) => `<tr>
      <td>${dateTime(r.start_at)} → ${dateTime(r.end_at)}</td>
      <td><span class="chip ${r.kind === 'above' ? 'ok' : 'warn'}">
        <span class="dot"></span>${esc(r.kind)} baseline</span></td>
      <td class="num">${r.publications}</td>
      <td class="num">${r.median_relative}x</td></tr>`).join('')}
    </tbody></table>`;
}

// ------------------------------------------------------------------ report

const BASIS_CHIP = {
  observed: '<span class="chip ok"><span class="dot"></span>observed</span>',
  inferred:
    '<span class="chip proposed"><span class="dot"></span>inferred</span>',
  unavailable:
    '<span class="chip"><span class="dot"></span>unavailable</span>',
};

export const creatorReport = {
  title: 'Research report',
  async render(root, params, id) {
    root.innerHTML = spinner('Building report');
    const r = await api.creatorReport(id);

    root.innerHTML = `
      <div class="row" style="justify-content:space-between;align-items:start">
        <h1>${esc(r.handle)} — research report ${fixtureChip(r.is_fixture)}</h1>
        <div class="row" style="gap:8px">
          <a href="#/research/${id}"><button>Profile</button></a>
          <a href="#/research/${id}/timeline"><button>Timeline</button></a>
        </div>
      </div>
      ${note(`Researched ${r.research_date ? dateTime(r.research_date)
    : 'never'} via <code>${esc(r.provider || 'unknown')}</code>. Cost:
      ${r.cost_usd === null ? 'unknown'
    : `$${r.cost_usd.toFixed(4)}`}${r.job_id ? ` (job ${r.job_id})` : ''}.`,
  'accent')}
      ${plainSummary(r)}
      ${(r.groups || [])
    .filter((g) => g.key !== 'summary')
    .map((g) => groupBlock(g, r)).join('')}
      ${card('Data limitations', `<ul style="margin:0 0 0 18px">${
      r.data_limitations.map((l) => `<li>${esc(l)}</li>`).join('')}</ul>`)}`;
  },
};

/**
 * The first screen: six questions, answered in English.
 *
 * Deliberately before everything else and deliberately without a single
 * variable name. A reader who stops here should still have understood
 * what we found and how far to trust it; a reader who continues gets
 * the measurements underneath, which is the right way round.
 */
function plainSummary(r) {
  const s = (r.sections || []).find((x) => x.key === 'plain_summary');
  if (!s) return '';
  if (s.basis === 'unavailable') {
    return card('What we found',
      note(esc(s.unavailable_reason || 'Nothing to summarise yet.'), ''));
  }
  const qs = (s.body?.questions || []).map((q) => {
    const items = (q.items || []).length
      ? `<ul class="plain-list">${q.items.map(
        (i) => `<li>${esc(i)}</li>`).join('')}</ul>`
      : '';
    const links = (q.video_ids || []).length
      ? `<div class="sub">Supporting videos: ${q.video_ids.map(
        (v) => `<a href="#/video/${v}">#${v}</a>`).join(' ')}</div>`
      : '';
    const weak = q.confident === false
      ? '<span class="chip warn">not enough evidence</span>' : '';
    return `<div class="qa">
      <h3>${esc(q.question)} ${weak}</h3>
      <p>${esc(q.text || '')}</p>${items}${links}</div>`;
  }).join('');
  return `<section class="card plain-summary">
    <h2>What we found</h2>${qs}
    ${(s.caveats || []).map((c) => note(esc(c), '')).join('')}
  </section>`;
}

/** One of M5's four sections, with its blocks inside. */
function groupBlock(g, r) {
  const inside = (r.sections || [])
    .filter((s) => g.section_keys.includes(s.key));
  if (!inside.length) return '';
  const body = inside.map(section).join('');
  // Everything below the summary is detail, so it opens closed. The
  // science stays one click away rather than in the reader's face.
  return `<details class="group" ${g.key === 'overview' ? 'open' : ''}>
    <summary><h2>${esc(g.title)}</h2>
      <span class="sub">${inside.length} section(s)</span></summary>
    ${body}</details>`;
}

const GRADE_CHIP = {
  Unavailable: '<span class="chip danger">Unavailable</span>',
  Observed: '<span class="chip">Observed</span>',
  Hypothesised: '<span class="chip warn">Hypothesised</span>',
  Associated: '<span class="chip ok">Associated</span>',
  Validated: '<span class="chip ok">Validated</span>',
};

function section(s) {
  const grade = GRADE_CHIP[s.grade] || '';
  if (s.basis === 'unavailable') {
    return card(`${esc(s.title)} ${grade}`,
      `${note(esc(s.unavailable_reason || 'Not available.'), '')}
       ${s.grade_reason ? `<div class="sub">${esc(s.grade_reason)}</div>`
    : ''}`);
  }
  const caveats = s.caveats?.length
    ? `<div style="margin-top:10px">${s.caveats.map(
      (c) => note(esc(c), 'warn')).join('')}</div>`
    : '';
  // Every number in a block links to the publications it came from.
  const links = (s.evidence_video_ids || []).length
    ? `<div class="sub" style="margin-top:8px">Computed from ${
      s.evidence_video_ids.length} publication(s): ${
      s.evidence_video_ids.slice(0, 25).map(
        (v) => `<a href="#/video/${v}">#${v}</a>`).join(' ')}${
      s.evidence_video_ids.length > 25 ? ' …' : ''}</div>`
    : '';
  // Counterexamples are shown, never dropped. A block that lists only
  // its supporting cases is an argument, not a measurement.
  const against = (s.contradictions || []).length
    ? `<details style="margin-top:10px"><summary>Examples that cut the
        other way (${s.contradictions.length})</summary>
       ${itemsTable(s.contradictions)}</details>`
    : '';
  return card(`${esc(s.title)} ${grade}`,
    `${sectionBody(s)}${links}${against}${caveats}
     ${s.grade_reason ? `<div class="sub">Grade: ${esc(s.grade_reason)}
      </div>` : ''}`);
}

function sectionBody(s) {
  const b = s.body || {};
  if (s.key === 'breakthrough') return breakthroughBody(b);
  if (s.key === 'strongest' || s.key === 'outliers') return itemsTable(b.items);
  if (s.key === 'repertoire') return repertoireBody(b);
  if (s.key === 'collections') return collectionsBody(b.items);
  if (s.key === 'frequency') return frequencyBody(b);
  return kvBody(b);
}

function kvBody(b) {
  const rows = Object.entries(b)
    .filter(([, v]) => typeof v !== 'object' || v === null)
    .map(([k, v]) => `<tr><td>${esc(k.replace(/_/g, ' '))}</td>
      <td class="num">${v === null || v === undefined
    ? '<span class="unknown"></span>'
    : (typeof v === 'number' ? num(v) : esc(String(v)))}</td></tr>`).join('');
  const nested = Object.entries(b)
    .filter(([, v]) => v && typeof v === 'object' && !Array.isArray(v))
    .map(([k, v]) => `<details style="margin-top:8px"><summary>${
      esc(k.replace(/_/g, ' '))}</summary><pre class="mono">${
      esc(JSON.stringify(v, null, 1))}</pre></details>`).join('');
  const lists = Object.entries(b)
    .filter(([, v]) => Array.isArray(v) && v.length
      && typeof v[0] !== 'object')
    .map(([k, v]) => `<div style="margin-top:8px"><b>${
      esc(k.replace(/_/g, ' '))}</b><ul style="margin:4px 0 0 18px">${
      v.map((x) => `<li>${esc(String(x))}</li>`).join('')}</ul></div>`).join('');
  return `<table>${rows}</table>${lists}${nested}`;
}

function itemsTable(items) {
  if (!items?.length) return '<p class="muted">Nothing flagged.</p>';
  return `<table><thead><tr><th>Published</th><th class="num">Views</th>
    <th class="num">Age</th><th class="num">vs baseline</th>
    <th class="num">z</th><th>Caption</th></tr></thead><tbody>
    ${items.map((i) => `<tr>
      <td>${i.published_at ? dateTime(i.published_at) : '—'}</td>
      <td class="num">${num(i.views ?? i.observed_views)}</td>
      <td class="num">${i.age_days ?? '—'}${i.age_days ? 'd' : ''}</td>
      <td class="num"><b>${i.relative_performance ?? '—'}x</b></td>
      <td class="num">${i.robust_z ?? '—'}</td>
      <td class="caption-text">${esc((i.caption || '').slice(0, 90))}</td>
      </tr>`).join('')}</tbody></table>`;
}

function breakthroughBody(b) {
  if (!b.found) return '<p class="muted">No change point detected.</p>';
  return `
    <div class="grid cols-4">
      ${statCard('Classification', classChip(b.classification))}
      ${statCard('At publication',
    `${(b.publication_index ?? 0) + 1}/${b.publications_total}`)}
      ${statCard('Step', b.step_ratio ? `${b.step_ratio.toFixed(1)}x` : '—')}
      ${statCard('Sustained', b.sustained ? 'yes' : 'no')}
    </div>
    <h4 style="margin:14px 0 4px">Why it was flagged</h4>
    <ol style="margin:0 0 0 18px">${(b.why_flagged || []).map(
    (w) => `<li>${esc(w)}</li>`).join('')}</ol>
    <h4 style="margin:14px 0 4px">What would strengthen this</h4>
    <ul style="margin:0 0 0 18px">${(b.what_would_strengthen || []).map(
    (w) => `<li>${esc(w)}</li>`).join('')}</ul>
    ${b.missing_evidence?.length ? `
      <h4 style="margin:14px 0 4px">Evidence it lacks</h4>
      <ul style="margin:0 0 0 18px">${b.missing_evidence.map(
    (m) => `<li>${esc(m)}</li>`).join('')}</ul>` : ''}
    ${b.frequency_change ? note(`<b>Posting cadence</b>:
      ${b.frequency_change.days_between_posts_before}d before →
      ${b.frequency_change.days_between_posts_after}d after
      (${b.frequency_change.changed ? 'changed' : 'about the same'}).
      ${esc(b.frequency_change.note)}`, 'warn') : ''}
    ${note(`<strong>Confidence ${b.confidence ?? '—'}.</strong>
      ${esc(b.confidence_note || '')}`, 'warn')}`;
}

function repertoireBody(b) {
  const list = (title, items) => (items?.length ? `
    <div><b>${esc(title)}</b><p style="margin:4px 0 0">${items.map(
    (i) => `<span class="chip">${esc(i.name)} · ${i.publications}</span>`)
    .join(' ')}</p></div>` : '');
  return `
    ${note(`Method: <code>${esc(b.method)}</code> — keyword matching over
      captions, hashtags and music titles. These are <b>mentions</b>, not
      identifications of what was played.`, 'warn')}
    <div class="stack" style="display:grid;gap:12px">
      ${list('Composers mentioned', b.composer_mentions)}
      ${list('Formats mentioned', b.format_mentions)}
      ${list('Hashtags', b.hashtags)}
      ${list('Music titles', b.music_titles)}
    </div>
    <p class="muted" style="margin-top:10px">
      ${num(b.publications_with_no_composer_mention)} of
      ${num(b.publications_searched)} publications mention no known
      composer.</p>`;
}

function frequencyBody(b) {
  return `
    ${frequencyChart(b.by_month || [])}
    <p>Median <b>${b.median_days_between_posts ?? '—'}</b> days between
      publications; busiest month ${esc(b.busiest_month || '—')}.</p>
    ${b.change_around_breakthrough ? note(`Cadence around the change point:
      ${b.change_around_breakthrough.days_between_posts_before}d →
      ${b.change_around_breakthrough.days_between_posts_after}d.
      ${esc(b.change_around_breakthrough.note)}`, 'warn') : ''}`;
}

function collectionsBody(items) {
  if (!items?.length) return '<p class="muted">Not in any collection.</p>';
  return `<ul style="margin:0 0 0 18px">${items.map((c) => `<li>
    <a href="#/collections/${c.id}">${esc(c.name)}</a>
    <span class="muted">${esc(c.purpose)}${
    c.reason ? ` — ${esc(c.reason)}` : ''}</span></li>`).join('')}</ul>`;
}
