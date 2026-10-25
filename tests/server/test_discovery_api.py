"""HTTP contract for the Phase 4.5 surfaces.

What this file is actually checking, beyond "the route returns 200":

  * **a read can never spend.** The estimate endpoint is the one the
    interface calls to show a price, so it has to be free; a GET that could
    reach a provider would make the price itself cost money.
  * **authentication and the CSRF guard apply to every new route**, with the
    mutating ones refusing a request that has a session cookie but no
    custom header.
  * **unavailable is not zero.** Instagram candidates must serialise a null
    follower count, not a 0, all the way through Pydantic.
"""

from __future__ import annotations

import importlib
import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from server.db.models import Base, Creator
from tests.conftest import TEST_DB_URL, assert_disposable

DB_URL = TEST_DB_URL or ""
PASSWORD = "discovery-test-password"

pytestmark = pytest.mark.skipif(not DB_URL,
                                reason="VIRALLAB_TEST_DB_URL not set")

TABLES = ("analysis_results", "batch_videos", "analysis_batches",
          "breakthrough_candidates", "creator_collection_members",
          "creator_collections", "metrics_snapshots", "video_master_links",
          "publication_links", "videos", "creator_snapshots",
          "budget_reservations", "creators", "discovery_jobs")

H = {"X-ViralLab-UI": "1"}


@pytest.fixture(scope="module")
def ctx(tmp_path_factory):
    assert_disposable(DB_URL)
    os.environ.update(
        VIRALLAB_DB_URL=DB_URL,
        VIRALLAB_UI_PASSWORD=PASSWORD,
        VIRALLAB_MEDIA_SECRET="s" * 32,
        VIRALLAB_WORKER_TOKENS="gpu-1:tok",
        VIRALLAB_MEDIA_ROOT=str(tmp_path_factory.mktemp("disc-media")),
    )
    from server.api import deps as deps_module
    importlib.reload(deps_module)
    from server.api import ui as ui_module
    importlib.reload(ui_module)
    from server.api import research_api as ra_module
    importlib.reload(ra_module)
    from server.api import discovery_api as da_module
    importlib.reload(da_module)
    from server.api import app as app_module
    importlib.reload(app_module)

    eng = create_engine(DB_URL, future=True)
    Base.metadata.create_all(eng)
    maker = sessionmaker(eng, expire_on_commit=False, future=True)
    with maker() as s:
        for t in TABLES:
            s.execute(text(f"truncate {t} restart identity cascade"))
        s.commit()

    # The engine is created in the app lifespan, so the client must be
    # entered as a context manager.
    with TestClient(app_module.app, base_url="https://testserver") as c:
        yield c, maker
    eng.dispose()


@pytest.fixture
def client(ctx):
    c, _ = ctx
    c.cookies.clear()
    r = c.post("/api/session", json={"password": PASSWORD})
    assert r.status_code == 200
    return c


@pytest.fixture
def anon(ctx):
    c, _ = ctx
    c.cookies.clear()
    return c


# ---------------------------------------------------------------------------
# authentication and the write guard
# ---------------------------------------------------------------------------

NEW_READS = (
    "/api/discovery/niches",
    "/api/discovery/searches",
    "/api/collections",
    "/api/analysis/contract",
)


@pytest.mark.parametrize("path", NEW_READS)
def test_every_new_read_requires_a_session(anon, path):
    assert anon.get(path).status_code == 401


def test_every_new_write_requires_the_csrf_header(client):
    """A session cookie alone must not be enough to mutate anything."""
    cases = [
        ("post", "/api/discovery/search",
         {"kind": "keyword", "platform": "tiktok", "value": "x"}),
        ("post", "/api/collections", {"name": "x"}),
        ("post", "/api/analysis/requests", {"video_id": 1}),
    ]
    for method, path, body in cases:
        r = getattr(client, method)(path, json=body)
        assert r.status_code == 403, f"{path} accepted a request with no header"


# ---------------------------------------------------------------------------
# estimates are free
# ---------------------------------------------------------------------------

def test_an_estimate_is_free_and_describes_the_provider(client):
    r = client.get("/api/discovery/estimate", params={
        "kind": "keyword", "platform": "tiktok", "value": "classical piano",
        "limit": 50})
    assert r.status_code == 200
    body = r.json()
    assert body["provider"] == "fixture"
    assert body["is_fixture"] is True
    assert body["estimated_usd"] == 0.0
    assert body["supported"] is True
    assert body["coverage_warnings"], "an estimate must carry its limits"
    assert any("biased toward" in w for w in body["coverage_warnings"])


