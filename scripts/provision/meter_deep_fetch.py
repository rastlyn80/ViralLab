"""Measure what a DEEP per-creator fetch actually costs. Runs ON Ubuntu.

The question this exists to answer is a $228/year one: does collecting
20–50 publications each for 10–20 competitor creators fit inside Apify's
free $5/month, or does it need a paid plan?

Two things are unmeasured and the answer turns on both:

1. **Does the 10-item cap bite?** `CAPS_TIKTOK` records that
   apidojo/tiktok-scraper "caps a run at 10 items regardless of maxItems"
   on its free plan. That is the ACTOR's own free tier, not Apify's
   account plan, so upgrading the Apify subscription may or may not lift
   it — this run asks for 30 and counts what comes back.
2. **What does a run cost including compute?** The per-item rate
   ($0.0003/item for this actor, measured in Phase 0A) covers results.
   Apify also bills compute units per run, and depth collection means
   many runs. `settled_cost_usd` reads Apify's own `usageTotalUsd` after
   accounting settles, which is the billed total, not an estimate.

Everything goes through the real `research_creator` path, so the budget
reservation, the advisory lock and the settle discipline are exercised
exactly as production would. One creator, one run. Costs cents.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys

sys.path.insert(0, "/home/rastlyn/virallab")

from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from server.budget import budget_state  # noqa: E402
from server.discovery.apify import ApifyDiscoveryAdapter  # noqa: E402
from server.research.service import research_creator  # noqa: E402

logging.basicConfig(level=logging.INFO,
                    format="%(levelname)s %(name)s %(message)s")
logger = logging.getLogger("virallab.meter")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--handle", help="creator to fetch deeply; defaults to "
                                     "the competitor with the most "
                                     "publications already collected")
    ap.add_argument("--platform", default="tiktok",
                    choices=["tiktok", "instagram"])
    ap.add_argument("--limit", type=int, default=30,
                    help="how many publications to ASK for. The point is "
                         "to ask for more than 10 and see what arrives.")
    ap.add_argument("--yes", action="store_true",
                    help="required: this spends real money")
    args = ap.parse_args()

    eng = create_engine(os.environ["VIRALLAB_DB_URL"], future=True)
    S = sessionmaker(eng, expire_on_commit=False, future=True)

    with S() as s:
        before = budget_state(s, provider="apify")
        handle = args.handle
        if not handle:
            row = s.execute(text("""
                select c.handle, count(v.id) n
                from creators c join videos v on v.creator_id = c.id
                where not c.is_own_account and c.platform = :p
                group by c.handle order by n desc, c.handle limit 1"""),
                {"p": args.platform}).one_or_none()
            if row is None:
                print(json.dumps({"error": "no competitor creator stored "
                                           f"for {args.platform}"}))
                return 1
            handle = row[0]

        plan = {"platform": args.platform, "handle": handle,
                "asked_for": args.limit,
                "budget_before": before}
        if not args.yes:
            plan["dry_run"] = ("re-run with --yes to actually spend; this "
                               "would start one paid Apify run")
            print(json.dumps(plan, indent=2, default=str))
            return 0

        out = research_creator(s, ApifyDiscoveryAdapter(
            os.environ["APIFY_TOKEN"]), platform=args.platform,
            handle=handle, limit=args.limit, refresh=True)
        s.commit()

        after = budget_state(s, provider="apify")
        got = s.execute(text("""
            select count(*) from videos v join creators c on c.id=v.creator_id
            where c.handle = :h and c.platform = :p"""),
            {"h": handle, "p": args.platform}).scalar()

        billed = (out.actual_usd if out.actual_usd is not None
                  else None)
        result = {
            **plan,
            "status": out.status,
            "message": out.message,
            "estimated_usd": out.estimated_usd,
            "billed_usd": billed,
            "publications_now_stored_for_creator": got,
            "budget_after": after,
            # The two numbers the decision turns on.
            "returned_this_run": out.publications_seen,
            "new_this_run": out.publications_new,
            "cap_appears_to_bite": (out.publications_seen <= 10 < args.limit),
        }
        seen = out.publications_seen
        if billed is not None and seen:
            result["billed_per_item_usd"] = round(billed / seen, 6)
            # 15 creators x 30 publications is the shape M5/M6 need: enough
            # per creator to compute that creator's own baseline, which is
            # what one-publication-per-creator cannot support.
            result["projected_450_items_usd"] = round(billed / seen * 450, 4)
            runs_needed = (450 + seen - 1) // seen
            result["runs_needed_for_450_items"] = runs_needed
            result["projected_450_items_by_run_cost_usd"] = round(
                billed * runs_needed, 4)

    print(json.dumps(result, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
