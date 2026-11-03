"""Study one creator the owner names. Runs ON the Ubuntu server.

The owner: *"tell me how can I train you on even more stuff by
others"*. The honest answer had a hole in it. He could ask the
platform to go **find** creators by hashtag, and he could ask it to
deepen the ones it already had - but if he watched a pianist he
admired and wanted that account studied, there was no way to say
so. Discovery was automatic or nothing.

That is the wrong shape. He knows this niche far better than a
hashtag search does, and the single most valuable thing he can add
is a name.

## What it does

One paid research pass over one creator's recent catalogue, through
the same `research_creator` the cohort builder uses - so the row is
created, the videos land, and everything downstream treats them
exactly like any other creator. There is no second path into the
corpus.

## What it will not do

* **Spend without `--yes`.** The default prints the estimate and
  exits, like every other paid script here.
* **Accept anything that is not a handle.** The pattern below is
  the whole of what may arrive. Nothing is passed through a shell -
  the operations runner builds an argv list - but a handle is the
  first string the owner can put into a command line from a
  browser, so it is checked on the way in as well.
* **Collect a private account.** `research_creator` goes through the
  public, logged-out provider path. An account that does not resolve
  publicly fails and is reported, never worked around
  (DEVELOPMENT_PLAN §2.3, ADR 0010).

    python follow_creator.py --handle somepianist          # estimate
    python follow_creator.py --handle somepianist --yes    # collect
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys

sys.path.insert(0, "/home/rastlyn/virallab")

from sqlalchemy import create_engine, select  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from server.budget import budget_state  # noqa: E402
from server.db.models import Creator, Video  # noqa: E402
from server.discovery.apify import ApifyDiscoveryAdapter  # noqa: E402
from server.research.service import research_creator  # noqa: E402

#: Both platforms allow letters, digits, underscore and period.
#: Instagram allows up to 30 characters, TikTok 24. Taking the
#: looser of the two and letting the platform reject the rest is
#: better than guessing a rule that changes.
HANDLE = re.compile(r"^[A-Za-z0-9_.]{2,30}$")


def clean(raw: str) -> str:
    """A handle, or an exception. Accepts what a person would paste.

    `@someone`, a profile URL and a bare handle all mean the same
    thing to him and should not be three different errors.
    """
    text = (raw or "").strip()
    if "/" in text:                        # a pasted profile URL
        parts = [p for p in text.split("?")[0].rstrip("/").split("/") if p]
        text = parts[-1] if parts else ""
    text = text.lstrip("@").strip()
    if not HANDLE.match(text):
        raise ValueError(
            f"{raw!r} does not look like a handle. Expected something "
            f"like 'somepianist' - letters, digits, underscore and "
            f"full stop, 2 to 30 characters.")
    return text


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--handle", required=True)
    ap.add_argument("--platform", default="tiktok",
                    choices=("tiktok", "instagram"))
    ap.add_argument("--posts", type=int, default=30,
                    help="how much of their catalogue to collect")
    ap.add_argument("--yes", action="store_true",
                    help="actually spend; without it this only estimates")
    args = ap.parse_args()

    try:
        handle = clean(args.handle)
    except ValueError as exc:
        print(json.dumps({"status": "rejected", "message": str(exc)}))
        return 2

    eng = create_engine(os.environ["VIRALLAB_DB_URL"], future=True)
    with sessionmaker(eng, future=True)() as s:
        existing = s.scalar(select(Creator).where(
            Creator.platform == args.platform, Creator.handle == handle))
        have = 0
        if existing is not None:
            have = len(list(s.scalars(select(Video.id).where(
                Video.creator_id == existing.id)).all()))

        adapter = ApifyDiscoveryAdapter(os.environ["APIFY_TOKEN"])
        from server.discovery.base import (DiscoveryQuery, Platform,
                                           QueryKind)

        estimate = adapter.estimate_usd(DiscoveryQuery(
            kind=QueryKind.CREATOR, value=handle,
            platform=Platform(args.platform), limit=args.posts))

        plan = {
            "handle": handle, "platform": args.platform,
            "posts_requested": args.posts,
            "already_known": existing is not None,
            "publications_already_held": have,
            "estimate_usd": estimate,
            "budget_before": budget_state(s),
        }
        if not args.yes:
            plan["status"] = "estimate_only"
            plan["message"] = (
                "Nothing was collected and nothing was spent. Research "
                "is idempotent, so a creator collected in the last "
                "twenty hours costs nothing to ask for again.")
            print(json.dumps(plan, indent=2, default=str))
            return 0

        out = research_creator(s, adapter, platform=args.platform,
                               handle=handle, limit=args.posts)
        s.commit()
        plan.update({
            "status": out.status,
            "reused": out.reused,
            "creator_id": out.creator_id,
            "publications_seen": out.publications_seen,
            "billed_usd": out.actual_usd or 0.0,
            "message": out.message,
            "budget_after": budget_state(s),
        })
        if out.status == "failed":
            # Said plainly rather than buried: the commonest cause
            # is a private or renamed account, and the owner can fix
            # that in a second if he is told which it was.
            plan["what_to_check"] = (
                "The handle may be spelled differently, the account "
                "may have been renamed, or it may not be public. "
                "Only publicly visible accounts are collected.")
        print(json.dumps(plan, indent=2, default=str))
        return 0 if out.status != "failed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
