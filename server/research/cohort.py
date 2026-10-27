"""The owner against a saved cohort, and what that cannot tell you.

M6. Formats, hooks, visual presentation, editing and cadence compared
across creators - "always with audience size, data age, completeness
and platform visible on the same screen as the comparison, so a
61-follower account and an 853k one are never silently averaged".

## The comparison this can make

For every measurement both sides carry, how the owner's publications
sit against the cohort's. Reported as a rank comparison rather than a
difference of means: these distributions are skewed, a single viral
outlier moves a mean and does not move a median, and the question is
"are the owner's openings typically busier than this cohort's", not
"by how many edge-pixels".

**Per creator, then across creators.** Each cohort member's own
median is computed first and the comparison runs over those twelve
numbers, not over 205 publications pooled. Pooling would let the
creator with 32 publications outvote the one with 8, and the result
would describe that creator rather than the cohort. It costs
statistical power and buys the thing the brief asks for.

## The comparison this cannot make, and says so

**No competitor retention data exists.** TikTok's API returns no
watch-time or skip metric for anybody, and nothing can be scraped
that would stand up. So a difference found here is a difference in
*what the videos are*, never in *what the videos achieve*. The
honest form is "your openings carry more visual detail than this
cohort's", and never "...which is why they perform better", because
the second half is unmeasurable with the data that exists.

Every finding here is therefore graded `Observed` at most. The ladder
in `features/catalogue.py` requires an outcome for anything higher,
and a cross-creator difference has none available.
"""

from __future__ import annotations

import logging
import math
import statistics
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from server.db.models import (Creator, CreatorCollection,
                              CreatorCollectionMember, Video, VideoFeature)
from server.features import lexicon as lex
from server.features.retention import benjamini_hochberg
# Deterministic measurements plus whatever the vision
# benchmark actually promoted. A withheld field may not
# enter an analysis however convenient it would be here,
# and `VISION_FEATURES` is exactly the list that passed.
from server.features.visual_structure import (
    STRUCTURE_FEATURES, VISION_FEATURES)

logger = logging.getLogger("virallab.research.cohort")

#: Below this many cohort members a comparison is a comparison with a
#: handful of people. M6 names ten and so does this.
MIN_MEMBERS = 10

#: A member needs enough publications for a median of their own.
MIN_PUBLICATIONS = 5

@dataclass
class CohortMember:
    creator_id: int
    handle: str
    platform: str
    followers: int | None
    publications: int
    measured: int
    oldest_post: datetime | None = None
    newest_post: datetime | None = None


@dataclass
class Difference:
    """One measurement, owner against cohort."""

    feature: str
    display_name: str
    owner_n: int
    owner_median: float | None
    cohort_members: int
    cohort_median_of_medians: float | None
    cohort_low: float | None = None
    cohort_high: float | None = None
    #: Where the owner sits among the cohort's own medians, 0 to 1.
    owner_percentile: float | None = None
    #: Two-sided p from a rank test of the owner's publications
    #: against the cohort members' medians.
    p_value: float | None = None
    q_value: float | None = None
    direction: str = ""
    plain: str = ""
    grade: str = "Observed"
    note: str = ""

    def as_dict(self) -> dict:
        return {
            "feature": self.feature,
            "display_name": self.display_name,
            "plain": self.plain,
            "owner_n": self.owner_n,
            "owner_median": self.owner_median,
            "cohort_members": self.cohort_members,
            "cohort_median": self.cohort_median_of_medians,
            "cohort_range": ([self.cohort_low, self.cohort_high]
                             if self.cohort_low is not None else None),
            "owner_percentile": self.owner_percentile,
            "direction": self.direction,
            "grade": self.grade,
            "note": self.note,
            "statistics": {"p_value": self.p_value,
                           "q_value": self.q_value},
        }


@dataclass
class CohortComparison:
    collection_id: int
    collection_name: str
    members: list[CohortMember] = field(default_factory=list)
    differences: list[Difference] = field(default_factory=list)
    owner_handle: str = ""
    owner_platform: str = ""
    meets_minimum: bool = False
    limitations: list[str] = field(default_factory=list)
    composition: dict = field(default_factory=dict)


