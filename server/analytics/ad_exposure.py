"""How much of a publication's exposure was paid — measured, or refused.

The owner boosts Instagram posts. Every per-viewer metric on that account
is therefore a figure about *some* audience, and which audience differs per
publication. `BLUEPRINT.md` §1.5 made the rule: every retention or
engagement analysis on this account carries an ad-exposure covariate and
reports its results with and without it. This module is that covariate.

**It is not a paid-views estimator.** It answers one narrower question —
"how contaminated is this publication, on a 0–1 scale, and how do we know?"
— and it answers `unknown` whenever it cannot.

Three sources, in descending order of what they actually establish
(ADR 0008):

``ads_insights``
    Paid impressions for the ad whose creative carries this media id,
    against organic views. The real answer. Requires an `ads_read` token,
    which as of 2026-10-26 does not exist, so `ad_exposures` is empty and
    this source never fires yet.

``surface_residual``
    ``(total_views − crossposted_views) / total_views``. Measured, from the
    existing token, no new scope. Meta documents `views` as organic-only
    and `total_views` as spanning "all surfaces, including views from
    promoted/boosted/ad media and Facebook", and `crossposted_views` was
    measured to equal `views + facebook_views` exactly on this account. So
    the residual is exposure that is neither organic Instagram nor
    Facebook. It is an **upper bound** on the paid share, not the paid
    share: "all surfaces" names promoted media as one member, not the only
    one.

``gap_proxy``
    Days until the next publication, from `posted_at` alone. The ad rotates
    to each new post, so paid exposure is roughly proportional to how long
    a post stayed the newest one. This is what `BLUEPRINT.md` §1.2 used and
    it is the weakest of the three by a wide margin, because the same gap
    also buys *organic* accumulation. Kept only for publications the other
    two cannot reach, and never silently.

## The rule this module exists to enforce

**Sources are never mixed inside one analysis.** Partialling out a column
that means "paid impression share" for some rows and "days to next post"
for others is not a control; it is a new variable with no definition, and
it will produce a number that looks like a result. `resolve()` therefore
tags every value with its source and `homogeneous()` is the only supported
way to feed them to a correlation.
"""

from __future__ import annotations

import logging
import statistics
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger("virallab.analytics.ad_exposure")

METHOD_VERSION = "ad-exposure-v1"

#: Best first. `resolve()` walks this in order and takes the first source
#: that can answer for a given publication.
SOURCE_PRIORITY = ("ads_insights", "surface_residual", "gap_proxy")

#: Residual share at or above which a publication is called boosted for the
#: purpose of dating the ad window. Deliberately generous: Facebook
#: cross-recommendation already lives in `facebook_views`, so a clean
#: unboosted reel should sit near zero, and the measured unboosted control
#: sat at exactly zero.
BOOSTED_SHARE_THRESHOLD = 0.25

#: Gap proxy values are capped before use. A publication followed by a
#: three-month silence did not receive three months of $1/day attention —
#: the ad budget is daily and the campaign was not running for most of it —
#: and leaving the tail uncapped lets two or three dormant periods dominate
#: a rank correlation on 48 points.
GAP_CAP_DAYS = 30.0


@dataclass(slots=True)
class Exposure:
    """One publication's ad-exposure covariate, with its provenance."""

    video_id: int
    #: 0–1 where the source supports a share, else a monotone stand-in on
    #: its own scale (gap_proxy is in days). Comparable WITHIN a source and
    #: meaningless across sources, which is why `source` is never optional.
    value: float | None
    source: str
    #: Set whenever `value` is None, so "not measured" can never be read as
    #: "no ad exposure". These are different states and the whole module
    #: exists to keep them apart.
    unavailable_reason: str | None = None
    detail: dict = field(default_factory=dict)

    @property
    def available(self) -> bool:
        return self.value is not None


