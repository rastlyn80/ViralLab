# Phase 0A — Data-Access Feasibility Report

**Status: IN PROGRESS — blocked on a credential decision (see §5).**
Started 2026-10-23. Gate definition: `DEVELOPMENT_PLAN.md` §7 Phase 0A.

No money has been spent. No accounts have been created. No provider has been
called. Nothing in this report is a measurement of a live provider yet.

---

## 1. What Phase 0A has to answer

| # | Question | Status |
|---|---|---|
| 1 | Do official APIs cover own-account data, and exactly what do they return? | ✅ **Answered** (§2) |
| 2 | Is the Research API / official discovery route genuinely closed? | ✅ **Answered** (§2) |
| 3 | What does ViralLab actually need from a provider, measurably? | ✅ **Answered** (§3) |
| 4 | Which providers return usable niche data, at what completeness? | ⛔ **Blocked** — needs a live sample (§5) |
| 5 | Is `posted_at` trustworthy enough for fixed-age comparison? | ⛔ **Blocked** — harness ready, needs a sample |
| 6 | Are media files obtainable from a provider at all? | ⛔ **Blocked** — needs a live sample |
| 7 | Real cost per 1,000 results | ⛔ **Blocked** — needs a live run |
| 8 | Provider ToS compatible with §2.3 policy? | 🟡 **Partial** (§4) |

---

## 2. Official API capabilities — verified against primary sources

All claims below are **[PRIMARY]**: checked against platform-official
documentation on 2026-10-23, not against third-party write-ups. Four claims
were upgraded from secondary during this phase, and doing so surfaced three
findings that change scope.

### Confirmed, and newly found

