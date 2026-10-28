"""The web UI's JSON API (ADR 0006).

Separate from the worker API in `app.py` on purpose: the worker
authenticates with a per-worker bearer token, while a browser cannot hold
one safely. This router uses a signed session cookie instead, and every
mutation additionally requires a header that a cross-site form cannot set.

Three rules shape every response here, all from CLAUDE.md §3:

  * **Unknown is never zero.** A metric the platform does not report is
    null, and the interface renders a dash.
  * **No metric travels without its age.** Every reading carries
    `age_days`, so two differently-aged numbers cannot be presented as
    comparable.
  * **Machine proposals and human decisions are different states.**
    `status` and `decided_by` are always returned together, so the UI can
    show a proposal as a proposal.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import secrets
import time
from datetime import datetime, timezone

from fastapi import (APIRouter, Cookie, Depends, Header, HTTPException, Query,
                     Request, Response)
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from server import config
from server.api.deps import get_session
from server.collectors import permalink
from server.collectors.media_preservation import coverage as media_coverage
from server.db.models import (BreakthroughCandidate, CollectionRun, Creator,
                              LocalMaster, MetricsSnapshot, OAuthToken,
                              Performance, PublicationLink, Video,
                              VideoFeature, VideoMasterLink)
from shared.schemas.ui import (CapabilityList, CapabilityState,
                               CollectionRunSummary, DecisionRequest,
                               DecisionResult, MasterLinkView,
                               MasterReviewItem, MediaCoverage, MetricReading,
                               Overview, PairSide, PlatformSummary,
                               PublicationLinkView, PublicationPairView,
                               ReviewCounts, SessionRequest, SessionState,
                               VideoDetail, VideoListItem, VideoPage)
from shared.utils.signing import sign_media_url

logger = logging.getLogger("virallab.api")

router = APIRouter(prefix="/api", tags=["ui"])

#: Set this to require a password for the UI. Absent means the UI is
#: unauthenticated, which is only acceptable because the API binds to the
#: tailnet; a warning is logged at startup either way.
UI_PASSWORD = os.environ.get("VIRALLAB_UI_PASSWORD", "")
SESSION_SECRET = os.environ.get("VIRALLAB_MEDIA_SECRET", "")
SESSION_COOKIE = "virallab_session"
SESSION_TTL = int(os.environ.get("VIRALLAB_UI_SESSION_TTL", "604800"))
#: A browser will not attach this to a cross-site form post, so requiring it
#: on mutations is a CSRF guard that needs no token round-trip.
CSRF_HEADER = "x-virallab-ui"

MEDIA_TTL = int(os.environ.get("VIRALLAB_MEDIA_TTL", "900"))


# ---------------------------------------------------------------------------
# session
# ---------------------------------------------------------------------------

def _sign(payload: str) -> str:
    mac = hmac.new(SESSION_SECRET.encode(), payload.encode(),
                   hashlib.sha256).hexdigest()
    return f"{payload}.{mac}"


def _verify(cookie: str | None) -> bool:
    if not cookie or "." not in cookie or not SESSION_SECRET:
        return False
    payload, _, mac = cookie.rpartition(".")
    expected = hmac.new(SESSION_SECRET.encode(), payload.encode(),
                        hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, mac):
        return False
    try:
        issued = int(payload.split(":", 1)[1])
    except (IndexError, ValueError):
        return False
    return (time.time() - issued) < SESSION_TTL


def auth_required() -> bool:
    return bool(UI_PASSWORD)


def require_ui(session_cookie: str | None = Cookie(default=None,
                                                   alias=SESSION_COOKIE)
               ) -> None:
    """Read access. Open when no password is configured."""
    if not auth_required():
        return
    if not _verify(session_cookie):
        raise HTTPException(401, "authentication required")


def require_ui_write(request: Request,
                     session_cookie: str | None = Cookie(
                         default=None, alias=SESSION_COOKIE),
                     x_virallab_ui: str = Header(default="")) -> None:
    """Write access: a valid session AND the custom header.

    The header is what makes a cross-site request unable to reach a
    state-changing endpoint, since a plain HTML form cannot set it and a
    cross-origin fetch that tries is stopped by the preflight.
    """
    require_ui(session_cookie)
    if not x_virallab_ui:
        raise HTTPException(403, f"missing {CSRF_HEADER} header")


@router.get("/session", response_model=SessionState)
def get_session_state(session_cookie: str | None = Cookie(
        default=None, alias=SESSION_COOKIE)) -> SessionState:
    return SessionState(
        authenticated=(not auth_required()) or _verify(session_cookie),
        auth_configured=auth_required())


@router.post("/session", response_model=SessionState)
def create_session(req: SessionRequest, response: Response) -> SessionState:
    if not auth_required():
        return SessionState(authenticated=True, auth_configured=False)
    # Constant-time: a timing difference would leak the password length.
    if not secrets.compare_digest(req.password, UI_PASSWORD):
        raise HTTPException(401, "incorrect password")
    response.set_cookie(
        SESSION_COOKIE, _sign(f"ui:{int(time.time())}"),
        max_age=SESSION_TTL, httponly=True, samesite="strict", secure=True)
    return SessionState(authenticated=True, auth_configured=True)


@router.delete("/session", response_model=SessionState)
def end_session(response: Response) -> SessionState:
    response.delete_cookie(SESSION_COOKIE, httponly=True, samesite="strict",
                           secure=True)
    return SessionState(authenticated=False, auth_configured=auth_required())


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _age_days(posted_at: datetime | None,
              captured_at: datetime | None) -> int | None:
    if not posted_at or not captured_at:
        return None
    return max(0, (captured_at - posted_at).days)


def _reading(snap: MetricsSnapshot, posted_at: datetime | None
             ) -> MetricReading:
    raw = snap.raw or {}
    return MetricReading(
        captured_at=snap.captured_at,
        age_days=_age_days(posted_at, snap.captured_at),
        views=snap.views, likes=snap.likes, comments=snap.comments,
        shares=snap.shares, saves=snap.saves,
        is_rounded=bool(snap.is_rounded),
        avg_watch_time_ms=raw.get("ig_reels_avg_watch_time"),
        total_view_time_ms=raw.get("ig_reels_video_view_total_time"),
        skip_rate_pct=raw.get("reels_skip_rate"))


def _signed_media_url(video: Video) -> str | None:
    """A short-lived capability URL for one video, or None.

    Relative on purpose: the UI is served from the same origin, so there is
    no need to embed the server's hostname, and a path cannot leak which
    interface the browser reached us on. The signature is scoped to this one
    video and this one time window, so the browser never receives - and
    cannot construct - a filesystem path.
    """
    if video.media_status != "available" or not video.media_path:
        return None
    # Defence in depth. Media preservation already refuses to fetch a third
    # party's publication, so this should be unreachable - but the cost of
    # being wrong is serving a competitor's media out of our own API, so the
    # policy is enforced at the point of use as well as at collection.
    if video.creator is not None and not video.creator.is_own_account:
        logger.warning("refused to sign media for non-own-account video %s",
                       video.id)
        return None
    if not SESSION_SECRET:
        return None
    return sign_media_url("", video.id, SESSION_SECRET,
                          ttl_seconds=MEDIA_TTL)


def _latest_snapshot_map(session: Session, video_ids: list[int]
                         ) -> dict[int, MetricsSnapshot]:
    if not video_ids:
        return {}
    newest = (select(MetricsSnapshot.video_id,
                     func.max(MetricsSnapshot.captured_at).label("ts"))
              .where(MetricsSnapshot.video_id.in_(video_ids))
              .group_by(MetricsSnapshot.video_id).subquery())
    rows = session.scalars(
        select(MetricsSnapshot).join(
            newest,
            (MetricsSnapshot.video_id == newest.c.video_id)
            & (MetricsSnapshot.captured_at == newest.c.ts))).all()
    return {s.video_id: s for s in rows}


# ---------------------------------------------------------------------------
# overview
# ---------------------------------------------------------------------------

def build_overview(session: Session) -> Overview:
    tokens = {t.platform: t for t in session.scalars(select(OAuthToken)).all()}
    creators = {c.platform: c for c in session.scalars(
        select(Creator).where(Creator.is_own_account.is_(True))).all()}

    platforms: list[PlatformSummary] = []
    for platform in ("tiktok", "instagram"):
        tok = tokens.get(platform)
        # Own-account only. Phase 4 put third-party publications in the
        # same table, and a researched competitor must not inflate the
        # owner's corpus size, publication dates, or capture-day count -
        # the last of which governs whether any fixed-age anchor is
        # computable at all.
        agg = session.execute(
            select(func.count(Video.id),
                   func.min(Video.posted_at),
                   func.max(Video.posted_at))
            .join(Creator, Creator.id == Video.creator_id)
            .where(Video.platform == platform,
                   Creator.is_own_account.is_(True))).one()
        snaps = session.scalar(
            select(func.count(MetricsSnapshot.id))
            .join(Video, Video.id == MetricsSnapshot.video_id)
            .join(Creator, Creator.id == Video.creator_id)
            .where(Video.platform == platform,
                   Creator.is_own_account.is_(True))) or 0
        platforms.append(PlatformSummary(
            platform=platform,
            account_ref=tok.account_ref if tok else None,
            handle=creators[platform].handle if platform in creators else None,
            connected=tok is not None,
            needs_reauth=bool(tok.needs_reauth) if tok else False,
            token_expires_at=tok.expires_at if tok else None,
            publications=agg[0] or 0, snapshots=snaps,
            first_published=agg[1], last_published=agg[2]))

    runs = [CollectionRunSummary(
        platform=r.platform, kind=r.kind, status=r.status,
        started_at=r.started_at, finished_at=r.finished_at,
        videos_seen=r.videos_seen, videos_new=r.videos_new,
        snapshots_written=r.snapshots_written, error=r.error)
        for r in session.scalars(
            select(CollectionRun).order_by(CollectionRun.id.desc()).limit(8)
        ).all()]

    media = []
    for platform in ("instagram", "tiktok"):
        cov = media_coverage(session, platform)
        # [PRIMARY, ADR 0001] TikTok's API returns no downloadable media, so
        # nothing is preservable there and a percentage would be meaningless.
        obtainable = platform != "tiktok"
        media.append(MediaCoverage(
            platform=platform, total=cov["total"],
            media_obtainable=obtainable,
            preservable=cov["preservable"] if obtainable else 0,
            preserved=cov["preserved"],
            coverage_pct=cov["coverage_pct"] if obtainable else None,
            by_status=cov["by_status"],
            by_reason_class=cov["by_reason_class"]))

    def count(stmt) -> int:
        return session.scalar(stmt) or 0

    review = ReviewCounts(
        master_links_pending=count(
            select(func.count(VideoMasterLink.id))
            .where(VideoMasterLink.status == "pending")),
        master_links_auto_unverified=count(
            select(func.count(VideoMasterLink.id))
            .where(VideoMasterLink.status == "auto",
                   VideoMasterLink.decided_by != "human")),
        master_links_human_decided=count(
            select(func.count(VideoMasterLink.id))
            .where(VideoMasterLink.decided_by == "human")),
        publication_links_proposed=count(
            select(func.count(PublicationLink.id))
            .where(PublicationLink.status == "proposed")),
        publication_links_confirmed=count(
            select(func.count(PublicationLink.id))
            .where(PublicationLink.status == "confirmed")),
        performances_inferred=count(
            select(func.count(Performance.id))
            .where(Performance.status == "inferred")),
        performances_confirmed=count(
            select(func.count(Performance.id))
            .where(Performance.status == "confirmed")))

    # Distinct capture DAYS, not snapshots. One day means no fixed-age
    # anchor is computable for anything, which is the single most important
    # fact about the current corpus.
    own_snaps = (select(MetricsSnapshot.captured_at)
                 .join(Video, Video.id == MetricsSnapshot.video_id)
                 .join(Creator, Creator.id == Video.creator_id)
                 .where(Creator.is_own_account.is_(True)).subquery())
    days = count(select(func.count(func.distinct(
        func.date_trunc("day", own_snaps.c.captured_at)))))
    bounds = session.execute(select(func.min(own_snaps.c.captured_at),
                                    func.max(own_snaps.c.captured_at))
                             ).one()

    return Overview(
        generated_at=datetime.now(timezone.utc),
        platforms=platforms, recent_runs=runs, media=media, review=review,
        local_masters=count(select(func.count(LocalMaster.id))),
        performances=count(select(func.count(Performance.id))),
        snapshot_days=days,
        earliest_snapshot=bounds[0], latest_snapshot=bounds[1])


# ---------------------------------------------------------------------------
# video library
# ---------------------------------------------------------------------------

def build_video_page(session: Session, *, platform: str | None,
                     q: str | None, has_media: bool | None,
                     linked: str | None, order: str,
                     limit: int, offset: int) -> VideoPage:
    # The library is the owner's own archive: it offers signed media URLs
    # and drives master-link review against local master files, neither of
    # which means anything for a third party's publication. Researched
    # creators are browsed through /api/research/* instead.
    stmt = (select(Video).join(Creator, Creator.id == Video.creator_id)
            .where(Creator.is_own_account.is_(True)))
    if platform:
        stmt = stmt.where(Video.platform == platform)
    if has_media is True:
        stmt = stmt.where(Video.media_status == "available")
    elif has_media is False:
        stmt = stmt.where(Video.media_status != "available")
    if q:
        like = f"%{q}%"
        stmt = stmt.where(Video.caption.ilike(like)
                          | Video.external_id.ilike(like))
    if linked == "yes":
        stmt = stmt.where(Video.id.in_(select(VideoMasterLink.video_id)))
    elif linked == "no":
        stmt = stmt.where(Video.id.notin_(select(VideoMasterLink.video_id)))

    total = session.scalar(
        select(func.count()).select_from(stmt.subquery())) or 0

    if order == "oldest":
        stmt = stmt.order_by(Video.posted_at.asc().nulls_last())
    elif order == "duration":
        stmt = stmt.order_by(Video.duration_s.desc().nulls_last())
    else:
        stmt = stmt.order_by(Video.posted_at.desc().nulls_last())

    videos = session.scalars(stmt.limit(limit).offset(offset)).all()
    ids = [v.id for v in videos]
    latest = _latest_snapshot_map(session, ids)

    link_rows = session.execute(
        select(VideoMasterLink.video_id, VideoMasterLink.status,
               VideoMasterLink.decided_by, LocalMaster.id, Performance.title)
        .join(LocalMaster, LocalMaster.id == VideoMasterLink.master_id)
        .outerjoin(Performance, Performance.id == LocalMaster.performance_id)
        .where(VideoMasterLink.video_id.in_(ids))).all() if ids else []
    per_video: dict[int, list] = {}
    for vid, status, decided_by, _mid, perf in link_rows:
        per_video.setdefault(vid, []).append((status, decided_by, perf))

    pub_counts: dict[int, int] = {}
    if ids:
        for col in (PublicationLink.video_a_id, PublicationLink.video_b_id):
            for vid, n in session.execute(
                    select(col, func.count(PublicationLink.id))
                    .where(col.in_(ids)).group_by(col)).all():
                pub_counts[vid] = pub_counts.get(vid, 0) + n

    # One query for every post URL on the page rather than one per
    # row: the list renders 50 at a time and a per-row lookup would
    # turn the cheapest page in the app into the slowest.
    post_urls = permalink.bulk(session, ids)

    items = []
    for v in videos:
        snap = latest.get(v.id)
        links = per_video.get(v.id, [])
        items.append(VideoListItem(
            id=v.id, platform=v.platform, external_id=v.external_id,
            permalink=post_urls.get(v.id),
            caption_preview=(v.caption or "")[:140] or None,
            posted_at=v.posted_at, duration_s=v.duration_s,
            latest_views=snap.views if snap else None,
            latest_age_days=_age_days(v.posted_at,
                                      snap.captured_at) if snap else None,
            has_media=v.media_status == "available",
            media_status=v.media_status,
            master_links=len(links),
            master_link_verified=any(
                d == "human" and s in ("confirmed", "variant")
                for s, d, _ in links),
            cross_platform_links=pub_counts.get(v.id, 0),
            performance=next((p for _, _, p in links if p), None)))
    return VideoPage(total=total, offset=offset, limit=limit, items=items)


def build_video_detail(session: Session, video_id: int) -> VideoDetail:
    v = session.get(Video, video_id)
    if v is None:
        raise HTTPException(404, "no such publication")
    creator = session.get(Creator, v.creator_id) if v.creator_id else None

    snaps = session.scalars(
        select(MetricsSnapshot)
        .where(MetricsSnapshot.video_id == video_id)
        .order_by(MetricsSnapshot.captured_at.asc())).all()

    master_links = []
    rows = session.execute(
        select(VideoMasterLink, LocalMaster, Performance.title)
        .join(LocalMaster, LocalMaster.id == VideoMasterLink.master_id)
        .outerjoin(Performance, Performance.id == LocalMaster.performance_id)
        .where(VideoMasterLink.video_id == video_id)
        .order_by(VideoMasterLink.score.desc().nulls_last())).all()
    for link, master, perf in rows:
        master_links.append(MasterLinkView(
            id=link.id, master_id=master.id, filename=master.filename,
            master_duration_s=master.duration_s, performance=perf,
            score=link.score, frame_overlap=link.frame_overlap,
            audio_similarity=link.audio_similarity,
            audio_method=link.audio_method,
            audio_offset_s=link.audio_offset_s,
            duration_delta_s=link.duration_delta_s,
            runner_up=link.runner_up, status=link.status,
            decided_by=link.decided_by, decided_at=link.decided_at,
            evidence=link.evidence, notes=link.notes))

    pub_links = []
    pls = session.scalars(select(PublicationLink).where(
        (PublicationLink.video_a_id == video_id)
        | (PublicationLink.video_b_id == video_id))).all()
    other_ids = [pl.video_b_id if pl.video_a_id == video_id
                 else pl.video_a_id for pl in pls]
    others = {o.id: o for o in session.scalars(
        select(Video).where(Video.id.in_(other_ids))).all()} if other_ids \
        else {}
    other_latest = _latest_snapshot_map(session, other_ids)
    for pl in pls:
        oid = pl.video_b_id if pl.video_a_id == video_id else pl.video_a_id
        o = others.get(oid)
        if o is None:
            continue
        osnap = other_latest.get(oid)
        pub_links.append(PublicationLinkView(
            id=pl.id, other_video_id=oid, other_platform=o.platform,
            other_external_id=o.external_id, other_posted_at=o.posted_at,
            other_duration_s=o.duration_s,
            other_latest_views=osnap.views if osnap else None,
            other_latest_age_days=_age_days(
                o.posted_at, osnap.captured_at) if osnap else None,
            relationship_type=pl.relationship_type, status=pl.status,
            score=pl.score, decided_by=pl.decided_by, evidence=pl.evidence))

    # Fixed-age anchors: say why, never interpolate.
    anchors: dict[str, str | int | None] = {}
    for anchor in (7, 30, 90):
        bracketing = [s for s in snaps
                      if (_age_days(v.posted_at, s.captured_at) or 0) >= anchor]
        before = [s for s in snaps
                  if (_age_days(v.posted_at, s.captured_at) or 0) <= anchor]
        if bracketing and before and len(snaps) >= 2:
            anchors[f"views@{anchor}d"] = "available"
        elif not snaps:
            anchors[f"views@{anchor}d"] = "no snapshots"
        else:
            anchors[f"views@{anchor}d"] = (
                "no bracketing snapshots - never interpolated")

    return VideoDetail(
        id=v.id, platform=v.platform, external_id=v.external_id,
        permalink=permalink.for_video(v, creator),
        caption=v.caption, hashtags=list(v.hashtags or []),
        posted_at=v.posted_at, duration_s=v.duration_s, source=v.source,
        media_status=v.media_status, media_reason=v.media_reason,
        media_url=_signed_media_url(v), cover_url=v.cover_url,
        creator_handle=creator.handle if creator else None,
        is_own_account=bool(creator.is_own_account) if creator else False,
        history=[_reading(s, v.posted_at) for s in snaps],
        master_links=master_links, publication_links=pub_links,
        anchors=anchors)


# ---------------------------------------------------------------------------
# review queues
# ---------------------------------------------------------------------------

def build_master_link_queue(session: Session, *, status: str = "pending",
                            limit: int = 50) -> list[MasterReviewItem]:
    """Pending master links, each with everything needed to decide it.

    The local master's path is included because only a machine that can see
    `D:` can play it. The server never serves that file - it hands the path
    to a local bridge, and the interface says plainly when no bridge is
    reachable rather than showing a broken player.
    """
    rows = session.execute(
        select(VideoMasterLink, LocalMaster, Video, Performance.title)
        .join(LocalMaster, LocalMaster.id == VideoMasterLink.master_id)
        .join(Video, Video.id == VideoMasterLink.video_id)
        .outerjoin(Performance, Performance.id == LocalMaster.performance_id)
        .where(VideoMasterLink.status == status)
        .order_by(VideoMasterLink.score.desc().nulls_last())
        .limit(limit)).all()
    ids = [v.id for _l, _m, v, _p in rows]
    latest = _latest_snapshot_map(session, ids)

    out: list[MasterReviewItem] = []
    for link, master, video, perf in rows:
        snap = latest.get(video.id)
        out.append(MasterReviewItem(
            link=MasterLinkView(
                id=link.id, master_id=master.id, filename=master.filename,
                master_duration_s=master.duration_s, performance=perf,
                score=link.score, frame_overlap=link.frame_overlap,
                audio_similarity=link.audio_similarity,
                audio_method=link.audio_method,
                audio_offset_s=link.audio_offset_s,
                duration_delta_s=link.duration_delta_s,
                runner_up=link.runner_up, status=link.status,
                decided_by=link.decided_by, decided_at=link.decided_at,
                evidence=link.evidence, notes=link.notes),
            video=VideoListItem(
                id=video.id, platform=video.platform,
                external_id=video.external_id,
                caption_preview=(video.caption or "")[:140] or None,
                posted_at=video.posted_at, duration_s=video.duration_s,
                latest_views=snap.views if snap else None,
                latest_age_days=_age_days(
                    video.posted_at, snap.captured_at) if snap else None,
                has_media=video.media_status == "available",
                media_status=video.media_status, master_links=1,
                master_link_verified=link.decided_by == "human",
                cross_platform_links=0, performance=perf),
            video_media_url=_signed_media_url(video),
            video_caption=video.caption,
            master_local_path=master.path))
    return out


def _pair_side(video: Video, snap: MetricsSnapshot | None,
               master: str | None, perf: str | None,
               creator: Creator | None = None) -> PairSide:
    return PairSide(
        video_id=video.id, platform=video.platform,
        external_id=video.external_id,
        permalink=permalink.for_video(video, creator),
        posted_at=video.posted_at,
        duration_s=video.duration_s, caption=video.caption,
        media_url=_signed_media_url(video), cover_url=video.cover_url,
        latest_views=snap.views if snap else None,
        latest_age_days=_age_days(
            video.posted_at, snap.captured_at) if snap else None,
        master_filename=master, performance=perf)


def build_pair_queue(session: Session, *, status: str = "proposed",
                     limit: int = 50) -> list[PublicationPairView]:
    links = session.scalars(
        select(PublicationLink)
        .where(PublicationLink.status == status)
        .order_by(PublicationLink.score.desc().nulls_last())
        .limit(limit)).all()
    ids = {i for pl in links for i in (pl.video_a_id, pl.video_b_id)}
    if not ids:
        return []
    videos = {v.id: v for v in session.scalars(
        select(Video).where(Video.id.in_(ids))).all()}
    latest = _latest_snapshot_map(session, list(ids))

    # Master and performance per side, so the pair can be judged against
    # whatever content-level evidence exists on either platform.
    master_of: dict[int, tuple[str | None, str | None]] = {}
    for vid, fname, perf in session.execute(
            select(VideoMasterLink.video_id, LocalMaster.filename,
                   Performance.title)
            .join(LocalMaster, LocalMaster.id == VideoMasterLink.master_id)
            .outerjoin(Performance,
                       Performance.id == LocalMaster.performance_id)
            .where(VideoMasterLink.video_id.in_(ids),
                   VideoMasterLink.status.in_(
                       ("auto", "confirmed", "variant")))).all():
        master_of.setdefault(vid, (fname, perf))

    out = []
    for pl in links:
        a, b = videos.get(pl.video_a_id), videos.get(pl.video_b_id)
        if a is None or b is None:
            continue
        ma, pa = master_of.get(a.id, (None, None))
        mb, pb = master_of.get(b.id, (None, None))
        out.append(PublicationPairView(
            id=pl.id, relationship_type=pl.relationship_type,
            status=pl.status, score=pl.score, decided_by=pl.decided_by,
            evidence=pl.evidence,
            a=_pair_side(a, latest.get(a.id), ma, pa,
                         session.get(Creator, a.creator_id)),
            b=_pair_side(b, latest.get(b.id), mb, pb,
                         session.get(Creator, b.creator_id))))
    return out


# ---------------------------------------------------------------------------
# review decisions
# ---------------------------------------------------------------------------

MASTER_ANSWERS = {"confirmed", "rejected", "variant"}
PAIR_ANSWERS = {"confirmed", "rejected", "platform_edit"}


def apply_master_decision(session: Session, link_id: int,
                          req: DecisionRequest) -> DecisionResult:
    link = session.get(VideoMasterLink, link_id)
    if link is None:
        raise HTTPException(404, "no such link")
    if req.answer == "skip":
        remaining = session.scalar(
            select(func.count(VideoMasterLink.id))
            .where(VideoMasterLink.status == "pending")) or 0
        return DecisionResult(id=link.id, status=link.status,
                              decided_by=link.decided_by,
                              decided_at=link.decided_at,
                              remaining=remaining)
    if req.answer not in MASTER_ANSWERS:
        raise HTTPException(400, f"answer must be one of "
                                 f"{sorted(MASTER_ANSWERS | {'skip'})}")
    link.status = req.answer
    link.decided_by = "human"
    link.decided_at = datetime.now(timezone.utc)
    if req.notes:
        link.notes = req.notes
    session.commit()
    remaining = session.scalar(
        select(func.count(VideoMasterLink.id))
        .where(VideoMasterLink.status == "pending")) or 0
    return DecisionResult(id=link.id, status=link.status,
                          decided_by=link.decided_by,
                          decided_at=link.decided_at, remaining=remaining)


def apply_pair_decision(session: Session, link_id: int,
                        req: DecisionRequest) -> DecisionResult:
    link = session.get(PublicationLink, link_id)
    if link is None:
        raise HTTPException(404, "no such pair")
    if req.answer == "skip":
        remaining = session.scalar(
            select(func.count(PublicationLink.id))
            .where(PublicationLink.status == "proposed")) or 0
        return DecisionResult(id=link.id, status=link.status,
                              decided_by=link.decided_by,
                              decided_at=link.decided_at,
                              remaining=remaining)
    if req.answer not in PAIR_ANSWERS:
        raise HTTPException(400, f"answer must be one of "
                                 f"{sorted(PAIR_ANSWERS | {'skip'})}")
    if req.answer == "platform_edit":
        link.status = "confirmed"
        link.relationship_type = "platform_edit"
    else:
        link.status = req.answer
    link.decided_by = "human"
    link.decided_at = datetime.now(timezone.utc)
    if req.notes:
        link.notes = req.notes
    session.commit()
    remaining = session.scalar(
        select(func.count(PublicationLink.id))
        .where(PublicationLink.status == "proposed")) or 0
    return DecisionResult(id=link.id, status=link.status,
                          decided_by=link.decided_by,
                          decided_at=link.decided_at, remaining=remaining)


# ---------------------------------------------------------------------------
# capabilities — the honest state of what does not exist yet
# ---------------------------------------------------------------------------

def build_capabilities(session: Session) -> CapabilityList:
    """What each product capability can actually do right now.

    The competitor-intelligence navigation exists before the collection
    does, because the navigation shape is architecture. This endpoint is
    what keeps those sections honest: nothing fabricates rows, and each one
    states the phase that will deliver it and what currently blocks it.
    """
    own = session.scalar(select(func.count(Creator.id))
                         .where(Creator.is_own_account.is_(True))) or 0
    others = session.scalar(select(func.count(Creator.id))
                            .where(Creator.is_own_account.is_(False))) or 0
    pubs = session.scalar(select(func.count(Video.id))) or 0
    discovered_pubs = session.scalar(
        select(func.count(Video.id))
        .where(Video.discovery_job_id.isnot(None))) or 0
    fixture_creators = session.scalar(
        select(func.count(Creator.id))
        .where(Creator.is_fixture.is_(True))) or 0
    discovered_by_search = session.scalar(
        select(func.count(Creator.id))
        .where(Creator.source == "discovery")) or 0
    candidates = session.scalar(
        select(func.count(BreakthroughCandidate.id))) or 0
    inferred = session.scalar(
        select(func.count(BreakthroughCandidate.id))
        .where(BreakthroughCandidate.classification
               == "inferred_breakthrough")) or 0
    budget_ok = config.settings().spending.any_authorised

    # Competitor publications deep enough to analyse. A creator with
    # one video contributes nothing to a within-creator comparison,
    # and 282 of them still have exactly one.
    deep = session.scalar(select(func.count()).select_from(
        select(Video.creator_id)
        .join(Creator, Creator.id == Video.creator_id)
        .where(Creator.is_own_account.is_(False))
        .group_by(Video.creator_id)
        .having(func.count(Video.id) >= 8).subquery())) or 0
    their_pubs = session.scalar(
        select(func.count(Video.id))
        .join(Creator, Creator.id == Video.creator_id)
        .where(Creator.is_own_account.is_(False))) or 0
    # How many of them anything visual has ever been measured on.
    # This is the live gap and the number this page exists to show.
    their_visual = session.scalar(
        select(func.count(func.distinct(VideoFeature.video_id)))
        .join(Video, Video.id == VideoFeature.video_id)
        .join(Creator, Creator.id == Video.creator_id)
        .where(Creator.is_own_account.is_(False),
               VideoFeature.status == "ok",
               VideoFeature.feature.like(r"ocr\_%"))) or 0
    their_asked = session.scalar(
        select(func.count(func.distinct(VideoFeature.video_id)))
        .join(Video, Video.id == VideoFeature.video_id)
        .join(Creator, Creator.id == Video.creator_id)
        .where(Creator.is_own_account.is_(False),
               VideoFeature.status == "ok",
               VideoFeature.feature.like(r"vision\_%"))) or 0

    return CapabilityList(capabilities=[
        CapabilityState(
            key="own_accounts", title="Own-account intelligence",
            summary="Official-API collection, preserved media, "
                    "content-level matching to local masters.",
            implemented=True, phase="2",
            counts={"creators": own, "publications": pubs}),
        CapabilityState(
            key="review", title="Relationship review",
            summary="Confirm or reject proposed master links and "
                    "cross-platform pairs.",
            implemented=True, phase="3",
            counts={
                "pending": session.scalar(
                    select(func.count(VideoMasterLink.id))
                    .where(VideoMasterLink.status == "pending")) or 0,
                "proposed": session.scalar(
                    select(func.count(PublicationLink.id))
                    .where(PublicationLink.status == "proposed")) or 0}),
        CapabilityState(
            key="creator_analysis", title="Targeted creator analysis",
            summary="Enter a TikTok or Instagram handle and analyse that "
                    "creator: publication history, performance against "
                    "their own baseline, and change points.",
            implemented=True, phase="4",
            blocked_by=([] if budget_ok else [
                "runs against synthetic fixtures until a provider and a "
                "monthly cap are authorised in config/settings.yaml",
            ]),
            counts={"creators_researched": others,
                    "publications": discovered_pubs,
                    "fixture_creators": fixture_creators}),
        CapabilityState(
            key="breakthrough", title="Breakthrough analysis",
            summary="Where a creator's performance level changed, "
                    "classified as an observed outlier, an inferred "
                    "breakthrough, or a verified trajectory change.",
            implemented=True, phase="4",
            blocked_by=[
                "a verified trajectory change needs repeated observations "
                "of the same publications over time, which a single "
                "research pass cannot produce - research again later and "
                "the evidence accumulates",
            ],
            counts={"candidates": candidates,
                    "inferred_breakthroughs": inferred}),
        CapabilityState(
            key="discovery", title="Keyword and hashtag discovery",
            summary="Find creators nobody told us about, by keyword, "
                    "hashtag, niche, genre or composer.",
            implemented=True, phase="4",
            blocked_by=([] if budget_ok else [
                "spending is not authorised: monthly_cap_usd is 0.00 and "
                "providers_authorised is empty",
            ]),
            counts={"discovered_creators": discovered_by_search,
                    "deep_enough_to_analyse": deep}),
        CapabilityState(
            key="multimodal", title="Multimodal video intelligence",
            summary="Structured musical, visual, editing and language "
                    "features with model provenance and confidence.",
            implemented=True, phase="6",
            counts={"their_publications": their_pubs,
                    "captions_and_timing_measured": their_pubs,
                    "anything_visual_measured": their_visual}),
        # The live gap, and the reason this page should be read
        # before any competitor finding about how a video LOOKS.
        CapabilityState(
            key="competitor_vision",
            title="What other creators' videos look like",
            summary="Camera framing, who is in shot, the room, sheet "
                    "music, how formal it looks - asked of other "
                    "creators' video rather than only of yours.",
            implemented=False, phase="6",
            blocked_by=[
                f"measured on {their_asked} of {their_pubs} of their "
                f"publications. Every competitor finding about how a "
                f"video looks rests on the {their_visual} with any "
                f"visual measurement at all",
                "it needs their video downloaded first, and the "
                "budget set aside for that is $3.00 a month - which "
                "is what actually stops this, not the overall "
                "monthly limit",
                "frames are deleted after six hours by ADR 0009, so "
                "this is a recurring cost rather than a one-off",
            ],
            counts={"asked_of_their_videos": their_asked,
                    "any_visual_measurement": their_visual,
                    "their_publications": their_pubs}),
        CapabilityState(
            key="recommendations", title="AI creative strategist",
            summary="Evidence-backed ideas, opening text and captions, "
                    "written against measured evidence and checked "
                    "against what you are actually doing.",
            implemented=True, phase="8",
            blocked_by=[
                "grounded in Observed associations only. Nothing here "
                "has survived an experiment, so a suggestion is "
                "something to try rather than something that works",
            ]),
    ])


# ---------------------------------------------------------------------------
# routes
#
# Thin by design: each one validates, delegates to a `build_*` or `apply_*`
# function above, and returns a typed model. Keeping the logic out of the
# decorated functions is what lets the tests exercise it against a session
# without going through HTTP.
# ---------------------------------------------------------------------------

@router.get("/overview", response_model=Overview,
            dependencies=[Depends(require_ui)])
def overview(session: Session = Depends(get_session)) -> Overview:
    return build_overview(session)


@router.get("/videos", response_model=VideoPage,
            dependencies=[Depends(require_ui)])
def videos(
    platform: str | None = Query(default=None, pattern="^(tiktok|instagram)$"),
    q: str | None = Query(default=None, max_length=200),
    has_media: bool | None = Query(default=None),
    linked: str | None = Query(default=None, pattern="^(yes|no)$"),
    order: str = Query(default="newest",
                       pattern="^(newest|oldest|duration)$"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(get_session),
) -> VideoPage:
    return build_video_page(session, platform=platform, q=q,
                            has_media=has_media, linked=linked, order=order,
                            limit=limit, offset=offset)


@router.get("/videos/{video_id}", response_model=VideoDetail,
            dependencies=[Depends(require_ui)])
def video_detail(video_id: int,
                 session: Session = Depends(get_session)) -> VideoDetail:
    return build_video_detail(session, video_id)


@router.get("/review/master-links", response_model=list[MasterReviewItem],
            dependencies=[Depends(require_ui)])
def review_master_links(
    status: str = Query(default="pending",
                        pattern="^(pending|auto|confirmed|rejected|variant)$"),
    limit: int = Query(default=50, ge=1, le=200),
    session: Session = Depends(get_session),
) -> list[MasterReviewItem]:
    return build_master_link_queue(session, status=status, limit=limit)


@router.post("/review/master-links/{link_id}", response_model=DecisionResult,
             dependencies=[Depends(require_ui_write)])
def decide_master_link(link_id: int, req: DecisionRequest,
                       session: Session = Depends(get_session)
                       ) -> DecisionResult:
    return apply_master_decision(session, link_id, req)


@router.get("/review/publication-links",
            response_model=list[PublicationPairView],
            dependencies=[Depends(require_ui)])
def review_publication_links(
    status: str = Query(default="proposed",
                        pattern="^(proposed|confirmed|rejected)$"),
    limit: int = Query(default=50, ge=1, le=200),
    session: Session = Depends(get_session),
) -> list[PublicationPairView]:
    return build_pair_queue(session, status=status, limit=limit)


@router.post("/review/publication-links/{link_id}",
             response_model=DecisionResult,
             dependencies=[Depends(require_ui_write)])
def decide_publication_link(link_id: int, req: DecisionRequest,
                            session: Session = Depends(get_session)
                            ) -> DecisionResult:
    return apply_pair_decision(session, link_id, req)


@router.get("/capabilities", response_model=CapabilityList,
            dependencies=[Depends(require_ui)])
def capabilities(session: Session = Depends(get_session)) -> CapabilityList:
    return build_capabilities(session)
