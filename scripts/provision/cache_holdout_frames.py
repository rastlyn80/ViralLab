"""Frames for the 80 labelled publications, once, for every benchmark.

Runs ON Ubuntu. The vision benchmark compares four approaches over
the same images; decoding them once and caching is the difference
between a comparison and four slightly different experiments.

**The competitor half has to be bought again.** Its media was deleted
under ADR 0009 after M3 labelled it, which is the rule working. The
labels persisted and the bytes did not, so re-measuring those forty
publications costs another acquisition - the measured $0.00404 a
file, about $0.16 for the set. That is the standing price of the
retention guarantee and it is cheaper than keeping the files.

Own media is preserved and costs nothing.

The frame plan is `vision_task.FramePlan` and the encoder is
`frames_api.encode_frames`, imported rather than reimplemented, so
every approach sees exactly the images Gemma saw when it produced the
baseline that is already committed.

    python cache_holdout_frames.py --yes
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
from server.budget import budget_state  # noqa: E402
from server.collectors import competitor_media as cm  # noqa: E402
from server.collectors import transient  # noqa: E402
from server.db.models import Video  # noqa: E402

FIX = Path("/home/rastlyn/virallab/tests/fixtures/vision")


def ids_from(name: str) -> list[int]:
    p = FIX / name
    return sorted(int(k) for k in
                  json.loads(p.read_text(encoding="utf-8"))["labels"])


def encode_for(session, ids: list[int], width: int) -> tuple[list, list]:
    out, skipped = [], []
    for vid in ids:
        v = session.get(Video, vid)
        if v is None:
            skipped.append({"video_id": vid, "why": "no such publication"})
            continue
        path = None
        if v.media_path and Path(v.media_path).exists():
            path = Path(v.media_path)
        else:
            row = session.execute(text(
                "select path from transient_media where video_id=:v "
                "and deleted_at is null order by acquired_at desc limit 1"),
                {"v": vid}).scalar()
            if row and Path(row).exists():
                path = Path(row)
        if path is None:
            skipped.append({"video_id": vid, "why": "no media on disk"})
            continue
        stamps = vision_task.FramePlan().timestamps(v.duration_s)[:MAX_FRAMES]
        try:
            frames = encode_frames(path, stamps, width=width)
        except Exception as exc:              # noqa: BLE001 - recorded
            skipped.append({"video_id": vid, "why": str(exc)[:140]})
            continue
        if not frames:
            skipped.append({"video_id": vid, "why": "no frames decoded"})
            continue
        out.append({"video_id": vid, "frames": frames})
    return out, skipped


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--width", type=int, default=512)
    ap.add_argument("--batch", type=int, default=8,
                    help="files per budget reservation; small enough "
                         "that the conservative estimate fits the cap")
    ap.add_argument("--out", default="")
    ap.add_argument("--yes", action="store_true",
                    help="required: re-acquiring competitor media costs")
    args = ap.parse_args()

    eng = create_engine(os.environ["VIRALLAB_DB_URL"], future=True)
    S = sessionmaker(eng, expire_on_commit=False, future=True)
    media_root = Path(os.environ["VIRALLAB_MEDIA_ROOT"])
    out_path = Path(args.out) if args.out else (
        transient.quarantine_root() / "holdout_frames.json")

    own = ids_from("holdout_labels_own.json")
    comp = ids_from("holdout_labels_competitor.json")

    with S() as s:
        before = budget_state(s, provider="apify")
    plan = {"own": len(own), "competitor": len(comp),
            "estimate_usd": round(len(comp) * 0.00404, 4),
            "budget_before": before}
    if not args.yes:
        plan["dry_run"] = "re-run with --yes; competitor media is bought again"
        print(json.dumps(plan, indent=2, default=str))
        return 0

    # Acquire in small batches. The budget guard reserves against a
    # conservative per-file ESTIMATE (about $0.05) rather than the
    # measured $0.004, so a single reservation for forty files reads
    # as $2.05 and is refused against the remaining category
    # allowance - correctly, since the guard cannot know in advance
    # that the provider will bill a tenth of that. Smaller bites let
    # each reservation settle at its real cost before the next one is
    # taken, and the cap is respected rather than argued with.
    acquired = failed = 0
    billed = 0.0
    with S() as s:
        for start in range(0, len(comp), args.batch):
            chunk = comp[start:start + args.batch]
            videos = [s.get(Video, i) for i in chunk]
            videos = [v for v in videos if v is not None]
            if not videos:
                continue
            out = cm.acquire(s, videos=videos,
                             token=os.environ["APIFY_TOKEN"],
                             media_root=media_root,
                             max_files=len(videos))
            s.commit()
            acquired += out.acquired
            failed += out.failed
            billed += out.billed_usd or 0.0

        own_frames, own_skip = encode_for(s, own, args.width)
        comp_frames, comp_skip = encode_for(s, comp, args.width)

        payload = {
            "_what": ("Frames for the 80 labelled publications, cached so "
                      "every approach is compared on identical images."),
            "_lifetime": ("Competitor frames follow ADR 0009: this file "
                          "lives in quarantine and dies with the media. "
                          "The operator's local copy must be deleted too."),
            "width": args.width,
            "cohorts": {"own": [f["video_id"] for f in own_frames],
                        "competitor": [f["video_id"] for f in comp_frames]},
            "videos": own_frames + comp_frames,
        }
        out_path.write_text(json.dumps(payload), encoding="utf-8")

        # The bytes have served their purpose the moment the frames
        # are encoded. Nothing is analysed from the mp4 after this.
        released = 0
        for row in transient.live_rows(s):
            if transient.release(s, row, reason="features_persisted"):
                released += 1
        s.commit()
        after = budget_state(s, provider="apify")
        audit = transient.audit(s)

    print(json.dumps({
        **plan,
        "acquired": acquired, "failed": failed,
        "billed_usd": round(billed, 4),
        "own_cached": len(own_frames), "own_skipped": own_skip,
        "competitor_cached": len(comp_frames),
        "competitor_skipped": comp_skip[:8],
        "released_after_encoding": released,
        "out": str(out_path), "bytes": out_path.stat().st_size,
        "budget_after": after, "transient_audit": audit,
    }, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
