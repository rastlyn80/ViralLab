"""Start an operation, follow it, and record what happened.

Runs inside the API process. CLAUDE.md §6 fixes the topology at
three processes - Postgres, this app, the Windows worker - and a
job runner is not worth a fourth. A thread per operation, at most
one per kind, is enough for passes that are measured in minutes and
started by one person.

## Why a subprocess and not an import

Every operation here already exists as a provision script that has
been run by hand, with its own dry-run, its own ceilings and its
own output. Importing and re-driving that logic would mean a second
code path for the same work, and the second one would be the one
nobody tested. So the runner starts the script the same way an
operator would and reads its output.

The cost of that choice is that a crashed API loses the follower,
not the work: the child keeps running, and `reconcile` adopts or
closes the orphaned row on the next start. That is the honest
trade, and it is recorded rather than hidden.

## What cannot happen

* **No caller chooses a command.** `catalogue.argv` builds the
  argument list from a key and bounded numbers. Nothing is passed
  to a shell - `shell=False`, a list, always.
* **No two runs of a kind at once.** A partial unique index in
  Postgres enforces it, not a Python check, because these passes
  hit other people's servers and paid APIs and a double run would
  double both.
* **No unbounded log.** The tail is kept; the middle is dropped
  with a line saying so. A diagnostics feature that fills a disk is
  a bug.
"""

from __future__ import annotations

import json
import logging
import os
import re
import signal
import subprocess
import threading
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from server.db.models import Operation
from server.ops import catalogue as cat

logger = logging.getLogger("virallab.ops.runner")

#: How much of the output to keep on the row.
LOG_LIMIT = 20_000

#: Lines that look like progress, so the page can show something
#: more useful than a spinner. Matched loosely on purpose: these
#: scripts were written to be read by a person, not parsed.
_PROGRESS = re.compile(
    r"(?P<done>\d+)\s*/\s*(?P<total>\d+)|=== cycle (?P<c>\d+) of (?P<n>\d+)")

LIVE = ("queued", "running")


def live(session: Session, kind: str | None = None) -> list[Operation]:
    stmt = select(Operation).where(Operation.status.in_(LIVE))
    if kind:
        stmt = stmt.where(Operation.kind == kind)
    return list(session.scalars(stmt).all())


def recent(session: Session, limit: int = 20) -> list[Operation]:
    return list(session.scalars(
        select(Operation).order_by(Operation.requested_at.desc())
        .limit(limit)).all())


class AlreadyRunning(RuntimeError):
    """This kind of operation is already going."""


def start(session: Session, kind: str, params: dict | None = None
          ) -> Operation:
    """Record the intent, then run it. Raises if one is already live.

    The row is committed *before* the thread starts, so a page
    refreshed immediately after the button shows the operation
    rather than nothing.
    """
    op = cat.get(kind)                      # KeyError for anything else
    clean = cat.clean_params(kind, params)
    if live(session, kind):
        raise AlreadyRunning(f"{op.title} is already running")

    row = Operation(kind=kind, status="queued", params=clean)
    session.add(row)
    session.commit()
    session.refresh(row)

    thread = threading.Thread(
        target=_run, args=(row.id, kind, clean),
        name=f"op-{kind}-{row.id}", daemon=True)
    thread.start()
    return row


def _update(row_id: int, **fields) -> None:
    """One short transaction per update, on its own session.

    The runner thread must not share the request's session: that
    session belongs to a request that has already returned.
    """
    from server.api.deps import session_factory

    Factory = session_factory()
    with Factory() as s:
        row = s.get(Operation, row_id)
        if row is None:
            return
        for key, value in fields.items():
            setattr(row, key, value)
        s.commit()


def _tail(text: str) -> str:
    if len(text) <= LOG_LIMIT:
        return text
    keep = LOG_LIMIT // 2
    return (text[:keep]
            + f"\n\n[... {len(text) - LOG_LIMIT} characters dropped ...]\n\n"
            + text[-keep:])