def _surface_rows(session: Session, platform: str) -> dict[int, dict]:
    """Latest snapshot per video that carries the surface decomposition.

    A seam worth naming: this picks the latest snapshot carrying
    `total_views`, while `retention._outcome_rows` picks the latest
    carrying `reels_skip_rate`. Those are the **same** snapshot for all 286
    publications today, because the M1 collection pass requested both in
    one run — verified 2026-10-26. They would diverge if a later pass
    collected one and not the other, and the covariate would then describe
    a different moment from the outcome it is controlling.

    Left as is rather than joined, because forcing one snapshot to carry
    both would silently drop publications where only one is available, and
    a missing covariate is better reported than hidden. If the two ever
    diverge materially, the fix is to require a shared `captured_at`, not
    to prefer one side.
    """
    rows = session.execute(text("""
        select distinct on (s.video_id)
               s.video_id, s.views, s.total_views, s.crossposted_views,
               s.facebook_views, s.captured_at
        from metrics_snapshots s
             join videos v on v.id = s.video_id
             join creators c on c.id = v.creator_id
        where v.platform = :p and c.is_own_account
          and s.total_views is not null
        order by s.video_id, s.captured_at desc"""),
        {"p": platform}).mappings().all()
    return {r["video_id"]: dict(r) for r in rows}


def _ads_rows(session: Session, platform: str) -> dict[int, dict]:
    """Paid impressions per video, summed over the ad's reporting days."""
    rows = session.execute(text("""
        select a.video_id,
               sum(a.impressions) as paid_impressions,
               sum(a.spend)       as spend,
               min(a.currency)    as currency,
               count(*)           as day_rows
        from ad_exposures a
        where a.video_id is not null and a.platform = :p
        group by a.video_id"""), {"p": platform}).mappings().all()
    return {r["video_id"]: dict(r) for r in rows}


def _gap_rows(session: Session, platform: str) -> dict[int, dict]:
    """Days from each publication to the next one on the same account."""
    rows = session.execute(text("""
        select v.id as video_id, v.posted_at,
               lead(v.posted_at) over (partition by v.creator_id
                                       order by v.posted_at) as next_posted_at
        from videos v join creators c on c.id = v.creator_id
        where v.platform = :p and c.is_own_account
          and v.posted_at is not null"""), {"p": platform}).mappings().all()
    out: dict[int, dict] = {}
    for r in rows:
        nxt = r["next_posted_at"]
        gap = (None if nxt is None
               else (nxt - r["posted_at"]).total_seconds() / 86400.0)
        out[r["video_id"]] = {"gap_days": gap, "posted_at": r["posted_at"]}
    return out


