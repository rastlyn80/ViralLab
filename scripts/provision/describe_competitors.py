"""Ask the wide question set of other creators' video. Runs ON Ubuntu.

The last parameter the owner named that the product could not
measure about anyone but himself. `extract`/`analyse_competitor_media`
gives on-screen text, background complexity and audio - which covers
"overlay" - and says nothing about camera framing, who is in shot,
whether there is sheet music, or how formal the room looks.

    python describe_competitors.py --dry-run
    python describe_competitors.py --limit 20 --max-usd 1.00 --yes

## Why this is a separate script

`describe_library.py` is own-account only, by a check on
`is_own_account` rather than a flag, and that safeguard is left
exactly as it is. Adding a `--competitors` switch to it would turn a
structural guarantee into a command-line argument.

The gate here is stronger than a flag anyway, and it is not one this
script invents:

    A competitor publication can be described only while a LIVE
    `transient_media` row exists for it.

Which means it must have been fetched under ADR 0010 (public,
logged-out, no credentials) and must not yet have passed the ADR 0009
six-hour deadline. There is no path to describing a competitor video
we are not currently and legitimately holding, and nothing here can
cause a fetch.

## Cost

Measured, not projected: the own-account pass settled $3.985 across
523 calls, **$0.0076 a publication** on Haiku 4.5. Every call
reserves against `cloud_vision` before it is made and settles on the
token counts the API returns, `--max-usd` stops the run, and the
category cap underneath stops it again.

## What a row here may support

The same rule as everywhere: each feature lands with the reliability
grade `vision_questions` assigns it, and only `verified` fields may
enter a statistic. Describing more videos more richly is not
evidence that anything causes anything.
"""

from __future__ import annotations

import argparse
import base64
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, "/home/rastlyn/virallab")
# This script's own directory, so `describe_library` imports whether
# it is run from the repository root or from here. Without it the
# run depends on PYTHONPATH being set, which is the kind of thing
# that works when you test it and fails at 3am.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from sqlalchemy import create_engine, select                      # noqa: E402
from sqlalchemy.orm import sessionmaker                           # noqa: E402

from server import budget as bud                                  # noqa: E402
from server.ai import vision_task                                 # noqa: E402
from server.ai import wide_vision as wv                           # noqa: E402
from server.ai.base import AnalysisRequest                        # noqa: E402
from server.api.frames_api import MAX_FRAMES, encode_frames       # noqa: E402
from server.db.models import (Creator, TransientMedia, Video,     # noqa: E402
                              VideoFeature, VideoFeatureRun)

# Reused rather than copied: `_store` carries the reliability grade
# and the deliberate absence of a confidence number, and two copies
# of that would drift.
from describe_library import _provider, _store                    # noqa: E402

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)-7s %(message)s")
logger = logging.getLogger("describe_competitors")

KIND = "vision_wide_competitor"


