"""Web UI API tests (ADR 0006).

What these are actually checking is not "does the endpoint return 200" but
the three honesty properties the product vision makes load-bearing:

  * a metric the platform does not report comes back **null**, never 0
  * every performance figure travels with the **age** it was observed at
  * a machine proposal and a human decision are **distinguishable states**

Plus the security posture: the session gate, and that a state-changing call
cannot be made without the header a cross-site form cannot set.
"""

from __future__ import annotations

import importlib
import os
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from server.db.models import (Base, Creator, LocalMaster, MetricsSnapshot,
                              OAuthToken, Performance, PublicationLink, Video,
                              VideoMasterLink)

from tests.conftest import TEST_DB_URL, assert_disposable

DB_URL = TEST_DB_URL
pytestmark = pytest.mark.skipif(
    not DB_URL, reason="VIRALLAB_TEST_DB_URL not set")
if DB_URL:
    assert_disposable(DB_URL)

SECRET = "ui-test-media-secret-0123456789"
PASSWORD = "correct-horse-battery"
NOW = datetime(2026, 10, 24, 12, 0, tzinfo=timezone.utc)


@pytest.fixture(scope="module")
def ctx(tmp_path_factory):
    """App + seeded disposable database.

    Module-scoped because reloading the app module per test would be slow
    and the seeded data is read-only for most tests; the two that mutate
    restore what they changed.
    """
    media_root = tmp_path_factory.mktemp("ui-media")
    (media_root / "ig_IG1.mp4").write_bytes(b"\x00" * 4096)

    os.environ.update(
        VIRALLAB_DB_URL=DB_URL,
        VIRALLAB_MEDIA_SECRET=SECRET,
        VIRALLAB_WORKER_TOKENS="gpu-1:tok",
        VIRALLAB_MEDIA_ROOT=str(media_root),
        VIRALLAB_UI_PASSWORD=PASSWORD,
    )
    from server.api import deps as deps_module
    from server.api import ui as ui_module
    importlib.reload(ui_module)
    from server.api import app as app_module
    importlib.reload(app_module)

    eng = create_engine(DB_URL, future=True)
    Base.metadata.drop_all(eng)
    Base.metadata.create_all(eng)
    maker = sessionmaker(eng, expire_on_commit=False, future=True)

    with maker() as s:
        s.add_all([
            OAuthToken(platform="tiktok", account_ref="tt-acct",
                       access_token="a", refresh_token="r",
                       expires_at=NOW + timedelta(hours=20),
                       needs_reauth=False),
            OAuthToken(platform="instagram", account_ref="ig-acct",
                       access_token="a", expires_at=None, needs_reauth=False),
        ])
        tt = Creator(platform="tiktok", handle="@owner", external_id="tt-acct",
                     is_own_account=True)
        ig = Creator(platform="instagram", handle="@owner",
                     external_id="ig-acct", is_own_account=True)
        s.add_all([tt, ig])
        s.flush()

        perf = Performance(title="Chopin Ballade", status="inferred")
        s.add(perf)
        s.flush()
        master = LocalMaster(quick_id="q" * 8, path="D:/Videos/ballade.mp4",
                             filename="ballade.mp4", duration_s=31.4,
                             performance_id=perf.id)
        s.add(master)
        s.flush()

        v_ig = Video(creator_id=ig.id, platform="instagram",
                     external_id="IG1", caption="Chopin Ballade #piano",
                     hashtags=["piano"], posted_at=NOW - timedelta(days=40),
                     duration_s=31.2, source="official_api",
                     media_status="available",
                     media_path=str(media_root / "ig_IG1.mp4"))
        v_tt = Video(creator_id=tt.id, platform="tiktok", external_id="TT1",
                     caption="Chopin Ballade #pianotok",
                     posted_at=NOW - timedelta(days=40), duration_s=31.0,
                     source="official_api", media_status="absent",
                     cover_url="https://cdn.example/cover.jpg")
        v_bare = Video(creator_id=ig.id, platform="instagram",
                       external_id="IG2", caption="an image post",
                       posted_at=NOW - timedelta(days=5),
                       source="official_api", media_status="unsupported",
                       media_reason="not_a_video:IMAGE")
        s.add_all([v_ig, v_tt, v_bare])
        s.flush()

        # Instagram reports saves and retention; TikTok reports neither, and
        # the difference must survive all the way to the API response.
        s.add(MetricsSnapshot(
            video_id=v_ig.id, captured_at=NOW, views=45111, likes=3195,
            comments=57, shares=668, saves=812, is_rounded=False,
            raw={"ig_reels_avg_watch_time": 6006,
                 "ig_reels_video_view_total_time": 3411578,
                 "reels_skip_rate": 57.7}))
        s.add(MetricsSnapshot(
            video_id=v_tt.id, captured_at=NOW, views=1388, likes=35,
            comments=0, shares=0, saves=None, is_rounded=False,
            raw={"view_count": 1388}))

        s.add(VideoMasterLink(
            video_id=v_ig.id, master_id=master.id, score=0.97,
            frame_overlap=1.0, duration_delta_s=0.2, status="pending",
            method="dhash+audiofp-v3", audio_similarity=0.98,
            audio_method="spectral", audio_offset_s=0.0,
            decided_by="matcher", evidence={"coverage": 1.0}))
        s.flush()
        a, b = sorted((v_ig.id, v_tt.id))
        s.add(PublicationLink(
            video_a_id=a, video_b_id=b, relationship_type="same_variant",
            status="proposed", score=0.94, method="metadata-v1",
            decided_by="matcher",
            evidence={"tier": "strong", "agreeing": 3, "margin": 0.4,
                      "signals": {"duration": 1.0, "date": 1.0,
                                  "caption": 0.8}}))
        s.commit()
        ids = {"ig": v_ig.id, "tt": v_tt.id, "bare": v_bare.id}

    with TestClient(app_module.app, base_url="https://testserver") as c:
        yield c, maker, ids
    eng.dispose()


