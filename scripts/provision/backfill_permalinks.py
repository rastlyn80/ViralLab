"""Fill in Instagram permalinks for publications already collected.

Runs ON Ubuntu. Free: `permalink` has been in
`instagram_official.MEDIA_FIELDS` since the first version of that
collector, so every past response carried it and every past response
threw it away. This asks the media edge for the same field set again
and keeps the one value that was being dropped.

TikTok needs nothing here. Its canonical URL is derived from handle
plus publication id at read time, and for the owner's account it is
deliberately NOT derived at all - the stored handle is the API's
`open_id` rather than a username, so a URL built from it would 404.

    python backfill_permalinks.py --dry-run
    python backfill_permalinks.py
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, "/home/rastlyn/virallab")

from sqlalchemy import create_engine, select, text  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from server.collectors.instagram_official import (  # noqa: E402
    InstagramOfficialAdapter)
from server.db.models import Creator, Video  # noqa: E402
from server.collectors.tokens import TokenStore  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    eng = create_engine(os.environ["VIRALLAB_DB_URL"], future=True)
    S = sessionmaker(eng, expire_on_commit=False, future=True)

    out: dict = {}
    with S() as s:
        creator = s.scalar(select(Creator).where(
            Creator.platform == "instagram",
            Creator.is_own_account.is_(True)))
        if creator is None:
            print(json.dumps({"error": "no own Instagram creator"}))
            return 1

        before = s.scalar(select(Video).where(
            Video.creator_id == creator.id,
            Video.permalink.is_(None)))
        missing = len(list(s.scalars(select(Video.id).where(
            Video.creator_id == creator.id,
            Video.permalink.is_(None))).all()))
        out["missing_before"] = missing
        if args.dry_run or before is None:
            out["dry_run"] = True
            print(json.dumps(out, indent=1))
            return 0

        adapter = InstagramOfficialAdapter(TokenStore(s))
        # The credential is keyed by the Instagram user id, not by the
        # handle: `oauth_tokens.account_ref` holds 1784143..., and
        # passing the handle raises ReauthRequired for a credential
        # that is present and valid.
        account_ref = s.execute(text(
            "select account_ref from oauth_tokens "
            "where platform='instagram' limit 1")).scalar()
        if not account_ref:
            print(json.dumps({"error": "no instagram credential"}))
            return 1
        out["account_ref"] = account_ref
        by_external = {
            v.external_id: v for v in s.scalars(select(Video).where(
                Video.creator_id == creator.id)).all()}

        filled = seen = 0
        for nv in adapter.fetch_account_videos(account_ref):
            seen += 1
            row = by_external.get(nv.external_id)
            if row is None or not nv.permalink:
                continue
            if row.permalink != nv.permalink:
                row.permalink = nv.permalink
                filled += 1
        s.commit()

        out.update({
            "listed": seen,
            "filled": filled,
            "missing_after": len(list(s.scalars(select(Video.id).where(
                Video.creator_id == creator.id,
                Video.permalink.is_(None))).all())),
        })
    print(json.dumps(out, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
