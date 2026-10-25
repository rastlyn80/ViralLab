/**
 * API client. The only place in the frontend that knows about HTTP.
 *
 * Shapes come from `shared/schemas/ui.py` via OpenAPI; `types.js` holds the
 * generated typedefs so an editor can check call sites (ADR 0006).
 */

/** Mutations carry this header. A cross-site form cannot set it, which is
 *  what makes it a CSRF guard without a token round-trip. */
const CSRF_HEADER = 'X-ViralLab-UI';

export class ApiError extends Error {
  constructor(status, detail) {
    super(detail || `HTTP ${status}`);
    this.status = status;
    this.detail = detail;
  }
}

async function request(path, { method = 'GET', body } = {}) {
  const init = {
    method,
    headers: {},
    // The session cookie is HttpOnly and SameSite=Strict.
    credentials: 'same-origin',
  };
  if (body !== undefined) {
    init.headers['Content-Type'] = 'application/json';
    init.body = JSON.stringify(body);
  }
  if (method !== 'GET') init.headers[CSRF_HEADER] = '1';

  const res = await fetch(path, init);
  if (res.status === 204) return null;

  let payload = null;
  const text = await res.text();
  if (text) {
    try {
      payload = JSON.parse(text);
    } catch {
      payload = { detail: text.slice(0, 400) };
    }
  }
  if (!res.ok) {
    throw new ApiError(res.status, payload?.detail || res.statusText);
  }
  return payload;
}

const qs = (params) => {
  const sp = new URLSearchParams();
  for (const [k, v] of Object.entries(params || {})) {
    if (v !== undefined && v !== null && v !== '') sp.set(k, String(v));
  }
  const s = sp.toString();
  return s ? `?${s}` : '';
};

export const api = {
  sessionState: () => request('/api/session'),
  login: (password) => request('/api/session', { method: 'POST', body: { password } }),
  logout: () => request('/api/session', { method: 'DELETE' }),

  overview: () => request('/api/overview'),
  capabilities: () => request('/api/capabilities'),

  videos: (params) => request(`/api/videos${qs(params)}`),
  video: (id) => request(`/api/videos/${id}`),

  masterLinks: (params) => request(`/api/review/master-links${qs(params)}`),
  decideMasterLink: (id, answer, notes) =>
    request(`/api/review/master-links/${id}`, {
      method: 'POST',
      body: { answer, notes: notes || null },
    }),

  publicationLinks: (params) =>
    request(`/api/review/publication-links${qs(params)}`),
  decidePublicationLink: (id, answer, notes) =>
    request(`/api/review/publication-links/${id}`, {
      method: 'POST',
      body: { answer, notes: notes || null },
    }),
};

/**
 * Local media bridge.
 *
 * Local master files live on `D:` on the Windows machine and are
 * deliberately never transferred to the server, so the server cannot play
 * them (ADR 0006). When the user happens to be at that machine, a small
 * bridge on 127.0.0.1 can. We probe once and cache the answer, so the
 * review view can offer master playback where it is genuinely available and
 * say so plainly where it is not — rather than rendering a dead player.
 */
const BRIDGE = 'http://127.0.0.1:8770';
let bridgePromise = null;

export function localBridge() {
  if (!bridgePromise) {
    bridgePromise = fetch(`${BRIDGE}/bridge/health`, { mode: 'cors' })
      .then((r) => (r.ok ? BRIDGE : null))
      .catch(() => null);
  }
  return bridgePromise;
}

export function bridgeMediaUrl(base, localPath) {
  return `${base}/bridge/master?path=${encodeURIComponent(localPath)}`;
}
