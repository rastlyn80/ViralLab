"""What goes with more views, measured inside each creator.

The owner asked which elements are "responsible for virality" in
other people's videos. Responsibility is causal and cannot be had
from watching: there is no version of somebody's reel that was made
without the thing. What *can* be had, and is worth having, is which
measurements go with a video doing well **for the creator who made
it** - and that is a sharper question than it first sounds.

## Why within-creator, and why it is not a technicality

Compare raw views across creators and the answer is always
followers. tonyannn has 2.7 million and cirruspiano has 64; every
video the first posts out-views every video the second posts, and a
correlation between any feature and raw views is mostly a
correlation between that feature and being tonyannn.

So each video is scored against **its own creator's distribution** -
its percentile among that creator's other videos. A video at the
90th percentile did well *for that account*, whatever the absolute
number. Creator-level confounds - followers, niche, posting rate,
whatever the algorithm thinks of them - are differenced out, because
they are constant within the comparison.

## Age is partialled out, and it is not optional

Competitor view counts are lifetime totals read at wildly different
ages: 0 to 2,400 days, median 97. Views accumulate, so an older
video has more of them for no reason connected to how it was made.
Within a creator that does not vanish - their old videos are still
older than their new ones - so `log(age)` is partialled out of every
correlation and the uncontrolled figure is reported beside it.

CLAUDE.md 3.8a asks for fixed-age anchors and this is not one. A
proper anchor needs views measured at a known age, which for
somebody else's videos this project cannot obtain. The partial
correlation is the available second best and is labelled as such
rather than presented as the thing that was asked for.

## What a result here may be called

`Observed` at best, and usually that. BLUEPRINT's ladder reserves
`Associated` for a corrected result with the ad covariate applied,
and there is no ad covariate for other people's accounts - nobody
knows which of their videos were boosted. So a finding here says
"creators' videos with more X tended to do better for them", and
stops.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from server.db.models import Creator, MetricsSnapshot, Video, VideoFeature
from server.features import lexicon as lex
from server.features.retention import (benjamini_hochberg, partial_spearman,
                                       spearman, spearman_p)

logger = logging.getLogger("virallab.research.virality")

#: A creator with fewer than this cannot contribute a percentile that
#: means anything: with four videos the ranks are 0, 33, 67 and 100
#: whatever the views were.
MIN_PER_CREATOR = 8

#: Below this many publications pooled, a correlation is noise.
MIN_TOTAL = 60

#: Benjamini-Hochberg threshold, matching `features/catalogue.py`.
Q_THRESHOLD = 0.05

#: Measurements that are a count of elapsed time wearing a content
#: name. `publications_before_this` is literally an index of how far
#: into the account's life a video sits, and age is the one thing
#: this analysis is trying to hold constant - so when it surfaces as
#: a "finding" it is residual age confounding, not a property of the
#: video.
#:
#: The tell was that it came out POSITIVE on the owner's account and
#: NEGATIVE on competitors'. A content property would not reverse;
#: an imperfectly-controlled time trend does, depending on whether
#: the account was growing over the window.
#:
#: They are still computed and still reported - "your later videos
#: do better" is worth knowing - but as an account trend, under a
#: separate heading, never mixed in with what the videos are.
TIME_PROXIES = frozenset({
    "publications_before_this", "is_first_post",
})


@dataclass
class Finding:
    feature: str
    display_name: str
    describes: str
    n: int
    creators: int
    rho: float | None
    partial_rho: float | None
    p_value: float | None
    q_value: float | None
    direction: str
    grade: str = "Observed"
    plain: str = ""
    limitations: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "feature": self.feature,
            "display_name": self.display_name,
            "describes": self.describes,
            "n": self.n, "creators": self.creators,
            "direction": self.direction, "grade": self.grade,
            "plain": self.plain,
            "statistics": {
                "rho_uncontrolled": self.rho,
                "rho_controlling_for_age": self.partial_rho,
                "p_value": self.p_value, "q_value": self.q_value,
            },
            "limitations": list(self.limitations),
        }


def _percentile(value: float, pool: list[float]) -> float:
    """Where this sits among the creator's own videos, 0 to 1."""
    if len(pool) < 2:
        return 0.5
    below = sum(1 for v in pool if v < value)
    equal = sum(1 for v in pool if v == value)
    return (below + 0.5 * equal) / len(pool)


