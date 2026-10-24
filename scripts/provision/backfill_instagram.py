"""Backfill Instagram Reels into the authoritative database. Runs ON Ubuntu."""
import os, sys
sys.path.insert(0, "/home/rastlyn80/virallab".replace("rastlyn80","rastlyn"))
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker
from server.collectors.instagram_official import InstagramOfficialAdapter
from server.collectors.tokens import TokenStore
from server.collectors.base import (
    upsert_creator, upsert_video, record_snapshot, start_run, finish_run)
from server.db.models import OAuthToken, Video

eng = create_engine(os.environ["VIRALLAB_DB_URL"], future=True)
S = sessionmaker(eng, expire_on_commit=False, future=True)
with S() as s:
    store = TokenStore(s)
    row = s.scalar(select(OAuthToken).where(OAuthToken.platform == "instagram"))
    if row is None:
        raise SystemExit("no instagram credential")
    acct = row.account_ref
    ad = InstagramOfficialAdapter(store)

    run = start_run(s, platform="instagram", account_ref=acct, kind="backfill")
    creator = s.scalar(select(__import__("server.db.models", fromlist=["Creator"]).Creator)
                       .where(text("platform='instagram'")))
    handle = creator.handle if creator else acct
    creator = upsert_creator(s, platform="instagram", handle=handle,
                             external_id=acct, is_own_account=True)
    seen = new = 0
    reels = 0
    for v in ad.fetch_account_videos(acct):
        mt = (v.raw or {}).get("media_product_type")
        if mt == "REELS":
            reels += 1
        _, created = upsert_video(s, creator, v, source="official_api")
        seen += 1; new += int(created)
    s.flush()
    print(f"  media listed: {seen} (REELS: {reels}), new rows: {new}")

    by_ext = {v.external_id: v for v in s.scalars(
        select(Video).where(Video.platform == "instagram")).all()}
    snaps = 0
    for m in ad.fetch_metrics(acct, list(by_ext)):
        t = by_ext.get(m.external_id)
        if t is not None:
            record_snapshot(s, t, m); snaps += 1
    finish_run(s, run, status="ok", videos_seen=seen, videos_new=new,
               snapshots_written=snaps)
    s.commit()
    print(f"  snapshots written: {snaps}")