def test_an_estimate_reports_an_unsupported_combination(client):
    """`[PRIMARY]` No keyword search exists for the validated IG actors."""
    r = client.get("/api/discovery/estimate", params={
        "kind": "keyword", "platform": "instagram", "value": "piano"})
    assert r.status_code == 200
    body = r.json()
    assert body["supported"] is False
    assert "provider limit" in body["unsupported_reason"]


def test_an_estimate_rejects_a_bad_kind_or_platform(client):
    assert client.get("/api/discovery/estimate", params={
        "kind": "vibes", "platform": "tiktok", "value": "x"}
    ).status_code == 400
    assert client.get("/api/discovery/estimate", params={
        "kind": "keyword", "platform": "myspace", "value": "x"}
    ).status_code == 400
    assert client.get("/api/discovery/estimate", params={
        "kind": "keyword", "platform": "tiktok", "value": "  "}
    ).status_code == 400


# ---------------------------------------------------------------------------
# search and candidates
# ---------------------------------------------------------------------------

def test_a_search_returns_ranked_candidates_with_their_reasons(client):
    r = client.post("/api/discovery/search", headers=H, json={
        "kind": "keyword", "platform": "tiktok", "value": "classical piano",
        "limit": 50, "niche": "classical_piano", "force": True})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["is_fixture"] is True
    assert body["coverage_notes"]
    cands = body["candidates"]
    assert cands
    # Sorted by score, descending.
    assert [c["score"] for c in cands] == sorted(
        (c["score"] for c in cands), reverse=True)
    top = cands[0]
    assert top["handle"] == "demo_smallbreakout"
    assert top["reasons"], "a ranked candidate must say why"
    assert top["estimated_research_usd"] == 0.0


def test_instagram_candidates_serialise_a_null_follower_count(client):
    r = client.post("/api/discovery/search", headers=H, json={
        "kind": "hashtag", "platform": "instagram",
        "value": "classicalpiano", "limit": 50, "force": True})
    assert r.status_code == 200
    cands = r.json()["candidates"]
    assert cands
    for c in cands:
        assert c["followers_available"] is False
        assert c["follower_count"] is None, "unknown must not become 0"
        assert c["best_views_per_follower"] is None


def test_a_stored_result_set_reopens_for_free(client):
    r = client.post("/api/discovery/search", headers=H, json={
        "kind": "keyword", "platform": "tiktok", "value": "classical piano",
        "limit": 50, "force": True})
    job_id = r.json()["job_id"]

    again = client.get(f"/api/discovery/searches/{job_id}/candidates")
    assert again.status_code == 200
    assert len(again.json()) == len(r.json()["candidates"])

    history = client.get("/api/discovery/searches").json()
    assert any(h["job_id"] == job_id for h in history)
    row = next(h for h in history if h["job_id"] == job_id)
    assert row["publications"] > 0
    assert row["creators"] > 0


def test_candidates_for_an_unknown_job_is_404(client):
    assert client.get(
        "/api/discovery/searches/999999/candidates").status_code == 404


def test_an_expansion_plan_costs_nothing_and_states_a_total(client):
    r = client.post("/api/discovery/search", headers=H, json={
        "kind": "keyword", "platform": "tiktok", "value": "classical piano",
        "limit": 50, "force": True})
    job_id = r.json()["job_id"]
    plan = client.post("/api/discovery/expansion-plan", headers=H,
                       json={"job_id": job_id, "max_creators": 2})
    assert plan.status_code == 200
    body = plan.json()
    assert len(body["candidates"]) == 2
    assert body["total_estimated_usd"] == 0.0
    assert body["notes"]


# ---------------------------------------------------------------------------
# timeline and report
# ---------------------------------------------------------------------------

@pytest.fixture
def researched(client):
    r = client.post("/api/research/creators", headers=H, json={
        "platform": "tiktok", "handle": "demo_pianist", "limit": 200})
    assert r.status_code == 200
    return r.json()["creator_id"]


def test_the_timeline_contract_declares_its_mode(client, researched):
    r = client.get(f"/api/research/creators/{researched}/timeline")
    assert r.status_code == 200
    tl = r.json()
    assert tl["mode"] == "lifetime_exploratory"
    assert "NOT a trajectory" in tl["mode_label"]
    assert tl["points"]
    assert tl["caveats"]
    for p in tl["points"]:
        assert p["age_days"] is not None
        assert p["views_at_anchor"] is None
        assert p["media_preview_available"] is False


def test_the_timeline_marks_the_change_point_without_calibration(
        client, researched):
    tl = client.get(
        f"/api/research/creators/{researched}/timeline").json()
    cp = tl["change_point"]
    assert cp["classification"] == "inferred_breakthrough"
    assert cp["confidence_is_calibrated"] is False
    assert "not a probability" in cp["confidence_note"].lower()
    assert cp["missing_evidence"]
    assert sum(1 for p in tl["points"] if p["is_change_point"]) == 1


