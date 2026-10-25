"""The creator breakthrough timeline, and the one thing it must not imply.

**A timeline of current lifetime view counts arranged by publication date is
not a view-count trajectory.** It looks exactly like one. Read naively it
says "this creator grew", when all it actually shows is that older posts
have had longer to accumulate views - or, just as often, that the creator's
*recent* posts have not caught up yet and therefore look like a decline.

This module refuses to let that confusion be silent. Every timeline
declares a `mode`:

  `fixed_age`             every point is views at the same age, bracketed by
                          real observations either side. Comparable. This is
                          the honest trajectory, and it needs repeated
                          observation that only time produces.

  `lifetime_exploratory`  one lifetime figure per publication, read at
                          whatever age it happened to be. Useful for
                          spotting candidates; **not** a trajectory, and
                          labelled as such at every level - the mode, a
                          caveat list, and per-point `age_days` so the
                          confound is visible rather than described.

  `mixed`                 some publications have anchors and some do not.
                          The anchored subset is marked so the two are never
                          averaged.

Nothing here presents the detector's confidence as a probability. It is an
ordinal score from a method whose calibration has never been measured on
this corpus, and `confidence_is_calibrated` is `False` everywhere until
someone demonstrates otherwise.
"""

from __future__ import annotations

import logging
import statistics
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from server.analytics.fixed_age import ANCHORS, metric_at_age
from server.db.models import BreakthroughCandidate, Creator, Video
from server.research import baseline as bl
from server.research.service import observations_for

logger = logging.getLogger("virallab.research.timeline")

#: A sustained run must be at least this many consecutive publications
#: before it is drawn as a band. Two good posts are not a period.
MIN_RUN = 4
#: Relative-performance boundaries for a sustained band.
RUN_HIGH = 1.5
RUN_LOW = 0.6


@dataclass
class TimelinePoint:
    """One publication on the timeline."""

    video_id: int
    external_id: str
    published_at: datetime
    #: As observed. In `lifetime_exploratory` mode this is a lifetime total.
    views: int | None
    observed_at: datetime | None
    #: Age at observation. The reason two points may not be comparable, so
    #: it travels with every point rather than being mentioned once.
    age_days: int | None
    #: Views at a fixed anchor age, where real bracketing observations
    #: exist. None is the normal case and is never interpolated.
    views_at_anchor: int | None = None
    anchor_days: int | None = None
    #: "exact" when a snapshot landed on the anchor, "interpolated" when it
    #: was bracketed. Both are real; the distinction is reported anyway.
    anchor_method: str | None = None
    #: Why no anchor could be produced, so a gap is never read as a zero.
    anchor_unavailable_reason: str | None = None
    #: Performance against this creator's own trailing window. The only
    #: comparison this module considers meaningful.
    relative_performance: float | None = None
    robust_z: float | None = None
    baseline_views: float | None = None
    #: An unusually high publication *for this creator*. A fact about one
    #: post, not a claim about a trend.
    is_outlier_candidate: bool = False
    #: The located change point, if this is it.
    is_change_point: bool = False
    #: How many times this publication has been observed. 1 means no growth
    #: is visible for it, ever, until it is observed again.
    observation_count: int = 1
    caption: str | None = None
    duration_s: float | None = None
    url: str | None = None
    #: Provider thumbnail, hotlinked rather than stored. Having a URL is not
    #: permission to retain the bytes (docs/MULTIMODAL_PIPELINE.md).
    thumbnail_url: str | None = None
    media_preview_available: bool = False
    media_preview_note: str | None = None
    counts_rounded: bool = False


@dataclass
class SustainedRun:
    """A stretch where performance stayed above or below the baseline."""

    kind: str                      # "above" | "below"
    start_index: int
    end_index: int
    start_at: datetime
    end_at: datetime
    publications: int
    median_relative: float


@dataclass
class FrequencyBucket:
    """Publication frequency, from observed dates only."""

    month: str                     # YYYY-MM
    publications: int


