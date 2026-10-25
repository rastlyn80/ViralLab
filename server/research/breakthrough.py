"""First-version breakthrough detector.

A breakthrough is **a sustained change in a creator's own performance
level**, not a single high-view video. That distinction is the whole design,
because the easy version of this feature - "find the post with the most
views" - is wrong in a specific and seductive way: the oldest posts have had
the longest to accumulate views, so on a single snapshot the "first viral
hit" tends to be whatever was published earliest.

What this detector will and will not claim:

| classification | means | needs |
|---|---|---|
| `observed_outlier` | this post beat the creator's own local baseline | one observation per post |
| `inferred_breakthrough` | the level appears to step up here and stay up | the above, plus a sustained shift |
| `verified_trajectory_change` | the change is confirmed by genuine longitudinal evidence | repeated observations per post at comparable ages, or historical follower counts |

`verified_trajectory_change` is **not reachable from a single research
pass**, and the detector says so rather than quietly settling for the
strongest word available. Promotion needs the data to exist.

Why a step and not a spike. Age effects are smooth and monotone - older
posts drift upward gradually - so a smooth upward trend across a career is
exactly what the age confound predicts and is therefore weak evidence. A
*step*, where the level before and after differ sharply across one boundary,
is not what gradual accumulation produces. That is why the test is a
change-point test on local medians rather than a threshold on views.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from server.research.baseline import (MAD_TO_SIGMA, MIN_LOG_SPREAD,
                                      BaselinePoint, Observation, _mad,
                                      _median, rolling_baseline)

METHOD_VERSION = "breakthrough-v1"

#: Robust z above which one publication is an outlier against its local
#: baseline. 3.5 on a MAD-derived sigma is a conventional robust-outlier
#: cut; it is a decision boundary, not a probability.
OUTLIER_Z = 3.5
#: A step must move the local median by at least this factor to count. 2.0
#: is deliberately coarse: smaller shifts are indistinguishable from the age
#: drift we cannot remove.
MIN_STEP_RATIO = 2.0
#: Publications either side of a candidate boundary.
STEP_WINDOW = 6
#: The level after must stay up across at least this fraction of the
#: remaining publications, or it was a run of luck rather than a change.
SUSTAIN_FRACTION = 0.6
#: A creator with fewer publications than this cannot support the test.
MIN_PUBLICATIONS = 12
#: Fraction of the series trimmed from each end before looking for a
#: boundary. A "change point" in the first or last few publications is
#: indistinguishable from the series simply starting or ending.
EDGE_MARGIN = 0.1
#: Quantiles the log-views are clipped to before locating the boundary.
#: Winsorising keeps one lucky video from dragging the location, while
#: leaving the mean's good localisation intact.
WINSOR = 0.1


@dataclass
class Breakthrough:
    classification: str
    index: int
    external_id: str | None
    published_at: object | None
    observed_views: int | None
    baseline_views: float | None
    baseline_mad: float | None
    relative_performance: float | None
    robust_z: float | None
    window_before: int
    window_after: int
    median_before: float | None
    median_after: float | None
    step_ratio: float | None
    sustained: bool | None
    confidence: float
    evidence: dict = field(default_factory=dict)
    missing_evidence: list[str] = field(default_factory=list)


@dataclass
class BreakthroughReport:
    method_version: str
    publications_considered: int
    outliers: list[Breakthrough] = field(default_factory=list)
    #: The single best change-point candidate, if any survived.
    breakthrough: Breakthrough | None = None
    #: Why no breakthrough was reported, when none was. An empty result with
    #: no explanation is indistinguishable from a bug.
    refusals: list[str] = field(default_factory=list)
    caveats: list[str] = field(default_factory=list)


def _has_longitudinal_evidence(points: list[BaselinePoint]) -> bool:
    """Could a trajectory change be *verified* from what we hold?

    It needs more than one observation of the same publication at different
    times. A single research pass produces exactly one, so this is normally
    False - and saying so is the point.
    """
    seen: dict[str, set] = {}
    for p in points:
        seen.setdefault(p.observation.external_id, set()).add(
            p.observation.observed_at)
    return any(len(v) > 1 for v in seen.values())


def detect(observations: list[Observation], *,
           window: int = STEP_WINDOW,
           min_step_ratio: float = MIN_STEP_RATIO,
           outlier_z: float = OUTLIER_Z,
           has_follower_history: bool = False) -> BreakthroughReport:
    points = rolling_baseline(observations)
    report = BreakthroughReport(method_version=METHOD_VERSION,
                                publications_considered=len(points))

    scored = [p for p in points if p.observation.views is not None
              and p.observation.views > 0]
    if len(scored) < MIN_PUBLICATIONS:
        report.refusals.append(
            f"only {len(scored)} publication(s) carry a view count; "
            f"{MIN_PUBLICATIONS} are needed before a change point means "
            f"anything. Collect more history first.")
        return report

    longitudinal = _has_longitudinal_evidence(points)

    # ---- outliers against the local baseline -------------------------
    for p in points:
        if p.robust_z is not None and p.robust_z >= outlier_z:
            report.outliers.append(Breakthrough(
                classification="observed_outlier",
                index=p.index, external_id=p.observation.external_id,
                published_at=p.observation.published_at,
                observed_views=p.observation.views,
                baseline_views=p.baseline_views,
                baseline_mad=p.baseline_mad,
                relative_performance=p.relative_performance,
                robust_z=p.robust_z,
                window_before=p.window_used, window_after=0,
                median_before=p.baseline_views, median_after=None,
                step_ratio=None, sustained=None,
                confidence=round(
                    min(0.9, 0.4 + 0.1 * (p.robust_z - outlier_z)), 2),
                evidence={
                    "beat_local_baseline_by": round(
                        p.relative_performance or 0, 2),
                    "robust_z": round(p.robust_z, 2),
                    "window_used": p.window_used,
                    "window_age_spread_days": p.window_age_spread_days,
                    "note": "A fact about one publication against its "
                            "neighbours. Not a trajectory change.",
                },
                missing_evidence=[
                    "no repeated observations of this publication, so its "
                    "growth over time is unknown"]))

    # ---- locate the boundary, then test whether it is real ------------
    #
    # Two steps, because one statistic cannot do both jobs well.
    #
    # LOCATION uses a CUSUM scan over the whole series. A windowed median
    # test cannot locate a step: the median of a trailing window flips as
    # soon as more than half of it is past the change, so it fires about
    # half a window early - measured at index 18 for a step truly at 20.
    # CUSUM weights each split by sqrt(k(n-k)/n) and peaks at the actual
    # boundary.
    #
    # SIGNIFICANCE then uses robust medians either side of the located
    # boundary, because that is what survives outliers.
    logs = [math.log(max(1, p.observation.views)) for p in scored]
    n = len(logs)
    best = _locate(logs, window=window)

    if best is None:
        report.refusals.append(
            "no single boundary separates this creator's publications into "
            "a distinctly higher and lower period. Gradual growth and one "
            "lucky post both look like this, and neither is a breakthrough.")
    else:
        idx = best
        before = logs[max(0, idx - window):idx]
        after = logs[idx:idx + window]
        m_before, m_after = _median(before), _median(after)
        ratio = math.exp(m_after - m_before)
        spread = max(_mad(before, m_before), _mad(after, m_after),
                     MIN_LOG_SPREAD) * MAD_TO_SIGMA
        strength = (m_after - m_before) / spread
        if ratio < min_step_ratio:
            report.refusals.append(
                f"the strongest boundary moves the local median by only "
                f"{ratio:.2f}x, below the {min_step_ratio:.1f}x needed. A "
                f"smaller shift is not distinguishable from the age drift "
                f"that cannot be removed.")
            best = None

    if best is not None:
        # Sustained? Measured over everything after the boundary, not just
        # the window that found it - otherwise a six-post hot streak counts.
        tail = logs[idx:]
        above = sum(1 for v in tail if v > m_before + math.log(
            min_step_ratio) / 2)
        sustained = (len(tail) >= window
                     and above / len(tail) >= SUSTAIN_FRACTION)

        point = scored[idx]
        classification = ("verified_trajectory_change"
                          if (longitudinal and has_follower_history
                              and sustained)
                          else "inferred_breakthrough")

        missing: list[str] = []
        if not longitudinal:
            missing.append(
                "only one observation exists per publication, so no "
                "publication's own growth over time can be seen")
        if not has_follower_history:
            missing.append(
                "no historical follower counts, so no claim can be made "
                "that this publication caused account growth")
        missing.append(
            "view counts were all read at one moment across publications of "
            "different ages, so the before/after comparison carries whatever "
            "age drift remains")

        report.breakthrough = Breakthrough(
            classification=classification,
            index=idx,
            external_id=point.observation.external_id,
            published_at=point.observation.published_at,
            observed_views=point.observation.views,
            baseline_views=math.exp(m_before),
            baseline_mad=point.baseline_mad,
            relative_performance=point.relative_performance,
            robust_z=point.robust_z,
            window_before=len(before), window_after=len(after),
            median_before=math.exp(m_before),
            median_after=math.exp(m_after),
            step_ratio=round(ratio, 2),
            sustained=sustained,
            confidence=_confidence(strength, ratio, sustained, longitudinal),
            evidence={
                "median_views_before": int(math.exp(m_before)),
                "median_views_after": int(math.exp(m_after)),
                "step_ratio": round(ratio, 2),
                "separation_sigma": round(strength, 2),
                "sustained_fraction": round(above / max(1, len(tail)), 2),
                "publications_after": len(tail),
                "window": window,
                "note": ("A step in the creator's own local median, which "
                         "gradual age-driven accumulation does not produce. "
                         "Correlational: it says the level changed here, "
                         "not that this publication caused it."),
            },
            missing_evidence=missing)

    report.caveats = [
        "Every comparison is within this creator and against neighbouring "
        "publications, never against other creators or absolute view counts.",
        "Studying a creator who already succeeded cannot establish what "
        "causes success - the creators who did the same things and did not "
        "grow are not in the data. A control cohort is required before any "
        "pattern here is promoted above a hypothesis.",
        "A niche-wide shift would look identical to an individual "
        "breakthrough from inside one account. Cohort comparison is not "
        "implemented yet, so platform-level causes are not ruled out.",
    ]
    if not longitudinal:
        report.caveats.append(
            "No publication has been observed more than once, so nothing "
            "here can be a verified trajectory change. Repeat the research "
            "later and the same detector gains that evidence.")
    return report


def _locate(logs: list[float], *, window: int) -> int | None:
    """Index where the level most plausibly changes, by CUSUM scan.

    Returns the first index of the higher period, or None when the series
    is too short or no split favours an increase.

    The logs are winsorised first, so a single spectacular video shifts the
    location only slightly - the statistic is mean-based, and an unclipped
    mean would chase the spike.
    """
    n = len(logs)
    if n < max(2 * window, 8):
        return None

    ordered = sorted(logs)
    lo = ordered[int(WINSOR * (n - 1))]
    hi = ordered[int((1 - WINSOR) * (n - 1))]
    clipped = [min(max(v, lo), hi) for v in logs]

    margin = max(window // 2, int(EDGE_MARGIN * n), 2)
    best_stat, best_k = 0.0, None
    for k in range(margin, n - margin + 1):
        head, tail = clipped[:k], clipped[k:]
        mean_head = sum(head) / len(head)
        mean_tail = sum(tail) / len(tail)
        if mean_tail <= mean_head:
            continue                  # only an increase is a breakthrough
        stat = math.sqrt(k * (n - k) / n) * (mean_tail - mean_head)
        if stat > best_stat:
            best_stat, best_k = stat, k
    return best_k


def _confidence(strength: float, ratio: float, sustained: bool,
                longitudinal: bool) -> float:
    """A rough, deliberately capped score.

    Capped at 0.75 without longitudinal evidence, because no amount of
    single-snapshot separation can establish a trajectory change, and a
    number near 1.0 would imply otherwise.
    """
    c = 0.25
    c += min(0.25, strength / 20.0)
    c += min(0.2, math.log(max(ratio, 1.0)) / 10.0)
    if sustained:
        c += 0.15
    cap = 0.95 if longitudinal else 0.75
    return round(min(cap, c), 2)
