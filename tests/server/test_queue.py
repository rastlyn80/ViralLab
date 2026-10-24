"""Phase 1 queue acceptance tests.

These are the four adversarial tests named in DEVELOPMENT_PLAN.md section 7
Phase 1, plus coverage of the ADR 0002 batch semantics. They are permanent
regression tests, not one-off checks.

Requires a live Postgres. Set VIRALLAB_DB_URL, e.g.
  postgresql+psycopg://virallab:virallab_dev@127.0.0.1:5433/virallab
"""

from __future__ import annotations

import os
from datetime import timedelta

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session, sessionmaker

from server.db.models import (
    AnalysisBatch, AnalysisResult, Base, BatchVideo, Creator, Video, utcnow,
)
from server.queue.queue import (
    LeaseError, enqueue_batch, fail_batch, heartbeat, lease_batch,
    queue_depth, reap_expired, record_worker_status, submit_results,
)
from shared.schemas.jobs import (
    Heartbeat, Modality, ResultSubmission, Tier, VideoResult, WorkerStatus,
    make_idempotency_key,
)

from tests.conftest import TEST_DB_URL, assert_disposable

#: Guarded in tests/conftest.py: never the application database.
DB_URL = TEST_DB_URL
pytestmark = pytest.mark.skipif(
    not DB_URL, reason="VIRALLAB_TEST_DB_URL not set")
if DB_URL:
    assert_disposable(DB_URL)

METHOD = "test-v1"


@pytest.fixture(scope="module")
def engine():
    eng = create_engine(DB_URL, future=True)
    Base.metadata.drop_all(eng)
    Base.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def session(engine):
    maker = sessionmaker(engine, expire_on_commit=False, future=True)
    with maker() as s:
        for table in ("analysis_results", "batch_videos", "analysis_batches",
                      "metrics_snapshots", "videos", "creators",
                      "worker_heartbeats"):
            s.execute(text(f"truncate {table} restart identity cascade"))
        s.commit()
        yield s
        s.rollback()


def make_videos(session: Session, n: int) -> list[int]:
    creator = Creator(platform="tiktok", handle="test_creator",
                      follower_count=1000, is_own_account=False)
    session.add(creator)
    session.flush()
    ids = []
    for i in range(n):
        v = Video(creator_id=creator.id, platform="tiktok",
                  external_id=f"ext{i}", duration_s=30.0, source="provider",
                  media_status="available", media_path=f"/media/v{i}.mp4")
        session.add(v)
        session.flush()
        ids.append(v.id)
    session.commit()
    return ids


def results_for(lease, *, error_on: set[int] | None = None):
    error_on = error_on or set()
    return [
        VideoResult(
            video_id=ref.video_id,
            idempotency_key=make_idempotency_key(
                lease.modality, ref.video_id, lease.method_version),
            payload={} if ref.video_id in error_on else {"ok": True},
            error="boom" if ref.video_id in error_on else None,
            peak_vram_mib=7634,
        )
        for ref in lease.videos
    ]


# ---------------------------------------------------------------------------
# basics
# ---------------------------------------------------------------------------

def test_lease_and_complete(session):
    ids = make_videos(session, 3)
    enqueue_batch(session, modality=Modality.VISION, tier=Tier.T2,
                  method_version=METHOD, video_ids=ids)
    session.commit()

    lease = lease_batch(session, worker_id="w1",
                        capabilities=[Modality.VISION])
    assert lease is not None
    assert lease.requires_gpu is True
    assert len(lease.videos) == 3
    assert lease.attempt == 1

    submit_results(session, ResultSubmission(
        batch_id=lease.batch_id, lease_token=lease.lease_token,
        worker_id="w1", results=results_for(lease), final=True))
    session.commit()

    batch = session.get(AnalysisBatch, lease.batch_id)
    assert batch.status == "done"
    assert batch.lease_token is None
    assert batch.peak_vram_mib == 7634          # ADR 0004: peak recorded
    assert session.scalar(select(AnalysisResult).where(
        AnalysisResult.video_id == ids[0])).payload == {"ok": True}


def test_capability_filter_and_empty_queue(session):
    ids = make_videos(session, 2)
    enqueue_batch(session, modality=Modality.VISION, tier=Tier.T2,
                  method_version=METHOD, video_ids=ids)
    session.commit()
    # A worker without the vision capability must not receive this batch.
    assert lease_batch(session, worker_id="cpu-only",
                       capabilities=[Modality.AUDIO]) is None
    assert lease_batch(session, worker_id="w", capabilities=[]) is None


