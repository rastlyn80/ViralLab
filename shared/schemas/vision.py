"""Contracts between the GPU worker and the server.

The worker posts back what it did, including when it failed. A result that
the schema rejected is still submitted - an invalid response is a fact
about the model, and dropping it would make the accuracy figure optimistic
by hiding its own failures.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class VisionPendingItem(BaseModel):
    video_id: int
    external_id: str
    duration_s: float | None = None
    posted_at: datetime | None = None


class VisionPendingList(BaseModel):
    items: list[VisionPendingItem] = Field(default_factory=list)
    total_analysable: int = 0
    already_done: int = 0
    method_version: str


class FrameRef(BaseModel):
    """What the worker was actually given, by content hash.

    The hash matters: it ties a stored description to the exact pixels it
    describes, so a re-encode at a different quality is visibly a different
    input rather than silently the same one.
    """

    at_s: float
    sha256: str
    bytes: int


class VisionResultBody(BaseModel):
    video_id: int
    #: ok | invalid | failed
    status: str = "ok"
    task: str | None = None
    provider: str | None = None
    model: str | None = None
    hosting: str | None = "local"
    #: The validated object, present only when status is ok.
    data: dict | None = None
    #: What the model said when it did not validate, truncated. Kept
    #: because a malformed answer is evidence about the model.
    raw_text: str | None = None
    schema_valid: bool | None = None
    validation_errors: list[str] = Field(default_factory=list)
    refused_reason: str | None = None
    error: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    images: int | None = None
    latency_s: float | None = None
    #: Total VRAM on the card during the run, not this process's share.
    peak_vram_mib: int | None = None
    frames: list[FrameRef] = Field(default_factory=list)
    media_sha256: str | None = None


class VisionResultView(BaseModel):
    video_id: int
    status: str
    features_written: int
    method_version: str
    run_id: int | None = None
    operation_id: int | None = None


class VisionRequestBody(BaseModel):
    """Ask for one publication's opening to be analysed visually."""

    video_id: int
    #: Re-analyse a video that already has features under this version.
    force: bool = False


class VisionRequestRow(BaseModel):
    id: int
    video_id: int
    external_id: str | None = None
    task: str
    method_version: str
    status: str
    mode: str
    force: bool = False
    attempts: int = 0
    claimed_by: str | None = None
    requested_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error: str | None = None


class VisionQueueView(BaseModel):
    """The queue, plus whether the machine that drains it is awake.

    `worker` is the honest part. A queue with no worker online is a list of
    things that will not happen until the workstation is switched on, and
    the interface says so rather than showing a spinner.
    """

    requests: list[VisionRequestRow] = Field(default_factory=list)
    counts: dict[str, int] = Field(default_factory=dict)
    worker: dict = Field(default_factory=dict)
    method_version: str
    analysed: int = 0
