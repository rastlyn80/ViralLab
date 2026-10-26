/**
 * Hook Intelligence: what the opening three seconds actually contain, and
 * whether any of it relates to retention.
 *
 * Three things this view is built to avoid.
 *
 * **Presenting an unanalysed video as analysed.** Every row shows how many
 * features exist for it; a video with none says so rather than showing
 * blanks that look like zeros.
 *
 * **Presenting a proxy as a measurement.** A value arrives with its
 * `method`, and the badge differs: a reproducible measurement, a threshold
 * proxy with its measured error rate, a model's answer, or a human
 * correction sitting beside the machine's.
 *
 * **Presenting average watch time as a completion rate.** It includes
 * replays — one video in this corpus averages 3.38x its own duration — so
 * it is shown in seconds with the caveat attached, never as a percentage.
 */

import { api } from './api.js';
import {
  card, compact, dateTime, empty, esc, note, num, secs, spinner, toast,
} from './components.js';

const METHOD_CHIP = {
  deterministic: '<span class="chip ok" title="Signal processing on the '
    + 'file. Reproducible to the bit."><span class="dot"></span>measured'
    + '</span>',
  proxy: '<span class="chip warn" title="A threshold on a measurement, not '
    + 'a reading."><span class="dot"></span>proxy</span>',
  model: '<span class="chip accent" title="A model\'s answer."><span '
    + 'class="dot"></span>model</span>',
  human: '<span class="chip"><span class="dot"></span>corrected</span>',
  unavailable: '<span class="chip" title="This pass could not produce it."'
    + '><span class="dot"></span>unavailable</span>',
};

const fmtValue = (v) => {
  if (v === null || v === undefined) return '<span class="unknown"></span>';
  if (typeof v === 'boolean') return v ? 'yes' : 'no';
  if (typeof v === 'number') return Number.isInteger(v) ? String(v) : v.toFixed(3);
  if (Array.isArray(v)) return v.length ? esc(v.join(', ')) : '—';
  if (typeof v === 'object') return `<code>${esc(JSON.stringify(v))}</code>`;
  return esc(String(v));
};

// -------------------------------------------------------------- list view

export const hooks = {
  title: 'Hook intelligence',
  async render(root, params) {
    root.innerHTML = spinner('Loading hook analysis');
    const only = params.get('only') || 'retention';
    const order = params.get('order') || 'skip';
    const [cov, page] = await Promise.all([
      api.hookCoverage(),
      api.hookVideos({ only, order, limit: 200 }),
    ]);

    root.innerHTML = `
      <h1>Hook intelligence</h1>
      <p class="lede">What the first ${3} seconds of each video actually
        contain, measured from the file — and whether any of it relates to
        how many people skip.</p>
      ${note(`<strong>${num(cov.analysable)}</strong> publications have a
        preserved file.
        <strong>${num(cov.deterministic_done)}</strong> have been through
        the extractor.
        <strong>${num(cov.with_retention)}</strong> also have a retention
        metric — and that smaller number is the ceiling on any statistical
        claim here. Processing more videos cannot raise it.`, 'accent')}
      <div class="grid cols-4" style="margin-bottom:14px">
        ${stat('Preserved', num(cov.analysable), 'files on disk')}
        ${stat('Extracted', num(cov.deterministic_done), cov.method_version)}
        ${stat('With retention', num(cov.with_retention),
    'the analysable set')}
        ${stat('Corrections', num(cov.corrections), 'human')}
      </div>
      ${card('Run extraction', extractForm())}
      <div id="extract-result"></div>
      <div class="toolbar" style="margin-top:14px">
        <div class="field">
          <label>Show</label>
          <select id="h-only">
            ${[['retention', 'With retention metrics'],
    ['extracted', 'Extracted'],
    ['analysable', 'All preserved']].map(([v, l]) =>
    `<option value="${v}"${v === only ? ' selected' : ''}>${l}</option>`)
    .join('')}
          </select>
        </div>
        <div class="field">
          <label>Order</label>
          <select id="h-order">
            ${[['skip', 'Best retention first'], ['recent', 'Most recent'],
    ['extracted', 'Most features']].map(([v, l]) =>
    `<option value="${v}"${v === order ? ' selected' : ''}>${l}</option>`)
    .join('')}
          </select>
        </div>
      </div>
      ${videoTable(page)}`;

    const go = () => {
      location.hash = `#/hooks?only=${document.getElementById('h-only').value}`
        + `&order=${document.getElementById('h-order').value}`;
    };
    document.getElementById('h-only').addEventListener('change', go);
    document.getElementById('h-order').addEventListener('change', go);
    wireExtract(root);
  },
};

