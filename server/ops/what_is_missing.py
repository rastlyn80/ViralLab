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

from server.db.models import Creator, Video
#: A creator under this cannot be scored against themselves.
#: Imported from the analysis rather than restated, so the page and
#: the findings can never disagree about who counts.
from server.research.virality import MIN_PER_CREATOR

logger = logging.getLogger("virallab.ops.what_is_missing")



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
    and has no colour, framing or movement until something watches
    it. So the caption findings rest on thousands of videos and the
    visual ones on a few hundred, and nothing on the page said so.

    "Analysed" is `competitor_archive.analysed_ids`, which derives
    it from the stage list that does the work. This counted one
    feature name - `vision_shot_distance` - which belongs to the
    paid model pass only, so it reported 470 when the real figure
    was 1,362: every video measured by the free deterministic
    stages was invisible to it. A number on this page that is
    wrong by a factor of three is worse than no number.
    """
    from server.collectors.competitor_archive import analysed_ids

    total = session.scalar(
        select(func.count(Video.id))
        .join(Creator, Creator.id == Video.creator_id)
        .where(Creator.is_own_account.is_(False))) or 0
    seen = session.scalar(
        select(func.count(Video.id))
        .join(Creator, Creator.id == Video.creator_id)
        .where(Creator.is_own_account.is_(False),
               Video.id.in_(analysed_ids(session)))) or 0
    return {"publications": int(total), "watched": int(seen)}


def provider_allowance() -> dict:
    """What the data provider says is left on the account.

    Shown because every paid button goes through Apify, and when
    Apify stops serving the account they all fail the same way.
    The owner met this as "Study a creator you name - Did not
    finish, exited with code 1", under advice to check the
    spelling of a handle that was perfectly correct.

    One unmetered call, cached by the page's own refresh rate. It
    never raises and never blocks: a provider that cannot be asked
    is reported as unknown, because not knowing is not a reason to
    hide the buttons.
    """
    import os

    token = os.environ.get("APIFY_TOKEN")
    if not token:
        return {"known": False, "why": "no provider token is configured"}
    try:
        from server.discovery.apify import ApifyDiscoveryAdapter

        return ApifyDiscoveryAdapter(token).allowance()
    except Exception as exc:               # noqa: BLE001 - reported
        logger.warning("could not read the provider allowance: %s", exc)
        return {"known": False, "why": str(exc)[:200]}


def _collection_is_blocked(session: Session) -> str | None:
    """Whether the last attempt to fetch video was refused.

    Read from the run history rather than by making another
    request: asking the platform whether it will talk to us is
    itself a request, and the whole route is conditioned on not
    knocking when we have been told to stop.
    """
    from server.db.models import Operation

    row = session.scalars(
        select(Operation)
        .where(Operation.kind.in_(("watch_competitors",
                                   "watch_competitors_closely")),
               Operation.status.in_(("ok", "partial")))
        .order_by(Operation.id.desc()).limit(1)).first()
    if row is None or row.status != "partial":
        return None
    # `removeprefix`, not `lstrip`. lstrip takes a SET of
    # characters, so "Stopped early — the platform..." came out as
    # "he platform..." - it ate the leading t because t is in
    # "Stopped". A classic, and invisible until you read the
    # sentence.
    note = (row.progress_note or "").removeprefix("Stopped early — ")
    return (f"The last attempt stopped before collecting anything: "
            f"{note}. "
            f"TikTok has been refusing this server since the end of "
            f"October, and that is where almost all of these videos "
            f"are. Pressing it again will not help until that "
            f"changes.")


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

    allowance = provider_allowance()
    if allowance.get("exhausted"):
        # First, and on its own, because nothing else on the list
        # can run until this is cleared.
        jobs.append({
            "do": "Raise the limit on your Apify account",
            "run": None,
            "because": (
                f"Apify has stopped serving this account - it has "
                f"used ${allowance.get('used_usd')} of its "
                f"${allowance.get('limit_usd')} monthly limit. Every "
                f"button below that costs money will fail until that "
                f"is raised or their billing month resets. Nothing "
                f"has been charged here: this project has spent "
                f"nothing with them this month."),
            "worth": "blocking",
        })

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
    blocked = _collection_is_blocked(session)
    if unwatched > 0:
        job = {
            "do": "Look at competitors' videos",
            "run": "watch_competitors",
            "because": (
                f"{vision['watched']} of {vision['publications']} "
                f"other creators' publications have ever been "
                f"looked at. Captions, hashtags and posting times "
                f"arrive with the post; how a video is framed, lit "
                f"and cut does not. So findings about what people "
                f"SAY rest on thousands of videos and findings "
                f"about what they SHOW rest on a few hundred."),
            "worth": "high" if vision["watched"] < 1000 else "medium",
        }
        if blocked:
            # Said here rather than discovered by pressing it. The
            # owner pressed this five times, each run ended in ten
            # seconds saying "Finished", and he had to ask whether
            # anything was working. It was not.
            job["blocked_because"] = blocked
            job["worth"] = "cannot run"
        jobs.append(job)

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
        "provider": allowance,
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
