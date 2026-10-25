"""Keyword, hashtag and niche discovery: finding creators nobody listed.

This is the half of Phase 4 that `research.service` could not do. Research
answers "tell me about this account". Discovery answers "which accounts
should I be looking at at all", which is the question the product exists to
answer and the one that cannot be demonstrated on fixtures alone.

The pipeline is fixed by what the providers can actually do
(`[PRIMARY, measured 0A]`, see `server/discovery/apify.py`):

    search a concept -> get publications -> derive creators from them
      -> deduplicate -> rank -> research the promising ones SELECTIVELY

The last step is deliberately not automatic. Researching every discovered
creator's full back-catalogue is the single easiest way to spend a lot of
money on accounts that turn out to be irrelevant, so this module produces a
*candidate list with a per-creator price tag* and stops. Expansion is a
separate, bounded decision.

**The sampling warning that governs every number here.** A hashtag or
keyword feed is not a sample of content; it is a sample of *what the feed
chose to show*, which is biased toward recent and already-popular posts. In
Phase 0A a hashtag feed returned 0 of 45 records aged >= 35 days. So:

  * discovery finds creators, it does not measure them;
  * nothing in a candidate list is a performance baseline;
  * a creator's absence means nothing at all.

Those limits are attached to the stored job and travel to the interface
rather than living in this docstring.
"""

from __future__ import annotations

import logging
import statistics
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from server import budget
from server.config import SpendRefused
from server.db.models import Creator, DiscoveryJob, MetricsSnapshot, Video
from server.discovery.base import (DiscoveryAdapter, DiscoveryError,
                                   DiscoveryQuery, DiscoveryResult, Platform,
                                   QueryKind)
from server.research import service

logger = logging.getLogger("virallab.research.discover")

#: Curated starting points for this owner's niche. A "niche" is not a
#: provider concept - it is a named bundle of queries plus a label that gets
#: recorded on whatever the queries find, so candidates can later be grouped
#: and compared within a cohort (docs/SCHEMA_PLAN.md 3).
#:
#: Each entry runs as ONE query at a time. Expanding a whole niche in a
#: single click would multiply cost by the number of terms without the owner
#: seeing the bill first.
NICHES: dict[str, dict] = {
    "classical_piano": {
        "label": "Classical piano",
        "keywords": ["classical piano", "virtuoso pianist",
                     "piano performance"],
        "hashtags": ["pianotok", "classicalpiano", "pianist"],
    },
    "piano_composition": {
        "label": "Piano composition",
        "keywords": ["piano composition", "original piano"],
        "hashtags": ["pianocomposition", "originalmusic"],
    },
    "chopin": {
        "label": "Chopin",
        "keywords": ["Chopin", "Chopin nocturne", "Chopin etude"],
        "hashtags": ["chopin"],
    },
    "emotional_piano": {
        "label": "Emotional piano",
        "keywords": ["emotional piano", "sad piano"],
        "hashtags": ["emotionalpiano", "relaxingpiano"],
    },
    "piano_covers": {
        "label": "Piano covers",
        "keywords": ["piano cover", "piano covers"],
        "hashtags": ["pianocover"],
    },
}

#: Attached to every discovery job, because a result that looks like a
#: ranking invites being read as one.
SAMPLING_NOTES = (
    "A keyword or hashtag feed is biased toward recent and already-popular "
    "posts. It is a sample of what the feed surfaced, not of what exists.",
    "`[PRIMARY, measured 0A]` A hashtag feed returned 0 of 45 records aged "
    ">= 35 days, so discovery cannot supply a mature fixed-age corpus. It "
    "finds creators; it does not measure them.",
    "Only publications that used this term are visible. A creator's absence "
    "from these results is not evidence of anything.",
    "Figures here are single observations taken at one moment, at whatever "
    "age each publication happened to be. They are not a baseline.",
)

