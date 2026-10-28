"""M9: the only place in this project a causal claim can be born.

Everything upstream measures what already happened. A correlation
over 483 publications, however carefully corrected, cannot say that
changing something would change anything - there is no version of
those videos that was made differently. An experiment can, and only
if three things hold:

1. **The prediction was fixed before the data existed.** Not "we
   expected something like this"; a direction, a sample size, a test
   and a stopping rule, written down and frozen.
2. **The stopping rule was honoured.** Looking early and continuing
   because it is nearly significant turns a null into a success
   story, and it is the easiest way to destroy the only causal
   evidence this project will produce. `read_out` refuses to compute
   anything before the pre-registered n is reached.
3. **The ad was not the treatment.** ADR 0008 and M1 between them
   established that a rotating $1/day promotion contaminates the
   outcome. An experiment whose arms were boosted differently
   measured the boost; `read_out` refuses to interpret one.

## What the owner actually does

`design` turns an M7 opportunity into a draft. `lock` freezes it and
draws the arm order - treatment or control for each of the next
2n publications, decided now so the owner is told what to make next
rather than sorting afterwards. Sorting afterwards is a post-hoc
comparison wearing an experiment's clothes.

Then the owner posts, fills each slot as they go, and when the last
one reaches its anchor age `read_out` gives a verdict against the
prediction - whichever way it went. A null result is a result and
is recorded as one.

## Feeding back

A surviving Associated pattern becomes Validated; a failing one is
marked and the opportunity that proposed it is retired. That is the
only route to `Validated` in the whole system, which is why
`strategist.py` can enforce "nothing causes anything" as an absolute
today and have it open by itself later.
"""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from server.db.models import (Creator, Experiment, ExperimentAssignment,
                              Video)
from server.features import lexicon as lex

logger = logging.getLogger("virallab.research.experiment")

#: Fields that constitute the prediction. Frozen at lock.
FROZEN = ("feature", "outcome", "platform", "predicted_direction",
          "prediction", "n_per_arm", "anchor_age_days", "ad_policy",
          "analysis", "alpha", "stopping_rule")

#: Instagram lags up to 48 hours, so a 7-day anchor is read at day 9.
#: CLAUDE.md 3.8a. Reading earlier measures the lag.
ANCHOR_GRACE_DAYS = 2


class NotReady(Exception):
    """The stopping rule has not been met. Deliberately an exception.

    Returning a partial result with a warning would mean the number
    existed, and a number that exists gets quoted. The only safe
    answer before the rule is met is no answer.
    """


class Contaminated(Exception):
    """The arms differ in something the design said they would not."""


@dataclass(frozen=True)
class Readout:
    verdict: str
    detail: dict

    def as_dict(self) -> dict:
        return {"verdict": self.verdict, **self.detail}


def design(*, title: str, feature: str, outcome: str, platform: str,
           predicted_direction: str, n_per_arm: int,
           opportunity_key: str | None = None,
           anchor_age_days: int = 7,
           ad_policy: str = "held_constant") -> Experiment:
    """A draft, not yet binding. Nothing is frozen until `lock`."""
    name = lex.display_name(feature).lower()
    more = "more" if predicted_direction == "lower" else "less"
    # The prediction is written out in full, because the readout
    # quotes it back and a paraphrase at that point would be the
    # experiment marking its own homework.
    prediction = (
        f"Publications whose openings carry deliberately {more} {name} "
        f"will show a {predicted_direction} "
        f"{lex.display_name(outcome).lower()} at "
        f"{anchor_age_days} days than publications in the other arm. "
        f"If the difference is in the other direction, or is not "
        f"distinguishable from none, the prediction is wrong.")
    stopping = (
        f"Stop at {n_per_arm} publications per arm, {n_per_arm * 2} in "
        f"total, and read the result once. Do not look before then and "
        f"do not continue past it because the answer is nearly "
        f"significant - that is how a null result becomes a success "
        f"story. If the run is abandoned, record why; an abandoned "
        f"experiment is not an inconclusive one.")
    return Experiment(
        title=title, opportunity_key=opportunity_key, feature=feature,
        outcome=outcome, platform=platform,
        predicted_direction=predicted_direction, prediction=prediction,
        n_per_arm=n_per_arm, anchor_age_days=anchor_age_days,
        ad_policy=ad_policy, analysis="mann_whitney_u", alpha=0.05,
        stopping_rule=stopping, status="draft")


