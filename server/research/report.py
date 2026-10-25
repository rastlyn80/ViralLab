"""The creator research report: everything one research pass can honestly say.

Structured so that each section states its own evidential basis. The hardest
part of this file is not computing the figures - it is making sure that a
reader skimming it cannot come away with a belief the data does not support.
Three specific confusions are designed against:

1. **Lifetime views read as a trajectory.** Handled by never comparing a
   recent publication to an old one without reporting the age gap, and by
   routing every comparison through the creator's own trailing baseline.

2. **An inferred breakthrough read as an established cause.** The report
   states why the boundary was flagged, what the evidence actually is, and -
   as a first-class section rather than a footnote - precisely what is
   missing and what would have to be observed to strengthen it.

3. **A metadata string read as an identification.** Repertoire detection
   here is keyword matching over captions, hashtags and music titles. It
   finds mentions, not works. `docs/MULTIMODAL_PIPELINE.md` 6 records
   general repertoire identification as an open research question; every
   item produced here is tagged `metadata_mention` and needs human
   confirmation before it means anything.
"""

from __future__ import annotations

import logging
import re
import statistics
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from server.db.models import (BreakthroughCandidate, Creator, DiscoveryJob,
                              MetricsSnapshot, Video)
from server.research import baseline as bl
from server.research import collections as coll
from server.research import timeline as tlmod
from server.research.service import observations_for

logger = logging.getLogger("virallab.research.report")

#: Composers whose names appear in this niche. Used for *mention* detection
#: only. A list is not a classifier.
COMPOSERS = (
    "chopin", "debussy", "satie", "rachmaninoff", "liszt", "bach",
    "beethoven", "ravel", "einaudi", "mozart", "schubert", "schumann",
    "brahms", "grieg", "tchaikovsky", "scriabin", "prokofiev", "scarlatti",
    "mendelssohn", "faure", "albeniz", "granados", "ginastera",
)
#: Format words that describe how a video is made rather than what is played.
FORMAT_WORDS = (
    "cover", "tutorial", "improvisation", "improv", "practice", "challenge",
    "duet", "collab", "live", "concert", "recital", "pov", "asmr",
    "arrangement", "medley", "sightreading", "transcription",
)

#: "Recent" for the recent-vs-historical split. Deliberately generous: a
#: narrower window would compare two handfuls of posts.
RECENT_N = 12


@dataclass
class ReportSection:
    """A block of the report, with what it rests on stated inline."""

    key: str
    title: str
    #: observed | inferred | unavailable
    basis: str
    body: dict = field(default_factory=dict)
    caveats: list[str] = field(default_factory=list)
    unavailable_reason: str | None = None


@dataclass
class CreatorReport:
    creator_id: int
    platform: str
    handle: str
    display_name: str | None
    profile_url: str | None
    is_fixture: bool
    generated_at: datetime
    sections: list[ReportSection] = field(default_factory=list)
    #: Provenance of the research itself, which is part of the finding.
    research_date: datetime | None = None
    provider: str | None = None
    cost_usd: float | None = None
    job_id: int | None = None
    data_limitations: list[str] = field(default_factory=list)


def build(session: Session, creator_id: int) -> CreatorReport:
    creator = session.get(Creator, creator_id)
    if creator is None:
        raise ValueError(f"no such creator {creator_id}")

    obs = observations_for(session, creator_id)
    points = bl.rolling_baseline(obs)
    tl = tlmod.build(session, creator_id)
    job = (session.get(DiscoveryJob, creator.last_job_id)
           if creator.last_job_id else None)

    rep = CreatorReport(
        creator_id=creator.id, platform=creator.platform,
        handle=creator.handle, display_name=creator.display_name,
        profile_url=creator.profile_url,
        is_fixture=bool(creator.is_fixture),
        generated_at=datetime.now(timezone.utc),
        research_date=creator.last_refreshed_at,
        provider=job.provider if job else None,
        cost_usd=job.actual_usd if job else None,
        job_id=job.id if job else None)

    rep.sections.append(_overview(session, creator, obs, job))
    rep.sections.append(_coverage(session, creator, obs, job))
    rep.sections.append(_typical(obs, points))
    rep.sections.append(_strongest(points))
    rep.sections.append(_outliers(session, creator_id, points))
    rep.sections.append(_breakthrough(session, creator_id, tl))
    rep.sections.append(_frequency(tl))
    rep.sections.append(_repertoire(session, creator_id, obs))
    rep.sections.append(_recent_vs_historical(obs, points))
    rep.sections.append(_membership(session, creator_id))

    rep.data_limitations = _limitations(creator, obs, tl, job)
    return rep


