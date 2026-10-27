"""Competitor media: where it may live, and what deletes it.

`docs/MULTIMODAL_PIPELINE.md` §0 is the rule — competitor media is
fetched, analysed and deleted, never accumulated. This module is the
enforcement. The design principle throughout is that **the deadline is
kept by code that runs, not by code that intends**: a retention policy
nothing executes is a policy nobody has.

## The quarantine root is outside MEDIA_ROOT, deliberately

`server/api/app.py` serves preserved own-account media from `MEDIA_ROOT`
through an HMAC-signed URL, and it contains the path to stop a crafted
`media_path` escaping that directory. Putting competitor bytes anywhere
under `MEDIA_ROOT` would mean the only thing standing between them and a
signed URL is the `is_own_account` check in the frames endpoint — one
check, one mistake away from serving third-party video.

Keeping them in a sibling directory means the containment check that
already exists refuses them structurally, whatever any later handler
believes. Two independent barriers rather than one.

## Everything that reaps

The six-hour cap has to survive the ways a job can end badly, so there
are four entry points and they all land in `release()`:

* ``features_persisted`` — the normal path, the moment the derived
  features are safely stored (CLAUDE.md §4.7: delete only after the
  result is confirmed).
* ``expired`` — the reaper's sweep, for anything past `expires_at`.
* ``cancelled`` — the operator stopped the work.
* ``crash_recovery`` — a file on disk with no live ledger row, or a row
  whose job vanished. This is the one that catches the case nobody
  planned for, which is why `sweep()` reconciles the directory against
  the table in both directions rather than only walking the table.

A row is never deleted. The file goes; the record that it existed and
was removed stays, because that is what an audit reads.
"""

from __future__ import annotations

import hashlib
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from server.db.models import TransientMedia

logger = logging.getLogger("virallab.collectors.transient")

#: How long a competitor file may exist. `analysis_seam.COMPETITOR_MEDIA_TTL`
#: declared this first; it is re-stated here because this module is what
#: enforces it and a constant you have to follow an import to find is a
#: constant that drifts.
TTL = timedelta(hours=6)

#: Sibling of MEDIA_ROOT, never inside it. See the module docstring.
#: Overridable so a test can point it at tmp_path without touching the
#: real directory.
QUARANTINE_ROOT = Path(os.environ.get(
    "VIRALLAB_QUARANTINE_ROOT", "./media_transient")).resolve()

DELETE_REASONS = ("features_persisted", "expired", "cancelled",
                  "crash_recovery", "manual")

#: How recently a file may have been written before the orphan rule stops
#: applying to it.
#:
#: MEASURED 2026-10-27, by losing 17 of 25 paid downloads. A batch writes
#: bytes, registers a row, and commits; between the write and the commit
#: the file exists while no OTHER session can see its row, so the reaper
#: running concurrently classified it as unregistered and deleted it. The
#: acquisition then committed 25 rows pointing at 8 surviving files.
#:
#: Committing per file (which `competitor_media.acquire` now does) narrows
#: that window to milliseconds but cannot close it: the bytes necessarily
#: hit disk before the row is durable. This grace period closes it, and is
#: safe because it is three orders of magnitude below the six-hour TTL —
#: it delays the deletion of genuinely orphaned files by minutes, and
#: delays the deletion of nothing else at all.
ORPHAN_GRACE = timedelta(minutes=10)


class QuarantineViolation(RuntimeError):
    """A competitor file was about to land somewhere it may not."""


def quarantine_root() -> Path:
    """The quarantine directory, created if absent."""
    QUARANTINE_ROOT.mkdir(parents=True, exist_ok=True)
    return QUARANTINE_ROOT


