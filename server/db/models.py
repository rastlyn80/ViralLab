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

from sqlalchemy import (JSON, 
    BigInteger, Boolean, CheckConstraint, Date, DateTime, Float, ForeignKey,
    Index, Integer, Numeric, String, Text, UniqueConstraint, func, text,
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
    """One account on one platform.

    Discovered creators, manually looked-up creators and the owner's own
    accounts all live here. That is a deliberate commitment from
    docs/PRODUCT_VISION.md: there is no separate "competitor" dataset, and a
    creator found by hashtag differs from one typed in by hand only by
    `source`. Analysis that silently studied one population would be
    studying a biased sample of its own making.
    """

    __tablename__ = "creators"

    id: Mapped[int] = mapped_column(primary_key=True)
    platform: Mapped[str] = mapped_column(String(32))
    external_id: Mapped[str | None] = mapped_column(String(128))
    handle: Mapped[str] = mapped_column(String(256))
    display_name: Mapped[str | None] = mapped_column(Text)
    profile_url: Mapped[str | None] = mapped_column(Text)
    #: Latest known value, for convenience. History lives in
    #: `creator_snapshots` and is what any growth claim must rest on - a
    #: single mutable number cannot evidence "this video grew the account".
    follower_count: Mapped[int | None] = mapped_column(BigInteger)
    niche_tags: Mapped[list | None] = mapped_column(JSONB)
    #: Own accounts get true daily snapshots via official APIs, so their
    #: fixed-age anchors are exact rather than inferred (plan 5.4).
    is_own_account: Mapped[bool] = mapped_column(Boolean, default=False)
    #: own_api | discovery | manual_lookup | cross_reference
    source: Mapped[str] = mapped_column(String(32), default="own_api")
    #: The query, hashtag or handle that surfaced this creator.
    discovered_via: Mapped[str | None] = mapped_column(Text)
    first_seen_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True))
    last_refreshed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True))
    #: Coverage and restrictions of whatever produced this row, so a partial
    #: back-catalogue is never mistaken for a complete one.
    provenance: Mapped[dict | None] = mapped_column(JSONB)
    #: none | pending | collected | partial | failed
    research_status: Mapped[str] = mapped_column(String(16), default="none")
    publications_collected: Mapped[int] = mapped_column(
        Integer, default=0, server_default=text("0"))
    last_job_id: Mapped[int | None] = mapped_column(
        ForeignKey("discovery_jobs.id"))
    collection_cost_usd: Mapped[float | None] = mapped_column(Float)
    #: True when this creator came from synthetic fixtures. Reaches the
    #: interface, so a demonstration is never mistaken for collected data.
    is_fixture: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now())

    videos: Mapped[list["Video"]] = relationship(back_populates="creator")
    snapshots: Mapped[list["CreatorSnapshot"]] = relationship(
        back_populates="creator")

    __table_args__ = (
        UniqueConstraint("platform", "handle", name="uq_creator_platform_handle"),
        CheckConstraint(
            "source in ('own_api','discovery','manual_lookup',"
            "'cross_reference')", name="ck_creator_source"),
        CheckConstraint(
            "research_status in ('none','pending','collected','partial',"
            "'failed')", name="ck_creator_research_status"),
        Index("ix_creators_source", "source"),
    )


