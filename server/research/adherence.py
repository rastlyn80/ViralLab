"""Are you actually doing the things your own breakthroughs did?

The owner's requirement has three clauses, and this is the middle
one: research what separates the videos that broke out, *then check
that he is following the same*, then suggest. The first and third
exist - `virality.breakthroughs()` finds the differences and
`ai/creative.py` writes against them. Nothing closed the loop back
onto his own recent work, so a finding could sit on a dashboard for
weeks while every video published in those weeks ignored it.

## What "following" means here, exactly

Each breakthrough difference comes with two levels: what the
publications that broke out averaged, and what the rest averaged. A
recent publication **follows** that finding when its value sits
closer to the breakout level than to the ordinary one.

The midpoint is an arbitrary line and is named as one. It is used
because the alternatives are worse: comparing against the breakout
mean alone would mark half of the breakouts themselves as failures,
and comparing against the ordinary mean would pass almost
everything. The threshold is returned with every finding so it can
be argued with rather than taken on faith.

## What this is not

It is not a score, and deliberately not presented as one. Three
things make a single number dishonest here:

* **The findings are Observed, not causal.** Matching all nine is
  not a prediction, and a publication that matches none may still
  do well. `breakthroughs()` says what went together in videos that
  already exist; nothing yet says what happens if you change one.
* **Not every finding is measured on every publication.** A video
  with no vision analysis is unmeasured, not failing, and it is
  counted as neither.
* **Findings overlap.** Overall visual detail and background visual
  detail are not independent, so "7 of 9" counts the same thing
  more than once. Summing them into a percentage would launder that
  into a precision the measurements do not have.

So the output is per finding and per publication, and the summary
counts rather than scores.
"""

from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from server.db.models import Creator, MetricsSnapshot, Video
from server.features import lexicon as lex

logger = logging.getLogger("virallab.research.adherence")

METHOD_VERSION = "adherence-v1"

#: How many recent publications to check. Twelve is roughly a
#: quarter at this account's cadence - long enough that one unusual
#: video does not dominate, short enough to still describe what he
#: is doing now rather than what he did in 2023.
RECENT = 12

#: Below this many measured publications a finding is reported but
#: its verdict is withheld. Three videos agreeing is not a habit.
MIN_MEASURED = 4

#: Share of measured recent publications that must follow a finding
#: before it counts as a habit rather than a coincidence.
FOLLOWING = 0.70
NOT_FOLLOWING = 0.30


def _recent_own(session: Session, limit: int) -> list[dict]:
    """The owner's most recent publications, newest first."""
    rows = session.execute(
        select(Video.id, Video.posted_at, Video.caption, Video.platform)
        .join(Creator, Creator.id == Video.creator_id)
        .where(Creator.is_own_account.is_(True),
               Video.posted_at.is_not(None))
        .order_by(desc(Video.posted_at)).limit(limit)).all()
    out = []
    for vid, posted, caption, platform in rows:
        views = session.scalar(
            select(MetricsSnapshot.views)
            .where(MetricsSnapshot.video_id == vid,
                   MetricsSnapshot.views.is_not(None))
            .order_by(desc(MetricsSnapshot.captured_at)).limit(1))
        out.append({"video_id": vid, "posted_at": posted,
                    "caption": (caption or "")[:160],
                    "platform": platform,
                    "views": int(views) if views is not None else None})
    return out


def _median(sorted_values: list[float]) -> float | None:
    if not sorted_values:
        return None
    n = len(sorted_values)
    mid = n // 2
    if n % 2:
        return round(sorted_values[mid], 4)
    return round((sorted_values[mid - 1] + sorted_values[mid]) / 2.0, 4)


def _separates(sorted_values: list[float], hi: float, lo: float,
               wants_higher: bool) -> bool:
    """Whether this finding still tells recent publications apart.

    Four of the owner's findings came back "9 of 9 following", which
    looks like excellent discipline and is not. His recent work sits
    *beyond* the breakthrough level on all of them, so the line is
    passed by everything and separates nothing. Reporting that as a
    pass would be flattery: the honest statement is that the finding
    has stopped distinguishing between his publications, and the
    place to look for what separates them now is elsewhere.
    """
    typical = _median(sorted_values)
    if typical is None:
        return False
    beyond = typical >= hi if wants_higher else typical <= lo
    unanimous = all((v >= (hi + lo) / 2.0) == wants_higher
                    for v in sorted_values)
    return not (beyond and unanimous)


def _verdict(followed: int, measured: int) -> str:
    if measured < MIN_MEASURED:
        return "not enough measured"
    share = followed / measured
    if share >= FOLLOWING:
        return "following"
    if share <= NOT_FOLLOWING:
        return "not following"
    return "mixed"