def _mann_whitney_p(a: list[float], b: list[float]) -> float | None:
    """Two-sided rank-sum p, normal approximation with tie correction.

    Written out rather than pulled from scipy: the project has no
    scipy dependency and adding one for a single test would be a new
    package for convenience, which CLAUDE.md §6 asks us not to do.
    The normal approximation is adequate at these sizes and its
    limitation is stated beside the number rather than hidden.
    """
    n1, n2 = len(a), len(b)
    if n1 < 3 or n2 < 3:
        return None
    pooled = sorted([(v, 0) for v in a] + [(v, 1) for v in b])
    ranks: list[float] = [0.0] * len(pooled)
    i = 0
    tie_term = 0.0
    while i < len(pooled):
        j = i
        while j + 1 < len(pooled) and pooled[j + 1][0] == pooled[i][0]:
            j += 1
        avg = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[k] = avg
        t = j - i + 1
        if t > 1:
            tie_term += t ** 3 - t
        i = j + 1

    r1 = sum(r for r, (_v, g) in zip(ranks, pooled) if g == 0)
    u1 = r1 - n1 * (n1 + 1) / 2.0
    mu = n1 * n2 / 2.0
    n = n1 + n2
    var = n1 * n2 / 12.0 * ((n + 1) - tie_term / (n * (n - 1)))
    if var <= 0:
        return None
    z = (u1 - mu) / math.sqrt(var)
    return round(math.erfc(abs(z) / math.sqrt(2)), 6)


def _percentile_of(value: float, among: list[float]) -> float | None:
    if not among:
        return None
    below = sum(1 for x in among if x < value)
    same = sum(1 for x in among if x == value)
    return round((below + 0.5 * same) / len(among), 3)


def _feature_values(session: Session, feature: str, method_version: str,
                    creator_ids: list[int]) -> dict[int, list[float]]:
    """creator_id -> their stored values for one measurement."""
    if not creator_ids:
        return {}
    rows = session.execute(
        select(Video.creator_id, VideoFeature.value_num)
        .join(VideoFeature, VideoFeature.video_id == Video.id)
        .where(Video.creator_id.in_(creator_ids),
               VideoFeature.feature == feature,
               VideoFeature.method_version == method_version,
               VideoFeature.status == "ok",
               VideoFeature.value_num.is_not(None))).all()
    out: dict[int, list[float]] = {}
    for cid, val in rows:
        if val is None or val != val:        # NaN never enters a median
            continue
        out.setdefault(cid, []).append(float(val))
    return out


def members(session: Session, collection_id: int) -> list[CohortMember]:
    rows = session.execute(
        select(Creator.id, Creator.handle, Creator.platform,
               Creator.follower_count, func.count(Video.id),
               func.min(Video.posted_at), func.max(Video.posted_at))
        .join(CreatorCollectionMember,
              CreatorCollectionMember.creator_id == Creator.id)
        .outerjoin(Video, Video.creator_id == Creator.id)
        .where(CreatorCollectionMember.collection_id == collection_id)
        .group_by(Creator.id, Creator.handle, Creator.platform,
                  Creator.follower_count)
        .order_by(func.coalesce(Creator.follower_count, 0).desc())).all()

    out = []
    for cid, handle, plat, foll, pubs, oldest, newest in rows:
        measured = session.scalar(
            select(func.count(func.distinct(VideoFeature.video_id)))
            .join(Video, Video.id == VideoFeature.video_id)
            .where(Video.creator_id == cid,
                   VideoFeature.status == "ok")) or 0
        out.append(CohortMember(
            creator_id=cid, handle=handle, platform=plat,
            followers=foll, publications=pubs, measured=measured,
            oldest_post=oldest, newest_post=newest))
    return out


