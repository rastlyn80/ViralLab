"""Ship masters, performances and match links to the authoritative server.

Run after `match_local_to_posts.py` and `group_performances.py`. The video
files themselves never move - only metadata and fingerprints, which is what
lets the server match newly collected posts on its own.

    python scripts/match_local_to_posts.py
    python scripts/group_performances.py
    python scripts/sync_matching.py
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from remote import connect, run  # noqa: E402

REMOTE = "/home/rastlyn/virallab"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--local", type=Path,
                    default=Path("probes/local_library/fingerprints.json"))
    ap.add_argument("--matches", type=Path,
                    default=Path("probes/local_library/matches_spectral.json"))
    ap.add_argument("--performances", type=Path,
                    default=Path("probes/local_library/performances.json"))
    ap.add_argument("--platform", default="instagram")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    local = json.loads(args.local.read_text(encoding="utf-8"))
    matches = json.loads(args.matches.read_text(encoding="utf-8"))
    perfs = (json.loads(args.performances.read_text(encoding="utf-8"))
             if args.performances.exists() else [])

    masters = []
    for qid, v in local.items():
        masters.append({
            "quick_id": qid,
            "path": v["path"],
            "filename": v["name"],
            "duration_s": v.get("duration_s"),
            "bytes": v.get("bytes"),
            "width": v.get("width"),
            "height": v.get("height"),
            # The full fingerprint, audio included, so the server can match
            # without this machine.
            "fingerprint": {
                "hashes": v.get("hashes"),
                "frames": v.get("frames"),
                "duration_s": v.get("duration_s"),
                "audio": v.get("audio"),
                "audiofp": v.get("audiofp"),
                "audiofp_status": v.get("audiofp_status"),
            },
        })

    links = []
    for bucket, status in (("auto", "auto"), ("review", "pending")):
        for r in matches.get(bucket, []):
            links.append({
                "platform": r.get("platform", args.platform),
                "post_id": r["post_id"],
                "quick_id": r["quick_id"],
                "score": r["score"],
                "frame_overlap": r.get("frame_overlap"),
                "duration_delta_s": r.get("duration_delta_s"),
                "runner_up": r.get("runner_up"),
                "status": status,
                "method": "dhash+audiofp-v3",
                "audio_similarity": r.get("audio_similarity"),
                "audio_method": r.get("audio_method"),
                "audio_offset_s": r.get("audio_offset_s"),
                "evidence": {
                    k: r.get(k) for k in
                    ("frame_overlap", "audio_similarity", "audio_method",
                     "audio_offset_s", "duration_delta_s", "same_cut",
                     "offset", "ambiguous", "runner_up", "alternatives",
                     "performance_agreement")
                    if r.get(k) is not None},
            })

    payload = {
        "masters": masters,
        "performances": [{"label": g["label"], "members": g["members"],
                          "notes": (
                              f"grouped by spectral audio; {g.get('edges', 0)}"
                              f" internal edge(s), weakest "
                              f"{g.get('cohesion')}")}
                         for g in perfs],
        "links": links,
    }

    blob = json.dumps(payload)
    print(f"  {len(masters)} master(s), {len(payload['performances'])} "
          f"performance(s), {len(links)} link(s) -> {len(blob)/1e6:.1f} MB")
    if args.dry_run:
        print("  (dry run)")
        return 0

    c = connect()
    try:
        sftp = c.open_sftp()
        sftp.put(str(Path(__file__).resolve().parent / "provision"
                     / "store_matching.py"),
                 f"{REMOTE}/store_matching.py")
        with sftp.open(f"{REMOTE}/.matching_payload.json", "w") as fh:
            fh.write(blob)
        sftp.close()
        print("  payload uploaded")

        rc, out, err = run(
            c, f"cd {REMOTE} && set -a && . ./.env && set +a && "
               f".venv/bin/python store_matching.py", timeout=1800)
        txt = (out or err).strip()
        print(txt.encode("ascii", "replace").decode("ascii"))
        # The payload carries the whole local library; do not leave it lying
        # around on the server.
        run(c, f"rm -f {REMOTE}/.matching_payload.json", timeout=120)
        return rc
    finally:
        c.close()


if __name__ == "__main__":
    raise SystemExit(main())
