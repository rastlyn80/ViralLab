"""Ask ViralLab: the chat, under the strategy cap.

Thin on purpose. Everything that decides whether an answer is
allowed lives in `server/ai/strategist.py`, so the rules are testable
without an HTTP client and cannot be bypassed by a second caller
appearing later.

The endpoint's own jobs are three: hold the transaction so a
reservation and a logged turn land together, keep the owner's money
behind the `strategy` category cap, and store the evidence context
beside the answer so a wrong figure can be traced to either the tool
or the model.
"""

from __future__ import annotations

import logging
import time
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from server import budget as bud
from server.ai import strategist as st
from server.api.deps import get_session, session_factory
from server.api.ui import require_ui, require_ui_write
from server.db.models import StrategistTurn

logger = logging.getLogger("virallab.api.strategist")

router = APIRouter(prefix="/api/strategist", tags=["strategist"])


class Question(BaseModel):
    question: str = Field(min_length=2, max_length=2000)
    conversation_id: str | None = Field(default=None, max_length=64)
    model: str = Field(default="haiku", pattern="^(haiku|sonnet)$")


def _provider(name: str):
    from server.ai.providers import (AnthropicSonnetVisionProvider,
                                     AnthropicVisionProvider)
    return (AnthropicSonnetVisionProvider() if name == "sonnet"
            else AnthropicVisionProvider())


@router.get("/status", dependencies=[Depends(require_ui)])
def status(session: Session = Depends(get_session)) -> dict:
    """Whether the chat can run, and what it has cost.

    Answered before the owner types anything, because "the cap is
    zero" is a configuration fact and finding it out by being refused
    mid-question is a poor way to learn it.
    """
    from server import config
    from server.budget import budget_state

    sp = config.settings().spending
    cap = sp.cap_for("strategy") if hasattr(sp, "cap_for") else 0.0
    provider = _provider("haiku")
    ok, why = provider.available()
    state = budget_state(session, provider=provider.name)
    turns = session.scalar(select(func.count(StrategistTurn.id))) or 0
    return {
        "available": bool(ok and cap > 0),
        "why_not": (None if ok and cap > 0 else
                    (why if not ok else
                     "The `strategy` spending cap is $0.00. The chat is "
                     "built and will not run until you raise it.")),
        "strategy_cap_usd": cap,
        "committed_usd": state.get("committed_usd"),
        "turns_logged": turns,
        "model": provider.MODEL,
        "what_it_cannot_do": [
            "Say that anything causes anything. Nothing in this "
            "project holds the grade that would license it.",
            "Predict or promise reach, in any phrasing.",
            "Answer from a measurement that was tested and withheld.",
            "Identify anyone in a video.",
            "See the database. It can only call five read-only tools, "
            "and every number it states is checked against what they "
            "returned.",
        ],
    }


@router.post("/ask", dependencies=[Depends(require_ui_write)])
def ask(body: Question, session: Session = Depends(get_session)) -> dict:
    provider = _provider(body.model)
    ok, why = provider.available()
    if not ok:
        raise HTTPException(503, why or "no provider configured")

    conversation_id = body.conversation_id or uuid.uuid4().hex[:16]
    started = time.monotonic()

    # A refusal decided before any model is called is logged with
    # `refused_before_spending`, which is how the acceptance test can
    # show the trap questions cost nothing.
    pre = st.precheck(body.question) or st.withheld_question(body.question)
    if pre:
        turn = StrategistTurn(
            conversation_id=conversation_id, question=body.question,
            answer=pre, tool_calls=[], violations=[],
            unverified_numbers=[], blocked=True,
            refused_before_spending=True, provider=provider.name,
            model=provider.MODEL, billed_usd=0.0,
            latency_s=round(time.monotonic() - started, 3))
        session.add(turn)
        session.commit()
        return {"conversation_id": conversation_id, "text": pre,
                "blocked": True, "refused_before_spending": True,
                "tool_calls": [], "violations": [],
                "unverified_numbers": [], "billed_usd": 0.0}

    spent = {"usd": 0.0}

    def reserve(estimate: float, key: str):
        """Reserve against the strategy cap. None stops the loop."""
        Factory = session_factory()
        with Factory() as s2:
            try:
                r = bud.reserve(s2, provider=provider.name,
                                estimated_usd=estimate,
                                idempotency_key=key, category="strategy",
                                detail={"surface": "strategist"})
                s2.commit()
                rid = r.id
            except Exception as exc:          # noqa: BLE001 - reported
                logger.warning("strategy budget refused: %s", exc)
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

    answer = st.ask(session, body.question, provider=provider,
                    reserve=reserve)
    d = answer.as_dict()

    turn = StrategistTurn(
        conversation_id=conversation_id, question=body.question,
        answer=d["text"], tool_calls=d["tool_calls"],
        violations=d["violations"],
        unverified_numbers=d["unverified_numbers"],
        blocked=d["blocked"], refused_before_spending=False,
        provider=provider.name, model=provider.MODEL,
        billed_usd=round(spent["usd"], 6),
        latency_s=round(time.monotonic() - started, 3))
    session.add(turn)
    session.commit()

    return {"conversation_id": conversation_id,
            "refused_before_spending": False,
            "billed_usd": round(spent["usd"], 6), **d}


@router.get("/history", dependencies=[Depends(require_ui)])
def history(limit: int = 20,
            session: Session = Depends(get_session)) -> dict:
    """Past turns, with what was caught.

    The enforcement counters are the point. A layer nobody measures
    is one nobody knows has stopped working.
    """
    rows = list(session.scalars(
        select(StrategistTurn)
        .order_by(desc(StrategistTurn.created_at))
        .limit(min(limit, 100))).all())
    return {
        "turns": [{
            "id": r.id, "conversation_id": r.conversation_id,
            "question": r.question, "answer": r.answer,
            "blocked": r.blocked,
            "refused_before_spending": r.refused_before_spending,
            "violations": r.violations or [],
            "unverified_numbers": r.unverified_numbers or [],
            "tools_used": [t.get("name") for t in (r.tool_calls or [])],
            "billed_usd": r.billed_usd,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        } for r in rows],
        "enforcement": {
            "turns": len(rows),
            "blocked": sum(1 for r in rows if r.blocked),
            "refused_before_spending": sum(
                1 for r in rows if r.refused_before_spending),
            "with_unverified_numbers": sum(
                1 for r in rows if r.unverified_numbers),
        },
    }