def lock(session: Session, exp: Experiment, *,
         seed: int | None = None) -> list[ExperimentAssignment]:
    """Freeze the prediction and draw the arm order.

    The order is drawn NOW, before any of these videos exists, and
    the owner is told what to make next. Deciding afterwards which
    publications belonged to which arm is the thing pre-registration
    exists to prevent, and no amount of good faith substitutes for
    having written it down first.

    Blocks are of two - one treatment, one control, shuffled within
    the pair - so the arms stay balanced if the run is abandoned
    partway and so a drift in the owner's playing or the platform's
    behaviour hits both arms equally.
    """
    if exp.locked_at is not None:
        raise ValueError("already locked; a prediction is frozen once")
    rng = random.Random(seed if seed is not None
                        else int(datetime.now(timezone.utc).timestamp()))
    assignments: list[ExperimentAssignment] = []
    slot = 1
    for _ in range(exp.n_per_arm):
        pair = ["treatment", "control"]
        rng.shuffle(pair)
        for arm in pair:
            assignments.append(ExperimentAssignment(
                experiment=exp, slot=slot, arm=arm))
            slot += 1
    exp.status = "running"
    exp.locked_at = datetime.now(timezone.utc)
    session.add(exp)
    for a in assignments:
        session.add(a)
    return assignments


def assert_unchanged(exp: Experiment, before: dict) -> None:
    """The prediction has not moved since it was locked.

    Called by the readout. A frozen field that quietly changed would
    make the whole exercise worthless while leaving every page
    looking correct, so it is checked at the moment of reading
    rather than trusted.
    """
    for field in FROZEN:
        if field in before and getattr(exp, field) != before[field]:
            raise Contaminated(
                f"{field} changed after the experiment was locked: "
                f"{before[field]!r} -> {getattr(exp, field)!r}. The "
                f"prediction is no longer the one that was registered "
                f"and this run cannot produce a causal claim.")


def progress(session: Session, exp: Experiment) -> dict:
    """How far along, without revealing anything about the outcome.

    Deliberately returns no outcome values. A progress endpoint that
    leaked the running difference would be a peeking endpoint with a
    modest name.
    """
    rows = list(session.scalars(
        select(ExperimentAssignment)
        .where(ExperimentAssignment.experiment_id == exp.id)
        .order_by(ExperimentAssignment.slot)).all())
    filled = [r for r in rows if r.video_id is not None]
    needed = exp.n_per_arm * 2
    ready = _mature(session, exp, filled)
    nxt = next((r for r in rows if r.video_id is None), None)
    return {
        "experiment_id": exp.id, "status": exp.status,
        "slots": needed, "filled": len(filled),
        "mature_at_anchor": len(ready),
        "by_arm": {"treatment": sum(1 for r in filled
                                    if r.arm == "treatment"),
                   "control": sum(1 for r in filled
                                  if r.arm == "control")},
        "next_slot": ({"slot": nxt.slot, "arm": nxt.arm}
                      if nxt else None),
        "can_read_out": exp.status == "running" and len(ready) >= needed,
        "why_not": _why_not(exp, len(filled), len(ready), needed),
    }


def _why_not(exp: Experiment, filled: int, mature: int,
             needed: int) -> str | None:
    if exp.status == "complete":
        return "Already read out. A result is computed once."
    if exp.status == "abandoned":
        return "Abandoned. " + (exp.abandoned_reason or "")
    if filled < needed:
        return (f"{needed - filled} of {needed} publications still to "
                f"post. The result cannot be computed before then, and "
                f"the refusal is the stopping rule doing its job.")
    if mature < needed:
        return (f"{needed - mature} publications have not yet reached "
                f"{exp.anchor_age_days} days plus the "
                f"{ANCHOR_GRACE_DAYS}-day reporting lag. Reading them "
                f"early would compare different amounts of elapsed "
                f"exposure, not different videos.")
    return None