@pytest.fixture
def client(ctx):
    c, _maker, _ids = ctx
    c.cookies.clear()
    r = c.post("/api/session", json={"password": PASSWORD})
    assert r.status_code == 200, r.text
    return c


@pytest.fixture
def ids(ctx):
    return ctx[2]


# ---------------------------------------------------------------------------
# authentication
# ---------------------------------------------------------------------------

def test_unauthenticated_reads_are_refused(ctx):
    c, _m, _i = ctx
    c.cookies.clear()
    assert c.get("/api/overview").status_code == 401
    assert c.get("/api/videos").status_code == 401
    # The session endpoint itself must stay reachable, or there is no way in.
    state = c.get("/api/session")
    assert state.status_code == 200
    assert state.json() == {"authenticated": False, "auth_configured": True}


def test_wrong_password_is_rejected(ctx):
    c, _m, _i = ctx
    c.cookies.clear()
    assert c.post("/api/session", json={"password": "nope"}).status_code == 401


def test_session_cookie_is_httponly_and_strict(ctx):
    c, _m, _i = ctx
    c.cookies.clear()
    r = c.post("/api/session", json={"password": PASSWORD})
    cookie = r.headers.get("set-cookie", "")
    assert "HttpOnly" in cookie
    assert "SameSite=strict" in cookie.replace("Strict", "strict")
    assert "Secure" in cookie


def test_status_endpoints_are_no_longer_open(ctx):
    """These disclosed queue depth and collection history to anything on the
    tailnet. Recorded as a minor outstanding issue; now closed."""
    c, _m, _i = ctx
    c.cookies.clear()
    assert c.get("/status/queue").status_code == 401
    assert c.get("/status/collection").status_code == 401


def test_mutation_requires_the_csrf_header(client, ids, ctx):
    """A cross-site form can carry the cookie but cannot set this header."""
    _c, maker, _i = ctx
    with maker() as s:
        link_id = s.scalars(
            text("select id from video_master_links limit 1")).one()
    r = client.post(f"/api/review/master-links/{link_id}",
                    json={"answer": "confirmed"},
                    headers={"X-ViralLab-UI": ""})
    assert r.status_code == 403
    assert "x-virallab-ui" in r.json()["detail"].lower()


# ---------------------------------------------------------------------------
# overview
# ---------------------------------------------------------------------------

def test_overview_reports_both_platforms_and_health(client):
    o = client.get("/api/overview").json()
    plats = {p["platform"]: p for p in o["platforms"]}
    assert plats["tiktok"]["connected"] is True
    assert plats["tiktok"]["needs_reauth"] is False
    assert plats["instagram"]["publications"] == 2
    assert plats["tiktok"]["publications"] == 1
    # The Instagram Page token does not expire (ADR 0005); that must not be
    # rendered as an expiry of "now".
    assert plats["instagram"]["token_expires_at"] is None
    assert plats["tiktok"]["token_expires_at"] is not None


