"""Frames for the competitor half of M3's held-out set. Runs ON Ubuntu.

`/api/frames/{id}` serves own publications only, and refuses third-party
media with "not retained and must not be acquired through this path".
That guard is right and is not touched here: its job is to stop the
worker endpoint becoming a channel for pulling the competitor library,
and a benchmark has no business weakening it.

So the frames come out the same way the strips did - decoded on this
server from media that is already legitimately held, and handed to the
operator over the existing SSH session. The difference from the API path
is the thing that matters: this is a one-shot export of a fixed list of
publications the operator has already paid for and is already labelling,
not a standing endpoint anything can call.

Three properties are enforced rather than promised:

  * **Only live transient rows.** A publication whose six-hour window has
    closed is skipped. Nothing here can resurrect expired media.
  * **The payload dies with the media.** It is written into the
    quarantine directory, so the existing reaper deletes it on the same
    clock as the mp4s it was decoded from (ADR 0009).
  * **Identical frame plan to the API.** `vision_task.FramePlan` and
    `frames_api.encode_frames` are imported, not reimplemented, so the
    competitor arm and the own arm are measured on the same geometry,
    the same JPEG quality and the same timestamps. A reimplementation
    that drifted would silently make the two halves incomparable.

    python m3_competitor_frames.py --width 512 --out frames.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, "/home/rastlyn/virallab")

from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from server.ai import vision_task  # noqa: E402
from server.api.frames_api import MAX_FRAMES, encode_frames  # noqa: E402
from server.collectors import transient  # noqa: E402

LABELS = Path("/home/rastlyn/virallab/tests/fixtures/vision/"
              "holdout_labels_competitor.json")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--width", type=int, default=512)
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    out_path = Path(args.out) if args.out else (
        transient.quarantine_root() / "m3_competitor_frames.json")

    wanted = set()
    if LABELS.exists():
        wanted = {int(k) for k in
                  json.loads(LABELS.read_text(encoding="utf-8"))["labels"]}

    eng = create_engine(os.environ["VIRALLAB_DB_URL"], future=True)
    S = sessionmaker(eng, future=True)

    payloads, skipped = [], []
    with S() as s:
        rows = s.execute(text("""
            select tm.video_id, tm.path, v.duration_s
            from transient_media tm join videos v on v.id = tm.video_id
            where tm.deleted_at is null
            order by tm.video_id""")).all()
        for vid, path_s, duration in rows:
            if wanted and vid not in wanted:
                skipped.append({"video_id": vid,
                                "why": "not in the label set"})
                continue
            p = Path(path_s)
            if not p.exists():
                skipped.append({"video_id": vid, "why": "file already reaped"})
                continue
            stamps = vision_task.FramePlan().timestamps(duration)[:MAX_FRAMES]
            try:
                frames = encode_frames(p, stamps, width=args.width)
            except Exception as exc:          # noqa: BLE001 - recorded
                skipped.append({"video_id": vid, "why": str(exc)[:160]})
                continue
            if not frames:
                skipped.append({"video_id": vid, "why": "no frames decoded"})
                continue
            payloads.append({"video_id": vid, "duration_s": duration,
                             "task": vision_task.TASK, "frames": frames,
                             "schema": vision_task.SCHEMA})

    out_path.write_text(json.dumps({
        "_what": ("Frames for the competitor half of M3's held-out set, "
                  "decoded on the server from live transient_media rows."),
        "_lifetime": ("This file lives in the quarantine directory and is "
                      "deleted by the transient reaper on the same six-hour "
                      "clock as the media it was decoded from (ADR 0009). "
                      "The operator's local copy must be deleted too."),
        "_frame_plan": ("vision_task.FramePlan and frames_api.encode_frames, "
                        "imported not reimplemented, so this half is "
                        "measured on identical geometry to the own half."),
        "width": args.width,
        "videos": len(payloads),
        "skipped": skipped,
        "payloads": payloads,
    }), encoding="utf-8")

    print(json.dumps({
        "out": str(out_path),
        "videos": len(payloads),
        "skipped": len(skipped),
        "skipped_detail": skipped[:8],
        "total_frames": sum(len(p["frames"]) for p in payloads),
        "bytes": out_path.stat().st_size,
    }, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
