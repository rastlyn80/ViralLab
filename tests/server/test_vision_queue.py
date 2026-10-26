"""The visual-analysis request queue.

The server has no GPU, so "analyse this video" is a queued intent the
Windows worker drains. These tests cover the three ways that goes wrong:
a second click creating duplicate work, a dead worker holding a request
forever, and a failing video retrying without bound.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy import text as _sql
from sqlalchemy.orm import sessionmaker

from server.api import vision_api as vapi
from server.db.models import Base, Creator, Video, VisionRequest

from tests.conftest import TEST_DB_URL, assert_disposable

DB_URL = TEST_DB_URL
pytestmark = pytest.mark.skipif(
    not DB_URL, reason="VIRALLAB_TEST_DB_URL not set")
if DB_URL:
    assert_disposable(DB_URL)


@pytest.fixture(scope="module")
def engine():
    eng = create_engine(DB_URL, future=True)
    Base.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def session(engine):
    maker = sessionmaker(engine, expire_on_commit=False, future=True)
    with maker() as s:
        # Order matters: metrics_snapshots and video_features reference
        # videos, and other modules in the suite leave rows behind. Deleting
        # videos first raises ForeignKeyViolation and takes this whole module
        # down with an ERROR rather than a FAIL.
        s.execute(_sql("""
            truncate table vision_requests, video_features,
                           video_feature_runs, metrics_snapshots,
                           videos, creators
            restart identity cascade"""))
        s.commit()
        yield s


@pytest.fixture
def own_video(session):
    creator = Creator(platform="instagram", handle="owner",
                      external_id="own-1", is_own_account=True)
    session.add(creator)
    session.flush()
    video = Video(creator_id=creator.id, platform="instagram",
                  external_id="vid-1", source="test",
                  media_status="available", media_path="/tmp/x.mp4",
                  duration_s=20.0)
    session.add(video)
    session.commit()
    return video


def _queue(session, video, force=False):
    from shared.schemas.vision import VisionRequestBody
    return vapi.request_analysis(VisionRequestBody(video_id=video.id,
                                                   force=force),
                                 session=session)


def _row(session, request_id: int) -> VisionRequest:
    """The ORM row behind a response.

    `request_analysis` returns a Pydantic view, not the mapped entity, so a
    test that mutates the response and commits changes nothing at all - and
    then asserts against stale state.
    """
    return session.get(VisionRequest, request_id)


def test_second_request_returns_the_first(session, own_video):
    """A second click must not create a second unit of work."""
    first = _queue(session, own_video)
    second = _queue(session, own_video)
    assert first.id == second.id
    assert session.scalar(
        select(VisionRequest).where(VisionRequest.id == first.id)) is not None
    assert len(session.scalars(select(VisionRequest)).all()) == 1


def test_request_is_pinned_to_local(session, own_video):
    """A queued request must never become a paid cloud call later."""
    req = _queue(session, own_video)
    assert req.mode == "local_only"


def test_competitor_video_is_refused(session):
    """Visual analysis is own-account only."""
    from shared.schemas.vision import VisionRequestBody

    other = Creator(platform="instagram", handle="someone_else",
                    external_id="other-1", is_own_account=False)
    session.add(other)
    session.flush()
    video = Video(creator_id=other.id, platform="instagram",
                  external_id="other-vid", source="test",
                  media_status="available", media_path="/tmp/y.mp4")
    session.add(video)
    session.commit()

    with pytest.raises(Exception) as exc:
        vapi.request_analysis(VisionRequestBody(video_id=video.id),
                              session=session)
    assert "own publications" in str(exc.value)


def test_video_without_media_is_refused(session):
    from shared.schemas.vision import VisionRequestBody

    creator = Creator(platform="instagram", handle="owner2",
                      external_id="own-2", is_own_account=True)
    session.add(creator)
    session.flush()
    video = Video(creator_id=creator.id, platform="instagram",
                  external_id="no-media", source="test",
                  media_status="absent")
    session.add(video)
    session.commit()

    with pytest.raises(Exception) as exc:
        vapi.request_analysis(VisionRequestBody(video_id=video.id),
                              session=session)
    assert "no preserved file" in str(exc.value)


def test_stale_running_request_is_requeued(session, own_video):
    """A worker killed mid-video must not hold the slot forever."""
    view = _queue(session, own_video)
    req = _row(session, view.id)
    req.status = "running"
    req.claimed_by = "dead-worker"
    req.started_at = (datetime.now(timezone.utc)
                      - timedelta(minutes=vapi.STALE_RUNNING_MINUTES + 5))
    req.attempts = 1
    session.commit()

    moved = vapi.requeue_stale(session)
    assert moved == 1
    session.refresh(req)
    assert req.status == "queued"
    assert req.claimed_by is None
    assert "stopped reporting" in (req.error or "")


def test_repeated_abandonment_stops_rather_than_cycling(session, own_video):
    """Bounded retries: CLAUDE.md 4.4. No job may retry forever."""
    view = _queue(session, own_video)
    req = _row(session, view.id)
    req.status = "running"
    req.claimed_by = "flaky"
    req.started_at = (datetime.now(timezone.utc)
                      - timedelta(minutes=vapi.STALE_RUNNING_MINUTES + 5))
    req.attempts = vapi.MAX_ATTEMPTS
    session.commit()

    vapi.requeue_stale(session)
    session.refresh(req)
    assert req.status == "failed"
    assert "not retried again" in (req.error or "")


def test_a_finished_request_allows_a_new_one(session, own_video):
    """The unique index is partial on purpose.

    Re-analysing after a completed run is legitimate; only an OPEN request
    blocks a duplicate.
    """
    first = _queue(session, own_video)
    row = _row(session, first.id)
    row.status = "done"
    row.finished_at = datetime.now(timezone.utc)
    session.commit()

    second = _queue(session, own_video, force=True)
    assert second.id != first.id
    assert len(session.scalars(select(VisionRequest)).all()) == 2


def test_cancel_only_applies_to_open_requests(session, own_video):
    req = _queue(session, own_video)
    out = vapi.cancel_request(req.id, session=session)
    assert out.status == "cancelled"

    with pytest.raises(Exception) as exc:
        vapi.cancel_request(req.id, session=session)
    assert "already cancelled" in str(exc.value)


def test_worker_state_reports_offline_without_a_heartbeat(session):
    state = vapi._worker_state(session)
    assert state["online"] is False
    assert state["detail"]
