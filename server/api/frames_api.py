"""Frame delivery to the GPU worker.

The media library is on the Ubuntu server; the GPU is on Windows. Moving
the library would violate the plan's standing rule against large media
transfers, and would also be absurd: analysing a 3-second opening needs
about 250 KiB of JPEG, not a 30 MB video.

So this endpoint decodes the *planned* frames on the server - the same
timestamps `vision_task.FramePlan` declares - and returns them as bounded
base64 JPEG. Per analysed video that is roughly **0.8% of the file**.

Three properties it has to have.

**Authenticated.** The per-worker bearer token that already protects
`/jobs/*`. There is no unauthenticated path to a frame, and no
signed-URL variant: a signed URL is a bearer token with a longer life and
worse revocation, and the worker already has a real credential.

**Bounded.** A hard cap on frame count, JPEG dimension and payload size. A
caller cannot ask for a thousand frames at full resolution and turn this
into a media mirror.

**Own-account only.** Competitor publications have no preserved file and
must never acquire one through this path either
(`docs/MULTIMODAL_PIPELINE.md`).
"""

from __future__ import annotations

import base64
import hashlib
import io
import logging
from pathlib import Path

import secrets

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from sqlalchemy.orm import Session

from server.ai import vision_task
from server.api.deps import get_session
from server.db.models import Creator, Video
from shared import hookfeat

logger = logging.getLogger("virallab.api.frames")

router = APIRouter(prefix="/api/frames", tags=["frames"])

#: Hard ceilings. Not configuration - the point is that no caller can lift
#: them, so this cannot become a way to pull the library.
MAX_FRAMES = 12
MAX_WIDTH = 640
MAX_PAYLOAD_BYTES = 8 * 1024 * 1024
JPEG_QUALITY = 85


def require_worker(request: Request,
                   authorization: str = Header(default="")) -> str:
    """Per-worker bearer token, the same credential `/jobs/*` uses.

    Defined here rather than imported from `app` because `app` imports this
    module; it reads the token table off `app.state`, so there is one
    source of truth and no cycle. Constant-time comparison, and the token
    itself is never logged.
    """
    scheme, _, token = authorization.partition(" ")
    tokens: dict[str, str] = getattr(request.app.state, "tokens", {})
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(401, "missing bearer token")
    for known, name in tokens.items():
        if secrets.compare_digest(known, token):
            return name
    raise HTTPException(403, "unknown worker token")


def encode_frames(path: Path, timestamps: list[float], *, width: int
                  ) -> list[dict]:
    """Decode exactly the requested instants and JPEG them.

    One ffmpeg call per timestamp rather than one long decode: the
    requested instants are seconds apart, and decoding the span between
    them to throw it away would cost more than it saves.
    """
    from PIL import Image

    info = hookfeat.probe(path)
    height = max(2, int(round(width * (info["height"] or 16)
                              / (info["width"] or 9))))
    height += height % 2

    out = []
    for at in timestamps:
        if info["duration_s"] and at > info["duration_s"] - 0.05:
            continue
        frames = hookfeat.decode_frames(path, start=at, seconds=0.2,
                                        fps=5.0, width=width, height=height)
        if frames.shape[0] == 0:
            continue
        buf = io.BytesIO()
        Image.fromarray(frames[0]).save(buf, format="JPEG",
                                        quality=JPEG_QUALITY)
        blob = buf.getvalue()
        out.append({
            "at_s": round(float(at), 2),
            "width": width, "height": height,
            "bytes": len(blob),
            "sha256": hashlib.sha256(blob).hexdigest(),
            "jpeg_b64": base64.b64encode(blob).decode(),
        })
    return out


@router.get("/{video_id}")
def frames(video_id: int, width: int = Query(512, ge=128, le=MAX_WIDTH),
           task: str = Query(vision_task.TASK),
           session: Session = Depends(get_session),
           worker: str = Depends(require_worker)) -> dict:
    """Planned frames for one publication, for an authenticated worker."""
    video = session.get(Video, video_id)
    if video is None:
        raise HTTPException(404, "no such publication")
    creator = session.get(Creator, video.creator_id)
    if creator is None or not creator.is_own_account:
        raise HTTPException(
            403, "frames are served for your own publications only; "
                 "third-party media is not retained and must not be "
                 "acquired through this path")
    if video.media_status != "available" or not video.media_path:
        raise HTTPException(
            409, f"no preserved file for this publication "
                 f"(media_status={video.media_status})")

    path = Path(video.media_path)
    if not path.exists():
        raise HTTPException(
            409, "the publication is marked available but the file is "
                 "missing from disk")

    stamps = vision_task.FramePlan().timestamps(video.duration_s)[:MAX_FRAMES]
    try:
        encoded = encode_frames(path, stamps, width=width)
    except hookfeat.ExtractionError as exc:
        raise HTTPException(500, f"frame decode failed: {exc}") from exc

    total = sum(f["bytes"] for f in encoded)
    if total > MAX_PAYLOAD_BYTES:
        raise HTTPException(
            413, f"frames total {total} bytes, above the "
                 f"{MAX_PAYLOAD_BYTES} cap; request a smaller width")

    logger.info("served %d frame(s), %.0f KiB, for video %s to worker %s",
                len(encoded), total / 1024, video_id, worker)
    return {
        "video_id": video.id,
        "external_id": video.external_id,
        "duration_s": video.duration_s,
        "task": task,
        "media_sha256": video.media_sha256,
        "frames": encoded,
        "total_bytes": total,
        "prompt": vision_task.build_prompt(),
        "schema": vision_task.SCHEMA,
        "note": (
            f"{len(encoded)} frames totalling {total/1024:.0f} KiB. The "
            f"source file is not transferred and never leaves this server."),
    }
