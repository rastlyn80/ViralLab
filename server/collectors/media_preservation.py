"""Preserve Instagram media before its URLs expire.

Promoted from a hand-run provision script into a deployed module so the
scheduler can run it: a Reel collected today is useless to match next week,
because `media_url` is a signed CDN link with a short life and the window
does not reopen. Preservation has to happen on the same schedule as
collection, not whenever someone remembers.

Hard rules, each from something that went wrong:

  * **Fresh URLs only.** A URL stored on an earlier pass is assumed dead and
    never retried.
  * **Validation requires a video stream, not merely a decodable file.**
    ffprobe succeeds on a JPEG (`format_name=jpeg_pipe`), so three image
    posts were saved as `.mp4` and marked `available` - which is why the
    corpus had 261 files but 258 fingerprints. A file now counts as media
    only if it has a video stream AND a positive duration.
  * **Reasons are classified, not lumped together.** `api_restricted`,
    `not_a_video`, `download_failed` and `validation_failed` are different
    situations; only the retryable ones are retried, and `not_a_video` is
    not a coverage gap at all.
  * **Bounded attempts.** `MAX_ATTEMPTS` per publication, tracked in the
    database, so a permanently broken item cannot be retried forever.
  * **A media failure never breaks metric collection.** Metrics are the
    irreplaceable record; media is best-effort. The caller runs this inside
    its own try/except and the scheduler treats a failure here as a logged
    warning, never a collection error.
  * **Committed per item, not per pass.** The first version committed once at
    the end, so a crash mid-pass lost every status update and attempt
    increment while leaving the downloaded files on disk: the next run saw
    unattempted rows with orphaned files, and a repeatedly-crashing run could
    re-download the same media indefinitely without ever exhausting its
    attempts. Each item is now its own transaction.
  * **Orphans and part-files are swept.** A pass that dies between writing a
    file and committing its row leaves a file nothing points at, and a pass
    killed mid-download leaves a `.part`. Both are cleaned up on the next
    run rather than accumulating silently.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import subprocess
import urllib.error
import urllib.request
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from server.db.models import Video

logger = logging.getLogger(__name__)

#: Overridable so the Windows side can point at its own build; on the server
#: ffprobe is on PATH at /usr/bin/ffprobe.
FFPROBE = os.environ.get("FFPROBE", "ffprobe")

#: Give up on one publication after this many passes.
MAX_ATTEMPTS = 3
#: Refuse to grow the store beyond this. 736 MB for 258 clips measured, so
#: this leaves room for several years of the owner's posting rate.
STORAGE_CAP_BYTES = 20 * 1024 ** 3
#: Per-run ceiling, so one pass cannot monopolise the night.
DEFAULT_MAX_PER_RUN = 40
#: Don't re-attempt a failure immediately; the next daily pass is soon enough.
RETRY_AFTER = timedelta(hours=20)

VIDEO_TYPES = {"VIDEO"}
NON_VIDEO_TYPES = {"IMAGE", "CAROUSEL_ALBUM"}


@dataclass
class PreservationResult:
    considered: int = 0
    saved: int = 0
    deduplicated: int = 0
    unsupported: int = 0
    api_restricted: int = 0
    failed: int = 0
    skipped_attempts: int = 0
    stopped_reason: str | None = None
    bytes_added: int = 0
    orphans_adopted: int = 0
    orphans_removed: int = 0
    part_files_removed: int = 0
    reasons: dict[str, int] = field(default_factory=dict)

    def note(self, reason: str) -> None:
        self.reasons[reason] = self.reasons.get(reason, 0) + 1

    def as_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items()}


def probe(path: Path) -> tuple[bool, float | None]:
    """(is a real video, duration). ffprobe alone is not enough - it happily
    reports success for a JPEG - so a video stream is required explicitly."""
    try:
        r = subprocess.run(
            [FFPROBE, "-v", "error", "-show_entries",
             "stream=codec_type,duration:format=duration",
             "-of", "json", str(path)],
            capture_output=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired):
        return False, None
    if r.returncode != 0:
        return False, None
    try:
        info = json.loads(r.stdout or b"{}")
    except ValueError:
        return False, None
    streams = info.get("streams") or []
    if not any(s.get("codec_type") == "video" for s in streams):
        return False, None
    dur = (info.get("format") or {}).get("duration")
    for s in streams:
        if dur is None and s.get("codec_type") == "video":
            dur = s.get("duration")
    try:
        d = float(dur)
    except (TypeError, ValueError):
        return False, None
    # A still image can present as a one-frame "video" with no real duration.
    return (d > 0.2), (d if d > 0 else None)


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def store_size(media_root: Path) -> int:
    if not media_root.exists():
        return 0
    return sum(f.stat().st_size for f in media_root.glob("*") if f.is_file())


def download(url: str, dest: Path, *, timeout: int = 120) -> tuple[bool, str]:
    tmp = dest.with_suffix(dest.suffix + ".part")
    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": "ViralLab/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp, \
                tmp.open("wb") as fh:
            shutil.copyfileobj(resp, fh, length=1024 * 256)
    except urllib.error.HTTPError as exc:
        tmp.unlink(missing_ok=True)
        # 403/410 on a signed CDN link means the URL died, not that the media
        # is gone - the next pass fetches a fresh one.
        kind = "url_expired" if exc.code in (403, 410) else f"http_{exc.code}"
        return False, f"download_failed:{kind}"
    except Exception as exc:                                  # noqa: BLE001
        tmp.unlink(missing_ok=True)
        return False, f"download_failed:{type(exc).__name__}"
    if tmp.stat().st_size == 0:
        tmp.unlink(missing_ok=True)
        return False, "download_failed:empty"
    tmp.replace(dest)
    return True, "ok"


def sweep(session: Session, media_root: Path,
          res: PreservationResult) -> None:
    """Reconcile the filesystem against the database.

    Three states have to be resolved before a pass starts, all of them
    produced by a process dying at the wrong moment:

      * a `.part` file - the download was interrupted. Always junk: the next
        attempt fetches a fresh URL anyway, so a half file is never resumed.
      * a complete file whose row says the media is absent - the pass died
        between `replace()` and `commit()`. The file is almost certainly
        good, so it is **adopted** (validated, hashed, recorded) rather than
        deleted and re-downloaded.
      * a complete file no row refers to at all - a publication that has
        since been deleted. Removed.
    """
    if not media_root.exists():
        return

    for part in media_root.glob("*.part"):
        try:
            part.unlink()
            res.part_files_removed += 1
        except OSError:
            logger.warning("could not remove stale part file %s", part)

    by_ext = {v.external_id: v for v in session.scalars(
        select(Video).where(Video.platform == "instagram")).all()}

    for f in media_root.glob("ig_*.mp4"):
        ext = f.stem[3:]
        v = by_ext.get(ext)
        if v is None:
            try:
                f.unlink()
                res.orphans_removed += 1
                logger.info("removed orphaned media with no publication: %s",
                            f.name)
            except OSError:
                pass
            continue
        if v.media_status == "available" and v.media_path:
            continue
        # A finished file whose row never got committed.
        is_video, dur = probe(f)
        if not is_video:
            f.unlink(missing_ok=True)
            continue
        v.media_status = "available"
        v.media_path = str(f)
        v.media_reason = None
        v.media_sha256 = sha256_of(f)
        if v.duration_s is None and dur:
            v.duration_s = dur
        res.orphans_adopted += 1
        logger.info("adopted orphaned media for %s (a previous pass died "
                    "before committing it)", ext)
    if res.orphans_adopted or res.orphans_removed or res.part_files_removed:
        session.commit()


def backfill_hashes(session: Session, limit: int = 500) -> int:
    """Record digests for files preserved before the column existed."""
    rows = session.scalars(select(Video).where(
        Video.platform == "instagram",
        Video.media_status == "available",
        Video.media_sha256.is_(None)).limit(limit)).all()
    done = 0
    for v in rows:
        p = Path(v.media_path or "")
        if not p.exists():
            continue
        try:
            v.media_sha256 = sha256_of(p)
            done += 1
        except OSError:
            continue
    if done:
        session.commit()
    return done


def _retryable(v: Video, now: datetime) -> bool:
    if v.media_status in ("available", "unsupported", "purged"):
        return False
    if (v.media_attempts or 0) >= MAX_ATTEMPTS:
        return False
    if v.media_checked_at and now - v.media_checked_at < RETRY_AFTER:
        return False
    return True


def preserve_instagram_media(
        session: Session, adapter, account_ref: str, *,
        media_root: Path,
        max_items: int = DEFAULT_MAX_PER_RUN,
        storage_cap: int = STORAGE_CAP_BYTES,
        items: Iterable | None = None) -> PreservationResult:
    """One preservation pass. Resumable, bounded, and safe to interrupt.

    `items` lets the caller hand over publications it has already paginated.
    The scheduler does: `collect_instagram_once` has just walked the whole
    media edge, so re-walking it here would double the API calls for one
    daily pass.
    """
    res = PreservationResult()
    media_root.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)

    # Resolve anything a previous crash left behind BEFORE deciding what is
    # outstanding, so an adopted file is not downloaded again.
    sweep(session, media_root, res)
    backfill_hashes(session)

    rows = {v.external_id: v for v in session.scalars(
        select(Video).where(Video.platform == "instagram")).all()}
    wanted = {e for e, v in rows.items() if _retryable(v, now)}
    res.skipped_attempts = sum(
        1 for v in rows.values()
        if v.media_status not in ("available", "unsupported", "purged")
        and (v.media_attempts or 0) >= MAX_ATTEMPTS)
    if not wanted:
        res.stopped_reason = "nothing outstanding"
        return res

    # Dedup index straight from the database - no disk reads.
    known: dict[str, str] = {
        v.media_sha256: v.external_id for v in rows.values()
        if v.media_sha256 and v.media_status == "available"
        and not (v.media_reason or "").startswith("deduplicated")}

    used = store_size(media_root)
    source = items if items is not None else \
        adapter.fetch_account_videos(account_ref)

    for item in source:
        ext = str(getattr(item, "external_id", "") or "")
        if ext not in wanted:
            continue
        v = rows[ext]
        res.considered += 1

        raw = getattr(item, "raw", None) or {}
        mtype = (raw.get("media_type") or "").upper()
        url = raw.get("media_url") or getattr(item, "media_url", None)

        # Our own storage cap is not the publication's fault: it must not
        # consume an attempt, and it must not push the item into the 20-hour
        # retry cooldown either.
        if used >= storage_cap:
            res.stopped_reason = "storage cap reached"
            res.considered -= 1
            break

        v.media_checked_at = now
        v.media_attempts = (v.media_attempts or 0) + 1

        if mtype in NON_VIDEO_TYPES or (mtype and mtype not in VIDEO_TYPES):
            # Not a failure and not a gap: there is no video here to keep.
            v.media_status = "unsupported"
            v.media_reason = f"not_a_video:{mtype or 'unknown'}"
            res.unsupported += 1
            res.note(v.media_reason)
            _commit(session)
            continue
        if not url:
            # The API returned the publication but withheld its media URL.
            v.media_status = "absent"
            v.media_reason = "api_restricted:no_media_url"
            res.api_restricted += 1
            res.note(v.media_reason)
            _commit(session)
            continue

        dest = media_root / f"ig_{ext}.mp4"
        ok, reason = download(url, dest)
        if not ok:
            v.media_status = "absent"
            v.media_reason = reason
            res.failed += 1
            res.note(reason)
            _commit(session)
            continue

        is_video, dur = probe(dest)
        if not is_video:
            dest.unlink(missing_ok=True)
            v.media_status = "unsupported"
            v.media_reason = "not_a_video:no_video_stream"
            res.unsupported += 1
            res.note(v.media_reason)
            _commit(session)
            continue

        digest = sha256_of(dest)
        owner = known.get(digest)
        if owner and owner != ext:
            dest.unlink(missing_ok=True)
            v.media_status = "available"
            v.media_path = rows[owner].media_path
            v.media_sha256 = digest
            v.media_reason = f"deduplicated:{owner}"
            res.deduplicated += 1
            res.note("deduplicated")
            _commit(session)
            continue

        size = dest.stat().st_size
        used += size
        res.bytes_added += size
        known[digest] = ext
        v.media_status = "available"
        v.media_path = str(dest)
        v.media_sha256 = digest
        v.media_reason = None
        if v.duration_s is None and dur:
            v.duration_s = dur
        res.saved += 1
        # Committed before the next download starts, so a crash costs at
        # most the item in flight.
        _commit(session)

        if res.saved >= max_items:
            res.stopped_reason = f"per-run limit of {max_items} reached"
            break

    _commit(session)
    return res


def _commit(session: Session) -> None:
    """Commit, or roll back and carry on.

    A database problem on one item must not abort the pass and must not
    leave the session unusable for the next one.
    """
    try:
        session.commit()
    except Exception:                                        # noqa: BLE001
        logger.exception("could not commit media state; rolling back this "
                         "item and continuing")
        session.rollback()


def coverage(session: Session, platform: str = "instagram") -> dict:
    """Media coverage, with unsupported types excluded from the denominator.

    Counting an image post as a missing video overstates the gap and makes
    the number impossible to act on.
    """
    rows = session.scalars(select(Video).where(
        Video.platform == platform)).all()
    by_status: dict[str, int] = {}
    by_reason: dict[str, int] = {}
    for v in rows:
        by_status[v.media_status] = by_status.get(v.media_status, 0) + 1
        if v.media_reason:
            cls = v.media_reason.split(":")[0]
            by_reason[cls] = by_reason.get(cls, 0) + 1
    preservable = sum(n for s, n in by_status.items() if s != "unsupported")
    have = by_status.get("available", 0)
    return {
        "total": len(rows),
        "by_status": by_status,
        "by_reason_class": by_reason,
        "preservable": preservable,
        "preserved": have,
        "coverage_pct": round(have / preservable * 100, 1) if preservable
        else None,
        "exhausted_attempts": sum(
            1 for v in rows
            if v.media_status not in ("available", "unsupported", "purged")
            and (v.media_attempts or 0) >= MAX_ATTEMPTS),
    }
