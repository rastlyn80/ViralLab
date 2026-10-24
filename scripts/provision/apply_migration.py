"""Apply the migration payload on the Ubuntu server. Runs ON the server.

Everything happens in ONE transaction: either the server ends up with the
complete dataset or it is left exactly as it was. A partial migration would
be worse than none, because the counts would look plausible.

Original ids and `captured_at` timestamps are inserted verbatim — this
copies observations, it does not re-collect them.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, "/home/rastlyn/virallab")

from sqlalchemy import bindparam, create_engine, text  # noqa: E402

PAYLOAD = Path("/home/rastlyn/virallab/.migration_payload.json")
TEST_HANDLES = ("test_creator", "roundtrip_creator")


def main() -> int:
    data = json.loads(PAYLOAD.read_text(encoding="utf-8"))
    eng = create_engine(os.environ["VIRALLAB_DB_URL"], future=True)

    with eng.begin() as c:
        # 1. Remove synthetic residue only. Real data would be caught by the
        #    guard below, not silently overwritten.
        existing = c.execute(
            text("select count(*) from creators where handle not in :h")
            .bindparams(bindparam("h", expanding=True)),
            {"h": list(TEST_HANDLES)}).scalar()
        if existing:
            print(f"  ABORT: {existing} non-test creator(s) already on the "
                  "server. Refusing to overwrite real data.")
            return 1

        c.execute(text("delete from batch_videos"))
        c.execute(text("delete from analysis_results"))
        c.execute(text("delete from analysis_batches"))
        c.execute(text("delete from metrics_snapshots"))
        c.execute(text("delete from videos"))
        c.execute(text("delete from creators where handle in :h")
                  .bindparams(bindparam("h", expanding=True)),
                  {"h": list(TEST_HANDLES)})
        print("  cleared test residue")

        # 2. Insert preserving primary keys.
        for r in data["creators"]:
            c.execute(text("""
                insert into creators (id, platform, external_id, handle,
                    follower_count, niche_tags, is_own_account, created_at)
                values (:id,:platform,:external_id,:handle,:follower_count,
                        cast(:niche_tags as jsonb),:is_own_account,:created_at)
            """), {**r, "niche_tags": json.dumps(r["niche_tags"])
                   if r["niche_tags"] is not None else None})
        print(f"  creators inserted : {len(data['creators'])}")

        for r in data["videos"]:
            c.execute(text("""
                insert into videos (id, creator_id, platform, external_id,
                    caption, hashtags, posted_at, duration_s, source,
                    media_status, media_path, created_at)
                values (:id,:creator_id,:platform,:external_id,:caption,
                        cast(:hashtags as jsonb),:posted_at,:duration_s,
                        :source,:media_status,:media_path,:created_at)
            """), {**r, "hashtags": json.dumps(r["hashtags"])
                   if r["hashtags"] is not None else None})
        print(f"  videos inserted   : {len(data['videos'])}")

        for r in data["snapshots"]:
            c.execute(text("""
                insert into metrics_snapshots (id, video_id, captured_at,
                    views, likes, comments, shares, saves, is_rounded, raw)
                values (:id,:video_id,:captured_at,:views,:likes,:comments,
                        :shares,:saves,:is_rounded, cast(:raw as jsonb))
            """), {**r, "raw": json.dumps(r["raw"])
                   if r["raw"] is not None else None})
        print(f"  snapshots inserted: {len(data['snapshots'])}")

        for r in data["tokens"]:
            c.execute(text("""
                insert into oauth_tokens (platform, account_ref, access_token,
                    refresh_token, expires_at, refresh_expires_at, scope,
                    page_id, needs_reauth, last_refreshed_at)
                values (:platform,:account_ref,:access_token,:refresh_token,
                        :expires_at,:refresh_expires_at,:scope,:page_id,
                        :needs_reauth,:last_refreshed_at)
                on conflict (platform, account_ref) do update set
                    access_token = excluded.access_token,
                    refresh_token = excluded.refresh_token,
                    expires_at = excluded.expires_at,
                    refresh_expires_at = excluded.refresh_expires_at,
                    scope = excluded.scope,
                    needs_reauth = excluded.needs_reauth
            """), r)
        print(f"  credentials moved : {len(data['tokens'])} (values not shown)")

        # 3. Sequences must advance past the inserted ids, or the next insert
        #    collides with a migrated row.
        for tbl in ("creators", "videos", "metrics_snapshots", "oauth_tokens"):
            c.execute(text(
                f"select setval(pg_get_serial_sequence('{tbl}','id'), "
                f"coalesce((select max(id) from {tbl}), 1))"))
        print("  sequences advanced past migrated ids")

    # 4. Verify in a fresh connection, after commit.
    with eng.connect() as c:
        v = c.execute(text("""
            select (select count(*) from creators),
                   (select count(*) from videos),
                   (select count(distinct external_id) from videos),
                   (select count(*) from metrics_snapshots),
                   (select sum(views) from metrics_snapshots),
                   (select sum(likes) from metrics_snapshots),
                   (select sum(comments) from metrics_snapshots),
                   (select sum(shares) from metrics_snapshots),
                   (select min(captured_at) from metrics_snapshots),
                   (select max(captured_at) from metrics_snapshots),
                   (select count(*) from oauth_tokens),
                   (select count(*) from videos v
                      left join creators cr on cr.id=v.creator_id
                      where cr.id is null),
                   (select count(*) from metrics_snapshots s
                      left join videos vi on vi.id=s.video_id
                      where vi.id is null)""")).one()
        print("\nVERIFY (server, post-commit)")
        print(f"  creators            : {v[0]}")
        print(f"  videos              : {v[1]}  distinct ext: {v[2]}")
        print(f"  snapshots           : {v[3]}")
        print(f"  sum views/likes     : {v[4]} / {v[5]}")
        print(f"  sum comments/shares : {v[6]} / {v[7]}")
        print(f"  captured_at range   : {v[8]} .. {v[9]}")
        print(f"  credentials         : {v[10]}")
        print(f"  orphan videos       : {v[11]}")
        print(f"  orphan snapshots    : {v[12]}")
        if v[11] or v[12]:
            print("  FOREIGN KEY INTEGRITY FAILURE")
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
