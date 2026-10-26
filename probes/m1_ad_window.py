"""M1 — measure the ad window from the data instead of asking for a date.

The probe `m1_paid_organic.py` established the identity empirically:

    crossposted_views == views + facebook_views          (exactly, 5 of 5)
    total_views       == crossposted_views + residual

and that the residual was exactly 0 on the one probed publication that was
not a reel. If the residual is paid exposure, it should be ~0 before the ad
started and large afterwards — which dates the ad window from measurement
rather than recollection. This probe collects the three surface metrics for
every own-account Instagram publication and writes them out.

Throwaway (CLAUDE.md §1.2): the keeper version is the collector change.
"""

from __future__ import annotations

import json
import os
import sys
import time

sys.path.insert(0, "/home/rastlyn/virallab")

from sqlalchemy import create_engine, select, text  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from server.collectors.instagram_official import (  # noqa: E402
    InstagramOfficialAdapter, RateLimited,
)
from server.collectors.tokens import TokenStore  # noqa: E402
from server.db.models import OAuthToken  # noqa: E402

SURFACE = ["total_views", "crossposted_views", "facebook_views", "views"]
OUT = "/home/rastlyn/virallab/m1_ad_window.json"


def main() -> int:
    eng = create_engine(os.environ["VIRALLAB_DB_URL"], future=True)
    S = sessionmaker(eng, expire_on_commit=False, future=True)
    with S() as s:
        row = s.scalar(select(OAuthToken).where(
            OAuthToken.platform == "instagram"))
        acct = row.account_ref
        ad = InstagramOfficialAdapter(TokenStore(s))
        vids = s.execute(text("""
            select v.id, v.external_id, v.posted_at
            from videos v join creators c on c.id = v.creator_id
            where v.platform='instagram' and c.is_own_account
              and v.posted_at is not null
            order by v.posted_at desc""")).all()

        results = []
        for i, (vid, ext, posted) in enumerate(vids):
            rec = {"video_id": vid, "media_id": ext, "posted_at": str(posted)}
            try:
                payload = ad._get(acct, f"{ext}/insights",
                                  metric=",".join(SURFACE))
            except RateLimited as exc:
                rec["error"] = f"ratelimited: {exc}"
                results.append(rec)
                print(json.dumps({"stopped_at": i, "reason": "rate limit"}))
                break
            except Exception as exc:              # noqa: BLE001 - recorded
                # A whole-set failure means one name is unsupported for this
                # media product type. Retry singly so the supported ones are
                # still captured rather than the row being lost.
                rec["combined_error"] = str(exc)[:160]
                for m in SURFACE:
                    try:
                        p = ad._get(acct, f"{ext}/insights", metric=m)
                        d = p.get("data", [])
                        if d:
                            rec[m] = d[0].get("values", [{}])[0].get("value")
                    except Exception:             # noqa: BLE001
                        rec[m] = None
                results.append(rec)
                continue
            for item in payload.get("data", []):
                rec[item.get("name")] = (
                    item.get("values", [{}])[0].get("value"))
            results.append(rec)
            if i % 25 == 0:
                print(f"... {i}/{len(vids)}", flush=True)
                time.sleep(0.5)

    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(results, fh, indent=1, default=str)
    print(json.dumps({"written": OUT, "rows": len(results)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
