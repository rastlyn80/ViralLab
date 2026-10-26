"""Phase 6C Part 5 - what `text_region_score` is actually measuring.

Phase 6A found that `text_region_score`, the deterministic caption proxy,
correlates 0.01 with `ocr_word_count`. It was never measuring text. Part 5
asks what it *is* measuring, and whether that thing relates to retention.

The brief names six candidates: keyboard visibility, sheet music, hand
close-ups, shot distance, general visual density, background complexity.
**Four of the six are not answerable with the available vision model.** The
Phase 6C benchmark (docs/benchmarks/6c-vision-benchmark.md) measured
`piano_or_keyboard_visible` and `hands_visible` at specificity 0.000 - on
every opening that contained no piano, the model said there was one - and
`sheet_music_visible` at 0.235, `shot_distance` at 0.304 against a 0.500
baseline. Correlating a near-constant, confabulated feature against
retention would manufacture a relationship out of the model's own bias, so
those four are withheld here with their measured reason attached rather
than quietly included.

The two that *are* answerable are answered properly, because visual density
and background complexity are deterministic image statistics and need no
model: `edge_density` from `hookfeat` and the `hook-background-v1`
measurements from `hookbg`.

Two arms, reported separately:

  A. Is `text_region_score` associated with visual structure? If the proxy
     is really measuring background busyness, this is where it shows.
  B. Is any of that structure associated with `reels_skip_rate`? Controlled
     for duration and publication age, and repeated with the repeated-take
     collapse applied.

A null result in either arm is a result. CLAUDE.md §3 forbids dressing one
up as anything else.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from server.db.models import VideoFeature
from server.analytics import ad_exposure
from server.features.retention import (MIN_N, _outcome_rows,
                                       partial_spearman,
                                       benjamini_hochberg, effect_label,
                                       fisher_ci, partial_spearman2,
                                       partial_spearman_n, spearman,
                                       spearman_p)
from shared import hookbg, hookfeat

logger = logging.getLogger("virallab.features.visual_structure")

PROXY = "text_region_score"

#: Deterministic visual-structure measures, with the method version each
#: comes from. Nothing here depends on a model.
STRUCTURE_FEATURES: tuple[tuple[str, str], ...] = (
    ("edge_density", hookfeat.METHOD_VERSION),
    ("contrast_rms", hookfeat.METHOD_VERSION),
    ("brightness_mean", hookfeat.METHOD_VERSION),
    ("saturation_mean", hookfeat.METHOD_VERSION),
    ("background_static_fraction", hookbg.METHOD_VERSION),
    ("background_edge_density", hookbg.METHOD_VERSION),
    ("background_grey_entropy", hookbg.METHOD_VERSION),
    ("subject_edge_density", hookbg.METHOD_VERSION),
    ("background_subject_edge_ratio", hookbg.METHOD_VERSION),
)

#: The one model-derived field the benchmark cleared for use: 1.000
#: accuracy, specificity 1.000, lift +0.083 over the majority baseline.
VISION_FEATURES: tuple[str, ...] = (
    "vision_text_overlay_present",
)

#: Withheld, with the measured reason. These are stored in the database and
#: visible in the interface; what they may not do is enter an analysis.
WITHHELD_VISION: dict[str, str] = {
    "vision_piano_or_keyboard_visible":
        "specificity 0.000 (0 of 3 correct denials) - the model reported a "
        "piano in all three openings that contained none, including a man "
        "walking through a garden",
    "vision_hands_visible":
        "specificity 0.000 (0 of 3 correct denials)",
    "vision_sheet_music_visible":
        "specificity 0.235 (4 of 17) - reported sheet music in 13 openings "
        "that had none",
    "vision_shot_distance":
        "accuracy 0.304 against a 0.500 majority baseline, lift -0.196; "
        "answered 'medium' 16 times of 23 and missed all six wide shots",
    "vision_performer_visible":
        "the reference label is true for all 24 videos, so the field "
        "carries no variance and nothing can be learned from it",
}

#: Controls. Duration because a longer video has more opportunity to be
#: skipped; age because the metric is captured at one moment and older
#: publications have had longer to accumulate.
CONTROLS = ("duration_s", "age_days")


@dataclass
class Association:
    feature: str
    method_version: str = ""
    n: int = 0
    rho: float | None = None
    p: float | None = None
    q: float | None = None
    ci: tuple[float, float] | None = None
    effect: str = ""
    #: Controlled for duration and age, where both are available.
    partial_rho: float | None = None
    partial_n: int = 0
    #: Same association after collapsing repeated takes of one performance.
    clustered_rho: float | None = None
    clustered_n: int = 0
    #: Controlling for duration, age AND ad exposure (M1). None where the
    #: covariate did not cover enough of the sample; never a figure that
    #: quietly omitted the control it is named for.
    partial_ad_rho: float | None = None
    partial_ad_n: int = 0
    unavailable: int = 0
    note: str = ""


@dataclass
class Mediation:
    """Which of two correlated features carries the association.

    `text_region_score` and `background_edge_density` measure overlapping
    things. If the proxy's apparent relationship with retention is really
    the background's, then controlling for the background removes it and
    controlling for the proxy does not. Running it both ways is the point:
    one direction alone cannot distinguish "explained by" from "shares
    variance with".
    """

    a: str = ""
    b: str = ""
    outcome: str = "reels_skip_rate"
    n: int = 0
    rho_a: float | None = None
    rho_b: float | None = None
    rho_ab: float | None = None
    #: a vs outcome, controlling for b - and the reverse.
    a_given_b: float | None = None
    b_given_a: float | None = None
    #: The same head-to-head with ad exposure also partialled out. Arm C is
    #: where a confound does the most damage — if boosted publications
    #: happen to be the busy ones, the "winner" of the head-to-head is just
    #: whichever measure tracks boosting more closely.
    a_given_b_ad: float | None = None
    b_given_a_ad: float | None = None
    n_ad: int = 0
    verdict: str = ""


@dataclass
class VisualStructureReport:
    n_population: int = 0
    n_with_media: int = 0
    proxy: str = PROXY
    #: Arm A: proxy vs visual structure.
    proxy_associations: list[Association] = field(default_factory=list)
    #: Arm B: visual structure vs retention.
    outcome_associations: list[Association] = field(default_factory=list)
    #: Arm C: does the background explain the proxy, or the other way?
    mediation: list[Mediation] = field(default_factory=list)
    withheld: dict[str, str] = field(default_factory=dict)
    verdict_a: str = ""
    verdict_b: str = ""
    limitations: list[str] = field(default_factory=list)
    # -- ad exposure (M1) --------------------------------------------------
    ad_covariate_source: str | None = None
    ad_covariate_n: int = 0
    #: How many of those were actually boosted (>= the boost threshold).
    #: Printed beside `ad_covariate_n` because a covariate that is zero for
    #: most of the sample controls for very little.
    ad_covariate_exposed: int = 0
    #: The covariate against the outcome itself — the contamination
    #: diagnostic (ADR 0008 §5.6).
    ad_outcome_rho: float | None = None
    ad_outcome_p: float | None = None
    ad_covariate_note: str = ""


def _feature_values(session: Session, feature: str,
                    method_version: str | None = None) -> dict[int, float]:
    """Numeric values for one feature, skipping withheld measurements.

    A row whose `method` is `unavailable` carries a null value and a reason;
    including it as a zero would turn "we could not measure this" into "this
    measured zero", which is the specific error this project keeps guarding
    against.
    """
    stmt = select(VideoFeature).where(VideoFeature.feature == feature,
                                      VideoFeature.status == "ok")
    if method_version:
        stmt = stmt.where(VideoFeature.method_version == method_version)
    got: dict[int, float] = {}
    for row in session.scalars(stmt).all():
        if row.method == "unavailable":
            continue
        v = row.effective_value
        if isinstance(v, bool):
            v = float(v)
        if isinstance(v, (int, float)):
            got[row.video_id] = float(v)
    return got


def _unavailable_count(session: Session, feature: str,
                       method_version: str) -> int:
    rows = session.scalars(select(VideoFeature).where(
        VideoFeature.feature == feature,
        VideoFeature.method_version == method_version)).all()
    return sum(1 for r in rows if r.method == "unavailable")


def _performance_groups(session: Session) -> dict[int, str]:
    """video_id -> performance key, for the repeated-take collapse."""
    from sqlalchemy import text as _sql

    try:
        rows = session.execute(_sql("""
            select l.video_id, m.performance_id
              from video_master_links l
              join local_masters m on m.id = l.master_id
             where m.performance_id is not null
               and l.status in ('auto', 'confirmed', 'variant')
        """)).all()
    except Exception as exc:  # pragma: no cover - schema variance
        # Without the rollback the failed statement poisons the session and
        # every subsequent read fails with InFailedSqlTransaction, turning a
        # missing optional table into a total analysis failure.
        session.rollback()
        logger.warning("performance grouping unavailable: %s", exc)
        return {}
    return {int(vid): str(pid) for vid, pid in rows}


def _collapse(values: dict[int, float], outcomes: dict[int, float],
              groups: dict[int, str]) -> tuple[list[float], list[float]]:
    """One observation per performance, by mean, for shared videos."""
    buckets: dict[str, list[tuple[float, float]]] = {}
    for vid, x in values.items():
        if vid not in outcomes:
            continue
        key = groups.get(vid, f"solo:{vid}")
        buckets.setdefault(key, []).append((x, outcomes[vid]))
    xs, ys = [], []
    for pairs in buckets.values():
        xs.append(sum(p[0] for p in pairs) / len(pairs))
        ys.append(sum(p[1] for p in pairs) / len(pairs))
    return xs, ys


def _associate(name: str, method_version: str, xs_by_vid: dict[int, float],
               target_by_vid: dict[int, float]) -> Association:
    a = Association(feature=name, method_version=method_version)
    common = sorted(set(xs_by_vid) & set(target_by_vid))
    a.n = len(common)
    if a.n < MIN_N:
        a.note = f"only {a.n} publications have both; too few to test"
        return a
    xs = [xs_by_vid[v] for v in common]
    ys = [target_by_vid[v] for v in common]
    if len(set(xs)) < 2:
        a.note = ("the feature takes one value across the whole population, "
                  "so no association can exist")
        return a
    a.rho = round(spearman(xs, ys) or 0.0, 3)
    a.p = spearman_p(a.rho, a.n)
    a.ci = fisher_ci(a.rho, a.n)
    a.effect = effect_label(a.rho)
    return a


def analyse(session: Session, *,
            population: set[int] | None = None,
            control_ad_exposure: bool = True) -> VisualStructureReport:
    """Part 5's three arms.

    `population` restricts the analysis to a set of video ids, so that the
    effect of M1's larger sample can be separated from the effect of M1's
    new ad covariate — the two landed together and would otherwise be
    impossible to attribute. `control_ad_exposure=False` reproduces the
    pre-M1 computation exactly.
    """
    rep = VisualStructureReport(withheld=dict(WITHHELD_VISION))

    outcomes = _outcome_rows(session)
    rep.n_population = len(outcomes)
    usable = {vid: d for vid, d in outcomes.items()
              if d["video"].media_status == "available"
              and (population is None or vid in population)}
    rep.n_with_media = len(usable)

    skip = {vid: d["skip_rate"] for vid, d in usable.items()}
    duration = {vid: float(d["duration_s"]) for vid, d in usable.items()
                if d["duration_s"] is not None}
    age = {vid: float(d["age_days"]) for vid, d in usable.items()
           if d["age_days"] is not None}
    groups = _performance_groups(session)

    # ---- the ad-exposure covariate (M1, BLUEPRINT §1.5) -----------------
    if control_ad_exposure:
        exposures = ad_exposure.resolve(session, platform="instagram",
                                        video_ids=list(usable))
        ad_src, ad_vals = ad_exposure.homogeneous(exposures, list(usable))
    else:
        exposures, ad_src, ad_vals = {}, None, {}
    rep.ad_covariate_source = ad_src
    rep.ad_covariate_n = len(ad_vals)
    rep.ad_covariate_exposed = ad_exposure.exposed_count(ad_vals)
    cov = ad_exposure.coverage(exposures)
    rep.ad_covariate_note = (
        f"Covariate source: {ad_src or 'none'}, covering {len(ad_vals)} of "
        f"{len(usable)} publications with media. Coverage by source "
        f"{cov['by_source']}, {cov['unknown']} unresolved. Sources are "
        f"never mixed: a column meaning paid share for some rows and days "
        f"to next post for others is not a control. Of the covered "
        f"publications, {ad_exposure.exposed_count(ad_vals)} were actually "
        f"boosted; the rest sit at zero and the control does little for "
        f"them."
        if ad_src else
        "Ad control switched off by the caller: this run reproduces the "
        "pre-M1 computation exactly."
        if not control_ad_exposure else
        "No ad-exposure covariate could be resolved. Arms B and C below are "
        "UNCONTROLLED for paid exposure.")
    if len(ad_vals) >= MIN_N:
        pairs = [(ad_vals[v], skip[v]) for v in sorted(ad_vals) if v in skip]
        if len(pairs) >= MIN_N:
            r = spearman([a for a, _ in pairs], [b for _, b in pairs])
            if r is not None:
                rep.ad_outcome_rho = round(r, 3)
                rep.ad_outcome_p = spearman_p(r, len(pairs))

    proxy_vals = _feature_values(session, PROXY, hookfeat.METHOD_VERSION)
    proxy_vals = {v: x for v, x in proxy_vals.items() if v in usable}

    candidates: list[tuple[str, str]] = list(STRUCTURE_FEATURES)
    # Pin to the CURRENT vision method version. Picking whatever version
    # the first matching row happened to carry silently selected the v1
    # benchmark run - 24 videos, 18 of them in this population - and made
    # the field look untestable when v2 covers all 48.
    from server.api.vision_api import METHOD_VERSION as VISION_MV

    for name in VISION_FEATURES:
        if _feature_values(session, name, VISION_MV):
            candidates.append((name, VISION_MV))

    # ---- Arm A: does the proxy track visual structure? ----
    for name, mv in candidates:
        vals = {v: x for v, x in _feature_values(session, name, mv).items()
                if v in usable}
        a = _associate(name, mv, vals, proxy_vals)
        a.unavailable = _unavailable_count(session, name, mv)
        rep.proxy_associations.append(a)
    qs = benjamini_hochberg([a.p for a in rep.proxy_associations])
    for a, q in zip(rep.proxy_associations, qs):
        a.q = q

    # ---- Arm B: does any of it track retention? ----
    # The proxy is included here as a feature, not only as Arm A's target:
    # Part 5 cannot say the background explains the proxy's signal without
    # first measuring whether the proxy has one.
    for name, mv in [(PROXY, hookfeat.METHOD_VERSION)] + candidates:
        vals = {v: x for v, x in _feature_values(session, name, mv).items()
                if v in usable}
        a = _associate(name, mv, vals, skip)
        a.unavailable = _unavailable_count(session, name, mv)
        both = sorted(set(vals) & set(skip) & set(duration) & set(age))
        if len(both) >= MIN_N:
            a.partial_n = len(both)
            a.partial_rho = partial_spearman2(
                [vals[v] for v in both], [skip[v] for v in both],
                [duration[v] for v in both], [age[v] for v in both])
            if a.partial_rho is not None:
                a.partial_rho = round(a.partial_rho, 3)
        both_ad = [v for v in both if v in ad_vals]
        if len(both_ad) >= MIN_N:
            a.partial_ad_n = len(both_ad)
            r = partial_spearman_n(
                [vals[v] for v in both_ad], [skip[v] for v in both_ad],
                [[duration[v] for v in both_ad],
                 [age[v] for v in both_ad],
                 [ad_vals[v] for v in both_ad]])
            a.partial_ad_rho = round(r, 3) if r is not None else None
        cx, cy = _collapse(vals, skip, groups)
        if len(cx) >= MIN_N and len(set(cx)) > 1:
            a.clustered_n = len(cx)
            r = spearman(cx, cy)
            a.clustered_rho = round(r, 3) if r is not None else None
        rep.outcome_associations.append(a)
    qs = benjamini_hochberg([a.p for a in rep.outcome_associations])
    for a, q in zip(rep.outcome_associations, qs):
        a.q = q

    # ---- Arm C: proxy vs background, head to head ----
    bg_vals = {v: x for v, x
               in _feature_values(session, "background_edge_density",
                                  hookbg.METHOD_VERSION).items()
               if v in usable}
    rep.mediation = [_mediate(PROXY, proxy_vals,
                              "background_edge_density", bg_vals, skip,
                              ad_vals)]
    ed_vals = {v: x for v, x
               in _feature_values(session, "edge_density",
                                  hookfeat.METHOD_VERSION).items()
               if v in usable}
    rep.mediation.append(_mediate(PROXY, proxy_vals,
                                  "edge_density", ed_vals, skip, ad_vals))

    rep.verdict_a = _verdict(rep.proxy_associations,
                             what=f"`{PROXY}`",
                             null=(f"Nothing measured here accounts for what "
                                   f"`{PROXY}` responds to. Phase 6A showed "
                                   f"it is not text; Part 5 shows it is not "
                                   f"visual density or background complexity "
                                   f"either, on this population."))
    rep.verdict_b = _verdict(rep.outcome_associations,
                             what="`reels_skip_rate`",
                             null=("No visual-structure measurement is "
                                   "associated with early abandonment on "
                                   "this population. That is consistent with "
                                   "Phase 6A, which found no hook "
                                   "measurement of any kind related to "
                                   "retention here."))

    rep.limitations = [
        f"n = {rep.n_with_media} publications with both retention data and "
        f"media. Phase 6A established this is the real sample size, not the "
        f"258 preserved videos.",
        "Four of the six properties the brief named - keyboard visibility, "
        "sheet music, hand close-ups, shot distance - are withheld because "
        "the vision benchmark measured the model as unable to supply them. "
        "They are not reported as null results; they are unmeasured.",
        "`reels_skip_rate` is observed once per publication at whatever age "
        "it happened to be captured. Age is controlled for in the partial "
        "correlation, which is weaker than comparing at a fixed anchor age.",
        "These are correlations on one creator's account. Nothing here is "
        "causal and nothing here transfers to another niche.",
        "The clustered column collapses repeated takes of one performance "
        "to a single observation. Where it differs from the raw column, "
        "the raw column is overstating the independent evidence.",
    ]
    return rep


def _verdict(assocs: list[Association], *, what: str, null: str) -> str:
    tested = [a for a in assocs if a.rho is not None]
    if not tested:
        return (f"Nothing could be tested against {what}: no feature had "
                f"enough paired observations.")
    survivors = [a for a in tested if a.q is not None and a.q < 0.05]
    if not survivors:
        s = max(tested, key=lambda a: abs(a.rho or 0.0))
        detail = f"n = {s.n}"
        if s.p is not None:
            detail += f", p = {s.p:.3f}"
        if s.q is not None:
            detail += f", q = {s.q:.3f}"
        return (f"{null} The strongest association was `{s.feature}` at "
                f"rho = {s.rho:+.3f} ({detail}), which does not survive "
                f"multiple-comparison correction.")
    parts = ", ".join(f"`{a.feature}` rho = {a.rho:+.3f} (q = {a.q:.3f})"
                      for a in survivors)
    return (f"{len(survivors)} of {len(tested)} associations with {what} "
            f"survive Benjamini-Hochberg correction at q < 0.05: {parts}. "
            f"Correlational only.")


def _mediate(a_name: str, a_vals: dict[int, float],
             b_name: str, b_vals: dict[int, float],
             outcome: dict[int, float],
             ad_vals: dict[int, float] | None = None) -> Mediation:
    """Run the control both ways and say which feature survives."""
    m = Mediation(a=a_name, b=b_name)
    common = sorted(set(a_vals) & set(b_vals) & set(outcome))
    m.n = len(common)
    if m.n < MIN_N:
        m.verdict = f"only {m.n} paired observations; too few to test"
        return m
    xa = [a_vals[v] for v in common]
    xb = [b_vals[v] for v in common]
    yy = [outcome[v] for v in common]

    def r(x, y):
        v = spearman(x, y)
        return round(v, 3) if v is not None else None

    m.rho_a, m.rho_b, m.rho_ab = r(xa, yy), r(xb, yy), r(xa, xb)
    pa = partial_spearman(xa, yy, xb)
    pb = partial_spearman(xb, yy, xa)
    m.a_given_b = round(pa, 3) if pa is not None else None
    m.b_given_a = round(pb, 3) if pb is not None else None

    if ad_vals:
        shared = [v for v in common if v in ad_vals]
        if len(shared) >= MIN_N:
            m.n_ad = len(shared)
            aa = [a_vals[v] for v in shared]
            bb = [b_vals[v] for v in shared]
            yy2 = [outcome[v] for v in shared]
            cc = [ad_vals[v] for v in shared]
            r1 = partial_spearman_n(aa, yy2, [bb, cc])
            r2 = partial_spearman_n(bb, yy2, [aa, cc])
            m.a_given_b_ad = round(r1, 3) if r1 is not None else None
            m.b_given_a_ad = round(r2, 3) if r2 is not None else None

    if m.a_given_b is None or m.b_given_a is None:
        m.verdict = "the partial correlation could not be computed"
        return m
    fa, fb = abs(m.a_given_b), abs(m.b_given_a)
    shrink_a = abs(m.rho_a or 0.0) - fa
    shrink_b = abs(m.rho_b or 0.0) - fb
    if fb >= 0.2 and fa < 0.15:
        m.verdict = (
            f"`{b_name}` carries the association. Controlling for it drops "
            f"`{a_name}` from {m.rho_a:+.3f} to {m.a_given_b:+.3f}, while "
            f"controlling for `{a_name}` leaves `{b_name}` at "
            f"{m.b_given_a:+.3f}. On this population `{a_name}` looks like "
            f"a noisier reading of the same underlying quantity.")
    elif fa >= 0.2 and fb < 0.15:
        m.verdict = (
            f"`{a_name}` carries the association, not `{b_name}` "
            f"({m.b_given_a:+.3f} once `{a_name}` is controlled).")
    elif fa < 0.15 and fb < 0.15:
        m.verdict = (
            f"Neither survives the other: {a_name} {m.a_given_b:+.3f}, "
            f"{b_name} {m.b_given_a:+.3f}. The two measure nearly the same "
            f"thing (rho = {m.rho_ab:+.3f}) and this sample cannot separate "
            f"them.")
    else:
        m.verdict = (
            f"Both retain some association after controlling for the other "
            f"(`{a_name}` {m.a_given_b:+.3f}, `{b_name}` {m.b_given_a:+.3f}; "
            f"they correlate {m.rho_ab:+.3f} with each other). Shrinkage "
            f"{shrink_a:+.3f} and {shrink_b:+.3f}. Not separable here.")
    return m
