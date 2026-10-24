"""Fixed-age metric derivation — and the refusal to fake it.

Plan section 5.4 requires every compared metric to carry an explicit anchor
age (views@7d, views@30d). The hazard this module exists to prevent is
specific and easy to fall into:

    A backfilled video's FIRST snapshot is a lifetime cumulative reading
    taken at an arbitrary age. For this account the median age at first
    capture was 237 days, the oldest 1358. Treating that number as
    "views@30d" would measure elapsed exposure, not performance.

So an anchor is only ever derived when real snapshots BRACKET the anchor
date. Otherwise the answer is None. The module never extrapolates, never
falls back to "closest available", and never silently substitutes a lifetime
total — those are the three ways this goes wrong quietly.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import text
from sqlalchemy.orm import Session

#: Canonical anchors. 7-day values are read at day 9+ in practice because
#: Instagram lags up to 48h (ADR 0001); that is a collection concern, not a
#: derivation one.
ANCHORS = (7, 30, 90)


@dataclass(slots=True)
class AnchorValue:
    """A fixed-age metric, or an explicit refusal to produce one."""

    video_id: int
    anchor_days: int
    metric: str
    value: int | None
    #: "interpolated" | "exact" | None when unavailable.
    method: str | None = None
    #: Why no value could be produced. Always set when value is None, so a
    #: missing anchor is never mistaken for a zero.
    unavailable_reason: str | None = None

    @property
    def available(self) -> bool:
        return self.value is not None


def observation_age_days(session: Session, video_id: int) -> float | None:
    """Age of the video, in days, when it was FIRST observed.

    This is the number that decides whether fixed-age anchors are possible at
    all. A video first seen at age 237 days can never have a real views@7d.
    """
    row = session.execute(text("""
        select extract(epoch from (min(s.captured_at) - v.posted_at))/86400.0
        from metrics_snapshots s join videos v on v.id = s.video_id
        where s.video_id = :vid and v.posted_at is not null
        group by v.posted_at"""), {"vid": video_id}).scalar()
    return float(row) if row is not None else None


def metric_at_age(session: Session, video_id: int, anchor_days: int,
                  metric: str = "views") -> AnchorValue:
    """Value of `metric` at exactly `anchor_days` after publication.

    Requires snapshots on both sides of the anchor date. Linear interpolation
    between the bracketing pair is honest because these are monotonically
    accumulating counters; extrapolation beyond them is not, so it is refused.
    """
    if metric not in ("views", "likes", "comments", "shares", "saves"):
        raise ValueError(f"unsupported metric: {metric!r}")

    posted = session.execute(text(
        "select posted_at from videos where id = :vid"), {"vid": video_id}
    ).scalar()
    if posted is None:
        return AnchorValue(video_id, anchor_days, metric, None,
                           unavailable_reason="video has no posted_at")

    target = posted + timedelta(days=anchor_days)

    before = session.execute(text(f"""
        select captured_at, {metric} from metrics_snapshots
        where video_id = :vid and captured_at <= :t and {metric} is not null
        order by captured_at desc limit 1"""),
        {"vid": video_id, "t": target}).one_or_none()
    after = session.execute(text(f"""
        select captured_at, {metric} from metrics_snapshots
        where video_id = :vid and captured_at >= :t and {metric} is not null
        order by captured_at asc limit 1"""),
        {"vid": video_id, "t": target}).one_or_none()

    if before is None:
        age = observation_age_days(session, video_id)
        return AnchorValue(
            video_id, anchor_days, metric, None,
            unavailable_reason=(
                f"first observed at age {age:.0f}d, after the {anchor_days}d "
                f"anchor; a backfilled lifetime total is NOT this anchor"
                if age is not None else "no snapshot at or before the anchor"))
    if after is None:
        return AnchorValue(
            video_id, anchor_days, metric, None,
            unavailable_reason=(
                f"no snapshot at or after the {anchor_days}d anchor yet; "
                "refusing to extrapolate"))

    (t0, v0), (t1, v1) = before, after
    if t0 == t1 or v0 == v1:
        return AnchorValue(video_id, anchor_days, metric, int(v0), "exact")

    frac = (target - t0).total_seconds() / (t1 - t0).total_seconds()
    return AnchorValue(video_id, anchor_days, metric,
                       int(round(v0 + (v1 - v0) * frac)), "interpolated")


def anchor_eligibility(session: Session, platform: str = "tiktok") -> dict:
    """How much of the corpus can support fixed-age comparison at all.

    Reported rather than assumed, because the honest answer right after a
    backfill is "almost none", and that shapes what Phase 4 can do.
    """
    rows = session.execute(text("""
        with firsts as (
          select s.video_id,
                 extract(epoch from (min(s.captured_at) - v.posted_at))/86400.0
                   as first_age
          from metrics_snapshots s join videos v on v.id = s.video_id
          where v.platform = :p and v.posted_at is not null
          group by s.video_id, v.posted_at)
        select count(*),
               count(*) filter (where first_age <= 7),
               count(*) filter (where first_age <= 30),
               count(*) filter (where first_age <= 90)
        from firsts"""), {"p": platform}).one()
    total, le7, le30, le90 = rows
    return {
        "videos_with_snapshots": total,
        "could_support_7d": le7,
        "could_support_30d": le30,
        "could_support_90d": le90,
        "backfill_only": total - le90,
        "note": ("Counts are an UPPER BOUND: a video first seen before the "
                 "anchor still needs a later snapshot to bracket it. "
                 "Backfilled lifetime totals are not fixed-age values."),
    }
