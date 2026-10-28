"""M9: pre-register, post, read out once.

Four endpoints and one of them refuses most of the time, which is
the point. `read_out` will not compute anything before the
pre-registered sample is complete and mature, so the stopping rule
is a property of the system rather than of the owner's willpower at
the moment the numbers look interesting.

`from-opportunity` is the one-click M7 promised. The sample size
comes from the opportunity's own power calculation rather than from
a box the owner types into, because a number typed in the moment is
a number chosen after seeing how long it would take.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from server.api.deps import get_session
from server.api.ui import require_ui, require_ui_write
from server.db.models import Experiment, ExperimentAssignment, Video
from server.research import experiment as ex

logger = logging.getLogger("virallab.api.experiments")

router = APIRouter(prefix="/api/experiments", tags=["experiments"])


class FromOpportunity(BaseModel):
    opportunity_key: str = Field(max_length=200)
    collection_id: int | None = None
    #: The owner may shorten a run, and is told what it costs. They
    #: may not lengthen it after seeing a result, because `lock`
    #: refuses a second lock.
    n_per_arm: int | None = Field(default=None, ge=2, le=200)


class Fill(BaseModel):
    slot: int = Field(ge=1)
    video_id: int
    note: str | None = Field(default=None, max_length=1000)


class Abandon(BaseModel):
    reason: str = Field(min_length=5, max_length=1000)


def _view(session: Session, e: Experiment) -> dict:
    rows = list(session.scalars(
        select(ExperimentAssignment)
        .where(ExperimentAssignment.experiment_id == e.id)
        .order_by(ExperimentAssignment.slot)).all())
    return {
        "id": e.id, "title": e.title, "status": e.status,
        "feature": e.feature, "outcome": e.outcome,
        "platform": e.platform,
        "predicted_direction": e.predicted_direction,
        "prediction": e.prediction,
        "n_per_arm": e.n_per_arm,
        "anchor_age_days": e.anchor_age_days,
        "ad_policy": e.ad_policy, "analysis": e.analysis,
        "alpha": e.alpha, "stopping_rule": e.stopping_rule,
        "opportunity_key": e.opportunity_key,
        "locked_at": e.locked_at.isoformat() if e.locked_at else None,
        "completed_at": (e.completed_at.isoformat()
                         if e.completed_at else None),
        "verdict": e.verdict, "result": e.result,
        "abandoned_reason": e.abandoned_reason,
        "schedule": [{"slot": r.slot, "arm": r.arm,
                      "video_id": r.video_id, "note": r.note}
                     for r in rows],
        "progress": ex.progress(session, e),
    }


@router.get("", dependencies=[Depends(require_ui)])
def list_experiments(session: Session = Depends(get_session)) -> dict:
    rows = list(session.scalars(
        select(Experiment).order_by(Experiment.id.desc())).all())
    return {
        "experiments": [_view(session, e) for e in rows],
        "how_to_read": {
            "why_it_refuses": (
                "The result cannot be computed before the "
                "pre-registered number of publications is posted and "
                "has reached its anchor age. That refusal is the "
                "stopping rule, and it is the difference between an "
                "experiment and a comparison made afterwards."),
            "what_a_result_licenses": (
                "A supported result is the only thing in this system "
                "that licenses saying one thing caused another - and "
                "only for this account, this platform and this "
                "period."),
            "a_null_is_a_result": (
                "An experiment whose prediction fails is recorded as "
                "failed, and the opportunity that proposed it is "
                "retired. That is the system working."),
        },
    }


@router.post("/from-opportunity", dependencies=[Depends(require_ui_write)])
def from_opportunity(body: FromOpportunity,
                     session: Session = Depends(get_session)) -> dict:
    """Turn an M7 opportunity into a locked, pre-registered run."""
    from server.research import opportunities as opp

    items = opp.build(session, collection_id=body.collection_id)
    match = next((o for o in items if o.key == body.opportunity_key), None)
    if match is None:
        raise HTTPException(
            404, f"no opportunity {body.opportunity_key!r}. They are "
                 f"recomputed from the evidence, so a key from an old "
                 f"page may no longer exist.")
    if not match.outcome:
        raise HTTPException(
            400, "this opportunity has no measured outcome - it comes "
                 "from a cohort difference, where nothing was measured "
                 "on the other creators. There is nothing to predict "
                 "against yet.")

    e = match.experiment
    # The contrast design is the one worth running: it is what makes
    # these answerable inside a year at all.
    n = (body.n_per_arm
         or (e.contrast_publications_needed
             and max(2, e.contrast_publications_needed // 2))
         or (e.publications_needed and max(2, e.publications_needed // 2))
         or 12)
    # The opportunity's `direction` lives on its experiment plan and
    # says which way to move the FEATURE. The pre-registered
    # prediction is about the OUTCOME, which is a different thing:
    # the opportunity says "more visual detail", the prediction says
    # "a lower skip rate". Conflating them would register the
    # opposite of the finding and no test downstream would notice,
    # because both are valid strings.
    from server.features import lexicon as lex
    better = lex.better_when(match.outcome)
    direction = "lower" if better == "lower" else "higher"

    exp = ex.design(
        title=match.approach[:200], feature=match.feature,
        outcome=match.outcome, platform=match.platform or "instagram",
        predicted_direction=direction, n_per_arm=int(n),
        opportunity_key=match.key)
    session.add(exp)
    session.flush()
    ex.lock(session, exp)
    session.commit()
    return {"created": True, **_view(session, exp)}


@router.get("/{experiment_id}", dependencies=[Depends(require_ui)])
def get_experiment(experiment_id: int,
                   session: Session = Depends(get_session)) -> dict:
    e = session.get(Experiment, experiment_id)
    if e is None:
        raise HTTPException(404, "no such experiment")
    return _view(session, e)


@router.put("/{experiment_id}/fill", dependencies=[Depends(require_ui_write)])
def fill_slot(experiment_id: int, body: Fill,
              session: Session = Depends(get_session)) -> dict:
    """Say which publication filled a pre-assigned slot."""
    e = session.get(Experiment, experiment_id)
    if e is None:
        raise HTTPException(404, "no such experiment")
    if e.status != "running":
        raise HTTPException(409, f"experiment is {e.status}")
    row = session.scalar(select(ExperimentAssignment).where(
        ExperimentAssignment.experiment_id == experiment_id,
        ExperimentAssignment.slot == body.slot))
    if row is None:
        raise HTTPException(404, f"no slot {body.slot}")
    v = session.get(Video, body.video_id)
    if v is None:
        raise HTTPException(404, f"no publication {body.video_id}")
    if v.platform != e.platform:
        raise HTTPException(
            400, f"publication {body.video_id} is on {v.platform} and "
                 f"this experiment is on {e.platform}. ADR 0008: the "
                 f"two platforms' metrics are not comparable.")
    clash = session.scalar(select(ExperimentAssignment).where(
        ExperimentAssignment.experiment_id == experiment_id,
        ExperimentAssignment.video_id == body.video_id,
        ExperimentAssignment.slot != body.slot))
    if clash is not None:
        raise HTTPException(
            409, f"publication {body.video_id} already fills slot "
                 f"{clash.slot}. One publication cannot be in both arms.")

    from datetime import datetime, timezone
    row.video_id = body.video_id
    row.note = body.note
    row.filled_at = datetime.now(timezone.utc)
    session.commit()
    return _view(session, e)


@router.post("/{experiment_id}/read-out",
             dependencies=[Depends(require_ui_write)])
def read_out(experiment_id: int,
             session: Session = Depends(get_session)) -> dict:
    """The verdict, once, whichever way it went."""
    e = session.get(Experiment, experiment_id)
    if e is None:
        raise HTTPException(404, "no such experiment")
    try:
        out = ex.read_out(session, e)
    except ex.NotReady as exc:
        # 409 rather than 400: the request is well formed and the
        # answer is "not yet", which is a state and not a mistake.
        raise HTTPException(409, str(exc)) from exc
    except ex.Contaminated as exc:
        raise HTTPException(422, str(exc)) from exc
    session.commit()
    return {**_view(session, e), "readout": out.as_dict(),
            "feedback": ex.feedback(session, e)}


@router.post("/{experiment_id}/abandon",
             dependencies=[Depends(require_ui_write)])
def abandon(experiment_id: int, body: Abandon,
            session: Session = Depends(get_session)) -> dict:
    """Stop a run, with the reason recorded.

    An abandoned experiment is not an inconclusive one, and the
    distinction matters: inconclusive means it ran and could not
    tell, abandoned means it did not run. Collapsing them would let
    a run that was going badly be retired as "no effect found".
    """
    e = session.get(Experiment, experiment_id)
    if e is None:
        raise HTTPException(404, "no such experiment")
    if e.status != "running":
        raise HTTPException(409, f"experiment is {e.status}")
    from datetime import datetime, timezone
    e.status = "abandoned"
    e.abandoned_reason = body.reason
    e.completed_at = datetime.now(timezone.utc)
    session.commit()
    return _view(session, e)
