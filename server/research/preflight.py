"""What a paid operation would actually buy, before it is paid for.

The existing reuse window already stops an identical search re-running
within a few hours. It does not answer the question that actually wastes
money, which is subtler:

    "I already hold 40 of this creator's publications. If I research them
     again, what am I paying for?"

Provider billing is per returned item, not per new item. A creator research
run that returns 40 publications costs the same whether all 40 are already
in the database or none of them are. So the only way not to burn credit on
work already done is to **look before spending**, and that is what this
module is for.

It never blocks on its own. It reports: what is already held, how fresh it
is, what the run is estimated to cost, and what - on the evidence of the
last comparable run - is likely to be genuinely new. The caller decides.
A guard that silently refuses is as unhelpful as one that silently spends.

Two things it deliberately does **not** do:

* Guess the overlap. The estimate of "likely new" comes from the measured
  new-vs-seen ratio of previous runs of the same shape, and when there is
  no such history it says so rather than inventing a number.
* Treat "already held" as "already analysed". A publication's metadata
  being present says nothing about whether its media was analysed; that is
  a separate ledger (`analysis_gap`), because re-paying a provider and
  re-running a GPU are different costs with different triggers.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from server.db.models import (Creator, DiscoveryJob, MetricsSnapshot, Video,
                              VideoFeature)

logger = logging.getLogger("virallab.research.preflight")

#: Beyond this, held data is old enough that a refresh is defensible even
#: though it will re-pay for publications already known.
STALE_AFTER = timedelta(days=14)

#: How far back to look when estimating the new-vs-seen ratio.
HISTORY_JOBS = 5


@dataclass
class Holding:
    """What is already in the database for this target."""

    creator_id: int | None = None
    handle: str | None = None
    platform: str | None = None
    publications: int = 0
    with_media: int = 0
    analysed: int = 0
    oldest_observed: datetime | None = None
    newest_observed: datetime | None = None
    follower_count: int | None = None

    @property
    def age_days(self) -> int | None:
        if self.newest_observed is None:
            return None
        now = datetime.now(timezone.utc)
        seen = self.newest_observed
        if seen.tzinfo is None:
            seen = seen.replace(tzinfo=timezone.utc)
        return max(0, (now - seen).days)


@dataclass
class Preflight:
    kind: str = ""
    platform: str = ""
    value: str = ""
    limit: int = 0
    estimated_usd: float = 0.0
    is_fixture: bool = True

    #: An identical search inside the reuse window, if there is one.
    reusable_job_id: int | None = None
    reusable_age_hours: float | None = None

    holding: Holding = field(default_factory=Holding)

    #: Measured from previous runs of this shape. None when no history.
    observed_new_ratio: float | None = None
    observed_from_jobs: int = 0
    likely_new: int | None = None
    likely_already_held: int | None = None

    #: free | reuse | refresh | new
    recommendation: str = "new"
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def wasted_usd_estimate(self) -> float | None:
        """Share of the estimate that would buy rows already held."""
        if self.likely_already_held is None or not self.limit:
            return None
        return round(self.estimated_usd
                     * (self.likely_already_held / self.limit), 4)


def _holding_for_creator(session: Session, platform: str,
                         handle: str) -> Holding:
    want = (handle or "").lstrip("@").lower()
    creator = session.scalars(
        select(Creator).where(
            Creator.platform == platform,
            func.lower(Creator.handle) == want)).first()
    if creator is None:
        return Holding(platform=platform, handle=want)

    pubs = session.scalars(select(Video).where(
        Video.creator_id == creator.id)).all()
    observed = session.execute(
        select(func.min(MetricsSnapshot.captured_at),
               func.max(MetricsSnapshot.captured_at))
        .join(Video, Video.id == MetricsSnapshot.video_id)
        .where(Video.creator_id == creator.id)).first()

    analysed = 0
    if pubs:
        analysed = session.scalar(
            select(func.count(func.distinct(VideoFeature.video_id)))
            .where(VideoFeature.video_id.in_([v.id for v in pubs]),
                   VideoFeature.status == "ok")) or 0

    return Holding(
        creator_id=creator.id, handle=creator.handle, platform=platform,
        publications=len(pubs),
        with_media=sum(1 for v in pubs if v.media_status == "available"),
        analysed=analysed,
        oldest_observed=observed[0] if observed else None,
        newest_observed=observed[1] if observed else None,
        follower_count=creator.follower_count)


def _observed_new_ratio(session: Session, kind: str,
                        platform: str) -> tuple[float | None, int]:
    """How much of a run of this shape has historically been new.

    Measured, not assumed. Uses `metrics_snapshots.discovery_job_id` to
    count what each past job actually attached, against how many
    publications already existed at the time. With no history it returns
    None, and the caller must not substitute a guess.
    """
    jobs = session.scalars(
        select(DiscoveryJob)
        .where(DiscoveryJob.kind == kind,
               DiscoveryJob.platform == platform,
               DiscoveryJob.status == "ok",
               DiscoveryJob.is_fixture.is_(False))
        .order_by(DiscoveryJob.id.desc()).limit(HISTORY_JOBS)).all()
    if not jobs:
        return None, 0

    ratios: list[float] = []
    for job in jobs:
        seen = session.scalar(
            select(func.count(func.distinct(MetricsSnapshot.video_id)))
            .where(MetricsSnapshot.discovery_job_id == job.id)) or 0
        if not seen:
            continue
        # A publication row created at or after the job started is new to
        # us; one that predates it was already held. `videos.created_at` is
        # the row's own insertion time, not the publication date, which is
        # exactly the distinction needed here.
        new = session.scalar(
            select(func.count(func.distinct(Video.id)))
            .join(MetricsSnapshot, MetricsSnapshot.video_id == Video.id)
            .where(MetricsSnapshot.discovery_job_id == job.id,
                   Video.created_at >= job.started_at)) or 0
        ratios.append(new / seen)
    if not ratios:
        return None, 0
    return round(sum(ratios) / len(ratios), 3), len(ratios)


def check(session: Session, *, kind: str, platform: str, value: str,
          limit: int, estimated_usd: float, is_fixture: bool,
          reusable_job: DiscoveryJob | None = None) -> Preflight:
    """Report what this operation would buy. Never spends, never blocks."""
    pf = Preflight(kind=kind, platform=platform, value=value, limit=limit,
                   estimated_usd=estimated_usd, is_fixture=is_fixture)

    if is_fixture:
        pf.recommendation = "free"
        pf.reasons.append(
            "Demonstration data. Nothing is sent to a provider and nothing "
            "is charged.")
        return pf

    if reusable_job is not None:
        pf.reusable_job_id = reusable_job.id
        started = reusable_job.started_at
        if started is not None:
            if started.tzinfo is None:
                started = started.replace(tzinfo=timezone.utc)
            pf.reusable_age_hours = round(
                (datetime.now(timezone.utc) - started).total_seconds()
                / 3600.0, 1)
        pf.recommendation = "reuse"
        pf.reasons.append(
            f"An identical search ran {pf.reusable_age_hours}h ago "
            f"(job {reusable_job.id}). Reusing it costs nothing.")
        return pf

    if kind == "creator":
        pf.holding = _holding_for_creator(session, platform, value)
        h = pf.holding
        if h.publications:
            pf.reasons.append(
                f"@{h.handle} is already held: {h.publications} "
                f"publication(s), {h.with_media} with media, {h.analysed} "
                f"with extracted features.")
            if h.age_days is not None:
                pf.reasons.append(
                    f"Last observed {h.age_days} day(s) ago.")
            if h.age_days is not None and h.age_days < STALE_AFTER.days:
                pf.recommendation = "refresh"
                pf.warnings.append(
                    f"This creator was researched within the last "
                    f"{STALE_AFTER.days} days. The provider bills per "
                    f"returned publication whether or not it is new, so "
                    f"most of this charge would buy rows already held.")
        else:
            pf.reasons.append(
                "No publications held for this creator yet; everything "
                "returned would be new.")

    ratio, n_jobs = _observed_new_ratio(session, kind, platform)
    pf.observed_new_ratio = ratio
    pf.observed_from_jobs = n_jobs
    if ratio is not None and limit:
        pf.likely_new = int(round(limit * ratio))
        pf.likely_already_held = limit - pf.likely_new
        pf.reasons.append(
            f"Across the last {n_jobs} comparable run(s), "
            f"{ratio * 100:.0f}% of returned publications were new.")
        waste = pf.wasted_usd_estimate
        if waste:
            pf.reasons.append(
                f"On that ratio roughly ${waste:.4f} of the ${estimated_usd:.4f} "
                f"estimate would buy publications already held.")
    else:
        pf.reasons.append(
            "No comparable run has been recorded, so how much of this "
            "would be new is unknown. It is not estimated.")

    return pf


def analysis_gap(session: Session, creator_id: int,
                 method_version: str) -> dict:
    """Which of a creator's publications still need analysing.

    Separate from the provider question on purpose. Metadata already held
    costs nothing to re-read; GPU time is a different budget with a
    different trigger, and conflating them hides both.
    """
    pubs = session.scalars(select(Video).where(
        Video.creator_id == creator_id)).all()
    ids = [v.id for v in pubs]
    if not ids:
        return {"total": 0, "with_media": 0, "analysed": 0, "pending": 0,
                "pending_ids": []}
    done = {r[0] for r in session.execute(
        select(VideoFeature.video_id).distinct().where(
            VideoFeature.video_id.in_(ids),
            VideoFeature.method_version == method_version,
            VideoFeature.status == "ok")).all()}
    playable = [v for v in pubs if v.media_status == "available"]
    pending = [v.id for v in playable if v.id not in done]
    return {
        "total": len(pubs),
        "with_media": len(playable),
        "analysed": len(done),
        "pending": len(pending),
        "pending_ids": pending[:200],
        "method_version": method_version,
    }
