"""Do opening characteristics relate to retention? Probably not detectably.

This module is written to make a negative result easy to report and a
positive one hard to overclaim, because with the sample this corpus can
offer that is the honest balance.

**The outcome.** `reels_skip_rate` - the share of viewers who skipped - is
the retention figure a hook can plausibly move, and it is bounded 0-100.
`ig_reels_avg_watch_time` is reported too but is **not** a completion
fraction: it includes replays, and one real video in this corpus averages
3.38x its own duration. Dividing it by duration does not give "percentage
watched" and this module never treats it as such.

**The confounds that are actually controlled.** Duration is the big one: a
5-second reel and a 90-second reel cannot have comparable skip rates, and
duration also correlates with what a creator puts in the opening. Partial
Spearman correlation against duration is therefore the headline, with the
raw correlation shown beside it so the difference is visible.

**What cannot be controlled away.** Publication age, because the retention
figures are read once at whatever age the post happened to be. Repeated
performances of the same work. The fact that one creator made all of these,
so nothing here generalises beyond them. These are reported, not corrected.

**Multiple comparisons.** Testing a dozen features against one outcome will
produce a p < 0.05 by chance about half the time. Benjamini-Hochberg FDR
control is applied across the whole family and the adjusted value is what
is shown; the unadjusted one is kept visible so the correction's effect is
auditable rather than hidden.

No model is fitted here. `DEVELOPMENT_PLAN.md` and the brief both say that
establishing whether a relationship exists comes first, and on n of this
size a fitted model would be a confident restatement of noise.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field

from sqlalchemy import Float, select
from sqlalchemy.orm import Session

from server.analytics import ad_exposure
from server.db.models import Creator, MetricsSnapshot, Video, VideoFeature
from shared import hookfeat

logger = logging.getLogger("virallab.features.retention")

#: Features worth testing. Deliberately a short, pre-declared list: picking
#: them after seeing the correlations would be exactly the practice the FDR
#: correction is meant to protect against.
CANDIDATE_FEATURES = (
    "cut_count",
    "mean_abs_frame_diff",
    "camera_motion_px_per_frame",
    "brightness_mean",
    "contrast_rms",
    "saturation_mean",
    "edge_density",
    "text_region_score",
    "first_sound_at_s",
    "audio_rms_mean",
    "audio_rms_peak",
    "audio_onset_count",
    "audio_attack_time_s",
    "visual_novelty_vs_later",
    "audio_novelty_vs_later",
)

#: Real OCR measurements, tested as their own family so the FDR correction
#: is not diluted by the deterministic set.
OCR_FEATURES = (
    "ocr_word_count",
    "ocr_element_count",
    "ocr_max_text_height_frac",
    "ocr_text_duration_s",
    "ocr_text_changes",
    "ocr_first_text_at_s",
)

#: Below this, a correlation is reported as uninterpretable rather than
#: reported with a confidence interval that spans everything.
MIN_N = 20

#: The outcome family. Added in M1 because views are the least interesting
#: thing this corpus measures and the most contaminated: a boost buys views
#: directly, while what it can do to a per-viewer *rate* is only indirect,
#: through who ends up watching.
#:
#: Every one of these is organic on both sides of the ratio. Meta documents
#: `views`, `likes`, `comments`, `saved`, `shares` and `total_interactions`
#: as reporting "organic interaction metrics only; interactions on ads
#: containing a media object are not counted" (ADR 0008). So a rate built
#: from two of them is already free of paid delivery in numerator and
#: denominator alike, and ad exposure enters the analysis only as a
#: covariate on who the organic audience became.
#:
#: `direction` says which way is good. It exists so no reader has to
#: remember that a high skip rate is bad while a high save rate is good,
#: and so the sign of a correlation can be reported in words.
OUTCOMES: dict[str, dict] = {
    "reels_skip_rate": {
        #: What a HIGHER feature value goes with, and what a lower one
        #: goes with. Phrased per outcome because "higher value, better
        #: outcome" tells a reader nothing about what actually moved.
        "words": ("more skipping", "less skipping"),
        "direction": "lower_is_better",
        "unit": "percent",
        "note": ("Share who skipped in the first 3 seconds. Meta marks it "
                 "estimated and in development, and does NOT document "
                 "whether its denominator is organic — see ADR 0008 §5.6."),
    },
    "ig_reels_avg_watch_time": {
        #: What a HIGHER feature value goes with, and what a lower one
        #: goes with. Phrased per outcome because "higher value, better
        #: outcome" tells a reader nothing about what actually moved.
        "words": ("longer watching", "shorter watching"),
        "direction": "higher_is_better",
        "unit": "ms",
        "note": ("INCLUDES REPLAYS, so it is not a completion fraction and "
                 "dividing it by duration does not give percent watched. "
                 "One publication here averages 3.38x its own length."),
    },
    "saves_per_view": {
        #: What a HIGHER feature value goes with, and what a lower one
        #: goes with. Phrased per outcome because "higher value, better
        #: outcome" tells a reader nothing about what actually moved.
        "words": ("more saving", "less saving"),
        "direction": "higher_is_better",
        "unit": "ratio",
        "note": ("saved / views, both organic. The strongest intent signal "
                 "available: a save costs the viewer something."),
    },
    "shares_per_view": {
        #: What a HIGHER feature value goes with, and what a lower one
        #: goes with. Phrased per outcome because "higher value, better
        #: outcome" tells a reader nothing about what actually moved.
        "words": ("more sharing", "less sharing"),
        "direction": "higher_is_better",
        "unit": "ratio",
        "note": "shares / views, both organic.",
    },
    "comments_per_view": {
        #: What a HIGHER feature value goes with, and what a lower one
        #: goes with. Phrased per outcome because "higher value, better
        #: outcome" tells a reader nothing about what actually moved.
        "words": ("more commenting", "less commenting"),
        "direction": "higher_is_better",
        "unit": "ratio",
        "note": "comments / views, both organic.",
    },
    "likes_per_view": {
        #: What a HIGHER feature value goes with, and what a lower one
        #: goes with. Phrased per outcome because "higher value, better
        #: outcome" tells a reader nothing about what actually moved.
        "words": ("more liking", "less liking"),
        "direction": "higher_is_better",
        "unit": "ratio",
        "note": ("likes / views, both organic. Cheapest of the four and "
                 "the most inflated by audience composition."),
    },
}

#: The outcome everything before M1 was reported against. Kept as the
#: default so existing callers and the published Phase 6C figures are
#: unchanged.
DEFAULT_OUTCOME = "reels_skip_rate"


@dataclass
class Association:
    feature: str
    n: int
    #: Spearman rho against skip rate. Rank-based: the relationships here
    #: have no reason to be linear and several features are bounded.
    rho: float | None = None
    p_value: float | None = None
    #: Benjamini-Hochberg adjusted across the whole family.
    p_adjusted: float | None = None
    #: Partial Spearman, controlling for video duration.
    rho_partial_duration: float | None = None
    #: Controlling for duration AND publication age. The one to read.
    rho_partial_duration_age: float | None = None
    #: Controlling for duration, age AND ad exposure (M1). None when no
    #: single-source covariate covered enough of the sample — never a
    #: silently uncontrolled number wearing a controlled name.
    rho_partial_duration_age_ad: float | None = None
    #: How many publications the ad-controlled figure above rests on. Lower
    #: than `n` whenever the covariate could not be resolved for some.
    n_ad_controlled: int = 0
    ci_low: float | None = None
    ci_high: float | None = None
    #: Spearman rho is itself the effect size for a rank association.
    effect_size_label: str = "negligible"
    direction: str | None = None
    interpretation: str | None = None
    caveats: list[str] = field(default_factory=list)


@dataclass
class RetentionReport:
    outcome: str = "reels_skip_rate"
    outcome_note: str = ""
    n_videos: int = 0
    n_with_media: int = 0
    n_with_features: int = 0
    n_analysed: int = 0
    duration_range_s: tuple[float, float] | None = None
    age_range_days: tuple[int, int] | None = None
    skip_range: tuple[float, float] | None = None
    associations: list[Association] = field(default_factory=list)
    survivors: list[str] = field(default_factory=list)
    #: Pairwise Spearman among the survivors. Two features that measure the
    #: same thing are one finding, not two, and a list of "significant
    #: features" that does not say so is misleading by omission.
    survivor_collinearity: list[dict] = field(default_factory=list)
    method_version: str = hookfeat.METHOD_VERSION
    verdict: str = ""
    limitations: list[str] = field(default_factory=list)

    # -- ad exposure (M1) --------------------------------------------------
    #: Which single source supplied the covariate: ads_insights,
    #: surface_residual, gap_proxy, or None when nothing covered enough of
    #: the sample. Sources are never mixed (see `analytics.ad_exposure`).
    ad_covariate_source: str | None = None
    ad_covariate_n: int = 0
    #: How many of those were actually boosted. `ad_covariate_n` alone
    #: overstates the control when the covariate is zero for most of the
    #: sample — see `ad_exposure.exposed_count`.
    ad_covariate_exposed: int = 0
    #: Association between the covariate and the OUTCOME itself. This is the
    #: standing contamination diagnostic required by ADR 0008 §5.6: if the
    #: outcome is paid-inclusive it will move with ad exposure, and if it is
    #: organic-only it should not. Reported every run rather than settled
    #: once, because `reels_skip_rate` is documented as "in development".
    ad_outcome_rho: float | None = None
    ad_outcome_p: float | None = None
    ad_covariate_note: str = ""


# ---------------------------------------------------------------------------
# statistics, implemented here because scipy is not installed and one
# correlation does not justify adding it (CLAUDE.md 6)
# ---------------------------------------------------------------------------

def _rank(xs: list[float]) -> list[float]:
    """Ranks with ties averaged, which is what Spearman requires."""
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    ranks = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    n = len(xs)
    if n < 3:
        return None
    mx = sum(xs) / n
    my = sum(ys) / n
    sxy = sum((a - mx) * (b - my) for a, b in zip(xs, ys))
    sxx = sum((a - mx) ** 2 for a in xs)
    syy = sum((b - my) ** 2 for b in ys)
    if sxx <= 0 or syy <= 0:
        return None
    return sxy / math.sqrt(sxx * syy)


def spearman(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) < 3:
        return None
    return _pearson(_rank(xs), _rank(ys))


def _norm_cdf(z: float) -> float:
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


#: The Fisher z transform is infinite at |rho| = 1, so a perfect monotonic
#: association has no finite p-value or interval. Clamping just inside 1 is
#: the standard treatment and is far better than the alternative, which was
#: returning None and letting the report describe a perfect correlation as
#: "indistinguishable from noise".
_RHO_CLAMP = 1.0 - 1e-9


def spearman_p(rho: float, n: int) -> float | None:
    """Two-sided p from the Fisher z transform.

    An approximation, and labelled as one. For n near 40 it is close
    enough to decide "this is indistinguishable from noise", which is the
    only decision being made here.
    """
    if rho is None or n < 10:
        return None
    rho = max(-_RHO_CLAMP, min(_RHO_CLAMP, rho))
    z = 0.5 * math.log((1 + rho) / (1 - rho)) * math.sqrt((n - 3) / 1.06)
    return float(min(1.0, 2.0 * (1.0 - _norm_cdf(abs(z)))))


def fisher_ci(rho: float, n: int, conf: float = 0.95
              ) -> tuple[float, float] | None:
    if rho is None or n < 10:
        return None
    rho = max(-_RHO_CLAMP, min(_RHO_CLAMP, rho))
    z = 0.5 * math.log((1 + rho) / (1 - rho))
    se = 1.0 / math.sqrt((n - 3) / 1.06)
    # 1.96 for 95%; the only level offered, because offering more would
    # imply a precision this approximation does not have.
    crit = 1.959964
    lo, hi = z - crit * se, z + crit * se
    return (round(math.tanh(lo), 3), round(math.tanh(hi), 3))


def partial_spearman(xs: list[float], ys: list[float],
                     zs: list[float]) -> float | None:
    """Spearman between x and y with z partialled out, on ranks."""
    rxy = spearman(xs, ys)
    rxz = spearman(xs, zs)
    ryz = spearman(ys, zs)
    if None in (rxy, rxz, ryz):
        return None
    denom = math.sqrt(max(1e-12, (1 - rxz ** 2) * (1 - ryz ** 2)))
    if denom <= 0:
        return None
    return float(max(-1.0, min(1.0, (rxy - rxz * ryz) / denom)))


def partial_spearman2(xs: list[float], ys: list[float],
                      z1: list[float], z2: list[float]) -> float | None:
    """x against y, controlling for BOTH z1 and z2.

    Duration is the obvious confound; publication age is the one that is
    easy to forget. This creator's style changed over 549 days and the
    platform's behaviour may have too, so a feature that drifted over time
    will correlate with any outcome that also drifted - without either
    causing the other. Recursive second-order partial on ranks.
    """
    def pc(a, b, c):
        return partial_spearman(a, b, c)

    r_xy_z1 = pc(xs, ys, z1)
    r_xz2_z1 = pc(xs, z2, z1)
    r_yz2_z1 = pc(ys, z2, z1)
    if None in (r_xy_z1, r_xz2_z1, r_yz2_z1):
        return None
    denom = math.sqrt(max(1e-12,
                          (1 - r_xz2_z1 ** 2) * (1 - r_yz2_z1 ** 2)))
    if denom <= 0:
        return None
    return float(max(-1.0, min(1.0,
                               (r_xy_z1 - r_xz2_z1 * r_yz2_z1) / denom)))


def partial_spearman_n(xs: list[float], ys: list[float],
                       controls: list[list[float]]) -> float | None:
    """x against y controlling for any number of variables, on ranks.

    `partial_spearman` and `partial_spearman2` above are the one- and
    two-control cases and are left as they are, because Phase 6C's published
    figures were produced by them and a rewrite would silently change
    numbers that are already in a document.

    This generalises them by the same recursion. It is exponential in the
    number of controls — 3^k correlations — which is irrelevant at k=3 and
    is the reason it is not offered for more. The recursion is the textbook
    identity:

        r(x,y | Z,z) = (r(x,y|Z) - r(x,z|Z)·r(y,z|Z))
                       / sqrt((1 - r(x,z|Z)²)(1 - r(y,z|Z)²))

    A control that is nearly collinear with x or y drives the denominator
    toward zero and the result toward ±1. That is a real property of partial
    correlation rather than a bug, and the caller reports the controls it
    used so a reader can judge it.
    """
    if not controls:
        return spearman(xs, ys)
    z, rest = controls[-1], controls[:-1]
    r_xy = partial_spearman_n(xs, ys, rest)
    r_xz = partial_spearman_n(xs, z, rest)
    r_yz = partial_spearman_n(ys, z, rest)
    if None in (r_xy, r_xz, r_yz):
        return None
    denom = math.sqrt(max(1e-12, (1 - r_xz ** 2) * (1 - r_yz ** 2)))
    if denom <= 0:
        return None
    return float(max(-1.0, min(1.0, (r_xy - r_xz * r_yz) / denom)))


def benjamini_hochberg(ps: list[float | None]) -> list[float | None]:
    """FDR-adjusted p-values, order preserved."""
    idx = [i for i, p in enumerate(ps) if p is not None]
    if not idx:
        return list(ps)
    m = len(idx)
    ordered = sorted(idx, key=lambda i: ps[i])
    out: list[float | None] = list(ps)
    prev = 1.0
    for rank, i in enumerate(reversed(ordered), start=1):
        k = m - rank + 1
        adj = min(prev, ps[i] * m / k)
        out[i] = round(min(1.0, adj), 4)
        prev = adj
    return out


def effect_label(rho: float | None) -> str:
    """Plain words for a rank correlation's size."""
    if rho is None:
        return "unknown"
    a = abs(rho)
    if a < 0.1:
        return "negligible"
    if a < 0.3:
        return "small"
    if a < 0.5:
        return "moderate"
    return "large"


