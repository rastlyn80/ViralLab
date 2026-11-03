"""Fetch other creators' public video, briefly, with no credentials.

ADR 0010. Nothing visual had ever been measured on another creator's
video - 0 of 3,388 publications had been asked a single vision
question - and the owner's requirement names camera angle and
overlay explicitly, so this was the largest hole in the product.

The blocker was never technical. `yt-dlp` was already here, already
running with `--no-cookies`, already fetching public TikTok video for
the owner's own account. The same call works on anybody's public
video. What stopped it was a policy position, and the owner changed
it.

## What makes this safe to exist

The conditions are properties of this module, not of its
configuration, because a rule you can switch off with an argument is
a comment:

* **There is no cookie parameter.** `fetch_one` in
  `tiktok_archive.py` takes `cookies`; this module never passes it,
  and exposes no way for a caller to. The private, authenticated
  path is not reachable from here. A caller cannot turn this into an
  authenticated scraper by passing an argument, because there is no
  argument to pass.
* **Public or skipped.** A video that will not resolve logged-out is
  recorded as unavailable and left alone. It is never retried with
  credentials and never worked around. That is the line between
  collecting public data and circumventing an access control, and
  DEVELOPMENT_PLAN §2.3 rests on it entirely.
* **Transient by construction.** Every file is registered with
  `transient_media` before anything reads it and deleted on the ADR
  0009 six-hour deadline, whether or not feature extraction
  succeeded. If registration fails the file is deleted immediately:
  an unregistered file is invisible to the reaper, and invisible is
  how a research cache becomes a creator database.
* **Slower than the owner's own archive.** Ten seconds between
  fetches against six, and a per-run ceiling. This is research with
  no deadline, and being in a hurry is not a reason the platform
  would recognise.

## What this is not

Not authenticated access, not private or region-restricted content,
not a redistributable creator database, not commercial use. The paid
provider route in `competitor_media.py` is still there and is still
the right answer if this is ever commercialised, because it carries
a receipt and this does not.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import or_ as sa_or
from sqlalchemy import select
from sqlalchemy.orm import Session

from server.collectors import permalink, transient
from server.collectors.tiktok_archive import (SyncStatus, fetch_one,
                                              normalise_url, tool_available)
from server.db.models import (Creator, MediaAttempt,
                              TransientMedia, Video)

logger = logging.getLogger("virallab.collectors.competitor_archive")

#: How this acquisition is recorded on the transient row, so a file's
#: origin is auditable without reading code.
SOURCE = "yt-dlp-public"

#: Why we believe we may hold it, recorded per file.
PERMISSION_BASIS = "public-logged-out (ADR 0010)"

#: Politeness. The own-account path waits 6 seconds; this waits
#: longer because it is somebody else's catalogue and there is no
#: deadline attached to any of it.
MIN_SECONDS_BETWEEN = 10.0

#: Per-run ceiling. A loop over three thousand publications is
#: exactly the shape of thing that runs all night unattended.
DEFAULT_MAX_PER_RUN = 40

#: Statuses worth trying again later, as opposed to settled facts
#: about the video.
RETRYABLE = frozenset({
    SyncStatus.RATE_LIMITED, SyncStatus.TIMEOUT, SyncStatus.FAILED,
})

#: How many publications in a row may offer no video stream before
#: the pass stops anyway.
#:
#: One is a fact about one video: TikTok answers, and hands over an
#: m4a track and no video rendition. Measured 2026-11-03, three
#: publications sampled a minute apart served 720p h265 while two
#: others served audio only, so it is plainly per-publication.
#:
#: A long unbroken run of them is a different thing - it is what a
#: platform-wide change would look like from here, and it is the
#: shape the 30 October incident had. So the rate is watched even
#: though the single case is not. Stopping at six costs at most
#: six polite requests and keeps the ADR 0010 argument intact.
NO_VIDEO_IN_A_ROW_BEFORE_STOPPING = 6

#: How long before a transient failure is worth another go.
#: Hours, because this is research with no deadline and the
#: alternative is asking somebody else's servers the same question
#: every few minutes.
RETRY_AFTER = {
    SyncStatus.RATE_LIMITED: timedelta(hours=6),
    SyncStatus.TIMEOUT: timedelta(hours=2),
    SyncStatus.FAILED: timedelta(hours=12),
    # Settled. A publication the platform serves without pixels
    # today could in principle gain a rendition, but asking daily
    # on the chance is exactly the knocking ADR 0010 rules out.
    # A week, and `redo=True` for anyone who means it.
    SyncStatus.NO_VIDEO_OFFERED: timedelta(days=7),
}

#: Outcomes that will not change by asking again: the video is
#: gone, or private, or there is no URL to ask with. `None` in
#: `try_again_after` means exactly this.
TERMINAL = frozenset({
    SyncStatus.UNAVAILABLE, SyncStatus.PRIVATE, SyncStatus.GEO_BLOCKED,
    SyncStatus.BAD_URL, SyncStatus.NOT_OWNED,
})


def remember(session: Session, video_id: int, status: str,
             detail: str | None, *, route: str = "yt-dlp",
             now: datetime | None = None) -> None:
    """Record how this attempt went, so the next pass can skip it.

    Upserted, not appended: what matters is the last outcome and
    how many times we have asked, not a history nobody reads.
    """
    stamp = now or datetime.now(timezone.utc)
    wait = None if status in TERMINAL else RETRY_AFTER.get(status)
    row = session.scalar(select(MediaAttempt).where(
        MediaAttempt.video_id == video_id))
    if row is None:
        row = MediaAttempt(video_id=video_id, route=route,
                           first_tried_at=stamp, attempts=0)
        session.add(row)
    row.route = route
    row.status = status
    row.detail = (detail or "")[:1000] or None
    row.attempts = (row.attempts or 0) + 1
    row.last_tried_at = stamp
    row.try_again_after = (stamp + wait) if wait else None


def _asked_too_recently(now: datetime):
    """Publications it is not yet polite to ask about again."""
    return select(MediaAttempt.video_id).where(
        MediaAttempt.status != SyncStatus.OK,
        sa_or(MediaAttempt.try_again_after.is_(None),
              MediaAttempt.try_again_after > now))


@dataclass
class AcquireResult:
    attempted: int = 0
    acquired: int = 0
    #: TikTok answered and offered no video rendition. Counted
    #: separately from `unavailable` because the publication is
    #: there and public - we simply cannot measure its pixels.
    no_video_offered: int = 0
    skipped_have_it: int = 0
    unavailable: int = 0
    rate_limited: int = 0
    failures: list[dict] = field(default_factory=list)
    #: Terminal refusals - deleted, private, region-blocked. Kept
    #: apart from failures because they will never succeed and a
    #: retry loop over them is wasted politeness.
    refusals: list[dict] = field(default_factory=list)
    stopped_reason: str = ""

    def why_counts(self) -> dict:
        """How many of each reason, so a pattern is visible at a
        glance rather than by reading twenty rows."""
        counts: dict[str, int] = {}
        for item in self.failures + self.refusals:
            key = (item.get("why") or item.get("status") or "?")[:80]
            counts[key] = counts.get(key, 0) + 1
        return dict(sorted(counts.items(), key=lambda kv: -kv[1])[:8])

    def as_dict(self) -> dict:
        return {
            "attempted": self.attempted, "acquired": self.acquired,
            "skipped_have_it": self.skipped_have_it,
            "unavailable": self.unavailable,
            "rate_limited": self.rate_limited,
            "failures": self.failures[:20],
            "refusals": self.refusals[:20],
            "why_counts": self.why_counts(),
            "stopped_reason": self.stopped_reason,
            "cost_usd": 0.0,
            "permission_basis": PERMISSION_BASIS,
        }


def analysed_ids(session: Session):
    """Publications that already have every deterministic stage.

    The stage list comes from `features/competitor.py` rather
    than being restated, so what counts as analysed cannot drift
    between the thing that measures coverage and the thing that
    decides what to fetch.
    """
    from sqlalchemy import func

    from server.db.models import VideoFeature
    from server.features.competitor import STAGES

    wanted = {mv for _n, _r, mv in STAGES}
    return (select(VideoFeature.video_id)
            .where(VideoFeature.method_version.in_(sorted(wanted)),
            VideoFeature.status == "ok")
            .group_by(VideoFeature.video_id)
            .having(func.count(func.distinct(
                VideoFeature.method_version)) >= len(wanted)))


def candidates(session: Session, *, limit: int,
               creator_id: int | None = None,
               redo: bool = False,
               now: datetime | None = None
               ) -> list[tuple[Video, Creator]]:
    """Competitor publications we can reach and do not already hold.

    `videos.permalink` is empty for every competitor publication -
    the clockworks parser reads `webVideoUrl` and the ingest never
    wrote it - so the URL is derived from handle and external id by
    `permalink.for_video`, which the rest of the product already
    uses for exactly this. Deriving is better than re-scraping to
    recover something we can compute.

    **Already-analysed publications are excluded, and that is not
    an optimisation.** The first version skipped only what was
    currently held, and a file stops being held the moment the
    deterministic pass releases it - so a twelve-cycle run
    downloaded the same forty videos twelve times, analysed them
    twelve times, and moved coverage by nothing after the first
    cycle. Against somebody else's servers, that is not merely
    wasteful; it is the opposite of the rate limiting the whole
    route is conditioned on.

    `redo=True` is for a method-version bump, when re-fetching is
    the point.
    """
    have = select(TransientMedia.video_id).where(
        TransientMedia.deleted_at.is_(None))
    stmt = (select(Video, Creator)
            .join(Creator, Creator.id == Video.creator_id)
            .where(Creator.is_own_account.is_(False),
                   Video.platform == "tiktok",
                   Video.external_id.is_not(None),
                   Video.id.not_in(have)))
    if not redo:
        stmt = stmt.where(Video.id.not_in(analysed_ids(session)))
        # Skip what we have already asked about and been told
        # about. Without this the same publication came back
        # first on every pass - and with no ORDER BY it really
        # was the same one - so a video TikTok serves without a
        # rendition stopped five consecutive runs after one
        # attempt each.
        stmt = stmt.where(
            Video.id.not_in(_asked_too_recently(
                now or datetime.now(timezone.utc))))
    if creator_id is not None:
        stmt = stmt.where(Video.creator_id == creator_id)
    # Deterministic, and oldest first. An unordered query is not
    # random - Postgres returns whatever the plan produces, which
    # is stable enough to hand back the same rows forever.
    stmt = stmt.order_by(Video.id)

    out: list[tuple[Video, Creator]] = []
    for video, creator in session.execute(stmt).all():
        if permalink.for_video(video, creator):
            out.append((video, creator))
        if len(out) >= limit:
            break
    return out


def acquire_one(session: Session, video: Video,
                creator: Creator | None = None, *,
                now: datetime | None = None) -> tuple[str, str]:
    """Fetch one public video. Returns a `SyncStatus`.

    No credential reaches `fetch_one`: `cookies` is left at its
    default of `None`, which puts `--no-cookies` on the command line
    and leaves yt-dlp with nothing to authenticate with.
    """
    url = permalink.for_video(video, creator)
    if not url:
        return SyncStatus.UNAVAILABLE, "no public URL could be derived"
    # `(target, reason)`, not a target. It reports a rejected URL
    # rather than raising, so a caller that forgets to unpack hands
    # a tuple straight to the subprocess layer.
    target, reason = normalise_url(url)
    if target is None:
        logger.info("video %s has an unusable url: %s", video.id, reason)
        return SyncStatus.BAD_URL, reason

    dest = transient.path_for(video.id)
    # Deliberately no `cookies=`. See the module docstring: the
    # absence is the control.
    # `transcode=False`, and that is a measurement decision not a
    # performance one.
    #
    # The default re-encodes anything a browser cannot play, so
    # the owner can watch it in his own dashboard. Competitor
    # media is never shown to him - it is quarantined, measured
    # and deleted - so the re-encode buys nothing here and costs
    # something real: it rewrites the pixels before the features
    # are derived, which means contrast, colour and motion get
    # measured on an H.264 approximation of an HEVC original.
    # Our own videos would be measured on their originals. That
    # is a difference between the two halves of the corpus
    # introduced by a playback convenience.
    out = fetch_one(target, dest, transcode=False)
    if out.status != SyncStatus.OK:
        # The reason travels with the status. It was computed and
        # dropped, so a run that fetched nothing reported eleven
        # rows of {"status": "failed"} and no way to tell a deleted
        # video from a blocked one from a broken extractor.
        return out.status, (out.detail or "")[:300]

    try:
        transient.register(session, video_id=video.id, path=dest,
                           source=SOURCE,
                           permission_basis=PERMISSION_BASIS,
                           cost_usd=0.0, now=now)
    except Exception:
        # An unregistered file is invisible to the reaper. Delete it
        # now rather than leave a third-party file on disk with no
        # deadline attached to it.
        dest.unlink(missing_ok=True)
        logger.exception("could not register transient media for video %s; "
                         "the file has been deleted", video.id)
        raise
    return SyncStatus.OK, ""


def acquire(session: Session, *, max_videos: int = DEFAULT_MAX_PER_RUN,
            creator_id: int | None = None,
            pause_s: float = MIN_SECONDS_BETWEEN,
            redo: bool = False,
            sleeper=time.sleep) -> AcquireResult:
    """Fetch up to `max_videos` public competitor videos."""
    res = AcquireResult()
    available, detail = tool_available()
    if not available:
        res.stopped_reason = detail
        return res

    todo = candidates(session, limit=max_videos,
                      creator_id=creator_id, redo=redo)
    no_video_run = 0
    for i, (video, creator) in enumerate(todo):
        if i:
            sleeper(pause_s)
        res.attempted += 1
        status, detail = acquire_one(session, video, creator)
        remember(session, video.id, status, detail)
        if status == SyncStatus.OK:
            res.acquired += 1
            no_video_run = 0
            session.commit()
            continue
        if status == SyncStatus.RATE_LIMITED:
            res.rate_limited += 1
            # Stop the whole run rather than keep knocking. Being
            # told to slow down and continuing anyway is the thing
            # §2.3 forbids in spirit whatever the retry count says.
            #
            # `detail` is carried now. It used to be dropped here,
            # so the result said "the platform rate-limited the
            # pass" and nothing anywhere recorded which message
            # had produced that - which is how a per-video format
            # failure was read, for four days, as an IP block.
            res.failures.append({"video_id": video.id, "status": status,
                                 "why": detail})
            res.stopped_reason = (
                f"the platform refused the pass and it stopped rather "
                f"than continuing to request: {detail}")
            session.commit()
            break
        if status == SyncStatus.NO_VIDEO_OFFERED:
            # Not a refusal. TikTok answered and offered no video
            # rendition for this one publication, so skip it and
            # carry on - unless it keeps happening, which would
            # look like something changing underneath us.
            res.no_video_offered += 1
            no_video_run += 1
            res.refusals.append({"video_id": video.id, "status": status,
                                 "why": detail})
            if no_video_run >= NO_VIDEO_IN_A_ROW_BEFORE_STOPPING:
                res.stopped_reason = (
                    f"{no_video_run} publications in a row offered no "
                    f"video stream. One is normal; this many in a row "
                    f"is what a platform-wide change would look like, "
                    f"so the pass stopped rather than carry on "
                    f"requesting.")
                session.commit()
                break
            continue
        no_video_run = 0
        if status in RETRYABLE:
            res.failures.append({"video_id": video.id, "status": status,
                                 "why": detail})
        else:
            res.unavailable += 1
            res.refusals.append({"video_id": video.id, "status": status,
                                 "why": detail})
    session.commit()
    return res


def coverage(session: Session) -> dict:
    """How much of the competitor corpus has ever been measured."""
    from sqlalchemy import func

    from server.db.models import VideoFeature

    total = session.scalar(
        select(func.count(Video.id))
        .join(Creator, Creator.id == Video.creator_id)
        .where(Creator.is_own_account.is_(False))) or 0
    with_url = session.scalar(
        select(func.count(Video.id))
        .join(Creator, Creator.id == Video.creator_id)
        .where(Creator.is_own_account.is_(False),
               Video.platform == "tiktok",
               Video.external_id.is_not(None))) or 0
    measured = session.scalar(
        select(func.count(func.distinct(VideoFeature.video_id)))
        .join(Video, Video.id == VideoFeature.video_id)
        .join(Creator, Creator.id == Video.creator_id)
        .where(Creator.is_own_account.is_(False),
               VideoFeature.status == "ok",
               VideoFeature.feature.like(r"ocr\_%"))) or 0
    live = session.scalar(
        select(func.count(TransientMedia.id))
        .where(TransientMedia.deleted_at.is_(None))) or 0
    return {
        "competitor_publications": total,
        "with_a_public_url": with_url,
        "any_visual_measurement": measured,
        "files_held_right_now": live,
        "note": (
            "Files are deleted six hours after acquisition (ADR "
            "0009). 'Held right now' is a working set, not a "
            "library, and is expected to be near zero between runs."),
    }
