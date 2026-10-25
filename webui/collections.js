/**
 * Competitive research collections.
 *
 * The one rule this screen exists to enforce: **members are compared on
 * performance against their own baseline, never on lifetime views.** A
 * table sorted by total views would be saying that the biggest account
 * makes the best content, and it would be saying it silently. So the
 * sortable columns are the relative ones, lifetime views are present but
 * labelled, and the default order is by best relative performance.
 */

import { api } from './api.js';
import {
  card, compact, dateTime, empty, esc, note, num, spinner, table, toast,
} from './components.js';
import { classChip, fixtureChip } from './research.js';

const PURPOSES = [
  ['research', 'Accounts to learn from'],
  ['control_cohort', 'Control group — did NOT break through, same niche'],
  ['watchlist', 'Watching for changes'],
  ['inspiration', 'Formats and ideas'],
];

const maybe = (v, fmt = (x) => num(x)) =>
  (v === null || v === undefined ? '<span class="unknown"></span>' : fmt(v));

export const collections = {
  title: 'Collections',
  async render(root) {
    root.innerHTML = spinner('Loading collections');
    const rows = await api.collections();

    root.innerHTML = `
      <h1>Competitive research collections</h1>
      <p class="lede">Named groups of creators you can reopen, annotate and
        compare. A <b>control cohort</b> is the prerequisite for claiming
        any pattern is a breakthrough rather than a niche-wide shift.</p>
      ${card('New collection', `
        <form id="c-form">
          <div class="toolbar">
            <div class="grow">
              <label for="c-name">Name</label>
              <input id="c-name" placeholder="Classical Piano Competitors"
                required>
            </div>
            <div class="field">
              <label for="c-purpose">Purpose</label>
              <select id="c-purpose">${PURPOSES.map(
    ([v, l]) => `<option value="${v}">${esc(l)}</option>`).join('')}</select>
            </div>
            <div class="field">
              <label for="c-niche">Niche tag</label>
              <input id="c-niche" placeholder="classical_piano">
            </div>
            <div style="align-self:end">
              <button class="primary" type="submit">Create</button>
            </div>
          </div>
          <label for="c-notes">Notes</label>
          <input id="c-notes" placeholder="Why this group exists">
        </form>`)}
      ${rows.length ? listTable(rows)
    : empty('No collections yet', 'Create one above, then save creators to '
      + 'it from a discovery result or a research profile.')}`;

    document.getElementById('c-form').addEventListener('submit', async (e) => {
      e.preventDefault();
      try {
        await api.createCollection({
          name: document.getElementById('c-name').value.trim(),
          purpose: document.getElementById('c-purpose').value,
          niche: document.getElementById('c-niche').value.trim() || null,
          notes: document.getElementById('c-notes').value.trim() || null,
        });
        toast('Collection created');
        await collections.render(root);
      } catch (err) {
        toast(err.message, true);
      }
    });
  },
};

function listTable(rows) {
  return `<div class="card flush"><table>
    <thead><tr><th>Collection</th><th>Purpose</th>
      <th class="num">Members</th><th class="num">Researched</th>
      <th>Stalest refresh</th></tr></thead>
    <tbody>${rows.map((r) => `<tr>
      <td><a href="#/collections/${r.id}"><b>${esc(r.name)}</b></a>
        ${r.niche ? `<span class="chip">${esc(r.niche)}</span>` : ''}
        <div class="muted">${esc(r.notes || '')}</div></td>
      <td>${esc(r.purpose_label)}</td>
      <td class="num">${num(r.member_count)}</td>
      <td class="num">${num(r.researched_count)}</td>
      <td>${r.stalest_refresh ? dateTime(r.stalest_refresh)
    : '<span class="unknown"></span>'}</td>
    </tr>`).join('')}</tbody></table></div>`;
}

export const collectionDetail = {
  title: 'Collection',
  async render(root, params, id) {
    root.innerHTML = spinner('Loading collection');
    const c = await api.collection(id);

    const order = params.get('order') || 'relative';
    const members = [...c.members].sort(sorter(order));

    root.innerHTML = `
      <div class="row" style="justify-content:space-between;align-items:start">
        <div>
          <h1>${esc(c.name)}</h1>
          <p class="muted">${esc(c.purpose_label)}${
      c.niche ? ` · ${esc(c.niche)}` : ''} · created
            ${dateTime(c.created_at)}</p>
        </div>
        <div class="row" style="gap:8px">
          <a href="#/collections"><button>All collections</button></a>
          <button id="c-del" class="danger">Delete</button>
        </div>
      </div>
      ${c.notes ? note(esc(c.notes), '') : ''}
      <div class="grid cols-4" style="margin-bottom:14px">
        ${statCard('Members', num(c.member_count))}
        ${statCard('Researched', `${num(c.researched_count)}/${
      num(c.member_count)}`, 'have collected history')}
        ${statCard('Need research', num(c.needs_refresh.length),
    c.needs_refresh.length ? 'contribute nothing yet' : '')}
        ${statCard('Stalest refresh', c.stalest_refresh
    ? dateTime(c.stalest_refresh) : '—')}
      </div>
      ${c.caveats.map((x) => note(esc(x), 'warn')).join('')}
      ${card('Purpose', purposeForm(c))}
      ${members.length ? memberTable(members, order, id)
    : empty('No members', 'Save creators here from a discovery result or a '
      + 'research profile.')}`;

    document.getElementById('c-del').addEventListener('click', async () => {
      if (!window.confirm(`Delete "${c.name}"? The creators themselves and `
        + 'all their collected data are kept — only the grouping is '
        + 'removed.')) return;
      try {
        await api.deleteCollection(id);
        toast('Collection deleted');
        location.hash = '#/collections';
      } catch (err) {
        toast(err.message, true);
      }
    });

    document.getElementById('p-form').addEventListener('submit',
      async (e) => {
        e.preventDefault();
        try {
          await api.updateCollection(id, {
            purpose: document.getElementById('p-purpose').value,
            notes: document.getElementById('p-notes').value.trim() || null,
          });
          toast('Updated');
          await collectionDetail.render(root, params, id);
        } catch (err) {
          toast(err.message, true);
        }
      });

    root.querySelectorAll('.m-remove').forEach((b) => {
      b.addEventListener('click', async () => {
        try {
          await api.removeCollectionMember(id, b.dataset.creator);
          toast('Removed from collection');
          await collectionDetail.render(root, params, id);
        } catch (err) {
          toast(err.message, true);
        }
      });
    });

    root.querySelectorAll('.m-refresh').forEach((b) => {
      b.addEventListener('click', async () => {
        b.disabled = true;
        b.textContent = 'Researching...';
        try {
          const r = await api.runResearch({
            platform: b.dataset.platform, handle: b.dataset.handle,
            limit: 200,
          });
          toast(`${b.dataset.handle}: ${r.status}`);
          await collectionDetail.render(root, params, id);
        } catch (err) {
          toast(err.message, true);
          b.disabled = false;
          b.textContent = 'Research';
        }
      });
    });
  },
};

