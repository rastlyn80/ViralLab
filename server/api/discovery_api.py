"""Discovery, timeline, report and collection endpoints.

Same division of labour as `research_api.py`: validate, delegate, return a
typed model. Two rules are enforced here and nowhere else:

**Only one endpoint can spend money.** `POST /api/discovery/search` is it,
and only when a paid provider is active. Everything else - estimates,
candidates, timelines, reports, collections - reads what is already stored.
A GET in this module must never be capable of a provider call.

**An estimate is free and must stay free.** `GET /api/discovery/estimate`
exists so the interface can show a price before the owner commits, which is
only useful if asking the price costs nothing.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from server import budget, config
from server.api.deps import get_session
from server.api.research_api import active_provider, build_adapter
from server.api.ui import require_ui, require_ui_write
from server.db.models import Creator, DiscoveryJob
from server.discovery.base import DiscoveryQuery, Platform, QueryKind
from server.research import analysis_seam as seam
from server.research import collections as coll
from server.research import discover as disc
from server.research import preflight
from server.research import report as rep
from server.research import service
from server.research import timeline as tlmod
from shared.schemas.discovery import (AnalysisRequestBody,
                                      AnalysisRequestView, CandidateView,
                                      ChangePointDetail, CollectionDetail,
                                      CollectionCreateRequest,
                                      CollectionMemberRequest,
                                      CollectionMemberView, CollectionRow,
                                      CollectionUpdateRequest,
                                      CreatorReportView,
                                      CreatorTimelineView, DiscoveryEstimate,
                                      DiscoveryRequest, DiscoveryResultView,
                                      ExpansionPlanView, ExpansionRequest,
                                      FeatureContractView,
                                      FrequencyBucketView, NicheView,
                                      ReportGroupView, ReportSectionView,
                                      SearchHistoryRow,
                                      SustainedRunView, TimelinePointView)

logger = logging.getLogger("virallab.api.discovery")

router = APIRouter(prefix="/api", tags=["discovery"])


# ---------------------------------------------------------------------------
# discovery
# ---------------------------------------------------------------------------

@router.get("/discovery/niches", response_model=list[NicheView],
            dependencies=[Depends(require_ui)])
def niches() -> list[NicheView]:
    """Curated starting points. A niche expands into individual queries."""
    return [NicheView(key=k, label=v["label"], keywords=v["keywords"],
                      hashtags=v["hashtags"])
            for k, v in disc.NICHES.items()]


@router.get("/discovery/estimate", response_model=DiscoveryEstimate,
            dependencies=[Depends(require_ui)])
def estimate(kind: str = Query(...), platform: str = Query(...),
             value: str = Query(...), limit: int = Query(50, ge=1, le=500),
             session: Session = Depends(get_session)) -> DiscoveryEstimate:
    """What this search would cost. Reads only; never calls a provider."""
    if kind not in ("keyword", "hashtag", "creator"):
        raise HTTPException(
            400, "kind must be keyword, hashtag or creator")
    if platform not in ("tiktok", "instagram"):
        raise HTTPException(400, "platform must be tiktok or instagram")
    value = (value or "").strip()
    if not value:
        raise HTTPException(400, "a search term is required")
    if kind == "hashtag":
        value = value.lstrip("#")

    adapter = build_adapter()
    plat = Platform(platform)
    caps = adapter.capabilities(plat)
    qk = QueryKind(kind)
    supported = qk in caps.supported_queries

    out = DiscoveryEstimate(
        kind=kind, platform=platform, value=value, limit=limit,
        provider=adapter.name, is_fixture=adapter.is_fixture,
        estimated_usd=0.0, supported=supported)

    if not supported:
        out.unsupported_reason = (
            f"{adapter.name} cannot run a {kind} search on {platform}. "
            f"This is a provider limit, not a setting.")
        return out

    query = DiscoveryQuery(kind=qk, platform=plat, value=value, limit=limit)
    out.estimated_usd = round(adapter.estimate_usd(query), 4)

    spending = config.settings().spending
    out.monthly_cap_usd = spending.monthly_cap_usd
    out.spending_blocked = (active_provider() == "fixture")
    state = budget.budget_state(session, provider=adapter.name)
    out.remaining_usd = state.get("remaining_usd")

    recent = disc.recent_search(session, kind, platform, value)
    if recent is not None:
        out.reusable_job_id = recent.id
        out.reusable_started_at = recent.started_at

    # What this actually buys, as opposed to what it costs. These are
    # different questions and only the second one was being answered.
    pf = preflight.check(
        session, kind=kind, platform=platform, value=value, limit=limit,
        estimated_usd=out.estimated_usd, is_fixture=adapter.is_fixture,
        reusable_job=recent)
    out.recommendation = pf.recommendation
    out.observed_new_ratio = pf.observed_new_ratio
    out.observed_from_jobs = pf.observed_from_jobs
    out.likely_new = pf.likely_new
    out.likely_already_held = pf.likely_already_held
    out.wasted_usd_estimate = pf.wasted_usd_estimate
    out.preflight_reasons = list(pf.reasons)
    out.preflight_warnings = list(pf.warnings)
    if pf.holding.publications:
        out.already_held = {
            "creator_id": pf.holding.creator_id,
            "handle": pf.holding.handle,
            "publications": pf.holding.publications,
            "with_media": pf.holding.with_media,
            "analysed": pf.holding.analysed,
            "age_days": pf.holding.age_days,
            "follower_count": pf.holding.follower_count,
        }

    out.coverage_warnings = list(disc.SAMPLING_NOTES)
    for note in caps.coverage_notes:
        out.coverage_warnings.append(str(note))
    return out


def _candidate_view(c: disc.Candidate) -> CandidateView:
    return CandidateView(
        creator_id=c.creator_id, platform=c.platform, handle=c.handle,
        display_name=c.display_name, profile_url=c.profile_url,
        follower_count=c.follower_count,
        followers_available=c.followers_available,
        sampled_publications=c.sampled_publications,
        sampled_median_views=c.sampled_median_views,
        sampled_best_views=c.sampled_best_views,
        best_publication_external_id=c.best_publication_external_id,
        best_views_per_follower=c.best_views_per_follower,
        engagement_rate=c.engagement_rate,
        counts_rounded=c.counts_rounded,
        newest_publication_at=c.newest_publication_at,
        oldest_publication_at=c.oldest_publication_at, niche=c.niche,
        already_researched=c.already_researched,
        last_refreshed_at=c.last_refreshed_at,
        publications_collected=c.publications_collected,
        estimated_research_usd=c.estimated_research_usd, score=c.score,
        tier=c.tier, reasons=c.reasons,
        signals=[{"key": s.key, "label": s.label, "value": s.value,
                  "available": s.available, "note": s.note}
                 for s in c.signals],
        is_fixture=c.is_fixture)


@router.post("/discovery/search", response_model=DiscoveryResultView,
             dependencies=[Depends(require_ui_write)])
def search(req: DiscoveryRequest,
           session: Session = Depends(get_session)) -> DiscoveryResultView:
    """Run a concept search. The only endpoint here that can spend."""
    adapter = build_adapter()
    try:
        outcome = disc.discover(
            session, adapter, kind=req.kind, platform=req.platform,
            value=req.value, limit=max(1, min(req.limit, 500)),
            niche=req.niche, force=req.force)
    except disc.DiscoveryRefused as exc:
        raise HTTPException(400, str(exc)) from exc

    view = DiscoveryResultView(
        job_id=outcome.job_id, status=outcome.status, kind=outcome.kind,
        platform=outcome.platform, value=outcome.value, niche=outcome.niche,
        is_fixture=outcome.is_fixture,
        publications_seen=outcome.publications_seen,
        publications_new=outcome.publications_new,
        creators_seen=outcome.creators_seen,
        creators_new=outcome.creators_new,
        estimated_usd=outcome.estimated_usd, actual_usd=outcome.actual_usd,
        coverage_notes=outcome.coverage_notes,
        item_errors=outcome.item_errors, message=outcome.message)

    if outcome.job_id and outcome.status in ("ok", "reused"):
        view.candidates = [
            _candidate_view(c) for c in
            disc.candidates_for_job(session, adapter, outcome.job_id)]
    return view


@router.get("/discovery/searches", response_model=list[SearchHistoryRow],
            dependencies=[Depends(require_ui)])
def searches(limit: int = Query(25, ge=1, le=100),
             session: Session = Depends(get_session)
             ) -> list[SearchHistoryRow]:
    """Past searches, reopenable without recollecting."""
    out = []
    for job in disc.previous_searches(session, limit=limit):
        counts = disc.search_result_counts(session, job.id)
        reusable = disc.recent_search(
            session, job.kind, job.platform, job.query_value) is not None
        out.append(SearchHistoryRow(
            job_id=job.id, kind=job.kind, platform=job.platform,
            value=job.query_value, niche=(job.params or {}).get("niche"),
            provider=job.provider, is_fixture=bool(job.is_fixture),
            status=job.status, started_at=job.started_at,
            publications=counts["publications"], creators=counts["creators"],
            estimated_usd=job.estimated_usd, actual_usd=job.actual_usd,
            reusable=reusable))
    return out


@router.get("/discovery/searches/{job_id}/candidates",
            response_model=list[CandidateView],
            dependencies=[Depends(require_ui)])
def candidates(job_id: int, session: Session = Depends(get_session)
               ) -> list[CandidateView]:
    """Reopen a stored result set. Free, always."""
    adapter = build_adapter()
    try:
        rows = disc.candidates_for_job(session, adapter, job_id)
    except disc.DiscoveryRefused as exc:
        raise HTTPException(404, str(exc)) from exc
    return [_candidate_view(c) for c in rows]


@router.post("/discovery/expansion-plan", response_model=ExpansionPlanView,
             dependencies=[Depends(require_ui)])
def expansion_plan(req: ExpansionRequest,
                   session: Session = Depends(get_session)
                   ) -> ExpansionPlanView:
    """What researching a chosen set would cost. Spends nothing."""
    adapter = build_adapter()
    try:
        rows = disc.candidates_for_job(session, adapter, req.job_id)
    except disc.DiscoveryRefused as exc:
        raise HTTPException(404, str(exc)) from exc
    if req.creator_ids:
        wanted = set(req.creator_ids)
        rows = [c for c in rows if c.creator_id in wanted]
    plan = disc.plan_expansion(
        session, adapter, rows, max_creators=req.max_creators,
        max_spend_usd=req.max_spend_usd)
    return ExpansionPlanView(
        candidates=[_candidate_view(c) for c in plan.candidates],
        skipped_already_fresh=plan.skipped_already_fresh,
        total_estimated_usd=plan.total_estimated_usd,
        budget_remaining_usd=plan.budget_remaining_usd,
        within_budget=plan.within_budget, notes=plan.notes)


# ---------------------------------------------------------------------------
# timeline
# ---------------------------------------------------------------------------

@router.get("/research/creators/{creator_id}/timeline",
            response_model=CreatorTimelineView,
            dependencies=[Depends(require_ui)])
def timeline(creator_id: int, anchor_days: int | None = Query(None),
             session: Session = Depends(get_session)) -> CreatorTimelineView:
    try:
        tl = tlmod.build(session, creator_id, anchor_days=anchor_days)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc

    cp = None
    if tl.change_point is not None:
        c = tl.change_point
        cp = ChangePointDetail(
            classification=c.classification,
            publication_index=c.publication_index, video_id=c.video_id,
            at=c.at, median_before=c.median_before,
            median_after=c.median_after, step_ratio=c.step_ratio,
            sustained=c.sustained, confidence=c.confidence,
            confidence_is_calibrated=False,
            confidence_note=c.confidence_note, evidence=c.evidence,
            missing_evidence=c.missing_evidence)

    return CreatorTimelineView(
        creator_id=tl.creator_id, platform=tl.platform, handle=tl.handle,
        display_name=tl.display_name, is_fixture=tl.is_fixture,
        mode=tl.mode, mode_label=tl.mode_label,
        points=[TimelinePointView(**vars(p)) for p in tl.points],
        change_point=cp, outlier_candidates=tl.outlier_candidates,
        sustained_runs=[SustainedRunView(**vars(r))
                        for r in tl.sustained_runs],
        frequency=[FrequencyBucketView(**vars(f)) for f in tl.frequency],
        repeatedly_observed=tl.repeatedly_observed,
        anchored_points=tl.anchored_points, age_span_days=tl.age_span_days,
        median_days_between_posts=tl.median_days_between_posts,
        caveats=tl.caveats)


# ---------------------------------------------------------------------------
# report
# ---------------------------------------------------------------------------

@router.get("/research/creators/{creator_id}/report",
            response_model=CreatorReportView,
            dependencies=[Depends(require_ui)])
def creator_report(creator_id: int,
                   session: Session = Depends(get_session)
                   ) -> CreatorReportView:
    try:
        r = rep.build(session, creator_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    return CreatorReportView(
        creator_id=r.creator_id, platform=r.platform, handle=r.handle,
        display_name=r.display_name, profile_url=r.profile_url,
        is_fixture=r.is_fixture, generated_at=r.generated_at,
        sections=[ReportSectionView(
            key=s.key, title=s.title, basis=s.basis, body=_jsonable(s.body),
            caveats=s.caveats, unavailable_reason=s.unavailable_reason,
            group=s.group, grade=s.grade, grade_reason=s.grade_reason,
            evidence_video_ids=s.evidence_video_ids,
            contradictions=_jsonable(s.contradictions))
            for s in r.sections],
        # The four M5 sections, in reading order, each naming the
        # blocks it holds. Sent rather than hard-coded in the client so
        # the report's shape has exactly one definition.
        groups=[ReportGroupView(
            key=key, title=title,
            section_keys=[s.key for s in r.sections if s.group == key])
            for key, title in rep.GROUPS],
        lifetime_approximation=r.lifetime_approximation,
        age_span_days=r.age_span_days,
        research_date=r.research_date, provider=r.provider,
        cost_usd=r.cost_usd, job_id=r.job_id,
        data_limitations=r.data_limitations)


def _jsonable(value):
    """Datetimes inside a free-form dict body need coercing for JSON."""
    from datetime import datetime as _dt
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, _dt):
        return value.isoformat()
    return value


# ---------------------------------------------------------------------------
# collections
# ---------------------------------------------------------------------------

@router.get("/collections", response_model=list[CollectionRow],
            dependencies=[Depends(require_ui)])
def list_collections(session: Session = Depends(get_session)
                     ) -> list[CollectionRow]:
    return [CollectionRow(**row) for row in coll.list_collections(session)]


@router.post("/collections", response_model=CollectionRow,
             dependencies=[Depends(require_ui_write)])
def create_collection(req: CollectionCreateRequest,
                      session: Session = Depends(get_session)
                      ) -> CollectionRow:
    try:
        row = coll.create(session, name=req.name, purpose=req.purpose,
                          niche=req.niche, notes=req.notes)
    except coll.CollectionError as exc:
        raise HTTPException(400, str(exc)) from exc
    session.commit()
    return _collection_row(session, row.id)


def _collection_row(session: Session, collection_id: int) -> CollectionRow:
    for row in coll.list_collections(session):
        if row["id"] == collection_id:
            return CollectionRow(**row)
    raise HTTPException(404, f"no such collection {collection_id}")


@router.patch("/collections/{collection_id}", response_model=CollectionRow,
              dependencies=[Depends(require_ui_write)])
def update_collection(collection_id: int, req: CollectionUpdateRequest,
                      session: Session = Depends(get_session)
                      ) -> CollectionRow:
    try:
        coll.update(session, collection_id, name=req.name,
                    purpose=req.purpose, niche=req.niche, notes=req.notes)
    except coll.CollectionError as exc:
        raise HTTPException(400, str(exc)) from exc
    session.commit()
    return _collection_row(session, collection_id)


@router.delete("/collections/{collection_id}",
               dependencies=[Depends(require_ui_write)])
def delete_collection(collection_id: int,
                      session: Session = Depends(get_session)) -> dict:
    ok = coll.delete(session, collection_id)
    if not ok:
        raise HTTPException(404, f"no such collection {collection_id}")
    session.commit()
    return {"deleted": collection_id}


@router.get("/collections/{collection_id}", response_model=CollectionDetail,
            dependencies=[Depends(require_ui)])
def collection_detail(collection_id: int,
                      session: Session = Depends(get_session)
                      ) -> CollectionDetail:
    try:
        v = coll.view(session, collection_id)
    except coll.CollectionError as exc:
        raise HTTPException(404, str(exc)) from exc
    return CollectionDetail(
        id=v.id, name=v.name, purpose=v.purpose,
        purpose_label=v.purpose_label, niche=v.niche, notes=v.notes,
        created_at=v.created_at, updated_at=v.updated_at,
        members=[CollectionMemberView(**vars(m)) for m in v.members],
        member_count=v.member_count, researched_count=v.researched_count,
        stalest_refresh=v.stalest_refresh, needs_refresh=v.needs_refresh,
        caveats=v.caveats)


@router.post("/collections/{collection_id}/members",
             response_model=CollectionDetail,
             dependencies=[Depends(require_ui_write)])
def add_member(collection_id: int, req: CollectionMemberRequest,
               session: Session = Depends(get_session)) -> CollectionDetail:
    try:
        coll.add_member(session, collection_id, req.creator_id,
                        reason=req.reason, notes=req.notes,
                        discovered_by_job_id=req.discovered_by_job_id)
    except coll.CollectionError as exc:
        raise HTTPException(400, str(exc)) from exc
    session.commit()
    return collection_detail(collection_id, session)


@router.delete("/collections/{collection_id}/members/{creator_id}",
               response_model=CollectionDetail,
               dependencies=[Depends(require_ui_write)])
def remove_member(collection_id: int, creator_id: int,
                  session: Session = Depends(get_session)
                  ) -> CollectionDetail:
    if not coll.remove_member(session, collection_id, creator_id):
        raise HTTPException(404, "that creator is not in this collection")
    session.commit()
    return collection_detail(collection_id, session)


# ---------------------------------------------------------------------------
# analysis requests (Phase 6 seam)
# ---------------------------------------------------------------------------

@router.get("/analysis/contract", response_model=FeatureContractView,
            dependencies=[Depends(require_ui)])
def feature_contract(session: Session = Depends(get_session)
                     ) -> FeatureContractView:
    """What a future analysis pass will produce, and what is queued."""
    return FeatureContractView(
        version=seam.FEATURE_CONTRACT_VERSION,
        modalities=seam.FEATURE_CONTRACT,
        required_item_fields=list(seam.REQUIRED_ITEM_FIELDS),
        pending_requests=seam.pending_requests(session))


@router.post("/analysis/requests", response_model=AnalysisRequestView,
             dependencies=[Depends(require_ui_write)])
def request_analysis(req: AnalysisRequestBody,
                     session: Session = Depends(get_session)
                     ) -> AnalysisRequestView:
    """Queue analysis of one publication. Runs no GPU work."""
    try:
        r = seam.request_analysis(session, req.video_id, tier=req.tier,
                                  modalities=tuple(req.modalities))
    except seam.AnalysisRefused as exc:
        raise HTTPException(400, str(exc)) from exc
    session.commit()
    return AnalysisRequestView(
        video_id=r.video_id, external_id=r.external_id, platform=r.platform,
        tier=r.tier, modalities=r.modalities, batch_ids=r.batch_ids,
        media_policy=r.media_policy,
        estimated_gpu_seconds=r.estimated_gpu_seconds,
        estimated_transient_mb=r.estimated_transient_mb,
        will_run=r.will_run, why_not=r.why_not, notes=r.notes)
