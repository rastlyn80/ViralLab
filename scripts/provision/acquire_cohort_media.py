"""Measure the cohort: acquire, extract, release, repeat. Runs ON Ubuntu.

The cohort has 294 publications and no features. Nothing can be
compared across creators until the videos have been looked at, and
looking at them means holding the bytes for as long as extraction
takes and no longer.

## Balanced by creator, on purpose

`acquire_competitor_media.py` takes the newest files across the whole
corpus. That is right for metering a price and wrong here: one
prolific creator would supply most of the sample and the "cross-
creator" comparison would mostly be a comparison with them. This
takes at most `--per-creator` from each cohort member, so the sample
reflects the cohort rather than its busiest member.

## The six-hour rule shapes the loop

Competitor media is transient (ADR 0009, `collectors/transient.py`).
So this works in batches: acquire a batch, extract features from it,
confirm the features are stored, release the files, move on. Nothing
is held while the next batch downloads, and a crash leaves at most
one batch to be reaped rather than the whole run.

Two ceilings, both enforced: `--max-usd` for this run, and the
`competitor_media` category cap underneath it.

    python acquire_cohort_media.py --per-creator 20            # plan
    python acquire_cohort_media.py --per-creator 20 --yes --max-usd 1.00
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
from server.features import competitor as comp  # noqa: E402

COHORT = "Classical Piano"

#: How many files are held at once. Small enough that a crash costs
#: one batch, large enough that the per-run overhead is amortised.
BATCH = 20

CANDIDATE_SQL = """
with ranked as (
  select v.id, v.creator_id,
         row_number() over (partition by v.creator_id
                            order by v.posted_at desc nulls last,
                                     v.id desc) rn
  from videos v
       join creator_collection_members m on m.creator_id = v.creator_id
       join creator_collections cc on cc.id = m.collection_id
  where cc.name = :cohort
    and v.platform = 'tiktok'
    and v.media_status = 'absent'
    and not exists (
      select 1 from video_features f
      where f.video_id = v.id and f.method_version = :mv)
)
select id from ranked where rn <= :per order by creator_id, rn
"""


def main() -> int:
    from shared import hookfeat

    ap = argparse.ArgumentParser()
    ap.add_argument("--cohort", default=COHORT)
    ap.add_argument("--per-creator", type=int, default=20)
    ap.add_argument("--batch", type=int, default=BATCH)
    ap.add_argument("--max-usd", type=float, default=1.00)
    ap.add_argument("--yes", action="store_true",
                    help="required: this spends real money")
    args = ap.parse_args()

    eng = create_engine(os.environ["VIRALLAB_DB_URL"], future=True)
    S = sessionmaker(eng, expire_on_commit=False, future=True)
    media_root = Path(os.environ["VIRALLAB_MEDIA_ROOT"])

    with S() as s:
        before = budget_state(s, provider="apify")
        ids = [r[0] for r in s.execute(
            text(CANDIDATE_SQL),
            {"cohort": args.cohort, "per": args.per_creator,
             "mv": hookfeat.METHOD_VERSION}).all()]

    plan = {
        "cohort": args.cohort,
        "per_creator": args.per_creator,
        "candidates": len(ids),
        "batch": args.batch,
        "run_ceiling_usd": args.max_usd,
        "estimate_usd": round(len(ids) * 0.00404, 4),
        "budget_before": before,
    }
    if not args.yes:
        plan["dry_run"] = ("re-run with --yes to spend. Price is the "
                           "measured $0.00404 per file.")
        print(json.dumps(plan, indent=2, default=str))
        return 0

    spent = 0.0
    batches = []
    for start in range(0, len(ids), args.batch):
        chunk = ids[start:start + args.batch]
        with S() as s:
            state = budget_state(s, provider="apify")
            if spent >= args.max_usd:
                batches.append({"stopped": "run ceiling reached",
                                "spent_usd": round(spent, 4)})
                break
            videos = [s.get(Video, i) for i in chunk]
            videos = [v for v in videos if v is not None]
            out = cm.acquire(s, videos=videos,
                             token=os.environ["APIFY_TOKEN"],
                             media_root=media_root,
                             max_files=len(videos))
            s.commit()
            spent += out.billed_usd or 0.0

            # Extract while the bytes are legitimately held, confirm
            # the features landed, and let `analyse_held` release
            # only what is genuinely complete.
            an = comp.analyse_held(s, media_root=media_root,
                                   limit=len(videos) + 5)
            s.commit()
            batches.append({
                "requested": out.requested, "acquired": out.acquired,
                "failed": out.failed, "billed_usd": out.billed_usd,
                "complete": an.complete, "partial": an.partial,
                "analysis_failed": an.failed, "expired": an.expired,
                "released": an.released,
                "features_written": an.features_written,
            })

    with S() as s:
        after = budget_state(s, provider="apify")
        audit = transient.audit(s)
        coverage = comp.coverage(s)

    print(json.dumps({
        **plan, "batches": batches,
        "spent_this_run_usd": round(spent, 4),
        "budget_after": after,
        "transient_audit": audit,
        "coverage": coverage,
    }, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