# ---------------------------------------------------------------------------
# the analysis
# ---------------------------------------------------------------------------

def _outcome_rows(session: Session) -> dict[int, dict]:
    """Latest retention observation per own-account Instagram publication."""
    rows = session.execute(
        select(Video, MetricsSnapshot)
        .join(Creator, Creator.id == Video.creator_id)
        .join(MetricsSnapshot, MetricsSnapshot.video_id == Video.id)
        .where(Video.platform == "instagram",
               Creator.is_own_account.is_(True),
               MetricsSnapshot.raw.has_key("reels_skip_rate"))  # noqa: W601
        .order_by(Video.id, MetricsSnapshot.captured_at.desc())).all()
    out: dict[int, dict] = {}
    for video, snap in rows:
        if video.id in out:
            continue
        raw = snap.raw or {}
        try:
            skip = float(raw.get("reels_skip_rate"))
        except (TypeError, ValueError):
            continue
        age = None
        if video.posted_at and snap.captured_at:
            age = int((snap.captured_at - video.posted_at).days)
        watch_ms = raw.get("ig_reels_avg_watch_time")
        row = {
            "video": video,
            "skip_rate": skip,
            "avg_watch_ms": (float(watch_ms) if watch_ms is not None
                             else None),
            "duration_s": video.duration_s,
            "age_days": age,
            "captured_at": snap.captured_at,
            "views": snap.views,
            "total_views": snap.total_views,
        }
        # Per-viewer engagement rates, organic over organic (ADR 0008). A
        # zero denominator yields None rather than a division error or a
        # zero rate: a publication nobody saw has no rate, and recording one
        # as 0.0 would drag every correlation toward it.
        views = float(snap.views) if snap.views else 0.0
        for name, col in (("saves_per_view", snap.saves),
                          ("shares_per_view", snap.shares),
                          ("comments_per_view", snap.comments),
                          ("likes_per_view", snap.likes)):
            row[name] = (float(col) / views
                         if views > 0 and col is not None else None)
        row["reels_skip_rate"] = skip
        row["ig_reels_avg_watch_time"] = row["avg_watch_ms"]
        out[video.id] = row
    return out


