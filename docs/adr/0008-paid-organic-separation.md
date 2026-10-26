# ADR 0008 — Paid/organic separation for boosted Instagram posts

**Date:** 2026-10-26
**Status:** Accepted
**Driver:** Milestone M1 in `docs/BLUEPRINT.md`. The owner runs two $1/day
Instagram ads, so every recent Instagram metric is potentially a blend of
paid and organic exposure. `BLUEPRINT.md` §1.3 proposed a decomposition and
tagged it `[SECONDARY]`. CLAUDE.md §2.1 forbids writing integration code
against an unverified capability claim; this ADR is that verification.

---

## 1. What the blueprint assumed, and why it was wrong

`BLUEPRINT.md` §1.3 said:

> A boosted Instagram post becomes an ad with its own `ad_id` in the
> Marketing API […] then for every boosted post:
> `organic ≈ media_insights(total) − ads_insights(paid)`

That subtraction assumes the **organic media-insights endpoint reports a
paid-inclusive total**. It does not. The assumption is inverted, and
building on it would have subtracted paid exposure from a figure that never
contained it — producing a negative "organic" number for every boosted
post, or worse, a plausible-looking one that was wrong.

## 2. Verified — primary sources, checked 2026-10-26

| Claim | API / version | Source |
|---|---|---|
| "Metrics such as `comments`, `likes`, `views`, and `total_interactions` report organic interaction metrics only; interactions on ads containing a media object are not counted." | Instagram Platform, Media Insights | [IG Media Insights reference](https://developers.facebook.com/documentation/instagram-platform/reference/instagram-media/insights) |
| `total_views` = "Total number of times the media has been seen across all surfaces, **including views from promoted/boosted/ad media and Facebook**." Likewise `total_likes`, `total_comments`. | same | same |
| The `total_*` aggregated metrics are available **for Instagram API with Facebook Login only** | same | same |
| `views` = "Total number of times IG Media has been played on Instagram." | same | same |
| `facebook_views` = "Total number of times IG Media has been played on Facebook. […] For REELS, this can be either plays from crossposted or cross recommended from Instagram to Facebook" | same | same |
| `crossposted_views` = "Total number of times the video IG Media was played, aggregated across Instagram and Facebook." | same | same |
| `reels_skip_rate` = "The percentage of views from people who skipped during the first 3 seconds of the reel […] divided by the number of intial views." Marked **"[Metric is estimated and in development.]"** | same | same |
| No breakdown parameter separates paid from organic. The only breakdowns are `action_type` and `story_navigation_action_type`. | same | same |
| Ads Insights is available at ad, ad set, campaign and ad-account level (`/{ad-id}/insights`) and **requires the `ads_read` permission** | Marketing API **v26.0** | [Ads Insights API](https://developers.facebook.com/docs/marketing-api/insights) |
| Ads Insights supports `publisher_platform`, `platform_position`, `impression_device` breakdowns | Marketing API | [Breakdowns](https://developers.facebook.com/docs/marketing-api/insights/breakdowns/) |
| `video_p25_watched_actions` … `video_p100_watched_actions` exist; they **do not support the `region` breakdown**, and no `video_*` field may be combined with hourly-stats breakdowns | Marketing API | same |
| `effective_instagram_media_id` ("The ID of an Instagram post to use in an ad") exists on the AdCreative node — this is the join key from an ad back to the organic media id | Marketing API **v26.0** | [Ad Creative reference](https://developers.facebook.com/docs/marketing-api/reference/ad-creative/) |

### Not verified, and therefore not relied on `[SECONDARY]`

- Whether `reach` and `ig_reels_avg_watch_time` are organic-only. The
  organic-only sentence reads "**Metrics such as** comments, likes, views,
  and total_interactions" — a non-exhaustive list. `reach` and the reels
  watch-time metrics are not named by it and not named by the paid-inclusive
  `total_*` set either. **Status: `[OPEN]`.**
- **Whether `reels_skip_rate` is organic-only.** This is the single most
  important open question in M1, because `reels_skip_rate` is the project's
  headline outcome variable. Its documented denominator is "initial views",
  and `views` is documented as organic-only, but the doc never states that
  the skip-rate denominator *is* that metric. **Status: `[OPEN]`**, with an
  empirical test specified in §5.
- That an in-app Instagram boost is retrievable through the Marketing API
  as an ad with an `effective_instagram_media_id`. Highly likely, and the
  mechanism is documented for API-created ads, but no platform-official
  sentence was found stating it for app-created boosts. **Status: `[OPEN]`
  until an `ads_read` token actually returns the rows.**

## 3. Confirmed empirically — this account, 2026-10-26

CLAUDE.md §2.4 requires the live field set to be compared against the docs.
Probe: `probes/m1_paid_organic.py`, six most recent publications, each
metric requested **singly** so an unsupported name could be distinguished
from an absent value. API version in use is **v22.0** (the docs above are
v26.0; the metric names are unchanged between them, and nothing here depends
on a v26-only field).

| media | posted | `views` | `facebook_views` | `crossposted_views` | `total_views` |
|---|---|---|---|---|---|
| 18114141676850150 | 2026-10-22 | 801 | 530 | 1331 | 30605 |
| 18129262708798801 | 2026-10-19 | 5929 | 4739 | 10668 | 33136 |
| 18078275789404587 | 2026-10-14 | 5755 | 6992 | 12747 | 53909 |
| 18197180011344666 | 2026-10-11 | 729 | 523 | *unsupported* | 1252 |
| 17872771344639434 | 2026-10-07 | 5634 | 2197 | 7831 | 40727 |
| 18238835560317385 | 2026-10-05 | 1265 | 741 | 2006 | 2818 |

Two identities hold exactly, with no rounding error, on every row:

    crossposted_views == views + facebook_views        (5 of 5 reels)
    total_views       == crossposted_views + residual

The fourth row is the control that makes this interpretable. It is not a
reel — `crossposted_views`, `reels_skip_rate`, `ig_reels_avg_watch_time`
and `ig_reels_video_view_total_time` all returned Meta error 100, *"does not
support … for this media product type"* — and for it `total_views` equals
`views + facebook_views` **exactly**, residual **0**. A zero residual on an
unpromoted publication and a large one on its neighbours is what a
paid-exposure term looks like.

So the decomposition the project actually has is the opposite of the
blueprint's:

    organic_views  = views                     (already organic, no work)
    facebook_views = facebook_views            (a separate surface)
    residual_views = total_views − crossposted_views

and `residual_views` is obtainable **today, from the existing Page token,
with no `ads_read` scope and no Ads Manager grant**.

### The residual is not yet proven to be only paid

`total_views` is documented as "all surfaces", of which promoted media is
one named example rather than the only member. The measured residuals are
also large for the stated spend: 29,274 non-organic non-Facebook views on a
post boosted at the owner's stated $1/day implies a CPM around $0.03, which
is not a credible paid price. Either the residual contains a non-paid
surface, or the spend on these posts was higher than $1/day, or both.

This is recorded as `[OPEN]` and the field is named
**`residual_surface_views`**, not `paid_views`, so that nothing downstream
can quietly read it as spend-driven exposure. Ads Insights is what settles
it: once `ads_read` exists, `ad.insights{impressions}` for the ad whose
`effective_instagram_media_id` matches the media gives the paid figure
directly, and the difference from the residual names whatever else is in it.

## 3.1 The campaign window, measured rather than recalled

The residual was then collected for **all 385** own-account Instagram
publications (`probes/m1_ad_window.py`). This dates the boosting without
anyone having to remember, which matters because the recollection has
already moved: `BLUEPRINT.md` §1 recorded *"a $1/day ad that rotates to
each new post […] for several months"*, and the owner said on 2026-10-26
*"the two $1/day ads have run since a few weeks"*.

**Neither is what the data shows.** Of 385 publications, 373 resolve and
12 refuse (§3.2). Of the 373:

| | count |
|---|---|
| residual exactly 0 | 338 |
| residual > 0 | 35 |
| residual share ≥ 0.25 | 29 |
| residual share ≥ 0.50 | 25 |

The distribution is strongly bimodal — among the non-zero ones the median
share is 0.74 — which is what a boost looks like: a post is either
promoted or it is not, and there is no continuum.

By date, the 29 publications at ≥ 0.25 fall into two groups:

- **Three isolated events** in the preceding eighteen months: 2025-06-17,
  2026-03-07 and 2026-06-30.
- **A continuous run beginning 2026-07-13** and reaching the most recent
  publication: 8 in July, 7 in August, 6 in September, 5 in October.

So the campaign is roughly **three and a half months old**, not "several
months" and not "a few weeks", and it does **not** boost every new post —
40 publications in August yielded 7 boosted. `[PRIMARY, 2026-10-26]`

Two consequences for the science, both material:

1. **The retention corpus is mostly clean.** The 48 publications carrying
   `reels_skip_rate` span 2025-04 to 2026-10, with the great majority
   posted before 2026-07-13. The ad contaminates a minority of that
   sample, not all of it.
2. **`BLUEPRINT.md` §1.2's interpretation is doubtful.** That analysis
   attributed a gap-to-next-post ↔ skip-rate correlation of −0.378 to ad
   exposure. If almost none of those 47 publications was boosted, the gap
   was measuring organic accumulation, which is what the blueprint itself
   warned the proxy could not distinguish. The re-run in
   `docs/benchmarks/6c-visual-density.md` settles it with the measured
   covariate.

## 3.2 Where the residual refuses to resolve, and why that matters

`facebook_views` fails in two different ways and they must not be
conflated:

- **"does not support … for this media product type"** — the publication
  is not on Facebook. 252 of the 264 rows in this state had
  `total_views == views` exactly, confirming there is nothing outside the
  organic count. Residual 0, confidently.
- **`Meta error -1: Fatal`** — a server-side failure, **persistent, not
  transient**: all 12 affected publications were retried four times each
  and failed every time (`probes/m1_retry_fatal.py`). For these the
  Facebook surface is unknown, so a gap between `total_views` and `views`
  cannot be attributed to paid delivery.

This is not a cosmetic distinction. Treating the unknown as zero would
have produced the two largest "paid" figures in the corpus — 1,705,704 and
1,282,565 residual views — out of what may simply be Facebook
cross-recommendation. `_from_surface()` therefore returns `unknown` for
those 12 rather than a number, and they are excluded from the covariate
with a stated reason.

## 3.3 A collection defect this found

The first version of the collector requested all three surface metrics in
one call. Meta fails a combined insights call **entirely** when any single
metric name is unsupported for the media product type, and
`crossposted_views` is unsupported for everything that is not a reel — so
`total_views`, the one metric that actually carries paid exposure, was
lost for every non-reel publication. Measured: 68 of 210 publications got
it, against 385 of 385 for the probe, which had requested each metric
singly.

The collector now falls back to one call per metric when the combined call
fails, records which names were refused in `surface_unavailable`, and
marks the snapshot `partial`. Regression test:
`test_surface_metrics_fall_back_to_one_call_per_metric`.

## 4. Ads Insights reachability — answered, and the answer is no

The `[OPEN]` question in `BLUEPRINT.md` §1.3 was whether the existing token
can reach Ads Insights. Measured on 2026-10-26:

- `GET /me/adaccounts` → HTTP 400, `(#100) Tried accessing nonexisting
  field (adaccounts)`. Expected: the stored credential is a **Page** token
  (ADR 0005), and `adaccounts` is an edge of the User node.
- Token scopes, from `/debug_token`: `pages_show_list`,
  `business_management`, `instagram_basic`, `instagram_manage_insights`,
  `pages_read_engagement`, `public_profile`.

**No `ads_read`, no `ads_management`.** Ads Insights is unreachable without
a fresh interactive authorisation, which is an owner action, not a code
change.

## 5. Decision

1. **Two tracks, and track A does not wait for track B.**
   - **Track A — surface decomposition, available now.** Collect
     `total_views`, `crossposted_views` and `facebook_views` alongside the
     existing metric set and store them. `residual_surface_views` and
     `residual_share` become the ad-exposure covariate, replacing the
     gap-to-next-post proxy. No new permission, no owner action.
   - **Track B — Ads Insights, when `ads_read` is granted.** Join
     `adcreative.effective_instagram_media_id` → our media id, read
     `spend`, `impressions`, `reach`, `video_p25…p100_watched_actions` and
     `video_avg_time_watched_actions` per ad per day into `ad_exposures`.
     The code is written and gated; it stays inert until the scope exists.
2. **Surface metrics are requested in their own call**, as the retention
   metrics already are. `crossposted_views` hard-fails for non-reel media
   (measured above), and one unsupported name must never take down the core
   metric set.
3. **Raw readings are never overwritten.** `metrics_snapshots` keeps the
   platform's numbers as returned; organic and residual figures are stored
   as their own columns, and shares are derived at read time. This preserves
   the append-only guarantee the table was built on.
4. **The field is `residual_surface_views`, never `paid_views`**, until
   track B measures what fraction of it is actually paid.
5. **The outcome family is retention and per-viewer engagement rates, not
   views.** Because `views`, `likes`, `comments`, `saved`, `shares` and
   `total_interactions` are all documented organic-only, every rate built
   from them — saves/view, shares/view, comments/view — is already free of
   paid contamination in both numerator and denominator. Paid exposure
   enters the analysis **only as a covariate**. This is also what CLAUDE.md
   §3.8 asks for and what the owner asked for on 2026-10-26.
6. **The skip-rate question is tested, not assumed.** If `reels_skip_rate`
   were computed over all surfaces, it would move with `residual_share`;
   if it is organic-only, it should not. That association is reported in
   every run of the retention analysis as a standing diagnostic rather than
   settled once. Until it reads clean, `reels_skip_rate` results are
   reported with and without the covariate, and a finding that does not
   survive is reported as not having survived.

## 6. Consequences

- The gap-to-next-post proxy used in `BLUEPRINT.md` §1.2 is superseded for
  every publication where the surface metrics can be collected. It remains
  the fallback for publications where they cannot, and the covariate records
  which source it came from so the two are never silently mixed.
- `BLUEPRINT.md` §1.3's subtraction formula is **withdrawn**. The blueprint
  must be corrected rather than left to be re-read as a plan.
- Track B requires the owner to re-authorise with `ads_read`. Until then
  every paid figure in the system is a residual, labelled as one.

## 7. Review trigger

`reels_skip_rate` is documented as *estimated and in development*. A metric
in development can change definition or disappear, and this project's
headline finding rests on it. Re-read its reference entry before any
analysis that depends on it is published, and record the date here.

Checked: 2026-10-26.
