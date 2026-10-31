"""Work that happens without anyone pressing anything.

The owner asked for a platform that does its own research. The
buttons on `#/collect` were half of that; this is the other half.
Data that only refreshes when somebody remembers to refresh it is
data that is always a bit stale, and findings computed on stale
data are quietly wrong rather than loudly missing.

## Only free work runs on its own

The hard rule, asserted in code and in a test: **a scheduled
operation may not cost money.** Not "is capped", not "is cheap" -
may not cost anything at all. An overnight loop that spends is the
single worst thing this file could contain, and no amount of
ceilings makes an unattended charge something the owner agreed to.
Paid work stays a button he presses.

## No new table, no new process

Whether a kind is due is answered from the `operations` table
itself: has a run of this kind finished successfully within its
period? That is the same record the page already shows, so the
schedule cannot drift from the history - there is one account of
what ran and when.

The loop lives in the API process, like the reaper and the daily
collector that are already there. CLAUDE.md §6 fixes the topology
at three processes and a cron would be a fourth.

## It defers rather than queues

If a kind is already running, or ran recently, the tick does
nothing and tries again next hour. Nothing accumulates: a server
that was off for a week does not wake up and start seven
collections.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from server.db.models import Operation
from server.ops import catalogue as cat
from server.ops import runner

logger = logging.getLogger("virallab.ops.schedule")

#: How often the loop looks. Hourly is plenty for work measured in
#: days, and cheap enough to be uninteresting.
CHECK_INTERVAL_SECONDS = 3600


class Paid(RuntimeError):
    """A paid operation was put on the schedule."""


def _free(kind: str) -> str:
    """The kind, if it is free. Refuses loudly otherwise."""
    op = cat.get(kind)
    if op.costs_money:
        raise Paid(
            f"{kind!r} costs money and cannot be scheduled. Unattended "
            f"spending is not something a ceiling makes acceptable.")
    return kind


#: What runs on its own, and how often. Hours, not a cron string:
#: a cron expression in a config file is a thing nobody reads and
#: everybody mis-copies.
#:
#: The three extractors are here because they are pure computation
#: over rows already collected - no network, no provider, seconds
#: each - so running them nightly simply means the measurements
#: never lag the data.
#:
#: Own-account collection is NOT here either: the API already
#: refreshes it in-process every hour, and scheduling a second
#: path to the same job is how two collectors start disagreeing.
#:
#: `watch_competitors` is free (ADR 0010) and is deliberately NOT
#: here. It talks to someone else's servers for hours, and the one
#: thing ADR 0010's politeness argument cannot survive is doing
#: that unattended.
SCHEDULE: tuple[tuple[str, int], ...] = (
    ("read_captions", 24),
    ("read_repertoire", 24),
    ("read_hooks", 24),
)


def _validate() -> None:
    for kind, _hours in SCHEDULE:
        _free(kind)


_validate()


def last_success(session: Session, kind: str) -> datetime | None:
    """When a run of this kind last finished without failing."""
    return session.scalar(
        select(Operation.finished_at)
        .where(Operation.kind == kind,
               Operation.status.in_(("ok", "partial")),
               Operation.finished_at.is_not(None))
        .order_by(Operation.finished_at.desc()).limit(1))


def due(session: Session, *, now: datetime | None = None
        ) -> list[str]:
    """Which kinds should run, right now.

    A kind that is already running is not due, and a kind that
    finished inside its period is not due. Nothing queues: a
    machine that was off for a week wakes up and runs each kind
    once, not seven times.
    """
    stamp = now or datetime.now(timezone.utc)
    running = {op.kind for op in runner.live(session)}
    out = []
    for kind, hours in SCHEDULE:
        if kind in running:
            continue
        last = last_success(session, kind)
        if last is not None:
            if last.tzinfo is None:
                last = last.replace(tzinfo=timezone.utc)
            if stamp - last < timedelta(hours=hours):
                continue
        out.append(kind)
    return out


def tick(session: Session, *, now: datetime | None = None) -> list[str]:
    """Start whatever is due. Returns what was started."""
    started = []
    for kind in due(session, now=now):
        try:
            _free(kind)                     # belt and braces
            runner.start(session, kind)
            started.append(kind)
            logger.info("scheduled run of %s started", kind)
        except runner.AlreadyRunning:
            continue
        except Exception:                   # noqa: BLE001 - reported
            logger.exception("scheduled run of %s could not start", kind)
    return started


def describe(session: Session) -> list[dict]:
    """What runs on its own, for the page to show."""
    out = []
    for kind, hours in SCHEDULE:
        op = cat.get(kind)
        last = last_success(session, kind)
        out.append({
            "kind": kind, "title": op.title,
            "every_hours": hours,
            "last_finished": last,
            "costs_money": op.costs_money,
        })
    return out


async def loop(session_factory,
               check_interval_seconds: int = CHECK_INTERVAL_SECONDS
               ) -> None:
    """Look every hour, start what is due, never raise.

    A scheduler that dies on one bad tick is worse than none,
    because the page keeps saying work is automatic.
    """
    import asyncio

    while True:
        try:
            with session_factory() as session:
                started = tick(session)
            if started:
                logger.info("schedule started: %s", ", ".join(started))
        except asyncio.CancelledError:
            raise
        except Exception:                   # noqa: BLE001 - reported
            logger.exception("the schedule tick failed; continuing")
        await asyncio.sleep(check_interval_seconds)
