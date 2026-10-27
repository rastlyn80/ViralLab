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


#: M5's four sections. Every block belongs to exactly one, so a reader
#: meets the report in the order the brief specified rather than in the
#: order the code happens to compute things.
GROUPS: tuple[tuple[str, str], ...] = (
    ("summary", "What we found"),
    ("overview", "Overview"),
    ("publications", "Publication intelligence"),
    ("content", "Content intelligence"),
    ("evidence", "Evidence and uncertainty"),
)

#: Which group each section belongs to. Keyed by section key so a new
#: section has to be placed deliberately - a section with no group is a
#: section nobody decided where to show, and `build` raises rather than
#: quietly dropping it into a default.
SECTION_GROUP: dict[str, str] = {
    "plain_summary": "summary",
    "overview": "overview",
    "coverage": "evidence",
    "typical": "publications",
    "strongest": "publications",
    "outliers": "publications",
    "breakthrough": "publications",
    "frequency": "publications",
    "repertoire": "content",
    "owner_repertoire": "content",
    "paid_vs_organic": "publications",
    "recent_vs_historical": "content",
    "collections": "evidence",
}

#: BLUEPRINT.md section 2's ladder, as it applies to a report block.
#: Derived from the section's own basis, never written per section:
#:
#:   unavailable -> Unavailable   the measure does not exist
#:   observed    -> Observed      a direct measurement on publications
#:   inferred    -> Hypothesised  suggested by the data, not tested
#:
#: Nothing in a report reaches Associated or Validated on its own. Those
#: grades belong to a *feature* that survived correction or an
#: experiment, and they are read from the M4 catalogue rather than
#: minted here - a report block describing what happened cannot promote
#: itself by describing it well.
BASIS_GRADE: dict[str, tuple[str, str]] = {
    "unavailable": ("Unavailable", "the measure does not exist for this "
                                   "creator"),
    "observed": ("Observed", "a direct measurement on this creator's "
                             "publications"),
    "inferred": ("Hypothesised", "suggested by the data and not tested; "
                                 "may become an experiment proposal, "
                                 "never a recommendation"),
}


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
    # -- M5 -------------------------------------------------------------
    #: Which of the four report sections this block belongs to.
    group: str = ""
    #: The BLUEPRINT section 2 grade, derived from `basis` in `build`.
    grade: str = ""
    grade_reason: str = ""
    #: The publications this block's numbers were computed from, so every
    #: figure can be opened rather than taken on trust.
    evidence_video_ids: list[int] = field(default_factory=list)
    #: Examples that cut against the block's own reading. Listed, never
    #: hidden: a section that only shows its supporting cases is an
    #: argument, not a measurement.
    contradictions: list[dict] = field(default_factory=list)


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
    #: True when no publication carries a known age, so every
    #: comparison here is between lifetime totals (BLUEPRINT M5).
    lifetime_approximation: bool = False
    #: Spread between the oldest and youngest observation age. Above
    #: `WIDE_AGE_SPAN_DAYS` a single snapshot per publication is not a
    #: fixed-age comparison even though every date is known.
    age_span_days: int | None = None


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

    rep.sections.append(_plain_summary(session, creator, obs, points, tl))
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

    # Owner-only. Both rest on data that exists for the owner and cannot
    # exist for anyone else: paid exposure is visible only on an account
    # whose ad manager we can read, and repertoire is known only where
    # the local performance archive links to a publication. Adding them
    # for a competitor would mean inventing the input.
    if creator.is_own_account:
        rep.sections.append(_paid_vs_organic(session, creator))
        rep.sections.append(_owner_repertoire(session, creator))

    # Where no publication carries a known age, every comparison in the
    # report is between lifetime totals rather than at a fixed age, and
    # the brief requires that to be said out loud rather than left for
    # the reader to deduce. True for essentially every competitor.
    rep.lifetime_approximation = not _ages_known(obs)
    rep.age_span_days = _age_span_days(obs)
    _finalise(rep, basis_sentence=comparison_basis(obs))
    rep.data_limitations = _limitations(creator, obs, tl, job)
    return rep


