"""Response contracts for competitor research.

Same two conventions as `shared/schemas/ui.py`, and one more that matters
more here than anywhere else:

**Every figure says where it came from and what it is missing.** A
researched creator's numbers come from a third-party provider with known
blind spots - the validated Instagram actors report no follower count at
all - and a research result that looks like an own-account result would be
actively misleading. So `is_fixture`, `provider`, `observed_at` and
`caveats` travel with the data, and absent values are null with a reason.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class SpendingState(BaseModel):
    """Whether research may cost money, and how much is left."""

    #: True when nothing may be spent at all. The interface shows this
    #: instead of offering a control that cannot work.
    spending_blocked: bool
    monthly_cap_usd: float
    committed_usd: float
    available_usd: float
    providers_authorised: list[str] = Field(default_factory=list)
    period: str
    #: Which adapter will serve a request right now.
    active_provider: str
    #: True when results come from committed synthetic fixtures.
    fixture_mode: bool
    #: Fixtures that can be demonstrated, so the UI offers real options
    #: rather than inviting a search that will fail.
    available_fixtures: list[dict] = Field(default_factory=list)


class ProviderCapabilityView(BaseModel):
    platform: str
    provider: str
    supported_queries: list[str] = Field(default_factory=list)
    follower_count: bool
    creator_bio: bool
    view_count: bool
    views_rounded: bool
    saves_count: bool
    media_url: bool
    full_history: bool
    missing_creator_fields: list[str] = Field(default_factory=list)
    coverage_notes: list[str] = Field(default_factory=list)


class ResearchRequest(BaseModel):
    platform: str
    #: A username or a profile URL; normalised server-side.
    handle: str
    limit: int = 100
    #: Re-collect even if existing research is fresh. Costs money when a
    #: paid provider is active, which is why it is explicit.
    refresh: bool = False


class ResearchJobView(BaseModel):
    id: int
    kind: str
    platform: str
    query_value: str
    provider: str
    is_fixture: bool
    #: queued | running | ok | failed | refused. `refused` is not a failure:
    #: it is the budget guard working as configured.
    status: str
    started_at: datetime
    finished_at: datetime | None = None
    estimated_usd: float | None = None
    actual_usd: float | None = None
    creators_new: int = 0
    publications_seen: int = 0
    publications_new: int = 0
    snapshots_written: int = 0
    coverage_notes: list[str] = Field(default_factory=list)
    item_errors: list[dict] = Field(default_factory=list)
    error: str | None = None


class ResearchResultView(BaseModel):
    status: str
    message: str | None = None
    creator_id: int | None = None
    job: ResearchJobView | None = None
    reused: bool = False
    is_fixture: bool = False
    breakthroughs_found: int = 0


class ResearchedCreatorRow(BaseModel):
    """One researched account, for the research-history list."""

    creator_id: int
    platform: str
    handle: str
    display_name: str | None = None
    #: Null where the source does not report it - the normal case on
    #: Instagram - never 0.
    follower_count: int | None = None
    publications_collected: int = 0
    research_status: str
    last_refreshed_at: datetime | None = None
    #: True when the data is older than the freshness window, so the UI can
    #: say "stale" rather than implying it is current.
    is_stale: bool = False
    #: What a refresh would cost right now. 0.0 in fixture mode.
    refresh_cost_usd: float | None = None
    is_fixture: bool = False
    source: str
    breakthrough_count: int = 0
    collection_cost_usd: float | None = None


class PerformanceSummary(BaseModel):
    """Typical performance, with the caveats as part of the payload.

    Not called "average": these are medians over single observations taken
    at one moment across publications of very different ages, and the
    caveats explain exactly what that does and does not support.
    """

    publications: int
    with_views: int
    first_published: datetime | None = None
    last_published: datetime | None = None
    observed_at: datetime | None = None
    median_views: float | None = None
    median_views_recent: float | None = None
    top_views: int | None = None
    median_likes: float | None = None
    age_span_days: float | None = None
    counts_rounded: bool = False
    caveats: list[str] = Field(default_factory=list)


class ResearchedCreatorDetail(BaseModel):
    creator_id: int
    platform: str
    handle: str
    display_name: str | None = None
    profile_url: str | None = None
    bio: str | None = None
    follower_count: int | None = None
    #: Repeated account-level observations. Length <= 1 means there is no
    #: follower history, and therefore no growth claim is possible.
    follower_observations: list[dict] = Field(default_factory=list)
    research_status: str
    last_refreshed_at: datetime | None = None
    is_stale: bool = False
    is_fixture: bool = False
    source: str
    discovered_via: str | None = None
    refresh_cost_usd: float | None = None
    summary: PerformanceSummary
    capabilities: ProviderCapabilityView | None = None
    last_job: ResearchJobView | None = None


class ResearchPublicationRow(BaseModel):
    video_id: int
    external_id: str
    posted_at: datetime | None = None
    caption: str | None = None
    hashtags: list[str] = Field(default_factory=list)
    duration_s: float | None = None
    url: str | None = None
    thumbnail_url: str | None = None
    views: int | None = None
    likes: int | None = None
    comments: int | None = None
    shares: int | None = None
    #: Null on sources that do not report saves - both Instagram actors.
    saves: int | None = None
    counts_rounded: bool = False
    observed_at: datetime | None = None
    #: Days between publication and the observation. Without it a view
    #: count is not comparable to any other.
    age_days: int | None = None
    #: views / the creator's own local baseline. The only honest ranking.
    relative_performance: float | None = None
    robust_z: float | None = None
    music_title: str | None = None
    music_artist: str | None = None
    breakthrough: str | None = None
    is_fixture: bool = False


class ResearchPublicationPage(BaseModel):
    total: int
    offset: int
    limit: int
    items: list[ResearchPublicationRow] = Field(default_factory=list)
    #: Why a relative-performance column may be empty for early rows.
    notes: list[str] = Field(default_factory=list)


class BreakthroughView(BaseModel):
    id: int
    #: observed_outlier | inferred_breakthrough |
    #: verified_trajectory_change | rejected. These are different claims,
    #: not different wordings.
    classification: str
    video_id: int | None = None
    external_id: str | None = None
    posted_at: datetime | None = None
    caption: str | None = None
    publication_index: int | None = None
    publications_total: int | None = None
    observed_views: int | None = None
    baseline_views: float | None = None
    median_after: float | None = None
    relative_performance: float | None = None
    robust_z: float | None = None
    step_ratio: float | None = None
    sustained: bool | None = None
    confidence: float | None = None
    evidence: dict = Field(default_factory=dict)
    #: Required for an inferred breakthrough: exactly which evidence was
    #: absent, so nobody later reads it as established.
    missing_evidence: list[str] = Field(default_factory=list)
    decided_by: str | None = None
    method_version: str


class BreakthroughReportView(BaseModel):
    creator_id: int
    method_version: str
    publications_considered: int
    breakthrough: BreakthroughView | None = None
    outliers: list[BreakthroughView] = Field(default_factory=list)
    #: Why nothing was reported, when nothing was. An empty result with no
    #: explanation is indistinguishable from a bug.
    refusals: list[str] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)
    has_follower_history: bool = False
    has_repeated_observations: bool = False
