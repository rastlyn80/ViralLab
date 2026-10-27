"""Keyword/hashtag discovery, candidate ranking, collections, timeline.

The Phase 4.5 surfaces. Several of these tests exist because the behaviour
they pin down was wrong first:

  * a discovery sample poisoned the incremental watermark, so the first real
    research pass on a discovered creator collected only publications newer
    than the one sampled post - 33 of 62;
  * job result sets resolved through `videos.discovery_job_id`, which
    records only the first job to see a publication, so re-running a search
    appeared to return nothing;
  * a views-per-follower ratio on a 640-follower account ranked level with a
    genuine breakout, because the ratio is unstable when the denominator is
    small.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import sessionmaker

from server import config as cfg
from server.db.models import (Base, BudgetReservation, Creator,
                              CreatorCollection, DiscoveryJob,
                              MetricsSnapshot, Video)
from server.discovery.fixture import FixtureDiscoveryAdapter
from server.research import collections as coll
from server.research import discover as disc
from server.research import report as rep
from server.research import service
from server.research import timeline as tlmod
from tests.conftest import TEST_DB_URL, assert_disposable

DB_URL = TEST_DB_URL or ""
NOW = datetime(2026, 10, 26, 9, 0, tzinfo=timezone.utc)

pytestmark = pytest.mark.skipif(not DB_URL,
                                reason="VIRALLAB_TEST_DB_URL not set")

TABLES = ("analysis_results", "batch_videos", "analysis_batches",
          "breakthrough_candidates", "creator_collection_members",
          "creator_collections", "metrics_snapshots", "video_master_links",
          "publication_links", "videos", "creator_snapshots",
          "budget_reservations", "creators", "discovery_jobs")


@pytest.fixture(scope="module")
def engine():
    assert_disposable(DB_URL)
    eng = create_engine(DB_URL, future=True)
    Base.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def session(engine):
    maker = sessionmaker(engine, expire_on_commit=False, future=True)
    with maker() as s:
        for t in TABLES:
            s.execute(text(f"truncate {t} restart identity cascade"))
        s.commit()
        yield s
        s.rollback()


@pytest.fixture
def adapter():
    return FixtureDiscoveryAdapter()


def run_keyword(session, adapter, **kw):
    return disc.discover(session, adapter, kind="keyword", platform="tiktok",
                         value="classical piano", limit=50, now=NOW, **kw)


# ---------------------------------------------------------------------------
# the search itself
# ---------------------------------------------------------------------------

def test_a_keyword_search_stores_creators_and_publications(session, adapter):
    out = run_keyword(session, adapter, niche="classical_piano")
    assert out.status == "ok"
    assert out.publications_seen == 11
    assert out.creators_seen == 5
    assert out.creators_new == 5
    assert out.actual_usd == 0.0

    # Observed data, with both times recorded separately.
    vids = session.scalars(select(Video)).all()
    assert len(vids) == 11
    assert all(v.posted_at is not None for v in vids)
    snaps = session.scalars(select(MetricsSnapshot)).all()
    assert len(snaps) == 11
    assert all(s.captured_at is not None for s in snaps)
    assert all(s.discovery_job_id == out.job_id for s in snaps)

    # Provenance on every row, and the sampling limits with it.
    for v in vids:
        assert v.provenance["provider"] == "fixture"
        assert v.discovery_job_id == out.job_id
        assert v.is_fixture is True
    job = session.get(DiscoveryJob, out.job_id)
    assert job.params == {"niche": "classical_piano", "cursor": None,
                          "limit": 50}
    assert any("biased toward recent" in n for n in job.coverage_notes)


def test_a_hashtag_search_normalises_the_leading_hash(session, adapter):
    out = disc.discover(session, adapter, kind="hashtag", platform="tiktok",
                        value="#pianotok", limit=50, now=NOW)
    assert out.status == "ok"
    assert out.value == "pianotok"
    job = session.get(DiscoveryJob, out.job_id)
    assert job.query_value == "pianotok"


def test_rerunning_a_search_does_not_duplicate_creators(session, adapter):
    first = run_keyword(session, adapter)
    second = disc.discover(session, adapter, kind="hashtag",
                           platform="tiktok", value="pianotok", limit=50,
                           now=NOW)
    assert second.status == "ok"
    # The hashtag feed covers four of the same creators.
    assert second.creators_new == 0
    assert session.scalar(select(func.count(Creator.id))) == 5

    # And the second job's result set is NOT empty, which is the bug this
    # pins down: it resolves through its own observations, not through
    # `videos.discovery_job_id`.
    counts = disc.search_result_counts(session, second.job_id)
    assert counts["publications"] == second.publications_seen
    assert counts["creators"] == 4
    assert disc.candidates_for_job(session, adapter, second.job_id)


def test_an_identical_search_is_reused_rather_than_rerun(session, adapter):
    first = run_keyword(session, adapter)
    again = run_keyword(session, adapter)
    assert again.status == "reused"
    assert again.job_id == first.job_id
    assert "nothing was spent" in again.message
    assert session.scalar(select(func.count(DiscoveryJob.id))) == 1


def test_force_reruns_and_appends_an_observation(session, adapter):
    first = run_keyword(session, adapter)
    forced = run_keyword(session, adapter, force=True)
    assert forced.status == "ok"
    assert forced.job_id != first.job_id
    # Publications are not duplicated; observations accumulate.
    assert session.scalar(select(func.count(Video.id))) == 11
    assert session.scalar(select(func.count(MetricsSnapshot.id))) == 22


def test_a_search_in_flight_is_not_started_twice(session, adapter):
    session.add(DiscoveryJob(
        kind="keyword", platform="tiktok", query_value="classical piano",
        requested_limit=50, provider="fixture", is_fixture=True,
        status="running", started_at=NOW))
    session.commit()
    out = run_keyword(session, adapter)
    assert out.status == "in_flight"
    assert session.scalar(select(func.count(Video.id))) == 0


def test_an_unsupported_query_is_refused_as_a_provider_limit(session,
                                                             adapter):
    """`[PRIMARY]` The validated Instagram actors have no keyword search."""
    with pytest.raises(disc.DiscoveryRefused) as exc:
        disc.discover(session, adapter, kind="keyword", platform="instagram",
                      value="piano", limit=10, now=NOW)
    assert "provider limit" in str(exc.value)


def test_an_unknown_niche_is_refused(session, adapter):
    with pytest.raises(disc.DiscoveryRefused):
        run_keyword(session, adapter, niche="not_a_niche")


def test_pagination_limit_is_respected(session, adapter):
    out = disc.discover(session, adapter, kind="keyword", platform="tiktok",
                        value="classical piano", limit=3, now=NOW)
    assert out.publications_seen == 3


# ---------------------------------------------------------------------------
# ranking
# ---------------------------------------------------------------------------

def test_a_small_creator_breakout_ranks_first(session, adapter):
    out = run_keyword(session, adapter, niche="classical_piano")
    cands = disc.candidates_for_job(session, adapter, out.job_id)
    assert cands[0].handle == "demo_smallbreakout"
    assert cands[0].tier == "small_creator_breakout"
    assert cands[0].best_views_per_follower > 3.0
    assert any("beyond their own audience" in r for r in cands[0].reasons)


def test_a_tiny_follower_base_does_not_manufacture_a_breakout(session,
                                                              adapter):
    """The ratio is unstable when the denominator is small.

    `demo_tiny` has 640 followers and a 9,300-view post: 14.5x, which
    without an absolute floor ranked it level with a genuine 78,400-view
    breakout on 1,900 followers.
    """
    out = run_keyword(session, adapter)
    cands = {c.handle: c for c in
             disc.candidates_for_job(session, adapter, out.job_id)}
    tiny = cands["demo_tiny"]
    assert tiny.best_views_per_follower > 3.0     # the ratio is reported
    assert tiny.tier == "relevant"                # but does not drive a tier
    assert any("rests on a small audience" in r for r in tiny.reasons)
    assert tiny.score < cands["demo_smallbreakout"].score / 5


def test_repeated_appearance_is_not_called_consistent(session, adapter):
    out = run_keyword(session, adapter)
    cands = {c.handle: c for c in
             disc.candidates_for_job(session, adapter, out.job_id)}
    steady = cands["demo_steady"]
    assert steady.sampled_publications == 4
    assert steady.tier == "repeatedly_relevant"
    reason = next(r for r in steady.reasons if "matched the search" in r)
    assert "NOT evidence of consistent performance" in reason


def test_instagram_candidates_report_no_follower_count(session, adapter):
    """`[PRIMARY]` The validated Instagram actors return no follower count."""
    out = disc.discover(session, adapter, kind="hashtag",
                        platform="instagram", value="classicalpiano",
                        limit=50, now=NOW)
    cands = disc.candidates_for_job(session, adapter, out.job_id)
    assert cands
    for c in cands:
        assert c.followers_available is False
        assert c.follower_count is None          # unknown, not zero
        assert c.best_views_per_follower is None
        assert any("No follower count is available" in r for r in c.reasons)
        sig = next(s for s in c.signals if s.key == "views_per_follower")
        assert sig.available is False
        assert sig.value is None


def test_a_rounded_provider_is_declared_on_the_candidate(session, adapter):
    out = disc.discover(session, adapter, kind="hashtag", platform="tiktok",
                        value="pianotok", limit=50, now=NOW)
    cands = disc.candidates_for_job(session, adapter, out.job_id)
    assert cands and all(c.counts_rounded for c in cands)
    assert any("rounds view counts" in r for r in cands[0].reasons)


# ---------------------------------------------------------------------------
# bounded expansion
# ---------------------------------------------------------------------------

def test_expansion_stops_at_the_creator_ceiling(session, adapter):
    out = run_keyword(session, adapter)
    cands = disc.candidates_for_job(session, adapter, out.job_id)
    plan = disc.plan_expansion(session, adapter, cands, max_creators=2,
                               now=NOW)
    assert len(plan.candidates) == 2
    assert plan.within_budget is True


def test_a_billed_run_that_returns_nothing_is_empty_not_ok(session,
                                                           monkeypatch):
    """Paying for nothing must not look like a successful search.

    MEASURED 2026-10-26: `apidojo/tiktok-scraper` started answering every
    query with ten `{"noResults": true}` sentinel rows while Apify kept
    billing $0.0030 a run. The job landed as `ok` with zero publications,
    which is exactly what a search for a genuinely nonexistent term looks
    like, so three runs bought nothing before anyone noticed.
    """
    from server.discovery.base import DiscoveryResult

    class PaidAndEmpty(FixtureDiscoveryAdapter):
        name = "apify"
        is_fixture = False

        def estimate_usd(self, query):
            return 0.05

        def search(self, query):
            return DiscoveryResult(
                provider=self.name, query=query, observed_at=NOW,
                creators=[], publications=[], actual_usd=0.003,
                capabilities=self.capabilities(query.platform),
                item_errors=[{"reason": "provider_found_nothing",
                              "detail": "sentinel"}])

    monkeypatch.setattr(
        cfg, "settings",
        lambda: cfg.Settings(raw={}, spending=cfg.Spending(
            monthly_cap_usd=5.0, providers_authorised=("apify",))))

    out = run_keyword(session, PaidAndEmpty())
    assert out.status == "empty", (
        f"a run billed $0.003 for zero rows reported {out.status!r}")
    assert out.actual_usd == pytest.approx(0.003)
    assert out.message and "returned no usable rows" in out.message
    job = session.get(DiscoveryJob, out.job_id)
    assert job.status == "empty"


def test_a_free_fixture_returning_nothing_is_still_ok(session, adapter,
                                                      monkeypatch):
    """`empty` is about having paid, not about being empty.

    A fixture costs nothing, so an empty fixture is just an empty fixture
    and must not be flagged as money wasted.
    """
    from server.discovery.base import DiscoveryResult

    class FreeAndEmpty(FixtureDiscoveryAdapter):
        def search(self, query):
            return DiscoveryResult(
                provider=self.name, query=query, observed_at=NOW,
                creators=[], publications=[], actual_usd=0.0,
                capabilities=self.capabilities(query.platform))

    out = run_keyword(session, FreeAndEmpty())
    assert out.status == "ok"


def test_expansion_stops_at_a_spend_ceiling(session, adapter, monkeypatch):
    class Paid(FixtureDiscoveryAdapter):
        name = "apify"
        is_fixture = False

        def estimate_usd(self, query):
            return 0.30

    out = run_keyword(session, adapter)
    cands = disc.candidates_for_job(session, adapter, out.job_id)
    monkeypatch.setattr(
        cfg, "settings",
        lambda: cfg.Settings(raw={}, spending=cfg.Spending(
            monthly_cap_usd=5.0, providers_authorised=("apify",))))
    paid = Paid()
    priced = disc.candidates_for_job(session, paid, out.job_id)
    plan = disc.plan_expansion(session, paid, priced, max_spend_usd=0.70,
                               now=NOW)
    assert len(plan.candidates) == 2            # 2 x 0.30 fits, 3 does not
    assert plan.within_budget is False
    assert any("Stopped at" in n for n in plan.notes)


def test_expansion_skips_creators_whose_research_is_fresh(session, adapter):
    out = run_keyword(session, adapter)
    cands = disc.candidates_for_job(session, adapter, out.job_id)
    target = cands[0]
    creator = session.get(Creator, target.creator_id)
    creator.last_refreshed_at = NOW - timedelta(minutes=5)
    session.commit()
    plan = disc.plan_expansion(session, adapter, cands, max_creators=5,
                               now=NOW)
    assert target.handle in plan.skipped_already_fresh
    assert target.handle not in [c.handle for c in plan.candidates]


# ---------------------------------------------------------------------------
# discovery feeding research
# ---------------------------------------------------------------------------

def test_research_after_discovery_collects_the_full_back_catalogue(session,
                                                                   adapter):
    """A discovery sample must not become an incremental watermark.

    The keyword feed stores ONE `demo_pianist` publication. Before the fix,
    `research_creator` treated that as "collected up to here" and fetched
    only newer posts: 33 of 62.
    """
    run_keyword(session, adapter)
    discovered = service.find_creator(session, "tiktok", "demo_pianist")
    assert discovered is not None
    assert discovered.research_status == "none"
    assert session.scalar(
        select(func.count(Video.id))
        .where(Video.creator_id == discovered.id)) == 1

    out = service.research_creator(session, adapter, platform="tiktok",
                                   handle="demo_pianist", limit=200, now=NOW)
    assert out.status == "ok"
    total = session.scalar(select(func.count(Video.id))
                           .where(Video.creator_id == discovered.id))
    # 62 from the back-catalogue plus the one the keyword feed surfaced.
    assert total == 63


def test_a_second_research_pass_is_incremental_once_collected(session,
                                                              adapter):
    service.research_creator(session, adapter, platform="tiktok",
                             handle="demo_pianist", limit=200, now=NOW)
    creator = service.find_creator(session, "tiktok", "demo_pianist")
    assert creator.research_status == "collected"
    before = session.scalar(select(func.count(Video.id)))
    service.research_creator(session, adapter, platform="tiktok",
                             handle="demo_pianist", limit=200, refresh=True,
                             now=NOW + timedelta(days=1))
    assert session.scalar(select(func.count(Video.id))) == before


# ---------------------------------------------------------------------------
# timeline
# ---------------------------------------------------------------------------

@pytest.fixture
def researched(session, adapter):
    service.research_creator(session, adapter, platform="tiktok",
                             handle="demo_pianist", limit=200, now=NOW)
    creator = service.find_creator(session, "tiktok", "demo_pianist")
    session.commit()
    return creator


def test_the_timeline_declares_an_exploratory_mode(session, researched):
    tl = tlmod.build(session, researched.id)
    assert tl.mode == "lifetime_exploratory"
    assert "NOT a trajectory" in tl.mode_label
    assert tl.anchored_points == 0
    assert tl.repeatedly_observed == 0
    assert any("not a historical view trajectory" in c for c in tl.caveats)
    assert any("observed exactly once" in c for c in tl.caveats)


def test_every_timeline_point_carries_its_own_age(session, researched):
    tl = tlmod.build(session, researched.id)
    assert tl.points
    for p in tl.points:
        assert p.age_days is not None, (
            f"{p.external_id} has a view count with no age, which makes it "
            f"uncomparable")
        assert p.views_at_anchor is None      # never interpolated into


def test_the_timeline_locates_the_planted_change_point(session, researched):
    tl = tlmod.build(session, researched.id)
    cp = tl.change_point
    assert cp is not None
    assert cp.classification == "inferred_breakthrough"
    # The fixture plants the regime change at index 34 of 62.
    assert cp.publication_index == 34
    assert cp.sustained is True
    assert cp.missing_evidence


def test_confidence_is_never_presented_as_calibrated(session, researched):
    tl = tlmod.build(session, researched.id)
    assert tl.change_point.confidence_is_calibrated is False
    note = tl.change_point.confidence_note.lower()
    assert "not a probability" in note
    assert "calibration has never been measured" in note


def test_outliers_and_change_points_are_distinct_markers(session, researched):
    tl = tlmod.build(session, researched.id)
    assert tl.outlier_candidates >= 1
    outliers = [p for p in tl.points if p.is_outlier_candidate]
    changes = [p for p in tl.points if p.is_change_point]
    assert len(changes) == 1
    assert any("different claims" in c for c in tl.caveats)
    # The two markers are independent flags, not one reused.
    assert all(isinstance(p.is_outlier_candidate, bool) for p in outliers)


def test_competitor_points_offer_no_stored_media_preview(session,
                                                         researched):
    tl = tlmod.build(session, researched.id)
    for p in tl.points:
        assert p.media_preview_available is False
        assert "not retained" in (p.media_preview_note or "")


def test_sustained_runs_need_more_than_two_publications(session, researched):
    tl = tlmod.build(session, researched.id)
    for r in tl.sustained_runs:
        assert r.publications >= tlmod.MIN_RUN
        assert r.kind in ("above", "below")


def test_frequency_change_is_reported_as_an_alternative_explanation(
        session, researched):
    tl = tlmod.build(session, researched.id)
    fc = tlmod.frequency_change(tl)
    assert fc is not None
    assert "alternative explanation" in fc["note"]


# ---------------------------------------------------------------------------
# report
# ---------------------------------------------------------------------------

def test_the_report_states_a_basis_for_every_section(session, researched):
    r = rep.build(session, researched.id)
    keys = {s.key for s in r.sections}
    assert {"overview", "coverage", "typical", "strongest", "outliers",
            "breakthrough", "frequency", "repertoire",
            "recent_vs_historical"} <= keys
    for s in r.sections:
        assert s.basis in ("observed", "inferred", "unavailable")
        if s.basis == "unavailable":
            assert s.unavailable_reason


def test_the_report_explains_why_a_breakthrough_was_flagged(session,
                                                            researched):
    r = rep.build(session, researched.id)
    sec = next(s for s in r.sections if s.key == "breakthrough")
    assert sec.body["found"] is True
    assert sec.body["classification"] == "inferred_breakthrough"
    why = sec.body["why_flagged"]
    assert any("CUSUM" in w for w in why)
    assert any("robust median" in w for w in why)
    strengthen = sec.body["what_would_strengthen"]
    assert any("again in a week" in w for w in strengthen)
    assert any("follower history" in w for w in strengthen)
    assert sec.body["confidence_is_calibrated"] is False
    assert any("INFERRED, not established" in c for c in sec.caveats)


def test_repertoire_is_reported_as_mentions_not_identifications(session,
                                                                researched):
    r = rep.build(session, researched.id)
    sec = next(s for s in r.sections if s.key == "repertoire")
    assert sec.body["method"] == "metadata_mention"
    assert sec.body["composer_mentions"]
    assert any("not identifications" in c for c in sec.caveats)
    assert any("open research question" in c for c in sec.caveats)


def test_recent_versus_historical_reports_the_age_gap(session, researched):
    r = rep.build(session, researched.id)
    sec = next(s for s in r.sections if s.key == "recent_vs_historical")
    assert sec.basis == "inferred"
    assert sec.body["age_gap_days"] is not None
    assert any("elapsed exposure" in c for c in sec.caveats)


def test_the_report_carries_its_own_provenance_and_limitations(session,
                                                               researched):
    r = rep.build(session, researched.id)
    assert r.provider == "fixture"
    assert r.cost_usd == 0.0
    assert r.research_date is not None
    assert any("FIXTURE DATA" in x for x in r.data_limitations)
    assert any("observed twice" in x for x in r.data_limitations)
    assert any("Survivorship" in x for x in r.data_limitations)


# ---------------------------------------------------------------------------
# collections
# ---------------------------------------------------------------------------

def test_a_collection_groups_creators_and_keeps_notes(session, adapter):
    run_keyword(session, adapter)
    c = coll.create(session, name="Classical Piano Competitors",
                    purpose="research", niche="classical_piano")
    session.commit()
    creator = service.find_creator(session, "tiktok", "demo_smallbreakout")
    coll.add_member(session, c.id, creator.id, reason="small creator breakout")
    session.commit()

    v = coll.view(session, c.id)
    assert v.member_count == 1
    assert v.members[0].reason == "small creator breakout"
    assert v.purpose_label == "Accounts to learn from"


def test_adding_a_member_twice_updates_rather_than_fails(session, adapter):
    run_keyword(session, adapter)
    c = coll.create(session, name="Group")
    session.commit()
    creator = service.find_creator(session, "tiktok", "demo_steady")
    coll.add_member(session, c.id, creator.id, reason="first")
    coll.add_member(session, c.id, creator.id, reason="second")
    session.commit()
    v = coll.view(session, c.id)
    assert v.member_count == 1
    assert v.members[0].reason == "second"


def test_an_own_account_cannot_join_a_collection(session):
    own = Creator(platform="tiktok", handle="me", external_id="mine",
                  is_own_account=True, source="own_api")
    session.add(own)
    c = coll.create(session, name="Group")
    session.commit()
    with pytest.raises(coll.CollectionError) as exc:
        coll.add_member(session, c.id, own.id)
    assert "your own accounts" in str(exc.value)


def test_a_duplicate_collection_name_is_refused(session):
    coll.create(session, name="Group")
    session.commit()
    with pytest.raises(coll.CollectionError):
        coll.create(session, name="Group")


def test_an_unknown_purpose_is_refused(session):
    with pytest.raises(coll.CollectionError):
        coll.create(session, name="Group", purpose="whatever")


def test_collection_comparison_uses_within_creator_performance(session,
                                                               researched):
    c = coll.create(session, name="Group")
    session.commit()
    coll.add_member(session, c.id, researched.id)
    session.commit()
    v = coll.view(session, c.id)
    m = v.members[0]
    assert m.best_relative_performance is not None
    assert m.typical_relative_performance is not None
    assert any("never on lifetime views" in x for x in v.caveats)


def test_a_non_control_collection_says_its_patterns_are_hypotheses(session):
    c = coll.create(session, name="Group", purpose="research")
    session.commit()
    v = coll.view(session, c.id)
    assert any("hypotheses" in x for x in v.caveats)

    c2 = coll.create(session, name="Controls", purpose="control_cohort")
    session.commit()
    v2 = coll.view(session, c2.id)
    assert not any("hypotheses" in x for x in v2.caveats)


def test_deleting_a_collection_keeps_the_creators(session, adapter):
    run_keyword(session, adapter)
    c = coll.create(session, name="Group")
    session.commit()
    creator = service.find_creator(session, "tiktok", "demo_steady")
    coll.add_member(session, c.id, creator.id)
    session.commit()
    before = session.scalar(select(func.count(Creator.id)))

    assert coll.delete(session, c.id) is True
    session.commit()
    assert session.scalar(select(func.count(Creator.id))) == before
    assert session.scalar(select(func.count(CreatorCollection.id))) == 0


def test_an_unresearched_member_contributes_nothing_and_says_so(session,
                                                                adapter):
    run_keyword(session, adapter)
    c = coll.create(session, name="Group")
    session.commit()
    creator = service.find_creator(session, "tiktok", "demo_steady")
    coll.add_member(session, c.id, creator.id)
    session.commit()
    v = coll.view(session, c.id)
    m = v.members[0]
    assert m.research_status == "none"
    assert m.best_relative_performance is None
    assert creator.handle in v.needs_refresh


# ---------------------------------------------------------------------------
# a failed lookup must not leave an account behind
#
# Found in production from real use: 14 attempts to research a real handle
# while no provider was authorised left two permanent `creators` rows with
# `research_status='failed'` and zero publications. Every attempt correctly
# spent nothing; the residue was the defect.
# ---------------------------------------------------------------------------

def test_a_failed_first_lookup_leaves_no_creator_stub(session, adapter):
    before = session.scalar(select(func.count(Creator.id)))
    out = service.research_creator(session, adapter, platform="tiktok",
                                   handle="nobody_has_this_fixture",
                                   limit=100, now=NOW)
    assert out.status == "failed"
    assert out.creator_id is None
    assert session.scalar(select(func.count(Creator.id))) == before
    # The attempt itself is still on the record.
    job = session.scalars(select(DiscoveryJob)).one()
    assert job.status == "failed"
    assert job.creators_new == 0
    assert job.error


def test_fixture_mode_explains_itself_rather_than_listing_internals(
        session, adapter):
    out = service.research_creator(session, adapter, platform="tiktok",
                                   handle="some_real_person", limit=100,
                                   now=NOW)
    assert out.status == "failed"
    assert "No paid provider is authorised" in out.message
    assert "config/settings.yaml" in out.message
    assert "APIFY_TOKEN" in out.message


def test_a_failed_refresh_keeps_an_already_known_creator(session, adapter):
    """Only a stub is removed. Real collected history is never discarded."""
    service.research_creator(session, adapter, platform="tiktok",
                             handle="demo_pianist", limit=200, now=NOW)
    creator = service.find_creator(session, "tiktok", "demo_pianist")
    assert creator.publications_collected == 62

    class Broken(FixtureDiscoveryAdapter):
        def search(self, query):
            from server.discovery.base import DiscoveryError
            raise DiscoveryError("provider exploded", permanent=True,
                                 provider="fixture")

    out = service.research_creator(session, Broken(), platform="tiktok",
                                   handle="demo_pianist", limit=200,
                                   refresh=True,
                                   now=NOW + timedelta(days=2))
    assert out.status == "failed"
    assert out.creator_id == creator.id
    kept = service.find_creator(session, "tiktok", "demo_pianist")
    assert kept is not None
    assert kept.publications_collected == 62
    assert kept.research_status == "failed"