def _from_surface(vid: int, s: dict) -> Exposure:
    """Residual share from one snapshot's surface metrics, or a refusal.

    The arithmetic looks trivial and is not, because two of the three
    metrics go missing for different reasons that must not be conflated
    (all figures below measured on this account, 385 publications,
    2026-10-26):

    - `crossposted_views` returns **0**, not null, for a publication that
      was never shared to Facebook: 15 of 15 such rows also had
      `facebook_views == 0`. Taking 0 as the organic+Facebook basis would
      make every one of them read as 100% paid.
    - `facebook_views` errors with "does not support … for this media
      product type" when the post is not on Facebook — 252 of those 264
      rows had `total_views == views` exactly, confirming there is nothing
      outside the organic count.
    - but `facebook_views` ALSO fails with `Meta error -1: Fatal` on 12
      publications, and that failure is **persistent, not transient**: all
      twelve were retried four times each and failed every time. For those
      the Facebook surface is *unknown*, and the gap between `total_views`
      and `views` cannot be attributed. Two of the twelve carried the
      largest gaps in the corpus, so guessing would have been expensive.

    So the basis is the largest defensible figure, and where a gap exists
    that a missing `facebook_views` could explain, this refuses instead of
    calling it paid.
    """
    total = float(s["total_views"])
    views = float(s["views"])
    fbv = s.get("facebook_views")
    cross = s.get("crossposted_views")

    candidates: list[tuple[float, str]] = []
    if fbv is not None:
        candidates.append((views + float(fbv), "views+facebook_views"))
    if cross is not None:
        candidates.append((float(cross), "crossposted_views"))
    if not candidates:
        candidates.append((views, "views"))
    base, basis = max(candidates)

    if total <= 0:
        return Exposure(vid, None, "surface_residual",
                        unavailable_reason="total_views is zero")

    residual = total - base
    if residual < 0:
        # The identity broke. Report rather than clamp: a negative residual
        # means one of the three metrics changed meaning, and flooring it
        # at zero would hide that behind a plausible number.
        logger.warning("video %s: negative surface residual %.0f", vid,
                       residual)
        return Exposure(
            vid, None, "surface_residual",
            unavailable_reason=(
                f"negative residual ({residual:.0f}): total_views "
                f"{total:.0f} < {basis} {base:.0f}"),
            detail={"total_views": total, "basis_value": base,
                    "basis": basis})

    if basis == "views" and residual > 0:
        # No Facebook-inclusive basis at all, AND an unexplained gap. This
        # is the 12-row failure mode above. Either the post is on Facebook
        # and the gap is Facebook, or it is boosted and the gap is paid,
        # and this function cannot tell which.
        #
        # The test is on the BASIS, not on `facebook_views` being None: a
        # publication whose `facebook_views` call failed but whose
        # `crossposted_views` succeeded already has Facebook accounted for,
        # because crossposted_views == views + facebook_views. Refusing
        # those would discard a resolvable row. (In the 12 observed cases
        # both metrics failed together, so this is correctness for a case
        # the current data does not contain — which is when it is cheapest
        # to get right.)
        return Exposure(
            vid, None, "surface_residual",
            unavailable_reason=(
                f"facebook_views unknown and total_views exceeds views by "
                f"{residual:.0f}; the excess cannot be attributed to paid "
                f"delivery without it"),
            detail={"total_views": total, "views": views,
                    "unexplained": residual})

    return Exposure(
        vid, residual / total, "surface_residual",
        detail={"total_views": total, "basis_value": base, "basis": basis,
                "residual_surface_views": residual,
                "captured_at": s.get("captured_at")})


def resolve(session: Session, *, platform: str = "instagram",
            video_ids: list[int] | None = None) -> dict[int, Exposure]:
    """The ad-exposure covariate for every own-account publication.

    Returns one `Exposure` per video, including the ones it cannot answer
    for — an absent key would be indistinguishable from an unexposed
    publication at the call site, and that confusion is the failure this
    module is built to prevent.
    """
    ads = _ads_rows(session, platform)
    surface = _surface_rows(session, platform)
    gaps = _gap_rows(session, platform)

    ids = set(gaps) | set(surface) | set(ads)
    if video_ids is not None:
        ids &= set(video_ids)

    out: dict[int, Exposure] = {}
    for vid in sorted(ids):
        a, s, g = ads.get(vid), surface.get(vid), gaps.get(vid)

        if a and a.get("paid_impressions") is not None and s:
            # Paid impressions against the total of paid plus organic. Not
            # against `total_views`: those are views, impressions are
            # deliveries, and dividing one by the other would be a ratio of
            # two different things.
            paid = float(a["paid_impressions"])
            organic = float(s.get("views") or 0)
            denom = paid + organic
            if denom > 0:
                out[vid] = Exposure(
                    vid, paid / denom, "ads_insights",
                    detail={"paid_impressions": paid,
                            "organic_views": organic,
                            "spend": (float(a["spend"])
                                      if a.get("spend") is not None else None),
                            "currency": a.get("currency"),
                            "day_rows": a.get("day_rows")})
                continue

        if s and s.get("total_views") is not None and s.get("views") is not None:
            exp = _from_surface(vid, s)
            if exp is not None:
                out[vid] = exp
                continue

        if g and g.get("gap_days") is not None:
            out[vid] = Exposure(
                vid, min(float(g["gap_days"]), GAP_CAP_DAYS), "gap_proxy",
                detail={"gap_days_raw": g["gap_days"],
                        "capped_at": GAP_CAP_DAYS})
            continue

        out[vid] = Exposure(
            vid, None, "none",
            unavailable_reason=(
                "no ad insights, no surface metrics, and no following "
                "publication to measure a gap against"))
    return out