def holdings(session) -> list[tuple[Video, str]]:
    """Competitor publications we are legitimately holding right now.

    The join to `transient_media` is the permission check. A row
    that is live means the file was fetched under ADR 0010 and has
    not passed its ADR 0009 deadline; no live row means no
    description, and this script cannot cause a fetch.
    """
    rows = session.execute(
        select(Video, TransientMedia.path)
        .join(Creator, Creator.id == Video.creator_id)
        .join(TransientMedia, TransientMedia.video_id == Video.id)
        .where(Creator.is_own_account.is_(False),
               TransientMedia.deleted_at.is_(None))
        .order_by(Video.id)).all()
    return [(v, p) for v, p in rows]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="haiku", choices=("haiku", "sonnet"))
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--max-usd", type=float, default=1.00,
                    help="hard ceiling for THIS run, under the category cap")
    ap.add_argument("--max-output-tokens", type=int, default=1200)
    ap.add_argument("--width", type=int, default=512)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--yes", action="store_true")
    args = ap.parse_args()

    provider = _provider(args.model)
    ok, why = provider.available()
    if not ok:
        print(f"cannot run: {why}")
        return 1

    engine = create_engine(os.environ["VIRALLAB_DB_URL"], future=True)
    Session = sessionmaker(engine, expire_on_commit=False, future=True)

    with Session() as s:
        held = holdings(s)
        done = set(s.scalars(
            select(VideoFeature.video_id).where(
                VideoFeature.method_version == wv.METHOD_VERSION,
                VideoFeature.status == "ok")).all())

    todo = [(v, p) for v, p in held if v.id not in done]
    if args.limit:
        todo = todo[:args.limit]

    per_call = (3645 / 1e6 * provider.USD_PER_MTOK_IN
                + args.max_output_tokens / 1e6 * provider.USD_PER_MTOK_OUT)
    plan = {
        "model": provider.MODEL,
        "questions_asked": len(wv.ASKABLE),
        "held_right_now": len(held),
        "already_described": len([v for v, _ in held if v.id in done]),
        "to_do": len(todo),
        "estimate_usd": round(len(todo) * per_call, 4),
        "script_ceiling_usd": args.max_usd,
        "method_version": wv.METHOD_VERSION,
        "permission": ("only publications with a live transient_media "
                       "row, i.e. fetched under ADR 0010 and not yet "
                       "past the ADR 0009 deadline"),
    }
    if args.dry_run or not args.yes:
        plan["dry_run"] = "re-run with --yes to spend"
        print(json.dumps(plan, indent=1))
        return 0
    print(json.dumps(plan, indent=1))

    spent, described, failed, skipped = 0.0, 0, 0, 0
    t0 = time.time()
    with Session() as s:
        run = VideoFeatureRun(method_version=wv.METHOD_VERSION, kind=KIND,
                              status="running")
        s.add(run)
        s.commit()
        run_id = run.id

    for video, media_path in todo:
        path = Path(media_path or "")
        if not path.exists():
            # The reaper got there first. Normal, not an error: the
            # whole point of a six-hour deadline is that it fires.
            skipped += 1
            continue
        stamps = vision_task.FramePlan().timestamps(
            video.duration_s)[:MAX_FRAMES]
        try:
            frames = encode_frames(path, stamps, width=args.width)
        except Exception as exc:             # noqa: BLE001 - recorded
            logger.warning("%s: frame decode failed: %s", video.id, exc)
            failed += 1
            continue
        if not frames:
            skipped += 1
            continue

        blobs = [(float(f["at_s"]), base64.b64decode(f["jpeg_b64"]))
                 for f in frames]
        req = AnalysisRequest(
            task="hook_visual_wide", schema=wv.schema(),
            prompt=wv.prompt(), frames=blobs, video_id=video.id,
            max_output_tokens=args.max_output_tokens)
        est = provider.estimate_usd(req)
        if spent + est > args.max_usd:
            logger.warning("stopping at --max-usd %.2f (spent %.4f)",
                           args.max_usd, spent)
            break

        with Session() as s:
            try:
                res = bud.reserve(
                    s, provider=provider.name, estimated_usd=est,
                    idempotency_key=(f"describe_competitors:{video.id}:"
                                     f"{wv.METHOD_VERSION}"),
                    category="cloud_vision",
                    detail={"script": "describe_competitors"})
                s.commit()
                rid = res.id
            except Exception as exc:         # noqa: BLE001 - reported
                logger.error("budget refused at %s: %s", video.id, exc)
                break

        try:
            out = provider.analyse(req)
        except Exception as exc:             # noqa: BLE001 - recorded
            with Session() as s:
                bud.release(s, rid, reason="failed")
                s.commit()
            logger.warning("%s: model call failed: %s", video.id, exc)
            failed += 1
            continue

        # `usage.actual_usd` is what the provider billed; the
        # estimate is never promoted into that field, so falling back
        # to `est` here is explicit rather than accidental.
        actual = float((out.usage.actual_usd if out.usage else None)
                       or est)
        with Session() as s:
            bud.settle(s, rid, actual_usd=actual)
            s.commit()
        spent += actual

        parsed = wv.parse(out.raw_text or "")
        rows = wv.feature_rows(parsed)
        if not rows:
            logger.warning("%s: nothing parseable in the reply", video.id)
            failed += 1
            continue
        # A reply cut off by `max_tokens` still has usable answers in
        # front of the cut; the note says so rather than the row
        # pretending to be a clean read.
        note = ("truncated reply; answers salvaged field by field"
                if parsed.get("_truncated") else None)
        with Session() as s:
            v = s.get(Video, video.id)
            r = s.get(VideoFeatureRun, run_id)
            for feature, value in rows.items():
                _store(s, v, r, feature, value,
                       model=provider.MODEL, note=note)
            s.commit()
        described += 1

    with Session() as s:
        run = s.get(VideoFeatureRun, run_id)
        run.status = "ok" if described else "partial"
        run.finished_at = datetime.now(timezone.utc)
        s.commit()
        remaining = len([v for v, _ in holdings(s)])

    print(json.dumps({
        **plan, "described": described, "failed": failed,
        "skipped_expired": skipped,
        "spent_usd": round(spent, 4),
        "seconds": round(time.time() - t0, 1),
        "still_held": remaining,
    }, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
