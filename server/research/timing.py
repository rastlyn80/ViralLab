"""When he posted, against how it did. And what that cannot tell him.

The owner asked the chat, on a Monday afternoon: *"What time do you
recommend I post it here in the UK?"* It answered that it had no
access to posting times or their relationship to performance. That
was wrong in an avoidable way - `videos.posted_at` is populated on
all 9,142 publications and has been since the first collector - and
wrong in the same shape as the caption failure before it: a tool
that could see the answer did not exist, so the chat denied the
answer existed.

BLUEPRINT M10 lists "cadence analysis - posting rhythm vs outcomes".
This is the hour-and-weekday half of it.

## What is measured

Each publication is scored by its robust z against **its own
creator's** trailing window - the same within-creator measure used
everywhere else, so a creator who grew tenfold over the corpus does
not make their later hours look good. Publications are then grouped
by the hour and weekday they went out, and each group is compared
against every other publication by the same creators with a
two-sided Mann-Whitney, corrected across the family with
Benjamini-Hochberg.

Parts of day rather than 24 separate hours. Twenty-four tests on a
few hundred publications finds something at random and calls it
five o'clock; four tests on the same data answer the question he
actually asked, which is "morning, afternoon or evening".

## Timezone, which decides what can be claimed

`posted_at` is stored timezone-aware in Europe/London, so **the
owner's local hour is known exactly**. His audience's is not. A
finding here says "publications he put out in the afternoon did
better", not "his audience is awake in the afternoon", and those
differ by the whole unknown distribution of where his viewers are.

**This cannot be replicated on other creators**, which is the
project's usual bar, and the reason is not sample size. We know the
UTC instant a competitor posted and nothing about where they were,
so their "9 a.m." and his are different moments and pooling them
measures the timezone spread of the cohort. The cross-creator
figures are therefore computed and reported as *unavailable for
promotion*, never as replication. Promoting one would require
either a creator-location field nobody has, or an audience-location
breakdown that only each creator's own analytics can give.

## The confound that does not go away

He does not choose posting times at random. A video finished on a
concert day goes out when the concert ends; a piece he is pleased
with may get posted when he thinks people are watching. So the hour
carries some of *what* he posted and some of *how he felt about
it*, and no amount of data here separates those from the hour
itself. Observed, and it cannot be more.
"""

from __future__ import annotations

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from server.db.models import Creator, MetricsSnapshot, Video
from server.features.retention import benjamini_hochberg
from server.research.cohort import _mann_whitney_p

logger = logging.getLogger("virallab.research.timing")

#: Where the owner is. `posted_at` is already stored aware in this
#: zone; naming it here is what makes "his local hour" a claim
#: rather than an assumption, and it is the only zone in the project
#: that is known rather than guessed.
OWNER_ZONE = ZoneInfo("Europe/London")

#: Four, not twenty-four. See the docstring: twenty-four tests on a
#: few hundred publications will find an hour, and the question he
#: asked was "morning, afternoon or evening".
#:
#: Boundaries are the ordinary English ones rather than anything
#: data-driven, because a band chosen to maximise a difference is
#: not a band, it is the difference.
PARTS = (
    ("night", 23, 6, "11pm-6am"),
    ("morning", 6, 12, "6am-12pm"),
    ("afternoon", 12, 18, "12pm-6pm"),
    ("evening", 18, 23, "6pm-11pm"),
)

WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday",
            "Saturday", "Sunday")

#: Below this a group is described and not tested. `_mann_whitney_p`
#: already refuses under 3 a side; this is the larger number at
#: which the normal approximation is worth reading.
MIN_PER_GROUP = 12

#: Same threshold as everything else in the project.
Q_THRESHOLD = 0.10


def part_of_day(hour: int) -> str:
    for name, start, end, _ in PARTS:
        if start > end:                    # the band that wraps midnight
            if hour >= start or hour < end:
                return name
        elif start <= hour < end:
            return name
    return "night"                         # unreachable; kept total


