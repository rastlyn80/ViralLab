"""Buttons that make the platform collect its own data.

Thin, like the other routers: the catalogue decides what may run
and the runner decides how, so neither is reachable by a caller
inventing a request.

`POST /run` takes a **key and numbers**, never a command. Anything
the catalogue does not declare is dropped before it reaches a
command line.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from server.api.deps import get_session
from server.api.ui import require_ui, require_ui_write
from server.ops import catalogue as cat
from server.ops import runner
from server.ops import schedule as sched

logger = logging.getLogger("virallab.api.ops")

router = APIRouter(prefix="/api/operations", tags=["operations"])


class Run(BaseModel):
    kind: str = Field(max_length=64)
    params: dict[str, int] = Field(default_factory=dict)


def _row(op) -> dict:
    pct = None
    if op.total:
        pct = round(100.0 * (op.done or 0) / op.total, 1)
    return {
        "id": op.id, "kind": op.kind, "status": op.status,
        # A run of an operation that no longer exists still has a
        # row. Falling back to the raw key put `collect_own` on the
        # page the moment that operation was removed, which is the
        # column-name-in-the-interface problem arriving through the
        # back door.
        "title": (cat.BY_KEY[op.kind].title if op.kind in cat.BY_KEY
                  else op.kind.replace("_", " ").capitalize()
                  + " (no longer offered)"),
        "params": op.params or {},
        "requested_at": op.requested_at,
        "started_at": op.started_at, "finished_at": op.finished_at,
        "progress_note": op.progress_note,
        "done": op.done, "total": op.total, "percent": pct,
        "cost_usd": float(op.cost_usd or 0),
        "error": op.error,
        "summary": op.summary,
        "is_live": op.status in runner.LIVE,
    }


@router.get("", dependencies=[Depends(require_ui)])
def index(session: Session = Depends(get_session)) -> dict:
    """Everything that can be run, and everything that has been."""
    running = {op.kind for op in runner.live(session)}
    return {
        "operations": [
            {**cat.as_dict(op), "is_running": op.key in running}
            for op in cat.CATALOGUE],
        "history": [_row(r) for r in runner.recent(session)],
        "automatic": sched.describe(session),
        "automatic_note": (
            "These run on their own, once a day. Only work that "
            "costs nothing is ever automatic - anything that spends "
            "stays a button you press."),
        "what_this_is": (
            "The platform collects and measures its own data. "
            "Press a button and it runs here, not on somebody's "
            "laptop."),
    }


@router.get("/{op_id}", dependencies=[Depends(require_ui)])
def one(op_id: int, session: Session = Depends(get_session)) -> dict:
    from server.db.models import Operation

    row = session.get(Operation, op_id)
    if row is None:
        raise HTTPException(404, "no such run")
    return {**_row(row), "log": row.log or ""}


@router.post("/run", dependencies=[Depends(require_ui_write)])
def run(body: Run, session: Session = Depends(get_session)) -> dict:
    try:
        row = runner.start(session, body.kind, body.params)
    except KeyError:
        raise HTTPException(404, "no such operation") from None
    except runner.AlreadyRunning as exc:
        raise HTTPException(409, str(exc)) from None
    return _row(row)


@router.post("/{op_id}/stop", dependencies=[Depends(require_ui_write)])
def stop(op_id: int, session: Session = Depends(get_session)) -> dict:
    stopped = runner.cancel(session, op_id)
    if not stopped:
        raise HTTPException(409, "that run had already finished")
    return {"stopped": True}