#: A candidate needs at least this many sampled publications before the word
#: "consistent" is allowed anywhere near it.
CONSISTENCY_MIN_SAMPLED = 3
#: Follower count below which a strong publication is notable rather than
#: expected. Round number, chosen as a display threshold, not measured.
SMALL_CREATOR_FOLLOWERS = 25_000
#: Views-per-follower above which a publication reached well beyond the
#: creator's own audience.
BREAKOUT_VIEWS_PER_FOLLOWER = 3.0
#: A views-per-follower ratio is unstable when the denominator is small: at
#: 600 followers one ordinary post looks like a 15x breakout. Below this
#: absolute view count the ratio is reported but not allowed to drive the
#: `breakout` tier, because the alternative is ranking noise at the top of
#: the list. Chosen as a display threshold, not measured.
BREAKOUT_MIN_VIEWS = 25_000
#: Shrink the ratio toward 1 when followers are few, so a tiny denominator
#: cannot dominate. Standard smoothing: add a pseudo-audience.
FOLLOWER_PRIOR = 2_000


#: An identical concept search inside this window returns the stored result
#: instead of re-running. The in-flight guard only covers genuinely
#: concurrent calls; this covers clicking search twice a minute apart, which
#: on a paid provider is the same money.
SEARCH_FRESH_FOR = timedelta(hours=6)


class DiscoveryRefused(RuntimeError):
    """Discovery was not run, and why."""


@dataclass
class CandidateSignal:
    """One reason a candidate is ranked where it is.

    Signals are listed rather than collapsed into a single number alone,
    because a composite score with no explanation is indistinguishable from
    a made-up one.
    """

    key: str
    label: str
    #: None when the provider does not report what this signal needs.
    value: float | None
    available: bool = True
    note: str | None = None


@dataclass
class Candidate:
    """A creator a discovery run surfaced, with a price to look closer."""

    creator_id: int
    platform: str
    handle: str
    display_name: str | None
    profile_url: str | None
    #: None on Instagram: `[PRIMARY]` the validated actors report no
    #: follower count, so this stays null rather than becoming zero.
    follower_count: int | None
    followers_available: bool
    #: How many of this creator's publications THIS feed surfaced. Not how
    #: many they have.
    sampled_publications: int
    sampled_median_views: int | None
    sampled_best_views: int | None
    best_publication_external_id: str | None
    #: views / followers for the best sampled publication, where followers
    #: are known.
    best_views_per_follower: float | None
    engagement_rate: float | None
    counts_rounded: bool
    newest_publication_at: datetime | None
    oldest_publication_at: datetime | None
    niche: str | None
    #: Research state, so the interface never offers to re-buy something.
    already_researched: bool
    last_refreshed_at: datetime | None
    publications_collected: int
    #: What a full back-catalogue research pass would cost right now.
    estimated_research_usd: float
    score: float
    tier: str
    reasons: list[str] = field(default_factory=list)
    signals: list[CandidateSignal] = field(default_factory=list)
    is_fixture: bool = False


@dataclass
class DiscoveryOutcome:
    job_id: int | None
    status: str
    kind: str
    platform: str
    value: str
    niche: str | None = None
    is_fixture: bool = False
    publications_seen: int = 0
    publications_new: int = 0
    creators_seen: int = 0
    creators_new: int = 0
    estimated_usd: float | None = None
    actual_usd: float | None = None
    coverage_notes: list[str] = field(default_factory=list)
    item_errors: list[dict] = field(default_factory=list)
    message: str | None = None


# ---------------------------------------------------------------------------
# running a discovery search
# ---------------------------------------------------------------------------

