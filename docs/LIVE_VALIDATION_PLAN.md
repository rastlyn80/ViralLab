# First live piano-niche research experiment

A concrete, costed plan for the first real provider calls ViralLab would
make. **Nothing here is authorised.** The monthly cap is `$0.00` and
`providers_authorised` is empty, so every path below currently resolves to
the fixture adapter and refuses rather than attempts.

Written 2026-10-26, against migration head `e7c2b94f83da`.

Figures come from `server/discovery/apify.py`, whose per-actor rates were
taken from the provider's own pricing and whose estimator is the same code
that would reserve the budget — so the numbers below are what the system
would actually hold, not a separate guess.

---

## 0a. Prices re-verified, 2026-10-26

`[PRIMARY]` Checked against the actors' own Apify store pages, and against
the estimator that would reserve the budget. Every one of the five
operations below matches the figure this document quotes, and the plan
total of **$0.4170** is what the code computes.

| actor | listed | code | |
|---|---|---|---|
| `apidojo/tiktok-scraper` | $0.30 / 1k posts, no platform fee | $0.30 / 1k | ✅ 12,914 users, 99.9% success |
| `apify/instagram-hashtag-scraper` | $1.90–$2.60 / 1k (tier-dependent) | $2.56 / 1k | ✅ within band |

Two capability claims this plan depends on were re-confirmed on the same
pages: the Instagram hashtag actor returns **no follower counts** and
**recent posts only**. Both were already encoded in
`ProviderCapabilities`.

A step-by-step activation guide is in `docs/ACTIVATION_CHECKLIST.md`.

---

## 0. What has to happen before any of this runs

Three things, all the owner's:

1. `config/settings.yaml`: set `spending.monthly_cap_usd` to a real figure
   and add `apify` to `spending.providers_authorised`.
2. Provide `APIFY_TOKEN` in the gitignored `.env`.
3. Nothing else. `active_provider()` requires **both** the settings
   authorisation and the token, and the same application paths then serve
   live collection with no code change. That is the property the fixture
   adapter exists to protect: it subclasses the real adapter, so fixtures
   and live calls run through identical normalisers, budget reservations and
   storage.

**Recommended first cap: `$5.00`.** The whole plan below is `$1.06`, which
leaves room for one repeat without a second authorisation step.

---

## 1. The experiment

Five operations in order, because each answers a question the next one
depends on.

### Operation 1 — TikTok keyword discovery

| | |
|---|---|
| **Provider / actor** | Apify, `apidojo/tiktok-scraper` |
| **Query** | `keyword: "classical piano"`, limit 60 |
| **Expected results** | 60 publications, perhaps 25–45 distinct creators `[ASSUMPTION]` |
| **Estimated cost** | **$0.0500** (floor applies: 60 × $0.30/1k = $0.018, raised to the $0.05 minimum) |
| **Known limitations** | A keyword feed is biased toward recent and already-popular posts. `[PRIMARY, measured 0A]` a hashtag feed returned 0 of 45 records aged ≥ 35 days, so this cannot supply a mature fixed-age corpus. Only creators who used the term are visible. |
| **Establishes** | Whether real keyword search returns the field set the normaliser expects; how many distinct creators one query surfaces; whether the ranking separates small-creator breakouts from large accounts on real data. |
| **Cannot establish** | Anything about creators who did not use this phrasing. Any performance baseline. Whether the niche is bigger or smaller than this sample suggests. |

### Operation 2 — A second keyword, for vocabulary sensitivity

| | |
|---|---|
| **Provider / actor** | Apify, `apidojo/tiktok-scraper` |
| **Query** | `keyword: "Chopin"`, limit 60 |
| **Expected results** | 60 publications; overlap with Operation 1 unknown and **is the measurement** |
| **Estimated cost** | **$0.0500** |
| **Known limitations** | As Operation 1. |
| **Establishes** | How much the creator set depends on the search phrasing. If the overlap is small, a single keyword is not a view of the niche and the discovery surface needs multi-term sweeps before any cohort claim. |
| **Cannot establish** | Which phrasing is "correct". Neither is; they are different samples. |

### Operation 3 — TikTok hashtag discovery

| | |
|---|---|
| **Provider / actor** | Apify, `apidojo/tiktok-scraper` |
| **Query** | `hashtag: "pianotok"`, limit 60 |
| **Expected results** | 60 publications |
| **Estimated cost** | **$0.0500** |
| **Known limitations** | As Operation 1, plus: hashtag use is a creator behaviour, not a content property. Creators in this niche who do not tag are invisible to it. |
| **Establishes** | Whether hashtag and keyword discovery find the same people. A large disjoint set means neither alone is a sampling frame. |
| **Cannot establish** | Niche size. Hashtag popularity is not niche popularity. |

### Operation 4 — Full back-catalogue research, 1 creator

