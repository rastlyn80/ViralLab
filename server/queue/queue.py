"""Postgres-backed batch queue.

One Postgres instance serves data and queue (plan 4.1): atomic dequeue via
SELECT ... FOR UPDATE SKIP LOCKED, durability and resumability for free, and
no second always-on stateful service on an 8-16 GB box.

Guarantees, all exercised by tests/server/test_queue.py:

  * atomic dequeue        - two concurrent workers never get the same batch
  * lease ownership       - a stale lease_token cannot write anything
  * heartbeat             - a slow-but-alive batch is never reaped
  * bounded retries       - attempts exhaust into terminal 'failed'
  * per-video idempotency - a replayed batch yields exactly one row per video
  * partial credit        - an interrupted batch resumes on the remainder only
"""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from server.db.models import (
    AnalysisBatch, AnalysisResult, BatchVideo, Video, WorkerHeartbeat, utcnow,
)
from shared.schemas.jobs import (
    BatchLease, Heartbeat, Modality, ResultSubmission, Tier, VideoRef,
    WorkerStatus, make_idempotency_key,
)

DEFAULT_LEASE_SECONDS = 900
DEFAULT_MAX_ATTEMPTS = 3
BACKOFF_BASE_SECONDS = 60
#: A video that fails this many times inside batches is quarantined, so one
#: pathological file cannot fail its batch forever (ADR 0002).
VIDEO_QUARANTINE_AFTER = 3
#: Video states a lease will pick up. 'failed' must be included or a video
#: that failed once would never be retried, while _finalise would still count
#: it as outstanding work - requeueing the batch forever with no progress.
RETRYABLE_VIDEO_STATES = ("pending", "failed")


class LeaseError(RuntimeError):
    """Raised when a caller presents a lease token that is not current.

    The API layer maps this to HTTP 409. This is the check that stops a
    reaped zombie worker from overwriting fresher results.
    """


# ---------------------------------------------------------------------------
# enqueue
# ---------------------------------------------------------------------------

def enqueue_batch(session: Session, *, modality: Modality, tier: Tier,
                  method_version: str, video_ids: list[int],
                  frames: int = 4,
                  max_attempts: int = DEFAULT_MAX_ATTEMPTS) -> AnalysisBatch:
    """Create a pending batch. Videos whose result already exists for this
    (modality, method_version) are skipped, so re-enqueueing after a partial
    run costs only the remainder."""
    if not video_ids:
        raise ValueError("cannot enqueue an empty batch")

    keys = {vid: make_idempotency_key(modality, vid, method_version)
            for vid in video_ids}
    existing = set(session.scalars(
        select(AnalysisResult.idempotency_key).where(
            AnalysisResult.idempotency_key.in_(list(keys.values())),
            AnalysisResult.error.is_(None),
        )).all())
    todo = [vid for vid, k in keys.items() if k not in existing]
    if not todo:
        raise ValueError("all videos already have results for this method")

    batch = AnalysisBatch(
        modality=str(modality), tier=str(tier),
        method_version=method_version, status="pending",
        max_attempts=max_attempts,
    )
    session.add(batch)
    session.flush()
    session.add_all([BatchVideo(batch_id=batch.id, video_id=vid, frames=frames)
                     for vid in todo])
    session.flush()
    return batch


# ---------------------------------------------------------------------------
# dequeue
# ---------------------------------------------------------------------------