def discover(session: Session, adapter: DiscoveryAdapter, *,
             kind: str, platform: str, value: str,
             limit: int = 50, niche: str | None = None,
             cursor: str | None = None, force: bool = False,
             now: datetime | None = None) -> DiscoveryOutcome:
    """Run one concept search. Spends nothing unless it has to and may.

    Mirrors `research.service.research_creator` deliberately: same advisory
    lock, same in-flight guard, same reserve-before-call ordering, same
    settle-or-leave-open discipline. Two code paths that both spend money
    should not have two different safety stories.
    """
    stamp = now or datetime.now(timezone.utc)
    value = (value or "").strip()
    if not value:
        raise DiscoveryRefused("a search needs a term")
    if kind not in ("keyword", "hashtag"):
        raise DiscoveryRefused(
            f"unsupported discovery kind {kind!r}; expected keyword or "
            f"hashtag (a niche expands into these)")
    if niche is not None and niche not in NICHES:
        raise DiscoveryRefused(f"unknown niche {niche!r}")

    plat = Platform(platform)
    query_kind = QueryKind(kind)
    if query_kind not in adapter.capabilities(plat).supported_queries:
        raise DiscoveryRefused(
            f"{adapter.name} cannot run a {kind} search on {platform}. "
            f"This is a provider limit, not a configuration choice.")

    value = value.lstrip("#") if kind == "hashtag" else value

    service._lock_query(session, kind, platform, value)
    running = service.in_flight_job(session, kind, platform, value, now=stamp)
    if running is not None:
        return DiscoveryOutcome(
            job_id=running.id, status="in_flight", kind=kind,
            platform=platform, value=value, niche=niche,
            is_fixture=bool(running.is_fixture),
            estimated_usd=running.estimated_usd,
            message=(f"This search is already running (job {running.id}). "
                     f"Nothing was collected and nothing was spent."))

    if not force:
        recent = recent_search(session, kind, platform, value, now=stamp)
        if recent is not None:
            counts = search_result_counts(session, recent.id)
            return DiscoveryOutcome(
                job_id=recent.id, status="reused", kind=kind,
                platform=platform, value=value,
                niche=(recent.params or {}).get("niche"),
                is_fixture=bool(recent.is_fixture),
                publications_seen=counts["publications"],
                creators_seen=counts["creators"],
                estimated_usd=recent.estimated_usd,
                actual_usd=recent.actual_usd,
                coverage_notes=list(recent.coverage_notes or []),
                message=(f"Showing the search from "
                         f"{recent.started_at:%Y-%m-%d %H:%M} UTC "
                         f"(job {recent.id}). Nothing was collected and "
                         f"nothing was spent. Use refresh to re-run it."))

    query = DiscoveryQuery(kind=query_kind, platform=plat, value=value,
                           limit=limit, cursor=cursor)
    estimate = adapter.estimate_usd(query)

    job = DiscoveryJob(
        kind=kind, platform=platform, query_value=value,
        requested_limit=limit, provider=adapter.name,
        is_fixture=adapter.is_fixture, status="running",
        estimated_usd=estimate,
        # Set explicitly rather than left to the column default: a caller
        # that passes `now=` must get a row consistent with it, or the reuse
        # window below compares a caller's clock against the database's.
        started_at=stamp,
        params={"niche": niche, "cursor": cursor, "limit": limit})
    session.add(job)
    session.flush()
    session.commit()

    reservation = None
    if not adapter.is_fixture and estimate > 0:
        try:
            reservation = budget.reserve(
                session, provider=adapter.name, estimated_usd=estimate,
                idempotency_key=f"discover:{job.id}", run_id=None,
                detail={"kind": kind, "platform": platform, "value": value})
            job.reservation_id = reservation.id
            # Durable before the provider request begins: if this process
            # dies during the call, the reservation is already holding
            # budget and expires rather than vanishing.
            session.commit()
        except SpendRefused as exc:
            job.status, job.error = "refused", str(exc)
            job.finished_at = stamp
            session.commit()
            logger.warning("discovery refused for %s %s: %s", kind, value,
                           exc)
            return DiscoveryOutcome(
                job_id=job.id, status="refused", kind=kind,
                platform=platform, value=value, niche=niche,
                estimated_usd=estimate, message=str(exc))

    try:
        result = adapter.search(query)
    except DiscoveryError as exc:
        if reservation is not None:
            if exc.permanent:
                budget.release(session, reservation.id,
                               f"provider refused permanently: {exc}")
            else:
                logger.warning(
                    "leaving reservation %s open after a transient provider "
                    "failure; it may have billed", reservation.id)
        job.status, job.error = "failed", str(exc)[:1000]
        job.finished_at = stamp
        session.commit()
        return DiscoveryOutcome(
            job_id=job.id, status="failed", kind=kind, platform=platform,
            value=value, niche=niche, estimated_usd=estimate,
            message=str(exc))

    if reservation is not None:
        if result.actual_usd is None:
            logger.warning(
                "provider %s reported no cost for discovery job %s; the "
                "reservation is left open rather than assumed free",
                adapter.name, job.id)
        else:
            budget.settle(session, reservation.id,
                          actual_usd=result.actual_usd,
                          notes=f"discovery job {job.id}")

    outcome = _store_discovery(session, job, result, niche=niche, stamp=stamp)
    session.commit()
    return outcome