# ---------------------------------------------------------------------------
# sections
# ---------------------------------------------------------------------------

def _overview(session: Session, creator: Creator, obs: list,
              job: DiscoveryJob | None) -> ReportSection:
    caps = ((creator.provenance or {}).get("capabilities") or {})
    followers_reported = caps.get("follower_count")
    snap_count = session.scalar(
        select(func.count()).select_from(
            select(MetricsSnapshot.id)
            .join(Video, Video.id == MetricsSnapshot.video_id)
            .where(Video.creator_id == creator.id).subquery())) or 0

    sec = ReportSection(
        key="overview", title="Account overview", basis="observed",
        body={
            "platform": creator.platform,
            "handle": creator.handle,
            "display_name": creator.display_name,
            "profile_url": creator.profile_url,
            "follower_count": creator.follower_count,
            "follower_count_available": followers_reported is not False,
            "source": creator.source,
            "discovered_via": creator.discovered_via,
            "first_seen_at": creator.first_seen_at,
            "research_status": creator.research_status,
            "publications_collected": creator.publications_collected or 0,
            "observations_total": snap_count,
            "is_fixture": bool(creator.is_fixture),
        })
    if followers_reported is False:
        sec.caveats.append(
            "`[PRIMARY]` This provider reports no follower count for "
            f"{creator.platform}. The field is empty because the number is "
            f"unknown, not because it is zero.")
    if creator.follower_count is not None:
        sec.caveats.append(
            "The follower count is a single current value. It cannot "
            "evidence growth, and no claim here attributes follower change "
            "to any publication.")
    return sec


def _coverage(session: Session, creator: Creator, obs: list,
              job: DiscoveryJob | None) -> ReportSection:
    dates = sorted(o.published_at for o in obs if o.published_at)
    notes = list((job.coverage_notes or []) if job else [])
    if isinstance(notes, dict):
        notes = list(notes.get("items") or [])

    sec = ReportSection(
        key="coverage", title="Publication history and coverage",
        basis="observed",
        body={
            "publications_observed": len(obs),
            "claimed_publication_count": (
                (creator.provenance or {}).get("publication_count")),
            "earliest_published": dates[0] if dates else None,
            "latest_published": dates[-1] if dates else None,
            "span_days": ((dates[-1] - dates[0]).days
                          if len(dates) >= 2 else None),
            "provider_coverage_notes": [str(n) for n in notes],
        })
    if not obs:
        sec.basis = "unavailable"
        sec.unavailable_reason = "No publications collected yet."
        return sec
    sec.caveats.append(
        "This is what the provider returned, which is not necessarily the "
        "creator's complete back-catalogue. A gap here is indistinguishable "
        "from a post that was deleted or made private.")
    return sec


def _typical(obs: list, points: list) -> ReportSection:
    views = [o.views for o in obs if o.views is not None]
    if not views:
        return ReportSection(
            key="typical", title="Typical content performance",
            basis="unavailable",
            unavailable_reason="No view counts were reported.")

    ages = [(o.observed_at - o.published_at).days for o in obs
            if o.published_at and o.observed_at]
    rels = [p.relative_performance for p in points
            if p.relative_performance is not None]

    sec = ReportSection(
        key="typical", title="Typical content performance", basis="observed",
        body={
            "median_views": int(statistics.median(views)),
            "min_views": min(views),
            "max_views": max(views),
            "publications": len(views),
            "age_span_days": (max(ages) - min(ages)) if ages else None,
            "median_relative_performance": (round(statistics.median(rels), 2)
                                            if rels else None),
            "publications_without_baseline": sum(
                1 for p in points if p.relative_performance is None),
        })
    if ages and (max(ages) - min(ages)) > 60:
        sec.caveats.append(
            f"These publications were observed at ages spanning "
            f"{max(ages) - min(ages)} days, so a single median across them "
            f"mixes posts that have had very different amounts of time to "
            f"accumulate views. The median relative figure does not have "
            f"this problem: it compares each post to its own neighbours.")
    return sec