| Finding | Source | Consequence |
|---|---|---|
| Instagram deprecated `plays`, `clips_replays_count`, `ig_reels_aggregated_all_plays_count`, `impressions` on **21 Apr 2025**, replaced by unified **`views`** | [Graph API v22.0 changelog](https://developers.facebook.com/docs/graph-api/changelog/version22.0/) | Target metric set is `views`/`reach`/`saved`/`shares`/likes/comments |
| `impressions` survives **only for media created before 2 Jul 2024** | same | Available for your older archive; **absent for all new media** → usable as historical context, never as a model feature |
| **Insights retained ~2 years**; values delayed **up to 48 h** | [IG Media Insights reference](https://developers.facebook.com/documentation/instagram-platform/reference/instagram-media/insights) | **NEW — affects Phase 2 scope.** Own-account backfill is capped at ~2 years on a rolling window; anything older is permanently unrecoverable. Also forces 7-day anchors to be read at **day 9+** |
| Two non-equivalent IG API paths; **Hashtag Search and Business Discovery are Facebook-Login-only** | [Instagram Platform overview](https://developers.facebook.com/docs/instagram-platform/overview/) | Commits the project to the Facebook Login path |
| TikTok Display API returns **no media URL** and **no `collect_count`** | [TikTok Video Object](https://developers.tiktok.com/docs/en/tiktok-api-v2-video-object) | Own TikTok masters must come from your local archive |
| TikTok Research API requires being **"independent of commercial interests"** on a **"not-for-profit or non-commercial basis"**, plus ethical review | [Research Tools: Access and Eligibility](https://developers.tiktok.com/products/research-api/) | Closed to ViralLab permanently, not merely inconvenient |
| Apify **Free plan: $0, no credit card, $5 Store credit** (expires monthly, cannot be topped up on free) | [Apify pricing](https://apify.com/pricing) | **Makes a zero-cost 0A probe possible** — see §5 |

### Answer to questions 1 and 2

**Own-account coverage is confirmed sufficient and free**, with the two-year
retention caveat. **Official niche discovery is confirmed closed** — the
non-commercial requirement in TikTok's own eligibility text is explicit, so
this is settled rather than assumed. Instagram's Hashtag Search / Business
Discovery remain a narrow official foothold, capped by the 200 req/hr budget.

---

## 3. Provider requirements — now measurable

Built and self-tested: `probes/data_access/` (stdlib only, no dependencies).

`field_requirements.py` defines what the provider must deliver:

- **BLOCKING** (gate fails below 100%): `video_id`, `creator_id`,
  **`posted_at`**, `views`, `duration_s`
- **IMPORTANT** (≥80% weighted): `caption`, `likes`, `comments`, `shares`,
  `follower_count`, `cover_image_url`
- **NICE**: `saves`, `music_id`, `hashtags`, `region`
- **CAPABILITY**, reported separately and never scored: `media_url`

`score_provider.py` scores a raw sample and prints a gate verdict, including
timestamp-plausibility checks that go beyond "does the field parse":
no dominant value (scrape-time contamination), time-of-day preserved (date
rounding destroys the posting-time features the brief asks for), sufficient
date span for chronological validation, and at least some videos ≥35 days
old for Strategy A (§5.4).

**Harness self-test passes both ways**: the synthetic good fixture exits 0,
the negative control exits 1. Writing the negative control caught two real
gaps in the scorer's first version — partial scrape-date contamination and a
sample with zero fixed-age-eligible videos both passed silently. Both are
now hard failures. It also caught two alias gaps in the requirements file:
real Apify output nests author and music fields, which the first draft
missed.

**`media_url` deliberately excludes page links** (`share_url`,
`webVideoUrl`, `embed_link`). A page link is not downloadable media;
conflating them is the exact error corrected in plan §2.1.

---

## 4. Legal review — partial

The §2.3 operating policy is written and binding. What remains is
provider-specific: each candidate's own Terms must be read against it before
use, in particular their stance on (a) what the customer may do with
returned data, (b) whether they claim to access only logged-out public
pages, and (c) any restriction on commercial or redistributive use.

**Not yet done**, because it should be done for the provider actually
chosen, not speculatively for a shortlist. Blocked behind the same decision
as §5.

Note for the record: both court-case summaries underpinning §2.3 are
**[SECONDARY]** sources. They are adequate for setting an internal operating
policy and are **not** legal advice.

---

## 5. ⛔ Blocked — decision needed

Questions 4–7 all require one live provider sample (~500 videos from your
niche). I have not created an account or spent anything.

**Recommended route — Apify free tier, $0:**
- $0/month, **no credit card required**, $5 of Store credit **[PRIMARY]**
- At the observed $0.30–$3.70 per 1,000 results **[SECONDARY]**, $5 buys
  roughly **1,300–1,600 results** — enough for the ~500-video probe plus a
  re-query test, with headroom
- Credits expire monthly and cannot be topped up on the free plan, so the
  probe should be run in one sitting

**What I need from you:** either

1. **Create a free Apify account and give me the API token** (no card, no
   charge) — then I run the probe, score it, and complete §§4–7; or
2. **Approve a small paid budget** (state the cap) if you would rather use a
   provider with better coverage than the free tier allows; or
3. **Name a provider you already have access to**, and I will adapt the
   harness to its output format.

I will not create accounts, accept terms on your behalf, or spend anything
without an explicit instruction.

### Candidate providers to sample (shortlist, unverified)

Each needs its own ToS review per §4 before use. Listed by what they claim,
not by what has been confirmed:

| Candidate | Claimed relevance | Key unknown |
|---|---|---|
| Apify TikTok actors (several, varying price/quality) | Pay-per-result, free-tier accessible; multiple actors to compare | Which actor returns `posted_at` with time-of-day, and whether any returns media |
| Apify Instagram actors | Reels metadata | Reels coverage vs posts; `views` availability post-Apr-2025 |
| Bright Data | Named in the Jan 2024 ruling; large scale | Minimum spend, likely above probe scale |

Sampling **two or three** actors rather than one is worth the credit: the
differences between them on `posted_at` quality and media availability are
exactly what the gate turns on, and the free credit covers it.

---

## 6. Interim conclusion

Nothing found so far blocks the project, and the own-account foundation is
confirmed stronger than assumed in Revision 1 — free, officially supported,
and sufficient for the Phase 8 experiment on its own.

Two scope reductions are now established fact rather than risk:
Instagram's **~2-year insights retention** caps historical backfill, and
**own TikTok media must come from your local archive**.

The open question is entirely about third-party niche data, and it is now
*measurable* rather than arguable: the harness exists, the thresholds are
written down, and the gate verdict will be mechanical once a sample is in
hand.

**Next action: your decision in §5.**
