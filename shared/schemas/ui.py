"""Response contracts for the web UI.

These models are the single source of truth for the API/frontend boundary
(ADR 0006). FastAPI publishes them as OpenAPI, and `scripts/gen_ui_types.py`
turns that into `webui/types.js` JSDoc typedefs, so the frontend is typed
against the real contract rather than a hand-maintained copy of it.

Two conventions run through all of them, both from CLAUDE.md §3:

  * **Unknown is not zero.** Optional numeric fields are `None` when the
    platform does not report them. TikTok has no `saves`, so the field is
    null rather than 0, and the UI renders a dash rather than a number.
  * **A metric without an anchor age is meaningless.** Every performance
    figure travels with the age at which it was observed, so the interface
    cannot accidentally present two differently-aged numbers as comparable.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class PlatformSummary(BaseModel):
    platform: str
    account_ref: str | None = None
    handle: str | None = None
    #: False means collection is paused until a human re-authorises.
    connected: bool
    needs_reauth: bool
    #: None for the Instagram Page token, which does not expire (ADR 0005).
    token_expires_at: datetime | None = None
    publications: int
    snapshots: int
    first_published: datetime | None = None
    last_published: datetime | None = None


class CollectionRunSummary(BaseModel):
    platform: str
    kind: str
    status: str
    started_at: datetime | None = None
    finished_at: datetime | None = None
    videos_seen: int | None = None
    videos_new: int | None = None
    snapshots_written: int | None = None
    error: str | None = None


class MediaCoverage(BaseModel):
    platform: str
    total: int
    #: False when the platform's API returns no media file at all, as
    #: TikTok's does. Without this, TikTok renders as "0% preserved", which
    #: reads as a failure on our side rather than a platform limitation -
    #: the same unknown-vs-zero error the rest of this module avoids.
    media_obtainable: bool = True
    #: Excludes publications that are not video at all, because counting an
    #: image post as missing video overstates the gap.
    preservable: int
    preserved: int
    coverage_pct: float | None = None
    by_status: dict[str, int] = Field(default_factory=dict)
    by_reason_class: dict[str, int] = Field(default_factory=dict)


class ReviewCounts(BaseModel):
    master_links_pending: int
    master_links_auto_unverified: int
    master_links_human_decided: int
    publication_links_proposed: int
    publication_links_confirmed: int
    performances_inferred: int
    performances_confirmed: int


class Overview(BaseModel):
    generated_at: datetime
    platforms: list[PlatformSummary]
    recent_runs: list[CollectionRunSummary]
    media: list[MediaCoverage]
    review: ReviewCounts
    local_masters: int
    performances: int
    #: Explicit, because the whole corpus currently shares one capture day
    #: and that fact governs what Phase 3 analysis is possible at all.
    snapshot_days: int
    earliest_snapshot: datetime | None = None
    latest_snapshot: datetime | None = None


class MetricReading(BaseModel):
    """One observation, with the age that makes it interpretable."""

    captured_at: datetime
    #: Days between publication and this reading. None when the publication
    #: time is unknown, which makes the reading uncomparable by design.
    age_days: int | None = None
    views: int | None = None
    likes: int | None = None
    comments: int | None = None
    shares: int | None = None
    #: Null on TikTok: the platform exposes no saves field at all.
    saves: int | None = None
    #: True when the source rounded the figure, e.g. "54.9M".
    is_rounded: bool = False
    #: Instagram retention, milliseconds. Absent elsewhere.
    avg_watch_time_ms: int | None = None
    total_view_time_ms: int | None = None
    #: Percentage of views that skipped in the first 3 seconds.
    skip_rate_pct: float | None = None


class VideoListItem(BaseModel):
    id: int
    platform: str
    external_id: str
    #: The public URL of the post, so a figure can be checked against
    #: the thing it describes. Null where none is known - Instagram
    #: permalinks carry a shortcode that cannot be computed from the
    #: media id, so one that was never collected cannot be invented.
    permalink: str | None = None
    caption_preview: str | None = None
    posted_at: datetime | None = None
    duration_s: float | None = None
    #: Latest reading only. The age is included so the list cannot be sorted
    #: by views and read as a quality ranking without the caveat being
    #: visible.
    latest_views: int | None = None
    latest_age_days: int | None = None
    has_media: bool
    media_status: str
    #: Number of local masters linked, and whether any is human-confirmed.
    master_links: int
    master_link_verified: bool
    cross_platform_links: int
    performance: str | None = None


class VideoPage(BaseModel):
    total: int
    offset: int
    limit: int
    items: list[VideoListItem]


class MasterLinkView(BaseModel):
    id: int
    master_id: int
    filename: str
    master_duration_s: float | None = None
    performance: str | None = None
    score: float | None = None
    frame_overlap: float | None = None
    audio_similarity: float | None = None
    #: spectral | envelope | none. Shown because an envelope-era score is
    #: materially weaker evidence than a spectral one.
    audio_method: str | None = None
    audio_offset_s: float | None = None
    duration_delta_s: float | None = None
    runner_up: float | None = None
    status: str
    decided_by: str | None = None
    decided_at: datetime | None = None
    evidence: dict | None = None
    notes: str | None = None


class PublicationLinkView(BaseModel):
    id: int
    other_video_id: int
    other_platform: str
    other_external_id: str
    other_posted_at: datetime | None = None
    other_duration_s: float | None = None
    other_latest_views: int | None = None
    other_latest_age_days: int | None = None
    relationship_type: str
    status: str
    score: float | None = None
    decided_by: str | None = None
    evidence: dict | None = None


class PairSide(BaseModel):
    video_id: int
    platform: str
    external_id: str
    #: The public URL of the post, so a figure can be checked against
    #: the thing it describes. Null where none is known - Instagram
    #: permalinks carry a shortcode that cannot be computed from the
    #: media id, so one that was never collected cannot be invented.
    permalink: str | None = None
    posted_at: datetime | None = None
    duration_s: float | None = None
    caption: str | None = None
    media_url: str | None = None
    cover_url: str | None = None
    latest_views: int | None = None
    latest_age_days: int | None = None
    #: The local master this side is linked to, when one is established.
    master_filename: str | None = None
    performance: str | None = None


class PublicationPairView(BaseModel):
    """A cross-platform pair as the review queue needs it: both sides in
    full, so the decision can be made without a second request."""

    id: int
    relationship_type: str
    status: str
    score: float | None = None
    decided_by: str | None = None
    evidence: dict | None = None
    a: PairSide
    b: PairSide


class MasterReviewItem(BaseModel):
    """A pending master link with everything needed to decide it."""

    link: "MasterLinkView"
    video: "VideoListItem"
    #: Signed, short-lived. None when no media is stored for the publication.
    video_media_url: str | None = None
    video_caption: str | None = None
    #: Absolute path on the OWNER's machine. Sent so a local bridge can play
    #: it; the server itself cannot, and never serves it.
    master_local_path: str | None = None


class VideoDetail(BaseModel):
    id: int
    platform: str
    external_id: str
    #: The public URL of the post, so a figure can be checked against
    #: the thing it describes. Null where none is known - Instagram
    #: permalinks carry a shortcode that cannot be computed from the
    #: media id, so one that was never collected cannot be invented.
    permalink: str | None = None
    caption: str | None = None
    hashtags: list[str] = Field(default_factory=list)
    posted_at: datetime | None = None
    duration_s: float | None = None
    #: official_api | provider | local_master — provenance, never inferred.
    source: str
    media_status: str
    media_reason: str | None = None
    #: Short-lived signed URL, or None when no media is stored. A filesystem
    #: path is never sent to the browser.
    media_url: str | None = None
    cover_url: str | None = None
    creator_handle: str | None = None
    is_own_account: bool
    history: list[MetricReading]
    master_links: list[MasterLinkView]
    publication_links: list[PublicationLinkView]
    #: Why a fixed-age anchor is or is not available for this publication.
    anchors: dict[str, str | int | None] = Field(default_factory=dict)


class DecisionRequest(BaseModel):
    #: confirmed | rejected | variant | platform_edit | skip
    answer: str
    notes: str | None = None


class DecisionResult(BaseModel):
    id: int
    status: str
    decided_by: str | None = None
    decided_at: datetime | None = None
    remaining: int


class CapabilityState(BaseModel):
    """Honest state of a product capability.

    The UI renders sections for capabilities that do not exist yet, because
    the navigation shape is part of the architecture. This model is what
    stops those sections from pretending: `implemented` is false, `blocked_by`
    says what is actually missing, and no fabricated rows are ever returned.
    """

    key: str
    title: str
    summary: str
    implemented: bool
    phase: str
    blocked_by: list[str] = Field(default_factory=list)
    #: Real counts where any exist, so an empty section is visibly empty
    #: rather than ambiguous.
    counts: dict[str, int] = Field(default_factory=dict)


class CapabilityList(BaseModel):
    capabilities: list[CapabilityState]


class SessionRequest(BaseModel):
    password: str


class SessionState(BaseModel):
    authenticated: bool
    #: Present only when a UI password is configured at all.
    auth_configured: bool