class CreatorSnapshot(Base):
    """One observation of an account's own figures. Append-only.

    Exists so a growth claim can be evidenced or honestly refused. The
    product vision forbids asserting that a video caused follower growth
    without historical follower counts; where this table has no rows
    covering the period, the finding is an `inferred_breakthrough` and says
    which evidence was missing.
    """

    __tablename__ = "creator_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True)
    creator_id: Mapped[int] = mapped_column(ForeignKey("creators.id"))
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now())
    follower_count: Mapped[int | None] = mapped_column(BigInteger)
    following_count: Mapped[int | None] = mapped_column(BigInteger)
    publication_count: Mapped[int | None] = mapped_column(BigInteger)
    total_likes: Mapped[int | None] = mapped_column(BigInteger)
    source: Mapped[str] = mapped_column(String(32), default="own_api")
    #: One validated provider rounds large figures to ~3 significant digits,
    #: which blurs small changes. Recorded per reading rather than assumed.
    is_rounded: Mapped[bool] = mapped_column(Boolean, default=False)
    raw: Mapped[dict | None] = mapped_column(JSONB)

    creator: Mapped[Creator] = relationship(back_populates="snapshots")

    __table_args__ = (
        UniqueConstraint("creator_id", "captured_at",
                         name="uq_creator_snapshot"),
        Index("ix_creator_snapshots_creator", "creator_id", "captured_at"),
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
    #: Content digest of the preserved file. Stored rather than recomputed:
    #: hashing every preserved file at the start of every pass meant reading
    #: 736 MB from disk daily for information that never changes. Also what
    #: makes an orphaned file detectable.
    media_sha256: Mapped[str | None] = mapped_column(String(64))
    #: Platform-hosted cover image. The URL expires, so the dHash is what is
    #: actually relied on; the URL is kept for provenance. For TikTok this is
    #: the ONLY content signal available - the API returns no media file - so
    #: without it cross-platform matching has no visual evidence at all.
    cover_url: Mapped[str | None] = mapped_column(Text)
    cover_hash: Mapped[str | None] = mapped_column(String(32))
    cover_fetched_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True))
    #: Which discovery job produced this row. NULL for own-account
    #: publications, which come from the official APIs.
    discovery_job_id: Mapped[int | None] = mapped_column(
        ForeignKey("discovery_jobs.id"))
    thumbnail_url: Mapped[str | None] = mapped_column(Text)
    #: Music as the provider reported it: title, artist, id, whether it is
    #: original audio. A JSONB blob because the shape differs per provider
    #: and the structured feature vocabulary is Phase 6.
    music: Mapped[dict | None] = mapped_column(JSONB)
    #: Source, coverage limits and observation time for this row.
    provenance: Mapped[dict | None] = mapped_column(JSONB)
    is_fixture: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false"))
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

    # -- surface decomposition (ADR 0008, M1) ------------------------------
    #: `views` above is Instagram's ORGANIC view count: Meta documents that
    #: `views`, `likes`, `comments` and `total_interactions` "report organic
    #: interaction metrics only; interactions on ads containing a media
    #: object are not counted". The three columns here are the surfaces that
    #: sit outside it, so paid exposure can be measured rather than inferred
    #: from posting rhythm.
    #:
    #: Measured on this account 2026-10-26, exactly, on 5 of 5 reels:
    #:     crossposted_views == views + facebook_views
    #:     total_views       == crossposted_views + residual
    #: and the residual was 0 on the one probed publication that was not a
    #: boosted reel.
    #:
    #: The residual is DERIVED at read time, never stored: this table is
    #: append-only and holds what the platform returned, nothing computed.
    total_views: Mapped[int | None] = mapped_column(BigInteger)
    facebook_views: Mapped[int | None] = mapped_column(BigInteger)
    crossposted_views: Mapped[int | None] = mapped_column(BigInteger)

    raw: Mapped[dict | None] = mapped_column(JSONB)
    #: Which discovery search produced this observation. NULL means the
    #: owner's own collector did. Needed because `videos.discovery_job_id`
    #: records only the first job to see a publication, so it cannot answer
    #: "what did this search return" for a repeated search.
    discovery_job_id: Mapped[int | None] = mapped_column(
        ForeignKey("discovery_jobs.id"))

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
    #: Which third-party provider served this run, if any. NULL for the
    #: official APIs, which are free.
    provider: Mapped[str | None] = mapped_column(String(64))
    #: Actual billed cost, not an estimate (CLAUDE.md 5.3). The spending
    #: guard in server/config.py sums this for the current month, so a paid
    #: call that fails to record its cost makes the budget look emptier than
    #: it is.
    cost_usd: Mapped[float | None] = mapped_column(Float)
    error: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        CheckConstraint("status in ('running','ok','failed','deferred')",
                        name="ck_run_status"),
        Index("ix_runs_platform_started", "platform", "started_at"),
    )


class DiscoveryJob(Base):
    """One competitor search, and everything it did or could not do.

    The unit of provenance: every creator and publication a search produces
    points back here, so "where did this row come from, under what limits,
    at what cost" has one answer.
    """

    __tablename__ = "discovery_jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    #: creator | keyword | hashtag
    kind: Mapped[str] = mapped_column(String(16))
    platform: Mapped[str] = mapped_column(String(32))
    #: The normalised query, so a repeat is recognisable as the same request
    #: and does not pay twice.
    query_value: Mapped[str] = mapped_column(Text)
    requested_limit: Mapped[int] = mapped_column(
        Integer, default=50, server_default=text("50"))
    #: Exactly what was sent, so the search is reproducible from the row.
    #: A keyword search carries a niche, a cursor and a limit; reconstructing
    #: those by guesswork is how "repeat this search" silently runs a
    #: different one.
    params: Mapped[dict | None] = mapped_column(JSONB)
    provider: Mapped[str] = mapped_column(String(64))
    is_fixture: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false"))
    #: queued | running | ok | failed | refused.
    #: `refused` is its own state: a budget refusal is not a failure, it is
    #: the system working as configured.
    status: Mapped[str] = mapped_column(String(16), default="queued")
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True))
    estimated_usd: Mapped[float | None] = mapped_column(Float)
    #: Billed, not projected (CLAUDE.md 5.3).
    actual_usd: Mapped[float | None] = mapped_column(Float)
    reservation_id: Mapped[int | None] = mapped_column(
        ForeignKey("budget_reservations.id"))
    creators_seen: Mapped[int] = mapped_column(
        Integer, default=0, server_default=text("0"))
    creators_new: Mapped[int] = mapped_column(
        Integer, default=0, server_default=text("0"))
    publications_seen: Mapped[int] = mapped_column(
        Integer, default=0, server_default=text("0"))
    publications_new: Mapped[int] = mapped_column(
        Integer, default=0, server_default=text("0"))
    snapshots_written: Mapped[int] = mapped_column(
        Integer, default=0, server_default=text("0"))
    #: What the source could NOT see. A partial result silently treated as
    #: complete is the most dangerous kind of data in this system.
    coverage_notes: Mapped[dict | None] = mapped_column(JSONB)
    #: Per-item provider failures, kept rather than dropped: a private
    #: account must not look like an empty one.
    item_errors: Mapped[dict | None] = mapped_column(JSONB)
    provenance: Mapped[dict | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        CheckConstraint("kind in ('creator','keyword','hashtag')",
                        name="ck_discovery_job_kind"),
        CheckConstraint(
            "status in ('queued','running','ok','failed','refused')",
            name="ck_discovery_job_status"),
        Index("ix_discovery_jobs_lookup",
              "platform", "kind", "query_value", "started_at"),
    )