def _strongest(points: list) -> ReportSection:
    scored = [p for p in points if p.relative_performance is not None]
    if not scored:
        return ReportSection(
            key="strongest", title="Strongest relative performers",
            basis="unavailable",
            unavailable_reason=(
                "No publication has enough preceding posts to form a "
                "trailing baseline, so none can be ranked against it. "
                "Shown as unknown rather than 1.0."))
    scored.sort(key=lambda p: p.relative_performance, reverse=True)
    top = scored[:10]
    sec = ReportSection(
        key="strongest", title="Strongest relative performers",
        basis="inferred",
        body={"items": [{
            "external_id": p.observation.external_id,
            "video_id": p.observation.video_id,
            "published_at": p.observation.published_at,
            "views": p.observation.views,
            "age_days": ((p.observation.observed_at
                          - p.observation.published_at).days
                         if p.observation.published_at
                         and p.observation.observed_at else None),
            "relative_performance": round(p.relative_performance, 2),
            "robust_z": round(p.robust_z, 2) if p.robust_z is not None
            else None,
            "baseline_views": (round(p.baseline_views, 1)
                               if p.baseline_views is not None else None),
            "caption": p.observation.caption,
        } for p in top]})
    sec.caveats.append(
        "Ranked against each publication's own preceding window, not "
        "against raw views. Ranking by raw views would put the oldest posts "
        "on top simply because they have had longest to accumulate.")
    return sec


def _outliers(session: Session, creator_id: int,
              points: list) -> ReportSection:
    rows = session.scalars(
        select(BreakthroughCandidate).where(
            BreakthroughCandidate.creator_id == creator_id,
            BreakthroughCandidate.classification == "observed_outlier")
        .order_by(BreakthroughCandidate.publication_index)).all()
    if not rows:
        return ReportSection(
            key="outliers", title="Potential breakthrough videos",
            basis="observed",
            body={"items": []},
            caveats=["No publication stood out far enough from this "
                     "creator's own baseline to be flagged."])
    by_video = {p.observation.video_id: p for p in points}
    sec = ReportSection(
        key="outliers", title="Potential breakthrough videos",
        basis="inferred",
        body={"items": [{
            "video_id": r.video_id,
            "publication_index": r.publication_index,
            "observed_views": r.observed_views,
            "relative_performance": r.relative_performance,
            "robust_z": r.robust_z,
            "baseline_views": r.baseline_views,
            "published_at": (by_video[r.video_id].observation.published_at
                             if r.video_id in by_video else None),
            "caption": (by_video[r.video_id].observation.caption
                        if r.video_id in by_video else None),
        } for r in rows]})
    sec.caveats.append(
        "These are OBSERVED OUTLIERS: facts about single publications that "
        "performed far above this creator's local baseline. An outlier is "
        "not a breakthrough. One lucky video is noise; a breakthrough is a "
        "sustained change in level, reported separately below.")
    return sec


