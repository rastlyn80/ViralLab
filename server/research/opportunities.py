"""M7: what to try next, with the cost of finding out whether it worked.

An opportunity is a proposed creative change, the finding that
motivates it, the publications it is drawn from, the publications that
contradict it, and a concrete experiment. It is assembled from the
evidence table and the cohort comparison, never from a model's
opinion: nothing here asks a language model anything.

## The thing this milestone discovered

The suggested experiments that M5 printed beside each finding are, at
the owner's measured posting rate, **mostly impossible**.

Instagram: 386 publications over 2391 days, **4.8 a month**. To
detect a Spearman correlation of the size actually observed - rho
around 0.3 - at 80% power needs **85 publications**, which is
**eighteen months** of posting. The weaker findings, at rho 0.2, need
194 publications: more than three years.

"Post a run of videos with more background detail and compare" is
therefore not advice, it is a three-year commitment described as an
afternoon's work. So every opportunity here carries the number of
publications its experiment needs and how long that is at the rate
the owner actually posts, and they are **ranked by how quickly they
could be answered** rather than by how interesting they sound.

An opportunity whose experiment cannot finish inside two years is
still listed - hiding it would be its own distortion - but it is
marked `impractical` and says why.

## Grading

An opportunity may never be graded above the evidence under it. A
cohort difference is `Observed` and stays `Observed` however
suggestive, because no outcome was measured on the other creators at
all. An association that survived correction with the ad covariate
is `Associated`. Nothing here reaches `Validated`, because only the
pre-registered experiment of M9 can produce that, which is the whole
point of making these pre-registerable.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from server.ai import vision_questions as vq
from server.collectors import permalink
from server.db.models import Creator, Video
from server.features import lexicon as lex
from server.research import insights as ins

logger = logging.getLogger("virallab.research.opportunities")

#: Power target for every sample-size figure quoted. Two-sided 5%.
ALPHA, POWER = 0.05, 0.80
Z_ALPHA, Z_BETA = 1.959964, 0.841621

#: Beyond this, an experiment is not a plan. Two years of posting at
#: the owner's rate is roughly 115 publications on Instagram.
IMPRACTICAL_MONTHS = 24.0
#: Under this it is worth starting now.
PRACTICAL_MONTHS = 9.0

#: How many publications to show on each side. Enough to check the
#: claim by eye, few enough to actually look at.
EXAMPLES = 4

GRADE_ORDER = {"Observed": 0, "Hypothesised": 1,
               "Associated": 2, "Validated": 3}

#: `row_status` speaks lower case; the blueprint ladder is capitalised.
STATUS_TO_GRADE = {"observed": "Observed", "hypothesised": "Hypothesised",
                   "associated": "Associated", "validated": "Validated"}


def n_for_rho(rho: float | None) -> int | None:
    """Publications needed to detect `rho` again, at 80% power.

    Fisher's z. The point is not precision - it is that the answer is
    85 rather than 10, and that nobody reading an opportunity should
    have to work that out for themselves.
    """
    if rho is None:
        return None
    r = min(abs(rho), 0.95)
    if r < 1e-6:
        return None
    z = 0.5 * math.log((1 + r) / (1 - r))
    return int(math.ceil(((Z_ALPHA + Z_BETA) / z) ** 2 + 3))


#: Mean of the top (and bottom) decile of a standard normal, in SDs.
#: A deliberately-contrasted run samples the ends of the distribution
#: rather than its natural spread, which is why it needs fewer
#: publications than re-detecting the correlation does.
DECILE_MEAN_SD = 1.755


def n_for_contrast(rho: float | None) -> int | None:
    """Publications needed if the opening is deliberately contrasted.

    `n_for_rho` answers "how long to see this correlation again in
    the stuff I post anyway", and for the effects measured here that
    is well over a year. An intervention does better, because it
    stops sampling the natural range and samples the ends of it.

    Under bivariate normality, selecting the top and bottom deciles
    of the feature separates the outcome by `2 * 1.755 * rho` standard
    deviations, against a within-group spread of `sqrt(1 - rho^2)`.
    Two groups, 80% power, two-sided 5%.

    **The trade is real and is stated wherever this number is shown.**
    It answers a question about clearly-high against clearly-low
    openings, which is not the same question as the one the
    correlation describes, and it is only cheaper if the contrast is
    actually struck.
    """
    if rho is None:
        return None
    r = min(abs(rho), 0.95)
    if r < 1e-6:
        return None
    d = (2 * DECILE_MEAN_SD * r) / math.sqrt(1 - r * r)
    per_group = 2 * ((Z_ALPHA + Z_BETA) ** 2) / (d * d)
    return int(math.ceil(per_group) * 2)


def posting_rate(session: Session, platform: str) -> float | None:
    """Own-account publications a month, measured, not assumed."""
    row = session.execute(
        select(func.count(Video.id), func.min(Video.posted_at),
               func.max(Video.posted_at))
        .join(Creator, Creator.id == Video.creator_id)
        .where(Creator.is_own_account.is_(True),
               Video.platform == platform,
               Video.posted_at.is_not(None))).one()
    n, first, last = row
    if not n or first is None or last is None:
        return None
    days = max((last - first).days, 1)
    return round(n / days * 30.0, 2)


@dataclass(frozen=True)
class Example:
    """One real publication, so a claim can be checked by eye."""

    video_id: int
    permalink: str | None
    feature_value: float
    #: None where no outcome exists for this comparison at all - a
    #: cohort difference has none, because no retention data exists
    #: for any creator but the owner. NaN would serialise to invalid
    #: JSON and read as a number that happened to be missing; None
    #: says the right thing.
    outcome_value: float | None
    note: str


@dataclass(frozen=True)
class Experiment:
    """A plan specific enough to pre-register, and honest about cost."""

    change: str
    feature: str
    direction: str
    outcome: str
    comparison: str
    stopping_rule: str
    metrics_to_collect: tuple[str, ...]

    publications_needed: int | None
    posting_rate_per_month: float | None
    months_to_answer: float | None
    #: practical | slow | impractical | unknown
    feasibility: str
    feasibility_note: str

    #: The cheaper design, and what it costs in interpretation.
    contrast_publications_needed: int | None = None
    contrast_months_to_answer: float | None = None
    contrast_note: str = ""

    def as_dict(self) -> dict:
        return {
            "change": self.change, "feature": self.feature,
            "direction": self.direction, "outcome": self.outcome,
            "comparison": self.comparison,
            "stopping_rule": self.stopping_rule,
            "metrics_to_collect": list(self.metrics_to_collect),
            "publications_needed": self.publications_needed,
            "posting_rate_per_month": self.posting_rate_per_month,
            "months_to_answer": self.months_to_answer,
            "feasibility": self.feasibility,
            "feasibility_note": self.feasibility_note,
            "faster_alternative": {
                "publications_needed": self.contrast_publications_needed,
                "months_to_answer": self.contrast_months_to_answer,
                "note": self.contrast_note,
            } if self.contrast_publications_needed else None,
        }


@dataclass
class Opportunity:
    key: str
    approach: str
    motivating_pattern: str
    grade: str
    source: str

    feature: str
    feature_display: str
    outcome: str
    outcome_display: str
    platform: str
    n: int

    experiment: Experiment
    supporting: list[Example] = field(default_factory=list)
    counterexamples: list[Example] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)
    evidence_note: str = ""

    def traceable_video_ids(self) -> list[int]:
        return sorted({e.video_id for e in
                       self.supporting + self.counterexamples})

    def as_dict(self) -> dict:
        def ex(e: Example) -> dict:
            return {"video_id": e.video_id, "permalink": e.permalink,
                    "feature_value": e.feature_value,
                    "outcome_value": e.outcome_value, "note": e.note}
        return {
            "key": self.key, "approach": self.approach,
            "motivating_pattern": self.motivating_pattern,
            "grade": self.grade, "source": self.source,
            "feature": self.feature,
            "feature_display": self.feature_display,
            "outcome": self.outcome,
            "outcome_display": self.outcome_display,
            "platform": self.platform, "n": self.n,
            "supporting_examples": [ex(e) for e in self.supporting],
            "counterexamples": [ex(e) for e in self.counterexamples],
            "limitations": list(self.limitations),
            "evidence_note": self.evidence_note,
            "experiment": self.experiment.as_dict(),
            "traceable_video_ids": self.traceable_video_ids(),
        }


def display_months(months: float | None) -> int | None:
    """Months, always rounded UP.

    Python's `format(4.5, '.0f')` gives 4 and JavaScript's
    `Math.round(4.5)` gives 5, so the same number was printed as both
    on the same card. Rounding up everywhere settles it in the
    direction that cannot mislead: an experiment that takes longer
    than advertised is a worse surprise than one that finishes early.
    """
    return None if months is None else int(math.ceil(months))


def _feasibility(months: float | None) -> tuple[str, str]:
    if months is None:
        return "unknown", (
            "The effect size behind this is not recorded, so how many "
            "publications it would take to confirm cannot be worked out. "
            "Treat the experiment as unscoped.")
    if months <= PRACTICAL_MONTHS:
        return "practical", (
            f"About {display_months(months)} months of posting at your "
            f"current rate. Worth starting.")
    if months <= IMPRACTICAL_MONTHS:
        return "slow", (
            f"About {display_months(months)} months at your current rate. "
            f"Runnable, "
            f"but you would be committing most of a year before the "
            f"answer arrives.")
    return "impractical", (
        f"About {display_months(months)} months - over two years - at your "
        f"current "
        f"rate. This is listed because hiding it would be its own "
        f"distortion, not because it is a plan. A larger effect, a "
        f"faster posting rate or a paired design would be needed to "
        f"make it answerable.")


def _method_version(feature: str) -> str | None:
    """The version the catalogue declares for this feature.

    Guessing is not an option here and the project has the scar to
    prove it: picking whichever version a matching row happened to
    carry once selected a 24-publication v1 benchmark run and made a
    field look untestable when v2 covered all of them. The first
    draft of this module guessed too, with `hook-deterministic-v1`,
    and silently produced no examples at all for every
    `background_*` feature, which lives under `hook-background-v1`.
    The catalogue is the one place that knows.
    """
    from server.features import catalogue as cat
    for spec in cat.SPEC:
        if spec.name == feature:
            return spec.method_version or None
    return None


def _pairs(session: Session, feature: str) -> dict[int, tuple[float, float]]:
    """Per-publication (feature value, skip rate).

    Reuses `visual_structure`'s own accessors rather than writing a
    second query: an example drawn under different filters from the
    association it illustrates would be an example of something else.
    """
    from server.features.visual_structure import (_feature_values,
                                                  _outcome_rows)

    mv = _method_version(feature)
    if mv is None:
        logger.info("no declared method version for %s; no examples",
                    feature)
        return {}
    outcomes = _outcome_rows(session)
    vals = _feature_values(session, feature, mv)
    out: dict[int, tuple[float, float]] = {}
    for vid, x in vals.items():
        row = outcomes.get(vid)
        if row is None or row.get("skip_rate") is None:
            continue
        if row["video"].media_status != "available":
            continue
        out[vid] = (float(x), float(row["skip_rate"]))
    return out


def _examples(session: Session, pairs: dict[int, tuple[float, float]],
              *, helpful_direction: str, feature_name: str
              ) -> tuple[list[Example], list[Example]]:
    """Publications that fit the pattern, and publications that defy it.

    Counterexamples are not a courtesy. A correlation of 0.3 means
    most publications do NOT follow the line, and an opportunity that
    showed only its supporters would misrepresent its own evidence by
    omission.
    """
    if len(pairs) < 2 * EXAMPLES:
        return [], []
    links = permalink.bulk(session, list(pairs))

    # Rank by feature value in the direction the finding points.
    reverse = helpful_direction == "more"
    ranked = sorted(pairs.items(), key=lambda kv: kv[1][0], reverse=reverse)
    # Among the publications that did the recommended thing, the ones
    # that worked and the ones that did not.
    head = ranked[:max(EXAMPLES * 3, len(ranked) // 4)]
    by_outcome = sorted(head, key=lambda kv: kv[1][1])   # low skip = good

    def mk(items, good: bool) -> list[Example]:
        out = []
        for vid, (fv, ov) in items:
            out.append(Example(
                video_id=vid, permalink=links.get(vid),
                feature_value=round(fv, 4), outcome_value=round(ov, 4),
                # `skip_rate` is stored 0-100, not 0-1. Formatting it
                # with `%` rendered a 77.6% skip rate as "7760%",
                # which is the kind of error a reader spots instantly
                # and which quietly destroys trust in every other
                # number on the page.
                note=(f"{'High' if reverse else 'Low'} {feature_name}, "
                      f"{'few' if good else 'many'} viewers skipped "
                      f"({ov:.0f}%)")))
        return out

    return mk(by_outcome[:EXAMPLES], True), mk(by_outcome[-EXAMPLES:], False)


def _own_extremes(session: Session, feature: str, platform: str, *,
                  display: str) -> tuple[list[Example], list[Example]]:
    """The owner's own publications at each end of one measure.

    A cohort difference has no outcome attached to it - nobody has
    retention data for anyone but the owner - so these cannot be
    "publications where it worked". They are the publications the
    difference is computed from, at both ends, which is what makes
    the claim checkable by eye rather than merely asserted.
    """
    from server.features.visual_structure import _feature_values

    mv = _method_version(feature)
    if mv is None:
        return [], []
    own = set(session.scalars(
        select(Video.id).join(Creator, Creator.id == Video.creator_id)
        .where(Creator.is_own_account.is_(True),
               Video.platform == platform)).all())
    vals = {v: x for v, x in _feature_values(session, feature, mv).items()
            if v in own}
    if len(vals) < 2 * EXAMPLES:
        return [], []
    links = permalink.bulk(session, list(vals))
    ranked = sorted(vals.items(), key=lambda kv: kv[1], reverse=True)

    def mk(items, high: bool) -> list[Example]:
        return [Example(
            video_id=v, permalink=links.get(v), feature_value=round(x, 4),
            outcome_value=None,
            note=(f"Your {'highest' if high else 'lowest'} {display} - "
                  f"no outcome is measured for the other creators, so "
                  f"this shows the measure, not a result"))
            for v, x in items]

    return mk(ranked[:EXAMPLES], True), mk(ranked[-EXAMPLES:], False)


def _approach(feature: str, direction: str) -> str:
    name = lex.display_name(feature).lower()
    verb = "more" if direction == "more" else "less"
    return (f"Try openings with {verb} {name} than your recent average, "
            f"and keep everything else about how you post the same.")


def from_associations(session: Session, *,
                      population: str | None = None) -> list[Opportunity]:
    """One opportunity per surviving association."""
    out: list[Opportunity] = []
    for f in ins.build(session, population=population):
        # A measurement nobody has checked may not motivate an action,
        # whatever its correlation. This is the gate the vision work of
        # 2026-10-27 exists to enforce: `indicative` is shown to a
        # reader and never drives a recommendation.
        short = f.feature[len("vision_"):] if f.feature.startswith(
            "vision_") else f.feature
        if vq.by_name(short) and not vq.may_enter_statistics(short):
            logger.info("skipping %s: measurement not verified", f.feature)
            continue

        grade = STATUS_TO_GRADE.get(f.status, "Observed")
        direction = "more" if f.direction in ("higher", "more") else "less"
        if lex.better_when(f.outcome) == "lower" and (f.rho or 0) > 0:
            direction = "less"
        elif lex.better_when(f.outcome) == "lower" and (f.rho or 0) < 0:
            direction = "more"

        pairs = _pairs(session, f.feature)
        sup, counter = _examples(
            session, pairs, helpful_direction=direction,
            feature_name=lex.display_name(f.feature).lower())

        effect = f.partial_rho if f.partial_rho is not None else f.rho
        needed = n_for_rho(effect)
        rate = posting_rate(session, f.platform or "instagram")
        months = (round(needed / rate, 1)
                  if needed and rate and rate > 0 else None)

        # The cheaper design. Feasibility is judged on the BEST
        # available design, because telling someone a thing takes two
        # years when it takes seven months is as misleading as the
        # reverse.
        c_needed = n_for_contrast(effect)
        c_months = (round(c_needed / rate, 1)
                    if c_needed and rate and rate > 0 else None)
        best_months = min([m for m in (months, c_months) if m is not None],
                          default=None)
        feas, note = _feasibility(best_months)
        c_note = ""
        if c_needed and c_months is not None:
            c_note = (
                f"Deliberately alternating clearly-high and clearly-low "
                f"openings needs about {c_needed} publications "
                f"({display_months(c_months)} months) instead of "
                f"{needed} ({display_months(months)} months). The trade "
                f"is that it answers a "
                f"question about the extremes rather than about the "
                f"range you normally post in, and it is only cheaper if "
                f"the contrast is actually struck - half-hearted "
                f"variation buys none of the saving.")

        limits = list(f.limitations)
        if feas == "impractical":
            limits.insert(0, note)
        if not counter:
            limits.append(
                "Too few publications carry this measurement to show "
                "counterexamples, so the pattern is stated without the "
                "cases that contradict it.")

        out.append(Opportunity(
            key=f"assoc:{f.feature}:{f.outcome}:{f.platform}",
            approach=_approach(f.feature, direction),
            motivating_pattern=f.headline,
            grade=grade, source="association",
            feature=f.feature, feature_display=f.feature_display,
            outcome=f.outcome, outcome_display=f.outcome_display,
            platform=f.platform, n=f.n,
            supporting=sup, counterexamples=counter,
            limitations=limits,
            evidence_note=f.explanation,
            experiment=Experiment(
                change=_approach(f.feature, direction),
                feature=f.feature, direction=direction, outcome=f.outcome,
                comparison=("your own publications from the same period, "
                            "compared at the same age"),
                stopping_rule=(
                    f"Decide before starting: {needed or 'an unknown '
                                               'number of'} publications, "
                    f"then stop and read the result once. Extending the "
                    f"run because it is nearly significant is how a null "
                    f"result becomes a success story."),
                metrics_to_collect=(f.outcome, "views@7d", "duration_s",
                                    "ad_exposure"),
                publications_needed=needed,
                posting_rate_per_month=rate,
                months_to_answer=months,
                feasibility=feas, feasibility_note=note,
                contrast_publications_needed=c_needed,
                contrast_months_to_answer=c_months,
                contrast_note=c_note)))
    return out


def from_cohort(session: Session, collection_id: int, *,
                owner_platform: str = "tiktok") -> list[Opportunity]:
    """Opportunities from how the owner differs from other creators.

    Capped at `Observed` by construction. No outcome exists for any
    creator but the owner, so a cohort difference says what the videos
    *are* and never what they achieve - and an opportunity drawn from
    one inherits that ceiling however small its q-value.
    """
    from server.research import cohort as co

    rep = co.compare(session, collection_id, owner_platform=owner_platform)
    rate = posting_rate(session, owner_platform)
    links_cache: dict[str, tuple[list[Example], list[Example]]] = {}
    out: list[Opportunity] = []
    for d in rep.differences:
        if d.q_value is not None and d.q_value > 0.05:
            continue
        direction = "less" if d.direction == "higher" else "more"
        note = (
            "No retention, watch-time or skip data exists for any creator "
            "but you, so this says how your openings differ from theirs "
            "and nothing about whether the difference helps. Testing it "
            "means running it on your own account and measuring the "
            "outcome there.")
        # Unknowable: there is no effect size on an outcome to power
        # against, because there is no outcome.
        feas, fnote = _feasibility(None)
        if d.feature not in links_cache:
            links_cache[d.feature] = _own_extremes(
                session, d.feature, owner_platform,
                display=d.display_name.lower())
        most, least = links_cache[d.feature]
        out.append(Opportunity(
            key=f"cohort:{collection_id}:{d.feature}",
            approach=_approach(d.feature, direction),
            motivating_pattern=d.plain,
            grade="Observed", source="cohort",
            feature=d.feature, feature_display=d.display_name,
            outcome="", outcome_display="no outcome measured",
            platform=owner_platform, n=d.cohort_members or 0,
            supporting=most, counterexamples=least,
            limitations=[note, *rep.limitations],
            evidence_note=d.note,
            experiment=Experiment(
                change=_approach(d.feature, direction),
                feature=d.feature, direction=direction, outcome="",
                comparison="your own publications before the change",
                stopping_rule=(
                    "Decide the number of publications before starting. "
                    "Because no outcome was measured on the other "
                    "creators, this experiment has to establish from "
                    "scratch whether the difference matters at all."),
                metrics_to_collect=("reels_skip_rate", "views@7d",
                                    "duration_s", "ad_exposure"),
                publications_needed=None,
                posting_rate_per_month=rate, months_to_answer=None,
                feasibility=feas, feasibility_note=fnote)))
    return out


def build(session: Session, *, population: str | None = None,
          collection_id: int | None = None) -> list[Opportunity]:
    """Every opportunity the evidence supports, most answerable first.

    Ordered by how quickly it could be settled, then by evidence
    grade. Ranking by grade alone would put an eighteen-month
    commitment above a six-month one purely because its q-value is
    smaller, which is the wrong advice to give someone deciding what
    to do next.
    """
    out = from_associations(session, population=population)
    if collection_id is not None:
        try:
            out += from_cohort(session, collection_id)
        except Exception:                      # noqa: BLE001 - reported
            logger.exception("cohort opportunities unavailable")

    rank = {"practical": 0, "slow": 1, "unknown": 2, "impractical": 3}
    out.sort(key=lambda o: (rank.get(o.experiment.feasibility, 9),
                            -GRADE_ORDER.get(o.grade, 0),
                            o.experiment.months_to_answer or 1e9))
    return out


def summary(items: list[Opportunity]) -> dict:
    """What a reader needs before reading any single opportunity."""
    by_feas: dict[str, int] = {}
    by_grade: dict[str, int] = {}
    for o in items:
        by_feas[o.experiment.feasibility] = by_feas.get(
            o.experiment.feasibility, 0) + 1
        by_grade[o.grade] = by_grade.get(o.grade, 0) + 1
    practical = [o for o in items
                 if o.experiment.feasibility == "practical"]
    return {
        "total": len(items),
        "by_feasibility": by_feas,
        "by_grade": by_grade,
        "traceable": sum(1 for o in items if o.traceable_video_ids()),
        "headline": (
            f"{len(practical)} of {len(items)} could be answered within "
            f"{PRACTICAL_MONTHS:.0f} months at your current posting rate."
            if items else "No opportunity is supported by the evidence yet."),
        "caveat": (
            "None of these is a prediction. Each is a change worth "
            "testing, with the cost of testing it stated. Nothing here "
            "has been shown to cause anything."),
    }
