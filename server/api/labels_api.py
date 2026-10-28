"""The twenty-one labels worth a human verdict, and a way to give it.

## Why this endpoint is the most valuable one in the vision stack

Every vision benchmark in this project has been scored against labels
Claude wrote. On 2026-10-27 Claude Sonnet answered 40 of 40 own-account
publications correctly - the best result the project has produced -
and that is when the problem stopped being a footnote. A perfect score
against an answer key written by another Claude model measures
agreement between two models at least as much as it measures either
being right.

Twenty-one labels were flagged genuinely ambiguous when they were
written: a grand piano side-on where the keys may sit at the frame
edge, notation composited in as a graphic rather than paper on a
stand, faces a few pixels across in a wide stage shot. Those are
exactly the cases where a human verdict is worth most, and 18
publications is a quarter of an hour's work rather than a project.

## Two kinds of evidence, and the difference is recorded

Own-account media is preserved, so those eight are reviewed from **the
same four frames the model saw** - a disagreement is then about
judgement and not about having looked at different things.

Competitor media was deleted under ADR 0009, so those ten are reviewed
from **the live post**. That is better evidence in one sense, the whole
video rather than four frames, and different evidence in another, and
`reviewed_from` records which it was rather than letting the two blur.

## What a verdict may be

`confirmed`, `overturned`, or `cannot_tell`. The third is not a
cop-out: "a human could not tell either" says the question is
ambiguous rather than that the model was wrong, which is a different
and useful fact. Forcing a binary would manufacture certainty.

The model's label is kept whatever the owner decides. Overwriting it
would destroy the record of how often the labelling process needed
correcting, which is the thing the next benchmark depends on.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from server.api.deps import get_session
from server.api.ui import require_ui, require_ui_write
from server.collectors import permalink
from server.db.models import Creator, LabelReview, Video

logger = logging.getLogger("virallab.api.labels")

router = APIRouter(prefix="/api/labels", tags=["labels"])

FIXTURES = (Path(__file__).resolve().parents[2]
            / "tests" / "fixtures" / "vision")
SOURCES = (("own", "holdout_labels_own.json"),
           ("competitor", "holdout_labels_competitor.json"))

#: Plain-English form of each question. The RULE is not written
#: here - it is read from the fixture, because the fixture is what
#: the labels were actually made under and a second copy would drift.
QUESTIONS: dict[str, str] = {
    "piano_or_keyboard_visible": "Can you see the piano keys?",
    "hands_visible": "Are the performer's hands visible?",
    "face_visible": "Is a face visible?",
    "sheet_music_visible": "Is sheet music visible?",
    "text_overlay_present": "Is there text added on top of the video?",
    "performer_visible": "Is the performer in shot at all?",
    "setting": "Where does this appear to be?",
    "shot_distance": "How close is the camera?",
}


#: Definitions the fixture does not carry. `setting` and
#: `shot_distance` were labelled from the task enum alone with no
#: written rule, which is how publication 403 - a public piano in a
#: railway concourse - ended up with no value and the owner's
#: complaint that "the enum range is so restricted". Supplied here and
#: marked as supplied, rather than left blank for the reviewer to
#: invent.
SUPPLIED_DEFINITIONS: dict[str, str] = {
    "setting": ("Where the video appears to have been shot. "
                "`public_space` covers a station concourse, a street "
                "or a shopping centre - it was added after this very "
                "publication had to be left blank for want of it. "
                "Choose the closest; if none fits, say you cannot "
                "tell rather than forcing one."),
    "shot_distance": ("How much of the subject fills the frame, over "
                      "the frames you are shown. Extreme close is "
                      "hands or a face filling it; very wide is a "
                      "whole stage."),
}


def _rulebook() -> tuple[str, dict[str, str]]:
    """The scope rule and the per-field definitions, from the fixture.

    **This is the fix for the first review pass.** The page showed a
    paraphrase of each question and never showed the rule the labels
    were actually made under - "a field is true if the thing is
    visible in ANY of the four frames". The owner's note on
    publication 91 is the proof of what that cost: "we can see the
    keys only in the last frame, no keys at all on the previous
    ones", filed as agreeing that no keys were visible. Under the
    stated rule that publication is TRUE, and the label is wrong by
    its own definition.

    Read rather than restated, so the rule a reviewer is held to is
    the rule the data was built with.
    """
    rule, defs = "", {}
    for _cohort, name in SOURCES:
        p = FIXTURES / name
        if not p.exists():
            continue
        d = json.loads(p.read_text(encoding="utf-8"))
        rule = rule or d.get("_rule", "")
        for k, v in (d.get("_definitions") or {}).items():
            defs.setdefault(k, v)
    for k, v in SUPPLIED_DEFINITIONS.items():
        defs.setdefault(k, v)
    return rule, defs


def _uncertain() -> list[dict]:
    """The flagged-ambiguous entries, from the committed fixtures."""
    out: list[dict] = []
    for cohort, name in SOURCES:
        p = FIXTURES / name
        if not p.exists():
            logger.warning("label fixture missing: %s", p)
            continue
        d = json.loads(p.read_text(encoding="utf-8"))
        labels = d.get("labels", {})
        for u in d.get("_uncertain", []):
            vid = int(u["video_id"])
            field = u["field"]
            model_value = labels.get(str(vid), {}).get(field)
            out.append({
                "video_id": vid, "field": field, "cohort": cohort,
                "why_uncertain": u.get("why", ""),
                "model_label": (None if model_value is None
                                else str(model_value).lower()),
                # Not a gap. Publication 403 is a public piano in a
                # railway concourse and the `setting` vocabulary has
                # no value for it, so the label was deliberately
                # OMITTED rather than forced into the nearest wrong
                # box. The reviewer is asked a different question
                # here - whether leaving it blank was right - and the
                # interface must not print "null" at them.
                "model_omitted": model_value is None,
            })
    return out


#: Boolean questions take one of these; enum questions take a value
#: from their own vocabulary.
YES, NO, UNSURE = "yes", "no", "cannot_tell"

#: Fields whose answer is a value rather than a yes or no.
ENUM_FIELDS = ("setting", "shot_distance")

#: The vocabularies, served to the client so they live in one place.
#: `setting` is deliberately wider than the one the model was given:
#: the owner's note on publication 403 - "public piano in a railway
#: concourse, I don't like that the enum range is so restricted" - is
#: a finding about the vocabulary, and the review screen should not
#: force the same bad choice that caused it.
_SETTING_VALUES = ("concert_hall", "studio", "home_room", "outdoor",
                   "stage", "plain_backdrop", "church", "rehearsal_room",
                   "backstage", "public_space")
_SHOT_VALUES = ("extreme_close", "close", "medium", "wide", "very_wide")


class Answer(BaseModel):
    """What the reviewer says the answer IS.

    The first version of this asked for a *verdict* - confirm or
    overturn - while deliberately hiding the model's label so it could
    not anchor the judgement. Those two decisions contradict each
    other: you cannot agree or disagree with something you have not
    been shown, and the owner's first pass proved it. Four of eight
    "overturned" answers gave exactly what the model had said, and
    three "confirmed" ones carried notes describing the opposite of
    what was confirmed.

    So the question is now the only one a reviewer can actually
    answer - what do you see? - and agreement is computed.
    """

    answer: str = Field(max_length=32)
    note: str | None = Field(default=None, max_length=2000)


def _normalise(answer: str) -> str:
    a = (answer or "").strip().lower()
    if a in ("yes", "y", "true", "1"):
        return YES
    if a in ("no", "n", "false", "0"):
        return NO
    if a in ("cannot_tell", "cant_tell", "unsure", "?"):
        return UNSURE
    return a


def derive_verdict(model_label: str | None, answer: str) -> str:
    """Agreement, computed rather than asserted.

    `model_label` of None means the label was deliberately omitted -
    publication 403's railway concourse, which the `setting`
    vocabulary has no value for. Agreeing with an omission means
    answering `cannot_tell`; supplying a value overturns it.
    """
    a = _normalise(answer)
    if a == UNSURE:
        return "cannot_tell" if model_label is not None else "confirmed"
    if model_label is None:
        return "overturned"
    m = _normalise(str(model_label))
    return "confirmed" if a == m else "overturned"


@router.get("/review", dependencies=[Depends(require_ui)])
def review_queue(session: Session = Depends(get_session)) -> dict:
    """Every ambiguous label, with whatever can be shown of its video."""
    items = _uncertain()
    ids = sorted({i["video_id"] for i in items})
    videos = {v.id: v for v in session.scalars(
        select(Video).where(Video.id.in_(ids))).all()} if ids else {}
    creators = {c.id: c for c in session.scalars(
        select(Creator).where(Creator.id.in_(
            {v.creator_id for v in videos.values()}))).all()}
    done = {(r.video_id, r.field): r for r in session.scalars(
        select(LabelReview).where(LabelReview.video_id.in_(ids))).all()
    } if ids else {}

    scope_rule, definitions = _rulebook()
    out = []
    for it in items:
        v = videos.get(it["video_id"])
        c = creators.get(v.creator_id) if v else None
        prior = done.get((it["video_id"], it["field"]))
        ask = QUESTIONS.get(it["field"], it["field"])
        # Own media is preserved, so those can be judged on exactly the
        # frames the model saw. The competitor half cannot - ADR 0009
        # deleted it - and the live post is the honest substitute.
        has_frames = bool(v and v.media_status == "available"
                          and v.media_path)
        out.append({
            **it,
            "ask": (ask if not it["model_omitted"]
                    else f"The model left this blank. Was that right? "
                         f"({ask})"),
            "rule": definitions.get(it["field"], ""),
            # The sentence whose absence caused the first pass to go
            # wrong. Shown on every question, not buried in a help
            # page.
            "scope_rule": scope_rule,
            "caption": (v.caption or "")[:200] if v else "",
            "platform": v.platform if v else None,
            "permalink": permalink.for_video(v, c) if v else None,
            "permalink_missing_because": (
                permalink.why_missing(v, c) if v else "no such publication"),
            "frames_available": has_frames,
            "review_from": "frames" if has_frames else "live_post",
            "already_reviewed": bool(prior and prior.verdict),
            "your_verdict": prior.verdict if prior else None,
            "your_answer": prior.owner_label if prior else None,
            "your_note": prior.note if prior else None,
            # The answers this question accepts. A yes/no question and
            # a "where is this" question cannot share a set of
            # buttons, and guessing in the client would put the
            # vocabulary in two places.
            "choices": (list(_SETTING_VALUES) if it["field"] == "setting"
                        else list(_SHOT_VALUES)
                        if it["field"] == "shot_distance"
                        else [YES, NO]) + [UNSURE],
        })

    answered = sum(1 for o in out if o["already_reviewed"])
    return {
        "total": len(out),
        "answered": answered,
        "remaining": len(out) - answered,
        "publications": len({o["video_id"] for o in out}),
        "why_this_matters": (
            "Every vision benchmark here was scored against labels "
            "Claude wrote. The strongest result so far - 40 of 40 - was "
            "measured against an answer key written by another Claude "
            "model, which measures agreement as much as correctness. "
            "These are the labels Claude itself flagged as genuinely "
            "ambiguous, so a human verdict is worth most here."),
        "two_kinds_of_evidence": (
            "Your own videos are reviewed from the same four frames the "
            "model saw. Competitor media was deleted after measurement, "
            "so those are judged from the live post - more of the video, "
            "but not the same evidence, and the record says which."),
        "items": out,
    }


@router.put("/review/{video_id}/{field}",
            dependencies=[Depends(require_ui_write)])
def record_answer(video_id: int, field: str, body: Answer,
                  session: Session = Depends(get_session)) -> dict:
    """Record what the reviewer saw, and compute whether it agrees.

    The reviewer is never asked to agree or disagree. They are asked
    what the answer is; `derive_verdict` does the comparison. That is
    the whole correction from the first version, and it is why the
    model's label can stay hidden until afterwards without making the
    question unanswerable.
    """
    known = {(i["video_id"], i["field"]): i for i in _uncertain()}
    item = known.get((video_id, field))
    if item is None:
        # Only the flagged set is reviewable here. Accepting arbitrary
        # (video, field) pairs would let the holdout be edited through
        # a review screen, which is a different and much larger thing.
        raise HTTPException(
            404, f"{field} on publication {video_id} is not one of the "
                 f"flagged-ambiguous labels")

    answer = _normalise(body.answer)
    if field not in ENUM_FIELDS and answer not in (YES, NO, UNSURE):
        raise HTTPException(
            400, f"{field} is a yes/no question; got {body.answer!r}")
    if not answer:
        raise HTTPException(400, "an answer is required")

    v = session.get(Video, video_id)
    if v is None:
        raise HTTPException(404, f"no publication {video_id}")
    has_frames = bool(v.media_status == "available" and v.media_path)

    row = session.scalar(select(LabelReview).where(
        LabelReview.video_id == video_id, LabelReview.field == field))
    if row is None:
        row = LabelReview(video_id=video_id, field=field,
                          cohort=item["cohort"])
        session.add(row)
    row.why_uncertain = item["why_uncertain"]
    # Never overwritten by an answer: how often the labelling process
    # needed correcting is the measurement this whole exercise is for.
    row.model_label = item["model_label"]
    row.owner_label = answer
    row.verdict = derive_verdict(item["model_label"], answer)
    row.note = body.note
    row.reviewed_from = "frames" if has_frames else "live_post"
    row.reviewed_at = datetime.now(timezone.utc)
    session.commit()

    total = len(known)
    answered = session.scalar(
        select(func.count(LabelReview.id))
        .where(LabelReview.verdict.is_not(None))) or 0
    return {"saved": True, "video_id": video_id, "field": field,
            "you_said": answer, "model_said": item["model_label"],
            "agreement": row.verdict,
            "answered": answered, "total": total,
            "remaining": max(0, total - answered)}


@router.get("/review/summary", dependencies=[Depends(require_ui)])
def summary(session: Session = Depends(get_session)) -> dict:
    """What the review has established so far.

    The number that matters is `overturned`: it is the measured error
    rate of the labelling process on its own hardest cases, and it is
    the first independent evidence about the answer key that every
    vision benchmark has been scored against.
    """
    rows = list(session.scalars(select(LabelReview)).all())
    done = [r for r in rows if r.verdict]
    by = {"confirmed": 0, "overturned": 0, "cannot_tell": 0}
    for r in done:
        by[r.verdict] = by.get(r.verdict, 0) + 1
    total = len(_uncertain())
    return {
        "total": total, "answered": len(done),
        "remaining": max(0, total - len(done)),
        "by_verdict": by,
        "overturned_examples": [
            {"video_id": r.video_id, "field": r.field,
             "model_said": r.model_label, "you_said": r.owner_label,
             "note": r.note}
            for r in done if r.verdict == "overturned"],
        "what_this_establishes": (
            "How often the labelling needed correcting, on the cases it "
            "already knew were hard. It does not generalise to the "
            "labels nobody flagged, and it is not a correction to any "
            "published benchmark - those stay as published until a "
            "holdout is labelled with this anchoring from the start."
            if done else
            "Nothing yet. No verdict has been recorded."),
    }


@router.get("/frames/{video_id}", dependencies=[Depends(require_ui)])
def review_frames(video_id: int,
                  session: Session = Depends(get_session)) -> dict:
    """The exact frames the model saw, for the reviewer to judge from.

    Separate from `/api/frames/{id}`, which authenticates a *worker*
    bearer token and cannot be called from a browser session. Rather
    than widen that endpoint's auth, this one is scoped to the single
    job it has: own-account publications that are already in the
    flagged-ambiguous set.

    **Own account only, and the restriction is structural.** The
    competitor half of the holdout had its media deleted under ADR
    0009 and must not be re-acquired through a review screen; those
    are reviewed from the live post instead. A check on
    `is_own_account` is what stops this becoming a general media
    endpoint by increment.
    """
    if not any(i["video_id"] == video_id for i in _uncertain()):
        raise HTTPException(
            404, "frames are served here only for publications in the "
                 "flagged-ambiguous set")
    video = session.get(Video, video_id)
    if video is None:
        raise HTTPException(404, "no such publication")
    creator = session.get(Creator, video.creator_id)
    if creator is None or not creator.is_own_account:
        raise HTTPException(
            403, "third-party media is not retained and is reviewed "
                 "from the live post, not from frames")
    if video.media_status != "available" or not video.media_path:
        raise HTTPException(409, "no preserved file for this publication")

    path = Path(video.media_path)
    if not path.exists():
        raise HTTPException(409, "the preserved file is missing on disk")

    from server.ai import vision_task
    from server.api.frames_api import MAX_FRAMES, encode_frames

    stamps = vision_task.FramePlan().timestamps(video.duration_s)[:MAX_FRAMES]
    try:
        frames = encode_frames(path, stamps, width=512)
    except Exception as exc:                   # noqa: BLE001 - reported
        logger.exception("frame decode failed for %s", video_id)
        raise HTTPException(500, f"could not decode frames: {exc}") from exc
    return {
        "video_id": video_id,
        "note": ("These are the frames the model was given - the same "
                 "four images, at the same width. A disagreement is "
                 "then about judgement rather than about having looked "
                 "at different things."),
        "frames": [{"at_s": f["at_s"], "jpeg_b64": f["jpeg_b64"]}
                   for f in frames],
    }
