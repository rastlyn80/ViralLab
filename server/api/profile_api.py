"""What the owner's videos actually look like, across the whole library.

The 2026-10-28 wide run described 483 own publications against 31
questions and produced 15,445 measurements. Until this endpoint
existed none of it was visible: every page in the interface reads a
different method version, so the data sat in a table nobody queried
and the product was exactly as vague as it had been the day before.

This is the surface for it, and it answers one question - *what are
my videos like?* - rather than *what works*. Those are different
questions and conflating them is how a description becomes a claim.

## Grades are shown, not implied

Every answer carries the reliability of the measurement behind it:

* **verified** - checked against labels it was not tuned on. Two
  questions hold this.
* **indicative** - the model's reading, shown because it is worth
  seeing, never counted toward a statistic. Most of the library.
* **unverified** - measured and failed. Not shown at all, and
  `performer_visible` and `shot_distance` are here because the wide
  run itself showed them saturating.

The distinction is not decoration. "54.7% of your videos show a
face" rests on a measurement that cleared a pre-registered bar;
"35.4% are warmly lit" rests on a model's opinion nobody has
checked. Printing both in the same typeface without saying which is
which would be the most quietly dishonest thing this product could
do.

## What it deliberately does not do

No outcomes. No "and these do better". Everything here is a
description of what was made, and the moment a count sits next to a
performance number a reader will connect them. That connection is
what `/api/opportunities` is for, where it arrives with a grade, a
q-value and a list of counterexamples.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from server.ai import vision_questions as vq
from server.ai import wide_vision as wv
from server.api.deps import get_session
from server.api.ui import require_ui
from server.db.models import Creator, Video, VideoFeature
from server.features import lexicon as lex

logger = logging.getLogger("virallab.api.profile")

router = APIRouter(prefix="/api/visual-profile", tags=["profile"])

#: Below this many answers a proportion is noise dressed as a fact.
MIN_ANSWERS = 20


def _own_ids(session: Session) -> set[int]:
    return set(session.scalars(
        select(Video.id).join(Creator, Creator.id == Video.creator_id)
        .where(Creator.is_own_account.is_(True))).all())


@router.get("", dependencies=[Depends(require_ui)])
def visual_profile(session: Session = Depends(get_session)) -> dict:
    """Every described question, grouped, with its grade."""
    described = session.scalar(
        select(func.count(func.distinct(VideoFeature.video_id)))
        .where(VideoFeature.method_version == wv.METHOD_VERSION,
               VideoFeature.status == "ok")) or 0

    # Booleans: how many yes, out of how many answered.
    bool_rows = session.execute(
        select(VideoFeature.feature,
               func.count(VideoFeature.id),
               # A filtered count, not a cast sum: `func.cast(..., 
               # type_=None)` compiles to NullType and SQLAlchemy
               # refuses it, and counting the trues directly says
               # what is meant anyway.
               func.count(VideoFeature.id).filter(
                   VideoFeature.value_bool.is_(True)))
        .where(VideoFeature.method_version == wv.METHOD_VERSION,
               VideoFeature.status == "ok",
               VideoFeature.value_bool.is_not(None))
        .group_by(VideoFeature.feature)).all()
    yes_by: dict[str, tuple[int, int]] = {
        f: (int(y or 0), int(n or 0)) for f, n, y in bool_rows}

    # Enums and free text: the distribution.
    text_rows = session.execute(
        select(VideoFeature.feature, VideoFeature.value_text,
               func.count(VideoFeature.id))
        .where(VideoFeature.method_version == wv.METHOD_VERSION,
               VideoFeature.status == "ok",
               VideoFeature.value_text.is_not(None))
        .group_by(VideoFeature.feature, VideoFeature.value_text)).all()
    dist: dict[str, list[tuple[str, int]]] = {}
    for feature, value, n in text_rows:
        dist.setdefault(feature, []).append((value, int(n)))

    groups = []
    for title, questions in vq.GROUPS.items():
        items = []
        for q in questions:
            key = f"vision_{q.name}"
            grade = wv.reliability_of(key)
            if grade == "unverified":
                # Not shown. `performer_visible` answered yes on 475
                # of 483 and `shot_distance` put 94% into two
                # adjacent buckets - the wide run is what withheld
                # them, and printing them anyway would undo that.
                continue
            entry = {
                "feature": key, "asks": q.asks,
                "display_name": lex.display_name(key),
                "reliability": grade, "basis": q.basis,
                "interpretive": q.interpretive,
            }
            if key in yes_by:
                y, n = yes_by[key]
                if n < MIN_ANSWERS:
                    continue
                entry |= {"kind": "yes_no", "yes": y, "answered": n,
                          "share": round(y / n, 3)}
            elif key in dist:
                vals = sorted(dist[key], key=lambda kv: -kv[1])
                total = sum(n for _v, n in vals)
                if total < MIN_ANSWERS:
                    continue
                entry |= {"kind": "choice", "answered": total,
                          "values": [{"value": v,
                                      "label": v.replace("_", " "),
                                      "n": n,
                                      "share": round(n / total, 3)}
                                     for v, n in vals[:8]]}
            else:
                continue
            items.append(entry)
        if items:
            groups.append({"title": title, "items": items})

    counts = {}
    for g in groups:
        for it in g["items"]:
            counts[it["reliability"]] = counts.get(it["reliability"], 0) + 1

    return {
        "publications_described": described,
        "method_version": wv.METHOD_VERSION,
        "questions_shown": sum(len(g["items"]) for g in groups),
        "by_reliability": counts,
        "groups": groups,
        "how_to_read": {
            "this_is_description": (
                "What your videos are, not what works. Nothing on this "
                "page is connected to how any of them performed, and "
                "the moment a count sits beside a performance number a "
                "reader connects them whether or not the connection "
                "holds."),
            "verified": (
                "Checked against labels it was not tuned on and cleared "
                "a bar set in advance. Two questions hold this."),
            "indicative": (
                "The model's reading. Worth seeing, and never counted "
                "toward a statistic - no finding anywhere in this "
                "product rests on one."),
            "withheld": (
                "Two questions are collected and deliberately not shown: "
                "'is the performer in shot' answered yes on 475 of 483, "
                "and 'how close is the camera' put 94% of your videos "
                "into two adjacent buckets. Both are the signature of a "
                "model not looking, so neither is reported."),
        },
    }


@router.get("/publication/{video_id}", dependencies=[Depends(require_ui)])
def publication_profile(video_id: int,
                        session: Session = Depends(get_session)) -> dict:
    """One publication's description, including what the model wrote.

    The frame descriptions are the most human-readable thing this
    pipeline produces. A reader who disagrees with an answer can see
    what the model thought it was looking at, which is the whole
    reason they are stored.
    """
    rows = list(session.scalars(
        select(VideoFeature).where(
            VideoFeature.video_id == video_id,
            VideoFeature.method_version == wv.METHOD_VERSION,
            VideoFeature.status == "ok")).all())
    if not rows:
        return {"video_id": video_id, "described": False,
                "why": ("This publication has not been described by the "
                        "wide vision pass. It covers own-account "
                        "publications whose media is still on disk.")}

    descriptions, answers = [], []
    for r in rows:
        if r.feature.endswith("frame_descriptions"):
            descriptions = (r.value_json or {}).get("items", [])
            continue
        grade = wv.reliability_of(r.feature)
        if grade == "unverified":
            continue
        value = (r.value_bool if r.value_bool is not None
                 else r.value_num if r.value_num is not None
                 else r.value_text)
        if value is None:
            continue
        q = vq.by_name(r.feature[len("vision_"):])
        answers.append({
            "feature": r.feature, "asks": q.asks if q else r.feature,
            "display_name": lex.display_name(r.feature),
            "value": value,
            "label": (value.replace("_", " ")
                      if isinstance(value, str) else value),
            "reliability": grade,
        })
    answers.sort(key=lambda a: (a["reliability"] != "verified",
                                a["display_name"]))
    return {
        "video_id": video_id, "described": True,
        "frame_descriptions": descriptions,
        "answers": answers,
        "note": ("These are the model's own words about the frames it "
                 "was shown, kept so a reader who disagrees with an "
                 "answer can see what it thought it was looking at."),
    }