def _population(session: Session, *, own: bool) -> dict:
    """Per-video within-creator view percentile, and age in days."""
    rows = session.execute(
        select(Video.id, Video.creator_id, Video.posted_at,
               MetricsSnapshot.views, MetricsSnapshot.captured_at)
        .join(Creator, Creator.id == Video.creator_id)
        .join(MetricsSnapshot, MetricsSnapshot.video_id == Video.id)
        .where(Creator.is_own_account.is_(own),
               MetricsSnapshot.views.is_not(None),
               Video.posted_at.is_not(None))
        .order_by(Video.id, MetricsSnapshot.captured_at.desc())).all()

    latest: dict[int, tuple] = {}
    for vid, cid, posted, views, captured in rows:
        if vid in latest:
            continue                       # newest snapshot wins
        latest[vid] = (cid, posted, float(views), captured)

    by_creator: dict[int, list[int]] = {}
    for vid, (cid, *_rest) in latest.items():
        by_creator.setdefault(cid, []).append(vid)

    out: dict[int, dict] = {}
    for cid, vids in by_creator.items():
        if len(vids) < MIN_PER_CREATOR:
            continue
        pool = [latest[v][2] for v in vids]
        for v in vids:
            _c, posted, views, captured = latest[v]
            age = max((captured - posted).total_seconds() / 86400.0, 0.5)
            out[v] = {"creator_id": cid, "views": views,
                      "percentile": _percentile(views, pool),
                      "log_age": math.log(age)}
    return out


def _feature_values(session: Session, video_ids: list[int]
                    ) -> dict[str, dict[int, float]]:
    """Numeric and boolean features, as numbers, per publication."""
    rows = session.execute(
        select(VideoFeature.feature, VideoFeature.video_id,
               VideoFeature.value_num, VideoFeature.value_bool)
        .where(VideoFeature.video_id.in_(video_ids),
               VideoFeature.status == "ok")).all()
    out: dict[str, dict[int, float]] = {}
    for feature, vid, num, flag in rows:
        if num is not None:
            out.setdefault(feature, {})[vid] = float(num)
        elif flag is not None:
            out.setdefault(feature, {})[vid] = 1.0 if flag else 0.0
    return out


def _sentence(feature: str, direction: str, n: int, creators: int, *,
              own: bool) -> str:
    """The finding in the owner's language, and whose videos it is.

    "Across 2 creators" is true of the owner's own side and reads as
    nonsense - the two are his Instagram and TikTok accounts, not two
    people. Same number, different sentence.
    """
    name = lex.display_name(feature).lower()
    more = "more" if direction == "positive" else "less"
    if own:
        where = (f"Across {n} of your videos on "
                 f"{'both your accounts' if creators > 1 else 'your account'}")
        whose = "your own average"
        tail = ("It is a pattern across what you have already posted, "
                "not something shown to make a video do better.")
    else:
        where = f"Across {creators} creators and {n} videos"
        whose = "that creator's own average"
        tail = ("It is a tendency across their catalogues, not "
                "something shown to make any video do better.")
    return f"{where}, the ones with {more} {name} tended to do better "            f"than {whose}. {tail}"


def build(session: Session, *, own: bool = False) -> dict:
    """The gradient analysis, remembered briefly. See `_build`."""
    from server.research import memo
    return memo.remember("virality.build",
                         lambda: _build(session, own=own),
                         session=session, own=own)


