"""Describe the owner's whole library with the wide question set.

Runs ON THE SERVER: the media and the spend ledger are both there,
and this needs no GPU because the model is an API call.

    python scripts/provision/describe_library.py --dry-run
    python scripts/provision/describe_library.py --limit 20 --yes
    python scripts/provision/describe_library.py --yes

## What this is for

The product has 42 visual questions it can ask and two it is allowed
to trust, and that gap is why the reports read thin. This closes the
*description* half of it: one pass over the 483 own-account
publications that already have media on disk, asking 31 questions
each instead of 6, with a model measured to be worth asking.

It closes none of the *claim* half. Every row lands with the
reliability grade `vision_questions` gives it, and only `verified`
fields may enter a statistic. A better description of more videos is
not evidence that anything causes anything.

## Safeguards, none of them optional

* **Resumable.** A publication with a row under this method version
  is skipped, so an interrupted run continues rather than restarts
  and nothing is paid for twice.
* **Idempotent.** Each reservation carries
  `describe_library:{video_id}:{method_version}`; a retry after a
  crash matches an existing settlement instead of billing again.
* **Budget-guarded.** Every call reserves before it is made and
  settles with the token counts the API returns. `--max-usd` is this
  script's own ceiling and the ledger's category cap sits underneath;
  the lower binds and neither is bypassable from here.
* **Own account only.** A check on `is_own_account`, not a flag.
  Competitor media was deleted under ADR 0009 and this must never
  become the path that re-acquires it.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, "/home/rastlyn/virallab")

from sqlalchemy import create_engine, func, select                # noqa: E402
from sqlalchemy.orm import sessionmaker                           # noqa: E402

from server import budget as bud                                  # noqa: E402
from server.ai import vision_task, wide_vision as wv              # noqa: E402
from server.ai.base import AnalysisRequest                        # noqa: E402
from server.ai.providers import (AnthropicSonnetVisionProvider,   # noqa: E402
                                 AnthropicVisionProvider)
from server.api.frames_api import MAX_FRAMES, encode_frames       # noqa: E402
from server.db.models import (Creator, Video, VideoFeature,       # noqa: E402
                              VideoFeatureRun)

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)-7s %(message)s")
logger = logging.getLogger("describe_library")

KIND = "vision_wide"


def _provider(name: str):
    return (AnthropicSonnetVisionProvider() if name == "sonnet"
            else AnthropicVisionProvider())


def _store(session, video: Video, run: VideoFeatureRun, feature: str,
           value, *, model: str, note: str | None) -> None:
    row = session.scalar(select(VideoFeature).where(
        VideoFeature.video_id == video.id,
        VideoFeature.feature == feature,
        VideoFeature.method_version == wv.METHOD_VERSION))
    if row is None:
        row = VideoFeature(video_id=video.id, feature=feature,
                           method_version=wv.METHOD_VERSION)
        session.add(row)
    row.value_num = row.value_bool = row.value_text = row.value_json = None
    if isinstance(value, bool):
        row.value_bool = value
    elif isinstance(value, (int, float)):
        row.value_num = float(value)
    elif isinstance(value, str):
        row.value_text = value
    elif isinstance(value, (list, dict)):
        row.value_json = ({"items": value} if isinstance(value, list)
                          else value)
    row.media_sha256 = video.media_sha256
    row.media_path = video.media_path
    row.method = "model"
    row.model = model
    # No confidence is stored. The 2026-10-27 benchmark measured the
    # old model's self-reported confidence as identical when right and
    # wrong (0.932 against 0.933), so a number here would be decoration
    # that a reader would reasonably mistake for information.
    row.uncertainty = None
    row.uncertainty_kind = None
    row.interval_start_s = 0.0
    row.interval_end_s = vision_task.HOOK_SECONDS
    # The grade travels with the row, so nothing downstream has to
    # look it up to know whether this may support a claim.
    row.params = {"reliability": wv.reliability_of(feature)}
    row.note = note
    row.extracted_at = datetime.now(timezone.utc)
    row.run_id = run.id
    row.status = "ok"
    row.error = None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="haiku", choices=("haiku", "sonnet"))
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--max-usd", type=float, default=6.00)
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
        videos = list(s.scalars(
            select(Video).join(Creator, Creator.id == Video.creator_id)
            .where(Creator.is_own_account.is_(True),
                   Video.media_status == "available",
                   Video.media_path.is_not(None))
            .order_by(Video.id)).all())
        done = set(s.scalars(
            select(VideoFeature.video_id).where(
                VideoFeature.method_version == wv.METHOD_VERSION,
                VideoFeature.status == "ok")).all())

    todo = [v for v in videos if v.id not in done]
    if args.limit:
        todo = todo[:args.limit]

    per_call = (3645 / 1e6 * provider.USD_PER_MTOK_IN
                + args.max_output_tokens / 1e6 * provider.USD_PER_MTOK_OUT)
    plan = {
        "model": provider.MODEL,
        "questions_asked": len(wv.ASKABLE),
        "own_publications_with_media": len(videos),
        "already_described": len(done),
        "to_do": len(todo),
        "estimate_usd": round(len(todo) * per_call, 2),
        "script_ceiling_usd": args.max_usd,
        "method_version": wv.METHOD_VERSION,
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

    for i, video in enumerate(todo, 1):
        path = Path(video.media_path or "")
        if not path.exists():
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

        import base64
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
                    idempotency_key=(f"describe_library:{video.id}:"
                                     f"{wv.METHOD_VERSION}"),
                    category="cloud_vision",
                    detail={"script": "describe_library"})
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
            logger.warning("%s: %s", video.id, str(exc)[:160])
            failed += 1
            continue

        actual = (out.usage.actual_usd if out.usage else None) or est
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
        if i % 25 == 0 or i == len(todo):
            logger.info("%d/%d described, $%.3f spent, %.0fs elapsed",
                        described, len(todo), spent, time.time() - t0)

    with Session() as s:
        r = s.get(VideoFeatureRun, run_id)
        # The column allows running | ok | partial | failed, and
        # nothing else. "complete" was refused by the check
        # constraint AFTER every publication had been described and
        # paid for, which is the right order for that to happen in
        # but still left the run row reading "running" forever.
        r.status = ("ok" if not failed and not skipped
                    else "partial" if described else "failed")
        r.finished_at = datetime.now(timezone.utc)
        s.commit()
        feat = s.scalar(select(func.count(VideoFeature.id)).where(
            VideoFeature.method_version == wv.METHOD_VERSION)) or 0
        from server.budget import budget_state
        state = budget_state(s)

    print(json.dumps({
        **plan, "described": described, "failed": failed,
        "skipped_no_media": skipped,
        "billed_usd": round(spent, 4),
        "seconds": round(time.time() - t0, 1),
        "feature_rows_total": feat,
        "budget_after": state,
    }, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
