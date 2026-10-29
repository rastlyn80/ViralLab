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
import { researchHub } from './research_hub.js';
import { settings } from './settings.js';
import { creatorReport, creatorTimeline } from './timeline.js';
import { labelReview } from './labelreview.js';
import { opportunities } from './opportunities.js';
import { creative } from './creative.js';
import { following } from './following.js';
import { work } from './work.js';
import { visualProfile } from './profile.js';
import { virality } from './virality.js';
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
  // `#/research` is the findings hub now. The list of creators
  // we study moved to `#/creators`, because "research" is what
  // the owner calls the answers, not the inputs.
  { pattern: /^\/research$/, view: researchHub, nav: 'research-hub' },
  { pattern: /^\/creators$/, view: research, nav: 'creators' },
  { pattern: /^\/research\/(\d+)$/, view: researchCreator, nav: 'creators' },
  { pattern: /^\/research\/(\d+)\/timeline$/, view: creatorTimeline,
    nav: 'creators' },
  { pattern: /^\/research\/(\d+)\/report$/, view: creatorReport,
    nav: 'creators' },
  { pattern: /^\/discover$/, view: discover, nav: 'discover' },
  { pattern: /^\/collections$/, view: collections, nav: 'collections' },
  { pattern: /^\/collections\/(\d+)$/, view: collectionDetail,
    nav: 'collections' },
  { pattern: /^\/hooks$/, view: hooks, nav: 'hooks' },
  { pattern: /^\/hooks\/retention$/, view: hookRetention, nav: 'hooks' },
  { pattern: /^\/hooks\/(\d+)$/, view: hookDetail, nav: 'hooks' },
  { pattern: /^\/cohort\/(\d+)$/, view: cohortCompare,
    nav: 'cohort' },
  { pattern: /^\/look$/, view: visualProfile, nav: 'look' },
  { pattern: /^\/views$/, view: virality, nav: 'views' },
  { pattern: /^\/make$/, view: creative, nav: 'make' },
  { pattern: /^\/collect$/, view: work, nav: 'work' },
  { pattern: /^\/following$/, view: following,
    nav: 'following' },
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

/**
 * Four places to be, and everything else folded away.
 *
 * There were twenty items here. Six of them were findings pages
 * whose names differed only by their verb, which meant the owner
 * opened three to find out which one answered his question.
 *
 * `MORE` is not a junk drawer and not a demotion. It holds the
 * pages that explain the machinery - what every measurement means,
 * what the models cost, which creators are in the set - and those
 * are reference, consulted when a number looks wrong, not part of
 * the daily loop. Nothing was deleted and every old URL still
 * works.
 */
const NAV = [
  { id: 'dashboard', href: '#/', label: 'Home' },
  { id: 'research-hub', href: '#/research', label: 'Research' },
  { id: 'make', href: '#/make', label: 'Make something' },
  { id: 'work', href: '#/collect', label: 'Collect data' },
];

//: Shown only when the owner opens "Everything else".
const MORE = [
  { group: 'Needs you' },
  { id: 'labelreview', href: '#/review/labels',
    label: 'Check uncertain labels' },
  { id: 'review-masters', href: '#/review/masters', label: 'Match my files',
    count: (o) => o?.review.master_links_pending },
  { id: 'review-pairs', href: '#/review/pairs',
    label: 'Same video, both platforms',
    count: (o) => o?.review.publication_links_proposed },
  { group: 'Your videos' },
  { id: 'library', href: '#/library', label: 'All my videos' },
  { id: 'next', href: '#/next', label: 'What to try next' },
  { group: 'Other creators' },
  { id: 'creators', href: '#/creators', label: 'Creators we study' },
  { id: 'discover', href: '#/discover', label: 'Find creators' },
  { id: 'collections', href: '#/collections', label: 'Groups of creators' },
  { group: 'Reference' },
  { id: 'glossary', href: '#/glossary', label: 'What everything means' },
  { id: 'features', href: '#/features',
    label: 'Everything we can measure' },
  { id: 'competitors', href: '#/competitors',
    label: 'What we cannot do yet' },
  { id: 'ai', href: '#/ai', label: 'AI models and spending' },
  { id: 'strategist', href: '#/ask', label: 'Ask ViralLab' },
  { id: 'settings', href: '#/settings', label: 'Connections' },
];

//: Which nav item lights up for a page that lives under one.
const UNDER = {
  views: 'research-hub', following: 'research-hub',
  look: 'research-hub', hooks: 'research-hub',
  'hook-retention': 'research-hub', cohort: 'research-hub',
};

let overviewCache = null;

function navLink(n, active) {
  if (n.group) return `<div class="nav-group">${esc(n.group)}</div>`;
  if (n.planned) {
    return `<a class="is-planned" aria-disabled="true" title="Not built yet"
      >${esc(n.label)}<span class="count">${esc(n.planned)}</span></a>`;
  }
  const c = n.count?.(overviewCache);
  return `<a href="${n.href}" data-nav="${n.id}"${
    active === n.id ? ' aria-current="page"' : ''}>${esc(n.label)}${
    c ? `<span class="count">${c}</span>` : ''}</a>`;
}

function renderNav(active) {
  const lit = UNDER[active] || active;
  // A count on something hidden inside the fold would never be
  // seen, so anything waiting bubbles up onto the disclosure
  // itself. Work the owner has to do must not be discoverable
  // only by opening a drawer.
  const waiting = MORE.reduce(
    (sum, n) => sum + (n.count?.(overviewCache) || 0), 0);
  const open = MORE.some((n) => n.id === active);
  const items = NAV.map((n) => navLink(n, lit)).join('')
    + `<details class="nav-more"${open ? ' open' : ''}>
         <summary>Everything else${waiting
  ? `<span class="count">${waiting}</span>` : ''}</summary>
         ${MORE.map((n) => navLink(n, active)).join('')}
       </details>`;
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
