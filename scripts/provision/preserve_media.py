"""Run one Instagram media preservation pass by hand, and report coverage.

Runs ON Ubuntu. The scheduler does this automatically at the end of every
Instagram collection pass (server/scheduler/collect.py); this is for a
one-off run and for reconciling what is and is not preserved.

    .venv/bin/python preserve_media.py --report       # coverage only
    .venv/bin/python preserve_media.py                # one pass, then report
    .venv/bin/python preserve_media.py --max 200      # larger pass
"""
import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, "/home/rastlyn/virallab")
from sqlalchemy import create_engine, select  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from server.collectors.instagram_official import (  # noqa: E402
    InstagramOfficialAdapter)
from server.collectors.media_preservation import (  # noqa: E402
    coverage, preserve_instagram_media)
from server.collectors.tokens import TokenStore  # noqa: E402
from server.db.models import OAuthToken, Video  # noqa: E402


def report(session) -> None:
    cov = coverage(session, "instagram")
    print("\n=== INSTAGRAM MEDIA COVERAGE ===")
    print(f"  publications                 : {cov['total']}")
    for status, n in sorted(cov["by_status"].items()):
        print(f"    {status:<26} {n:>4}")
    print(f"  preservable (excludes types that are not video): "
          f"{cov['preservable']}")
    print(f"  preserved                    : {cov['preserved']}")
    print(f"  coverage of preservable      : {cov['coverage_pct']}%")
    if cov["exhausted_attempts"]:
        print(f"  exhausted all attempts       : "
              f"{cov['exhausted_attempts']}  <- need a look")
    print("\n  why media is not stored, by class:")
    for cls, n in sorted(cov["by_reason_class"].items()):
        meaning = {
            "not_a_video": "the publication is not a video - nothing to keep",
            "api_restricted": "the API returned the post but withheld media",
            "download_failed": "transient; retried on the next pass",
            "deduplicated": "identical media already stored under another id",
            "unknown": "not yet classified by a preservation pass",
        }.get(cls, "")
        print(f"    {cls:<18} {n:>4}   {meaning}")

    detail = session.scalars(select(Video).where(
        Video.platform == "instagram",
        Video.media_status != "available")).all()
    by_reason: dict[str, list] = {}
    for v in detail:
        by_reason.setdefault(v.media_reason or "unset", []).append(v)
    print("\n  full breakdown:")
    for reason, rows in sorted(by_reason.items()):
        print(f"    {reason:<34} {len(rows):>4}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", action="store_true",
                    help="report coverage without running a pass")
    ap.add_argument("--max", type=int, default=40,
                    help="most items to save in this pass")
    args = ap.parse_args()

    eng = create_engine(os.environ["VIRALLAB_DB_URL"], future=True)
    S = sessionmaker(eng, expire_on_commit=False, future=True)
    with S() as session:
        if not args.report:
            row = session.scalar(select(OAuthToken).where(
                OAuthToken.platform == "instagram"))
            if row is None:
                print("  no stored Instagram credential")
                return 1
            adapter = InstagramOfficialAdapter(TokenStore(session))
            root = Path(os.environ.get("VIRALLAB_MEDIA_ROOT")
                        or "/home/rastlyn/virallab/media")
            res = preserve_instagram_media(
                session, adapter, row.account_ref,
                media_root=root, max_items=args.max)
            print("=== PRESERVATION PASS ===")
            print(f"  considered     {res.considered}")
            print(f"  saved          {res.saved} "
                  f"({res.bytes_added/1e6:.1f} MB)")
            print(f"  deduplicated   {res.deduplicated}")
            print(f"  unsupported    {res.unsupported}")
            print(f"  api restricted {res.api_restricted}")
            print(f"  failed         {res.failed}")
            print(f"  skipped (attempts exhausted) {res.skipped_attempts}")
            if res.stopped_reason:
                print(f"  stopped: {res.stopped_reason}")
            if res.reasons:
                print(f"  reasons: {res.reasons}")
        report(session)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
