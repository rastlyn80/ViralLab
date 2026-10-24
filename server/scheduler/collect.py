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
from server.collectors.tiktok_official import TikTokAuth, TikTokOfficialAdapter
from server.collectors.tokens import ReauthRequired, TokenStore, TransientAuthError
from server.db.models import CollectionRun, OAuthToken, Video, utcnow

logger = logging.getLogger("virallab.collect")

MAX_ATTEMPTS = 3
BACKOFF_BASE_SECONDS = 30
#: Don't start another pass if one already succeeded within this window.
#: Survives process restarts because it is a database check, not a timer.
MIN_INTERVAL = timedelta(hours=20)


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


def due(session: Session, platform: str, account_ref: str, *,
        interval: timedelta = MIN_INTERVAL) -> bool:
    """Has enough time passed since the last SUCCESSFUL pass?

    Checked against the database rather than an in-memory timer, so a restart
    does not re-run a pass that already happened, and a long outage does not
    queue up a backlog of them.
    """
    last = session.execute(text("""
        select max(finished_at) from collection_runs
        where platform = :p and account_ref = :a and status = 'ok'"""),
        {"p": platform, "a": account_ref}).scalar()
    return last is None or (utcnow() - last) >= interval


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
        for v in adapter.fetch_account_videos(account):
            _, created = upsert_video(session, creator, v,
                                      source="official_api")
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


def _is_rate_limited(exc: Exception) -> bool:
    return (isinstance(exc, httpx.HTTPStatusError)
            and exc.response is not None
            and exc.response.status_code == 429)


async def collect_with_retries(session_factory, *, kind: str = "daily") -> str:
    """Run a pass, retrying bounded times on transient failures only."""
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            with session_factory() as session:
                collect_tiktok_once(session, kind=kind)
                session.commit()
            return "ok"
        except ReauthRequired as exc:
            # A human must act. Retrying cannot help and only burns quota.
            logger.error("tiktok collection needs re-authorisation: %s", exc)
            with session_factory() as session:
                session.commit()
            return "failed"
        except Exception as exc:
            limited = _is_rate_limited(exc)
            if attempt >= MAX_ATTEMPTS:
                logger.error("tiktok collection gave up after %d attempts: %s",
                             attempt, exc)
                return "deferred"
            # Full jitter: avoids synchronised retries if ever more than one
            # account is collected.
            delay = BACKOFF_BASE_SECONDS * (2 ** (attempt - 1))
            if limited:
                delay = max(delay, 300)
                logger.warning("tiktok rate-limited (429); backing off %ss", delay)
            delay = random.uniform(delay / 2, delay)
            logger.warning("tiktok collection attempt %d/%d failed (%s); "
                           "retrying in %.0fs", attempt, MAX_ATTEMPTS,
                           type(exc).__name__, delay)
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
            with session_factory() as session:
                row = session.scalar(
                    select(OAuthToken).where(OAuthToken.platform == "tiktok"))
                ready = row is not None and not row.needs_reauth
                is_due = ready and due(session, "tiktok", row.account_ref)
            if ready and is_due:
                logger.info("tiktok daily collection is due; starting")
                await collect_with_retries(session_factory)
            elif row is not None and row.needs_reauth:
                logger.warning("tiktok collection paused: re-authorisation "
                               "required")
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("daily collection loop error; continuing")
        await asyncio.sleep(check_interval_seconds)