def test_overview_counts_distinct_capture_days_not_snapshots(client):
    """One capture day means no fixed-age anchor is computable, which is the
    single most important fact about the current corpus."""
    o = client.get("/api/overview").json()
    assert o["snapshot_days"] == 1
    assert sum(p["snapshots"] for p in o["platforms"]) == 2


def test_overview_media_coverage_excludes_non_video(client):
    o = client.get("/api/overview").json()
    ig = next(m for m in o["media"] if m["platform"] == "instagram")
    assert ig["total"] == 2
    # One of the two is an image post, so it is not preservable video.
    assert ig["preservable"] == 1
    assert ig["preserved"] == 1
    assert ig["coverage_pct"] == 100.0
    assert ig["by_reason_class"]["not_a_video"] == 1


def test_overview_separates_machine_and_human_review_state(client):
    r = client.get("/api/overview").json()["review"]
    assert r["master_links_pending"] == 1
    assert r["master_links_human_decided"] == 0
    assert r["publication_links_proposed"] == 1
    assert r["publication_links_confirmed"] == 0
    assert r["performances_inferred"] == 1
    assert r["performances_confirmed"] == 0


# ---------------------------------------------------------------------------
# library
# ---------------------------------------------------------------------------

def test_video_list_pairs_every_view_count_with_an_age(client):
    page = client.get("/api/videos").json()
    assert page["total"] == 3
    with_views = [i for i in page["items"] if i["latest_views"] is not None]
    assert with_views, "no item carried a view count"
    for item in with_views:
        assert item["latest_age_days"] is not None, (
            f"{item['external_id']} reported views with no age, which makes "
            f"it uncomparable")
        assert item["latest_age_days"] == 40


def test_video_list_filters(client):
    assert client.get("/api/videos?platform=tiktok").json()["total"] == 1
    assert client.get("/api/videos?has_media=true").json()["total"] == 1
    assert client.get("/api/videos?linked=yes").json()["total"] == 1
    assert client.get("/api/videos?linked=no").json()["total"] == 2
    assert client.get("/api/videos?q=pianotok").json()["total"] == 1
    assert client.get("/api/videos?q=ballade").json()["total"] == 2


def test_video_list_rejects_bad_parameters(client):
    assert client.get("/api/videos?platform=youtube").status_code == 422
    assert client.get("/api/videos?limit=5000").status_code == 422
    assert client.get("/api/videos?order=views").status_code == 422


def test_video_list_paginates(client):
    first = client.get("/api/videos?limit=2&offset=0").json()
    second = client.get("/api/videos?limit=2&offset=2").json()
    assert len(first["items"]) == 2
    assert len(second["items"]) == 1
    assert first["total"] == second["total"] == 3
    assert {i["id"] for i in first["items"]} & {
        i["id"] for i in second["items"]} == set()


# ---------------------------------------------------------------------------
# detail — the honesty properties
# ---------------------------------------------------------------------------

def test_tiktok_saves_are_null_not_zero(client, ids):
    """TikTok exposes no saves field. Rendering that as 0 would invent a
    finding; CLAUDE.md §3 forbids it."""
    d = client.get(f"/api/videos/{ids['tt']}").json()
    reading = d["history"][0]
    assert reading["saves"] is None, "an unreported metric came back as a value"
    assert reading["views"] == 1388
    # Retention is Instagram-only, so these must be absent too.
    assert reading["avg_watch_time_ms"] is None
    assert reading["skip_rate_pct"] is None


def test_instagram_retention_is_surfaced_with_units_intact(client, ids):
    d = client.get(f"/api/videos/{ids['ig']}").json()
    reading = d["history"][0]
    assert reading["saves"] == 812
    assert reading["avg_watch_time_ms"] == 6006
    assert reading["total_view_time_ms"] == 3411578
    assert reading["skip_rate_pct"] == pytest.approx(57.7)