def compare(session: Session, collection_id: int, *,
            owner_platform: str = "tiktok") -> CohortComparison:
    """The owner against one saved cohort, on every shared measurement."""
    coll = session.get(CreatorCollection, collection_id)
    if coll is None:
        raise ValueError(f"no such collection {collection_id}")

    owner = session.scalar(select(Creator).where(
        Creator.is_own_account.is_(True),
        Creator.platform == owner_platform))

    rep = CohortComparison(
        collection_id=collection_id, collection_name=coll.name,
        owner_handle=owner.handle if owner else "",
        owner_platform=owner_platform)
    rep.members = [m for m in members(session, collection_id)
                   if m.platform == owner_platform]
    usable = [m for m in rep.members
              if m.measured >= MIN_PUBLICATIONS]
    rep.meets_minimum = len(usable) >= MIN_MEMBERS

    folls = [m.followers for m in rep.members if m.followers is not None]
    rep.composition = {
        "members": len(rep.members),
        "members_with_enough_measured": len(usable),
        "minimum_required": MIN_MEMBERS,
        "min_publications_per_member": MIN_PUBLICATIONS,
        "publications_in_cohort": sum(m.publications for m in rep.members),
        "publications_measured": sum(m.measured for m in rep.members),
        "follower_range": ([min(folls), max(folls)] if folls else None),
        "platform": owner_platform,
        "oldest_post": min((m.oldest_post for m in rep.members
                            if m.oldest_post), default=None),
        "newest_post": max((m.newest_post for m in rep.members
                            if m.newest_post), default=None),
    }

    if owner is None:
        rep.limitations.append(
            f"No own {owner_platform} account is recorded, so there is "
            f"nothing to compare the cohort against.")
        return rep
    if not usable:
        rep.limitations.append(
            "No cohort member has enough measured publications yet. "
            "Collecting a creator's back catalogue and measuring it "
            "are separate steps, and only the first has run.")
        return rep

    ids = [m.creator_id for m in usable]
    candidates = list(STRUCTURE_FEATURES) + [
        (name, "hook-vision-gemma3-4b-v2") for name in VISION_FEATURES]

    raw: list[Difference] = []
    for feature, mv in candidates:
        own_vals = _feature_values(session, feature, mv,
                                   [owner.id]).get(owner.id, [])
        per_creator = _feature_values(session, feature, mv, ids)
        medians = [statistics.median(v) for v in per_creator.values()
                   if len(v) >= 3]
        if len(own_vals) < 5 or len(medians) < MIN_MEMBERS // 2:
            continue

        own_median = statistics.median(own_vals)
        d = Difference(
            feature=feature,
            display_name=lex.display_name(feature),
            owner_n=len(own_vals),
            owner_median=round(own_median, 4),
            cohort_members=len(medians),
            cohort_median_of_medians=round(statistics.median(medians), 4),
            cohort_low=round(min(medians), 4),
            cohort_high=round(max(medians), 4),
            owner_percentile=_percentile_of(own_median, medians),
            p_value=_mann_whitney_p(own_vals, medians),
        )
        higher = own_median > (d.cohort_median_of_medians or 0)
        d.direction = "higher" if higher else "lower"
        name = d.display_name.lower()
        # "carry more {name}" breaks on a display name that is already
        # a phrase - "carry less how much of the frame holds still" -
        # so the comparison goes on the verb instead of the noun, which
        # reads correctly whatever shape the name has.
        d.plain = (
            f"On {name}, your openings score "
            f"{'higher' if higher else 'lower'} than this cohort "
            f"typically does.")
        d.note = (
            f"Your median across {len(own_vals)} publications against "
            f"the median of {len(medians)} creators' own medians. "
            f"Compared creator by creator so the busiest account "
            f"cannot outvote the rest.")
        raw.append(d)

    qs = benjamini_hochberg([d.p_value for d in raw])
    for d, q in zip(raw, qs):
        d.q_value = q
    rep.differences = sorted(
        raw, key=lambda d: (d.q_value if d.q_value is not None else 1.0))

    rep.limitations = [
        ("No retention, watch-time or skip data exists for anybody but "
         "you. Every difference here is a difference in what the videos "
         "ARE, never in what they achieve - nothing on this page can "
         "say a difference is why anyone performs better or worse."),
        (f"The cohort spans {rep.composition['follower_range'][0]:,} to "
         f"{rep.composition['follower_range'][1]:,} followers"
         if rep.composition.get("follower_range") else
         "Follower counts are unknown for this cohort") +
        (". Audience size is shown per creator and never averaged "
         "away; a measurement that differs between a 64-follower "
         "account and a 2.7-million-follower one may be about "
         "audience rather than craft."),
        ("Everything here is graded Observed. A cross-creator "
         "difference has no outcome to be tested against, so it "
         "cannot reach Associated however large it is."),
        ("Competitor publications are measured from a sample of each "
         "creator's catalogue, not all of it, and the media was "
         "deleted after measurement under the six-hour rule "
         "(ADR 0009)."),
    ]
    return rep
