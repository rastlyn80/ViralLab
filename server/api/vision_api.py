"""Work list and result intake for the GPU vision worker.

Two endpoints, both behind the per-worker bearer token:

  `GET  /api/vision/pending`  what still needs analysing under the current
                              method version
  `POST /api/vision/results`  one video's validated scene description

Results are stored as ordinary `video_features` rows, keyed on
(video, feature, method_version) like every other feature in the system.
That is deliberate: a model's answer is evidence of the same kind as a
measured cut count, differing in `method` (`model`) and in carrying its own
confidence. It inherits versioned storage, human correction and the
own-account scoping for free.

**Idempotent.** Re-submitting the same video under the same method version
updates that version's rows rather than appending duplicates, so a retry
after a network failure is safe and a worker crash loses nothing.

**An invalid response is recorded, not discarded.** A model that produced
something the schema rejected is a finding about the model; dropping it
would make the failure invisible and the accuracy figure optimistic.
"""

from __future__ import annotations

import logging
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from server.ai import vision_task
from server.api.deps import get_session
from server.api.ui import require_ui, require_ui_write
from server.db.models import (AiOperation, Creator, Video, VideoFeature,
                              VideoFeatureRun, VisionRequest,
                              WorkerHeartbeat)
from shared.schemas.vision import (VisionPendingList, VisionQueueView,
                                   VisionRequestBody, VisionRequestRow,
                                   VisionResultBody, VisionResultView)

logger = logging.getLogger("virallab.api.vision")

router = APIRouter(prefix="/api/vision", tags=["vision"])

#: v2 (2026-10-26): `sheet_music_visible` and `scene_changes` moved into the
#: task schema's `required` list. Under grammar-constrained decoding an
#: optional field is simply never produced, so v1 stored 24 videos with no
#: answer at all for sheet music. The v1 rows are kept rather than
#: overwritten - they are a correct record of what v1 asked for.
METHOD_VERSION = "hook-vision-gemma3-4b-v2"
KIND = "hook_vision"

#: Fields lifted out of the model's object into their own feature rows, so
#: they are queryable and correctable individually rather than buried in a
#: blob. The blob is stored too, under `vision_raw`.
SCALAR_FIELDS = (
    "performer_visible", "piano_or_keyboard_visible", "hands_visible",
    "face_visible", "sheet_music_visible", "text_overlay_present",
    "opening_is_representative", "camera_angle", "shot_distance",
    "framing", "lighting", "setting", "opening_type", "scene_changes",
    "confidence", "frames_described", "emotional_presentation",
    "narrative_structure",
)


def require_worker(request: Request,
                   authorization: str = Header(default="")) -> str:
    """The same per-worker credential `/jobs/*` and `/api/frames` use."""
    scheme, _, token = authorization.partition(" ")
    tokens: dict[str, str] = getattr(request.app.state, "tokens", {})
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(401, "missing bearer token")
    for known, name in tokens.items():
        if secrets.compare_digest(known, token):
            return name
    raise HTTPException(403, "unknown worker token")


def analysed_ids(session: Session, *,
                 method_version: str = METHOD_VERSION) -> set[int]:
    rows = session.execute(
        select(VideoFeature.video_id).distinct().where(
            VideoFeature.method_version == method_version,
            VideoFeature.status == "ok")).all()
    return {r[0] for r in rows}


