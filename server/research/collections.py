"""Named groups of creators, and normalised comparison within a group.

A collection is a membership list and nothing more. Every figure shown for
one is derived from the observations at read time, so changing an analytical
policy can never corrupt a saved group - the same rule that governs
baselines and breakthroughs (`docs/SCHEMA_PLAN.md`).

`purpose` exists because two groups that look identical are not. A
`research` group is "accounts I want to learn from"; a `control_cohort` is
"accounts that did NOT break through, in the same niche", which is the
prerequisite `docs/SCHEMA_PLAN.md` 3 names before any pattern may be
promoted above `hypothesis`. A later cohort analysis has to be able to tell
them apart, and asking the owner to remember which was which is not a
design.

**The comparison rule.** Creators in a collection are compared on
*within-creator relative performance*, never on lifetime views. A creator
with more lifetime views has a bigger audience; that is not evidence they
make better content, and ranking a collection by total views would state
exactly that. So the collection view reports, per creator, how their best
and typical publications sit against *their own* trailing baseline, and
labels the age confound it cannot remove.
"""

from __future__ import annotations

import logging
import statistics
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from server.db.models import (BreakthroughCandidate, Creator,
                              CreatorCollection, CreatorCollectionMember,
                              MetricsSnapshot, Video)
from server.research import baseline as bl
from server.research.service import observations_for

logger = logging.getLogger("virallab.research.collections")

PURPOSES = ("research", "control_cohort", "watchlist", "inspiration")

PURPOSE_LABELS = {
    "research": "Accounts to learn from",
    "control_cohort": "Control group - did NOT break through, same niche",
    "watchlist": "Watching for changes",
    "inspiration": "Formats and ideas",
}


class CollectionError(RuntimeError):
    """A collection operation was refused, and why."""


@dataclass
class MemberView:
    """One creator inside a collection, with within-creator figures only."""

    creator_id: int
    platform: str
    handle: str
    display_name: str | None
    follower_count: int | None
    is_fixture: bool
    research_status: str
    last_refreshed_at: datetime | None
    publications_collected: int
    notes: str | None
    reason: str | None
    added_at: datetime | None
    #: Observations available for this creator, which bounds everything else.
    observations: int = 0
    publications_observed: int = 0
    #: Median views across observed publications. Reported because it is
    #: asked for, labelled because it mixes publication ages.
    median_views: int | None = None
    #: The honest comparison: how the best publication sat against this
    #: creator's OWN trailing baseline.
    best_relative_performance: float | None = None
    typical_relative_performance: float | None = None
    #: Age spread of the observed window, which is the size of the confound.
    age_span_days: int | None = None
    breakthrough_classification: str | None = None
    breakthrough_at: datetime | None = None
    #: Days between publications, from observed dates only.
    median_days_between_posts: float | None = None
    caveats: list[str] = field(default_factory=list)


@dataclass
class CollectionView:
    id: int
    name: str
    purpose: str
    purpose_label: str
    niche: str | None
    notes: str | None
    created_at: datetime | None
    updated_at: datetime | None
    members: list[MemberView] = field(default_factory=list)
    member_count: int = 0
    researched_count: int = 0
    stalest_refresh: datetime | None = None
    #: Members needing a refresh before the group can be compared fairly.
    needs_refresh: list[str] = field(default_factory=list)
    caveats: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# mutation
# ---------------------------------------------------------------------------

def create(session: Session, *, name: str, purpose: str = "research",
           niche: str | None = None, notes: str | None = None
           ) -> CreatorCollection:
    name = (name or "").strip()
    if not name:
        raise CollectionError("a collection needs a name")
    if purpose not in PURPOSES:
        raise CollectionError(
            f"unknown purpose {purpose!r}; expected one of {PURPOSES}")
    existing = session.scalar(
        select(CreatorCollection).where(CreatorCollection.name == name))
    if existing is not None:
        raise CollectionError(f"a collection named {name!r} already exists")
    row = CreatorCollection(name=name, purpose=purpose, niche=niche,
                            notes=notes)
    session.add(row)
    session.flush()
    logger.info("created collection %s (%s)", name, purpose)
    return row


def update(session: Session, collection_id: int, *,
           name: str | None = None, purpose: str | None = None,
           niche: str | None = None, notes: str | None = None
           ) -> CreatorCollection:
    row = session.get(CreatorCollection, collection_id)
    if row is None:
        raise CollectionError(f"no such collection {collection_id}")
    if purpose is not None:
        if purpose not in PURPOSES:
            raise CollectionError(f"unknown purpose {purpose!r}")
        row.purpose = purpose
    if name is not None:
        if not name.strip():
            raise CollectionError("a collection needs a name")
        row.name = name.strip()
    if niche is not None:
        row.niche = niche or None
    if notes is not None:
        row.notes = notes or None
    row.updated_at = datetime.now(timezone.utc)
    session.flush()
    return row