def test_concurrent_workers_never_share_a_batch(engine, session):
    """SKIP LOCKED: two simultaneous leases must get different batches."""
    ids = make_videos(session, 2)
    enqueue_batch(session, modality=Modality.VISION, tier=Tier.T2,
                  method_version=METHOD, video_ids=[ids[0]])
    enqueue_batch(session, modality=Modality.VISION, tier=Tier.T2,
                  method_version="other-v1", video_ids=[ids[1]])
    session.commit()

    maker = sessionmaker(engine, expire_on_commit=False, future=True)
    with maker() as s1, maker() as s2:
        l1 = lease_batch(s1, worker_id="w1", capabilities=[Modality.VISION])
        l2 = lease_batch(s2, worker_id="w2", capabilities=[Modality.VISION])
        assert l1 and l2
        assert l1.batch_id != l2.batch_id, "two workers leased the same batch"
        s1.commit()
        s2.commit()


# ---------------------------------------------------------------------------
# acceptance test 1 — killed worker is requeued by the reaper
# ---------------------------------------------------------------------------

def test_expired_lease_is_reaped_and_requeued(session):
    ids = make_videos(session, 3)
    enqueue_batch(session, modality=Modality.VISION, tier=Tier.T2,
                  method_version=METHOD, video_ids=ids)
    session.commit()

    lease = lease_batch(session, worker_id="doomed",
                        capabilities=[Modality.VISION])
    session.commit()

    # Simulate the worker dying: lease expires, no further heartbeat.
    session.execute(text(
        "update analysis_batches set lease_expires_at = now() - interval '1 minute'"
        " where id = :i"), {"i": lease.batch_id})
    session.commit()

    reaped = reap_expired(session)
    session.commit()
    assert lease.batch_id in reaped

    batch = session.get(AnalysisBatch, lease.batch_id)
    session.refresh(batch)
    assert batch.status == "pending"
    assert batch.attempt_count == 1
    assert batch.not_before is not None          # backoff applied
    assert "lease expired" in batch.last_error

    # Clear the backoff gate and confirm it can be leased again.
    session.execute(text("update analysis_batches set not_before = null"))
    session.commit()
    again = lease_batch(session, worker_id="w2",
                        capabilities=[Modality.VISION])
    assert again is not None and again.attempt == 2


def test_heartbeat_keeps_a_slow_batch_alive(session):
    """A slow-but-alive batch must never be reaped."""
    ids = make_videos(session, 2)
    enqueue_batch(session, modality=Modality.VISION, tier=Tier.T2,
                  method_version=METHOD, video_ids=ids)
    session.commit()
    lease = lease_batch(session, worker_id="slow",
                        capabilities=[Modality.VISION], lease_seconds=60)
    session.commit()

    # Push the clock past the original expiry, but keep heartbeating.
    session.execute(text(
        "update analysis_batches set lease_expires_at = now() - interval '1 second'"
        " where id = :i"), {"i": lease.batch_id})
    session.commit()
    new_expiry = heartbeat(session, Heartbeat(
        batch_id=lease.batch_id, lease_token=lease.lease_token,
        worker_id="slow", videos_done=1, peak_vram_mib=7000))
    session.commit()
    assert new_expiry > utcnow()

    assert reap_expired(session) == [], "a heartbeating batch was reaped"
    assert session.get(AnalysisBatch, lease.batch_id).status == "leased"


# ---------------------------------------------------------------------------
# acceptance test 2 — a stale lease token cannot write
# ---------------------------------------------------------------------------

def test_stale_lease_token_is_rejected(session):
    """The zombie-worker race: a reaped worker must not overwrite the newer
    lease holder's results."""
    ids = make_videos(session, 2)
    enqueue_batch(session, modality=Modality.VISION, tier=Tier.T2,
                  method_version=METHOD, video_ids=ids)
    session.commit()

    zombie = lease_batch(session, worker_id="zombie",
                         capabilities=[Modality.VISION])
    session.commit()

    # Reap it and hand the batch to a new worker.
    session.execute(text(
        "update analysis_batches set lease_expires_at = now() - interval '1 min'"
        " where id = :i"), {"i": zombie.batch_id})
    session.commit()
    reap_expired(session)
    session.execute(text("update analysis_batches set not_before = null"))
    session.commit()
    fresh = lease_batch(session, worker_id="fresh",
                        capabilities=[Modality.VISION])
    session.commit()
    assert fresh.lease_token != zombie.lease_token

    # The zombie wakes up and tries to write with its old token.
    for call in (
        lambda: submit_results(session, ResultSubmission(
            batch_id=zombie.batch_id, lease_token=zombie.lease_token,
            worker_id="zombie", results=results_for(zombie), final=True)),
        lambda: heartbeat(session, Heartbeat(
            batch_id=zombie.batch_id, lease_token=zombie.lease_token,
            worker_id="zombie")),
        lambda: fail_batch(session, zombie.batch_id, zombie.lease_token, "x"),
    ):
        with pytest.raises(LeaseError):
            call()
        session.rollback()

    # Nothing was written, and the fresh holder still owns the batch.
    assert session.scalar(select(AnalysisResult)) is None
    batch = session.get(AnalysisBatch, fresh.batch_id)
    session.refresh(batch)
    assert batch.lease_token == fresh.lease_token