def _store_discovery(session: Session, job: DiscoveryJob,
                     result: DiscoveryResult, *, niche: str | None,
                     stamp: datetime) -> DiscoveryOutcome:
    """Persist what a concept search found, idempotently.

    Creators are deduplicated on (platform, handle) - the database's own
    unique key - so the same account appearing in ten results is one row.
    Publications are deduplicated on (platform, external_id) for the same
    reason, and a repeat sighting appends an observation rather than
    overwriting one.
    """
    provenance = result.provenance()
    notes = list(result.coverage_notes) + list(SAMPLING_NOTES)
    provenance["coverage_notes"] = notes
    provenance["discovery_kind"] = job.kind
    provenance["discovery_value"] = job.query_value
    if niche:
        provenance["niche"] = niche

    # ---- creators ----------------------------------------------------
    by_handle: dict[str, Creator] = {}
    #: Mutable so the closure below can count without `nonlocal`.
    counts = {"creators_new": 0}
    reported = {c.username.lstrip("@").lower(): c for c in result.creators}

    def creator_for(username: str) -> Creator | None:
        key = (username or "").lstrip("@").lower()
        if not key:
            return None
        if key in by_handle:
            return by_handle[key]
        row = service.find_creator(session, job.platform, key)
        if row is None:
            row = Creator(
                platform=job.platform, handle=key, source="discovery",
                discovered_via=f"{job.kind}:{job.query_value}",
                first_seen_at=stamp, is_fixture=bool(job.is_fixture),
                research_status="none", provenance=provenance)
            session.add(row)
            counts["creators_new"] += 1
        # Fill only what the provider actually reported. An absent field
        # stays absent: Instagram reports no follower count at all, and
        # writing 0 there would be a fabricated number.
        rep = reported.get(key)
        if rep is not None:
            row.display_name = row.display_name or rep.display_name
            row.profile_url = row.profile_url or rep.profile_url
            row.external_id = row.external_id or rep.external_id
            if rep.follower_count is not None:
                row.follower_count = rep.follower_count
        tags = list(row.niche_tags or [])
        if niche and niche not in tags:
            tags.append(niche)
            row.niche_tags = tags
        session.flush()
        by_handle[key] = row
        return row

    # ---- publications ------------------------------------------------
    pubs_new = 0
    for pub in result.publications:
        creator = creator_for(pub.creator_username)
        if creator is None:
            continue
        video = session.scalar(select(Video).where(
            Video.platform == job.platform,
            Video.external_id == pub.external_id))
        if video is None:
            video = Video(
                creator_id=creator.id, platform=job.platform,
                external_id=pub.external_id, source="discovery",
                discovery_job_id=job.id, is_fixture=bool(job.is_fixture),
                provenance=provenance, media_status="absent")
            session.add(video)
            pubs_new += 1
        # Published time and observed time are different columns, always
        # (docs/TEMPORAL_INTELLIGENCE.md rule 1).
        if pub.published_at is not None:
            video.posted_at = pub.published_at
        video.caption = pub.caption if pub.caption is not None else video.caption
        if pub.hashtags:
            video.hashtags = pub.hashtags
        if pub.duration_s is not None:
            video.duration_s = pub.duration_s
        if pub.music_title or pub.music_id:
            video.music = {"title": pub.music_title,
                           "artist": pub.music_artist,
                           "id": pub.music_id,
                           "is_original": pub.music_is_original}
        session.flush()

        # Append-only observation (rule 2). A second sighting of the same
        # publication is a second row, which is what eventually makes
        # growth visible.
        session.add(MetricsSnapshot(
            video_id=video.id, captured_at=result.observed_at,
            discovery_job_id=job.id,
            views=pub.views, likes=pub.likes, comments=pub.comments,
            shares=pub.shares, saves=pub.saves,
            is_rounded=bool(pub.counts_rounded),
            raw={"source": "discovery", "job_id": job.id,
                 "provider": result.provider,
                 "unavailable_fields": pub.unavailable_fields}))

    creators_new = counts["creators_new"]
    job.status = "ok"
    job.finished_at = stamp
    job.publications_seen = len(result.publications)
    job.publications_new = pubs_new
    job.creators_seen = len(by_handle)
    job.creators_new = creators_new
    job.snapshots_written = len(result.publications)
    job.actual_usd = result.actual_usd
    job.coverage_notes = notes
    job.item_errors = result.item_errors or []
    session.flush()

    logger.info("discovery %s %s -> %d publication(s), %d creator(s), "
                "%d new creator(s), cost %s", job.kind, job.query_value,
                len(result.publications), len(by_handle), creators_new,
                "fixture" if job.is_fixture else result.actual_usd)

    return DiscoveryOutcome(
        job_id=job.id, status="ok", kind=job.kind, platform=job.platform,
        value=job.query_value, niche=niche, is_fixture=bool(job.is_fixture),
        publications_seen=len(result.publications), publications_new=pubs_new,
        creators_seen=len(by_handle), creators_new=creators_new,
        estimated_usd=job.estimated_usd, actual_usd=result.actual_usd,
        coverage_notes=notes, item_errors=result.item_errors or [])