def test_the_report_serialises_every_section_with_a_basis(client,
                                                           researched):
    r = client.get(f"/api/research/creators/{researched}/report")
    assert r.status_code == 200
    body = r.json()
    assert body["provider"] == "fixture"
    assert body["data_limitations"]
    keys = {s["key"] for s in body["sections"]}
    assert "breakthrough" in keys and "repertoire" in keys
    for s in body["sections"]:
        assert s["basis"] in ("observed", "inferred", "unavailable")
    bt = next(s for s in body["sections"] if s["key"] == "breakthrough")
    assert bt["body"]["why_flagged"]
    assert bt["body"]["what_would_strengthen"]


def test_a_timeline_or_report_for_an_unknown_creator_is_404(client):
    assert client.get(
        "/api/research/creators/999999/timeline").status_code == 404
    assert client.get(
        "/api/research/creators/999999/report").status_code == 404


# ---------------------------------------------------------------------------
# collections
# ---------------------------------------------------------------------------

def test_a_collection_round_trips_through_the_api(client, ctx):
    _, maker = ctx
    name = "API Collection"
    r = client.post("/api/collections", headers=H, json={
        "name": name, "purpose": "control_cohort", "niche": "classical_piano",
        "notes": "did not break through"})
    assert r.status_code == 200
    col = r.json()
    assert col["purpose"] == "control_cohort"
    assert "Control group" in col["purpose_label"]

    search = client.post("/api/discovery/search", headers=H, json={
        "kind": "keyword", "platform": "tiktok", "value": "classical piano",
        "limit": 50, "force": True}).json()
    creator_id = search["candidates"][0]["creator_id"]

    added = client.post(f"/api/collections/{col['id']}/members", headers=H,
                        json={"creator_id": creator_id, "reason": "breakout"})
    assert added.status_code == 200
    assert added.json()["member_count"] == 1
    assert added.json()["members"][0]["reason"] == "breakout"
    # A control cohort does not get the "these are only hypotheses" caveat.
    assert not any("hypotheses" in c for c in added.json()["caveats"])

    removed = client.delete(
        f"/api/collections/{col['id']}/members/{creator_id}", headers=H)
    assert removed.status_code == 200
    assert removed.json()["member_count"] == 0

    assert client.delete(f"/api/collections/{col['id']}",
                         headers=H).status_code == 200


def test_adding_an_own_account_to_a_collection_is_refused(client, ctx):
    _, maker = ctx
    with maker() as s:
        own = Creator(platform="tiktok", handle="own_acct_api",
                      external_id="own-api", is_own_account=True,
                      source="own_api")
        s.add(own)
        s.commit()
        own_id = own.id

    col = client.post("/api/collections", headers=H,
                      json={"name": "Isolation check"}).json()
    r = client.post(f"/api/collections/{col['id']}/members", headers=H,
                    json={"creator_id": own_id})
    assert r.status_code == 400
    assert "own accounts" in r.json()["detail"]
    client.delete(f"/api/collections/{col['id']}", headers=H)


def test_an_unknown_collection_is_404(client):
    assert client.get("/api/collections/999999").status_code == 404
    assert client.delete("/api/collections/999999",
                         headers=H).status_code == 404


# ---------------------------------------------------------------------------
# analysis seam
# ---------------------------------------------------------------------------

def test_the_feature_contract_is_published(client):
    r = client.get("/api/analysis/contract")
    assert r.status_code == 200
    body = r.json()
    assert body["version"] == "features-v1"
    assert {"audio", "vision", "cross_platform"} <= set(body["modalities"])
    fields = body["modalities"]["vision"]["fields"]
    # The nine things the next phase has to identify.
    assert "hook_description" in fields
    assert "on_screen_text" in fields
    assert "camera_angle" in fields
    assert "lighting" in fields
    assert "cut_count" in fields
    assert "emotional_presentation" in fields
    audio = body["modalities"]["audio"]["fields"]
    assert "musical_work" in audio and "composer" in audio
    assert "speech_present" in audio
    assert "needs_human_review" in body["required_item_fields"]
    assert "confidence_calibrated" in body["required_item_fields"]


def test_analysis_of_a_fixture_publication_is_refused(client, researched):
    tl = client.get(
        f"/api/research/creators/{researched}/timeline").json()
    video_id = tl["points"][0]["video_id"]
    r = client.post("/api/analysis/requests", headers=H,
                    json={"video_id": video_id})
    assert r.status_code == 400
    assert "fabricated features" in r.json()["detail"]


def test_analysis_of_an_unknown_publication_is_refused(client):
    r = client.post("/api/analysis/requests", headers=H,
                    json={"video_id": 999999})
    assert r.status_code == 400
