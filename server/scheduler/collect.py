"""Daily own-account collection.

Runs in the API process alongside the reaper (plan 4.1: no second service).
Every pass is recorded in `collection_runs` whether it succeeds, defers or
fails, so a silent stall is visible as an absence of recent rows rather than
being invisible.

Design points that matter:

  * **Bounded retries with backoff.** A pass retries a few times, then gives
    up until the next scheduled run. It never loops.
  * **Re-auth is terminal, not retried.** ReauthRequired means a human must
    act; retrying cannot help and would just burn quota.
  * **Transient failures defer.** A 5xx or a dropped connection is recorded
    as `deferred`, not `failed` — the same distinction the token layer makes.
  * **Rate limits are respected.** HTTP 429 backs off rather than hammering.
  * **Snapshots append.** A daily pass adds one reading per video, which is
    what eventually makes real fixed-age anchors possible (plan 5.4).
"""

from __future__ import annotations

import asyncio
import logging
import os
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from server.collectors.base import (
    finish_run, record_snapshot, start_run, upsert_creator, upsert_video,
)
from server.collectors.instagram_official import InstagramOfficialAdapter
from server.collectors.media_preservation import (
    MAX_ATTEMPTS as MEDIA_MAX_ATTEMPTS,
    preserve_instagram_media)
from server.collectors.snapshot_policy import select_for_refresh
from server.collectors.tiktok_official import TikTokAuth, TikTokOfficialAdapter
from server.collectors.tokens import ReauthRequired, TokenStore, TransientAuthError
from server.db.models import (
    CollectionRun, Creator, OAuthToken, Video, utcnow,
)

logger = logging.getLogger("virallab.collect")

MAX_ATTEMPTS = 3
BACKOFF_BASE_SECONDS = 30
#: Don't start another pass if one already succeeded within this window.
#: Survives process restarts because it is a database check, not a timer.
MIN_INTERVAL = timedelta(hours=20)
#: Per-run ceiling on Instagram insight calls. The real quota is
#: 4800 x impressions over 24h and is monitored live from the
#: response header; this is a second, static belt.
IG_MAX_CALLS_PER_RUN = 300


def _env() -> dict[str, str]:
    out = {}
    f = Path(__file__).resolve().parent.parent.parent / ".env"
    if f.exists():
        for line in f.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, _, v = line.partition("=")
                out[k.strip()] = v.strip()
    out.update(os.environ)
    return out


#: A pass still running after this long is assumed dead. Generous: a full
#: Instagram pass paginates the account, writes snapshots and preserves
#: media, and must never be declared dead while it is merely slow - the same
#: principle as the job reaper (CLAUDE.md 4.3).
RUN_STALE_AFTER = timedelta(hours=3)


def reconcile_stale_runs(session: Session, *,
                         stale_after: timedelta = RUN_STALE_AFTER) -> int:
    """Close out runs whose process died, and report how many.

    A pass killed mid-flight leaves `status='running'` forever. That is the
    opposite of what `collection_runs` is for: the table exists so a silent
    stall is visible, and a row stuck at 'running' makes a dead pass look
    like a working one. Such rows are marked `deferred` with an explanation,
    which is also what lets `due()` schedule the next attempt.
    """
    cutoff = utcnow() - stale_after
    result = session.execute(text("""
        update collection_runs
           set status = 'deferred',
               finished_at = now(),
               error = coalesce(error, '') ||
                 'process exited before this pass finished; closed out by '
                 'reconcile_stale_runs'
         where status = 'running' and started_at < :cutoff"""),
        {"cutoff": cutoff})
    n = result.rowcount or 0
    if n:
        session.commit()
        logger.warning("closed out %d collection run(s) left in 'running' by "
                       "a process that exited", n)
    return n