# ---------------------------------------------------------------------------
# ranking what a search found
# ---------------------------------------------------------------------------

def candidates_for_job(session: Session, adapter: DiscoveryAdapter,
                       job_id: int, *, niche: str | None = None
                       ) -> list[Candidate]:
    """Rank the creators one discovery job surfaced.

    Computed on read, never stored: this is an inferred ordering over a
    biased sample, and freezing it into a table would give it a permanence
    it has not earned.
    """
    job = session.get(DiscoveryJob, job_id)
    if job is None:
        raise DiscoveryRefused(f"no such discovery job {job_id}")

    # Resolved through the OBSERVATIONS this job wrote, not through
    # `videos.discovery_job_id`. That column records only the first job to
    # see a publication, so a repeated search - or a search that re-finds a
    # creator an earlier search already stored - would appear to have
    # returned nothing.
    rows = session.execute(
        select(Video, MetricsSnapshot, Creator)
        .join(Creator, Creator.id == Video.creator_id)
        .join(MetricsSnapshot, MetricsSnapshot.video_id == Video.id)
        .where(MetricsSnapshot.discovery_job_id == job_id)).all()

    per_creator: dict[int, dict] = {}
    for video, snap, creator in rows:
        slot = per_creator.setdefault(creator.id, {
            "creator": creator, "pubs": {}, "rounded": False})
        # One observation per publication: the newest, if a publication was
        # somehow observed twice inside one job.
        prev = slot["pubs"].get(video.id)
        if prev is None or (snap.captured_at or stamp_min()) > (
                prev[1].captured_at or stamp_min()):
            slot["pubs"][video.id] = (video, snap)
        slot["rounded"] = slot["rounded"] or bool(snap.is_rounded)

    out: list[Candidate] = []
    for data in per_creator.values():
        out.append(_score_candidate(session, adapter, job, data,
                                    niche=niche or _job_niche(job)))
    out.sort(key=lambda c: (-c.score, c.handle))
    return out


