"""Migrate authoritative application data from Windows to the Ubuntu server.

Ubuntu becomes the system of record; Windows keeps its copy untouched as a
fallback until the owner approves cleanup.

What this preserves, deliberately:

  * **Original primary keys.** Video ids carry through unchanged, so foreign
    keys need no remapping and any external reference stays valid.
  * **Original `captured_at` timestamps.** A re-backfill would stamp every
    snapshot with today's date and destroy the observation history — the one
    thing that makes fixed-age anchors possible later (plan 5.4). This copies
    observations; it does not re-collect them.

What it excludes: test residue, job-queue rows, worker heartbeats. Only
creators, videos, metrics_snapshots and the OAuth credential move.

    python scripts/migrate_to_server.py --dry-run
    python scripts/migrate_to_server.py --execute
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from sqlalchemy import bindparam, create_engine, text

sys.path.insert(0, str(Path(__file__).resolve().parent))
from remote import connect, env, run  # noqa: E402

REMOTE = "/home/rastlyn/virallab"

#: Synthetic rows from earlier test runs. Identified by handle, which is
#: unambiguous: no real creator is called this.
TEST_CREATOR_HANDLES = ("test_creator", "roundtrip_creator")


def remote_sql(c, sql: str, *, query: bool = True) -> str:
    import shlex
    flag = "--query " if query else ""
    rc, out, err = run(
        c, f"cd {REMOTE} && set -a && . ./.env && set +a && "
           f".venv/bin/python sql.py {flag}" + shlex.quote(sql), timeout=300)
    return (out or err).strip()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--execute", action="store_true",
                    help="without this, nothing is written")
    ap.add_argument("--local-url", default=None)
    args = ap.parse_args()

    e = env()
    local_url = args.local_url or e.get("VIRALLAB_DB_URL")
    if not local_url:
        print("VIRALLAB_DB_URL not set", file=sys.stderr)
        return 2

    eng = create_engine(local_url, future=True)
    c = connect()
    try:
        # ---- read source -------------------------------------------------
        with eng.connect() as lc:
            creators = [dict(r._mapping) for r in lc.execute(
                text("""
                select id, platform, external_id, handle, follower_count,
                       niche_tags, is_own_account, created_at
                from creators where handle not in :skip order by id""")
                .bindparams(bindparam("skip", expanding=True)),
                {"skip": list(TEST_CREATOR_HANDLES)})]
            keep_ids = [r["id"] for r in creators]
            if not keep_ids:
                print("no non-test creators to migrate", file=sys.stderr)
                return 1
            videos = [dict(r._mapping) for r in lc.execute(
                text("""
                select id, creator_id, platform, external_id, caption,
                       hashtags, posted_at, duration_s, source, media_status,
                       media_path, created_at
                from videos where creator_id in :ids order by id""")
                .bindparams(bindparam("ids", expanding=True)),
                {"ids": keep_ids})]
            vids = [v["id"] for v in videos]
            snaps = [dict(r._mapping) for r in lc.execute(
                text("""
                select id, video_id, captured_at, views, likes, comments,
                       shares, saves, is_rounded, raw
                from metrics_snapshots where video_id in :ids
                order by id""").bindparams(bindparam("ids", expanding=True)),
                {"ids": vids})] if vids else []
            tokens = [dict(r._mapping) for r in lc.execute(text("""
                select platform, account_ref, access_token, refresh_token,
                       expires_at, refresh_expires_at, scope, page_id,
                       needs_reauth, last_refreshed_at
                from oauth_tokens order by id"""))]

        print("SOURCE (Windows)")
        print(f"  creators          : {len(creators)}")
        print(f"  videos            : {len(videos)}")
        print(f"  metrics_snapshots : {len(snaps)}")
        print(f"  oauth credentials : {len(tokens)}  (values never printed)")
        if snaps:
            caps = sorted(s["captured_at"] for s in snaps)
            print(f"  snapshot captured_at range: {caps[0]} .. {caps[-1]}")
            print(f"  sum(views)        : {sum(s['views'] or 0 for s in snaps)}")

        print("\nTARGET (Ubuntu) before:")
        print("  " + remote_sql(c, "select (select count(*) from creators)||"
                                   "' creators, '||(select count(*) from videos)||"
                                   "' videos, '||(select count(*) from metrics_snapshots)||"
                                   "' snapshots'"))

        if not args.execute:
            print("\nDRY RUN - nothing written. Re-run with --execute.")
            return 0

        # ---- write target -------------------------------------------------
        # Payload travels as a file over SFTP, never on a command line, so no
        # token value reaches shell history or a process list.
        payload = {"creators": creators, "videos": videos,
                   "snapshots": snaps, "tokens": tokens}
        tmp = Path(__file__).resolve().parent.parent / ".migration_payload.json"
        tmp.write_text(json.dumps(payload, default=str), encoding="utf-8")
        sftp = c.open_sftp()
        sftp.put(str(tmp), f"{REMOTE}/.migration_payload.json")
        sftp.chmod(f"{REMOTE}/.migration_payload.json", 0o600)
        sftp.close()
        tmp.unlink()
        print("\n  payload uploaded (0600, deleted locally)")

        rc, out, err = run(
            c, f"cd {REMOTE} && set -a && . ./.env && set +a && "
               f".venv/bin/python apply_migration.py", timeout=600)
        print(out.strip() or err.strip())
        run(c, f"rm -f {REMOTE}/.migration_payload.json", timeout=60)
        print("  payload removed from the server")
        return rc
    finally:
        c.close()


if __name__ == "__main__":
    raise SystemExit(main())