const stat = (label, value, sub) => `
  <div class="card stat"><div class="label">${esc(label)}</div>
    <div class="value">${value}</div>
    ${sub ? `<div class="sub">${esc(sub)}</div>` : ''}</div>`;

function extractForm() {
  return `
    <div class="toolbar">
      <div class="field">
        <label for="x-scope">Scope</label>
        <select id="x-scope">
          <option value="retention">Videos with retention metrics</option>
          <option value="sample">A representative sample</option>
          <option value="all">All preserved videos</option>
        </select>
      </div>
      <div class="field">
        <label for="x-n">Sample size</label>
        <input id="x-n" type="number" value="12" min="1" max="50">
      </div>
      <div style="align-self:end;display:flex;gap:8px;align-items:center">
        <label class="muted" style="display:flex;gap:6px;align-items:center">
          <input type="checkbox" id="x-speech"> also run speech (slower)
        </label>
        <button class="primary" id="x-run">Extract</button>
      </div>
    </div>
    <p class="muted">Runs on the server where the media already is — no GPU,
      nothing uploaded, and the source files are never modified. Re-running
      the same method version rewrites that version's rows; a new version
      writes beside them, so previous evidence is never lost.</p>`;
}

function wireExtract(root) {
  document.getElementById('x-run').addEventListener('click', async () => {
    const btn = document.getElementById('x-run');
    const box = document.getElementById('extract-result');
    btn.disabled = true;
    btn.textContent = 'Extracting…';
    box.innerHTML = spinner('Measuring openings');
    try {
      const res = await api.runHookExtraction({
        scope: document.getElementById('x-scope').value,
        sample_size: Number(document.getElementById('x-n').value) || 12,
        include_speech: document.getElementById('x-speech').checked,
      });
      box.innerHTML = res.map((r) => note(
        `<b>${esc(r.kind)}</b> (${esc(r.method_version)}):
         ${r.done} done, ${r.failed} failed, ${num(r.features_written)}
         features in ${r.compute_seconds}s
         (${(r.compute_seconds / Math.max(r.done, 1)).toFixed(2)}s per
         video).` + (r.errors.length
    ? `<br>First error: ${esc(JSON.stringify(r.errors[0]))}` : ''),
        r.failed ? 'warn' : 'ok')).join('');
      toast('Extraction finished');
    } catch (err) {
      box.innerHTML = note(esc(err.message), 'danger');
    } finally {
      btn.disabled = false;
      btn.textContent = 'Extract';
    }
  });
}