def stamp_min() -> datetime:
    return datetime.min.replace(tzinfo=timezone.utc)


def _job_niche(job: DiscoveryJob) -> str | None:
    return (job.params or {}).get("niche")


def _score_candidate(session: Session, adapter: DiscoveryAdapter,
                     job: DiscoveryJob, data: dict, *,
                     niche: str | None) -> Candidate:
    creator: Creator = data["creator"]
    pubs = list(data["pubs"].values())
    views = [s.views for _v, s in pubs if s.views is not None]
    best_video, best_snap = max(
        pubs, key=lambda p: (p[1].views if p[1].views is not None else -1))

    caps = adapter.capabilities(Platform(job.platform))
    followers_available = bool(caps.follower_count)
    followers = creator.follower_count if followers_available else None

    median_views = int(statistics.median(views)) if views else None
    best_views = max(views) if views else None

    vpf = None
    vpf_smoothed = None
    if followers and best_views:
        vpf = round(best_views / followers, 2)
        # Smoothed against a pseudo-audience so a 600-follower account
        # cannot outrank a real breakout on arithmetic alone.
        vpf_smoothed = best_views / (followers + FOLLOWER_PRIOR)

    eng = None
    if best_snap.views:
        interactions = sum(x or 0 for x in (best_snap.likes,
                                            best_snap.comments,
                                            best_snap.shares))
        if interactions:
            eng = round(interactions / best_snap.views, 4)

    dates = [v.posted_at for v, _s in pubs if v.posted_at is not None]

    signals: list[CandidateSignal] = [
        CandidateSignal(
            "sampled", "Publications this search surfaced",
            float(len(pubs)),
            note=("How many of this creator's posts the feed showed, not "
                  "how many they have.")),
        CandidateSignal(
            "best_views", "Best sampled view count",
            float(best_views) if best_views is not None else None,
            available=best_views is not None),
        CandidateSignal(
            "views_per_follower", "Best post's views per follower", vpf,
            available=vpf is not None,
            note=(None if followers_available else
                  "`[PRIMARY]` This provider reports no follower count for "
                  f"{job.platform}, so reach beyond the creator's own "
                  "audience cannot be computed. Not estimated.")),
        CandidateSignal(
            "engagement_rate", "Interactions per view on the best post",
            eng, available=eng is not None),
    ]

    score = 0.0
    reasons: list[str] = []
    tier = "relevant"

    # 1. An unusually successful publication from a small creator. The
    #    strongest signal available, and the only one that needs followers.
    #    Gated on an absolute floor: the ratio is unstable when the
    #    denominator is small, and without this a 640-follower account with
    #    9,300 views ranked level with a genuine 78,000-view breakout.
    breakout = (vpf is not None
                and vpf >= BREAKOUT_VIEWS_PER_FOLLOWER
                and (best_views or 0) >= BREAKOUT_MIN_VIEWS)
    if (vpf is not None and vpf >= BREAKOUT_VIEWS_PER_FOLLOWER
            and not breakout):
        reasons.append(
            f"A post reached {vpf:g}x this account's {followers:,} "
            f"followers, but at {best_views:,} views the ratio rests on a "
            f"small audience and one ordinary post can produce it. Reported, "
            f"not ranked as a breakout.")
    if breakout:
        score += 40 * min(vpf_smoothed / BREAKOUT_VIEWS_PER_FOLLOWER, 3.0)
        if followers is not None and followers <= SMALL_CREATOR_FOLLOWERS:
            tier = "small_creator_breakout"
            score += 25
            reasons.append(
                f"A post reached {vpf:g}x this account's follower count "
                f"({followers:,} followers). Reach well beyond their own "
                f"audience is what we are looking for.")
        else:
            tier = "established_breakout_candidate"
            reasons.append(
                f"A post reached {vpf:g}x the follower count on an "
                f"established account ({followers:,} followers), which "
                f"makes it a breakout candidate worth researching.")

    # 2. Appeared repeatedly in this feed. Carefully worded: the feed cannot
    #    establish consistency, only repeated relevance.
    if len(pubs) >= CONSISTENCY_MIN_SAMPLED:
        score += 10 * min(len(pubs), 6)
        if tier == "relevant":
            tier = "repeatedly_relevant"
        reasons.append(
            f"{len(pubs)} of this creator's publications matched the "
            f"search. That is repeated relevance to the term - it is NOT "
            f"evidence of consistent performance, which needs their full "
            f"history.")

    # 3. Absolute reach, heavily discounted. A big number from a big account
    #    is the least interesting thing on this list, but not nothing.
    if best_views:
        score += min(best_views / 50_000.0, 12.0)

    # 4. Recency, as a weak tie-breaker toward creators who are active now.
    if dates:
        newest = max(dates)
        age_days = (datetime.now(timezone.utc) - newest).days
        if age_days <= 30:
            score += 6
            reasons.append("Posted within the last 30 days, so the account "
                           "is currently active.")

    # 5. Niche relevance, where the search carried one.
    if niche and niche in (creator.niche_tags or []):
        score += 5

    if eng is not None and eng >= 0.1:
        score += 6
        reasons.append(f"{eng:.1%} of viewers interacted with the best "
                       f"sampled post.")

    if not followers_available:
        reasons.append(
            f"`[PRIMARY]` No follower count is available on "
            f"{job.platform} from this provider, so this candidate is "
            f"ranked without the strongest signal. It is not comparable to "
            f"a TikTok candidate's ranking.")

    if data["rounded"]:
        reasons.append(
            "This provider rounds view counts to about 3 significant "
            "figures, which blurs the boundary between an outlier and a "
            "merely good post.")

    est = adapter.estimate_usd(DiscoveryQuery(
        kind=QueryKind.CREATOR, platform=Platform(job.platform),
        value=creator.handle, limit=service.DEFAULT_LIMIT))

    return Candidate(
        creator_id=creator.id, platform=creator.platform,
        handle=creator.handle, display_name=creator.display_name,
        profile_url=creator.profile_url, follower_count=followers,
        followers_available=followers_available,
        sampled_publications=len(pubs), sampled_median_views=median_views,
        sampled_best_views=best_views,
        best_publication_external_id=best_video.external_id,
        best_views_per_follower=vpf, engagement_rate=eng,
        counts_rounded=bool(data["rounded"]),
        newest_publication_at=max(dates) if dates else None,
        oldest_publication_at=min(dates) if dates else None,
        niche=niche,
        already_researched=creator.research_status == "collected",
        last_refreshed_at=creator.last_refreshed_at,
        publications_collected=creator.publications_collected or 0,
        estimated_research_usd=round(est, 4),
        score=round(score, 2), tier=tier, reasons=reasons, signals=signals,
        is_fixture=bool(creator.is_fixture))