class CreatorIdentity(Base):
    """One person, who may hold several platform accounts.

    Sits ABOVE `creators` rather than replacing it: every collector depends
    on `creators` being one account on one platform, and the product vision
    needs one row per person. Both are true at once this way.
    """

    __tablename__ = "creator_identities"

    id: Mapped[int] = mapped_column(primary_key=True)
    display_name: Mapped[str] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)
    #: inferred | confirmed
    status: Mapped[str] = mapped_column(String(16), default="inferred")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now())

    members: Mapped[list["CreatorIdentityMember"]] = relationship(
        back_populates="identity")

    __table_args__ = (
        CheckConstraint("status in ('inferred','confirmed')",
                        name="ck_creator_identity_status"),
    )


class CreatorIdentityMember(Base):
    """One account tied to one identity, with the evidence for it.

    `evidence_kind` is a closed set that deliberately omits "same handle".
    A coincidental name match is not evidence, and leaving it unrepresentable
    is stronger than documenting that it should not be used.
    """

    __tablename__ = "creator_identity_members"

    id: Mapped[int] = mapped_column(primary_key=True)
    identity_id: Mapped[int] = mapped_column(
        ForeignKey("creator_identities.id"))
    creator_id: Mapped[int] = mapped_column(ForeignKey("creators.id"))
    #: bio_link | cross_posted_content | name_and_content | manual |
    #: self_declared
    evidence_kind: Mapped[str] = mapped_column(String(32))
    evidence: Mapped[dict | None] = mapped_column(JSONB)
    confidence: Mapped[float | None] = mapped_column(Float)
    decided_by: Mapped[str | None] = mapped_column(String(16))
    decided_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now())

    identity: Mapped[CreatorIdentity] = relationship(
        back_populates="members")

    __table_args__ = (
        UniqueConstraint("creator_id", name="uq_identity_member_creator"),
        CheckConstraint(
            "evidence_kind in ('bio_link','cross_posted_content',"
            "'name_and_content','manual','self_declared')",
            name="ck_identity_evidence_kind"),
        CheckConstraint("decided_by is null or "
                        "decided_by in ('human','matcher')",
                        name="ck_identity_decided_by"),
        Index("ix_identity_members_identity", "identity_id"),
    )


class BreakthroughCandidate(Base):
    """Where a creator's trajectory may have changed.

    The classification is the whole point, and the three states are not
    synonyms:

      * `observed_outlier` - this publication beat the creator's own
        baseline. A fact about one post, nothing more.
      * `inferred_breakthrough` - the trajectory appears to shift here, but
        the longitudinal evidence to establish it does not exist.
        `missing_evidence` records exactly what was absent.
      * `verified_trajectory_change` - repeated observations over time show
        a sustained change. Requires real history, which for a
        newly-discovered creator does not exist yet.

    The product vision forbids claiming a video caused growth without
    historical follower counts or age-normalised views. Defaulting to
    `inferred_breakthrough` and naming the gap is how that rule is kept.
    """

    __tablename__ = "breakthrough_candidates"

    id: Mapped[int] = mapped_column(primary_key=True)
    creator_id: Mapped[int] = mapped_column(ForeignKey("creators.id"))
    #: Nullable: a gradual regime change has no single video.
    video_id: Mapped[int | None] = mapped_column(ForeignKey("videos.id"))
    classification: Mapped[str] = mapped_column(String(32))
    detected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now())
    method_version: Mapped[str] = mapped_column(String(32))
    publication_index: Mapped[int | None] = mapped_column(Integer)
    baseline_views: Mapped[float | None] = mapped_column(Float)
    #: Median absolute deviation, not standard deviation: robust to the
    #: outliers this is looking for.
    baseline_mad: Mapped[float | None] = mapped_column(Float)
    observed_views: Mapped[int | None] = mapped_column(BigInteger)
    relative_performance: Mapped[float | None] = mapped_column(Float)
    robust_z: Mapped[float | None] = mapped_column(Float)
    window_before: Mapped[int | None] = mapped_column(Integer)
    window_after: Mapped[int | None] = mapped_column(Integer)
    median_after: Mapped[float | None] = mapped_column(Float)
    sustained: Mapped[bool | None] = mapped_column(Boolean)
    confidence: Mapped[float | None] = mapped_column(Float)
    evidence: Mapped[dict | None] = mapped_column(JSONB)
    #: Required when the classification is `inferred_breakthrough`, so
    #: nobody later reads it as established.
    missing_evidence: Mapped[dict | None] = mapped_column(JSONB)
    decided_by: Mapped[str | None] = mapped_column(String(16))
    decided_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True))
    notes: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        UniqueConstraint("creator_id", "video_id", "method_version",
                         name="uq_breakthrough_candidate"),
        CheckConstraint(
            "classification in ('observed_outlier','inferred_breakthrough',"
            "'verified_trajectory_change','rejected')",
            name="ck_breakthrough_classification"),
        CheckConstraint("decided_by is null or "
                        "decided_by in ('human','matcher')",
                        name="ck_breakthrough_decided_by"),
        Index("ix_breakthrough_creator", "creator_id", "classification"),
    )


