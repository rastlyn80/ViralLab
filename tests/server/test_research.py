"""Research workflow and API: idempotency, freshness, refusal, labelling.

The properties under test are the ones a user would notice being wrong:
researching twice should not duplicate anything or cost twice; a budget
refusal should look different from a failure; and fixture data must be
labelled wherever it surfaces.
"""

from __future__ import annotations

import importlib
import os
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import sessionmaker

from server import config as cfg
from server.db.models import (Base, BreakthroughCandidate, BudgetReservation,
                              Creator, CreatorSnapshot, DiscoveryJob,
                              MetricsSnapshot, Video)
from server.discovery.apify import ApifyDiscoveryAdapter
from server.discovery.fixture import FixtureDiscoveryAdapter
from server.research import service

from tests.conftest import TEST_DB_URL, assert_disposable

DB_URL = TEST_DB_URL
pytestmark = pytest.mark.skipif(
    not DB_URL, reason="VIRALLAB_TEST_DB_URL not set")
if DB_URL:
    assert_disposable(DB_URL)

NOW = datetime(2026, 10, 25, 12, 0, tzinfo=timezone.utc)
SECRET = "research-test-secret-0123456789"
PASSWORD = "research-test-password"
TABLES = ("breakthrough_candidates", "metrics_snapshots",
          "video_master_links", "publication_links", "videos",
          "creator_snapshots", "creator_identity_members",
          "creator_identities", "local_masters", "performances",
          "creators", "discovery_jobs", "budget_reservations",
          "collection_runs")


@pytest.fixture(scope="module")
def maker():
    eng = create_engine(DB_URL, future=True)
    Base.metadata.create_all(eng)
    yield sessionmaker(eng, expire_on_commit=False, future=True)
    eng.dispose()


@pytest.fixture
def session(maker):
    with maker() as s:
        for t in TABLES:
            s.execute(text(f"truncate {t} restart identity cascade"))
        s.commit()
        yield s
        s.rollback()


@pytest.fixture
def adapter():
    return FixtureDiscoveryAdapter()


def counts(s):
    return {
        "creators": s.scalar(select(func.count(Creator.id))),
        "videos": s.scalar(select(func.count(Video.id))),
        "snapshots": s.scalar(select(func.count(MetricsSnapshot.id))),
        "jobs": s.scalar(select(func.count(DiscoveryJob.id))),
        "candidates": s.scalar(select(func.count(
            BreakthroughCandidate.id))),
    }


# ---------------------------------------------------------------------------
# collection and storage
# ---------------------------------------------------------------------------

def test_a_first_pass_stores_creator_publications_and_observations(
        session, adapter):
    out = service.research_creator(session, adapter, platform="tiktok",
                                   handle="@demo_pianist", limit=200,
                                   now=NOW)
    assert out.status == "ok"
    assert out.is_fixture is True
    c = counts(session)
    assert c["creators"] == 1
    assert c["videos"] == 62
    assert c["snapshots"] == 62
    assert c["candidates"] > 0

    creator = session.scalars(select(Creator)).one()
    assert creator.handle == "demo_pianist"
    assert creator.is_own_account is False
    assert creator.source == "manual_lookup"
    assert creator.research_status == "collected"
    assert creator.publications_collected == 62
    assert creator.is_fixture is True


def test_publication_and_observation_times_are_stored_separately(session,
                                                                  adapter):
    """Conflating them would make "what worked then versus now"
    unanswerable, and it cannot be retrofitted."""
    service.research_creator(session, adapter, platform="tiktok",
                             handle="demo_pianist", limit=50, now=NOW)
    rows = session.execute(
        select(Video.posted_at, MetricsSnapshot.captured_at)
        .join(MetricsSnapshot, MetricsSnapshot.video_id == Video.id)
        .limit(10)).all()
    assert rows
    for posted, captured in rows:
        assert posted is not None
        assert captured == NOW
        assert posted < captured


