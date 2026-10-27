"""Acquire competitor media, and report what it cost. Runs ON Ubuntu.

Defaults to **one** file. The per-file price of the provider's
video-download add-on has never been measured, and M2's whole economics
rest on it: at $0.003 a file, twenty publications is six cents; at $0.30
a file it is six dollars and does not fit the ceiling at all.

    python acquire_competitor_media.py                 # plan only
    python acquire_competitor_media.py --yes           # acquire one
    python acquire_competitor_media.py --yes --max 20  # the real batch

Always prints the budget before and after, and runs the transient audit
at the end so the six-hour guarantee is reported rather than assumed.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, "/home/rastlyn/virallab")

from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from server.budget import budget_state  # noqa: E402
from server.collectors import competitor_media as cm  # noqa: E402
from server.collectors import transient  # noqa: E402
from server.db.models import Video  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max", type=int, default=1,
                    help="how many files to acquire (default 1)")
    ap.add_argument("--yes", action="store_true",
                    help="required: this spends real money")
    ap.add_argument("--sweep", action="store_true",
                    help="run the TTL sweep and exit, acquiring nothing")
    args = ap.parse_args()

    eng = create_engine(os.environ["VIRALLAB_DB_URL"], future=True)
    S = sessionmaker(eng, expire_on_commit=False, future=True)
    media_root = Path(os.environ["VIRALLAB_MEDIA_ROOT"])

    with S() as s:
        if args.sweep:
            out = transient.sweep(s, media_root=media_root)
            s.commit()
            print(json.dumps({"sweep": out,
                              "audit": transient.audit(s)}, indent=2))
            return 0

        before = budget_state(s, provider="apify")
        # Competitor TikTok publications, newest first. Only TikTok has a
        # canonical URL this module can rebuild today.
        ids = [r[0] for r in s.execute(text("""
            select v.id
            from videos v join creators c on c.id = v.creator_id
            where not c.is_own_account and v.platform = 'tiktok'
              and v.media_status = 'absent'
            order by v.id desc limit :n"""), {"n": args.max * 4}).all()]
        videos = [s.get(Video, i) for i in ids]

        plan = {
            "candidates": len(videos),
            "will_acquire_at_most": args.max,
            "competitor_media_cap_usd": before["by_category"].get(
                "competitor_media") if "by_category" in before else None,
            "budget_before": before,
            "quarantine_root": str(transient.quarantine_root()),
            "ttl_hours": transient.TTL.total_seconds() / 3600,
        }
        if not args.yes:
            plan["dry_run"] = ("re-run with --yes to spend; the add-on "
                               "price is unmeasured, so start with --max 1")
            print(json.dumps(plan, indent=2, default=str))
            return 0

        out = cm.acquire(s, videos=videos, token=os.environ["APIFY_TOKEN"],
                         media_root=media_root, max_files=args.max)
        s.commit()

        after = budget_state(s, provider="apify")
        result = {
            **plan,
            "requested": out.requested,
            "acquired": out.acquired,
            "skipped_already_held": out.skipped_already_held,
            "failed": out.failed,
            "billed_usd": out.billed_usd,
            "bytes_total": out.bytes_total,
            "errors": out.errors[:5],
            "budget_after": after,
            "audit": transient.audit(s),
        }
        if out.billed_usd and out.acquired:
            per = out.billed_usd / out.acquired
            result["billed_per_file_usd"] = round(per, 6)
            result["projected_20_files_usd"] = round(per * 20, 4)
            result["fits_competitor_media_cap_for_20"] = (per * 20) <= 3.0

    print(json.dumps(result, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