class BudgetReservation(Base):
    """One claim on the monthly provider budget.

    Taken BEFORE a paid call and settled after, so the budget is enforced
    rather than merely observed. See migration c3e8b47d105a for the four
    failure modes this replaces.

    The state machine is deliberately small:

        reserved ──settle(actual)──> settled     the call happened, cost known
                 ──release(reason)─> released    the call never happened
                 ──(timeout)───────> expired     outcome unknown

    `expired` still counts against the budget. That is the conservative
    direction on purpose: a reservation whose outcome we do not know may
    well have been billed, and assuming it was not is the assumption that
    overspends. It stays visible until a human settles or releases it.
    """

    __tablename__ = "budget_reservations"

    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(String(64))
    #: discovery | cloud_vision | cloud_audio | strategy. Part of the budget
    #: grain, because "I will pay for discovery but not for frame analysis"
    #: is the control the owner actually needs and a single cap cannot
    #: express it.
    category: Mapped[str] = mapped_column(
        String(32), default="discovery", server_default="discovery")
    #: The caller's identifier for this unit of work. A retry presenting the
    #: same key gets the original reservation rather than a second charge.
    idempotency_key: Mapped[str] = mapped_column(String(200))
    estimated_usd: Mapped[float] = mapped_column(Float)
    actual_usd: Mapped[float | None] = mapped_column(Float)
    #: reserved | settled | released | expired
    status: Mapped[str] = mapped_column(String(16), default="reserved")
    #: "YYYY-MM". Stored rather than derived so settling after a month
    #: boundary cannot move the charge into the wrong period.
    period: Mapped[str] = mapped_column(String(7))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    settled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True))
    run_id: Mapped[int | None] = mapped_column(
        ForeignKey("collection_runs.id"))
    notes: Mapped[str | None] = mapped_column(Text)
    detail: Mapped[dict | None] = mapped_column(JSONB)

    __table_args__ = (
        UniqueConstraint("provider", "idempotency_key",
                         name="uq_budget_reservation_key"),
        CheckConstraint(
            "status in ('reserved','settled','released','expired')",
            name="ck_budget_reservation_status"),
        CheckConstraint("estimated_usd >= 0",
                        name="ck_budget_reservation_estimate"),
        CheckConstraint("actual_usd is null or actual_usd >= 0",
                        name="ck_budget_reservation_actual"),
        CheckConstraint("status <> 'settled' or actual_usd is not null",
                        name="ck_budget_reservation_settled_has_actual"),
        Index("ix_budget_reservations_period",
              "provider", "period", "status"),
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


class CreatorCollection(Base):
    """A named group of creators, revisitable and annotatable.

    Not organisation for its own sake. `docs/SCHEMA_PLAN.md` 3 makes a
    control cohort of non-breakthrough creators in the same niche a
    prerequisite for promoting any pattern above `hypothesis`, and a cohort
    has to be an explicit, stable set of accounts first. `purpose`
    distinguishes a group assembled to copy formats from one assembled as a
    statistical control, because a later analysis must be able to tell.

    Carries no performance figures. Every number shown for a collection is
    derived from the observations at read time, so changing an analytical
    policy cannot corrupt a saved group.
    """

    __tablename__ = "creator_collections"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    #: research | control_cohort | watchlist | inspiration
    purpose: Mapped[str] = mapped_column(
        String(32), default="research", server_default="research")
    niche: Mapped[str | None] = mapped_column(String(64))
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True))

    members: Mapped[list["CreatorCollectionMember"]] = relationship(
        back_populates="collection", cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint("name", name="uq_collection_name"),
        CheckConstraint(
            "purpose in ('research','control_cohort','watchlist',"
            "'inspiration')", name="ck_collection_purpose"),
    )


