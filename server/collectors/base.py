"""DataSourceAdapter — the seam every collector sits behind.

Platform APIs and third-party providers change on months-long cycles (three
claims in this plan were already wrong once), so swapping a source must never
require touching the schema or anything downstream. Adapters normalise into
`NormalizedVideo` / `NormalizedMetrics` and nothing else.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterator

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from server.db.models import CollectionRun, Creator, MetricsSnapshot, Video, utcnow

logger = logging.getLogger("virallab.collectors")


@dataclass(slots=True)
class NormalizedVideo:
    """Platform-agnostic video metadata."""

    external_id: str
    platform: str
    caption: str | None = None
    hashtags: list[str] = field(default_factory=list)
    #: BLOCKING. Without an absolute post time, video age is unknown and
    #: fixed-age comparison is impossible (plan 5.4).
    posted_at: datetime | None = None
    duration_s: float | None = None
    #: Downloadable media URL, if the source provides one at all. TikTok's
    #: Display API does NOT (ADR 0001) — own TikTok media comes from the
    #: local master archive.
    media_url: str | None = None
    #: Instagram CDN URLs expire; download on collection or lose it.
    media_url_expires: bool = False
    raw: dict = field(default_factory=dict)


@dataclass(slots=True)
class NormalizedMetrics:
    """One reading at one moment. Append-only; never mutated."""

    external_id: str
    captured_at: datetime
    views: int | None = None
    likes: int | None = None
    comments: int | None = None
    shares: int | None = None
    saves: int | None = None
    #: True when the source reports a rounded/abbreviated figure rather than
    #: an exact count (Phase 0A finding 2). Recorded so precision loss is
    #: never silently assumed away.
    is_rounded: bool = False
    raw: dict = field(default_factory=dict)


class DataSourceAdapter(ABC):
    """One source of videos and metrics."""

    platform: str
    #: True for official first-party APIs. False for third-party providers,
    #: which carry the operating-policy constraints in plan 2.3.
    official: bool = True

    @abstractmethod
    def fetch_account_videos(self, account_ref: str, *, since: datetime | None
                             = None) -> Iterator[NormalizedVideo]:
        """Videos for one account the owner controls."""

    @abstractmethod
    def fetch_metrics(self, account_ref: str, external_ids: list[str],
                      ) -> Iterator[NormalizedMetrics]:
        """Current metrics for the given videos."""

    def fetch_niche_videos(self, query: str, *, limit: int = 100
                           ) -> Iterator[NormalizedVideo]:
        """Discovery. Not available on official APIs (plan 2.2)."""
        raise NotImplementedError(
            f"{type(self).__name__} does not support niche discovery")


# ---------------------------------------------------------------------------
# persistence — shared by every adapter so normalisation stays in one place
# ---------------------------------------------------------------------------

def upsert_creator(session: Session, *, platform: str, handle: str,
                   external_id: str | None = None,
                   follower_count: int | None = None,
                   is_own_account: bool = False) -> Creator:
    # Handles are mutable: a creator who renames would otherwise be recorded
    # as a second creator, splitting their history and corrupting the
    # per-creator baseline that the whole relative-performance target rests
    # on. Prefer the platform's stable id whenever we have one.
    row = None
    if external_id:
        row = session.scalar(select(Creator).where(
            Creator.platform == platform,
            Creator.external_id == external_id))
    if row is None:
        row = session.scalar(select(Creator).where(
            Creator.platform == platform, Creator.handle == handle))
    if row is None:
        row = Creator(platform=platform, handle=handle)
        session.add(row)
    if external_id:
        row.external_id = external_id
    if row.handle != handle:
        logger.info("creator %s renamed %s -> %s (matched on stable id)",
                    platform, row.handle, handle)
        row.handle = handle
    if follower_count is not None:
        row.follower_count = follower_count
    if is_own_account:
        row.is_own_account = True
    session.flush()
    return row


def upsert_video(session: Session, creator: Creator, v: NormalizedVideo, *,
                 source: str) -> tuple[Video, bool]:
    """Insert or update a video. Returns (row, created).

    Idempotent on (platform, external_id), so re-running a collection never
    duplicates rows — required by the Phase 3 acceptance criterion and
    relied on by the daily schedule.
    """
    row = session.scalar(select(Video).where(
        Video.platform == v.platform, Video.external_id == v.external_id))
    created = row is None
    if created:
        row = Video(creator_id=creator.id, platform=v.platform,
                    external_id=v.external_id, source=source)
        session.add(row)
    if v.caption is not None:
        row.caption = v.caption
    if v.hashtags:
        row.hashtags = v.hashtags
    if v.posted_at is not None:
        row.posted_at = v.posted_at
    if v.duration_s is not None:
        row.duration_s = v.duration_s
    session.flush()
    return row, created


def record_snapshot(session: Session, video: Video, m: NormalizedMetrics
                    ) -> MetricsSnapshot:
    """Append one reading. Raw snapshots are never mutated — fixed-age
    anchors are a read-time derivation (plan 5.4)."""
    snap = MetricsSnapshot(
        video_id=video.id, captured_at=m.captured_at, views=m.views,
        likes=m.likes, comments=m.comments, shares=m.shares, saves=m.saves,
        is_rounded=m.is_rounded, raw=m.raw or None)
    session.add(snap)
    session.flush()
    return snap


def start_run(session: Session, *, platform: str, account_ref: str,
              kind: str) -> CollectionRun:
    run = CollectionRun(platform=platform, account_ref=account_ref, kind=kind)
    session.add(run)
    session.flush()
    return run


def finish_run(session: Session, run: CollectionRun, *, status: str,
               error: str | None = None, **counts: int) -> CollectionRun:
    run.status = status
    run.finished_at = utcnow()
    run.error = error[:2000] if error else None
    for k, val in counts.items():
        if hasattr(run, k):
            setattr(run, k, val)
    session.flush()
    logger.info("collection run %s %s/%s -> %s (%s)", run.id, platform_of(run),
                run.account_ref, status, counts)
    return run


def platform_of(run: CollectionRun) -> str:
    return run.platform