def analyse(session: Session, *,
            features: tuple[str, ...] = CANDIDATE_FEATURES,
            method_version: str = hookfeat.METHOD_VERSION,
            outcome: str = DEFAULT_OUTCOME,
            control_ad_exposure: bool = True,
            population: set[int] | None = None,
            ) -> RetentionReport:
    """Associations between opening features and one outcome.

    `population` restricts the analysis to a given set of video ids. It
    exists so that a change in the *sample* and a change in the *controls*
    can be told apart. M1 did both at once — it added the ad covariate and,
    by collecting retention metrics for the whole back catalogue, grew the
    population from 48 to roughly 190 — and a reader given only the new
    numbers could not say which change moved them.
    """
    if outcome not in OUTCOMES:
        raise ValueError(f"unknown outcome: {outcome!r}")
    rep = RetentionReport()
    rep.outcome = outcome
    rep.method_version = method_version
    rep.outcome_note = (
        "reels_skip_rate: the share of viewers who skipped. Lower is "
        "better. Chosen over average watch time because it is bounded, it "
        "is about early abandonment - which is what a hook can affect - "
        "and because ig_reels_avg_watch_time INCLUDES REPLAYS and so is "
        "not a completion fraction."
        if outcome == DEFAULT_OUTCOME
        else f"{outcome}: {OUTCOMES[outcome]['note']} "
             f"({OUTCOMES[outcome]['direction'].replace('_', ' ')})")

    outcomes = _outcome_rows(session)
    rep.n_videos = len(outcomes)
    rep.n_with_media = sum(
        1 for d in outcomes.values()
        if d["video"].media_status == "available")

    rows = session.scalars(
        select(VideoFeature).where(
            VideoFeature.method_version == method_version,
            VideoFeature.feature.in_(list(features)),
            VideoFeature.status == "ok")).all()
    by_video: dict[int, dict] = {}
    for row in rows:
        val = row.effective_value
        if isinstance(val, bool):
            val = float(val)
        if isinstance(val, (int, float)):
            by_video.setdefault(row.video_id, {})[row.feature] = float(val)
    rep.n_with_features = len(by_video)

    usable = {vid: d for vid, d in outcomes.items()
              if vid in by_video and d.get(outcome) is not None
              and (population is None or vid in population)}
    rep.n_analysed = len(usable)

    # ---- the ad-exposure covariate (M1, BLUEPRINT §1.5) -----------------
    ad_values: dict[int, float] = {}
    if control_ad_exposure and usable:
        exposures = ad_exposure.resolve(session, platform="instagram",
                                        video_ids=list(usable))
        src, ad_values = ad_exposure.homogeneous(exposures, list(usable))
        rep.ad_covariate_source = src
        rep.ad_covariate_n = len(ad_values)
        rep.ad_covariate_exposed = ad_exposure.exposed_count(ad_values)
        cov = ad_exposure.coverage(
            {k: v for k, v in exposures.items() if k in usable})
        if src is None:
            rep.ad_covariate_note = (
                "No ad-exposure covariate: no single source could be "
                "resolved for any publication in this sample. Results below "
                "are UNCONTROLLED for paid exposure.")
        else:
            rep.ad_covariate_note = (
                f"Covariate source: {src}, covering {len(ad_values)} of "
                f"{len(usable)} analysed publications. Sources are never "
                f"mixed; coverage by source was {cov['by_source']} with "
                f"{cov['unknown']} unresolved. Of the covered "
                f"publications, {rep.ad_covariate_exposed} were actually "
                f"boosted — the control can only do work where the "
                f"covariate varies.")

    if rep.n_analysed:
        durs = [d["duration_s"] for d in usable.values()
                if d["duration_s"] is not None]
        ages = [d["age_days"] for d in usable.values()
                if d["age_days"] is not None]
        skips = [d["skip_rate"] for d in usable.values()]
        rep.duration_range_s = (round(min(durs), 1), round(max(durs), 1)) \
            if durs else None
        rep.age_range_days = (min(ages), max(ages)) if ages else None
        rep.skip_range = (round(min(skips), 1), round(max(skips), 1))

        # The contamination diagnostic. Run before any feature is tested,
        # because if this is large the feature results below are about the
        # audience the ad bought rather than about the openings.
        if len(ad_values) >= MIN_N:
            pairs = [(ad_values[v], usable[v][outcome]) for v in usable
                     if v in ad_values]
            r = spearman([a for a, _ in pairs], [b for _, b in pairs])
            if r is not None:
                rep.ad_outcome_rho = round(r, 3)
                rep.ad_outcome_p = spearman_p(r, len(pairs))

    if rep.n_analysed < MIN_N:
        rep.verdict = (
            f"Not analysed: {rep.n_analysed} publications have both a "
            f"retention figure and extracted features, below the minimum "
            f"of {MIN_N}. A correlation on this many points has a "
            f"confidence interval wide enough to include almost any "
            f"conclusion, so none is offered.")
        rep.limitations = _limitations(rep, usable)
        return rep

    # ---- the tests ---------------------------------------------------
    assocs: list[Association] = []
    for name in features:
        xs, ys, zs, ages = [], [], [], []
        # Collected in parallel with the above, then filtered down to the
        # covered subset. Dropping covariate-less rows from the headline
        # correlation too would quietly change which publications the
        # uncontrolled figure describes, and then the two columns would not
        # be comparable — which is the whole point of printing both.
        ad_xs, ad_ys, ad_zs, ad_ages, ad_cov = [], [], [], [], []
        for vid, d in usable.items():
            v = by_video[vid].get(name)
            if v is None or d["duration_s"] is None:
                continue
            xs.append(v)
            ys.append(d[outcome])
            zs.append(float(d["duration_s"]))
            ages.append(float(d["age_days"]
                              if d["age_days"] is not None else 0.0))
            if vid in ad_values:
                ad_xs.append(v)
                ad_ys.append(d[outcome])
                ad_zs.append(zs[-1])
                ad_ages.append(ages[-1])
                ad_cov.append(ad_values[vid])
        a = Association(feature=name, n=len(xs))
        if len(xs) < MIN_N or len(set(xs)) < 3:
            a.interpretation = (
                f"Not tested: {len(xs)} usable values and "
                f"{len(set(xs))} distinct levels. A feature that barely "
                f"varies in this corpus cannot explain variation in "
                f"anything.")
            assocs.append(a)
            continue
        a.rho = round(spearman(xs, ys) or 0.0, 3)
        a.p_value = spearman_p(a.rho, len(xs))
        a.ci_low, a.ci_high = (fisher_ci(a.rho, len(xs))
                               or (None, None))
        pd = partial_spearman(xs, ys, zs)
        a.rho_partial_duration = round(pd, 3) if pd is not None else None
        pda = partial_spearman2(xs, ys, zs, ages)
        a.rho_partial_duration_age = (round(pda, 3) if pda is not None
                                      else None)
        a.n_ad_controlled = len(ad_xs)
        if len(ad_xs) >= MIN_N:
            pdaa = partial_spearman_n(ad_xs, ad_ys, [ad_zs, ad_ages, ad_cov])
            a.rho_partial_duration_age_ad = (round(pdaa, 3)
                                             if pdaa is not None else None)
        a.effect_size_label = effect_label(a.rho)
        up, down = OUTCOMES[outcome]["words"]
        a.direction = f"higher value, {up if a.rho > 0 else down}"
        assocs.append(a)

    adjusted = benjamini_hochberg([a.p_value for a in assocs])
    for a, p in zip(assocs, adjusted):
        a.p_adjusted = p
        if a.rho is None:
            continue
        if p is not None and p < 0.05:
            a.interpretation = (
                f"Survives FDR correction (adjusted p={p}). With n={a.n} "
                f"from one creator this is a hypothesis worth testing "
                f"deliberately, not a finding to act on.")
        else:
            a.interpretation = (
                f"Indistinguishable from noise after correcting for "
                f"{len(assocs)} comparisons"
                + (f" (adjusted p={p})" if p is not None else "") + ".")
        if (a.ci_low is not None and a.ci_high is not None
                and a.ci_low < 0 < a.ci_high):
            a.caveats.append(
                f"The 95% interval [{a.ci_low}, {a.ci_high}] contains zero, "
                f"so the direction of this relationship is not established.")
        if (a.rho_partial_duration is not None
                and abs(a.rho_partial_duration) < abs(a.rho) * 0.6):
            a.caveats.append(
                f"Controlling for duration drops the correlation from "
                f"{a.rho} to {a.rho_partial_duration}, so much of the raw "
                f"association is length, not opening content.")
        if (a.rho_partial_duration_age is not None
                and abs(a.rho_partial_duration_age) < abs(a.rho) * 0.6):
            a.caveats.append(
                f"Controlling for duration AND publication age drops it "
                f"further to {a.rho_partial_duration_age}. This creator's "
                f"style changed over the period covered, so a feature that "
                f"drifted with time will track any outcome that also "
                f"drifted, without either causing the other.")
        if a.rho_partial_duration_age_ad is None and a.rho is not None:
            a.caveats.append(
                f"NOT controlled for ad exposure: only {a.n_ad_controlled} "
                f"of {a.n} publications had a single-source covariate, "
                f"below the minimum of {MIN_N}. This association is "
                f"uncontrolled for paid delivery.")
        elif (a.rho_partial_duration_age_ad is not None
                and abs(a.rho_partial_duration_age_ad) < abs(a.rho) * 0.6):
            a.caveats.append(
                f"Adding the ad-exposure control drops it to "
                f"{a.rho_partial_duration_age_ad} on "
                f"n={a.n_ad_controlled}. Much of this association is which "
                f"publications were boosted, not what was in the opening. "
                f"Reported as having weakened, per CLAUDE.md §3.7.")

    rep.associations = sorted(
        assocs, key=lambda a: (a.p_adjusted if a.p_adjusted is not None
                               else 1.0))
    rep.survivors = [a.feature for a in rep.associations
                     if a.p_adjusted is not None and a.p_adjusted < 0.05]

    # Are the survivors actually one finding?
    if len(rep.survivors) > 1:
        for i, fa in enumerate(rep.survivors):
            for fb in rep.survivors[i + 1:]:
                pa, pb = [], []
                for vid in usable:
                    va, vb = by_video[vid].get(fa), by_video[vid].get(fb)
                    if va is None or vb is None:
                        continue
                    pa.append(va)
                    pb.append(vb)
                r = spearman(pa, pb)
                if r is None:
                    continue
                rep.survivor_collinearity.append({
                    "a": fa, "b": fb, "rho": round(r, 3), "n": len(pa),
                    "note": (
                        f"{fa} and {fb} correlate at {r:.2f} with each "
                        f"other. They are close to the same measurement, "
                        f"so this is one finding reported twice, not two "
                        f"independent ones."
                        if abs(r) >= 0.7 else
                        f"{fa} and {fb} correlate at {r:.2f}; largely "
                        f"independent of each other.")})

    if rep.survivors:
        rep.verdict = (
            f"{len(rep.survivors)} of {len(assocs)} tested features survive "
            f"FDR correction: {', '.join(rep.survivors)}. On n={rep.n_analysed} "
            f"from a single creator, these are hypotheses to test "
            f"deliberately, not established effects and not grounds for a "
            f"recommendation.")
        collinear = [c for c in rep.survivor_collinearity
                     if abs(c["rho"]) >= 0.7]
        if collinear:
            rep.verdict += (
                f" {collinear[0]['a']} and {collinear[0]['b']} correlate at "
                f"{collinear[0]['rho']} with each other, so this is closer "
                f"to one finding than to two.")
    else:
        rep.verdict = (
            f"No opening characteristic shows a detectable association with "
            f"skip rate on this corpus: {len(assocs)} features tested on "
            f"n={rep.n_analysed}, none surviving correction for multiple "
            f"comparisons. This is a real result, not a failure - it says "
            f"the measurable opening features do not explain retention "
            f"here, and it is what stops a recommendation engine being "
            f"built on noise.")
    rep.limitations = _limitations(rep, usable)
    return rep