class CreatorCollectionMember(Base):
    """One creator's membership of one collection."""

    __tablename__ = "creator_collection_members"

    id: Mapped[int] = mapped_column(primary_key=True)
    collection_id: Mapped[int] = mapped_column(
        ForeignKey("creator_collections.id", ondelete="CASCADE"))
    creator_id: Mapped[int] = mapped_column(
        ForeignKey("creators.id", ondelete="CASCADE"))
    notes: Mapped[str | None] = mapped_column(Text)
    #: A one-line reason, kept separate from `notes` so it stays scannable.
    reason: Mapped[str | None] = mapped_column(String(200))
    added_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now())
    discovered_by_job_id: Mapped[int | None] = mapped_column(
        ForeignKey("discovery_jobs.id"))

    collection: Mapped[CreatorCollection] = relationship(
        back_populates="members")

    __table_args__ = (
        UniqueConstraint("collection_id", "creator_id",
                         name="uq_collection_member"),
        Index("ix_collection_members_creator", "creator_id"),
    )


class VideoFeatureRun(Base):
    """One extraction pass, so a partial run is visible rather than inferred."""

    __tablename__ = "video_feature_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    method_version: Mapped[str] = mapped_column(String(64))
    #: hook_deterministic | hook_speech | ...
    kind: Mapped[str] = mapped_column(String(32))
    #: running | ok | partial | failed
    status: Mapped[str] = mapped_column(
        String(16), default="running", server_default="running")
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True))
    videos_requested: Mapped[int] = mapped_column(
        Integer, default=0, server_default=text("0"))
    videos_done: Mapped[int] = mapped_column(
        Integer, default=0, server_default=text("0"))
    videos_failed: Mapped[int] = mapped_column(
        Integer, default=0, server_default=text("0"))
    features_written: Mapped[int] = mapped_column(
        Integer, default=0, server_default=text("0"))
    compute_seconds: Mapped[float | None] = mapped_column(Float)
    params: Mapped[dict | None] = mapped_column(JSONB)
    notes: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        CheckConstraint("status in ('running','ok','partial','failed')",
                        name="ck_feature_run_status"),
    )


class VideoFeature(Base):
    """One measured or inferred property of one publication.

    The unique key is (video_id, feature, method_version). Reprocessing
    with a new version writes NEW rows beside the old ones, so a threshold
    change or a model swap can never silently rewrite what was previously
    measured - and the two can be compared.

    `method` keeps the kinds apart: `deterministic` is signal processing on
    the file, `proxy` is a threshold on such a measurement, `model` is a
    model's answer, `human` is a correction, `unavailable` records that
    this pass could not produce the feature and why. Nothing averages
    across them.
    """

    __tablename__ = "video_features"

    id: Mapped[int] = mapped_column(primary_key=True)
    video_id: Mapped[int] = mapped_column(
        ForeignKey("videos.id", ondelete="CASCADE"))
    #: The exact bytes measured. A re-download can change the file under a
    #: publication, and a feature from different bytes is a different
    #: measurement.
    media_sha256: Mapped[str | None] = mapped_column(String(64))
    media_path: Mapped[str | None] = mapped_column(Text)
    feature: Mapped[str] = mapped_column(String(64))
    #: Exactly one of these carries the value, so a number is never
    #: stringified and a string never coerced to a number.
    value_num: Mapped[float | None] = mapped_column(Float)
    value_bool: Mapped[bool | None] = mapped_column(Boolean)
    value_text: Mapped[str | None] = mapped_column(Text)
    value_json: Mapped[dict | None] = mapped_column(JSONB)
    unit: Mapped[str | None] = mapped_column(String(32))
    method: Mapped[str] = mapped_column(String(16))
    method_version: Mapped[str] = mapped_column(String(64))
    model: Mapped[str | None] = mapped_column(String(128))
    uncertainty: Mapped[float | None] = mapped_column(Float)
    uncertainty_kind: Mapped[str | None] = mapped_column(String(24))
    interval_start_s: Mapped[float | None] = mapped_column(Float)
    interval_end_s: Mapped[float | None] = mapped_column(Float)
    params: Mapped[dict | None] = mapped_column(JSONB)
    note: Mapped[str | None] = mapped_column(Text)
    unavailable_reason: Mapped[str | None] = mapped_column(Text)
    extracted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now())
    run_id: Mapped[int | None] = mapped_column(
        ForeignKey("video_feature_runs.id"))
    #: ok | failed | superseded
    status: Mapped[str] = mapped_column(
        String(16), default="ok", server_default="ok")
    error: Mapped[str | None] = mapped_column(Text)

    #: A human correction sits BESIDE the machine value, never over it.
    #: Erasing the original would make it impossible to ever measure how
    #: often the machine was wrong, which is the thing corrections exist to
    #: reveal.
    corrected_value_num: Mapped[float | None] = mapped_column(Float)
    corrected_value_bool: Mapped[bool | None] = mapped_column(Boolean)
    corrected_value_text: Mapped[str | None] = mapped_column(Text)
    corrected_by: Mapped[str | None] = mapped_column(String(32))
    corrected_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True))
    correction_note: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        CheckConstraint(
            "method in ('deterministic','proxy','model','human',"
            "'unavailable')", name="ck_feature_method"),
        CheckConstraint("status in ('ok','failed','superseded')",
                        name="ck_feature_status"),
        UniqueConstraint("video_id", "feature", "method_version",
                         name="uq_video_feature_version"),
        Index("ix_video_features_video", "video_id", "method_version"),
        Index("ix_video_features_feature", "feature", "method_version"),
        Index("ix_video_features_corrected", "corrected_at"),
    )

    @property
    def value(self):
        """The machine's value, whichever column holds it."""
        for v in (self.value_num, self.value_bool, self.value_text,
                  self.value_json):
            if v is not None:
                return v
        return None

    @property
    def effective_value(self):
        """What analysis should use: the correction if one exists."""
        for v in (self.corrected_value_num, self.corrected_value_bool,
                  self.corrected_value_text):
            if v is not None:
                return v
        return self.value

    @property
    def is_corrected(self) -> bool:
        return self.corrected_at is not None