def homogeneous(exposures: dict[int, Exposure], video_ids: list[int],
                ) -> tuple[str | None, dict[int, float]]:
    """The largest single-source covariate available for these videos.

    The only supported way to get numbers out of this module for a
    correlation. Returns the winning source and its values; everything from
    a different source is dropped rather than coerced, and the caller is
    expected to report how many were dropped.
    """
    wanted = set(video_ids)
    best: tuple[str | None, dict[int, float]] = (None, {})
    for src in SOURCE_PRIORITY:
        vals = {vid: e.value for vid, e in exposures.items()
                if vid in wanted and e.source == src and e.value is not None}
        if len(vals) > len(best[1]):
            best = (src, vals)
    return best


def exposed_count(values: dict[int, float], *,
                  threshold: float = BOOSTED_SHARE_THRESHOLD) -> int:
    """How many of these publications actually carry ad exposure.

    Reported alongside every ad-controlled correlation, because `n` alone
    is misleading here. A covariate that is 0 for 44 of 48 publications
    controls for almost nothing, and the partial correlation is then
    carried by the four that are not — which is a low-powered comparison
    wearing the same notation as a well-powered one. A reader cannot tell
    those apart from `n`, so this is printed next to it.
    """
    return sum(1 for v in values.values() if v >= threshold)


def coverage(exposures: dict[int, Exposure]) -> dict:
    """How many publications each source could answer for.

    M1's acceptance criterion is that every own-account publication has an
    ad-exposure value or an explicit `unknown`. This is the report that
    demonstrates it.
    """
    by_source: dict[str, int] = {}
    unknown = 0
    for e in exposures.values():
        if e.available:
            by_source[e.source] = by_source.get(e.source, 0) + 1
        else:
            unknown += 1
    return {"total": len(exposures), "by_source": by_source,
            "unknown": unknown,
            "method_version": METHOD_VERSION}


def ad_window(session: Session, *, platform: str = "instagram") -> dict:
    """When did the boosting start? Measured, not recalled.

    The owner's recollection of when the ad began has already shifted once
    — `BLUEPRINT.md` §1 recorded "several months", the owner later said "a
    few weeks" — and the answer decides which publications are
    contaminated. The residual share settles it without anyone having to
    remember: it should sit near zero before the campaign and rise after.

    Returns per-month medians and the first month whose median crosses
    `BOOSTED_SHARE_THRESHOLD`. Reported as evidence, never as a fact about
    the campaign: a surface residual is an upper bound (see the module
    docstring), so an early month that crosses the line is a reason to look,
    not a start date.
    """
    exposures = resolve(session, platform=platform)
    rows = session.execute(text("""
        select v.id as video_id, v.posted_at
        from videos v join creators c on c.id = v.creator_id
        where v.platform = :p and c.is_own_account
          and v.posted_at is not null"""), {"p": platform}).mappings().all()
    posted = {r["video_id"]: r["posted_at"] for r in rows}

    buckets: dict[str, list[float]] = {}
    for vid, e in exposures.items():
        if e.source != "surface_residual" or not e.available:
            continue
        when: datetime | None = posted.get(vid)
        if when is None:
            continue
        buckets.setdefault(when.strftime("%Y-%m"), []).append(e.value)

    months = []
    for key in sorted(buckets):
        vals = buckets[key]
        months.append({"month": key, "n": len(vals),
                       "median_residual_share": round(
                           statistics.median(vals), 4),
                       "max_residual_share": round(max(vals), 4),
                       "n_above_threshold": sum(
                           1 for v in vals if v >= BOOSTED_SHARE_THRESHOLD)})

    onset = next((m["month"] for m in months
                  if m["median_residual_share"] >= BOOSTED_SHARE_THRESHOLD),
                 None)
    return {
        "months": months,
        "first_month_above_threshold": onset,
        "threshold": BOOSTED_SHARE_THRESHOLD,
        "measured_on": "surface_residual only",
        "caveat": ("The residual is an UPPER BOUND on paid share: "
                   "total_views spans all surfaces and promoted media is "
                   "one named member of that set. A month above the "
                   "threshold is a reason to check the ads account, not a "
                   "campaign start date."),
        "method_version": METHOD_VERSION,
    }