def add_member(session: Session, collection_id: int, creator_id: int, *,
               reason: str | None = None, notes: str | None = None,
               discovered_by_job_id: int | None = None
               ) -> CreatorCollectionMember:
    """Add a creator, idempotently.

    Adding twice updates the annotation rather than failing: the unique
    constraint exists to keep the membership a set, not to punish a
    double-click.
    """
    if session.get(CreatorCollection, collection_id) is None:
        raise CollectionError(f"no such collection {collection_id}")
    creator = session.get(Creator, creator_id)
    if creator is None:
        raise CollectionError(f"no such creator {creator_id}")
    if creator.is_own_account:
        raise CollectionError(
            "this is one of your own accounts. Collections group creators "
            "you are researching; your own accounts are the dashboard.")

    row = session.scalar(
        select(CreatorCollectionMember).where(
            CreatorCollectionMember.collection_id == collection_id,
            CreatorCollectionMember.creator_id == creator_id))
    if row is None:
        row = CreatorCollectionMember(
            collection_id=collection_id, creator_id=creator_id,
            reason=reason, notes=notes,
            discovered_by_job_id=discovered_by_job_id)
        session.add(row)
    else:
        if reason is not None:
            row.reason = reason
        if notes is not None:
            row.notes = notes
    session.flush()
    return row


def remove_member(session: Session, collection_id: int,
                  creator_id: int) -> bool:
    row = session.scalar(
        select(CreatorCollectionMember).where(
            CreatorCollectionMember.collection_id == collection_id,
            CreatorCollectionMember.creator_id == creator_id))
    if row is None:
        return False
    session.delete(row)
    session.flush()
    return True


def delete(session: Session, collection_id: int) -> bool:
    """Remove a collection. Memberships cascade; no creator is deleted."""
    row = session.get(CreatorCollection, collection_id)
    if row is None:
        return False
    session.delete(row)
    session.flush()
    logger.info("deleted collection %s", collection_id)
    return True


# ---------------------------------------------------------------------------
# reading
# ---------------------------------------------------------------------------

def list_collections(session: Session) -> list[dict]:
    rows = session.scalars(
        select(CreatorCollection).order_by(CreatorCollection.name)).all()
    out = []
    for c in rows:
        n = session.scalar(
            select(func.count(CreatorCollectionMember.id))
            .where(CreatorCollectionMember.collection_id == c.id)) or 0
        researched = session.scalar(
            select(func.count(CreatorCollectionMember.id))
            .join(Creator, Creator.id == CreatorCollectionMember.creator_id)
            .where(CreatorCollectionMember.collection_id == c.id,
                   Creator.research_status == "collected")) or 0
        stalest = session.scalar(
            select(func.min(Creator.last_refreshed_at))
            .join(CreatorCollectionMember,
                  CreatorCollectionMember.creator_id == Creator.id)
            .where(CreatorCollectionMember.collection_id == c.id))
        out.append({
            "id": c.id, "name": c.name, "purpose": c.purpose,
            "purpose_label": PURPOSE_LABELS.get(c.purpose, c.purpose),
            "niche": c.niche, "notes": c.notes,
            "created_at": c.created_at, "updated_at": c.updated_at,
            "member_count": n, "researched_count": researched,
            "stalest_refresh": stalest})
    return out