def _finalise(rep: CreatorReport, *, basis_sentence: str = "") -> None:
    """Group, grade and link every section. One pass, after the fact.

    Doing this centrally rather than inside each section is deliberate:
    a grade computed beside the number it grades is a grade somebody can
    nudge, and the rule that a section's grade follows from its basis is
    easier to keep true when there is one place it happens.
    """
    for sec in rep.sections:
        group = SECTION_GROUP.get(sec.key)
        if group is None:
            raise ValueError(
                f"report section {sec.key!r} has no group; add it to "
                f"SECTION_GROUP rather than letting it default - a "
                f"section nobody placed is a section nobody reviewed")
        sec.group = group
        grade, why = BASIS_GRADE.get(
            sec.basis, ("Unavailable", f"unknown basis {sec.basis!r}"))
        sec.grade = grade
        sec.grade_reason = why
        if not sec.evidence_video_ids:
            sec.evidence_video_ids = sorted(_video_ids(sec.body))
        if (basis_sentence and sec.basis != "unavailable"
                and sec.group in ("publications", "content")):
            sec.body.setdefault("comparison_basis", basis_sentence)


#: How the figures in a comparing section may be read. Computed from
#: the observations, never assumed.
#:
#: **The brief's parenthetical is wrong for this corpus and the code
#: follows the data instead.** BLUEPRINT M5 says performance is
#: compared "at a fixed age where ages are known, and labelled
#: *lifetime approximation* where they are not (every competitor)".
#: Measured 2026-10-27: the discovery provider returns a publication
#: date for every competitor publication held - 2 of 2 for pianza88,
#: 5 of 5 for tranquilstreamsmusic, 2 of 2 for music.with.hazem - so
#: competitor ages ARE known and labelling them "lifetime" would be
#: false. What is still missing is something else, and worth naming
#: separately: each competitor publication is observed exactly once,
#: at whatever age it happened to be, so two of them cannot be read at
#: a common anchor however well their dates are known.
LIFETIME_APPROXIMATION = (
    "LIFETIME APPROXIMATION. No publication here carries a known "
    "publication date, so every figure compares lifetime totals rather "
    "than values at a fixed age. An older post has had longer to "
    "accumulate, and nothing in this report can separate that from "
    "performance."
)

SINGLE_OBSERVATION_BASIS = (
    "SINGLE OBSERVATION PER PUBLICATION. Publication dates are known, "
    "so each age is known - but every publication was measured once, "
    "at its own age, and the ages span {span} days. Figures are "
    "therefore compared through each publication's own trailing "
    "baseline rather than at a shared anchor age, and a cross-"
    "publication comparison still carries some elapsed-exposure "
    "difference that cannot be removed from one snapshot."
)

FIXED_AGE_BASIS = (
    "Compared at a known age. Each publication's figures are read "
    "against its own trailing baseline of preceding posts, and the "
    "ages are close enough ({span} days apart at most) that elapsed "
    "exposure is not driving the comparison."
)

#: Above this spread in observation ages, a single snapshot per
#: publication stops being readable as a fixed-age comparison. The same
#: threshold `_typical` already uses for its own caveat, kept in one
#: place rather than repeated.
WIDE_AGE_SPAN_DAYS = 60


def _ages_known(obs: list) -> bool:
    """Does any publication carry both a publication and an observation
    date? Without both, an age cannot be computed and every comparison
    is a lifetime one."""
    return any(o.published_at and o.observed_at for o in obs)


def _age_span_days(obs: list) -> int | None:
    ages = [(o.observed_at - o.published_at).days for o in obs
            if o.published_at and o.observed_at]
    return (max(ages) - min(ages)) if ages else None


def comparison_basis(obs: list) -> str:
    """The sentence every comparing section carries, from the data."""
    if not _ages_known(obs):
        return LIFETIME_APPROXIMATION
    span = _age_span_days(obs)
    if span is not None and span > WIDE_AGE_SPAN_DAYS:
        return SINGLE_OBSERVATION_BASIS.format(span=span)
    return FIXED_AGE_BASIS.format(span=span if span is not None else 0)


