"""Hook Intelligence endpoints.

Reads what the extractor stored and runs the analysis on demand. Two things
this layer is responsible for:

**Never presenting an unprocessed video as analysed.** The list reports
`features_extracted` per row and the coverage figures say how much of the
corpus has been through the extractor at all. A video with no features
appears as exactly that.

**Keeping extraction a write.** `POST /api/hooks/extract` is the only
endpoint that does work, it carries the CSRF guard, and it is bounded by an
explicit scope so a click cannot start an unbounded job by accident.
"""

from __future__ import annotations

import logging
import os

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from server.api.deps import get_session
from server.api.ui import MEDIA_TTL, SESSION_SECRET, require_ui, require_ui_write
from server.db.models import (Creator, MetricsSnapshot, Video, VideoFeature,
                              VideoFeatureRun)
from server.features import extract as fx
from server.features import retention as rt
from shared import hookfeat
from shared.schemas.hooks import (AnalysedVideoPage, AnalysedVideoRow,
                                  AssociationView, CorrectionRequest,
                                  ExtractionRequest, ExtractionResultView,
                                  FeatureView, HookCoverage, HookDetail,
                                  RetentionReportView)
from shared.utils.signing import sign_media_url

logger = logging.getLogger("virallab.api.hooks")

router = APIRouter(prefix="/api/hooks", tags=["hooks"])

HEADLINE = ("cut_count", "text_region_score", "speech_present",
            "first_sound_at_s")


def _feature_view(row: VideoFeature) -> FeatureView:
    corrected = None
    for v in (row.corrected_value_num, row.corrected_value_bool,
              row.corrected_value_text):
        if v is not None:
            corrected = v
            break
    value = row.value
    if isinstance(value, dict) and set(value) == {"items"}:
        value = value["items"]
    return FeatureView(
        id=row.id, feature=row.feature, value=value, unit=row.unit,
        method=row.method, method_version=row.method_version,
        model=row.model, uncertainty=row.uncertainty,
        uncertainty_kind=row.uncertainty_kind,
        interval_start_s=row.interval_start_s,
        interval_end_s=row.interval_end_s, params=row.params or {},
        note=row.note, unavailable_reason=row.unavailable_reason,
        extracted_at=row.extracted_at, corrected_value=corrected,
        corrected_by=row.corrected_by, corrected_at=row.corrected_at,
        correction_note=row.correction_note,
        is_corrected=row.corrected_at is not None)


def _outcomes(session: Session) -> dict[int, dict]:
    return rt._outcome_rows(session)


@router.get("/coverage", response_model=HookCoverage,
            dependencies=[Depends(require_ui)])
def coverage(session: Session = Depends(get_session)) -> HookCoverage:
    c = fx.coverage(session)
    return HookCoverage(
        **c,
        note=(f"{c['analysable']} publications have a preserved file, but "
              f"only {c['with_retention']} also have a retention metric. "
              f"That smaller number is the ceiling on any statistical "
              f"claim here, and it cannot be raised by processing more "
              f"videos."))


@router.get("/videos", response_model=AnalysedVideoPage,
            dependencies=[Depends(require_ui)])
