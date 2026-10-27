"""Build frame strips for M3's held-out label set. Runs ON Ubuntu.

M3 requires "a fresh held-out label set: 40 own + 40 competitor
openings, labelled from full frame strips before any inference. The
existing 24 become the *tuning* set and are never scored again."

That exclusion is enforced here rather than remembered: the 24 ids in
`human_labels_v2.json` are removed from the candidate pool, and the
script refuses to emit a strip for any of them.

Each strip is one PNG per publication showing the four hook timestamps
side by side, which is what "labelled from full frame strips" means —
the first pass of the old benchmark labelled a single frame at t=1.0s
and produced contradictions that a strip resolves.

**Competitor strips are bound by ADR 0009.** The labels persist; the
frames do not. A competitor strip may only be made while that
publication's `transient_media` row is live, and the strip is written
into the quarantine directory so the existing reaper deletes it on the
same six-hour clock as the mp4 it came from. Nothing about a competitor
strip survives the window.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, "/home/rastlyn/virallab")

import numpy as np  # noqa: E402
from PIL import Image  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from server.collectors import transient  # noqa: E402
from shared import hookfeat  # noqa: E402

TUNING_LABELS = Path("/home/rastlyn/virallab/tests/fixtures/vision/"
                     "human_labels_v2.json")
#: Where own-account strips go. Not the quarantine directory: these are
#: frames of the owner's own media, which the project already retains.
OWN_STRIP_DIR = Path("/home/rastlyn/virallab/holdout_strips")

#: The four moments the hook window is labelled across.
STAMPS = (0.0, 1.0, 2.0, 3.0)
STRIP_WIDTH = 320


def tuning_ids() -> set[int]:
    data = json.loads(TUNING_LABELS.read_text(encoding="utf-8"))
    return {int(k) for k in data["labels"]}


def strip_for(path: Path, out: Path) -> bool:
    """One PNG, four moments side by side. True if written."""
    tiles = []
    for t in STAMPS:
        frames = hookfeat.decode_frames(
            path, start=t, seconds=0.2, fps=5.0, width=STRIP_WIDTH,
            height=STRIP_WIDTH * 16 // 9 // 2 * 2)
        if frames.shape[0] == 0:
            continue
        tiles.append(frames[0])
    if not tiles:
        return False
    strip = np.concatenate(tiles, axis=1)
    Image.fromarray(strip).save(out, format="PNG", optimize=True)
    return True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--kind", choices=["own", "competitor"], default="own")
    ap.add_argument("--count", type=int, default=40)
    args = ap.parse_args()

    eng = create_engine(os.environ["VIRALLAB_DB_URL"], future=True)
    S = sessionmaker(eng, future=True)
    media_root = Path(os.environ["VIRALLAB_MEDIA_ROOT"])
    excluded = tuning_ids()

    made, skipped = [], []
    with S() as s:
        if args.kind == "own":
            OWN_STRIP_DIR.mkdir(parents=True, exist_ok=True)
            dest_dir = OWN_STRIP_DIR
            rows = s.execute(text("""
                select v.id, v.media_path, v.platform
                from videos v join creators c on c.id = v.creator_id
                where c.is_own_account and v.media_status = 'available'
                  and v.media_path is not null
                order by v.id""")).all()
            cands = [(vid, Path(p), plat) for vid, p, plat in rows
                     if vid not in excluded]
        else:
            # ADR 0009: only while the bytes are legitimately held.
            dest_dir = transient.quarantine_root()
            rows = s.execute(text("""
                select tm.video_id, tm.path, v.platform
                from transient_media tm join videos v on v.id = tm.video_id
                where tm.deleted_at is null
                order by tm.acquired_at""")).all()
            cands = [(vid, Path(p), plat) for vid, p, plat in rows
                     if vid not in excluded]

    # Spread across the corpus rather than taking the newest 40, so the
    # held-out set is not all one period of the account's style.
    if len(cands) > args.count:
        step = len(cands) / args.count
        cands = [cands[int(i * step)] for i in range(args.count)]

    for vid, path, platform in cands:
        if vid in excluded:
            skipped.append({"video_id": vid, "why": "in the tuning set"})
            continue
        p = path if path.is_absolute() else media_root / path.name
        if not p.exists():
            skipped.append({"video_id": vid, "why": "file absent"})
            continue
        out = dest_dir / f"strip_{vid}.png"
        try:
            if strip_for(p, out):
                made.append({"video_id": vid, "platform": platform,
                             "strip": str(out)})
            else:
                skipped.append({"video_id": vid, "why": "no frames"})
        except Exception as exc:              # noqa: BLE001 - recorded
            skipped.append({"video_id": vid, "why": str(exc)[:160]})

    print(json.dumps({
        "kind": args.kind,
        "requested": args.count,
        "made": len(made),
        "skipped": len(skipped),
        "tuning_ids_excluded": len(excluded),
        "dir": str(dest_dir),
        "strips": made,
        "skipped_detail": skipped[:10],
        "adr_note": ("competitor strips live in the quarantine directory "
                     "and die on the same six-hour clock as their mp4 "
                     "(ADR 0009); own strips are of retained media"),
    }, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
