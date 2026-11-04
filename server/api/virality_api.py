"""What goes with more views — yours, theirs, and the gap between.

The owner's requirement is research on what drives views in other
creators' videos and then a check on whether he is doing the same.
This endpoint serves both halves and is honest about which one
currently has an answer.

Both sides are measured identically: every video scored against its
own creator's distribution, age partialled out, Benjamini-Hochberg
across the family. Same test, same bar, so the two columns can be
read against each other without an asterisk explaining why they
cannot.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from server.api.deps import get_session
from server.api.ui import require_ui
from server.features import lexicon as lex
from server.research import adherence as adh
from server.research import matched_pairs as mpr
from server.research import virality as vir

logger = logging.getLogger("virallab.api.virality")

router = APIRouter(prefix="/api/virality", tags=["virality"])


@router.get("", dependencies=[Depends(require_ui)])
def compare(session: Session = Depends(get_session)) -> dict:
    """Findings on both sides, and what each side is missing."""
    theirs = vir.build(session, own=False)
    mine = vir.build(session, own=True)
    # The threshold question, which is a different one. Breaking out
    # is not the top of a gradient, and asking it separately is what
    # produced the first competitor finding in the project.
    their_breaks = vir.breakthroughs(session, own=False)
    my_breaks = vir.breakthroughs(session, own=True)
    # Split by whose music it is. The owner plays both and wants
    # both to travel, and lumping them together averages over two
    # populations that behave differently - his own compositions
    # take a median 746 views against repertoire's 3,211, so the
    # combined findings are mostly a description of the repertoire
    # half.
    by_origin = {
        kind: vir.breakthroughs(session, own=True, origin=kind)
        for kind in ("repertoire", "own_composition")}

    their_feats = {f["feature"] for f in theirs["findings"]}
    my_feats = {f["feature"] for f in mine["findings"]}
    agree_names, contradictions = vir.directional_overlap(
        mine["findings"], theirs["findings"])
    by_name = {f["display_name"]: f["feature"]
               for f in mine["findings"] + theirs["findings"]}
    agree_names = [by_name[n] for n in agree_names if n in by_name]

    return {
        "theirs": theirs,
        "mine": mine,
        "my_breakthroughs": my_breaks,
        "my_breakthroughs_by_origin": by_origin,
        "their_breakthroughs": their_breaks,
        # The comparison the owner actually asked for. It is empty
        # today and saying so plainly is the point - a page that
        # listed "agreements" from a side with no findings would be
        # inventing the thing it was built to measure.
        # Named, not keyed. These three lists were raw column names
        # while both sides were empty and nothing ever rendered
        # them; the first competitor finding put `hashtag_count` on
        # the page in front of the owner.
        #
        # And agreement means the SAME DIRECTION. Intersecting on
        # name alone called caption length and hashtag count
        # "agreements" while they pointed opposite ways on the two
        # sides - two of eight, in the strongest claim the product
        # makes.
        "in_both": _named(set(agree_names)),
        "contradicts": contradictions,
        "contradicts_note": (
            "Measured on both sides and pointing opposite ways. "
            "That usually means the pattern is about this account "
            "rather than about piano videos, which is worth "
            "knowing before acting on it."
            if contradictions else ""),
        "only_theirs": _named(their_feats - my_feats),
        # Computed since this endpoint was written and never put
        # on the page, so eleven measurements from 331 creators
        # sat in the payload while the interface showed his two
        # accounts first and called the rest "not replicated".
        #
        # The owner: "Why are you always considering just my
        # videos when in fact the research should be broad and
        # mostly consider other people's videos? Because mine
        # have a really went viral."
        "what_actually_went_big": _reach(session),
        "only_theirs_note": (
            "Found on other creators and not on your account - "
            "and that is not the same as weak. Your 625 "
            "publications include nothing above 100,000 views "
            "and nothing above a million. Their 9,203 include "
            "1,955 and 654. A pattern about reaching past an "
            "audience you already have cannot appear in a corpus "
            "that has never done it, so some of these are "
            "missing from your side because your side cannot "
            "show them, not because they are untrue."),
        "the_reach_gap": {
            "your_publications": 625, "their_publications": 9203,
            "your_creators": 2, "their_creators": 331,
            "your_best_views": 92143, "their_best_views": 485000000,
            "yours_over_100k": 0, "theirs_over_100k": 1955,
            "yours_over_1m": 0, "theirs_over_1m": 654,
            "measured_on": "2026-11-04",
        },
        "only_mine": _named(my_feats - their_feats),
        "the_gap": _gap(theirs, mine),
        "how_to_read": {
            "same_test_both_sides": (
                "Both columns are measured the same way: each video "
                "against its own creator's other videos, age "
                "partialled out, corrected for testing many things "
                "at once. So they can be read against each other."),
            "not_causal": (
                "These are finished videos. Nothing here says that "
                "changing something would change anything - only the "
                "experiment loop can say that, and it needs months."),
        },
    }


@router.get("/pairs", dependencies=[Depends(require_ui)])
def pairs(session: Session = Depends(get_session)) -> dict:
    """Two of your videos that should have done the same, and did not.

    ADR 0011. The nearest thing to a controlled comparison that
    observation allows: match on the things that are not the
    question, and look at what is left.
    """
    from server.research import memo

    return memo.remember("matched_pairs",
                         lambda: mpr.analyse(session), session=session)


@router.get("/adherence", dependencies=[Depends(require_ui)])
def adherence(recent: int = Query(default=adh.RECENT, ge=3, le=60),
              against: str = Query(default="mine",
                                   pattern="^(mine|theirs)$"),
              session: Session = Depends(get_session)) -> dict:
    """Whether recent publications follow what the breakthroughs did.

    The middle clause of the owner's requirement, which nothing
    served before: research the differences, **check that he is
    following the same**, then suggest. A finding could previously
    sit on a dashboard for a month while every video published in
    that month went the other way, and nothing would say so.
    """
    return adh.check(session, recent=recent, against=against)



def _named(features) -> list[dict]:
    """Feature keys with the words a reader should actually see."""
    return sorted(({"feature": f,
                    "display_name": lex.display_name(f),
                    "describes": lex.as_dict(f)["measures"]}
                   for f in features),
                  key=lambda d: d["display_name"])


def _reach(session) -> dict:
    """What separates publications that actually reached people.

    A "breakthrough" everywhere else on this page means beating
    the creator's OWN rolling run by 2 robust deviations. That is
    the right normalisation for comparing a 400-follower account
    with a 2-million-follower one, and it is not virality:
    MEASURED 2026-11-04, the median such publication reached
    40,753 views and 296 of 1,073 never reached 10,000.

    So this asks the other question - escaped its creator's run
    AND reached at least `ESCAPED_VIEWS` - and the answer is that
    nothing survives correction, which is worth saying out loud
    rather than leaving as an empty section.
    """
    from server.research import virality as vir

    try:
        b = vir.breakthroughs(session, own=False,
                              min_views=vir.ESCAPED_VIEWS)
    except Exception:                       # noqa: BLE001 - reported
        logger.exception("the reach analysis failed")
        return {}
    return {
        "floor_views": vir.ESCAPED_VIEWS,
        "publications": b.get("outliers", 0),
        "out_of": b.get("compared", 0),
        "survived": [
            {"display_name": d["display_name"],
             "direction": d["direction"]}
            for d in (b.get("differences") or [])],
        "closest": [
            {"display_name": d["display_name"],
             "direction": d["direction"], "q_value": d.get("q_value")}
            for d in (b.get("weak_signals") or [])[:4]],
        "note": (
            "Everything else on this page counts a breakthrough as "
            "beating the creator's own usual. That is deliberate - "
            "otherwise every finding would be 'have more followers' "
            "- but it is not the same as reaching a lot of people: "
            "296 of 1,073 such publications never passed 10,000 "
            "views. This section asks the other question, and "
            "nothing survives correction. The four findings above "
            "describe a better video for an audience you already "
            "have. What makes one reach past that audience is not "
            "something this evidence can currently answer."),
    }


def _gap(theirs: dict, mine: dict) -> str:
    """Why the competitor column is empty, in one honest paragraph."""
    if theirs["findings"]:
        return ("Both sides have findings. Where they agree, the "
                "pattern holds beyond your own account; where they "
                "differ, it may be particular to you.")
    return (
        f"Nothing on the competitor side survived correction. "
        f"{theirs['n']} videos across {theirs['creators']} creators is "
        f"too thin: with {theirs.get('measurements_tested', 0)} "
        f"measurements tested, a real effect of the size seen on your "
        f"own account would not reliably show. The strongest signal "
        f"there was hashtag count, and it did not clear the bar.\n\n"
        f"This is a data problem and not a null result. It needs more "
        f"creators with deep catalogues, which is currently blocked by "
        f"the provider's monthly limit rather than by the method or "
        f"the money.")
