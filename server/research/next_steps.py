"""What changed since you last looked, and what to do about it.

The home screen answered "what has been collected" - true, and not
what anyone opens a product to find out. These two functions answer
the questions a person actually has on a Monday morning: *did the
thing I posted work*, and *what should I do now*.

## What makes a step worth showing

Every suggestion here has to survive three tests, because a list of
plausible-sounding chores is worse than an empty page:

* **It names something specific.** "Improve your hooks" is not a
  step. "You named the form on 2 of your last 12, and your
  breakthroughs named it far more often" is.
* **It comes from measurement**, not from a rule somebody typed.
  Every step below is computed from the database and disappears on
  its own when the situation changes.
* **It goes somewhere.** A step with no page to act on is a
  complaint.

Ordered by how directly the owner can act, not by how interesting
it is: work waiting on him first, then where his own behaviour
goes against his own evidence, then where the data has gone stale.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from server.db.models import Creator, MetricsSnapshot, Video

logger = logging.getLogger("virallab.research.next_steps")

#: How many recent publications "what changed" covers.
RECENT = 6

#: Competitor data older than this is worth refreshing. Chosen
#: because their view counts keep accumulating while ours stand
#: still, which quietly ages every comparison.
STALE_AFTER = timedelta(days=21)


def what_changed(session: Session, limit: int = RECENT) -> dict:
    """The owner's recent publications, and how they actually did.

    Scored against the creator's own trailing baseline, not against
    a target: "did this beat what you normally do" is the only
    question that survives two accounts of different sizes and
    videos of different ages.
    """
    from server.research.baseline import Observation, rolling_baseline

    rows = session.execute(
        select(Video.id, Video.creator_id, Video.external_id,
               Video.posted_at, Video.caption, Video.platform,
               MetricsSnapshot.views, MetricsSnapshot.captured_at)
        .join(Creator, Creator.id == Video.creator_id)
        .join(MetricsSnapshot, MetricsSnapshot.video_id == Video.id)
        .where(Creator.is_own_account.is_(True),
               MetricsSnapshot.views.is_not(None),
               Video.posted_at.is_not(None))
        .order_by(Video.id, MetricsSnapshot.captured_at.desc())).all()

    seen: set[int] = set()
    by_creator: dict[int, list] = {}
    meta: dict[int, dict] = {}
    for vid, cid, ext, posted, caption, platform, views, captured in rows:
        if vid in seen:
            continue
        seen.add(vid)
        by_creator.setdefault(cid, []).append(Observation(
            video_id=vid, external_id=ext or str(vid),
            published_at=posted, views=int(views),
            observed_at=captured, caption=caption))
        meta[vid] = {"caption": (caption or "")[:120],
                     "platform": platform, "posted_at": posted,
                     "views": int(views)}

    scored: dict[int, float] = {}
    for obs in by_creator.values():
        obs.sort(key=lambda o: o.published_at)
        for point in rolling_baseline(obs):
            vid = point.observation.video_id
            if vid is not None and point.robust_z is not None:
                scored[vid] = float(point.robust_z)

    newest = sorted(meta, key=lambda v: meta[v]["posted_at"],
                    reverse=True)[:limit]
    out = []
    for vid in newest:
        z = scored.get(vid)
        out.append({
            "video_id": vid,
            **{k: v for k, v in meta[vid].items() if k != "posted_at"},
            "posted_at": meta[vid]["posted_at"].isoformat(),
            "verdict": _verdict(z),
            "compared": z is not None,
        })
    return {
        "publications": out,
        "how_scored": (
            "Each one against the run of posts immediately before "
            "it on the same account, so a big account and a small "
            "one are judged the same way and an old post is not "
            "flattered by having had longer to collect views."),
    }


def _verdict(z: float | None) -> str:
    if z is None:
        return "too early to say"
    if z >= 2.5:
        return "broke out"
    if z >= 1.0:
        return "did well"
    if z <= -1.0:
        return "did poorly"
    return "about usual"


def next_steps(session: Session) -> list[dict]:
    """What to do now, strongest claim on the owner's time first."""
    steps: list[dict] = []
    steps.extend(_waiting_on_you(session))
    steps.extend(_against_your_own_evidence(session))
    steps.extend(_data_going_stale(session))
    return steps


def _waiting_on_you(session: Session) -> list[dict]:
    """Review queues. Work only he can do, so it goes first."""
    from server.db.models import PublicationLink, VideoMasterLink

    out = []
    pairs = session.scalar(
        select(func.count(PublicationLink.id))
        .where(PublicationLink.status == "proposed")) or 0
    if pairs:
        out.append({
            "what": f"Confirm {pairs} suggested pairs of the same video",
            "why": ("We think these are the same performance posted to "
                    "both platforms. Confirming them lets the two "
                    "accounts be compared as one piece of work."),
            "where": "#/review/pairs", "action": "Check them",
        })
    masters = session.scalar(
        select(func.count(VideoMasterLink.id))
        .where(VideoMasterLink.status == "proposed")) or 0
    if masters:
        out.append({
            "what": f"Match {masters} of your files to posts",
            "why": ("Linking your own recordings to what you posted is "
                    "what lets the platform talk about the piece rather "
                    "than about a video id."),
            "where": "#/review/masters", "action": "Match them",
        })
    return out


def _against_your_own_evidence(session: Session) -> list[dict]:
    """Where recent work runs against the measurements.

    The most useful thing on the page, and the only one that is
    about the videos rather than about the plumbing.
    """
    from server.research import adherence as adh

    out = []
    for source in ("mine", "theirs"):
        try:
            check = adh.check(session, against=source)
        except Exception:                   # noqa: BLE001 - optional
            logger.exception("adherence unavailable for next steps")
            continue
        whose = ("your own breakthroughs" if source == "mine"
                 else "other creators' breakthroughs")
        for f in check.get("findings", []):
            if f["verdict"] != "not following" or not f.get("separates"):
                continue
            out.append({
                "what": f"Your recent posts go against {f['display_name']}",
                "why": (f"{f['plain']} It held on {whose}."),
                "where": (f"#/research?on=doing&against={source}"),
                "action": "See the detail",
            })
    return out[:4]


def _data_going_stale(session: Session) -> list[dict]:
    """Where the evidence is thinning, with the button that fixes it.

    Deliberately last. A creator should not open their own product
    and be told to go and do maintenance before being told anything
    about their videos.
    """
    out = []
    now = datetime.now(timezone.utc)

    newest = session.scalar(
        select(func.max(Video.created_at))
        .join(Creator, Creator.id == Video.creator_id)
        .where(Creator.is_own_account.is_(False)))
    if newest is None or (now - newest) > STALE_AFTER:
        days = "ever" if newest is None else f"{(now - newest).days} days"
        out.append({
            "what": "Collect more from other creators",
            "why": (f"Nothing new has come in from them for {days}. "
                    f"Their view counts keep climbing while ours stand "
                    f"still, which quietly ages every comparison."),
            "where": "#/collect", "action": "Collect",
        })

    thin = session.scalar(select(func.count()).select_from(
        select(Video.creator_id)
        .join(Creator, Creator.id == Video.creator_id)
        .where(Creator.is_own_account.is_(False))
        .group_by(Video.creator_id)
        .having(func.count(Video.id) < 8).subquery())) or 0
    if thin >= 50:
        out.append({
            "what": f"{thin} creators are too thin to learn from",
            "why": ("We hold only a handful of posts each for these, "
                    "and a creator with one video cannot show which of "
                    "their videos did unusually well."),
            "where": "#/collect", "action": "Fill them in",
        })
    return out
