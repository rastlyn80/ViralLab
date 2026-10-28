"""Repertoire, cadence and novelty across every publication. Free.

Runs ON the server. BLUEPRINT §8.12 puts this first because it needs
no media and lands on **both** sides of the comparison, which is the
half that has been starving: competitors have 958 captions and 958
post times and almost no features derived from either.

Unlike the other extractors, two of the three groups here are
**order-dependent**. Cadence and novelty describe a publication by
what its creator had already posted at that moment, so the pass
walks each creator's catalogue in date order and never looks
forward. Using a creator's later videos to describe an earlier one
would leak the future into a feature, which is the thing CLAUDE.md
§3.9 forbids and the kind of leak that produces a beautiful model
that cannot predict anything.

    python extract_context.py --dry-run
    python extract_context.py --yes
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

from server.db.models import Video, VideoFeature, VideoFeatureRun  # noqa
from server.features import context as cx  # noqa: E402

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)-7s %(message)s")
logger = logging.getLogger("extract_context")

KIND = "context"


def _existing(session, video_ids: list[int]) -> dict[int, dict]:
    """The composer and form already detected, per publication."""
    rows = session.execute(
        select(VideoFeature.video_id, VideoFeature.feature,
               VideoFeature.value_text, VideoFeature.value_bool)
        .where(VideoFeature.video_id.in_(video_ids),
               VideoFeature.feature.in_(("composer", "work_form_named")),
               VideoFeature.status == "ok")).all()
    out: dict[int, dict] = {}
    for vid, feature, text, flag in rows:
        out.setdefault(vid, {})[feature] = text if text is not None else flag
    return out


def _store(session, video_id: int, run_id: int, feature: str,
           value) -> None:
    row = session.scalar(select(VideoFeature).where(
        VideoFeature.video_id == video_id,
        VideoFeature.feature == feature,
        VideoFeature.method_version == cx.METHOD_VERSION))
    if row is None:
        row = VideoFeature(video_id=video_id, feature=feature,
                           method_version=cx.METHOD_VERSION)
        session.add(row)
    row.value_num = row.value_bool = row.value_text = row.value_json = None
    if feature in cx.BOOLEANS:
        row.value_bool = bool(value)
    elif feature in cx.TEXTS:
        row.value_text = str(value)
    else:
        row.value_num = float(value)
    row.method = "deterministic"
    row.note = cx.describes(feature)
    row.extracted_at = datetime.now(timezone.utc)
    row.run_id = run_id
    row.status = "ok"
    row.error = None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--yes", action="store_true")
    args = ap.parse_args()

    engine = create_engine(os.environ["VIRALLAB_DB_URL"], future=True)
    Session = sessionmaker(engine, expire_on_commit=False, future=True)

    with Session() as s:
        videos = list(s.execute(
            select(Video.id, Video.creator_id, Video.posted_at,
                   Video.caption)
            .where(Video.posted_at.is_not(None))
            .order_by(Video.creator_id, Video.posted_at)).all())
        prior = _existing(s, [v.id for v in videos])

    by_creator: dict[int, list] = {}
    for v in videos:
        by_creator.setdefault(v.creator_id, []).append(v)

    plan = {"publications": len(videos), "creators": len(by_creator),
            "method_version": cx.METHOD_VERSION, "cost_usd": 0.0,
            "recent_window": cx.RECENT_WINDOW}
    if args.dry_run or not args.yes:
        plan["dry_run"] = "re-run with --yes"
        print(json.dumps(plan, indent=1))
        return 0
    print(json.dumps(plan, indent=1))

    with Session() as s:
        run = VideoFeatureRun(method_version=cx.METHOD_VERSION, kind=KIND,
                              status="running")
        s.add(run)
        s.commit()
        run_id = run.id

    written = 0
    with Session() as s:
        for cid, pubs in by_creator.items():
            # Date order, and only ever looking backwards.
            pubs = sorted(pubs, key=lambda p: p.posted_at)
            seen_composers: list[str | None] = []
            seen_forms: list[bool] = []
            seen_dates: list[datetime] = []
            for p in pubs:
                facts = prior.get(p.id, {})
                composer = facts.get("composer")
                form = bool(facts.get("work_form_named"))

                feats: dict[str, object] = {}
                feats |= cx.repertoire(p.caption, composer)
                feats |= cx.cadence(p.posted_at, list(seen_dates))
                feats |= cx.novelty(
                    composer=composer, form_named=form,
                    recent_composers=seen_composers[-cx.RECENT_WINDOW:],
                    recent_forms=seen_forms[-cx.RECENT_WINDOW:])

                for feature, value in feats.items():
                    _store(s, p.id, run_id, feature, value)
                if feats:
                    written += 1

                seen_composers.append(composer)
                seen_forms.append(form)
                seen_dates.append(p.posted_at)
            s.commit()

    with Session() as s:
        r = s.get(VideoFeatureRun, run_id)
        r.status = "ok"
        r.finished_at = datetime.now(timezone.utc)
        s.commit()
        per = s.execute(
            select(VideoFeature.feature, func.count(VideoFeature.id))
            .where(VideoFeature.method_version == cx.METHOD_VERSION)
            .group_by(VideoFeature.feature)
            .order_by(func.count(VideoFeature.id).desc())).all()
        tiers = s.execute(
            select(VideoFeature.value_num, func.count(VideoFeature.id))
            .where(VideoFeature.method_version == cx.METHOD_VERSION,
                   VideoFeature.feature == "piece_recognisability")
            .group_by(VideoFeature.value_num)).all()

    print(json.dumps({
        **plan, "described": written,
        "per_feature": {f: n for f, n in per},
        "recognisability": {f"tier_{int(t)}": n for t, n in tiers},
    }, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