def test_researching_twice_reuses_and_spends_nothing(session, adapter):
    first = service.research_creator(session, adapter, platform="tiktok",
                                     handle="demo_pianist", limit=200,
                                     now=NOW)
    before = counts(session)
    second = service.research_creator(session, adapter, platform="tiktok",
                                      handle="demo_pianist", now=NOW)
    assert second.reused is True
    assert second.creator_id == first.creator_id
    assert counts(session) == before, "a reused lookup changed stored data"
    assert "spent" in (second.message or "")


def test_a_refresh_appends_an_observation_without_duplicating(session,
                                                              adapter):
    service.research_creator(session, adapter, platform="tiktok",
                             handle="demo_pianist", limit=200, now=NOW)
    later = NOW + timedelta(days=1)
    service.research_creator(session, adapter, platform="tiktok",
                             handle="demo_pianist", limit=200,
                             refresh=True, now=later)
    c = counts(session)
    assert c["videos"] == 62, "publications duplicated on refresh"
    assert c["snapshots"] == 124, "a second observation was not appended"
    assert c["creators"] == 1
    # Two readings of the same publication is exactly what eventually makes
    # a verified trajectory possible.
    per_video = session.execute(
        select(func.count(MetricsSnapshot.id))
        .group_by(MetricsSnapshot.video_id)).all()
    assert all(n == 2 for (n,) in per_video)


def test_a_handle_and_its_url_resolve_to_the_same_creator(session, adapter):
    a = service.research_creator(session, adapter, platform="tiktok",
                                 handle="demo_pianist", limit=50, now=NOW)
    b = service.research_creator(
        session, adapter, platform="tiktok",
        handle="https://www.tiktok.com/@demo_pianist", now=NOW)
    assert b.creator_id == a.creator_id
    assert session.scalar(select(func.count(Creator.id))) == 1


def test_both_platforms_are_stored_as_separate_accounts(session, adapter):
    service.research_creator(session, adapter, platform="tiktok",
                             handle="demo_pianist", limit=200, now=NOW)
    service.research_creator(session, adapter, platform="instagram",
                             handle="demo_pianist_ig", limit=200, now=NOW)
    rows = session.scalars(select(Creator)).all()
    assert {r.platform for r in rows} == {"tiktok", "instagram"}


def test_an_unreadable_account_is_recorded_not_silently_dropped(session,
                                                                adapter):
    """The Instagram actor reports a private account as an item error. If
    that were discarded, a private account would look like an empty one."""
    out = service.research_creator(session, adapter, platform="instagram",
                                   handle="demo_pianist_ig", limit=200,
                                   now=NOW)
    assert out.item_errors
    job = session.scalars(select(DiscoveryJob)).one()
    assert (job.item_errors or {}).get("errors")


def test_follower_history_is_appended_only_where_reported(session, adapter):
    """TikTok reports followers; the Instagram actors report nothing
    account-level, so no snapshot is invented for them."""
    service.research_creator(session, adapter, platform="tiktok",
                             handle="demo_pianist", limit=50, now=NOW)
    service.research_creator(session, adapter, platform="instagram",
                             handle="demo_pianist_ig", limit=50, now=NOW)
    by_platform = dict(session.execute(
        select(Creator.platform, func.count(CreatorSnapshot.id))
        .outerjoin(CreatorSnapshot,
                   CreatorSnapshot.creator_id == Creator.id)
        .group_by(Creator.platform)).all())
    assert by_platform["tiktok"] == 1
    assert by_platform["instagram"] == 0, (
        "an account snapshot was invented for a source that reports none")

    ig = session.scalar(select(Creator).where(
        Creator.platform == "instagram"))
    assert ig.follower_count is None, "a follower count was fabricated"


def test_coverage_notes_are_stored_with_the_job(session, adapter):
    service.research_creator(session, adapter, platform="instagram",
                             handle="demo_pianist_ig", limit=50, now=NOW)
    job = session.scalars(select(DiscoveryJob)).one()
    notes = " ".join((job.coverage_notes or {}).get("notes") or [])
    assert "FIXTURE" in notes
    assert "follower" in notes.lower()