def _video_ids(value) -> set[int]:
    """Every publication id a section's body refers to.

    Harvested from the body rather than declared alongside it, so the
    link list is exactly the set of publications the section's own
    numbers were computed from. A hand-maintained list would drift from
    the body the first time a section changed.
    """
    found: set[int] = set()
    if isinstance(value, dict):
        for k, v in value.items():
            if k == "video_id" and isinstance(v, int):
                found.add(v)
            elif k in ("video_ids", "publication_ids") and isinstance(v, list):
                found.update(i for i in v if isinstance(i, int))
            else:
                found |= _video_ids(v)
    elif isinstance(value, (list, tuple)):
        for v in value:
            found |= _video_ids(v)
    return found


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
    # The other end of the same ranking. A section that shows only its
    # top ten invites the reader to generalise from them; the bottom
    # five are the cases any such generalisation has to survive.
    sec.contradictions = [{
        "why": ("ranked lowest against its own trailing baseline by the "
                "same measure that produced the list above"),
        "video_id": p.observation.video_id,
        "external_id": p.observation.external_id,
        "relative_performance": round(p.relative_performance, 2),
        "views": p.observation.views,
        "caption": p.observation.caption,
    } for p in list(reversed(scored))[:5]]
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
    # Whichever way the split leans, the publications that lean the
    # other way are listed. "Recent posts do better" with the three
    # recent flops hidden is not a finding, it is a selection.
    scored = [p for p in points if p.relative_performance is not None]
    if scored:
        scored.sort(key=lambda p: p.relative_performance)
        recent_ids = {o.video_id for o in obs[:RECENT_N]}
        against = [p for p in scored if p.observation.video_id in recent_ids]
        sec.contradictions = [{
            "why": ("a recent publication that underperformed its own "
                    "trailing baseline, against the direction of the "
                    "split above"),
            "video_id": p.observation.video_id,
            "external_id": p.observation.external_id,
            "relative_performance": round(p.relative_performance, 2),
            "views": p.observation.views,
        } for p in against[:5]]
    return sec


def _membership(session: Session, creator_id: int) -> ReportSection:
    rows = coll.collections_for_creator(session, creator_id)
    return ReportSection(
        key="collections", title="Collections", basis="observed",
        body={"items": rows},
        caveats=([] if rows else
                 ["Not in any collection. Saving this creator to one is "
                  "what makes them part of a cohort later."]))


#: The six questions the first screen of a report has to answer, in
#: the order a creator asks them. Keys are stable; the prose is built
#: per creator from what the evidence actually supports.
SUMMARY_QUESTIONS: tuple[tuple[str, str], ...] = (
    ("performing", "How is this creator performing?"),
    ("works", "What appears to work best in their videos?"),
    ("works_less", "What appears to work less well?"),
    ("distinctive", "What makes their content distinctive?"),
    ("try_next", "What could they experiment with next?"),
    ("reliability", "How reliable are these conclusions?"),
)