def videos(limit: int = Query(50, ge=1, le=200),
           offset: int = Query(0, ge=0),
           only: str = Query("retention",
                             pattern="^(retention|extracted|analysable)$"),
           order: str = Query("skip", pattern="^(skip|recent|extracted)$"),
           session: Session = Depends(get_session)) -> AnalysedVideoPage:
    outcomes = _outcomes(session)
    cov = fx.coverage(session)

    extracted_ids = {
        vid for (vid,) in session.execute(
            select(VideoFeature.video_id).distinct()).all()}

    if only == "retention":
        pool = [d["video"] for d in outcomes.values()
                if d["video"].media_status == "available"]
    elif only == "extracted":
        pool = list(session.scalars(
            select(Video).where(Video.id.in_(extracted_ids or {-1}))).all())
    else:
        pool = fx.analysable(session)

    counts = dict(session.execute(
        select(VideoFeature.video_id, func.count(VideoFeature.id))
        .group_by(VideoFeature.video_id)).all())
    corrections = dict(session.execute(
        select(VideoFeature.video_id, func.count(VideoFeature.id))
        .where(VideoFeature.corrected_at.isnot(None))
        .group_by(VideoFeature.video_id)).all())
    versions: dict[int, set] = {}
    for vid, mv in session.execute(
            select(VideoFeature.video_id,
                   VideoFeature.method_version).distinct()).all():
        versions.setdefault(vid, set()).add(mv)

    head = {}
    for row in session.scalars(select(VideoFeature).where(
            VideoFeature.feature.in_(HEADLINE))).all():
        head.setdefault(row.video_id, {})[row.feature] = row.effective_value

    rows = []
    for v in pool:
        d = outcomes.get(v.id, {})
        h = head.get(v.id, {})
        sp = h.get("speech_present")
        rows.append(AnalysedVideoRow(
            video_id=v.id, external_id=v.external_id, posted_at=v.posted_at,
            duration_s=v.duration_s, caption=v.caption,
            views=None, skip_rate=d.get("skip_rate"),
            avg_watch_ms=d.get("avg_watch_ms"),
            age_days_at_capture=d.get("age_days"),
            features_extracted=counts.get(v.id, 0),
            method_versions=sorted(versions.get(v.id, set())),
            has_media=v.media_status == "available",
            corrections=corrections.get(v.id, 0),
            cut_count=h.get("cut_count"),
            text_region_score=h.get("text_region_score"),
            speech_present=bool(sp) if sp is not None else None,
            first_sound_at_s=h.get("first_sound_at_s")))

    if order == "skip":
        rows.sort(key=lambda r: (r.skip_rate is None,
                                 r.skip_rate if r.skip_rate is not None
                                 else 0))
    elif order == "extracted":
        rows.sort(key=lambda r: -r.features_extracted)
    else:
        rows.sort(key=lambda r: (r.posted_at is None, r.posted_at),
                  reverse=True)

    total = len(rows)
    return AnalysedVideoPage(
        total=total, offset=offset, limit=limit,
        items=rows[offset:offset + limit],
        analysable=cov["analysable"], extracted=cov["deterministic_done"],
        with_retention=cov["with_retention"])


@router.get("/videos/{video_id}", response_model=HookDetail,
            dependencies=[Depends(require_ui)])
def detail(video_id: int,
           session: Session = Depends(get_session)) -> HookDetail:
    video = session.get(Video, video_id)
    if video is None:
        raise HTTPException(404, "no such publication")
    creator = session.get(Creator, video.creator_id)
    if creator is None or not creator.is_own_account:
        raise HTTPException(
            403, "hook analysis covers your own publications only; "
                 "third-party media is not retained")

    d = _outcomes(session).get(video_id, {})
    rows = fx.features_for(session, video_id)

    media_url = None
    if video.media_status == "available" and SESSION_SECRET:
        media_url = sign_media_url("", video.id, SESSION_SECRET,
                                   ttl_seconds=MEDIA_TTL)

    runs = []
    run_ids = {r.run_id for r in rows if r.run_id}
    for run in session.scalars(select(VideoFeatureRun).where(
            VideoFeatureRun.id.in_(run_ids or {-1}))).all():
        runs.append({"id": run.id, "kind": run.kind,
                     "method_version": run.method_version,
                     "status": run.status, "started_at": run.started_at,
                     "compute_seconds": run.compute_seconds,
                     "params": run.params})

    caveats = []
    if not rows:
        caveats.append(
            "Not analysed. Nothing on this page describes the opening "
            "because no extraction has been run for it.")
    if d.get("skip_rate") is None:
        caveats.append(
            "No retention metric for this publication, so it contributes "
            "nothing to the hook-versus-retention analysis.")
    if d.get("avg_watch_ms") is not None and video.duration_s:
        ratio = d["avg_watch_ms"] / 1000.0 / video.duration_s
        if ratio > 1.0:
            caveats.append(
                f"Average watch time is {ratio:.2f}x the video's duration. "
                f"Instagram counts replays, so this is not a completion "
                f"percentage and must not be read as one.")
    if any(r.method == "proxy" for r in rows):
        caveats.append(
            "Some values are proxies derived from thresholds, not "
            "measurements. Each carries its own confidence and its "
            "measured error rate where one exists.")

    return HookDetail(
        video_id=video.id, external_id=video.external_id,
        posted_at=video.posted_at, duration_s=video.duration_s,
        caption=video.caption, platform=video.platform,
        skip_rate=d.get("skip_rate"), avg_watch_ms=d.get("avg_watch_ms"),
        views=None, media_url=media_url, media_status=video.media_status,
        hook_seconds=hookfeat.HOOK_SECONDS,
        features=[_feature_view(r) for r in rows], runs=runs,
        caveats=caveats)


