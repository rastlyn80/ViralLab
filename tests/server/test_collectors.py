"""Phase 2 collector tests — fixtures only, never a live API (CLAUDE.md §6).

The most important test here is `test_tiktok_refresh_persists_rotated_token`:
TikTok rotates the refresh token, and an implementation that keeps the old one
works for exactly one cycle before failing permanently 24 hours later.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import sessionmaker

from server.collectors.base import (
    record_snapshot, upsert_creator, upsert_video,
)
from server.collectors.instagram_official import InstagramOfficialAdapter
from server.collectors.tiktok_official import (
    TikTokAuth, TikTokOfficialAdapter, authorize_url,
)
from server.collectors.tokens import (
    REFRESH_WHEN_UNDER, ReauthRequired, TokenStore, apply_tiktok_refresh,
)
from server.db.models import Base, MetricsSnapshot, OAuthToken, Video, utcnow

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


# ---------------------------------------------------------------------------
# token store
# ---------------------------------------------------------------------------

def test_tiktok_refresh_persists_rotated_token(session):
    """The whole credential dies one cycle later if the new refresh token is
    not persisted. This is the single most important Phase 2 behaviour."""
    store = TokenStore(session)
    store.upsert(platform="tiktok", account_ref="me",
                 access_token="old-access", refresh_token="old-refresh",
                 expires_in=86400, refresh_expires_in=31536000)
    session.commit()

    apply_tiktok_refresh(store, store.get("tiktok", "me"), {
        "access_token": "new-access", "refresh_token": "ROTATED-refresh",
        "expires_in": 86400, "refresh_expires_in": 31536000})
    session.commit()

    row = store.get("tiktok", "me")
    assert row.access_token == "new-access"
    assert row.refresh_token == "ROTATED-refresh", (
        "rotated refresh token was not persisted - auth would break in 24h")


def test_refresh_response_without_rotation_keeps_existing(session):
    store = TokenStore(session)
    store.upsert(platform="tiktok", account_ref="me",
                 access_token="a", refresh_token="keep-me", expires_in=86400)
    session.commit()
    apply_tiktok_refresh(store, store.get("tiktok", "me"),
                         {"access_token": "b", "expires_in": 86400})
    session.commit()
    assert store.get("tiktok", "me").refresh_token == "keep-me"


def test_refresh_triggers_before_expiry_not_after(session):
    """Refresh-on-401 would make every expiry a visible outage."""
    store = TokenStore(session)
    row = store.upsert(platform="tiktok", account_ref="me", access_token="a",
                       refresh_token="r", expires_in=86400)
    session.commit()
    assert store.needs_refresh(row) is False
    row.expires_at = utcnow() + REFRESH_WHEN_UNDER - timedelta(minutes=1)
    assert store.needs_refresh(row) is True


def test_non_expiring_token_never_needs_refresh(session):
    """Instagram Page tokens do not expire (ADR 0005)."""
    store = TokenStore(session)
    row = store.upsert(platform="instagram", account_ref="123",
                       access_token="page-token", expires_in=None)
    session.commit()
    assert row.expires_at is None
    assert store.needs_refresh(row) is False
    assert store.access_token("instagram", "123") == "page-token"


def test_expired_refresh_token_demands_reauth(session):
    store = TokenStore(session)
    row = store.upsert(platform="tiktok", account_ref="me", access_token="a",
                       refresh_token="r", expires_in=60)
    row.refresh_expires_at = utcnow() - timedelta(days=1)
    session.commit()
    with pytest.raises(ReauthRequired, match="refresh token expired"):
        store.access_token("tiktok", "me", refresher=lambda s, r: None)
    assert store.get("tiktok", "me").needs_reauth is True


def test_missing_credential_and_reauth_flag_raise(session):
    store = TokenStore(session)
    with pytest.raises(ReauthRequired, match="no stored credential"):
        store.access_token("tiktok", "nobody")
    store.upsert(platform="tiktok", account_ref="me", access_token="a",
                 expires_in=86400)
    store.mark_reauth_required("tiktok", "me", "revoked by user")
    session.commit()
    with pytest.raises(ReauthRequired, match="needs re-authorisation"):
        store.access_token("tiktok", "me")


# ---------------------------------------------------------------------------
# TikTok adapter
# ---------------------------------------------------------------------------

TIKTOK_PAGE = {
    "data": {
        "videos": [
            {"id": "7001", "create_time": 1735689600,
             "video_description": "Chopin Ballade No.1 #piano #classicalmusic",
             "duration": 42, "view_count": 144292, "like_count": 20232,
             "comment_count": 243, "share_count": 430,
             "share_url": "https://www.tiktok.com/@me/video/7001",
             "embed_link": "https://www.tiktok.com/embed/7001"},
            {"id": "7002", "create_time": 1738368000, "title": "Liszt study",
             "duration": 18, "view_count": 8292, "like_count": 311,
             "comment_count": 7, "share_count": 2},
        ],
        "cursor": 2, "has_more": False,
    },
    "error": {"code": "ok"},
}


def tiktok_adapter(session, handler) -> TikTokOfficialAdapter:
    store = TokenStore(session)
    store.upsert(platform="tiktok", account_ref="me", access_token="tok",
                 refresh_token="ref", expires_in=86400,
                 refresh_expires_in=31536000)
    session.commit()
    client = mock_client(handler)
    return TikTokOfficialAdapter(store, TikTokAuth("key", "secret", client),
                                 client)


def test_tiktok_normalises_videos(session):
    adapter = tiktok_adapter(
        session, lambda req: httpx.Response(200, json=TIKTOK_PAGE))
    vids = list(adapter.fetch_account_videos("me"))
    assert [v.external_id for v in vids] == ["7001", "7002"]
    first = vids[0]
    assert first.posted_at == datetime(2025, 1, 1, tzinfo=timezone.utc)
    assert first.duration_s == 42.0
    assert sorted(first.hashtags) == ["classicalmusic", "piano"]
    # The Display API returns no downloadable media (ADR 0001).
    assert first.media_url is None and first.media_url_expires is False


def test_tiktok_requests_only_documented_fields(session):
    """A field TikTok does not document would make the whole call fail."""
    captured = {}

    def handler(req):
        captured["fields"] = req.url.params.get("fields", "").split(",")
        return httpx.Response(200, json=TIKTOK_PAGE)

    list(tiktok_adapter(session, handler).fetch_account_videos("me"))
    documented = {
        "id", "create_time", "cover_image_url", "share_url",
        "video_description", "duration", "height", "width", "title",
        "embed_link", "like_count", "comment_count", "share_count",
        "view_count", "is_aigc"}
    assert set(captured["fields"]) <= documented
    assert "collect_count" not in captured["fields"], (
        "collect_count is not on TikTok's documented Video Object")


def test_tiktok_metrics_leave_saves_null_not_zero(session):
    """A zero would be indistinguishable from a genuine zero."""
    adapter = tiktok_adapter(
        session, lambda req: httpx.Response(200, json=TIKTOK_PAGE))
    metrics = {m.external_id: m for m in adapter.fetch_metrics("me", ["7001"])}
    assert list(metrics) == ["7001"]
    assert metrics["7001"].views == 144292
    assert metrics["7001"].saves is None
    assert metrics["7001"].is_rounded is False


def test_tiktok_paginates(session):
    pages = [
        {"data": {"videos": [{"id": "1", "create_time": 1735689600,
                              "view_count": 10}],
                  "cursor": 1, "has_more": True}, "error": {"code": "ok"}},
        {"data": {"videos": [{"id": "2", "create_time": 1735689600,
                              "view_count": 20}],
                  "cursor": 2, "has_more": False}, "error": {"code": "ok"}},
    ]
    calls = {"n": 0}

    def handler(req):
        page = pages[min(calls["n"], 1)]
        calls["n"] += 1
        return httpx.Response(200, json=page)

    vids = list(tiktok_adapter(session, handler).fetch_account_videos("me"))
    assert [v.external_id for v in vids] == ["1", "2"]
    assert calls["n"] == 2


def test_tiktok_401_marks_reauth_required(session):
    adapter = tiktok_adapter(session, lambda req: httpx.Response(401, json={}))
    with pytest.raises(ReauthRequired):
        list(adapter.fetch_account_videos("me"))
    assert adapter.store.get("tiktok", "me").needs_reauth is True


def test_authorize_url_contains_required_scopes():
    url = authorize_url("ck", "https://example.test/cb", "state123")
    assert "client_key=ck" in url and "response_type=code" in url
    assert "video.list" in url and "user.info.basic" in url
    assert "state=state123" in url


# ---------------------------------------------------------------------------
# Instagram adapter
# ---------------------------------------------------------------------------

IG_MEDIA = {
    "data": [
        {"id": "ig1", "caption": "Bach Toccata #classicalmusic",
         "media_type": "VIDEO", "media_product_type": "REELS",
         "media_url": "https://cdn.example.test/ig1.mp4",
         "timestamp": "2026-09-01T10:30:00+0000",
         "like_count": 3195, "comments_count": 5},
    ],
    "paging": {},
}
IG_INSIGHTS = {
    "data": [
        {"name": "views", "values": [{"value": 45111}]},
        {"name": "reach", "values": [{"value": 31002}]},
        {"name": "saved", "values": [{"value": 812}]},
        {"name": "shares", "values": [{"value": 668}]},
    ]
}


def ig_adapter(session, handler) -> InstagramOfficialAdapter:
    store = TokenStore(session)
    store.upsert(platform="instagram", account_ref="17841400000000000",
                 access_token="page-token", expires_in=None, page_id="99")
    session.commit()
    return InstagramOfficialAdapter(store, client=mock_client(handler))


def test_instagram_normalises_and_flags_expiring_media(session):
    adapter = ig_adapter(session, lambda req: httpx.Response(200, json=IG_MEDIA))
    vids = list(adapter.fetch_account_videos("17841400000000000"))
    assert len(vids) == 1
    v = vids[0]
    assert v.external_id == "ig1"
    assert v.posted_at == datetime(2026, 9, 1, 10, 30, tzinfo=timezone.utc)
    assert v.hashtags == ["classicalmusic"]
    # Signed CDN link: must be downloaded on collection or it is lost.
    assert v.media_url and v.media_url_expires is True


def test_instagram_requests_post_april_2025_metrics(session):
    """`plays` and `impressions` were retired on 21 April 2025 (ADR 0001).

    Three calls per publication, each deliberately separate, so every
    requested metric across all of them is checked - asserting on one call
    would have missed a retired name in another:

      1. core metrics, the irreplaceable part of a pass;
      2. retention, two of whose three names Meta marks "in development";
      3. surface decomposition (M1, ADR 0008), one of whose names is
         unsupported for any media that is not a reel.

    The separation is the point. Meta fails a combined insights call
    WHOLE when any single name in it is unsupported or withdrawn, so
    mixing these would let a fragile metric take the core set down with
    it.
    """
    requested: list[list[str]] = []

    def handler(req):
        if "insights" in str(req.url):
            requested.append(req.url.params.get("metric", "").split(","))
            return httpx.Response(200, json=IG_INSIGHTS)
        return httpx.Response(200, json=IG_MEDIA)

    list(ig_adapter(session, handler).fetch_metrics("17841400000000000",
                                                   ["ig1"]))
    assert len(requested) == 3, (
        f"expected a core, a retention and a surface call, got "
        f"{len(requested)}")
    every = [m for call in requested for m in call]
    assert "views" in every
    assert "plays" not in every
    assert "impressions" not in every
    assert "clips_replays_count" not in every
    # The retention names must NOT be mixed into the core request: an
    # "in development" metric being withdrawn would then take the core
    # metrics down with it.
    assert "reels_skip_rate" not in requested[0]
    assert "reels_skip_rate" in requested[1]
    # Nor the surface names, for the same reason: `crossposted_views` is
    # rejected outright for every publication that is not a reel.
    core_and_retention = requested[0] + requested[1]
    assert "total_views" not in core_and_retention
    assert "crossposted_views" not in core_and_retention
    assert set(requested[2]) == {"total_views", "crossposted_views",
                                 "facebook_views"}


def test_instagram_metrics_mapped(session):
    def handler(req):
        return httpx.Response(200, json=IG_INSIGHTS)

    m = list(ig_adapter(session, handler).fetch_metrics("17841400000000000",
                                                       ["ig1"]))[0]
    assert (m.views, m.saves, m.shares) == (45111, 812, 668)


def test_instagram_190_error_marks_reauth(session):
    """Meta's 190-series means invalidated, not busy: retrying cannot help."""
    adapter = ig_adapter(session, lambda req: httpx.Response(200, json={
        "error": {"code": 190, "message": "Error validating access token"}}))
    with pytest.raises(ReauthRequired, match="190"):
        list(adapter.fetch_account_videos("17841400000000000"))
    assert adapter.store.get("instagram", "17841400000000000").needs_reauth


