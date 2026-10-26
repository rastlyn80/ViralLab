"""The Windows GPU worker for scene understanding.

Closes the loop the architecture has had open since Phase 6: the media
lives on the Ubuntu server, the GPU is on this machine, and the two are
joined by frames rather than files.

    server: decode the 6 planned frames  ->  ~250 KiB of JPEG over TLS
    windows: Gemma 3 4B through llama.cpp, schema-constrained
    server: store as versioned features with full provenance

**Idempotent and retryable.** A video already analysed under this method
version is skipped unless `--force`. Each video is posted back
individually, so a crash half way through a batch keeps the completed work
and the next run resumes. Nothing is held in memory that matters.

**Never escalates.** If the model is missing or the server unreachable,
the worker stops with an error. It has no cloud credentials and no code
path that would use one.

**Does not disturb the collectors.** It only reads frames and writes
features; the unattended collection runs on the server are untouched.

Usage:

    python -m gpu_worker.vision_worker --limit 5
    python -m gpu_worker.vision_worker --video-ids 332,333 --force
"""

from __future__ import annotations

import argparse
import base64
import json
import logging
import os
import socket
import ssl
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

logger = logging.getLogger("virallab.vision_worker")

DEFAULT_WIDTH = 512
#: Pause between videos so a long batch cannot monopolise the card while
#: someone is using the desktop.
BETWEEN_S = 0.5


def _env() -> dict:
    out = {}
    path = Path(".env")
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, _, v = line.partition("=")
                out[k.strip()] = v.strip()
    return out


#: Identifies this machine in `worker_heartbeats`. The hostname is
#: enough: there is exactly one GPU workstation, and a name is not a
#: credential.
WORKER_ID = f"vision-{socket.gethostname()}"


class ServerClient:
    """Authenticated client for the ViralLab API."""

    def __init__(self, base: str, token: str, *, verify: bool = False):
        self.base = base.rstrip("/")
        self.token = token
        self.ctx = ssl.create_default_context()
        if not verify:
            # The server presents a self-signed certificate pinned by
            # deployment; this worker reaches it over the tailnet only.
            self.ctx.check_hostname = False
            self.ctx.verify_mode = ssl.CERT_NONE

    def _call(self, path: str, *, method: str = "GET", body: dict | None = None,
              timeout: float = 300.0) -> dict:
        req = urllib.request.Request(self.base + path, method=method)
        # The credential is a header. It is never placed in the URL, which
        # would put it in logs on both ends.
        req.add_header("Authorization", f"Bearer {self.token}")
        if body is not None:
            req.add_header("Content-Type", "application/json")
            req.data = json.dumps(body).encode()
        try:
            with urllib.request.urlopen(req, timeout=timeout,
                                        context=self.ctx) as r:
                raw = r.read()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")[:400]
            raise RuntimeError(f"{method} {path} -> {exc.code}: {detail}"
                               ) from exc

    def frames(self, video_id: int, width: int = DEFAULT_WIDTH) -> dict:
        return self._call(f"/api/frames/{video_id}?width={width}")

    def pending(self, limit: int, *, force: bool = False) -> list[dict]:
        return self._call(
            f"/api/vision/pending?limit={limit}&force="
            f"{'true' if force else 'false'}")["items"]

    def submit(self, payload: dict) -> dict:
        return self._call("/api/vision/results", method="POST", body=payload)

    def heartbeat(self, state: str, *, reason: str | None = None,
                  free_vram_mib: int | None = None,
                  total_vram_mib: int | None = None) -> None:
        """Tell the server this machine is awake.

        Best effort: a failed heartbeat must never abort a run that is
        otherwise working, so it is logged and swallowed. The queue view
        degrades to "silent", which is the honest reading anyway.
        """
        try:
            self._call("/api/vision/heartbeat", method="POST", timeout=30.0,
                       body={"worker_id": WORKER_ID, "state": state,
                             "reason": reason,
                             "free_vram_mib": free_vram_mib,
                             "total_vram_mib": total_vram_mib})
        except Exception as exc:            # noqa: BLE001 - never fatal
            logger.debug("heartbeat (%s) failed: %s", state, exc)