def _build(session: Session, *, own: bool = False) -> dict:
    """Every measurement tested against within-creator view rank."""
    pop = _population(session, own=own)
    if len(pop) < MIN_TOTAL:
        return {
            "findings": [], "n": len(pop),
            "creators": len({d["creator_id"] for d in pop.values()}),
            "why_empty": (
                f"Only {len(pop)} publications sit with a creator who "
                f"has at least {MIN_PER_CREATOR} measured videos. Below "
                f"{MIN_TOTAL} pooled, a correlation is noise, and "
                f"reporting one would be worse than reporting none."),
            "limitations": [],
        }

    ids = sorted(pop)
    feats = _feature_values(session, ids)
    ranks = [pop[v]["percentile"] for v in ids]
    ages = [pop[v]["log_age"] for v in ids]

    candidates = []
    for feature, values in sorted(feats.items()):
        paired = [(values[v], pop[v]["percentile"], pop[v]["log_age"])
                  for v in ids if v in values]
        if len(paired) < MIN_TOTAL:
            continue
        xs = [p[0] for p in paired]
        if is_boolean(xs):
            # A yes/no answer has exactly two values, so the
            # "nearly constant" rule below must not see one.
            # Excluding booleans by value count threw away every
            # boolean measurement in the project - see the note on
            # `why_not_testable`. A Spearman correlation against a
            # 0/1 variable is a rank-biserial correlation, which is
            # a real and well-defined thing to compute.
            #
            # The same floor the threshold analysis uses: a boolean
            # true a handful of times cannot support a correlation
            # however small its p. A constant one falls out too.
            trues = sum(xs)
            if min(trues, len(xs) - trues) < MIN_BOOLEAN_CASES:
                continue
        elif len(set(xs)) < 3:
            # A feature that is nearly constant has no information in
            # it, and a correlation on three distinct values is a
            # coin dressed as a measurement.
            continue
        ys = [p[1] for p in paired]
        zs = [p[2] for p in paired]
        rho = spearman(xs, ys)
        prho = partial_spearman(xs, ys, zs)
        if prho is None:
            continue
        p = spearman_p(prho, len(paired))
        candidates.append((feature, len(paired), rho, prho, p))

    qs = benjamini_hochberg([c[4] for c in candidates])
    findings: list[Finding] = []
    ungraded: list[Finding] = []
    n_creators = len({d["creator_id"] for d in pop.values()})
    for (feature, n, rho, prho, p), q in zip(candidates, qs):
        if q is None or q > Q_THRESHOLD:
            continue
        direction = "positive" if prho > 0 else "negative"
        f = Finding(
            feature=feature, display_name=lex.display_name(feature),
            describes=_describes(feature), n=n, creators=n_creators,
            rho=round(rho, 3) if rho is not None else None,
            partial_rho=round(prho, 3), p_value=round(p, 5) if p else None,
            q_value=round(q, 5), direction=direction, grade="Observed",
            plain=_sentence(feature, direction, n, n_creators,
                            own=own),
            limitations=[
                "Nothing here is evidence that changing this would "
                "change anything. These are other people's finished "
                "videos; there is no version of them made differently.",
                "Views are lifetime totals read at ages from days to "
                "years. Age is partialled out of every figure, which "
                "is a correction and not the fixed-age anchor the "
                "method actually asks for.",
                "No ad covariate exists for other creators - nobody "
                "knows which of their videos were promoted - so no "
                "finding here can rise above Observed.",
            ])
        if not may_be_a_finding(feature):
            # Same gate as `breakthroughs`. A question the project
            # has not checked against enough human-confirmed answers
            # does not become a finding by correlating well.
            f.grade = "Unavailable"
            f.limitations.insert(0, (
                "This question has not been checked against enough "
                "human-confirmed answers to support a claim. It is "
                "reported and not counted."))
            ungraded.append(f)
            continue
        findings.append(f)

    trends = [f for f in findings if f.feature in TIME_PROXIES]
    findings = [f for f in findings if f.feature not in TIME_PROXIES]
    findings.sort(key=lambda f: -abs(f.partial_rho or 0))
    trends.sort(key=lambda f: -abs(f.partial_rho or 0))
    return {
        "n": len(pop), "creators": n_creators,
        "measurements_tested": len(candidates),
        "findings": [f.as_dict() for f in findings],
        "not_verified_enough": [f.as_dict() for f in ungraded],
        "not_verified_warning": (
            "Held back because the QUESTION has not been checked "
            "against enough human-confirmed answers, not because the "
            "result was weak. Confirming more labels is what moves "
            "one of these into the list above."
            if ungraded else ""),
        "account_trends": [f.as_dict() for f in trends],
        "trends_note": (
            "These are counts of elapsed time, not properties of a "
            "video. They are reported because an account that is "
            "growing or fading is worth knowing about, and kept apart "
            "because nothing about them can be acted on when making "
            "the next video."
            if trends else ""),
        "how_to_read": {
            "within_creator": (
                "Every video is scored against its own creator's other "
                "videos, so a 2.7-million-follower account and a "
                "64-follower one contribute on the same scale. Comparing "
                "raw views across creators measures followers."),
            "age": (
                "Views accumulate, and these were read at ages from "
                "days to years, so the age of each video is partialled "
                "out. The uncontrolled figure is reported beside it."),
            "grade": (
                "Observed, and it cannot be more. Associated needs the "
                "ad covariate, which does not exist for anyone but "
                "you."),
            "not_causal": (
                "These say what tended to go together in videos that "
                "already exist. Only the experiment loop can say "
                "anything caused anything."),
            "time_is_not_content": (
                "Measurements that are really a count of elapsed time "
                "are listed separately. One of them came out positive "
                "on this account and negative on other creators', "
                "which is what an imperfectly-controlled time trend "
                "does and what a content property does not."),
        },
    }