def _scored(session: Session, *, own: bool) -> list[tuple[datetime, float]]:
    """Every publication's local posting time and its robust z.

    Returns the owner's publications in his own zone. For other
    creators the instant is returned unconverted, because their zone
    is not known - see the module docstring. The caller must not
    treat the two as comparable.
    """
    from server.research.baseline import Observation, rolling_baseline

    rows = session.execute(
        select(Video.id, Video.creator_id, Video.external_id,
               Video.posted_at, MetricsSnapshot.views,
               MetricsSnapshot.captured_at)
        .join(Creator, Creator.id == Video.creator_id)
        .join(MetricsSnapshot, MetricsSnapshot.video_id == Video.id)
        .where(Creator.is_own_account.is_(own),
               MetricsSnapshot.views.is_not(None),
               Video.posted_at.is_not(None))
        .order_by(Video.id, MetricsSnapshot.captured_at.desc())).all()

    seen: set[int] = set()
    by_creator: dict[int, list[Observation]] = {}
    when: dict[int, datetime] = {}
    for vid, cid, ext, posted, views, captured in rows:
        if vid in seen:
            continue                       # latest snapshot only
        seen.add(vid)
        when[vid] = posted
        by_creator.setdefault(cid, []).append(Observation(
            video_id=vid, external_id=ext or str(vid),
            published_at=posted, views=int(views),
            observed_at=captured))

    out: list[tuple[datetime, float]] = []
    for obs in by_creator.values():
        obs.sort(key=lambda o: o.published_at)
        for point in rolling_baseline(obs):
            vid = point.observation.video_id
            if vid is None or point.robust_z is None:
                continue
            moment = when[vid]
            if own and moment.tzinfo is not None:
                moment = moment.astimezone(OWNER_ZONE)
            out.append((moment, float(point.robust_z)))
    return out


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    s = sorted(values)
    mid = len(s) // 2
    return (s[mid] if len(s) % 2
            else (s[mid - 1] + s[mid]) / 2.0)


def _compare(groups: dict[str, list[float]], order: tuple[str, ...]
             ) -> list[dict]:
    """Each group against everything else, BH-corrected together."""
    raw: list[dict] = []
    for name in order:
        mine = groups.get(name) or []
        rest = [v for key, vals in groups.items() if key != name
                for v in vals]
        entry = {
            "when": name,
            "publications": len(mine),
            "median_vs_your_usual": (
                None if not mine else round(_median(mine) or 0.0, 3)),
        }
        if len(mine) < MIN_PER_GROUP or len(rest) < MIN_PER_GROUP:
            entry["tested"] = False
            entry["why_not_tested"] = (
                f"{len(mine)} publications. Below {MIN_PER_GROUP} this "
                f"is counted and described, not tested.")
            entry["p_value"] = None
        else:
            entry["tested"] = True
            entry["p_value"] = _mann_whitney_p(mine, rest)
        raw.append(entry)

    qs = benjamini_hochberg([e["p_value"] for e in raw])
    for entry, q in zip(raw, qs):
        entry["q_value"] = None if q is None else round(q, 4)
        entry["separates"] = bool(
            entry["tested"] and q is not None and q < Q_THRESHOLD)
    return raw


def _describe(entry: dict, noun: str) -> str:
    med = entry["median_vs_your_usual"]
    if med is None:
        return f"Nothing posted on a {noun} yet."
    way = "better" if med > 0 else "worse"
    body = (f"Your {entry['publications']} {noun} publications ran "
            f"{abs(med):.2f} robust deviations {way} than the run "
            f"around them, at the middle.")
    if entry.get("separates"):
        return (f"{body} That difference survives correction "
                f"(q = {entry['q_value']}).")
    if entry.get("tested"):
        return (f"{body} It does not separate from the rest "
                f"(q = {entry['q_value']}), so treat it as describing "
                f"these publications rather than {noun}s.")
    return f"{body} {entry.get('why_not_tested', '')}".strip()