def _limitations(rep: RetentionReport, usable: dict) -> list[str]:
    out = [
        f"n = {rep.n_analysed}. Of {rep.n_videos} publications with a "
        f"retention figure, {rep.n_with_media} have a preserved file and "
        f"{rep.n_with_features} have extracted features.",
        "One creator, one niche, one account. Nothing here generalises to "
        "anyone else, and it is not meant to.",
        "Retention was read once, at whatever age each publication "
        "happened to be. It is not a fixed-age measurement and the ages "
        "are not comparable.",
        "Correlational. Even a surviving association is a relationship "
        "between a creative choice and an outcome, not evidence that "
        "changing the choice would change the outcome. Only the Phase 8 "
        "pre-registered experiment can address that.",
        "ig_reels_avg_watch_time includes replays and is not used as a "
        "completion fraction anywhere in this analysis.",
    ]
    if rep.age_range_days:
        out.append(
            f"Publication ages span {rep.age_range_days[0]}-"
            f"{rep.age_range_days[1]} days, so the oldest posts have had "
            f"far longer to accumulate the views these rates are "
            f"computed over.")
    if rep.duration_range_s:
        out.append(
            f"Durations span {rep.duration_range_s[0]}-"
            f"{rep.duration_range_s[1]}s. Duration is partialled out of "
            f"the headline figure, but a 5s reel and a 90s reel are "
            f"different formats, not two points on one scale.")
    out.append(
        "Repeated performances of the same work are not deduplicated. Two "
        "takes of one piece are not independent observations, which makes "
        "the effective sample smaller than n suggests.")
    return out