def _describes(feature: str) -> str:
    from server.features import publication_meta as pm
    d = pm.describes(feature)
    return d if d != feature else lex.display_name(feature)


# ---------------------------------------------------------------------------
# breakthroughs, which are a different question from "did well"
# ---------------------------------------------------------------------------

#: A publication whose robust z against its own rolling baseline
#: clears this is an outlier for that creator. `breakthrough.py`
#: uses 3.5 for declaring a career change-point; this is lower,
#: because here the job is to collect enough outliers to compare
#: rather than to assert that a creator's level permanently moved.
OUTLIER_Z = 2.0

#: Below this many outliers there is nothing to compare them against.
def _direction_of(entry: dict) -> str:
    """One word for which way a result points, whichever analysis
    produced it.

    `build` says positive/negative; `breakthroughs` says
    higher/lower. Comparing the two needs them in the same words.
    """
    raw = entry.get("direction", "")
    return {"positive": "higher", "negative": "lower"}.get(raw, raw)


def directional_overlap(mine: list[dict], theirs: list[dict]
                        ) -> tuple[list[str], list[dict]]:
    """Measurements found on both sides, split by whether they agree.

    Intersecting on NAME alone was wrong and the error was not
    cosmetic: caption length came out negative on the owner's
    account and positive on other creators', hashtag count the
    same, and both were being presented as "held on both sides" -
    the strongest claim the product makes. Two of eight so-called
    agreements were contradictions.

    A contradiction is worth seeing, not hiding. It usually means
    the thing is about this creator rather than about piano videos,
    which is exactly what a reader needs to know before acting.
    """
    a = {d["display_name"]: _direction_of(d) for d in mine}
    b = {d["display_name"]: _direction_of(d) for d in theirs}
    agree, disagree = [], []
    for name in sorted(set(a) & set(b)):
        if a[name] == b[name]:
            agree.append(name)
        else:
            disagree.append({"display_name": name,
                             "yours": a[name], "theirs": b[name]})
    return agree, disagree


def measurement_grade(feature: str) -> str:
    """How far a measurement can be trusted: the gate Phase 1 set.

    `vision_questions` grades all 42 visual questions, and
    `may_enter_statistics` allows exactly the 9 graded `verified`.
    Nothing in this module consulted it, so an `indicative`
    measurement could become a finding - and one had.
    **"Sheet music visible" was reported as a breakthrough
    difference at q = 0.024, carried into the adherence check and
    handed to the generator**, on a question the project had
    already decided was not reliable enough to support a claim.

    Deterministic measurements are not graded here because they are
    not model answers: an edge-density figure is arithmetic on
    pixels, and whether it is *meaningful* is a separate argument
    from whether it is *accurate*.
    """
    if not feature.startswith("vision_"):
        return "deterministic"
    from server.ai.wide_vision import reliability_of
    return reliability_of(feature)


def may_be_a_finding(feature: str) -> bool:
    """Whether this measurement may support a claim at all."""
    return measurement_grade(feature) in ("deterministic", "verified")


def is_boolean(values) -> bool:
    """Whether a measurement is a yes/no answer stored as 0 and 1."""
    return set(values) <= {0.0, 1.0}