def lease_batch(session: Session, *, worker_id: str,
                capabilities: list[Modality],
                max_videos: int = 100,
                lease_seconds: int = DEFAULT_LEASE_SECONDS,
                media_url_builder=None) -> BatchLease | None:
    """Atomically lease the oldest eligible pending batch, or return None.

    SKIP LOCKED is what makes this safe under concurrency: a row already
    locked by another transaction is passed over rather than waited on.
    """
    if not capabilities:
        return None
    now = utcnow()

    row = session.execute(
        select(AnalysisBatch)
        .where(
            AnalysisBatch.status == "pending",
            AnalysisBatch.modality.in_([str(c) for c in capabilities]),
            (AnalysisBatch.not_before.is_(None))
            | (AnalysisBatch.not_before <= now),
        )
        .order_by(AnalysisBatch.created_at)
        .limit(1)
        .with_for_update(skip_locked=True)
    ).scalar_one_or_none()
    if row is None:
        return None

    token = secrets.token_urlsafe(32)
    row.status = "leased"
    row.lease_token = token
    row.leased_by = worker_id
    row.leased_at = now
    row.lease_expires_at = now + timedelta(seconds=lease_seconds)
    row.last_heartbeat_at = now
    row.attempt_count += 1

    pending = session.scalars(
        select(BatchVideo)
        .where(BatchVideo.batch_id == row.id,
               BatchVideo.state.in_(RETRYABLE_VIDEO_STATES))
        .order_by(BatchVideo.id)
        .limit(max_videos)
    ).all()

    refs: list[VideoRef] = []
    for bv in pending:
        video = session.get(Video, bv.video_id)
        url = (media_url_builder(video) if media_url_builder
               else (video.media_path or ""))
        refs.append(VideoRef(video_id=bv.video_id, media_url=url,
                             duration_s=video.duration_s if video else None,
                             frames=bv.frames))
    session.flush()

    return BatchLease(
        batch_id=row.id, lease_token=token, modality=Modality(row.modality),
        tier=Tier(row.tier), method_version=row.method_version, videos=refs,
        lease_expires_at=row.lease_expires_at, attempt=row.attempt_count,
        max_attempts=row.max_attempts,
    )


def _locked_leased_batch(session: Session, batch_id: int,
                         lease_token: str) -> AnalysisBatch:
    """Load a batch and verify the caller still owns its lease."""
    row = session.execute(
        select(AnalysisBatch).where(AnalysisBatch.id == batch_id)
        .with_for_update()
    ).scalar_one_or_none()
    if row is None:
        raise LeaseError(f"batch {batch_id} does not exist")
    if row.status != "leased" or row.lease_token != lease_token:
        raise LeaseError(
            f"stale or invalid lease for batch {batch_id} "
            f"(status={row.status})")
    return row


# ---------------------------------------------------------------------------
# heartbeat
# ---------------------------------------------------------------------------

def heartbeat(session: Session, hb: Heartbeat,
              lease_seconds: int = DEFAULT_LEASE_SECONDS) -> datetime:
    row = _locked_leased_batch(session, hb.batch_id, hb.lease_token)
    now = utcnow()
    row.last_heartbeat_at = now
    row.lease_expires_at = now + timedelta(seconds=lease_seconds)
    if hb.peak_vram_mib:
        row.peak_vram_mib = max(row.peak_vram_mib or 0, hb.peak_vram_mib)
    session.flush()
    return row.lease_expires_at


def record_worker_status(session: Session, status: WorkerStatus) -> None:
    session.execute(pg_insert(WorkerHeartbeat).values(
        worker_id=status.worker_id, state=status.state, reason=status.reason,
        free_vram_mib=status.free_vram_mib,
        total_vram_mib=status.total_vram_mib,
    ).on_conflict_do_update(
        index_elements=[WorkerHeartbeat.worker_id],
        set_={"state": status.state, "reason": status.reason,
              "free_vram_mib": status.free_vram_mib,
              "total_vram_mib": status.total_vram_mib,
              "updated_at": utcnow()},
    ))
    session.flush()


# ---------------------------------------------------------------------------
# results
# ---------------------------------------------------------------------------

def submit_results(session: Session, sub: ResultSubmission) -> dict:
    """Commit per-video results for a leased batch.

    Results are upserted on idempotency_key, so replaying a batch updates its
    own rows instead of inserting duplicates. Committing per video is what
    gives an interrupted batch partial credit.
    """
    row = _locked_leased_batch(session, sub.batch_id, sub.lease_token)
    written = 0

    for res in sub.results:
        expected = make_idempotency_key(row.modality, res.video_id,
                                        row.method_version)
        if res.idempotency_key != expected:
            raise ValueError(
                f"idempotency_key mismatch for video {res.video_id}: "
                f"got {res.idempotency_key!r}, expected {expected!r}")

        session.execute(pg_insert(AnalysisResult).values(
            video_id=res.video_id, modality=row.modality, tier=row.tier,
            method_version=row.method_version,
            idempotency_key=res.idempotency_key,
            payload=res.payload, error=res.error,
            compute_seconds=res.compute_seconds,
            peak_vram_mib=res.peak_vram_mib,
            produced_by_batch=row.id,
        ).on_conflict_do_update(
            index_elements=[AnalysisResult.idempotency_key],
            set_={"payload": res.payload, "error": res.error,
                  "compute_seconds": res.compute_seconds,
                  "peak_vram_mib": res.peak_vram_mib,
                  "produced_by_batch": row.id, "updated_at": utcnow()},
        ))
        written += 1

        bv = session.scalar(select(BatchVideo).where(
            BatchVideo.batch_id == row.id,
            BatchVideo.video_id == res.video_id).with_for_update())
        if bv is None:
            continue
        if res.error:
            bv.failure_count += 1
            bv.last_error = res.error
            bv.state = ("quarantined"
                        if bv.failure_count >= VIDEO_QUARANTINE_AFTER
                        else "failed")
        else:
            bv.state = "done"
            bv.last_error = None

        if res.peak_vram_mib:
            row.peak_vram_mib = max(row.peak_vram_mib or 0, res.peak_vram_mib)

    if sub.batch_peak_vram_mib:
        row.peak_vram_mib = max(row.peak_vram_mib or 0, sub.batch_peak_vram_mib)

    row.last_heartbeat_at = utcnow()
    session.flush()

    if sub.final:
        _finalise(session, row, partial_reason=sub.partial_reason)
    session.flush()
    return {"written": written, "batch_status": row.status}


