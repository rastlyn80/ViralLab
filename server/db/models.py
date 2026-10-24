"""SQLAlchemy models — Phase 1 core schema.

Scope note: Phase 1 creates the tables the job queue and its acceptance tests
need, plus the minimum content tables they reference. The analytical tables
(embeddings, creator_baselines, performance_labels, analysis_samples, models,
recommendations, experiments, outcomes) arrive with the phases that use them,
each in its own migration — there is no value in empty tables now, and their
columns will be better informed later.

Queue design follows ADR 0002 (batch lease, per-video idempotency) and
ADR 0004 (GPU memory safety telemetry).
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger, Boolean, CheckConstraint, DateTime, Float, ForeignKey, Index,
    Integer, String, Text, UniqueConstraint, func, text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


# --------------------------------------------------------------------------
# content
# --------------------------------------------------------------------------

class Creator(Base):
    __tablename__ = "creators"

    id: Mapped[int] = mapped_column(primary_key=True)
    platform: Mapped[str] = mapped_column(String(32))
    external_id: Mapped[str | None] = mapped_column(String(128))
    handle: Mapped[str] = mapped_column(String(256))
    follower_count: Mapped[int | None] = mapped_column(BigInteger)
    niche_tags: Mapped[list | None] = mapped_column(JSONB)
    #: Own accounts get true daily snapshots via official APIs, so their
    #: fixed-age anchors are exact rather than inferred (plan 5.4).
    is_own_account: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now())

    videos: Mapped[list["Video"]] = relationship(back_populates="creator")

    __table_args__ = (
        UniqueConstraint("platform", "handle", name="uq_creator_platform_handle"),
    )


class Video(Base):
    __tablename__ = "videos"

    id: Mapped[int] = mapped_column(primary_key=True)
    creator_id: Mapped[int] = mapped_column(ForeignKey("creators.id"))
    platform: Mapped[str] = mapped_column(String(32))
    external_id: Mapped[str] = mapped_column(String(128))
    caption: Mapped[str | None] = mapped_column(Text)
    hashtags: Mapped[list | None] = mapped_column(JSONB)
    #: BLOCKING field per the Phase 0A scorecard: without an absolute post
    #: time, video age is unknown and fixed-age comparison is impossible.
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_s: Mapped[float | None] = mapped_column(Float)
    #: official_api | provider | local_master
    source: Mapped[str] = mapped_column(String(32))
    #: absent | available | unsupported | purged (retention policy, plan 10).
    #: `unsupported` means the publication is not a video - an image or a
    #: carousel - so there is nothing to preserve. Keeping it distinct from
    #: `absent` stops the preserver retrying it forever and stops it being
    #: reported as a coverage gap.
    media_status: Mapped[str] = mapped_column(String(16), default="absent")
    media_path: Mapped[str | None] = mapped_column(Text)
    #: Why media is not stored, as `<class>:<detail>`. The class separates a
    #: genuine API restriction from a processing failure from a type that was
    #: never a video; only the middle one is worth retrying.
    media_reason: Mapped[str | None] = mapped_column(String(64))
    media_checked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True))
    #: Bounded, so a permanently failing item cannot be retried forever.
    media_attempts: Mapped[int] = mapped_column(Integer, default=0)
    #: Platform-hosted cover image. The URL expires, so the dHash is what is
    #: actually relied on; the URL is kept for provenance. For TikTok this is
    #: the ONLY content signal available - the API returns no media file - so
    #: without it cross-platform matching has no visual evidence at all.
    cover_url: Mapped[str | None] = mapped_column(Text)
    cover_hash: Mapped[str | None] = mapped_column(String(32))
    cover_fetched_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now())

    creator: Mapped[Creator] = relationship(back_populates="videos")

    __table_args__ = (
        UniqueConstraint("platform", "external_id", name="uq_video_platform_ext"),
        Index("ix_videos_posted_at", "posted_at"),
    )


class MetricsSnapshot(Base):
    """Append-only. Raw readings are never mutated; fixed-age anchors
    (views@7d, views@30d) are derived at read time, so changing the anchor
    policy can never destroy collected data (plan 5.4)."""

    __tablename__ = "metrics_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True)
    video_id: Mapped[int] = mapped_column(ForeignKey("videos.id"))
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    views: Mapped[int | None] = mapped_column(BigInteger)
    likes: Mapped[int | None] = mapped_column(BigInteger)
    comments: Mapped[int | None] = mapped_column(BigInteger)
    shares: Mapped[int | None] = mapped_column(BigInteger)
    saves: Mapped[int | None] = mapped_column(BigInteger)
    #: Some providers expand the displayed abbreviation ("54.9M" -> 54900000).
    #: Recorded per snapshot so precision loss is never silently assumed away
    #: (Phase 0A section 4, finding 2).
    is_rounded: Mapped[bool] = mapped_column(Boolean, default=False)
    raw: Mapped[dict | None] = mapped_column(JSONB)

    __table_args__ = (
        Index("ix_metrics_video_captured", "video_id", "captured_at"),
    )


# --------------------------------------------------------------------------
# job queue
# --------------------------------------------------------------------------

class AnalysisBatch(Base):
    """The lease unit (ADR 0002): one modality across many videos, so a model
    is loaded once per batch rather than once per video."""

    __tablename__ = "analysis_batches"

    id: Mapped[int] = mapped_column(primary_key=True)
    modality: Mapped[str] = mapped_column(String(32))
    tier: Mapped[str] = mapped_column(String(4))
    method_version: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), default="pending")

    #: Minted at dequeue. Every heartbeat/result/completion must present it;
    #: a mismatch is rejected so a reaped zombie worker cannot overwrite
    #: fresher results.
    lease_token: Mapped[str | None] = mapped_column(String(64))
    leased_by: Mapped[str | None] = mapped_column(String(128))
    leased_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True))
    #: Last heartbeat. The reaper requeues only batches that STOPPED
    #: heartbeating, so a slow batch is never killed for being slow.
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True))

    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    #: Exponential backoff gate. A batch is invisible to dequeue until then.
    not_before: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    last_error: Mapped[str | None] = mapped_column(Text)
    peak_vram_mib: Mapped[int | None] = mapped_column(Integer)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True))

    videos: Mapped[list["BatchVideo"]] = relationship(
        back_populates="batch", cascade="all, delete-orphan")

    __table_args__ = (
        CheckConstraint(
            "status in ('pending','leased','done','failed','cancelled')",
            name="ck_batch_status"),
        CheckConstraint("attempt_count >= 0", name="ck_batch_attempts"),
        # Partial index: dequeue only ever scans pending rows.
        Index("ix_batches_dequeue", "modality", "not_before",
              postgresql_where="status = 'pending'"),
        Index("ix_batches_reaper", "lease_expires_at",
              postgresql_where="status = 'leased'"),
    )


class BatchVideo(Base):
    """Membership of a video in a batch, with its own per-video state so an
    interrupted batch can resume on only the unfinished remainder."""

    __tablename__ = "batch_videos"

    id: Mapped[int] = mapped_column(primary_key=True)
    batch_id: Mapped[int] = mapped_column(
        ForeignKey("analysis_batches.id", ondelete="CASCADE"))
    video_id: Mapped[int] = mapped_column(ForeignKey("videos.id"))
    frames: Mapped[int] = mapped_column(Integer, default=4)
    #: pending | done | failed | quarantined
    state: Mapped[str] = mapped_column(String(16), default="pending")
    #: Own failure count, so one pathological video is quarantined instead of
    #: failing its whole batch forever (ADR 0002, consequences).
    failure_count: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text)

    batch: Mapped[AnalysisBatch] = relationship(back_populates="videos")

    __table_args__ = (
        UniqueConstraint("batch_id", "video_id", name="uq_batch_video"),
        CheckConstraint(
            "state in ('pending','done','failed','quarantined')",
            name="ck_batch_video_state"),
    )


class AnalysisResult(Base):
    """Per-video analysis output. The unique index on idempotency_key is what
    makes a replayed batch produce exactly one row per video."""

    __tablename__ = "analysis_results"

    id: Mapped[int] = mapped_column(primary_key=True)
    video_id: Mapped[int] = mapped_column(ForeignKey("videos.id"))
    modality: Mapped[str] = mapped_column(String(32))
    tier: Mapped[str] = mapped_column(String(4))
    method_version: Mapped[str] = mapped_column(String(64))
    #: "{modality}:{video_id}:{method_version}" — built only by
    #: shared.schemas.jobs.make_idempotency_key so both halves agree.
    idempotency_key: Mapped[str] = mapped_column(String(256))

    payload: Mapped[dict | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)
    compute_seconds: Mapped[float | None] = mapped_column(Float)
    peak_vram_mib: Mapped[int | None] = mapped_column(Integer)

    produced_by_batch: Mapped[int | None] = mapped_column(
        ForeignKey("analysis_batches.id"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(),
        onupdate=func.now())

    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_result_idempotency"),
        Index("ix_results_video_modality", "video_id", "modality"),
    )


class Performance(Base):
    """The underlying thing that was performed - one layer above any file.

    Three layers are deliberately distinct, because conflating them is what
    makes cross-platform analysis wrong:

      1. **Performance** (this table) - the musical performance itself. One
         sitting at the piano, one reading of one work.
      2. **Variant** (`local_masters`) - one *edit* of a performance: a
         specific export, with its own hook, overlays and trim. A performance
         may have several, and typically does when the owner tailors a cut
         per platform.
      3. **Publication** (`videos`) - one variant posted to one platform at
         one time. The same variant published to TikTok and to Instagram is
         two publications with independent metrics.

    Collapsing 2 into 3 would make a TikTok/Instagram pair look like two
    different videos; collapsing 1 into 2 would make a re-edit look like a
    different performance. Both are errors this project exists to avoid.
    """

    __tablename__ = "performances"

    id: Mapped[int] = mapped_column(primary_key=True)
    #: Free text, owner-facing: "Chopin Ballade No. 1, studio, Oct 2026".
    title: Mapped[str] = mapped_column(Text)
    #: Composer / piece, kept separate so several performances of one work
    #: can be grouped later without re-parsing titles.
    work: Mapped[str | None] = mapped_column(Text)
    recorded_on: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True))
    notes: Mapped[str | None] = mapped_column(Text)
    #: inferred | confirmed - a performance grouped automatically is not the
    #: same claim as one the owner has confirmed.
    status: Mapped[str] = mapped_column(String(16), default="inferred")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now())

    masters: Mapped[list["LocalMaster"]] = relationship(
        back_populates="performance")

    __table_args__ = (
        CheckConstraint("status in ('inferred','confirmed')",
                        name="ck_performance_status"),
    )


class LocalMaster(Base):
    """A source video on the owner's machine.

    The file itself stays where it is - this records its identity and
    fingerprint so posts can be matched to it without moving anything.
    """

    __tablename__ = "local_masters"

    id: Mapped[int] = mapped_column(primary_key=True)
    #: Content-derived id from the local scan; stable across re-scans.
    quick_id: Mapped[str] = mapped_column(String(64))
    path: Mapped[str] = mapped_column(Text)
    filename: Mapped[str] = mapped_column(Text)
    duration_s: Mapped[float | None] = mapped_column(Float)
    bytes: Mapped[int | None] = mapped_column(BigInteger)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    #: dHash sequence, hex strings. Stored so re-matching needs no re-scan.
    fingerprint: Mapped[dict | None] = mapped_column(JSONB)
    #: Set only once a copy has been transferred to the server.
    server_path: Mapped[str | None] = mapped_column(Text)
    #: Which performance this file is an edit of. Nullable: a master whose
    #: performance has not been established is a known unknown, not a
    #: performance of its own.
    performance_id: Mapped[int | None] = mapped_column(
        ForeignKey("performances.id"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now())

    performance: Mapped[Performance | None] = relationship(
        back_populates="masters")

    __table_args__ = (
        UniqueConstraint("quick_id", name="uq_local_master_quick_id"),
        Index("ix_local_masters_performance", "performance_id"),
    )


class VideoMasterLink(Base):
    """Which published post came from which local master.

    Deliberately many-to-many: one master can produce several posts (a
    repost, a re-edit), and a post could in principle draw on more than one
    master. Forcing one-to-one would quietly discard exactly the cases this
    project most wants to study.
    """

    __tablename__ = "video_master_links"

    id: Mapped[int] = mapped_column(primary_key=True)
    video_id: Mapped[int] = mapped_column(ForeignKey("videos.id"))
    master_id: Mapped[int] = mapped_column(ForeignKey("local_masters.id"))
    #: Combined frame-overlap and duration score, 0-1.
    score: Mapped[float] = mapped_column(Float)
    frame_overlap: Mapped[float | None] = mapped_column(Float)
    duration_delta_s: Mapped[float | None] = mapped_column(Float)
    #: auto | confirmed | rejected | pending
    status: Mapped[str] = mapped_column(String(16), default="pending")
    method: Mapped[str] = mapped_column(String(32), default="dhash-seq-v1")
    #: Runner-up score: a near-tie means the top pick was not clearly right.
    runner_up: Mapped[float | None] = mapped_column(Float)
    #: Audio agreement and which descriptor produced it. Stored separately
    #: from `score` so a link can never be read as better evidenced than it
    #: was: "envelope" and "none" are materially weaker than "spectral", and
    #: the combined score alone does not reveal which was used.
    audio_similarity: Mapped[float | None] = mapped_column(Float)
    #: spectral | envelope | none
    audio_method: Mapped[str | None] = mapped_column(String(16))
    #: Seconds the publication starts after the master. Nonzero is itself
    #: evidence: it means a trimmed head or a different opening hook.
    audio_offset_s: Mapped[float | None] = mapped_column(Float)
    #: Full per-signal detail, for the review UI and for auditing a decision
    #: after the fact.
    evidence: Mapped[dict | None] = mapped_column(JSONB)
    #: human | matcher - which agent set the current status. A human decision
    #: is never overwritten by a re-run; see scripts/validate_matches.py.
    decided_by: Mapped[str | None] = mapped_column(String(16))
    decided_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True))
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("video_id", "master_id", name="uq_video_master"),
        # 'variant' records a human saying "same performance, different edit"
        # - a real relationship that is neither a match nor a rejection, and
        # which previously had nowhere to go.
        CheckConstraint(
            "status in ('auto','confirmed','rejected','pending','variant')",
            name="ck_link_status"),
        CheckConstraint("audio_method is null or "
                        "audio_method in ('spectral','envelope','none')",
                        name="ck_link_audio_method"),
        CheckConstraint("decided_by is null or decided_by in "
                        "('human','matcher')", name="ck_link_decided_by"),
        Index("ix_links_status", "status"),
    )


class PublicationLink(Base):
    """Two publications that are the same content on different platforms.

    This is the cross-posting table. It exists separately from
    `video_master_links` because a TikTok/Instagram pair can be established
    *without* a local master: the owner has 234 TikTok and 286 Instagram
    publications but local files for only part of the archive, and a pair
    evidenced by audio, date and caption is still a pair.

    Ordered by `video_a_id < video_b_id` so a pair has exactly one row
    regardless of which side is discovered first.
    """

    __tablename__ = "publication_links"

    id: Mapped[int] = mapped_column(primary_key=True)
    video_a_id: Mapped[int] = mapped_column(ForeignKey("videos.id"))
    video_b_id: Mapped[int] = mapped_column(ForeignKey("videos.id"))
    #: same_variant  - the same edit posted to both platforms
    #: platform_edit - same performance, deliberately different edit
    #: repost        - the same content published again, same platform
    relationship_type: Mapped[str] = mapped_column(String(24))
    #: proposed | confirmed | rejected. There is no 'auto': a cross-platform
    #: pair is never promoted to verified without a human, because unlike a
    #: local master there is no file to fall back on as proof.
    status: Mapped[str] = mapped_column(String(16), default="proposed")
    score: Mapped[float | None] = mapped_column(Float)
    #: Which independent signals agreed, and what each one said.
    evidence: Mapped[dict | None] = mapped_column(JSONB)
    method: Mapped[str | None] = mapped_column(String(32))
    decided_by: Mapped[str | None] = mapped_column(String(16))
    decided_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True))
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("video_a_id", "video_b_id",
                         name="uq_publication_pair"),
        CheckConstraint("video_a_id < video_b_id", name="ck_pub_link_order"),
        CheckConstraint(
            "relationship_type in ('same_variant','platform_edit','repost')",
            name="ck_pub_link_type"),
        CheckConstraint("status in ('proposed','confirmed','rejected')",
                        name="ck_pub_link_status"),
        CheckConstraint("decided_by is null or decided_by in "
                        "('human','matcher')", name="ck_pub_link_decided_by"),
        Index("ix_pub_links_status", "status"),
    )


class OAuthToken(Base):
    """Platform credentials that rotate at runtime.

    Kept in the database rather than .env because they change: TikTok rotates
    the refresh token on every refresh (ADR 0005), so a static file would go
    stale. .env holds only the app credentials, which do not rotate.
    """

    __tablename__ = "oauth_tokens"

    id: Mapped[int] = mapped_column(primary_key=True)
    platform: Mapped[str] = mapped_column(String(32))
    #: Which account these belong to, e.g. the handle or the IG user id.
    account_ref: Mapped[str] = mapped_column(String(128))

    access_token: Mapped[str] = mapped_column(Text)
    #: TikTok only. Rotates on refresh and must be written together with the
    #: access token, or auth breaks permanently one cycle later.
    refresh_token: Mapped[str | None] = mapped_column(Text)
    #: NULL means "does not expire" — true of Instagram Page tokens on the
    #: Facebook Login path.
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    refresh_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True))
    scope: Mapped[str | None] = mapped_column(Text)
    #: Facebook Page id backing an Instagram Business account.
    page_id: Mapped[str | None] = mapped_column(String(128))
    #: Set when the platform says re-authorisation is required (e.g. a
    #: 190-series Meta error). Collectors defer instead of retrying forever.
    needs_reauth: Mapped[bool] = mapped_column(Boolean, default=False)
    last_error: Mapped[str | None] = mapped_column(Text)
    last_refreshed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint("platform", "account_ref", name="uq_token_account"),
    )


class CollectionRun(Base):
    """One collector execution. Gives the 7-day unattended-run acceptance
    criterion something to be measured against, and makes a silent stall
    visible."""

    __tablename__ = "collection_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    platform: Mapped[str] = mapped_column(String(32))
    account_ref: Mapped[str] = mapped_column(String(128))
    kind: Mapped[str] = mapped_column(String(32))     # backfill | daily
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16), default="running")
    videos_seen: Mapped[int] = mapped_column(
        Integer, default=0, server_default=text("0"))
    videos_new: Mapped[int] = mapped_column(
        Integer, default=0, server_default=text("0"))
    snapshots_written: Mapped[int] = mapped_column(
        Integer, default=0, server_default=text("0"))
    media_downloaded: Mapped[int] = mapped_column(
        Integer, default=0, server_default=text("0"))
    error: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        CheckConstraint("status in ('running','ok','failed','deferred')",
                        name="ck_run_status"),
        Index("ix_runs_platform_started", "platform", "started_at"),
    )


class WorkerHeartbeat(Base):
    """Last known worker state, so the dashboard can show 'deferred:
    insufficient VRAM' rather than an unexplained stall (ADR 0004)."""

    __tablename__ = "worker_heartbeats"

    worker_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    state: Mapped[str] = mapped_column(String(16))
    reason: Mapped[str | None] = mapped_column(Text)
    free_vram_mib: Mapped[int | None] = mapped_column(Integer)
    total_vram_mib: Mapped[int | None] = mapped_column(Integer)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(),
        onupdate=func.now())
