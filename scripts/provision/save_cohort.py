"""Save the researched creators as a cohort. Runs ON Ubuntu. Free.

M6's acceptance criterion is a *saved* cohort, not an ad-hoc list, and
`docs/SCHEMA_PLAN.md` §3 requires a control cohort before any
cross-creator pattern is graded above Hypothesised. The machinery has
existed since Phase 5; the collection was empty.

Membership is earned, not assumed. A creator joins only when enough
of their back catalogue is held to support a baseline of their own -
comparing the owner against somebody's single video is what the
corpus already did, and it is what this is correcting.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, "/home/rastlyn/virallab")

from sqlalchemy import create_engine, select, text  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from server.db.models import CreatorCollection  # noqa: E402
from server.research import collections as coll  # noqa: E402

NAME = "Classical Piano"

#: Below this a creator has no baseline of their own, so including
#: them would mean comparing the owner against a single data point
#: and calling it a cohort.
MIN_PUBLICATIONS = 5


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", default=NAME)
    ap.add_argument("--min-publications", type=int,
                    default=MIN_PUBLICATIONS)
    args = ap.parse_args()

    eng = create_engine(os.environ["VIRALLAB_DB_URL"], future=True)
    S = sessionmaker(eng, expire_on_commit=False, future=True)

    with S() as s:
        c = s.scalar(select(CreatorCollection).where(
            CreatorCollection.name == args.name))
        if c is None:
            c = coll.create(s, name=args.name,
                            purpose="research")
            s.flush()

        rows = s.execute(text("""
            select c.id, c.handle, c.platform, c.follower_count,
                   count(v.id) pubs
            from creators c join videos v on v.creator_id = c.id
            where not c.is_own_account
            group by c.id, c.handle, c.platform, c.follower_count
            having count(v.id) >= :n
            order by c.follower_count desc nulls last"""),
            {"n": args.min_publications}).all()

        added = []
        for cid, handle, platform, followers, pubs in rows:
            coll.add_member(
                s, c.id, cid,
                reason="deep_catalogue",
                notes=(f"{pubs} publications held; "
                       f"{followers if followers is not None else 'unknown'}"
                       f" followers at research time"))
            added.append({"handle": handle, "platform": platform,
                          "followers": followers, "publications": pubs})
        s.commit()
        view = coll.view(s, c.id)

    print(json.dumps({
        "collection": args.name,
        "collection_id": c.id,
        "min_publications": args.min_publications,
        "members": len(added),
        "meets_m6_minimum_of_10": len(added) >= 10,
        "composition": added,
        "follower_range": {
            "min": min((a["followers"] for a in added
                        if a["followers"] is not None), default=None),
            "max": max((a["followers"] for a in added
                        if a["followers"] is not None), default=None),
        },
        "publications_in_cohort": sum(a["publications"] for a in added),
        "view_members": len(getattr(view, "members", []) or []),
    }, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