class AiOperation(Base):
    """One AI analysis call: what it did, what it cost, whether it validated.

    Separate from `budget_reservations`, which is about money held. This is
    about work performed, and the two have different lifetimes: a
    reservation is released or settled within hours, while the record of
    what a model said about a video is evidence that outlives it.
    """

    __tablename__ = "ai_operations"

    id: Mapped[int] = mapped_column(primary_key=True)
    video_id: Mapped[int | None] = mapped_column(
        ForeignKey("videos.id", ondelete="CASCADE"))
    task: Mapped[str] = mapped_column(String(64))
    provider: Mapped[str] = mapped_column(String(64))
    model: Mapped[str] = mapped_column(String(128))
    #: local | cloud. The single most important field for the interface:
    #: the owner must always be able to see whether their media left the
    #: machine.
    hosting: Mapped[str] = mapped_column(String(8))
    category: Mapped[str] = mapped_column(
        String(32), default="cloud_vision", server_default="cloud_vision")
    mode: Mapped[str | None] = mapped_column(String(16))
    #: ok | refused | failed | invalid
    status: Mapped[str] = mapped_column(
        String(16), default="ok", server_default="ok")
    schema_valid: Mapped[bool | None] = mapped_column(Boolean)
    validation_errors: Mapped[list | None] = mapped_column(JSON)
    refused_reason: Mapped[str | None] = mapped_column(Text)
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    images: Mapped[int | None] = mapped_column(Integer)
    estimated_usd: Mapped[float | None] = mapped_column(Float)
    #: Billed, never an estimate promoted (CLAUDE.md 5.3).
    actual_usd: Mapped[float | None] = mapped_column(Float)
    latency_s: Mapped[float | None] = mapped_column(Float)
    peak_vram_mib: Mapped[int | None] = mapped_column(Integer)
    reservation_id: Mapped[int | None] = mapped_column(
        ForeignKey("budget_reservations.id"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        CheckConstraint("hosting in ('local','cloud')",
                        name="ck_ai_operation_hosting"),
        CheckConstraint("status in ('ok','refused','failed','invalid')",
                        name="ck_ai_operation_status"),
        Index("ix_ai_operations_video", "video_id", "task"),
        Index("ix_ai_operations_provider", "provider", "created_at"),
    )


class VisionRequest(Base):
    """Someone asked for a video's opening to be analysed visually.

    The server cannot do it. The local vision model runs on the Windows
    workstation, which is only sometimes switched on, so the interface
    records the intent here and the worker drains it on its next run.

    This is intentionally not a leased job. The worker writes its results
    keyed on (video, feature, method_version), so running the same request
    twice overwrites rather than duplicates, and a crashed worker loses
    nothing but the `running` marker - which `requeue_stale` clears. What
    this table buys is an honest queue the interface can show, instead of a
    button that appears to do nothing because a machine in another room is
    asleep.
    """

    __tablename__ = "vision_requests"

    id: Mapped[int] = mapped_column(primary_key=True)
    video_id: Mapped[int] = mapped_column(
        ForeignKey("videos.id", ondelete="CASCADE"))
    task: Mapped[str] = mapped_column(String(64))
    method_version: Mapped[str] = mapped_column(String(64))
    #: queued | running | done | failed | cancelled
    status: Mapped[str] = mapped_column(
        String(16), default="queued", server_default="queued")
    #: Pinned at request time. A request made while only a local model was
    #: authorised must not turn into a paid call if a cloud key appears
    #: later.
    mode: Mapped[str] = mapped_column(
        String(16), default="local_only", server_default="local_only")
    requested_by: Mapped[str | None] = mapped_column(String(64))
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True))
    claimed_by: Mapped[str | None] = mapped_column(String(64))
    attempts: Mapped[int] = mapped_column(
        Integer, default=0, server_default=text("0"))
    #: Re-analyse a video that already has features under this version.
    force: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false"))
    error: Mapped[str | None] = mapped_column(Text)
    params: Mapped[dict | None] = mapped_column(JSONB)

    __table_args__ = (
        CheckConstraint(
            "status in ('queued','running','done','failed','cancelled')",
            name="ck_vision_request_status"),
        CheckConstraint("attempts >= 0 and attempts <= 10",
                        name="ck_vision_request_attempts"),
        Index("uq_vision_request_open", "video_id", "task", "method_version",
              unique=True,
              postgresql_where=text("status in ('queued','running')")),
        Index("ix_vision_requests_status", "status", "requested_at"),
    )