def test_instagram_unsupported_insight_does_not_abort_collection(session):
    """One media type without insights must not kill the whole run."""
    def handler(req):
        return httpx.Response(200, json={
            "error": {"code": 100, "message": "metric not supported"}})

    out = list(ig_adapter(session, handler).fetch_metrics(
        "17841400000000000", ["ig1", "ig2"]))
    assert out == []


# ---------------------------------------------------------------------------
# persistence
# ---------------------------------------------------------------------------

def test_collection_is_idempotent_across_runs(session):
    """Re-running a collection must not duplicate videos."""
    adapter = tiktok_adapter(
        session, lambda req: httpx.Response(200, json=TIKTOK_PAGE))
    creator = upsert_creator(session, platform="tiktok", handle="me",
                             is_own_account=True)
    for _ in range(2):
        for v in adapter.fetch_account_videos("me"):
            upsert_video(session, creator, v, source="official_api")
    session.commit()
    n = session.scalar(select(func.count()).select_from(Video))
    assert n == 2, f"re-running the collection produced {n} rows, expected 2"


def test_snapshots_are_append_only(session):
    """Two readings of the same video are two rows, not an overwrite — the
    trajectory is what fixed-age anchoring is derived from (plan 5.4)."""
    adapter = tiktok_adapter(
        session, lambda req: httpx.Response(200, json=TIKTOK_PAGE))
    creator = upsert_creator(session, platform="tiktok", handle="me",
                             is_own_account=True)
    vids = {v.external_id: upsert_video(session, creator, v,
                                        source="official_api")[0]
            for v in adapter.fetch_account_videos("me")}
    for _ in range(2):
        for m in adapter.fetch_metrics("me", ["7001"]):
            record_snapshot(session, vids[m.external_id], m)
    session.commit()
    rows = session.scalars(select(MetricsSnapshot).where(
        MetricsSnapshot.video_id == vids["7001"].id)).all()
    assert len(rows) == 2, "snapshots must append, never overwrite"
    assert all(r.views == 144292 for r in rows)


