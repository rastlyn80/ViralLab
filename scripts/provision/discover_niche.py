"""Widen the creator pool, so within-creator analysis has creators.

Runs ON the server. The owner chose observational scale over the
prospective experiment of M9, and the binding constraint turned out
not to be money but creators: `build_cohort.py` could offer only 30
candidates, because 30 is every competitor creator with a follower
count that anything has ever found.

Within-creator analysis compares each publication against the same
creator's own median, which is what removes the follower-count
confound that makes raw view counts useless. It needs creators with
depth, and enough of them.

So this runs the curated niche searches, which cost about $0.75 per
200 results, and reports how many creators each one actually added.
A search that adds nobody is worth knowing about: the niche is small
and the same accounts dominate every hashtag in it.

    python discover_niche.py --plan
    python discover_niche.py --yes --max-usd 6.00
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, "/home/rastlyn/virallab")

from sqlalchemy import create_engine, func, select  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from server.budget import budget_state  # noqa: E402
from server.db.models import Creator  # noqa: E402
from server.discovery.apify import ApifyDiscoveryAdapter  # noqa: E402
from server.research import discover as disc  # noqa: E402

#: The curated niche, expanded. Hashtags first: they are cheaper per
#: result and in this niche they are where the creators are.
SEARCHES = [
    ("hashtag", "pianotok"),
    ("hashtag", "classicalpiano"),
    ("hashtag", "pianist"),
    ("keyword", "classical piano"),
    ("keyword", "virtuoso pianist"),
    ("keyword", "piano performance"),
]


def _creators(session) -> int:
    return session.scalar(
        select(func.count(Creator.id))
        .where(Creator.is_own_account.is_(False))) or 0


def stop_if_out_of_credit(adapter) -> dict | None:
    """The provider's own allowance, or a refusal to print and exit.

    Apify enforces a monthly limit on the account that is nothing
    to do with this project's cap, and when it is reached every
    call fails identically. The owner met that as "Did not finish,
    exited with code 1" under advice to check a handle that was
    perfectly correct.

    Returns the allowance when there is room, or a dict to print
    and exit 1 when there is not. Unknown is treated as room: not
    being able to ask is not a reason to refuse to work.
    """
    allowance = adapter.allowance()
    if not allowance.get("exhausted"):
        return allowance
    return {
        "status": "provider_out_of_credit",
        "allowance": allowance,
        "message": (
            f"Apify has stopped serving this account: it has used "
            f"${allowance.get('used_usd')} of its "
            f"${allowance.get('limit_usd')} monthly limit. Nothing "
            f"was collected and nothing was charged."),
        "what_to_check": (
            "This is a limit on your Apify account, not on anything "
            "here. Raise it in the Apify console or wait for their "
            "billing month to reset."),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--platform", default="tiktok")
    ap.add_argument("--limit", type=int, default=200)
    ap.add_argument("--max-usd", type=float, default=6.00)
    ap.add_argument("--yes", action="store_true")
    args = ap.parse_args()

    engine = create_engine(os.environ["VIRALLAB_DB_URL"], future=True)
    Session = sessionmaker(engine, expire_on_commit=False, future=True)
    adapter = ApifyDiscoveryAdapter(token=os.environ["APIFY_TOKEN"])

    allowance = stop_if_out_of_credit(adapter)
    if allowance.get("status") == "provider_out_of_credit":
        print(json.dumps(allowance, indent=1, default=str))
        return 1

    with Session() as s:
        before = _creators(s)
        state = budget_state(s)
    plan = {"searches": len(SEARCHES), "limit_each": args.limit,
            "creators_before": before, "ceiling_usd": args.max_usd,
            "provider_allowance": allowance, "budget": state}
    if not args.yes:
        plan["dry_run"] = "re-run with --yes to spend"
        print(json.dumps(plan, indent=1, default=str))
        return 0
    print(json.dumps(plan, indent=1, default=str))

    spent = 0.0
    rows = []
    for kind, value in SEARCHES:
        with Session() as s:
            running = _creators(s)
        if spent >= args.max_usd:
            print(f"stopping at --max-usd {args.max_usd}")
            break
        with Session() as s:
            try:
                out = disc.discover(s, adapter, kind=kind,
                                    platform=args.platform, value=value,
                                    limit=args.limit,
                                    niche="classical_piano")
                s.commit()
            except Exception as exc:       # noqa: BLE001 - reported
                print(f"  {kind:8s} {value:20s} FAILED "
                      f"{str(exc)[:110]}")
                continue
        cost = (out.actual_usd if out.actual_usd is not None
                else out.estimated_usd) or 0.0
        spent += cost
        with Session() as s:
            after = _creators(s)
        rows.append({"kind": kind, "value": value, "status": out.status,
                     "creators_new": after - running,
                     "usd": round(cost, 4)})
        print(f"  {kind:8s} {value:20s} {out.status:10s} "
              f"+{after - running:3d} creators  {cost:.4f} usd")

    with Session() as s:
        after = _creators(s)
        state = budget_state(s)
    print(json.dumps({"searches_run": len(rows), "rows": rows,
                      "creators_before": before, "creators_after": after,
                      "creators_added": after - before,
                      "billed_usd": round(spent, 4),
                      "budget_after": state}, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
