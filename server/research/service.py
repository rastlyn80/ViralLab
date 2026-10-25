"""Targeted creator research: resolve, collect, store, analyse.

One entry point, `research_creator`, which the API and the CLI both call.
The order of operations is the design:

    resolve handle
      -> find or create the account row            (idempotent)
      -> is existing research fresh enough?        (spend nothing if so)
      -> open a job
      -> reserve budget                            (refuse rather than spend)
      -> call the provider
      -> settle or release the reservation         (always, even on failure)
      -> store creators and publications           (idempotent)
      -> append performance observations           (never overwrite)
      -> detect breakthrough candidates
      -> record research state and close the job

Properties that matter more than the code:

**Repeatable without duplicating.** Accounts are keyed on
(platform, handle) and publications on (platform, external_id), so running
the same research twice updates rows and appends a new observation. It never
forks the record.

**Incremental.** A refresh passes `since` from the newest publication we
already hold, so a provider that supports it is not paid to re-send known
rows.

**Observation time is never conflated with publication time.** Every
snapshot records when we looked; `posted_at` records when the creator
posted. That separation is what makes "what worked then versus now"
answerable later, and it cannot be retrofitted.

**Fixture results stay labelled.** `is_fixture` is written onto the job, the
creator and every publication, so a demonstration cannot be mistaken for
collected data at any point downstream.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from server import budget
from server.config import SpendRefused
from server.db.models import (BreakthroughCandidate, Creator,
                              CreatorSnapshot, DiscoveryJob, MetricsSnapshot,
                              Video)
from server.discovery.base import (DiscoveryAdapter, DiscoveryError,
                                   DiscoveryQuery, DiscoveryResult, Platform,
                                   QueryKind, normalise_handle)
from server.research import baseline as bl
from server.research.breakthrough import METHOD_VERSION, detect

logger = logging.getLogger("virallab.research")

#: Research newer than this is reused rather than re-collected, so clicking
#: twice costs nothing.
FRESH_FOR = timedelta(hours=20)
DEFAULT_LIMIT = 100


@dataclass
class ResearchOutcome:
    creator_id: int | None
    job_id: int | None
    status: str
    platform: str
    handle: str
    #: True when nothing was collected because existing research was fresh.
    reused: bool = False
    is_fixture: bool = False
    creators_new: int = 0
    publications_seen: int = 0
    publications_new: int = 0
    snapshots_written: int = 0
    breakthroughs_found: int = 0
    estimated_usd: float | None = None
    actual_usd: float | None = None
    coverage_notes: list[str] = field(default_factory=list)
    item_errors: list[dict] = field(default_factory=list)
    message: str | None = None


def _newest_publication(session: Session, creator_id: int
                        ) -> datetime | None:
    return session.scalar(
        select(func.max(Video.posted_at)).where(
            Video.creator_id == creator_id))


def find_creator(session: Session, platform: str, handle: str
                 ) -> Creator | None:
    return session.scalar(select(Creator).where(
        Creator.platform == platform, Creator.handle == handle))


def upsert_creator_row(session: Session, platform: str, handle: str, *,
                       source: str, is_fixture: bool,
                       discovered_via: str | None = None) -> Creator:
    """Find or create the account row. Never duplicates."""
    row = find_creator(session, platform, handle)
    now = datetime.now(timezone.utc)
    if row is None:
        row = Creator(platform=platform, handle=handle,
                      is_own_account=False, source=source,
                      discovered_via=discovered_via, first_seen_at=now,
                      is_fixture=is_fixture, research_status="pending")
        session.add(row)
        session.flush()
    return row


def is_fresh(creator: Creator, *, now: datetime | None = None,
             fresh_for: timedelta = FRESH_FOR) -> bool:
    if creator.last_refreshed_at is None:
        return False
    stamp = now or datetime.now(timezone.utc)
    return (stamp - creator.last_refreshed_at) < fresh_for


def research_creator(session: Session, adapter: DiscoveryAdapter, *,
                     platform: str, handle: str,
                     limit: int = DEFAULT_LIMIT,
                     refresh: bool = False,
                     incremental: bool = True,
                     now: datetime | None = None) -> ResearchOutcome:
    """Research one creator. Spends nothing unless it has to and may."""
    stamp = now or datetime.now(timezone.utc)
    try:
        handle = normalise_handle(handle)
    except ValueError as exc:
        return ResearchOutcome(
            creator_id=None, job_id=None, status="failed", platform=platform,
            handle=handle, message=str(exc))

    plat = Platform(platform)
    existing = find_creator(session, platform, handle)

    # Fresh research is reused. Clicking research twice must not cost money.
    if existing is not None and not refresh and is_fresh(existing, now=stamp):
        return ResearchOutcome(
            creator_id=existing.id, job_id=existing.last_job_id,
            status="ok", platform=platform, handle=handle, reused=True,
            is_fixture=bool(existing.is_fixture),
            publications_seen=existing.publications_collected or 0,
            message=(f"Reusing research from "
                     f"{existing.last_refreshed_at:%Y-%m-%d %H:%M} UTC. "
                     f"Nothing was collected and nothing was spent."))

    since = None
    if incremental and existing is not None and not refresh:
        since = _newest_publication(session, existing.id)

    query = DiscoveryQuery(kind=QueryKind.CREATOR, platform=plat,
                           value=handle, limit=limit, since=since)
    estimate = adapter.estimate_usd(query)

    job = DiscoveryJob(
        kind="creator", platform=platform, query_value=handle,
        requested_limit=limit, provider=adapter.name,
        is_fixture=adapter.is_fixture, status="running",
        estimated_usd=estimate)
    session.add(job)
    session.flush()

    creator = existing or upsert_creator_row(
        session, platform, handle, source="manual_lookup",
        is_fixture=adapter.is_fixture, discovered_via=f"handle:{handle}")
    creator.last_job_id = job.id
    creator.research_status = "pending"
    session.commit()

    # ---- budget ------------------------------------------------------
    reservation = None
    if not adapter.is_fixture and estimate > 0:
        try:
            reservation = budget.reserve(
                session, provider=adapter.name, estimated_usd=estimate,
                idempotency_key=f"research:{job.id}", run_id=None,
                detail={"kind": "creator", "platform": platform,
                        "handle": handle})
            job.reservation_id = reservation.id
            session.commit()
        except SpendRefused as exc:
            job.status, job.error = "refused", str(exc)
            job.finished_at = datetime.now(timezone.utc)
            creator.research_status = (
                "collected" if creator.publications_collected else "none")
            session.commit()
            logger.warning("research refused for %s/%s: %s", platform,
                           handle, exc)
            return ResearchOutcome(
                creator_id=creator.id, job_id=job.id, status="refused",
                platform=platform, handle=handle, estimated_usd=estimate,
                message=str(exc))

    # ---- collect -----------------------------------------------------
    try:
        result = adapter.search(query)
    except DiscoveryError as exc:
        if reservation is not None:
            # The call failed; whether it billed is the question. A
            # permanent refusal (bad handle, unsupported query) never
            # reached the provider's meter, so that budget is released. A
            # transient failure may have, so it is left to expire rather
            # than optimistically freed.
            if exc.permanent:
                budget.release(session, reservation.id,
                               f"provider refused permanently: {exc}")
            else:
                logger.warning(
                    "leaving reservation %s open after a transient provider "
                    "failure; it may have billed and will expire if not "
                    "settled", reservation.id)
        job.status, job.error = "failed", str(exc)[:1000]
        job.finished_at = datetime.now(timezone.utc)
        creator.research_status = "failed"
        session.commit()
        return ResearchOutcome(
            creator_id=creator.id, job_id=job.id, status="failed",
            platform=platform, handle=handle, estimated_usd=estimate,
            message=str(exc))

    if reservation is not None:
        actual = result.actual_usd
        if actual is None:
            logger.warning(
                "provider %s reported no cost for job %s; the reservation "
                "is left open rather than assumed free", adapter.name,
                job.id)
        else:
            budget.settle(session, reservation.id, actual_usd=actual,
                          notes=f"discovery job {job.id}")

    outcome = _store(session, job, creator, result, stamp)
    session.commit()
    return outcome


def _store(session: Session, job: DiscoveryJob, creator: Creator,
           result: DiscoveryResult, stamp: datetime) -> ResearchOutcome:
    """Persist a result idempotently, then analyse it."""
    prov = result.provenance()

    # ---- creator rows ------------------------------------------------
    creators_new = 0
    for dc in result.creators:
        row = find_creator(session, dc.platform.value, dc.username)
        if row is None:
            row = Creator(platform=dc.platform.value, handle=dc.username,
                          is_own_account=False, source="discovery",
                          first_seen_at=stamp, is_fixture=result.is_fixture,
                          discovered_via=job.query_value)
            session.add(row)
            session.flush()
            creators_new += 1
        row.external_id = row.external_id or dc.external_id
        row.display_name = dc.display_name or row.display_name
        row.profile_url = dc.profile_url or row.profile_url
        # The latest known value, for convenience. History is appended
        # below and is what any growth claim must rest on.
        if dc.follower_count is not None:
            row.follower_count = dc.follower_count
        row.provenance = prov
        row.last_refreshed_at = stamp
        row.is_fixture = result.is_fixture

        # Account-level observation. Appended, never overwritten, and only
        # when the provider actually reported something - an empty snapshot
        # would imply we looked and saw zero.
        if any(v is not None for v in (dc.follower_count, dc.following_count,
                                       dc.publication_count,
                                       dc.total_likes)):
            exists = session.scalar(select(CreatorSnapshot).where(
                CreatorSnapshot.creator_id == row.id,
                CreatorSnapshot.captured_at == stamp))
            if exists is None:
                session.add(CreatorSnapshot(
                    creator_id=row.id, captured_at=stamp,
                    follower_count=dc.follower_count,
                    following_count=dc.following_count,
                    publication_count=dc.publication_count,
                    total_likes=dc.total_likes,
                    source=result.provider,
                    is_rounded=dc.counts_rounded,
                    raw={"unavailable_fields": dc.unavailable_fields,
                         "bio_links": dc.bio_links,
                         "is_fixture": result.is_fixture}))
    session.flush()

    # ---- publications and their observations -------------------------
    by_handle = {c.handle: c for c in session.scalars(select(Creator).where(
        Creator.platform == result.query.platform.value)).all()}
    pubs_new = snaps = 0
    for dp in result.publications:
        owner = by_handle.get(dp.creator_username) or creator
        video = session.scalar(select(Video).where(
            Video.platform == dp.platform.value,
            Video.external_id == dp.external_id))
        if video is None:
            video = Video(platform=dp.platform.value,
                          external_id=dp.external_id,
                          creator_id=owner.id, source="provider",
                          media_status="absent")
            session.add(video)
            session.flush()
            pubs_new += 1
        video.creator_id = owner.id
        video.caption = dp.caption if dp.caption is not None else video.caption
        video.hashtags = dp.hashtags or video.hashtags
        # Publication time. Never overwritten with None, because losing it
        # would make every age-based comparison for this row impossible.
        if dp.published_at is not None:
            video.posted_at = dp.published_at
        video.duration_s = dp.duration_s or video.duration_s
        video.thumbnail_url = dp.thumbnail_url or video.thumbnail_url
        video.discovery_job_id = job.id
        video.is_fixture = result.is_fixture
        video.provenance = prov
        if dp.music_title or dp.music_artist or dp.music_id:
            video.music = {"title": dp.music_title, "artist": dp.music_artist,
                           "id": dp.music_id,
                           "is_original": dp.music_is_original}

        # Append-only observation. Keyed on (video, captured_at), so a
        # repeated research pass adds a reading rather than replacing one -
        # which is what eventually makes a real trajectory visible.
        existing_snap = session.scalar(select(MetricsSnapshot).where(
            MetricsSnapshot.video_id == video.id,
            MetricsSnapshot.captured_at == stamp))
        if existing_snap is None:
            session.add(MetricsSnapshot(
                video_id=video.id, captured_at=stamp,
                views=dp.views, likes=dp.likes, comments=dp.comments,
                shares=dp.shares, saves=dp.saves,
                is_rounded=dp.counts_rounded,
                raw={"provider": result.provider,
                     "is_fixture": result.is_fixture,
                     "unavailable_fields": dp.unavailable_fields,
                     "observed_at": stamp.isoformat()}))
            snaps += 1
    session.flush()

    # ---- analysis ----------------------------------------------------
    found = detect_and_store(session, creator, result_provider=result.provider,
                             is_fixture=result.is_fixture)

    # ---- state -------------------------------------------------------
    total = session.scalar(select(func.count(Video.id)).where(
        Video.creator_id == creator.id)) or 0
    creator.publications_collected = total
    creator.last_refreshed_at = stamp
    creator.research_status = "collected" if total else "partial"
    if result.actual_usd:
        creator.collection_cost_usd = (
            (creator.collection_cost_usd or 0.0) + result.actual_usd)

    job.status = "ok"
    job.finished_at = datetime.now(timezone.utc)
    job.actual_usd = result.actual_usd
    job.creators_seen = len(result.creators)
    job.creators_new = creators_new
    job.publications_seen = len(result.publications)
    job.publications_new = pubs_new
    job.snapshots_written = snaps
    job.coverage_notes = {"notes": result.coverage_notes}
    job.item_errors = {"errors": result.item_errors}
    job.provenance = prov

    return ResearchOutcome(
        creator_id=creator.id, job_id=job.id, status="ok",
        platform=creator.platform, handle=creator.handle,
        is_fixture=result.is_fixture, creators_new=creators_new,
        publications_seen=len(result.publications), publications_new=pubs_new,
        snapshots_written=snaps, breakthroughs_found=found,
        estimated_usd=result.estimated_usd, actual_usd=result.actual_usd,
        coverage_notes=list(result.coverage_notes),
        item_errors=list(result.item_errors))


def observations_for(session: Session, creator_id: int
                     ) -> list[bl.Observation]:
    """Every publication's latest observation, for analysis.

    Latest per publication rather than all of them: a baseline mixing two
    readings of the same post would count it twice.
    """
    rows = session.execute(
        select(Video.id, Video.external_id, Video.posted_at, Video.caption,
               MetricsSnapshot.views, MetricsSnapshot.likes,
               MetricsSnapshot.comments, MetricsSnapshot.shares,
               MetricsSnapshot.saves, MetricsSnapshot.captured_at,
               MetricsSnapshot.is_rounded)
        .join(MetricsSnapshot, MetricsSnapshot.video_id == Video.id)
        .where(Video.creator_id == creator_id)
        .order_by(Video.id, MetricsSnapshot.captured_at.asc())).all()
    latest: dict[int, bl.Observation] = {}
    for (vid, ext, posted, caption, views, likes, comments, shares, saves,
         captured, rounded) in rows:
        latest[vid] = bl.Observation(
            video_id=vid, external_id=ext, published_at=posted, views=views,
            observed_at=captured, caption=caption, likes=likes,
            comments=comments, shares=shares, saves=saves,
            counts_rounded=bool(rounded))
    return list(latest.values())


def detect_and_store(session: Session, creator: Creator, *,
                     result_provider: str = "", is_fixture: bool = False
                     ) -> int:
    """Run the detector and upsert its candidates.

    Upsert on (creator, video, method_version), so re-running research does
    not accumulate duplicate candidates - and a human decision on a
    candidate is never overwritten.
    """
    observations = observations_for(session, creator.id)
    if not observations:
        return 0

    history = session.scalar(
        select(func.count(CreatorSnapshot.id)).where(
            CreatorSnapshot.creator_id == creator.id,
            CreatorSnapshot.follower_count.isnot(None))) or 0

    report = detect(observations, has_follower_history=history > 1)
    by_ext = {o.external_id: o.video_id for o in observations}

    # One row per publication. The change-point publication is usually also
    # an outlier against its own window, and the two share a key - so the
    # stronger statement wins rather than the two overwriting each other and
    # making the stored count disagree with the row count.
    found: dict[str | None, object] = {}
    for item in report.outliers:
        found[item.external_id] = item
    if report.breakthrough is not None:
        found[report.breakthrough.external_id] = report.breakthrough

    stored = 0
    for item in found.values():
        video_id = by_ext.get(item.external_id or "")
        row = session.scalar(select(BreakthroughCandidate).where(
            BreakthroughCandidate.creator_id == creator.id,
            BreakthroughCandidate.video_id == video_id,
            BreakthroughCandidate.method_version == report.method_version))
        if row is not None and row.decided_by == "human":
            continue                       # never overwrite a judgement
        if row is None:
            row = BreakthroughCandidate(
                creator_id=creator.id, video_id=video_id,
                method_version=report.method_version)
            session.add(row)
        row.classification = item.classification
        row.publication_index = item.index
        row.baseline_views = item.baseline_views
        row.baseline_mad = item.baseline_mad
        row.observed_views = item.observed_views
        row.relative_performance = item.relative_performance
        row.robust_z = item.robust_z
        row.window_before = item.window_before
        row.window_after = item.window_after
        row.median_after = item.median_after
        row.sustained = item.sustained
        row.confidence = item.confidence
        row.evidence = {**item.evidence, "caveats": report.caveats,
                        "provider": result_provider,
                        "is_fixture": is_fixture}
        row.missing_evidence = {"missing": item.missing_evidence}
        row.decided_by = "matcher"
        stored += 1
    session.flush()
    return stored
