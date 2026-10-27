"""M3: run both prompt arms over the held-out set. Runs on the WORKSTATION.

The geometric route is closed (docs/benchmarks/6c-vision-benchmark.md).
This measures the next candidate: whether Gemma 3 4B's specificity
collapse can be fixed by telling it, in the prompt, that absence is a
real and common answer and giving it the measured false positives as
worked negative examples.

**It writes nothing to the database.** That is the point. `run_detect`
was built as a pipeline stage before it was measured, failed, and had to
be removed the same day; a benchmark is a measurement, and a field only
becomes a pipeline stage after it has cleared the bar. Results land in a
JSON file, the scorer reads that file, and `vision_task.DEFAULT_VARIANT`
stays on `original` until the numbers say otherwise.

**Both arms, same videos, same session.** The control arm's existing rows
cover only 1 of the 40 held-out publications, so running the treatment
alone would compare 40 new videos against 24 old ones - a different
corpus, not a different prompt. Both arms are therefore run here, back to
back, over the identical frame payloads: the frames are fetched once per
video and handed to both prompts, so even JPEG encoding is held constant.

Order is interleaved per video rather than arm-by-arm, so that if the run
is interrupted the two arms are still balanced over whatever completed.

    python scripts/provision/m3_prompt_arms.py --out holdout_arms.json
    python scripts/provision/m3_prompt_arms.py --limit 2 --dry-run
"""

from __future__ import annotations

import argparse
import base64
import json
import logging
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from gpu_worker.vision_worker import ServerClient, _env  # noqa: E402
from server.ai import vision_task  # noqa: E402

logger = logging.getLogger("virallab.m3_prompt_arms")

LABELS_DEFAULT = (ROOT / "tests" / "fixtures" / "vision"
                  / "holdout_labels_own.json")
ARMS = ("original", "negative_example")
DEFAULT_WIDTH = 512


def holdout_ids(path: Path) -> list[int]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return sorted(int(k) for k in data["labels"])


