/**
 * Reusable render helpers.
 *
 * Plain functions returning HTML strings, composed by the views. No
 * framework (ADR 0006), but the same discipline: a component owns one piece
 * of presentation and is used everywhere that thing appears, so a change to
 * how a proposal looks happens once.
 *
 * The functions that matter most here are `num`, `stateChip` and
 * `ageQualified`. They are what stop the interface from lying:
 *   - `num` renders a missing value as "unknown", never as 0
 *   - `stateChip` makes a machine proposal look different from a human fact
 *   - `ageQualified` refuses to show a metric without the age it was taken at
 */

export const esc = (s) =>
  String(s ?? '').replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[c]));

/** A number, or a visibly-not-a-number marker. Never 0 for "unknown". */
export function num(v, { digits = 0, suffix = '' } = {}) {
  if (v === null || v === undefined) return '<span class="unknown"></span>';
  const n = Number(v);
  if (!Number.isFinite(n)) return '<span class="unknown"></span>';
  return `<span class="num">${n.toLocaleString(undefined, {
    minimumFractionDigits: digits, maximumFractionDigits: digits,
  })}${esc(suffix)}</span>`;
}

export function compact(v) {
  if (v === null || v === undefined) return '<span class="unknown"></span>';
  const n = Number(v);
  if (!Number.isFinite(n)) return '<span class="unknown"></span>';
  const units = [[1e9, 'B'], [1e6, 'M'], [1e3, 'K']];
  for (const [div, u] of units) {
    if (Math.abs(n) >= div) {
      return `<span class="num">${(n / div).toFixed(n / div < 10 ? 1 : 0)}${u}</span>`;
    }
  }
  return `<span class="num">${n}</span>`;
}

export const dateOnly = (iso) => (iso ? esc(String(iso).slice(0, 10)) : '—');

export function dateTime(iso) {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(+d)) return esc(String(iso).slice(0, 16));
  return esc(d.toLocaleString(undefined, {
    year: 'numeric', month: 'short', day: '2-digit',
    hour: '2-digit', minute: '2-digit',
  }));
}

export function secs(v) {
  if (v === null || v === undefined) return '<span class="unknown"></span>';
  return `<span class="num">${Number(v).toFixed(1)}s</span>`;
}

/**
 * A performance figure with the age it was observed at.
 *
 * CLAUDE.md §3.8a: comparing metrics captured at different ages measures
 * elapsed exposure, not content quality. The interface therefore never
 * shows a view count on its own.
 */
export function ageQualified(value, ageDays) {
  const v = compact(value);
  if (value === null || value === undefined) return v;
  if (ageDays === null || ageDays === undefined) {
    return `${v} <span class="faint">(age unknown)</span>`;
  }
  return `${v} <span class="faint">@${ageDays}d</span>`;
}

const STATE_STYLES = {
  confirmed: ['ok', 'confirmed'],
  rejected: ['danger', 'rejected'],
  variant: ['warn', 'variant'],
  platform_edit: ['warn', 'platform edit'],
  auto: ['accent', 'auto-linked'],
  pending: ['', 'pending'],
  proposed: ['proposed', 'proposed'],
  inferred: ['proposed', 'inferred'],
  unsupported: ['', 'not a video'],
  absent: ['warn', 'no media'],
  available: ['ok', 'media stored'],
  purged: ['', 'purged'],
  ok: ['ok', 'ok'],
  failed: ['danger', 'failed'],
  deferred: ['warn', 'deferred'],
};

/**
 * Status, with who decided it.
 *
 * `decided_by` is rendered because "auto-linked by the matcher" and
 * "confirmed by a human" are different claims about the same row, and the
 * product vision forbids presenting the first as if it were the second.
 */
export function stateChip(status, decidedBy) {
  const [cls, label] = STATE_STYLES[status] || ['', status || 'unknown'];
  const by = decidedBy === 'human'
    ? ' <span class="faint">· you</span>'
    : (decidedBy === 'matcher' ? ' <span class="faint">· machine</span>' : '');
  return `<span class="chip ${cls}"><span class="dot"></span>${esc(label)}${by}</span>`;
}

export const platformChip = (p) =>
  `<span class="chip">${esc(p === 'tiktok' ? 'TikTok' : 'Instagram')}</span>`;

