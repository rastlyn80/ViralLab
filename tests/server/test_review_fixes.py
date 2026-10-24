"""Regression tests for the independent review findings.

One focused test per finding, kept in its own module so each fix has an
obvious guard that will fail loudly if it is ever undone.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

import httpx
import pytest
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import sessionmaker

from server.collectors.base import (
    NormalizedVideo, record_snapshot, upsert_creator, upsert_video,
)
from server.collectors.instagram_official import InstagramOfficialAdapter
from server.collectors.tiktok_official import TikTokAuth
from server.collectors.tokens import (
    ReauthRequired, TokenStore, TransientAuthError, apply_tiktok_refresh,
)
from server.db.models import Base, Creator, MetricsSnapshot
from tests.conftest import TEST_DB_URL, assert_disposable

#: Guarded in tests/conftest.py: never the application database.
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


def mock_client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def ig_adapter(session, handler) -> InstagramOfficialAdapter:
    store = TokenStore(session)
    store.upsert(platform="instagram", account_ref="17841400000000000",
                 access_token="page-token", expires_in=None)
    session.commit()
    return InstagramOfficialAdapter(store, client=mock_client(handler))


# ---------------------------------------------------------------------------
# #1 Instagram likes/comments were mapped but never requested
# ---------------------------------------------------------------------------

def test_instagram_requests_and_persists_likes_and_comments(session):
    """`likes` and `comments` ARE valid reel insight metrics (verified against
    Meta's metric list). They were mapped from the response but never asked
    for, so both were silently always None."""
    requested: list[list[str]] = []

    def handler(req):
        metric = req.url.params.get("metric", "").split(",")
        requested.append(metric)
        if "reels_skip_rate" in metric:          # the separate retention call
            return httpx.Response(200, json={"data": []})
        return httpx.Response(200, json={"data": [
            {"name": "views", "values": [{"value": 45111}]},
            {"name": "likes", "values": [{"value": 3195}]},
            {"name": "comments", "values": [{"value": 57}]},
            {"name": "saved", "values": [{"value": 812}]},
            {"name": "shares", "values": [{"value": 668}]},
        ]})

    adapter = ig_adapter(session, handler)
    m = list(adapter.fetch_metrics("17841400000000000", ["ig1"]))[0]
    core = requested[0]
    assert "likes" in core, "likes was never requested"
    assert "comments" in core, "comments was never requested"
    assert (m.likes, m.comments) == (3195, 57)

    # They must survive persistence, not merely normalisation.
    creator = upsert_creator(session, platform="instagram", handle="me",
                             external_id="17841400000000000",
                             is_own_account=True)
    video, _ = upsert_video(session, creator, NormalizedVideo(
        external_id="ig1", platform="instagram"), source="official_api")
    record_snapshot(session, video, m)
    session.commit()
    row = session.scalar(select(MetricsSnapshot).where(
        MetricsSnapshot.video_id == video.id))
    assert (row.likes, row.comments, row.views) == (3195, 57, 45111)


# ---------------------------------------------------------------------------
# #2 transient vs permanent auth failures, and refresh concurrency
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("status", [500, 502, 503, 429])
def test_tiktok_server_errors_are_transient_not_reauth(status):
    """A 503 reported as 'needs re-authorisation' would demand a pointless
    manual re-consent and halt collection over a blip."""
    auth = TikTokAuth("k", "s", mock_client(
        lambda req: httpx.Response(status, json={})))
    with pytest.raises(TransientAuthError):
        auth.refresh("some-refresh-token")


def test_tiktok_network_failure_is_transient():
    def handler(req):
        raise httpx.ConnectError("connection refused")

    auth = TikTokAuth("k", "s", mock_client(handler))
    with pytest.raises(TransientAuthError):
        auth.refresh("r")


def test_tiktok_invalid_grant_is_permanent():
    auth = TikTokAuth("k", "s", mock_client(lambda req: httpx.Response(
        200, json={"error": {"code": "invalid_grant"}})))
    with pytest.raises(ReauthRequired):
        auth.refresh("r")


def test_tiktok_unknown_error_code_is_treated_as_transient():
    """An unrecognised failure must not burn a working credential."""
    auth = TikTokAuth("k", "s", mock_client(lambda req: httpx.Response(
        200, json={"error": {"code": "something_new"}})))
    with pytest.raises(TransientAuthError):
        auth.refresh("r")


def test_stale_refresh_response_cannot_overwrite_a_rotated_token(session):
    """Two concurrent refreshes: the slower reply carries an already-rotated
    token and must be discarded, not written over the newer one."""
    store = TokenStore(session)
    store.upsert(platform="tiktok", account_ref="me", access_token="a0",
                 refresh_token="R1", expires_in=86400)
    session.commit()
    stale_view = store.get("tiktok", "me")            # caller A reads R1

    # Caller B finishes first and rotates R1 -> R2.
    apply_tiktok_refresh(store, store.get("tiktok", "me"), {
        "access_token": "a2", "refresh_token": "R2", "expires_in": 86400})
    session.commit()
    assert store.get("tiktok", "me").refresh_token == "R2"

    # Caller A's late reply, derived from the now-dead R1, must be dropped.
    apply_tiktok_refresh(store, stale_view, {
        "access_token": "a1-late", "refresh_token": "R1-late",
        "expires_in": 86400}, used_refresh_token="R1")
    session.commit()
    row = store.get("tiktok", "me")
    assert row.refresh_token == "R2", "a stale reply clobbered the live token"
    assert row.access_token == "a2"


def test_refresh_happens_once_when_called_repeatedly(session):
    """The row is locked before the refresh decision and re-checked under the
    lock, so a second caller finds the token already fresh instead of
    rotating it again."""
    store = TokenStore(session)
    store.upsert(platform="tiktok", account_ref="me", access_token="a",
                 refresh_token="R1", expires_in=60)     # inside the window
    session.commit()
    calls = {"n": 0}

    def refresher(st, r):
        calls["n"] += 1
        apply_tiktok_refresh(st, r, {"access_token": "fresh",
                                     "refresh_token": "R2",
                                     "expires_in": 86400})

    assert store.access_token("tiktok", "me", refresher=refresher) == "fresh"
    assert store.access_token("tiktok", "me", refresher=refresher) == "fresh"
    assert calls["n"] == 1, "refreshed twice; concurrent callers would rotate twice"


# ---------------------------------------------------------------------------
# #3 pagination must not silently drop posts
# ---------------------------------------------------------------------------

def test_instagram_early_stop_disabled_when_order_breaks(session):
    """Stopping at the first older post assumes newest-first ordering, which
    Meta does not document. If the order breaks, keep scanning."""
    page = {"data": [
        {"id": "a", "timestamp": "2026-09-10T00:00:00+0000"},
        {"id": "b", "timestamp": "2026-09-01T00:00:00+0000"},   # older
        {"id": "c", "timestamp": "2026-09-20T00:00:00+0000"},   # NEWER again
        {"id": "d", "timestamp": "2026-09-15T00:00:00+0000"},
    ], "paging": {}}
    adapter = ig_adapter(session, lambda req: httpx.Response(200, json=page))
    since = datetime(2026, 9, 5, tzinfo=timezone.utc)
    got = [v.external_id for v in
           adapter.fetch_account_videos("17841400000000000", since=since)]
    assert got == ["a", "c", "d"], f"eligible posts were dropped: {got}"


def test_instagram_early_stop_still_applies_when_ordered(session):
    """The optimisation must survive: ordered input still stops early."""
    page = {"data": [
        {"id": "a", "timestamp": "2026-09-10T00:00:00+0000"},
        {"id": "b", "timestamp": "2026-09-01T00:00:00+0000"},   # older -> stop
        {"id": "c", "timestamp": "2026-08-20T00:00:00+0000"},
    ], "paging": {}}
    adapter = ig_adapter(session, lambda req: httpx.Response(200, json=page))
    since = datetime(2026, 9, 5, tzinfo=timezone.utc)
    got = [v.external_id for v in
           adapter.fetch_account_videos("17841400000000000", since=since)]
    assert got == ["a"]


# ---------------------------------------------------------------------------
# #4 the test database guard itself
# ---------------------------------------------------------------------------

def test_guard_rejects_a_non_disposable_database():
    from tests.conftest import _dbname, _normalise, assert_disposable as guard
    with pytest.raises(AssertionError):
        guard("postgresql+psycopg://u:p@h:5432/virallab")
    guard("postgresql+psycopg://u:p@h:5432/virallab_test")       # fine
    assert _dbname("postgresql://u:p@h/db_test?sslmode=require") == "db_test"
    # Same target, different driver/credentials, must compare equal.
    assert (_normalise("postgresql+psycopg://a:b@h:5432/x")
            == _normalise("postgresql://c:d@h:5432/x"))


def test_tests_are_not_pointed_at_the_application_database():
    from tests.conftest import APP_DB_URL_AT_IMPORT, _normalise
    if not APP_DB_URL_AT_IMPORT or not DB_URL:
        pytest.skip("both URLs needed for this check")
    assert _normalise(APP_DB_URL_AT_IMPORT) != _normalise(DB_URL), (
        "the test suite is pointed at the application database")


# ---------------------------------------------------------------------------
# #5 stable creator ids over mutable handles
# ---------------------------------------------------------------------------

def test_creator_renaming_does_not_create_a_duplicate(session):
    """A renamed creator must stay one row: splitting their history would
    corrupt the per-creator baseline the whole target rests on."""
    first = upsert_creator(session, platform="tiktok", handle="old_name",
                           external_id="7269957969739727918")
    session.commit()
    again = upsert_creator(session, platform="tiktok", handle="NEW_name",
                           external_id="7269957969739727918")
    session.commit()
    assert again.id == first.id, "rename created a second creator row"
    assert again.handle == "NEW_name"
    assert session.scalar(select(func.count()).select_from(Creator)) == 1


def test_creator_without_external_id_still_matches_on_handle(session):
    """Sources that supply no stable id must keep working."""
    a = upsert_creator(session, platform="tiktok", handle="only_handle")
    session.commit()
    b = upsert_creator(session, platform="tiktok", handle="only_handle")
    session.commit()
    assert a.id == b.id


# ---------------------------------------------------------------------------
# credential hygiene
# ---------------------------------------------------------------------------

def test_instagram_token_is_not_put_in_the_url(session):
    """A token in a query string leaks into proxy logs, browser history and
    Referer headers. It belongs in the Authorization header."""
    seen = {}

    def handler(req):
        seen["url"] = str(req.url)
        seen["auth"] = req.headers.get("Authorization", "")
        return httpx.Response(200, json={"data": []})

    list(ig_adapter(session, handler).fetch_account_videos("17841400000000000"))
    assert "access_token" not in seen["url"], seen["url"]
    assert "page-token" not in seen["url"]
    assert seen["auth"] == "Bearer page-token"
