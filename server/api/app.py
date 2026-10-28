"""FastAPI application — the only network surface between the two machines.

Security posture (DEVELOPMENT_PLAN.md 4.2, ADR 0004):
  * binds to the LAN/tailnet interface only, never the public internet
  * every worker call carries a per-worker bearer token
  * media is served through HMAC-signed, short-lived, single-video URLs
  * a stale lease token is rejected with 409, so a reaped zombie worker
    cannot overwrite fresher results

The worker always initiates. If the Windows machine is off, nothing here
blocks or times out waiting for it — batches simply accumulate as rows.
"""

from __future__ import annotations

import asyncio
import logging
import os
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select
from sqlalchemy.orm import Session

from server.api import (ai_api, credentials_api, deps, discovery_api,
                        experiments_api,
                        features_api, frames_api, home_api, hooks_api,
                        labels_api,
                        opportunities_api, research_api,
                        strategist_api, ui, vision_api)
from server.api.deps import get_session
from server.db.models import Video
from server.queue.queue import (
    LeaseError, fail_batch, heartbeat, lease_batch, queue_depth, reap_expired,
    record_worker_status, submit_results,
)
from shared.schemas.jobs import (
    BatchLease, Heartbeat, LeaseRequest, Modality, ResultSubmission,
    WorkerStatus,
)
from shared.utils.signing import sign_media_url, verify_media_signature

DB_URL = os.environ.get("VIRALLAB_DB_URL", "")
MEDIA_SECRET = os.environ.get("VIRALLAB_MEDIA_SECRET", "")
#: "name:token,name:token" — one token per worker so a single worker's
#: credential can be revoked without disturbing the others.
WORKER_TOKENS = os.environ.get("VIRALLAB_WORKER_TOKENS", "")
PUBLIC_BASE_URL = os.environ.get("VIRALLAB_PUBLIC_BASE_URL",
                                 "https://127.0.0.1:8443")
MEDIA_ROOT = Path(os.environ.get("VIRALLAB_MEDIA_ROOT", "./media")).resolve()
MEDIA_TTL = int(os.environ.get("VIRALLAB_MEDIA_TTL", "900"))
REAPER_INTERVAL = int(os.environ.get(
    "VIRALLAB_REAPER_INTERVAL", "60"))
#: Daily own-account collection, in-process beside the reaper.
COLLECTION_ENABLED = os.environ.get(
    "VIRALLAB_COLLECTION_ENABLED", "1") not in ("0", "false", "")
COLLECTION_INTERVAL = int(os.environ.get(
    "VIRALLAB_COLLECTION_INTERVAL", "3600"))

logger = logging.getLogger("virallab.api")


def _configure_logging() -> None:
    """Attach our own handler to the `virallab` logger tree.

    uvicorn configures only its own loggers, so everything logged here -
    including the reaper and the daily collector - went nowhere. The loop was
    verifiably running but invisible in logs/api.log, which is the worst
    combination: working, and unobservable.
    """
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)-8s %(name)s %(message)s"))
    level = os.environ.get("VIRALLAB_LOG_LEVEL", "INFO").upper()
    # BOTH trees. Configuring only "virallab" fixed the reaper and left the
    # collectors silent, because they use `getLogger(__name__)` and so live
    # under "server.*". That is how a failing media-preservation pass
    # produced no log line at all: the exception handler logged, and the
    # record went nowhere.
    for name in ("virallab", "server"):
        root = logging.getLogger(name)
        if root.handlers:
            continue
        root.addHandler(handler)
        root.setLevel(level)
        # Already captured in logs/api.log by the unit's stdout append.
        root.propagate = False



def _parse_tokens(raw: str) -> dict[str, str]:
    out = {}
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        name, _, token = part.partition(":")
        if name and token:
            out[token] = name
    return out


async def _reaper_loop(interval: int) -> None:
    """Requeue dead leases, and enforce the competitor-media deadline.

    Without this running, a crashed worker leaves its batch 'leased' forever
    and the work silently stops — the reaper function alone is not a recovery
    guarantee unless something calls it. Runs in-process (plan 4.1: no second
    service); Phase 2 may move it onto APScheduler alongside the collection
    schedules.

    The transient-media sweep rides the same loop for exactly that reason.
    `server/collectors/transient.py` enforces a six-hour ceiling on
    competitor bytes, and it was written and tested before anything called
    it — a retention policy nothing executes is a policy nobody has. It is
    deliberately in its own try block: a failure to sweep must not stop
    leases being reaped, and a failure to reap must not stop the sweep,
    because the two protect different things and one is a legal
    commitment.
    """
    while True:
        await asyncio.sleep(interval)
        try:
            with deps.session_factory()() as session:
                reaped = reap_expired(session)
                session.commit()
            if reaped:
                logger.warning("reaped %d expired lease(s): %s",
                               len(reaped), reaped)
        except asyncio.CancelledError:
            raise
        except Exception:                         # never kill the loop
            logger.exception("reaper pass failed; continuing")

        try:
            from server.collectors import transient
            with deps.session_factory()() as session:
                swept = transient.sweep(session, media_root=MEDIA_ROOT)
                session.commit()
            if swept["expired"] or swept["orphans_deleted"]:
                logger.warning("transient media swept: %s", swept)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("transient sweep failed; continuing")


