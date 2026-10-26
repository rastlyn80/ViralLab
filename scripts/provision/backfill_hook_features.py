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


def run_ocr(session, videos: list, *, media_root: Path | None):
    """OCR the opening of each video. CPU-only, no GPU, no money.

    RapidOCR on onnxruntime runs where the media already lives
    (`shared/hookocr.py`), so this needs neither the Windows box nor a
    paid provider — the AI layer's local OCR provider exists for requests
    that arrive through the API, and this is the same engine driven
    directly for a backfill.

    Decoding is done here rather than inside `hookocr` because
    `analyse_frames` deliberately takes frames, so a caller can decode
    once and share them.
    """
    import time

    out = extract.ExtractOutcome(kind="hook_ocr",
                                 method_version=hookocr.METHOD_VERSION,
                                 requested=len(videos))
    run = VideoFeatureRun(
        method_version=hookocr.METHOD_VERSION, kind="hook_ocr",
        status="running", videos_requested=len(videos),
        params={"engine": hookocr.ENGINE, "ocr_fps": hookocr.OCR_FPS,
                "ocr_width": hookocr.OCR_WIDTH})
    session.add(run)
    session.flush()
    out.run_id = run.id

    started = time.monotonic()
    hook_seconds = hookfeat.HOOK_SECONDS
    for video in videos:
        path = Path(video.media_path or "")
        if media_root is not None and not path.is_absolute():
            path = media_root / path.name
        if not path.exists():
            out.failed += 1
            out.errors.append({"video_id": video.id,
                               "error": f"file missing: {path}"})
            continue
        try:
            info = hookfeat.probe(path)
            w = hookocr.OCR_WIDTH
            # Preserve aspect ratio: OCR on a squashed frame reads worse,
            # and the height must be even for the rawvideo pipe.
            src_w = int(info.get("width") or w)
            src_h = int(info.get("height") or w)
            h = max(2, int(round(w * src_h / max(1, src_w))) // 2 * 2)
            frames = hookfeat.decode_frames(
                path, start=0.0, seconds=hook_seconds,
                fps=hookocr.OCR_FPS, width=w, height=h)
            if frames.shape[0] == 0:
                raise hookfeat.ExtractionError("no frames decoded")
            stamps = [i / hookocr.OCR_FPS for i in range(frames.shape[0])]
            res = hookocr.analyse_frames(frames, timestamps=stamps,
                                         hook_seconds=hook_seconds)
            meas = hookocr.measurements(res, (0.0, hook_seconds))
        except Exception as exc:              # noqa: BLE001 - recorded
            out.failed += 1
            out.errors.append({"video_id": video.id,
                               "external_id": video.external_id,
                               "error": str(exc)[:300]})
            continue
        out.features_written += extract._store(
            session, video, run, meas,
            method_version=hookocr.METHOD_VERSION, model=hookocr.ENGINE)
        out.done += 1
        # Commit per video, as `run_deterministic` does: a crash half way
        # through a 210-file run must leave the completed work behind.
        session.commit()

    out.compute_seconds = round(time.monotonic() - started, 2)
    run.videos_done = out.done
    run.videos_failed = out.failed
    run.features_written = out.features_written
    run.compute_seconds = out.compute_seconds
    run.finished_at = datetime.now(timezone.utc)
    run.status = ("ok" if out.failed == 0
                  else ("failed" if out.done == 0 else "partial"))
    if out.errors:
        run.notes = (f"{len(out.errors)} failure(s); first: "
                     f"{out.errors[0]['error'][:200]}")
    session.commit()
    logger.info("ocr run %s: %d done, %d failed, %d features, %.1fs",
                run.id, out.done, out.failed, out.features_written,
                out.compute_seconds)
    return out


def already_done(session, method_version: str) -> set[int]:
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
    args = ap.parse_args()

    media_root = os.environ.get("VIRALLAB_MEDIA_ROOT")
    eng = create_engine(os.environ["VIRALLAB_DB_URL"], future=True)
    S = sessionmaker(eng, expire_on_commit=False, future=True)

    summary: dict = {"media_root": media_root}
    with S() as s:
        pool = extract.analysable(s, platform="instagram")
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
                  "ocr": run_ocr}[kind]
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