def _mature(session: Session, exp: Experiment,
            filled: list[ExperimentAssignment]) -> list:
    """Assignments whose publication has reached the anchor age.

    CLAUDE.md 3.8a: a 7-day anchor is read at day 9, because
    Instagram lags up to 48 hours. Comparing a 9-day-old video with
    a 3-day-old one measures elapsed exposure.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(
        days=exp.anchor_age_days + ANCHOR_GRACE_DAYS)
    out = []
    for a in filled:
        v = session.get(Video, a.video_id)
        if v is None or v.posted_at is None:
            continue
        posted = v.posted_at
        if posted.tzinfo is None:
            posted = posted.replace(tzinfo=timezone.utc)
        if posted <= cutoff:
            out.append(a)
    return out


def _outcome_values(session: Session, exp: Experiment,
                    rows: list) -> dict[str, list[float]]:
    from server.features.visual_structure import _outcome_rows
    outcomes = _outcome_rows(session)
    by_arm: dict[str, list[float]] = {"treatment": [], "control": []}
    for a in rows:
        row = outcomes.get(a.video_id)
        if row is None:
            continue
        val = row.get("skip_rate")
        if val is None:
            continue
        by_arm[a.arm].append(float(val))
    return by_arm


def _ad_balanced(session: Session, exp: Experiment,
                 rows: list) -> tuple[bool, str]:
    """Were the arms boosted the same way?

    `held_constant` means nothing was boosted. If anything was, the
    experiment compared a boosted arm with an unboosted one and
    measured the boost - which M1 exists because of.
    """
    from server.analytics import ad_exposure
    ids = [a.video_id for a in rows if a.video_id]
    if not ids:
        return True, "no publications to check"
    try:
        exposures = ad_exposure.resolve(session, platform=exp.platform,
                                        video_ids=ids)
    except Exception as exc:              # noqa: BLE001 - reported
        return False, f"ad exposure could not be resolved: {exc}"
    _src, vals = ad_exposure.homogeneous(exposures, ids)
    # `exposed_count` is the module's own notion of boosted, and it
    # applies a threshold. Treating any non-zero covariate as a boost
    # - which the first draft did - flagged every publication in a
    # run with no ads at all, because the winning covariate source is
    # a continuous measure whose zero is not "no ad".
    thr = ad_exposure.BOOSTED_SHARE_THRESHOLD
    exposed = {a.arm: 0 for a in rows}
    for a in rows:
        v = vals.get(a.video_id)
        if v is not None and v >= thr:
            exposed[a.arm] = exposed.get(a.arm, 0) + 1
    if exp.ad_policy == "held_constant":
        total = sum(exposed.values())
        if total:
            return False, (
                f"{total} publications were boosted during a run whose "
                f"design said the ad was held constant "
                f"({exposed}). This run measured the ad as well as the "
                f"opening, and the two cannot be separated after the "
                f"fact.")
        return True, "nothing was boosted, as designed"
    # randomised: the arms must be close
    t, c = exposed.get("treatment", 0), exposed.get("control", 0)
    if abs(t - c) > max(1, exp.n_per_arm // 4):
        return False, (
            f"the ad was randomised but landed unevenly: {t} boosted in "
            f"treatment against {c} in control. The imbalance is large "
            f"enough to be the difference.")
    return True, f"ad exposure balanced across arms ({t} vs {c})"


def read_out(session: Session, exp: Experiment, *,
             frozen_before: dict | None = None) -> Readout:
    """The verdict, computed once, whichever way it went.

    Raises rather than returning a partial answer. A number that
    exists gets quoted, so before the stopping rule is met the only
    safe thing to produce is no number at all.
    """
    if exp.status == "complete":
        raise NotReady("already read out; a result is computed once")
    if exp.status != "running":
        raise NotReady(f"status is {exp.status!r}, not running")
    if frozen_before:
        assert_unchanged(exp, frozen_before)

    rows = list(session.scalars(
        select(ExperimentAssignment)
        .where(ExperimentAssignment.experiment_id == exp.id,
               ExperimentAssignment.video_id.is_not(None))).all())
    needed = exp.n_per_arm * 2
    mature = _mature(session, exp, rows)
    if len(mature) < needed:
        raise NotReady(_why_not(exp, len(rows), len(mature), needed))

    ok, why = _ad_balanced(session, exp, mature)
    if not ok:
        raise Contaminated(why)

    by_arm = _outcome_values(session, exp, mature)
    t, c = by_arm["treatment"], by_arm["control"]
    if len(t) < 2 or len(c) < 2:
        raise NotReady("an arm has fewer than two measured outcomes")

    from statistics import median

    from server.research.cohort import _mann_whitney_p
    p = _mann_whitney_p(t, c)
    mt, mc = median(t), median(c)
    observed = ("lower" if mt < mc else "higher" if mt > mc else "same")
    significant = p is not None and p <= exp.alpha
    as_predicted = observed == exp.predicted_direction

    if p is None:
        verdict = "inconclusive"
    elif significant and as_predicted:
        verdict = "supported"
    elif significant and not as_predicted:
        # A significant result in the WRONG direction is not a null.
        # Calling it one would hide the most interesting outcome an
        # experiment can have.
        verdict = "not_supported"
    else:
        verdict = "inconclusive"

    detail = {
        "n_treatment": len(t), "n_control": len(c),
        "median_treatment": round(mt, 4), "median_control": round(mc, 4),
        "observed_direction": observed,
        "predicted_direction": exp.predicted_direction,
        "p_value": None if p is None else round(p, 5),
        "alpha": exp.alpha,
        "significant": significant,
        "ad_check": why,
        "prediction_as_written": exp.prediction,
        "what_this_does_not_show": (
            "One experiment on one account. It says what happened when "
            "this creator deliberately varied this one thing, and "
            "nothing about any other creator, platform or period. A "
            "supported result licenses a causal claim about this "
            "account only."),
    }
    exp.verdict = verdict
    exp.result = detail
    exp.status = "complete"
    exp.completed_at = datetime.now(timezone.utc)
    session.add(exp)
    return Readout(verdict=verdict, detail=detail)


def feedback(session: Session, exp: Experiment) -> dict:
    """What the result changes in the registry.

    BLUEPRINT M9: a surviving Associated pattern becomes Validated;
    a failing one is marked and the opportunity that proposed it is
    retired. This returns the change rather than applying it blindly,
    because promoting a feature to the only grade that licenses
    causal language is not something to do as a side effect of
    reading a page.
    """
    if exp.status != "complete" or not exp.verdict:
        return {"applies": False,
                "why": "no verdict yet; nothing to feed back"}
    if exp.verdict == "supported":
        return {
            "applies": True, "feature": exp.feature,
            "new_grade": "Validated",
            "why": (f"A pre-registered experiment predicted a "
                    f"{exp.predicted_direction} {exp.outcome} and "
                    f"observed one, p={exp.result.get('p_value')}. This "
                    f"is the only route to Validated in the system."),
            "scope": ("This account, this platform, this period. "
                      "Validated does not mean universal."),
        }
    if exp.verdict == "not_supported":
        return {
            "applies": True, "feature": exp.feature,
            "new_grade": "Observed",
            "retire_opportunity": exp.opportunity_key,
            "why": ("The experiment ran and the prediction was wrong. "
                    "The association may still exist; what has been "
                    "ruled out is that acting on it produces the "
                    "predicted effect. The opportunity that proposed "
                    "it is retired."),
        }
    return {
        "applies": False, "feature": exp.feature,
        "retire_opportunity": None,
        "why": ("Inconclusive. The experiment did not distinguish the "
                "prediction from no effect, which is not evidence that "
                "there is no effect - it is evidence that this many "
                "publications could not tell. The grade is unchanged "
                "and an inconclusive result stays inconclusive."),
    }
