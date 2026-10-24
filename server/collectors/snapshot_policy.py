"""Which videos to re-observe on a given run, and how hard to push the API.

Two problems this solves.

**Quota.** Instagram Platform uses Business Use Case limiting —
`4800 x impressions` over a rolling 24 hours `[PRIMARY]` — so the ceiling
*scales with account activity* rather than being a flat number. A quiet day
shrinks it. Refreshing every stored post on every run is therefore not a
fixed cost but a growing one, and it would eventually exhaust the budget on
posts whose numbers stopped moving years ago.

**Fixed-age observations.** Plan §5.4 needs snapshots that *bracket* day 7
and day 30. That requires dense sampling while a post is young and almost
none once it is old. The tiers below are shaped by that need, not by a guess
at what seems reasonable.

The policy never stops collecting historical data — old posts rotate through
a slice each run, so everything is refreshed within a bounded period.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger("virallab.snapshot_policy")

#: Age bands, in days, and how often a post in each is re-observed.
#: The first band is deliberately generous: it must produce snapshots on both
#: sides of the 7- and 30-day anchors, and a missed day there cannot be
#: recovered later.
FRESH_DAYS = 35          # every run
RECENT_DAYS = 90         # weekly
#: Everything older rotates: each run takes a slice, so the whole archive is
#: refreshed within roughly this many days.
ARCHIVE_CYCLE_DAYS = 30

#: Stop issuing calls once BUC usage passes this percentage. Well below 100
#: so a concurrent process or a sudden drop in impressions cannot push us
#: over.
BUC_SOFT_LIMIT_PCT = 70


@dataclass(slots=True)
class BucUsage:
    """Parsed `X-Business-Use-Case-Usage`. All values are percentages."""

    call_count: int = 0
    total_cputime: int = 0
    total_time: int = 0
    estimated_time_to_regain_access: int = 0

    @property
    def worst(self) -> int:
        return max(self.call_count, self.total_cputime, self.total_time)

    @property
    def should_stop(self) -> bool:
        return self.worst >= BUC_SOFT_LIMIT_PCT or bool(
            self.estimated_time_to_regain_access)

    @classmethod
    def parse(cls, header_value: str | None) -> "BucUsage | None":
        """The header is JSON keyed by business id; take the worst entry."""
        if not header_value:
            return None
        try:
            data = json.loads(header_value)
        except ValueError:
            return None
        worst = cls()
        for entries in data.values():
            for e in entries if isinstance(entries, list) else [entries]:
                if not isinstance(e, dict):
                    continue
                worst = cls(
                    call_count=max(worst.call_count, e.get("call_count", 0)),
                    total_cputime=max(worst.total_cputime,
                                      e.get("total_cputime", 0)),
                    total_time=max(worst.total_time, e.get("total_time", 0)),
                    estimated_time_to_regain_access=max(
                        worst.estimated_time_to_regain_access,
                        e.get("estimated_time_to_regain_access", 0) or 0),
                )
        return worst


def select_for_refresh(session: Session, platform: str, *,
                       max_calls: int | None = None,
                       run_index: int | None = None) -> list[str]:
    """External ids to re-observe on this run, highest priority first.

    Priority order, and why:

      1. **Never observed** — a post with no snapshot at all is the biggest
         gap in the dataset.
      2. **Young (< 35 days)** — the only window in which real views@7d and
         views@30d anchors can still be captured. Missing a day here is
         permanent.
      3. **Recent (35-90 days)** — weekly is enough; the curve has flattened.
      4. **Archive (> 90 days)** — a rotating slice, so nothing is abandoned
         but the cost per run stays bounded.
    """
    rows = session.execute(text("""
        with latest as (
          select v.id, v.external_id, v.posted_at,
                 max(s.captured_at) as last_seen
          from videos v left join metrics_snapshots s on s.video_id = v.id
          where v.platform = :p and v.posted_at is not null
          group by v.id, v.external_id, v.posted_at)
        select external_id,
               extract(epoch from (now() - posted_at))/86400.0 as age_days,
               case when last_seen is null then null
                    else extract(epoch from (now() - last_seen))/86400.0 end
                 as since_seen,
               id
        from latest order by posted_at desc"""), {"p": platform}).all()

    never, young, recent, archive = [], [], [], []
    for ext, age, since, vid in rows:
        age = float(age)
        if since is None:
            never.append(ext)
        elif age <= FRESH_DAYS:
            young.append(ext)
        elif age <= RECENT_DAYS:
            if float(since) >= 7:
                recent.append(ext)
        else:
            archive.append((vid, ext))

    # Rotate the archive deterministically: slice by video id modulo the
    # cycle length, so every post is picked up within ARCHIVE_CYCLE_DAYS and
    # the same post is not refreshed twice in a cycle.
    slot = (run_index if run_index is not None
            else _runs_so_far(session, platform)) % ARCHIVE_CYCLE_DAYS
    archive_slice = [ext for vid, ext in archive
                     if vid % ARCHIVE_CYCLE_DAYS == slot]

    ordered = never + young + recent + archive_slice
    if max_calls is not None and len(ordered) > max_calls:
        logger.info("snapshot policy: %d candidates trimmed to budget %d",
                    len(ordered), max_calls)
        ordered = ordered[:max_calls]

    logger.info("snapshot policy for %s: %d never-seen, %d young, %d recent, "
                "%d archive slice (slot %d/%d) -> %d calls",
                platform, len(never), len(young), len(recent),
                len(archive_slice), slot, ARCHIVE_CYCLE_DAYS, len(ordered))
    return ordered


def _runs_so_far(session: Session, platform: str) -> int:
    return session.execute(text(
        "select count(*) from collection_runs "
        "where platform = :p and status = 'ok'"), {"p": platform}).scalar() or 0


def coverage_report(session: Session, platform: str) -> dict:
    """How well the corpus is covered, so a gap is visible rather than
    discovered later during modelling."""
    r = session.execute(text("""
        with latest as (
          select v.id, v.posted_at, max(s.captured_at) as last_seen,
                 count(s.id) as n
          from videos v left join metrics_snapshots s on s.video_id = v.id
          where v.platform = :p and v.posted_at is not null
          group by v.id, v.posted_at)
        select count(*),
               count(*) filter (where n = 0),
               count(*) filter (where now() - posted_at <= interval '35 days'),
               count(*) filter (where n >= 2),
               avg(n)::numeric(10,2),
               max(extract(epoch from (now() - last_seen))/86400.0)::int
        from latest"""), {"p": platform}).one()
    return {"videos": r[0], "never_observed": r[1], "young": r[2],
            "with_trajectory": r[3], "mean_snapshots": float(r[4] or 0),
            "stalest_days": r[5]}
