/**
 * Shell: routing, navigation, auth gate.
 *
 * Hash routing on purpose — it needs no server-side rewrite rules, so the
 * static mount in `app.py` stays a plain StaticFiles mount (ADR 0006).
 */

import { aiModels, aiPlan } from './aimodels.js';
import { api, ApiError } from './api.js';
import { collectionDetail, collections } from './collections.js';
import { esc, spinner, toast } from './components.js';
import { loadLexicon } from './lexicon.js';
import { discover } from './discover.js';
import { hookDetail, hookRetention, hooks } from './hooks.js';
import { research, researchCreator } from './research.js';
import { settings } from './settings.js';
import { creatorReport, creatorTimeline } from './timeline.js';
import { labelReview } from './labelreview.js';
import { opportunities } from './opportunities.js';
import { strategist } from './strategist.js';
import {
  cohortCompare, competitors, dashboard, featureCatalogue, glossary,
  library, notFound, reviewMasters, reviewPairs, videoDetail,
} from './views.js';

const ROUTES = [
  { pattern: /^\/?$/, view: dashboard, nav: 'dashboard' },
  { pattern: /^\/library$/, view: library, nav: 'library' },
  { pattern: /^\/video\/(\d+)$/, view: videoDetail, nav: 'library' },
  { pattern: /^\/review\/masters$/, view: reviewMasters, nav: 'review-masters' },
  { pattern: /^\/review\/pairs$/, view: reviewPairs, nav: 'review-pairs' },
  { pattern: /^\/competitors$/, view: competitors, nav: 'competitors' },
  { pattern: /^\/research$/, view: research, nav: 'research' },
  { pattern: /^\/research\/(\d+)$/, view: researchCreator, nav: 'research' },
  { pattern: /^\/research\/(\d+)\/timeline$/, view: creatorTimeline,
    nav: 'research' },
  { pattern: /^\/research\/(\d+)\/report$/, view: creatorReport,
    nav: 'research' },
  { pattern: /^\/discover$/, view: discover, nav: 'discover' },
  { pattern: /^\/collections$/, view: collections, nav: 'collections' },
  { pattern: /^\/collections\/(\d+)$/, view: collectionDetail,
    nav: 'collections' },
  { pattern: /^\/hooks$/, view: hooks, nav: 'hooks' },
  { pattern: /^\/hooks\/retention$/, view: hookRetention, nav: 'hooks' },
  { pattern: /^\/hooks\/(\d+)$/, view: hookDetail, nav: 'hooks' },
  { pattern: /^\/cohort\/(\d+)$/, view: cohortCompare,
    nav: 'cohort' },
  { pattern: /^\/next$/, view: opportunities, nav: 'next' },
  { pattern: /^\/ask$/, view: strategist, nav: 'strategist' },
  { pattern: /^\/review\/labels$/, view: labelReview, nav: 'labelreview' },
  { pattern: /^\/glossary$/, view: glossary, nav: 'glossary' },
  { pattern: /^\/features$/, view: featureCatalogue,
    nav: 'features' },
  { pattern: /^\/ai$/, view: aiModels, nav: 'ai' },
  { pattern: /^\/ai\/(\d+)$/, view: aiPlan, nav: 'ai' },
  { pattern: /^\/settings$/, view: settings, nav: 'settings' },
];

const NAV = [
  { group: 'Your content' },
  { id: 'dashboard', href: '#/', label: 'Home' },
  { id: 'library', href: '#/library', label: 'My videos' },
  { group: 'Needs you' },
  { id: 'labelreview', href: '#/review/labels',
    label: 'Check uncertain labels' },
  { id: 'review-masters', href: '#/review/masters', label: 'Match my files',
    count: (o) => o?.review.master_links_pending },
  { id: 'review-pairs', href: '#/review/pairs',
    label: 'Same video, both platforms',
    count: (o) => o?.review.publication_links_proposed },
  { group: 'What we have learned' },
  { id: 'next', href: '#/next', label: 'What to try next' },
  { id: 'hooks', href: '#/hooks', label: 'How my openings look' },
  { id: 'hook-retention', href: '#/hooks/retention',
    label: 'What keeps people watching' },
  { id: 'glossary', href: '#/glossary',
    label: 'What everything means' },
  { id: 'features', href: '#/features',
    label: 'Everything we can measure' },
  { id: 'cohort', href: '#/cohort/2',
    label: 'You against other creators' },
  { group: 'Other creators' },
  { id: 'discover', href: '#/discover', label: 'Find creators' },
  { id: 'research', href: '#/research', label: 'Creators we study' },
  { id: 'collections', href: '#/collections', label: 'Groups of creators' },
  { id: 'competitors', href: '#/competitors', label: 'What we cannot do yet' },
  { group: 'Under the bonnet' },
  { id: 'ai', href: '#/ai', label: 'AI models and spending' },
  // Planned surfaces are listed but not linked: the navigation shape is
  // part of the architecture, and showing where things will live is honest
  // as long as nothing pretends to work.
  { id: 'strategist', href: '#/ask', label: 'Ask ViralLab' },
  { group: 'Setup' },
  { id: 'settings', href: '#/settings', label: 'Connections' },
];

let overviewCache = null;

