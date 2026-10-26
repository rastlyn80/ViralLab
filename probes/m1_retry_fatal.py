"""Re-probe the publications whose surface call died with Meta error -1.

`m1_ad_window.py` recorded `facebook_views: null` for 12 publications whose
per-metric retry hit "Meta error -1: Fatal" — a server fault rather than an
unsupported metric. Treating that null as zero would have attributed the
whole of `total_views - views` to paid exposure, and two of the twelve carry
the largest residuals in the corpus. Retried with backoff here so the
difference between "no Facebook views" and "the call failed" is settled by
measurement.

**Result (2026-10-26): the failure is PERSISTENT, not transient.** All 12
were retried four times each with increasing backoff and `facebook_views`
failed every time; `crossposted_views` failed alongside it in every case.
The Facebook surface for these publications is unobtainable, so their
residual is `unknown` rather than paid — see `ad_exposure._from_surface`.
"""

from __future__ import annotations

import json
import os
import sys
import time

sys.path.insert(0, "/home/rastlyn/virallab")

from sqlalchemy import create_engine, select  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from server.collectors.instagram_official import (  # noqa: E402
    InstagramOfficialAdapter,
)
from server.collectors.tokens import TokenStore  # noqa: E402
from server.db.models import OAuthToken  # noqa: E402

IDS = json.loads(os.environ["MEDIA_IDS"])
METRICS = ["views", "total_views", "facebook_views", "crossposted_views"]


def main() -> int:
    eng = create_engine(os.environ["VIRALLAB_DB_URL"], future=True)
    S = sessionmaker(eng, expire_on_commit=False, future=True)
    out = []
    with S() as s:
        acct = s.scalar(select(OAuthToken).where(
            OAuthToken.platform == "instagram")).account_ref
        ad = InstagramOfficialAdapter(TokenStore(s))
        for mid in IDS:
            rec = {"media_id": mid}
            for m in METRICS:
                for attempt in range(4):
                    try:
                        p = ad._get(acct, f"{mid}/insights", metric=m)
                        d = p.get("data", [])
                        rec[m] = (d[0].get("values", [{}])[0].get("value")
                                  if d else None)
                        break
                    except Exception as exc:          # noqa: BLE001
                        rec[f"{m}_err"] = str(exc)[:120]
                        time.sleep(1.5 * (attempt + 1))
            out.append(rec)
    print(json.dumps(out, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
