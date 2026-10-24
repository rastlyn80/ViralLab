# Phase 3 data readiness

What Phase 2 leaves behind for cross-platform analysis, and — more
importantly — what it **cannot** leave behind. Phase 3 is not implemented
here; this records the state of the data so Phase 3 starts from facts rather
than from assumptions.

Written 2026-10-24.

---

## 1. The blocking fact: there is no longitudinal history yet

| | |
|---|---|
| Instagram snapshots | 289 — **1 per publication** |
| TikTok snapshots | 702 — 3 per publication, **all captured on 2026-10-24** |
| Distinct capture days | **1** |
| Age at capture, TikTok | 1 → **1357** days (median 236) |
| Age at capture, Instagram | 1 → 1120 days (median 217) |

Every snapshot in the database is a **lifetime-cumulative reading taken at an
arbitrary age**. The three TikTok readings per video are three timestamps on
the same day, from the import and validation runs — not a time series.

Two consequences, and they are absolute:

**`views@7d` and `views@30d` cannot be computed for any publication that
exists today.** A fixed-age anchor needs two snapshots bracketing the anchor
age, and there is only one capture day. `server/analytics/fixed_age.py`
already enforces this: `metric_at_age()` returns `value=None` with an
`unavailable_reason` rather than interpolating. **Nothing may back-fill these
anchors.** A views@7d figure derived from a reading taken at day 236 is not a
measurement, it is a fabrication, and it would silently poison every model
trained on it.

**Cumulative views must never be compared across publications as they
stand.** The corpus spans 1 to 1357 days of exposure. Ranking those by
lifetime views measures elapsed time, not content quality. This is CLAUDE.md
§3.8a and it is the easiest invalidating error in the project to make
quietly.

### What does become possible, and when

The daily collector is running on Ubuntu and appends one reading per video
per pass. From the first pass onward:

| anchor | first available for a video published on day 0 | corpus-wide |
|---|---|---|
| `views@7d` | day 9+ (Instagram lags up to 48 h) | ~2026-11-02 for videos posted from now |
| `views@30d` | day 32+ | ~2026-11-25 |
| `views@90d` | day 92+ | ~2027-01-24 |

So Phase 3's modelling set grows from zero at the rate the owner publishes.
For the **existing** archive, the only sound comparison is Strategy A from
plan §5.4 — treat mature publications as mature and compare within an age
band — never a reconstructed anchor.

---

## 2. Metric availability is asymmetric, and must stay that way

Measured from the live APIs, not from documentation alone.

| signal | Instagram | TikTok |
|---|---|---|
| views | yes | yes |
| likes | yes | yes |
| comments | yes | yes |
| shares | yes | yes |
| **saves** | **yes** | **NO — field does not exist** |
| reach | yes | no |
| total_interactions | yes | no |
| **avg watch time** | **yes** (`ig_reels_avg_watch_time`, ms) | **no** |
| **total view time** | **yes** (`ig_reels_video_view_total_time`, ms) | **no** |
| **skip rate** | **yes** (`reels_skip_rate`, percent) | **no** |
| per-second retention curve | **no** | no |

### TikTok saves and retention stay unknown, never zero

`[PRIMARY]` TikTok's Video Object does not expose `collect_count` (saves) —
Revision 1 of the plan wrongly claimed it did. It exposes no retention or
watch-time field either.

Verified in the stored data: of 702 TikTok snapshots, `saves` is **NULL in
all 702**, and **zero of them hold the value 0**. That distinction is the
whole point. A `0` would mean "measured, and nobody saved it"; `NULL` means
"TikTok does not tell us". Any Phase 3 aggregate, ratio or model input must
treat these as missing — `COALESCE(saves, 0)` on TikTok data would invent a
finding.

### Instagram watch-time and skip-rate: available, and now collected

`[PRIMARY]` Verified against Meta's Instagram Media Insights reference
(checked 2026-10-24) and then confirmed empirically against this account
(media `18114141676850150`):

| metric | live value | documented status |
|---|---|---|
| `ig_reels_avg_watch_time` | 6006 (ms → 6.0 s) | available |
| `ig_reels_video_view_total_time` | 3411578 (ms → 56.9 min) | "metric in development" |
| `reels_skip_rate` | 57.7 (percent) | "estimated and in development" |

`reels_skip_rate` is documented as "the percentage of views from people who
skipped during the first 3 seconds" — directly relevant to the hook
question, which is the single most interesting cross-platform difference the
owner describes.

The same probe confirmed `plays` and `clips_replays_count` now return Meta
error 100, consistent with their April 2025 deprecation. No per-second
retention curve exists on either platform.