# --------------------------------------------------------------------------
# measurement integrity — M1 (ADR 0008)
# --------------------------------------------------------------------------

class AdExposure(Base):
    """Paid delivery of one ad against one organic publication, per day.

    Track B of ADR 0008. The join back to content is Meta's
    `adcreative.effective_instagram_media_id`, documented as "the ID of an
    Instagram post to use in an ad" — so an ad made by boosting an existing
    post carries the organic media id, and that is the only link between the
    two halves of the account.

    Nothing writes here until the owner's token carries `ads_read`. Measured
    2026-10-26, the stored Page token carries `pages_show_list`,
    `business_management`, `instagram_basic`, `instagram_manage_insights`,
    `pages_read_engagement` and `public_profile`, and `/me/adaccounts` is not
    even an edge of a Page token. The table exists ahead of the data so the
    covariate has somewhere to land the day the scope is granted.
    """

    __tablename__ = "ad_exposures"

    id: Mapped[int] = mapped_column(primary_key=True)
    #: NULL while an ad's creative references a media id never collected.
    #: The row is kept rather than dropped: an unmatched ad is evidence
    #: about coverage, and discarding it would hide spend.
    video_id: Mapped[int | None] = mapped_column(
        ForeignKey("videos.id", ondelete="SET NULL"))
    platform: Mapped[str] = mapped_column(String(32), default="instagram")
    ad_account_id: Mapped[str] = mapped_column(String(64))
    ad_id: Mapped[str] = mapped_column(String(64))
    adset_id: Mapped[str | None] = mapped_column(String(64))
    campaign_id: Mapped[str | None] = mapped_column(String(64))
    #: The creative's `effective_instagram_media_id`, kept verbatim even when
    #: `video_id` resolved, so the join can be re-audited without re-calling
    #: the API.
    effective_instagram_media_id: Mapped[str | None] = mapped_column(
        String(64))

    #: Ads Insights is a time series; one row is one reporting day.
    date_start: Mapped[datetime] = mapped_column(Date)
    date_stop: Mapped[datetime] = mapped_column(Date)

    spend: Mapped[float | None] = mapped_column(Numeric(12, 4))
    #: Meta reports spend in the ad account's currency, which is not
    #: necessarily USD. Stored rather than converted: an unrecorded currency
    #: becomes a silent unit error the first time someone sums the column.
    currency: Mapped[str | None] = mapped_column(String(8))
    impressions: Mapped[int | None] = mapped_column(BigInteger)
    reach: Mapped[int | None] = mapped_column(BigInteger)
    #: The watch-time distribution. These are the paid-side equivalent of a
    #: retention curve and the only route to an organic/paid retention
    #: comparison, because `reels_skip_rate` has no documented ad
    #: counterpart.
    video_p25: Mapped[int | None] = mapped_column(BigInteger)
    video_p50: Mapped[int | None] = mapped_column(BigInteger)
    video_p75: Mapped[int | None] = mapped_column(BigInteger)
    video_p95: Mapped[int | None] = mapped_column(BigInteger)
    video_p100: Mapped[int | None] = mapped_column(BigInteger)
    video_avg_time_watched_s: Mapped[float | None] = mapped_column(Float)

    raw: Mapped[dict | None] = mapped_column(JSONB)
    method_version: Mapped[str] = mapped_column(String(64))
    collected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now())

    #: `ads:{ad_id}:{date_start}:{method_version}` (CLAUDE.md §4.1). Ads
    #: Insights figures are revised for days after the fact, so collecting
    #: the same day twice must UPDATE this row, never append a second one.
    idempotency_key: Mapped[str] = mapped_column(String(160))

    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_ad_exposure_idem"),
        Index("ix_ad_exposures_video", "video_id", "date_start"),
        Index("ix_ad_exposures_media", "effective_instagram_media_id"),
    )