def _summarise(output: str) -> dict | None:
    """The last JSON object the script printed, if it printed one.

    Every provision script ends by printing its result as JSON, so
    this is how an operation reports what it actually did without
    the runner knowing anything about the specific pass.
    """
    start_at = output.rfind("\n{")
    if start_at < 0:
        start_at = 0 if output.lstrip().startswith("{") else -1
    if start_at < 0:
        return None
    try:
        return json.loads(output[start_at:])
    except json.JSONDecodeError:
        return None


def _cost_of(summary: dict | None) -> float:
    """What the pass says it spent. Zero unless it says otherwise."""
    if not isinstance(summary, dict):
        return 0.0
    for key in ("spent_usd", "spent_this_run_usd", "cost_usd", "billed_usd"):
        value = summary.get(key)
        if isinstance(value, (int, float)):
            return float(value)
    result = summary.get("result")
    if isinstance(result, dict):
        return _cost_of(result)
    return 0.0


def _run(row_id: int, kind: str, params: dict) -> None:
    argv = cat.argv(kind, params)
    env = dict(os.environ)
    _update(row_id, status="running",
            started_at=datetime.now(timezone.utc),
            progress_note="Starting")
    chunks: list[str] = []
    try:
        proc = subprocess.Popen(
            argv, cwd=cat.ROOT, env=env, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            # A list and `shell=False`: there is no shell to inject
            # into, and the argument list came from the catalogue.
            shell=False)
        _update(row_id, pid=proc.pid)
        assert proc.stdout is not None
        for line in proc.stdout:
            chunks.append(line)
            note = _progress_from(line)
            if note:
                _update(row_id, **note)
            if len(chunks) % 40 == 0:
                _update(row_id, log=_tail("".join(chunks)))
        code = proc.wait()
    except Exception as exc:                # noqa: BLE001 - recorded
        logger.exception("operation %s failed to run", kind)
        _update(row_id, status="failed", error=str(exc)[:1000],
                finished_at=datetime.now(timezone.utc),
                log=_tail("".join(chunks)))
        return

    output = "".join(chunks)
    summary = _summarise(output)
    _update(row_id,
            status="ok" if code == 0 else "failed",
            error=None if code == 0 else f"exited with code {code}",
            finished_at=datetime.now(timezone.utc),
            progress_note="Finished" if code == 0 else "Stopped",
            cost_usd=_cost_of(summary),
            summary=summary, log=_tail(output))


def _progress_from(line: str) -> dict | None:
    m = _PROGRESS.search(line)
    if not m:
        return None
    if m.group("c"):
        done, total = int(m.group("c")), int(m.group("n"))
        return {"done": done, "total": total,
                "progress_note": f"Round {done} of {total}"}
    done, total = int(m.group("done")), int(m.group("total"))
    return {"done": done, "total": total,
            "progress_note": f"{done:,} of {total:,} done"}


def cancel(session: Session, row_id: int) -> bool:
    """Ask a running operation to stop. Returns whether it was live."""
    row = session.get(Operation, row_id)
    if row is None or row.status not in LIVE:
        return False
    if row.pid:
        try:
            os.kill(row.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        except Exception:                   # noqa: BLE001 - reported
            logger.exception("could not signal pid %s", row.pid)
    row.status = "cancelled"
    row.finished_at = datetime.now(timezone.utc)
    row.progress_note = "Stopped by you"
    session.commit()
    return True


def reconcile(session: Session) -> int:
    """Close rows left running by an API restart.

    The follower is a thread in this process, so a restart loses
    it. The child may well have finished the work; what is certain
    is that nothing is watching it any more, and a row that claims
    to be running forever would block its kind from ever being
    started again.
    """
    stale = live(session)
    for row in stale:
        alive = False
        if row.pid:
            try:
                os.kill(row.pid, 0)
                alive = True
            except (ProcessLookupError, PermissionError):
                alive = False
            except Exception:               # noqa: BLE001
                alive = False
        row.status = "partial" if alive else "failed"
        row.finished_at = datetime.now(timezone.utc)
        row.error = (
            "the app restarted while this was running; the work may "
            "have finished but nothing was watching it"
            if alive else
            "the app restarted while this was running and the process "
            "is gone")
        row.progress_note = "Interrupted"
    if stale:
        session.commit()
        logger.warning("closed %d operation(s) left by a restart",
                       len(stale))
    return len(stale)
