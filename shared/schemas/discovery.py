"""Typed response models for discovery, timeline, report and collections.

Separate from `research.py` because these are the Phase 4.5 surfaces and
keeping them apart makes the generated JSDoc typedefs legible. Every model
here is the contract the no-build front end is written against
(`scripts/gen_ui_types.py`), so a field rename is a visible break rather
than a silently undefined property at runtime.

The recurring shape in this file is a value paired with whether it is
*available*, and a note saying why when it is not. That is not defensive
verbosity: the whole point of these screens is that Instagram reports no
follower count and one TikTok actor rounds its view counts, and a UI that
cannot tell "unknown" from "zero" will eventually render a fabricated
number.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# discovery
# ---------------------------------------------------------------------------

class NicheView(BaseModel):
    key: str
    label: str
    keywords: list[str]
    hashtags: list[str]


class DiscoveryRequest(BaseModel):
    kind: str = Field(description="keyword | hashtag")
    platform: str
    value: str
    limit: int = 50
    niche: str | None = None
    #: Re-run instead of reusing a recent identical search. On a paid
    #: provider this is the control that spends money.
    force: bool = False


class DiscoveryEstimate(BaseModel):
    """What a search would cost, before running it."""

    kind: str
    platform: str
    value: str
    limit: int
    provider: str
    is_fixture: bool
    estimated_usd: float
    supported: bool
    unsupported_reason: str | None = None
    #: A recent identical search exists and would be reused for free.
    reusable_job_id: int | None = None
    reusable_started_at: datetime | None = None
    spending_blocked: bool = False
    monthly_cap_usd: float = 0.0
    remaining_usd: float | None = None
    coverage_warnings: list[str] = Field(default_factory=list)

    # What this would buy that is not already held. The provider bills per
    # returned item, not per NEW item, so a run that returns forty already-
    # known publications costs exactly as much as forty new ones.
    #: free | reuse | refresh | new
    recommendation: str = "new"
    #: Measured share of past comparable runs that returned new rows.
    observed_new_ratio: float | None = None
    observed_from_jobs: int = 0
    likely_new: int | None = None
    likely_already_held: int | None = None
    #: Share of the estimate that would buy rows already in the database.
    wasted_usd_estimate: float | None = None
    already_held: dict = Field(default_factory=dict)
    preflight_reasons: list[str] = Field(default_factory=list)
    preflight_warnings: list[str] = Field(default_factory=list)


class CandidateSignalView(BaseModel):
    key: str
    label: str
    value: float | None
    available: bool
    note: str | None = None


class CandidateView(BaseModel):
    creator_id: int
    platform: str
    handle: str
    display_name: str | None
    profile_url: str | None
    follower_count: int | None
    #: False on Instagram. The field above is then null because the number
    #: is unknown, not because it is zero.
    followers_available: bool
    sampled_publications: int
    sampled_median_views: int | None
    sampled_best_views: int | None
    best_publication_external_id: str | None
    best_views_per_follower: float | None
    engagement_rate: float | None
    counts_rounded: bool
    newest_publication_at: datetime | None
    oldest_publication_at: datetime | None
    niche: str | None
    already_researched: bool
    last_refreshed_at: datetime | None
    publications_collected: int
    estimated_research_usd: float
    score: float
    tier: str
    reasons: list[str] = Field(default_factory=list)
    signals: list[CandidateSignalView] = Field(default_factory=list)
    is_fixture: bool = False


class DiscoveryResultView(BaseModel):
    job_id: int | None
    status: str = Field(
        description="ok | reused | in_flight | refused | failed")
    kind: str
    platform: str
    value: str
    niche: str | None = None
    is_fixture: bool = False
    publications_seen: int = 0
    publications_new: int = 0
    creators_seen: int = 0
    creators_new: int = 0
    estimated_usd: float | None = None
    actual_usd: float | None = None
    coverage_notes: list[str] = Field(default_factory=list)
    item_errors: list[dict] = Field(default_factory=list)
    message: str | None = None
    candidates: list[CandidateView] = Field(default_factory=list)


class SearchHistoryRow(BaseModel):
    job_id: int
    kind: str
    platform: str
    value: str
    niche: str | None
    provider: str
    is_fixture: bool
    status: str
    started_at: datetime | None
    publications: int
    creators: int
    estimated_usd: float | None
    actual_usd: float | None
    #: True while an identical search would be served from storage for free.
    reusable: bool = False


class ExpansionRequest(BaseModel):
    job_id: int
    creator_ids: list[int] = Field(default_factory=list)
    max_creators: int | None = None
    max_spend_usd: float | None = None


class ExpansionPlanView(BaseModel):
    candidates: list[CandidateView] = Field(default_factory=list)
    skipped_already_fresh: list[str] = Field(default_factory=list)
    total_estimated_usd: float = 0.0
    budget_remaining_usd: float | None = None
    within_budget: bool = True
    notes: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# timeline
# ---------------------------------------------------------------------------

class TimelinePointView(BaseModel):
    video_id: int
    external_id: str
    published_at: datetime
    views: int | None
    observed_at: datetime | None
    #: Travels with every point because it is the reason two points may not
    #: be comparable.
    age_days: int | None
    views_at_anchor: int | None = None
    anchor_days: int | None = None
    anchor_method: str | None = None
    anchor_unavailable_reason: str | None = None
    relative_performance: float | None = None
    robust_z: float | None = None
    baseline_views: float | None = None
    is_outlier_candidate: bool = False
    is_change_point: bool = False
    observation_count: int = 1
    caption: str | None = None
    duration_s: float | None = None
    url: str | None = None
    thumbnail_url: str | None = None
    media_preview_available: bool = False
    media_preview_note: str | None = None
    counts_rounded: bool = False


class SustainedRunView(BaseModel):
    kind: str
    start_index: int
    end_index: int
    start_at: datetime
    end_at: datetime
    publications: int
    median_relative: float


class FrequencyBucketView(BaseModel):
    month: str
    publications: int


class ChangePointDetail(BaseModel):
    classification: str
    publication_index: int | None
    video_id: int | None
    at: datetime | None
    median_before: float | None
    median_after: float | None
    step_ratio: float | None
    sustained: bool | None
    confidence: float | None
    #: Always False. Never render this score as a probability.
    confidence_is_calibrated: bool = False
    confidence_note: str
    evidence: dict = Field(default_factory=dict)
    missing_evidence: list[str] = Field(default_factory=list)


class CreatorTimelineView(BaseModel):
    creator_id: int
    platform: str
    handle: str
    display_name: str | None
    is_fixture: bool
    #: fixed_age | lifetime_exploratory | mixed. The front end must render
    #: the exploratory modes differently; they are not trajectories.
    mode: str
    mode_label: str
    points: list[TimelinePointView] = Field(default_factory=list)
    change_point: ChangePointDetail | None = None
    outlier_candidates: int = 0
    sustained_runs: list[SustainedRunView] = Field(default_factory=list)
    frequency: list[FrequencyBucketView] = Field(default_factory=list)
    repeatedly_observed: int = 0
    anchored_points: int = 0
    age_span_days: int | None = None
    median_days_between_posts: float | None = None
    caveats: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# report
# ---------------------------------------------------------------------------

class ReportSectionView(BaseModel):
    key: str
    title: str
    #: observed | inferred | unavailable - stated per section, because the
    #: sections do not all rest on the same kind of evidence.
    basis: str
    body: dict = Field(default_factory=dict)
    caveats: list[str] = Field(default_factory=list)
    unavailable_reason: str | None = None
    #: M5. Which of the four report sections this block belongs to.
    group: str = ""
    #: BLUEPRINT section 2's grade, derived from `basis`, plus why.
    grade: str = ""
    grade_reason: str = ""
    #: The publications this block's numbers came from, so a reader can
    #: open them rather than take the figure on trust.
    evidence_video_ids: list[int] = Field(default_factory=list)
    #: Examples that cut against this block's own reading.
    contradictions: list[dict] = Field(default_factory=list)


class ReportGroupView(BaseModel):
    """One of M5's four sections, and the blocks inside it."""

    key: str
    title: str
    section_keys: list[str] = Field(default_factory=list)