@dataclass
class ChangePointView:
    classification: str
    publication_index: int | None
    video_id: int | None
    at: datetime | None
    median_before: float | None
    median_after: float | None
    step_ratio: float | None
    sustained: bool | None
    confidence: float | None
    #: Always False until calibration is demonstrated on this corpus.
    confidence_is_calibrated: bool = False
    confidence_note: str = (
        "This is not a probability. It is an ordinal score from a method "
        "whose calibration has never been measured on this corpus, so 0.75 "
        "does not mean a 75% chance of anything.")
    evidence: dict = field(default_factory=dict)
    missing_evidence: list[str] = field(default_factory=list)


@dataclass
class CreatorTimeline:
    creator_id: int
    platform: str
    handle: str
    display_name: str | None
    is_fixture: bool
    #: fixed_age | lifetime_exploratory | mixed
    mode: str
    mode_label: str
    points: list[TimelinePoint] = field(default_factory=list)
    change_point: ChangePointView | None = None
    outlier_candidates: int = 0
    sustained_runs: list[SustainedRun] = field(default_factory=list)
    frequency: list[FrequencyBucket] = field(default_factory=list)
    #: Publications observed more than once. The count that decides whether
    #: a real trajectory is possible at all.
    repeatedly_observed: int = 0
    anchored_points: int = 0
    age_span_days: int | None = None
    median_days_between_posts: float | None = None
    caveats: list[str] = field(default_factory=list)


MODE_LABELS = {
    "fixed_age": "Fixed-age trajectory (comparable)",
    "lifetime_exploratory": (
        "Exploratory: lifetime views at mixed ages - NOT a trajectory"),
    "mixed": "Mixed: some publications are age-anchored, most are not",
}


def build(session: Session, creator_id: int, *,
          anchor_days: int | None = None) -> CreatorTimeline:
    """Assemble the timeline for one creator."""
    creator = session.get(Creator, creator_id)
    if creator is None:
        raise ValueError(f"no such creator {creator_id}")

    obs = observations_for(session, creator_id)
    points_bl = bl.rolling_baseline(obs)

    videos = {v.id: v for v in session.scalars(
        select(Video).where(Video.creator_id == creator_id)).all()}
    counts = _observation_counts(session, creator_id)

    tl = CreatorTimeline(
        creator_id=creator.id, platform=creator.platform,
        handle=creator.handle, display_name=creator.display_name,
        is_fixture=bool(creator.is_fixture),
        mode="lifetime_exploratory",
        mode_label=MODE_LABELS["lifetime_exploratory"])

    if not obs:
        tl.caveats.append(
            "No observations for this creator yet. Research them first.")
        return tl

    anchors_wanted = [anchor_days] if anchor_days else list(ANCHORS)

    for bp in points_bl:
        o = bp.observation
        video = videos.get(o.video_id) if o.video_id else None
        age = None
        if o.published_at and o.observed_at:
            age = (o.observed_at - o.published_at).days

        pt = TimelinePoint(
            video_id=o.video_id or 0, external_id=o.external_id,
            published_at=o.published_at, views=o.views,
            observed_at=o.observed_at, age_days=age,
            relative_performance=(round(bp.relative_performance, 3)
                                  if bp.relative_performance is not None
                                  else None),
            robust_z=(round(bp.robust_z, 2)
                      if bp.robust_z is not None else None),
            baseline_views=(round(bp.baseline_views, 1)
                            if bp.baseline_views is not None else None),
            observation_count=counts.get(o.video_id, 1),
            caption=o.caption,
            duration_s=video.duration_s if video else None,
            url=_public_url(creator.platform, creator.handle, o.external_id),
            thumbnail_url=video.thumbnail_url if video else None,
            counts_rounded=_rounded(video))

        # A real fixed-age value, or nothing. `metric_at_age` requires
        # observations on BOTH sides of the anchor and refuses to
        # extrapolate, so None here means the anchor genuinely cannot be
        # computed - it is never filled with a lifetime total.
        if o.video_id:
            for a in anchors_wanted:
                val = metric_at_age(session, o.video_id, a)
                if val.available:
                    pt.views_at_anchor = val.value
                    pt.anchor_days = a
                    pt.anchor_method = val.method
                    break
                pt.anchor_unavailable_reason = val.unavailable_reason

        # Competitor media is never retained, so there is never a playable
        # local copy. Say why rather than showing a dead control.
        if creator.is_own_account and video is not None and \
                video.media_status == "available":
            pt.media_preview_available = True
        else:
            pt.media_preview_note = (
                "No stored preview: this is a third-party publication and "
                "its media is not retained. The provider thumbnail, where "
                "present, is linked rather than copied.")
        tl.points.append(pt)

    _mark_outliers(session, creator_id, tl)
    _attach_change_point(session, creator_id, tl)
    tl.sustained_runs = _runs(tl.points)
    tl.frequency = _frequency(tl.points)

    tl.anchored_points = sum(1 for p in tl.points
                             if p.views_at_anchor is not None)
    tl.repeatedly_observed = sum(1 for p in tl.points
                                 if p.observation_count > 1)
    ages = [p.age_days for p in tl.points if p.age_days is not None]
    if ages:
        tl.age_span_days = max(ages) - min(ages)
    dates = sorted(p.published_at for p in tl.points if p.published_at)
    if len(dates) >= 2:
        gaps = [(b - a).total_seconds() / 86400.0
                for a, b in zip(dates, dates[1:])]
        tl.median_days_between_posts = round(statistics.median(gaps), 1)

    # ---- mode, and the caveats that go with it -----------------------
    if tl.anchored_points == len(tl.points) and tl.points:
        tl.mode = "fixed_age"
    elif tl.anchored_points:
        tl.mode = "mixed"
    tl.mode_label = MODE_LABELS[tl.mode]

    if tl.mode != "fixed_age":
        tl.caveats.append(
            "This chart plots each publication's CURRENT LIFETIME view "
            "count against the date it was posted. That is not a historical "
            "view trajectory: older posts have had longer to accumulate, so "
            "a rising or falling line here can be produced by elapsed time "
            "alone.")
        if tl.age_span_days:
            tl.caveats.append(
                f"Publications were observed at ages spanning "
                f"{tl.age_span_days} days. Two points are only comparable "
                f"to the extent their ages are, and each point carries its "
                f"own age for that reason.")
    if tl.mode == "mixed":
        tl.caveats.append(
            f"{tl.anchored_points} of {len(tl.points)} publications have a "
            f"real fixed-age value from bracketing observations. Those are "
            f"marked; they are not averaged with the rest.")
    if not tl.repeatedly_observed:
        tl.caveats.append(
            "Every publication here has been observed exactly once, so no "
            "individual publication's growth is visible. Observing the same "
            "creator again a week from now is what changes that, and "
            "nothing else can.")
    if any(p.counts_rounded for p in tl.points):
        tl.caveats.append(
            "Some view counts came from a provider that rounds to about 3 "
            "significant figures, which blurs the boundary between an "
            "outlier and a merely good publication.")
    tl.caveats.append(
        "Outlier candidates are facts about single publications. A change "
        "point is a claim about the creator's performance level. They are "
        "drawn differently because they are different claims.")
    return tl


