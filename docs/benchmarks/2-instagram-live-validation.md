# Instagram live-collection validation

Authorized and backfilled 2026-10-24 on the authoritative Ubuntu server.

## Authorization

Facebook Login path (ADR 0001). The chain ran automatically from one
short-lived token: short-lived User token → long-lived User token (60 days) →
**Page token, which does not expire** (ADR 0005).

Four attempts were needed, and the failures were informative rather than
wasted:

| Attempt | Outcome | Cause |
|---|---|---|
| 1 | 0 Pages | Token generated from the owner's *personal* account, which holds no Pages |
| 2 | 0 Pages | Same account; `business_management` not granted |
| 3 | 1 Page, 0 Instagram links | Correct account, but only `ViralityLab` visible — the real Pages sit in a Business portfolio that `/me/accounts` does not enumerate |
| 4 | **2 Pages, both linked** | `business_management` granted; portfolio Pages became visible |

**The lesson, recorded for the next time:** on the Facebook Login path, Pages
owned by a Business portfolio require `business_management`. Without it
`/me/accounts` returns an empty list and **no error**, which looks exactly
like "you have no Pages". `/me/businesses` returns `(#100) Missing
Permission`, and that is the only signal distinguishing the two cases.

`@gabriele_baldocci` was selected over `@clips_and_beads`, which is a
separate business account on the same portfolio.

## Backfill

| | |
|---|---|
| Media listed | **289** (286 of them REELS) |
| New rows | 289 |
| Snapshots written | **289** |
| Date range | 2023-09-29 → 2026-10-22 |
| Total views | **2,393,227** |
| Best single video | 81,546 views |

## Metric completeness — all 289 populated

| metric | populated |
|---|---|
| `views` | 289 / 289 |
| `likes` | 289 / 289 |
| `comments` | 289 / 289 |
| `shares` | 289 / 289 |
| **`saves`** | **289 / 289** |

Two things worth noting. **Instagram exposes `saves`, which TikTok's Display
API does not** — so the two platforms are not metric-equivalent, and any
cross-platform comparison must account for it rather than treating a NULL as
a zero.

And `likes`/`comments` are populated **because of the review fix**: they are
valid reel insight metrics that the collector mapped from the response but
never requested, so before that fix all 289 would have been NULL.

## Integrity

| check | result |
|---|---|
| duplicate `external_id` | **0** |
| orphan videos (no creator) | **0** |
| NULL `posted_at` | **0** |
| `likes > views` | **0** |
| `posted_at` in the future | **0** |

## API limitations recorded

| Limitation | Effect |
|---|---|
| `media_url` is a signed CDN link that **expires** (~24 h) | Media must be downloaded on collection; a stored URL is useless later |
| `duration` is not on the media edge | `duration_s` is NULL for all Instagram rows; it must come from the media file or a separate call |
| Insights retained ~2 years `[PRIMARY, ADR 0001]` | Anything older has metadata but no recoverable metrics — a rolling window that keeps closing |
| 200 calls/hour per IG Business account | 289 media + 289 insight calls is close to the ceiling for a single pass |
| Watch-time metrics exist but are not yet collected | `ig_reels_avg_watch_time`, `ig_reels_video_view_total_time`, `reels_skip_rate` are available for own reels — a Phase 3 opportunity, deliberately not taken here |

## Corpus after this step

| | TikTok | Instagram | Total |
|---|---|---|---|
| videos | 234 | 289 | **523** |
| snapshots | 702 | 289 | 991 |
| earliest post | 2023-02-04 | 2023-09-29 | |

Both platforms now collect daily from the same scheduler on the always-on
server, each tracked independently so one platform's success cannot suppress
the other's pass.