class CreatorReportView(BaseModel):
    creator_id: int
    platform: str
    handle: str
    display_name: str | None
    profile_url: str | None
    is_fixture: bool
    generated_at: datetime
    sections: list[ReportSectionView] = Field(default_factory=list)
    research_date: datetime | None = None
    provider: str | None = None
    cost_usd: float | None = None
    job_id: int | None = None
    data_limitations: list[str] = Field(default_factory=list)
    #: M5's four sections, in reading order, each naming the blocks it
    #: contains. Sent rather than hard-coded in the interface so the
    #: report's shape has one definition.
    groups: list[ReportGroupView] = Field(default_factory=list)
    #: True when no publication carries a known age, so every
    #: comparison in the report is between lifetime totals.
    lifetime_approximation: bool = False
    #: Spread between the oldest and youngest observation age.
    age_span_days: int | None = None


# ---------------------------------------------------------------------------
# collections
# ---------------------------------------------------------------------------

class CollectionCreateRequest(BaseModel):
    name: str
    purpose: str = "research"
    niche: str | None = None
    notes: str | None = None


class CollectionUpdateRequest(BaseModel):
    name: str | None = None
    purpose: str | None = None
    niche: str | None = None
    notes: str | None = None


class CollectionMemberRequest(BaseModel):
    creator_id: int
    reason: str | None = None
    notes: str | None = None
    discovered_by_job_id: int | None = None


