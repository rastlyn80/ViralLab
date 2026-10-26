"""Typed contracts for the AI provider, routing and analysis surfaces.

No model here carries a credential, and none ever should. Availability is
expressed as a boolean plus the *name* of the missing environment variable,
which is enough for the interface to tell the owner what to do and useless
to anyone who intercepts the response.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class ProviderRow(BaseModel):
    provider: str
    model: str
    #: local | cloud. The field the interface must always surface, because
    #: it answers "did my media leave this machine".
    hosting: str
    available: bool
    #: Why not, naming the missing variable - never its value.
    unavailable_reason: str | None = None
    accepts: list[str] = Field(default_factory=list)
    #: Empty means general-purpose. A provider that accepts
    #: images is not thereby able to answer every question
    #: about them.
    supported_tasks: list[str] = Field(default_factory=list)
    accepts_video_natively: bool = False
    max_images_per_request: int | None = None
    max_image_bytes: int | None = None
    structured_output: bool = False
    #: Things the vendor documents the model cannot do.
    documented_limits: list[str] = Field(default_factory=list)
    usd_per_mtok_in: float | None = None
    usd_per_mtok_out: float | None = None
    verified_on: str = ""
    source_url: str = ""
    notes: list[str] = Field(default_factory=list)


class SpendingCategoryView(BaseModel):
    category: str
    cap_usd: float
    #: False when the category has no configured cap. An unconfigured
    #: category is NOT authorised, never "unlimited".
    authorised: bool
    committed_usd: float = 0.0
    remaining_usd: float = 0.0
    note: str = ""


class PolicyView(BaseModel):
    mode: str
    escalation_authorised: bool
    authorised_providers: list[str] = Field(default_factory=list)
    max_usd_per_request: float
    escalate_below_confidence: float
    allow_third_party_media_to_cloud: bool
    monthly_cap_usd: float
    categories: list[SpendingCategoryView] = Field(default_factory=list)
    modes: list[str] = Field(default_factory=list)
    note: str = ""


class VisionTaskView(BaseModel):
    model_config = {"populate_by_name": True}

    task: str
    #: The JSON schema a response must satisfy. Shown so the owner can see
    #: exactly what is being asked for. Named `json_schema` because `schema`
    #: shadows a Pydantic BaseModel attribute; the wire name stays `schema`
    #: via the alias, so the front end is unaffected.
    json_schema: dict = Field(default_factory=dict, alias="schema")
    prompt: str
    hook_seconds: float
    hook_frames: int
    later_offsets_s: list[float] = Field(default_factory=list)
    #: What the task will not answer, and why.
    refusals: list[str] = Field(default_factory=list)


class AnalysisPlanView(BaseModel):
    video_id: int
    task: str
    mode: str
    providers: list[str] = Field(default_factory=list)
    refused: bool = False
    reason: str = ""
    estimated_usd: float = 0.0
    requires_authorisation: bool = False
    #: Every provider weighed and why it was or was not chosen.
    considered: list[dict] = Field(default_factory=list)
    frames_planned: int = 0
    frame_timestamps: list[float] = Field(default_factory=list)
    is_own_media: bool = True
    category: str = "cloud_vision"
    category_cap_usd: float = 0.0
    category_authorised: bool = False


class AnalyseRequestBody(BaseModel):
    video_id: int
    mode: str | None = None
    #: Required for any cloud call. A mode is not a confirmation.
    confirm_paid: bool = False


class AiOperationRow(BaseModel):
    id: int | None = None
    video_id: int | None = None
    task: str
    provider: str
    model: str
    hosting: str
    category: str
    mode: str | None = None
    status: str
    schema_valid: bool | None = None
    validation_errors: list = Field(default_factory=list)
    refused_reason: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    images: int | None = None
    estimated_usd: float | None = None
    #: Billed, never an estimate promoted into this field.
    actual_usd: float | None = None
    latency_s: float | None = None
    peak_vram_mib: int | None = None
    created_at: datetime | None = None
