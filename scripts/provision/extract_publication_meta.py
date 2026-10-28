"""Extract timing, caption and repertoire features. Free, no provider.

Runs ON the server. Costs nothing and calls nothing: every input is
metadata already in the database, for competitors as much as for the
owner.

This exists because the owner's actual requirement - research
"repertoire, camera angle, overlay, hooks, time of posting, and
anything that can be possibly put on a reel" - had **zero features**
for three of those five, on either side of the comparison, while the
visual half had thirty-one. The visual half is also the expensive
half and the one currently blocked by the provider's monthly limit,
so the cheap half had been skipped in favour of the costly one.

    python extract_publication_meta.py --dry-run
    python extract_publication_meta.py --yes
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, "/home/rastlyn/virallab")

from sqlalchemy import create_engine, func, select  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from server.db.models import (Creator, Video, VideoFeature,  # noqa: E402
                              VideoFeatureRun)
from server.features import publication_meta as pm  # noqa: E402

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)-7s %(message)s")
logger = logging.getLogger("extract_meta")

KIND = "publication_meta"


def _store(session, video: Video, run_id: int, feature: str,
           value) -> None:
    row = session.scalar(select(VideoFeature).where(
        VideoFeature.video_id == video.id,
        VideoFeature.feature == feature,
        VideoFeature.method_version == pm.METHOD_VERSION))
    if row is None:
        row = VideoFeature(video_id=video.id, feature=feature,
                           method_version=pm.METHOD_VERSION)
        session.add(row)
    row.value_num = row.value_bool = row.value_text = row.value_json = None
    if feature in pm.BOOLEANS:
        row.value_bool = bool(value)
    elif feature in pm.TEXTS:
        row.value_text = str(value)
    else:
        row.value_num = float(value)
    # Arithmetic on metadata, not a model's opinion. The distinction
    # matters downstream: `method` is what tells the catalogue
    # whether a measurement could in principle be wrong about what it
    # saw, and these cannot be.
    row.method = "deterministic"
    row.model = None
    row.uncertainty = None
    row.note = pm.describes(feature)
    row.extracted_at = datetime.now(timezone.utc)
    row.run_id = run_id
    row.status = "ok"
    row.error = None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--yes", action="store_true")
    args = ap.parse_args()

    engine = create_engine(os.environ["VIRALLAB_DB_URL"], future=True)
    Session = sessionmaker(engine, expire_on_commit=False, future=True)

    with Session() as s:
        videos = list(s.scalars(
            select(Video).order_by(Video.id)).all())
        done = set(s.scalars(
            select(VideoFeature.video_id).where(
                VideoFeature.method_version == pm.METHOD_VERSION)).all())
        own = set(s.scalars(
            select(Video.id).join(Creator, Creator.id == Video.creator_id)
            .where(Creator.is_own_account.is_(True))).all())

    todo = [v for v in videos if v.id not in done]
    if args.limit:
        todo = todo[:args.limit]
    plan = {"publications": len(videos), "already_done": len(done),
            "to_do": len(todo),
            "own": sum(1 for v in todo if v.id in own),
            "competitor": sum(1 for v in todo if v.id not in own),
            "method_version": pm.METHOD_VERSION, "cost_usd": 0.0}
    if args.dry_run or not args.yes:
        plan["dry_run"] = "re-run with --yes"
        print(json.dumps(plan, indent=1))
        return 0
    print(json.dumps(plan, indent=1))

    with Session() as s:
        run = VideoFeatureRun(method_version=pm.METHOD_VERSION, kind=KIND,
                              status="running")
        s.add(run)
        s.commit()
        run_id = run.id

    written = skipped = 0
    with Session() as s:
        for i, v in enumerate(todo, 1):
            feats = pm.extract(caption=v.caption, posted_at=v.posted_at,
                               duration_s=v.duration_s)
            if not feats:
                skipped += 1
                continue
            video = s.get(Video, v.id)
            for feature, value in feats.items():
                _store(s, video, run_id, feature, value)
            written += 1
            if i % 400 == 0:
                s.commit()
                logger.info("%d/%d", i, len(todo))
        s.commit()

    with Session() as s:
        r = s.get(VideoFeatureRun, run_id)
        r.status = "ok" if not skipped else "partial"
        r.finished_at = datetime.now(timezone.utc)
        s.commit()
        rows = s.scalar(select(func.count(VideoFeature.id)).where(
            VideoFeature.method_version == pm.METHOD_VERSION)) or 0
        per = s.execute(
            select(VideoFeature.feature, func.count(VideoFeature.id))
            .where(VideoFeature.method_version == pm.METHOD_VERSION)
            .group_by(VideoFeature.feature)
            .order_by(func.count(VideoFeature.id).desc())).all()

    print(json.dumps({
        **plan, "described": written,
        "skipped_no_metadata": skipped,
        "feature_rows": rows,
        "per_feature": {f: n for f, n in per},
    }, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