# ---------------------------------------------------------------------------
# bounded expansion
# ---------------------------------------------------------------------------

@dataclass
class ExpansionPlan:
    """What researching a set of candidates would cost, before doing it."""

    candidates: list[Candidate] = field(default_factory=list)
    skipped_already_fresh: list[str] = field(default_factory=list)
    total_estimated_usd: float = 0.0
    budget_remaining_usd: float | None = None
    within_budget: bool = True
    notes: list[str] = field(default_factory=list)


def plan_expansion(session: Session, adapter: DiscoveryAdapter,
                   candidates: list[Candidate], *,
                   max_creators: int | None = None,
                   max_spend_usd: float | None = None,
                   now: datetime | None = None) -> ExpansionPlan:
    """Choose which candidates to research under an explicit ceiling.

    Takes the ranked list and walks down it accumulating cost until a limit
    is hit. Creators whose research is already fresh are skipped rather than
    re-bought, and that is reported rather than silent.
    """
    stamp = now or datetime.now(timezone.utc)
    plan = ExpansionPlan()
    state = budget.budget_state(session, provider=adapter.name, now=stamp)
    remaining = state.get("remaining_usd")
    plan.budget_remaining_usd = remaining

    ceiling = max_spend_usd
    if remaining is not None and not adapter.is_fixture:
        ceiling = remaining if ceiling is None else min(ceiling, remaining)

    spend = 0.0
    for cand in candidates:
        if max_creators is not None and len(plan.candidates) >= max_creators:
            break
        creator = session.get(Creator, cand.creator_id)
        if creator is not None and service.is_fresh(creator, now=stamp):
            plan.skipped_already_fresh.append(cand.handle)
            continue
        cost = 0.0 if adapter.is_fixture else cand.estimated_research_usd
        if ceiling is not None and spend + cost > ceiling + 1e-9:
            plan.within_budget = False
            plan.notes.append(
                f"Stopped at {len(plan.candidates)} creator(s): researching "
                f"{cand.handle} would need ${cost:.2f} against "
                f"${ceiling - spend:.2f} left under the ceiling.")
            break
        plan.candidates.append(cand)
        spend += cost

    plan.total_estimated_usd = round(spend, 4)
    if plan.skipped_already_fresh:
        plan.notes.append(
            f"Skipped {len(plan.skipped_already_fresh)} creator(s) whose "
            f"research is still fresh; reopening them costs nothing.")
    plan.notes.append(
        "Estimates are per-creator upper bounds. The actual charge is "
        "whatever the provider bills, which is what gets recorded.")
    return plan