def test_fixed_age_anchors_say_why_rather_than_interpolating(client, ids):
    """One snapshot cannot bracket day 7, so the anchor must be reported as
    unavailable - never computed."""
    d = client.get(f"/api/videos/{ids['ig']}").json()
    assert set(d["anchors"]) == {"views@7d", "views@30d", "views@90d"}
    for anchor, state in d["anchors"].items():
        assert isinstance(state, str)
        assert state != "available", (
            f"{anchor} claimed to be available from a single snapshot")
        assert "bracketing" in state or "no snapshots" in state


def test_detail_exposes_a_signed_media_url_and_never_a_path(client, ids):
    d = client.get(f"/api/videos/{ids['ig']}").json()
    assert d["media_url"], "stored media produced no playable URL"
    assert "sig=" in d["media_url"] and "expires=" in d["media_url"]
    body = client.get(f"/api/videos/{ids['ig']}").text
    assert "media_path" not in body
    for leak in ("/tmp", "D:/", "D:\\\\", "ui-media"):
        assert leak not in body, f"a filesystem path ({leak}) reached the client"


def test_unsupported_media_has_no_url_but_keeps_its_reason(client, ids):
    d = client.get(f"/api/videos/{ids['bare']}").json()
    assert d["media_url"] is None
    assert d["media_status"] == "unsupported"
    assert d["media_reason"] == "not_a_video:IMAGE"


def test_detail_carries_link_state_and_audio_method(client, ids):
    d = client.get(f"/api/videos/{ids['ig']}").json()
    link = d["master_links"][0]
    assert link["status"] == "pending"
    assert link["decided_by"] == "matcher"
    # The descriptor is shown because an envelope-era score is materially
    # weaker evidence than a spectral one.
    assert link["audio_method"] == "spectral"
    assert link["performance"] == "Chopin Ballade"


def test_cross_platform_link_is_visible_from_both_sides(client, ids):
    for key in ("ig", "tt"):
        d = client.get(f"/api/videos/{ids[key]}").json()
        assert len(d["publication_links"]) == 1
        pl = d["publication_links"][0]
        assert pl["status"] == "proposed"
        assert pl["decided_by"] == "matcher"
        assert pl["other_platform"] != d["platform"]
        assert pl["other_latest_age_days"] == 40


def test_missing_video_is_404(client):
    assert client.get("/api/videos/999999").status_code == 404


# ---------------------------------------------------------------------------
# review queues and decisions
# ---------------------------------------------------------------------------

def test_master_queue_carries_everything_needed_to_decide(client):
    items = client.get("/api/review/master-links?status=pending").json()
    assert len(items) == 1
    it = items[0]
    assert it["link"]["audio_similarity"] == pytest.approx(0.98)
    assert it["video_media_url"], "the publication was not playable"
    assert it["video_caption"]
    # The master path is sent deliberately, for the local bridge; the server
    # itself never serves it.
    assert it["master_local_path"] == "D:/Videos/ballade.mp4"


def test_pair_queue_returns_both_sides_in_full(client):
    pairs = client.get("/api/review/publication-links").json()
    assert len(pairs) == 1
    p = pairs[0]
    assert {p["a"]["platform"], p["b"]["platform"]} == {"tiktok", "instagram"}
    assert p["evidence"]["tier"] == "strong"
    ig_side = p["a"] if p["a"]["platform"] == "instagram" else p["b"]
    tt_side = p["b"] if p["a"]["platform"] == "instagram" else p["a"]
    assert ig_side["media_url"], "the Instagram side was not playable"
    # TikTok returns no media file, so only a cover exists.
    assert tt_side["media_url"] is None
    assert tt_side["cover_url"]


def test_skip_changes_nothing(client, ctx):
    _c, maker, _i = ctx
    items = client.get("/api/review/master-links").json()
    link_id = items[0]["link"]["id"]
    r = client.post(f"/api/review/master-links/{link_id}",
                    json={"answer": "skip"},
                    headers={"X-ViralLab-UI": "1"})
    assert r.status_code == 200
    assert r.json()["status"] == "pending"
    with maker() as s:
        row = s.get(VideoMasterLink, link_id)
        assert row.status == "pending"
        assert row.decided_by == "matcher"


