"""M7: what to try next, and what it would cost to find out.

Read-only by design. An opportunity is a proposal and a plan, never
an instruction, and nothing here writes anything - the one-click
route into a pre-registered experiment is M9's job and it will need
its own write path, its own confirmation and its own stopping rule.

The ordering is the opinionated part and it is deliberate. Opportunities
come back ranked by how quickly they could be *answered*, not by how
strong the evidence behind them is. Ranking by evidence alone would put
a two-year commitment above a four-month one because its q-value is
smaller, which is the wrong answer to "what should I do next".
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from server.api.deps import get_session
from server.api.ui import require_ui
from server.research import opportunities as opp

logger = logging.getLogger("virallab.api.opportunities")

router = APIRouter(prefix="/api/opportunities", tags=["opportunities"])


@router.get("", dependencies=[Depends(require_ui)])
def list_opportunities(
    collection_id: int | None = Query(
        None, description="Include cohort-derived opportunities from this "
                          "saved collection. Those are capped at Observed."),
    population: str | None = Query(None),
    session: Session = Depends(get_session),
) -> dict:
    items = opp.build(session, population=population,
                      collection_id=collection_id)
    return {
        "summary": opp.summary(items),
        "opportunities": [o.as_dict() for o in items],
        "how_to_read": {
            "grade": ("An opportunity is never graded above the evidence "
                      "under it. Observed means a difference was seen and "
                      "nothing was measured about whether it helps; "
                      "Associated means a relationship survived "
                      "correction and the ad covariate. Nothing here is "
                      "Validated, because only a pre-registered "
                      "experiment can produce that."),
            "feasibility": ("How long the experiment would take at your "
                            "measured posting rate. This is the number "
                            "that decides what is worth starting."),
            "counterexamples": ("Publications that do not fit the "
                                "pattern. They are listed because a "
                                "moderate correlation means most "
                                "publications do not fit it, and showing "
                                "only the supporting ones would "
                                "misrepresent the evidence by omission."),
            "causation": ("None of these is a prediction, and nothing "
                          "here has been shown to cause anything."),
        },
    }