def test_a_bad_handle_fails_without_creating_anything(session, adapter):
    out = service.research_creator(session, adapter, platform="tiktok",
                                   handle="not a handle", now=NOW)
    assert out.status == "failed"
    assert counts(session)["creators"] == 0


# ---------------------------------------------------------------------------
# budget interaction
# ---------------------------------------------------------------------------

class NeverCalled(ApifyDiscoveryAdapter):
    """A paid adapter that fails the test if it is ever reached."""

    def __init__(self):
        self.max_items_cap = 100

    def search(self, query):                                  # noqa: D102
        raise AssertionError("a paid provider was called without authority")


def test_an_unauthorised_paid_provider_is_refused_before_calling_out(
        session):
    out = service.research_creator(session, NeverCalled(), platform="tiktok",
                                   handle="someone_new", limit=50, now=NOW)
    assert out.status == "refused"
    job = session.scalars(select(DiscoveryJob)).one()
    assert job.status == "refused", (
        "a budget refusal was recorded as a failure; they are different")
    assert job.error and "providers_authorised" in job.error
    assert session.scalar(select(func.count(BudgetReservation.id))) == 0


def test_a_refusal_does_not_leave_the_creator_marked_as_collected(session):
    service.research_creator(session, NeverCalled(), platform="tiktok",
                             handle="someone_new", limit=50, now=NOW)
    c = session.scalars(select(Creator)).one()
    assert c.research_status != "collected"
    assert (c.publications_collected or 0) == 0


def test_a_fixture_run_takes_no_reservation(session, adapter):
    service.research_creator(session, adapter, platform="tiktok",
                             handle="demo_pianist", limit=50, now=NOW)
    assert session.scalar(select(func.count(BudgetReservation.id))) == 0
    job = session.scalars(select(DiscoveryJob)).one()
    assert job.actual_usd == 0.0
    assert job.is_fixture is True


def test_a_paid_provider_reserves_then_settles(session, adapter,
                                                monkeypatch):
    """The happy path, with the provider faked out but the budget real."""
    monkeypatch.setattr(
        cfg, "settings",
        lambda: cfg.Settings(raw={}, spending=cfg.Spending(
            monthly_cap_usd=5.0, providers_authorised=("apify",))))

    class PaidFixture(FixtureDiscoveryAdapter):
        name = "apify"
        is_fixture = False

        def estimate_usd(self, query):
            return 0.30

        def search(self, query):
            res = super().search(query)
            res.provider = "apify"
            res.is_fixture = False
            res.actual_usd = 0.2871      # billed, not estimated
            return res

    out = service.research_creator(session, PaidFixture(), platform="tiktok",
                                   handle="demo_pianist", limit=100, now=NOW)
    assert out.status == "ok"
    res = session.scalars(select(BudgetReservation)).one()
    assert res.status == "settled"
    assert res.estimated_usd == pytest.approx(0.30)
    assert res.actual_usd == pytest.approx(0.2871)
    job = session.scalars(select(DiscoveryJob)).one()
    assert job.actual_usd == pytest.approx(0.2871)
    assert job.reservation_id == res.id


def test_a_permanent_provider_failure_releases_the_budget(session,
                                                           monkeypatch):
    monkeypatch.setattr(
        cfg, "settings",
        lambda: cfg.Settings(raw={}, spending=cfg.Spending(
            monthly_cap_usd=5.0, providers_authorised=("apify",))))

    from server.discovery.base import DiscoveryError

    class Broken(ApifyDiscoveryAdapter):
        name = "apify"
        is_fixture = False

        def __init__(self):
            self.max_items_cap = 100

        def estimate_usd(self, query):
            return 0.30

        def search(self, query):
            raise DiscoveryError("no such account", permanent=True,
                                 provider="apify")

    out = service.research_creator(session, Broken(), platform="tiktok",
                                   handle="ghost_account", now=NOW)
    assert out.status == "failed"
    res = session.scalars(select(BudgetReservation)).one()
    assert res.status == "released", (
        "budget stayed held after a call that never reached the provider")


