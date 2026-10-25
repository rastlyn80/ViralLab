"""Competitor research endpoints.

Thin: validate, delegate to `server/research/service.py`, return a typed
model. The analysis lives in the research package so the tests can drive it
against a session without going through HTTP.

Two things this layer is responsible for and nothing else is:

**Choosing the adapter.** `fixture` unless a provider is authorised AND a
token exists, so a request cannot accidentally reach a paid endpoint. The
active provider is reported to the client, which displays it.

**Never spending on a read.** Only `POST /api/research/creators` can cost
anything, and only when a paid provider is active. Everything else reads
what is already stored.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from server import budget, config
from server.api.deps import get_session
from server.api.ui import require_ui, require_ui_write
from server.db.models import (BreakthroughCandidate, Creator,
                              CreatorSnapshot, DiscoveryJob, MetricsSnapshot,
                              Video)
from server.discovery.base import (DiscoveryQuery, Platform,
                                   ProviderCapabilities, QueryKind)
from server.discovery.fixture import FixtureDiscoveryAdapter, adapter_for
from server.research import baseline as bl
from server.research import service
from server.research.breakthrough import METHOD_VERSION
from shared.schemas.research import (BreakthroughReportView,
                                     BreakthroughView, PerformanceSummary,
                                     ProviderCapabilityView,
                                     ResearchedCreatorDetail,
                                     ResearchedCreatorRow, ResearchJobView,
                                     ResearchPublicationPage,
                                     ResearchPublicationRow, ResearchRequest,
                                     ResearchResultView, SpendingState)

logger = logging.getLogger("virallab.api.research")

router = APIRouter(prefix="/api/research", tags=["research"])

APIFY_TOKEN_ENV = "APIFY_TOKEN"


def active_provider() -> str:
    """Which adapter serves requests right now.

    Fixture unless a provider is authorised in settings AND its credential
    is present. Both conditions, because either alone would let a request
    reach a paid endpoint the owner has not approved.
    """
    spending = config.settings().spending
    if "apify" in spending.providers_authorised and spending.monthly_cap_usd > 0:
        if os.environ.get(APIFY_TOKEN_ENV):
            return "apify"
        logger.warning("apify is authorised in settings but %s is not set; "
                       "staying in fixture mode", APIFY_TOKEN_ENV)
    return "fixture"


def build_adapter():
    return adapter_for(active_provider(),
                       token=os.environ.get(APIFY_TOKEN_ENV))


def _caps_view(caps: ProviderCapabilities) -> ProviderCapabilityView:
    return ProviderCapabilityView(
        platform=caps.platform.value, provider=caps.provider,
        supported_queries=[q.value for q in caps.supported_queries],
        follower_count=caps.follower_count, creator_bio=caps.creator_bio,
        view_count=caps.view_count, views_rounded=caps.views_rounded,
        saves_count=caps.saves_count, media_url=caps.media_url,
        full_history=caps.full_history,
        missing_creator_fields=list(caps.missing_creator_fields()),
        coverage_notes=list(caps.coverage_notes))


def _job_view(job: DiscoveryJob | None) -> ResearchJobView | None:
    if job is None:
        return None
    return ResearchJobView(
        id=job.id, kind=job.kind, platform=job.platform,
        query_value=job.query_value, provider=job.provider,
        is_fixture=bool(job.is_fixture), status=job.status,
        started_at=job.started_at, finished_at=job.finished_at,
        estimated_usd=job.estimated_usd, actual_usd=job.actual_usd,
        creators_new=job.creators_new or 0,
        publications_seen=job.publications_seen or 0,
        publications_new=job.publications_new or 0,
        snapshots_written=job.snapshots_written or 0,
        coverage_notes=list((job.coverage_notes or {}).get("notes") or []),
        item_errors=list((job.item_errors or {}).get("errors") or []),
        error=job.error)


def _refresh_cost(platform: str, limit: int = 100) -> float:
    adapter = build_adapter()
    try:
        return adapter.estimate_usd(DiscoveryQuery(
            kind=QueryKind.CREATOR, platform=Platform(platform),
            value="placeholder", limit=limit))
    except Exception:                                        # noqa: BLE001
        return 0.0


# ---------------------------------------------------------------------------
# state
# ---------------------------------------------------------------------------

@router.get("/state", response_model=SpendingState,
            dependencies=[Depends(require_ui)])
def research_state(session: Session = Depends(get_session)) -> SpendingState:
    state = budget.budget_state(session)
    provider = active_provider()
    fixtures = (FixtureDiscoveryAdapter().available()
                if provider == "fixture" else [])
    return SpendingState(
        spending_blocked=state["spending_blocked"],
        monthly_cap_usd=state["monthly_cap_usd"],
        committed_usd=state["committed_usd"],
        available_usd=state["available_usd"],
        providers_authorised=state["providers_authorised"],
        period=state["period"],
        active_provider=provider,
        fixture_mode=provider == "fixture",
        available_fixtures=fixtures)


@router.get("/capabilities/{platform}",
            response_model=ProviderCapabilityView,
            dependencies=[Depends(require_ui)])
def capabilities(platform: str) -> ProviderCapabilityView:
    try:
        plat = Platform(platform)
    except ValueError:
        raise HTTPException(400, "platform must be tiktok or instagram")
    return _caps_view(build_adapter().capabilities(plat))


# ---------------------------------------------------------------------------
# research history
# ---------------------------------------------------------------------------

def build_creator_rows(session: Session, *, include_own: bool = False
                       ) -> list[ResearchedCreatorRow]:
    stmt = select(Creator).order_by(
        Creator.last_refreshed_at.desc().nulls_last())
    if not include_own:
        stmt = stmt.where(Creator.is_own_account.is_(False))
    creators = session.scalars(stmt).all()
    if not creators:
        return []
    ids = [c.id for c in creators]
    bt_counts = dict(session.execute(
        select(BreakthroughCandidate.creator_id,
               func.count(BreakthroughCandidate.id))
        .where(BreakthroughCandidate.creator_id.in_(ids))
        .group_by(BreakthroughCandidate.creator_id)).all())

    out = []
    for c in creators:
        out.append(ResearchedCreatorRow(
            creator_id=c.id, platform=c.platform, handle=c.handle,
            display_name=c.display_name, follower_count=c.follower_count,
            publications_collected=c.publications_collected or 0,
            research_status=c.research_status,
            last_refreshed_at=c.last_refreshed_at,
            is_stale=not service.is_fresh(c),
            refresh_cost_usd=_refresh_cost(c.platform),
            is_fixture=bool(c.is_fixture), source=c.source,
            breakthrough_count=bt_counts.get(c.id, 0),
            collection_cost_usd=c.collection_cost_usd))
    return out


@router.get("/creators", response_model=list[ResearchedCreatorRow],
            dependencies=[Depends(require_ui)])
def researched_creators(
    include_own: bool = Query(default=False),
    session: Session = Depends(get_session),
) -> list[ResearchedCreatorRow]:
    return build_creator_rows(session, include_own=include_own)


# ---------------------------------------------------------------------------
# run research
# ---------------------------------------------------------------------------

@router.post("/creators", response_model=ResearchResultView,
             dependencies=[Depends(require_ui_write)])
def run_research(req: ResearchRequest,
                 session: Session = Depends(get_session)
                 ) -> ResearchResultView:
    if req.platform not in ("tiktok", "instagram"):
        raise HTTPException(400, "platform must be tiktok or instagram")
    if not req.handle.strip():
        raise HTTPException(400, "a username or profile URL is required")
    limit = max(1, min(req.limit, 500))

    adapter = build_adapter()
    outcome = service.research_creator(
        session, adapter, platform=req.platform, handle=req.handle,
        limit=limit, refresh=req.refresh)

    job = (session.get(DiscoveryJob, outcome.job_id)
           if outcome.job_id else None)
    return ResearchResultView(
        status=outcome.status, message=outcome.message,
        creator_id=outcome.creator_id, job=_job_view(job),
        reused=outcome.reused, is_fixture=outcome.is_fixture,
        breakthroughs_found=outcome.breakthroughs_found)


# ---------------------------------------------------------------------------
# creator detail
# ---------------------------------------------------------------------------

def build_creator_detail(session: Session, creator_id: int
                         ) -> ResearchedCreatorDetail:
    c = session.get(Creator, creator_id)
    if c is None:
        raise HTTPException(404, "no such creator")

    observations = service.observations_for(session, creator_id)
    summary = bl.summarise(observations)

    snaps = session.scalars(
        select(CreatorSnapshot)
        .where(CreatorSnapshot.creator_id == creator_id)
        .order_by(CreatorSnapshot.captured_at.asc())).all()
    follower_obs = [
        {"captured_at": s.captured_at.isoformat(),
         "follower_count": s.follower_count,
         "source": s.source, "is_rounded": bool(s.is_rounded)}
        for s in snaps]

    caps = None
    try:
        caps = _caps_view(build_adapter().capabilities(Platform(c.platform)))
    except ValueError:
        pass

    return ResearchedCreatorDetail(
        creator_id=c.id, platform=c.platform, handle=c.handle,
        display_name=c.display_name, profile_url=c.profile_url,
        bio=None, follower_count=c.follower_count,
        follower_observations=follower_obs,
        research_status=c.research_status,
        last_refreshed_at=c.last_refreshed_at,
        is_stale=not service.is_fresh(c), is_fixture=bool(c.is_fixture),
        source=c.source, discovered_via=c.discovered_via,
        refresh_cost_usd=_refresh_cost(c.platform),
        summary=PerformanceSummary(
            publications=summary.publications, with_views=summary.with_views,
            first_published=summary.first_published,
            last_published=summary.last_published,
            observed_at=summary.observed_at,
            median_views=summary.median_views,
            median_views_recent=summary.median_views_recent,
            top_views=summary.top_views, median_likes=summary.median_likes,
            age_span_days=summary.age_span_days,
            counts_rounded=summary.counts_rounded,
            caveats=summary.caveats),
        capabilities=caps,
        last_job=_job_view(session.get(DiscoveryJob, c.last_job_id)
                           if c.last_job_id else None))


@router.get("/creators/{creator_id}",
            response_model=ResearchedCreatorDetail,
            dependencies=[Depends(require_ui)])
def creator_detail(creator_id: int, session: Session = Depends(get_session)
                   ) -> ResearchedCreatorDetail:
    return build_creator_detail(session, creator_id)


# ---------------------------------------------------------------------------
# publication explorer
# ---------------------------------------------------------------------------

def build_publications(session: Session, creator_id: int, *,
                       q: str | None = None, order: str = "newest",
                       limit: int = 50, offset: int = 0
                       ) -> ResearchPublicationPage:
    if session.get(Creator, creator_id) is None:
        raise HTTPException(404, "no such creator")

    observations = service.observations_for(session, creator_id)
    points = {p.observation.video_id: p
              for p in bl.rolling_baseline(observations)}
    bts = {b.video_id: b for b in session.scalars(
        select(BreakthroughCandidate).where(
            BreakthroughCandidate.creator_id == creator_id)).all()}

    videos = session.scalars(select(Video).where(
        Video.creator_id == creator_id)).all()
    latest = {}
    for s in session.scalars(select(MetricsSnapshot).where(
            MetricsSnapshot.video_id.in_([v.id for v in videos]))
            .order_by(MetricsSnapshot.captured_at.asc())).all() if videos \
            else []:
        latest[s.video_id] = s

    rows: list[ResearchPublicationRow] = []
    for v in videos:
        snap = latest.get(v.id)
        point = points.get(v.id)
        music = v.music or {}
        age = None
        if v.posted_at and snap is not None:
            age = max(0, (snap.captured_at - v.posted_at).days)
        rows.append(ResearchPublicationRow(
            video_id=v.id, external_id=v.external_id, posted_at=v.posted_at,
            caption=v.caption, hashtags=list(v.hashtags or []),
            duration_s=v.duration_s, url=None,
            thumbnail_url=v.thumbnail_url,
            views=snap.views if snap else None,
            likes=snap.likes if snap else None,
            comments=snap.comments if snap else None,
            shares=snap.shares if snap else None,
            saves=snap.saves if snap else None,
            counts_rounded=bool(snap.is_rounded) if snap else False,
            observed_at=snap.captured_at if snap else None,
            age_days=age,
            relative_performance=point.relative_performance if point
            else None,
            robust_z=point.robust_z if point else None,
            music_title=music.get("title"),
            music_artist=music.get("artist"),
            breakthrough=(bts[v.id].classification if v.id in bts else None),
            is_fixture=bool(v.is_fixture)))

    if q:
        needle = q.lower()
        rows = [r for r in rows
                if needle in (r.caption or "").lower()
                or needle in (r.music_title or "").lower()
                or any(needle in h for h in r.hashtags)]

    if order == "views":
        rows.sort(key=lambda r: (r.views is None, -(r.views or 0)))
    elif order == "relative":
        # The honest ranking: performance against the creator's own local
        # baseline, which does not reward a post simply for being older.
        rows.sort(key=lambda r: (r.relative_performance is None,
                                 -(r.relative_performance or 0)))
    elif order == "oldest":
        rows.sort(key=lambda r: (r.posted_at is None, r.posted_at))
    else:
        rows.sort(key=lambda r: (r.posted_at is None, r.posted_at),
                  reverse=True)

    total = len(rows)
    notes = [
        "Relative performance compares a publication to the creator's own "
        "preceding publications, not to other creators or to absolute view "
        "counts.",
        "The earliest publications have no relative figure: there are not "
        "enough prior posts to form a baseline, and a weak baseline is "
        "worse than none.",
    ]
    if any(r.counts_rounded for r in rows):
        notes.append("This source rounds large counts to about three "
                     "significant figures, so small differences are not "
                     "resolvable.")
    return ResearchPublicationPage(
        total=total, offset=offset, limit=limit,
        items=rows[offset:offset + limit], notes=notes)


@router.get("/creators/{creator_id}/publications",
            response_model=ResearchPublicationPage,
            dependencies=[Depends(require_ui)])
def creator_publications(
    creator_id: int,
    q: str | None = Query(default=None, max_length=200),
    order: str = Query(default="newest",
                       pattern="^(newest|oldest|views|relative)$"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(get_session),
) -> ResearchPublicationPage:
    return build_publications(session, creator_id, q=q, order=order,
                              limit=limit, offset=offset)


# ---------------------------------------------------------------------------
# breakthroughs
# ---------------------------------------------------------------------------

def _bt_view(row: BreakthroughCandidate, video: Video | None,
             total: int) -> BreakthroughView:
    return BreakthroughView(
        id=row.id, classification=row.classification,
        video_id=row.video_id,
        external_id=video.external_id if video else None,
        posted_at=video.posted_at if video else None,
        caption=(video.caption[:200] if video and video.caption else None),
        publication_index=row.publication_index, publications_total=total,
        observed_views=row.observed_views,
        baseline_views=row.baseline_views, median_after=row.median_after,
        relative_performance=row.relative_performance,
        robust_z=row.robust_z,
        step_ratio=(row.evidence or {}).get("step_ratio"),
        sustained=row.sustained, confidence=row.confidence,
        evidence=row.evidence or {},
        missing_evidence=list((row.missing_evidence or {}).get("missing")
                              or []),
        decided_by=row.decided_by, method_version=row.method_version)


def build_breakthroughs(session: Session, creator_id: int
                        ) -> BreakthroughReportView:
    c = session.get(Creator, creator_id)
    if c is None:
        raise HTTPException(404, "no such creator")

    rows = session.scalars(select(BreakthroughCandidate).where(
        BreakthroughCandidate.creator_id == creator_id).order_by(
        BreakthroughCandidate.publication_index)).all()
    videos = {v.id: v for v in session.scalars(select(Video).where(
        Video.creator_id == creator_id)).all()}
    total = len(videos)

    follower_obs = session.scalar(
        select(func.count(CreatorSnapshot.id)).where(
            CreatorSnapshot.creator_id == creator_id,
            CreatorSnapshot.follower_count.isnot(None))) or 0
    repeated = session.scalar(
        select(func.count()).select_from(
            select(MetricsSnapshot.video_id)
            .join(Video, Video.id == MetricsSnapshot.video_id)
            .where(Video.creator_id == creator_id)
            .group_by(MetricsSnapshot.video_id)
            .having(func.count(MetricsSnapshot.id) > 1).subquery())) or 0

    breakthrough = next(
        (r for r in rows if r.classification in (
            "inferred_breakthrough", "verified_trajectory_change")), None)
    outliers = [r for r in rows if r.classification == "observed_outlier"]

    refusals: list[str] = []
    if breakthrough is None:
        refusals.append(
            "No change point met the threshold. Gradual growth and a single "
            "lucky post both look like this, and neither is a breakthrough.")
    caveats = list((rows[0].evidence or {}).get("caveats") or []) if rows \
        else []
    if repeated == 0:
        caveats.append(
            "No publication has been observed more than once, so no "
            "trajectory can be verified. Research this creator again later "
            "and the same detector gains that evidence.")
    if follower_obs <= 1:
        caveats.append(
            "There is no follower history for this account, so no claim "
            "can be made that any publication caused follower growth.")

    return BreakthroughReportView(
        creator_id=creator_id, method_version=METHOD_VERSION,
        publications_considered=total,
        breakthrough=(_bt_view(breakthrough, videos.get(
            breakthrough.video_id), total) if breakthrough else None),
        outliers=[_bt_view(r, videos.get(r.video_id), total)
                  for r in outliers],
        refusals=refusals, caveats=caveats,
        has_follower_history=follower_obs > 1,
        has_repeated_observations=repeated > 0)


@router.get("/creators/{creator_id}/breakthroughs",
            response_model=BreakthroughReportView,
            dependencies=[Depends(require_ui)])
def creator_breakthroughs(creator_id: int,
                          session: Session = Depends(get_session)
                          ) -> BreakthroughReportView:
    return build_breakthroughs(session, creator_id)


@router.get("/jobs", response_model=list[ResearchJobView],
            dependencies=[Depends(require_ui)])
def jobs(limit: int = Query(default=20, ge=1, le=100),
         session: Session = Depends(get_session)) -> list[ResearchJobView]:
    rows = session.scalars(select(DiscoveryJob).order_by(
        DiscoveryJob.id.desc()).limit(limit)).all()
    return [_job_view(r) for r in rows if r is not None]