def _plain_summary(session: Session, creator: Creator, obs: list,
                   points: list, tl) -> ReportSection:
    """The first screen, in English, for someone who has never heard of
    a rank correlation.

    Everything here is assembled from the same evidence the rest of the
    report shows - this is a rendering, not a second analysis. Where
    the evidence does not support an answer, the answer says so. A
    creator with four publications gets an honest "not enough yet",
    which is more useful than a confident paragraph built on four
    numbers.
    """
    from server.research import insights as ins

    answers: dict[str, dict] = {}
    n = len(obs)
    views = [o.views for o in obs if o.views is not None]
    scored = [p for p in points if p.relative_performance is not None]

    # 1. How are they performing?
    if not views:
        answers["performing"] = {
            "text": ("No view counts have been collected for this "
                     "creator, so there is nothing to say about how "
                     "they are performing."),
            "confident": False}
    else:
        import statistics as _st
        med = int(_st.median(views))
        answers["performing"] = {
            "text": (
                f"We hold {n} publications. The middle one has about "
                f"{med:,} views. Comparing publications to each other "
                f"here is only meaningful against each post's own "
                f"recent neighbours, which is how every figure below "
                f"is computed."),
            "confident": True}

    # 2 and 3. What works, what does not. Own account only: these come
    # from the association evidence, which exists for the owner's
    # Instagram population and for nobody else.
    findings = (ins.build(session) if creator.is_own_account
                and creator.platform == "instagram" else [])
    good = [i for i in findings if i.direction == "encouraging"][:3]
    bad = [i for i in findings if i.direction == "discouraging"][:3]

    if findings:
        answers["works"] = {
            "text": ("Across your Instagram videos, these openings "
                     "tended to hold viewers better. None of this is "
                     "proof that changing them would help - they are "
                     "tendencies we measured, not effects we tested."),
            "items": [i.headline for i in good],
            "confident": bool(good)}
        answers["works_less"] = {
            "text": ("And these tended to lose viewers. Same caveat: "
                     "a tendency, not a cause."),
            "items": [i.headline for i in bad],
            "confident": bool(bad)}
    else:
        why = ("We have only tested these relationships on the "
               "owner's own Instagram videos, where enough retention "
               "data exists. For this creator we hold public counts "
               "and no retention data at all, so what works cannot "
               "be answered - only what was posted.")
        answers["works"] = {"text": why, "items": [], "confident": False}
        answers["works_less"] = {"text": why, "items": [],
                                 "confident": False}

    # 4. Distinctive.
    top = sorted(scored, key=lambda p: p.relative_performance,
                 reverse=True)[:3]
    if top:
        answers["distinctive"] = {
            "text": ("Their strongest publications relative to their "
                     "own recent average are listed below; what those "
                     "have in common is a question this report cannot "
                     "answer on its own, because nothing has measured "
                     "the content of a competitor's video."
                     if not creator.is_own_account else
                     "Their strongest publications relative to their "
                     "own recent average are listed below."),
            "video_ids": [p.observation.video_id for p in top],
            "confident": True}
    else:
        answers["distinctive"] = {
            "text": ("Not enough publications in a row to compare any "
                     "of them against a trailing average, so nothing "
                     "here is distinctive in a measurable sense yet."),
            "confident": False}

    # 5. What to try.
    experiments = [i.suggested_experiment for i in findings[:2]
                   if i.suggested_experiment]
    answers["try_next"] = {
        "text": ("Each of these is a test, with the comparison fixed "
                 "before it starts. A test you interpret afterwards "
                 "tells you nothing."
                 if experiments else
                 "No experiment is worth proposing from this data. "
                 "Proposing one anyway would dress a guess as a plan."),
        "items": experiments,
        "confident": bool(experiments)}

    # 6. Reliability.
    rel = []
    if creator.is_fixture:
        rel.append("THIS IS FIXTURE DATA. Every figure is synthetic.")
    rel.append(f"{n} publications held" + (
        f", {len(scored)} of them with enough preceding posts to be "
        f"compared against their own baseline." if scored else
        ", none with enough preceding posts to compare against a "
        "baseline."))
    if findings:
        rel.append(
            f"{len(findings)} measured associations, each one "
            f"surviving a correction for testing many things at once "
            f"and an adjustment for paid promotion.")
        rel.append(
            "None of them has been through a planned experiment, so "
            "none is established as a cause.")
    if not creator.is_own_account:
        rel.append(
            "We cannot see retention, watch time or paid promotion "
            "for anyone but the owner. Public counts are all that "
            "exists here.")
    answers["reliability"] = {"text": " ".join(rel), "confident": True}

    sec = ReportSection(
        key="plain_summary", title="What we found",
        basis="observed" if views else "unavailable",
        unavailable_reason=(None if views else
                            "No metrics have been collected for this "
                            "creator, so there is nothing to summarise."),
        body={
            "questions": [
                {"key": k, "question": q, **answers.get(k, {})}
                for k, q in SUMMARY_QUESTIONS],
            "insight_count": len(findings),
        })
    sec.caveats.append(
        "This summary restates findings shown in full below. Where "
        "the two differ, the sections below are the record.")
    return sec


def _paid_vs_organic(session: Session, creator: Creator) -> ReportSection:
    """Paid and organic, separated — for the owner only.

    M1 established that the separation runs the opposite way to the way
    the blueprint assumed. Meta documents `views`, `likes`, `comments`
    and `total_interactions` as **organic only**: "interactions on ads
    containing a media object are not counted" (ADR 0008). So organic
    is not `total - paid`; organic is what the ordinary metric already
    reports, and the paid share has to be recovered from the surfaces
    around it.

    This section reports the covariate that recovery produced, and -
    just as importantly - how much of the account it covers. A
    covariate present on a third of publications controls for a third
    of the contamination, and saying so is the difference between a
    caveat and a decoration.
    """
    from server.analytics import ad_exposure

    if creator.platform != "instagram":
        return ReportSection(
            key="paid_vs_organic", title="Paid and organic",
            basis="unavailable",
            unavailable_reason=(
                "Paid exposure is resolved from Instagram's surface "
                "metrics and its Ads Insights API. This account is "
                f"{creator.platform}, which has never been boosted and "
                f"exposes no equivalent surface, so there is nothing to "
                f"separate. That is the reason TikTok is the cleaner "
                f"control in every own-account comparison."))

    exposures = ad_exposure.resolve(session, platform=creator.platform)
    cov = ad_exposure.coverage(exposures)
    window = ad_exposure.ad_window(session, platform=creator.platform)
    if not exposures:
        return ReportSection(
            key="paid_vs_organic", title="Paid and organic",
            basis="unavailable",
            unavailable_reason=(
                "No ad-exposure covariate could be resolved for any "
                "publication, so paid and organic cannot be told apart "
                "and every outcome figure on this account should be "
                "read as contaminated by an unknown amount."))

    sec = ReportSection(
        key="paid_vs_organic", title="Paid and organic", basis="observed",
        body={
            "coverage": cov,
            "ad_window": window,
            "what_the_platform_counts": (
                "Instagram's `views`, `likes`, `comments` and "
                "`total_interactions` are organic-only by Meta's own "
                "documentation. Organic is therefore NOT total minus "
                "paid - the ordinary metric already excludes ad "
                "interactions, and the paid share is recovered "
                "separately."),
        })
    sec.caveats.append(
        "Sources are never mixed. An exposure resolved from Ads "
        "Insights, from a surface residual and from a gap proxy are "
        "three different measurements, and a figure combining them "
        "would not mean anything; the covariate is used only across a "
        "homogeneous source.")
    exposed = cov.get("exposed") if isinstance(cov, dict) else None
    resolved = cov.get("resolved") if isinstance(cov, dict) else None
    if isinstance(resolved, int) and isinstance(exposed, int) and resolved:
        if exposed / resolved < 0.25:
            sec.caveats.append(
                f"Only {exposed} of {resolved} publications with a "
                f"resolved covariate were actually exposed to paid "
                f"promotion. A covariate that is zero for most of the "
                f"sample controls for very little, and a partial "
                f"correlation using it should not be read as a clean "
                f"organic estimate.")
    return sec