def _breakthrough(session: Session, creator_id: int,
                  tl: tlmod.CreatorTimeline) -> ReportSection:
    cp = tl.change_point
    if cp is None:
        return ReportSection(
            key="breakthrough", title="Change point analysis",
            basis="observed", body={"found": False},
            caveats=["No sustained change in performance level was "
                     "detected. A gradual rise is not a breakthrough, and "
                     "neither is a single spike."])

    sec = ReportSection(
        key="breakthrough", title="Change point analysis", basis="inferred",
        body={
            "found": True,
            "classification": cp.classification,
            "publication_index": cp.publication_index,
            "publications_total": len(tl.points),
            "video_id": cp.video_id,
            "at": cp.at,
            "median_before": cp.median_before,
            "median_after": cp.median_after,
            "step_ratio": cp.step_ratio,
            "sustained": cp.sustained,
            "confidence": cp.confidence,
            "confidence_is_calibrated": False,
            "confidence_note": cp.confidence_note,
            "evidence": cp.evidence,
            "missing_evidence": cp.missing_evidence,
            "why_flagged": _why_flagged(cp, tl),
            "what_would_strengthen": _what_would_strengthen(cp),
            "frequency_change": tlmod.frequency_change(tl),
        })

    if cp.classification == "inferred_breakthrough":
        sec.caveats.append(
            "INFERRED, not established. The creator's performance level "
            "changed here. Nothing claims this publication CAUSED the "
            "change, and no follower growth is attributed to it.")
    sec.caveats.append(
        "The confidence figure is an ordinal score, not a probability. Its "
        "calibration has never been measured on this corpus.")
    sec.caveats.append(
        "A niche-wide shift is indistinguishable from an individual "
        "breakthrough when only one account is in view. A control cohort of "
        "creators in the same niche is the named prerequisite for removing "
        "this caveat.")
    return sec


def _why_flagged(cp: tlmod.ChangePointView,
                 tl: tlmod.CreatorTimeline) -> list[str]:
    """The actual chain of reasoning, in order, with its numbers."""
    out: list[str] = []
    total = len(tl.points)
    if cp.publication_index is not None:
        out.append(
            f"A CUSUM scan over the log of view counts put the strongest "
            f"boundary at publication {cp.publication_index + 1} of "
            f"{total}.")
    if cp.median_before and cp.median_after:
        out.append(
            f"The robust median before the boundary is "
            f"{cp.median_before:,.0f} views; after it, "
            f"{cp.median_after:,.0f}. That is a "
            f"{cp.median_after / cp.median_before:.1f}x step, measured with "
            f"medians rather than means so a single spike cannot produce "
            f"it.")
    if cp.sustained:
        out.append(
            "Performance stayed at the higher level across the "
            "publications that followed, so this is not a hot streak that "
            "reverted.")
    elif cp.sustained is False:
        out.append(
            "Performance did NOT stay at the higher level, which is why "
            "this is reported as a candidate rather than a change.")
    if tl.mode != "fixed_age":
        out.append(
            "All of the above was computed from lifetime view counts read "
            "at one moment, at mixed publication ages. The step is "
            "therefore partly confounded with elapsed time, and the "
            "direction of that confound favours OLDER posts - which makes a "
            "step UP at a later date harder to produce by age alone, not "
            "easier.")
    return out


def _what_would_strengthen(cp: tlmod.ChangePointView) -> list[str]:
    out = [
        "Observe this creator again in a week. A second observation of the "
        "same publications is what makes any individual publication's "
        "growth visible, and it is the evidence the classification is "
        "waiting on.",
        "Collect follower history across the boundary. Without it, no claim "
        "can connect a publication to account growth.",
        "Research several creators in the same niche over the same period. "
        "If they all stepped up together, this is a platform or niche "
        "effect rather than this creator's breakthrough.",
    ]
    if cp.classification == "inferred_breakthrough":
        out.append(
            "Promotion to `verified_trajectory_change` requires repeated "
            "observations AND follower history. Both, not either.")
    return out


def _frequency(tl: tlmod.CreatorTimeline) -> ReportSection:
    if not tl.frequency:
        return ReportSection(
            key="frequency", title="Posting frequency", basis="unavailable",
            unavailable_reason="No publication dates were reported.")
    counts = [b.publications for b in tl.frequency]
    sec = ReportSection(
        key="frequency", title="Posting frequency", basis="observed",
        body={
            "by_month": [{"month": b.month, "publications": b.publications}
                         for b in tl.frequency],
            "median_days_between_posts": tl.median_days_between_posts,
            "busiest_month": max(tl.frequency,
                                 key=lambda b: b.publications).month,
            "median_per_month": statistics.median(counts),
            "change_around_breakthrough": tlmod.frequency_change(tl),
        })
    sec.caveats.append(
        "Frequency is computed only from publications the provider "
        "returned. A month with no posts here may be a month the provider "
        "did not cover.")
    return sec


