"""Archive the owner's own TikTok publications, which the API will not give.

TikTok's Display API returns `embed_link` and `share_url` and no downloadable
file (ADR 0001, re-verified for this module). So a publication that exists in
`videos` with `platform='tiktok'` has metadata, metrics and no media - which
means none of the hook, OCR, speech or vision pipelines can run on it. This
module closes that gap for **the owner's own profiles** using `yt-dlp`.

Why this is scoped the way it is
--------------------------------

The obvious generalisation - take a list of "owned" handles from a config
object and download whatever URLs arrive - is not implemented, deliberately.
A caller-supplied list of owned accounts is a self-declaration, not a
verification: whatever handle is in the list gets downloaded, so the
ownership check becomes a comment rather than a control. Combined with
arbitrary URL input and session cookies, that is a general-purpose
authenticated scraper, which CLAUDE.md 2.3 and 7 rule out ("public
logged-out data only; no circumvention of technical access controls").

Ownership here is therefore resolved from evidence the platform itself
produced:

  `oauth`           an `oauth_tokens` row exists for (tiktok, account_ref).
                    TikTok authenticated this profile to us. This is the
                    strong basis and the one an agency with many profiles
                    scales through - each profile is connected once.
  `operator_marked` `creators.is_own_account` is set but no token exists.
                    Accepted, because the owner's own database is their own
                    assertion about their own property, but recorded as the
                    weaker basis and reported as such to the dashboard.

Multiple profiles are first-class: `owned_profiles()` returns every owned
account, and nothing in the download path is specific to one handle. What is
not configurable is the *membership* of that set.

Two paths, and what separates them
----------------------------------

**Public path (default).** Public TikTok videos resolve without a session,
so the owner's own *published* work needs no authentication. yt-dlp runs
with `--no-cookies`, no credentials of any kind, and that covers the normal
backfill.

**Private path (opt-in, off by default).** Private, unlisted or
region-restricted content needs a logged-in session, and a TikTok OAuth
token cannot provide one: OAuth tokens authenticate the Display API, not
yt-dlp's player extraction. The only thing that works is a browser session
cookie jar - which is a full-account credential, unrelated to and
unverifiable from the OAuth token.

So "OAuth-verified owner" on its own does not gate the cookie path: holding
a token for @alice does not prove the supplied cookie jar belongs to @alice.
The gate that *is* binding is a different one:

    The OAuth API is the authority on whether a video is yours.
    The cookie is only transport.

`authorise_private_sync` calls TikTok's own `video/list` endpoint with the
profile's OAuth token and requires the target video id to appear in the
owner's own video list. TikTok itself therefore confirms the video belongs
to the authenticated owner before any cookie is used. A video absent from
that list is refused however many credentials are offered.

Cookies are supplied per sync, written to a 0600 temporary file, passed to
yt-dlp by path, and deleted immediately afterwards. They are never stored in
the database, never written to a log, and never returned by an endpoint.

Terms posture
-------------

`[PRIMARY]` TikTok's Terms of Service prohibit programmatic collection.
Downloading one's own published videos is a different posture from
harvesting third-party media - it is the operator's own copyrighted work,
recovered into their own archive - but it is a terms question, not a
settled-law question, and it is recorded in `docs/adr/` rather than buried
here. This module will not fetch media for a creator who is not an owned
profile, whatever is asked of it.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.orm import Session

from server.collectors.media_preservation import (MAX_ATTEMPTS,
                                                  STORAGE_CAP_BYTES, probe,
                                                  sha256_of, store_size)
from server.db.models import Creator, OAuthToken, Video

logger = logging.getLogger("virallab.collectors.tiktok_archive")

PLATFORM = "tiktok"

#: Resolved from PATH unless overridden. Kept as an external binary rather
#: than the Python package so it can be updated independently - TikTok
#: changes its player often and yt-dlp tracks it in days.
YTDLP = os.environ.get("YTDLP", "yt-dlp")

#: Polite spacing between fetches. CLAUDE.md 7 requires aggressive rate
#: limiting for anything not coming through an official API, and this is a
#: personal archive backfill with no deadline.
MIN_SECONDS_BETWEEN = 6.0

#: Per-run ceiling so a backfill cannot run all night unattended.
DEFAULT_MAX_PER_RUN = 25

#: Don't retry a failure immediately.
RETRY_AFTER = timedelta(hours=20)

DOWNLOAD_TIMEOUT_S = 180

#: Hosts whose video URLs this module will parse. Anything else is rejected
#: before a subprocess is started, so a mistyped or hostile URL cannot be
#: handed to the downloader at all.
ALLOWED_HOSTS = {
    "tiktok.com", "www.tiktok.com", "m.tiktok.com",
    "vm.tiktok.com", "vt.tiktok.com",
}

#: /@handle/video/1234567890
_CANONICAL = re.compile(
    r"^/@(?P<handle>[A-Za-z0-9._]{1,24})/video/(?P<video_id>\d{6,32})/?$")
#: Short links. Resolved by following the redirect, with no credentials.
_SHORT = re.compile(r"^/(?P<slug>[A-Za-z0-9]{5,24})/?$")


class SyncStatus:
    """Dashboard-facing states. Strings, so they survive JSON and storage."""

    OK = "ok"
    ALREADY_HAVE = "already_have"
    NOT_OWNED = "not_owned"
    PRIVATE = "private"
    UNAVAILABLE = "unavailable"
    GEO_BLOCKED = "geo_blocked"
    RATE_LIMITED = "rate_limited"
    TOOL_MISSING = "tool_missing"
    STORAGE_CAP = "storage_cap"
    BAD_URL = "bad_url"
    TIMEOUT = "timeout"
    FAILED = "failed"

    #: Which states are worth trying again later, and which are terminal.
    TRANSIENT = {RATE_LIMITED, TIMEOUT, FAILED}
    TERMINAL = {NOT_OWNED, PRIVATE, UNAVAILABLE, GEO_BLOCKED, BAD_URL}


@dataclass
class OwnedProfile:
    """One profile the operator has established as theirs."""

    account_ref: str
    creator_id: int | None = None
    handle: str = ""
    #: oauth | operator_marked
    ownership_basis: str = "operator_marked"
    token_needs_reauth: bool = False

    @property
    def strong(self) -> bool:
        return self.ownership_basis == "oauth"


@dataclass
class UrlTarget:
    """A validated, normalised TikTok video URL."""

    url: str
    handle: str
    video_id: str
    was_short_link: bool = False


@dataclass
class FetchOutcome:
    video_id: int | None = None
    external_id: str | None = None
    status: str = SyncStatus.FAILED
    detail: str = ""
    path: str | None = None
    bytes_written: int = 0
    duration_s: float | None = None
    sha256: str | None = None

    @property
    def ok(self) -> bool:
        return self.status == SyncStatus.OK


@dataclass
class ArchiveResult:
    considered: int = 0
    saved: int = 0
    skipped: int = 0
    failed: int = 0
    bytes_added: int = 0
    stopped_reason: str | None = None
    by_status: dict[str, int] = field(default_factory=dict)
    outcomes: list[FetchOutcome] = field(default_factory=list)

    def note(self, outcome: FetchOutcome) -> None:
        self.by_status[outcome.status] = self.by_status.get(outcome.status, 0) + 1
        self.outcomes.append(outcome)

    def as_dict(self) -> dict:
        return {"considered": self.considered, "saved": self.saved,
                "skipped": self.skipped, "failed": self.failed,
                "bytes_added": self.bytes_added,
                "stopped_reason": self.stopped_reason,
                "by_status": dict(self.by_status)}


# ---------------------------------------------------------------------------
# ownership
# ---------------------------------------------------------------------------

def owned_profiles(session: Session,
                   platform: str = PLATFORM) -> list[OwnedProfile]:
    """Every profile the operator has established as theirs, with the basis.

    Derived from the database, never from an argument. A caller cannot widen
    this set by passing a longer list, which is the entire point.
    """
    tokens = {
        t.account_ref.lstrip("@").lower(): t
        for t in session.scalars(select(OAuthToken).where(
            OAuthToken.platform == platform)).all()
    }
    out: list[OwnedProfile] = []
    seen: set[str] = set()

    for creator in session.scalars(select(Creator).where(
            Creator.platform == platform,
            Creator.is_own_account.is_(True))).all():
        handle = (creator.handle or "").lstrip("@").lower()
        if not handle or handle in seen:
            continue
        seen.add(handle)
        tok = tokens.get(handle)
        out.append(OwnedProfile(
            account_ref=creator.handle or handle, creator_id=creator.id,
            handle=handle,
            ownership_basis="oauth" if tok else "operator_marked",
            token_needs_reauth=bool(tok.needs_reauth) if tok else False))

    # A connected profile with no creator row yet is still owned; surface it
    # so the dashboard can show it rather than silently ignoring it.
    for handle, tok in tokens.items():
        if handle in seen:
            continue
        out.append(OwnedProfile(
            account_ref=tok.account_ref, handle=handle,
            ownership_basis="oauth",
            token_needs_reauth=bool(tok.needs_reauth)))
    return out


def ownership_of(handle: str,
                 profiles: list[OwnedProfile]) -> OwnedProfile | None:
    """The owned profile matching a handle, or None. Case-insensitive."""
    want = (handle or "").lstrip("@").lower()
    for p in profiles:
        if p.handle == want:
            return p
    return None


# ---------------------------------------------------------------------------
# URL handling
# ---------------------------------------------------------------------------

def normalise_url(raw: str, *, resolve_short: bool = True
                  ) -> tuple[UrlTarget | None, str]:
    """Validate and canonicalise one TikTok video URL.

    Returns `(target, reason)`. A rejected URL never reaches a subprocess.
    Short links are resolved by following redirects with no credentials
    attached, because `vm.tiktok.com/XXXX` carries no handle and the handle
    is what the ownership check needs.
    """
    raw = (raw or "").strip()
    if not raw:
        return None, "empty URL"
    if "://" not in raw:
        raw = "https://" + raw
    try:
        parsed = urlparse(raw)
    except ValueError as exc:
        return None, f"unparseable URL: {exc}"
    if parsed.scheme not in ("http", "https"):
        return None, f"refusing scheme {parsed.scheme!r}"
    host = (parsed.hostname or "").lower()
    if host not in ALLOWED_HOSTS:
        return None, (f"{host!r} is not a TikTok host this module will "
                      f"parse")

    m = _CANONICAL.match(parsed.path)
    if m:
        return UrlTarget(
            url=(f"https://www.tiktok.com/@{m.group('handle')}"
                 f"/video/{m.group('video_id')}"),
            handle=m.group("handle").lower(),
            video_id=m.group("video_id")), ""

    short = _SHORT.match(parsed.path)
    if short and host in ("vm.tiktok.com", "vt.tiktok.com"):
        if not resolve_short:
            return None, "short link not resolved (resolve_short=False)"
        resolved, why = _resolve_short_link(
            f"https://{host}/{short.group('slug')}")
        if resolved is None:
            return None, why
        target, reason = normalise_url(resolved, resolve_short=False)
        if target is not None:
            target.was_short_link = True
        return target, reason

    return None, (f"path {parsed.path!r} is not a TikTok video URL "
                  f"(expected /@handle/video/<id>)")


def _resolve_short_link(url: str) -> tuple[str | None, str]:
    """Follow a short link to its canonical form, without credentials."""
    import urllib.error
    import urllib.request

    req = urllib.request.Request(
        url, method="HEAD",
        # A plain identifying agent. No cookies, no session, no tokens.
        headers={"User-Agent": "ViralLab/1.0 (+own-account archive)"})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            final = resp.geturl()
        return (final, "") if final and final != url else (
            None, "short link did not redirect")
    except urllib.error.HTTPError as exc:
        return None, f"short link returned HTTP {exc.code}"
    except Exception as exc:                       # noqa: BLE001
        return None, f"short link could not be resolved: {exc}"


def oauth_video_index(adapter, account_ref: str) -> dict[str, str]:
    """`external_id -> canonical share_url`, straight from TikTok.

    MEASURED, 2026-10-26: `video/list` returns `share_url` in the form
    `https://www.tiktok.com/@username/video/<id>?utm_campaign=...`. That one
    response therefore answers both questions this module needs:

      * is this video the authenticated owner's?  It is in their list.
      * what is its URL?                          TikTok just said.

    This replaces reconstructing a URL from `creators.handle`, which cannot
    work: `handle` holds the OAuth `open_id`
    (`-000lRCwi-AFr6BTkIsmkCkFPfgeI8Izqw5p` for this account), not the
    username. `user.info.basic` does not return a username - that needs the
    `user.info.profile` scope, which this app has not requested - so the
    share_url is the only authoritative source available, and it is a better
    one than a reconstruction would have been.
    """
    index: dict[str, str] = {}
    for raw in adapter._iter_raw(account_ref):      # noqa: SLF001
        vid, share = raw.get("id"), raw.get("share_url")
        if vid is not None and share:
            index[str(vid)] = str(share)
    return index


def build_adapter(session: Session, env: dict | None = None):
    """The official TikTok adapter, from stored credentials."""
    from server.collectors.tiktok_official import (TikTokAuth,
                                                   TikTokOfficialAdapter)
    from server.collectors.tokens import TokenStore

    if env is None:
        from server.scheduler.collect import _env
        env = _env()
    key = env.get("TIKTOK_CLIENT_KEY")
    secret = env.get("TIKTOK_CLIENT_SECRET")
    if not key or not secret:
        raise RuntimeError("TIKTOK_CLIENT_KEY/SECRET are not configured")
    return TikTokOfficialAdapter(TokenStore(session),
                                 TikTokAuth(key, secret))


# ---------------------------------------------------------------------------
# the download itself
# ---------------------------------------------------------------------------

def tool_available() -> tuple[bool, str]:
    path = shutil.which(YTDLP)
    if not path:
        return False, (f"{YTDLP!r} is not on PATH. Install it with "
                       f"`pip install yt-dlp` or the platform package; this "
                       f"module shells out to the binary so it can be "
                       f"updated without touching ViralLab.")
    try:
        out = subprocess.run([path, "--version"], capture_output=True,
                             timeout=30, text=True)
        return True, out.stdout.strip() or "unknown version"
    except Exception as exc:                       # noqa: BLE001
        return False, f"{YTDLP} present but not runnable: {exc}"


def _classify(stderr: str, returncode: int) -> tuple[str, str]:
    """Map yt-dlp's output to a dashboard status.

    Matched on substrings rather than exit codes because yt-dlp returns 1
    for nearly everything; the message is the only signal that distinguishes
    "this is private" from "we are being throttled", and the dashboard needs
    that difference to decide whether retrying is sensible.
    """
    s = (stderr or "").lower()
    pairs = [
        (("http error 429", "too many requests", "rate-limit",
          "rate limit"), SyncStatus.RATE_LIMITED),
        (("private video", "is private", "login required",
          "sign in to confirm", "requires authentication",
          "this post is private"), SyncStatus.PRIVATE),
        (("video not available", "not available in your country",
          "geo restricted", "geo-restricted",
          "unavailable in your region"), SyncStatus.GEO_BLOCKED),
        (("video unavailable", "has been removed", "item doesn't exist",
          "could not find", "http error 404", "content isn't available",
          "video not found", "deleted"), SyncStatus.UNAVAILABLE),
        (("unsupported url", "unable to extract",
          "no video formats found"), SyncStatus.FAILED),
    ]
    for needles, status in pairs:
        for needle in needles:
            if needle in s:
                return status, (stderr or "").strip()[:400]
    return (SyncStatus.FAILED,
            (stderr or f"yt-dlp exited {returncode}").strip()[:400])


def fetch_one(target: UrlTarget, dest: Path, *,
              timeout_s: int = DOWNLOAD_TIMEOUT_S,
              cookies: Path | None = None) -> FetchOutcome:
    """Download one validated URL to `dest`. Assumes ownership is settled.

    The caller is responsible for the ownership gate; this function does not
    repeat it, and must therefore never be exposed directly to a request
    handler. `archive_own_tiktok`, `fetch_owned_url` and
    `fetch_private_owned` are the entry points.

    `cookies` is a path to a Netscape cookie jar, used only by the private
    path. When it is None - every public fetch - yt-dlp is run with
    `--no-cookies` and has no credentials available to it at all.
    """
    out = FetchOutcome(external_id=target.video_id)
    available, detail = tool_available()
    if not available:
        out.status, out.detail = SyncStatus.TOOL_MISSING, detail
        return out

    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    cmd = [
        shutil.which(YTDLP) or YTDLP,
        "--no-playlist",
        "--no-warnings",
        "--no-progress",
        "--no-cache-dir",
        # One attempt here; retry policy belongs to the caller's run loop so
        # it is visible and bounded in one place.
        "--retries", "1",
        "--socket-timeout", "30",
        # MEASURED 2026-10-26: TikTok's default rendition is HEVC
        # (`bytevc1_*`), which Chrome will not play in MP4 - the analysis
        # pipelines are unaffected because they go through ffmpeg, but the
        # web UI preview would be a black box. Every video checked also
        # offers an H.264 rendition, which yt-dlp labels `h264` rather than
        # `avc1`; an earlier selector matched only `avc1` and so silently
        # kept taking the HEVC.
        #
        # The H.264 rendition can be a lower resolution (540p against 720p
        # HEVC on one sample). That is an acceptable trade: every analysis
        # path downscales anyway - ANALYSIS_WIDTH in hookfeat, MAX_WIDTH 640
        # in the frames API - whereas a file the owner cannot play in their
        # own dashboard is a real loss.
        "-f", ("b[vcodec^=h264]/b[vcodec^=avc1]/"
               "bv*[vcodec^=h264]+ba/bv*[vcodec^=avc1]+ba/"
               "best[ext=mp4]/best"),
        "--merge-output-format", "mp4",
        "-o", str(tmp),
    ]
    if cookies is not None:
        # Private path only, and only after TikTok confirmed the video is
        # the owner's (authorise_private_sync).
        cmd += ["--cookies", str(cookies)]
    else:
        # No --username, no --netrc, no browser cookie import. A public
        # fetch has no credentials available to it.
        cmd += ["--no-cookies"]
    cmd.append(target.url)
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              timeout=timeout_s)
    except subprocess.TimeoutExpired:
        tmp.unlink(missing_ok=True)
        out.status = SyncStatus.TIMEOUT
        out.detail = f"yt-dlp exceeded {timeout_s}s"
        return out
    except Exception as exc:                       # noqa: BLE001
        tmp.unlink(missing_ok=True)
        out.status, out.detail = SyncStatus.FAILED, str(exc)[:400]
        return out

    if proc.returncode != 0 or not tmp.exists():
        tmp.unlink(missing_ok=True)
        out.status, out.detail = _classify(proc.stderr, proc.returncode)
        return out

    playable, duration = probe(tmp)
    if not playable:
        tmp.unlink(missing_ok=True)
        out.status = SyncStatus.FAILED
        out.detail = ("the downloaded file would not probe as playable "
                      "video; discarded rather than stored")
        return out

    tmp.replace(dest)
    out.status = SyncStatus.OK
    out.path = str(dest)
    out.bytes_written = dest.stat().st_size
    out.duration_s = duration
    out.sha256 = sha256_of(dest)
    out.detail = "archived"
    return out


# ---------------------------------------------------------------------------
# entry points
# ---------------------------------------------------------------------------

def fetch_owned_url(session: Session, raw_url: str, media_root: Path, *,
                    adapter=None,
                    timeout_s: int = DOWNLOAD_TIMEOUT_S) -> FetchOutcome:
    """Archive one URL, if and only if it belongs to an owned profile.

    This is the function a request handler may call. The ownership gate is
    here, before anything is fetched, and it cannot be bypassed by argument.
    """
    target, why = normalise_url(raw_url)
    if target is None:
        return FetchOutcome(status=SyncStatus.BAD_URL, detail=why)

    # Ownership is settled by asking TikTok, not by comparing handles: the
    # handle in a URL is attacker-controlled text, while membership of the
    # authenticated owner's video list is the platform's own statement.
    profiles = owned_profiles(session)
    strong = [p for p in profiles if p.strong]
    if not strong:
        return FetchOutcome(
            external_id=target.video_id, status=SyncStatus.NOT_OWNED,
            detail=("no TikTok profile is connected through OAuth, so "
                    "ownership cannot be confirmed. Connect the profile "
                    "first."))
    if adapter is None:
        try:
            adapter = build_adapter(session)
        except Exception as exc:                    # noqa: BLE001
            return FetchOutcome(
                external_id=target.video_id, status=SyncStatus.FAILED,
                detail=f"TikTok adapter could not be built: {exc}")

    owner = None
    for profile in strong:
        try:
            index = oauth_video_index(adapter, profile.account_ref)
        except Exception as exc:                    # noqa: BLE001
            return FetchOutcome(
                external_id=target.video_id, status=SyncStatus.FAILED,
                detail=(f"TikTok's video list could not be read "
                        f"({type(exc).__name__}), so ownership could not be "
                        f"confirmed; refusing"))
        if target.video_id in index:
            owner = profile
            # Prefer the platform's own URL over the one supplied.
            confirmed, why = normalise_url(index[target.video_id],
                                           resolve_short=False)
            if confirmed is not None:
                target = confirmed
            break
    if owner is None:
        return FetchOutcome(
            external_id=target.video_id, status=SyncStatus.NOT_OWNED,
            detail=(f"TikTok does not list video {target.video_id} as "
                    f"belonging to any connected profile. This archives "
                    f"your own publications only."))

    if store_size(media_root) >= STORAGE_CAP_BYTES:
        return FetchOutcome(
            external_id=target.video_id, status=SyncStatus.STORAGE_CAP,
            detail=f"media store is at its {STORAGE_CAP_BYTES} byte cap")

    dest = media_root / f"tiktok_{target.video_id}.mp4"
    if dest.exists():
        return FetchOutcome(
            external_id=target.video_id, status=SyncStatus.ALREADY_HAVE,
            path=str(dest), detail="already archived",
            bytes_written=dest.stat().st_size)

    outcome = fetch_one(target, dest, timeout_s=timeout_s)
    if outcome.ok:
        _attach_to_video(session, target.video_id, outcome)
    logger.info("tiktok archive @%s/%s -> %s (%s)", target.handle,
                target.video_id, outcome.status, owner.ownership_basis)
    return outcome


def _attach_to_video(session: Session, external_id: str,
                     outcome: FetchOutcome) -> None:
    """Record the file against the publication, if we know about it."""
    video = session.scalars(select(Video).where(
        Video.platform == PLATFORM,
        Video.external_id == external_id)).first()
    if video is None:
        logger.info("archived %s but no videos row matches it yet; the file "
                    "is on disk and the next collection pass will adopt it",
                    external_id)
        return
    video.media_path = outcome.path
    video.media_status = "available"
    video.media_sha256 = outcome.sha256
    if outcome.duration_s and not video.duration_s:
        video.duration_s = outcome.duration_s
    outcome.video_id = video.id
    session.commit()


def archive_own_tiktok(session: Session, media_root: Path, *,
                       max_per_run: int = DEFAULT_MAX_PER_RUN,
                       handles: list[str] | None = None,
                       adapter=None,
                       dry_run: bool = False) -> ArchiveResult:
    """Backfill media for owned TikTok publications that have none.

    `handles` NARROWS the owned set; it can never widen it. Passing a handle
    that is not already an owned profile selects nothing rather than
    granting permission, which is the invariant that keeps this from being
    a general downloader.
    """
    res = ArchiveResult()
    profiles = owned_profiles(session)
    if handles:
        want = {h.lstrip("@").lower() for h in handles}
        profiles = [p for p in profiles if p.handle in want]
        missing = want - {p.handle for p in profiles}
        if missing:
            logger.warning("ignoring %d handle(s) that are not owned "
                           "profiles: %s", len(missing),
                           ", ".join(sorted(missing)))
    if not profiles:
        res.stopped_reason = (
            "no owned TikTok profiles. Connect one through TikTok OAuth, or "
            "set is_own_account on the creator if it is genuinely yours.")
        return res

    available, detail = tool_available()
    if not available:
        res.stopped_reason = detail
        res.note(FetchOutcome(status=SyncStatus.TOOL_MISSING, detail=detail))
        return res

    creator_ids = [p.creator_id for p in profiles if p.creator_id]
    if not creator_ids:
        res.stopped_reason = ("owned profiles exist but none has a creator "
                              "row, so there are no publications to match")
        return res

    # TikTok's own answer to "which videos are yours, and where do they
    # live". Both the ownership check and the URL come from here; nothing is
    # reconstructed and nothing is taken on the caller's word.
    index: dict[str, str] = {}
    if adapter is None:
        try:
            adapter = build_adapter(session)
        except Exception as exc:                    # noqa: BLE001
            res.stopped_reason = (f"the TikTok adapter could not be built "
                                  f"({exc}), so ownership cannot be "
                                  f"confirmed; refusing to fetch anything")
            return res
    for profile in profiles:
        if not profile.strong:
            logger.info("skipping @%s: marked own but no OAuth token, so "
                        "TikTok cannot confirm its publications",
                        profile.handle)
            continue
        try:
            index.update(oauth_video_index(adapter, profile.account_ref))
        except Exception as exc:                    # noqa: BLE001
            res.stopped_reason = (f"TikTok's video list could not be read "
                                  f"for {profile.handle} "
                                  f"({type(exc).__name__}: {exc})")
            return res
    if not index:
        res.stopped_reason = ("TikTok returned no publications for the "
                              "connected profile(s), so there is nothing "
                              "confirmed to archive")
        return res
    logger.info("TikTok confirms %d publication(s) for %d profile(s)",
                len(index), len(profiles))

    candidates = session.scalars(
        select(Video)
        .where(Video.platform == PLATFORM,
               Video.creator_id.in_(creator_ids),
               Video.media_status.notin_(("available",)))
        .order_by(Video.posted_at.desc())).all()

    last_fetch = 0.0
    for video in candidates:
        if res.saved >= max_per_run:
            res.stopped_reason = f"per-run ceiling of {max_per_run} reached"
            break
        if store_size(media_root) >= STORAGE_CAP_BYTES:
            res.stopped_reason = "media store cap reached"
            break
        res.considered += 1

        attempts = getattr(video, "media_attempts", 0) or 0
        if attempts >= MAX_ATTEMPTS:
            res.skipped += 1
            res.note(FetchOutcome(
                video_id=video.id, external_id=video.external_id,
                status=SyncStatus.FAILED,
                detail=f"already failed {attempts} times; not retried"))
            continue

        share = index.get(str(video.external_id))
        if share is None:
            # Present locally, absent from TikTok's list of the owner's
            # videos. Deleted, made private, or never theirs. Either way the
            # platform has not confirmed it, so it is not fetched.
            res.skipped += 1
            res.note(FetchOutcome(
                video_id=video.id, external_id=video.external_id,
                status=SyncStatus.UNAVAILABLE,
                detail=("TikTok does not list this publication for the "
                        "connected profile; it may have been deleted or "
                        "made private. Not fetched.")))
            continue

        target, why = normalise_url(share, resolve_short=False)
        if target is None:
            res.skipped += 1
            res.note(FetchOutcome(video_id=video.id,
                                  external_id=video.external_id,
                                  status=SyncStatus.BAD_URL,
                                  detail=f"TikTok's own share_url did not "
                                         f"parse: {why}"))
            continue

        dest = media_root / f"tiktok_{target.video_id}.mp4"
        if dest.exists():
            res.skipped += 1
            outcome = FetchOutcome(
                video_id=video.id, external_id=video.external_id,
                status=SyncStatus.ALREADY_HAVE, path=str(dest),
                bytes_written=dest.stat().st_size)
            _attach_to_video(session, video.external_id, outcome)
            res.note(outcome)
            continue
        if dry_run:
            res.note(FetchOutcome(
                video_id=video.id, external_id=video.external_id,
                status="would_fetch", detail=target.url))
            continue

        wait = MIN_SECONDS_BETWEEN - (time.monotonic() - last_fetch)
        if wait > 0:
            time.sleep(wait)
        last_fetch = time.monotonic()

        outcome = fetch_one(target, dest)
        outcome.video_id = video.id
        outcome.external_id = video.external_id
        res.note(outcome)

        if outcome.ok:
            res.saved += 1
            res.bytes_added += outcome.bytes_written
            _attach_to_video(session, video.external_id, outcome)
        else:
            res.failed += 1
            if hasattr(video, "media_attempts"):
                video.media_attempts = attempts + 1
            if hasattr(video, "media_reason"):
                # media_reason is String(64); the full text lives in the run log.
                video.media_reason = outcome.status[:64]
            if outcome.status in SyncStatus.TERMINAL:
                video.media_status = "unavailable"
            session.commit()

        if outcome.status == SyncStatus.RATE_LIMITED:
            res.stopped_reason = ("TikTok rate-limited the archive pass; "
                                  "stopping rather than pushing through it")
            break

    logger.info("tiktok archive: %s", json.dumps(res.as_dict()))
    return res


def coverage(session: Session) -> dict:
    """How much owned TikTok media exists, for the dashboard."""
    profiles = owned_profiles(session)
    creator_ids = [p.creator_id for p in profiles if p.creator_id]
    if not creator_ids:
        return {"profiles": [p.__dict__ for p in profiles], "total": 0,
                "archived": 0, "missing": 0,
                "tool": dict(zip(("available", "detail"), tool_available()))}
    rows = session.scalars(select(Video).where(
        Video.platform == PLATFORM,
        Video.creator_id.in_(creator_ids))).all()
    archived = sum(1 for v in rows if v.media_status == "available")
    return {
        "profiles": [p.__dict__ for p in profiles],
        "total": len(rows),
        "archived": archived,
        "missing": len(rows) - archived,
        "tool": dict(zip(("available", "detail"), tool_available())),
    }

# ---------------------------------------------------------------------------
# the private path
# ---------------------------------------------------------------------------
#
# Off unless explicitly enabled. See this module's docstring for why the
# OAuth video list, not the OAuth token, is what makes this safe.

#: Must be "1" in the server environment for any cookie to be used at all.
#: A deployment that never sets it cannot run the authenticated path, which
#: is the desired default for a single-operator install.
PRIVATE_SYNC_ENV = "VIRALLAB_TIKTOK_PRIVATE_SYNC"

#: A cookie jar larger than this is not a cookie jar.
MAX_COOKIE_BYTES = 256 * 1024

#: Overwrite byte for the cookie jar, named so the source
#: carries no literal NUL.
NUL = bytes(1)


def private_sync_enabled() -> bool:
    return os.environ.get(PRIVATE_SYNC_ENV, "").strip() == "1"


@dataclass
class PrivateAuthorisation:
    allowed: bool = False
    reason: str = ""
    profile: OwnedProfile | None = None
    #: How many videos the OAuth list returned, for the dashboard to show.
    oauth_listed: int = 0
    confirmed_by_platform: bool = False


def oauth_owned_video_ids(adapter, account_ref: str) -> set[str]:
    """Video ids TikTok itself reports as belonging to this account.

    This is the ownership authority for the private path. It costs an API
    call against an already-authorised scope (`video.list`) and returns the
    owner's own publications - including, per TikTok's documentation for
    that endpoint, ones that are not publicly visible.
    """
    ids: set[str] = set()
    for raw in adapter._iter_raw(account_ref):      # noqa: SLF001
        vid = raw.get("id")
        if vid is not None:
            ids.add(str(vid))
    return ids


def authorise_private_sync(session: Session, target: UrlTarget, *,
                           adapter=None,
                           profiles: list[OwnedProfile] | None = None
                           ) -> PrivateAuthorisation:
    """Decide whether one private video may be fetched with credentials.

    Every condition must hold. None of them is supplied by the caller as
    data; they are read from the environment, the database and TikTok.
    """
    auth = PrivateAuthorisation()

    if not private_sync_enabled():
        auth.reason = (f"the authenticated path is disabled. Set "
                       f"{PRIVATE_SYNC_ENV}=1 in the server environment to "
                       f"enable it; it is off by default because it uses a "
                       f"full-account session credential.")
        return auth

    profiles = profiles if profiles is not None else owned_profiles(session)
    profile = ownership_of(target.handle, profiles)
    if profile is None:
        auth.reason = (f"@{target.handle} is not one of your profiles")
        return auth
    auth.profile = profile

    if not profile.strong:
        auth.reason = (
            f"@{target.handle} is marked as your own account but has no "
            f"TikTok OAuth token, so TikTok cannot confirm the video is "
            f"yours. Connect the profile before syncing private content.")
        return auth
    if profile.token_needs_reauth:
        auth.reason = (f"the OAuth token for @{target.handle} needs "
                       f"re-authorisation, so ownership cannot be confirmed")
        return auth

    if adapter is None:
        auth.reason = ("no TikTok adapter was supplied, so the ownership "
                       "list could not be fetched; refusing rather than "
                       "assuming")
        return auth

    try:
        owned_ids = oauth_owned_video_ids(adapter, profile.account_ref)
    except Exception as exc:                        # noqa: BLE001
        auth.reason = (f"TikTok's video list could not be read "
                       f"({type(exc).__name__}), so ownership could not be "
                       f"confirmed; refusing")
        return auth

    auth.oauth_listed = len(owned_ids)
    if target.video_id not in owned_ids:
        auth.reason = (
            f"video {target.video_id} is not in the {len(owned_ids)} "
            f"publication(s) TikTok reports for @{profile.handle}. Only "
            f"videos the platform confirms are yours can be fetched with "
            f"credentials.")
        return auth

    auth.allowed = True
    auth.confirmed_by_platform = True
    auth.reason = (f"TikTok confirms video {target.video_id} belongs to "
                   f"@{profile.handle}")
    return auth


def _write_cookie_jar(cookies_txt: str) -> Path:
    """Put a cookie jar on disk with restrictive permissions, briefly.

    Returned path must be deleted by the caller in a `finally`. The content
    is never logged and never stored anywhere else.
    """
    import tempfile

    fd, name = tempfile.mkstemp(prefix="vl-tt-", suffix=".txt")
    path = Path(name)
    try:
        os.write(fd, cookies_txt.encode("utf-8"))
    finally:
        os.close(fd)
    try:
        os.chmod(path, 0o600)
    except OSError:                                 # pragma: no cover
        logger.warning("could not restrict permissions on the cookie jar; "
                       "removing it rather than leaving it readable")
        path.unlink(missing_ok=True)
        raise
    return path


def _shred(path: Path) -> None:
    """Overwrite then remove. Best effort - the point is not to leave a live
    session credential sitting in the system temp directory."""
    try:
        if path.exists():
            size = path.stat().st_size
            with open(path, "r+b") as fh:
                fh.write(NUL * size)
                fh.flush()
                os.fsync(fh.fileno())
    except OSError as exc:                          # pragma: no cover
        logger.debug("cookie jar could not be overwritten: %s", exc)
    finally:
        path.unlink(missing_ok=True)


def fetch_private_owned(session: Session, raw_url: str, media_root: Path, *,
                        cookies_txt: str, adapter=None,
                        timeout_s: int = DOWNLOAD_TIMEOUT_S) -> FetchOutcome:
    """Archive one private video the platform confirms belongs to the owner.

    `cookies_txt` is Netscape cookies.txt content, supplied per call. It is
    not persisted, not logged, and not echoed back in the outcome.
    """
    target, why = normalise_url(raw_url)
    if target is None:
        return FetchOutcome(status=SyncStatus.BAD_URL, detail=why)

    auth = authorise_private_sync(session, target, adapter=adapter)
    if not auth.allowed:
        logger.warning("private sync refused for @%s/%s: %s", target.handle,
                       target.video_id, auth.reason)
        return FetchOutcome(external_id=target.video_id,
                            status=SyncStatus.NOT_OWNED, detail=auth.reason)

    if not cookies_txt or not cookies_txt.strip():
        return FetchOutcome(
            external_id=target.video_id, status=SyncStatus.PRIVATE,
            detail=("ownership is confirmed but no session was supplied, and "
                    "private content cannot be fetched without one"))
    if len(cookies_txt.encode("utf-8")) > MAX_COOKIE_BYTES:
        return FetchOutcome(
            external_id=target.video_id, status=SyncStatus.FAILED,
            detail=f"cookie jar exceeds {MAX_COOKIE_BYTES} bytes; rejected")

    if store_size(media_root) >= STORAGE_CAP_BYTES:
        return FetchOutcome(external_id=target.video_id,
                            status=SyncStatus.STORAGE_CAP,
                            detail="media store is at its cap")

    dest = media_root / f"tiktok_{target.video_id}.mp4"
    if dest.exists():
        return FetchOutcome(external_id=target.video_id,
                            status=SyncStatus.ALREADY_HAVE, path=str(dest),
                            bytes_written=dest.stat().st_size,
                            detail="already archived")

    jar: Path | None = None
    try:
        jar = _write_cookie_jar(cookies_txt)
        # Size only. The content is a live credential and never goes to a log.
        logger.info("private sync for @%s/%s authorised by TikTok's own "
                    "video list (%d listed); session jar supplied (%d bytes)",
                    target.handle, target.video_id, auth.oauth_listed,
                    len(cookies_txt.encode("utf-8")))
        outcome = fetch_one(target, dest, timeout_s=timeout_s, cookies=jar)
    except OSError as exc:
        return FetchOutcome(external_id=target.video_id,
                            status=SyncStatus.FAILED,
                            detail=f"cookie jar could not be prepared: {exc}")
    finally:
        if jar is not None:
            _shred(jar)

    outcome.external_id = target.video_id
    if outcome.ok:
        _attach_to_video(session, target.video_id, outcome)
    return outcome