def why_not_testable(a: list[float], b: list[float],
                     floor_b: int = 30) -> str:
    """Why this measurement cannot be compared, or "" if it can.

    Pulled out of the loop and given a reason string after it
    silently discarded **43 of 103 measurements** for the whole life
    of the analysis. The test was:

        if len(a) < 8 or len(b) < floor_b or len(set(a + b)) < 3:

    and a boolean has exactly two distinct values. Every yes/no
    measurement in the project failed it: eleven hook devices, every
    caption shape, whether the music is his own, twenty vision
    questions. The boolean-specific rule underneath was unreachable
    code the entire time, which is why it never caught anything
    either, and why nobody noticed.

    The lesson is in the shape of the thing rather than the
    arithmetic: a silent `continue` inside a loop over a hundred
    measurements cannot be audited by reading the output, because a
    measurement that is never tested looks exactly like one that was
    tested and found nothing. Hence a reason, hence a function with
    a name, hence tests.
    """
    if len(a) < 8:
        return f"only {len(a)} breakthroughs measured, need 8"
    if len(b) < floor_b:
        return f"only {len(b)} others measured, need {floor_b}"
    both = a + b
    if is_boolean(both):
        # A boolean true a handful of times cannot support a
        # comparison, however small its p. Measured: hook_is_what_if
        # and hook_names_celebrity are each true on
        # 3 publications EACH out of 442, and both came out at
        # p = 0.011 against breaking out - which reads as a finding
        # and is two videos. A rank test does not know that two is
        # not a sample; this does. A constant boolean falls out here
        # too, because `min` is then zero.
        trues = sum(both)
        falses = len(both) - trues
        if min(trues, falses) < MIN_BOOLEAN_CASES:
            return (f"only {int(min(trues, falses))} publications on the "
                    f"smaller side, need {MIN_BOOLEAN_CASES}")
        if sum(a) < 3:
            return f"true on only {int(sum(a))} breakthroughs, need 3"
        return ""
    if len(set(both)) < 3:
        return f"only {len(set(both))} distinct values"
    return ""


#: Measurements that describe the DISTANCE to the baseline a
#: publication is scored against, rather than the publication.
#:
#: Breaking out means beating a trailing window of the creator's
#: previous posts. A publication put out 60 days after the last one
#: is compared against a window whose newest member is 60 days old,
#: during which the account kept growing and the platform kept
#: recommending the back catalogue. The longer the gap, the staler
#: the thing it has to beat.
#:
#: That is structural, not a hypothesis: `days_since_previous_post`
#: IS the age gap to the newest member of the window. So when the
#: competitor corpus got deep enough to test cadence, both gap
#: measurements came out as the strongest differences on the
#: breakthrough side - 20.9 days against 11.1 - and "post less
#: often" is exactly the advice this project must not give on the
#: strength of an artefact of its own outlier definition.
#:
#: They are still reported, separately and with the reason, because
#: cadence is a real decision and hiding the measurement would make
#: the next person re-derive it. They are not called findings.
BASELINE_DISTANCE = frozenset({
    "days_since_previous_post",
    "gap_vs_usual",
})

MIN_OUTLIERS = 12

#: A yes/no measurement needs this many of each answer across the
#: whole comparison before a difference in it means anything.
MIN_BOOLEAN_CASES = 12


def _origin_of(session: Session) -> dict[int, str]:
    """Whose music each publication is, by video id."""
    rows = session.execute(
        select(VideoFeature.video_id, VideoFeature.value_text)
        .where(VideoFeature.feature == "content_origin",
               VideoFeature.status == "ok")).all()
    return {vid: text for vid, text in rows if text}


def breakthroughs(session: Session, *, own: bool = False,
                  origin: str | None = None) -> dict:
    """The threshold analysis, remembered briefly.

    See `_breakthroughs` for what it does and why. The wrapper
    exists because four surfaces ask for the same numbers to draw
    one page, and recomputing a few hundred rank tests each time
    made every page a two-second wait.
    """
    from server.research import memo
    return memo.remember(
        "virality.breakthroughs",
        lambda: _breakthroughs(session, own=own, origin=origin),
        session=session, own=own, origin=origin)


