"""Analyse whatever competitor media is in quarantine, then delete it.

Runs ON Ubuntu. The second half of M2: acquisition brings bytes in,
this derives the features and releases the file once they are stored.

    python analyse_competitor_media.py            # analyse and release
    python analyse_competitor_media.py --coverage # just report

Prints the transient audit afterwards, so the six-hour guarantee is
reported rather than assumed, and the M2 coverage counts alongside it.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, "/home/rastlyn/virallab")

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from server.collectors import transient  # noqa: E402
from server.features import competitor  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--coverage", action="store_true",
                    help="report M2 coverage and exit")
    ap.add_argument("--limit", type=int, default=50)
    args = ap.parse_args()

    eng = create_engine(os.environ["VIRALLAB_DB_URL"], future=True)
    S = sessionmaker(eng, expire_on_commit=False, future=True)
    media_root = Path(os.environ["VIRALLAB_MEDIA_ROOT"])

    with S() as s:
        if args.coverage:
            print(json.dumps({"coverage": competitor.coverage(s),
                              "audit": transient.audit(s)},
                             indent=2, default=str))
            return 0
        out = competitor.analyse_held(s, media_root=media_root,
                                      limit=args.limit)
        s.commit()
        payload = asdict(out)
        payload["coverage"] = competitor.coverage(s)
    print(json.dumps(payload, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
