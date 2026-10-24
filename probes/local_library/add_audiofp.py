"""Add the spectral audio fingerprint to an existing fingerprint file.

Runs on either side of the system without transferring media:

    # local masters (Windows) - paths come from the `path` field
    python probes/local_library/add_audiofp.py --file fingerprints.json

    # published Instagram clips (Ubuntu) - paths built from the key
    python add_audiofp.py --file ig_fingerprints.json \
        --media-dir media --name-template "ig_{key}.mp4"

Resumable and additive: entries that already carry an `audiofp` are skipped,
the legacy `audio` envelope is left untouched, and progress is flushed to
disk every few files so an interrupted run loses almost nothing.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from shared import audiofp  # noqa: E402

FLUSH_EVERY = 25


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", type=Path, required=True)
    ap.add_argument("--media-dir", type=Path)
    ap.add_argument("--name-template", default="{key}")
    ap.add_argument("--redo", action="store_true",
                    help="recompute even where an audiofp already exists")
    args = ap.parse_args()

    data = json.loads(args.file.read_text(encoding="utf-8"))
    todo = [k for k, v in data.items()
            if args.redo or not v.get("audiofp")]
    print(f"  {len(data)} entries, {len(todo)} need a spectral fingerprint",
          flush=True)

    done = failed = missing = 0
    for i, key in enumerate(todo, 1):
        entry = data[key]
        if args.media_dir:
            path = args.media_dir / args.name_template.format(key=key)
        else:
            path = Path(entry["path"])
        if not path.exists():
            # Recorded explicitly: an absent file is a coverage gap to
            # reconcile, not a fingerprint that failed to compute.
            entry["audiofp"] = None
            entry["audiofp_status"] = "media_missing"
            missing += 1
        else:
            fp = audiofp.fingerprint(path)
            entry["audiofp"] = fp
            entry["audiofp_status"] = "ok" if fp else "decode_failed"
            if fp:
                done += 1
            else:
                failed += 1
        if i % FLUSH_EVERY == 0 or i == len(todo):
            args.file.write_text(json.dumps(data, indent=1), encoding="utf-8")
            print(f"  {i}/{len(todo)}  ok={done} failed={failed} "
                  f"missing={missing}", flush=True)

    print(f"\n  fingerprinted {done}, decode failed {failed}, "
          f"media missing {missing}")
    print(f"  -> {args.file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