# ---------------------------------------------------------------------------
# Instagram retention metrics (watch time, skip rate)
# ---------------------------------------------------------------------------

def _ig_adapter_with(handler, session):
    """An Instagram adapter wired to a mock transport and a stored token."""
    session.add(OAuthToken(
        platform="instagram", account_ref="ig-1", access_token="t",
        expires_at=datetime.now(timezone.utc) + timedelta(days=30),
        needs_reauth=False))
    session.commit()
    ad = InstagramOfficialAdapter(TokenStore(session),
                                  client=mock_client(handler))
    ad._ig_user_id = lambda ref: "ig-1"                     # skip discovery
    return ad


def test_retention_metrics_are_collected_and_floats_survive(session):
    """`reels_skip_rate` arrives as a float; an int-only filter dropped it."""
    def handler(request: httpx.Request) -> httpx.Response:
        metric = request.url.params.get("metric", "")
        if "ig_reels_avg_watch_time" in metric:
            return httpx.Response(200, json={"data": [
                {"name": "ig_reels_avg_watch_time",
                 "values": [{"value": 6006}]},
                {"name": "ig_reels_video_view_total_time",
                 "values": [{"value": 3411578}]},
                {"name": "reels_skip_rate", "values": [{"value": 57.7}]},
            ]})
        return httpx.Response(200, json={"data": [
            {"name": "views", "values": [{"value": 1000}]},
            {"name": "saved", "values": [{"value": 12}]},
        ]})

    ad = _ig_adapter_with(handler, session)
    m = list(ad.fetch_metrics("ig-1", ["m1"]))[0]
    assert m.views == 1000
    assert m.saves == 12
    assert m.raw["reels_skip_rate"] == pytest.approx(57.7)
    assert m.raw["ig_reels_avg_watch_time"] == 6006
    assert m.raw["retention_status"] == "ok"
    assert m.raw["retention_units"]["ig_reels_avg_watch_time"] == "ms"


