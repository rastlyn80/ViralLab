"""Typed contracts for the Hook Intelligence surfaces.

The shape that matters here is `FeatureView`: a value is never sent without
its `method`, its `uncertainty` and, when it has one, the reason it could
not be produced. The interface has to be able to show that
`text_present_estimate` is a weak proxy and `cut_count` is a reproducible
measurement, and it can only do that if the difference survives the wire.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class FeatureView(BaseModel):
    id: int
    feature: str
    #: Whichever of the four value columns is populated.
    value: float | bool | str | dict | list | None = None
    unit: str | None = None
    #: deterministic | proxy | model | human | unavailable
    method: str
    method_version: str
    model: str | None = None
    uncertainty: float | None = None
    uncertainty_kind: str | None = None
    interval_start_s: float | None = None
    interval_end_s: float | None = None
    params: dict = Field(default_factory=dict)
    note: str | None = None
    unavailable_reason: str | None = None
    extracted_at: datetime | None = None
    #: A correction sits beside the machine value, never over it, so the
    #: interface can show both and the pair can be audited later.
    corrected_value: float | bool | str | None = None
    corrected_by: str | None = None
    corrected_at: datetime | None = None
    correction_note: str | None = None
    is_corrected: bool = False


class AnalysedVideoRow(BaseModel):
    video_id: int
    external_id: str
    posted_at: datetime | None
    duration_s: float | None
    caption: str | None
    views: int | None
    #: The outcome. Lower is better.
    skip_rate: float | None = None
    #: Reported, but NOT a completion fraction: it includes replays.
    avg_watch_ms: float | None = None
    avg_watch_includes_replays: bool = True
    age_days_at_capture: int | None = None
    features_extracted: int = 0
    method_versions: list[str] = Field(default_factory=list)
    has_media: bool = False
    corrections: int = 0
    #: A few headline opening characteristics, for the list view.
    cut_count: float | None = None
    text_region_score: float | None = None
    speech_present: bool | None = None
    first_sound_at_s: float | None = None


class AnalysedVideoPage(BaseModel):
    total: int
    offset: int
    limit: int
    items: list[AnalysedVideoRow] = Field(default_factory=list)
    #: How much of the corpus this view can speak about.
    analysable: int = 0
    extracted: int = 0
    with_retention: int = 0


class HookDetail(BaseModel):
    video_id: int
    external_id: str
    posted_at: datetime | None
    duration_s: float | None
    caption: str | None
    platform: str
    skip_rate: float | None = None
    avg_watch_ms: float | None = None
    views: int | None = None
    #: Signed, short-lived, single-video. Null when no file is preserved.
    media_url: str | None = None
    media_status: str | None = None
    hook_seconds: float = 3.0
    features: list[FeatureView] = Field(default_factory=list)
    runs: list[dict] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)


class CorrectionRequest(BaseModel):
    value: float | bool | str | None = None
    note: str | None = None


class ExtractionRequest(BaseModel):
    #: Empty means "the retention population", which is the only set the
    #: analysis can use.
    video_ids: list[int] = Field(default_factory=list)
    scope: str = Field(
        default="retention",
        description="retention | sample | all | ids")
    sample_size: int = 12
    include_speech: bool = False
    #: Re-running an existing version rewrites that version's rows; a new
    #: version writes beside them. Never destructive either way.
    force: bool = False


class ExtractionResultView(BaseModel):
    run_id: int | None
    kind: str
    method_version: str
    requested: int
    done: int
    failed: int
    features_written: int
    compute_seconds: float
    errors: list[dict] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class AssociationView(BaseModel):
    feature: str
    n: int
    rho: float | None = None
    p_value: float | None = None
    p_adjusted: float | None = None
    rho_partial_duration: float | None = None
    rho_partial_duration_age: float | None = None
    ci_low: float | None = None
    ci_high: float | None = None
    effect_size_label: str = "negligible"
    direction: str | None = None
    interpretation: str | None = None
    caveats: list[str] = Field(default_factory=list)


class RetentionReportView(BaseModel):
    outcome: str
    outcome_note: str
    n_videos: int
    n_with_media: int
    n_with_features: int
    n_analysed: int
    duration_range_s: list[float] | None = None
    age_range_days: list[int] | None = None
    skip_range: list[float] | None = None
    associations: list[AssociationView] = Field(default_factory=list)
    survivors: list[str] = Field(default_factory=list)
    survivor_collinearity: list[dict] = Field(default_factory=list)
    method_version: str
    verdict: str
    limitations: list[str] = Field(default_factory=list)


class HookCoverage(BaseModel):
    analysable: int
    deterministic_done: int
    speech_done: int
    with_retention: int
    corrections: int
    method_version: str
    #: Honest framing for the interface: the retention population is the
    #: ceiling on anything statistical, and it is far below the corpus.
    note: str = ""