def view(session: Session, collection_id: int) -> CollectionView:
    """A collection with per-creator, within-creator comparison figures."""
    c = session.get(CreatorCollection, collection_id)
    if c is None:
        raise CollectionError(f"no such collection {collection_id}")

    members = session.execute(
        select(CreatorCollectionMember, Creator)
        .join(Creator, Creator.id == CreatorCollectionMember.creator_id)
        .where(CreatorCollectionMember.collection_id == collection_id)
        .order_by(Creator.handle)).all()

    views: list[MemberView] = []
    for member, creator in members:
        views.append(_member_view(session, member, creator))

    out = CollectionView(
        id=c.id, name=c.name, purpose=c.purpose,
        purpose_label=PURPOSE_LABELS.get(c.purpose, c.purpose),
        niche=c.niche, notes=c.notes, created_at=c.created_at,
        updated_at=c.updated_at, members=views, member_count=len(views),
        researched_count=sum(1 for v in views
                             if v.research_status == "collected"),
        stalest_refresh=min(
            (v.last_refreshed_at for v in views
             if v.last_refreshed_at is not None), default=None),
        needs_refresh=[v.handle for v in views
                       if v.research_status != "collected"])

    out.caveats.append(
        "Creators here are compared on performance against their OWN "
        "trailing baseline, never on lifetime views. A bigger account has a "
        "bigger audience; that is not evidence it makes better content.")
    if out.needs_refresh:
        out.caveats.append(
            f"{len(out.needs_refresh)} member(s) have no collected "
            f"publication history yet, so they contribute nothing to a "
            f"comparison: {', '.join(out.needs_refresh[:5])}"
            + ("..." if len(out.needs_refresh) > 5 else ""))
    if any(v.observations and v.observations == v.publications_observed
           for v in views):
        out.caveats.append(
            "Most members have one observation per publication, so no "
            "publication's growth over time is visible and nothing here is "
            "a trajectory.")
    if c.purpose != "control_cohort":
        out.caveats.append(
            "This group was not assembled as a control cohort. Patterns "
            "found inside it are hypotheses: without creators who did NOT "
            "break through, there is nothing to distinguish a breakthrough "
            "pattern from a niche-wide one.")
    return out


def _member_view(session: Session, member: CreatorCollectionMember,
                 creator: Creator) -> MemberView:
    obs = observations_for(session, creator.id)
    mv = MemberView(
        creator_id=creator.id, platform=creator.platform,
        handle=creator.handle, display_name=creator.display_name,
        follower_count=creator.follower_count,
        is_fixture=bool(creator.is_fixture),
        research_status=creator.research_status,
        last_refreshed_at=creator.last_refreshed_at,
        publications_collected=creator.publications_collected or 0,
        notes=member.notes, reason=member.reason, added_at=member.added_at)

    total_obs = session.scalar(
        select(func.count(MetricsSnapshot.id))
        .join(Video, Video.id == MetricsSnapshot.video_id)
        .where(Video.creator_id == creator.id)) or 0
    mv.observations = total_obs
    mv.publications_observed = len(obs)

    if not obs:
        mv.caveats.append("No observations yet. Research this creator first.")
        return mv

    views = [o.views for o in obs if o.views is not None]
    if views:
        mv.median_views = int(statistics.median(views))

    points = bl.rolling_baseline(obs)
    rels = [p.relative_performance for p in points
            if p.relative_performance is not None]
    if rels:
        mv.best_relative_performance = round(max(rels), 2)
        mv.typical_relative_performance = round(statistics.median(rels), 2)
    else:
        mv.caveats.append(
            "Too few publications to form a trailing baseline, so no "
            "relative figure can be computed. Shown as unknown, not 1.0.")

    dates = sorted(o.published_at for o in obs if o.published_at is not None)
    if len(dates) >= 2:
        gaps = [(b - a).total_seconds() / 86400.0
                for a, b in zip(dates, dates[1:])]
        mv.median_days_between_posts = round(statistics.median(gaps), 1)
        ages = [(o.observed_at - o.published_at).days for o in obs
                if o.published_at is not None and o.observed_at is not None]
        if ages:
            mv.age_span_days = max(ages) - min(ages)
            if mv.age_span_days > 60:
                mv.caveats.append(
                    f"Observed publications span {mv.age_span_days} days of "
                    f"age, so a single median mixes posts with very "
                    f"different amounts of time to accumulate views.")

    cand = session.scalars(
        select(BreakthroughCandidate)
        .where(BreakthroughCandidate.creator_id == creator.id)
        .order_by(BreakthroughCandidate.id.desc()).limit(1)).first()
    if cand is not None:
        mv.breakthrough_classification = cand.classification
        # There is no stored change-point date: the boundary is identified
        # by the publication at it, so the date comes from that publication.
        if cand.video_id is not None:
            trigger = session.get(Video, cand.video_id)
            if trigger is not None:
                mv.breakthrough_at = trigger.posted_at
    return mv


def collections_for_creator(session: Session, creator_id: int) -> list[dict]:
    """Which collections a creator belongs to, for the creator report."""
    rows = session.execute(
        select(CreatorCollection, CreatorCollectionMember)
        .join(CreatorCollectionMember,
              CreatorCollectionMember.collection_id == CreatorCollection.id)
        .where(CreatorCollectionMember.creator_id == creator_id)
        .order_by(CreatorCollection.name)).all()
    return [{"id": c.id, "name": c.name, "purpose": c.purpose,
             "reason": m.reason, "notes": m.notes, "added_at": m.added_at}
            for c, m in rows]
