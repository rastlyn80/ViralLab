"""Within-creator performance baselines.

The hard constraint this module is built around: for a newly discovered
creator we hold **one observation per publication, all taken at the same
moment, at wildly different publication ages**. A two-year-old post has had
two years to accumulate views; a week-old post has had a week. Ranking them
by cumulative views measures elapsed exposure, not quality, and CLAUDE.md
§3.8a forbids treating those as comparable.

So nothing here compares a publication to the creator's whole history.
Everything is **local**: a publication is compared to its `window`
immediate predecessors, which were posted close to it in time and therefore
share most of their age. The residual age difference inside a window is
computed and reported rather than assumed away.

Two further choices, both to survive the data this will actually see:

  * **Median and MAD, not mean and standard deviation.** We are hunting
    outliers; a mean is dragged by the very thing we are looking for, and a
    standard deviation computed over a distribution containing a 50x spike
    is meaningless.
  * **Log space.** Social performance is multiplicative - a good post does
    3x, not +40,000 - so ratios are the natural scale and a log median is
    the honest centre.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime

#: Publications compared against, immediately preceding the one in question.
#: Big enough for a stable median, small enough that the window spans weeks
#: rather than years.
DEFAULT_WINDOW = 8
#: Below this a median is not worth computing, and the publication is
#: reported as having no baseline rather than being compared to noise.
MIN_WINDOW = 4
#: 1.4826 * MAD estimates sigma for a normal distribution, which is the
#: standard way to put a MAD on a comparable scale to a standard deviation.
MAD_TO_SIGMA = 1.4826
#: Floor on the log-space spread of a baseline window, about +/-5%.
#:
#: Without it a window whose publications all scored identically has a MAD
#: of exactly zero, and every z-score becomes either undefined or infinite.
#: That is not a rare synthetic case: a source that rounds counts to three
#: significant figures can easily report the same value repeatedly. The
#: floor is also the honest claim - we do not know a creator's baseline to
#: better than a few percent - and it keeps a genuine spike against a flat
#: run detectable instead of silently unscoreable.
MIN_LOG_SPREAD = 0.05


@dataclass
class Observation:
    """One publication's performance, as actually observed."""

    video_id: int | None
    external_id: str
    published_at: datetime
    views: int | None
    #: When the view count was read. Differs from published_at always, and
    #: the gap between them is the age that makes the figure interpretable.
    observed_at: datetime
    caption: str | None = None
    likes: int | None = None
    comments: int | None = None
    shares: int | None = None
    saves: int | None = None
    counts_rounded: bool = False

    @property
    def age_days_at_observation(self) -> float | None:
        if not self.published_at or not self.observed_at:
            return None
        return max(0.0, (self.observed_at - self.published_at
                         ).total_seconds() / 86400.0)


@dataclass
class BaselinePoint:
    """A publication scored against its local neighbourhood."""

    index: int
    observation: Observation
    #: None when there were too few prior publications to compare against.
    baseline_views: float | None = None
    baseline_mad: float | None = None
    window_used: int = 0
    #: views / baseline. 1.0 is exactly typical for this creator, locally.
    relative_performance: float | None = None
    #: Robust z in log space. ~0 is typical; >3.5 is a strong outlier.
    robust_z: float | None = None
    #: The largest age gap, in days, between this publication and the
    #: window it was compared against. Reported because it is the size of
    #: the confound we could not remove.
    window_age_spread_days: float | None = None
    notes: list[str] = field(default_factory=list)


def _median(values: list[float]) -> float:
    s = sorted(values)
    n = len(s)
    mid = n // 2
    return s[mid] if n % 2 else (s[mid - 1] + s[mid]) / 2.0


def _mad(values: list[float], centre: float) -> float:
    return _median([abs(v - centre) for v in values])


