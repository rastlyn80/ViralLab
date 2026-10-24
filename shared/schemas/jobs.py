"""Job and batch contracts shared by the Ubuntu server and the Windows worker.

This module is imported by BOTH halves of the system, so it must stay free of
server-only or worker-only dependencies (no SQLAlchemy, no CUDA, no ffmpeg).
It is the single definition of the wire format between them.

Design follows ADR 0002: the lease unit is a batch of one modality, while the
result unit stays per video with its own idempotency key.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

SCHEMA_VERSION = "1"


class Modality(StrEnum):
    """What kind of work a batch contains. One modality per batch, because the
    models cannot be co-resident in 8 GB VRAM (ADR 0004)."""

    VISION = "vision"              # Qwen2.5-VL frame captioning
    TRANSCRIPTION = "transcription"  # faster-whisper
    AUDIO = "audio"                # librosa features (CPU)
    SHOTS = "shots"                # OpenCV shot detection (CPU)
    SCREENING = "screening"        # Tier 1 metadata features (CPU, server-side)


#: Modalities that require the GPU machine. The rest can run on the server.
GPU_MODALITIES: frozenset[Modality] = frozenset(
    {Modality.VISION, Modality.TRANSCRIPTION}
)


class Tier(StrEnum):
    """Progressive analysis tier (DEVELOPMENT_PLAN.md 5.2)."""

    T1 = "1"  # metadata screening, all videos
    T2 = "2"  # selective multimodal, stratified sample
    T3 = "3"  # deep analysis, small justified subset


class BatchStatus(StrEnum):
    PENDING = "pending"
    LEASED = "leased"
    DONE = "done"
    FAILED = "failed"      # terminal: attempts exhausted, dead-lettered
    CANCELLED = "cancelled"


class VideoRef(BaseModel):
    """One unit of work inside a batch."""

    video_id: int
    #: Server-issued, short-lived, HMAC-signed, single-video media URL.
    #: Never a blanket static mount (DEVELOPMENT_PLAN.md 4.2).
    media_url: str
    duration_s: float | None = None
    #: Frames to sample. Tier 2 default 4, Tier 3 default 12; the single
    #: biggest cost lever in the pipeline (0B: VLM is ~79% of Tier 2).
    frames: int = 4


class BatchLease(BaseModel):
    """What the server hands a worker on a successful dequeue."""

    batch_id: int
    #: Must be echoed on EVERY heartbeat, result and completion call. A
    #: mismatch is rejected 409 so a reaped zombie cannot overwrite fresher
    #: results (ADR 0002).
    lease_token: str
    modality: Modality
    tier: Tier
    method_version: str
    videos: list[VideoRef]
    lease_expires_at: datetime
    attempt: int
    max_attempts: int
    schema_version: str = SCHEMA_VERSION

    @property
    def requires_gpu(self) -> bool:
        return self.modality in GPU_MODALITIES


class LeaseRequest(BaseModel):
    """Worker -> server: ask for work it can actually run right now."""

    worker_id: str
    #: Modalities this worker is willing to take. The worker filters by its
    #: own free VRAM before asking (ADR 0004), so the server never has to
    #: model the client's memory state.
    capabilities: list[Modality]
    free_vram_mib: int | None = None
    max_videos: int = 100


class VideoResult(BaseModel):
    """One video's analysis output, committed independently of its siblings.

    Per-video commits are what make an interrupted batch cost only its
    unfinished remainder (ADR 0002).
    """

    video_id: int
    #: "{modality}:{video_id}:{method_version}" - unique index in the DB,
    #: written by upsert, so a replayed batch cannot duplicate a row.
    idempotency_key: str
    payload: dict[str, Any] = Field(default_factory=dict)
    #: Set when THIS video failed while the batch continued. A video that
    #: repeatedly fails is quarantined rather than failing the batch forever.
    error: str | None = None
    compute_seconds: float | None = None
    peak_vram_mib: int | None = None

    @field_validator("idempotency_key")
    @classmethod
    def _shape(cls, v: str) -> str:
        if v.count(":") != 2:
            raise ValueError(
                "idempotency_key must be '{modality}:{video_id}:{method_version}'")
        return v


def make_idempotency_key(modality: Modality | str, video_id: int,
                         method_version: str) -> str:
    """Single place this key is constructed, so the server and the worker can
    never disagree about its shape."""
    if ":" in str(method_version):
        raise ValueError("method_version must not contain ':'")
    return f"{modality}:{video_id}:{method_version}"


class ResultSubmission(BaseModel):
    """Worker -> server: commit results for part or all of a leased batch."""

    batch_id: int
    lease_token: str
    worker_id: str
    results: list[VideoResult]
    #: True when the worker is done with the batch. False means "progress
    #: commit, still working" and the lease stays live.
    final: bool = False
    #: Set with final=True when the worker is handing back an incomplete
    #: batch on purpose (e.g. VRAM fell below the critical floor). The
    #: remainder is requeued without counting as a failure.
    partial_reason: str | None = None
    #: Observed peak VRAM for the batch as a whole. Sent explicitly because
    #: per-video peaks are None on batches that finish faster than the
    #: sampler's interval, which would leave the batch peak NULL (ADR 0004
    #: section 4 requires it recorded).
    batch_peak_vram_mib: int | None = None


class Heartbeat(BaseModel):
    """Worker -> server: 'still alive, extend my lease'.

    The reaper requeues only batches that STOPPED heartbeating, so a slow
    batch is never killed merely for being slow.
    """

    batch_id: int
    lease_token: str
    worker_id: str
    videos_done: int = 0
    peak_vram_mib: int | None = None


class WorkerStatus(BaseModel):
    """Worker -> server: why it is not taking work, so the dashboard can show
    'deferred: insufficient VRAM' rather than looking like a stall."""

    worker_id: str
    state: Literal["idle", "working", "deferred", "error"]
    reason: str | None = None
    free_vram_mib: int | None = None
    total_vram_mib: int | None = None