@asynccontextmanager
async def lifespan(app: FastAPI):
    if not DB_URL:
        raise RuntimeError("VIRALLAB_DB_URL is not set")
    if not MEDIA_SECRET:
        raise RuntimeError("VIRALLAB_MEDIA_SECRET is not set")
    _configure_logging()
    deps.configure(DB_URL)
    app.state.tokens = _parse_tokens(WORKER_TOKENS)
    if not ui.auth_required():
        logger.warning(
            "VIRALLAB_UI_PASSWORD is not set: the web UI is readable by "
            "anything that can reach this tailnet address. Set it to "
            "require a password.")

    tasks = [asyncio.create_task(_reaper_loop(REAPER_INTERVAL))]
    logger.info("reaper started (every %ss)", REAPER_INTERVAL)
    if COLLECTION_ENABLED:
        from server.scheduler.collect import daily_collection_loop
        tasks.append(asyncio.create_task(
            daily_collection_loop(
                deps.session_factory(),
                check_interval_seconds=COLLECTION_INTERVAL)))
        logger.info("daily collection started (checks every %ss)",
                    COLLECTION_INTERVAL)
    try:
        yield
    finally:
        for t in tasks:
            t.cancel()
        for t in tasks:
            try:
                await t
            except asyncio.CancelledError:
                pass
        deps.dispose()


app = FastAPI(title="ViralLab", version="0.1.0", lifespan=lifespan)




def require_worker(request: Request,
                   authorization: str = Header(default="")) -> str:
    """Per-worker bearer token. Returns the worker's configured name."""
    scheme, _, token = authorization.partition(" ")
    tokens: dict[str, str] = getattr(request.app.state, "tokens", {})
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(401, "missing bearer token")
    # Constant-time comparison against each known token.
    for known, name in tokens.items():
        if secrets.compare_digest(known, token):
            return name
    raise HTTPException(403, "unknown worker token")


@app.exception_handler(LeaseError)
async def _lease_error(_: Request, exc: LeaseError):
    # 409 Conflict: the caller's lease is no longer current. This is the
    # zombie-worker guard, and it must be distinguishable from a 500.
    return JSONResponse(status_code=409, content={"detail": str(exc)})


app.include_router(ui.router)
app.include_router(research_api.router)
app.include_router(discovery_api.router)
app.include_router(hooks_api.router)
app.include_router(ai_api.router)
app.include_router(frames_api.router)
app.include_router(vision_api.router)
app.include_router(credentials_api.router)
app.include_router(features_api.router)
app.include_router(home_api.router)
app.include_router(opportunities_api.router)
app.include_router(labels_api.router)
app.include_router(strategist_api.router)
app.include_router(experiments_api.router)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# job queue
# ---------------------------------------------------------------------------

@app.post("/jobs/lease", response_model=BatchLease | None)
def lease(req: LeaseRequest, session: Session = Depends(get_session),
          worker: str = Depends(require_worker)):
    """Worker asks for work it can actually run right now.

    The worker filters by its own free VRAM before calling (ADR 0004), so the
    server never has to model the client's memory state.
    """
    def build_url(video: Video | None) -> str:
        if video is None or not video.media_path:
            return ""
        return sign_media_url(PUBLIC_BASE_URL, video.id, MEDIA_SECRET,
                              ttl_seconds=MEDIA_TTL)

    result = lease_batch(session, worker_id=req.worker_id,
                         capabilities=[Modality(c) for c in req.capabilities],
                         max_videos=req.max_videos,
                         media_url_builder=build_url)
    session.commit()
    return result


@app.post("/jobs/heartbeat")
def post_heartbeat(hb: Heartbeat, session: Session = Depends(get_session),
                   worker: str = Depends(require_worker)):
    expires = heartbeat(session, hb)
    session.commit()
    return {"lease_expires_at": expires}


