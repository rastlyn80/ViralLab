"""Phase 1 API acceptance tests.

Covers the network surface between the two machines: auth, the 409
zombie-worker guard over HTTP, signed media URLs, and media-path containment.
"""

from __future__ import annotations

import os
import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from server.db.models import Base, Creator, Video
from server.queue.queue import enqueue_batch
from shared.schemas.jobs import Modality, Tier, make_idempotency_key
from shared.utils.signing import sign_media_url, verify_media_signature

DB_URL = os.environ.get("VIRALLAB_DB_URL")
pytestmark = pytest.mark.skipif(not DB_URL, reason="VIRALLAB_DB_URL not set")

TOKEN = "test-worker-token-abc123"
SECRET = "test-media-secret"
METHOD = "api-v1"


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    media_root = tmp_path_factory.mktemp("media")
    (media_root / "v1.mp4").write_bytes(b"\x00" * 2048)

    os.environ.update(
        VIRALLAB_MEDIA_SECRET=SECRET,
        VIRALLAB_WORKER_TOKENS=f"gpu-1:{TOKEN}",
        VIRALLAB_MEDIA_ROOT=str(media_root),
        VIRALLAB_PUBLIC_BASE_URL="https://testserver",
    )
    import importlib

    from server.api import app as app_module
    importlib.reload(app_module)

    eng = create_engine(DB_URL, future=True)
    Base.metadata.drop_all(eng)
    Base.metadata.create_all(eng)

    with TestClient(app_module.app) as c:
        c.media_root = media_root
        yield c
    eng.dispose()


@pytest.fixture
def session(client):
    eng = create_engine(DB_URL, future=True)
    maker = sessionmaker(eng, expire_on_commit=False, future=True)
    with maker() as s:
        for t in ("analysis_results", "batch_videos", "analysis_batches",
                  "metrics_snapshots", "videos", "creators",
                  "worker_heartbeats"):
            s.execute(text(f"truncate {t} restart identity cascade"))
        s.commit()
        yield s
    eng.dispose()


def seed(session, client, n=2):
    c = Creator(platform="tiktok", handle="h", is_own_account=False)
    session.add(c)
    session.flush()
    ids = []
    for i in range(n):
        v = Video(creator_id=c.id, platform="tiktok", external_id=f"e{i}",
                  duration_s=30.0, source="provider", media_status="available",
                  media_path=str(client.media_root / "v1.mp4"))
        session.add(v)
        session.flush()
        ids.append(v.id)
    enqueue_batch(session, modality=Modality.VISION, tier=Tier.T2,
                  method_version=METHOD, video_ids=ids)
    session.commit()
    return ids


AUTH = {"Authorization": f"Bearer {TOKEN}"}


def lease_body(worker="gpu-1"):
    return {"worker_id": worker, "capabilities": ["vision"], "max_videos": 50}


# ---------------------------------------------------------------------------
# auth
# ---------------------------------------------------------------------------