def test_a_transient_failure_leaves_the_budget_held(session, monkeypatch):
    """It may have billed. Releasing it would be the assumption that
    overspends."""
    monkeypatch.setattr(
        cfg, "settings",
        lambda: cfg.Settings(raw={}, spending=cfg.Spending(
            monthly_cap_usd=5.0, providers_authorised=("apify",))))
    from server.discovery.base import DiscoveryError

    class Flaky(ApifyDiscoveryAdapter):
        name = "apify"
        is_fixture = False

        def __init__(self):
            self.max_items_cap = 100

        def estimate_usd(self, query):
            return 0.30

        def search(self, query):
            raise DiscoveryError("gateway timeout", provider="apify")

    service.research_creator(session, Flaky(), platform="tiktok",
                             handle="someone", now=NOW)
    res = session.scalars(select(BudgetReservation)).one()
    assert res.status == "reserved"


# ---------------------------------------------------------------------------
# breakthrough storage
# ---------------------------------------------------------------------------

def test_candidates_are_stored_with_their_classification_and_gaps(session,
                                                                   adapter):
    service.research_creator(session, adapter, platform="tiktok",
                             handle="demo_pianist", limit=200, now=NOW)
    rows = session.scalars(select(BreakthroughCandidate)).all()
    assert rows
    bt = [r for r in rows if r.classification == "inferred_breakthrough"]
    assert len(bt) == 1, "expected exactly one change point"
    assert bt[0].sustained is True
    assert (bt[0].missing_evidence or {}).get("missing"), (
        "an inferred breakthrough recorded no missing evidence, which reads "
        "as established")
    assert all(r.decided_by == "matcher" for r in rows)
    assert not any(r.classification == "verified_trajectory_change"
                   for r in rows), (
        "a single research pass cannot verify a trajectory")


def test_rerunning_does_not_accumulate_duplicate_candidates(session,
                                                             adapter):
    service.research_creator(session, adapter, platform="tiktok",
                             handle="demo_pianist", limit=200, now=NOW)
    before = session.scalar(select(func.count(BreakthroughCandidate.id)))
    service.research_creator(session, adapter, platform="tiktok",
                             handle="demo_pianist", limit=200, refresh=True,
                             now=NOW + timedelta(days=1))
    after = session.scalar(select(func.count(BreakthroughCandidate.id)))
    assert after == before


def test_a_human_decision_on_a_candidate_is_never_overwritten(session,
                                                               adapter):
    service.research_creator(session, adapter, platform="tiktok",
                             handle="demo_pianist", limit=200, now=NOW)
    row = session.scalars(select(BreakthroughCandidate).where(
        BreakthroughCandidate.classification == "inferred_breakthrough")
    ).one()
    row.classification = "rejected"
    row.decided_by = "human"
    row.decided_at = NOW
    session.commit()

    service.research_creator(session, adapter, platform="tiktok",
                             handle="demo_pianist", limit=200, refresh=True,
                             now=NOW + timedelta(days=1))
    again = session.get(BreakthroughCandidate, row.id)
    assert again.classification == "rejected"
    assert again.decided_by == "human"


# ---------------------------------------------------------------------------
# the API surface
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def client(maker, tmp_path_factory):
    os.environ.update(
        VIRALLAB_DB_URL=DB_URL,
        VIRALLAB_MEDIA_SECRET=SECRET,
        VIRALLAB_WORKER_TOKENS="gpu-1:tok",
        VIRALLAB_MEDIA_ROOT=str(tmp_path_factory.mktemp("rmedia")),
        VIRALLAB_UI_PASSWORD=PASSWORD,
    )
    from server.api import ui as ui_module
    importlib.reload(ui_module)
    from server.api import research_api as ra_module
    importlib.reload(ra_module)
    from server.api import app as app_module
    importlib.reload(app_module)
    with TestClient(app_module.app, base_url="https://testserver") as c:
        c.post("/api/session", json={"password": PASSWORD})
        yield c