@app.post("/jobs/results")
def post_results(sub: ResultSubmission,
                 session: Session = Depends(get_session),
                 worker: str = Depends(require_worker)):
    try:
        out = submit_results(session, sub)
    except ValueError as exc:            # malformed idempotency key
        session.rollback()
        raise HTTPException(422, str(exc)) from exc
    session.commit()
    return out


@app.post("/jobs/fail")
def post_fail(batch_id: int, lease_token: str, error: str,
              session: Session = Depends(get_session),
              worker: str = Depends(require_worker)):
    status = fail_batch(session, batch_id, lease_token, error)
    session.commit()
    return {"batch_status": status}


@app.post("/worker/status")
def post_worker_status(status: WorkerStatus,
                       session: Session = Depends(get_session),
                       worker: str = Depends(require_worker)):
    """Why a worker is not taking work, so a deferred GPU reads as
    'deferred: insufficient VRAM' rather than an unexplained stall."""
    record_worker_status(session, status)
    session.commit()
    return {"recorded": True}


@app.get("/status/collection", dependencies=[Depends(ui.require_ui)])
def get_collection_status(session: Session = Depends(get_session)):
    """Recent collection runs and credential health.

    A stall shows up as an old `last_ok`, and a dead credential as
    needs_reauth - neither is silent.
    """
    from server.db.models import CollectionRun, OAuthToken
    runs = session.execute(
        select(CollectionRun).order_by(CollectionRun.started_at.desc()).limit(10)
    ).scalars().all()
    creds = session.execute(select(OAuthToken)).scalars().all()
    return {
        "runs": [{"platform": r.platform, "kind": r.kind, "status": r.status,
                  "videos_seen": r.videos_seen, "videos_new": r.videos_new,
                  "snapshots": r.snapshots_written,
                  "started_at": r.started_at, "finished_at": r.finished_at,
                  "error": r.error} for r in runs],
        # Never the tokens themselves - only their health.
        "credentials": [{"platform": c.platform, "needs_reauth": c.needs_reauth,
                         "expires_at": c.expires_at,
                         "last_refreshed_at": c.last_refreshed_at}
                        for c in creds],
    }


@app.get("/status/queue", dependencies=[Depends(ui.require_ui)])
def get_queue_depth(session: Session = Depends(get_session)):
    """Queue visibility: a GPU backlog must be observable, not silent."""
    return {"depth": queue_depth(session)}


@app.post("/admin/reap")
def admin_reap(session: Session = Depends(get_session),
               worker: str = Depends(require_worker)):
    """Requeue batches whose lease expired without a heartbeat.

    The background reaper calls reap_expired() directly; this endpoint exists
    so the behaviour is testable from outside. It mutates lease state, so it
    requires a token like every other mutating route.
    """
    reaped = reap_expired(session)
    session.commit()
    return {"reaped": reaped}


# ---------------------------------------------------------------------------
# media
# ---------------------------------------------------------------------------

WEBUI_DIR = Path(__file__).resolve().parent.parent.parent / "webui"


@app.get("/media/{video_id}")
def get_media(video_id: int, expires: int = Query(...), sig: str = Query(...),
              session: Session = Depends(get_session)):
    """Serve one video to a holder of a valid, unexpired signature.

    Deliberately NOT behind the worker bearer token: the signature is the
    capability, scoped to this one video and this one time window.
    """
    try:
        verify_media_signature(video_id, expires, sig, MEDIA_SECRET)
    except ValueError as exc:
        raise HTTPException(403, str(exc)) from exc

    video = session.get(Video, video_id)
    if video is None or not video.media_path:
        raise HTTPException(404, "no media for this video")

    path = Path(video.media_path)
    if not path.is_absolute():
        path = (MEDIA_ROOT / path).resolve()
    # Containment check: a crafted media_path must not escape MEDIA_ROOT.
    if not str(path).startswith(str(MEDIA_ROOT)):
        raise HTTPException(403, "media path outside media root")
    if not path.is_file():
        raise HTTPException(404, "media file missing")
    return FileResponse(path, media_type="video/mp4")


# ---------------------------------------------------------------------------
# the web UI
#
# Mounted LAST and at the root, so every API route above is matched first and
# the SPA only handles what is left. `html=True` serves index.html for
# unknown paths, which is what lets the client-side router own the URL bar.
# ---------------------------------------------------------------------------

if WEBUI_DIR.is_dir():
    app.mount("/", StaticFiles(directory=str(WEBUI_DIR), html=True),
              name="webui")
else:                                                    # pragma: no cover
    logger.warning("webui/ not found at %s; the API is running without a "
                   "user interface", WEBUI_DIR)