@router.post("/videos/{video_id}/features/{feature_id}/correct",
             response_model=FeatureView,
             dependencies=[Depends(require_ui_write)])
def correct(video_id: int, feature_id: int, req: CorrectionRequest,
            session: Session = Depends(get_session)) -> FeatureView:
    row = session.get(VideoFeature, feature_id)
    if row is None or row.video_id != video_id:
        raise HTTPException(404, "no such feature on that publication")
    try:
        updated = fx.correct(session, feature_id, value=req.value,
                             by="human", note=req.note)
    except fx.ExtractionRefused as exc:
        raise HTTPException(400, str(exc)) from exc
    session.commit()
    return _feature_view(updated)


@router.post("/extract", response_model=list[ExtractionResultView],
             dependencies=[Depends(require_ui_write)])
def run_extraction(req: ExtractionRequest,
                   session: Session = Depends(get_session)
                   ) -> list[ExtractionResultView]:
    """Run extraction over a bounded scope. The only endpoint that works."""
    if req.scope == "ids":
        if not req.video_ids:
            raise HTTPException(400, "scope 'ids' needs video_ids")
        videos = list(session.scalars(
            select(Video).where(Video.id.in_(req.video_ids))).all())
    elif req.scope == "sample":
        videos = fx.representative_sample(
            session, n=max(1, min(req.sample_size, 50)))
    elif req.scope == "all":
        videos = fx.analysable(session)
    else:
        ids = fx.with_retention(session)
        videos = list(session.scalars(
            select(Video).where(Video.id.in_(ids or [-1]))).all())

    if not videos:
        raise HTTPException(400, "that scope selected no publications")

    own = {v.id for v in fx.analysable(session)}
    refused = [v.external_id for v in videos if v.id not in own]
    if refused:
        raise HTTPException(
            400, f"{len(refused)} publication(s) have no preserved file or "
                 f"are not your own; extraction reads a local file and "
                 f"third-party media is never retained")

    out = [_result_view(fx.run_deterministic(session, videos))]
    if req.include_speech:
        out.append(_result_view(fx.run_speech(session, videos)))
    return out


def _result_view(o: fx.ExtractOutcome) -> ExtractionResultView:
    return ExtractionResultView(
        run_id=o.run_id, kind=o.kind, method_version=o.method_version,
        requested=o.requested, done=o.done, failed=o.failed,
        features_written=o.features_written,
        compute_seconds=o.compute_seconds, errors=o.errors, notes=o.notes)


@router.get("/retention", response_model=RetentionReportView,
            dependencies=[Depends(require_ui)])
def retention_report(session: Session = Depends(get_session)
                     ) -> RetentionReportView:
    rep = rt.analyse(session)
    return RetentionReportView(
        outcome=rep.outcome, outcome_note=rep.outcome_note,
        n_videos=rep.n_videos, n_with_media=rep.n_with_media,
        n_with_features=rep.n_with_features, n_analysed=rep.n_analysed,
        duration_range_s=(list(rep.duration_range_s)
                          if rep.duration_range_s else None),
        age_range_days=(list(rep.age_range_days)
                        if rep.age_range_days else None),
        skip_range=list(rep.skip_range) if rep.skip_range else None,
        associations=[AssociationView(**vars(a)) for a in rep.associations],
        survivors=rep.survivors,
        survivor_collinearity=rep.survivor_collinearity,
        method_version=rep.method_version, verdict=rep.verdict,
        limitations=rep.limitations)
