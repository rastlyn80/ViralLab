"""Fetch other creators' public video with yt-dlp. Costs nothing.

ADR 0010. Runs ON Ubuntu. Prints the plan and exits unless `--yes`.

    python acquire_competitor_free.py                 # plan only
    python acquire_competitor_free.py --yes --max 5   # a careful start
    python acquire_competitor_free.py --sweep         # delete expired

Deliberately slow: ten seconds between fetches, a per-run ceiling,
and the whole pass stops the moment the platform rate-limits rather
than continuing to knock.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, "/home/rastlyn/virallab")

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from server.collectors import competitor_archive as ca  # noqa: E402
from server.collectors import transient  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max", type=int, default=ca.DEFAULT_MAX_PER_RUN)
    ap.add_argument("--creator-id", type=int, default=None)
    ap.add_argument("--pause", type=float, default=ca.MIN_SECONDS_BETWEEN)
    ap.add_argument("--yes", action="store_true")
    ap.add_argument("--sweep", action="store_true",
                    help="delete expired files and exit")
    args = ap.parse_args()

    engine = create_engine(os.environ["VIRALLAB_DB_URL"], future=True)
    Session = sessionmaker(engine, expire_on_commit=False, future=True)

    with Session() as s:
        if args.sweep:
            swept = transient.sweep(s)
            s.commit()
            print(json.dumps({"swept": swept,
                              "coverage": ca.coverage(s)}, indent=1,
                             default=str))
            return 0

        ok, detail = ca.tool_available()
        plan = {
            "tool_available": ok, "tool_detail": detail,
            "max_videos": args.max, "pause_seconds": args.pause,
            "cost_usd": 0.0,
            "permission_basis": ca.PERMISSION_BASIS,
            "coverage_before": ca.coverage(s),
        }
        if not args.yes:
            plan["dry_run"] = "re-run with --yes"
            print(json.dumps(plan, indent=1, default=str))
            return 0
        print(json.dumps(plan, indent=1, default=str))

        res = ca.acquire(s, max_videos=args.max,
                         creator_id=args.creator_id, pause_s=args.pause)
        s.commit()
        out = {**plan, "result": res.as_dict(),
               "coverage_after": ca.coverage(s),
               # The six-hour guarantee reported rather than assumed.
               "transient_audit": transient.audit(s)}
    print(json.dumps(out, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
