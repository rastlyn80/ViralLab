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

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from server import budget as bud
from server.ai import creative as cr
from server.api.deps import get_session, session_factory
from server.api.ui import require_ui, require_ui_write

logger = logging.getLogger("virallab.api.creative")

router = APIRouter(prefix="/api/creative", tags=["creative"])


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
