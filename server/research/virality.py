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
        if len(set(xs)) < 3:
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
        findings.append(f)

    trends = [f for f in findings if f.feature in TIME_PROXIES]
    findings = [f for f in findings if f.feature not in TIME_PROXIES]
    findings.sort(key=lambda f: -abs(f.partial_rho or 0))
    trends.sort(key=lambda f: -abs(f.partial_rho or 0))
    return {
        "n": len(pop), "creators": n_creators,
        "measurements_tested": len(candidates),
        "findings": [f.as_dict() for f in findings],
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
MIN_OUTLIERS = 12


def breakthroughs(session: Session, *, own: bool = False) -> dict:
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

    n_out = sum(1 for v in is_outlier.values() if v)
    if n_out < MIN_OUTLIERS:
        return {
            "outliers": n_out, "compared": len(is_outlier),
            "differences": [],
            "why_empty": (
                f"Only {n_out} publications cleared their own creator's "
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
        if len(a) < 8 or len(b) < 30 or len(set(a + b)) < 3:
            continue
        p = _mann_whitney_p(a, b)
        if p is None:
            continue
        ma = sum(a) / len(a)
        mb = sum(b) / len(b)
        cands.append((feature, len(a), len(b), ma, mb, p))

    qs = benjamini_hochberg([c[5] for c in cands])
    out = []
    for (feature, na, nb, ma, mb, p), q in zip(cands, qs):
        if q is None or q > Q_THRESHOLD:
            continue
        out.append({
            "feature": feature,
            "display_name": lex.display_name(feature),
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
        })
    out.sort(key=lambda d: d["q_value"])
    return {
        "outliers": n_out, "compared": len(is_outlier),
        "creators": len([c for c, o in by_creator.items()
                         if len(o) >= MIN_PER_CREATOR]),
        "outlier_threshold_z": OUTLIER_Z,
        "differences": out,
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
