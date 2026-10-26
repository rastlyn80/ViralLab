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

#: Below this, a correlation is reported as uninterpretable rather than
#: reported with a confidence interval that spans everything.
MIN_N = 20


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
        out[video.id] = {
            "video": video,
            "skip_rate": skip,
            "avg_watch_ms": (float(watch_ms) if watch_ms is not None
                             else None),
            "duration_s": video.duration_s,
            "age_days": age,
            "captured_at": snap.captured_at,
        }
    return out


def analyse(session: Session, *,
            features: tuple[str, ...] = CANDIDATE_FEATURES,
            method_version: str = hookfeat.METHOD_VERSION
            ) -> RetentionReport:
    rep = RetentionReport()
    rep.method_version = method_version
    rep.outcome_note = (
        "reels_skip_rate: the share of viewers who skipped. Lower is "
        "better. Chosen over average watch time because it is bounded, it "
        "is about early abandonment - which is what a hook can affect - "
        "and because ig_reels_avg_watch_time INCLUDES REPLAYS and so is "
        "not a completion fraction.")

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

    usable = {vid: d for vid, d in outcomes.items() if vid in by_video}
    rep.n_analysed = len(usable)

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
        for vid, d in usable.items():
            v = by_video[vid].get(name)
            if v is None or d["duration_s"] is None:
                continue
            xs.append(v)
            ys.append(d["skip_rate"])
            zs.append(float(d["duration_s"]))
            ages.append(float(d["age_days"]
                              if d["age_days"] is not None else 0.0))
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
        a.effect_size_label = effect_label(a.rho)
        a.direction = ("higher value, more skipping" if a.rho > 0
                       else "higher value, less skipping")
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