# ---------------------------------------------------------------------------
# Phase 6B: does real OCR explain the proxy that survived in Phase 6A?
# ---------------------------------------------------------------------------

@dataclass
class ProxyExplanation:
    """Whether `text_region_score` was measuring what it was named for.

    Phase 6A found `text_region_score` associated with skip rate and called
    it a text signal, because that is what an edge-density-in-a-horizontal-
    band statistic was built to approximate. Phase 6B can check that
    directly, and the answer changes the interpretation completely.
    """

    n: int = 0
    proxy_rho: float | None = None
    #: {ocr_feature: correlation between the proxy and that measurement}
    proxy_vs_ocr: dict = field(default_factory=dict)
    #: {ocr_feature: proxy-vs-skip correlation after controlling for it}
    controlled_rho: dict = field(default_factory=dict)
    explained: bool = False
    verdict: str = ""
    caveats: list[str] = field(default_factory=list)


def explain_proxy(session: Session, *,
                  proxy: str = "text_region_score") -> ProxyExplanation:
    """Test whether the OCR measurements account for the proxy's signal."""
    out = ProxyExplanation()
    outcomes = {vid: d for vid, d in _outcome_rows(session).items()
                if d["video"].media_status == "available"}

    def values(feature: str) -> dict:
        got = {}
        for row in session.scalars(select(VideoFeature).where(
                VideoFeature.feature == feature,
                VideoFeature.status == "ok")).all():
            v = row.effective_value
            if isinstance(v, bool):
                v = float(v)
            if isinstance(v, (int, float)):
                got[row.video_id] = float(v)
        return got

    proxy_vals = values(proxy)
    common = [vid for vid in outcomes if vid in proxy_vals]
    out.n = len(common)
    if out.n < MIN_N:
        out.verdict = (
            f"Only {out.n} publications have both the proxy and a retention "
            f"figure; too few to test.")
        return out

    xs = [proxy_vals[v] for v in common]
    ys = [outcomes[v]["skip_rate"] for v in common]
    out.proxy_rho = round(spearman(xs, ys) or 0.0, 3)

    weakened = []
    for feature in OCR_FEATURES:
        vals = values(feature)
        zs = [vals.get(v) for v in common]
        if any(z is None for z in zs) or len(set(zs)) < 3:
            continue
        out.proxy_vs_ocr[feature] = round(spearman(xs, zs) or 0.0, 3)
        pc = partial_spearman(xs, ys, zs)
        if pc is None:
            continue
        out.controlled_rho[feature] = round(pc, 3)
        if abs(pc) < abs(out.proxy_rho) * 0.6:
            weakened.append(feature)

    out.explained = bool(weakened)
    strongest_link = max((abs(v) for v in out.proxy_vs_ocr.values()),
                         default=0.0)

    if out.explained:
        out.verdict = (
            f"Controlling for {', '.join(weakened)} substantially weakens "
            f"the proxy's association, so it was at least partly measuring "
            f"on-screen text after all.")
    else:
        out.verdict = (
            f"**The proxy was not measuring text.** `{proxy}` correlates at "
            f"most {strongest_link:.2f} with any real OCR measurement, and "
            f"controlling for all of them leaves its association with skip "
            f"rate essentially unchanged (rho {out.proxy_rho}). Whatever it "
            f"detects - dense, high-contrast vertical strokes - is "
            f"something other than text, and that something is what tracks "
            f"retention here. Candidate explanations include the keyboard "
            f"itself, printed sheet music, and general visual density of "
            f"the opening frame. Distinguishing them needs visual "
            f"recognition, which this phase declares but cannot yet run.")
        out.caveats.append(
            "The name `text_region_score` is now misleading and is kept "
            "only because renaming a stored feature would break the "
            "comparison with the Phase 6A evidence. Its note records what "
            "it actually measures.")

    out.caveats.append(
        "No OCR measurement - word count, element count, text height, "
        "on-screen duration or mid-hook changes - shows a detectable "
        "association with skip rate in this corpus.")
    # Computed, not asserted. This read "96% ... (46 of 48)" as a literal,
    # which was true of the 48-publication sample it was written against
    # and became wrong the moment OCR was backfilled to 258. A hardcoded
    # sample statistic is a stale claim waiting to happen.
    present = values("ocr_text_present")
    n_present = len([v for vid, v in present.items()
                     if vid in outcomes and v])
    n_known = len([vid for vid in present if vid in outcomes])
    if n_known:
        pct = round(100.0 * n_present / n_known)
        out.caveats.append(
            f"{pct}% of these publications carry on-screen text "
            f"({n_present} of {n_known}), so the presence of text has "
            f"{'almost no' if pct >= 90 or pct <= 10 else 'limited'} "
            f"variance to explain anything with.")
    return out


def cluster_by_performance(session: Session) -> dict:
    """Collapse repeated takes of one performance to one observation.

    Two takes of the same piece are not independent evidence. This reports
    the association with and without that collapse so the difference is
    visible rather than asserted.
    """
    from sqlalchemy import text as _sql

    outcomes = {vid: d for vid, d in _outcome_rows(session).items()
                if d["video"].media_status == "available"}
    rows = session.execute(_sql("""
        select l.video_id, m.performance_id
        from video_master_links l
        join local_masters m on m.id = l.master_id
        where m.performance_id is not null
          and l.status in ('auto','confirmed','variant')
    """)).all()
    perf_of = {vid: pid for vid, pid in rows}

    groups: dict = {}
    for vid in outcomes:
        key = ("perf", perf_of[vid]) if vid in perf_of else ("solo", vid)
        groups.setdefault(key, []).append(vid)

    return {
        "videos": len(outcomes),
        "independent_units": len(groups),
        "units_with_repeats": sum(1 for v in groups.values() if len(v) > 1),
        "note": (
            f"{len(outcomes)} publications collapse to {len(groups)} "
            f"independent units once repeated takes of the same performance "
            f"are grouped. The effective sample is the smaller number."),
    }