def assert_outside_media_root(path: Path, media_root: Path) -> None:
    """Refuse a quarantine path that sits under the served media root.

    Checked at acquisition rather than trusted from configuration,
    because `VIRALLAB_QUARANTINE_ROOT` is an environment variable and a
    deployment that set it to `./media/transient` would quietly make
    every competitor file addressable by the signed-URL endpoint.
    """
    p, m = path.resolve(), media_root.resolve()
    if p == m or m in p.parents:
        raise QuarantineViolation(
            f"the quarantine root {p} is inside MEDIA_ROOT {m}. Competitor "
            f"media must not be reachable by the media-serving endpoint; "
            f"point VIRALLAB_QUARANTINE_ROOT at a sibling directory.")


def path_for(video_id: int, suffix: str = ".mp4") -> Path:
    return quarantine_root() / f"competitor_{video_id}{suffix}"


def register(session: Session, *, video_id: int, path: Path, source: str,
             permission_basis: str, cost_usd: float | None = None,
             media_root: Path | None = None,
             now: datetime | None = None,
             ttl: timedelta = TTL) -> TransientMedia:
    """Record a competitor file the moment it lands, with its deadline.

    Called straight after the bytes are written and before anything reads
    them. If this raises, the caller must delete the file: an unregistered
    file is invisible to the reaper, and invisible is exactly how a
    research cache becomes a creator database.
    """
    stamp = now or datetime.now(timezone.utc)
    if media_root is not None:
        assert_outside_media_root(path.parent, media_root)

    size = path.stat().st_size if path.exists() else None
    digest = None
    if path.exists():
        h = hashlib.sha256()
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        digest = h.hexdigest()

    row = TransientMedia(
        video_id=video_id, path=str(path.resolve()), sha256=digest,
        size_bytes=size, acquired_at=stamp, expires_at=stamp + ttl,
        source=source, permission_basis=permission_basis,
        cost_usd=cost_usd)
    session.add(row)
    session.flush()
    logger.info("transient media registered for video %s, expires %s",
                video_id, row.expires_at.isoformat())
    return row


def release(session: Session, row: TransientMedia, *, reason: str,
            now: datetime | None = None) -> bool:
    """Delete the file and record that it happened. Idempotent.

    Returns True when this call removed a file, False when there was
    nothing left to remove — which is a normal race, not an error.
    """
    if reason not in DELETE_REASONS:
        raise ValueError(f"unknown delete reason {reason!r}")
    stamp = now or datetime.now(timezone.utc)
    p = Path(row.path)
    removed = False
    try:
        if p.exists():
            p.unlink()
            removed = True
    except OSError as exc:
        # Record the failure on the row rather than raising: a file that
        # could not be deleted is exactly what the next sweep must retry,
        # and an exception here would abort a batch mid-flight.
        row.audit = {**(row.audit or {}),
                     "delete_error": str(exc)[:300],
                     "delete_attempted_at": stamp.isoformat()}
        logger.error("could not delete transient media %s: %s", p, exc)
        session.flush()
        return False

    if row.deleted_at is None:
        row.deleted_at = stamp
        row.delete_reason = reason
    session.flush()
    logger.info("transient media released (%s) for video %s", reason,
                row.video_id)
    return removed


def live_rows(session: Session) -> list[TransientMedia]:
    return list(session.scalars(select(TransientMedia).where(
        TransientMedia.deleted_at.is_(None))).all())