@router.get("/pending", response_model=VisionPendingList)
def pending(limit: int = Query(20, ge=1, le=200),
            force: bool = Query(False),
            requested_only: bool = Query(False),
            session: Session = Depends(get_session),
            worker: str = Depends(require_worker)) -> VisionPendingList:
    """What to analyse next: explicit requests first, then the backlog.

    Requests a person made through the interface jump the queue, because
    someone is waiting on them. Claiming happens here rather than at result
    time so the interface can show `running` while a video is in flight;
    `requeue_stale` returns anything a dying worker left behind.
    """
    requeue_stale(session)

    queued = session.scalars(
        select(VisionRequest)
        .where(VisionRequest.status == "queued",
               VisionRequest.task == vision_task.TASK,
               VisionRequest.method_version == METHOD_VERSION)
        .order_by(VisionRequest.requested_at)
        .limit(limit)).all()

    requested_ids: list[int] = []
    forced_ids: set[int] = set()
    now = datetime.now(timezone.utc)
    for req in queued:
        req.status = "running"
        req.claimed_by = worker
        req.started_at = now
        req.attempts = (req.attempts or 0) + 1
        requested_ids.append(req.video_id)
        if req.force:
            forced_ids.add(req.video_id)
    if queued:
        session.commit()
        logger.info("worker %s claimed %d requested video(s)",
                    worker, len(queued))

    stmt = (select(Video)
            .join(Creator, Creator.id == Video.creator_id)
            .where(Video.platform == "instagram",
                   Creator.is_own_account.is_(True),
                   Video.media_status == "available",
                   Video.media_path.isnot(None))
            .order_by(Video.posted_at.desc()))
    videos = list(session.scalars(stmt).all())
    by_id = {v.id: v for v in videos}
    done = set() if force else analysed_ids(session)

    def item(v: Video) -> dict:
        return {"video_id": v.id, "external_id": v.external_id,
                "duration_s": v.duration_s, "posted_at": v.posted_at}

    # Requested videos first, in request order, then the unanalysed backlog.
    items = [item(by_id[vid]) for vid in requested_ids if vid in by_id]
    if not requested_only:
        seen = set(requested_ids)
        items += [item(v) for v in videos
                  if v.id not in done and v.id not in seen]
    items = items[:limit]
    return VisionPendingList(
        items=items, total_analysable=len(videos), already_done=len(done),
        method_version=METHOD_VERSION)


def _store_feature(session: Session, video: Video, run: VideoFeatureRun,
                   name: str, value, *, model: str, confidence: float | None,
                   note: str | None = None, params: dict | None = None,
                   unavailable_reason: str | None = None) -> None:
    row = session.scalar(select(VideoFeature).where(
        VideoFeature.video_id == video.id,
        VideoFeature.feature == name,
        VideoFeature.method_version == METHOD_VERSION))
    if row is None:
        row = VideoFeature(video_id=video.id, feature=name,
                           method_version=METHOD_VERSION)
        session.add(row)
    row.value_num = row.value_bool = row.value_text = row.value_json = None
    if isinstance(value, bool):
        row.value_bool = value
    elif isinstance(value, (int, float)):
        row.value_num = float(value)
    elif isinstance(value, str):
        row.value_text = value
    elif isinstance(value, (list, dict)):
        row.value_json = {"items": value} if isinstance(value, list) else value
    row.media_sha256 = video.media_sha256
    row.media_path = video.media_path
    row.method = "model"
    row.model = model
    row.uncertainty = confidence
    row.uncertainty_kind = "confidence" if confidence is not None else None
    row.interval_start_s = 0.0
    row.interval_end_s = vision_task.HOOK_SECONDS
    row.params = params
    row.note = note
    row.unavailable_reason = unavailable_reason
    row.extracted_at = datetime.now(timezone.utc)
    row.run_id = run.id
    row.status = "ok"
    row.error = None


