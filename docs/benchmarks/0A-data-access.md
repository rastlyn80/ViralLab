# Phase 0A — Data-Access Feasibility Report

**Status: 3 of 4 gate conditions PASSED. Awaiting (a) ToS review and (b) one
provider decision from the owner.**
Run 2026-10-23. Gate definition: `DEVELOPMENT_PLAN.md` §7 Phase 0A.

**Total spent: $0.3056 of the $5.00 free credit.** 4 actors, 183 records.
No paid plan was purchased. (Apify's account-level usage counter lags; the
per-run `*.run.json` sidecars are authoritative, and two early runs settled
to a non-zero figure after capture, so the true total is ≈$0.31.)

---

## 1. Gate verdict

| # | Condition | Result |
|---|---|---|
| 1 | ≥80% BLOCKING+IMPORTANT completeness, **100% on every BLOCKING field** | ✅ **PASS** — three of four actors reach 100% on all BLOCKING fields |
| 2 | Timestamp quality cross-check | ✅ **PASS** — all four preserve time-of-day; none show scrape-time contamination |
| 3 | Measured cost within §10's projected band | ✅ **PASS** — measured **$0.30–$3.00 / 1,000**, inside the $0.30–$3.70 secondary band |
| 4 | Provider ToS reviewed against §2.3 | ⛔ **NOT DONE** — see §6 |
| + | Media availability answered in writing | ✅ **YES — media is directly downloadable.** See §3 |

**Phase 0A is not complete until condition 4 is discharged.** Nothing found
so far blocks the project; the remaining work is a terms review and one
cost/quality decision that is the owner's to make (§7).

---

## 2. Provider scorecards (measured)

| Actor | n | **$/1k measured** | BLOCKING | Completeness | Timestamps | Media | View precision | Verdict |
|---|---|---|---|---|---|---|---|---|
| `apidojo/tiktok-scraper` | 10 | **$0.30** | 100% | **100%** | PASS — 825-day span | ✅ direct mp4 | **exact** | PASS\* |
| `clockworks/free-tiktok-scraper` | 60 | **$3.00** | 100% | **100%** | PASS — 1,448-day span | ✗ paid extra | **rounded** | PASS |
| `apify/instagram-hashtag-scraper` | 45 | ~$0 observed | 100% | 90.1% | **FAIL — 0/45 ≥35d** | ✅ | exact | discovery only |
| `apify/instagram-reel-scraper` | 36 | **$2.56** | 100% | 81.6% | PASS — 1,452-day span | ✅ | exact | PASS |

\* `apidojo` passes on quality but is **hard-capped at 10 items per run on
the free plan** — see §4.

Raw samples are deliberately **not committed** (gitignored per §2.3); the
`*.run.json` provenance sidecars are also excluded. Re-run from
`probes/data_access/inputs/*.json` to reproduce.

---

## 3. Media availability — answered: **YES**

This was the decisive unknown for Tier 2/3, and the answer is positive.

`apidojo` returns `video.url`, a direct TikTok CDN link. Verified by
**actually downloading bytes**, not by inspecting the field:

```
HTTP 206 Partial Content
content-type : video/mp4
content-range: bytes 0-262143/2472817
file magic   : b'ftypisom'  -> MP4/ISO-BMFF, genuine video
```

No auth headers, no referer, and **range requests are honoured** — which
means partial fetches are possible (useful for cheap Tier 1 visual features
without pulling whole files).

The Instagram actors likewise return a working `videoUrl` at 100%.
`clockworks` returns an empty `mediaUrls` unless `shouldDownloadVideos` is
enabled, which bills a separate `video-download` event — so its media is
available but not free.

**Consequence:** third-party multimodal analysis is feasible, which
Revision 2 had listed as an open risk. Niche videos are not restricted to
metadata-only.

### Measured storage — replaces an assumption that was 3.4× too high

A 25.7-second video was **2,472,817 bytes → 0.096 MB/s**.

| Corpus (30 s average) | Projection from measurement | Rev 2 `[ASSUMPTION]` (~10 MB/video) |
|---|---|---|
| 1,000 | **~2.9 GB** | 10 GB |
| 10,000 | **~29 GB** | 100 GB |
| 100,000 | **~289 GB** | 1 TB |

Storage is a non-issue at any plausible scale for this project. `§10` of the
plan should be updated accordingly.

---

## 4. Findings that change the design

**1. The free-tier constraint is per-actor, not the credit.**
`apidojo` emits at most 10 items per run on the free plan regardless of
`maxItems`, stated plainly in its own run log:

> `Users with the Free Plan can retrieve a maximum of 10 items.`

Its log also showed it had fetched 20 results internally before truncating
to 10. So the $5 credit was never the limiting factor — $0.31 covered the
whole probe. This would have been invisible without reading the run log.

**2. `clockworks` rounds view counts; `apidojo` and the Instagram actors do
not.** 78% of `clockworks` values ≥10,000 carry ≤3 significant digits
(54900000, 36000000, 28300000 …) — it is expanding the abbreviated figure
shown on screen ("54.9M"). `apidojo` returns exact integers (2905713,
1385329, 144292).

Both scored **100% on field presence**, so the completeness scorecard alone
could never have distinguished them. A `views` precision check is now part
of `score_provider.py`.

Impact: for a `log(views / creator_median)` target, 3 significant figures
introduces <0.5% error and is tolerable. It does, however, blur
outlier-detection boundaries and makes small within-creator differences
unresolvable. Acceptable, but it should be recorded as a known limitation of
any corpus built from `clockworks`.