def _owner_repertoire(session: Session, creator: Creator) -> ReportSection:
    """What was played, for the owner, from the performance archive.

    This is the asset no competitor has: the owner's own publications
    trace back through `video_master_links` to a `local_master` to a
    `performance`, so repertoire here is a *record* rather than the
    keyword matching the general `repertoire` section does.

    It is also, today, mostly empty in the one way that matters, and
    the section says so rather than filling the gap with the keyword
    guesses sitting next to it.
    """
    from server.db.models import LocalMaster, Performance, VideoMasterLink

    rows = session.execute(
        select(Video.id, Performance.id, Performance.title,
               Performance.work, Performance.status, VideoMasterLink.status)
        .join(VideoMasterLink, VideoMasterLink.video_id == Video.id)
        .join(LocalMaster, LocalMaster.id == VideoMasterLink.master_id)
        .join(Performance, Performance.id == LocalMaster.performance_id)
        .where(Video.creator_id == creator.id,
               VideoMasterLink.status.in_(("confirmed", "variant")))).all()

    total = session.scalar(
        select(func.count(Video.id))
        .where(Video.creator_id == creator.id)) or 0

    if not rows:
        return ReportSection(
            key="owner_repertoire", title="Repertoire (from the archive)",
            basis="unavailable",
            unavailable_reason=(
                f"None of this account's {total} publications has a "
                f"confirmed link to a performance in the local archive, "
                f"so what was played is not known from the archive for "
                f"any of them. The `repertoire` section above detects "
                f"*mentions* in captions, which is a different and much "
                f"weaker claim."))

    by_perf: dict[int, dict] = {}
    for vid, pid, title, work, pstatus, lstatus in rows:
        e = by_perf.setdefault(pid, {
            "performance_id": pid, "title": title, "work": work,
            "performance_status": pstatus, "video_ids": [],
            "link_statuses": set()})
        e["video_ids"].append(vid)
        e["link_statuses"].add(lstatus)

    items = sorted(
        ({**e, "link_statuses": sorted(e["link_statuses"]),
          "publications": len(e["video_ids"])} for e in by_perf.values()),
        key=lambda e: (-e["publications"], e["performance_id"]))

    linked = len({r[0] for r in rows})
    with_work = sum(1 for e in items if e["work"])

    sec = ReportSection(
        key="owner_repertoire", title="Repertoire (from the archive)",
        basis="observed",
        body={
            "publications_linked_to_a_performance": linked,
            "publications_total": total,
            "distinct_performances": len(items),
            "performances_naming_a_work": with_work,
            "reused_performances": [e for e in items
                                    if e["publications"] > 1],
            "items": items[:40],
        })
    sec.caveats.append(
        f"{linked} of {total} publications on this account trace to a "
        f"performance. The rest are not covered, so nothing here is a "
        f"statement about the account as a whole.")
    if with_work == 0:
        sec.caveats.append(
            "The `work` field is empty on every linked performance, so "
            "these group publications that share a *performance* and "
            "cannot group them by *piece*. Grouping by repertoire needs "
            "that field populated; until then a per-work comparison is "
            "Unavailable rather than approximated from titles.")
    return sec


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