def run_arm(provider, arm: str, frames, video_id: int, schema: dict,
            timeout_s: float) -> dict:
    """One prompt against one video's frames."""
    from server.ai.base import AnalysisRequest

    os.environ["VIRALLAB_VISION_PROMPT"] = arm
    prompt = vision_task.build_prompt()
    request = AnalysisRequest(
        task=vision_task.TASK, schema=schema, prompt=prompt,
        frames=frames, video_id=video_id, timeout_s=timeout_s)

    started = time.monotonic()
    try:
        result = provider.analyse(request)
    except Exception as exc:                      # noqa: BLE001 - recorded
        return {"arm": arm, "video_id": video_id, "status": "failed",
                "error": f"{type(exc).__name__}: {exc}"[:400],
                "latency_s": round(time.monotonic() - started, 2)}
    return {
        "arm": arm, "video_id": video_id,
        "status": "ok" if result.ok else "invalid",
        "method_version": vision_task.method_version(),
        "model": result.model,
        "data": result.data,
        "schema_valid": result.schema_valid,
        "validation_errors": result.validation_errors[:6],
        "raw_text": None if result.ok else (result.raw_text or "")[:800],
        "input_tokens": result.usage.input_tokens,
        "output_tokens": result.usage.output_tokens,
        "latency_s": result.latency_s,
        "peak_vram_mib": result.peak_vram_mib,
        "prompt_chars": len(prompt),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default="m3_prompt_arms.json")
    ap.add_argument("--only", default="",
                    help="comma-separated video ids, for re-running the "
                         "publications a previous run lost. Results are "
                         "MERGED into --out rather than replacing it, so "
                         "the two arms stay paired per publication.")
    ap.add_argument("--frames-file", default="",
                    help="a payload written by m3_competitor_frames.py, "
                         "used instead of /api/frames. Required for the "
                         "competitor half: that endpoint serves own "
                         "publications only, by design, and this script "
                         "does not weaken it.")
    ap.add_argument("--labels", default=str(LABELS_DEFAULT),
                    help="which half of the held-out set to run. The "
                         "competitor half is only runnable while its "
                         "transient_media rows are live (ADR 0009).")
    ap.add_argument("--limit", type=int, default=0,
                    help="first N held-out videos only; 0 means all")
    ap.add_argument("--width", type=int, default=DEFAULT_WIDTH)
    ap.add_argument("--timeout", type=float, default=300.0)
    ap.add_argument("--dry-run", action="store_true",
                    help="fetch frames, run no inference")
    args = ap.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")

    preloaded: dict[int, dict] = {}
    client = None
    if args.frames_file:
        doc = json.loads(Path(args.frames_file).read_text(encoding="utf-8"))
        preloaded = {int(p["video_id"]): p for p in doc["payloads"]}
        logger.info("frames from %s: %d publication(s)",
                    args.frames_file, len(preloaded))
    else:
        env = _env()
        host = env.get("UBUNTU_HOST") or os.environ.get("UBUNTU_HOST")
        token = (env.get("SERVER_WORKER_TOKEN")
                 or os.environ.get("VIRALLAB_WORKER_TOKEN"))
        if not host or not token:
            logger.error("UBUNTU_HOST and SERVER_WORKER_TOKEN must be "
                         "available")
            return 2
        client = ServerClient(f"https://{host}:8443", token)

    labels_path = Path(args.labels)
    ids = holdout_ids(labels_path)
    if args.limit:
        ids = ids[:args.limit]
    logger.info("%d held-out publication(s), %d arm(s)", len(ids), len(ARMS))

    provider = None
    if not args.dry_run:
        from server.ai.local_vlm import (LocalVlmProvider, installed,
                                         start_server)
        ok, why = installed()
        if not ok:
            logger.error("%s", why)
            return 3
        started, why = start_server()
        if not started:
            logger.error("llama-server did not start: %s", why)
            return 4
        provider = LocalVlmProvider()

    if args.only:
        keep = {int(x) for x in args.only.split(",") if x.strip()}
        ids = [v for v in ids if v in keep]
        logger.info("restricted to %d publication(s)", len(ids))

    out_path = Path(args.out)
    results: list[dict] = []
    missing: list[dict] = []
    # Merge rather than overwrite. A re-run exists to repair publications
    # a previous run lost; replacing the file would throw away the ones
    # that succeeded, and a half-written file is how a paired comparison
    # silently becomes an unpaired one.
    if args.only and out_path.exists():
        prior = json.loads(out_path.read_text(encoding="utf-8"))
        results = [r for r in prior.get("results", [])
                   if int(r["video_id"]) not in keep]
        logger.info("kept %d existing row(s) from %s", len(results), out_path)

    for n, vid in enumerate(ids, 1):
        if preloaded:
            payload = preloaded.get(vid)
            if payload is None:
                missing.append({"video_id": vid,
                                "why": "absent from the frames file; its "
                                       "transient window had closed"})
                continue
        else:
            try:
                payload = client.frames(vid, args.width)
            except Exception as exc:              # noqa: BLE001 - recorded
                missing.append({"video_id": vid, "why": str(exc)[:200]})
                continue
        frames = [(f["at_s"], base64.b64decode(f["jpeg_b64"]))
                  for f in payload["frames"]]
        if not frames:
            missing.append({"video_id": vid,
                            "why": "server returned no frames"})
            continue
        if args.dry_run:
            logger.info("[%d/%d] %s: %d frames", n, len(ids), vid, len(frames))
            continue
        for arm in ARMS:
            r = run_arm(provider, arm, frames, vid, payload["schema"],
                        args.timeout)
            r["frames_sent"] = len(frames)
            results.append(r)
            logger.info("[%d/%d] %s %-16s %s %.1fs", n, len(ids), vid, arm,
                        r["status"], r.get("latency_s") or 0.0)
        # Written after every video, so an interrupted run keeps its work
        # and both arms are present for each completed publication.
        out_path.write_text(json.dumps({
            "_what": ("M3 prompt-arm comparison, held-out own-account set. "
                      "Measurement only - nothing here is in the database."),
            "labels_file": labels_path.name,
            "arms": list(ARMS),
            "frame_width": args.width,
            "frames_source": (args.frames_file or "/api/frames"),
            "videos_requested": len(ids),
            "videos_done": len({r["video_id"] for r in results}),
            "missing": missing,
            "results": results,
        }, indent=1), encoding="utf-8")

    done = len({r["video_id"] for r in results})
    print(json.dumps({"videos_done": done, "rows": len(results),
                      "missing": len(missing), "out": str(out_path)},
                     indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
