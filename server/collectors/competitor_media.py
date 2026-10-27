"""Acquire competitor media through the provider, briefly.

M2. The bytes arrive, features are derived, the bytes go. This module
owns the first half; `server/collectors/transient.py` owns the deadline
and the deletion.

## Why the provider and not the platform

`DEVELOPMENT_PLAN.md` §2.3 permits public, logged-out data for internal
research, with no circumvention of technical access controls. Fetching a
competitor's mp4 directly off TikTok's CDN would mean this project doing
the access itself. Routing it through the scraper's own paid
`video-download` add-on means the provider delivers the file under terms
it has already agreed with the platform, and we hold a receipt.

**Verified against the actor's own input schema, 2026-10-27**
(`clockworks/free-tiktok-scraper`, via `GET /v2/actor-builds/{id}`):

* ``postURLs`` — "Direct URLs for scraping specific tiktoks". This is how
  a named publication is fetched rather than a whole profile.
* ``shouldDownloadVideos`` — "⬇️ Add-on: video download ($)", default
  ``False``. The `($)` is the provider's own marking for a billed event,
  which is what makes this a paid add-on rather than a free flag.
* ``videoKvStoreIdOrName`` — "Unnamed key-value stores will be deleted
  after 7 days".

## A limit on what "transient" can mean

That last one matters and is recorded here rather than glossed. Our
six-hour guarantee covers **our** disk. The provider keeps its own copy
in its key-value store for up to seven days, and nothing this project
does shortens that. So the honest claim is "ViralLab does not retain
competitor media", not "no copy of this file exists anywhere" — and a
legal review before commercialisation (§2.3) needs the second sentence,
not the first.

## Spending

Every acquisition reserves against the `competitor_media` category,
which is separate from `discovery` so a batch of downloads cannot
consume the budget metadata collection runs on. A retry never
re-downloads and never re-bills: a publication with a live
`transient_media` row is skipped, and the reservation is keyed on the
video id.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from server import budget
from server.collectors import transient
from server.config import SpendRefused
from server.db.models import Creator, TransientMedia, Video

logger = logging.getLogger("virallab.collectors.competitor_media")

METHOD_VERSION = "competitor-media-v1"
ACTOR = "clockworks/free-tiktok-scraper"
CATEGORY = "competitor_media"

#: The permission record written against every acquired file, stored per
#: file so a later reviewer reads the basis that applied at acquisition
#: rather than whatever the policy says by then.
#:
#: **Paying a provider is not authorisation, and this text used to imply
#: it was.** The previous wording said the bytes arrived "under the
#: provider's own terms; not fetched from the platform by ViralLab",
#: which reads as though the contract with Apify settled the question.
#: It does not, and DEVELOPMENT_PLAN.md §2.3(4) already said so before
#: this module was written: vendors are preferred "for lower maintenance
#: and better reliability — **not** for risk transfer. Apify's terms
#: explicitly push liability onto the customer ... so legal exposure is
#: the same as scraping directly" (`docs/benchmarks/0A-tos-review.md`
#: §1). The code was claiming the opposite of the project's own rule.
#:
#: So the record is now split into what is actually established and what
#: is not, because a legal review needs to be able to tell them apart.
PERMISSION_BASIS = (
    "ESTABLISHED: the publication is public and reachable logged-out; "
    "ViralLab circumvented no authentication, captcha or other technical "
    "access control, and used no credentialed access and no fake account "
    "(DEVELOPMENT_PLAN.md §2.3 policy 1). The bytes were delivered by a "
    "third-party provider, {actor}, under a paid contract between "
    "ViralLab and that provider. Use is internal research input only: "
    "the file is held in quarantine outside the served media root, is "
    "never served over any endpoint, is never redistributed, and is "
    "deleted within {hours:g}h, after which only derived numeric "
    "features remain. "
    "NOT ESTABLISHED: that any of the above amounts to authorisation "
    "from the platform or from the creator. Paying a provider transfers "
    "no permission and no liability — Apify's terms place liability on "
    "the customer (DEVELOPMENT_PLAN.md §2.3 policy 4, "
    "docs/benchmarks/0A-tos-review.md §1), so the exposure is the same "
    "as collecting directly. The platform's own Terms of Service have "
    "NOT been verified against this use; whether the provider holds any "
    "authorisation of its own is unknown; no copyright licence has been "
    "obtained for the reproduction a download makes, and no fair-dealing "
    "or text-and-data-mining exception has been assessed for the "
    "applicable jurisdiction; no GDPR lawful basis has been formally "
    "documented for the personal data a creator's video contains. "
    "CONSEQUENCE: internal research use only while the project is "
    "private, and legal review before any commercialisation or any "
    "redistribution (CLAUDE.md §7, DEVELOPMENT_PLAN.md §2.3 policy 3). "
    "ALSO: the provider retains its own copy in its key-value store for "
    "up to 7 days; deleting ours does not reach it."
)

#: The claims above that are verified facts about what this code does,
#: as opposed to legal conclusions. Separated so a reviewer can see at a
#: glance which half of the record is engineering and which half is an
#: open question.
PERMISSION_ESTABLISHED = (
    "public logged-out publication",
    "no circumvention of technical access controls",
    "no credentialed access, no fake account",
    "delivered by a contracted third-party provider",
    "quarantined outside the served media root, never served",
    "never redistributed",
    "deleted within the TTL; only derived features persist",
)

#: The questions a legal review would have to answer. None of these is
#: settled, and nothing in this module may behave as though one is.
PERMISSION_OPEN = (
    "platform Terms of Service not verified against this use",
    "provider's own authorisation from the platform unknown",
    "no copyright licence for the reproduction a download makes",
    "no TDM or fair-dealing exception assessed for the jurisdiction",
    "no documented GDPR lawful basis for the personal data involved",
)

#: Refuse to start a download larger than this. A competitor reel is a few
#: megabytes; anything near this is a misidentified URL or a feature film,
#: and either way it should not land in quarantine unnoticed.
MAX_BYTES = 200 * 1024 * 1024


class AcquisitionRefused(RuntimeError):
    """Not acquired, and why. Not transient — a human decides."""


@dataclass
class AcquisitionOutcome:
    requested: int = 0
    acquired: int = 0
    skipped_already_held: int = 0
    failed: int = 0
    billed_usd: float | None = None
    bytes_total: int = 0
    errors: list[dict] = field(default_factory=list)
    media_ids: list[int] = field(default_factory=list)
    note: str = ""


def _already_held(session: Session, video_id: int) -> bool:
    return session.scalar(select(TransientMedia).where(
        TransientMedia.video_id == video_id,
        TransientMedia.deleted_at.is_(None))) is not None


def canonical_url(session: Session, video: Video) -> str | None:
    """The platform URL for a publication, rebuilt rather than stored.

    `videos` has no `url` column — an earlier draft of this module assumed
    one and would have raised at the first real call. TikTok's canonical
    form is fully determined by the handle and the publication id, so it
    is derived here instead of denormalised into the table.
    """
    creator = session.get(Creator, video.creator_id)
    if creator is None or not creator.handle or not video.external_id:
        return None
    if video.platform == "tiktok":
        return (f"https://www.tiktok.com/@{creator.handle}"
                f"/video/{video.external_id}")
    return None


def _assert_competitor(session: Session, video: Video) -> None:
    """Own-account media is preserved, not quarantined.

    Routing an own publication through here would delete a file this
    project is supposed to keep, and `media_preservation` already owns
    that path.
    """
    creator = session.get(Creator, video.creator_id)
    if creator is None:
        raise AcquisitionRefused(f"video {video.id} has no creator row")
    if creator.is_own_account:
        raise AcquisitionRefused(
            f"video {video.id} belongs to the owner's own account; use "
            f"media_preservation, which retains rather than quarantines")


def acquire(session: Session, *, videos: list[Video], token: str,
            media_root: Path | None = None,
            client: httpx.Client | None = None,
            now: datetime | None = None,
            max_files: int = 1) -> AcquisitionOutcome:
    """Fetch competitor media for up to `max_files` publications.

    `max_files` defaults to **one** on purpose. The per-file price of the
    download add-on has never been measured, and the first thing anyone
    should do with this function is run it once and read the bill.
    """
    from server.discovery.apify import ApifyClient

    stamp = now or datetime.now(timezone.utc)
    out = AcquisitionOutcome(requested=len(videos))

    todo: list[Video] = []
    urls: dict[int, str] = {}
    for v in videos:
        _assert_competitor(session, v)
        if _already_held(session, v.id):
            out.skipped_already_held += 1
            continue
        url = canonical_url(session, v)
        if not url:
            out.failed += 1
            out.errors.append({
                "video_id": v.id,
                "error": (f"no canonical URL can be built for platform "
                          f"{v.platform!r}")})
            continue
        todo.append(v)
        urls[v.id] = url
        if len(todo) >= max_files:
            break

    if not todo:
        out.note = ("nothing to acquire: every requested publication is "
                    "already held or has no URL")
        return out

    root = transient.quarantine_root()
    if media_root is not None:
        transient.assert_outside_media_root(root, media_root)

    # Estimate generously: the add-on price is unmeasured, and reserving
    # too little is the failure that cannot be undone.
    estimate = round(0.05 * len(todo) + 0.05, 4)
    reservation = budget.reserve(
        session, provider="apify", estimated_usd=estimate,
        category=CATEGORY,
        idempotency_key=f"competitor_media:{','.join(str(v.id) for v in todo)}",
        detail={"actor": ACTOR, "videos": [v.id for v in todo],
                "method_version": METHOD_VERSION})
    session.commit()

    http = client or httpx.Client(timeout=120.0, follow_redirects=True)
    api = ApifyClient(token)
    billed = None
    try:
        run = api.run_actor(ACTOR, {
            "postURLs": [urls[v.id] for v in todo],
            "resultsPerPage": len(todo),
            "shouldDownloadVideos": True,     # the paid add-on
            "shouldDownloadCovers": False,
            "shouldDownloadSubtitles": False,
            "shouldDownloadSlideshowImages": False,
        })
        finished = api.wait(run["id"])
        items = api.dataset_items(finished["defaultDatasetId"],
                                  limit=len(todo) * 4)
        billed = api.settled_cost_usd(run["id"], expected_items=len(todo))
        out.billed_usd = billed

        by_external = {str(v.external_id): v for v in todo}
        for item in items:
            ext = str(item.get("id") or "")
            video = by_external.get(ext)
            if video is None:
                continue
            urls = item.get("mediaUrls") or []
            if not urls:
                out.failed += 1
                out.errors.append({
                    "video_id": video.id,
                    "error": ("the actor returned no mediaUrls; the "
                              "download add-on may not have run")})
                continue
            dest = transient.path_for(video.id)
            try:
                size = _download(http, str(urls[0]), dest, token=token)
            except Exception as exc:          # noqa: BLE001 - recorded
                out.failed += 1
                out.errors.append({"video_id": video.id,
                                   "error": str(exc)[:300]})
                dest.unlink(missing_ok=True)
                continue
            row = transient.register(
                session, video_id=video.id, path=dest, source=f"apify:{ACTOR}",
                permission_basis=PERMISSION_BASIS.format(
                    actor=ACTOR, hours=transient.TTL.total_seconds() / 3600),
                cost_usd=(billed / len(todo) if billed else None),
                media_root=media_root, now=stamp)
            # Commit THIS registration before downloading the next.
            # MEASURED 2026-10-27: committing once after the loop left
            # every file of a 25-item batch unregistered from any other
            # session's view for as long as the batch ran, and the
            # concurrent reaper deleted 17 of them as orphans. We paid
            # for all 25. Per-file commit plus `transient.ORPHAN_GRACE`
            # closes that from both ends.
            session.commit()
            out.acquired += 1
            out.bytes_total += size
            out.media_ids.append(row.id)
        # Commit the registrations BEFORE settling. The settle block
        # below rolls back first to survive a poisoned session, and an
        # uncommitted registration would be discarded by that rollback —
        # leaving the bytes on disk with no ledger row, which is the
        # exact orphan state this module exists to prevent. Measured:
        # three files were stranded that way before this line existed.
        session.commit()
    finally:
        # Settle whatever happened. An unsettled reservation holds budget
        # until it expires, which would make the next acquisition look
        # unaffordable for no reason.
        #
        # The rollback first is not optional. If the body raised a
        # database error — a constraint violation, say — the session is
        # poisoned and every statement after it fails, including this
        # settle, so the reservation would be stranded as `reserved` by
        # the very path meant to release it. Measured: that is exactly
        # what happened on the first re-acquisition attempt.
        try:
            # Safe now: anything worth keeping is already committed, so
            # this only discards a half-finished failed transaction.
            session.rollback()
            budget.settle(session, reservation.id,
                          actual_usd=float(billed or 0.0),
                          notes=f"{out.acquired} file(s) acquired")
            session.commit()
        except Exception:                         # noqa: BLE001
            logger.exception(
                "could not settle reservation %s; it will expire and "
                "release its hold rather than being stranded",
                reservation.id)
            session.rollback()

    logger.info("competitor media: %d acquired, %d failed, billed %s",
                out.acquired, out.failed, out.billed_usd)
    return out


#: Hosts the Apify token may be sent to. The download add-on puts files in
#: Apify's key-value store, which needs the token — but `mediaUrls` is a
#: list of URLs supplied by a third party, and sending our credential
#: wherever one happens to point would leak it. So the token goes to
#: these hosts and nowhere else.
APIFY_HOSTS = ("api.apify.com",)


def _download(http: httpx.Client, url: str, dest: Path, *,
              token: str | None = None) -> int:
    """Stream to disk with a size ceiling, or leave nothing behind.

    MEASURED 2026-10-27: the add-on stores the file in Apify's key-value
    store and `mediaUrls` points at
    `api.apify.com/v2/key-value-stores/{id}/records/{name}`, which
    answers 403 without the token. An unauthenticated fetch therefore
    looks exactly like a permissions problem at the platform when it is
    really a missing header on our side.
    """
    from urllib.parse import urlsplit

    headers = {}
    host = urlsplit(url).hostname or ""
    if token and host in APIFY_HOSTS:
        headers["Authorization"] = f"Bearer {token}"
    elif token:
        logger.info("not sending the provider token to %s; it is not an "
                    "Apify host", host)

    written = 0
    with http.stream("GET", url, headers=headers) as resp:
        resp.raise_for_status()
        declared = resp.headers.get("content-length")
        if declared and int(declared) > MAX_BYTES:
            raise AcquisitionRefused(
                f"declared size {int(declared)} exceeds the {MAX_BYTES} "
                f"byte ceiling")
        with dest.open("wb") as fh:
            for chunk in resp.iter_bytes(1 << 20):
                written += len(chunk)
                if written > MAX_BYTES:
                    raise AcquisitionRefused(
                        f"stream exceeded the {MAX_BYTES} byte ceiling")
                fh.write(chunk)
    return written