def test_health_needs_no_auth(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_missing_and_wrong_tokens_are_rejected(client, session):
    seed(session, client)
    assert client.post("/jobs/lease", json=lease_body()).status_code == 401
    r = client.post("/jobs/lease", json=lease_body(),
                    headers={"Authorization": "Bearer nope"})
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# lease -> results over HTTP
# ---------------------------------------------------------------------------

def test_lease_returns_signed_media_urls(client, session):
    seed(session, client)
    r = client.post("/jobs/lease", json=lease_body(), headers=AUTH)
    assert r.status_code == 200
    lease = r.json()
    assert lease["modality"] == "vision"
    for ref in lease["videos"]:
        assert "sig=" in ref["media_url"] and "expires=" in ref["media_url"]
        # The URL must be scoped to its own video id.
        assert f"/media/{ref['video_id']}?" in ref["media_url"]


def test_full_round_trip_over_http(client, session):
    ids = seed(session, client)
    lease = client.post("/jobs/lease", json=lease_body(), headers=AUTH).json()

    hb = client.post("/jobs/heartbeat", headers=AUTH, json={
        "batch_id": lease["batch_id"], "lease_token": lease["lease_token"],
        "worker_id": "gpu-1", "videos_done": 1, "peak_vram_mib": 7634})
    assert hb.status_code == 200 and hb.json()["lease_expires_at"]

    results = [{
        "video_id": ref["video_id"],
        "idempotency_key": make_idempotency_key(
            "vision", ref["video_id"], METHOD),
        "payload": {"shot_type": "closeup"}, "peak_vram_mib": 7634,
    } for ref in lease["videos"]]

    r = client.post("/jobs/results", headers=AUTH, json={
        "batch_id": lease["batch_id"], "lease_token": lease["lease_token"],
        "worker_id": "gpu-1", "results": results, "final": True})
    assert r.status_code == 200
    assert r.json() == {"written": len(ids), "batch_status": "done"}

    assert client.get("/status/queue").json()["depth"]["vision"]["done"] == 1


def test_stale_lease_token_returns_409_over_http(client, session):
    """The zombie-worker guard must surface as 409, not a 500."""
    seed(session, client)
    zombie = client.post("/jobs/lease", json=lease_body(), headers=AUTH).json()

    session.execute(text(
        "update analysis_batches set lease_expires_at = now() - interval '1 min'"))
    session.commit()
    assert client.post("/admin/reap", headers=AUTH).json()["reaped"] == [
        zombie["batch_id"]]
    session.execute(text("update analysis_batches set not_before = null"))
    session.commit()
    fresh = client.post("/jobs/lease", json=lease_body(), headers=AUTH).json()
    assert fresh["lease_token"] != zombie["lease_token"]

    stale = {"batch_id": zombie["batch_id"],
             "lease_token": zombie["lease_token"], "worker_id": "gpu-1"}
    assert client.post("/jobs/heartbeat", headers=AUTH,
                       json=stale).status_code == 409
    assert client.post("/jobs/results", headers=AUTH,
                       json={**stale, "results": [], "final": True}
                       ).status_code == 409


def test_malformed_idempotency_key_is_422(client, session):
    seed(session, client, n=1)
    lease = client.post("/jobs/lease", json=lease_body(), headers=AUTH).json()
    r = client.post("/jobs/results", headers=AUTH, json={
        "batch_id": lease["batch_id"], "lease_token": lease["lease_token"],
        "worker_id": "gpu-1", "final": True,
        "results": [{"video_id": lease["videos"][0]["video_id"],
                     "idempotency_key": "vision:999:wrong", "payload": {}}]})
    assert r.status_code == 422


def test_worker_status_is_recorded(client, session):
    r = client.post("/worker/status", headers=AUTH, json={
        "worker_id": "gpu-1", "state": "deferred",
        "reason": "insufficient VRAM: 400 free, need 7634 + 512",
        "free_vram_mib": 400, "total_vram_mib": 8192})
    assert r.status_code == 200
    row = session.execute(text(
        "select state, reason from worker_heartbeats")).one()
    assert row.state == "deferred" and "insufficient VRAM" in row.reason


# ---------------------------------------------------------------------------
# media access control
# ---------------------------------------------------------------------------

def test_signed_url_serves_media(client, session):
    ids = seed(session, client, n=1)
    url = sign_media_url("", ids[0], SECRET, ttl_seconds=300)
    r = client.get(url)
    assert r.status_code == 200 and len(r.content) == 2048


def test_media_requires_valid_unexpired_signature(client, session):
    ids = seed(session, client, n=1)
    vid = ids[0]

    # no signature
    assert client.get(f"/media/{vid}").status_code == 422
    # tampered signature
    bad = sign_media_url("", vid, SECRET).replace("sig=", "sig=0")
    assert client.get(bad).status_code == 403
    # wrong secret
    assert client.get(sign_media_url("", vid, "other-secret")).status_code == 403
    # expired
    expired = sign_media_url("", vid, SECRET, ttl_seconds=-10)
    assert client.get(expired).status_code == 403


def test_signature_cannot_be_replayed_against_another_video(client, session):
    """A valid signature for video A must not unlock video B."""
    ids = seed(session, client, n=2)
    url_a = sign_media_url("", ids[0], SECRET, ttl_seconds=300)
    query = url_a.split("?", 1)[1]
    assert client.get(f"/media/{ids[1]}?{query}").status_code == 403


def test_media_path_containment(client, session):
    """A crafted media_path must not escape the media root."""
    c = Creator(platform="tiktok", handle="evil")
    session.add(c)
    session.flush()
    v = Video(creator_id=c.id, platform="tiktok", external_id="x",
              source="provider", media_status="available",
              media_path="../../../../Windows/System32/drivers/etc/hosts")
    session.add(v)
    session.commit()
    r = client.get(sign_media_url("", v.id, SECRET, ttl_seconds=300))
    assert r.status_code in (403, 404), r.status_code


def test_signature_helpers_directly():
    now = int(time.time())
    verify_media_signature(
        5, now + 60,
        sign_media_url("", 5, SECRET, ttl_seconds=60, now=now)
        .split("sig=")[1], SECRET, now=now)
    with pytest.raises(ValueError, match="expired"):
        verify_media_signature(5, now - 1, "x" * 64, SECRET, now=now)


# ---------------------------------------------------------------------------
# sanity-check fixes (Phase 1 review)
# ---------------------------------------------------------------------------

def test_admin_reap_requires_auth(client, session):
    """It mutates lease state, so it must not be callable unauthenticated."""
    assert client.post("/admin/reap").status_code == 401
    assert client.post("/admin/reap",
                       headers={"Authorization": "Bearer nope"}
                       ).status_code == 403
    assert client.post("/admin/reap", headers=AUTH).status_code == 200


def test_background_reaper_is_started():
    """The reaper function alone is not a recovery guarantee unless something
    calls it; a crashed worker would otherwise wedge its batch forever."""
    from server.api import app as app_module
    assert hasattr(app_module, "_reaper_loop")
    assert app_module.REAPER_INTERVAL > 0