def build(session: Session) -> dict:
    """When his publications went out, against how they did."""
    mine = _scored(session, own=True)

    by_part: dict[str, list[float]] = {}
    by_day: dict[str, list[float]] = {}
    by_hour: dict[int, list[float]] = {}
    for moment, z in mine:
        by_part.setdefault(part_of_day(moment.hour), []).append(z)
        by_day.setdefault(WEEKDAYS[moment.weekday()], []).append(z)
        by_hour.setdefault(moment.hour, []).append(z)

    parts = _compare(by_part, tuple(p[0] for p in PARTS))
    days = _compare(by_day, WEEKDAYS)
    labels = {p[0]: p[3] for p in PARTS}
    for entry in parts:
        entry["clock"] = labels[entry["when"]]
        entry["plain"] = _describe(entry, entry["when"])
    for entry in days:
        entry["plain"] = _describe(entry, entry["when"])

    separating = [e for e in parts + days if e.get("separates")]
    return {
        "publications_scored": len(mine),
        "timezone": "Europe/London",
        "parts_of_day": parts,
        "days_of_week": days,
        "by_hour": [
            {"hour": h, "publications": len(by_hour[h]),
             "median_vs_your_usual": round(_median(by_hour[h]) or 0.0, 3)}
            for h in sorted(by_hour)],
        "by_hour_note": (
            "Descriptive only. Twenty-four tests on this many "
            "publications would find an hour whether or not one is "
            "there, so the hours are shown and not tested; the "
            "testing is done on the four parts of the day."),
        "grade": "Observed",
        "headline": _headline(separating, len(mine)),
        "can_this_be_replicated": (
            "No, and not for want of data. Posting time only means "
            "something in the poster's own local hour, and for other "
            "creators we know the instant they posted but not where "
            "they were - so pooling them measures how spread out the "
            "cohort's timezones are. This is the one measurement in "
            "the project that cannot reach the replication bar, and "
            "it would take a creator-location field nobody has."),
        "the_confound": (
            "You do not choose posting times at random. A video "
            "finished on a concert day goes out when the concert "
            "ends. So the hour carries some of what you posted and "
            "how you felt about it, not only the hour. Observed, and "
            "it cannot be more."),
        "about_your_audience": (
            "These are YOUR local hours, which are known exactly. "
            "Where your viewers are is not known here - only each "
            "platform's own analytics can tell you that - so a good "
            "hour here is a fact about your publications, not a "
            "deduction about when anyone is awake."),
    }


def _headline(separating: list[dict], n: int) -> str:
    """One sentence for the top of the page.

    Written first for the case where something runs ABOVE his
    usual, and the real corpus turned out to be the other one:
    the only band that survives correction is morning, 0.36
    deviations *below*. The first version produced "Across 562
    publications, , after correcting..." - it had a clause for
    the good news and none for the bad, which is the wrong way
    round for a project whose negative results are its strongest.
    """
    if not n:
        return ("No publication has a posting time and a baseline to "
                "score it against yet.")
    if not separating:
        return (f"Across {n} publications, no part of the day and no "
                f"weekday separates from the rest once the tests are "
                f"corrected for each other. On this evidence, when "
                f"you post is not where your differences are.")

    above = sorted((e for e in separating
                    if (e["median_vs_your_usual"] or 0) > 0),
                   key=lambda e: -(e["median_vs_your_usual"] or 0))
    below = sorted((e for e in separating
                    if (e["median_vs_your_usual"] or 0) < 0),
                   key=lambda e: e["median_vs_your_usual"] or 0)
    bits = []
    if above:
        bits.append(_side(above, "above your usual"))
    if below:
        bits.append(_side(below, "below it" if above
                          else "below your usual"))
    return (f"Across {n} publications, " + ", and ".join(bits)
            + ", after correcting for the other times tested. "
              "Observed: when you post is tangled up with what you "
              "post, and nothing here separates them.")


def _side(entries: list[dict], where: str) -> str:
    names = [e["when"] for e in entries]
    if len(names) == 1:
        return f"{names[0]} ran {where}"
    return f"{', '.join(names[:-1])} and {names[-1]} ran {where}"


def advice(session: Session, *, weekday: str = "", hour: int | None = None
           ) -> dict:
    """What the record says about posting now. Not a recommendation.

    Built for the chat, which was asked "it's Monday afternoon,
    what time should I post". The honest answer has a shape: here is
    what your publications at that time did, here is what the best
    and worst parts of your day look like, and here is why none of
    that is an instruction.
    """
    out = build(session)
    asked: dict = {"weekday": weekday or None, "hour": hour}
    if hour is not None and 0 <= hour <= 23:
        name = part_of_day(hour)
        asked["part_of_day"] = name
        asked["your_record_then"] = next(
            (e for e in out["parts_of_day"] if e["when"] == name), None)
    if weekday:
        match = next((d for d in WEEKDAYS
                      if d.lower() == weekday.strip().lower()), None)
        asked["weekday"] = match
        asked["your_record_that_day"] = next(
            (e for e in out["days_of_week"] if e["when"] == match), None)
    out["you_asked_about"] = asked
    out["why_this_is_not_a_recommendation"] = (
        "Picking the hour with the best number would be acting on a "
        "ranking of noise. Only a part of the day that survives "
        "correction is worth anything, and even then it says your "
        "publications at that time did better - not that moving this "
        "one would.")
    return out