def due(session: Session, platform: str, account_ref: str, *,
        interval: timedelta = MIN_INTERVAL,
        stale_after: timedelta = RUN_STALE_AFTER) -> bool:
    """Should a pass start now?

    Checked against the database rather than an in-memory timer, so a restart
    does not re-run a pass that already happened, and a long outage does not
    queue up a backlog of them.

    Two conditions, not one. The original only asked whether enough time had
    passed since the last *successful* pass, which made a pass that was still
    **in flight** invisible: restarting the service mid-pass started a second
    overlapping one, because the first had no `finished_at` yet. Observed in
    production - three Instagram passes inside four minutes, duplicating API
    calls and writing near-duplicate snapshots at effectively the same age.
    """
    row = session.execute(text("""
        select
          (select max(finished_at) from collection_runs
            where platform = :p and account_ref = :a and status = 'ok')
            as last_ok,
          (select max(started_at) from collection_runs
            where platform = :p and account_ref = :a and status = 'running')
            as in_flight"""),
        {"p": platform, "a": account_ref}).one()
    last_ok, in_flight = row[0], row[1]

    # A pass that is plausibly still working owns the slot. One that has
    # exceeded the stale window is assumed dead and does not block forever -
    # reconcile_stale_runs() closes it out separately.
    if in_flight is not None and (utcnow() - in_flight) < stale_after:
        logger.info("%s pass started %s is still in flight; not starting "
                    "another", platform, in_flight)
        return False

    return last_ok is None or (utcnow() - last_ok) >= interval


class CollectionDisabled(RuntimeError):
    """This host is not the designated collector.

    Ubuntu is the authoritative database and collector; Windows is the
    intermittent GPU worker. Collecting from both would fork the dataset into
    two partial histories, which is far worse than not collecting at all.
    """


def collect_tiktok_once(session: Session, *, kind: str = "daily") -> CollectionRun:
    """One collection pass for the stored TikTok account."""
    env = _env()
    if env.get("VIRALLAB_COLLECTION_ENABLED", "1") in ("0", "false", ""):
        raise CollectionDisabled(
            "collection is disabled on this host; Ubuntu is the authoritative "
            "collector (set VIRALLAB_COLLECTION_ENABLED=1 only there)")
    token_row = session.scalar(
        select(OAuthToken).where(OAuthToken.platform == "tiktok"))
    if token_row is None:
        raise ReauthRequired("no stored TikTok credential")
    account = token_row.account_ref

    run = start_run(session, platform="tiktok", account_ref=account, kind=kind)
    store = TokenStore(session)
    adapter = TikTokOfficialAdapter(
        store, TikTokAuth(env["TIKTOK_CLIENT_KEY"], env["TIKTOK_CLIENT_SECRET"]))

    try:
        creator = upsert_creator(session, platform="tiktok",
                                 handle=env.get("TIKTOK_HANDLE", account),
                                 external_id=account, is_own_account=True)
        seen = new = 0
        # Kept so media preservation does not have to walk the media edge a
        # second time: one daily pass should paginate the account once.
        fetched = []
        for v in adapter.fetch_account_videos(account):
            _, created = upsert_video(session, creator, v,
                                      source="official_api")
            fetched.append(v)
            seen += 1
            new += int(created)
        session.flush()

        by_ext = {v.external_id: v for v in session.scalars(
            select(Video).where(Video.platform == "tiktok")).all()}
        snaps = 0
        for m in adapter.fetch_metrics(account, list(by_ext)):
            target = by_ext.get(m.external_id)
            if target is not None:
                record_snapshot(session, target, m)
                snaps += 1

        finish_run(session, run, status="ok", videos_seen=seen,
                   videos_new=new, snapshots_written=snaps)
        logger.info("tiktok daily pass ok: %d seen, %d new, %d snapshots",
                    seen, new, snaps)
        return run
    except Exception as exc:
        session.rollback()
        # The run row was rolled back with everything else; re-record it so a
        # failure leaves a trace rather than vanishing.
        failed = start_run(session, platform="tiktok", account_ref=account,
                           kind=kind)
        status = "failed" if isinstance(exc, ReauthRequired) else "deferred"
        finish_run(session, failed, status=status,
                   error=f"{type(exc).__name__}: {exc}")
        raise