def _breakthroughs(session: Session, *, own: bool = False,
                   origin: str | None = None) -> dict:
    """What is different about a creator's outliers.

    The analysis above treats view rank as a smooth gradient, and
    virality is not one. A video at the 60th percentile of its
    creator's distribution is an ordinary video; one at ten times
    their median is a different kind of event, and what separates
    the two is not necessarily what separates 50th from 60th.

    So this asks the threshold question: take every publication that
    beat its creator's own **rolling local baseline** by a wide
    margin, and compare it with everything that did not.

    The rolling baseline is why this is better founded than the
    percentile version. `baseline.rolling_baseline` scores each
    publication against the window that *preceded* it, trailing
    rather than centred, so age is handled by construction instead
    of by partialling out a log - an older post is compared with its
    contemporaries, not with the creator's whole career.
    """
    from server.research.baseline import Observation, rolling_baseline

    rows = session.execute(
        select(Video.id, Video.creator_id, Video.external_id,
               Video.posted_at, Video.caption,
               MetricsSnapshot.views, MetricsSnapshot.captured_at)
        .join(Creator, Creator.id == Video.creator_id)
        .join(MetricsSnapshot, MetricsSnapshot.video_id == Video.id)
        .where(Creator.is_own_account.is_(own),
               MetricsSnapshot.views.is_not(None),
               Video.posted_at.is_not(None))
        .order_by(Video.id, MetricsSnapshot.captured_at.desc())).all()

    seen: set[int] = set()
    by_creator: dict[int, list[Observation]] = {}
    for vid, cid, ext, posted, caption, views, captured in rows:
        if vid in seen:
            continue
        seen.add(vid)
        by_creator.setdefault(cid, []).append(Observation(
            video_id=vid, external_id=ext or str(vid),
            published_at=posted, views=int(views),
            observed_at=captured, caption=caption))

    # The baseline is always built from the creator's WHOLE run.
    # Scoring an original composition only against other original
    # compositions would ask whether it beat the ones around it in
    # a sparse series, which is a different and much noisier
    # question. Breaking out means beating what the account normally
    # does; the filter applies to which outliers are then compared,
    # not to what they were compared against.
    origins = _origin_of(session) if origin else {}

    is_outlier: dict[int, bool] = {}
    z_by_video: dict[int, float] = {}
    for cid, obs in by_creator.items():
        if len(obs) < MIN_PER_CREATOR:
            continue
        obs.sort(key=lambda o: o.published_at)
        for point in rolling_baseline(obs):
            # The id lives on the observation, not the point: a
            # BaselinePoint is a scoring OF an observation and keeps
            # it rather than copying its fields.
            vid = point.observation.video_id
            z = point.robust_z
            if vid is None or z is None:
                continue          # too few prior posts to score against
            z_by_video[vid] = float(z)
            is_outlier[vid] = float(z) >= OUTLIER_Z

    if origin:
        is_outlier = {v: flag for v, flag in is_outlier.items()
                      if origins.get(v) == origin}

    n_out = sum(1 for v in is_outlier.values() if v)
    if n_out < MIN_OUTLIERS:
        return {
            "outliers": n_out, "compared": len(is_outlier),
            "origin": origin, "differences": [],
            "why_empty": (
                f"Only {n_out} "
                f"{origin.replace('_', ' ') + ' ' if origin else ''}"
                f"publications cleared their own creator's "
                f"rolling baseline by {OUTLIER_Z} robust deviations. "
                f"Below {MIN_OUTLIERS} there is nothing to compare, and "
                f"a difference computed on a handful of outliers would "
                f"be a description of those few videos rather than of "
                f"breakthroughs."),
        }

    ids = sorted(is_outlier)
    feats = _feature_values(session, ids)
    hits = [v for v in ids if is_outlier[v]]
    rest = [v for v in ids if not is_outlier[v]]

    from server.research.cohort import _mann_whitney_p

    cands = []
    for feature, values in sorted(feats.items()):
        if feature in TIME_PROXIES:
            continue
        a = [values[v] for v in hits if v in values]
        b = [values[v] for v in rest if v in values]
        floor_b = 30 if origin is None else 15
        if why_not_testable(a, b, floor_b):
            continue
        p = _mann_whitney_p(a, b)
        if p is None:
            continue
        ma = sum(a) / len(a)
        mb = sum(b) / len(b)
        cands.append((feature, len(a), len(b), ma, mb, p))

    qs = benjamini_hochberg([c[5] for c in cands])
    out, weak, cadence, unverified = [], [], [], []
    for (feature, na, nb, ma, mb, p), q in zip(cands, qs):
        if q is None or q > Q_THRESHOLD:
            # Did not survive correction. Kept separately and never
            # called a finding: splitting by category leaves few
            # outliers per cell, and the strongest uncorrected signal
            # in a thin cell is exactly what noise looks like. It is
            # shown because knowing what to watch is useful and
            # because hiding it would make the next analyst redo it.
            if p is not None and p <= 0.05 and len(out) == 0:
                weak.append({
                    "feature": feature,
                    "display_name": lex.display_name(feature),
                    "describes": _describes(feature),
                    "outliers_mean": round(ma, 4),
                    "others_mean": round(mb, 4),
                    "direction": "higher" if ma > mb else "lower",
                    "n_outliers": na, "n_others": nb,
                    "p_value": round(p, 5), "q_value": round(q, 5),
                })
            continue
        record = {
            "feature": feature,
            "display_name": lex.display_name(feature),
            # Carried so a reader downstream can phrase it. "Had
            # more colour intensity" works; "had more the form is
            # named" does not, and a yes/no measurement needs to be
            # described as a share rather than an amount.
            "is_boolean": is_boolean(a + b),
            "describes": _describes(feature),
            "outliers_mean": round(ma, 4),
            "others_mean": round(mb, 4),
            "direction": "higher" if ma > mb else "lower",
            "n_outliers": na, "n_others": nb,
            "p_value": round(p, 5), "q_value": round(q, 5),
            "grade": "Observed",
            "plain": (
                f"Publications that broke out had "
                f"{'more' if ma > mb else 'less'} "
                f"{lex.display_name(feature).lower()} than the ones "
                f"that did not - {ma:.3g} against {mb:.3g}."),
        }
        if not may_be_a_finding(feature):
            # Shown, never called a finding. The grade travels with
            # it so a reader can see WHY it is set aside, rather
            # than wondering why something interesting is in the
            # second list.
            record["grade"] = "Unavailable"
            record["reliability"] = measurement_grade(feature)
            record["plain"] = (
                f"{record['plain']} This question has not been "
                f"checked against enough human-confirmed answers to "
                f"support a claim, so it is reported and not "
                f"counted.")
            unverified.append(record)
            continue
        if feature in BASELINE_DISTANCE:
            record["grade"] = "Unavailable"
            record["plain"] = (
                f"{record['plain']} This one cannot be read as a "
                f"property of the video: a publication put out after "
                f"a long gap is compared against an older window, "
                f"which the account has had longer to outgrow.")
            cadence.append(record)
            continue
        out.append(record)
    out.sort(key=lambda d: d["q_value"])
    cadence.sort(key=lambda d: d["q_value"])
    unverified.sort(key=lambda d: d["q_value"])
    return {
        "outliers": n_out, "compared": len(is_outlier),
        "origin": origin,
        "creators": len([c for c, o in by_creator.items()
                         if len(o) >= MIN_PER_CREATOR]),
        "outlier_threshold_z": OUTLIER_Z,
        "differences": out,
        # Reported, never called a finding. See `BASELINE_DISTANCE`.
        "cadence_against_the_baseline": cadence,
        # Likewise, for a different reason: the question itself is
        # not reliable enough yet. See `measurement_grade`.
        "not_verified_enough": unverified,
        "not_verified_warning": (
            "These came out of the same test as the findings above "
            "and are held back because the QUESTION has not been "
            "checked against enough human-confirmed answers, not "
            "because the result was weak. Confirming more labels on "
            "'Check uncertain labels' is what moves one of these "
            "into the list above."
            if unverified else ""),
        "cadence_warning": (
            "Breaking out means beating a trailing window of earlier "
            "posts, so a publication put out after a long gap is "
            "measured against an older and smaller audience. These "
            "two measurements are the size of that gap. They came "
            "out as the largest differences on this corpus, and the "
            "advice they appear to give - post less often - is "
            "exactly what an artefact of the method would look "
            "like. Settling it needs a baseline anchored to a fixed "
            "age rather than to the previous publications."
            if cadence else ""),
        "weak_signals": sorted(weak, key=lambda d: d["p_value"])[:8],
        "weak_signals_warning": (
            "None of these survived correction for testing many "
            "measurements at once. They are what to watch, not what "
            "to act on, and in a cell this small the strongest "
            "uncorrected signal is exactly what noise looks like."
            if weak else ""),
        "how_to_read": {
            "why_a_threshold": (
                "Breaking out is not the top of a gradient. A video at "
                "ten times its creator's median is a different kind of "
                "event from one at 1.2 times, and what separates those "
                "two is not necessarily what separates an average video "
                "from a slightly-above-average one."),
            "why_this_handles_age": (
                "Each publication is scored against the window that "
                "preceded it, so an old post is compared with its "
                "contemporaries rather than with the creator's whole "
                "career. Age is handled by construction here, not by "
                "partialling out a log."),
            "still_not_causal": (
                "A breakthrough is also the thing most likely to have "
                "been luck: short-form distribution is path-dependent "
                "and a better first hour compounds. These differences "
                "are what outliers had in common, not what made them "
                "outliers."),
        },
    }