def rolling_baseline(observations: list[Observation], *,
                     window: int = DEFAULT_WINDOW,
                     min_window: int = MIN_WINDOW) -> list[BaselinePoint]:
    """Score each publication against the `window` that preceded it.

    Trailing, not centred, and deliberately so: a centred window would let a
    publication's own neighbourhood include the posts that came *after* it,
    which is how a breakthrough gets smeared across the posts that preceded
    it and the change point lands in the wrong place.
    """
    ordered = sorted(
        [o for o in observations if o.published_at is not None],
        key=lambda o: o.published_at)

    out: list[BaselinePoint] = []
    for i, obs in enumerate(ordered):
        point = BaselinePoint(index=i, observation=obs)

        if obs.views is None:
            point.notes.append(
                "no view count reported, so this publication cannot be "
                "scored. Absent, not zero.")
            out.append(point)
            continue

        prior = [p for p in ordered[max(0, i - window):i]
                 if p.views is not None and p.views > 0]
        point.window_used = len(prior)
        if len(prior) < min_window:
            point.notes.append(
                f"only {len(prior)} prior publication(s) with a view count; "
                f"{min_window} are needed for a baseline. No comparison is "
                f"made rather than a weak one.")
            out.append(point)
            continue

        logs = [math.log(p.views) for p in prior]
        centre = _median(logs)
        mad = _mad(logs, centre)
        point.baseline_views = math.exp(centre)
        point.baseline_mad = mad

        views = max(1, obs.views)
        point.relative_performance = views / point.baseline_views
        # Floored, so an identical-valued window cannot produce an infinite
        # or undefined z. See MIN_LOG_SPREAD.
        sigma = max(mad, MIN_LOG_SPREAD) * MAD_TO_SIGMA
        point.robust_z = (math.log(views) - centre) / sigma
        if mad < MIN_LOG_SPREAD:
            point.notes.append(
                f"the local window has almost no spread, so the baseline "
                f"spread was floored at {MIN_LOG_SPREAD:.0%}. The z-score "
                f"is therefore a lower bound on how unusual this is.")

        ages = [p.age_days_at_observation for p in prior
                if p.age_days_at_observation is not None]
        own_age = obs.age_days_at_observation
        if ages and own_age is not None:
            point.window_age_spread_days = round(
                max(abs(own_age - a) for a in ages), 1)

        if obs.counts_rounded:
            point.notes.append(
                "the source rounds large counts to about three significant "
                "figures, so small differences here are not resolvable.")
        out.append(point)
    return out


@dataclass
class CreatorSummary:
    """Typical performance for a creator, with the caveats attached.

    Deliberately not called "average performance": the figures are medians
    over observations taken at a single moment across publications of very
    different ages, and the caveats are part of the result rather than a
    footnote someone may not read.
    """

    publications: int
    with_views: int
    first_published: datetime | None
    last_published: datetime | None
    observed_at: datetime | None
    median_views: float | None
    median_views_recent: float | None
    top_views: int | None
    median_likes: float | None
    median_duration_s: float | None
    #: Age range across the corpus, in days. The larger this is, the less a
    #: single median over all publications means.
    age_span_days: float | None
    counts_rounded: bool
    caveats: list[str] = field(default_factory=list)


def summarise(observations: list[Observation], *, recent_n: int = 12
              ) -> CreatorSummary:
    ordered = sorted([o for o in observations if o.published_at],
                     key=lambda o: o.published_at)
    with_views = [o for o in ordered if o.views is not None]
    views = [float(o.views) for o in with_views]
    likes = [float(o.likes) for o in ordered if o.likes is not None]

    ages = [o.age_days_at_observation for o in ordered
            if o.age_days_at_observation is not None]
    span = round(max(ages) - min(ages), 1) if len(ages) > 1 else None
    rounded = any(o.counts_rounded for o in ordered)

    caveats: list[str] = []
    if span and span > 180:
        caveats.append(
            f"These publications span {span:.0f} days of age at the moment "
            f"they were observed, so a single median across all of them "
            f"mixes posts that have had very different amounts of time to "
            f"accumulate views. Compare within a period, not across one.")
    if rounded:
        caveats.append(
            "This source rounds large counts to about three significant "
            "figures, which blurs outlier boundaries.")
    if len(with_views) < len(ordered):
        caveats.append(
            f"{len(ordered) - len(with_views)} publication(s) reported no "
            f"view count and are excluded from these figures rather than "
            f"counted as zero.")
    caveats.append(
        "Every figure here is a single observation per publication, taken "
        "at one moment. None of it is a trajectory: repeated observations "
        "over time are what would make growth visible, and for a "
        "newly-researched creator they do not exist yet.")

    recent = with_views[-recent_n:] if with_views else []
    return CreatorSummary(
        publications=len(ordered),
        with_views=len(with_views),
        first_published=ordered[0].published_at if ordered else None,
        last_published=ordered[-1].published_at if ordered else None,
        observed_at=max((o.observed_at for o in ordered), default=None),
        median_views=_median(views) if views else None,
        median_views_recent=(_median([float(o.views) for o in recent])
                             if recent else None),
        top_views=max((o.views for o in with_views), default=None),
        median_likes=_median(likes) if likes else None,
        median_duration_s=None,
        age_span_days=span,
        counts_rounded=rounded,
        caveats=caveats)