const statCard = (label, value, sub) => `
  <div class="card stat"><div class="label">${esc(label)}</div>
    <div class="value">${value}</div>
    ${sub ? `<div class="sub">${esc(sub)}</div>` : ''}</div>`;

function purposeForm(c) {
  return `<form id="p-form">
    <div class="toolbar">
      <div class="field">
        <label for="p-purpose">Purpose</label>
        <select id="p-purpose">${PURPOSES.map(([v, l]) =>
    `<option value="${v}"${v === c.purpose ? ' selected' : ''}>${
      esc(l)}</option>`).join('')}</select>
      </div>
      <div class="grow">
        <label for="p-notes">Notes</label>
        <input id="p-notes" value="${esc(c.notes || '')}">
      </div>
      <div style="align-self:end"><button type="submit">Save</button></div>
    </div>
    <p class="muted">A group assembled to copy formats and one assembled as
      a statistical control are different things. Recording which is which
      is what lets a later cohort analysis use this group.</p>
  </form>`;
}

function sorter(order) {
  if (order === 'views') {
    return (a, b) => (b.median_views ?? -1) - (a.median_views ?? -1);
  }
  if (order === 'handle') return (a, b) => a.handle.localeCompare(b.handle);
  return (a, b) => (b.best_relative_performance ?? -1)
    - (a.best_relative_performance ?? -1);
}

function memberTable(members, order, id) {
  const head = (key, label, title) => `<th class="num">
    <a href="#/collections/${id}?order=${key}"${
    order === key ? ' aria-current="true"' : ''} title="${esc(title)}">${
    esc(label)}</a></th>`;

  return `
    ${note(`Sorted by <b>${order === 'views' ? 'median lifetime views'
    : 'best performance against each creator’s own baseline'}</b>.
    Lifetime views measure audience size and elapsed time, not content
    quality — which is why the relative columns are the comparable ones.`,
  order === 'views' ? 'warn' : 'accent')}
    <div class="card flush"><table>
      <thead><tr>
        <th><a href="#/collections/${id}?order=handle">Creator</a></th>
        <th class="num">Followers</th>
        ${head('relative', 'Best vs own baseline',
    'Each creator compared to their own trailing window. Comparable.')}
        <th class="num">Typical vs own baseline</th>
        ${head('views', 'Median views',
    'Lifetime totals at mixed ages. NOT comparable across creators.')}
        <th class="num">Posts/obs</th>
        <th>Change point</th>
        <th>Why saved</th>
        <th></th>
      </tr></thead>
      <tbody>${members.map((m) => `<tr>
        <td>
          <div><a href="#/research/${m.creator_id}"><b>${esc(m.handle)}</b>
            </a> ${fixtureChip(m.is_fixture)}</div>
          <div class="muted">${esc(m.display_name || '')}</div>
          ${m.research_status !== 'collected'
    ? '<span class="chip warn"><span class="dot"></span>not researched'
      + '</span>' : ''}
        </td>
        <td class="num">${maybe(m.follower_count, compact)}</td>
        <td class="num"><b>${maybe(m.best_relative_performance,
    (v) => `${v}x`)}</b></td>
        <td class="num">${maybe(m.typical_relative_performance,
    (v) => `${v}x`)}</td>
        <td class="num">${maybe(m.median_views, compact)}</td>
        <td class="num">${num(m.publications_observed)}/${
  num(m.observations)}</td>
        <td>${m.breakthrough_classification
    ? classChip(m.breakthrough_classification) : ''}</td>
        <td class="muted">${esc(m.reason || '')}</td>
        <td>
          <div class="row" style="gap:6px">
            <a href="#/research/${m.creator_id}/timeline"><button>Timeline
              </button></a>
            ${m.research_status === 'collected' ? ''
    : `<button class="m-refresh" data-handle="${esc(m.handle)}"
                 data-platform="${esc(m.platform)}">Research</button>`}
            <button class="m-remove" data-creator="${m.creator_id}"
              title="Remove from this collection">&times;</button>
          </div>
        </td>
      </tr>${m.caveats.length ? `<tr><td colspan="9" class="muted">
        ${m.caveats.map((x) => esc(x)).join(' ')}</td></tr>` : ''}`).join('')}
      </tbody></table></div>`;
}