def previous_searches(session: Session, *, limit: int = 25
                      ) -> list[DiscoveryJob]:
    """Past concept searches, so a result set can be reopened for free."""
    return list(session.scalars(
        select(DiscoveryJob)
        .where(DiscoveryJob.kind.in_(("keyword", "hashtag")))
        .order_by(DiscoveryJob.id.desc()).limit(limit)).all())


def search_result_counts(session: Session, job_id: int) -> dict:
    """Cheap summary of a stored result set, for a history list."""
    pubs = session.scalar(
        select(func.count(func.distinct(MetricsSnapshot.video_id)))
        .where(MetricsSnapshot.discovery_job_id == job_id)) or 0
    creators = session.scalar(
        select(func.count(func.distinct(Video.creator_id)))
        .join(MetricsSnapshot, MetricsSnapshot.video_id == Video.id)
        .where(MetricsSnapshot.discovery_job_id == job_id)) or 0
    return {"publications": pubs, "creators": creators}


def recent_search(session: Session, kind: str, platform: str, value: str, *,
                  now: datetime | None = None,
                  fresh_for: timedelta = SEARCH_FRESH_FOR
                  ) -> DiscoveryJob | None:
    """A successful identical search inside the reuse window, if any."""
    stamp = now or datetime.now(timezone.utc)
    job = session.scalars(
        select(DiscoveryJob)
        .where(DiscoveryJob.kind == kind,
               DiscoveryJob.platform == platform,
               DiscoveryJob.query_value == value,
               DiscoveryJob.status == "ok")
        .order_by(DiscoveryJob.id.desc()).limit(1)).first()
    if job is None:
        return None
    started = job.started_at
    if started is None:
        return None
    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    return job if (stamp - started) < fresh_for else None
