# ADR 0001 — API capability verification record

**Date:** 2026-10-23
**Status:** Accepted
**Context:** Required by `CLAUDE.md` §2 rules 1–2, which mandate that every
API capability claim be verified against platform-official documentation and
that the verification be recorded with its source URL, check date, and API
version.

## Why this record exists

Revision 1 of `DEVELOPMENT_PLAN.md` carried three incorrect API claims taken
from third-party blog posts. All three were wrong in ways that would have
produced real rework after implementation had begun. This ADR is the
standing record of what has since been verified against primary sources, so
that future sessions can tell verified capability from inherited assumption
without re-litigating it.

## Verified — primary sources, checked 2026-10-23

| Claim | API / version | Source |
|---|---|---|
| `plays`, `clips_replays_count`, `ig_reels_aggregated_all_plays_count`, `impressions` deprecated 21 Apr 2025; replaced by unified `views` | Instagram, Graph API v22.0 | [v22.0 changelog](https://developers.facebook.com/docs/graph-api/changelog/version22.0/) |
| `impressions` deprecated **only for media created on/after 2 Jul 2024**; older media still returns it | Instagram, all versions | same |
| Media insights metric set: `views`, `reach`, `saved`, `shares` (+ likes, comments) | Instagram Platform | [IG Media Insights reference](https://developers.facebook.com/documentation/instagram-platform/reference/instagram-media/insights) |
| Insights data retained **~2 years**; values delayed **up to 48 h** | Instagram Platform | same |
| Two non-equivalent API paths; **Hashtag Search and Business Discovery are Facebook-Login-only**; Professional account mandatory | Instagram Platform | [Instagram Platform overview](https://developers.facebook.com/docs/instagram-platform/overview/) |
| Display API Video Object fields are `id`, `create_time`, `cover_image_url`, `share_url`, `video_description`, `duration`, `height`, `width`, `title`, `embed_html`, `embed_link`, `like_count`, `comment_count`, `share_count`, `view_count`, `is_aigc` — **no media URL, no `collect_count`** | TikTok Display API v2 | [Video Object](https://developers.tiktok.com/docs/en/tiktok-api-v2-video-object), [List Videos](https://developers.tiktok.com/docs/en/tiktok-api-v1-video-list) |
| Research API requires academic/non-profit affiliation, independence from commercial interests, non-commercial basis, funding disclosure, research proposal, and **evidence of ethical review** | TikTok Research API | [Access and Eligibility](https://developers.tiktok.com/products/research-api/) |
| Free plan: $0/month, no credit card, $5 Store credit, expires monthly, not toppable on free tier | Apify platform | [Apify pricing](https://apify.com/pricing) |

## Decisions that follow

1. **Instagram: use the Facebook Login path**, accepting the Facebook Page
   requirement, because Hashtag Search and Business Discovery exist only
   there and are the project's sole official foothold beyond its own
   account.
2. **Own TikTok media comes from the local master archive**, never from the
   API and never by scraping the owner's own published content back off the
   platform.
3. **Instagram media must be downloaded on collection**, because `media_url`
   is a short-lived signed CDN link.
4. **Own-account backfill is capped at ~2 years** by Instagram's retention
   window. The window is rolling, which is an argument for starting
   collection sooner.
5. **`impressions` is historical context only**, never a model feature,
   since it is absent for all media created after 2 Jul 2024.
6. **The TikTok Research API is permanently out of scope.**

## Still unverified (explicitly [SECONDARY])

These remain provisional and must not be cited as fact:

- TikTok Display API rate limit (~600 req/min/endpoint) and Instagram's
  200 calls/hour — blog-sourced; confirm against official rate-limit docs in
  Phase 2.
- Instagram `media_url` TTL (~24 h, one source says ~105 h) — not documented
  in Meta's primary reference. **Measure empirically in Phase 2** rather than
  trusting any quoted figure.
- Qwen2.5-VL VRAM figures at FP16/INT8/INT4 — third-party estimates;
  Phase 0B measures them directly.
- `faster-whisper` real-time factors — no published RTX 2070 benchmark
  exists at all.
- Apify per-result pricing bands — vendor marketing pages; Phase 0A will
  produce a measured figure from an actual billed run.
- Both web-scraping case summaries (*hiQ v. LinkedIn*, *Meta v. Bright
  Data*) — case summaries, not dockets. Adequate for internal policy, not
  legal advice.

## Review trigger

Re-verify any row above before writing or modifying code that depends on it,
and whenever an integration has not been checked in several months. Platform
APIs deprecate metrics and change auth paths on months-long cycles — three
of the corrections above were less than two years old.
