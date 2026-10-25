# Temporal intelligence — foundations

How the data collected now will support the question the product exists to
answer: **what worked before, what works now, and what is becoming
successful?**

This is a foundations document, not an implementation. The implementation is
Phase 7. What matters today is that the storage decisions being made in
Phase 4 do not make that question unanswerable later — because most of them
cannot be retrofitted.

Written 2026-10-25.

---

## 1. The four storage rules that cannot be added later

### Rule 1 — publication time and observation time are different columns

`videos.posted_at` is when the creator posted. `metrics_snapshots.captured_at`
is when we looked. A system that stores only one of them can never compute
an age, and without an age no two view counts are comparable.

Enforced now: `server/research/service.py` writes both on every pass, and
`tests/server/test_research.py::test_publication_and_observation_times_are_stored_separately`
fails if they are ever conflated.

### Rule 2 — observations are append-only

A second research pass on the same creator writes a **new**
`metrics_snapshots` row. It does not update the old one. Verified: a refresh
takes 62 publications from 62 snapshots to 124, with the publication count
unchanged.

This is the single most valuable thing Phase 4 does for Phase 7. Two
readings of the same publication at different times are what make growth
visible; overwriting would have destroyed that permanently, and no amount of
later cleverness recovers it.

### Rule 3 — the period is fixed at write time, not derived at read time

`budget_reservations.period` stores `YYYY-MM` rather than deriving it from a
timestamp, so settling a charge after a month boundary cannot move it. The
same principle applies to any future aggregate: the window it belongs to is
part of the record.

### Rule 4 — provenance travels with every row

`videos.provenance`, `creators.provenance` and `discovery_jobs.coverage_notes`
record which source produced a row, under what limits, at what cost. A
2024 observation from a provider that rounds counts is not the same evidence
as a 2026 observation from one that does not, and a later analysis has to be
able to tell.

---

## 2. What the data can already support

| question | supported today | why |
|---|---|---|
| How did this creator's level change over their career? | **yes** | within-creator local baselines; change-point detection |
| Which publications over-performed for this creator? | **yes** | relative performance against their own trailing window |
| Did a specific publication grow over time? | **not yet** | needs ≥2 observations of it; one research pass gives one |
| Is this creator growing now? | **not yet** | needs repeated `creator_snapshots`; TikTok gives one per pass, Instagram gives none |
| Did a format that worked in 2024 still work in 2026? | **partially** | publication dates and features will allow it; features are Phase 6 |
| Is a pattern emerging across a niche? | **no** | needs many creators; one at a time so far |
| TikTok versus Instagram for the same content | **yes for own accounts** | `publication_links`, 201 proposed pairs |

The honest summary: the *schema* supports temporal analysis; the *data* does
not yet, because repeated observation takes elapsed time and nothing can
shorten that. Every research pass from here makes it more possible.

---

## 3. How recency weighting will work, and what it must not do

The governing rule from `docs/PRODUCT_VISION.md` §3.F:

> A strategy that worked in 2023 must not be recommended in 2026 merely
> because its historical videos accumulated many views. Conversely, older
> strategies must remain **discoverable and eligible for reconsideration**
> if they become effective again.

Both halves matter, and they pull in opposite directions. The resolution is
that **retention and relevance are different concerns, implemented
separately**:

- **Retention is permanent.** No observation is ever deleted or
  down-sampled. The 2023 data stays exactly as collected.
- **Relevance is a weight applied at analysis time**, not a filter applied
  at storage time.

Design, for Phase 7:

```
weight(observation) = exp(-age_days / HALFLIFE_DAYS * ln2)
```

with three requirements:

1. **`HALFLIFE_DAYS` is a recorded parameter on every finding**, not a
   constant buried in code. A finding computed with a 90-day half-life and
   one computed with a 540-day half-life are different claims, and the
   stored `findings` row says which it is (`docs/SCHEMA_PLAN.md` §5).
2. **Unweighted analysis stays available.** "What worked historically" is a
   legitimate question, answered by setting the weight to 1. Recency
   weighting is a lens, not a truth.
3. **A re-emerging pattern must be findable.** Because the raw observations
   are intact, a rolling-window scan can detect that a 2023 format is
   working again — which a system that had discarded or permanently
   discounted old data could not do. This is the concrete reason Rule 2
   above is non-negotiable.

### Avoiding training on stale data without discarding it

| mechanism | what it does |
|---|---|
| chronological splits only | train on the past, validate on the future; never shuffle a time series (CLAUDE.md §3.10) |
| walk-forward drift (V5) | re-validate a model on each new window; a finding that stops holding is **demoted**, not quietly reused |
| recency-weighted training | recent observations dominate the fit without old ones being deleted |
| `findings.expired_at` | an expired finding is marked, and a marked finding is not recommended |
| re-emergence scan | the same rolling window that detects decline detects recovery |

**A silently expired finding is worse than no finding**, because it carries
borrowed credibility. That is why expiry is a stored state rather than a
convention.

---

## 4. Comparisons the architecture must keep possible

### Recent versus historical content

Requires: publication date (have), features (Phase 6), performance at a
comparable age (needs repeated observations). Supported by trailing local
baselines, which compare like with like in time by construction.

### Different creator baselines

Already the only ranking the product offers. `relative_performance` is
always against **that creator's own** preceding publications, never against
absolute views or another creator. Account size therefore cannot masquerade
as content quality.

### Different niches

`creators.niche_tags` exists. `[OPEN]` niche assignment is currently
unpopulated and will need either hashtag clustering or manual labelling —
recorded rather than guessed.

### TikTok versus Instagram

Two distinct asymmetries, both measured and both already encoded in
`ProviderCapabilities` so no analysis can forget them:

- TikTok reports `saves`; **Instagram's actors do not**.
- Instagram reports **no follower count at all**; TikTok does.

A cross-platform comparison of saves or followers is therefore impossible,
not merely inadvisable, and the capability flags make attempting it fail
loudly rather than silently compare a number against a null.

### Emerging versus declining patterns

Phase 7, via `trend_measurements`: a feature's association with performance
stored **as a time series per platform and niche**, not as one coefficient.
One number cannot represent something that changes, which is the whole
point.

---

## 5. Attribution discipline

Not every change is the algorithm. `findings.attributed_cause` defaults to
`unknown` and may only be set with a signature to back it:

| cause | signature |
|---|---|
| algorithm change | niche-wide, abrupt, synchronous across unrelated creators |
| audience preference shift | gradual, correlated with content characteristics |
| sampling change | our collection changed, not the world |
| competition | more creators entering a format |
| creator behaviour | they changed what they post |

The breakthrough detector already carries the first of these as a standing
caveat — a niche-wide shift is indistinguishable from an individual
breakthrough when only one account is in view — and cohort comparison is the
named prerequisite for removing it.

---

## 6. What is deliberately not built

- **No trend prediction.** Detecting that something changed is a different
  and much easier claim than forecasting what will change, and only the
  first is currently supportable.
- **No automatic alerts.** An alerting system nobody trusts is worse than
  none, so this waits until there is a false-positive budget to hold it to.
- **No "current best strategy" summary.** It would be a single number
  standing in for a distribution that moves.

---

## 7. The nearest concrete milestone

Research the same creator twice, a week apart. That produces the first
repeated observation of a competitor publication, which is what upgrades
`inferred_breakthrough` toward `verified_trajectory_change` — the detector
already checks for exactly that evidence and refuses without it. Nothing new
has to be built for it; time has to pass.