function videoTable(page) {
  if (!page.items.length) {
    return empty('Nothing to show', 'Run an extraction, or change the '
      + 'filter.');
  }
  return `<div class="card flush"><table>
    <thead><tr>
      <th>Published</th><th>Opening</th>
      <th class="num">Skip rate</th><th class="num">Duration</th>
      <th class="num">Cuts</th><th class="num">Text score</th>
      <th>Speech</th><th class="num">Features</th><th></th>
    </tr></thead><tbody>
    ${page.items.map((r) => `<tr>
      <td>${r.posted_at ? dateTime(r.posted_at) : '—'}</td>
      <td class="caption-text">${esc((r.caption || '').slice(0, 64))}</td>
      <td class="num">${r.skip_rate === null
    ? '<span class="unknown"></span>'
    : `<b>${r.skip_rate.toFixed(1)}%</b>`}</td>
      <td class="num">${r.duration_s ? secs(r.duration_s) : '—'}</td>
      <td class="num">${r.cut_count === null ? '—' : r.cut_count}</td>
      <td class="num">${r.text_region_score === null ? '—'
    : r.text_region_score.toFixed(3)}</td>
      <td>${r.speech_present === null ? '—'
    : (r.speech_present ? '<span class="chip accent"><span class="dot">'
      + '</span>speech</span>' : 'music only')}</td>
      <td class="num">${r.features_extracted
    ? num(r.features_extracted)
    : '<span class="chip warn"><span class="dot"></span>not analysed</span>'}
        ${r.corrections ? `<span class="chip">${r.corrections} fixed</span>`
    : ''}</td>
      <td><a href="#/hooks/${r.video_id}"><button>Open</button></a></td>
    </tr>`).join('')}</tbody></table></div>
    <p class="muted">Skip rate is the share of viewers who skipped — lower
      is better. It is the outcome a hook can plausibly move.</p>`;
}

// ------------------------------------------------------------ detail view

export const hookDetail = {
  title: 'Hook detail',
  async render(root, params, id) {
    root.innerHTML = spinner('Loading');
    const d = await api.hookVideo(id);

    const byMethod = {};
    d.features.forEach((f) => {
      (byMethod[f.method_version] ||= []).push(f);
    });

    root.innerHTML = `
      <div class="row" style="justify-content:space-between;align-items:start">
        <div>
          <h1>Opening ${d.hook_seconds}s — ${esc(d.external_id)}</h1>
          <p class="muted">${d.posted_at ? dateTime(d.posted_at) : ''} ·
            ${d.duration_s ? secs(d.duration_s) : '—'} ·
            ${esc(d.platform)}</p>
        </div>
        <div class="row" style="gap:8px">
          <a href="#/ai/${d.video_id}"><button>Visual analysis…</button></a>
          <a href="#/hooks"><button>All videos</button></a>
        </div>
      </div>
      ${d.caption ? `<p class="caption-text">${esc(d.caption)}</p>` : ''}
      <div class="grid cols-3" style="margin:14px 0">
        ${stat('Skip rate', d.skip_rate === null ? '—'
    : `${d.skip_rate.toFixed(1)}%`, 'share who skipped; lower is better')}
        ${stat('Avg watch', d.avg_watch_ms === null ? '—'
    : `${(d.avg_watch_ms / 1000).toFixed(1)}s`, 'includes replays')}
        ${stat('Features', num(d.features.length),
    `${Object.keys(byMethod).length} method version(s)`)}
      </div>
      ${d.caveats.map((c) => note(esc(c), 'warn')).join('')}
      ${mediaBlock(d)}
      ${Object.entries(byMethod).map(([version, rows]) =>
    card(`${esc(version)} <span class="muted">${rows.length} features
      </span>`, featureTable(rows))).join('')}
      ${d.runs.length ? card('Extraction runs', runsTable(d.runs)) : ''}`;

    wireCorrections(root, id);
  },
};

function mediaBlock(d) {
  if (!d.media_url) {
    return note(`No preserved file for this publication, so the opening
      cannot be played here. ${esc(d.media_status || '')}`, '');
  }
  // Seek to the start and stop at the hook boundary, so what plays is
  // exactly what was measured.
  return card('The measured opening', `
    <video id="hook-video" src="${esc(d.media_url)}" controls preload="metadata"
      style="max-width:320px;border-radius:8px;background:#000"></video>
    <p class="muted">Plays the first ${d.hook_seconds} seconds — the window
      every feature below was measured over. Signed, short-lived link to
      your own preserved file.</p>
    <button id="play-hook">Play just the hook</button>`);
}