def test_retention_failure_does_not_lose_the_core_snapshot(session):
    """The retention call is separate precisely so this cannot cost metrics."""
    def handler(request: httpx.Request) -> httpx.Response:
        metric = request.url.params.get("metric", "")
        if "reels_skip_rate" in metric:
            return httpx.Response(400, json={"error": {
                "code": 100, "message": "metric[0] must be one of..."}})
        return httpx.Response(200, json={"data": [
            {"name": "views", "values": [{"value": 777}]},
        ]})

    ad = _ig_adapter_with(handler, session)
    out = list(ad.fetch_metrics("ig-1", ["m1"]))
    assert len(out) == 1, "a retention failure lost the whole snapshot"
    assert out[0].views == 777
    assert out[0].raw["retention_status"] == "unavailable"
    assert "reels_skip_rate" not in out[0].raw


def test_absent_retention_is_unknown_not_zero(session):
    """A missing watch time is not a watch time of zero."""
    def handler(request: httpx.Request) -> httpx.Response:
        metric = request.url.params.get("metric", "")
        if "reels_skip_rate" in metric:
            return httpx.Response(200, json={"data": []})
        return httpx.Response(200, json={"data": [
            {"name": "views", "values": [{"value": 5}]}]})

    ad = _ig_adapter_with(handler, session)
    raw = list(ad.fetch_metrics("ig-1", ["m1"]))[0].raw
    assert raw["retention_status"] == "empty"
    for k in ("ig_reels_avg_watch_time", "reels_skip_rate"):
        assert k not in raw, f"{k} was invented as a value"
