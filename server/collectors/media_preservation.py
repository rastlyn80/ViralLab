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
        storage_cap: int = STORAGE_CAP_BYTES) -> PreservationResult:
    """One preservation pass. Resumable, bounded, and safe to interrupt."""
    res = PreservationResult()
    media_root.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)

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

    # Content hashes of what is already stored, so the same media fetched
    # under two publication ids is kept once.
    known: dict[str, str] = {}
    for v in rows.values():
        if v.media_status == "available" and v.media_path:
            p = Path(v.media_path)
            if p.exists():
                try:
                    known[sha256_of(p)] = v.external_id
                except OSError:
                    pass

    used = store_size(media_root)

    for item in adapter.fetch_account_videos(account_ref):
        ext = str(getattr(item, "external_id", "") or "")
        if ext not in wanted:
            continue
        v = rows[ext]
        res.considered += 1
        v.media_checked_at = now
        v.media_attempts = (v.media_attempts or 0) + 1

        raw = getattr(item, "raw", None) or {}
        mtype = (raw.get("media_type") or "").upper()
        url = raw.get("media_url")

        if mtype in NON_VIDEO_TYPES or (mtype and mtype not in VIDEO_TYPES):
            # Not a failure and not a gap: there is no video here to keep.
            v.media_status = "unsupported"
            v.media_reason = f"not_a_video:{mtype or 'unknown'}"
            res.unsupported += 1
            res.note(v.media_reason)
            continue
        if not url:
            # The API returned the publication but withheld its media URL.
            v.media_status = "absent"
            v.media_reason = "api_restricted:no_media_url"
            res.api_restricted += 1
            res.note(v.media_reason)
            continue

        if used >= storage_cap:
            res.stopped_reason = "storage cap reached"
            v.media_attempts -= 1          # not the publication's fault
            break

        dest = media_root / f"ig_{ext}.mp4"
        ok, reason = download(url, dest)
        if not ok:
            v.media_status = "absent"
            v.media_reason = reason
            res.failed += 1
            res.note(reason)
            continue

        is_video, dur = probe(dest)
        if not is_video:
            dest.unlink(missing_ok=True)
            v.media_status = "unsupported"
            v.media_reason = "not_a_video:no_video_stream"
            res.unsupported += 1
            res.note(v.media_reason)
            continue

        digest = sha256_of(dest)
        if digest in known and known[digest] != ext:
            dest.unlink(missing_ok=True)
            v.media_status = "available"
            v.media_path = str(media_root / f"ig_{known[digest]}.mp4")
            v.media_reason = f"deduplicated:{known[digest]}"
            res.deduplicated += 1
            res.note("deduplicated")
            continue

        size = dest.stat().st_size
        used += size
        res.bytes_added += size
        known[digest] = ext
        v.media_status = "available"
        v.media_path = str(dest)
        v.media_reason = None
        if v.duration_s is None and dur:
            v.duration_s = dur
        res.saved += 1

        if res.saved >= max_items:
            res.stopped_reason = f"per-run limit of {max_items} reached"
            break

    session.commit()
    return res


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