function featureTable(rows) {
  const order = { deterministic: 0, model: 1, proxy: 2, unavailable: 3 };
  const sorted = [...rows].sort(
    (a, b) => (order[a.method] ?? 9) - (order[b.method] ?? 9)
      || a.feature.localeCompare(b.feature));
  return `<table><thead><tr>
      <th>Feature</th><th class="num">Value</th><th>Method</th>
      <th class="num">Uncertainty</th><th>Window</th><th></th>
    </tr></thead><tbody>
    ${sorted.map((f) => `<tr>
      <td><b>${esc(f.feature)}</b>
        ${f.note ? `<div class="muted" style="max-width:46ch">${
  esc(f.note)}</div>` : ''}
        ${f.unavailable_reason ? `<div class="muted"
          style="max-width:46ch">${esc(f.unavailable_reason)}</div>` : ''}</td>
      <td class="num">
        ${f.is_corrected
    ? `<s class="muted">${fmtValue(f.value)}</s>
       <b>${fmtValue(f.corrected_value)}</b>`
    : fmtValue(f.value)}
        ${f.unit ? ` <span class="muted">${esc(f.unit)}</span>` : ''}</td>
      <td>${METHOD_CHIP[f.method] || esc(f.method)}
        ${f.model ? `<div class="muted">${esc(f.model)}</div>` : ''}</td>
      <td class="num">${f.uncertainty === null ? '—'
    : `${f.uncertainty} <span class="muted">${esc(f.uncertainty_kind
      || '')}</span>`}</td>
      <td class="muted">${f.interval_start_s === null ? '—'
    : `${f.interval_start_s}–${f.interval_end_s}s`}</td>
      <td>${f.method === 'unavailable' ? ''
    : `<button class="fix" data-id="${f.id}"
         data-feature="${esc(f.feature)}">Correct</button>`}</td>
    </tr>${f.is_corrected ? `<tr><td colspan="6" class="muted">
      Corrected by ${esc(f.corrected_by || 'human')}
      ${f.corrected_at ? dateTime(f.corrected_at) : ''}${
  f.correction_note ? `: ${esc(f.correction_note)}` : ''}. The machine's
      original value is kept so the two can be compared later.</td></tr>`
    : ''}`).join('')}
    </tbody></table>`;
}

function runsTable(runs) {
  return `<table><thead><tr><th>Run</th><th>Kind</th><th>Version</th>
    <th>Status</th><th class="num">Compute</th><th>When</th></tr></thead>
    <tbody>${runs.map((r) => `<tr>
      <td>#${r.id}</td><td>${esc(r.kind)}</td>
      <td><code>${esc(r.method_version)}</code></td>
      <td>${esc(r.status)}</td>
      <td class="num">${r.compute_seconds ? `${r.compute_seconds}s` : '—'}</td>
      <td>${r.started_at ? dateTime(r.started_at) : '—'}</td>
    </tr>`).join('')}</tbody></table>`;
}

function wireCorrections(root, videoId) {
  const play = document.getElementById('play-hook');
  if (play) {
    play.addEventListener('click', () => {
      const v = document.getElementById('hook-video');
      v.currentTime = 0;
      v.play();
      const stop = () => {
        if (v.currentTime >= 3.0) { v.pause(); v.removeEventListener('timeupdate', stop); }
      };
      v.addEventListener('timeupdate', stop);
    });
  }

  root.querySelectorAll('.fix').forEach((b) => {
    b.addEventListener('click', async () => {
      const current = window.prompt(
        `Correct "${b.dataset.feature}".\n\n`
        + 'Enter the true value (yes/no for a boolean, a number, or text). '
        + 'The machine\'s value is kept beside yours.');
      if (current === null) return;
      let value = current.trim();
      if (/^(yes|true)$/i.test(value)) value = true;
      else if (/^(no|false)$/i.test(value)) value = false;
      else if (value !== '' && !Number.isNaN(Number(value))) {
        value = Number(value);
      }
      const whyNote = window.prompt('Why? (optional)') || null;
      try {
        await api.correctHookFeature(videoId, b.dataset.id,
          { value, note: whyNote });
        toast('Correction recorded');
        await hookDetail.render(root, new URLSearchParams(), videoId);
      } catch (err) {
        toast(err.message, true);
      }
    });
  });
}