def sweep(session: Session, *, now: datetime | None = None,
          media_root: Path | None = None) -> dict:
    """Enforce the deadline, in both directions.

    Walking only the table would miss a file that was written and never
    registered — the crash case. Walking only the directory would miss a
    row whose file is already gone but which still reads as live. So this
    does both and reports each separately.
    """
    stamp = now or datetime.now(timezone.utc)
    expired = orphans = reconciled = skipped_recent = 0

    for row in live_rows(session):
        if row.expires_at <= stamp:
            release(session, row, reason="expired", now=stamp)
            expired += 1
        elif not Path(row.path).exists():
            # The file is already gone; the ledger has not caught up.
            row.deleted_at = stamp
            row.delete_reason = "crash_recovery"
            row.audit = {**(row.audit or {}),
                         "note": "file absent at sweep; row reconciled"}
            reconciled += 1

    root = quarantine_root()
    if media_root is not None:
        assert_outside_media_root(root, media_root)
    # LIVE rows only. Including deleted rows here was a real bug: the
    # quarantine path is derived from the video id, so a file re-created
    # at a path whose only row is already marked deleted looked "known"
    # and was skipped — leaving bytes on disk under an audit record that
    # said they had been removed. That is strictly worse than no record.
    known = {Path(r.path).resolve() for r in live_rows(session)}
    for found in root.glob("*"):
        if not found.is_file() or found.resolve() in known:
            continue
        age = stamp - datetime.fromtimestamp(found.stat().st_mtime,
                                             tz=timezone.utc)
        if age < ORPHAN_GRACE:
            # Too new to judge: an acquisition may be mid-flight, with the
            # bytes written and the row not yet committed. Deleting here
            # destroys work that was paid for.
            skipped_recent += 1
            continue
        # A competitor file no LIVE row claims. Either nobody recorded it
        # (a crash between writing the bytes and committing the row) or a
        # row recorded it and already declared it deleted. Both mean there
        # is no basis to keep it and no expiry to measure it against, so
        # it goes now.
        try:
            found.unlink()
            orphans += 1
            logger.warning("deleted unregistered file in quarantine: %s",
                           found.name)
        except OSError as exc:
            logger.error("could not delete orphan %s: %s", found, exc)

    session.flush()
    out = {"expired": expired, "orphans_deleted": orphans,
           "rows_reconciled": reconciled,
           "recent_files_left_alone": skipped_recent,
           "still_live": len(live_rows(session)),
           "ttl_hours": TTL.total_seconds() / 3600.0,
           "orphan_grace_minutes": ORPHAN_GRACE.total_seconds() / 60.0}
    if expired or orphans or reconciled:
        logger.warning("transient sweep: %s", out)
    return out


def _file_age_s(path: Path, stamp: datetime) -> float | None:
    """Age of a file by its own mtime, in seconds. None if it is gone."""
    try:
        mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    except OSError:
        return None
    return (stamp - mtime).total_seconds()


