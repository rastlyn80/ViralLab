"""Generate ideas, hooks and captions from this account's evidence.

Thin, like the strategist's endpoint and for the same reason: every
rule about what a suggestion may say lives in `ai/creative.py`, so
it is testable without an HTTP client and cannot be sidestepped by a
second caller.

`GET /brief` returns what the generator would be given, without
calling a model and without spending anything. It exists because the
owner should be able to see the evidence a suggestion rests on
before paying for one, and because a brief that looks thin explains
a weak suggestion better than the suggestion does.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from server import budget as bud
from server.ai import creative as cr
from server.api.deps import get_session, session_factory
from server.api.ui import require_ui, require_ui_write

logger = logging.getLogger("virallab.api.creative")

router = APIRouter(prefix="/api/creative", tags=["creative"])

#: The largest draft that will be accepted. A vertical reel
#: is tens of megabytes; this is generous and still bounded,
#: because an unbounded upload is a way to fill a disk.
MAX_DRAFT_BYTES = 400 * 1024 * 1024


class Ask(BaseModel):
    kind: str = Field(pattern="^(ideas|hooks|captions)$")
    count: int = Field(default=6, ge=1, le=12)
    note: str | None = Field(default=None, max_length=400)
    model: str = Field(default="haiku", pattern="^(haiku|sonnet)$")


def _provider(name: str):
    from server.ai.providers import (AnthropicSonnetVisionProvider,
                                     AnthropicVisionProvider)
    return (AnthropicSonnetVisionProvider() if name == "sonnet"
            else AnthropicVisionProvider())


@router.get("/brief", dependencies=[Depends(require_ui)])
def brief(session: Session = Depends(get_session)) -> dict:
    """The evidence a suggestion would rest on. Costs nothing."""
    b = cr.build_brief(session)
    return {
        **b.as_dict(),
        "enough_to_generate": not b.is_thin(),
        "what_this_is": (
            "Everything the generator is allowed to know. It writes "
            "against this and nothing else - not against a general "
            "sense of what does well on social media."),
        "what_it_will_not_do": [
            "Promise or imply reach, in any phrasing.",
            "Say that one thing causes another.",
            "Quote a statistic, study or platform behaviour that is "
            "not in the brief.",
            "Repeat something posted recently.",
        ],
    }


@router.get("/openings", dependencies=[Depends(require_ui)])
def openings(whose: str = "all", device: str = "", platform: str = "",
             limit: int = 60,
             session: Session = Depends(get_session)) -> dict:
    """Openings that worked, to browse rather than to generate from.

    BLUEPRINT M10's "hook library". `creative/brief` returns the
    best fourteen for a model; this returns a readable set for a
    person, which is a different job - he wants twenty questions
    side by side to decide whether he likes any of them.
    """
    from server.ai import exemplars as ex
    from server.research import memo

    whose = whose if whose in ("all", "mine", "theirs") else "all"
    limit = max(10, min(200, limit))
    return memo.remember(
        "creative.openings",
        lambda: ex.library(session, whose=whose, device=device,
                           platform=platform, limit=limit),
        session=session, whose=whose, device=device,
        platform=platform, limit=limit)


@router.post("/generate", dependencies=[Depends(require_ui_write)])
def generate(body: Ask, session: Session = Depends(get_session)) -> dict:
    provider = _provider(body.model)
    ok, why = provider.available()
    if not ok:
        raise HTTPException(503, why or "no provider configured")

    spent = {"usd": 0.0}

    def reserve(estimate: float, key: str):
        Factory = session_factory()
        with Factory() as s2:
            try:
                r = bud.reserve(s2, provider=provider.name,
                                estimated_usd=estimate,
                                idempotency_key=key, category="strategy",
                                detail={"surface": "creative",
                                        "kind": body.kind})
                s2.commit()
                rid = r.id
            except Exception as exc:          # noqa: BLE001 - reported
                logger.warning("creative budget refused: %s", exc)
                return None

        def settle(outcome: str, actual: float) -> None:
            with Factory() as s3:
                if outcome == "ok":
                    bud.settle(s3, rid, actual_usd=actual)
                    spent["usd"] += actual
                else:
                    bud.release(s3, rid, reason=outcome)
                s3.commit()
        return settle

    out = cr.generate(session, kind=body.kind, provider=provider,
                      reserve=reserve, count=body.count,
                      note=body.note or "")
    out["billed_usd"] = round(spent["usd"], 6)
    return out


@router.post("/check", dependencies=[Depends(require_ui_write)])
async def check_draft(request: Request, caption: str = "",
                      session: Session = Depends(get_session)) -> dict:
    """Measure a video that has not been posted yet.

    The file arrives as the raw request body rather than as a
    multipart upload, which would need `python-multipart`.
    CLAUDE.md §6 asks for a reason beyond convenience before adding
    a package, and one endpoint that accepts one file is not one:
    the browser sends the `File` object as the body and the caption
    rides in the query string.

    Streamed to disk in chunks rather than read into memory, because
    a three-minute performance at 1080p is not a thing to hold in
    RAM on a 7 GB box that is also running Postgres.

    The draft is never given a `videos` row. It has no views, the
    analysis ranks videos within their creator's view distribution,
    and one unposted draft would sit at the bottom of every ranking
    and quietly drag the findings it is being judged against. The
    file is deleted in a `finally`.
    """
    import tempfile
    from pathlib import Path

    from server.ai import prepublish

    tmp_path: Path | None = None
    written = 0
    try:
        with tempfile.NamedTemporaryFile(delete=False,
                                         suffix=".mp4") as fh:
            async for chunk in request.stream():
                if not chunk:
                    continue
                written += len(chunk)
                if written > MAX_DRAFT_BYTES:
                    raise HTTPException(
                        413, f"that file is larger than "
                             f"{MAX_DRAFT_BYTES // (1024 * 1024)} MB")
                fh.write(chunk)
            tmp_path = Path(fh.name)
        if not written:
            tmp_path.unlink(missing_ok=True)
            tmp_path = None
        if tmp_path is None and not caption.strip():
            raise HTTPException(
                400, "give a video, a caption, or both - there is "
                     "nothing to measure otherwise")
        draft = prepublish.check(session, media_path=tmp_path,
                                 caption=caption)
        return draft.as_dict()
    finally:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)


class Verdict(BaseModel):
    status: str = Field(pattern="^(new|kept|discarded|used)$")
    note: str | None = Field(default=None, max_length=1000)
    used_video_id: int | None = None


@router.get("/saved", dependencies=[Depends(require_ui)])
def saved(kind: str = "", status: str = "", limit: int = 100,
          session: Session = Depends(get_session)) -> dict:
    """Everything the generator has produced, kept.

    The owner's objection: every call cost money and every result
    lived until the page was refreshed, so he was paying twice for
    the same thinking and building nothing up.
    """
    from sqlalchemy import desc, func, select

    from server.db.models import Suggestion

    stmt = select(Suggestion).order_by(desc(Suggestion.created_at))
    if kind:
        stmt = stmt.where(Suggestion.kind == kind)
    if status:
        stmt = stmt.where(Suggestion.status == status)
    rows = list(session.scalars(stmt.limit(max(1, min(400, limit)))).all())

    counts = dict(session.execute(
        select(Suggestion.status, func.count(Suggestion.id))
        .group_by(Suggestion.status)).all())
    spent = session.scalar(
        select(func.coalesce(func.sum(Suggestion.cost_usd), 0))) or 0

    return {
        "items": [{
            "id": r.id, "kind": r.kind, "text": r.text,
            # Translated on the way out rather than on the way in,
            # so the rows saved before the wording changed are
            # readable too.
            "grounded_in": cr.in_plain_words(r.grounded_in),
            "body": r.body,
            "batch": r.batch, "status": r.status, "note": r.note,
            "created_at": r.created_at,
            "used_video_id": r.used_video_id,
            "cost_usd": float(r.cost_usd or 0),
        } for r in rows],
        "counts": counts,
        "total_spent_usd": round(float(spent), 4),
        "what_this_is": (
            "Everything the generator has written, kept. Mark the "
            "ones worth trying; when you post one, say which "
            "publication it became and the platform can start "
            "telling you whether its own advice worked."),
    }


@router.post("/saved/{suggestion_id}",
             dependencies=[Depends(require_ui_write)])
def judge(suggestion_id: int, body: Verdict,
          session: Session = Depends(get_session)) -> dict:
    """Keep it, bin it, or say which publication it became."""
    from datetime import datetime, timezone

    from server.db.models import Suggestion

    row = session.get(Suggestion, suggestion_id)
    if row is None:
        raise HTTPException(404, "no such suggestion")
    row.status = body.status
    if body.note is not None:
        row.note = body.note or None
    if body.used_video_id is not None:
        row.used_video_id = body.used_video_id
        row.used_at = datetime.now(timezone.utc)
        row.status = "used"
    session.commit()
    return {"id": row.id, "status": row.status,
            "used_video_id": row.used_video_id}
