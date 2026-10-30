/**
 * Research — one page for everything the platform has worked out.
 *
 * There were twenty items in the sidebar and six of them were
 * findings pages with names that only differed by their verb:
 * "What goes with more views", "What my videos look like", "How my
 * openings look", "What keeps people watching", "You against other
 * creators", "Am I doing it?". The owner could not tell from the
 * names which one answered his question, which meant he opened
 * three to find out.
 *
 * They are now sub-tabs of one page. Nothing is deleted and no view
 * is rewritten: each tab calls the existing module's `render`
 * against this page's container, so one source of truth per
 * subject remains and the old URLs keep working.
 *
 * Order is deliberate, and it is the order of the owner's own
 * sentence: find out what works, check whether I am doing it, then
 * look at the detail.
 */

import { esc, spinner } from './components.js';
import { following } from './following.js';
import { hookRetention, hooks } from './hooks.js';
import { matchedPairs } from './pairs.js';
import { visualProfile } from './profile.js';
import { cohortCompare } from './views.js';
import { virality } from './virality.js';

const TABS = [
  ['works', 'What works', virality,
    'The measured differences, on your account and on other creators’.'],
  ['doing', 'Am I doing it?', following,
    'Your recent posts against those differences.'],
  ['look', 'How my videos look', visualProfile,
    'Framing, light, colour and text, across your own work.'],
  ['openings', 'My openings', hooks,
    'What the first seconds of your videos do.'],
  ['watching', 'Staying watched', hookRetention,
    'Where people stop watching, where the data allows it.'],
  ['others', 'Against other creators', cohortCompare,
    'You beside a saved group, each scored against their own average.'],
  ['pairs', 'Two at a time', matchedPairs,
    'Two of your videos that should have done the same, and did not.'],
];

const DEFAULT_TAB = 'works';

export const researchHub = {
  title: 'Research',
  async render(root, params) {
    const want = (params && params.get('on')) || DEFAULT_TAB;
    const tab = TABS.find((t) => t[0] === want) || TABS[0];
    const [, , view, blurb] = tab;

    root.innerHTML = `
      <h1>Research</h1>
      <p class="lede">What the platform has worked out from your
        videos and other people&rsquo;s.</p>
      <nav class="subtabs">${TABS.map(([key, label]) => `
        <a class="subtab${key === tab[0] ? ' active' : ''}"
          href="#/research?on=${key}">${esc(label)}</a>`).join('')}</nav>
      <p class="muted">${esc(blurb)}</p>
      <div id="sub">${spinner()}</div>`;

    const host = root.querySelector('#sub');
    try {
      // The sub-view owns everything below the tabs, including its
      // own heading. Its `<h1>` is demoted by CSS rather than
      // edited out, so the module stays usable on its own URL.
      await view.render(host, params, cohortArg(view, params));
    } catch (err) {
      host.innerHTML = `<section class="card"><p class="muted">
        Could not load this: ${esc(String(err))}</p></section>`;
    }
  },
};

/**
 * `cohortCompare` is the one sub-view that needs a route capture:
 * on its own URL the cohort id comes from the path. Here it comes
 * from the query, defaulting to the saved group we actually have.
 */
function cohortArg(view, params) {
  if (view !== cohortCompare) return undefined;
  return (params && params.get('cohort')) || '2';
}