| | |
|---|---|
| **Provider / actor** | Apify, `apidojo/tiktok-scraper` |
| **Query** | `creator: <highest-ranked candidate>`, limit 200 |
| **Expected results** | Up to 200 publications; typically far fewer |
| **Estimated cost** | **$0.0750** (200 × $0.30/1k × 1.25 safety) |
| **Known limitations** | Returns what the provider can see, which is not provably the complete back-catalogue; a deleted or private post is indistinguishable from a gap. One observation per publication, so **no publication's growth is visible**. |
| **Establishes** | Whether a real creator's history is long enough and clean enough for the trailing baseline and the CUSUM change-point detector to produce anything. This is the first real test of the breakthrough analysis on non-synthetic data. |
| **Cannot establish** | Any trajectory. Any causal link between a publication and follower growth — there is no follower history across the boundary, and the detector will classify at most `inferred_breakthrough` and say so. |

### Operation 5 — Instagram hashtag, for the capability asymmetry

| | |
|---|---|
| **Provider / actor** | Apify, `apify/instagram-hashtag-scraper` |
| **Query** | `hashtag: "classicalpiano"`, limit 60 |
| **Expected results** | 60 recent publications |
| **Estimated cost** | **$0.1920** (60 × $2.56/1k × 1.25) — nearly 4× the TikTok rate for the same volume |
| **Known limitations** | `[PRIMARY]` **No follower count and no bio.** `videoViewCount` is null; `videoPlayCount` is the usable figure. Recent posts only. |
| **Establishes** | That the interface renders a missing follower count as unknown rather than zero **on live data**, and that an Instagram candidate is visibly not comparable to a TikTok one. |
| **Cannot establish** | Any views-per-follower figure, so the strongest ranking signal is simply absent on this platform. Not estimated, not substituted. |

### Total

| | |
|---|---|
| **Five operations** | **$0.4170** |
| With one Instagram creator research (200 items, `apify/instagram-reel-scraper`, $0.6400) | **$1.0570** |

The Instagram creator pass is listed but **not recommended first**: at
$0.64 it is the most expensive single operation in the plan and it cannot
return a follower count, so it buys the least evidence per dollar.

---

## 2. What the whole experiment establishes, and what it cannot

**Establishes.** That live collection works end to end through the same
code path fixtures exercise; that the recorded field contract matches what
the actors return today; roughly how many creators a niche query surfaces;
whether real histories support change-point detection; and what one real
research cycle costs — which is the figure every later scaling decision
needs and the one thing no amount of fixture work can produce.

**Cannot establish**, and no amount of this kind of collection will:

- **Any trajectory.** One observation per publication. The second
  observation is what makes growth visible, and that needs elapsed time, not
  more money.
- **Any causal claim.** No historical follower counts exist for these
  accounts. A publication cannot be shown to have caused account growth, so
  nothing will say it did.
- **Whether a breakthrough is the creator's or the niche's.** That needs a
  control cohort of creators in the same niche who did *not* break through,
  observed over the same period. `creator_collections` with
  `purpose="control_cohort"` exists for exactly this, and it is empty.
- **Anything about failure.** Discovery surfaces what a feed chose to show.
  Creators who tried similar content and were not surfaced are absent, and
  that absence cannot be corrected by collecting more of the same.

---

## 3. The second pass, which costs almost nothing

One week after Operation 4, research the same creator again: **$0.0750**.

This is the cheapest and most valuable operation in the entire plan. It
produces the first *repeated* observation of a competitor publication, which
is the specific evidence that can move a classification from
`inferred_breakthrough` toward `verified_trajectory_change`. The detector
already checks for it and refuses without it; nothing new has to be built.

---

## 4. Stopping rules

Pre-committed, so a surprising result is not reinterpreted afterwards:

1. **If a keyword query returns fewer than 10 distinct creators**, stop and
   reconsider the search vocabulary before paying for more queries.
2. **If the field set differs from the recorded contract**, stop. An ADR
   recording the discrepancy comes before any further collection
   (CLAUDE.md §2.4).
3. **If Operation 4's creator has fewer than 12 publications**, the
   breakthrough detector will correctly refuse to classify. That is a valid
   outcome, not a failure, and it is reported rather than worked around by
   lowering the threshold.
4. **The cap is the cap.** A reservation that would exceed it is refused,
   not negotiated. Overage requires a new explicit authorisation.

---

## 5. What is deliberately not in this plan

- **No multi-creator sweep.** Researching 20 discovered creators at $0.075
  each is $1.50 and is the obvious next step — *after* the five operations
  above show that one creator's data is usable. Paying for 20 before
  checking 1 is how a budget disappears into unusable data.
- **No media download.** Nothing in this plan fetches a video file.
  `docs/MULTIMODAL_PIPELINE.md` §6 records where the retention policy is
  enforced, and competitor media is transient even when analysis exists.
- **No GPU analysis.** The extractors do not exist (Phase 6). Analysis
  requests queue and are visibly not runnable.
- **No `clockworks/free-tiktok-scraper`.** It is 10× the cost of the
  validated exact-count actor *and* rounds view counts to ~3 significant
  figures, which blurs the outlier boundary the whole analysis depends on.
  It stays implemented for provider redundancy and is not the default.
