"""The home screen, answered rather than decorated.

The dashboard used to open on operational health - snapshot counts,
token expiry, media coverage percentages, collection-run kinds. All
true, all necessary somewhere, and none of it what a creator opens
the product to find out.

This endpoint answers the six questions the home screen is for:

  1. What has been collected, per platform?
  2. How much of it has been analysed?
  3. How many other creators are being studied?
  4. What has been found?
  5. What is uncertain, contradicted or missing?
  6. What could be tried next?

Every number is counted from the database on request. None is
configured, cached or written down, because a dashboard figure that
drifts from the data is worse than no dashboard.

The operational numbers have not been deleted - `/api/overview` still
serves them and the home screen still shows them, below, under a
disclosure. "Secondary" is the requirement, not "absent".
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from server.api.deps import get_session
from server.api.ui import require_ui
from server.db.models import Creator, Video, VideoFeature
from server.features import catalogue as cat
from shared import hookfeat

logger = logging.getLogger("virallab.api.home")

router = APIRouter(prefix="/api/home", tags=["home"])

#: A publication counts as analysed when the deterministic pass has
#: stored something for it. That is the pass every publication gets;
#: using a narrower definition would make the figure depend on which
#: optional stage happened to run.
ANALYSED_METHOD = hookfeat.METHOD_VERSION


@router.get("", dependencies=[Depends(require_ui)])
def home(session: Session = Depends(get_session)) -> dict:
    """Everything the first screen needs, counted now."""
    from server.research import insights as ins

    own_rows = session.execute(
        select(Video.platform, func.count(Video.id))
        .join(Creator, Creator.id == Video.creator_id)
        .where(Creator.is_own_account.is_(True))
        .group_by(Video.platform)).all()
    analysed_rows = session.execute(
        select(Video.platform, func.count(func.distinct(Video.id)))
        .join(Creator, Creator.id == Video.creator_id)
        .join(VideoFeature, VideoFeature.video_id == Video.id)
        .where(Creator.is_own_account.is_(True),
               VideoFeature.method_version == ANALYSED_METHOD,
               VideoFeature.status == "ok")
        .group_by(Video.platform)).all()
    analysed = dict(analysed_rows)

    mine = [{
        "platform": p,
        "label": "Instagram" if p == "instagram" else "TikTok",
        "publications": n,
        "analysed": analysed.get(p, 0),
    } for p, n in sorted(own_rows)]

    competitors = session.scalar(
        select(func.count(Creator.id))
        .where(Creator.is_own_account.is_(False))) or 0
    competitor_pubs = session.scalar(
        select(func.count(Video.id))
        .join(Creator, Creator.id == Video.creator_id)
        .where(Creator.is_own_account.is_(False))) or 0

    found = ins.build(session)
    gaps = ins.gaps(session)
    entries = cat.build(session)

    # Which creators have a report worth opening. The owner always
    # does; a competitor with one publication does not, and offering
    # the link anyway is a dead end.
    reportable = session.execute(
        select(Creator.id, Creator.handle, Creator.platform,
               Creator.is_own_account, func.count(Video.id))
        .join(Video, Video.creator_id == Creator.id)
        .group_by(Creator.id, Creator.handle, Creator.platform,
                  Creator.is_own_account)
        .having(func.count(Video.id) >= 1)
        .order_by(Creator.is_own_account.desc(),
                  func.count(Video.id).desc())).all()

    from server.research import next_steps as ns

    # Added after the owner said the platform, not a coding session,
    # should be where the research happens - and then that he kept
    # losing track. A home screen that describes state answers
    # neither. These two answer "did the thing I posted work" and
    # "what should I do now", which are the questions he actually
    # arrives with.
    try:
        changed = ns.what_changed(session)
        steps = ns.next_steps(session)
    except Exception:                       # noqa: BLE001 - reported
        logger.exception("could not build the home guidance")
        changed, steps = {"publications": []}, []

    return {
        "what_changed": changed,
        "next_steps": steps,
        "mine": mine,
        "my_total": sum(m["publications"] for m in mine),
        "my_analysed": sum(m["analysed"] for m in mine),
        "competitors": {
            "creators": competitors,
            "publications": competitor_pubs,
        },
        "findings": [{
            "headline": i.headline,
            "platform": i.platform,
            "status": i.status,
            "direction": i.direction,
            "feature": i.feature,
        } for i in found[:5]],
        "findings_total": len(found),
        "uncertain": {
            "measurements_we_do_not_trust": len(gaps),
            "nothing_is_proven": (
                "No finding has been through a planned experiment, so "
                "none of them is established as a cause. They are "
                "patterns we measured, and the next step for any of "
                "them is a test."),
            "examples": [{"name": g["display_name"],
                          "why": g["why_unavailable"]}
                         for g in gaps[:4]],
        },
        "try_next": [i.suggested_experiment for i in found[:2]
                     if i.suggested_experiment],
        "reports": [{
            "creator_id": cid, "handle": handle, "platform": plat,
            "is_own_account": bool(own), "publications": n,
        } for cid, handle, plat, own, n in reportable[:8]],
        "measurement_counts": {
            "total": len(entries),
            "usable": sum(1 for e in entries
                          if e.grade in ("Associated", "Validated")),
            "unavailable": sum(1 for e in entries
                               if e.grade == "Unavailable"),
        },
    }
