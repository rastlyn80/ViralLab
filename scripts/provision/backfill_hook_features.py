"""Extract hook features for every preserved publication. Runs ON Ubuntu.

M1 found that hook features existed for **48** publications, not the 258
the blueprint claimed — and that media is preserved for 258 and retention
metrics now exist for 286. Feature extraction, not data access, is the
binding constraint on every retention finding in the project.

This closes that gap for the three extractors that need no GPU:

  * `hook-deterministic-v1` — 21 visual and audio measures, signal
    processing only (`shared/hookfeat.py`);
  * `hook-background-v1` — background complexity via temporal masking
    (`shared/hookbg.py`);
  * `hook-ocr-v1` — RapidOCR on onnxruntime, which runs on CPU where the
    media already lives and deliberately keeps the RTX 2070 free for the
    vision-language work that genuinely needs VRAM (`shared/hookocr.py`).

Speech and vision stay on the Windows worker and are not touched here.

Storage is keyed on (video, feature, method_version) and reprocessing is
additive, so this is safe to re-run: videos already carrying the current
version are skipped unless `--force` is given, and a re-run of the same
version updates that version's rows rather than duplicating them.

    python backfill_hook_features.py --dry-run
    python backfill_hook_features.py --batch 40
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, "/home/rastlyn/virallab")

from sqlalchemy import create_engine, select  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from server.db.models import Video, VideoFeature, VideoFeatureRun  # noqa: E402
from server.features import extract  # noqa: E402
from shared import hookbg, hookfeat, hookocr  # noqa: E402

logging.basicConfig(level=logging.INFO,
                    format="%(levelname)s %(name)s %(message)s")
logger = logging.getLogger("virallab.backfill.features")


def already_done(session, method_version: str) -> set[int]:
    """Video ids that already carry this method version, any platform.

    Not platform-scoped on purpose: the ids are compared against a pool
    that is already filtered to one platform, so scoping here would be
    redundant, and a set of ids cannot collide across platforms.
    """
    return set(session.scalars(select(VideoFeature.video_id).where(
        VideoFeature.method_version == method_version).distinct()).all())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch", type=int, default=0,
                    help="process at most this many videos per extractor "
                         "(0 = all remaining)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true",
                    help="re-measure videos that already carry the current "
                         "method version")
    ap.add_argument("--kind",
                    choices=["deterministic", "background", "ocr", "all"],
                    default="all")
    ap.add_argument("--platform", choices=["instagram", "tiktok"],
                    default="instagram",
                    help="TikTok media came from the owner's own archive "
                         "via yt-dlp (ADR 0007); the extractors do not care "
                         "which platform a file came from.")
    args = ap.parse_args()

    media_root = os.environ.get("VIRALLAB_MEDIA_ROOT")
    eng = create_engine(os.environ["VIRALLAB_DB_URL"], future=True)
    S = sessionmaker(eng, expire_on_commit=False, future=True)

    summary: dict = {"media_root": media_root}
    with S() as s:
        pool = extract.analysable(s, platform=args.platform)
        summary["platform"] = args.platform
        summary["analysable"] = len(pool)

        plan = {}
        if args.kind in ("deterministic", "all"):
            done = set() if args.force else already_done(
                s, hookfeat.METHOD_VERSION)
            todo = [v for v in pool if v.id not in done]
            plan["deterministic"] = (hookfeat.METHOD_VERSION, todo)
        if args.kind in ("background", "all"):
            done = set() if args.force else already_done(
                s, hookbg.METHOD_VERSION)
            todo = [v for v in pool if v.id not in done]
            plan["background"] = (hookbg.METHOD_VERSION, todo)
        if args.kind in ("ocr", "all"):
            done = set() if args.force else already_done(
                s, hookocr.METHOD_VERSION)
            todo = [v for v in pool if v.id not in done]
            plan["ocr"] = (hookocr.METHOD_VERSION, todo)

        summary["pending"] = {k: len(v[1]) for k, v in plan.items()}
        if args.dry_run:
            summary["dry_run"] = True
            print(json.dumps(summary, indent=2))
            return 0

        root = Path(media_root) if media_root else None
        summary["runs"] = {}
        for kind, (mv, todo) in plan.items():
            if args.batch:
                todo = todo[:args.batch]
            if not todo:
                summary["runs"][kind] = {"skipped": "nothing pending"}
                continue
            logger.info("%s (%s): %d video(s)", kind, mv, len(todo))
            fn = {"deterministic": extract.run_deterministic,
                  "background": extract.run_background,
                  "ocr": extract.run_ocr}[kind]
            out = fn(s, todo, media_root=root)
            s.commit()
            summary["runs"][kind] = {
                "method_version": out.method_version,
                "requested": out.requested,
                "done": out.done,
                "failed": out.failed,
                "features_written": out.features_written,
                "compute_seconds": round(out.compute_seconds, 1),
                # Errors are summarised rather than dumped: a missing file
                # for one publication is a coverage fact, not a crash, and
                # the full list is on the VideoFeatureRun row.
                "first_errors": out.errors[:5],
                "run_id": out.run_id,
            }

        summary["coverage_after"] = extract.coverage(s)

    print(json.dumps(summary, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
