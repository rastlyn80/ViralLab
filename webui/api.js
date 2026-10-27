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

  // -- competitor research ------------------------------------------------
  researchState: () => request('/api/research/state'),
  researchedCreators: (params) =>
    request(`/api/research/creators${qs(params)}`),
  runResearch: (body) =>
    request('/api/research/creators', { method: 'POST', body }),
  researchCreator: (id) => request(`/api/research/creators/${id}`),
  researchPublications: (id, params) =>
    request(`/api/research/creators/${id}/publications${qs(params)}`),
  researchBreakthroughs: (id) =>
    request(`/api/research/creators/${id}/breakthroughs`),
  researchJobs: (params) => request(`/api/research/jobs${qs(params)}`),
  decidePublicationLink: (id, answer, notes) =>
    request(`/api/review/publication-links/${id}`, {
      method: 'POST',
      body: { answer, notes: notes || null },
    }),

  // -- discovery ----------------------------------------------------------
  // `estimate` is a GET and must stay free: the interface shows a price
  // before the owner commits, which is only useful if asking costs nothing.
  niches: () => request('/api/discovery/niches'),
  discoveryEstimate: (params) =>
    request(`/api/discovery/estimate${qs(params)}`),
  runDiscovery: (body) =>
    request('/api/discovery/search', { method: 'POST', body }),
  discoverySearches: (params) =>
    request(`/api/discovery/searches${qs(params)}`),
  discoveryCandidates: (jobId) =>
    request(`/api/discovery/searches/${jobId}/candidates`),
  expansionPlan: (body) =>
    request('/api/discovery/expansion-plan', { method: 'POST', body }),

  // -- timeline and report ------------------------------------------------
  creatorTimeline: (id, params) =>
    request(`/api/research/creators/${id}/timeline${qs(params)}`),
  creatorReport: (id) => request(`/api/research/creators/${id}/report`),

  // -- collections --------------------------------------------------------
  collections: () => request('/api/collections'),
  createCollection: (body) =>
    request('/api/collections', { method: 'POST', body }),
  updateCollection: (id, body) =>
    request(`/api/collections/${id}`, { method: 'PATCH', body }),
  deleteCollection: (id) =>
    request(`/api/collections/${id}`, { method: 'DELETE' }),
  collection: (id) => request(`/api/collections/${id}`),
  addCollectionMember: (id, body) =>
    request(`/api/collections/${id}/members`, { method: 'POST', body }),
  removeCollectionMember: (id, creatorId) =>
    request(`/api/collections/${id}/members/${creatorId}`,
      { method: 'DELETE' }),

  // -- hook intelligence (Phase 6A) ---------------------------------------
  hookCoverage: () => request('/api/hooks/coverage'),
  hookVideos: (params) => request(`/api/hooks/videos${qs(params)}`),
  hookVideo: (id) => request(`/api/hooks/videos/${id}`),
  runHookExtraction: (body) =>
    request('/api/hooks/extract', { method: 'POST', body }),
  correctHookFeature: (videoId, featureId, body) =>
    request(`/api/hooks/videos/${videoId}/features/${featureId}/correct`,
      { method: 'POST', body }),
  hookRetention: () => request('/api/hooks/retention'),
  featureCatalogue: (params) =>
    request(`/api/features/catalogue${qs(params)}`),
  featureInsights: (params) => request(`/api/features/insights${qs(params)}`),
  featureLexicon: () => request('/api/features/lexicon'),
  home: () => request('/api/home'),
  cohortComparison: (id, params) =>
    request(`/api/features/cohort/${id}${qs(params)}`),

  // -- AI providers, routing, visual analysis (Phase 6B) ------------------
  // `aiPlan` is a GET and must stay free: it shows what a request would
  // cost before anyone commits, which is only useful if asking is free.
  aiProviders: () => request('/api/ai/providers'),
  aiPolicy: () => request('/api/ai/policy'),
  aiTask: () => request('/api/ai/task'),
  aiPlan: (params) => request(`/api/ai/plan${qs(params)}`),
  aiAnalyse: (body) => request('/api/ai/analyse', { method: 'POST', body }),

  // Platform connections. `credentials` never returns values - see
  // server/api/credentials_api.py - so nothing secret reaches this client.
  credentials: () => request('/api/credentials'),
  setCredential: (key, value) => request(
    `/api/credentials/${encodeURIComponent(key)}`,
    { method: 'PUT', body: { value } }),
  clearCredential: (key) => request(
    `/api/credentials/${encodeURIComponent(key)}`, { method: 'DELETE' }),
  setSpending: (body) => request('/api/credentials/spending',
    { method: 'PUT', body }),

  // Visual analysis queue. The local model runs on the Windows workstation,
  // so the server queues rather than executes; see vision_api.py.
  visionQueue: () => request('/api/vision/requests'),
  requestVision: (body) => request('/api/vision/requests',
    { method: 'POST', body }),
  cancelVision: (id) => request(`/api/vision/requests/${id}/cancel`,
    { method: 'POST' }),
  aiOperations: (params) => request(`/api/ai/operations${qs(params)}`),

  // -- analysis seam (Phase 6) --------------------------------------------
  analysisContract: () => request('/api/analysis/contract'),
  requestAnalysis: (body) =>
    request('/api/analysis/requests', { method: 'POST', body }),
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
