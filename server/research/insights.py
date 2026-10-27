"""Findings in English, built from stored evidence and nothing else.

Every object this module produces is assembled from a `feature_evidence`
row plus the plain-English record in `features/lexicon.py`. There is no
path by which a sentence here can describe a relationship the database
does not hold, which is the property that matters: the language layer
is a *renderer* of evidence, never a second opinion about it.

## The four refusals

**No causation.** A correlation is reported as "videos with X tended to
have Y", never "X improves Y" and never "adding X will". The verb
matters more than the caveat under it, because the verb is what a
reader remembers.

**No correlation converted into a percentage.** A rho of −0.40 is not
"40% fewer skips" and is not "a 40% improvement". `magnitude()` maps it
onto a coarse verbal scale and refuses to produce a number that looks
like an effect size on the outcome's own units.

**No direction read from a sign.** `reels_skip_rate` is good when it
falls. Whether a negative correlation is encouraging depends on the
outcome, so the direction comes from `lexicon.better_when` and an
outcome with an unknown direction gets no interpretation at all.

**No cross-platform equivalence.** Instagram's skip rate has no TikTok
counterpart in this data. An insight carries the platform its
population came from and says so; it never implies the same finding
holds elsewhere.

## Grades decide the verb

`Associated` earns "tended to". `Hypothesised` earns "might" and an
experiment proposal. `Observed` describes without relating anything to
an outcome. `Unavailable` produces no insight at all - a withheld
measurement has nothing to say, and the gap is reported instead.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from server.db.models import FeatureEvidence
from server.features import catalogue as cat
from server.features import lexicon as lex

logger = logging.getLogger("virallab.research.insights")

#: Coarse verbal bands for |rho|. Deliberately coarse: a finer scale
#: would imply a precision rank correlations on samples this size do
#: not have, and would invite the reader to compare two bands that
#: overlap once their intervals are drawn.
MAGNITUDE_BANDS: tuple[tuple[float, str], ...] = (
    (0.50, "a strong tendency"),
    (0.30, "a moderate tendency"),
    (0.15, "a weak tendency"),
    (0.00, "barely any tendency"),
)

#: Which platform a named population came from. An insight that cannot
#: name its platform is not shown, because "this finding is about
#: Instagram" is part of the finding.
POPULATION_PLATFORM: dict[str, str] = {
    "own_instagram_retention": "instagram",
}

#: Outcomes that describe what an audience did. Only these become
#: insights.
#:
#: The visual-structure analysis has two arms, and only one of them is
#: about viewers. Arm A relates a feature to `text_region_score`, which
#: is another extracted feature - a methodological check on whether the
#: proxy tracks the structure it is supposed to. Rendering it in the
#: product produces sentences like "videos with more background detail
#: tended to have more visual complexity in text areas", which is true,
#: internal, and worthless to a creator. Worse, it reads like a
#: finding. Arm A stays in `feature_evidence` where the methodology
#: section can show it, and never becomes an insight.
AUDIENCE_OUTCOMES: frozenset[str] = frozenset({"reels_skip_rate"})


def magnitude(rho: float | None) -> str:
    if rho is None:
        return "an unmeasured tendency"
    a = abs(rho)
    for floor, word in MAGNITUDE_BANDS:
        if a >= floor:
            return word
    return "barely any tendency"


@dataclass
class Insight:
    """One finding, in the form a non-technical reader can act on."""

    #: The sentence a reader meets first.
    headline: str
    #: Two or three sentences expanding it, including what was held
    #: constant and what the finding is not.
    explanation: str
    #: Why a creator might care. Explicitly a creative reading, kept
    #: apart from the measurement so the two are never confused.
    why_it_may_matter: str

    feature: str
    feature_display: str
    outcome: str
    outcome_display: str
    platform: str
    population: str
    n: int

    #: observed | associated | hypothesised | validated
    status: str
    direction: str
    magnitude: str

    rho: float | None = None
    partial_rho: float | None = None
    q_value: float | None = None
    ci: list[float] | None = None
    controls: list[str] = field(default_factory=list)

    limitations: list[str] = field(default_factory=list)
    #: Publication ids behind the finding, where the analysis recorded
    #: them. Empty is honest; a fabricated list is not.
    evidence_video_ids: list[int] = field(default_factory=list)
    contradictions: list[dict] = field(default_factory=list)
    suggested_experiment: str | None = None

    def as_dict(self) -> dict:
        return {
            "headline": self.headline,
            "explanation": self.explanation,
            "why_it_may_matter": self.why_it_may_matter,
            "feature": self.feature,
            "feature_display": self.feature_display,
            "outcome": self.outcome,
            "outcome_display": self.outcome_display,
            "platform": self.platform,
            "population": self.population,
            "n": self.n,
            "status": self.status,
            "direction": self.direction,
            "magnitude": self.magnitude,
            "statistics": {
                "rho": self.rho,
                "partial_rho": self.partial_rho,
                "q_value": self.q_value,
                "ci": self.ci,
                "controls": self.controls,
            },
            "limitations": self.limitations,
            "evidence_video_ids": self.evidence_video_ids,
            "contradictions": self.contradictions,
            "suggested_experiment": self.suggested_experiment,
        }


def _good_or_bad(rho: float | None, better_when: str) -> str:
    """Whether this tendency is the encouraging direction.

    Returns "", meaning no interpretation, when the outcome's good
    direction is unknown. Guessing here would invert findings on any
    outcome where lower is better, and `reels_skip_rate` - the only
    outcome currently carrying associations - is exactly that.
    """
    if rho is None or better_when not in ("higher", "lower"):
        return ""
    rises = rho > 0
    good = (rises and better_when == "higher") or (
        not rises and better_when == "lower")
    return "encouraging" if good else "discouraging"


def _headline(feat: str, outcome: str, rho: float | None,
              better_when: str) -> str:
    fname = lex.display_name(feat).lower()
    oname = lex.outcome_name(outcome).lower()
    if rho is None:
        return f"{lex.display_name(feat)} and {oname} were tested together."
    more_less = "more" if rho > 0 else "fewer"
    # Phrased as a tendency among videos, never as an effect of one
    # thing on another.
    if outcome == "reels_skip_rate":
        direction = "fewer" if rho < 0 else "more"
        return (f"Videos with more {fname} tended to have {direction} "
                f"viewers skipping them.")
    return (f"Videos with more {fname} tended to have {more_less} "
            f"{oname}.")


def _explanation(ev: FeatureEvidence, platform: str,
                 strength: str) -> str:
    fp = lex.PLAIN.get(ev.feature)
    op = lex.OUTCOMES.get(ev.outcome)
    bits = [
        f"Across the {ev.n} {platform.capitalize()} videos we could "
        f"measure, this was {strength}."]
    if ev.controls:
        human = {
            "duration_s": "how long the video is",
            "age_days": "how long it has been online",
            "ad_exposure": "how much paid promotion it received",
        }
        named = [human.get(c, c) for c in ev.controls]
        bits.append(
            "The relationship held after accounting for "
            + ", ".join(named[:-1])
            + (f" and {named[-1]}" if len(named) > 1 else named[0]
               if named else "")
            + ".")
    bits.append(
        "This is an association, not proof: nothing here shows that "
        "changing one would change the other.")
    if fp and fp.not_a_measure_of:
        bits.append(f"Note that {fp.display_name.lower()} is not a "
                    f"measure of {fp.not_a_measure_of}")
    if op and op.limitations:
        bits.append(op.limitations)
    return " ".join(b.strip() for b in bits if b and b.strip())


def _why_it_may_matter(feat: str, sense: str) -> str:
    """The creative reading. Separated from the measurement on purpose.

    This is the one field in an Insight that is interpretation rather
    than evidence, and it is phrased as a question to try rather than
    an instruction to follow.
    """
    name = lex.display_name(feat).lower()
    if sense == "encouraging":
        return (f"If this holds up, openings with more {name} may be "
                f"worth trying more often. It is a hypothesis to test "
                f"on your own next few videos, not a rule.")
    if sense == "discouraging":
        return (f"If this holds up, leaning on {name} in an opening "
                f"may be working against you. Worth testing "
                f"deliberately before changing anything.")
    return (f"What {name} does for an audience is not established "
            f"here; the measurement exists, the consequence does not.")


def _experiment(feat: str, sense: str, status: str) -> str | None:
    if status not in ("associated", "hypothesised"):
        return None
    name = lex.display_name(feat).lower()
    more_less = "more" if sense == "encouraging" else "less"
    return (f"Post a run of videos whose openings deliberately carry "
            f"{more_less} {name} than your recent average, decide the "
            f"comparison and the number of videos before you start, "
            f"and compare them against the videos either side. "
            f"Deciding afterwards is how a null result becomes a "
            f"success story.")


def row_status(ev: FeatureEvidence) -> str:
    """The grade THIS evidence row earns, on its own.

    Not the feature's grade. `catalogue.build` grades a feature by its
    best surviving result, which is right for deciding whether the
    feature may be used at all - and wrong here. An insight describes
    one relationship, and letting it inherit a sibling row's grade
    would label an uncorrected finding `associated` because some other
    finding about the same feature was corrected. That happened, and
    it is the kind of error that reads perfectly until someone checks.
    """
    controls = list(ev.controls or [])
    if ev.analysis in cat.EXPERIMENT_ANALYSES:
        return "validated"
    corrected = ev.q_value is not None and ev.q_value <= cat.Q_THRESHOLD
    big_enough = ev.n >= cat.MIN_N_FOR_ASSOCIATED
    ad_controlled = cat.AD_COVARIATE in controls
    if corrected and big_enough and ad_controlled:
        return "associated"
    if ev.p_value is not None and ev.p_value <= 0.05:
        return "hypothesised"
    return "observed"


def build(session: Session, *, population: str | None = None,
          min_status: str = "associated") -> list[Insight]:
    """Every finding the stored evidence supports, strongest first.

    `min_status` defaults to `associated` because that is the weakest
    grade BLUEPRINT §2 allows to drive anything. Lowering it surfaces
    hypotheses, which is useful on a report page and must never be the
    default anywhere that reads like advice.
    """
    order = {"observed": 0, "hypothesised": 1, "associated": 2,
             "validated": 3}
    floor = order.get(min_status, 2)

    q = select(FeatureEvidence)
    if population:
        q = q.where(FeatureEvidence.population == population)
    rows = list(session.scalars(q).all())

    entries = {e.spec.name: e for e in cat.build(session,
                                                 population=population)}

    out: list[Insight] = []
    for ev in rows:
        entry = entries.get(ev.feature)
        if entry is None:
            continue
        # A withheld or unavailable measurement has nothing to say. The
        # gap is reported elsewhere; it never becomes a sentence that
        # reads like a finding. This gate is at the FEATURE level,
        # which is correct: withheld means the measurement itself is
        # not trusted, whatever any single row says about it.
        if entry.grade == "Unavailable":
            continue
        # ...but the grade on the sentence is this row's own.
        status = row_status(ev)
        if order.get(status, 0) < floor:
            continue
        if ev.outcome not in AUDIENCE_OUTCOMES:
            continue

        platform = POPULATION_PLATFORM.get(ev.population, "")
        if not platform:
            logger.warning(
                "population %r has no platform mapping; its findings "
                "are withheld rather than shown without one",
                ev.population)
            continue

        better = lex.better_when(ev.outcome)
        sense = _good_or_bad(ev.rho, better)
        strength = magnitude(ev.rho)

        ins = Insight(
            headline=_headline(ev.feature, ev.outcome, ev.rho, better),
            explanation=_explanation(ev, platform, strength),
            why_it_may_matter=_why_it_may_matter(ev.feature, sense),
            feature=ev.feature,
            feature_display=lex.display_name(ev.feature),
            outcome=ev.outcome,
            outcome_display=lex.outcome_name(ev.outcome),
            platform=platform,
            population=ev.population,
            n=ev.n,
            status=status,
            direction=sense or "unclear",
            magnitude=strength,
            rho=ev.rho, partial_rho=ev.partial_rho, q_value=ev.q_value,
            ci=([ev.ci_low, ev.ci_high] if ev.ci_low is not None else None),
            controls=list(ev.controls or []),
            suggested_experiment=_experiment(ev.feature, sense, status),
        )

        fp = lex.PLAIN.get(ev.feature)
        if fp and fp.limitations:
            ins.limitations.append(fp.limitations)
        if ev.unavailable_n:
            ins.limitations.append(
                f"{ev.unavailable_n} publications had no value for "
                f"this measurement and are not in the {ev.n} behind "
                f"the figure.")
        if ev.note:
            ins.limitations.append(ev.note)
        ins.limitations.append(
            f"Measured on {platform.capitalize()} only. Nothing here "
            f"carries over to another platform unless the same "
            f"measurement has been shown to mean the same thing "
            f"there, which has not been done.")
        out.append(ins)

    out.sort(key=lambda i: (-order.get(i.status, 0),
                            -abs(i.rho or 0.0)))
    return out


def gaps(session: Session, *, population: str | None = None) -> list[dict]:
    """What cannot be concluded, and why. Shown, never omitted.

    A report that lists only what it found reads as though it looked
    everywhere. This is the other half: the measurements that exist and
    are not trustworthy, and the ones that do not exist at all.
    """
    out = []
    for e in cat.build(session, population=population):
        if e.grade != "Unavailable":
            continue
        p = lex.PLAIN.get(e.spec.name)
        out.append({
            "feature": e.spec.name,
            "display_name": lex.display_name(e.spec.name),
            "measures": (p.measures if p else e.spec.describes),
            "why_unavailable": e.grade_reason,
            "limitations": (p.limitations if p else ""),
        })
    return out