# ---------------------------------------------------------------------------
# acceptance test 3 — replay yields exactly one row per video
# ---------------------------------------------------------------------------

def test_replay_is_idempotent(session):
    ids = make_videos(session, 3)
    enqueue_batch(session, modality=Modality.VISION, tier=Tier.T2,
                  method_version=METHOD, video_ids=ids)
    session.commit()

    for _ in range(2):   # same work submitted twice
        lease = lease_batch(session, worker_id="w",
                            capabilities=[Modality.VISION])
        if lease is None:                      # batch already done
            break
        submit_results(session, ResultSubmission(
            batch_id=lease.batch_id, lease_token=lease.lease_token,
            worker_id="w", results=results_for(lease), final=True))
        session.commit()

    for vid in ids:
        n = session.scalar(text(
            "select count(*) from analysis_results where video_id = :v"
        ).bindparams(v=vid))
        assert n == 1, f"video {vid} produced {n} result rows, expected 1"


def test_mismatched_idempotency_key_is_rejected(session):
    ids = make_videos(session, 1)
    enqueue_batch(session, modality=Modality.VISION, tier=Tier.T2,
                  method_version=METHOD, video_ids=ids)
    session.commit()
    lease = lease_batch(session, worker_id="w",
                        capabilities=[Modality.VISION])
    bad = VideoResult(video_id=ids[0],
                      idempotency_key="vision:999:wrong-method",
                      payload={"ok": True})
    with pytest.raises(ValueError, match="idempotency_key mismatch"):
        submit_results(session, ResultSubmission(
            batch_id=lease.batch_id, lease_token=lease.lease_token,
            worker_id="w", results=[bad], final=True))


def test_enqueue_skips_videos_that_already_have_results(session):
    ids = make_videos(session, 4)
    enqueue_batch(session, modality=Modality.VISION, tier=Tier.T2,
                  method_version=METHOD, video_ids=ids[:2])
    session.commit()
    lease = lease_batch(session, worker_id="w",
                        capabilities=[Modality.VISION])
    submit_results(session, ResultSubmission(
        batch_id=lease.batch_id, lease_token=lease.lease_token, worker_id="w",
        results=results_for(lease), final=True))
    session.commit()

    # Re-enqueue all four: only the two without results should be included.
    batch2 = enqueue_batch(session, modality=Modality.VISION, tier=Tier.T2,
                           method_version=METHOD, video_ids=ids)
    session.commit()
    members = session.scalars(select(BatchVideo.video_id).where(
        BatchVideo.batch_id == batch2.id)).all()
    assert sorted(members) == sorted(ids[2:])


# ---------------------------------------------------------------------------
# acceptance test 4 — bounded retries, then dead-letter
# ---------------------------------------------------------------------------

def test_retries_are_bounded_then_dead_lettered(session):
    ids = make_videos(session, 2)
    enqueue_batch(session, modality=Modality.VISION, tier=Tier.T2,
                  method_version=METHOD, video_ids=ids, max_attempts=3)
    session.commit()

    statuses = []
    for _ in range(3):
        session.execute(text("update analysis_batches set not_before = null"))
        session.commit()
        lease = lease_batch(session, worker_id="w",
                            capabilities=[Modality.VISION])
        assert lease is not None, "batch stopped being leasable too early"
        statuses.append(fail_batch(session, lease.batch_id,
                                   lease.lease_token, "deliberate failure"))
        session.commit()

    assert statuses == ["pending", "pending", "failed"], statuses

    # Terminal: no further lease, ever.
    session.execute(text("update analysis_batches set not_before = null"))
    session.commit()
    assert lease_batch(session, worker_id="w",
                       capabilities=[Modality.VISION]) is None

    batch = session.get(AnalysisBatch, 1)
    session.refresh(batch)
    assert batch.status == "failed"
    assert batch.attempt_count == 3
    assert "deliberate failure" in batch.last_error


