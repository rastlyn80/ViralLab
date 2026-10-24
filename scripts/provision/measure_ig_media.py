"""Measure Instagram media storage before downloading. Runs ON Ubuntu.

Deliberately measurement-only. It fetches FRESH media_urls (the ones from
the backfill are signed CDN links that expire, so a stored URL is not
evidence of anything) and reads only HTTP headers — `Content-Length` via a
ranged GET — so the full corpus size is known before a byte of video is
written.
"""

from __future__ import annotations

import os
import statistics
import sys

sys.path.insert(0, "/home/rastlyn/virallab")

import httpx  # noqa: E402
from sqlalchemy import create_engine, select, text  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from server.collectors.instagram_official import (  # noqa: E402
    MEDIA_FIELDS, InstagramOfficialAdapter,
)
from server.collectors.tokens import TokenStore  # noqa: E402
from server.db.models import OAuthToken  # noqa: E402

SAMPLE = int(os.environ.get("SAMPLE", "25"))


def main() -> int:
    eng = create_engine(os.environ["VIRALLAB_DB_URL"], future=True)
    S = sessionmaker(eng, expire_on_commit=False, future=True)
    with S() as s:
        row = s.scalar(select(OAuthToken).where(
            OAuthToken.platform == "instagram"))
        if row is None:
            print("no instagram credential")
            return 1
        acct = row.account_ref
        adapter = InstagramOfficialAdapter(TokenStore(s))

        total = s.execute(text(
            "select count(*) from videos where platform='instagram'")).scalar()
        print(f"  stored Instagram posts: {total}")

        # 1. Are the backfill URLs still usable? Expect: no.
        stale = s.execute(text("""
            select raw->>'media_url' from videos v
            join metrics_snapshots s on s.video_id = v.id
            where v.platform='instagram' limit 1""")).scalar()
        print("  (backfill media_url was never persisted - by design, since "
              "it expires)")

        # 2. Fresh URLs, header-only size reads.
        print(f"\n  fetching fresh media_urls and measuring {SAMPLE} of them…")
        fresh = []
        for v in adapter.fetch_account_videos(acct):
            if v.media_url:
                fresh.append((v.external_id, v.media_url,
                              (v.raw or {}).get("media_product_type")))
            if len(fresh) >= SAMPLE:
                break
        print(f"  got {len(fresh)} fresh URLs")

        sizes, failed = [], 0
        h = httpx.Client(timeout=45, follow_redirects=True)
        for ext, url, kind in fresh:
            try:
                r = h.get(url, headers={"Range": "bytes=0-1023"})
                if r.status_code not in (200, 206):
                    failed += 1
                    continue
                cr = r.headers.get("Content-Range")
                size = (int(cr.split("/")[-1]) if cr and "/" in cr
                        else int(r.headers.get("Content-Length", 0)))
                if size:
                    sizes.append(size)
            except httpx.HTTPError:
                failed += 1

        if not sizes:
            print("  could not measure any media")
            return 1

        mean = statistics.fmean(sizes)
        med = statistics.median(sizes)
        print(f"\n  measured {len(sizes)} files ({failed} failed)")
        print(f"    min / median / max : {min(sizes)/1e6:.2f} / "
              f"{med/1e6:.2f} / {max(sizes)/1e6:.2f} MB")
        print(f"    mean               : {mean/1e6:.2f} MB")
        print(f"\n  PROJECTED for all {total} posts:")
        print(f"    median-based : {med*total/1e9:.2f} GB")
        print(f"    mean-based   : {mean*total/1e9:.2f} GB")
        p90 = sorted(sizes)[int(len(sizes)*0.9)-1] if len(sizes) >= 10 else max(sizes)
        print(f"    p90-based    : {p90*total/1e9:.2f} GB  <- provision against this")

        df = os.statvfs("/home/rastlyn")
        free_gb = df.f_bavail * df.f_frsize / 1e9
        print(f"\n  server free space: {free_gb:.1f} GB")
        print(f"  headroom after a p90-sized download: "
              f"{free_gb - p90*total/1e9:.1f} GB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