def collect_instagram_once(session: Session, *, kind: str = "daily"
                           ) -> CollectionRun:
    """One collection pass for the stored Instagram account.

    Unlike TikTok, Instagram exposes `saves` and returns an expiring
    `media_url`, so media preservation runs at the END of this pass rather
    than as a separate chore: a Reel collected today cannot be preserved
    next week, because the signed CDN link will be dead.

    Preservation is deliberately the last step and is wrapped so that it can
    fail without touching anything above it. Metrics are the irreplaceable
    record - a snapshot missed today is gone - while media is best-effort and
    retried on the next pass.
    """
    env = _env()
    if env.get("VIRALLAB_COLLECTION_ENABLED", "1") in ("0", "false", ""):
        raise CollectionDisabled(
            "collection is disabled on this host; Ubuntu is the authoritative "
            "collector")
    token_row = session.scalar(
        select(OAuthToken).where(OAuthToken.platform == "instagram"))
    if token_row is None:
        raise ReauthRequired("no stored Instagram credential")
    account = token_row.account_ref

    run = start_run(session, platform="instagram", account_ref=account,
                    kind=kind)
    adapter = InstagramOfficialAdapter(TokenStore(session))
    try:
        existing = session.scalar(select(Creator).where(
            Creator.platform == "instagram",
            Creator.external_id == account))
        creator = upsert_creator(
            session, platform="instagram",
            handle=existing.handle if existing else account,
            external_id=account, is_own_account=True)
        seen = new = 0
        for v in adapter.fetch_account_videos(account):
            _, created = upsert_video(session, creator, v,
                                      source="official_api")
            seen += 1
            new += int(created)
        session.flush()

        by_ext = {v.external_id: v for v in session.scalars(
            select(Video).where(Video.platform == "instagram")).all()}
        # Not every stored post: the policy prioritises never-seen and young
        # posts (where fixed-age anchors are still capturable) and rotates
        # the archive, so quota is spent where it buys something.
        targets = select_for_refresh(session, "instagram",
                                     max_calls=IG_MAX_CALLS_PER_RUN)
        snaps = 0
        for m in adapter.fetch_metrics(account, targets):
            target = by_ext.get(m.external_id)
            if target is not None:
                record_snapshot(session, target, m)
                snaps += 1

        # Commit the metrics before touching media, so a media problem
        # cannot roll back the snapshots this pass just collected.
        finish_run(session, run, status="ok", videos_seen=seen,
                   videos_new=new, snapshots_written=snaps)
        session.commit()
        logger.info("instagram daily pass ok: %d seen, %d new, %d snapshots",
                    seen, new, snaps)

        _preserve_media_quietly(session, adapter, account, items=fetched)
        return run
    except Exception as exc:
        session.rollback()
        failed = start_run(session, platform="instagram",
                           account_ref=account, kind=kind)
        status = "failed" if isinstance(exc, ReauthRequired) else "deferred"
        finish_run(session, failed, status=status,
                   error=f"{type(exc).__name__}: {exc}")
        raise


