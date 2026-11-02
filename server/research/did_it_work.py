"""Did the advice work? Asked properly, which mostly means saying no.

The suggestions table was built with a `used_video_id` column and a
comment claiming that once a suggestion has a publication attached,
"did our advice work" becomes a query rather than a question. It was
half true. The column existed and nothing read it, so the claim was
a promise.

This is the query. For every suggestion the owner marked as posted,
it scores that publication against **its own creator's rolling
baseline** - the same within-creator measure the rest of the project
uses - and counts how many came out above typical.

## Why this is not the experiment

It is tempting to read a good number here as evidence the product
works. It is not, and the reason is in the data's shape rather than
in its size:

* **He chose which suggestions to use.** A suggestion he liked
  enough to film is not a random draw from the ones generated. If
  his taste is any good, the used ones would beat the baseline
  whether or not the generator helped.
* **He chose when to post them.** Nothing holds the rest of the
  account constant.
* **There is no control arm.** The comparison is against his own
  earlier publications, not against the videos he would have made
  anyway.

So a positive result here means "the suggestions he chose to use did
better than his usual", which is a fact about his choosing as much
as about the advice. Only M9's pre-registered experiment, with its
random allocation, can separate the two. Everything here is graded
Observed and says so.

What it is genuinely good for is the negative case. If the used
suggestions land at or below his baseline, that is bad news which
survives the self-selection objection: he picked the ones he
believed in and they still did not beat his usual. The asymmetry is
real, and it is the reason to build this before the experiment
rather than after.

## Why the sign test is the right test

It compares against a 50% null, and that null is earned rather than
assumed. Scoring every one of the owner's publications against its
own trailing window puts 287 of 562 above typical - 51%, which is
what a measure centred on the local median should do. So "beat your
usual" really is a coin flip for a publication drawn at random from
his run, and a count of heads is a fair question to ask of the
suggestions he used.

Measured 2026-11-01 against the live corpus, read-only, and worth
re-checking if the baseline window ever changes: if that figure
drifts away from a half, the test below is measuring the baseline
rather than the advice.

## The stopping rule, stated before there is data

`MIN_TO_SAY_ANYTHING` publications, and the sign test is two-sided.
Below that the module reports the count and refuses a verdict,
because four out of five is what a fair coin does one time in five.
Fixed here, in code, so it cannot be relaxed on the day the number
is nearly interesting.
"""

from __future__ import annotations

import logging
from math import comb

from sqlalchemy import select
from sqlalchemy.orm import Session

from server.db.models import Creator, MetricsSnapshot, Suggestion, Video

logger = logging.getLogger("virallab.research.did_it_work")

#: Below this, no verdict. See the docstring: the rule is written
#: before the data exists, which is the only time it means anything.
MIN_TO_SAY_ANYTHING = 10

#: A publication counts as having beaten its own run when its robust
#: z against the trailing window is above this. Zero, not the
#: outlier threshold: the question is "better than usual", not "a
#: breakout".
BEAT_AT_Z = 0.0


def _sign_test_p(up: int, down: int) -> float | None:
    """Two-sided exact binomial at p = 0.5. No scipy in this project.

    Same arithmetic as `matched_pairs._sign_test_p`. Written out
    again rather than imported: the two modules answer different
    questions and should not acquire a dependency on each other's
    internals for eight lines.
    """
    n = up + down
    if n == 0:
        return None
    k = min(up, down)
    tail = sum(comb(n, i) for i in range(k + 1)) / (2 ** n)
    return min(1.0, 2 * tail)


def _scored_own(session: Session) -> dict[int, float]:
    """Every own publication's robust z against its trailing window.

    The baseline is built from the creator's whole run, in published
    order, exactly as `virality.breakthroughs` does it. Scoring the
    used suggestions against only each other would ask a different
    and much noisier question.
    """
    from server.research.baseline import Observation, rolling_baseline

    rows = session.execute(
        select(Video.id, Video.creator_id, Video.external_id,
               Video.posted_at, MetricsSnapshot.views,
               MetricsSnapshot.captured_at)
        .join(Creator, Creator.id == Video.creator_id)
        .join(MetricsSnapshot, MetricsSnapshot.video_id == Video.id)
        .where(Creator.is_own_account.is_(True),
               MetricsSnapshot.views.is_not(None),
               Video.posted_at.is_not(None))
        .order_by(Video.id, MetricsSnapshot.captured_at.desc())).all()

    seen: set[int] = set()
    by_creator: dict[int, list[Observation]] = {}
    for vid, cid, ext, posted, views, captured in rows:
        if vid in seen:
            continue                       # latest snapshot only
        seen.add(vid)
        by_creator.setdefault(cid, []).append(Observation(
            video_id=vid, external_id=ext or str(vid),
            published_at=posted, views=int(views),
            observed_at=captured))

    out: dict[int, float] = {}
    for obs in by_creator.values():
        obs.sort(key=lambda o: o.published_at)
        for point in rolling_baseline(obs):
            vid = point.observation.video_id
            if vid is not None and point.robust_z is not None:
                out[vid] = float(point.robust_z)
    return out