def analyse_one(provider, item: dict, frames_payload: dict, *,
                timeout_s: float) -> dict:
    """Run the model on one video's frames and shape the result."""
    from server.ai.base import AnalysisRequest, Modality

    frames = [(f["at_s"], base64.b64decode(f["jpeg_b64"]))
              for f in frames_payload["frames"]]
    if not frames:
        return {"video_id": item["video_id"], "status": "failed",
                "error": "the server returned no frames"}

    request = AnalysisRequest(
        task=frames_payload["task"], schema=frames_payload["schema"],
        prompt=frames_payload["prompt"], frames=frames,
        video_id=item["video_id"], timeout_s=timeout_s)

    started = time.monotonic()
    try:
        result = provider.analyse(request)
    except Exception as exc:                         # noqa: BLE001
        return {"video_id": item["video_id"], "status": "failed",
                "error": f"{type(exc).__name__}: {exc}"[:500],
                "latency_s": round(time.monotonic() - started, 2)}

    return {
        "video_id": item["video_id"],
        "status": "ok" if result.ok else "invalid",
        "task": request.task,
        "provider": result.provider,
        "model": result.model,
        "hosting": result.hosting.value,
        "data": result.data,
        "raw_text": None if result.ok else (result.raw_text or "")[:2000],
        "schema_valid": result.schema_valid,
        "validation_errors": result.validation_errors[:10],
        "refused_reason": result.refused_reason,
        "input_tokens": result.usage.input_tokens,
        "output_tokens": result.usage.output_tokens,
        "images": result.usage.images,
        "latency_s": result.latency_s,
        "peak_vram_mib": result.peak_vram_mib,
        "frames": [{"at_s": f["at_s"], "sha256": f["sha256"],
                    "bytes": f["bytes"]}
                   for f in frames_payload["frames"]],
        "media_sha256": frames_payload.get("media_sha256"),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit", type=int, default=5,
                    help="maximum videos to analyse in this run")
    ap.add_argument("--video-ids", default="",
                    help="comma-separated ids, instead of the pending list")
    ap.add_argument("--force", action="store_true",
                    help="re-analyse videos that already have results for "
                         "this method version")
    ap.add_argument("--width", type=int, default=DEFAULT_WIDTH)
    ap.add_argument("--timeout", type=float, default=300.0)
    ap.add_argument("--dry-run", action="store_true",
                    help="fetch frames and report, run no inference")
    args = ap.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")

    env = _env()
    host = env.get("UBUNTU_HOST") or os.environ.get("UBUNTU_HOST")
    token = (env.get("SERVER_WORKER_TOKEN")
             or os.environ.get("VIRALLAB_WORKER_TOKEN"))
    if not host or not token:
        logger.error("UBUNTU_HOST and SERVER_WORKER_TOKEN must be available "
                     "(.env or environment)")
        return 2

    client = ServerClient(f"https://{host}:8443", token)

    from server.ai.local_vlm import (LocalVlmProvider, MODEL_VERSION,
                                     installed, start_server, vram_used_mib)

    ok, why = installed()
    if not ok:
        logger.error("%s", why)
        return 3

    if args.video_ids:
        ids = [int(x) for x in args.video_ids.split(",") if x.strip()]
        items = [{"video_id": i} for i in ids]
    else:
        items = client.pending(args.limit, force=args.force)
    if not items:
        logger.info("nothing to analyse")
        return 0
    items = items[:args.limit]
    logger.info("%d video(s) to analyse under %s", len(items), MODEL_VERSION)

    if not args.dry_run:
        started, why = start_server()
        if not started:
            logger.error("%s", why)
            return 4
        logger.info("llama-server ready; GPU holds %s MiB total",
                    vram_used_mib())
        client.heartbeat("busy", reason=f"model loaded: {MODEL_VERSION}",
                         total_vram_mib=vram_used_mib())

    provider = LocalVlmProvider()
    done = failed = invalid = 0
    total_bytes = 0
    t0 = time.monotonic()

    for item in items:
        vid = item["video_id"]
        try:
            payload = client.frames(vid, width=args.width)
        except RuntimeError as exc:
            logger.warning("video %s: could not fetch frames: %s", vid, exc)
            failed += 1
            continue
        total_bytes += payload["total_bytes"]
        logger.info("video %s: %d frames, %.0f KiB", vid,
                    len(payload["frames"]), payload["total_bytes"] / 1024)
        # Per video, so a long run keeps reporting and the server never
        # requeues work from a worker that is still going (CLAUDE.md 4.3).
        client.heartbeat("busy", reason=f"video {vid}")

        if args.dry_run:
            continue

        result = analyse_one(provider, item, payload,
                             timeout_s=args.timeout)
        try:
            client.submit(result)
        except RuntimeError as exc:
            logger.error("video %s: could not submit: %s", vid, exc)
            failed += 1
            continue

        if result["status"] == "ok":
            done += 1
            logger.info("  stored: %.1fs, %s in / %s out tokens",
                        result.get("latency_s") or 0,
                        result.get("input_tokens"),
                        result.get("output_tokens"))
        elif result["status"] == "invalid":
            invalid += 1
            logger.warning("  schema invalid: %s",
                           (result.get("validation_errors") or [""])[0])
        else:
            failed += 1
            logger.warning("  failed: %s", result.get("error"))
        time.sleep(BETWEEN_S)

    elapsed = time.monotonic() - t0
    client.heartbeat("idle", reason="run complete")
    logger.info(
        "run complete: %d stored, %d invalid, %d failed in %.1fs "
        "(%.1fs/video); %.0f KiB of frames transferred",
        done, invalid, failed, elapsed,
        elapsed / max(done + invalid + failed, 1), total_bytes / 1024)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
