"""What the evidence needs next, worked out rather than written down.

The owner: *"tell me how can I train you on even more stuff by
others"*. The honest answer changes every week, and a paragraph of
advice I write today is stale by the time he reads it twice. So
this computes it.

## The one number that decides everything

A creator with seven publications contributes **nothing**. Every
finding in this project is a within-creator comparison - did this
video beat the ones around it on the same account - and under
`virality.MIN_PER_CREATOR` there is no "around it" to compare
against. Their videos sit in the database and are skipped at
analysis time, silently.

That makes "how many creators do we have" the wrong question and
"how many are deep enough to count" the right one. The gap between
those two numbers is usually the cheapest work available: a creator
already found, needing one collection pass to start counting.

## Why this is in `ops/` and not in `research/`

It answers "what should I press", not "what is true". Everything
here is a count of rows and a comparison against a threshold -
nothing is tested, nothing is a finding, and nothing it says needs
a grade. It points at buttons.
"""

from __future__ import annotations

import logging

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from server.db.models import Creator, Video, VideoFeature
#: A creator under this cannot be scored against themselves.
#: Imported from the analysis rather than restated, so the page and
#: the findings can never disagree about who counts.
from server.research.virality import MIN_PER_CREATOR

logger = logging.getLogger("virallab.ops.what_is_missing")

#: Measurements that only exist if somebody watched the video.
#: Everything else - captions, hashtags, posting time - comes free
#: with the publication record.
A_VISUAL_FEATURE = "vision_shot_distance"


def _counts(session: Session) -> list[dict]:
    pubs = (select(Video.creator_id,
                   func.count(Video.id).label("n"))
            .group_by(Video.creator_id).subquery())
    rows = session.execute(
        select(Creator.platform,
               func.count(Creator.id),
               func.count(Creator.id).filter(
                   func.coalesce(pubs.c.n, 0) >= MIN_PER_CREATOR),
               func.coalesce(func.sum(pubs.c.n), 0))
        .select_from(Creator)
        .outerjoin(pubs, pubs.c.creator_id == Creator.id)
        .where(Creator.is_own_account.is_(False))
        .group_by(Creator.platform)).all()
    return [{"platform": p, "creators": c, "deep_enough": d,
             "publications": int(v)} for p, c, d, v in rows]


def _watched(session: Session) -> dict:
    """How many other creators' videos anybody has actually looked at.

    The widest gap in the corpus and the least obvious one, because
    every other measurement is populated: a video has a caption, a
    posting time and a hashtag count the moment it is collected,
    and has no colour, framing or movement until a model watches
    it. So the caption findings rest on thousands of videos and the
    visual ones on a few hundred, and nothing on the page said so.
    """
    total = session.scalar(
        select(func.count(Video.id))
        .join(Creator, Creator.id == Video.creator_id)
        .where(Creator.is_own_account.is_(False))) or 0
    seen = session.scalar(
        select(func.count(func.distinct(VideoFeature.video_id)))
        .join(Video, Video.id == VideoFeature.video_id)
        .join(Creator, Creator.id == Video.creator_id)
        .where(Creator.is_own_account.is_(False),
               VideoFeature.feature == A_VISUAL_FEATURE,
               VideoFeature.status == "ok")) or 0
    return {"publications": int(total), "watched": int(seen)}


def summary(session: Session) -> dict:
    """What to do next, in the order it is worth doing."""
    try:
        platforms = _counts(session)
        vision = _watched(session)
    except Exception:                      # noqa: BLE001 - reported
        logger.exception("could not work out what the evidence needs")
        return {}

    total = sum(p["creators"] for p in platforms)
    deep = sum(p["deep_enough"] for p in platforms)
    thin = total - deep
    jobs: list[dict] = []

    if thin:
        jobs.append({
            "do": "Collect more of each creator's back catalogue",
            "run": "deepen_creators",
            "because": (
                f"{thin} of the {total} creators we follow have fewer "
                f"than {MIN_PER_CREATOR} posts collected. Every "
                f"finding compares a video against the others on the "
                f"same account, so those {thin} contribute nothing "
                f"at all until they are filled in. This is the "
                f"cheapest work available - they are already found."),
            "worth": "high" if thin > deep else "medium",
        })

    unwatched = vision["publications"] - vision["watched"]
    if unwatched > 0:
        jobs.append({
            "do": "Look at competitors' videos",
            "run": "watch_competitors",
            "because": (
                f"{vision['watched']} of {vision['publications']} "
                f"other creators' publications have ever been "
                f"watched. Captions, hashtags and posting times "
                f"arrive with the post; how a video is framed, lit "
                f"and cut does not. So findings about what people "
                f"SAY rest on thousands of videos and findings "
                f"about what they SHOW rest on a few hundred."),
            "worth": "high" if vision["watched"] < 1000 else "medium",
        })

    jobs.append({
        "do": "Study a creator you name",
        "run": "follow_creator",
        "because": (
            "You know this niche better than a hashtag search does. "
            "A pianist you already rate is worth more than ten the "
            "search turned up, and naming one is the single most "
            "useful thing you can add by hand."),
        "worth": "high",
    })
    jobs.append({
        "do": "Find more creators like you",
        "run": "find_creators",
        "because": (
            f"{deep} creators are deep enough to count. More of them "
            f"is what lets a measurement be checked on people other "
            f"than you, which is the only way anything here gets "
            f"promoted past Observed."),
        "worth": "medium" if deep >= 200 else "high",
    })

    return {
        "creators_followed": total,
        "creators_that_count": deep,
        "creators_too_thin": thin,
        "minimum_posts_to_count": MIN_PER_CREATOR,
        "by_platform": platforms,
        "publications_watched": vision["watched"],
        "publications_total": vision["publications"],
        "next": jobs,
        "how_to_read": (
            f"A creator with fewer than {MIN_PER_CREATOR} posts "
            f"collected is in the database and not in any finding. "
            f"Every result here compares a video against the others "
            f"on the same account, and under that many there is "
            f"nothing to compare it against."),
    }