_WORD = re.compile(r"[a-z]+")


def _repertoire(session: Session, creator_id: int,
                obs: list) -> ReportSection:
    """Mentions of composers and formats, not identifications.

    `docs/MULTIMODAL_PIPELINE.md` 6 records general repertoire
    identification as an open research question. This finds strings.
    """
    videos = {v.id: v for v in session.scalars(
        select(Video).where(Video.creator_id == creator_id)).all()}

    composer_hits: dict[str, int] = {}
    format_hits: dict[str, int] = {}
    hashtag_hits: dict[str, int] = {}
    music_titles: dict[str, int] = {}
    unmatched = 0

    for o in obs:
        video = videos.get(o.video_id) if o.video_id else None
        text_parts = [o.caption or ""]
        if video is not None:
            text_parts.extend(video.hashtags or [])
            music = video.music or {}
            title = (music.get("title") or "").strip()
            if title:
                music_titles[title] = music_titles.get(title, 0) + 1
        blob = " ".join(text_parts).lower()
        words = set(_WORD.findall(blob))

        found = False
        for c in COMPOSERS:
            if c in words:
                composer_hits[c] = composer_hits.get(c, 0) + 1
                found = True
        for f in FORMAT_WORDS:
            if f in words:
                format_hits[f] = format_hits.get(f, 0) + 1
        if video is not None:
            for h in (video.hashtags or []):
                k = str(h).lower().lstrip("#")
                hashtag_hits[k] = hashtag_hits.get(k, 0) + 1
        if not found:
            unmatched += 1

    if not composer_hits and not format_hits and not hashtag_hits:
        return ReportSection(
            key="repertoire", title="Repeated formats and repertoire",
            basis="unavailable",
            unavailable_reason=(
                "No captions, hashtags or music metadata were available to "
                "search. Nothing can be said about repertoire from "
                "metadata alone here."))

    def top(d: dict, n: int = 12) -> list[dict]:
        return [{"name": k, "publications": v}
                for k, v in sorted(d.items(), key=lambda kv: -kv[1])[:n]]

    sec = ReportSection(
        key="repertoire", title="Repeated formats and repertoire",
        basis="inferred",
        body={
            "method": "metadata_mention",
            "composer_mentions": top(composer_hits),
            "format_mentions": top(format_hits),
            "hashtags": top(hashtag_hits),
            "music_titles": top(music_titles, 8),
            "publications_with_no_composer_mention": unmatched,
            "publications_searched": len(obs),
        })
    sec.caveats.append(
        "These are MENTIONS found by keyword matching over captions, "
        "hashtags and music titles. They are not identifications of what "
        "was played: a caption can be wrong, absent, or about something "
        "else, and a piece can be performed without ever being named.")
    sec.caveats.append(
        f"{unmatched} of {len(obs)} publications mention no composer from "
        f"the known list. That is a limit of the list and of the captions, "
        f"not evidence about the content.")
    sec.caveats.append(
        "Reliable repertoire identification needs audio analysis and human "
        "confirmation. It is an open research question "
        "(docs/MULTIMODAL_PIPELINE.md 6), not a solved step.")
    return sec