# ---------------------------------------------------------------------------
# internals
# ---------------------------------------------------------------------------

def _observation_counts(session: Session, creator_id: int) -> dict[int, int]:
    from sqlalchemy import func

    from server.db.models import MetricsSnapshot
    rows = session.execute(
        select(MetricsSnapshot.video_id, func.count(MetricsSnapshot.id))
        .join(Video, Video.id == MetricsSnapshot.video_id)
        .where(Video.creator_id == creator_id)
        .group_by(MetricsSnapshot.video_id)).all()
    return {vid: n for vid, n in rows}


def _rounded(video: Video | None) -> bool:
    if video is None:
        return False
    caps = ((video.provenance or {}).get("capabilities") or {})
    return bool(caps.get("views_rounded"))


def _public_url(platform: str, handle: str, external_id: str) -> str | None:
    if platform == "tiktok":
        return f"https://www.tiktok.com/@{handle}/video/{external_id}"
    if platform == "instagram":
        return f"https://www.instagram.com/reel/{external_id}/"
    return None


def _mark_outliers(session: Session, creator_id: int,
                   tl: CreatorTimeline) -> None:
    rows = session.scalars(
        select(BreakthroughCandidate).where(
            BreakthroughCandidate.creator_id == creator_id,
            BreakthroughCandidate.classification == "observed_outlier")).all()
    ids = {r.video_id for r in rows if r.video_id is not None}
    for p in tl.points:
        if p.video_id in ids:
            p.is_outlier_candidate = True
    tl.outlier_candidates = sum(1 for p in tl.points
                                if p.is_outlier_candidate)


