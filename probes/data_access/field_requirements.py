"""Canonical field requirements for the Phase 0A provider scorecard.

This file is the single source of truth for what ViralLab needs from a
third-party niche-discovery provider. It is referenced by the Phase 0A gate
in DEVELOPMENT_PLAN.md section 7.

Criticality levels:
  BLOCKING  - the gate fails unless presence is 100%. Without these, the data
              cannot be used for labeling or modeling at any price.
  IMPORTANT - needed for the designed Tier 1 feature set. Counted toward the
              >=80% weighted completeness threshold.
  NICE      - degrades gracefully if absent. Reported, not gated.
  CAPABILITY- not a Tier 1 feature; determines whether Tier 2/3 multimodal
              analysis is possible at all for third-party videos. Reported
              separately and never folded into the completeness score.

Provider field names vary wildly, so each spec carries aliases. Add aliases
rather than renaming provider output, so the raw sample stays auditable.
"""

from dataclasses import dataclass, field
from typing import Callable, Any

BLOCKING = "BLOCKING"
IMPORTANT = "IMPORTANT"
NICE = "NICE"
CAPABILITY = "CAPABILITY"


def _nonempty_str(v: Any) -> bool:
    return isinstance(v, str) and v.strip() != ""


def _nonneg_number(v: Any) -> bool:
    # Providers signal "unknown" with -1, None or "" far more often than they document it.
    return isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 0


def _positive_number(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0


def _timestamp_like(v: Any) -> bool:
    # Accept epoch seconds, epoch ms, or an ISO-8601 string; plausibility
    # (past, varied, carries time-of-day) is checked separately by the scorer.
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return v > 1_000_000_000  # ~2001 onward, in seconds or ms
    return _nonempty_str(v)


@dataclass
class FieldSpec:
    name: str
    criticality: str
    why: str
    aliases: list[str] = field(default_factory=list)
    valid: Callable[[Any], bool] = _nonempty_str


REQUIREMENTS: list[FieldSpec] = [
    # ---- BLOCKING -----------------------------------------------------------
    FieldSpec(
        "video_id", BLOCKING,
        "Stable identity; without it, re-query and de-duplication are impossible.",
        ["id", "aweme_id", "awemeId", "videoId", "post_id", "shortcode", "pk"],
    ),
    FieldSpec(
        "creator_id", BLOCKING,
        "Per-creator baselines are the core of the relative-performance target.",
        ["author_id", "authorId", "author_unique_id", "uniqueId", "username",
         "ownerUsername", "author", "handle",
         # Apify's TikTok actors nest the author; dotted aliases reach it.
         "authorMeta.name", "authorMeta.id", "authorMeta.uniqueId",
         "author.uniqueId", "author.id", "owner.username",
         "channel.id", "channel.username", "channel.name"],
    ),
    FieldSpec(
        "posted_at", BLOCKING,
        "Absolute post time. Without it video age is unknown and fixed-age "
        "comparison (plan section 5.4) is impossible - the data is unusable.",
        ["create_time", "createTime", "createTimeISO", "taken_at", "timestamp",
         "created_at", "publish_time",
         "uploadedAtFormatted", "uploadedAt"],
        _timestamp_like,
    ),
    FieldSpec(
        "views", BLOCKING,
        "Primary performance metric; the modeling target derives from it.",
        ["play_count", "playCount", "view_count", "viewCount", "video_view_count",
         "plays", "video_play_count",
         # Instagram scrapers report the displayed play count; per Meta's
         # Apr-2025 change the platform now calls this metric "views".
         "videoPlayCount", "igPlayCount"],
        _nonneg_number,
    ),
    FieldSpec(
        "duration_s", BLOCKING,
        "Needed for normalisation and for Tier 2 cost estimation.",
        ["duration", "video_duration", "videoDuration", "length",
         "video.duration", "videoMeta.duration"],
        _positive_number,
    ),

    # ---- IMPORTANT ----------------------------------------------------------
    FieldSpec(
        "caption", IMPORTANT,
        "Text features and embeddings; hashtags are derived from it.",
        ["desc", "description", "text", "title", "caption_text"],
    ),
    FieldSpec(
        "likes", IMPORTANT, "Engagement-rate features.",
        ["digg_count", "diggCount", "like_count", "likeCount", "likesCount"],
        _nonneg_number,
    ),
    FieldSpec(
        "comments", IMPORTANT, "Engagement-rate features.",
        ["comment_count", "commentCount", "commentsCount"],
        _nonneg_number,
    ),
    FieldSpec(
        "shares", IMPORTANT, "Engagement-rate features; strong virality signal.",
        ["share_count", "shareCount", "sharesCount", "reshare_count"],
        _nonneg_number,
    ),
    FieldSpec(
        "follower_count", IMPORTANT,
        "Baseline normalisation; the dominant confound if missing.",
        ["author_follower_count", "authorMeta.fans", "authorMeta.followers",
         "author.followerCount", "channel.followers", "fans", "followers",
         "followerCount", "followersCount", "edge_followed_by"],
        _nonneg_number,
    ),
    FieldSpec(
        "cover_image_url", IMPORTANT,
        "Cheap Tier 1 visual features without touching the GPU.",
        ["cover", "coverUrl", "thumbnail", "thumbnail_url", "display_url",
         "originCover", "dynamicCover", "covers.default", "video.cover",
         "video.thumbnail", "videoMeta.coverUrl",
         "videoMeta.originalCoverUrl", "displayUrl"],
    ),

    # ---- NICE ---------------------------------------------------------------
    FieldSpec(
        "saves", NICE,
        "Strong intent signal. Absent from TikTok's own Display API, so "
        "expected to be missing from many providers too.",
        ["collect_count", "collectCount", "save_count", "saved",
         "bookmarks", "bookmarkCount"],
        _nonneg_number,
    ),
    FieldSpec(
        "music_id", NICE, "Trending-audio detection (post-MVP Phase 10).",
        ["music.id", "musicId", "audio_id", "music_meta.musicId", "song_id",
         "musicMeta.musicId", "musicMeta.musicName", "music.title",
         "song.id", "song.title"],
    ),
    FieldSpec(
        "hashtags", NICE, "Derivable from caption, so not blocking.",
        ["challenges", "hashtag_list", "tags", "textExtra"],
        lambda v: isinstance(v, (list, tuple)) and len(v) > 0 or _nonempty_str(v),
    ),
    FieldSpec(
        "region", NICE, "Geographic context; sparse in practice.",
        ["region_code", "locationCreated", "country", "location"],
    ),

    # ---- CAPABILITY (reported, never scored) --------------------------------
    FieldSpec(
        "media_url", CAPABILITY,
        "Determines whether Tier 2/3 analysis of third-party videos is "
        "possible at all. TikTok's official API returns none (plan 2.1), so "
        "this is the decisive question for niche multimodal coverage.",
        ["download_addr", "downloadAddr", "play_addr", "playAddr", "video_url",
         "videoUrl", "mediaUrl", "video.downloadAddr", "video.playAddr",
         "mediaUrls", "videoUrlNoWaterMark", "video.url"],
    ),
]

WEIGHTS = {BLOCKING: 1.0, IMPORTANT: 1.0, NICE: 0.0, CAPABILITY: 0.0}
