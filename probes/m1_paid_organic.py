"""M1 verification probe — does Instagram report paid and organic separately?

Throwaway (CLAUDE.md §1.2). Runs ON Ubuntu, where the Page token lives.

Documentary verification said three things that this probe has to confirm
empirically before any code depends on them (CLAUDE.md §2.4):

  1. `views` is organic-only, and `total_views` includes boosted exposure.
     If so, `total_views - views` is a paid-exposure signal obtainable from
     the EXISTING token with no `ads_read` scope at all.
  2. `crossposted_views` and `facebook_views` exist for this account, so the
     Facebook surface can be removed from that difference.
  3. Whether `reels_skip_rate` is organic-only is NOT documented either way.
     This probe cannot settle it; it records the field set so the question
     stays visible instead of being assumed away.

Also probes the Ads Insights reachability of the current token, which is the
`[OPEN]` question in BLUEPRINT.md §1.3.
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, "/home/rastlyn/virallab")

import httpx  # noqa: E402
from sqlalchemy import create_engine, select, text  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from server.collectors.instagram_official import (  # noqa: E402
    GRAPH, DEFAULT_VERSION, InstagramOfficialAdapter,
)
from server.collectors.tokens import TokenStore  # noqa: E402
from server.db.models import OAuthToken  # noqa: E402

#: Probed one at a time, deliberately. Meta fails the WHOLE insights call
#: when any single metric name is unsupported, so a combined request cannot
#: tell "this account has no boosted media" from "this metric does not
#: exist" — and that distinction is the entire point of the probe.
CANDIDATES = [
    "views", "total_views", "crossposted_views", "facebook_views",
    "reach", "reels_skip_rate", "ig_reels_avg_watch_time",
    "ig_reels_video_view_total_time", "reposts", "total_interactions",
]
SAMPLE = int(os.environ.get("SAMPLE", "6"))


def main() -> int:
    eng = create_engine(os.environ["VIRALLAB_DB_URL"], future=True)
    S = sessionmaker(eng, expire_on_commit=False, future=True)
    out: dict = {"api_version": DEFAULT_VERSION}
    with S() as s:
        row = s.scalar(select(OAuthToken).where(
            OAuthToken.platform == "instagram"))
        if row is None:
            print(json.dumps({"error": "no instagram credential"}))
            return 1
        acct = row.account_ref
        ad = InstagramOfficialAdapter(TokenStore(s))
        token = TokenStore(s).access_token("instagram", acct)

        # Recent reels first: a boosted post is recent by construction, and
        # older media predates the ad entirely.
        vids = s.execute(text("""
            select v.external_id, v.posted_at
            from videos v join creators c on c.id = v.creator_id
            where v.platform='instagram' and c.is_own_account
              and v.posted_at is not null
            order by v.posted_at desc limit :n"""), {"n": SAMPLE}).all()

        out["probed_media"] = []
        for ext, posted in vids:
            per: dict = {"media_id": ext, "posted_at": str(posted),
                         "supported": {}, "unsupported": {}}
            for metric in CANDIDATES:
                try:
                    payload = ad._get(acct, f"{ext}/insights", metric=metric)
                except Exception as exc:          # noqa: BLE001 - recorded
                    per["unsupported"][metric] = str(exc)[:200]
                    continue
                vals = payload.get("data", [])
                if not vals:
                    per["unsupported"][metric] = "empty data[]"
                    continue
                v = vals[0].get("values", [{}])[0].get("value")
                per["supported"][metric] = v
            out["probed_media"].append(per)

        # --- Ads Insights reachability with the CURRENT token -------------
        http = httpx.Client(timeout=30.0)
        hdr = {"Authorization": f"Bearer {token}"}
        probes = {}
        r = http.get(f"{GRAPH}/{DEFAULT_VERSION}/me/adaccounts",
                     params={"fields": "id,name,account_status"}, headers=hdr)
        probes["me/adaccounts"] = {"status": r.status_code,
                                   "body": r.text[:400]}
        r = http.get(f"{GRAPH}/{DEFAULT_VERSION}/debug_token",
                     params={"input_token": token,
                             "access_token": token}, headers=hdr)
        body = r.json() if r.content else {}
        scopes = (body.get("data") or {}).get("scopes")
        probes["token_scopes"] = scopes
        out["ads"] = probes

    print(json.dumps(out, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