def _attach_change_point(session: Session, creator_id: int,
                         tl: CreatorTimeline) -> None:
    cand = session.scalars(
        select(BreakthroughCandidate)
        .where(BreakthroughCandidate.creator_id == creator_id,
               BreakthroughCandidate.classification.in_(
                   ("inferred_breakthrough", "verified_trajectory_change")))
        .order_by(BreakthroughCandidate.id.desc()).limit(1)).first()
    if cand is None:
        return

    at = None
    if cand.video_id is not None:
        trigger = session.get(Video, cand.video_id)
        at = trigger.posted_at if trigger else None
        for p in tl.points:
            if p.video_id == cand.video_id:
                p.is_change_point = True

    ev = dict(cand.evidence or {})
    # `service._store_breakthroughs` writes {"missing": [...]}. Accept the
    # other shapes too rather than silently showing an inference with no
    # stated gaps, which is the one thing this field exists to prevent.
    missing = cand.missing_evidence or []
    if isinstance(missing, dict):
        missing = list(missing.get("missing")
                       or missing.get("items") or [])

    tl.change_point = ChangePointView(
        classification=cand.classification,
        publication_index=cand.publication_index, video_id=cand.video_id,
        at=at, median_before=cand.baseline_views,
        median_after=cand.median_after,
        step_ratio=ev.get("step_ratio"), sustained=cand.sustained,
        confidence=cand.confidence, evidence=ev,
        missing_evidence=[str(m) for m in missing])


def _runs(points: list[TimelinePoint]) -> list[SustainedRun]:
    """Consecutive stretches above or below the creator's own baseline."""
    runs: list[SustainedRun] = []
    current: list[tuple[int, TimelinePoint]] = []
    current_kind: str | None = None

    def flush() -> None:
        if current_kind and len(current) >= MIN_RUN:
            rels = [p.relative_performance for _i, p in current
                    if p.relative_performance is not None]
            runs.append(SustainedRun(
                kind=current_kind,
                start_index=current[0][0], end_index=current[-1][0],
                start_at=current[0][1].published_at,
                end_at=current[-1][1].published_at,
                publications=len(current),
                median_relative=round(statistics.median(rels), 2)
                if rels else 0.0))

    for i, p in enumerate(points):
        rel = p.relative_performance
        kind = None
        if rel is not None:
            if rel >= RUN_HIGH:
                kind = "above"
            elif rel <= RUN_LOW:
                kind = "below"
        if kind != current_kind:
            flush()
            current = []
            current_kind = kind
        if kind is not None:
            current.append((i, p))
    flush()
    return runs


def _frequency(points: list[TimelinePoint]) -> list[FrequencyBucket]:
    buckets: dict[str, int] = {}
    for p in points:
        if p.published_at is None:
            continue
        key = p.published_at.strftime("%Y-%m")
        buckets[key] = buckets.get(key, 0) + 1
    return [FrequencyBucket(month=k, publications=v)
            for k, v in sorted(buckets.items())]


def frequency_change(tl: CreatorTimeline) -> dict | None:
    """Did posting frequency change around the located change point?

    A behaviour change is a plausible alternative explanation for a level
    change, so it is reported next to it rather than left for the reader to
    wonder about.
    """
    if tl.change_point is None or tl.change_point.at is None:
        return None
    at = tl.change_point.at
    before = [p for p in tl.points
              if p.published_at and p.published_at < at]
    after = [p for p in tl.points
             if p.published_at and p.published_at >= at]
    if len(before) < 3 or len(after) < 3:
        return None

    def cadence(group: list[TimelinePoint]) -> float | None:
        dates = sorted(p.published_at for p in group)
        if len(dates) < 2:
            return None
        gaps = [(b - a).total_seconds() / 86400.0
                for a, b in zip(dates, dates[1:])]
        return round(statistics.median(gaps), 1)

    b, a = cadence(before), cadence(after)
    if b is None or a is None:
        return None
    return {
        "days_between_posts_before": b,
        "days_between_posts_after": a,
        "changed": abs(a - b) >= max(1.0, 0.3 * b),
        "note": ("A change in posting cadence is an alternative explanation "
                 "for a change in performance level, not a consequence of "
                 "it. Both are shown; neither is attributed."),
    }
