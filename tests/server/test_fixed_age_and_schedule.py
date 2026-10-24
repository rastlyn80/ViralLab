"""Phase 2 checkpoint: fixed-age honesty and schedule durability.

The first half guards the distinction the live import made concrete: a
backfilled video's first snapshot is a lifetime cumulative reading at an
arbitrary age (median 237 days for the real account), and must never be
served as views@7d or views@30d.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from server.analytics.fixed_age import (
    ANCHORS, anchor_eligibility, metric_at_age, observation_age_days,
)
from server.collectors.base import upsert_creator
from server.db.models import Base, Creator, MetricsSnapshot, Video, utcnow
from server.scheduler.collect import MIN_INTERVAL, due
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
        for t in ("analysis_results", "batch_videos", "analysis_batches",
                  "metrics_snapshots", "videos", "creators", "oauth_tokens",
                  "collection_runs", "worker_heartbeats"):
            s.execute(text(f"truncate {t} restart identity cascade"))
        s.commit()
        yield s
        s.rollback()


def make_video(session, posted_days_ago: float) -> Video:
    c = upsert_creator(session, platform="tiktok", handle="me",
                       external_id="acct1", is_own_account=True)
    v = Video(creator_id=c.id, platform="tiktok",
              external_id=f"v{posted_days_ago}", source="official_api",
              posted_at=utcnow() - timedelta(days=posted_days_ago))
    session.add(v)
    session.flush()
    return v


def snap(session, video, *, days_after_post: float, views: int):
    s = MetricsSnapshot(video_id=video.id, views=views, likes=0, comments=0,
                        shares=0,
                        captured_at=video.posted_at
                        + timedelta(days=days_after_post))
    session.add(s)
    session.flush()
    return s


# ---------------------------------------------------------------------------
# fixed-age honesty
# ---------------------------------------------------------------------------

def test_backfilled_video_has_no_7d_or_30d_value(session):
    """The core guard. A video first seen at 237 days old has a lifetime
    total, not a fixed-age metric, and asking for one must return None with
    a reason rather than the lifetime figure."""
    v = make_video(session, posted_days_ago=400)
    snap(session, v, days_after_post=237, views=50_000)   # backfill reading
    session.commit()

    for anchor in (7, 30, 90):
        got = metric_at_age(session, v.id, anchor)
        assert got.value is None, (
            f"returned {got.value} for views@{anchor}d on a video first seen "
            "at day 237 - that is the lifetime total, not an anchor")
        assert got.available is False
        assert "237" in got.unavailable_reason or "anchor" in got.unavailable_reason


def test_lifetime_total_is_never_substituted_for_an_anchor(session):
    """Explicitly: the refusal must not be a zero or the raw total."""
    v = make_video(session, posted_days_ago=500)
    snap(session, v, days_after_post=300, views=99_999)
    session.commit()
    got = metric_at_age(session, v.id, 30)
    assert got.value is None
    assert got.value != 99_999 and got.value != 0


def test_tracked_video_yields_a_real_interpolated_anchor(session):
    """A video followed from publication DOES get a real value."""
    v = make_video(session, posted_days_ago=40)
    snap(session, v, days_after_post=5, views=1_000)
    snap(session, v, days_after_post=9, views=1_400)      # brackets day 7
    snap(session, v, days_after_post=35, views=2_000)
    session.commit()

    got = metric_at_age(session, v.id, 7)
    assert got.available and got.method == "interpolated"
    assert 1_000 < got.value < 1_400, got.value


def test_exact_snapshot_on_the_anchor_is_used_as_is(session):
    v = make_video(session, posted_days_ago=40)
    snap(session, v, days_after_post=7, views=1_234)
    snap(session, v, days_after_post=20, views=5_000)
    session.commit()
    got = metric_at_age(session, v.id, 7)
    assert got.value == 1_234 and got.method == "exact"


def test_no_later_snapshot_refuses_to_extrapolate(session):
    """A video only 3 days old has no 7-day value yet, and inventing one by
    extrapolation would be worse than returning nothing."""
    v = make_video(session, posted_days_ago=3)
    snap(session, v, days_after_post=1, views=100)
    snap(session, v, days_after_post=2, views=300)
    session.commit()
    got = metric_at_age(session, v.id, 7)
    assert got.value is None
    assert "extrapolate" in got.unavailable_reason


def test_observation_age_reports_first_sighting(session):
    v = make_video(session, posted_days_ago=400)
    snap(session, v, days_after_post=237, views=1)
    snap(session, v, days_after_post=300, views=2)
    session.commit()
    assert round(observation_age_days(session, v.id)) == 237


def test_eligibility_reports_the_honest_upper_bound(session):
    """After a pure backfill the honest answer is 'almost none'."""
    old = make_video(session, posted_days_ago=400)
    snap(session, old, days_after_post=237, views=10)
    fresh = make_video(session, posted_days_ago=10)
    snap(session, fresh, days_after_post=1, views=5)
    session.commit()

    e = anchor_eligibility(session, platform="tiktok")
    assert e["videos_with_snapshots"] == 2
    assert e["could_support_7d"] == 1          # only the freshly tracked one
    assert e["backfill_only"] == 1
    assert "UPPER BOUND" in e["note"]


def test_anchors_are_the_documented_set():
    assert ANCHORS == (7, 30, 90)


# ---------------------------------------------------------------------------
# schedule durability
# ---------------------------------------------------------------------------

def test_due_is_true_when_nothing_has_run(session):
    assert due(session, "tiktok", "acct1") is True


def test_due_is_false_right_after_a_successful_run(session):
    session.execute(text("""
        insert into collection_runs
          (platform, account_ref, kind, status, started_at, finished_at,
           videos_seen, videos_new, snapshots_written)
        values ('tiktok','acct1','daily','ok', now(), now(), 1, 0, 1)"""))
    session.commit()
    assert due(session, "tiktok", "acct1") is False


def test_due_becomes_true_again_after_the_interval(session):
    session.execute(text("""
        insert into collection_runs
          (platform, account_ref, kind, status, started_at, finished_at,
           videos_seen, videos_new, snapshots_written)
        values ('tiktok','acct1','daily','ok', now() - interval '2 days',
                now() - interval '2 days', 1, 0, 1)"""))
    session.commit()
    assert due(session, "tiktok", "acct1") is True


def test_failed_runs_do_not_count_as_done(session):
    """A failed pass must not suppress the next attempt — otherwise one
    failure silently stops collection for a day."""
    session.execute(text("""
        insert into collection_runs
          (platform, account_ref, kind, status, started_at, finished_at)
        values ('tiktok','acct1','daily','failed', now(), now()),
               ('tiktok','acct1','daily','deferred', now(), now())"""))
    session.commit()
    assert due(session, "tiktok", "acct1") is True


def test_due_state_survives_process_restart(session):
    """`due` reads the database, not an in-memory timer, so a restart neither
    repeats a completed pass nor loses track of one."""
    session.execute(text("""
        insert into collection_runs
          (platform, account_ref, kind, status, started_at, finished_at)
        values ('tiktok','acct1','daily','ok', now(), now())"""))
    session.commit()

    # Simulate a restart: brand-new session, no shared state.
    eng = create_engine(DB_URL, future=True)
    fresh = sessionmaker(eng, expire_on_commit=False, future=True)
    with fresh() as s2:
        assert due(s2, "tiktok", "acct1") is False
    eng.dispose()


def test_min_interval_is_under_a_day_so_a_daily_slot_is_never_missed():
    """20h, not 24h: a pass that runs slightly later each day would otherwise
    drift past its own window and skip a day."""
    assert timedelta(hours=12) < MIN_INTERVAL < timedelta(hours=24)
