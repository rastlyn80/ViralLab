/**
 * Make — everything for producing the next video, in one place.
 *
 * Three tools arrived separately and each took a slot in the
 * sidebar: write something, check a draft, browse openings that
 * worked. That put the navigation back to six items a day after
 * it was cut to four, which is how a sidebar grows back.
 *
 * They belong together anyway. The order is the order of the
 * work: look at what has worked, write something, check it before
 * it goes out.
 *
 * Like the Research hub, each tab renders the existing module
 * against this container. Nothing was rewritten, every old URL
 * still resolves, and there is still one source of truth per
 * tool.
 */

import { esc, spinner } from './components.js';
import { creative } from './creative.js';
import { draft } from './draft.js';
import { openings } from './openings.js';

const TABS = [
  ['write', 'Write something', creative,
    'Ideas, opening text and captions, from your own evidence and '
    + "other creators'."],
  ['check', 'Check a draft', draft,
    'Measure a video you have not posted yet against what works.'],
  ['openings', 'Openings that worked', openings,
    'Yours and theirs, ranked by how far each beat its own '
    + "creator's usual run."],
];

const DEFAULT_TAB = 'write';

export const makeHub = {
  title: 'Make',
  async render(root, params) {
    const want = (params && params.get('on')) || DEFAULT_TAB;
    const tab = TABS.find((t) => t[0] === want) || TABS[0];
    const [, , view, blurb] = tab;

    root.innerHTML = `
      <h1>Make</h1>
      <nav class="subtabs">${TABS.map(([key, label]) => `
        <a class="subtab${key === tab[0] ? ' active' : ''}"
          href="#/make?on=${key}">${esc(label)}</a>`).join('')}</nav>
      <p class="muted">${esc(blurb)}</p>
      <div id="sub">${spinner()}</div>`;

    const host = root.querySelector('#sub');
    try {
      await view.render(host, params);
    } catch (err) {
      host.innerHTML = `<section class="card"><p class="muted">
        Could not load this: ${esc(String(err))}</p></section>`;
    }
  },
};