function renderNav(active) {
  const items = NAV.map((n) => {
    if (n.group) return `<div class="nav-group">${esc(n.group)}</div>`;
    if (n.planned) {
      return `<a class="is-planned" aria-disabled="true" title="Not built yet"
        >${esc(n.label)}<span class="count">${esc(n.planned)}</span></a>`;
    }
    const c = n.count?.(overviewCache);
    return `<a href="${n.href}" data-nav="${n.id}"${
      active === n.id ? ' aria-current="page"' : ''}>${esc(n.label)}${
      c ? `<span class="count">${c}</span>` : ''}</a>`;
  }).join('');
  document.getElementById('nav').innerHTML = items;
}

async function renderLogin(reason) {
  document.getElementById('shell').innerHTML = `
    <form class="login" id="login-form">
      <h1>ViralLab</h1>
      <p class="muted">${esc(reason || 'Sign in to continue.')}</p>
      <input type="password" id="pw" placeholder="Password" autocomplete=
        "current-password" required autofocus>
      <button class="primary" type="submit">Sign in</button>
    </form>`;
  document.getElementById('login-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const pw = document.getElementById('pw').value;
    try {
      await api.login(pw);
      location.reload();
    } catch (err) {
      toast(err.status === 401 ? 'Incorrect password' : err.message, true);
    }
  });
}

function renderShell() {
  document.getElementById('shell').innerHTML = `
    <div class="app">
      <aside class="sidebar">
        <div class="brand">ViralLab <small>phase 6B</small></div>
        <nav class="nav" id="nav"></nav>
        <div id="tech-toggle-host" class="tech-host"></div>
        <div style="margin-top:auto;padding:10px">
          <button id="logout" style="width:100%">Sign out</button>
        </div>
      </aside>
      <main id="view"></main>
    </div>`;
  document.getElementById('logout').addEventListener('click', async () => {
    try {
      await api.logout();
    } finally {
      location.reload();
    }
  });
}

async function route() {
  const raw = location.hash.replace(/^#/, '');
  const [path, query] = raw.split('?');
  const params = new URLSearchParams(query || '');
  const match = ROUTES.find((r) => r.pattern.test(path || '/'));
  const hit = match || { view: notFound, nav: null };
  const captured = match ? (path.match(match.pattern) || [])[1] : undefined;

  renderNav(hit.nav);
  const view = document.getElementById('view');
  document.title = `${hit.view.title} · ViralLab`;
  try {
    await hit.view.render(view, params, captured);
  } catch (err) {
    if (err instanceof ApiError && err.status === 401) {
      await renderLogin('Your session expired.');
      return;
    }
    view.innerHTML = `<h1>Something failed</h1>
      <div class="note danger">${esc(err.message)}</div>
      <p><a href="#/">Back to the dashboard</a></p>`;
    // Logged, not swallowed: a silent failure in a data view is worse than
    // a visible one.
    console.error(err);
  }
}

async function boot() {
  let state;
  try {
    state = await api.sessionState();
  } catch (err) {
    document.getElementById('shell').innerHTML =
      `<div class="login"><h1>ViralLab</h1>
        <div class="note danger">Cannot reach the API: ${esc(err.message)}</div>
      </div>`;
    return;
  }
  if (!state.authenticated) {
    await renderLogin(state.auth_configured
      ? 'This instance is password-protected.'
      : 'Sign in to continue.');
    return;
  }
  renderShell();
  mountTechnicalToggle();
  // The dashboard numbers double as nav badges, so they are fetched once
  // here and reused rather than re-requested per navigation.
  // The lexicon is fetched alongside: every view turns stored keys
  // into English through it, and awaiting it here means no view has
  // to, and none ever renders a key for a frame before the names
  // arrive.
  try {
    [overviewCache] = await Promise.all([api.overview(), loadLexicon()]);
  } catch { /* the view will surface its own error */ }
  window.addEventListener('hashchange', route);
  await route();
}

document.getElementById('shell').innerHTML = spinner('Starting');
boot();


/**
 * The two presentation levels, as one switch.
 *
 * Default is Creative Intelligence: plain language, no column names,
 * no coefficients. Technical Details reveals everything marked
 * `.technical` - exact feature names, rho, sample sizes, method
 * versions - without removing or duplicating anything.
 *
 * The preference is remembered, because a person who wants the
 * numbers wants them on every page and should not have to say so
 * twice. It is applied to <html> rather than per-view so a freshly
 * rendered page is never briefly wrong.
 */
const TECH_KEY = 'virallab.technical';

export function technicalOn() {
  return localStorage.getItem(TECH_KEY) === '1';
}

function applyTechnical() {
  document.documentElement.classList.toggle('show-technical',
    technicalOn());
  const b = document.getElementById('tech-toggle');
  if (b) {
    b.setAttribute('aria-pressed', technicalOn() ? 'true' : 'false');
    b.textContent = technicalOn()
      ? 'Hide technical details' : 'Show technical details';
  }
}

function mountTechnicalToggle() {
  const host = document.getElementById('tech-toggle-host');
  if (!host) return;
  host.innerHTML = `<button id="tech-toggle" class="ghost"
    aria-pressed="false" title="Exact feature names, coefficients and
    sample sizes. Nothing is removed when this is off - it is moved
    out of the way.">Show technical details</button>`;
  document.getElementById('tech-toggle').addEventListener('click', () => {
    localStorage.setItem(TECH_KEY, technicalOn() ? '0' : '1');
    applyTechnical();
  });
  applyTechnical();
}
