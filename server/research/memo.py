"""A short memory for the analyses, so a page is not two seconds.

`virality.build` and `virality.breakthroughs` read every feature
row in the database and run a few hundred rank tests. That is two
seconds, which is fine once and not fine four times for one page:
Home calls them through `next_steps`, the research hub calls them
directly, "Am I doing it?" calls them through `adherence`, and the
creative brief calls them again. Each of those recomputed the same
numbers from the same unchanged rows.

## Why a timed memo and not a materialised table

A stored result is a second copy of a derived number, and the
project's own rule is that a dashboard figure which drifts from the
data is worse than no dashboard. A memo that expires cannot drift
far: the worst case is a number that is a minute out of date, on a
corpus that changes when somebody presses a button.

It is also cleared explicitly when an operation finishes, which is
the only moment the underlying rows actually change in normal use.
So the TTL is a safety net, not the mechanism.

## What it must never do

Cache across databases or across arguments. The key carries the
database URL and every argument, because a memo that returns the
owner's findings for a competitor query would be the worst bug in
the product and the hardest to notice.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any, Callable

logger = logging.getLogger("virallab.research.memo")

#: How long a computed analysis may be reused. Short enough that a
#: stale number is never interesting, long enough that one page
#: load does not pay for the same work four times.
TTL_SECONDS = 90.0

_LOCK = threading.Lock()
_STORE: dict[tuple, tuple[float, Any]] = {}


def _database_of(session) -> str:
    """Which database this result came from.

    Read from the session's own bind rather than the environment.
    Tests point the app at a disposable database through
    `deps.configure`, which never touches `VIRALLAB_DB_URL`, so an
    env-based key would have been the same string for two different
    databases and one test would have been served another's
    findings.
    """
    try:
        return str(session.get_bind().url)
    except Exception:                       # noqa: BLE001
        return os.environ.get("VIRALLAB_DB_URL", "")


def remember(name: str, compute: Callable[[], Any], *,
             session=None, **kwargs) -> Any:
    """The value, computed at most once per TTL per argument set."""
    key = (_database_of(session) if session is not None
           else os.environ.get("VIRALLAB_DB_URL", ""),
           name, tuple(sorted(kwargs.items())))
    now = time.monotonic()
    with _LOCK:
        hit = _STORE.get(key)
        if hit and now - hit[0] < TTL_SECONDS:
            return hit[1]
    # Computed outside the lock: two callers racing may both run it,
    # which wastes a little work and never returns a wrong answer.
    # Holding the lock across a two-second query would serialise
    # every request in the app, which is a worse bug than the one
    # being fixed.
    value = compute()
    with _LOCK:
        _STORE[key] = (now, value)
    return value


def clear() -> int:
    """Forget everything. Called when the data underneath changes."""
    with _LOCK:
        n = len(_STORE)
        _STORE.clear()
    if n:
        logger.info("cleared %d remembered analyses", n)
    return n