class AnalyticsImport(Base):
    """One owner-supplied analytics export, as a versioned artefact.

    The only route to TikTok retention that exists. TikTok's Display API
    returns views, likes, comments and shares and nothing about watch time
    (ADR 0001), so average watch time and "watched full video" can only come
    out of TikTok Studio by hand.

    An import is immutable once recorded. Re-importing the same file is a
    no-op by `file_sha256`; importing a *newer* export covering the same
    period creates a new row, and readers take the latest import per metric
    rather than merging the two — a later export supersedes an earlier one,
    it does not average with it.
    """

    __tablename__ = "analytics_imports"

    id: Mapped[int] = mapped_column(primary_key=True)
    platform: Mapped[str] = mapped_column(String(32))
    #: tiktok_studio | instagram_insights
    source: Mapped[str] = mapped_column(String(32))
    filename: Mapped[str] = mapped_column(Text)
    #: Idempotency (CLAUDE.md §4.8). The same bytes may be submitted twice.
    file_sha256: Mapped[str] = mapped_column(String(64))
    #: The date the OWNER exported the file, which is when the figures were
    #: true. Not `imported_at`: a file exported in March and imported in
    #: October describes March, and conflating the two would age-anchor every
    #: row to the wrong date (CLAUDE.md §3.8a).
    exported_at: Mapped[datetime | None] = mapped_column(Date)
    imported_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now())
    row_count: Mapped[int] = mapped_column(
        Integer, default=0, server_default=text("0"))
    matched_count: Mapped[int] = mapped_column(
        Integer, default=0, server_default=text("0"))
    method_version: Mapped[str] = mapped_column(String(64))
    #: The column mapping actually used, plus anything the parser had to
    #: guess. A later reader must be able to tell a parsed column from an
    #: inferred one without reopening the original file.
    parse_notes: Mapped[dict | None] = mapped_column(JSONB)

    metrics: Mapped[list["ImportedMetric"]] = relationship(
        back_populates="analytics_import")

    __table_args__ = (
        UniqueConstraint("file_sha256", name="uq_analytics_import_sha"),
        CheckConstraint("source in ('tiktok_studio','instagram_insights')",
                        name="ck_analytics_import_source"),
        Index("ix_analytics_imports_platform", "platform", "exported_at"),
    )


class ImportedMetric(Base):
    """One metric for one publication, from a manual export.

    Kept apart from `metrics_snapshots` on purpose. A snapshot is something
    this system observed through an API at a known instant; an imported
    metric is something a person exported from a dashboard, at a resolution
    the dashboard chose, under a definition the platform does not publish.
    Merging them would leave a `provenance` string as the only thing between
    a measured number and a transcribed one, and that is too thin.
    """

    __tablename__ = "imported_metrics"

    id: Mapped[int] = mapped_column(primary_key=True)
    import_id: Mapped[int] = mapped_column(
        ForeignKey("analytics_imports.id", ondelete="CASCADE"))
    #: NULL when the export names a publication this system has not
    #: collected. Kept, not dropped, for the same reason as above.
    video_id: Mapped[int | None] = mapped_column(
        ForeignKey("videos.id", ondelete="SET NULL"))
    platform: Mapped[str] = mapped_column(String(32))
    #: Whatever the export used to name the publication: a share URL, a post
    #: id, sometimes only a caption. Stored verbatim so an unmatched row can
    #: be matched later without re-importing the file.
    external_ref: Mapped[str] = mapped_column(Text)
    metric: Mapped[str] = mapped_column(String(64))
    value: Mapped[float | None] = mapped_column(Float)
    #: Units are explicit because TikTok Studio exports seconds, Instagram
    #: milliseconds, and both export bare percentages. A unitless number here
    #: would be a unit bug waiting for a reader.
    unit: Mapped[str | None] = mapped_column(String(16))
    #: When the figure was true, per the export. Carries the same role as
    #: `captured_at` on a snapshot and is what age anchoring reads.
    observed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True))
    #: Age of the publication at `observed_at`, in days, where both are
    #: known. Stored because an export's own age basis is often the only
    #: thing that makes its numbers comparable, and recomputing it later
    #: needs a `posted_at` that unmatched rows do not have.
    observed_age_days: Mapped[float | None] = mapped_column(Float)
    provenance: Mapped[str] = mapped_column(
        String(32), default="manual_export", server_default="manual_export")
    raw: Mapped[dict | None] = mapped_column(JSONB)

    analytics_import: Mapped[AnalyticsImport] = relationship(
        back_populates="metrics")

    __table_args__ = (
        UniqueConstraint("import_id", "external_ref", "metric",
                         name="uq_imported_metric"),
        CheckConstraint("provenance = 'manual_export'",
                        name="ck_imported_metric_provenance"),
        Index("ix_imported_metrics_video", "video_id", "metric"),
    )
