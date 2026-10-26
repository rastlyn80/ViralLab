"""AI provider, routing and visual-analysis endpoints.

Every endpoint here is a read except `POST /api/ai/analyse`, and that one
cannot spend without both an authorised category and an explicit
confirmation. `GET /api/ai/plan` exists so the interface can show what a
request *would* do and cost before anyone commits - which is only useful if
asking is free, so it never calls a provider.

No endpoint returns a credential, and no response contains one. Availability
is reported as a boolean plus the name of the missing environment variable.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from server import budget, config
from server.ai import vision_task
from server.ai.base import AnalysisRequest, Hosting, Modality
from server.ai.providers import default_registry
from server.ai.routing import Mode, Policy, route
from server.api.deps import get_session
from server.api.ui import require_ui, require_ui_write
from server.db.models import AiOperation, Creator, Video
from shared.schemas.ai import (AiOperationRow, AnalyseRequestBody,
                               AnalysisPlanView, PolicyView, ProviderRow,
                               SpendingCategoryView, VisionTaskView)

logger = logging.getLogger("virallab.api.ai")

router = APIRouter(prefix="/api/ai", tags=["ai"])

_REGISTRY = default_registry()


def current_policy() -> Policy:
    """Policy from settings. Defaults refuse everything that costs."""
    spending = config.settings().spending
    raw = config.settings().section("ai") or {}
    mode = raw.get("mode", Mode.LOCAL_ONLY.value)
    try:
        mode = Mode(mode)
    except ValueError:
        logger.warning("unknown ai.mode %r; falling back to local_only", mode)
        mode = Mode.LOCAL_ONLY
    return Policy(
        mode=mode,
        escalation_authorised=bool(raw.get("escalation_authorised", False)),
        authorised_providers=tuple(raw.get("authorised_providers", ())),
        max_usd_per_request=float(raw.get("max_usd_per_request", 0.05)),
        escalate_below_confidence=float(
            raw.get("escalate_below_confidence", 0.60)),
        allow_third_party_media_to_cloud=bool(
            raw.get("allow_third_party_media_to_cloud", False)),
    )


@router.get("/providers", response_model=list[ProviderRow],
            dependencies=[Depends(require_ui)])
def providers() -> list[ProviderRow]:
    """Every registered provider, available or not, and why."""
    return [ProviderRow(**row) for row in _REGISTRY.describe()]


@router.get("/policy", response_model=PolicyView,
            dependencies=[Depends(require_ui)])
def policy(session: Session = Depends(get_session)) -> PolicyView:
    p = current_policy()
    spending = config.settings().spending
    cats = []
    for name in budget.CATEGORIES:
        cap = spending.cap_for(name)
        state = budget.committed_usd(
            session, "openai", budget.period_of(), name)
        cats.append(SpendingCategoryView(
            category=name, cap_usd=cap, authorised=cap > 0,
            committed_usd=state["committed_usd"],
            remaining_usd=max(0.0, round(cap - state["committed_usd"], 4)),
            note=("not configured, which means NOT authorised - an "
                  "unconfigured category is never treated as unlimited"
                  if cap <= 0 else "")))
    return PolicyView(
        mode=p.mode.value,
        escalation_authorised=p.escalation_authorised,
        authorised_providers=list(p.authorised_providers),
        max_usd_per_request=p.max_usd_per_request,
        escalate_below_confidence=p.escalate_below_confidence,
        allow_third_party_media_to_cloud=p.allow_third_party_media_to_cloud,
        monthly_cap_usd=spending.monthly_cap_usd,
        categories=cats,
        modes=[m.value for m in Mode],
        note=("Mode and authorisation are separate on purpose: choosing "
              "hybrid is not itself permission to spend. A local model that "
              "fails never causes a paid request."))


@router.get("/task", response_model=VisionTaskView,
            dependencies=[Depends(require_ui)])
def task() -> VisionTaskView:
    plan = vision_task.FramePlan()
    return VisionTaskView(
        task=vision_task.TASK,
        json_schema=vision_task.SCHEMA,
        prompt=vision_task.build_prompt(),
        hook_seconds=plan.hook_seconds,
        hook_frames=plan.hook_frames,
        later_offsets_s=list(plan.later_offsets_s),
        refusals=vision_task.refusals())


def _build_request(session: Session, video_id: int) -> tuple[AnalysisRequest,
                                                             Video, bool]:
    video = session.get(Video, video_id)
    if video is None:
        raise HTTPException(404, "no such publication")
    creator = session.get(Creator, video.creator_id)
    own = bool(creator and creator.is_own_account)
    plan = vision_task.FramePlan()
    stamps = plan.timestamps(video.duration_s)
    request = AnalysisRequest(
        task=vision_task.TASK, schema=vision_task.SCHEMA,
        prompt=vision_task.build_prompt(),
        frames=[(t, b"") for t in stamps],      # sizes filled at call time
        video_id=video.id,
        required_modalities=(Modality.IMAGE_SEQUENCE,))
    return request, video, own


@router.get("/plan", response_model=AnalysisPlanView,
            dependencies=[Depends(require_ui)])
def plan(video_id: int = Query(...), mode: str | None = Query(None),
         session: Session = Depends(get_session)) -> AnalysisPlanView:
    """What analysing this video would do and cost. Calls no provider."""
    request, video, own = _build_request(session, video_id)
    pol = current_policy()
    if mode:
        try:
            pol = Policy(**{**vars(pol), "mode": Mode(mode)})
        except ValueError as exc:
            raise HTTPException(400, f"unknown mode {mode!r}") from exc

    decision = route(_REGISTRY, request, pol, is_own_media=own)
    category = "cloud_vision"
    cap = config.settings().spending.cap_for(category)
    return AnalysisPlanView(
        video_id=video.id, task=request.task, mode=decision.mode.value,
        providers=decision.providers, refused=decision.refused,
        reason=decision.reason,
        estimated_usd=decision.estimated_usd,
        requires_authorisation=decision.requires_authorisation,
        considered=decision.considered,
        frames_planned=len(request.frames),
        frame_timestamps=[t for t, _ in request.frames],
        is_own_media=own,
        category=category, category_cap_usd=cap,
        category_authorised=cap > 0)


@router.post("/analyse", response_model=AiOperationRow,
             dependencies=[Depends(require_ui_write)])
def analyse(body: AnalyseRequestBody,
            session: Session = Depends(get_session)) -> AiOperationRow:
    """Run the visual task. Refuses rather than spending unconfirmed."""
    request, video, own = _build_request(session, body.video_id)
    pol = current_policy()
    if body.mode:
        try:
            pol = Policy(**{**vars(pol), "mode": Mode(body.mode)})
        except ValueError as exc:
            raise HTTPException(400, f"unknown mode {body.mode!r}") from exc

    decision = route(_REGISTRY, request, pol, is_own_media=own)
    if decision.refused or not decision.providers:
        raise HTTPException(400, decision.reason)

    provider = _REGISTRY.get(decision.providers[0])
    hosting = provider.capabilities().hosting

    if hosting is Hosting.CLOUD:
        if not body.confirm_paid:
            raise HTTPException(
                402,
                f"This would send {len(request.frames)} frames to "
                f"{provider.name} at an estimated "
                f"${decision.estimated_usd:.4f}. Re-send with "
                f"confirm_paid=true to authorise this specific request.")
        category = "cloud_vision"
        try:
            reservation = budget.reserve(
                session, provider=provider.name,
                estimated_usd=decision.estimated_usd,
                idempotency_key=f"vision:{video.id}:{provider.name}:"
                                f"{vision_task.TASK}",
                category=category)
        except config.SpendRefused as exc:
            raise HTTPException(402, str(exc)) from exc
        session.commit()
    else:
        reservation = None

    # The local model runs on the Windows workstation, not here. This
    # server has no GPU, so a local route cannot execute inline however
    # valid the request is - it has to be queued for the worker.
    #
    # An earlier revision of this comment said frames were decoded on this
    # machine and nothing was transferred for a local provider. That stopped
    # being true in Phase 6C: frames now travel to the worker over
    # authenticated /api/frames, and the execution path is the queue.
    if hosting is Hosting.LOCAL:
        raise HTTPException(
            409,
            f"Routing selected {provider.name}, which runs on the Windows "
            f"workstation. This server has no GPU and cannot invoke it, so "
            f"local analysis is queued rather than run inline: POST "
            f"/api/vision/requests with this video_id, and the worker picks "
            f"it up when that machine is next on. Progress is at "
            f"GET /api/vision/requests.")

    raise HTTPException(
        501,
        f"Routing selected {provider.name} ({hosting.value}) and the "
        f"request is valid, but no cloud execution path is wired. No cloud "
        f"credential is present, so this endpoint refuses rather than "
        f"returning an empty analysis. "
        f"{'A budget reservation was taken and will expire unused.' if reservation else ''}")


@router.get("/operations", response_model=list[AiOperationRow],
            dependencies=[Depends(require_ui)])
def operations(limit: int = Query(50, ge=1, le=200),
               session: Session = Depends(get_session)
               ) -> list[AiOperationRow]:
    rows = session.scalars(
        select(AiOperation).order_by(AiOperation.id.desc()).limit(limit)
    ).all()
    return [AiOperationRow(
        id=r.id, video_id=r.video_id, task=r.task, provider=r.provider,
        model=r.model, hosting=r.hosting, category=r.category, mode=r.mode,
        status=r.status, schema_valid=r.schema_valid,
        validation_errors=r.validation_errors or [],
        refused_reason=r.refused_reason, input_tokens=r.input_tokens,
        output_tokens=r.output_tokens, images=r.images,
        estimated_usd=r.estimated_usd, actual_usd=r.actual_usd,
        latency_s=r.latency_s, peak_vram_mib=r.peak_vram_mib,
        created_at=r.created_at) for r in rows]