def review(session: Session) -> dict:
    """What happened to the suggestions he said he posted."""
    rows = session.execute(
        select(Suggestion.id, Suggestion.kind, Suggestion.text,
               Suggestion.grounded_in, Suggestion.used_video_id,
               Suggestion.created_at, Video.external_id, Video.posted_at,
               Video.platform)
        .join(Video, Video.id == Suggestion.used_video_id)
        .where(Suggestion.used_video_id.is_not(None))
        .order_by(Suggestion.used_at.desc().nullslast(),
                  Suggestion.id.desc())).all()

    z_by_video = _scored_own(session) if rows else {}

    # Imported here rather than at module scope: `ai.creative`
    # pulls in the provider layer, and a research module should not
    # need one to be importable.
    from server.ai.creative import in_plain_words
    from server.collectors import permalink as pl

    # One lookup for the page. A suggestion that became a
    # publication should link to it: "did it work" is a question
    # you answer by watching the thing, not only by reading a z.
    urls = pl.bulk(session, [r[4] for r in rows]) if rows else {}

    used: list[dict] = []
    waiting: list[dict] = []
    for (sid, kind, text, grounded, vid, created, ext, posted,
         platform) in rows:
        entry = {
            "suggestion_id": sid, "kind": kind,
            "suggestion": (text or "")[:300],
            "grounded_in": in_plain_words(grounded),
            "video_id": vid, "external_id": ext,
            "platform": platform,
            "url": urls.get(vid),
            "posted_at": posted,
            "suggested_at": created,
        }
        z = z_by_video.get(vid)
        if z is None:
            # Not a failure. A publication needs enough earlier posts
            # on the same account before there is anything to compare
            # it against, and a brand new one has not been read often
            # enough either. Kept visible so the count of scored
            # suggestions is never quietly smaller than the count he
            # marked.
            entry["why_not_scored"] = (
                "Not enough earlier publications on that account to "
                "build a baseline for it yet, or no view count read "
                "since it went out.")
            waiting.append(entry)
            continue
        entry["robust_z"] = round(z, 3)
        entry["beat_your_usual"] = z > BEAT_AT_Z
        entry["plain"] = (
            f"This one did {'better' if z > BEAT_AT_Z else 'worse'} "
            f"than the run of publications before it on that account "
            f"({z:+.2f} robust deviations).")
        used.append(entry)

    up = sum(1 for e in used if e["beat_your_usual"])
    down = len(used) - up
    p = _sign_test_p(up, down)

    return {
        "scored": len(used),
        "beat_your_usual": up,
        "did_not": down,
        "waiting_for_a_baseline": waiting,
        "p_value": None if p is None else round(p, 4),
        "minimum_before_a_verdict": MIN_TO_SAY_ANYTHING,
        "verdict": _verdict(up, down, p),
        "used": used,
        "grade": "Observed",
        "why_this_is_not_proof": [
            "You chose which suggestions to film, so the ones counted "
            "here are the ones you believed in rather than a random "
            "draw. If your taste is good they would beat your usual "
            "whether or not the generator helped.",
            "You chose when to post them, and nothing holds the rest "
            "of the account steady.",
            "There is no control arm. The comparison is against your "
            "own earlier publications, not against the videos you "
            "would have made anyway.",
            "Only a pre-registered experiment with random allocation "
            "can separate the advice from your choosing. This is the "
            "weaker question, answered honestly.",
        ],
        "what_it_is_good_for": (
            "The bad news. If these land at or below your usual, that "
            "survives every objection above - you picked the ones you "
            "believed in and they still did not beat your run. A good "
            "result here is much weaker than a bad one."),
    }


def _verdict(up: int, down: int, p: float | None) -> str:
    n = up + down
    if not n:
        return ("Nothing to report yet. Mark a suggestion as posted "
                "under Kept, with the publication's id, and it will be "
                "scored against your own run once there is enough "
                "history around it.")
    if n < MIN_TO_SAY_ANYTHING:
        return (f"{up} of {n} did better than your usual. That is "
                f"counted and not judged: below {MIN_TO_SAY_ANYTHING} "
                f"publications a run like this is what chance does "
                f"often enough to mean nothing. The threshold was set "
                f"before there was any data.")
    if p is not None and p < 0.05:
        if up > down:
            return (f"{up} of {n} did better than your usual "
                    f"(p = {p:.3f}). That is more than chance would "
                    f"usually give - but you chose which to use, so it "
                    f"says your picking and the suggestions together "
                    f"beat your run, not that the suggestions did.")
        return (f"Only {up} of {n} did better than your usual "
                f"(p = {p:.3f}). This is the result that counts "
                f"against the advice: you chose the ones you believed "
                f"in and they still came out below your run.")
    return (f"{up} of {n} did better than your usual"
            + (f" (p = {p:.3f})" if p is not None else "")
            + ". Not separable from chance either way yet.")