def _recent_vs_historical(obs: list, points: list) -> ReportSection:
    if len(obs) < RECENT_N * 2:
        return ReportSection(
            key="recent_vs_historical",
            title="Recent versus historical performance",
            basis="unavailable",
            unavailable_reason=(
                f"Needs at least {RECENT_N * 2} observed publications to "
                f"split meaningfully; this creator has {len(obs)}."))

    ordered = sorted(
        [o for o in obs if o.published_at], key=lambda o: o.published_at)
    recent, older = ordered[-RECENT_N:], ordered[:-RECENT_N]

    def summarise(group: list) -> dict:
        views = [o.views for o in group if o.views is not None]
        ages = [(o.observed_at - o.published_at).days for o in group
                if o.published_at and o.observed_at]
        return {
            "publications": len(group),
            "median_views": int(statistics.median(views)) if views else None,
            "median_age_days": int(statistics.median(ages)) if ages else None,
        }

    rec, old = summarise(recent), summarise(older)
    rel_by_id = {p.observation.external_id: p.relative_performance
                 for p in points}
    rec_rel = [rel_by_id.get(o.external_id) for o in recent]
    old_rel = [rel_by_id.get(o.external_id) for o in older]
    rec_rel = [r for r in rec_rel if r is not None]
    old_rel = [r for r in old_rel if r is not None]

    sec = ReportSection(
        key="recent_vs_historical",
        title="Recent versus historical performance", basis="inferred",
        body={
            "recent": rec, "historical": old,
            "recent_median_relative": (round(statistics.median(rec_rel), 2)
                                       if rec_rel else None),
            "historical_median_relative": (
                round(statistics.median(old_rel), 2) if old_rel else None),
            "age_gap_days": (
                (old["median_age_days"] - rec["median_age_days"])
                if rec["median_age_days"] is not None
                and old["median_age_days"] is not None else None),
        })
    gap = sec.body["age_gap_days"]
    if gap:
        sec.caveats.append(
            f"The historical group was observed at a median age {gap} days "
            f"GREATER than the recent group. Comparing their raw medians "
            f"therefore measures elapsed exposure as much as content "
            f"quality, and the comparison favours the older group.")
    sec.caveats.append(
        "The relative figures are the comparable pair: each is already "
        "measured against its own local neighbourhood, so the age gap "
        "cancels out of them in a way it does not cancel out of raw views.")
    return sec


def _membership(session: Session, creator_id: int) -> ReportSection:
    rows = coll.collections_for_creator(session, creator_id)
    return ReportSection(
        key="collections", title="Collections", basis="observed",
        body={"items": rows},
        caveats=([] if rows else
                 ["Not in any collection. Saving this creator to one is "
                  "what makes them part of a cohort later."]))


def _limitations(creator: Creator, obs: list, tl: tlmod.CreatorTimeline,
                 job: DiscoveryJob | None) -> list[str]:
    out: list[str] = []
    if creator.is_fixture:
        out.append(
            "FIXTURE DATA. Every figure in this report is synthetic, "
            "fabricated for demonstration, and about no real person.")
    if not obs:
        out.append("No observations. Nothing in this report is measured.")
        return out
    if tl.mode != "fixed_age":
        out.append(
            "No fixed-age comparison is possible: the publications were "
            "each observed once, at whatever age they happened to be. "
            "Every raw view figure is a lifetime total.")
    if not tl.repeatedly_observed:
        out.append(
            "No publication has been observed twice, so no publication's "
            "growth over time is visible and no trajectory exists in this "
            "data.")
    caps = ((creator.provenance or {}).get("capabilities") or {})
    if caps.get("follower_count") is False:
        out.append(
            f"`[PRIMARY]` No follower count is available on "
            f"{creator.platform} from this provider, so no account-size "
            f"normalisation and no growth claim is possible.")
    if caps.get("saves_count") is False:
        out.append(
            "Saves are not reported on this platform by this provider, so "
            "they cannot be compared against a platform that does report "
            "them.")
    if caps.get("views_rounded"):
        out.append(
            "View counts are rounded to about 3 significant figures by this "
            "provider, which blurs outlier boundaries.")
    notes = (job.coverage_notes or []) if job else []
    if isinstance(notes, dict):
        notes = list(notes.get("items") or [])
    for n in notes:
        out.append(str(n))
    out.append(
        "Survivorship: this creator was researched because something made "
        "them interesting. Nothing here describes creators who tried "
        "similar content and did not succeed, and that absence cannot be "
        "corrected by collecting more of the same.")
    return out