def check(session: Session, *, recent: int = RECENT) -> dict:
    """Each breakthrough finding, against the owner's recent work."""
    from server.research import virality as vir

    breaks = vir.breakthroughs(session, own=True)
    differences = breaks.get("differences", [])
    pubs = _recent_own(session, recent)
    if not pubs:
        return {"findings": [], "publications": [], "recent": 0,
                "summary": {"why": "No publications with a date."},
                "how_to_read": _how_to_read()}

    ids = [p["video_id"] for p in pubs]
    values = vir._feature_values(session, ids)

    findings: list[dict] = []
    # Which findings each publication follows, filled in as we go so
    # the per-video view and the per-finding view cannot disagree.
    per_video: dict[int, dict[str, list[str]]] = {
        p["video_id"]: {"follows": [], "misses": [], "unmeasured": []}
        for p in pubs}

    for d in differences:
        feature = d["feature"]
        hi, lo = d["outliers_mean"], d["others_mean"]
        # The line, named as the arbitrary thing it is.
        threshold = (hi + lo) / 2.0
        wants_higher = d["direction"] == "higher"
        seen = values.get(feature, {})

        followed = measured = 0
        for p in pubs:
            v = seen.get(p["video_id"])
            name = d["display_name"]
            if v is None:
                per_video[p["video_id"]]["unmeasured"].append(name)
                continue
            measured += 1
            ok = v >= threshold if wants_higher else v <= threshold
            if ok:
                followed += 1
                per_video[p["video_id"]]["follows"].append(name)
            else:
                per_video[p["video_id"]]["misses"].append(name)

        mine = sorted(seen[p["video_id"]] for p in pubs
                      if seen.get(p["video_id"]) is not None)
        findings.append({
            "feature": feature,
            "display_name": d["display_name"],
            "describes": d.get("describes", ""),
            "direction": d["direction"],
            "breakout_level": hi,
            "usual_level": lo,
            "threshold": round(threshold, 4),
            # The median, not the mean. The count is what the verdict
            # rests on, and a skewed mean contradicts it: colour
            # intensity first showed an average above the line beside
            # "7 of 9 below it", which reads as a bug whether or not
            # it is one. The median cannot disagree with the count.
            "your_recent_typical": _median(mine),
            "followed": followed, "measured": measured,
            "unmeasured": len(pubs) - measured,
            "share": round(followed / measured, 3) if measured else None,
            "verdict": _verdict(followed, measured),
            "separates": _separates(mine, hi, lo, wants_higher),
            "grade": d.get("grade", "Observed"),
            "plain": _plain(d, followed, measured, len(pubs),
                            _separates(mine, hi, lo, wants_higher)),
        })

    findings.sort(key=lambda f: (f["verdict"] != "not following",
                                 f["share"] if f["share"] is not None else 2))

    publications = []
    for p in pubs:
        tally = per_video[p["video_id"]]
        publications.append({
            **p,
            "posted_at": (p["posted_at"].isoformat()
                          if isinstance(p["posted_at"], datetime)
                          else p["posted_at"]),
            "follows": tally["follows"], "misses": tally["misses"],
            "unmeasured": tally["unmeasured"],
            "n_follows": len(tally["follows"]),
            "n_checked": len(tally["follows"]) + len(tally["misses"]),
        })

    not_following = [f for f in findings if f["verdict"] == "not following"]
    following = [f for f in findings
                 if f["verdict"] == "following" and f["separates"]]
    settled = [f for f in findings if not f["separates"]]
    return {
        "recent": len(pubs),
        "findings": findings,
        "publications": publications,
        "summary": {
            "checked": len(findings),
            "following": len(following),
            "not_following": len(not_following),
            "no_longer_separates": len(settled),
            "headline": _headline(findings, not_following, settled,
                                  len(pubs)),
            "ignored": [f["display_name"] for f in not_following],
            "settled": [f["display_name"] for f in settled],
        },
        "how_to_read": _how_to_read(),
    }


def _plain(d: dict, followed: int, measured: int, total: int,
           separates: bool) -> str:
    name = lex.display_name(d["feature"]).lower()
    more = "more" if d["direction"] == "higher" else "less"
    had = f"Your publications that broke out had {more} {name}."
    if not measured:
        return (f"{had} None of your last {total} have been measured "
                f"for it.")
    if not separates:
        return (f"{had} All {measured} of your recent measured "
                f"publications are already past that level, so this "
                f"no longer tells them apart - whatever separates "
                f"them now, it is not this.")
    return (f"{had} {followed} of your last {measured} measured "
            f"publications are on that side of the line.")


def _headline(findings: list[dict], not_following: list[dict],
              settled: list[dict], total: int) -> str:
    if not findings:
        return ("Nothing to check yet: no differences have separated "
                "your breakthroughs from your other publications.")
    if not_following:
        names = ", ".join(f["display_name"].lower()
                          for f in not_following[:3])
        return (f"Your recent publications go against "
                f"{len(not_following)} of the {len(findings)} "
                f"differences measured on your own breakthroughs - "
                f"{names}.")
    if settled:
        # The result on this account the first time it ran: three of
        # four findings cleared by everything. A page saying "broadly
        # in line" there would be flattery, which is the specific way
        # a page like this fails.
        left = len(findings) - len(settled)
        spent = (f"{len(settled)} of the {len(findings)} differences "
                 f"no longer separate your recent publications - you "
                 f"are already past them on every one that has been "
                 f"measured.")
        if not left:
            return (f"{spent} That is not a pass mark. These findings "
                    f"have stopped being the useful question, and "
                    f"what distinguishes your recent publications "
                    f"from each other has not been measured yet.")
        return (f"{spent} {left} still tells them apart; that is the "
                f"one worth watching.")
    return (f"Your last {total} publications are broadly in line with "
            f"what your breakthroughs did.")


def _how_to_read() -> dict:
    return {
        "threshold": (
            "A publication counts as following a finding when it sits "
            "closer to your breakthrough level than to your ordinary "
            "level. That midpoint is an arbitrary line and is shown "
            "for every finding so you can disagree with it."),
        "not_a_score": (
            "There is no total. The findings overlap - overall visual "
            "detail and background visual detail measure much the "
            "same thing - so adding them up would count one choice "
            "several times and call it a percentage."),
        "not_causal": (
            "These are differences that were there, not causes. "
            "Matching all of them is not a prediction, and a "
            "publication that matches none of them may still do "
            "well. Only the experiment loop can say what changing "
            "something does."),
        "unmeasured": (
            "A publication with no vision analysis is unmeasured, not "
            "failing. It counts towards neither side."),
    }