// ---------------------------------------------------------- the analysis

export const hookRetention = {
  title: 'Hooks and retention',
  async render(root) {
    root.innerHTML = spinner('Running the analysis');
    const r = await api.hookRetention();

    const sig = (a) => (a.p_adjusted !== null && a.p_adjusted < 0.05);
    root.innerHTML = `
      <div class="row" style="justify-content:space-between;align-items:start">
        <h1>Do opening characteristics relate to retention?</h1>
        <a href="#/hooks"><button>Hook intelligence</button></a>
      </div>
      ${note(`<strong>Outcome:</strong> ${esc(r.outcome_note)}`, 'accent')}
      ${note(`<strong>${esc(r.verdict)}</strong>`,
    r.survivors.length ? 'warn' : '')}
      <div class="grid cols-4" style="margin:14px 0">
        ${stat('Analysed', num(r.n_analysed), 'with features and retention')}
        ${stat('Skip range', r.skip_range
    ? `${r.skip_range[0]}–${r.skip_range[1]}%` : '—')}
        ${stat('Duration range', r.duration_range_s
    ? `${r.duration_range_s[0]}–${r.duration_range_s[1]}s` : '—')}
        ${stat('Age range', r.age_range_days
    ? `${r.age_range_days[0]}–${r.age_range_days[1]}d` : '—',
    'not a fixed-age measurement')}
      </div>
      ${card('Associations with skip rate', `
        <table><thead><tr>
          <th>Opening feature</th><th class="num">n</th>
          <th class="num" title="Spearman rank correlation">rho</th>
          <th class="num" title="Controlling for duration">+dur</th>
          <th class="num" title="Controlling for duration and age">+dur+age</th>
          <th class="num">95% CI</th>
          <th class="num" title="Benjamini-Hochberg FDR">p adj</th>
          <th>Effect</th>
        </tr></thead><tbody>
        ${r.associations.map((a) => `<tr${sig(a)
    ? ' style="background:var(--warn-soft)"' : ''}>
          <td><b>${esc(a.feature)}</b>
            ${a.direction ? `<div class="muted">${esc(a.direction)}</div>`
    : ''}</td>
          <td class="num">${a.n}</td>
          <td class="num">${a.rho === null ? '—' : a.rho}</td>
          <td class="num">${a.rho_partial_duration === null ? '—'
    : a.rho_partial_duration}</td>
          <td class="num"><b>${a.rho_partial_duration_age === null ? '—'
    : a.rho_partial_duration_age}</b></td>
          <td class="num muted">${a.ci_low === null ? '—'
    : `[${a.ci_low}, ${a.ci_high}]`}</td>
          <td class="num">${a.p_adjusted === null ? '—' : a.p_adjusted}</td>
          <td>${esc(a.effect_size_label)}</td>
        </tr>${a.caveats.length || a.interpretation ? `<tr><td colspan="8"
          class="muted">${esc(a.interpretation || '')}
          ${a.caveats.map((c) => `<br>${esc(c)}`).join('')}</td></tr>` : ''}`)
    .join('')}
        </tbody></table>
        <p class="muted">Spearman rank correlation. <b>+dur+age</b> is the
          one to read: it controls for both video length and how old the
          post was when retention was captured. A negative value means a
          higher feature value goes with <em>less</em> skipping.</p>`)}
      ${r.survivor_collinearity.length ? card(
    'Are the survivors the same finding?', `<ul style="margin:0 0 0 18px">${
      r.survivor_collinearity.map((c) => `<li>${esc(c.note)}</li>`)
        .join('')}</ul>`) : ''}
      ${card('What this cannot tell you', `<ul style="margin:0 0 0 18px">${
      r.limitations.map((l) => `<li>${esc(l)}</li>`).join('')}</ul>`)}`;
  },
};