def audit(session: Session, *, now: datetime | None = None,
          ttl: timedelta = TTL) -> dict:
    """Is the six-hour guarantee currently true? Answers, does not assume.

    This is the check M2's acceptance criterion names — "zero competitor
    files older than six hours on disk at any audited moment".

    **It asks the disk, not only the ledger.** An earlier version of this
    function decided the guarantee purely from `expires_at`, which means
    it would have passed a directory full of six-hour-old bytes as long
    as every row said they were still fresh. The ledger is exactly the
    thing that is wrong when something has gone wrong, so the file's own
    mtime is now the primary evidence and the row is cross-checked
    against it:

    * `stale_files` counts files whose **mtime** is older than the TTL,
      whatever any row claims. This is the criterion as written, and it
      is the one that fails the guarantee.
    * `expiry_inconsistencies` counts live rows whose `expires_at` is
      not `acquired_at + ttl`, within a second of slack. A row with a
      deadline nobody set from the TTL is a row the reaper will honour
      and a human would not expect — the most likely way a file
      legitimately outlives six hours without anything looking broken.
    * `rows_disagreeing_with_disk` counts live rows whose file is
      measurably older than the row's own `acquired_at` implies. That is
      a path reused by a file the ledger did not see arrive.
    * `rows_with_missing_file` counts live rows whose file is already
      gone. Normal for a moment between a deletion and the next sweep,
      so it does not fail the guarantee — but it is reported, because a
      number that never falls back to zero means the sweep is not
      running.
    * `rows_with_delete_errors` counts rows where `release()` caught an
      OSError and recorded it instead of raising. Those files are still
      on disk and still ageing, and nothing else surfaces them.

    **Any unregistered file still fails the guarantee**, as it did
    before. A file no live row claims has no deadline governing it at
    all, which is the "research cache becomes a creator database" case
    this whole module exists to prevent, and it should be loud even when
    the file is seconds old. `unregistered_beyond_grace` is reported
    alongside as the subset `sweep()` would delete right now — useful
    for telling a mid-acquisition race apart from a real orphan, but it
    does not soften the verdict.

    **Ages are reported from both ends.** The previous version reported
    `min()` of the file ages under the name `newest_file_age_s`, which
    is accurate and useless: the guarantee is about the OLDEST file, and
    that number was never computed. Both are reported now.
    """
    stamp = now or datetime.now(timezone.utc)
    rows = live_rows(session)
    root = quarantine_root()
    on_disk = [p for p in root.glob("*") if p.is_file()]

    ages = {p: _file_age_s(p, stamp) for p in on_disk}
    present = [a for a in ages.values() if a is not None]
    ttl_s = ttl.total_seconds()
    grace_s = ORPHAN_GRACE.total_seconds()

    stale = [p for p, a in ages.items() if a is not None and a > ttl_s]

    known = {Path(r.path).resolve(): r for r in rows}
    unregistered = [p for p in on_disk if p.resolve() not in known]
    unregistered_old = [p for p in unregistered
                        if (ages.get(p) or 0.0) >= grace_s]

    overdue = [r for r in rows if r.expires_at <= stamp]
    missing_file = [r for r in rows if not Path(r.path).exists()]
    delete_errors = [r for r in rows if (r.audit or {}).get("delete_error")]

    expiry_bad, disagreeing = [], []
    for r in rows:
        want = r.acquired_at + ttl
        if abs((r.expires_at - want).total_seconds()) > 1.0:
            expiry_bad.append(r)
        age = _file_age_s(Path(r.path), stamp)
        if age is None:
            continue
        # The file claims to predate the acquisition that registered it,
        # by more than the orphan grace. Either the path was reused or
        # the row is describing different bytes than the ones present.
        row_age = (stamp - r.acquired_at).total_seconds()
        if age - row_age > grace_s:
            disagreeing.append(r)

    violations = []
    if stale:
        violations.append(f"{len(stale)} file(s) on disk older than the "
                          f"{ttl_s / 3600:.0f}h TTL by mtime")
    if unregistered:
        violations.append(
            f"{len(unregistered)} unregistered file(s) in quarantine"
            + (f", {len(unregistered_old)} past the orphan grace"
               if unregistered_old else ""))
    if overdue:
        violations.append(f"{len(overdue)} live row(s) past expires_at")

    inconsistencies = []
    if expiry_bad:
        inconsistencies.append(f"{len(expiry_bad)} row(s) whose expires_at "
                               f"is not acquired_at + TTL")
    if disagreeing:
        inconsistencies.append(f"{len(disagreeing)} row(s) whose file is "
                               f"older than the row")
    if delete_errors:
        inconsistencies.append(f"{len(delete_errors)} row(s) with a "
                               f"recorded delete failure")
    if missing_file:
        inconsistencies.append(f"{len(missing_file)} live row(s) with no "
                               f"file on disk")

    return {
        "live_rows": len(rows),
        "files_on_disk": len(on_disk),
        "overdue_rows": len(overdue),
        "unregistered_files": len(unregistered),
        "unregistered_beyond_grace": len(unregistered_old),
        "stale_files": len(stale),
        "rows_with_missing_file": len(missing_file),
        "rows_with_delete_errors": len(delete_errors),
        "expiry_inconsistencies": len(expiry_bad),
        "rows_disagreeing_with_disk": len(disagreeing),
        "oldest_file_age_s": (round(max(present), 1) if present else None),
        "newest_file_age_s": (round(min(present), 1) if present else None),
        "ttl_hours": ttl_s / 3600.0,
        # The retention guarantee, decided from the disk.
        "guarantee_holds": not violations,
        "violations": violations,
        # Bookkeeping. A failure here is not a retention breach, but it
        # means the ledger and the directory have drifted and the next
        # sweep has work to do.
        "ledger_consistent": not inconsistencies,
        "inconsistencies": inconsistencies,
        "quarantine_root": str(root),
    }
