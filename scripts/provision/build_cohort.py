"""Deepen the competitor corpus into a real cohort. Runs ON Ubuntu.

M6 asks for the owner "compared against a saved cohort of >= 10
creators, every cross-creator association reported with n and cohort
composition". That is not reachable on the corpus as it stands, and
the numbers say so plainly: **61 of 68 competitor creators have
exactly one publication**, 23 competitor publications carry any
features at all, and only 2 creators have ever been researched.

A comparison between the owner's 385 measured publications and
somebody's single video is not a comparison. So the first half of M6
is collecting the data M6 needs, and this is that.

## What it does, and what it costs

For each creator in the chosen cohort it runs one paid research pass
that collects their back catalogue. The provider bills per result, so
cost scales with `--per-creator` and is reported per creator and in
total, before and after.

Two guards that are not optional:

* **Nothing runs without `--yes`.** The default prints the plan and
  the estimate and exits.
* **A ceiling the operator sets.** `--max-usd` stops the loop as soon
  as the committed total would pass it, mid-cohort if necessary. The
  category caps in `config/spending.local.yaml` still apply underneath
  - this is a second, tighter limit for one run, because a loop over
  twelve creators is exactly the shape of thing that overspends while
  nobody is watching.

Research is idempotent and reuses fresh results, so re-running costs
nothing for creators already collected.

    python build_cohort.py --per-creator 30            # plan only
    python build_cohort.py --per-creator 30 --yes --max-usd 1.20
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, "/home/rastlyn/virallab")

from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from server.budget import budget_state  # noqa: E402
from server.discovery.apify import ApifyDiscoveryAdapter  # noqa: E402
from server.research.service import research_creator  # noqa: E402

#: Candidates, chosen to span audience size rather than to be the
#: biggest. M6's whole point is that "a 61-follower account and an
#: 853k one are never silently averaged", which needs both ends
#: present in the cohort, not a cohort of stars.
COHORT_SQL = """
select c.handle, c.platform, c.follower_count,
       (select count(*) from videos v where v.creator_id = c.id) pubs
from creators c
where not c.is_own_account
  and c.platform = :platform
  and c.follower_count is not null
order by c.follower_count desc
"""

#: The saved cohort M6's acceptance criterion names.
COLLECTION_NAME = "Classical Piano"


def pick(rows: list, n: int) -> list:
    """Spread the cohort across the audience range.

    Taking the top n would produce a cohort of megastars whose
    content has nothing to do with a 2k-follower pianist's, and every
    comparison against it would read as "you are not famous".
    Stratified by rank instead: biggest, smallest, and evenly spaced
    between.
    """
    if len(rows) <= n:
        return list(rows)
    step = (len(rows) - 1) / (n - 1)
    return [rows[round(i * step)] for i in range(n)]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--platform", default="tiktok")
    ap.add_argument("--creators", type=int, default=12)
    ap.add_argument("--per-creator", type=int, default=30,
                    help="publications to ask for per creator")
    ap.add_argument("--max-usd", type=float, default=1.20,
                    help="hard ceiling for THIS run, under the "
                         "category caps")
    ap.add_argument("--yes", action="store_true",
                    help="required: this spends real money")
    args = ap.parse_args()

    eng = create_engine(os.environ["VIRALLAB_DB_URL"], future=True)
    S = sessionmaker(eng, expire_on_commit=False, future=True)

    with S() as s:
        before = budget_state(s, provider="apify")
        rows = s.execute(text(COHORT_SQL),
                         {"platform": args.platform}).all()
        chosen = pick(rows, args.creators)

        adapter = ApifyDiscoveryAdapter(os.environ["APIFY_TOKEN"])
        from server.discovery.base import DiscoveryQuery, Platform, QueryKind

        per = adapter.estimate_usd(DiscoveryQuery(
            kind=QueryKind.CREATOR, value="x",
            platform=Platform(args.platform), limit=args.per_creator))
        plan = {
            "platform": args.platform,
            "candidates": len(rows),
            "cohort": [{"handle": h, "followers": f, "publications_now": p}
                       for h, _pl, f, p in chosen],
            "per_creator_estimate_usd": per,
            "cohort_estimate_usd": round(per * len(chosen), 4),
            "run_ceiling_usd": args.max_usd,
            "budget_before": before,
        }
        if not args.yes:
            plan["dry_run"] = ("re-run with --yes to spend. Research is "
                               "idempotent: creators already collected "
                               "recently cost nothing.")
            print(json.dumps(plan, indent=2, default=str))
            return 0

        spent_here = 0.0
        results = []
        for handle, platform, followers, _pubs in chosen:
            if spent_here + per > args.max_usd:
                results.append({
                    "handle": handle, "status": "stopped",
                    "message": (f"would pass the {args.max_usd} ceiling "
                                f"for this run; stopping with "
                                f"{len(results)} of {len(chosen)} done")})
                break
            out = research_creator(s, adapter, platform=platform,
                                   handle=handle,
                                   limit=args.per_creator)
            s.commit()
            billed = out.actual_usd or 0.0
            spent_here += billed
            results.append({
                "handle": handle, "followers": followers,
                "status": out.status, "reused": out.reused,
                "publications_seen": out.publications_seen,
                "billed_usd": out.actual_usd,
                "message": (out.message or "")[:160],
            })

        after = budget_state(s, provider="apify")
        depth = s.execute(text("""
            select pubs, count(*) from (
              select c.id, count(v.id) pubs from creators c
              left join videos v on v.creator_id = c.id
              where not c.is_own_account group by c.id) t
            group by pubs order by pubs desc""")).all()

    print(json.dumps({
        **plan,
        "results": results,
        "spent_this_run_usd": round(spent_here, 4),
        "budget_after": after,
        "competitor_depth_after": [
            {"publications": p, "creators": n} for p, n in depth],
    }, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
