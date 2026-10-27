/**
 * Plain English, client side. One fetch, one cache, one vocabulary.
 *
 * The server owns the words (`server/features/lexicon.py`). This is
 * the only thing the interface is allowed to use to turn a stored key
 * into something a reader sees, because the alternative - each view
 * inventing its own label - is how `background_edge_density` ends up
 * called three different things on three pages.
 *
 * It fails soft on purpose. If the lexicon cannot be fetched, every
 * name falls back to a de-underscored version of the key: ugly, but
 * readable, and the page still renders. A view that threw here would
 * take out a whole screen over a cosmetic lookup.
 */

import { api } from './api.js';

let cache = null;
let inflight = null;

export async function loadLexicon() {
  if (cache) return cache;
  if (!inflight) {
    inflight = api.featureLexicon()
      .then((d) => { cache = d; return d; })
      .catch(() => {
        // Soft failure. Names degrade, pages do not.
        cache = { features: {}, outcomes: {} };
        return cache;
      });
  }
  return inflight;
}

/** Readable form of a key, with or without the lexicon loaded. */
export function titleise(key) {
  if (!key) return '';
  return String(key)
    .replace(/^vision_/, '')
    .replace(/_/g, ' ')
    .replace(/^./, (c) => c.toUpperCase());
}

export function featureName(key) {
  return cache?.features?.[key]?.display_name || titleise(key);
}

export function outcomeName(key) {
  return cache?.outcomes?.[key]?.display_name || titleise(key);
}

/** Either kind, since a caller often does not know which it has. */
export function anyName(key) {
  return cache?.features?.[key]?.display_name
    || cache?.outcomes?.[key]?.display_name
    || titleise(key);
}

export function plain(key) {
  return cache?.features?.[key] || cache?.outcomes?.[key] || null;
}

/**
 * The one-line explanation under a name, where one exists.
 *
 * Returns '' rather than a placeholder: a missing sentence should
 * leave a clean gap, not the words "no description available", which
 * is noise on every row that lacks one.
 */
export function explain(key) {
  return plain(key)?.measures || '';
}

/** What a measurement is NOT, which is the line that prevents misreading. */
export function notAMeasureOf(key) {
  return plain(key)?.not_a_measure_of || '';
}

/**
 * Wrap exact figures so the audit and the reader both treat them as
 * secondary. Anything inside `.technical` is hidden unless the user
 * has asked for technical detail.
 */
export function technical(html) {
  return `<span class="technical">${html}</span>`;
}

/**
 * A strength word for a correlation, matching the server's bands.
 *
 * Duplicated deliberately rather than fetched: this is used while
 * rendering a table row, and a round trip per row to agree on an
 * adjective would be absurd. The bands are asserted equal to the
 * server's in the UI tests.
 */
export function strength(rho) {
  if (rho === null || rho === undefined) return 'not measured';
  const a = Math.abs(rho);
  if (a >= 0.5) return 'a strong tendency';
  if (a >= 0.3) return 'a moderate tendency';
  if (a >= 0.15) return 'a weak tendency';
  return 'barely any tendency';
}

/**
 * Whether a relationship is good news, bad news, or unreadable.
 *
 * Direction comes from the outcome's own definition - skip rate is
 * good when it falls - and an outcome with no stated direction gets
 * no interpretation at all rather than an optimistic guess.
 */
export function sense(rho, outcomeKey) {
  const better = cache?.outcomes?.[outcomeKey]?.better_when;
  if (rho === null || rho === undefined) return '';
  if (better !== 'higher' && better !== 'lower') return '';
  const rises = rho > 0;
  const good = (rises && better === 'higher') || (!rises && better === 'lower');
  return good ? 'encouraging' : 'discouraging';
}

/** A plain sentence for one feature-outcome relationship. */
export function relationshipSentence(featureKey, outcomeKey, rho) {
  const f = featureName(featureKey).toLowerCase();
  if (rho === null || rho === undefined) {
    return `${featureName(featureKey)} has not been related to an outcome.`;
  }
  if (outcomeKey === 'reels_skip_rate') {
    return `Videos with more ${f} tended to have ${
      rho < 0 ? 'fewer' : 'more'} viewers skipping them.`;
  }
  return `Videos with more ${f} tended to have ${
    rho > 0 ? 'more' : 'less'} ${outcomeName(outcomeKey).toLowerCase()}.`;
}