@router.post("/results", response_model=VisionResultView)
def results(body: VisionResultBody,
            session: Session = Depends(get_session),
            worker: str = Depends(require_worker)) -> VisionResultView:
    """Store one video's scene description, idempotently."""
    video = session.get(Video, body.video_id)
    if video is None:
        raise HTTPException(404, "no such publication")
    creator = session.get(Creator, video.creator_id)
    if creator is None or not creator.is_own_account:
        raise HTTPException(
            403, "scene descriptions are stored for your own publications "
                 "only")

    run = session.scalars(
        select(VideoFeatureRun)
        .where(VideoFeatureRun.method_version == METHOD_VERSION,
               VideoFeatureRun.status == "running")
        .order_by(VideoFeatureRun.id.desc()).limit(1)).first()
    if run is None:
        run = VideoFeatureRun(
            method_version=METHOD_VERSION, kind=KIND, status="running",
            params={"task": vision_task.TASK, "worker": worker,
                    "frames": vision_task.HOOK_FRAMES,
                    "later_offsets_s": list(vision_task.LATER_OFFSETS_S)})
        session.add(run)
        session.flush()

    op = AiOperation(
        video_id=video.id, task=body.task or vision_task.TASK,
        provider=body.provider or "local-vlm",
        model=body.model or "unknown", hosting=body.hosting or "local",
        category="cloud_vision" if body.hosting == "cloud" else "discovery",
        mode="local_only", status=body.status,
        schema_valid=body.schema_valid,
        validation_errors=body.validation_errors or [],
        refused_reason=body.refused_reason,
        input_tokens=body.input_tokens, output_tokens=body.output_tokens,
        images=body.images, estimated_usd=0.0,
        actual_usd=0.0 if body.hosting != "cloud" else None,
        latency_s=body.latency_s, peak_vram_mib=body.peak_vram_mib)
    session.add(op)

    written = 0
    if body.status == "ok" and body.data:
        data = dict(body.data)
        confidence = data.get("confidence")
        model = body.model or "unknown"
        uncertain = set(data.get("uncertain_fields") or [])
        frame_params = {"frames": [f.model_dump() for f in body.frames],
                        "task": body.task,
                        "model_stamp": data.get("_model")}

        for name in SCALAR_FIELDS:
            if name not in data:
                continue
            value = data[name]
            note = None
            if name in ("emotional_presentation", "narrative_structure"):
                note = ("An INTERPRETATION produced by a model, not a "
                        "measurement, and about the presentation rather "
                        "than the performer.")
            if name in uncertain:
                note = ((note + " ") if note else "") + (
                    "The model listed this field as one it was unsure of.")
            _store_feature(
                session, video, run, f"vision_{name}", value,
                model=model, confidence=confidence, note=note,
                params=frame_params,
                unavailable_reason=(
                    "the model answered 'unknown' from the frames it was "
                    "given" if value == "unknown" else None))
            written += 1

        for name in ("transitions", "uncertain_fields"):
            if name in data:
                _store_feature(session, video, run, f"vision_{name}",
                               data[name], model=model, confidence=confidence,
                               params=frame_params)
                written += 1

        _store_feature(session, video, run, "vision_raw", data, model=model,
                       confidence=confidence, params=frame_params,
                       note="The complete validated object as the model "
                            "returned it, kept so a later field can be "
                            "recovered without re-running inference.")
        written += 1
        run.videos_done = (run.videos_done or 0) + 1
    else:
        run.videos_failed = (run.videos_failed or 0) + 1

    run.features_written = (run.features_written or 0) + written
    run.compute_seconds = round(
        (run.compute_seconds or 0.0) + (body.latency_s or 0.0), 2)
    session.commit()

    # Close the request this result answers, if there is one. Done here
    # rather than in the worker so that a result arriving from any source
    # clears the queue entry, and a worker that dies after POSTing but
    # before updating its own state cannot leave a request stuck.
    req = session.scalars(select(VisionRequest).where(
        VisionRequest.video_id == video.id,
        VisionRequest.task == vision_task.TASK,
        VisionRequest.method_version == METHOD_VERSION,
        VisionRequest.status.in_(("running", "queued")))
        .order_by(VisionRequest.id.desc()).limit(1)).first()
    if req is not None:
        req.finished_at = datetime.now(timezone.utc)
        if body.status == "ok":
            req.status = "done"
            req.error = None
        elif req.attempts >= MAX_ATTEMPTS:
            req.status = "failed"
            req.error = (f"{body.status} after {req.attempts} attempt(s): "
                         f"{(body.error or body.refused_reason or '')[:200]}")
        else:
            # Bounded retry: back in the queue, attempts already counted at
            # claim time, and the constraint stops it cycling forever.
            req.status = "queued"
            req.claimed_by = None
            req.started_at = None
            req.finished_at = None
            req.error = (f"attempt {req.attempts} returned {body.status}: "
                         f"{(body.error or body.refused_reason or '')[:200]}")
        session.commit()

    logger.info("vision result for video %s from %s: %s, %d feature(s)",
                video.id, worker, body.status, written)
    return VisionResultView(
        video_id=video.id, status=body.status, features_written=written,
        method_version=METHOD_VERSION, run_id=run.id, operation_id=op.id)