def _preserve_media_quietly(session: Session, adapter, account: str, *,
                            items=None) -> None:
    """Preserve newly collected Instagram media; never raise.

    Isolated on purpose. Media preservation touches the filesystem, the
    network and a CDN whose links expire - three things that fail for
    reasons unrelated to collection. None of them is allowed to turn a
    successful metrics pass into a failed one.
    """
    if _env().get("VIRALLAB_PRESERVE_MEDIA", "1") in ("0", "false", ""):
        logger.info("instagram media preservation disabled by configuration")
        return
    try:
        root = Path(_env().get("VIRALLAB_MEDIA_ROOT")
                    or "/home/rastlyn/virallab/media")
        res = preserve_instagram_media(session, adapter, account,
                                       media_root=root, items=items)
        logger.info(
            "instagram media: %d saved, %d unsupported, %d api-restricted, "
            "%d failed, %d deduplicated, %.1f MB added%s",
            res.saved, res.unsupported, res.api_restricted, res.failed,
            res.deduplicated, res.bytes_added / 1e6,
            f" ({res.stopped_reason})" if res.stopped_reason else "")
        if (res.orphans_adopted or res.orphans_removed
                or res.part_files_removed):
            logger.info("instagram media sweep: %d orphan(s) adopted, "
                        "%d removed, %d part file(s) cleaned",
                        res.orphans_adopted, res.orphans_removed,
                        res.part_files_removed)
        if res.failed or res.skipped_attempts:
            logger.warning(
                "instagram media needs attention: %d failed this pass, "
                "%d have exhausted their %d attempts; reasons=%s",
                res.failed, res.skipped_attempts, MEDIA_MAX_ATTEMPTS, res.reasons)
    except Exception:
        # Deliberately swallowed: see the docstring. The next pass retries.
        logger.exception("instagram media preservation failed; metrics for "
                         "this pass are unaffected and media will be "
                         "retried on the next pass")
        session.rollback()


#: Every platform the daily loop should collect.
COLLECTORS = {"tiktok": collect_tiktok_once,
              "instagram": collect_instagram_once}


def _is_rate_limited(exc: Exception) -> bool:
    return (isinstance(exc, httpx.HTTPStatusError)
            and exc.response is not None
            and exc.response.status_code == 429)


async def collect_with_retries(session_factory, *, platform: str = "tiktok",
                               kind: str = "daily") -> str:
    """Run one platform's pass, retrying bounded times on transient failures."""
    collector = COLLECTORS[platform]
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            with session_factory() as session:
                collector(session, kind=kind)
                session.commit()
            return "ok"
        except ReauthRequired as exc:
            # A human must act. Retrying cannot help and only burns quota.
            logger.error("%s collection needs re-authorisation: %s", platform, exc)
            with session_factory() as session:
                session.commit()
            return "failed"
        except Exception as exc:
            limited = _is_rate_limited(exc)
            if attempt >= MAX_ATTEMPTS:
                logger.error("%s collection gave up after %d attempts: %s",
                             platform, attempt, exc)
                return "deferred"
            # Full jitter: avoids synchronised retries if ever more than one
            # account is collected.
            delay = BACKOFF_BASE_SECONDS * (2 ** (attempt - 1))
            if limited:
                delay = max(delay, 300)
                logger.warning("%s rate-limited (429); backing off %ss", platform, delay)
            delay = random.uniform(delay / 2, delay)
            logger.warning("%s collection attempt %d/%d failed (%s); "
                           "retrying in %.0fs", platform, attempt,
                           MAX_ATTEMPTS, type(exc).__name__, delay)
            await asyncio.sleep(delay)
    return "deferred"


async def daily_collection_loop(session_factory, *,
                                check_interval_seconds: int = 3600) -> None:
    """Background loop: wake hourly, collect if a pass is due.

    Hourly waking with a database-backed due() check means a restart at any
    point resumes correctly — no missed day, no double pass.
    """
    while True:
        try:
            # Close out anything a previous process left mid-flight, so a
            # dead pass neither masquerades as a live one nor blocks the
            # next attempt.
            with session_factory() as session:
                reconcile_stale_runs(session)
            for platform in COLLECTORS:
                with session_factory() as session:
                    row = session.scalar(select(OAuthToken).where(
                        OAuthToken.platform == platform))
                    ready = row is not None and not row.needs_reauth
                    is_due = ready and due(session, platform, row.account_ref)
                if ready and is_due:
                    logger.info("%s daily collection is due; starting",
                                platform)
                    await collect_with_retries(session_factory,
                                               platform=platform)
                elif row is not None and row.needs_reauth:
                    logger.warning("%s collection paused: re-authorisation "
                                   "required", platform)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("daily collection loop error; continuing")
        await asyncio.sleep(check_interval_seconds)