**3. Hashtag discovery returns only recent content; profile scraping returns
the full back-catalogue.** The Instagram hashtag scraper returned videos
aged **0–19 days, with 0 of 45 at ≥35 days** — so it cannot supply a
fixed-age mature corpus (§5.4 Strategy A). The profile-based reel scraper on
the same creators spanned **1,452 days**.

This yields a concrete pipeline shape, which the plan did not previously
specify:

> **discover creators via hashtag → fetch each creator's back-catalogue by
> profile → build the fixed-age corpus from the back-catalogues.**

Hashtag feeds also skew toward currently-promoted content, so using them
directly as a training corpus would have imported a selection bias on top of
the age problem.

**4. `follower_count` is absent from both the hashtag scraper and the IG reel
scraper** (0% in both), though `clockworks` and `apidojo` supply it for
TikTok. Since per-creator baseline normalisation depends on it, Instagram
discovery needs an **additional profile-scraper call per creator**. That is a
real cost line the §10 model does not yet carry — small (one call per
creator, not per video) but it should be counted.

**5. Re-query by stable ID works** — 10 of 10 TikTok URLs round-tripped to
the same IDs on a second fetch, so §5.4's Strategy B (longitudinal
re-querying for the trend cohort) is available. Metric deltas were zero over
minutes, as expected; proving actual trajectory capture needs fetches days
apart.

**6. IG `shares` did not populate** despite `includeSharesCount: true` on the
reel scraper (0% of 36), while the hashtag scraper returned `sharesCount` at
100%. Possibly a paid extra or a per-route difference. **[OPEN]** — minor,
since shares arrive via the hashtag route.

---

## 5. Honest note on the harness

The scorecard produced **three false FAIL verdicts** during this probe, every
one caused by my own alias list rather than a provider gap:

- `apidojo` initially failed on `creator_id`, `posted_at`, `duration_s` —
  all three were present, nested under `channel.*`, `uploadedAtFormatted`
  and `video.duration`.
- `clockworks` initially failed on `duration_s` and `cover_image_url` —
  present as `videoMeta.duration` and `videoMeta.coverUrl`.
- The IG hashtag scraper initially failed on `views` — present as
  `videoPlayCount`.

Had the verdict been trusted without inspecting the raw records, two
perfectly good providers would have been wrongly rejected. **Inspect the raw
structure before accepting any scorecard verdict** is now the standing rule
for this harness, recorded in `probes/data_access/README.md`.

Field presence proved to be the *easy* part. The findings that actually
matter — the 10-item cap, the rounded counts, the recency ceiling on hashtag
discovery — came from reading run logs and inspecting value distributions,
not from the completeness score.

---

## 6. ⛔ Outstanding: ToS review (gate condition 4)

**Not done.** No terms were read during this probe. Required before any
production collection:

1. **Apify's platform Terms** — what the customer may do with returned data.
2. **Each chosen actor's own terms** — actors are third-party code under
   their own conditions.
3. Confirmation that the actors access **logged-out public pages without
   circumventing technical controls**, which is the premise the §2.3 policy
   rests on. The actors are opaque here; this may only be answerable from
   their documentation and behaviour, and that limitation should be recorded
   rather than glossed.

Reminder: §2.3's legal footing rests on **[SECONDARY]** case summaries. It is
an internal operating policy, not legal advice.

---

## 7. Decision needed — provider configuration

Both routes clear the quality gate. They trade cost against data quality,
and the choice is the owner's.

| | **Option A — paid Starter** | **Option B — stay free-tier** |
|---|---|---|
| TikTok actor | `apidojo` | `clockworks/free-tiktok-scraper` |
| Cost | **$0.30/1k** + $19/mo | **$3.00/1k**, no subscription |
| View counts | **exact** | rounded to ~3 s.f. |
| Media | **included in the record** | separate paid download event |
| Item cap | lifted by the paid plan | none |
| 5,000-video corpus | **$1.50** + $19 = $20.50 | **$15.00** |
| 20,000-video corpus | **$6.00** + $19 = $25.00 | **$60.00** |

The $19/mo subscription breaks even at roughly **7,000 videos**, and Option A
is better data (exact counts, free media) above that point. Below it,
Option B is cheaper and needs no payment at all.

**My recommendation: start on Option B.** The MVP corpus target is
1,000–5,000 videos (§7 Phase 3), where free-tier `clockworks` costs ~$3–15
against ~$20 for Option A, the rounding is tolerable for a log-ratio target,
and no subscription is needed. Revisit if the corpus grows past ~7,000
videos or if exact counts turn out to matter for outlier detection — both
are observable later, and switching is cheap because the
`DataSourceAdapter` seam exists precisely for this.

Instagram needs no decision: hashtag scraper for discovery, reel scraper for
back-catalogues, both already passing.

---

## 8. What Phase 0A established

| # | Question | Answer |
|---|---|---|
| 1 | Official APIs cover own-account data? | ✅ Yes, free, with a ~2-year insights retention cap |
| 2 | Official niche discovery closed? | ✅ Confirmed closed (TikTok's own non-commercial requirement) |
| 3 | What does ViralLab need, measurably? | ✅ Scorecard built and self-tested |
| 4 | Providers with usable completeness? | ✅ Three of four at 100% on BLOCKING fields |
| 5 | Is `posted_at` trustworthy? | ✅ Yes — all four preserve time-of-day, no contamination |
| 6 | Is media obtainable? | ✅ **Yes — verified by downloading real MP4 bytes** |
| 7 | Real cost per 1,000? | ✅ **$0.30–$3.00 measured** |
| 8 | ToS compatible with §2.3? | ⛔ Outstanding (§6) |

**Next:** discharge §6, take the §7 decision, then Phase 0B (local AI
benchmarking on the RTX 2070), which needs no credentials and no spend.