@router.get("/coverage")
def coverage(session: Session = Depends(get_session),
             worker: str = Depends(require_worker)) -> dict:
    analysable = session.scalar(
        select(func.count(Video.id))
        .join(Creator, Creator.id == Video.creator_id)
        .where(Video.platform == "instagram",
               Creator.is_own_account.is_(True),
               Video.media_status == "available")) or 0
    return {"analysable": analysable,
            "analysed": len(analysed_ids(session)),
            "method_version": METHOD_VERSION}


# ---------------------------------------------------------------------------
# the request queue
# ---------------------------------------------------------------------------
#
# The server has no GPU. `POST /api/ai/analyse` cannot run the local model,
# so the interface records an intent here and the Windows worker drains it.
# Everything below exists to make that wait visible rather than to pretend
# the work happened.

#: A `running` request whose worker never came back. The worker heartbeats
#: while it runs, so this is generous enough that a slow 12-frame video is
#: never requeued out from under a live worker.
STALE_RUNNING_MINUTES = 20

#: Beyond this, a repeatedly abandoned request stops rather than cycling
#: forever (CLAUDE.md 4.4).
MAX_ATTEMPTS = 3


def requeue_stale(session: Session) -> int:
    """Return abandoned `running` requests to the queue.

    A worker killed mid-video leaves a `running` row, which would otherwise
    hold the partial unique index forever and make that video impossible to
    request again.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(
        minutes=STALE_RUNNING_MINUTES)
    stale = session.scalars(select(VisionRequest).where(
        VisionRequest.status == "running",
        VisionRequest.started_at < cutoff)).all()
    for req in stale:
        who = req.claimed_by or "unknown"
        if req.attempts >= MAX_ATTEMPTS:
            req.status = "failed"
            req.finished_at = datetime.now(timezone.utc)
            req.error = (f"abandoned {req.attempts} times without a result; "
                         f"not retried again")
        else:
            req.status = "queued"
            req.claimed_by = None
            req.started_at = None
            req.error = f"worker {who} stopped reporting; requeued"
    if stale:
        session.commit()
        logger.info("requeued %d stale vision request(s)", len(stale))
    return len(stale)


def _worker_state(session: Session) -> dict:
    """Is the GPU workstation alive, and what is it doing?"""
    hb = session.scalars(
        select(WorkerHeartbeat)
        .order_by(WorkerHeartbeat.updated_at.desc()).limit(1)).first()
    if hb is None:
        return {"online": False, "detail": "no worker has ever reported in"}
    age = (datetime.now(timezone.utc) - hb.updated_at).total_seconds()
    return {"online": age < 180,
            "worker_id": hb.worker_id,
            "state": hb.state,
            "seconds_since_heartbeat": int(age),
            "free_vram_mib": hb.free_vram_mib,
            "total_vram_mib": hb.total_vram_mib,
            "detail": (f"last heard from {int(age)}s ago" if age < 180 else
                       f"silent for {int(age // 60)} minute(s) - the "
                       f"workstation is probably switched off")}


def _request_row(req: VisionRequest, video: Video | None) -> VisionRequestRow:
    return VisionRequestRow(
        id=req.id, video_id=req.video_id,
        external_id=video.external_id if video else None,
        task=req.task, method_version=req.method_version,
        status=req.status, mode=req.mode, force=req.force,
        attempts=req.attempts, claimed_by=req.claimed_by,
        requested_at=req.requested_at, started_at=req.started_at,
        finished_at=req.finished_at, error=req.error)


@router.post("/requests", response_model=VisionRequestRow,
             dependencies=[Depends(require_ui_write)])
def request_analysis(body: VisionRequestBody,
                     session: Session = Depends(get_session)
                     ) -> VisionRequestRow:
    """Queue one of your own publications for local visual analysis."""
    video = session.get(Video, body.video_id)
    if video is None:
        raise HTTPException(404, "no such publication")
    creator = session.get(Creator, video.creator_id)
    if creator is None or not creator.is_own_account:
        raise HTTPException(
            403, "visual analysis runs on your own publications only")
    if video.media_status != "available" or not video.media_path:
        raise HTTPException(
            409, f"no preserved file for this publication "
                 f"(media_status={video.media_status!r}), so there are no "
                 f"frames to analyse")

    requeue_stale(session)
    existing = session.scalars(select(VisionRequest).where(
        VisionRequest.video_id == video.id,
        VisionRequest.task == vision_task.TASK,
        VisionRequest.method_version == METHOD_VERSION,
        VisionRequest.status.in_(("queued", "running")))).first()
    if existing is not None:
        # Idempotent: a second click returns the first request rather than
        # queueing the same video twice.
        return _request_row(existing, video)

    if not body.force and video.id in analysed_ids(session):
        raise HTTPException(
            409, f"this publication already has features under "
                 f"{METHOD_VERSION}. Re-send with force=true to analyse it "
                 f"again; the new answers replace that version's rows.")

    req = VisionRequest(
        video_id=video.id, task=vision_task.TASK,
        method_version=METHOD_VERSION, status="queued",
        # Pinned local. Queueing from the interface can never become a paid
        # cloud call, whatever is configured by the time the worker runs.
        mode="local_only", force=bool(body.force), requested_by="web-ui",
        params={"frames": vision_task.HOOK_FRAMES,
                "later_offsets_s": list(vision_task.LATER_OFFSETS_S)})
    session.add(req)
    session.commit()
    logger.info("queued vision request %s for video %s", req.id, video.id)
    return _request_row(req, video)


@router.get("/requests", response_model=VisionQueueView,
            dependencies=[Depends(require_ui)])
def list_requests(limit: int = Query(50, ge=1, le=200),
                  session: Session = Depends(get_session)) -> VisionQueueView:
    requeue_stale(session)
    rows = session.scalars(
        select(VisionRequest)
        .order_by(VisionRequest.id.desc()).limit(limit)).all()
    videos = {v.id: v for v in session.scalars(select(Video).where(
        Video.id.in_([r.video_id for r in rows] or [-1]))).all()}
    counts: dict = {}
    for r in rows:
        counts[r.status] = counts.get(r.status, 0) + 1
    return VisionQueueView(
        requests=[_request_row(r, videos.get(r.video_id)) for r in rows],
        counts=counts, worker=_worker_state(session),
        method_version=METHOD_VERSION,
        analysed=len(analysed_ids(session)))


@router.post("/requests/{request_id}/cancel", response_model=VisionRequestRow,
             dependencies=[Depends(require_ui_write)])
def cancel_request(request_id: int,
                   session: Session = Depends(get_session)
                   ) -> VisionRequestRow:
    req = session.get(VisionRequest, request_id)
    if req is None:
        raise HTTPException(404, "no such request")
    if req.status not in ("queued", "running"):
        raise HTTPException(
            409, f"request is already {req.status}; nothing to cancel")
    req.status = "cancelled"
    req.finished_at = datetime.now(timezone.utc)
    session.commit()
    return _request_row(req, session.get(Video, req.video_id))


@router.post("/heartbeat")
def post_heartbeat(body: dict,
                   session: Session = Depends(get_session),
                   worker: str = Depends(require_worker)) -> dict:
    """The vision worker reporting that it is alive and what it is doing.

    Deliberately not `/jobs/heartbeat`: that one extends a batch lease and
    requires the lease token minted at dequeue, which this worker never
    takes. Lease validation there must not be weakened to accommodate a
    worker that does not lease (CLAUDE.md 4.2), so presence is reported
    separately and writes only to `worker_heartbeats`.
    """
    worker_id = str(body.get("worker_id") or worker)[:128]
    state = str(body.get("state") or "idle")[:16]
    hb = session.get(WorkerHeartbeat, worker_id)
    if hb is None:
        hb = WorkerHeartbeat(worker_id=worker_id)
        session.add(hb)
    hb.state = state
    hb.reason = (str(body.get("reason"))[:500]
                 if body.get("reason") is not None else None)
    free = body.get("free_vram_mib")
    total = body.get("total_vram_mib")
    hb.free_vram_mib = int(free) if isinstance(free, (int, float)) else None
    hb.total_vram_mib = int(total) if isinstance(total, (int, float)) else None
    hb.updated_at = datetime.now(timezone.utc)
    session.commit()
    return {"ok": True, "worker_id": worker_id, "state": state}