class CollectionRow(BaseModel):
    id: int
    name: str
    purpose: str
    purpose_label: str
    niche: str | None
    notes: str | None
    created_at: datetime | None
    updated_at: datetime | None
    member_count: int
    researched_count: int
    stalest_refresh: datetime | None


class CollectionMemberView(BaseModel):
    creator_id: int
    platform: str
    handle: str
    display_name: str | None
    follower_count: int | None
    is_fixture: bool
    research_status: str
    last_refreshed_at: datetime | None
    publications_collected: int
    notes: str | None
    reason: str | None
    added_at: datetime | None
    observations: int = 0
    publications_observed: int = 0
    median_views: int | None = None
    #: The comparable pair. Lifetime views are never used to rank members.
    best_relative_performance: float | None = None
    typical_relative_performance: float | None = None
    age_span_days: int | None = None
    breakthrough_classification: str | None = None
    breakthrough_at: datetime | None = None
    median_days_between_posts: float | None = None
    caveats: list[str] = Field(default_factory=list)


class CollectionDetail(BaseModel):
    id: int
    name: str
    purpose: str
    purpose_label: str
    niche: str | None
    notes: str | None
    created_at: datetime | None
    updated_at: datetime | None
    members: list[CollectionMemberView] = Field(default_factory=list)
    member_count: int = 0
    researched_count: int = 0
    stalest_refresh: datetime | None = None
    needs_refresh: list[str] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# analysis requests (Phase 6 seam)
# ---------------------------------------------------------------------------

class AnalysisRequestBody(BaseModel):
    video_id: int
    tier: str = "tier2"
    modalities: list[str] = Field(default_factory=lambda: ["vision",
                                                           "audio"])


class AnalysisRequestView(BaseModel):
    video_id: int
    external_id: str
    platform: str
    tier: str
    modalities: list[str]
    batch_ids: list[int] = Field(default_factory=list)
    media_policy: str
    estimated_gpu_seconds: float
    estimated_transient_mb: float
    #: False in this milestone. A request that looked accepted and silently
    #: never ran would be worse than a refusal.
    will_run: bool
    why_not: str | None = None
    notes: list[str] = Field(default_factory=list)


class FeatureContractView(BaseModel):
    version: str
    modalities: dict
    required_item_fields: list[str]
    pending_requests: list[dict] = Field(default_factory=list)