def _finalise(session: Session, row: AnalysisBatch,
              partial_reason: str | None) -> None:
    """Close a batch, or return its unfinished remainder to the queue."""
    remaining = session.scalar(
        select(BatchVideo.id).where(
            BatchVideo.batch_id == row.id,
            BatchVideo.state.in_(RETRYABLE_VIDEO_STATES)).limit(1))

    if remaining is None:
        row.status = "done"
        row.completed_at = utcnow()
        row.lease_token = None
        row.lease_expires_at = None
        return

    # Work is left. A deliberate partial hand-back (e.g. VRAM fell below the
    # critical floor) is not a failure, so it does not consume an attempt.
    if partial_reason:
        row.attempt_count = max(0, row.attempt_count - 1)
        row.last_error = f"partial: {partial_reason}"
    _requeue_or_fail(session, row, reason=row.last_error or "incomplete batch")


def _requeue_or_fail(session: Session, row: AnalysisBatch,
                     reason: str) -> None:
    row.lease_token = None
    row.leased_by = None
    row.lease_expires_at = None
    row.last_error = reason[:2000] if reason else None

    if row.attempt_count >= row.max_attempts:
        row.status = "failed"          # terminal: dead-lettered
        row.completed_at = utcnow()
        return

    row.status = "pending"
    backoff = BACKOFF_BASE_SECONDS * (2 ** max(0, row.attempt_count - 1))
    row.not_before = utcnow() + timedelta(seconds=backoff)


def fail_batch(session: Session, batch_id: int, lease_token: str,
               error: str) -> str:
    """Worker reports the whole batch failed. Bounded retries apply."""
    row = _locked_leased_batch(session, batch_id, lease_token)
    _requeue_or_fail(session, row, reason=error)
    session.flush()
    return row.status


# ---------------------------------------------------------------------------
# reaper
# ---------------------------------------------------------------------------

def reap_expired(session: Session, *, now: datetime | None = None) -> list[int]:
    """Requeue batches whose lease expired without a heartbeat.

    Keyed on lease_expires_at, which every heartbeat pushes forward — so this
    only ever touches batches that stopped reporting, never slow ones.
    """
    now = now or utcnow()
    rows = session.execute(
        select(AnalysisBatch).where(
            AnalysisBatch.status == "leased",
            AnalysisBatch.lease_expires_at.is_not(None),
            AnalysisBatch.lease_expires_at < now,
        ).with_for_update(skip_locked=True)
    ).scalars().all()

    reaped = []
    for row in rows:
        _requeue_or_fail(
            session, row,
            reason=(f"lease expired at {row.lease_expires_at.isoformat()}; "
                    f"last heartbeat "
                    f"{row.last_heartbeat_at.isoformat() if row.last_heartbeat_at else 'never'}"))
        reaped.append(row.id)
    session.flush()
    return reaped


def queue_depth(session: Session) -> dict:
    """Queue visibility — a GPU backlog must be observable, not silent."""
    rows = session.execute(text("""
        select modality, status, count(*) as n
        from analysis_batches group by modality, status
        order by modality, status
    """)).all()
    out: dict[str, dict[str, int]] = {}
    for modality, status, n in rows:
        out.setdefault(modality, {})[status] = n
    return out