def test_research_endpoints_require_a_session(client):
    client.cookies.clear()
    assert client.get("/api/research/state").status_code == 401
    assert client.post("/api/research/creators",
                       json={"platform": "tiktok", "handle": "x"},
                       headers={"X-ViralLab-UI": "1"}).status_code == 401
    client.post("/api/session", json={"password": PASSWORD})


def test_state_reports_fixture_mode_and_a_blocked_budget(client):
    state = client.get("/api/research/state").json()
    assert state["spending_blocked"] is True
    assert state["fixture_mode"] is True
    assert state["active_provider"] == "fixture"
    assert state["monthly_cap_usd"] == 0.0
    assert state["available_fixtures"], (
        "fixture mode offered no fixtures, so the UI has nothing to run")


def test_capabilities_endpoint_declares_instagram_gaps(client):
    caps = client.get("/api/research/capabilities/instagram").json()
    assert caps["follower_count"] is False
    assert "follower_count" in caps["missing_creator_fields"]
    assert client.get("/api/research/capabilities/youtube").status_code == 400


def test_running_research_requires_the_csrf_header(client):
    r = client.post("/api/research/creators",
                    json={"platform": "tiktok", "handle": "demo_pianist"})
    assert r.status_code == 403


def test_the_full_api_workflow(client, session):
    r = client.post("/api/research/creators",
                    json={"platform": "tiktok", "handle": "@demo_pianist",
                          "limit": 200},
                    headers={"X-ViralLab-UI": "1"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "ok"
    assert body["is_fixture"] is True
    cid = body["creator_id"]

    detail = client.get(f"/api/research/creators/{cid}").json()
    assert detail["handle"] == "demo_pianist"
    assert detail["is_fixture"] is True
    assert detail["summary"]["publications"] == 62
    assert detail["summary"]["caveats"], "figures arrived with no caveats"
    assert detail["capabilities"]["follower_count"] is True

    pubs = client.get(f"/api/research/creators/{cid}/publications"
                      "?order=relative&limit=10").json()
    assert pubs["total"] == 62
    assert pubs["notes"]
    top = pubs["items"][0]
    assert top["relative_performance"] is not None
    # Every view count carries the age it was observed at.
    for item in pubs["items"]:
        if item["views"] is not None:
            assert item["age_days"] is not None

    bt = client.get(f"/api/research/creators/{cid}/breakthroughs").json()
    assert bt["breakthrough"]["classification"] == "inferred_breakthrough"
    assert bt["breakthrough"]["missing_evidence"]
    assert bt["has_repeated_observations"] is False
    assert bt["caveats"]

    rows = client.get("/api/research/creators").json()
    assert any(row["creator_id"] == cid and row["is_fixture"]
               for row in rows)

    jobs = client.get("/api/research/jobs").json()
    assert jobs and jobs[0]["is_fixture"] is True


def test_the_api_rejects_a_bad_platform_or_empty_handle(client):
    assert client.post("/api/research/creators",
                       json={"platform": "youtube", "handle": "x"},
                       headers={"X-ViralLab-UI": "1"}).status_code == 400
    assert client.post("/api/research/creators",
                       json={"platform": "tiktok", "handle": "   "},
                       headers={"X-ViralLab-UI": "1"}).status_code == 400


def test_a_missing_creator_is_404(client):
    assert client.get("/api/research/creators/999999").status_code == 404
    assert client.get(
        "/api/research/creators/999999/publications").status_code == 404
    assert client.get(
        "/api/research/creators/999999/breakthroughs").status_code == 404


def test_publication_sorting_is_validated(client):
    assert client.get("/api/research/creators/1/publications"
                      "?order=whatever").status_code == 422