def test_backoff_grows_and_gates_dequeue(session):
    ids = make_videos(session, 1)
    enqueue_batch(session, modality=Modality.VISION, tier=Tier.T2,
                  method_version=METHOD, video_ids=ids)
    session.commit()
    lease = lease_batch(session, worker_id="w",
                        capabilities=[Modality.VISION])
    fail_batch(session, lease.batch_id, lease.lease_token, "fail once")
    session.commit()

    batch = session.get(AnalysisBatch, lease.batch_id)
    session.refresh(batch)
    assert batch.not_before > utcnow()
    # The backoff gate must actually hide the row from dequeue.
    assert lease_batch(session, worker_id="w",
                       capabilities=[Modality.VISION]) is None


# ---------------------------------------------------------------------------
# ADR 0002 batch semantics — partial credit and quarantine
# ---------------------------------------------------------------------------

def test_partial_batch_keeps_completed_work_and_requeues_remainder(session):
    """ADR 0004 section 3: a VRAM bail-out must lose nothing."""
    ids = make_videos(session, 5)
    enqueue_batch(session, modality=Modality.VISION, tier=Tier.T2,
                  method_version=METHOD, video_ids=ids)
    session.commit()

    lease = lease_batch(session, worker_id="w",
                        capabilities=[Modality.VISION])
    done_first = results_for(lease)[:3]
    submit_results(session, ResultSubmission(
        batch_id=lease.batch_id, lease_token=lease.lease_token, worker_id="w",
        results=done_first, final=True,
        partial_reason="free VRAM below critical floor"))
    session.commit()

    batch = session.get(AnalysisBatch, lease.batch_id)
    session.refresh(batch)
    assert batch.status == "pending", "remainder was not requeued"
    # A deliberate hand-back must not consume a retry attempt.
    assert batch.attempt_count == 0
    assert "critical floor" in batch.last_error
    assert session.scalar(text(
        "select count(*) from analysis_results")) == 3

    # The retry sees only the unfinished remainder.
    session.execute(text("update analysis_batches set not_before = null"))
    session.commit()
    second = lease_batch(session, worker_id="w2",
                         capabilities=[Modality.VISION])
    assert {r.video_id for r in second.videos} == set(ids[3:])


def test_repeatedly_failing_video_is_quarantined(session):
    """One pathological video must not fail its batch forever."""
    ids = make_videos(session, 2)
    enqueue_batch(session, modality=Modality.VISION, tier=Tier.T2,
                  method_version=METHOD, video_ids=ids, max_attempts=9)
    session.commit()
    bad = ids[0]

    for _ in range(3):
        session.execute(text("update analysis_batches set not_before = null"))
        session.commit()
        lease = lease_batch(session, worker_id="w",
                            capabilities=[Modality.VISION])
        submit_results(session, ResultSubmission(
            batch_id=lease.batch_id, lease_token=lease.lease_token,
            worker_id="w", results=results_for(lease, error_on={bad}),
            final=True))
        session.commit()

    bv = session.scalar(select(BatchVideo).where(BatchVideo.video_id == bad))
    session.refresh(bv)
    assert bv.state == "quarantined"
    assert bv.failure_count >= 3

    # With the bad video quarantined, the batch can finally complete.
    session.execute(text("update analysis_batches set not_before = null"))
    session.commit()
    batch = session.get(AnalysisBatch, lease.batch_id)
    session.refresh(batch)
    assert batch.status == "done", f"batch stuck in {batch.status}"


def test_worker_status_and_queue_depth_are_observable(session):
    """A deferred worker and a GPU backlog must both be visible, not silent."""
    ids = make_videos(session, 2)
    enqueue_batch(session, modality=Modality.VISION, tier=Tier.T2,
                  method_version=METHOD, video_ids=ids)
    session.commit()

    record_worker_status(session, WorkerStatus(
        worker_id="gpu-1", state="deferred",
        reason="insufficient VRAM: 400 MiB free, need 7634 + 512",
        free_vram_mib=400, total_vram_mib=8192))
    session.commit()

    depth = queue_depth(session)
    assert depth["vision"]["pending"] == 1
    row = session.execute(text(
        "select state, reason from worker_heartbeats where worker_id='gpu-1'"
    )).one()
    assert row.state == "deferred" and "insufficient VRAM" in row.reason