def test_a_decision_is_attributed_to_the_human_and_then_restored(client, ctx):
    _c, maker, _i = ctx
    items = client.get("/api/review/master-links").json()
    link_id = items[0]["link"]["id"]
    r = client.post(f"/api/review/master-links/{link_id}",
                    json={"answer": "variant", "notes": "different export"},
                    headers={"X-ViralLab-UI": "1"})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "variant"
    assert body["decided_by"] == "human"
    assert body["decided_at"] is not None
    assert body["remaining"] == 0

    with maker() as s:
        row = s.get(VideoMasterLink, link_id)
        assert row.status == "variant"
        assert row.decided_by == "human"
        assert row.notes == "different export"
        # Restore, so the module-scoped fixture stays usable.
        row.status, row.decided_by, row.decided_at = "pending", "matcher", None
        row.notes = None
        s.commit()


def test_platform_edit_records_the_relationship_not_just_the_status(client,
                                                                    ctx):
    """"Same performance, different edit per platform" is a real
    relationship. It previously had nowhere to go, so it had to be recorded
    as a falsehood either way."""
    _c, maker, _i = ctx
    pairs = client.get("/api/review/publication-links").json()
    pid = pairs[0]["id"]
    r = client.post(f"/api/review/publication-links/{pid}",
                    json={"answer": "platform_edit"},
                    headers={"X-ViralLab-UI": "1"})
    assert r.status_code == 200
    with maker() as s:
        row = s.get(PublicationLink, pid)
        assert row.status == "confirmed"
        assert row.relationship_type == "platform_edit"
        assert row.decided_by == "human"
        row.status, row.relationship_type = "proposed", "same_variant"
        row.decided_by, row.decided_at = "matcher", None
        s.commit()


def test_an_invalid_answer_is_refused(client):
    items = client.get("/api/review/master-links").json()
    link_id = items[0]["link"]["id"]
    r = client.post(f"/api/review/master-links/{link_id}",
                    json={"answer": "probably"},
                    headers={"X-ViralLab-UI": "1"})
    assert r.status_code == 400


def test_deciding_a_missing_link_is_404(client):
    r = client.post("/api/review/master-links/999999",
                    json={"answer": "confirmed"},
                    headers={"X-ViralLab-UI": "1"})
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# capabilities — the anti-fabrication contract
# ---------------------------------------------------------------------------

def test_built_capabilities_are_reported_as_built(client):
    """Phase 4 delivered targeted creator research and breakthrough
    analysis, so they must stop claiming to be unavailable."""
    caps = {c["key"]: c for c in client.get("/api/capabilities").json()[
        "capabilities"]}
    for key in ("own_accounts", "review", "creator_analysis",
                "breakthrough"):
        assert caps[key]["implemented"] is True, f"{key} should be built"
    # Even a built capability states its remaining limits.
    assert caps["breakthrough"]["blocked_by"], (
        "breakthrough analysis must still say what it cannot establish")


def test_unbuilt_capabilities_report_honestly_and_return_no_rows(client):
    caps = {c["key"]: c for c in client.get("/api/capabilities").json()[
        "capabilities"]}
    for key in ("discovery", "multimodal", "recommendations"):
        cap = caps[key]
        assert cap["implemented"] is False, f"{key} claims to be built"
        assert cap["blocked_by"], f"{key} gives no reason for being unavailable"
        assert cap["phase"]
        # Counts may exist but must be real; nothing is invented.
        assert all(isinstance(v, int) for v in cap["counts"].values())
    assert caps["discovery"]["counts"]["discovered_creators"] == 0


def test_discovery_names_the_spending_gate(client):
    """Discovery cannot run until a budget is authorised, and the UI must
    say so rather than offering a button that fails."""
    caps = {c["key"]: c for c in client.get("/api/capabilities").json()[
        "capabilities"]}
    blockers = " ".join(caps["discovery"]["blocked_by"]).lower()
    assert "spending" in blockers or "cap" in blockers


# ---------------------------------------------------------------------------
# the static UI is actually served
# ---------------------------------------------------------------------------

def test_the_spa_is_mounted(ctx):
    c, _m, _i = ctx
    r = c.get("/")
    assert r.status_code == 200
    assert "ViralLab" in r.text
    assert 'src="/app.js"' in r.text
    for asset in ("/app.js", "/api.js", "/components.js", "/views.js",
                  "/styles.css", "/types.js"):
        assert c.get(asset).status_code == 200, f"{asset} is not served"


def test_api_routes_are_not_shadowed_by_the_static_mount(client):
    """The SPA is mounted at "/" so route order matters: an API path must
    still reach the API."""
    assert client.get("/api/overview").status_code == 200
    assert client.get("/health").status_code == 200
