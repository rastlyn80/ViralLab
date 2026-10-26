"""Collect the surface decomposition for every own-account Instagram post.

Runs ON Ubuntu. One pass of the real collector path — not a bespoke writer —
so what lands in `metrics_snapshots` is exactly what a daily run will
produce from now on.

Why a one-off script rather than waiting for the daily schedule: the
retention analysis cannot carry an ad-exposure covariate until the surface
metrics exist for the publications it analyses, and `select_for_refresh`
caps a daily pass at 300 insight calls and prioritises recent media. This
walks the whole back catalogue once.

It appends snapshots. It does not mutate existing ones — the raw readings
are append-only by design (plan 5.4) and an earlier capture is evidence
about an earlier moment, not a draft of this one.
"""

from __future__ import annotations

import json
import logging
import os
import sys

sys.path.insert(0, "/home/rastlyn/virallab")

from sqlalchemy import create_engine, select, text  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from server.collectors.base import (  # noqa: E402
    finish_run, record_snapshot, start_run,
)
from server.collectors.instagram_official import (  # noqa: E402
    InstagramOfficialAdapter,
)
from server.collectors.tokens import ReauthRequired, TokenStore  # noqa: E402
from server.db.models import OAuthToken, Video  # noqa: E402

logging.basicConfig(level=logging.INFO,
                    format="%(levelname)s %(name)s %(message)s")
logger = logging.getLogger("virallab.backfill.surface")

LIMIT = int(os.environ.get("LIMIT", "0"))


def main() -> int:
    eng = create_engine(os.environ["VIRALLAB_DB_URL"], future=True)
    S = sessionmaker(eng, expire_on_commit=False, future=True)
    with S() as s:
        tok = s.scalar(select(OAuthToken).where(
            OAuthToken.platform == "instagram"))
        if tok is None:
            print(json.dumps({"error": "no instagram credential"}))
            return 1
        acct = tok.account_ref
        adapter = InstagramOfficialAdapter(TokenStore(s))

        rows = s.execute(text("""
            select v.external_id
            from videos v join creators c on c.id = v.creator_id
            where v.platform = 'instagram' and c.is_own_account
            order by v.posted_at desc nulls last""")).all()
        ids = [r[0] for r in rows]
        if LIMIT:
            ids = ids[:LIMIT]

        run = start_run(s, platform="instagram", account_ref=acct,
                        kind="surface_backfill")
        s.commit()

        by_ext = {v.external_id: v for v in s.scalars(
            select(Video).where(Video.platform == "instagram",
                                Video.external_id.in_(ids))).all()}

        written = with_surface = 0
        try:
            for m in adapter.fetch_metrics(acct, ids):
                video = by_ext.get(m.external_id)
                if video is None:
                    continue
                record_snapshot(s, video, m)
                written += 1
                if m.total_views is not None:
                    with_surface += 1
                if written % 50 == 0:
                    s.commit()
                    logger.info("committed %d snapshots", written)
        except ReauthRequired as exc:
            s.rollback()
            finish_run(s, run, status="failed", error=str(exc))
            s.commit()
            print(json.dumps({"error": f"reauth required: {exc}"}))
            return 1

        finish_run(s, run, status="ok", videos_seen=len(ids),
                   snapshots_written=written)
        s.commit()

    print(json.dumps({"requested": len(ids), "snapshots_written": written,
                      "with_surface_metrics": with_surface}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