These three are now collected, in a **separate API call** from the core
metrics. Two of the three are "in development", so a withdrawal must not be
able to take down a whole pass: on failure the snapshot is still written with
`retention_status` set to `unavailable`, and when the call returns nothing
the status is `empty` and **no key is written at all** — absent stays absent.
Units are recorded alongside the values (`retention_units`) so no later
reader has to infer milliseconds from the magnitude.

**Caveats Phase 3 must carry:** both time metrics are "in development" and
`reels_skip_rate` is additionally "estimated", so they may change definition
or be withdrawn without notice. They exist on **Instagram only**, so any
retention comparison is within-platform; there is no TikTok counterpart to
compare against, and constructing a proxy would be inventing data.

---

## 3. What is preserved, and where

| requirement | where it lives | state |
|---|---|---|
| independent per-platform metrics | `metrics_snapshots`, one row per reading per video, **append-only** | 991 rows |
| exact publication timestamps | `videos.posted_at`, timezone-aware | 523/523 |
| platform-specific captions | `videos.caption`, full text | 522 captioned, max 1194 chars |
| platform-specific hashtags | `videos.hashtags` (JSONB) | 294 distinct on TikTok, 328 on Instagram |
| per-platform durations | `videos.duration_s` | TikTok 234/234; Instagram 258/289 (the 31 gaps are publications whose media the API withholds) |
| variant / hook differences | `performances` → `local_masters` → `video_master_links`, with `audio_offset_s` recording a trimmed head or changed hook | 156 performances, 250 masters |
| cross-platform correspondence | `publication_links`, status `proposed` only | 201 proposals |
| provenance of every relationship | `video_master_links.evidence` / `publication_links.evidence` (JSONB), plus `audio_method`, `decided_by`, `decided_at` | all rows |
| raw platform payloads | `metrics_snapshots.raw` (JSONB), lossless | all rows |
| media for re-analysis | 258 Instagram videos, 736 MB | 90.2% of preservable |

### Provenance is explicit, not implied

Phase 3 must be able to tell a machine guess from a human judgement, and
strong evidence from weak:

- `decided_by` — `matcher` or `human`. Never inferred.
- `audio_method` — `spectral` (measured, gap +0.437) / `envelope`
  (superseded, gap −0.236) / `none`. A score alone does not reveal which
  descriptor produced it, so the method is stored beside it.
- `publication_links.status` — `proposed` / `confirmed` / `rejected`. There
  is **no `auto`**: a cross-platform pair rests on metadata only and cannot
  be promoted without a human.
- `performances.status` — `inferred` / `confirmed`, with the grouping's
  internal cohesion in `notes`.

**Phase 3 should filter on these, not on score thresholds.** Treating a
`proposed` pair as established, or an `inferred` performance as confirmed,
would import exactly the uncertainty this layer exists to make visible.

---

## 4. Questions Phase 3 can and cannot answer today

**Can, now:**

- Within-platform comparisons across an age band (Strategy A, plan §5.4).
- Instagram retention vs Instagram engagement — `reels_skip_rate` against
  saves and shares, on the same platform and the same anchor.
- Caption and hashtag differences between a cross-posted pair, for the 201
  proposed pairs (as *proposals*).
- Whether a hook change coincides with a different outcome, for the pairs
  where `audio_offset_s` is nonzero — 3 auto-linked publications today.

**Cannot, yet:**

- Any fixed-age comparison. No bracketing snapshots exist. Earliest real
  `views@7d` is ~9 days after a publication posted from now.
- TikTok retention or saves, in any form.
- Cross-platform retention comparison — Instagram-only metrics have no
  counterpart.
- Causal claims about hooks. The archive is observational; the only causal
  evidence this project can produce comes from the Phase 8 pre-registered
  experiment (CLAUDE.md §3.4).

**Needs a human first:**

- 201 cross-platform proposals, unconfirmed. A model trained on them inherits
  whatever they get wrong.
- 27 pending master links.
- 156 inferred performance groupings.

None of this blocks other development. Review is incremental by design
(`scripts/review_ui.py`), and every unreviewed item is already stored with a
status that says what it is.

---

## 5. Invariants Phase 3 must not break

1. **Never compare metrics captured at different ages** as if equivalent.
2. **Never synthesise `views@7d` / `views@30d`** where bracketing snapshots
   do not exist. `fixed_age.py` returns `None` with a reason; honour it.
3. **Never coalesce missing TikTok saves or retention to zero.**
4. **Never mutate a raw snapshot.** Anchoring is a read-time derivation, so
   changing the anchor policy can never destroy collected data.
5. **Never read a similarity score as a probability of correctness.**
6. **Never treat `proposed` or `inferred` as established.**
7. **Both sides of any ratio must share the same anchor age**, including the
   creator baseline in the denominator.
