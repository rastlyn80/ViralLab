# Cross-platform matching: TikTok ↔ Instagram

2026-10-24. The owner cross-posts **~99%** of videos, so this is the normal
workflow, not an edge case. What makes it hard is that the two sides are not
symmetric.

| | Instagram | TikTok |
|---|---|---|
| publications | 289 | 234 |
| media file available | **258** | **0** |
| audio fingerprint possible | yes | **no** |
| frames available | yes | **no** |
| cover image | yes (`thumbnail_url`) | yes (`cover_image_url`) |
| duration | 258 (backfilled from media) | **234** |
| posted-at timestamp | 289 | 234 |
| caption | 288 | 234 |

`[PRIMARY]` TikTok's API returns no downloadable media (ADR 0001, confirmed
empirically — the field set logged from the live endpoint contains no media
URL). So the content-level evidence that makes Instagram matching
trustworthy simply does not exist on the TikTok side.

## The cover image was implemented, measured, and rejected

The cover was the obvious candidate for a visual signal, and the owner asked
for it to be evaluated. It was implemented fully rather than argued about:

- `videos.cover_url` / `cover_hash` / `cover_fetched_at` added (migration
  `e4b8f21c6d35`). The URL expires; the dHash does not, so the hash is what
  is relied on.
- All **234/234** TikTok covers fetched and hashed, **0 failures**. dHash
  computed with ffmpeg, so the server needed no imaging library.

Then measured against the 251 local masters' frame hashes:

| | result |
|---|---|
| best cover-vs-master Hamming distance | min 5, **median 16**, max 21 (of 64 bits) |
| masters within Hamming 12 of a cover | median **0**, max 48 |
| covers with a match both close (≤8) and clearly ahead of the runner-up (≥6 better) | **1 of 234** |

The obvious hypothesis was that a 12-frame fingerprint never samples the
frame the cover came from. Re-sampled the duration-matching masters at
**2 fps** for 14 covers:

| | result |
|---|---|
| dense sampling improved the best distance | **3 of 14** |
| now close *and* distinctive | **1 of 14** |

**Conclusion: the TikTok cover is not a frame of the video as we hold it** —
it behaves like a platform-composited thumbnail. It is **not used as a
matching signal.** The hashes are kept (they cost nothing and the URLs are
gone otherwise) and the cover is shown in the review UI for orientation,
labelled explicitly as not evidence.

This is a negative result, and it is the honest answer to "combine TikTok
cover images with Instagram frames": that combination was tried and does not
work.

## What is left: metadata, and structure

Three signals survive, each scored and reported **separately** so a proposal
reads as "these things agreed" rather than as one opaque number:

| signal | weight | why it is weak alone |
|---|---|---|
| **duration** | 0.45 | thousands of piano videos share a duration. TikTok reports whole seconds while Instagram's comes from the media file, so ≤1 s of difference is rounding, not evidence |
| **publication date** | 0.35 | the owner posts near-daily, so "within a day" barely discriminates |
| **caption / hashtags** | 0.20 | rewritten per platform by design, so low overlap is *not* evidence against |

Caption overlap was checked for boilerplate before being trusted, because a
signal that fires on everything carries no information. Over 4,000 random
cross-platform pairs — i.e. mostly wrong pairs — hashtag Jaccard had **median
0.000**, p90 0.167, and reached 1.0 in **0.2%** of pairs; token Jaccard
median 0.000, p90 0.059, 1.0 in 0.1%. So the hashtag sets are genuinely
distinctive and a high overlap really is strong evidence.

### Structure does the work a single signal cannot

Because almost everything is cross-posted, the true answer is close to a
**one-to-one pairing**. A pair is proposed only when each side is the other's
best candidate (**mutual best**). A one-sided preference usually means two
publications competing for the same partner — exactly where a greedy pick
goes wrong — and those are reported separately rather than resolved
arbitrarily.

The date band is deliberately tight for the same reason: with near-daily
posting, treating ±1 day as a perfect match made yesterday's post nearly as
good a candidate as today's and collapsed the margin over the runner-up.
Same-day scores 1.0, one day 0.75, two days 0.55.

## Results

| | count |
|---|---|
| **Proposed pairs** | **201** |
| of which `strong` (3 signals agree, score ≥ 0.85) | **136** |
| of which `moderate` (2 agree, ≥ 0.70) | 63 |
| of which `weak` | 2 |
| Not mutual best (two posts wanted the same partner) | 24 |
| Below the evidence floor (score < 0.55 or < 2 agreeing signals) | 9 |
| TikTok publications with no proposal | **33 of 234** |
| Instagram publications with no proposal | 88 of 289 |

Margin over the runner-up: min 0.00, **median 0.35**, max 1.00. **6
proposals** beat their runner-up by less than 0.10 and are flagged in the
review UI as the ones most worth a human eye.

## Nothing here is confirmed

`publication_links` has **no `auto` status**. The only statuses are
`proposed`, `confirmed` and `rejected`, and only a human sets the latter two.

That is a structural decision, not a cautious default. For an Instagram
publication there is a local file to fall back on as proof; for a TikTok pair
there is nothing but metadata, and metadata agreement cannot establish that
two publications are the same video. Every stored proposal carries its
per-signal evidence and an explicit note that the cover signal is
unavailable and why.

`[OPEN]` What would settle a pair properly: the owner's own editing project
files or export logs, or publishing one test video and observing which fields
the API returns for it. Neither is needed for Phase 2 to close — a `proposed`
pair is already enough to keep both platforms' metrics side by side without
asserting they describe the same content.

## Relationship model

Three layers, deliberately distinct (`server/db/models.py`):

1. **Performance** — one reading of one work. 156 inferred.
2. **Variant** (`local_masters`) — one export of a performance, with its own
   hook, overlays and trim. 250 masters, 63 performances with more than one.
3. **Publication** (`videos`) — one variant posted to one platform at one
   time, with its own independent metrics.

Collapsing 2 into 3 would make a TikTok/Instagram pair look like two
different videos. Collapsing 1 into 2 would make a re-edit look like a
different performance. Both errors are the kind this project exists to avoid,
and the schema now makes them unrepresentable rather than merely discouraged.

Supported, without rewriting the existing tables:

| relationship | how |
|---|---|
| one performance → many variants | `local_masters.performance_id` |
| one variant → many platforms | `video_master_links`, many-to-many |
| reposts | same master → several publications on one platform (5 observed) |
| platform-specific edits | separate masters sharing a `performance_id`, or `publication_links.relationship_type = 'platform_edit'` |
| multiple source files per performance | the performance group itself |
| a pair with no local file at all | `publication_links`, which needs no master |