export function stat(label, value, sub) {
  return `<div class="card stat">
    <div class="label">${esc(label)}</div>
    <div class="value">${value}</div>
    ${sub ? `<div class="sub">${sub}</div>` : ''}
  </div>`;
}

export function card(title, bodyHtml, { flush = false } = {}) {
  return `<div class="card${flush ? ' flush' : ''}">
    ${title ? `<h3>${esc(title)}</h3>` : ''}${bodyHtml}
  </div>`;
}

export function table(headers, rows, { empty = 'Nothing to show.' } = {}) {
  if (!rows.length) return `<div class="empty">${esc(empty)}</div>`;
  const head = headers
    .map((h) => (typeof h === 'string'
      ? `<th>${esc(h)}</th>`
      : `<th class="${h.cls || ''}">${esc(h.label)}</th>`))
    .join('');
  return `<table><thead><tr>${head}</tr></thead><tbody>${rows.join('')}</tbody></table>`;
}

export function kv(pairs) {
  const body = pairs
    .filter(([, v]) => v !== undefined && v !== null && v !== '')
    .map(([k, v]) => `<dt>${esc(k)}</dt><dd>${v}</dd>`)
    .join('');
  return `<dl class="kv">${body}</dl>`;
}

export function note(text, kind = '') {
  return `<div class="note ${kind}">${text}</div>`;
}

export function empty(title, detail) {
  return `<div class="empty"><strong>${esc(title)}</strong>${
    detail ? esc(detail) : ''}</div>`;
}

export function evidence(obj, label = 'Full evidence') {
  if (!obj || !Object.keys(obj).length) return '';
  return `<details class="ev"><summary>${esc(label)}</summary>
    <pre>${esc(JSON.stringify(obj, null, 2))}</pre></details>`;
}

export function bar(fraction) {
  const pct = Math.max(0, Math.min(100, (fraction || 0) * 100));
  return `<div class="bar"><span style="width:${pct.toFixed(1)}%"></span></div>`;
}

export function spinner(label = 'Loading') {
  return `<div class="row"><span class="spinner"></span>
    <span class="muted">${esc(label)}…</span></div>`;
}

let toastTimer = null;
export function toast(message, isError = false) {
  document.querySelector('.toast')?.remove();
  const el = document.createElement('div');
  el.className = `toast${isError ? ' err' : ''}`;
  el.textContent = message;
  document.body.appendChild(el);
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.remove(), isError ? 6000 : 2600);
}

/**
 * A video player, or an honest explanation of why there is none.
 *
 * Deliberately not a broken <video> tag: `media_status` and `media_reason`
 * carry real information (an image post is not a failed download, and an
 * Instagram-withheld URL is not our bug), so the absence is explained.
 */
export function player(url, { status, reason, poster } = {}) {
  if (url) {
    return `<video controls preload="metadata" ${
      poster ? `poster="${esc(poster)}"` : ''} src="${esc(url)}"></video>`;
  }
  if (status === 'unsupported') {
    return note('This publication is not a video, so there is nothing to '
      + 'play. Not a missing download.', 'warn');
  }
  if (reason && reason.startsWith('api_restricted')) {
    return note('The platform returned this publication but withheld its '
      + 'media URL, so it could never be downloaded.', 'warn');
  }
  if (poster) {
    return `<img class="cover" src="${esc(poster)}" alt="cover image">`
      + note('Cover image only — this platform returns no media file.', '');
  }
  return note(`No media stored${reason ? ` (${esc(reason)})` : ''}.`, '');
}


/**
 * A link out to the actual post on the platform.
 *
 * Returns '' when there is no URL rather than a dead link: an
 * Instagram permalink carries a shortcode that cannot be computed
 * from the media id, so one that was never collected cannot be
 * invented, and a guessed URL that 404s reads as "the post is gone"
 * rather than "we never stored the link".
 */
export function postLink(url, platform, { label = '' } = {}) {
  if (!url) return '';
  const where = platform === 'tiktok' ? 'TikTok'
    : (platform === 'instagram' ? 'Instagram' : 'the platform');
  return `<a class="postlink" href="${esc(url)}" target="_blank"
    rel="noopener noreferrer" title="Open on ${where}"
    >${esc(label || `Open on ${where}`)} \u2197</a>`;
}
