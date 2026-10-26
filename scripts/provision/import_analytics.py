"""Import an owner-supplied analytics export. Runs ON Ubuntu.

    python import_analytics.py --file tiktok.csv --source tiktok_studio \
        --platform tiktok --exported-at 2026-10-26

Prints what was mapped, what was ignored, and how many rows attached to a
known publication. Idempotent on the file's bytes, so re-running is safe.

See docs/setup/analytics-export.md for what to export and from where.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import date

sys.path.insert(0, "/home/rastlyn/virallab")

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from server.analytics.imports import SOURCES, ImportRefused, ingest  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", required=True)
    ap.add_argument("--source", required=True, choices=list(SOURCES))
    ap.add_argument("--platform", required=True,
                    choices=["tiktok", "instagram"])
    ap.add_argument("--exported-at", required=True,
                    help="the date YOU downloaded the file (YYYY-MM-DD). "
                         "What the figures are age-anchored against.")
    ap.add_argument("--dry-run", action="store_true",
                    help="parse and report, write nothing")
    args = ap.parse_args()

    eng = create_engine(os.environ["VIRALLAB_DB_URL"], future=True)
    S = sessionmaker(eng, expire_on_commit=False, future=True)
    with S() as s:
        try:
            _, summary = ingest(s, path=args.file, source=args.source,
                                platform=args.platform,
                                exported_at=date.fromisoformat(
                                    args.exported_at))
        except ImportRefused as exc:
            print(json.dumps({"refused": str(exc)}, indent=2))
            return 2
        if args.dry_run:
            s.rollback()
            summary["dry_run"] = True
        else:
            s.commit()
    print(json.dumps(summary, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
